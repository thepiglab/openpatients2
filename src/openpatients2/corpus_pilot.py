"""CPU acquisition/cleanup, one-GPU family search and eight-GPU patient trials.

No whole-corpus inference. GPU jobs consume already prepared, owned inputs.
"""
from __future__ import annotations

import argparse
import asyncio
import copy
import gzip
import json
import math
import os
from pathlib import Path
import signal
import tarfile
import time

import yaml

from .corpus_stats import sha256
from .data import write_json
from .provenance import json_digest

PATH_KEYS = ('source_config','engine_config','extraction_config','tokenizer_metadata','chat_template')
FIXED_PATH_KEYS = ('fixed_source','fixed_rosters','fidelity_reference')
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
    fixed = config.get('source_mode') == 'fixed_fixture'
    overnight = config.get('experiment') in {'overnight_v3','bundle_v4'}
    paths = tuple(k for k in PATH_KEYS if k != 'source_config' or not fixed) + (FIXED_PATH_KEYS if fixed else ())
    if config.get('holdout_source_config'): paths += ('holdout_source_config',)
    for key in paths:
        config[key] = str((root / config[key]).resolve())
        if not Path(config[key]).is_file(): raise ValueError('Missing pilot input: '+key)
    if not 1 <= config['sample_size'] <= 48: raise ValueError('Inference sample must contain at most 48 articles')
    if not 1 <= config['workers'] <= 32 or not 1 <= config['max_figures'] <= 12:
        raise ValueError('Pilot CPU/figure limits exceeded')
    if fixed:
        if overnight:
            from .overnight import validate_plan
            validate_plan(config)
        else:
            refinement = config.get('experiment') == 'refinement_v2'
            expected_seeds = [42,43,44,45] if refinement else [42,43,44]
            if config['sample_size'] != 20 or config.get('seeds') != expected_seeds:
                raise ValueError('Correctness campaign requires twenty fixed articles and seeds '+','.join(map(str,expected_seeds)))
            expected_variants = [
                {'name':'whole-targeted','arm':'targeted','input_scope':'whole_article'},
                {'name':'compact-targeted','arm':'targeted','input_scope':'patient_sections'}]
            if refinement:
                expected_variants = [
                    {'name':'whole-legacy','arm':'targeted','input_scope':'whole_article','refinement_policy':'legacy','roster':'frozen'},
                    {'name':'compact-legacy','arm':'targeted','input_scope':'patient_sections','refinement_policy':'legacy','roster':'frozen'},
                    {'name':'compact-refined','arm':'targeted','input_scope':'patient_sections','refinement_policy':'source_aware','roster':'frozen'},
                    {'name':'compact-live','arm':'targeted','input_scope':'patient_sections','refinement_policy':'source_aware','roster':'live'}]
            if config.get('variants') != expected_variants:
                raise ValueError('Correctness campaign requires matched whole and compact targeted variants')
            if config['matrix'] != [{'context':65536,'prefill':32768}]:
                raise ValueError('Correctness campaign requires the pinned 64k/32768 layout')
        for key in FIXED_PATH_KEYS:
            digest = sha256(config[key])
            if config.get(key+'_sha256') not in {None,digest}:
                raise ValueError('Fixed correctness input checksum mismatch: '+key)
            config[key+'_sha256'] = digest
    elif config['arms'] != ['direct','targeted']: raise ValueError('Keep independent matched direct/targeted arms')
    if not 1 <= len(config['matrix']) <= (6 if overnight else 4): raise ValueError('Bound the GPU matrix to at most four cells')
    for cell in config['matrix']:
        if cell['context'] not in ({32768,65536,131072} if overnight else {32768,65536}) or cell['prefill'] not in {8192,32768}:
            raise ValueError('Only explicitly validated context/prefill candidates are allowed in this pilot')
    if len({(c['context'],c['prefill'],c.get('tensor_parallel',1),c.get('speculation','dflash')) for c in config['matrix']}) != len(config['matrix']):
        raise ValueError('Duplicate GPU matrix cells')
    # Enforce source/network caps before scheduling any job.
    from .acquisition import AcquisitionConfig
    if not fixed:
        AcquisitionConfig.model_validate(yaml.safe_load(Path(config['source_config']).read_text()))
    if config.get('holdout_source_config'):
        if not fixed or (not overnight and (config.get('experiment') != 'refinement_v2' or config.get('holdout_sample_size') != 24)) or (overnight and not 1<=config.get('holdout_sample_size',0)<=48):
            raise ValueError('Holdout acquisition requires the bounded refinement campaign with 24 articles')
        AcquisitionConfig.model_validate(yaml.safe_load(Path(config['holdout_source_config']).read_text()))
    from .pilot_extract import load_pilot_config
    load_pilot_config(config['extraction_config'])
    work.mkdir(parents=True); (work/'logs').mkdir()
    files = sorted((root/'src/openpatients2').rglob('*.py')) + [root/'uv.lock',root/'pyproject.toml',root/'scripts/corpus_pilot.sbatch']
    files += sorted((root/'src/openpatients2/prompts').glob('*.md'))
    files += [Path(config[k]) for k in paths]
    files += sorted((root/'configs/hipergator/glimmer').glob('*'))
    manifest = {str(p):sha256(p) for p in files if p.is_file()}
    campaign = {'version':'corpus-pilot/1','root':str(root),'work':str(work),'config':config,
                'config_sha256':json_digest(config),'runtime':manifest,'created_unix':time.time()}
    write_json(work/'pilot.json',campaign)
    write_json(work/'source-owner.json',{'work':str(work),'config_sha256':campaign['config_sha256'],
                                       'scope':'corpus-pilot-sources/1'})
    return campaign


def campaign_phases(campaign, *, include_cpu=True):
    phases=(['cpu'] if include_cpu else [])+['setup','download']
    if campaign['config'].get('separate_gepa'):
        from .pilot_recovery import bootstrap_complete
        parent=campaign['config'].get('resume_from')
        if not parent or not bootstrap_complete(parent,campaign['config']): phases.append('bootstrap')
        phases.append('gepa')
    return phases+['gpu','cleanup','report','source-cleanup']


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
    from .pilot_extract import source_gate, load_pilot_config
    from .data import read_jsonl
    bounds = load_pilot_config(campaign['config']['extraction_config'])
    for relative in ('profile/sample.jsonl.gz', 'holdout/profile/sample.jsonl.gz'):
        source = work/relative
        if not source.exists(): continue
        rows = list(read_jsonl(source))
        if not rows: raise ValueError('Prepared source sample is empty: '+relative)
        for article in rows:
            errors = source_gate(article, bounds)
            if errors: raise ValueError('Prepared source packet failed gates: '+article.get('article_id','unknown')+': '+','.join(errors))
    return ready


