"""Submit a serial CPU-download / GPU-evaluate / CPU-delete Slurm campaign."""
from __future__ import annotations

import asyncio
import fcntl
import hashlib
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tarfile
import time
from contextlib import contextmanager
from pathlib import Path

import httpx
import yaml

from .data import write_json
from .hpg_eval import Replay, fixtures, stats
from .provenance import json_digest
from .serving import ServingConfig, ServerGroup
from .benchmark import environment_report

DEFAULT_CONFIG = 'configs/hipergator/k2.yaml'


def gpu_count(model):
    if any(type(model[k]) is not int or model[k] < 1 for k in ('replicas', 'tensor_parallel')):
        raise ValueError('Each profile must use between one and eight GPUs with positive integer topology sizes')
    count = model['replicas'] * model['tensor_parallel']
    if count > 8: raise ValueError('Each profile must use between one and eight GPUs')
    return count


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as f:
        for block in iter(lambda: f.read(4 * 1024 * 1024), b''): digest.update(block)
    return digest.hexdigest()


def load_campaign(path, root):
    config = yaml.safe_load(Path(path).read_text())
    if not isinstance(config, dict): raise ValueError('Campaign YAML must be an object')
    names = []
    for model in config['models']:
        name = model['name']; names.append(name)
        if not re.fullmatch(r'[a-z0-9][a-z0-9-]{0,63}', name): raise ValueError('Unsafe model name')
        if not re.fullmatch(r'[a-f0-9]{40}', model['revision']): raise ValueError('Pin every model to an exact HF commit')
        count = gpu_count(model)
        overrides = model.get('gpu_resources', {})
        if not isinstance(overrides, dict) or set(overrides) - {'cpus', 'mem', 'time'}:
            raise ValueError('GPU resource overrides support only cpus, mem and time')
        resources = {**config['slurm']['gpu'], **overrides}
        if type(resources['cpus']) is not int or resources['cpus'] < count:
            raise ValueError('Request at least one CPU per GPU')
        if 'MoVA' in model['id'] and model['tensor_parallel'] not in (1, 2):
            raise ValueError('MoVA FP8 requires TP=1 or TP=2 to preserve whole quantization blocks')
        # Metadata is pinned alongside the launch profile, never inferred from model names.
        meta_path = Path(root) / model.get('metadata', 'configs/hipergator/' + model['id'].split('/')[-1] + '.metadata.json')
        meta = json.loads(meta_path.read_text())
        if (meta['revision'], meta['model_id']) != (model['revision'], model['id']):
            raise ValueError('Checkpoint/profile metadata mismatch')
        if (meta['config'].get('quantization_config') or {}).get('quant_method') != model['quantization']:
            raise ValueError('Quantization profile differs from the checkpoint')
        if model['expected_bytes'] != sum(f['size'] for f in meta['files']):
            raise ValueError('Disk estimate differs from the pinned checkpoint inventory')
        if model['tensor_parallel'] < 1 or model['replicas'] < 1:
            raise ValueError('GPU topology sizes must be positive')
        profile = meta['reasoning_profile']
        for arm in config['arms'].values():
            if arm['reasoning_effort'] not in profile['supported_levels']:
                raise ValueError('Unsupported checkpoint reasoning level: ' + arm['reasoning_effort'])
        model['reasoning_profile'] = profile
        model['metadata'] = str(meta_path.resolve())
    if len(set(names)) != len(names): raise ValueError('Duplicate model names')
    for arm in config['arms'].values():
        if arm['max_tokens'] < 128 or arm['retry_tokens'] < arm['max_tokens'] or arm['retry_tokens'] >= config['max_model_len']:
            raise ValueError('Output budget must leave room for source tokens')
    if config.get('benchmark_family') == 'glimmer':
        from .glimmer_benchmark import validate_config
        validate_config(config, Path(root))
    fixtures(Path(root) / config['fixtures'])
    return config


def package(root, output):
    """An explicit allowlist excludes caches, credentials, historical raw responses and weights."""
    root = Path(root).resolve(); output = Path(output).resolve()
    files = {root / p for p in ('pyproject.toml', 'uv.lock', 'README.md', 'docs/HIPERGATOR_K2.md', 'docs/HIPERGATOR_GLIMMER.md', 'docs/HIPERGATOR_GLIMMER_TUNING.md') if (root / p).exists()}
    for name in ('LICENSE', 'LICENSE.md'):
        if (root / name).exists(): files.add(root / name)
    for name in ('figure-visuals.schema.json', 'joint-figure-analysis.schema.json'):
        if (root / 'schemas' / name).exists(): files.add(root / 'schemas' / name)
    for folder in ('src/openpatients2', 'configs/hipergator', 'benchmarks/hipergator-k2'):
        files.update(p for p in (root / folder).rglob('*') if p.is_file() and p.suffix in {'.py', '.md', '.json', '.jsonl', '.sha256', '.yaml', '.jinja', '.txt'})
    files.update(root.glob('scripts/hpg_*'))
    if any(p.is_symlink() for p in files): raise ValueError('Package files cannot be symlinks')
    fixtures(root / 'benchmarks/hipergator-k2/fixtures')
    output.parent.mkdir(parents=True, exist_ok=True)
    with tarfile.open(output, 'w:gz') as archive:
        for p in sorted(files): archive.add(p, arcname=str(Path('openpatients2-k2-benchmark') / p.relative_to(root)), recursive=False)
    # The same explicit allowlist supports updating an existing cluster checkout
    # with rsync, without transferring histories, environments or weights.
    transfer = output.with_suffix(output.suffix + '.files.txt')
    transfer.write_text(''.join(str(p.relative_to(root)) + '\n' for p in sorted(files)))
    result = {'archive': str(output), 'bytes': output.stat().st_size, 'sha256': sha256(output), 'files': len(files),
              'transfer_manifest': str(transfer), 'weights_included': False, 'credentials_included': False}
    write_json(output.with_suffix(output.suffix + '.manifest.json'), result)
    return result


