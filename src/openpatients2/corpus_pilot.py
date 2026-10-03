"""Two explicitly separated Slurm phases: bounded CPU sources, then GPU sample.

No whole-corpus inference. Model/container acquisition and deletion are CPU jobs;
the only GPU allocation is one eight-B200 node running already prepared inputs.
"""
from __future__ import annotations

import argparse
import asyncio
import copy
import json
import os
from pathlib import Path
import signal
import time

import yaml

from .corpus_stats import sha256
from .data import write_json
from .provenance import json_digest

PATH_KEYS = ('source_config','engine_config','extraction_config','tokenizer_metadata','chat_template')
CPU_RESOURCES = {'cpu':(32,'64G','06:00:00'), 'setup':(8,'32G','04:00:00'),
                 'download':(8,'32G','08:00:00'), 'cleanup':(2,'4G','01:00:00'),
                 'report':(2,'4G','01:00:00')}
CPU_RESOURCES['source-cleanup']=(2,'4G','01:00:00')


def read_json(path):
    return json.loads(Path(path).read_text())


def prepare(root, work, config_path):
    root, work = Path(root).resolve(), Path(work).resolve()
    if work.exists(): raise ValueError('Use a new work directory; pilot outputs are never overwritten')
    config = yaml.safe_load(Path(config_path).read_text())
    for key in PATH_KEYS:
        config[key] = str((root / config[key]).resolve())
        if not Path(config[key]).is_file(): raise ValueError('Missing pilot input: '+key)
    if not 1 <= config['sample_size'] <= 48: raise ValueError('Inference sample must contain at most 48 articles')
    if not 1 <= config['workers'] <= 32 or not 1 <= config['max_figures'] <= 12:
        raise ValueError('Pilot CPU/figure limits exceeded')
    if config['arms'] != ['direct','targeted']: raise ValueError('Keep independent matched direct/targeted arms')
    if not 1 <= len(config['matrix']) <= 4: raise ValueError('Bound the GPU matrix to at most four cells')
    for cell in config['matrix']:
        if cell['context'] not in {32768,65536} or cell['prefill'] not in {8192,32768}:
            raise ValueError('Only explicitly validated context/prefill candidates are allowed in this pilot')
    if len({(c['context'],c['prefill']) for c in config['matrix']}) != len(config['matrix']):
        raise ValueError('Duplicate GPU matrix cells')
    # Enforce source/network caps before scheduling any job.
    from .acquisition import AcquisitionConfig
    AcquisitionConfig.model_validate(yaml.safe_load(Path(config['source_config']).read_text()))
    from .pilot_extract import load_pilot_config
    load_pilot_config(config['extraction_config'])
    work.mkdir(parents=True); (work/'logs').mkdir()
    files = sorted((root/'src/openpatients2').rglob('*.py')) + [root/'uv.lock',root/'pyproject.toml',root/'scripts/corpus_pilot.sbatch']
    files += [Path(config[k]) for k in PATH_KEYS]
    files += sorted((root/'configs/hipergator/glimmer').glob('*'))
    manifest = {str(p):sha256(p) for p in files if p.is_file()}
    campaign = {'version':'corpus-pilot/1','root':str(root),'work':str(work),'config':config,
                'config_sha256':json_digest(config),'runtime':manifest,'created_unix':time.time()}
    write_json(work/'pilot.json',campaign)
    write_json(work/'source-owner.json',{'work':str(work),'config_sha256':campaign['config_sha256'],
                                       'scope':'corpus-pilot-sources/1'})
    return campaign


def load(work, *, check_runtime=True):
    work = Path(work).resolve(); campaign = read_json(work/'pilot.json')
    if campaign['work'] != str(work) or campaign['config_sha256'] != json_digest(campaign['config']):
        raise ValueError('Campaign identity/configuration changed')
    for path, digest in campaign['runtime'].items() if check_runtime else ():
        if sha256(path) != digest: raise ValueError('Pilot runtime changed: '+path)
    return campaign


def verify_cpu(campaign):
    work = Path(campaign['work']); ready = read_json(work/'cpu.json')
    if ready['status'] != 'ready': raise ValueError('CPU preparation is not ready; inspect cpu.json first')
    for path, digest in ready['hashes'].items():
        local = (work/path).resolve()
        trusted_external = campaign['runtime'].get(str(local)) == digest
        if (not local.is_relative_to(work) and not trusted_external) or sha256(local) != digest:
            raise ValueError('Prepared CPU input changed: '+path)
    return ready