def job_command(campaign, phase, dependency=None):
    config, root, work = campaign['config'], campaign['root'], Path(campaign['work'])
    gpu_phase=phase in {'gpu','bootstrap','gepa'}
    cpus, mem, duration = ((16,'96G','03:00:00') if phase=='gepa' else
        (32,'250G','12:00:00')) if gpu_phase else CPU_RESOURCES[phase]
    if phase=='gepa' and config.get('gepa_profiles'): duration='04:00:00'
    if phase=='gepa' and config.get('joint_gepa_in_separate_stage'):
        family_seconds=sum(p['seconds'] for p in config.get('gepa_profiles',[])) or config['gepa_seconds']
        hours=max(int(duration.split(':')[0]),math.ceil((family_seconds+config['joint_gepa_seconds']+1800)/3600))
        duration=f'{hours:02d}:00:00'
    args = ['sbatch','--parsable','--nodes=1','--ntasks=1','--no-requeue',
        '--account='+config['account'],'--qos='+config['qos'],'--chdir='+root,
        '--cpus-per-task='+str(cpus),'--mem='+mem,'--time='+duration,
        '--job-name=op2-pilot-'+phase,'--output='+str(work/'logs'/f'{phase}-%j.log'),
        '--partition='+config['gpu_partition' if gpu_phase else 'cpu_partition'],
        '--gres='+('gpu:b200:1' if phase=='gepa' else 'gpu:b200:8' if gpu_phase else 'none')]
    if gpu_phase: args.append('--signal=B:TERM@120')
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


async def correctness_runs(campaign, destination, endpoints, context):
    """Discover per seed; condition every comparison on one reviewed roster."""
    from .pilot_extract import prepare_rosters, run_pilot
    from .corpus_fidelity import evaluate
    work = Path(campaign['work']); config = campaign['config']
    sample = work/'profile/sample.jsonl.gz'; frozen = work/'profile/rosters.json'
    frozen_hash = sha256(frozen); results = []; discovery = []
    from .pilot_extract import load_pilot_config
    extraction_config = load_pilot_config(config['extraction_config']).model_dump()
    for index, seed in enumerate(config['seeds']):
        base = Path(destination)/f'seed{seed}'
        try:
            discovery_config = ({**extraction_config,'refinement_policy':'source_aware'}
                if config.get('experiment') == 'refinement_v2' else config['extraction_config'])
            roster_report = await prepare_rosters(discovery_config, sample, base/'discovery',
                endpoints, context, seed=seed)
        except Exception as exc:
            # The reviewed roster is independent of live discovery. A failed
            # discovery trial must not suppress extraction or pixel evaluation.
            roster_report = {'status':'failed','error_type':type(exc).__name__,'error':str(exc)}
            write_json(base/'discovery'/'FAILED.json',roster_report)
        predicted = roster_report.get('outputs', {}).get('predicted_rosters')
        if predicted:
            try:
                score = evaluate(config['fidelity_reference'], [], discovery=predicted)
                write_json(base/'discovery'/'fidelity.json', score)
                roster_report['fidelity'] = score['discovery']
            except Exception as exc:
                roster_report.update(status='failed',error='Discovery scoring failed: '+str(exc))
        discovery.append({'seed':seed, 'report':roster_report})
        variants = config['variants'] if index % 2 == 0 else list(reversed(config['variants']))
        if config.get('experiment') == 'refinement_v2':
            # Four seeds: each arm occupies every execution position once.
            variants = config['variants'][index:] + config['variants'][:index]
        for order, variant in enumerate(variants):
            if sha256(frozen) != frozen_hash:
                raise ValueError('Hand-reviewed frozen rosters changed during correctness run')
            write_json(work/'progress.json',{'phase':'extracting','seed':seed,'variant':variant['name'],
                'input_scope':variant['input_scope'],'order':order,'time_unix':time.time()})
            try:
                trial_config = {**extraction_config,
                    'refinement_policy': variant.get('refinement_policy', extraction_config['refinement_policy'])}
                if config.get('reset_between_trials'):
                    cache = await trial_warmup(endpoints, trial_config, seed)
                    write_json(base/(variant['name']+'-warmup.json'), cache)
                measured = await run_pilot(trial_config, sample, base/variant['name'], endpoints,
                    context, variant['arm'], image_manifest=work/'vision-assets/manifest.json',
                    frozen_rosters=frozen if variant.get('roster','frozen') == 'frozen' else None,
                    input_scope=variant['input_scope'], seed=seed)
                if config.get('reset_between_trials'): measured['warmup'] = cache
            except Exception as exc:
                measured = {'status':'failed','error_type':type(exc).__name__,'error':str(exc),
                            'tokens':{},'valid_tasks':0,'task_count':0}
                write_json(base/variant['name']/'FAILED.json',measured)
            if measured.get('outputs'):
                try:
                    trial = base/variant['name']
                    tasks = [read_json(p) for p in sorted((trial/'tasks').glob('*.json'))]
                    score = evaluate(config['fidelity_reference'], measured['outputs']['patients'],
                        task_rows=tasks, visual_rows=measured['outputs']['visual_annotations'],
                        discovery=measured['outputs'].get('predicted_rosters') if variant.get('roster')=='live' else None)
                    write_json(trial/'fidelity.json', score)
                    measured['fidelity'] = score
                    measured['outputs']['fidelity'] = str(trial/'fidelity.json')
                    write_json(trial/'report.json', measured)
                except Exception as exc:
                    measured.update(status='failed',error='Source-fidelity scoring failed: '+str(exc))
                    write_json(base/variant['name']/'SCORING_FAILED.json',{'error':str(exc)})
            results.append({'arm':variant['name'],'seed':seed,'input_scope':variant['input_scope'],
                'order':order,'refinement_policy':trial_config['refinement_policy'],
                'roster_conditioning':variant.get('roster','frozen'),
                'frozen_rosters_sha256':frozen_hash if variant.get('roster','frozen') == 'frozen' else None,
                'report':measured})
    return results, discovery