def sbatch_command(stage, root, work, config, model=None, dependency=None):
    resources = config['slurm'][stage]
    if stage == 'gpu': resources = {**resources, **model.get('gpu_resources', {})}
    name = model['name'] if model else stage
    args = ['sbatch', '--parsable', '--account=' + config['account'], '--qos=' + config['qos'],
            '--nodes=1', '--ntasks=1', '--no-requeue', '--cpus-per-task=' + str(resources['cpus']),
            '--mem=' + resources['mem'], '--time=' + resources['time'], '--chdir=' + str(root),
            '--job-name=op2-' + stage + '-' + name, '--output=' + str(work / 'logs' / (stage + '-' + name + '-%j.log'))]
    if stage == 'gpu':
        args += ['--partition=' + config['gpu_partition'], '--gres=gpu:b200:' + str(gpu_count(model)), '--signal=B:TERM@120']
    else:
        args += ['--gres=none']
        if config.get('cpu_partition'): args += ['--partition=' + config['cpu_partition']]
    if dependency: args += ['--dependency=' + dependency]
    # Arguments are shell quoted by Python, not interpolated into an SBATCH directive.
    args += [str(root / 'scripts' / ('hpg_gpu.sbatch' if stage == 'gpu' else 'hpg_cpu.sbatch')),
             stage, str(work)]
    if model: args.append(model['name'])
    return args


def slurm_command(args, env, audit_path, audit):
    """Preserve scheduler diagnostics, including failed or ambiguous submissions."""
    entry = {'command': args, 'started_unix': time.time(), 'status': 'started'}
    audit.append(entry)
    write_json(audit_path, {'commands': audit})
    try:
        result = subprocess.run(args, env=env, text=True, capture_output=True, check=False, timeout=60)
    except (OSError, subprocess.SubprocessError) as exc:
        # TimeoutExpired may carry bytes even with text=True. Never log the environment.
        def decoded(value):
            return value.decode('utf-8', errors='replace') if isinstance(value, bytes) else value or ''
        entry.update(status='failed', finished_unix=time.time(), error=str(exc),
                     stdout=decoded(getattr(exc, 'stdout', None)), stderr=decoded(getattr(exc, 'stderr', None)))
        write_json(audit_path, {'commands': audit})
        message = entry['stderr'].strip() or entry['stdout'].strip() or str(exc)
        if isinstance(exc, subprocess.TimeoutExpired) and args[0] == 'sbatch' and '--test-only' not in args:
            message += '\nSubmission outcome is unknown; inspect squeue for this job name before retrying.'
        raise RuntimeError(args[0] + ' failed: ' + message) from None
    entry.update(status='ok' if result.returncode == 0 else 'failed', finished_unix=time.time(),
                 returncode=result.returncode, stdout=result.stdout, stderr=result.stderr)
    write_json(audit_path, {'commands': audit})
    if result.returncode:
        message = result.stderr.strip() or result.stdout.strip() or '(no scheduler output)'
        raise RuntimeError(f'{args[0]} exited {result.returncode}: {message}')
    return result


def scheduler_preflight(root, work, config, env, audit):
    """Check every resource profile without submitting jobs or downloading files."""
    first = config['models'][0]
    profiles = [('setup', None), ('download', first)] + [('gpu', m) for m in config['models']]
    profiles += [('cleanup', first), ('report', None)]
    for stage, model in profiles:
        args = sbatch_command(stage, root, work, config, model)
        args.insert(1, '--test-only')
        try:
            slurm_command(args, env, work / 'slurm-commands.json', audit)
        except RuntimeError as exc:
            raise RuntimeError(f'{stage} resource preflight: {exc}') from None