def job_command(campaign, phase, dependency=None):
    config, root, work = campaign['config'], campaign['root'], Path(campaign['work'])
    cpus, mem, duration = (32,'250G','12:00:00') if phase=='gpu' else CPU_RESOURCES[phase]
    args = ['sbatch','--parsable','--nodes=1','--ntasks=1','--no-requeue',
        '--account='+config['account'],'--qos='+config['qos'],'--chdir='+root,
        '--cpus-per-task='+str(cpus),'--mem='+mem,'--time='+duration,
        '--job-name=op2-pilot-'+phase,'--output='+str(work/'logs'/f'{phase}-%j.log'),
        '--partition='+config['gpu_partition' if phase=='gpu' else 'cpu_partition'],
        '--gres='+('gpu:b200:8' if phase=='gpu' else 'none')]
    if phase=='gpu': args.append('--signal=B:TERM@120')
    if dependency: args.append('--dependency='+dependency)
    return args+[str(Path(root)/'scripts/corpus_pilot.sbatch'),phase,str(work)]


def submit_chain(campaign, phases, label):
    """Preflight, hold entire chain, then release successors before parent."""
    from .hipergator import slurm_command
    work = Path(campaign['work']); ledger = work/f'{label}-jobs.json'
    if ledger.exists(): raise ValueError('This chain was already submitted/planned; inspect its ledger')
    env = {k:v for k,v in os.environ.items() if not k.startswith('SBATCH_')}
    audit=[]; jobs=[]; audit_path=work/f'{label}-slurm.json'; releasing=False
    for phase in phases:
        args=job_command(campaign,phase); args.insert(1,'--test-only')
        slurm_command(args,env,audit_path,audit)
    write_json(ledger,{'status':'submitting_held','jobs':jobs})
    try:
        previous=None
        for phase in phases:
            # Even failed setup/downloads reach a cheap GPU readiness check,
            # then the owned CPU cleanup. No failed dependency strands weights.
            args=job_command(campaign,phase,'afterany:'+previous if previous else None)
            args.insert(1,'--hold')
            result=slurm_command(args,env,audit_path,audit)
            ident=result.stdout.strip().split(';')[0]
            if not ident.isdigit(): raise RuntimeError('Ambiguous sbatch response; inspect scheduler audit before retry')
            jobs.append({'phase':phase,'id':ident,'released':False}); previous=ident
            write_json(ledger,{'status':'submitting_held','jobs':jobs})
        releasing=True
        for job in reversed(jobs):
            slurm_command(['scontrol','release',job['id']],env,audit_path,audit); job['released']=True
            write_json(ledger,{'status':'releasing','jobs':jobs})
        result={'status':'released','jobs':jobs,'work':str(work)}; write_json(ledger,result); return result
    except BaseException as exc:
        rollback='no_jobs'
        if jobs:
            try:
                if releasing: slurm_command(['scontrol','hold',*[j['id'] for j in jobs]],env,audit_path,audit)
                slurm_command(['scancel',*[j['id'] for j in jobs]],env,audit_path,audit); rollback='cancellation_requested'
            except Exception as cancel_error: rollback='manual_inspection_required: '+str(cancel_error)
        write_json(ledger,{'status':'failed','jobs':jobs,'error':str(exc),'rollback':rollback})
        raise


def prepare_engine(campaign, sif=None):
    from .hipergator import prepare as engine_prepare
    work=Path(campaign['work']); config=copy.deepcopy(yaml.safe_load(Path(campaign['config']['engine_config']).read_text()))
    # This pilot has already prepared its own sampled pixels on CPU. Do not
    # accidentally acquire the separate historical vision fixture asset corpus.
    config.pop('vision_evaluation',None)
    config['account']=campaign['config']['account']; config['qos']=campaign['config']['qos']
    source=work/'engine-input.yaml'; source.write_text(yaml.safe_dump(config))
    return engine_prepare(source,campaign['root'],work/'engine',False,sif=sif)