async def trial_warmup(endpoints, config, seed):
    """Reset prefixes where supported, then equally warm every replica.

    Failures remain visible; unavailable resets do not prevent a quality trial
    or masquerade as a controlled throughput comparison. Calls are excluded
    from extraction usage/wall timers and have a strict small output cap.
    """
    import httpx
    async with httpx.AsyncClient(timeout=120) as http:
        async def one(endpoint):
            base = endpoint.removesuffix('/v1').rstrip('/')
            result = {'endpoint': endpoint, 'prefix_reset': False, 'warmup_completed': False}
            try:
                response = await http.post(base+'/reset_prefix_cache')
                result['prefix_reset'] = response.is_success
                if response.is_success and response.content:
                    body = response.json()
                    result['prefix_reset'] = (body is True or (isinstance(body,dict) and body.get('success') is True))
                result['reset_http_status'] = response.status_code
                response = await http.post(endpoint.rstrip('/')+'/chat/completions', json={
                    'model': config['served_model'], 'messages':[{'role':'user','content':'Return only {"ready":true}.'}],
                    'max_tokens': 128, 'temperature': 1, 'top_p': .95, 'top_k': 64, 'seed':seed,
                    'chat_template_kwargs':{'reasoning_strength':config.get('reasoning_strength','medium')}})
                response.raise_for_status(); result['warmup_completed'] = True
            except (httpx.HTTPError, ValueError) as exc: result['error'] = str(exc)
            return result
        rows = await asyncio.gather(*(one(endpoint) for endpoint in endpoints))
    return {'replicas':rows, 'controlled_prefix_reset':all(r['prefix_reset'] for r in rows),
            'all_replicas_warmed':all(r['warmup_completed'] for r in rows),
            'notice':'Equal bounded startup warmup; not a saturated throughput tuning experiment.'}


async def prepare_cpu_campaign(campaign):
    """Prepare regressions and a disjoint small new-source sample, on CPU only."""
    from .pilot_cpu import run_cpu
    from .data import read_jsonl
    work = Path(campaign['work']); config = campaign['config']
    if config.get('resume_from'):
        from .pilot_recovery import recover_cpu
        return recover_cpu(campaign)
    result = await run_cpu(work, config)
    if config.get('experiment')=='bundle_v4':
        import shutil
        benchmark=work/'benchmark';benchmark.mkdir()
        for key,name in [('fixed_source','articles.jsonl.gz'),('fixed_rosters','rosters.json'),('fidelity_reference','reference.json')]:
            target=benchmark/name;shutil.copyfile(config[key],target)
            result['hashes'][str(target.relative_to(work))]=sha256(target)
        write_json(work/'cpu.json',result)
    if not config.get('holdout_source_config'): return result
    child = work/'holdout'
    try:
        if child.exists(): raise ValueError('Existing holdout folder refused')
        child.mkdir()
        write_json(child/'source-owner.json', {'work':str(child),'config_sha256':campaign['config_sha256'],
                                             'scope':'corpus-pilot-holdout/1'})
        settings = {**config, 'source_mode':'acquisition', 'source_config':config['holdout_source_config'],
                    'sample_size':config['holdout_sample_size'], 'seed':5725,
                    'fixed_source':None, 'fixed_source_sha256':None, 'fixed_rosters':None,
                    'fixed_rosters_sha256':None, 'fidelity_reference':None, 'fidelity_reference_sha256':None,
                    'prioritize_reference_figures':False}
        holdout = await run_cpu(child, settings)
        if holdout['status'] != 'ready': raise ValueError('No eligible holdout sample; inspect holdout/cpu.json')
        excluded = {a['pmcid'] for a in read_jsonl(work/'profile/sample.jsonl.gz')}
        sample = child/'profile/sample.jsonl.gz'; candidates = list(read_jsonl(sample))
        selected = [a for a in candidates if a['pmcid'] not in excluded]
        if not selected: raise ValueError('Holdout sample overlaps the regression articles entirely')
        # Keep only the small selected review surface, including cases where
        # discovery finds no patients. Raw acquisition/download trees are still
        # deleted; these immutable result inputs let reviewers check omissions.
        review = {'schema_version':'review-source-sample/2','source_set':'heldout_unadjudicated',
            'clinical_adjudication':'pending','articles':selected}
        review_bytes = json.dumps(review,ensure_ascii=False).encode()
        if len(review_bytes) > 32_000_000: raise ValueError('Selected source review snapshot exceeds 32 MB')
        review_path = work/'review-source-sample.json'
        if review_path.exists(): raise ValueError('Existing source review snapshot refused')
        review_path.write_bytes(review_bytes)
        result['hashes']['review-source-sample.json'] = sha256(review_path)
        with gzip.open(sample, 'wt', encoding='utf-8') as stream:
            for article in selected: stream.write(json.dumps(article,ensure_ascii=False)+'\n')
        holdout['hashes']['profile/sample.jsonl.gz'] = sha256(sample)
        holdout['sample_size'] = len(selected)
        selected_ids = {a['article_id'] for a in selected}
        manifest_path = child/'vision-assets/manifest.json'
        manifest = read_json(manifest_path)
        manifest['figures'] = [row for row in manifest['figures'] if row['article_id'] in selected_ids]
        manifest['selection_after_overlap_exclusion'] = True
        write_json(manifest_path,manifest)
        holdout['hashes']['vision-assets/manifest.json'] = sha256(manifest_path)
        holdout['inference_selection'] = {'excluded_pmcids':sorted(excluded),
            'removed_overlap':len(candidates)-len(selected),'selected_articles':len(selected),
            'review_status':'unadjudicated new-source stress sample, not new accuracy gold'}
        write_json(child/'cpu.json',holdout)
        result['holdout'] = {'status':'ready','selected_articles':len(selected),
                            'cpu_report':'holdout/cpu.json','review_status':'source_adjudication_pending'}
        for path, digest in holdout['hashes'].items():
            absolute = (child/path).resolve()
            result['hashes'][str(absolute.relative_to(work)) if absolute.is_relative_to(work) else str(absolute)] = digest
        result['hashes']['holdout/cpu.json'] = sha256(child/'cpu.json')
        result['hashes']['holdout/source-owner.json'] = sha256(child/'source-owner.json')
        write_json(work/'cpu.json',result)
        return result
    except BaseException as exc:
        result.update(status='failed',holdout={'status':'failed','error':str(exc)})
        write_json(work/'cpu.json',result)
        raise


