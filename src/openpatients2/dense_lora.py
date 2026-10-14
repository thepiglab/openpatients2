"""CPU orchestration for independent, adapter-only Glimmer LoRA experiments."""
from __future__ import annotations
import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tarfile
import time

from . import dense_benchmark as b

MODEL = 'meta-models/Muse-Glimmer-30B'
REVISION = 'a4e59da52a7bc87ae7251dd5545c0dd437c44b68'
PACKAGES = ['peft==0.21.2', 'accelerate==1.15.0', 'wandb==0.30.0']
TRIALS = [dict(name=f'r{r}-lr{label}', rank=r, lr=lr, epochs=2, seed=42)
          for r in (8, 32) for label, lr in [('5e-5', 5e-5), ('1e-4', 1e-4)]]


def prepare(root, work, hours=8, concurrency=2, max_length=16384, env_file=None):
    from .dense_gepa_next import prepare as prepare_data
    from .experiment_logging import copy_env
    if not 2 <= hours <= 24 or not 1 <= concurrency <= 4 or not 2048 <= max_length <= 32768:
        raise ValueError('Use 2–24 hours, 1–4 concurrent trials, and 2048–32768 tokens')
    prepare_data(root, work, hours=max(6, hours), metric_calls=100)
    m = b.read(work / 'manifest.json')
    m['lora'] = {'model': MODEL, 'revision': REVISION, 'trials': TRIALS,
        'hours': hours, 'concurrency': concurrency, 'max_length': max_length,
        'accumulation': 8, 'preview_examples': 8, 'max_new_tokens': 4096,
        'reasoning_strength': 'medium', 'template_date': '2026-10-13',
        'test_evaluated': False, 'scope': 'text-only supervised adapter pilot; training-inner-validation only',
        'packages': PACKAGES, 'image': b.IMAGE}
    (work / 'code/docs/DENSE_LORA.md').write_text((root / 'docs/DENSE_LORA.md').read_text())
    (work / 'runtime').mkdir(exist_ok=True)
    b.write(work / 'runtime/owner.json', {'work': str(work.resolve()), 'model': MODEL, 'revision': REVISION})
    copy_env(env_file or root / '.env', work / 'runtime/wandb.env')
    m['files'] = b.inventory(work, ['code', 'inputs', 'reference'])
    b.write(work / 'manifest.json', m)
    b.write(work / 'prepared.json', {'manifest_sha256': b.sha(work / 'manifest.json')})
    return {'work': str(work), 'trials': TRIALS, 'concurrency': concurrency}


def container_command(work, args, gpu=False, python=None):
    command = ['apptainer', 'exec', '--cleanenv', '--bind', f'{work}:{work}',
               '--env', 'PYTHONDONTWRITEBYTECODE=1', '--env', 'TOKENIZERS_PARALLELISM=false',
               '--env', 'OMP_NUM_THREADS=1', '--env', 'OPENBLAS_NUM_THREADS=1',
               '--env', 'HF_HUB_OFFLINE=1', '--env', 'TRANSFORMERS_OFFLINE=1',
               '--env', f'HF_HOME={work}/runtime/hf-cache']
    if gpu:
        if not os.environ.get('CUDA_VISIBLE_DEVICES') or not os.environ.get('SLURM_JOB_ID'):
            raise ValueError('Training requires a Slurm GPU allocation')
        command += ['--nv', '--env', 'CUDA_VISIBLE_DEVICES=' + os.environ['CUDA_VISIBLE_DEVICES']]
    for key in ('SLURM_JOB_ID', 'SLURM_JOB_END_TIME'):
        if os.environ.get(key): command += ['--env', key + '=' + os.environ[key]]
    return command + [str(work / 'runtime/training.sif'),
                      python or str(work / 'runtime/train-venv/bin/python'), *args]