async def _gpu_locked(campaign):
    from . import hipergator as hpg
    from .glimmer_benchmark import serving_config, gpu_probe, server_metrics
    from .glimmer_tuning import gpu_snapshot, wait_for_release
    from .serving import ServerGroup
    from .pilot_extract import run_pilot
    verify_cpu(campaign)
    work=Path(campaign['work']); engine=hpg.read_campaign(work/'engine'); model=engine['config']['models'][0]
    # A failed download must not launch a server or make a network request.
    path=hpg.active_path(engine); hpg.check_owner(path,engine,model)
    audit=read_json(work/'engine/results'/model['name']/'download.json')
    if audit['status']!='ready' or not (path/'ready.json').is_file():
        raise ValueError('Checkpoint download is not ready')
    ready=read_json(path/'ready.json')
    if ready != {'campaign':engine['id'],'model':model['name'],'audit_sha256':json_digest(audit)}:
        raise ValueError('Checkpoint readiness receipt changed')
    for asset in audit['files']:
        local=(path/asset['folder']/asset['file']).resolve()
        if not local.is_relative_to(path) or local.stat().st_size!=asset['bytes'] or local.stat().st_mtime_ns!=asset['mtime_ns']:
            raise ValueError('Checkpoint changed after CPU verification: '+asset['file'])
    if len(os.environ.get('CUDA_VISIBLE_DEVICES','').split(','))!=8:
        raise ValueError('This pilot requires exactly eight GPUs on one node')
    started=time.monotonic(); results=[]; failed=[]
    baseline=gpu_snapshot()
    for cell in campaign['config']['matrix']:
        label=f'context{cell["context"]}-prefill{cell["prefill"]}'
        dest=work/'extraction'/label; dest.mkdir(parents=True,exist_ok=False)
        current=copy.deepcopy(engine); current['config']['max_model_len']=cell['context']
        layout={'name':label,'replicas':8,'tensor_parallel':1,'speculation':'dflash',
                'max_batched_tokens':cell['prefill'],'vision':True}
        cfg=serving_config(current,model,layout)
        gpu_probe(current,model,cfg)
        group=ServerGroup(cfg,engine['work'],str(dest/'servers'))
        write_json(work/'progress.json',{'phase':'starting','cell':label,'time_unix':time.time()})
        try:
            await group.start(current['config']['startup_timeout_seconds'])
            endpoints=[c['endpoint'] for c in group.commands]
            if campaign['config'].get('historical_regression') and cell=={'context':65536,'prefill':8192}:
                from .hpg_eval import Replay
                arm=current['config']['arms']['medium_seed42']
                replay=Replay(Path(campaign['root'])/current['config']['fixtures'],dest/'historical',model,arm,
                    endpoints,8,current['config']['request_timeout_seconds'],cell['context'])
                try:
                    await replay.run(); await replay.secondary()
                finally: await replay.close()
            before=await server_metrics(endpoints); write_json(dest/'metrics-before.json',before)
            for arm in campaign['config']['arms']:
                write_json(work/'progress.json',{'phase':'extracting','cell':label,'arm':arm,'time_unix':time.time()})
                report=await run_pilot(campaign['config']['extraction_config'],work/'profile/sample.jsonl.gz',
                    dest/arm,endpoints,cell['context'],arm,image_manifest=work/'vision-assets/manifest.json')
                results.append({'cell':cell,'arm':arm,'report':report})
            write_json(dest/'metrics-after.json',await server_metrics(endpoints))
        except Exception as exc:
            failure={'cell':cell,'error_type':type(exc).__name__,'error':str(exc)}
            failed.append(failure); write_json(dest/'FAILED.json',failure)
        finally:
            group.stop()  # synchronous; never mask startup failures with await None
            await wait_for_release(baseline,dest/'gpu-drain.json')
    result={'status':'completed' if not failed else 'partial','gpu_stage_seconds':time.monotonic()-started,
            'gpus':8,'results':results,'failures':failed,'pixel_expansion':'exact vLLM /tokenize guard',
            'medical_accuracy':'unadjudicated new sample; inspect source review forms',
            'throughput_notice':'Diverse sample includes orchestration/retries; distinct from warm repeated prompt tuning.'}
    write_json(work/'gpu.json',result)
    return result


async def gpu(campaign):
    from .hipergator import model_lock
    work=Path(campaign['work'])
    if (work/'gpu.json').exists() or (work/'extraction').exists():
        raise ValueError('GPU experiment already has outputs; use a new campaign')
    with model_lock(work/'engine'):
        return await _gpu_locked(campaign)