async def _gpu_locked(campaign, *, bootstrap_only=False):
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
    started=time.monotonic(); results=[]; failed=[]; discovery=[]
    baseline=gpu_snapshot()
    overnight=campaign['config'].get('experiment') in {'overnight_v3','bundle_v4'}
    deadline=started+campaign['config'].get('gpu_budget_seconds',36000)
    deferred=[]
    cells=campaign['config']['matrix'][:1] if bootstrap_only else campaign['config']['matrix']
    status_path=work/('bootstrap.json' if bootstrap_only else 'gpu.json')
    for cell in cells:
        label=f'context{cell["context"]}-prefill{cell["prefill"]}'
        if overnight:
            label+=f'-tp{cell.get("tensor_parallel",1)}-'+(cell.get('speculation') or 'ordinary')
            if time.monotonic()+900>=deadline:
                deferred.append({'cell':cell,'reason':'GPU deadline reserve'}); continue
        dest=work/'extraction'/label
        dest.mkdir(parents=True,exist_ok=bool(campaign['config'].get('separate_gepa') or campaign['config'].get('resume_from')))
        current=copy.deepcopy(engine); current['config']['max_model_len']=cell['context']
        layout={'name':label,'replicas':8,'tensor_parallel':1,'speculation':'dflash',
                'max_batched_tokens':cell['prefill'],'vision':True}
        if overnight:
            layout.update(tensor_parallel=cell.get('tensor_parallel',1),
                replicas=8//cell.get('tensor_parallel',1),speculation=cell.get('speculation'),
                prefix_cache=cell.get('prefix_cache',True))
        cfg=serving_config(current,model,layout)
        if overnight: cfg.environment['VLLM_SERVER_DEV_MODE']='1'
        gpu_probe(current,model,cfg)
        log_folder=dest/('bootstrap-servers' if bootstrap_only else 'servers')
        if log_folder.exists():
            number=0
            while log_folder.with_name(log_folder.name+f'-{number}').exists(): number+=1
            log_folder=log_folder.with_name(log_folder.name+f'-{number}')
        group=ServerGroup(cfg,engine['work'],str(log_folder))
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
            if overnight:
                from .overnight import run_trials
                rows,discovered,skipped=await run_trials(campaign,dest,endpoints,cell['context'],cell,
                    min(deadline,time.monotonic()+cell.get('budget_seconds',36000)),bootstrap_only=bootstrap_only)
                results.extend(rows); discovery.extend(discovered); deferred.extend(skipped)
                failed.extend({'cell':cell,'seed':row['seed'],'arm':row['arm'],'error':row['report'].get('error','Trial failed')}
                    for row in rows if row['report'].get('status')=='failed')
            elif campaign['config'].get('source_mode') == 'fixed_fixture':
                rows, discovered = await correctness_runs(campaign,dest,endpoints,cell['context'])
                results.extend({'cell':cell,**row} for row in rows)
                discovery.extend({'cell':cell,**row} for row in discovered)
                failed.extend({'cell':cell,'seed':row['seed'],'arm':row.get('arm','discovery'),
                    'error':row['report'].get('error','Correctness trial failed')}
                    for row in [*rows,*discovered] if row['report'].get('status') == 'failed')
                if campaign['config'].get('holdout_source_config'):
                    from .pilot_extract import load_pilot_config
                    sample = work/'holdout/profile/sample.jsonl.gz'
                    base_config = load_pilot_config(campaign['config']['extraction_config']).model_dump()
                    for index, seed in enumerate((42,43)):
                        policies = ['legacy','source_aware'] if index == 0 else ['source_aware','legacy']
                        for order, policy in enumerate(policies):
                            name = 'holdout-'+policy
                            trial = dest/'holdout'/f'seed{seed}'/name
                            write_json(work/'progress.json',{'phase':'heldout_extracting','seed':seed,
                                'variant':name,'time_unix':time.time()})
                            trial_config = {**base_config,'refinement_policy':policy,'max_articles':24}
                            cache = await trial_warmup(endpoints,trial_config,seed)
                            write_json(trial.parent/(name+'-warmup.json'),cache)
                            try:
                                measured = await run_pilot(trial_config,sample,trial,endpoints,cell['context'],
                                    'targeted',input_scope='patient_sections',seed=seed,
                                    image_manifest=work/'holdout/vision-assets/manifest.json')
                                measured.update(warmup=cache,accuracy_gold='unavailable; new source adjudication pending')
                                write_json(trial/'report.json',measured)
                            except Exception as exc:
                                measured={'status':'failed','error':str(exc),'tokens':{},'valid_tasks':0,'task_count':0}
                                write_json(trial/'FAILED.json',measured)
                                failed.append({'cell':cell,'arm':name,'seed':seed,'error':str(exc)})
                            results.append({'cell':cell,'arm':name,'seed':seed,'order':order,
                                'refinement_policy':policy,'roster_conditioning':'live',
                                'input_scope':'patient_sections','source_set':'heldout_unadjudicated','report':measured})
            else:
                for arm in campaign['config']['arms']:
                    write_json(work/'progress.json',{'phase':'extracting','cell':label,'arm':arm,'time_unix':time.time()})
                    report=await run_pilot(campaign['config']['extraction_config'],work/'profile/sample.jsonl.gz',
                        dest/arm,endpoints,cell['context'],arm,image_manifest=work/'vision-assets/manifest.json')
                    results.append({'cell':cell,'arm':arm,'report':report})
            write_json(dest/'metrics-after.json',await server_metrics(endpoints))
            if overnight:
                write_json(status_path,{'status':'running','results':results,'discovery':discovery,
                    'failures':failed,'deferred':deferred,'gpu_stage_seconds':time.monotonic()-started})
        except Exception as exc:
            failure={'cell':cell,'error_type':type(exc).__name__,'error':str(exc)}
            failed.append(failure); write_json(dest/'FAILED.json',failure)
        finally:
            group.stop()  # synchronous; never mask startup failures with await None
            await wait_for_release(baseline,dest/'gpu-drain.json')
    result={'status':'completed' if not failed and not deferred else 'partial','deferred':deferred,'gpu_stage_seconds':time.monotonic()-started,
            'gpus':8,'results':results,'failures':failed,'pixel_expansion':'exact vLLM /tokenize guard',
            'medical_accuracy':('finite source-reviewed checklist; remaining claims and pixel descriptions unadjudicated'
                if campaign['config'].get('source_mode') == 'fixed_fixture' else 'unadjudicated new sample; inspect source review forms'),
            'throughput_notice':'Diverse sample includes orchestration/retries; distinct from warm repeated prompt tuning.',
            'discovery':discovery}
    write_json(status_path,result)
    return result


