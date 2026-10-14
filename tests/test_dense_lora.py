import fcntl
import json
import os
from pathlib import Path
import subprocess
import tarfile
from types import SimpleNamespace

import pytest

from openpatients2 import dense_benchmark as b, dense_lora as l
from openpatients2 import experiment_logging as logging
from openpatients2.dense_lora_train import targets
from openpatients2.dense_lora_scoring import final_answer, score


def test_credentials_are_allowlisted_private_and_never_evaluated(tmp_path, monkeypatch):
    for key in logging.KEYS: monkeypatch.delenv(key, raising=False)
    env=tmp_path/'.env'; env.write_text('WANDB_API_KEY="fake-secret"\nWANDB_PROJECT=$(touch nope)\nOTHER_SECRET=hidden\n')
    monkeypatch.setenv('WANDB_ENTITY','team')
    out=tmp_path/'runtime/wandb.env'; logging.copy_env(env,out)
    assert out.stat().st_mode & 0o777 == 0o600
    assert logging.read_env(out)=={'WANDB_API_KEY':'fake-secret','WANDB_PROJECT':'$(touch nope)','WANDB_ENTITY':'team'}
    assert not (tmp_path/'nope').exists()
    with pytest.raises(FileExistsError):logging.copy_env(env,out)


@pytest.mark.parametrize('key,mode', [('', 'offline'), ('fake-secret','online')])
def test_wandb_receives_only_explicit_metrics_configuration(tmp_path,monkeypatch,key,mode):
    import sys
    (tmp_path/'runtime').mkdir();(tmp_path/'runtime/wandb.env').write_text('WANDB_API_KEY='+key)
    calls=[]
    monkeypatch.setitem(sys.modules,'wandb',SimpleNamespace(Settings=lambda **kw:kw,init=lambda **kw:calls.append(kw)))
    logging.start(tmp_path,'trial',{'rank':8})
    assert calls[0]['mode']==mode and calls[0]['config']=={'rank':8}
    assert 'fake-secret' not in str(calls)
    monkeypatch.delenv('WANDB_API_KEY',raising=False)


class Tokenizer:
    def apply_chat_template(self,messages,add_generation_prompt,**kw):
        assert kw['reasoning_strength']=='medium'
        return 'PROMPT' if add_generation_prompt else 'PROMPTanswer<EOT>'
    def __call__(self,text,**kw):
        if text=='PROMPT':return {'input_ids':[0,1]}
        return {'input_ids':[0,1,2,3], 'offset_mapping':[(0,5),(5,7),(7,12),(12,17)]}


def test_supervision_masks_prompt_and_boundary_token_without_truncating():
    row={'article_id':'PMC1.1','patient_id':'p1','task':'observations','prompt':[], 'completion':[{'content':'{}'}]}
    plan={'reasoning_strength':'medium','template_date':'2026-10-13','max_length':4}
    result=l.tokenize_example(Tokenizer(),row,plan)
    assert result['labels']==[-100,-100,2,3] and result['prompt_ids']==[0,1]
    with pytest.raises(ValueError,match='overlength'):l.tokenize_example(Tokenizer(),row,plan|{'max_length':3})


def test_targets_exclude_vision_head_and_embeddings():
    names=['model.language_model.layers.0.self_attn.q_proj','model.language_model.layers.0.mlp.down_proj',
           'model.visual.layers.0.q_proj','lm_head','model.language_model.embed_tokens']
    model=SimpleNamespace(named_modules=lambda:[(n,None) for n in names])
    assert targets(model)==names[:2]
    with pytest.raises(ValueError):targets(SimpleNamespace(named_modules=lambda:[]))


def test_atem_scoring_uses_only_complete_final_channel():
    assert final_answer(' to=self<|message|>{"bad":1}<|eom|><|start|>assistant to=user<|message|>{"ok":2}<|eot|>')=='{"ok":2}'
    for text in [' to=self<|message|>{}<|eot|>', ' to=user<|message|>{}',
                 ' to=user<|message|>{}<|eot|>extra']:
        with pytest.raises(ValueError):final_answer(text)


def manifest(work):
    b.write(work/'manifest.json',{'files':{},'lora':{'trials':l.TRIALS,'hours':8,'concurrency':2}})


