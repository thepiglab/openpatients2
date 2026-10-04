import copy
import gzip
import json
from pathlib import Path
from types import SimpleNamespace
import threading

import pytest

from openpatients2.corpus_pilot import prepare, campaign_phases, job_command, verify_cpu
from openpatients2.data import read_jsonl, write_json
from openpatients2.pilot_recovery import recover_cpu, bootstrap_paths, validate_parent
from openpatients2.prompt_optimization import ClinicalAdapter, optimize_prompts
from test_pilot_extract import article

ROOT=Path(__file__).resolve().parents[1]


def test_separate_allocation_chain_and_no_gpu_dependency_sync():
    c={'config':{'separate_gepa':True,'account':'cai5724','qos':'cai5724','gpu_partition':'hpg-b200',
        'cpu_partition':'hpg-default'},'root':str(ROOT),'work':'/tmp/campaign'}
    assert campaign_phases(c)==['cpu','setup','download','bootstrap','gepa','gpu','cleanup','report','source-cleanup']
    assert '--gres=gpu:b200:1' in job_command(c,'gepa')
    assert '--time=03:00:00' in job_command(c,'gepa')
    assert '--gres=gpu:b200:8' in job_command(c,'bootstrap')
    script=(ROOT/'scripts/corpus_pilot.sbatch').read_text()
    assert '"$phase" != gpu && "$phase" != bootstrap && "$phase" != gepa' in script


def ended_campaign(tmp_path):
    old=prepare(ROOT,tmp_path/'old',ROOT/'configs/pilot/overnight.yaml')
    parent=Path(old['work']);(parent/'engine').mkdir()
    write_json(parent/'gpu.json',{'status':'failed','error':'Slurm signal 15'})
    write_json(parent/'cpu.json',{'status':'ready','hashes':{},'sample_size':20})
    for path in bootstrap_paths(parent,old['config']):
        write_json(path/'report.json',{'status':'completed','outputs':{'patients':str(path/'patients.jsonl')}})
        (path/'patients.jsonl').write_text('')
        write_json(path/'rosters.json',{'articles':[]})
    a=article()
    write_json(parent/'review-source-sample.json',{'articles':[a]})
    for relative in ('vision-assets/manifest.json','holdout/vision-assets/manifest.json'):
        write_json(parent/relative,{'figures':[],'complete':True})
    write_json(parent/'profile/rosters.json',json.loads((ROOT/'benchmarks/corpus-correctness/rosters.json').read_text()))
    write_json(parent/'prompt-optimization/components.json',[{'component':'demographics','status':'completed'}])
    write_json(parent/'prompt-optimization/best-prompts.json',{'demographics':'Preserve age.'})
    new=prepare(ROOT,tmp_path/'new',ROOT/'configs/pilot/overnight.yaml')
    new['config']['resume_from']=str(parent)
    from openpatients2.provenance import json_digest
    new['config_sha256']=json_digest(new['config'])
    write_json(Path(new['work'])/'source-owner.json',{'work':new['work'],'config_sha256':new['config_sha256'],
        'scope':'corpus-pilot-sources/1'})
    return old,new


def test_recovery_after_source_cleanup_restores_same_samples_and_completed_trials(tmp_path,monkeypatch):
    monkeypatch.delenv('CUDA_VISIBLE_DEVICES',raising=False)
    old,new=ended_campaign(tmp_path)
    before=(Path(old['work'])/'prompt-optimization/components.json').read_bytes()
    r=recover_cpu(new);work=Path(new['work'])
    assert r['status']=='ready' and r['recovered_from']==old['work']
    verify_cpu(new)
    assert len(list(read_jsonl(work/'profile/sample.jsonl.gz')))==20
    assert len(list(read_jsonl(work/'holdout/profile/sample.jsonl.gz')))==1
    trial=bootstrap_paths(work,new['config'])[0]
    assert json.loads((trial/'report.json').read_text())['outputs']['patients']==str(trial/'patients.jsonl')
    assert (Path(old['work'])/'prompt-optimization/components.json').read_bytes()==before
    assert 'bootstrap' not in campaign_phases(new)
    assert not (work/'tokenizer').exists() and not (work/'acquisition').exists()
    assert json.loads((work/'holdout/source-owner.json').read_text())['config_sha256']==new['config_sha256']


def test_recovery_rejects_active_parent_and_changed_immutable_code(tmp_path):
    old,new=ended_campaign(tmp_path)
    write_json(Path(old['work'])/'gpu.json',{'status':'running'})
    with pytest.raises(ValueError,match='ended'):validate_parent(new,old['work'])
    write_json(Path(old['work'])/'gpu.json',{'status':'failed'})
    p=Path(old['work'])/'pilot.json'; data=json.loads(p.read_text())
    data['runtime'][str(ROOT/'src/openpatients2/measurements.py')]='0'*64
    write_json(p,data)
    with pytest.raises(ValueError,match='implementation changed'):validate_parent(new,old['work'])