async def gpu(campaign):
    from .hipergator import model_lock
    work=Path(campaign['work'])
    if (work/'gpu.json').exists() or ((work/'extraction').exists() and not campaign['config'].get('separate_gepa')):
        raise ValueError('GPU experiment already has outputs; use a new campaign')
    if campaign['config'].get('separate_gepa'):
        if not (work/'prompt-optimization/report.json').exists() or not (work/'prompt-optimization/best-prompts.json').exists():
            raise ValueError('GEPA stage did not produce its final receipts; no eight-GPU servers will be started')
        if campaign['config'].get('joint_gepa_in_separate_stage'):
            joint=work/'prompt-optimization/joint-program'
            if not (joint/'report.json').exists() or not (joint/'best-prompts.json').exists():
                raise ValueError('Joint GEPA stage did not produce final receipts; no eight-GPU servers will be started')
    with model_lock(work/'engine'):
        return await _gpu_locked(campaign)


async def optimize_joint_program(campaign, config, endpoints, context, independent):
    """Reuse the one-GPU server; a failed search retains explicit original controls."""
    from .bundle_optimization import optimize_bundle
    from .prompt_optimization import COMPONENTS
    work=Path(campaign['work']); settings=campaign['config']
    destination=work/'prompt-optimization/joint-program'
    write_json(work/'progress.json',{'phase':'joint_program_gepa','allocated_gpus':1,'time_unix':time.time()})
    try:
        prompts,receipt=await asyncio.to_thread(optimize_bundle,work/'profile/sample.jsonl.gz',
            destination,config,endpoints,context,settings['fidelity_reference'],independent,
            seconds=settings['joint_gepa_seconds'],calls=settings['joint_gepa_calls'])
    except Exception as exc:
        prompts=dict.fromkeys(COMPONENTS,'')
        receipt={'status':'failed','error_type':type(exc).__name__,'error':str(exc),
            'selected_original':True,'confirmation_complete':False,'confirmation_score':None,
            'confirmation_validation_facts':None,'clinical_nonregression_gate_passed':False}
    receipt['allocated_gpus']=1
    write_json(destination/'report.json',receipt)
    write_json(destination/'best-prompts.json',prompts)
    return receipt


async def gepa_stage(campaign):
    """One B200 serves concurrent independent prompt searches; CPU acquisition is complete."""
    from . import hipergator as hpg
    from .glimmer_benchmark import serving_config, gpu_probe, server_metrics
    from .glimmer_tuning import gpu_snapshot, wait_for_release
    from .serving import ServerGroup
    from .pilot_extract import load_pilot_config
    from .pilot_recovery import bootstrap_paths, bootstrap_complete
    from .prompt_optimization import optimize_prompts
    work=Path(campaign['work']); config=campaign['config']
    verify_cpu(campaign)
    if not bootstrap_complete(work,config): raise ValueError('GEPA requires all configured bootstrap trials')
    if len(os.environ.get('CUDA_VISIBLE_DEVICES','').split(','))!=1:
        raise ValueError('GEPA reserves exactly one B200, with concurrent prompt searches')
    engine=hpg.read_campaign(work/'engine'); model=engine['config']['models'][0]
    with hpg.model_lock(work/'engine'):
        hpg.check_owner(hpg.active_path(engine),engine,model)
        audit=read_json(work/'engine/results'/model['name']/'download.json')
        expected={'campaign':engine['id'],'model':model['name'],'audit_sha256':json_digest(audit)}
        if audit['status']!='ready' or read_json(hpg.active_path(engine)/'ready.json')!=expected:
            raise ValueError('GEPA checkpoint download is not ready')
        # Check CPU-recorded sizes/mtimes without rereading model tensors on GPU.
        for asset in audit['files']:
            path=(hpg.active_path(engine)/asset['folder']/asset['file']).resolve()
            if not path.is_relative_to(hpg.active_path(engine)) or path.stat().st_size!=asset['bytes'] or path.stat().st_mtime_ns!=asset['mtime_ns']:
                raise ValueError('GEPA checkpoint changed after CPU download')
        current=copy.deepcopy(engine); current['config']['max_model_len']=config.get('gepa_context',config['matrix'][0]['context'])
        layout={'name':'gepa','replicas':1,'tensor_parallel':1,'speculation':'dflash',
                'max_batched_tokens':32768,'vision':True}
        cfg=serving_config(current,model,layout); cfg.environment['VLLM_SERVER_DEV_MODE']='1'
        gpu_probe(current,model,cfg)
        folder=work/'gepa-servers'; baseline=gpu_snapshot(expected_gpus=1); started=time.monotonic()
        group=ServerGroup(cfg,engine['work'],str(folder))
        try:
            write_json(work/'progress.json',{'phase':'gepa_starting','allocated_gpus':1,'time_unix':time.time()})
            await group.start(current['config']['startup_timeout_seconds'])
            endpoints=[c['endpoint'] for c in group.commands]
            write_json(folder/'metrics-before.json',await server_metrics(endpoints))
            base=load_pilot_config(config['extraction_config']).model_dump()
            base.update(image_manifest=str(work/'vision-assets/manifest.json'),gpus_per_endpoint=1)
            write_json(work/'progress.json',{'phase':'gepa_parallel','allocated_gpus':1,
                'parallel_searches':config.get('gepa_workers',8),'time_unix':time.time()})
            profiles=config.get('gepa_profiles') or [{'name':None,'seconds':config['gepa_seconds'],
                'calls':config['gepa_metric_calls_per_prompt'],'options':config.get('gepa_options',{})}]
            profile_reports={}
            for profile in profiles:
                destination=work/'prompt-optimization'
                if profile['name']: destination=destination/profile['name']
                prompts,report=await asyncio.to_thread(optimize_prompts,work/'profile/sample.jsonl.gz',
                    bootstrap_paths(work,config),destination,base,endpoints,cfg.max_model_len,
                    config['fidelity_reference'],seconds=profile['seconds'],calls=profile['calls'],
                    workers=config.get('gepa_workers',8),resume=destination.exists(),options=profile['options'])
                profile_reports[profile['name'] or 'default']=report
            if config.get('gepa_profiles'):
                selected=work/'prompt-optimization/clinical'
                for name in ('report.json','splits.json','components.json','best-prompts.json'):
                    write_json(work/'prompt-optimization'/name,read_json(selected/name))
                write_json(work/'prompt-optimization/profiles.json',profile_reports)
                prompts=read_json(selected/'best-prompts.json')
            joint_report=None
            if config.get('joint_gepa_in_separate_stage'):
                joint_report=await optimize_joint_program(campaign,base,endpoints,cfg.max_model_len,prompts)
            write_json(folder/'metrics-after.json',await server_metrics(endpoints))
            result={'status':'completed' if all(p['status']=='completed' for p in profile_reports.values())
                    and (joint_report is None or joint_report['status']=='completed') else 'partial',
                'profiles':{name:p['status'] for name,p in profile_reports.items()},
                'joint_program_status':joint_report['status'] if joint_report else 'not_requested',
                'allocated_gpus':1,'parallel_searches':config.get('gepa_workers',8),
                'stage_seconds':time.monotonic()-started,'prompt_report':'prompt-optimization/report.json',
                'selected_supplements':len(prompts)}
            write_json(work/'gepa.json',result); return result
        finally:
            group.stop()
            await wait_for_release(baseline,folder/'gpu-drain.json')