def prepare(config_path, root, work, submit=False, account=None, qos=None, sif=None):
    root = Path(root).resolve(); work = Path(work).resolve()
    if work.exists(): raise ValueError('Use a new work directory for each campaign; existing results are never overwritten')
    config = load_campaign(config_path, root)
    if account: config['account'] = account
    if qos: config['qos'] = qos
    if sif:
        sif = Path(sif).resolve()
        if not sif.is_file(): raise FileNotFoundError(sif)
        config['existing_sif'] = str(sif)
    work.mkdir(parents=True); (work / 'logs').mkdir(); (work / 'results').mkdir()
    campaign = {'root': str(root), 'work': str(work), 'config': config,
                'config_sha256': json_digest(config), 'fixture_manifest_sha256': sha256(root / config['fixtures'] / 'manifest.json'),
                'created_unix': time.time(), 'id': json_digest({'work': str(work), 'config': config})}
    write_json(work / 'campaign.json', campaign)
    # CPU fixture/tokenizer dependencies and source code are checked against this
    # package fingerprint by every stage, so editing a running checkout fails closed.
    files = sorted(p for p in (root / 'src/openpatients2').rglob('*') if p.suffix in {'.py', '.md'})
    files += [root / 'pyproject.toml', root / 'uv.lock']
    files += sorted(root.glob('scripts/hpg_*'))
    files += sorted(p for p in (root / 'configs/hipergator').rglob('*') if p.is_file() and p.suffix in {'.json', '.jinja', '.md'})
    write_json(work / 'runtime-manifest.json', {str(p.relative_to(root)): sha256(p) for p in files})
    previous = None; previous_stage = None; jobs = []; audit = []
    # Environment options can silently replace CLI defaults; use only the configured request.
    env = {k: v for k, v in os.environ.items() if not k.startswith('SBATCH_')}
    submission = {'status': 'preflight' if submit else 'planned', 'preflight_passed': False}
    write_json(work / 'jobs.json', {'submitted': submit, 'jobs': jobs})
    write_json(work / 'submission.json', submission)
    stages = [('setup', None)]
    for model in config['models']: stages += [('download', model), ('gpu', model), ('cleanup', model)]
    stages += [('report', None)]
    phase = 'preflight'; current = None
    try:
        if submit:
            scheduler_preflight(root, work, config, env, audit)
            submission.update(status='submitting_held', preflight_passed=True)
            write_json(work / 'submission.json', submission)
        phase = 'submission'
        for stage, model in stages:
            current = stage + ('/' + model['name'] if model else '')
            # afterany lets failed downloads reach GPU preflight and then cleanup;
            # afterok on cleanup prevents a second checkpoint if deletion failed.
            dependency = None if previous is None else ('afterok:' if previous_stage == 'cleanup' else 'afterany:') + previous
            args = sbatch_command(stage, root, work, config, model, dependency)
            # Hold every job: canceling a held parent cannot accidentally start an
            # afterany child before the rest of a rejected chain has been canceled.
            args.insert(1, '--hold')
            if submit:
                result = slurm_command(args, env, work / 'slurm-commands.json', audit)
                job = result.stdout.strip().split(';')[0]
                if not job.isdigit():
                    raise RuntimeError('sbatch did not return a job ID; inspect squeue for this job name before retrying')
            else: job = '<' + str(len(jobs) + 1) + '>'
            jobs.append({'stage': stage, 'model': model['name'] if model else None, 'id': job,
                         'command': args, 'release_confirmed': False})
            previous = job
            previous_stage = stage
            write_json(work / 'jobs.json', {'submitted': submit, 'jobs': jobs})
        if submit:
            phase = 'release'
            submission['status'] = 'releasing'; write_json(work / 'submission.json', submission)
            # Release successors first and setup last. Until setup is released,
            # dependencies keep the entire chain from starting, even on a release failure.
            for job in reversed(jobs):
                current = job['stage'] + ('/' + job['model'] if job['model'] else '')
                slurm_command(['scontrol', 'release', job['id']], env, work / 'slurm-commands.json', audit)
                job['release_confirmed'] = True
                write_json(work / 'jobs.json', {'submitted': True, 'jobs': jobs})
            submission['status'] = 'released'; write_json(work / 'submission.json', submission)
    except BaseException as exc:
        # A partially submitted chain must not run with its cleanup/report missing.
        rollback = {'status': 'not_needed', 'job_ids': [j['id'] for j in jobs]}
        if submit and jobs:
            try:
                # A release error can leave some successors unheld. Rehold the
                # chain before canceling parents with afterany children. If that
                # fails, retain the chain (and its cleanup) for manual inspection.
                if phase == 'release':
                    slurm_command(['scontrol', 'hold', *rollback['job_ids']], env, work / 'slurm-commands.json', audit)
                slurm_command(['scancel', *rollback['job_ids']], env, work / 'slurm-commands.json', audit)
                rollback['status'] = 'cancellation_requested'
            except RuntimeError as cancel_error:
                rollback.update(status='failed', error=str(cancel_error))
        submission.update(status='failed', phase=phase, stage=current,
                          error=str(exc) or type(exc).__name__, rollback=rollback)
        write_json(work / 'submission.json', submission)
        if isinstance(exc, (KeyboardInterrupt, SystemExit)): raise
        message = f'Slurm {phase} failed' + (f' ({current})' if current else '') + f': {exc}'
        if rollback['status'] == 'failed':
            message += '\nRollback also failed: ' + rollback['error'] + '\nInspect these jobs: ' + ', '.join(rollback['job_ids'])
        elif rollback['job_ids']:
            message += '\nCancellation requested for job IDs: ' + ', '.join(rollback['job_ids'])
        message += f'\nDiagnostics: {work / "submission.json"} and {work / "slurm-commands.json"}'
        raise RuntimeError(message) from None
    (work / 'submit-plan.sh').write_text('#!/usr/bin/env bash\n# Review only; use the submit command to resolve job dependencies.\n' + '\n'.join(shlex.join(j['command']) for j in jobs) + '\n')
    return {'work_dir': str(work), 'submitted': submit, 'jobs': jobs,
            'notice': 'One owned checkpoint at a time. Cleanup gates the next download. Container and results are retained.'}


def read_campaign(work):
    work = Path(work).resolve()
    campaign = json.loads((work / 'campaign.json').read_text())
    if campaign['work'] != str(work) or campaign['config_sha256'] != json_digest(campaign['config']):
        raise ValueError('Campaign identity/configuration changed')
    root = Path(campaign['root'])
    runtime = json.loads((work / 'runtime-manifest.json').read_text())
    for name, sha in runtime.items():
        if sha256(root / name) != sha: raise ValueError('Running package changed: ' + name)
    if sha256(root / campaign['config']['fixtures'] / 'manifest.json') != campaign['fixture_manifest_sha256']:
        raise ValueError('Fixture manifest changed after submission')
    return campaign