def test_recovery_rejects_symlink_before_importing_checkpoints(tmp_path,monkeypatch):
    monkeypatch.delenv('CUDA_VISIBLE_DEVICES',raising=False)
    old,new=ended_campaign(tmp_path)
    folder=Path(old['work'])/'prompt-optimization'
    (folder/'escape.bin').symlink_to(ROOT/'pyproject.toml')
    with pytest.raises(ValueError,match='escaped'):recover_cpu(new)


def examples(tmp_path,tasks):
    articles=[];trial=tmp_path/'trial';trial.mkdir(); patients=[]; rosters=[]
    for i in range(8):
        a=article();a['article_id']=f'PMC{i}.1';a['pmcid']=f'PMC{i}';articles.append(a)
        rid=a['article_id']+':p1';patients.append({'source':{'record_id':rid}})
        rosters.append({'article_id':a['article_id'],'status':'valid','roster':{}})
        for task in tasks:
            write_json(trial/'tasks'/f'{i}-{task}.json',{'task':task,
                'identity':{'article_id':a['article_id'],'record_id':rid},'attempts':[{
                    'request':[{'role':'user','content':'Fixed input.'}],'errors':[]}]})
    (trial/'patients.jsonl').write_text(''.join(json.dumps(p)+'\n' for p in patients))
    write_json(trial/'rosters.json',{'articles':rosters})
    sample=tmp_path/'sample.jsonl.gz'
    with gzip.open(sample,'wt') as out:
        for a in articles:out.write(json.dumps(a)+'\n')
    ref=tmp_path/'reference.json';write_json(ref,{'checks':[]})
    return sample,trial,ref


def test_gepa_families_overlap_on_one_replica_and_reuse_completed_searches(tmp_path,monkeypatch):
    import gepa
    import openpatients2.prompt_optimization as m
    monkeypatch.setattr(m,'COMPONENTS',['demographics','observations'])
    sample,trial,ref=examples(tmp_path,m.COMPONENTS)
    barrier=threading.Barrier(2); calls=[]
    def optimize(**kwargs):
        task=next(iter(kwargs['seed_candidate']));calls.append(task)
        barrier.wait(timeout=3)
        return SimpleNamespace(candidates=[{task:'Preserve evidence.'}],val_aggregate_scores=[1.0],total_metric_calls=1)
    monkeypatch.setattr(gepa,'optimize',optimize)
    output=tmp_path/'optimization'
    winners,report=optimize_prompts(sample,[trial],output,{},['http://localhost:8000/v1'],65536,ref,
        seconds=10,calls=4,workers=2)
    assert set(calls)==set(m.COMPONENTS) and report['parallel_searches']==2
    before=(output/'components.json').read_bytes()
    def unexpected(**kwargs):raise AssertionError('Completed searches must not run again')
    monkeypatch.setattr(gepa,'optimize',unexpected)
    reused,r=optimize_prompts(sample,[trial],output,{},['http://localhost:8000/v1'],65536,ref,
        seconds=10,calls=4,workers=2,resume=True)
    assert reused==winners and all(c['reused_from_checkpoint'] for c in r['components'])
    with pytest.raises(ValueError,match='configuration changed'):
        optimize_prompts(sample,[trial],output,{'temperature':.5},['http://localhost:8000/v1'],65536,ref,resume=True)


def test_adapter_resume_does_not_overwrite_incomplete_rollouts(tmp_path):
    (tmp_path/'rollout-00003').mkdir();(tmp_path/'reflection-00007').mkdir()
    adapter=ClinicalAdapter([],{},['http://localhost:8000/v1'],65536,tmp_path,{},'observations',999)
    assert adapter.number==8


def test_one_gpu_probe_snapshot_and_telemetry_follow_allocated_physical_gpu(monkeypatch):
    from openpatients2 import glimmer_tuning as tuning
    from openpatients2 import gpu_telemetry as telemetry
    monkeypatch.setenv('CUDA_VISIBLE_DEVICES','0')
    monkeypatch.setenv('SLURM_JOB_GPUS','3')
    monkeypatch.delenv('SLURM_STEP_GPUS',raising=False)
    def command(args,**kwargs):
        if any('--query-compute-apps=' in x for x in args): text='GPU-other, 100, python, 30\nGPU-owned, 101, python, 40\n'
        elif any('timestamp' in x for x in args): text='now, 0, GPU-other, 99, 10, 30, 100, 10\nnow, 3, GPU-owned, 70, 10, 40, 100, 10\n'
        else:text='GPU-other, 0, 30\nGPU-owned, 3, 40\n'
        return SimpleNamespace(stdout=text,stderr='',returncode=0)
    monkeypatch.setattr(tuning.subprocess,'run',command)
    state=tuning.gpu_snapshot(expected_gpus=1)
    assert set(state['devices'])=={'GPU-owned'}
    assert state['processes'][0]['pid']==101
    sample=telemetry.sample()
    assert len(sample['gpus'])==1 and sample['gpus'][0][2]=='GPU-owned'


async def test_extraction_does_not_load_eight_replicas_after_gepa_failure(tmp_path):
    from openpatients2.corpus_pilot import gpu
    c={'work':str(tmp_path),'config':{'separate_gepa':True}}
    with pytest.raises(ValueError,match='no eight-GPU servers'):
        await gpu(c)