def setup(work):
    b.check_snapshot(work)
    from huggingface_hub import snapshot_download
    runtime = work / 'runtime'
    os.environ['HF_HOME'] = str(runtime / 'hf-cache')
    snapshot_download(MODEL, revision=REVISION, local_dir=runtime / 'base',
        cache_dir=runtime / 'hf-cache', allow_patterns=['*.json', '*.jinja', '*.safetensors', '*.model', '*.txt'])
    config = b.read(runtime / 'base/config.json')
    if config.get('quantization_config') or config.get('architectures') != ['MuseGlimmerForConditionalGeneration']:
        raise ValueError('Expected native BF16 Glimmer checkpoint, not the inference FP8 model')
    if b.sha(runtime / 'base/chat_template.jinja') != b.TEMPLATE_SHA:
        raise ValueError('Native template differs from the audited template')
    sif = runtime / 'training.sif'
    subprocess.run(['apptainer', 'pull', str(sif), b.IMAGE], check=True)
    subprocess.run(container_command(work, ['-m', 'venv', '--system-site-packages', str(runtime/'train-venv')], python='/usr/bin/python3'), check=True)
    # Preserve the container's torch/transformers stack while resolving additions
    # on CPU. The full resolved environment is exported for reproducibility.
    probe = "import importlib.metadata as m; print('\\n'.join(n+'=='+m.version(n) for n in ['torch','transformers','numpy','tokenizers','safetensors']))"
    versions = subprocess.run(container_command(work, ['-c', probe]), check=True, text=True, capture_output=True).stdout
    (runtime/'constraints.txt').write_text(versions)
    subprocess.run(container_command(work, ['-m','pip','install','--constraint',str(runtime/'constraints.txt'),*PACKAGES]), check=True)
    subprocess.run(container_command(work, [str(work/'code/scripts/benchmark_dense_lora.py'),'tokenize','--work',str(work)]), check=True)
    subprocess.run(container_command(work, [str(work/'code/scripts/benchmark_dense_lora.py'),'log-preflight','--work',str(work)]), check=True, timeout=120)
    freeze = subprocess.run(container_command(work, ['-m','pip','freeze']), check=True, text=True, capture_output=True).stdout
    (work/'results').mkdir(exist_ok=True)
    (work/'results/training-environment.txt').write_text(freeze)
    files = {}
    for path in [sif, *[p for p in (runtime/'base').rglob('*') if p.is_file() and '.cache' not in p.parts]]:
        files[str(path.relative_to(work))] = {'sha256': b.sha(path), 'bytes': path.stat().st_size, 'mtime_ns': path.stat().st_mtime_ns}
    b.write(work/'runtime.json', {'ready': True, 'files': files, 'model': MODEL, 'revision': REVISION,
        'tokenized_files': b.inventory(work, ['tokenized', 'tokenizer']), 'packages': PACKAGES})
    return {'ready': True}


def tokenize_example(tokenizer, row, plan):
    kwargs = dict(tokenize=False, reasoning_strength=plan['reasoning_strength'], current_date=plan['template_date'])
    prompt = tokenizer.apply_chat_template(row['prompt'], add_generation_prompt=True, **kwargs)
    text = tokenizer.apply_chat_template(row['prompt'] + row['completion'], add_generation_prompt=False, **kwargs)
    if not text.startswith(prompt): raise ValueError('Native completion does not extend generation prefix')
    encoded = tokenizer(text, add_special_tokens=False, return_offsets_mapping=True)
    ids, offsets = encoded['input_ids'], encoded['offset_mapping']
    # Mask any token straddling the prompt/completion boundary. No guessed token
    # counts and no answer truncation. Routing prefix + answer + EOT are trained.
    labels = [token if start >= len(prompt) and end > start else -100 for token,(start,end) in zip(ids,offsets)]
    if not any(x != -100 for x in labels): raise ValueError('No supervised completion tokens')
    if len(ids) > plan['max_length']: raise ValueError('overlength_no_truncation')
    prompt_ids = tokenizer(prompt, add_special_tokens=False)['input_ids']
    return {**{k:row[k] for k in ('article_id','patient_id','task')}, 'input_ids':ids, 'labels':labels,
            'prompt_ids':prompt_ids, 'target':row['completion'][0]['content']}


