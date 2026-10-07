"""Short paired evidence-ledger pilot on independent, one-GPU Slurm jobs.

The shared checkpoint is prepared once on CPU. Workers hold shared reader locks,
use immutable model mounts, and write only their own shards. CPU aggregation keeps
the complete source-gold denominator even when a worker fails or runs out of time.
"""
from __future__ import annotations

import argparse
import asyncio
import copy
from contextlib import contextmanager
import fcntl
import gzip
import json
import os
from pathlib import Path
import signal
import shutil
import subprocess
import tarfile
import time

import yaml

from .corpus_pilot import read_json, verify_cpu
from .corpus_stats import sha256
from .data import read_jsonl, write_json
from .provenance import json_digest

ALLOCATION = {'cpus': 32, 'mem_gb': 250, 'gpus': 8}
EXPORT_MAX_BYTES = 8_000_000_000
CPU_RESOURCES = {'prepare': (32, 64, '02:00:00'), 'setup': (8, 32, '02:00:00'),
                 'download': (8, 32, '04:00:00'), 'aggregate': (2, 8, '00:30:00'),
                 'cleanup': (2, 4, '00:30:00')}
TERMINAL = {'COMPLETED', 'FAILED', 'CANCELLED', 'TIMEOUT', 'OUT_OF_MEMORY',
            'NODE_FAIL', 'PREEMPTED', 'BOOT_FAIL', 'DEADLINE', 'REVOKED'}
VARIANTS = ('live-complete', 'ledger-delta', 'ledger-audit')


def validate_plan(config):
    if config.get('account') != 'cai5724' or config.get('qos') != 'cai5724':
        raise ValueError('This pilot is bounded to the cai5724 account and QoS')
    if type(config.get('gpu_workers')) is not int or config['gpu_workers'] not in (4, 8):
        raise ValueError('Use four or eight independent one-GPU workers')
    count = config['gpu_workers']
    expected = (8, 60) if count == 4 else (4, 30)
    if (any(type(config.get(k)) is not int for k in ('worker_cpus', 'worker_mem_gb')) or
            (config.get('worker_cpus'), config.get('worker_mem_gb')) != expected):
        raise ValueError('Workers must fit the global 32 CPU / 250 GB allocation')
    if (count * config['worker_cpus'] > ALLOCATION['cpus'] or
            count * config['worker_mem_gb'] > ALLOCATION['mem_gb'] or count > ALLOCATION['gpus']):
        raise ValueError('Concurrent worker resources exceed the allocation')
    if type(config.get('gpu_minutes')) is not int or not 60 <= config['gpu_minutes'] <= 120:
        raise ValueError('GPU pilot must last 60–120 minutes including server startup')
    if config.get('source_mode') != 'fixed_fixture' or config.get('sample_size') != 31:
        raise ValueError('This architecture pilot retains all 31 fixed bundle articles')
    if config.get('seeds') != [42, 43] or config.get('variants') != list(VARIANTS):
        raise ValueError('Keep the three paired architecture arms and seeds 42,43')
    if type(config.get('workers')) is not int or not 1 <= config['workers'] <= 32:
        raise ValueError('CPU preparation must fit 32 CPUs')
    if config.get('context') != 65536 or config.get('prefill') not in (8192, 32768):
        raise ValueError('Keep the verified TP1 64K serving layout')
    if config.get('holdout_source_config') or config.get('separate_gepa'):
        raise ValueError('The short architecture pilot has no acquisition or GEPA stage')
    return True


def trial_config(campaign, variant, articles):
    from .pilot_extract import load_pilot_config, PilotConfig
    base = load_pilot_config(campaign['config']['extraction_config']).model_dump()
    if variant not in VARIANTS:
        raise ValueError('Unknown frontier variant')
    base.update(max_articles=articles, gpus_per_endpoint=1)
    if variant != 'live-complete':
        base.update(experimental_pipeline='evidence_ledger', ledger_chunk_characters=12000,
                    ledger_audit=variant == 'ledger-audit', ledger_delta_timeline=True)
        for key in ('inventory_clinical_features', 'coverage_repair', 'audit_claims',
                    'review_ordering', 'timeline_completion', 'summary_completion'):
            base[key] = False
    return PilotConfig.model_validate(base).model_dump()


