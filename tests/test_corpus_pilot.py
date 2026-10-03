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