def tokenize(work):
    from transformers import AutoTokenizer, MuseGlimmerForConditionalGeneration
    import peft, wandb  # CPU import check before allocating a GPU
    plan = b.read(work/'manifest.json')['lora']
    tokenizer = AutoTokenizer.from_pretrained(work/'runtime/base', local_files_only=True)
    if not tokenizer.is_fast: raise ValueError('Need fast tokenizer offsets for exact loss masking')
    tokenizer.chat_template = (work/'runtime/base/chat_template.jinja').read_text()
    tokenizer.save_pretrained(work/'tokenizer')
    rejected, counts = [], {}
    article_sets = {}
    for split in ('train','eval'):
        source = [json.loads(l) for l in (work/f'inputs/training-data/{split}.jsonl').read_text().splitlines()]
        rows = []
        for row in source:
            try: rows.append(tokenize_example(tokenizer,row,plan))
            except ValueError as exc:
                rejected.append({k:row[k] for k in ('article_id','patient_id','task')} | {'split':split,'reason':str(exc)})
        if len(rows) < (16 if split=='train' else 4):
            b.write_rows(work/'tokenized/rejected.jsonl',rejected)
            raise ValueError('Too few complete examples after tokenization; inspect tokenized/rejected.jsonl')
        b.write_rows(work/f'tokenized/{split}.jsonl',rows)
        counts[split] = {'examples':len(rows),'articles':len({r['article_id'] for r in rows}),
                         'tokens':b.stats([len(r['input_ids']) for r in rows])}
        article_sets[split] = {r['article_id'] for r in rows}
        if split=='eval':
            # Stable, target-independent diagnostic subset, balanced by task.
            selected=[]
            for task in ('observations','medications'):
                candidates = [r for r in rows if r['task']==task]
                candidates.sort(key=lambda r:hashlib.sha256((r['article_id']+':'+r['patient_id']).encode()).hexdigest())
                selected += candidates[:plan['preview_examples']//2]
            b.write_rows(work/'tokenized/preview.jsonl',selected)
    if article_sets['train'] & article_sets['eval']: raise ValueError('Article leakage')
    b.write_rows(work/'tokenized/rejected.jsonl',rejected)
    b.write(work/'tokenized/audit.json',{'counts':counts,'length_rejected':len(rejected),'test_evaluated':False,
        'scope':'eligible 126/32 training partitions; generation preview is diagnostic only'})


def submit(work, account, qos, dry_run=False):
    plan = b.check_snapshot(work)['lora']
    if list((work/'submissions').glob('*.json')): raise ValueError('Campaign already submitted; use a new campaign')
    script = work/'code/scripts/dense_lora.sbatch'
    phases = [('setup',None),('smoke',None)] + [('train',i) for i in range(len(plan['trials']))] + [('aggregate',None)]
    commands=[]
    for phase,index in phases:
        gpu=phase in ('smoke','train')
        commands.append(['sbatch','--parsable','--hold','--no-requeue', '--nodes=1','--ntasks=1',
            f'--account={account}',f'--qos={qos}', '--partition='+('hpg-b200' if gpu else 'hpg-default'),
            '--cpus-per-task='+('6' if gpu else '4'), '--mem='+('64G' if gpu else '32G'),
            '--time='+(f'{plan["hours"]:02d}:00:00' if phase=='train' else '02:00:00' if phase=='smoke' else '04:00:00'),
            '--signal=B:TERM@180','--kill-on-invalid-dep=yes','--job-name=op2-lora-'+phase+(str(index) if index is not None else ''),
            '--output='+str(work/'logs'/f'{phase}-{index}-%j.log'),
            *(['--gres=gpu:b200:1'] if gpu else []),str(script),phase,str(work),str(index) if index is not None else '-'])
    if dry_run: return {'commands':commands,'policy':'setup -> smoke -> bounded parallel lanes -> CPU aggregate/cleanup'}
    (work/'logs').mkdir(exist_ok=True);(work/'submissions').mkdir(exist_ok=True)
    ledger=work/'submissions'/f'{time.time_ns()}.json';jobs=[]
    env={k:v for k,v in os.environ.items() if not k.startswith('SBATCH_')}
    try:
        for (phase,index),command in zip(phases,commands):
            deps=[]
            if phase=='smoke': deps=['afterok:'+jobs[0]['id']]
            if phase=='train':
                deps=['afterok:'+jobs[1]['id']]
                # Success gate for the smoke; completion gate for the lane.
                # Failed trials do not block unrelated hyperparameters.
                if index >= plan['concurrency']: deps.append('afterany:'+jobs[2+index-plan['concurrency']]['id'])
            if phase=='aggregate': deps=['afterany:'+':'.join(j['id'] for j in jobs)]
            if deps: command.insert(1,'--dependency='+','.join(deps))
            result=subprocess.run(command,check=True,text=True,capture_output=True,env=env)
            jid=result.stdout.strip().split(';')[0]
            if not jid.isdigit(): raise ValueError('Invalid sbatch job id')
            jobs.append({'id':jid,'phase':phase,'trial':index,'argv':command})
            b.write(ledger,{'jobs':jobs,'status':'held'})
        for job in reversed(jobs): subprocess.run(['scontrol','release',job['id']],check=True)
        b.write(ledger,{'jobs':jobs,'status':'released'})
    except BaseException:
        for job in jobs: subprocess.run(['scancel',job['id']],check=False)
        b.write(ledger,{'jobs':jobs,'status':'failed'})
        raise
    return {'jobs':jobs}


def check_runtime(work):
    b.check_snapshot(work)
    rt=b.read(work/'runtime.json')
    if not rt['ready']: raise ValueError('CPU setup did not complete')
    for name, info in rt['files'].items():
        p=work/name;st=p.stat()
        if st.st_size!=info['bytes'] or st.st_mtime_ns!=info['mtime_ns']: raise ValueError('Runtime changed: '+name)
    for name,digest in rt['tokenized_files'].items():
        if b.sha(work/name)!=digest: raise ValueError('Training data/tokenizer changed')


def archive(work):
    folders=('adapters','tokenizer','tokenized','results','summary','code','inputs','reference','submissions')
    files=b.inventory(work,folders)
    files.update({n:b.sha(work/n) for n in ('manifest.json','runtime.json') if (work/n).exists()})
    # Integrity is stored outside its own inventory; no secrets or base weights.
    for name in ('summary/export-integrity.json','summary/archive-verified.json','summary/cleanup.json'):
        files.pop(name,None)
    # W&B maintains convenience symlinks. Archive regular files only, never a
    # symlink pointing into credentials or another campaign.
    files = {n:d for n,d in files.items() if not (work/n).is_symlink() and
             (work/n).resolve().is_relative_to(work.resolve()) and
             not any(p == '.env' or p == 'wandb.env' or p.startswith('.env.') for p in Path(n).parts)}
    b.write(work/'summary/export-integrity.json',files)
    path=work/'results.tar.gz'
    with tarfile.open(path,'w:gz') as tf:
        for name in [*sorted(files), 'summary/export-integrity.json']:
            tf.add(work/name,arcname=work.name+'/'+name,recursive=False)
    b.checksum(path)
    # Re-read the archive and verify every recorded byte before cleanup.
    found=set()
    with tarfile.open(path) as tf:
        for member in tf:
            name=member.name.split('/',1)[-1]
            if name in files and member.isfile():
                if hashlib.file_digest(tf.extractfile(member),'sha256').hexdigest()!=files[name]: raise ValueError('Archive corruption')
                found.add(name)
    if found != set(files): raise ValueError('Archive missing files')
    return {'archive_sha256':b.sha(path),'verified_files':len(found)}


def cleanup_base(work):
    """Never touches shared caches, another campaign, or adapter outputs."""
    work=work.absolute()
    if work.is_symlink() or work.resolve()!=work: raise ValueError('Symlinked campaign')
    runtime=work/'runtime';base=runtime/'base'
    if runtime.is_symlink() or base.is_symlink(): raise ValueError('Symlinked model directory')
    owner=b.read(runtime/'owner.json')
    if owner != {'work':str(work),'model':MODEL,'revision':REVISION}: raise ValueError('Ownership mismatch')
    plan=b.read(work/'manifest.json')['lora']
    if not b.read(work/'summary/report.json')['complete']: raise ValueError('Incomplete campaign: preserve base for recovery')
    evidence=b.read(work/'summary/archive-verified.json')
    if b.sha(work/'results.tar.gz') != evidence['archive_sha256']: raise ValueError('Archive changed')
    for trial in plan['trials']:
        receipt=b.read(work/'results'/trial['name']/'receipt.json')
        if receipt.get('status')!='complete' or not receipt.get('adapter_reloaded'): raise ValueError('Unverified adapter')
        for name,digest in receipt['adapter_files'].items():
            if b.sha(work/name)!=digest: raise ValueError('Adapter changed')
    # All GPU phases hold shared locks; cleanup must acquire exclusive ownership.
    with (runtime/'model.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        if base.exists():
            if any(p.is_symlink() for p in base.rglob('*')): raise ValueError('Refuse symlink in model tree')
            shutil.rmtree(base)
    b.write(work/'summary/cleanup.json',{'base_deleted':True,'adapters_preserved':True,'revision':REVISION})


def aggregate(work):
    b.check_snapshot(work);plan=b.read(work/'manifest.json')['lora']
    trials={}
    for trial in plan['trials']:
        path=work/'results'/trial['name']/'receipt.json'
        trials[trial['name']]=b.read(path) if path.exists() else {'status':'missing'}
    complete=all(r.get('status')=='complete' and r.get('adapter_reloaded') for r in trials.values())
    from .dense_lora_scoring import score
    diagnostic=score(work)
    complete = complete and all(r['complete'] for r in diagnostic.get('runs',{}).values()) and bool(diagnostic.get('runs'))
    report={'generation_diagnostic':diagnostic,'complete':complete,'trials':trials,'test_evaluated':False,
            'scope':'SFT loss on eligible inner validation + small generation diagnostic; not full benchmark'}
    b.write(work/'summary/report.json',report)
    lines=['# Glimmer LoRA pilot','',f'Complete: {complete}. Test excluded.','',
        '| Trial | Status | Best validation loss | Seconds | Adapter reloaded |','|---|---|---|---|---|']
    for name,r in trials.items():lines.append(f'| {name} | {r["status"]} | {r.get("best_eval_loss")} | {r.get("seconds")} | {r.get("adapter_reloaded",False)} |')
    lines += ['', 'Small generation diagnostic (eight patient/task examples; failures remain misses):', '',
              '| Run | Task | F2 | Grounded F2 | Precision | Recall | Valid |', '|---|---|---|---|---|---|---|']
    for name,run in diagnostic.get('runs',{}).items():
        for task,s in run['tasks'].items():
            lines.append(f'| {name} | {task} | {s["f2"]:.3f} | {s["grounded_f2"]:.3f} | {s["precision"]:.3f} | {s["recall"]:.3f} | {s["statuses"].get("valid",0)}/{s["examples"]} |')
    lines += ['','Adapters remain under adapters/. Base weights are deleted only after all trials and archive verification succeed.',
              'Select a candidate for full extraction benchmarking; validation loss alone is not clinical performance.']
    (work/'summary/report.md').write_text('\n'.join(lines)+'\n')
    # Logging stays on CPU. Preserve scientific results if tracking is unavailable.
    if (work/'runtime/train-venv/bin/python').exists():
        try:
            subprocess.run(container_command(work,[str(work/'code/scripts/benchmark_dense_lora.py'),'log-summary','--work',str(work)]),check=True,timeout=120)
        except (OSError,subprocess.SubprocessError) as exc:
            b.write(work/'summary/tracking-error.json',{'error_type':type(exc).__name__})
    # Snapshot finished GPU/setup logs; never hash the actively written aggregate log.
    for path in (work/'logs').glob('*.log'):
        if not path.name.startswith('aggregate'):
            destination=work/'results/job-logs'/path.name;destination.parent.mkdir(parents=True,exist_ok=True)
            shutil.copyfile(path,destination)
    evidence=archive(work);b.write(work/'summary/archive-verified.json',evidence)
    if complete: cleanup_base(work)
    return report


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__);sub=parser.add_subparsers(dest='command',required=True)
    for cmd in ('prepare','submit','setup','tokenize','smoke','train','aggregate','log-summary','log-preflight','status','verify'):
        p=sub.add_parser(cmd);p.add_argument('--work',type=Path,required=True)
        if cmd=='prepare':
            p.add_argument('--root',type=Path,default=Path.cwd());p.add_argument('--hours',type=int,default=8)
            p.add_argument('--concurrency',type=int,default=2);p.add_argument('--max-length',type=int,default=16384)
            p.add_argument('--env-file',type=Path)
        if cmd=='submit':
            p.add_argument('--account',default='cai5724');p.add_argument('--qos',default='cai5724');p.add_argument('--dry-run',action='store_true')
        if cmd=='train':p.add_argument('--trial',type=int,required=True)
    a=parser.parse_args(argv);work=a.work.resolve()
    if a.command=='prepare':result=prepare(a.root.resolve(),work,a.hours,a.concurrency,a.max_length,a.env_file)
    elif a.command=='submit':result=submit(work,a.account,a.qos,a.dry_run)
    elif a.command=='setup':result=setup(work)
    elif a.command=='tokenize':result=tokenize(work)
    elif a.command in ('smoke','train'):
        from .dense_lora_train import run
        result=run(work,None if a.command=='smoke' else a.trial)
    elif a.command=='aggregate': aggregate(work);return
    elif a.command=='log-preflight':
        from .experiment_logging import start
        tracking=start(work,'logging-preflight',{'phase':'cpu-setup'})
        tracking.log({'preflight/ready':1});tracking.finish();return
    elif a.command=='log-summary':
        from .experiment_logging import log_summary
        log_summary(work);return
    elif a.command=='verify':
        for name,digest in b.read(work/'summary/export-integrity.json').items():
            if b.sha(work/name)!=digest:raise ValueError('Changed result: '+name)
        if not b.read(work/'summary/report.json')['complete']:raise ValueError('Incomplete training campaign')
        evidence=b.read(work/'summary/archive-verified.json')
        if b.sha(work/'results.tar.gz')!=evidence['archive_sha256']:raise ValueError('Archive changed')
        result={'verified':True}
    else:result={'work':str(work),'receipts':[b.read(p) for p in (work/'results').glob('*/receipt.json')]}
    print(json.dumps(result,indent=2))
