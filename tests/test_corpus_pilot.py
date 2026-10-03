import copy
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from openpatients2 import corpus_pilot as pilot
from openpatients2.provenance import json_digest


ROOT=Path(__file__).resolve().parents[1]


def prepared(tmp_path):
    return pilot.prepare(ROOT,tmp_path/'campaign',ROOT/'configs/pilot/corpus.yaml')


def test_preparation_has_no_download_or_scheduler_and_is_immutable(tmp_path):
    campaign=prepared(tmp_path)
    assert pilot.load(campaign['work'])==campaign
    assert campaign['config']['sample_size']==48
    assert not (Path(campaign['work'])/'engine').exists()
    with pytest.raises(ValueError,match='new work directory'):
        pilot.prepare(ROOT,campaign['work'],ROOT/'configs/pilot/corpus.yaml')


def test_separate_cpu_gpu_requests_and_afterany_cleanup(tmp_path):
    campaign=prepared(tmp_path)
    cpu=pilot.job_command(campaign,'cpu')
    gpu=pilot.job_command(campaign,'gpu','afterany:12')
    cleanup=pilot.job_command(campaign,'cleanup','afterany:13')
    assert '--gres=none' in cpu and '--gres=none' in cleanup
    assert '--cpus-per-task=32' in cpu and '--mem=64G' in cpu
    assert '--gres=gpu:b200:8' in gpu and '--nodes=1' in gpu
    assert '--account=cai5724' in gpu and '--mem=250G' in gpu
    assert '--dependency=afterany:13' in cleanup


def test_input_hash_checks_allow_pinned_config_but_not_outside_artifacts(tmp_path):
    campaign=prepared(tmp_path);work=Path(campaign['work'])
    sample=work/'sample';sample.write_text('original')
    external=campaign['config']['source_config']
    receipt={'status':'ready','hashes':{'sample':pilot.sha256(sample),external:pilot.sha256(external)}}
    (work/'cpu.json').write_text(json.dumps(receipt))
    pilot.verify_cpu(campaign)
    sample.write_text('changed')
    with pytest.raises(ValueError,match='changed'): pilot.verify_cpu(campaign)
    outside=tmp_path/'outside';outside.write_text('not pinned')
    receipt['hashes']={str(outside):pilot.sha256(outside)}
    (work/'cpu.json').write_text(json.dumps(receipt))
    with pytest.raises(ValueError,match='changed'): pilot.verify_cpu(campaign)


def test_held_chain_preflights_and_releases_successors_first(tmp_path,monkeypatch):
    campaign=prepared(tmp_path);calls=[];counter=100
    def command(args,env,path,audit):
        nonlocal counter
        calls.append(args)
        if args[0]=='sbatch' and '--test-only' not in args: counter+=1
        return SimpleNamespace(stdout=str(counter)+'\n')
    monkeypatch.setattr('openpatients2.hipergator.slurm_command',command)
    result=pilot.submit_chain(campaign,['setup','download','gpu','cleanup','report'],'gpu')
    assert result['status']=='released'
    submitted=[c for c in calls if c[0]=='sbatch' and '--test-only' not in c]
    assert all('--hold' in c for c in submitted)
    releases=[c[-1] for c in calls if c[:2]==['scontrol','release']]
    assert releases==['105','104','103','102','101']
    assert '--dependency=afterany:103' in submitted[3]
    with pytest.raises(ValueError,match='already'): pilot.submit_chain(campaign,['gpu'],'gpu')


def test_submit_failure_cancels_held_jobs(tmp_path,monkeypatch):
    campaign=prepared(tmp_path);calls=[];n=0
    def command(args,env,path,audit):
        nonlocal n
        calls.append(args)
        if args[0]=='sbatch' and '--test-only' not in args:
            n+=1
            if n==2: raise RuntimeError('account limit')
        return SimpleNamespace(stdout='101\n')
    monkeypatch.setattr('openpatients2.hipergator.slurm_command',command)
    with pytest.raises(RuntimeError,match='account'): pilot.submit_chain(campaign,['setup','download'],'gpu')
    assert ['scancel','101'] in calls
    saved=json.loads((Path(campaign['work'])/'gpu-jobs.json').read_text())
    assert saved['rollback']=='cancellation_requested'


def test_owned_cleanup_survives_changed_cpu_sources(tmp_path,monkeypatch):
    campaign=prepared(tmp_path);work=Path(campaign['work']);engine=work/'engine';engine.mkdir()
    config={'models':[{'name':'glimmer-fp8','revision':'a'*40}]}
    saved={'work':str(engine),'config':config,'config_sha256':json_digest(config)}
    (engine/'campaign.json').write_text(json.dumps(saved))
    monkeypatch.setattr('openpatients2.hipergator.cleanup',lambda c,m:{'status':'deleted'})
    assert pilot.stage(campaign,'cleanup')['status']=='deleted'
    saved['work']='/not-owned';(engine/'campaign.json').write_text(json.dumps(saved))
    with pytest.raises(ValueError,match='owned'): pilot.stage(campaign,'cleanup')