def require_cpu():
    # JOB/STEP_GPUS contain GPU identifiers: "0" means an allocated GPU 0.
    # GPUS_ON_NODE is a count, where "0" really does mean no allocation.
    if (any(os.environ.get(k) not in (None, '') for k in ('SLURM_JOB_GPUS', 'SLURM_STEP_GPUS'))
            or os.environ.get('SLURM_GPUS_ON_NODE') not in (None, '', '0')):
        raise RuntimeError('Download/setup/cleanup must run in a CPU-only allocation')
    os.environ['CUDA_VISIBLE_DEVICES'] = ''


@contextmanager
def model_lock(work):
    with (Path(work) / '.model.lock').open('a') as f:
        fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield


def active_path(campaign):
    path = Path(campaign['work']) / 'active-model'
    if path.is_symlink(): raise ValueError('Refusing a symlink for owned model storage')
    if path.parent.resolve() != Path(campaign['work']).resolve(): raise ValueError('Model storage escaped campaign')
    return path


def check_owner(path, campaign, model):
    marker = path / '.owner.json'
    if marker.is_symlink(): raise ValueError('Ownership marker cannot be a symlink')
    owner = json.loads(marker.read_text())
    if owner != {'campaign': campaign['id'], 'model': model['name'], 'revision': model['revision']}:
        raise ValueError('Refusing to delete/use storage owned by another model or campaign')


def cache_environment(path):
    # All weight, Xet and dynamically imported model-code caches share the owned
    # directory, so cleanup does not leave a second copy in ~/.cache/huggingface.
    env = {'HF_HOME': str(path / 'hf-home'), 'HF_HUB_CACHE': str(path / 'hf-home/hub'),
           'HF_XET_CACHE': str(path / 'hf-home/xet'), 'HF_MODULES_CACHE': str(path / 'modules'),
           'HF_XET_CHUNK_CACHE_SIZE_BYTES': '0', 'XDG_CACHE_HOME': str(path / 'runtime-cache')}
    os.environ.update(env)
    return env


def container_check(command, log_path, label):
    result = subprocess.run(command, capture_output=True, text=True, check=False)
    write_json(log_path, {'command': command, 'returncode': result.returncode,
                         'stdout': result.stdout, 'stderr': result.stderr})
    if result.returncode:
        detail = result.stderr.strip() or result.stdout.strip() or '(no subprocess output)'
        raise RuntimeError(f'{label} exited {result.returncode}: {detail}\nFull diagnostics: {log_path}')
    return result


def setup(campaign):
    require_cpu()
    work = Path(campaign['work']); config = campaign['config']
    cache = work / 'container-build'
    if cache.exists(): raise ValueError('Unexpected existing container build directory')
    cache.mkdir()
    try:
        sif = Path(config['existing_sif']) if config.get('existing_sif') else work / 'vllm.sif'
        if not config.get('existing_sif'):
            # Apptainer can temporarily hold both OCI layers and the SIF. This
            # entire CPU-side build cache is removed before any model download.
            env = {**os.environ, 'APPTAINER_CACHEDIR': str(cache / 'cache'), 'APPTAINER_TMPDIR': str(cache / 'tmp')}
            (cache / 'tmp').mkdir()
            if shutil.disk_usage(work).free < 70_000_000_000:
                raise RuntimeError('Container acquisition needs 70 GB free scratch; or supply --sif with an existing image')
            subprocess.run(['apptainer', 'pull', str(sif), config['image']], env=env, check=True)
        interpreter = config.get('container_python', '/usr/bin/python3')
        # This checks executable/package availability on CPU, without importing
        # CUDA libraries or downloading weights. GPU capability checks follow later.
        code = ('import importlib.metadata as m,json,sys; v=m.version("vllm"); '
                'print(json.dumps({"python":sys.executable,"vllm":v}),flush=True); '
                f'assert v=={config["engine_version"]!r}, (v,{config["engine_version"]!r})')
        container_check(['apptainer', 'exec', '--cleanenv', str(sif), interpreter, '-c', code],
                        work / 'container-python.json', 'CPU container interpreter/package check')
        if config.get('benchmark_family') == 'glimmer':
            from .glimmer_benchmark import setup_plugins
            setup_plugins(campaign, sif, interpreter)
        result = {'status': 'ready', 'sif': str(sif), 'sha256': sha256(sif),
                  'image': config['image'], 'engine_expected': config['engine_version'], 'container_python': interpreter}
        write_json(work / 'setup.json', result)
        return result
    finally:
        shutil.rmtree(cache)