def report(campaign):
    work=Path(campaign['work']); cpu=read_json(work/'cpu.json') if (work/'cpu.json').exists() else {}
    run=read_json(work/'gpu.json') if (work/'gpu.json').exists() else {}
    cleanup=work/'engine/results/glimmer-fp8/cleanup.json'
    result={'cpu':cpu.get('status','missing'),'gpu':run.get('status','missing'),
            'cleanup':read_json(cleanup).get('status') if cleanup.exists() else 'missing',
            'source_cleanup':read_json(work/'source-cleanup.json').get('status') if (work/'source-cleanup.json').exists() else 'pending',
            'failures':run.get('failures',[]),'cells':run.get('results',[]),'discovery':run.get('discovery',[]),
            'source_mode':campaign['config'].get('source_mode','acquisition')}
    write_json(work/'summary.json',result)
    lines=[]; fidelity_lines=[]; discovery_lines=[]; measurement_lines=[]; timeline_lines=[]
    for row in result['cells']:
        cell, measured=row['cell'],row['report']; tokens=measured['tokens']
        rate=tokens.get('all_gpus_output_tokens_per_second')
        display_rate=f'{rate:.1f}' if rate is not None else 'unavailable'
        lines.append(f'| {cell["context"]} | {cell["prefill"]} | {row.get("seed","—")} | {row["arm"]} | '
                     f'{measured["valid_tasks"]} / {measured["task_count"]-measured["valid_tasks"]} | '
                     f'{display_rate} |')
        comparisons = measured.get('measurement_comparison', {})
        if comparisons:
            measurement_lines.append(f'| {row.get("seed","—")} | {row["arm"]} | '
                f'{comparisons.get("matched_model_fields",0)} | {comparisons.get("conflict",0)} | '
                f'{comparisons.get("source_parse_only",0)} | {comparisons.get("ambiguous_source_notation",0)} | '
                f'{comparisons.get("unresolved",0)} |')
        ordering = measured.get('relative_timelines', {})
        if ordering:
            timeline_lines.append(f'| {row.get("seed","—")} | {row["arm"]} | '
                f'{ordering.get("patients",0)} | {ordering.get("events",0)} | '
                f'{ordering.get("ordered_pairs",0)} | {ordering.get("incomparable_pairs",0)} |')
        score = measured.get('fidelity', {})
        if score:
            first = score['summary']['first_pass']; delivered = score['summary']['delivered']
            timelines = measured.get('task_statuses_by_domain', {}).get('timeline', {})
            figures = score['figures']
            def figure_counts(name):
                counts = figures[name]['summary']
                return f'{counts["matched"]}/{counts["required"]} ({counts["unscorable"]} unavailable)'
            representation = score.get('representation_aware', {})
            semantic = f'{representation.get("matched","—")}/{representation.get("required","—")}'
            fidelity_lines.append(f'| {row["seed"]} | {row["arm"]} | {first["matched"]}/{first["required"]} | '
                f'{delivered["matched"]}/{delivered["required"]} | {delivered["missing"]} / {delivered["unscorable"]} | '
                f'{delivered["forbidden_violations"]}/{delivered["forbidden"]} ({delivered["forbidden_unscorable"]} unavailable) | '
                f'{timelines.get("valid",0)}/{sum(timelines.values())} ({timelines.get("partial",0)} partial) | '
                f'{figure_counts("caption")} | {figure_counts("pixels")} | {semantic} |')
    for trial in result['discovery']:
        counts = trial['report'].get('fidelity', {}).get('summary')
        if counts:
            discovery_lines.append(f'| {trial["seed"]} | {counts["count_matches"]}/{counts["definitive_articles"]} | '
                f'{counts["missing_patients"]} | {counts["extra_patients"]} | {counts["unscorable"]} |')
    correctness = ('\n## Reviewed source checklist\n\n'
        '| Seed | Scope | First-pass matches | Strict delivered | Missing / unavailable | Forbidden hits | Valid timelines | Caption ownership | Pixel ownership | Representation-aware delivered |\n'
        '| ---: | --- | ---: | ---: | ---: | ---: | --- | --- | --- | ---: |\n' + '\n'.join(fidelity_lines) +
        '\n\nMatches are finite reference assertions, not comprehensive clinical accuracy. Missing and unavailable remain separate; unavailable forbidden checks do not establish safety. Pixel descriptions still need source/pixel adjudication.\n'
        '\n## Independent patient discovery\n\n'
        '| Seed | Correct article counts | Missing patients | Extra patients | Unavailable articles |\n'
        '| ---: | ---: | ---: | ---: | ---: |\n'+'\n'.join(discovery_lines)+
        '\n\nMatching a count does not establish matching individual identities. Full source-scoped review forms and per-domain validity counts are saved in each trial.\n') if result['source_mode']=='fixed_fixture' else ''
    (work/'SUMMARY.md').write_text('# Bounded PMC / Glimmer pilot\n\n'+
        f'CPU: {result["cpu"]}; GPU: {result["gpu"]}; checkpoint cleanup: {result["cleanup"]}; article cleanup: {result["source_cleanup"]}.\n\n'+
        '| Context | Prefill budget | Seed | Arm | Valid / invalid tasks | Generated tok/s, all GPUs |\n'+
        '| ---: | ---: | ---: | --- | ---: | ---: |\n'+'\n'.join(lines)+'\n\n'+
        correctness +
        ('## Measurement notation: parser versus LLM fields\n\n'
         '| Seed | Arm | Agreement | Conflict | Source only | Ambiguous exponent | Unresolved |\n'
         '| ---: | --- | ---: | ---: | ---: | ---: | ---: |\n'+'\n'.join(measurement_lines)+
         '\n\nSidecars preserve raw text, decimal magnitude, unit, comparator and scientific scale. '
         'Unresolved includes qualitative results and unsupported unit formats; agreement is lexical, not clinical accuracy.\n\n'
         if measurement_lines else '') +
        ('## Relative clinical sequence\n\n'
         '| Seed | Arm | Patient timelines | Events | Before/after pairs | Unknown-order pairs |\n'
         '| ---: | --- | ---: | ---: | ---: | ---: |\n'+'\n'.join(timeline_lines)+
         '\n\nRelations are source-gated model claims. Topological layers are not simultaneous visits; '
         'exact dates are optional and disconnected events remain unordered.\n\n' if timeline_lines else '') +
        'CPU lengths: profile/SUMMARY.md. Per-cell extraction counts and input/output token distributions: extraction/*/{direct,targeted}/report.json.\n\n'+
        ('Roster conditioning is saved per trial: frozen regressions and live end-to-end trials have different denominators. '
         'Strict historical patterns remain unchanged; reviewed representation alternatives are a separate score, not medical accuracy. '
         'Heldout sources have no accuracy gold yet. Warmup/reset receipts accompany each refinement trial; '
         'a failed cache reset makes speed comparisons uncontrolled.\n'
         if result['source_mode']=='fixed_fixture' else
         'New-source clinical accuracy is pending source adjudication. Field validity and literal evidence checks are separate from entailment and recall.\n'))
    if campaign['config'].get('experiment') in {'overnight_v3','bundle_v4'}:
        from .overnight import write_comparison
        result['prompt_optimization']=write_comparison(campaign,result)
        result['deferred']=run.get('deferred',[])
        if campaign['config'].get('experiment')=='bundle_v4':
            from .bundle_report import write_bundle_comparison
            result['joint_program_optimization']=write_bundle_comparison(campaign,result)
        write_json(work/'summary.json',result)
    return result


