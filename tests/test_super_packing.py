import json
from pathlib import Path
from types import SimpleNamespace
import tarfile

import pytest

from openpatients2 import dense_benchmark as b, dense_nemotron_super as s
from openpatients2 import super_packing as p
from test_dense_nemotron_super import campaign
from test_dense_benchmark import finish


def packed(campaign, tmp_path):
    work = tmp_path/'packed'
    s.prepare(campaign[2],work,expected=3,with_images=False,scheduler='packed')
    return work


def test_wave_limits_and_independent_replicas():
    plan = p.plan(['nvfp4','bf16','spark','beam-nvfp4-dflash2'])
    assert len(plan['waves']) == 2
    assert plan['waves'][0]['totals'] == {'gpus':8,'cpus':32,'memory_gib':224}
    assert plan['waves'][1]['profiles'] == ['spark']
    assert plan['waves'][1]['totals'] == {'gpus':8,'cpus':16,'memory_gib':160}
    assert plan['resources']['nvfp4']['replicas'] == 2
    with pytest.raises(ValueError,match='four focused'):
        p.plan(['fastino'])


def test_profile_lifecycles_and_dag_are_isolated(campaign,tmp_path):
    work = packed(campaign,tmp_path)
    m = s.check(work)
    for profile in m['nemotron_super']['profiles']:
        child = p.slot(work,profile)
        cm = s.check(child)
        assert cm['articles'] == m['articles']
        assert cm['nemotron_super']['profiles'] == [profile]
        assert set(cm['nemotron_super']['arms']) <= set(m['nemotron_super']['arms'])
    plan = s.submit(work,'account','qos',dry_run=True)
    workers = [c for c in plan['commands_without_dependencies'] if c[-3]=='worker']
    assert len(workers)==5
    assert sorted(c[-1] for c in workers).count('nvfp4-text')==2
    assert {next(x for x in c if x.startswith('--export=')) .split('OP2_WORKER_REPLICA=')[1] for c in workers} == {'0','1'}
    deps = plan['dependencies']
    assert deps['worker-nvfp4-text-r0'] == [('afterok',['download-nvfp4'])]
    assert deps['worker-bf16-text-r0'] == [('afterok',['download-bf16'])]
    assert len(deps['worker-nvfp4-text-r1'][0][1]) == 3  # extra replica preserves staging headroom
    assert len(deps['cleanup-bf16'][0][1]) == 4  # wait for entire GPU wave
    assert deps['preflight-1'][0][0] == 'afterok'
    assert deps['setup-spark'] == [('afterok',['preflight-1'])]
    assert s.serving(p.slot(work,'nvfp4'),'nvfp4-text',0).environment['VLLM_CACHE_ROOT'] != s.serving(p.slot(work,'nvfp4'),'nvfp4-text',1).environment['VLLM_CACHE_ROOT']


def test_low_disk_fails_before_any_model_download(campaign,tmp_path,monkeypatch):
    work=packed(campaign,tmp_path)
    monkeypatch.setattr(p.shutil,'disk_usage',lambda _:SimpleNamespace(free=100*2**30))
    with pytest.raises(ValueError,match='Free old runtime'):
        p.preflight(work,0)
    assert not list(work.glob('profiles/*/runtime/weights'))
    assert not b.read(work/'results/packing-preflight-0.json')['passed']


def test_shared_worker_locks_exclude_download_and_cleanup(campaign,tmp_path):
    work=packed(campaign,tmp_path)
    child=p.slot(work,'nvfp4')
    s.runtime_owner(child,create=True)
    with s.runtime_lock(child,shared=True):
        with s.runtime_lock(child,shared=True):
            with pytest.raises(ValueError,match='owns the runtime'):
                with s.runtime_lock(child):
                    pass
    with s.runtime_lock(child):
        with pytest.raises(ValueError,match='owns the runtime'):
            with s.runtime_lock(child,shared=True):
                pass


def test_two_replica_queues_never_claim_same_article(campaign,tmp_path,monkeypatch):
    work=packed(campaign,tmp_path);child=p.slot(work,'nvfp4');arm=child/'arms/nvfp4-text'
    monkeypatch.setenv('OP2_SUBMISSION_ID','test')
    a=b.paired_jobs(arm,[arm],0);other=b.paired_jobs(arm,[arm],1)
    first=next(a)
    assert list(other)==[]  # fixture has one train article, already claimed
    assert first[1]['article_id']==b.read(arm/'manifest.json')['articles'][0]['article_id']
    a.close()


def test_collect_and_export_keep_results_and_exclude_weights(campaign,tmp_path):
    work=packed(campaign,tmp_path);m=s.check(work);reference=b.rows(work/'reference/unified.jsonl');row=next(iter(reference.values()))
    for name,definition in m['nemotron_super']['arms'].items():
        child=p.slot(work,definition['profile'])
        finish(child/'arms'/name,row)
        (child/'runtime/weights').mkdir(parents=True)
        (child/'runtime/weights/DO_NOT_EXPORT.safetensors').write_bytes(b'weights')
    assert s.status(work)['complete']
    report=s.aggregate(work)
    assert report['complete']
    assert s.verify(work)['verified']
    with tarfile.open(work/'results.tar.gz') as archive:
        assert not any('DO_NOT_EXPORT' in n for n in archive.getnames())
        dest=tmp_path/'unpack'
        archive.extractall(dest,filter='data')
    restored=dest/work.name
    assert s.check(restored)
    assert s.status(restored)['complete']
    assert s.verify(restored)['verified']