def download(campaign, model, downloader=None):
    require_cpu()
    work = Path(campaign['work']); config = campaign['config']; path = active_path(campaign)
    if not (work / 'setup.json').is_file(): raise RuntimeError('CPU container setup did not complete')
    if json.loads((work / 'setup.json').read_text()).get('status') != 'ready':
        raise RuntimeError('CPU container setup did not complete successfully; no model download')
    with model_lock(work):
        if path.exists(): raise ValueError('Previous checkpoint has not been deleted; refusing a second download')
        expected_bytes = model['expected_bytes']
        if config.get('benchmark_family') == 'glimmer' and model.get('test_dflash'):
            expected_bytes += json.loads((Path(campaign['root']) / config['drafter_metadata']).read_text())['expected_bytes']
            if config.get('dspark_metadata'):
                expected_bytes += json.loads((Path(campaign['root']) / config['dspark_metadata']).read_text())['expected_bytes']
        need = int(expected_bytes * 1.1) + config['disk_headroom_gb'] * 1_000_000_000
        free = shutil.disk_usage(work).free
        if free < need: raise RuntimeError(f'Need {need / 1e9:.1f} GB free scratch, have {free / 1e9:.1f} GB; filesystem quotas also apply')
        path.mkdir()
        write_json(path / '.owner.json', {'campaign': campaign['id'], 'model': model['name'], 'revision': model['revision']})
        cache_environment(path)
        os.environ.pop('HF_HUB_OFFLINE', None); os.environ.pop('TRANSFORMERS_OFFLINE', None)
        if downloader is None:
            from huggingface_hub import snapshot_download
            downloader = snapshot_download
        started = time.monotonic()
        meta = json.loads(Path(model['metadata']).read_text())
        inventories = [('weights', {**meta, 'model_id': model['id'], 'revision': model['revision']})]
        if config.get('benchmark_family') == 'glimmer' and model.get('test_dflash'):
            inventories.append(('drafter', json.loads((Path(campaign['root']) / config['drafter_metadata']).read_text())))
            if config.get('dspark_metadata'):
                inventories.append(('dspark', json.loads((Path(campaign['root']) / config['dspark_metadata']).read_text())))
        audited = []
        for folder, inventory in inventories:
            downloader(repo_id=inventory['model_id'], revision=inventory['revision'], local_dir=str(path / folder),
                       allow_patterns=[a['rfilename'] for a in inventory['files']], max_workers=config['download_workers'])
            audited.extend(verify_inventory(path / folder, inventory, folder))
        write_json(path / 'weights/op2_snapshot.json', {'model_id': model['id'], 'revision': model['revision'],
                                                      'local_path': str(path / 'weights')})
        if config.get('benchmark_family') == 'glimmer':
            from .glimmer_benchmark import template_probe
            template_probe(campaign, model)
            if config.get('vision_evaluation'):
                from .glimmer_vision import prepare_assets
                asyncio.run(prepare_assets(campaign))  # Bounded image fetches belong to the CPU stage.
        report = {'status': 'ready', 'model': model, 'download_and_verify_seconds': time.monotonic() - started,
                  'files': audited, 'bytes': sum(a['bytes'] for a in audited)}
        if config.get('vision_evaluation'):
            report['vision_manifest_sha256'] = sha256(work / 'vision-assets/manifest.json')
        write_json(work / 'results' / model['name'] / 'download.json', report)
        write_json(path / 'ready.json', {'campaign': campaign['id'], 'model': model['name'], 'audit_sha256': json_digest(report)})
        return {'model': model['name'], 'downloaded_bytes': report['bytes'], 'status': 'ready'}


def verify_inventory(directory, meta, folder='weights'):
    audited = []
    for asset in meta['files']:
        local = directory / asset['rfilename']
        if local.is_symlink() or not local.resolve().is_relative_to(directory.resolve()):
            raise ValueError('Snapshot contains an unexpected external file')
        if not local.is_file() or local.stat().st_size != asset['size']:
            raise ValueError('Incomplete checkpoint file: ' + asset['rfilename'])
        expected = (asset.get('lfs') or {}).get('sha256')
        if asset['rfilename'] == 'chat_template.jinja':
            expected = meta['reasoning_profile']['template_sha256']
        actual = sha256(local)
        if expected and expected != actual: raise ValueError('Checkpoint SHA256 mismatch: ' + asset['rfilename'])
        audited.append({'file': asset['rfilename'], 'bytes': asset['size'], 'sha256': actual,
                       'mtime_ns': local.stat().st_mtime_ns, 'folder': folder})
    return audited


def cleanup(campaign, model):
    require_cpu()
    work = Path(campaign['work']); path = active_path(campaign)
    with model_lock(work):
        existed = path.exists()
        if existed:
            check_owner(path, campaign, model)
            shutil.rmtree(path)
        if path.exists(): raise RuntimeError('Checkpoint deletion did not finish')
        result = {'status': 'deleted', 'model': model['name'], 'storage_existed': existed,
                  'free_bytes_after_cleanup': shutil.disk_usage(work).free,
                  'preserved': ['results', 'frozen fixtures', 'container', 'uv environment']}
        write_json(work / 'results' / model['name'] / 'cleanup.json', result)
        return result


def serving_config(campaign, model):
    config = campaign['config']; path = active_path(campaign)
    setup_info = json.loads((Path(campaign['work']) / 'setup.json').read_text())
    cache = cache_environment(path)
    return ServingConfig(name=model['name'], backend='vllm', version=config['engine_version'],
        image_reference=config['image'], sif=setup_info['sif'], model_path=str(path / 'weights'),
        model_id=model['id'], replicas=model['replicas'], tensor_parallel=model['tensor_parallel'],
        container_python=config.get('container_python', '/usr/bin/python3'),
        expert_parallel=model['expert_parallel'], data_parallel=1, quantization=model['quantization'],
        reasoning_parser='k2_horizon', max_model_len=config['max_model_len'], max_num_seqs=config['max_num_seqs'],
        max_batched_tokens=config['max_batched_tokens'], gpu_memory_utilization=config['gpu_memory_utilization'],
        environment={**cache, 'HF_HUB_OFFLINE': '1', 'TRANSFORMERS_OFFLINE': '1', 'HF_DATASETS_OFFLINE': '1',
                     'VLLM_CACHE_ROOT': str(path / 'runtime-cache/vllm'), 'TRITON_CACHE_DIR': str(path / 'runtime-cache/triton'),
                     'TORCHINDUCTOR_CACHE_DIR': str(path / 'runtime-cache/inductor'), 'VLLM_WORKER_MULTIPROC_METHOD': 'spawn'},
        extra_args=['--model-impl', 'vllm', '--chat-template-content-format', 'string', '--generation-config', 'vllm'],
        experimental=True, validation_note='Pinned native K2 and quantization support; exact B200 topology requires hardware measurement')