def test_single_command_prepares_engine_and_submits_full_chain(tmp_path, monkeypatch):
    events=[]
    monkeypatch.setattr(pilot,'prepare_engine',lambda campaign,sif: events.append(('engine',sif)))
    def submit(campaign, phases, label):
        events.append((label,phases))
        return {'status':'released'}
    monkeypatch.setattr(pilot,'submit_chain',submit)
    args=SimpleNamespace(pilot_action='submit',work_dir=str(tmp_path/'all'),
                         config=str(ROOT/'configs/pilot/corpus.yaml'),sif=None)
    assert pilot.dispatch(args)['status']=='released'
    assert events==[('engine',None),('campaign',['cpu','setup','download','gpu','cleanup','report','source-cleanup'])]


def test_template_library_is_runtime_dependency():
    import tomllib
    project=tomllib.loads((ROOT/'pyproject.toml').read_text())
    assert 'jinja2==3.1.6' in project['project']['dependencies']
    assert 'jinja2==3.1.6' not in project['dependency-groups']['dev']


def fixed_campaign(tmp_path):
    import yaml
    config=yaml.safe_load((ROOT/'configs/pilot/correctness.yaml').read_text())
    for key in pilot.FIXED_PATH_KEYS:
        source=tmp_path/(key+'.json');source.write_text('{}')
        config[key]=str(source)
    path=tmp_path/'correctness.yaml';path.write_text(yaml.safe_dump(config))
    return pilot.prepare(ROOT,tmp_path/'correctness',path)


def test_fixed_fixture_and_rosters_are_pinned_and_old_configs_still_prepare(tmp_path):
    campaign=fixed_campaign(tmp_path)
    assert campaign['config']['source_mode']=='fixed_fixture'
    assert campaign['config']['sample_size']==20
    assert campaign['config']['seeds']==[42,43,44]
    for key in pilot.FIXED_PATH_KEYS:
        assert campaign['runtime'][campaign['config'][key]]==pilot.sha256(campaign['config'][key])
    Path(campaign['config']['fixed_rosters']).write_text('changed')
    with pytest.raises(ValueError,match='runtime changed'):
        pilot.load(campaign['work'])


@pytest.mark.asyncio
async def test_correctness_runs_share_manual_rosters_and_counterbalance_seed_order(tmp_path,monkeypatch):
    campaign=fixed_campaign(tmp_path);work=Path(campaign['work'])
    (work/'profile').mkdir();(work/'profile'/'rosters.json').write_text('{}')
    calls=[];discoveries=[]
    async def discover(config,sample,output,endpoints,context,*,seed):
        discoveries.append(seed)
        return {'status':'completed','seed':seed,'rosters':str(output/'rosters.json')}
    async def run(config,sample,output,endpoints,context,arm,**kwargs):
        calls.append((kwargs['seed'],kwargs['input_scope'],kwargs['frozen_rosters']))
        return {'tokens':{},'task_count':1,'valid_tasks':1}
    monkeypatch.setattr('openpatients2.pilot_extract.prepare_rosters',discover,raising=False)
    monkeypatch.setattr('openpatients2.pilot_extract.run_pilot',run)
    rows, found=await pilot.correctness_runs(campaign,work/'extraction'/'layout',['mock'],65536)
    assert discoveries==[42,43,44] and len(rows)==6 and len(found)==3
    assert [(s,scope) for s,scope,_ in calls]==[(42,'whole_article'),(42,'patient_sections'),
        (43,'patient_sections'),(43,'whole_article'),(44,'whole_article'),(44,'patient_sections')]
    assert {str(path) for _,_,path in calls}=={str(work/'profile'/'rosters.json')}
    assert len({r['frozen_rosters_sha256'] for r in rows})==1


def test_result_archive_excludes_source_bodies_models_and_environment(tmp_path):
    import tarfile
    campaign=prepared(tmp_path);work=Path(campaign['work'])
    for name in ('gpu.json','extraction/run/report.json','profile/counts.jsonl.gz','vision-assets/review.image',
                 'profile/sample.jsonl.gz','articles.jsonl.gz','engine/model/weight.safetensors',
                 'extraction/.venv/tool','logs/server.sif'):
        path=work/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_text('small')
    result=pilot.archive_results(campaign,tmp_path/'results.tar.gz')
    with tarfile.open(result['output']) as archive:
        names=set(archive.getnames())
    assert {'gpu.json','extraction/run/report.json','profile/counts.jsonl.gz','vision-assets/review.image'} <= names
    assert not {'profile/sample.jsonl.gz','articles.jsonl.gz','engine/model/weight.safetensors',
                'extraction/.venv/tool','logs/server.sif'} & names
    with pytest.raises(ValueError,match='new archive'):
        pilot.archive_results(campaign,tmp_path/'results.tar.gz')