def prepare(root, work, config_path, *, sif=None, gpu_workers=None, gpu_minutes=None):
    root, work = Path(root).resolve(), Path(work).resolve()
    if work.exists():
        raise ValueError('Use a new campaign directory; results are never overwritten')
    config = yaml.safe_load(Path(config_path).read_text())
    if gpu_workers is not None:
        if gpu_workers not in (4, 8):
            raise ValueError('Use four or eight independent one-GPU workers')
        config.update(gpu_workers=gpu_workers, worker_cpus=32 // gpu_workers,
                      worker_mem_gb=240 // gpu_workers)
    if gpu_minutes is not None:
        config['gpu_minutes'] = gpu_minutes
    validate_plan(config)
    paths = ('engine_config', 'extraction_config', 'tokenizer_metadata', 'chat_template',
             'fixed_source', 'fixed_rosters', 'fidelity_reference')
    for key in paths:
        path = (root / config[key]).resolve()
        if not path.is_file():
            raise ValueError('Missing frontier input: ' + key)
        config[key] = str(path)
        if key.startswith('fixed_') or key == 'fidelity_reference':
            digest = sha256(path)
            if config.get(key + '_sha256') not in (None, digest):
                raise ValueError('Fixed input checksum mismatch: ' + key)
            config[key + '_sha256'] = digest
    articles = list(read_jsonl(config['fixed_source']))
    if len(articles) != 31 or len({a['article_id'] for a in articles}) != 31:
        raise ValueError('The fixed fixture must contain exactly 31 distinct articles')
    from .bundle_quality import validate_bundle_gold
    validate_bundle_gold(read_json(config['fidelity_reference']), articles)
    campaign = {'version': 'frontier-campaign/1', 'root': str(root), 'work': str(work),
                'config': config, 'config_sha256': json_digest(config), 'created_unix': time.time()}
    for variant in VARIANTS:
        trial_config(campaign, variant, 31)
    files = sorted((root / 'src/openpatients2').rglob('*.py'))
    files += sorted((root / 'src/openpatients2/prompts').glob('*.md'))
    files += [root / 'pyproject.toml', root / 'uv.lock', root / 'scripts/frontier_pilot.sbatch']
    files += [Path(config[k]) for k in paths]
    campaign['runtime'] = {str(p): sha256(p) for p in files if p.is_file()}
    work.mkdir(parents=True)
    (work / 'logs').mkdir()
    write_json(work / 'frontier.json', campaign)
    write_json(work / 'source-owner.json', {'work': str(work), 'config_sha256': campaign['config_sha256'],
                                          'scope': 'corpus-pilot-sources/1'})
    from .ledger_evaluation import objective_adequacy
    write_json(work / 'objective-adequacy.json', objective_adequacy(read_json(config['fidelity_reference'])))
    from .corpus_pilot import prepare_engine
    prepare_engine(campaign, sif=sif)
    return campaign


def load(work, *, check_runtime=True):
    work = Path(work).resolve()
    campaign = read_json(work / 'frontier.json')
    if campaign['work'] != str(work) or campaign['config_sha256'] != json_digest(campaign['config']):
        raise ValueError('Frontier campaign identity/configuration changed')
    validate_plan(campaign['config'])
    if check_runtime:
        for path, digest in campaign['runtime'].items():
            if sha256(path) != digest:
                raise ValueError('Frontier runtime changed: ' + path)
    return campaign


def reviewed_reference(campaign):
    """Use the retained CPU review copy after cleanup; preserve pinned gold."""
    retained = Path(campaign['work']) / 'benchmark/reference.json'
    path = retained if retained.is_file() else Path(campaign['config']['fidelity_reference'])
    expected = campaign['config'].get('fidelity_reference_sha256')
    if path.is_symlink() or (expected is not None and sha256(path) != expected):
        raise ValueError('Reviewed frontier reference changed after preparation')
    return read_json(path)


def stages(campaign):
    return [('prepare', None), ('setup', None), ('download', None)] + [
        ('worker', i) for i in range(campaign['config']['gpu_workers'])] + [
        ('aggregate', None), ('cleanup', None)]


def job_command(campaign, phase, shard=None, dependency=None):
    config = campaign['config']
    if phase == 'worker':
        if type(shard) is not int or not 0 <= shard < config['gpu_workers']:
            raise ValueError('Worker shard is outside the plan')
        cpus, mem = config['worker_cpus'], config['worker_mem_gb']
        duration = f'{config["gpu_minutes"] // 60:02d}:{config["gpu_minutes"] % 60:02d}:00'
        name = f'worker-{shard:03d}'
    else:
        cpus, mem, duration = CPU_RESOURCES[phase]
        name = phase
    args = ['sbatch', '--parsable', '--nodes=1', '--ntasks=1', '--no-requeue',
            '--account=' + config['account'], '--qos=' + config['qos'],
            '--chdir=' + campaign['root'], '--cpus-per-task=' + str(cpus),
            '--mem=' + str(mem) + 'G', '--time=' + duration,
            '--partition=' + config['gpu_partition' if phase == 'worker' else 'cpu_partition'],
            '--gres=' + ('gpu:b200:1' if phase == 'worker' else 'none'),
            '--job-name=op2-frontier-' + name,
            '--output=' + str(Path(campaign['work']) / 'logs' / (name + '-%j.log'))]
    if phase == 'worker':
        args.append('--signal=B:TERM@120')
    if dependency:
        args.append('--dependency=' + dependency)
    return args + [str(Path(campaign['root']) / 'scripts/frontier_pilot.sbatch'),
                   phase, campaign['work'], str(shard) if shard is not None else '-']


def submit(campaign):
    """Preflight and hold the complete DAG before releasing its CPU root last.

    afterany deliberately reaches readiness checks after failed prerequisites.
    afterok would strand workers in DependencyNeverSatisfied and block cleanup.
    """
    from .hipergator import slurm_command
    work = Path(campaign['work'])
    ledger = work / 'frontier-jobs.json'
    if ledger.exists():
        raise ValueError('Campaign already submitted; inspect its job ledger')
    env = {k: v for k, v in os.environ.items() if not k.startswith('SBATCH_')}
    audit = []; jobs = []; releasing = False
    for phase, shard in stages(campaign):
        args = job_command(campaign, phase, shard)
        slurm_command([args[0], '--test-only', *args[1:]], env, work / 'frontier-slurm.json', audit)
    write_json(ledger, {'status': 'submitting_held', 'jobs': jobs})
    try:
        ids = {}; workers = []
        for phase, shard in stages(campaign):
            parents = ([ids['prepare']] if phase == 'setup' else [ids['setup']] if phase == 'download'
                       else [ids['download']] if phase == 'worker' else workers if phase == 'aggregate'
                       else [ids['aggregate'], ids['download'], *workers] if phase == 'cleanup' else [])
            args = job_command(campaign, phase, shard, 'afterany:' + ':'.join(parents) if parents else None)
            result = slurm_command([args[0], '--hold', *args[1:]], env, work / 'frontier-slurm.json', audit)
            ident = result.stdout.strip().split(';')[0]
            if not ident.isdigit():
                raise RuntimeError('Ambiguous sbatch response; inspect scheduler audit before retrying')
            ids[phase] = ident
            if phase == 'worker': workers.append(ident)
            jobs.append({'phase': phase, 'shard': shard, 'id': ident, 'released': False, 'command': args})
            write_json(ledger, {'status': 'submitting_held', 'jobs': jobs})
        releasing = True
        for job in reversed(jobs):
            slurm_command(['scontrol', 'release', job['id']], env, work / 'frontier-slurm.json', audit)
            job['released'] = True
            write_json(ledger, {'status': 'releasing', 'jobs': jobs})
        result = {'status': 'released', 'jobs': jobs, 'work': str(work)}
        write_json(ledger, result)
        return result
    except BaseException as exc:
        rollback = 'no_jobs'
        if jobs:
            try:
                if releasing:
                    slurm_command(['scontrol', 'hold', *[j['id'] for j in jobs]], env, work / 'frontier-slurm.json', audit)
                slurm_command(['scancel', *[j['id'] for j in jobs]], env, work / 'frontier-slurm.json', audit)
                rollback = 'cancellation_requested'
            except Exception as error:
                rollback = 'manual_inspection_required: ' + str(error)
        write_json(ledger, {'status': 'failed', 'jobs': jobs, 'error': str(exc), 'rollback': rollback})
        raise


def prepare_shards(campaign):
    """CPU-only deterministic article partitions; every shard runs every pair."""
    from .hipergator import require_cpu
    require_cpu()
    ready = verify_cpu(campaign)
    work = Path(campaign['work'])
    rows = sorted(read_jsonl(work / 'profile/sample.jsonl.gz'), key=lambda a: a['article_id'])
    if len(rows) != campaign['config']['sample_size'] or len({a['article_id'] for a in rows}) != len(rows):
        raise ValueError('CPU preparation must retain every distinct fixed article')
    media = read_json(work / 'vision-assets/manifest.json')
    benchmark = work / 'benchmark'
    benchmark.mkdir(exist_ok=False)
    fixture_files = [('fixed_source', 'articles.jsonl.gz'), ('fixed_rosters', 'rosters.json'),
                     ('fidelity_reference', 'reference.json')]
    if sum(Path(campaign['config'][key]).stat().st_size for key, _ in fixture_files) > 64_000_000:
        raise ValueError('Retained fixed review inputs exceed 64 MB')
    for key, name in fixture_files:
        target = benchmark / name
        shutil.copyfile(campaign['config'][key], target)
        digest = sha256(target)
        if campaign['config'].get(key + '_sha256') not in (None, digest):
            raise ValueError('Retained fixed review input changed during preparation')
        ready['hashes'][str(target.relative_to(work))] = digest
    shards = []
    for i in range(campaign['config']['gpu_workers']):
        chosen = rows[i::campaign['config']['gpu_workers']]
        ids = {a['article_id'] for a in chosen}
        base = work / 'shards' / f'{i:03d}'
        base.mkdir(parents=True, exist_ok=False)
        sample = base / 'sample.jsonl.gz'
        with gzip.open(sample, 'wt', encoding='utf-8') as handle:
            for row in chosen: handle.write(json.dumps(row, ensure_ascii=False) + '\n')
        manifest = base / 'media.json'
        # Asset files stay shared and immutable; only their selection is sharded.
        write_json(manifest, {**media, 'figures': [r for r in media['figures'] if r['article_id'] in ids]})
        shards.append({'shard': i, 'articles': sorted(ids), 'sample': str(sample), 'media': str(manifest),
                       'sample_sha256': sha256(sample), 'media_sha256': sha256(manifest)})
        for path in (sample, manifest): ready['hashes'][str(path.relative_to(work))] = sha256(path)
    write_json(work / 'shards.json', {'status': 'ready', 'shards': shards,
                                     'article_ids': sorted(a['article_id'] for a in rows)})
    ready['hashes']['shards.json'] = sha256(work / 'shards.json')
    write_json(work / 'cpu.json', ready)
    return {'status': 'ready', 'shards': shards}


@contextmanager
def checkpoint_reader(engine_work):
    """Concurrent readers coexist; existing owned cleanup's exclusive lock fails."""
    with (Path(engine_work) / '.model.lock').open('a') as handle:
        fcntl.flock(handle, fcntl.LOCK_SH | fcntl.LOCK_NB)
        yield


def verify_checkpoint(engine):
    from . import hipergator as hpg
    model = engine['config']['models'][0]
    path = hpg.active_path(engine)
    hpg.check_owner(path, engine, model)
    audit = read_json(Path(engine['work']) / 'results' / model['name'] / 'download.json')
    if audit['status'] != 'ready' or read_json(path / 'ready.json') != {
            'campaign': engine['id'], 'model': model['name'], 'audit_sha256': json_digest(audit)}:
        raise ValueError('Shared checkpoint has no verified readiness receipt')
    for asset in audit['files']:
        local = path / asset['folder'] / asset['file']
        if (local.is_symlink() or not local.resolve().is_relative_to(path) or
                local.stat().st_size != asset['bytes'] or local.stat().st_mtime_ns != asset['mtime_ns']):
            raise ValueError('Shared checkpoint changed after CPU verification')
    return model, path


def worker_serving_config(campaign, engine, model, shard):
    from .glimmer_benchmark import serving_config
    current = copy.deepcopy(engine)
    current['config']['max_model_len'] = campaign['config']['context']
    cfg = serving_config(current, model, {'name': f'frontier-{shard:03d}', 'replicas': 1,
                         'tensor_parallel': 1, 'speculation': 'dflash', 'vision': True,
                         'max_batched_tokens': campaign['config']['prefill']})
    base = Path(campaign['work']) / 'shards' / f'{shard:03d}' / 'runtime-cache'
    base.mkdir(parents=True, exist_ok=False)
    cfg.port = 8600 + shard
    cfg.rpc_port = 18000 + shard * 100
    cfg.startup_wave_size = 1
    for key, relative in {'HF_HOME': 'hf', 'HF_HUB_CACHE': 'hf/hub', 'HF_XET_CACHE': 'hf/xet',
                          'HF_MODULES_CACHE': 'hf/modules', 'VLLM_CACHE_ROOT': 'vllm',
                          'TRITON_CACHE_DIR': 'triton', 'TORCHINDUCTOR_CACHE_DIR': 'inductor',
                          'XDG_CACHE_HOME': 'xdg'}.items():
        cfg.environment[key] = str(base / relative)
        os.environ[key] = str(base / relative)
    # The later, read-only bind covers weights, drafter, template and SIF. All
    # writable compilation/HF caches are outside this immutable tree.
    cfg.bind_files[str(Path(engine['work']))] = str(Path(engine['work']))
    cfg.bind_files[campaign['root']] = campaign['root']
    return cfg


def probe_worker(engine, cfg, destination):
    """Verify the GPU/container contract without writing shared engine results."""
    from .hipergator import container_check
    setup = read_json(Path(engine['work']) / 'setup.json')
    if sha256(cfg.sif) != setup['sha256']:
        raise ValueError('Container changed after CPU setup')
    code = ('import json,torch,vllm; from vllm.model_executor.models import ModelRegistry; '
            'from vllm.reasoning import ReasoningParserManager; '
            'assert vllm.__version__==' + repr(cfg.version) + '; '
            'assert "MuseGlimmerForConditionalGeneration" in ModelRegistry.get_supported_archs(); '
            'ReasoningParserManager.get_reasoning_parser("muse_glimmer"); '
            'assert torch.cuda.device_count()==1; assert torch.cuda.get_device_capability(0)==(10,0); '
            'print(json.dumps({"vllm":vllm.__version__,"torch":torch.__version__,"gpu":torch.cuda.get_device_name(0)}))')
    shared = str(Path(engine['work']).parent)
    args = ['apptainer', 'exec', '--nv', '--cleanenv', '--bind', f'{shared}:{shared}',
            '--bind', f'{engine["work"]}:{engine["work"]}:ro',
            '--env', 'CUDA_VISIBLE_DEVICES=' + os.environ['CUDA_VISIBLE_DEVICES']]
    for key, value in cfg.environment.items(): args += ['--env', key + '=' + value]
    args += [cfg.sif, cfg.container_python, '-c', code]
    container_check(args, destination, 'One-GPU Glimmer architecture/parser check')


async def run_worker(campaign, shard):
    from . import hipergator as hpg
    from .pilot_extract import run_pilot
    from .corpus_pilot import trial_warmup
    from .serving import ServerGroup
    from .gpu_telemetry import monitor
    if type(shard) is not int or not 0 <= shard < campaign['config']['gpu_workers']:
        raise ValueError('Worker shard is outside the plan')
    visible = [x for x in os.environ.get('CUDA_VISIBLE_DEVICES', '').split(',') if x.strip()]
    if len(visible) != 1:
        raise ValueError('Each worker requires exactly one allocated GPU')
    work = Path(campaign['work']); base = work / 'shards' / f'{shard:03d}'
    verify_cpu(campaign)
    assignment = read_json(work / 'shards.json')['shards'][shard]
    engine = hpg.read_campaign(work / 'engine')
    started = time.monotonic()
    deadline = started + campaign['config']['gpu_minutes'] * 60 - 150
    rows = []
    with checkpoint_reader(engine['work']):
        model, _ = verify_checkpoint(engine)
        cfg = worker_serving_config(campaign, engine, model, shard)
        probe_worker(engine, cfg, base / 'container-probe.json')
        group = ServerGroup(cfg, campaign['work'], str(base / 'servers'))
        async with monitor(base / 'telemetry.jsonl'):
            try:
                commands = await asyncio.wait_for(group.start(timeout=min(1200, deadline - time.monotonic())),
                                                  timeout=max(1, deadline - time.monotonic()))
                endpoints = [command['endpoint'] for command in commands]
                for index, seed in enumerate(campaign['config']['seeds']):
                    # Counterbalance order by seed and shard, preserving all arms
                    # for exactly the same article identities in this worker.
                    names = list(VARIANTS)
                    offset = (index + shard) % len(names)
                    names = names[offset:] + names[:offset]
                    for variant in names:
                        trial = base / 'trials' / f'seed{seed}' / variant
                        row = {'shard': shard, 'seed': seed, 'variant': variant, 'articles': assignment['articles']}
                        remaining = deadline - time.monotonic()
                        if remaining < 180:
                            row.update(status='deferred', reason='Worker deadline reserve')
                        else:
                            config = trial_config(campaign, variant, len(assignment['articles']))
                            try:
                                async def execute():
                                    warmup = await trial_warmup(endpoints, config, seed)
                                    result = await run_pilot(config, assignment['sample'], trial, endpoints,
                                        campaign['config']['context'], 'targeted', input_scope='patient_sections',
                                        seed=seed, image_manifest={**read_json(assignment['media']), 'output_dir': str(work)})
                                    result['warmup'] = warmup
                                    write_json(trial / 'report.json', result)
                                    return result
                                result = await asyncio.wait_for(execute(), timeout=remaining)
                                row.update(status='completed', report=result)
                            except Exception as error:
                                row.update(status='failed', error_type=type(error).__name__, error=str(error))
                        rows.append(row)
                        write_json(base / 'trials.json', rows)
            finally:
                group.stop()
    result = {'status': 'completed' if all(r['status'] == 'completed' for r in rows) else 'partial',
              'shard': shard, 'trials': rows, 'wall_seconds': time.monotonic() - started,
              'allocated_gpus': 1, 'checkpoint_downloads': 0}
    write_json(base / 'worker.json', result)
    return result


def require_workers_terminal(campaign):
    """Fail closed for manual cleanup while submitted workers are still queued."""
    ledger = read_json(Path(campaign['work']) / 'frontier-jobs.json')
    ids = {j['id'] for j in ledger['jobs'] if j['phase'] == 'worker'}
    if len(ids) != campaign['config']['gpu_workers']:
        raise ValueError('All worker job identities are required before cleanup')
    result = subprocess.run(['sacct', '--noheader', '--parsable2', '--jobs=' + ','.join(sorted(ids)),
                             '--format=JobIDRaw,State'], capture_output=True, text=True, check=True, timeout=30)
    states = {}
    for line in result.stdout.splitlines():
        fields = line.split('|')
        if len(fields) >= 2 and fields[0] in ids:
            states[fields[0]] = fields[1].split()[0].rstrip('+')
    if set(states) != ids or any(state not in TERMINAL for state in states.values()):
        raise ValueError('Cleanup deferred until every worker is terminal in Slurm accounting')
    return states


def aggregate(campaign):
    from .hipergator import require_cpu
    from .bundle_quality import score_bundle, align_patients
    from .ledger_evaluation import evaluate as evaluate_ledger, objective_adequacy, paired_evaluate
    require_cpu()
    work = Path(campaign['work'])
    reference = reviewed_reference(campaign)
    planned = {(i, seed, name) for i in range(campaign['config']['gpu_workers'])
               for seed in campaign['config']['seeds'] for name in VARIANTS}
    rows = {}; workers = []
    for i in range(campaign['config']['gpu_workers']):
        base = work / 'shards' / f'{i:03d}'
        if (base / 'worker.json').is_file(): workers.append(read_json(base / 'worker.json'))
        if (base / 'trials.json').is_file():
            for row in read_json(base / 'trials.json'):
                key = (i, row['seed'], row['variant'])
                if key not in planned or key in rows or row['shard'] != i:
                    raise ValueError('Duplicate or unplanned shard trial receipt')
                rows[key] = row
    results = []; exports = {}
    for seed in campaign['config']['seeds']:
        for name in VARIANTS:
            patients = []; rosters = []; visuals = []; media = []; missing = []; walls = []; tokens = 0; unknown_usage = 0
            task_count = 0; valid_tasks = 0
            for i in range(campaign['config']['gpu_workers']):
                row = rows.get((i, seed, name))
                if not row or row['status'] != 'completed':
                    missing.append({'shard': i, 'status': row['status'] if row else 'missing'})
                    continue
                report = row['report']; output = report['outputs']
                trial = work / 'shards' / f'{i:03d}' / 'trials' / f'seed{seed}' / name
                for key in ('patients', 'predicted_rosters', 'visual_annotations'):
                    path = Path(output[key])
                    if path.is_symlink() or not path.resolve().is_relative_to(trial.resolve()):
                        raise ValueError('Trial output escaped its isolated shard')
                patients.extend(read_jsonl(output['patients']))
                rosters.extend(read_json(output['predicted_rosters'])['articles'])
                visuals.extend(read_json(output['visual_annotations']))
                for path in sorted((trial / 'tasks').glob('*.json')):
                    task = read_json(path)
                    if task.get('task') in {'figure_attribution', 'pixel_attribution', 'joint_figure'}:
                        media.append(task)
                walls.append(report.get('wall_seconds', 0))
                usage = report.get('tokens', {})
                tokens += usage.get('reported_output_tokens', usage.get('output_tokens') or 0)
                unknown_usage += usage.get('unknown_output_usage_calls', int(usage.get('output_tokens') is None))
                task_count += report.get('task_count', 0)
                valid_tasks += report.get('valid_tasks', 0)
            records = [p['source']['record_id'] for p in patients]
            if len(records) != len(set(records)) or len(rosters) != len({r['article_id'] for r in rosters}):
                raise ValueError('Shard exports overlap article or patient identities')
            dest = work / 'aggregate' / f'seed{seed}' / name
            dest.mkdir(parents=True, exist_ok=True)
            with (dest / 'patients.jsonl').open('w', encoding='utf-8') as handle:
                for patient in patients: handle.write(json.dumps(patient, ensure_ascii=False) + '\n')
            write_json(dest / 'rosters.json', {'articles': rosters})
            write_json(dest / 'visual-annotations.json', visuals)
            exports[(seed, name)] = dest
            # Score the global reference ONCE per arm/seed. Missing shards stay
            # missing in the full denominator, never become zero-sized gold.
            score = score_bundle(reference, patients, visual_rows=media,
                                 discovery={'articles': rosters}, pixel_rows=visuals)
            write_json(dest / 'bundle-quality.json', score)
            aligned, _ = align_patients(reference, patients, {'articles': rosters})
            ledger_score = evaluate_ledger(reference, aligned)
            write_json(dest / 'ledger-evaluation.json', ledger_score)
            results.append({'seed': seed, 'variant': name, 'status': 'partial' if missing else 'completed',
                            'missing_shards': missing, 'bundle_quality': score, 'wall_gpu_seconds': sum(walls),
                            'ledger_evaluation': ledger_score,
                            'completion_tokens': tokens if not unknown_usage else None,
                            'reported_completion_tokens': tokens, 'unknown_output_usage_calls': unknown_usage,
                            'output_tokens_per_gpu_second': tokens / sum(walls) if sum(walls) > 0 and not unknown_usage else None,
                            'token_rate_basis': 'Completed trial output usage / summed one-GPU arm wall; excludes startup/warmup and failed/deferred trials',
                            'task_count': task_count, 'valid_tasks': valid_tasks,
                            'failed_or_missing_shards': len(missing),
                            'delivered_patients': len(patients)})
    complete = all(r['status'] == 'completed' for r in results)
    comparisons = []
    for seed in campaign['config']['seeds']:
        original = exports[(seed, 'live-complete')]
        baseline = list(read_jsonl(original / 'patients.jsonl'))
        baseline_discovery = read_json(original / 'rosters.json')
        for name in VARIANTS[1:]:
            candidate = exports[(seed, name)]
            pair = paired_evaluate(reference, baseline, list(read_jsonl(candidate / 'patients.jsonl')),
                splits=reference['optimization_splits'], seed=seed,
                baseline_discovery=baseline_discovery, candidate_discovery=read_json(candidate / 'rosters.json'))
            available = all(r['status'] == 'completed' for r in results if r['seed'] == seed and r['variant'] in ('live-complete', name))
            pair.update(seed=seed, variant=name, paired_comparison_available=available)
            write_json(candidate / 'paired-comparison.json', pair)
            comparisons.append(pair)
    result = {'status': 'completed' if complete else 'partial' if rows else 'failed',
              'paired_comparison_available': complete, 'results': results,
              'paired_comparisons': comparisons,
              'planned_trials': len(planned), 'received_trials': len(rows),
              'gpu_job_seconds': sum(w.get('wall_seconds', 0) for w in workers),
              'source_articles': campaign['config']['sample_size'],
              'scoring_scope': 'Full fixed gold once per arm/seed; failed shards retain missing denominators',
              'objective_adequacy': objective_adequacy(reference),
              'clinical_accuracy_verified': False}
    write_json(work / 'gpu.json', result)
    write_json(work / 'summary.json', result)
    lines = ['# Short distributed evidence-ledger pilot', '',
             f'Status: {result["status"]}; paired comparison available: {complete}.', '',
             '| Seed | Arm | Status | Valid tasks | Missing shards | Clinical probes | Ordering probes | Bundle | Output tok/GPU-s |',
             '| --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |']
    for r in results:
        score = r['bundle_quality']; clinical = score.get('clinical', {}); temporal = score.get('parts', {}).get('temporal_relations', {})
        rate = r['output_tokens_per_gpu_second']
        rate_text = f'{rate:.1f}' if rate is not None else 'unavailable'
        lines.append(f'| {r["seed"]} | {r["variant"]} | {r["status"]} | {r["valid_tasks"]}/{r["task_count"]} | '
                     f'{r["failed_or_missing_shards"]} | {clinical.get("matched",0)}/{clinical.get("required",0)} | '
                     f'{temporal.get("matched",0)}/{temporal.get("required",0)} | {score["score"]:.4f} | '
                     f'{rate_text} |')
    lines += ['', '| Seed | Candidate | Paired available | Test probes baseline → candidate | Test gains/losses |',
              '| --- | --- | --- | --- | --- |']
    for pair in comparisons:
        test = pair['splits']['test']; before = test['baseline']['summary']; after = test['candidate']['summary']
        lines.append(f'| {pair["seed"]} | {pair["variant"]} | {pair["paired_comparison_available"]} | '
                     f'{before["matched_probes"]}/{before["required_probes"]} → '
                     f'{after["matched_probes"]}/{after["required_probes"]} | '
                     f'+{len(test["paired_gained"])}/−{len(test["paired_lost"])} |')
    lines += ['', 'Scores use the full source-reviewed development gold. Missing shards prevent paired comparison.',
              'Test is the existing development split, previously exposed in historical campaigns; it is not new blinded clinical gold.',
              'Output token rates pool usage over summed arm GPU wall, exclude startup/warmup, and do not sum staggered worker rates.',
              'This is an architectural ablation; no GEPA search or clinical accuracy certification.']
    (work / 'SUMMARY.md').write_text('\n'.join(lines) + '\n')
    return result


def export_results(campaign, output):
    """Bounded, explicit result/review-source export; no model or runtime caches."""
    work = Path(campaign['work']).resolve(); output = Path(output).absolute()
    if output.exists() or output.is_symlink() or output.resolve().is_relative_to(work):
        raise ValueError('Use a new archive outside the campaign directory')
    from .pilot_cleanup import _safe_path
    files = set()
    names = ['frontier.json', 'frontier-jobs.json', 'frontier-plan.json', 'frontier-slurm.json',
             'cpu.json', 'setup.json', 'download.json', 'aggregate.json', 'gpu.json', 'summary.json', 'SUMMARY.md',
             'objective-adequacy.json', 'cleanup.json', 'source-owner.json', 'source-cleanup.json',
             'benchmark', 'aggregate', 'logs', 'vision-assets',
             'profile/counts.jsonl.gz', 'profile/profile.json', 'profile/SUMMARY.md', 'profile/rosters.json',
             'engine/setup.json', 'engine/container-python.json',
             'engine/results/glimmer-fp8/download.json', 'engine/results/glimmer-fp8/cleanup.json']
    for i in range(campaign['config']['gpu_workers']):
        prefix = f'shards/{i:03d}/'
        names += [prefix + name for name in ('worker.json', 'trials.json', 'telemetry.jsonl', 'container-probe.json', 'servers')]
        for seed in campaign['config']['seeds']:
            for name in VARIANTS:
                trial = prefix + f'trials/seed{seed}/{name}/'
                names += [trial + name for name in ('report.json', 'run-config.json', 'attempts.jsonl', 'patients.jsonl',
                    'rosters.json', 'visual-annotations.json', 'tasks', 'patients', 'review', 'source-ledgers', 'episode-ledgers')]
    allowed_suffixes = {'.json', '.jsonl', '.gz', '.log', '.md', '.image', '.png', '.svg'}
    for name in names:
        path = _safe_path(work, name)
        entries = sorted(path.rglob('*')) if path.is_dir() else [path]
        for entry in entries:
            if entry.is_symlink() or not entry.resolve().is_relative_to(work):
                raise ValueError('Result archive path escaped campaign')
            if entry.is_file() and entry.suffix in allowed_suffixes:
                if any(part in {'.venv', 'client-venv', '.cache', 'uv-cache', 'runtime-cache', 'active-model'}
                       for part in entry.relative_to(work).parts):
                    continue
                files.add(entry)
    total = sum(path.stat().st_size for path in files)
    if total > EXPORT_MAX_BYTES:
        raise ValueError('Result archive exceeds the 8 GB uncompressed cap')
    created = False
    try:
        with output.open('xb') as handle:
            created = True
            with tarfile.open(fileobj=handle, mode='w:gz') as archive:
                for path in sorted(files): archive.add(path, arcname=str(path.relative_to(work)), recursive=False)
    except BaseException:
        if created: output.unlink(missing_ok=True)
        raise
    return {'output': str(output), 'files': len(files), 'uncompressed_bytes': total, 'sha256': sha256(output),
            'scope': 'Reports, trial/task/patient/source-ledger/episode audits, frozen review fixture and bounded pixels; no model/container/environment caches'}


def cleanup(campaign):
    from . import hipergator as hpg
    from .pilot_cleanup import cleanup_sources
    hpg.require_cpu()
    work = Path(campaign['work'])
    states = require_workers_terminal(campaign)
    engine = read_json(work / 'engine/campaign.json')
    if engine['work'] != str(work / 'engine') or engine['config_sha256'] != json_digest(engine['config']):
        raise ValueError('Invalid owned engine campaign')
    result = hpg.cleanup(engine, engine['config']['models'][0])
    # Even a crashed aggregate does not strand sources; build explicit partial
    # receipts from whatever worker outputs survived before source deletion.
    if not (work / 'summary.json').exists(): aggregate(campaign)
    sources = cleanup_sources(campaign)
    from .pilot_cleanup import _safe_path
    paths = [_safe_path(work, f'shards/{i:03d}/{name}')
             for i in range(campaign['config']['gpu_workers']) for name in ('sample.jsonl.gz', 'media.json')]
    caches = [_safe_path(work, f'shards/{i:03d}/runtime-cache')
              for i in range(campaign['config']['gpu_workers'])]
    for path in paths:
        if path.exists() and not path.is_file(): raise ValueError('Invalid shard source cleanup target')
    for path in caches:
        if path.exists() and not path.is_dir(): raise ValueError('Invalid owned worker cache target')
    for path in paths: path.unlink(missing_ok=True)
    # rmtree does not follow descendant symlinks; _safe_path refuses a linked
    # cache root or parent. Results, traces and deployment logs are separate.
    for path in caches:
        if path.exists(): shutil.rmtree(path)
    result.update(worker_states=states, source_cleanup=sources, shard_sources_deleted=True,
                  worker_runtime_caches_deleted=True)
    write_json(work / 'cleanup.json', result)
    return result


def stage(campaign, phase, shard=None):
    work = Path(campaign['work'])
    if phase == 'worker' and (type(shard) is not int or not 0 <= shard < campaign['config']['gpu_workers']):
        raise ValueError('Worker shard is outside the plan')
    destination = work / 'shards' / f'{shard:03d}' / 'worker.json' if phase == 'worker' else work / (phase + '.json')
    try:
        if phase != 'worker':
            from .hipergator import require_cpu
            require_cpu()
        if phase == 'prepare':
            from .corpus_pilot import prepare_cpu_campaign
            asyncio.run(prepare_cpu_campaign(campaign))
            return prepare_shards(campaign)
        if phase in ('setup', 'download'):
            from .corpus_pilot import stage as cpu_stage
            return cpu_stage(campaign, phase)
        if phase == 'worker':
            def interrupted(signum, frame):
                signal.signal(signal.SIGTERM, signal.SIG_IGN)
                raise KeyboardInterrupt('Slurm signal ' + str(signum))
            signal.signal(signal.SIGTERM, interrupted)
            return asyncio.run(run_worker(campaign, shard))
        if phase == 'aggregate': return aggregate(campaign)
        if phase == 'cleanup': return cleanup(campaign)
        raise ValueError('Unknown frontier stage')
    except BaseException as exc:
        failure = {'status': 'failed', 'phase': phase, 'shard': shard,
                   'error_type': type(exc).__name__, 'error': str(exc)}
        write_json(destination, failure)
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('plan', 'submit', 'stage', 'export-results'))
    parser.add_argument('--work-dir', required=True)
    parser.add_argument('--config', default='configs/pilot/frontier.yaml')
    parser.add_argument('--sif')
    parser.add_argument('--gpu-workers', type=int, choices=(4, 8))
    parser.add_argument('--gpu-minutes', type=int)
    parser.add_argument('--phase', choices=tuple(CPU_RESOURCES) + ('worker',))
    parser.add_argument('--shard', type=int)
    parser.add_argument('--output')
    args = parser.parse_args()
    if args.action == 'export-results':
        if args.output is None: parser.error('export-results needs --output')
        result = export_results(load(args.work_dir, check_runtime=False), args.output)
    elif args.action == 'stage':
        if args.phase is None or (args.phase == 'worker' and args.shard is None):
            parser.error('stage needs --phase and workers need --shard')
        campaign = load(args.work_dir, check_runtime=args.phase not in ('aggregate', 'cleanup'))
        result = stage(campaign, args.phase, args.shard)
    else:
        campaign = prepare(Path.cwd(), args.work_dir, args.config, sif=args.sif,
                           gpu_workers=args.gpu_workers, gpu_minutes=args.gpu_minutes)
        result = submit(campaign) if args.action == 'submit' else {
            'work': campaign['work'], 'status': 'planned', 'allocation': ALLOCATION,
            'jobs': [job_command(campaign, phase, shard) for phase, shard in stages(campaign)]}
        if args.action == 'plan': write_json(Path(args.work_dir) / 'frontier-plan.json', result)
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