def test_held_submission_resolves_wave_dependencies_before_release(campaign,tmp_path,monkeypatch):
    work=packed(campaign,tmp_path); calls=[];counter=iter(range(100,1000))
    def run(cmd,**kwargs):
        calls.append(cmd)
        if cmd[0]=='sbatch':
            return SimpleNamespace(stdout=str(next(counter))+';cluster\n')
        return SimpleNamespace(stdout='')
    monkeypatch.setattr(p.subprocess,'run',run)
    result=s.submit(work,'account','qos')
    jobs=result['jobs']; nv=[j for j in jobs if j['phase']=='worker' and j['arm']=='nvfp4-text']
    assert len(nv)==2
    assert all('--gres=gpu:b200:1' in j['argv'] and '--mem=32G' in j['argv'] for j in nv)
    assert next(x for x in nv[0]['argv'] if x.startswith('--dependency=')) != next(x for x in nv[1]['argv'] if x.startswith('--dependency='))
    first_release=next(i for i,c in enumerate(calls) if c[:2]==['scontrol','release'])
    assert all(c[0]=='sbatch' for c in calls[:first_release])
    assert b.read(Path(result['ledger']))['status']=='released'


def test_submission_failure_cancels_every_accepted_job(campaign,tmp_path,monkeypatch):
    import subprocess
    work=packed(campaign,tmp_path); calls=[]
    def run(cmd,**kwargs):
        calls.append(cmd)
        if cmd[0]=='sbatch':
            if len([c for c in calls if c[0]=='sbatch'])==3:
                raise subprocess.CalledProcessError(1,cmd)
            return SimpleNamespace(stdout=str(100+len(calls)))
        return SimpleNamespace(stdout='')
    monkeypatch.setattr(p.subprocess,'run',run)
    with pytest.raises(subprocess.CalledProcessError):
        s.submit(work,'account','qos')
    assert len([c for c in calls if c[0]=='scancel'])==2
    assert not any(c[0]=='scontrol' for c in calls)


def test_host_memory_uses_cgroup_not_node_memory(monkeypatch):
    from openpatients2.dense_benchmark_telemetry import host_memory
    values={'/proc/self/cgroup':'0::/slurm/job1/batch\n',
            '/sys/fs/cgroup/slurm/job1/batch/memory.current':'123',
            '/sys/fs/cgroup/slurm/job1/batch/memory.peak':'456',
            '/sys/fs/cgroup/slurm/job1/batch/memory.max':'max'}
    def read(path,*args,**kwargs):
        if str(path) not in values:
            raise FileNotFoundError(str(path))
        return values[str(path)]
    monkeypatch.setattr(Path,'read_text',read)
    result=host_memory()
    assert result['available'] and result['peak_bytes']==456 and result['limit_bytes'] is None
    values.clear()
    assert host_memory()['available'] is False


def test_cleanup_one_profile_cannot_remove_another_models_weights(campaign,tmp_path):
    work=packed(campaign,tmp_path)
    for profile in ('nvfp4','bf16'):
        child=p.slot(work,profile)
        s.runtime_owner(child,create=True)
        b.write(child/'runtime/active-profile.json',{'profile':profile})
        weights=s.weights_path(child,s.PROFILES[profile]['model'])
        weights.mkdir(parents=True)
        (weights/'weights.safetensors').write_bytes(b'keep other profile')
    result=s.cleanup(work,'nvfp4')
    assert result['profiles']['nvfp4']['deleted']
    assert not s.weights_path(p.slot(work,'nvfp4'),s.PROFILES['nvfp4']['model']).exists()
    assert (s.weights_path(p.slot(work,'bf16'),s.PROFILES['bf16']['model'])/'weights.safetensors').exists()


def test_every_permitted_pipeline_overlap_fits_allocation(campaign,tmp_path):
    import itertools
    work=packed(campaign,tmp_path)
    result=s.submit(work,'account','qos',dry_run=True)
    deps=result['dependencies']; ancestors={}
    for name,conditions in deps.items():
        direct={k for _,keys in conditions for k in keys}
        ancestors[name]=direct | set().union(*(ancestors[k] for k in direct))
    resources={}
    for cmd in result['commands_without_dependencies']:
        name=next(x for x in cmd if x.startswith('--job-name=')).split('op2-super-',1)[1]
        cpu=int(next(x for x in cmd if x.startswith('--cpus-per-task=')).split('=')[1])
        mem=int(next(x for x in cmd if x.startswith('--mem=')).split('=')[1][:-1])
        gpu=next((int(x.rsplit(':',1)[1]) for x in cmd if x.startswith('--gres=')),0)
        resources[name]={'cpus':cpu,'memory_gib':mem,'gpus':gpu}
    def visit(candidates,totals):
        for i,name in enumerate(candidates):
            total={k:totals[k]+resources[name][k] for k in p.LIMITS}
            assert all(total[k]<=limit for k,limit in p.LIMITS.items()), (name,total)
            remaining=[n for n in candidates[i+1:] if n not in ancestors[name] and name not in ancestors[n]]
            visit(remaining,total)
    visit(list(deps),dict.fromkeys(p.LIMITS,0))


def test_legacy_packed_plan_keeps_original_download_barrier(campaign,tmp_path):
    work=packed(campaign,tmp_path);m=s.check(work)
    m['nemotron_super']['scheduler']=p.plan(m['nemotron_super']['profiles'],pipelined=False)
    b.write(work/'manifest.json',m)
    b.write(work/'prepared.json',{'manifest_sha256':b.sha(work/'manifest.json')})
    result=s.submit(work,'account','qos',dry_run=True)
    assert len(result['dependencies']['worker-nvfp4-text-r0'][0][1])==3