async def gpu(campaign, model):
    if campaign['config'].get('benchmark_family') == 'glimmer':
        from .glimmer_benchmark import gpu_gauntlet
        return await gpu_gauntlet(campaign, model)
    if not os.environ.get('SLURM_JOB_ID') or not os.environ.get('CUDA_VISIBLE_DEVICES'):
        raise RuntimeError('GPU evaluation requires a Slurm allocation with visible GPUs')
    work = Path(campaign['work']); config = campaign['config']; path = active_path(campaign)
    started = time.monotonic()
    # This lock covers the entire inference lifetime, including server shutdown.
    with model_lock(work):
        check_owner(path, campaign, model)
        ready = json.loads((path / 'ready.json').read_text())
        audit = json.loads((work / 'results' / model['name'] / 'download.json').read_text())
        if ready != {'campaign': campaign['id'], 'model': model['name'], 'audit_sha256': json_digest(audit)}:
            raise ValueError('Download completion/audit does not belong to this model')
        for item in audit['files']:
            local = path / 'weights' / item['file']; stat = local.stat()
            if stat.st_size != item['bytes'] or stat.st_mtime_ns != item['mtime_ns']:
                raise ValueError('Checkpoint changed after CPU verification')
        cfg = serving_config(campaign, model)
        count = cfg.gpus
        setup_info = json.loads((work / 'setup.json').read_text())
        if sha256(cfg.sif) != setup_info['sha256']: raise ValueError('Container changed since CPU setup')
        result_root = work / 'results' / model['name']
        probe = ('import json,torch,vllm,transformers; from vllm.model_executor.models import ModelRegistry; '
                 'from vllm.reasoning import ReasoningParserManager; '
                 'assert vllm.__version__ == ' + repr(config['engine_version']) + '; '
                 'assert "K2HorizonForCausalLM" in ModelRegistry.get_supported_archs(); '
                 'ReasoningParserManager.get_reasoning_parser("k2_horizon"); '
                 f'assert torch.cuda.device_count()=={count}; '
                 f'assert all(torch.cuda.get_device_capability(i)[0]>=10 for i in range({count})); '
                 'print(json.dumps({"vllm":vllm.__version__,"torch":torch.__version__,"transformers":transformers.__version__, '
                 f'"gpus":[torch.cuda.get_device_name(i) for i in range({count})]}}))')
        probe_args = ['apptainer', 'exec', '--nv', '--cleanenv', '--bind', f'{work}:{work}',
                      '--env', 'CUDA_VISIBLE_DEVICES=' + os.environ['CUDA_VISIBLE_DEVICES']]
        for key, value in cfg.environment.items(): probe_args += ['--env', key + '=' + value]
        probe_command = [*probe_args, cfg.sif, cfg.container_python, '-c', probe]
        probe_log = result_root / 'container-probe.json'
        probed = container_check(probe_command, probe_log, 'GPU container preflight')
        write_json(result_root / 'environment.json', {'host': environment_report(), 'container_probe': probed.stdout,
                                                      'container_sha256': setup_info['sha256'], 'serving': cfg.model_dump()})
        group = ServerGroup(cfg, campaign['work'], str(result_root / 'servers'))
        monitor = None; log = None
        try:
            await group.start(config['startup_timeout_seconds'])
            endpoints = [c['endpoint'] for c in group.commands]
            async with httpx.AsyncClient(timeout=config['request_timeout_seconds']) as client:
                for endpoint in endpoints:
                    warm = await client.post(endpoint + '/chat/completions', json={'model': 'clinical-extractor',
                        'messages': [{'role': 'user', 'content': 'Reply with OK.'}], 'max_tokens': 64,
                        'temperature': 0, 'chat_template_kwargs': {'reasoning_effort': 'low'}})
                    warm.raise_for_status()
            log = (result_root / 'gpu-utilization.csv').open('w')
            monitor = subprocess.Popen(['nvidia-smi', '--query-gpu=timestamp,index,name,utilization.gpu,memory.used,power.draw',
                                         '--format=csv', '-l', '1'], stdout=log, stderr=subprocess.STDOUT)
            reports = {}
            for arm_name, arm in config['arms'].items():
                replay = Replay(Path(campaign['root']) / config['fixtures'], result_root / arm_name, model, arm,
                                endpoints, config['concurrency_per_replica'], config['request_timeout_seconds'], config['max_model_len'])
                try:
                    reports[arm_name] = await replay.run()
                    if arm_name.startswith('ifm_'): await replay.secondary()
                finally: await replay.close()
            # Plain article token lengths use this checkpoint's serving tokenizer,
            # distinct from the repeated input tokens spent across extraction calls.
            async with httpx.AsyncClient(timeout=60) as client:
                _, articles, _, _, _ = fixtures(Path(campaign['root']) / config['fixtures'])
                lengths = []
                for aid, article in articles.items():
                    response = await client.post(endpoints[0].removesuffix('/v1') + '/tokenize',
                        json={'model': 'clinical-extractor', 'prompt': article['text'], 'add_special_tokens': False})
                    response.raise_for_status()
                    lengths.append({'article_id': aid, 'words': len(article['text'].split()), 'model_tokens': response.json()['count']})
                write_json(result_root / 'article-lengths.json', {'articles': lengths,
                    'words': stats(r['words'] for r in lengths), 'model_tokens': stats(r['model_tokens'] for r in lengths),
                    'notice': 'Exact nine-article convenience sample, not a corpus-wide length distribution.'})
            async with httpx.AsyncClient(timeout=20) as client:
                for i, endpoint in enumerate(endpoints):
                    metrics = await client.get(endpoint.removesuffix('/v1') + '/metrics')
                    metrics.raise_for_status(); (result_root / f'server-{i}-metrics.txt').write_text(metrics.text)
            duration = time.monotonic() - started
            measured_arms = {a: json.loads((result_root / a / 'report.json').read_text()) for a in config['arms']}
            throughput = throughput_summary(model, measured_arms, duration)
            write_json(result_root / 'throughput.json', throughput)
            return {'status': 'completed', 'model': model['name'], 'gpu_stage_seconds': duration,
                    'gpu_count': count,
                    'throughput': throughput,
                    'arms': {a: {'tasks': r['tasks'], 'valid_tasks': r['valid_tasks'], 'scores': r['scores']} for a, r in reports.items()}}
        finally:
            primary_error = sys.exception()
            try:
                group.stop()
            except Exception as cleanup_error:
                write_json(result_root / 'server-cleanup-error.json', {'error': repr(cleanup_error)})
                if primary_error is None:
                    raise
            finally:
                if monitor:
                    monitor.terminate()
                    try: monitor.wait(timeout=10)
                    except subprocess.TimeoutExpired: monitor.kill(); monitor.wait()
                if log: log.close()