def test_submission_bounds_lanes_and_cancels_only_own_jobs_on_error(tmp_path,monkeypatch):
    manifest(tmp_path);calls=[]
    def execute(cmd,**kw):
        calls.append(cmd[:])
        if cmd[0]=='sbatch':return SimpleNamespace(stdout=str(100+sum(c[0]=='sbatch' for c in calls)))
        return SimpleNamespace(stdout='')
    monkeypatch.setattr(l.subprocess,'run',execute)
    result=l.submit(tmp_path,'acct','qos');jobs=result['jobs']
    assert [j['phase'] for j in jobs]==['setup','smoke','train','train','train','train','aggregate']
    assert '--dependency=afterok:101' in jobs[1]['argv']
    assert '--kill-on-invalid-dep=yes' in jobs[1]['argv']
    assert '--dependency=afterok:102,afterany:103' in jobs[4]['argv']
    assert '--dependency=afterok:102,afterany:104' in jobs[5]['argv']
    assert '--dependency=afterany:101:102:103:104:105:106' in jobs[6]['argv']
    for job in jobs[1:6]:
        assert '--gres=gpu:b200:1' in job['argv'] and '--mem=64G' in job['argv'] and '--cpus-per-task=6' in job['argv']
    with pytest.raises(ValueError,match='already submitted'):l.submit(tmp_path,'acct','qos')
    second=tmp_path/'second';manifest(second);calls.clear()
    def fail(cmd,**kw):
        if cmd[0]=='sbatch' and sum(c[0]=='sbatch' for c in calls)==2:raise subprocess.CalledProcessError(1,cmd)
        return execute(cmd,**kw)
    monkeypatch.setattr(l.subprocess,'run',fail)
    with pytest.raises(subprocess.CalledProcessError):l.submit(second,'acct','qos')
    assert [c for c in calls if c[0]=='scancel']==[['scancel','101'],['scancel','102']]
    assert not any(c[0]=='scontrol' for c in calls)


def test_gpu_container_requires_allocation_and_has_no_key_in_args(tmp_path,monkeypatch):
    monkeypatch.delenv('SLURM_JOB_ID',raising=False)
    with pytest.raises(ValueError,match='Slurm'):l.container_command(tmp_path,[],gpu=True)
    monkeypatch.setenv('SLURM_JOB_ID','1');monkeypatch.setenv('CUDA_VISIBLE_DEVICES','0')
    monkeypatch.setenv('WANDB_API_KEY','fake-secret')
    command=l.container_command(tmp_path,['train'],gpu=True)
    assert '--nv' in command and 'CUDA_VISIBLE_DEVICES=0' in command and 'fake-secret' not in str(command)


def completed(work):
    manifest(work)
    b.write(work/'runtime/owner.json',{'work':str(work.resolve()),'model':l.MODEL,'revision':l.REVISION})
    (work/'runtime/base').mkdir();(work/'runtime/base/weights').write_text('base')
    (work/'runtime/wandb.env').write_text('WANDB_API_KEY=secret-never-export')
    for trial in l.TRIALS:
        name=trial['name'];p=work/'adapters'/name/'best/adapter_model.safetensors'
        p.parent.mkdir(parents=True);p.write_text('adapter')
        b.write(work/'results'/name/'receipt.json',{'status':'complete','adapter_reloaded':True,
            'adapter_files':{str(p.relative_to(work)):b.sha(p)}})
    b.write(work/'summary/report.json',{'complete':True})
    evidence=l.archive(work);b.write(work/'summary/archive-verified.json',evidence)


def test_archive_omits_secrets_base_symlinks_and_cleanup_preserves_adapters(tmp_path):
    completed(tmp_path)
    (tmp_path/'results/leak').symlink_to(tmp_path/'runtime/wandb.env')
    (tmp_path/'results/.env').write_text('private')
    evidence=l.archive(tmp_path);b.write(tmp_path/'summary/archive-verified.json',evidence)
    with tarfile.open(tmp_path/'results.tar.gz') as tf:
        names=tf.getnames()
        assert not any('/runtime/' in n or n.endswith('/.env') or n.endswith('/leak') for n in names)
        assert any('/adapters/' in n for n in names)
    l.cleanup_base(tmp_path)
    assert not (tmp_path/'runtime/base').exists() and len(list((tmp_path/'adapters').glob('*/best/*')))==4
    # Re-export never hashes mutable post-export receipts into its own inventory.
    l.archive(tmp_path)
    inv=b.read(tmp_path/'summary/export-integrity.json')
    assert 'summary/cleanup.json' not in inv and 'summary/archive-verified.json' not in inv