@pytest.mark.asyncio
async def test_live_discovery_failure_keeps_fixed_roster_extraction_trials(tmp_path,monkeypatch):
    campaign=fixed_campaign(tmp_path);work=Path(campaign['work'])
    (work/'profile').mkdir();(work/'profile'/'rosters.json').write_text('{}')
    called=[]
    async def discover(*args,**kwargs):
        raise RuntimeError('discovery failed')
    async def run(*args,**kwargs):
        called.append((kwargs['seed'],kwargs['input_scope']))
        return {'tokens':{},'task_count':1,'valid_tasks':1}
    monkeypatch.setattr('openpatients2.pilot_extract.prepare_rosters',discover,raising=False)
    monkeypatch.setattr('openpatients2.pilot_extract.run_pilot',run)
    rows, found=await pilot.correctness_runs(campaign,work/'extraction'/'layout',['mock'],65536)
    assert len(rows)==len(called)==6 and len(found)==3
    assert all(r['report']['status']=='failed' for r in found)


def test_result_archive_refuses_nested_links_before_creating_archive(tmp_path):
    campaign=prepared(tmp_path);work=Path(campaign['work'])
    (work/'extraction').mkdir();outside=tmp_path/'outside';outside.write_text('private')
    (work/'extraction'/'link').symlink_to(outside)
    output=tmp_path/'archive.tar.gz'
    with pytest.raises(ValueError,match='escaped'):
        pilot.archive_results(campaign,output)
    assert not output.exists() and outside.read_text()=='private'


@pytest.mark.asyncio
async def test_correctness_scores_saved_sources_outputs_and_discovery_and_renders_summary(tmp_path,monkeypatch):
    from test_corpus_fidelity import fixture
    reference, patient = fixture()
    campaign=fixed_campaign(tmp_path);work=Path(campaign['work'])
    Path(campaign['config']['fidelity_reference']).write_text(json.dumps(reference))
    (work/'profile').mkdir();(work/'profile'/'rosters.json').write_text('{}')
    async def discover(config,sample,output,endpoints,context,*,seed):
        output.mkdir(parents=True)
        path=output/'rosters.json'
        path.write_text(json.dumps({'articles':[{'article_id':'PMC1.1','text_sha256':'text',
            'roster':{'patients':[{'species':'human'}]}}]}))
        return {'outputs':{'predicted_rosters':str(path)}}
    async def run(config,sample,output,endpoints,context,arm,**kwargs):
        output.mkdir(parents=True); (output/'tasks').mkdir()
        path=output/'patients.jsonl';path.write_text(json.dumps(patient)+'\n')
        visuals=output/'visual-annotations.json';visuals.write_text('[]')
        (output/'tasks'/'observations.json').write_text(json.dumps({'task':'observations',
            'identity':{'article_id':'PMC1.1','patient_id':'p1'},'status':'valid',
            'attempts':[{'raw_candidate':patient['sections']['observations']}]}))
        return {'outputs':{'patients':str(path),'visual_annotations':str(visuals)},
            'tokens':{'all_gpus_output_tokens_per_second':100},'task_count':1,'valid_tasks':1}
    monkeypatch.setattr('openpatients2.pilot_extract.prepare_rosters',discover)
    monkeypatch.setattr('openpatients2.pilot_extract.run_pilot',run)
    rows,found=await pilot.correctness_runs(campaign,work/'extraction'/'layout',['mock'],65536)
    assert all(r['report']['fidelity']['summary']['delivered']['matched']==1 for r in rows)
    assert all(r['report']['fidelity']['summary']['first_pass']['matched']==1 for r in rows)
    assert all(r['report']['fidelity']['summary']['count_matches']==1 for r in found)
    cell={'context':65536,'prefill':32768}
    (work/'gpu.json').write_text(json.dumps({'status':'completed',
        'results':[{'cell':cell,**r} for r in rows],'discovery':found}))
    pilot.report(campaign)
    assert 'Reviewed source checklist' in (work/'SUMMARY.md').read_text()
    assert 'Independent patient discovery' in (work/'SUMMARY.md').read_text()
    assert len(list((work/'extraction'/'layout').glob('seed*/*/fidelity.json')))==9