def throughput_summary(model, arms, gpu_stage_seconds=None):
    """Weight rates by elapsed time, never average replica/arm token rates."""
    samples = [(r, r.get('workflow_tokens_including_secondary', r['tokens'])) for r in arms.values()]
    seconds = sum(r['wall_seconds'] + r.get('secondary_summary', {}).get('wall_seconds', 0) for r, _ in samples)
    fresh = bool(samples) and all(t.get('fresh_measurement', t.get('aggregate_output_tokens_per_second') is not None) for _, t in samples)
    def total(key):
        values = [row.get(key) for _, t in samples for row in t['per_article']]
        return sum(values) if values and all(v is not None for v in values) else None
    output = total('output_tokens'); inputs = total('input_tokens'); count = gpu_count(model)
    rate = output / seconds if fresh and output is not None and seconds > 0 else None
    articles = {row['article_id'] for _, t in samples for row in t['per_article'] if row['requests_sent']}
    return {'gpu_count': count, 'fresh_measurement': fresh, 'evaluation_seconds': seconds,
            'gpu_stage_seconds': gpu_stage_seconds, 'total_input_tokens': inputs, 'total_output_tokens': output,
            'aggregate_output_tokens_per_second': rate,
            'output_tokens_per_gpu_second': rate / count if rate is not None else None,
            'aggregate_input_tokens_per_second': inputs / seconds if fresh and inputs is not None and seconds > 0 else None,
            'benchmark_output_tokens_per_allocated_second': output / gpu_stage_seconds
                if fresh and output is not None and gpu_stage_seconds and gpu_stage_seconds > 0 else None,
            'unique_articles_processed': len(articles),
            'complete_benchmark_articles_per_hour': len(articles) * 3600 / gpu_stage_seconds
                if fresh and gpu_stage_seconds and gpu_stage_seconds > 0 else None,
            'notice': 'Aggregate across all replicas and all arms, including secondary calls when present. '
                      'Evaluation rates exclude startup/warmup. Allocated-time rates include stage startup. '
                      'Articles/hour measures the complete configured benchmark, not a single extraction pass. '
                      'Missing usage stays unknown; resumed runs have no fresh throughput rate.'}