def report(campaign):
    work=Path(campaign['work']); cpu=read_json(work/'cpu.json') if (work/'cpu.json').exists() else {}
    run=read_json(work/'gpu.json') if (work/'gpu.json').exists() else {}
    cleanup=work/'engine/results/glimmer-fp8/cleanup.json'
    result={'cpu':cpu.get('status','missing'),'gpu':run.get('status','missing'),
            'cleanup':read_json(cleanup).get('status') if cleanup.exists() else 'missing',
            'source_cleanup':read_json(work/'source-cleanup.json').get('status') if (work/'source-cleanup.json').exists() else 'pending',
            'failures':run.get('failures',[]),'cells':run.get('results',[])}
    write_json(work/'summary.json',result)
    lines=[]
    for row in result['cells']:
        cell, measured=row['cell'],row['report']; tokens=measured['tokens']
        rate=tokens.get('all_gpus_output_tokens_per_second')
        display_rate=f'{rate:.1f}' if rate is not None else 'unavailable'
        lines.append(f'| {cell["context"]} | {cell["prefill"]} | {row["arm"]} | '
                     f'{measured["valid_tasks"]} / {measured["task_count"]-measured["valid_tasks"]} | '
                     f'{display_rate} |')
    (work/'SUMMARY.md').write_text('# Bounded PMC / Glimmer pilot\n\n'+
        f'CPU: {result["cpu"]}; GPU: {result["gpu"]}; checkpoint cleanup: {result["cleanup"]}; article cleanup: {result["source_cleanup"]}.\n\n'+
        '| Context | Prefill budget | Arm | Valid / invalid tasks | Generated tok/s, all GPUs |\n'+
        '| ---: | ---: | --- | ---: | ---: |\n'+'\n'.join(lines)+'\n\n'+
        'CPU lengths: profile/SUMMARY.md. Per-cell extraction counts and input/output token distributions: extraction/*/{direct,targeted}/report.json.\n\n'+
        'New-source clinical accuracy is pending source adjudication. Field validity and literal evidence checks are separate from entailment and recall.\n')
    return result


def stage(campaign, phase):
    work=Path(campaign['work']); destination=work/f'{phase}.json'
    try:
        if phase=='cpu':
            from .pilot_cpu import run_cpu
            return asyncio.run(run_cpu(work,campaign['config']))
        if phase=='gpu':
            def interrupted(signum,frame): raise KeyboardInterrupt('Slurm signal '+str(signum))
            signal.signal(signal.SIGTERM,interrupted)
            return asyncio.run(gpu(campaign))
        if phase=='report': return report(campaign)
        if phase=='source-cleanup':
            from .pilot_cleanup import cleanup_sources
            result=cleanup_sources(campaign)
            report(campaign)
            return result
        from . import hipergator as hpg
        if phase=='cleanup':
            # Cleanup remains possible after code/input changes. Validate the
            # saved configuration and owned checkpoint, never guess a pathname.
            engine=read_json(work/'engine/campaign.json')
            if engine['work']!=str(work/'engine') or engine['config_sha256']!=json_digest(engine['config']):
                raise ValueError('Invalid owned engine campaign')
        else:
            engine=hpg.read_campaign(work/'engine'); verify_cpu(campaign)
        model=engine['config']['models'][0]
        if phase=='setup': result=hpg.setup(engine)
        elif phase=='download':
            if read_json(work/'engine/setup.json')['status']!='ready': raise ValueError('Container setup failed')
            result=hpg.download(engine,model)
        elif phase=='cleanup': result=hpg.cleanup(engine,model)
        else: raise ValueError('Unknown stage')
        write_json(destination,result); return result
    except BaseException as exc:
        failure={'status':'failed','phase':phase,'error_type':type(exc).__name__,'error':str(exc)}
        write_json(work/f'{phase}-error.json',failure)
        if not destination.exists(): write_json(destination,failure)
        raise


def add_parser(sub):
    cmd=sub.add_parser('corpus-pilot',help='CPU sources/token-length study first; explicitly submit GPU sample later')
    actions=cmd.add_subparsers(dest='pilot_action',required=True)
    for name in ('prepare','submit-cpu'):
        p=actions.add_parser(name); p.add_argument('--work-dir',required=True)
        p.add_argument('--config',default='configs/pilot/corpus.yaml')
    p=actions.add_parser('submit-gpu'); p.add_argument('--work-dir',required=True); p.add_argument('--sif')
    p=actions.add_parser('stage'); p.add_argument('phase',choices=['cpu','setup','download','gpu','cleanup','report','source-cleanup']); p.add_argument('--work-dir',required=True)
    p=actions.add_parser('status'); p.add_argument('--work-dir',required=True)


def dispatch(args):
    root=Path(__file__).resolve().parents[2]
    if args.pilot_action in {'prepare','submit-cpu'}:
        campaign=prepare(root,args.work_dir,args.config)
        return submit_chain(campaign,['cpu'],'cpu') if args.pilot_action=='submit-cpu' else campaign
    campaign=load(args.work_dir,check_runtime=not (args.pilot_action=='stage' and args.phase in {'cleanup','source-cleanup'}))
    if args.pilot_action=='submit-gpu':
        verify_cpu(campaign); prepare_engine(campaign,args.sif)
        return submit_chain(campaign,['setup','download','gpu','cleanup','report','source-cleanup'],'gpu')
    if args.pilot_action=='stage': return stage(campaign,args.phase)
    return report(campaign)