def stage(campaign, phase):
    work=Path(campaign['work']); destination=work/f'{phase}.json'
    try:
        if phase=='cpu':
            return asyncio.run(prepare_cpu_campaign(campaign))
        if phase in {'gpu','bootstrap','gepa'}:
            def interrupted(signum,frame):
                # A second TERM during process cleanup must not replace the
                # original interruption with a second teardown exception.
                signal.signal(signal.SIGTERM,signal.SIG_IGN)
                raise KeyboardInterrupt('Slurm signal '+str(signum))
            signal.signal(signal.SIGTERM,interrupted)
            async def measured_stage():
                from .gpu_telemetry import monitor
                async with monitor(work/f'{phase}-telemetry.jsonl'):
                    if phase=='gepa': return await gepa_stage(campaign)
                    if phase=='bootstrap':
                        from .hipergator import model_lock
                        with model_lock(work/'engine'):
                            return await _gpu_locked(campaign,bootstrap_only=True)
                    return await gpu(campaign)
            return asyncio.run(measured_stage())
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


def archive_results(campaign, output):
    """Export only bounded result paths; omit sources, environments and models."""
    work = Path(campaign['work']).resolve(); output = Path(output).resolve()
    if output.exists() or output.is_relative_to(work):
        raise ValueError('Use a new archive filename outside the campaign')
    names = ('pilot.json','cpu.json','gpu.json','bootstrap.json','gepa.json','recovery.json','gepa-servers',
             'bootstrap-error.json','gepa-error.json','summary.json','SUMMARY.md','COMPARISON.md','BUNDLE_COMPARISON.md','source-cleanup.json',
             'bootstrap-telemetry.jsonl','gepa-telemetry.jsonl','gpu-telemetry.jsonl',
             'source-owner.json','progress.json','review-source-sample.json','benchmark','extraction','logs','vision-assets','profile/counts.jsonl.gz',
             'profile/profile.json','profile/SUMMARY.md','profile/rosters.json',
             'profile/token-histograms.png','profile/token-histograms.svg','profile/token-histograms.json',
             'cpu-error.json','gpu-error.json','source-cleanup-error.json',
             'engine/setup.json','engine/container-python.json',
             'prompt-optimization','engine/results/glimmer-fp8/download.json','engine/results/glimmer-fp8/cleanup.json')
    if campaign['config'].get('holdout_source_config'):
        names += ('holdout/cpu.json','holdout/source-owner.json','holdout/vision-assets',
                  'holdout/profile/counts.jsonl.gz','holdout/profile/profile.json','holdout/profile/SUMMARY.md',
                  'holdout/profile/token-histograms.png','holdout/profile/token-histograms.svg',
                  'holdout/profile/token-histograms.json')
    files = []
    deduplicate = (campaign['config'].get('experiment') in {'refinement_v2','overnight_v3','bundle_v4'} and
                   (work/'gpu.json').exists() and read_json(work/'gpu.json').get('status') == 'completed')
    def redundant_attempts(path):
        task_dir = path.parent/'tasks'
        if not task_dir.is_dir() or task_dir.is_symlink(): return False
        saved = {}
        try:
            for task_path in task_dir.glob('*.json'):
                if task_path.is_symlink(): return False
                for attempt in read_json(task_path).get('attempts', []):
                    seq = attempt.get('ledger_sequence')
                    if not isinstance(seq,int) or seq in saved: return False
                    saved[seq] = json_digest(attempt)
            count = 0
            with path.open() as stream:
                for line in stream:
                    if not line.strip(): continue
                    attempt = json.loads(line); seq = attempt.get('ledger_sequence')
                    if seq not in saved or saved.pop(seq) != json_digest(attempt): return False
                    count += 1
            return count > 0 and not saved
        except (ValueError, TypeError, KeyError): return False
    patient_digests = {}
    def redundant_patient(path):
        aggregate = path.parent.parent/'patients.jsonl'
        if not aggregate.is_file() or aggregate.is_symlink(): return False
        try:
            if aggregate not in patient_digests:
                with aggregate.open() as stream:
                    patient_digests[aggregate] = {json_digest(json.loads(line)) for line in stream if line.strip()}
            return json_digest(read_json(path)) in patient_digests[aggregate]
        except (ValueError, TypeError, KeyError): return False
    for name in names:
        path = work/name
        if path.is_symlink():
            raise ValueError('Result archive refuses symlinks')
        entries = sorted(path.rglob('*')) if path.is_dir() else [path]
        for entry in entries:
            if entry.is_symlink() or not entry.resolve().is_relative_to(work):
                raise ValueError('Result archive path escaped campaign')
            if entry.is_file():
                relative = entry.relative_to(work)
                if deduplicate and relative.parts[0] == 'extraction' and (
                        (entry.name == 'attempts.jsonl' and redundant_attempts(entry)) or
                        (entry.parent.name == 'patients' and redundant_patient(entry))):
                    # Completed task JSON retains all attempts with their ledger
                    # sequence; patients.jsonl retains all individual bundles.
                    continue
                if entry.suffix == '.sif' or any(part in {'.venv','uv-cache','.cache'} for part in entry.relative_to(work).parts):
                    continue
                files.append(entry)
    total = sum(p.stat().st_size for p in files)
    if total > (32_000_000_000 if campaign['config'].get('experiment') in {'overnight_v3','bundle_v4'} else 2_000_000_000):
        raise ValueError('Result export exceeds the campaign archive size cap')
    created = False
    try:
        with output.open('xb') as handle:
            created = True
            with tarfile.open(fileobj=handle, mode='w:gz') as archive:
                for path in files:
                    archive.add(path,arcname=str(path.relative_to(work)),recursive=False)
    except BaseException:
        if created:
            output.unlink(missing_ok=True)
        raise
    return {'output':str(output),'files':len(files),'uncompressed_bytes':total,'sha256':sha256(output),
            'duplicate_attempt_and_patient_copies_omitted':deduplicate,
            'scope':'Result reports, extraction audits, logs, scalar profiles and bounded review pixels; source bodies/model caches excluded.'}