def report(campaign):
    require_cpu()
    if campaign['config'].get('benchmark_family') == 'glimmer':
        from .glimmer_benchmark import report_gauntlet
        return report_gauntlet(campaign)
    work = Path(campaign['work']); config = campaign['config']; results = []; failures = []
    for model in config['models']:
        root = work / 'results' / model['name']; result = {'model': model}
        for stage in ('download', 'gpu', 'cleanup'):
            path = root / (stage + '.json')
            result[stage] = json.loads(path.read_text()) if path.exists() else {'status': 'missing'}
        result['arms'] = {a: json.loads((root / a / 'report.json').read_text()) for a in config['arms'] if (root / a / 'report.json').exists()}
        result['throughput'] = throughput_summary(model, result['arms'], result['gpu'].get('gpu_stage_seconds'))
        write_json(root / 'throughput.json', result['throughput'])
        expected = result['gpu'].get('status') == 'completed' and result['cleanup'].get('status') == 'deleted'
        if not expected or len(result['arms']) != len(config['arms']): failures.append(model['name'])
        results.append(result)
    summary = {'models': results, 'failed_records': len(failures), 'failed_models': failures,
               'historical_scores': json.loads((Path(campaign['root']) / config['fixtures'] / 'historical-scores.json').read_text()),
               'weight_storage_exists': active_path(campaign).exists(),
               'notice': 'Clinical checklist scoring is source grounded but partial; attribution and physician reviews remain pending.'}
    write_json(work / 'summary.json', summary)
    lines = ['# K2 HiPerGator benchmark', '', '| Model | Arm | Required facts raw / delivered | Forbidden raw / delivered | Valid tasks | Output tokens/s (all GPUs) |',
             '| --- | --- | --- | --- | --- | --- |']
    for row in results:
        for name, arm in row['arms'].items():
            scores = arm['scores']; rate = arm['tokens']['aggregate_output_tokens_per_second']
            lines.append(f"| {row['model']['name']} | {name} | {scores['raw']['matched']} / {scores['delivered']['matched']} of 161 | {scores['raw']['forbidden_violations']} / {scores['delivered']['forbidden_violations']} of 36 | {arm['valid_tasks']}/{arm['tasks']} | {rate:.2f} |" if rate is not None else
                         f"| {row['model']['name']} | {name} | {scores['raw']['matched']} / {scores['delivered']['matched']} of 161 | {scores['raw']['forbidden_violations']} / {scores['delivered']['forbidden_violations']} of 36 | {arm['valid_tasks']}/{arm['tasks']} | unavailable |")
    lines += ['', '## Throughput across all replicas', '',
              '| Model | GPUs | Output tokens/s | Output tokens/GPU-second | GPU stage seconds | Complete benchmark articles/hour |',
              '| --- | ---: | ---: | ---: | ---: | ---: |']
    def shown(value): return f'{value:.2f}' if value is not None else 'unavailable'
    for row in results:
        t = row['throughput']
        lines.append(f"| {row['model']['name']} | {t['gpu_count']} | {shown(t['aggregate_output_tokens_per_second'])} | {shown(t['output_tokens_per_gpu_second'])} | {shown(t['gpu_stage_seconds'])} | {shown(t['complete_benchmark_articles_per_hour'])} |")
    lines += ['', 'Rates aggregate all replicas and all arms, including secondary calls. Evaluation token rates exclude startup and warmup; articles/hour includes GPU-stage startup and all benchmark arms. Queue waiting is excluded. Missing usage and resumed runs have unavailable rates.', '',
              'Failed/incomplete models: ' + (', '.join(failures) or 'none'), '',
              'Matched uses frozen prompts, historical clinical gates, T=0/top_p=1, low reasoning and 8k/16k output caps. IFM-low/medium/high use T=1/top_p=.95 and identical 32k output caps; high is the publisher recommendation, low/medium are speed-quality experiments. Historical hosted timings are not local GPU throughput.', '',
              'Visual interpretation is unavailable and omitted; captions and text-based figure attribution remain available. Secondary discovery/attribution results and pending review forms are in each IFM arm folder.', '',
              'The 197 checks are a partial checklist, not comprehensive medical precision/recall. See summary.json for historical comparison, per-article token mean/median/P95 and missing-usage counts.']
    (work / 'SUMMARY.md').write_text('\n'.join(lines) + '\n')
    return {'summary': str(work / 'summary.json'), 'markdown': str(work / 'SUMMARY.md'), 'failed_records': len(failures)}


def stage(stage_name, work, model_name=None):
    campaign = read_campaign(work)
    model = next((m for m in campaign['config']['models'] if m['name'] == model_name), None)
    if stage_name in {'download', 'gpu', 'cleanup'} and not model: raise ValueError('Unknown campaign model')
    output = Path(work) / 'results' / model_name / (stage_name + '.json') if model else Path(work) / (stage_name + '.json')
    try:
        if stage_name == 'setup': result = setup(campaign)
        elif stage_name == 'download': return download(campaign, model)  # Preserve the detailed download audit.
        elif stage_name == 'gpu': result = asyncio.run(gpu(campaign, model))
        elif stage_name == 'cleanup': result = cleanup(campaign, model)
        elif stage_name == 'report': return report(campaign)
        else: raise ValueError('Unknown stage')
        write_json(output, result)
        return result
    except BaseException as exc:
        write_json(output, {'status': 'failed', 'stage': stage_name, 'error_type': type(exc).__name__, 'error': str(exc)})
        if stage_name == 'download':
            # This download has unwound its ownership lock and saved its failure.
            # Cancel only its still-dependent GPU job to avoid waiting for allocated
            # GPUs just to discover that weights are incomplete. Cleanup remains
            # afterany on that canceled GPU job, then the serial chain continues.
            jobs_path = Path(work) / 'jobs.json'
            if jobs_path.exists():
                jobs = json.loads(jobs_path.read_text())
                if jobs['submitted']:
                    for job in jobs['jobs']:
                        if job['stage'] == 'gpu' and job['model'] == model_name:
                            subprocess.run(['scancel', job['id']], check=False)
        raise


def add_parser(sub):
    cmd = sub.add_parser('hpg-benchmark', help='Sequential K2 or Glimmer medical benchmarks on B200s')
    actions = cmd.add_subparsers(dest='hpg_action', required=True)
    p = actions.add_parser('package', help='Create a small transfer archive; no model download')
    p.add_argument('--output', default='dist/openpatients2-k2-benchmark.tar.gz')
    for name in ('plan', 'submit'):
        p = actions.add_parser(name)
        p.add_argument('--config', default=DEFAULT_CONFIG); p.add_argument('--work-dir', required=True)
        p.add_argument('--account'); p.add_argument('--qos'); p.add_argument('--sif', help='Use an existing vLLM 0.30.0 Apptainer image')
    p = actions.add_parser('stage', help='Slurm worker; normally called by the generated job chain')
    p.add_argument('stage', choices=['setup', 'download', 'gpu', 'cleanup', 'report']); p.add_argument('--work-dir', required=True)
    p.add_argument('--model')
    p = actions.add_parser('report', help='Regenerate summary without model weights')
    p.add_argument('--work-dir', required=True)


def dispatch(args):
    root = Path.cwd()
    if args.hpg_action == 'package': return package(root, args.output)
    if args.hpg_action in {'plan', 'submit'}:
        return prepare(args.config, root, args.work_dir, args.hpg_action == 'submit', args.account, args.qos, args.sif)
    if args.hpg_action == 'report': return report(read_campaign(args.work_dir))
    return stage(args.stage, args.work_dir, args.model)
