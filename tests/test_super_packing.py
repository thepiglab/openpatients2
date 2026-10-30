import json
import copy
import os
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
    s.prepare(campaign[2],work,expected=3,with_images=False,scheduler='packed',
              profiles=['nvfp4', 'bf16', 'spark', 'beam-nvfp4-dflash2'])
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
    # Direct diagnostic export must collect child results even if scoring has
    # never started (the missing-httpx failure previously skipped this step).
    s.export(work)
    assert list((work/'arms').glob('*/results/articles/*/*/receipt.json'))
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


def test_shared_beam_ablation_storage_uses_maximum_profile_estimate(monkeypatch):
    from openpatients2 import beam_recipe as beam
    profiles = ['beam-nvfp4-dflash2', 'beam-nvfp4-no-spec']
    wave = p.plan(profiles)['waves'][0]
    assert wave['required_free_gib'] == 418
    assert wave['totals'] == {'gpus': 8, 'cpus': 32, 'memory_gib': 192}
    groups = wave['artifact_residency']['groups']
    assert len(groups) == 1 and set(groups[0]['profiles']) == set(profiles)
    assert groups[0]['target_model'] == beam.NVFP4
    assert groups[0]['target_revision'] == beam.PINS[beam.NVFP4]['revision']
    assert groups[0]['engine_image'] == beam.ENGINE['image']
    assert {m['model'] for m in groups[0]['models']} == {beam.NVFP4, beam.DFLASH2}
    monkeypatch.setitem(p.RESOURCES, profiles[1], {**p.RESOURCES[profiles[1]], 'artifacts_gib': 310})
    assert p.plan(profiles)['waves'][0]['required_free_gib'] == 438


@pytest.mark.parametrize('difference', ['target', 'image', 'draft', 'unpinned'])
def test_storage_estimates_stay_additive_without_verified_full_sharing(monkeypatch, difference):
    from openpatients2 import nemotron_super_recipes as recipes
    profiles = ['beam-nvfp4-dflash2', 'beam-nvfp4-no-spec']
    altered = copy.deepcopy(recipes.PROFILES[profiles[1]])
    if difference == 'target':
        altered['model'] = recipes.PROFILES['bf16']['model']
    elif difference == 'image':
        alternate_engine = {**recipes.engine(altered), 'image': 'docker://alternate@sha256:' + '1' * 64}
        monkeypatch.setitem(recipes.ENGINES, 'alternate', alternate_engine)
        altered['engine'] = 'alternate'
    elif difference == 'draft':
        altered['drafter'] = recipes.PROFILES['spark']['drafter']
    else:
        model = altered['model']
        monkeypatch.setitem(recipes.PINS, model, {**recipes.PINS[model], 'revision': 'main'})
    monkeypatch.setitem(recipes.PROFILES, profiles[1], altered)
    wave = p.plan(profiles)['waves'][0]
    assert wave['required_free_gib'] == 708
    assert len(wave['artifact_residency']['groups']) == 2


def test_different_model_residency_and_same_target_draft_ablation():
    assert p.plan(['nvfp4', 'bf16', 'beam-nvfp4-dflash2'])['waves'][0]['required_free_gib'] == 743
    assert p.plan(['beam-nvfp4-dflash2', 'beam-nvfp4-dflash2-short'])['waves'][0]['required_free_gib'] == 418


def test_resume_preflight_counts_shared_inode_once_without_double_subtraction(tmp_path, monkeypatch):
    profiles = ['beam-nvfp4-dflash2', 'beam-nvfp4-no-spec']
    wave = p.plan(profiles)['waves'][0]
    monkeypatch.setattr(s, 'check', lambda _: {'nemotron_super': {'scheduler': {'waves': [wave]}}})
    monkeypatch.setattr(p.shutil, 'disk_usage', lambda _: SimpleNamespace(free=500 * 2**30))
    source, target = [tmp_path / 'profiles' / profile / 'runtime' for profile in profiles]
    for runtime in (source, target):
        (runtime / 'weights/beam').mkdir(parents=True)
    (source / 'weights/beam/model.safetensors').write_bytes(b'weight-data')
    os.link(source / 'weights/beam/model.safetensors', target / 'weights/beam/model.safetensors')
    (source / 'image.sif').write_bytes(b'image')
    os.link(source / 'image.sif', target / 'image.sif')
    # Equal contents alone do not establish shared disk residency.
    (source / 'weights/beam/unshared').write_bytes(b'copy')
    (target / 'weights/beam/unshared').write_bytes(b'copy')
    (target / 'weights/beam/alias').symlink_to(source / 'weights/beam/model.safetensors')
    (source / 'unfinished.building.sif').write_bytes(b'not counted')
    cache = source / 'weights/beam/.cache'; cache.mkdir()
    (cache / 'download').write_bytes(b'not counted')
    receipt = p.preflight(tmp_path, 0)
    assert receipt['resident_bytes'] == len(b'weight-data') + len(b'image') + 2 * len(b'copy')
    assert receipt['resident_unique_files'] == 4
    assert receipt['resident_duplicate_hardlinks'] == 2
    assert receipt['required_free_bytes'] == 418 * 2**30 - receipt['resident_bytes']
    assert receipt['passed']