def add_parser(sub):
    cmd=sub.add_parser('corpus-pilot',help='Bounded CPU preparation, GPU evaluation, reporting and cleanup campaign')
    actions=cmd.add_subparsers(dest='pilot_action',required=True)
    for name in ('prepare','submit-cpu','submit'):
        p=actions.add_parser(name); p.add_argument('--work-dir',required=True)
        p.add_argument('--config',default='configs/pilot/corpus.yaml')
        if name=='submit': p.add_argument('--sif')
    p=actions.add_parser('submit-gpu'); p.add_argument('--work-dir',required=True); p.add_argument('--sif')
    p=actions.add_parser('submit-resume',help='Fork an ended overnight campaign and reuse completed trials/GEPA checkpoints')
    p.add_argument('--from-work-dir',required=True); p.add_argument('--work-dir',required=True); p.add_argument('--sif')
    p=actions.add_parser('stage'); p.add_argument('phase',choices=['cpu','setup','download','bootstrap','gepa','gpu','cleanup','report','source-cleanup']); p.add_argument('--work-dir',required=True)
    p=actions.add_parser('status'); p.add_argument('--work-dir',required=True)
    p=actions.add_parser('export-results'); p.add_argument('--work-dir',required=True); p.add_argument('--output',required=True)


def dispatch(args):
    root=Path(__file__).resolve().parents[2]
    if args.pilot_action=='submit-resume':
        import tempfile
        from .pilot_recovery import validate_parent
        parent=Path(args.from_work_dir).resolve(); old=load(parent,check_runtime=False)
        config=copy.deepcopy(old['config']); oldroot=Path(old['root'])
        for key in (*PATH_KEYS,*FIXED_PATH_KEYS,'holdout_source_config'):
            if config.get(key) and Path(config[key]).is_relative_to(oldroot):
                config[key]=str(root/Path(config[key]).relative_to(oldroot))
        config.update(resume_from=str(parent),separate_gepa=True,gepa_workers=8)
        with tempfile.TemporaryDirectory(prefix='op2-recovery-') as temp:
            path=Path(temp)/'config.yaml'; path.write_text(yaml.safe_dump(config))
            campaign=prepare(root,args.work_dir,path)
        validate_parent(campaign,parent)
        sif=args.sif
        if not sif and (parent/'engine/setup.json').exists():
            candidate=read_json(parent/'engine/setup.json').get('sif')
            if candidate and Path(candidate).is_file(): sif=candidate
        prepare_engine(campaign,sif)
        return submit_chain(campaign,campaign_phases(campaign),'campaign')
    if args.pilot_action in {'prepare','submit-cpu','submit'}:
        campaign=prepare(root,args.work_dir,args.config)
        if args.pilot_action=='submit':
            prepare_engine(campaign,args.sif)
            return submit_chain(campaign,campaign_phases(campaign),'campaign')
        return submit_chain(campaign,['cpu'],'cpu') if args.pilot_action=='submit-cpu' else campaign
    campaign=load(args.work_dir,check_runtime=not (args.pilot_action=='export-results' or
        (args.pilot_action=='stage' and args.phase in {'cleanup','source-cleanup'})))
    if args.pilot_action=='export-results': return archive_results(campaign,args.output)
    if args.pilot_action=='submit-gpu':
        verify_cpu(campaign); prepare_engine(campaign,args.sif)
        return submit_chain(campaign,campaign_phases(campaign,include_cpu=False),'gpu')
    if args.pilot_action=='stage': return stage(campaign,args.phase)
    return report(campaign)