@pytest.mark.parametrize('problem',['incomplete','adapter','archive','symlink','lock'])
def test_cleanup_refuses_unverified_or_active_model(tmp_path,problem):
    completed(tmp_path);lock=None
    if problem=='incomplete':b.write(tmp_path/'summary/report.json',{'complete':False})
    if problem=='adapter':next((tmp_path/'adapters').glob('*/best/*')).write_text('changed')
    if problem=='archive':(tmp_path/'results.tar.gz').write_text('changed')
    if problem=='symlink':(tmp_path/'runtime/base/link').symlink_to(tmp_path/'manifest.json')
    if problem=='lock':
        lock=(tmp_path/'runtime/model.lock').open('a');fcntl.flock(lock,fcntl.LOCK_SH)
    try:
        with pytest.raises((ValueError,BlockingIOError)):l.cleanup_base(tmp_path)
        assert (tmp_path/'runtime/base/weights').exists()
    finally:
        if lock:lock.close()


def test_real_prepare_keeps_frozen_annotations_and_credentials_out_of_manifest(tmp_path,monkeypatch):
    for key in logging.KEYS:monkeypatch.delenv(key,raising=False)
    root=Path(__file__).resolve().parents[1];reference=root/'configs/hipergator/dense-benchmark/reference.jsonl.gz'
    digest=b.sha(reference);env=tmp_path/'private.env';env.write_text('WANDB_API_KEY=test-private-key')
    work=tmp_path/'lora';l.prepare(root,work,env_file=env)
    m=b.check_snapshot(work)
    assert digest==b.sha(reference) and m['lora']['concurrency']==2
    assert not any('runtime' in p for p in m['files']) and 'test-private-key' not in (work/'manifest.json').read_text()
    tr=[json.loads(x) for x in (work/'inputs/training-data/train.jsonl').read_text().splitlines()]
    ev=[json.loads(x) for x in (work/'inputs/training-data/eval.jsonl').read_text().splitlines()]
    assert len(tr)==374 and len(ev)==91
    assert {r['article_id'] for r in tr} <= set(m['gepa']['partition']['train'])
    assert {r['article_id'] for r in ev} <= set(m['gepa']['partition']['search_validation'])
    assert not {r['article_id'] for r in tr} & {r['article_id'] for r in ev}
    # Score real validated gold outputs, then show missing outputs remain misses.
    b.write_rows(work/'tokenized/preview.jsonl',ev[:8])
    outputs=[{k:r[k] for k in ('article_id','patient_id','task')} | {'text':' to=user<|message|>'+r['completion'][0]['content']+'<|eot|>', 'seconds':1.} for r in ev[:8]]
    b.write_rows(work/'results/baseline/generations.jsonl',outputs)
    report=score(work)
    assert report['runs']['baseline']['complete']
    assert all(t['f2']==1 for t in report['runs']['baseline']['tasks'].values())
    assert any(t['counts']['fn']>0 for t in report['runs'][l.TRIALS[0]['name']]['tasks'].values())
    # Failed campaign still exports, retains its base, and cannot verify as done.
    b.write(work/'runtime/base/marker',{'exists':True})
    assert not l.aggregate(work)['complete']
    assert (work/'results.tar.gz').exists() and (work/'runtime/base/marker').exists()
    with pytest.raises(ValueError,match='Incomplete'):l.main(['verify','--work',str(work)])
    # Completing the same scientific outputs yields a verified adapter-only
    # export and scoped cleanup; prior failed-export receipts do not break hashes.
    for trial in l.TRIALS:
        name=trial['name'];path=work/'adapters'/name/'best/adapter_model.safetensors'
        path.parent.mkdir(parents=True);path.write_text('test-adapter')
        b.write(work/'results'/name/'receipt.json',{'status':'complete','adapter_reloaded':True,
            'adapter_files':{str(path.relative_to(work)):b.sha(path)}})
        b.write_rows(work/'results'/name/'generations.jsonl',outputs)
    assert l.aggregate(work)['complete']
    assert not (work/'runtime/base').exists()
    l.main(['verify','--work',str(work)])


def test_audited_native_template_extends_prefix_with_answer_and_eot():
    from jinja2.sandbox import ImmutableSandboxedEnvironment
    root=Path(__file__).resolve().parents[1]
    path=root/'configs/hipergator/dense-benchmark/chat_template.jinja'
    assert b.sha(path)==b.TEMPLATE_SHA
    template=ImmutableSandboxedEnvironment().from_string(path.read_text())
    messages=[{'role':'system','content':'Extract clinical facts.'},{'role':'user','content':'source'}]
    kw={'reasoning_strength':'medium','current_date':'2026-10-13'}
    prompt=template.render(messages=messages,add_generation_prompt=True,**kw)
    full=template.render(messages=messages+[{'role':'assistant','content':'{"items":[]}'}],add_generation_prompt=False,**kw)
    assert full.startswith(prompt)
    assert full[len(prompt):]==' to=user<|message|>{"items":[]}<|eot|>'
