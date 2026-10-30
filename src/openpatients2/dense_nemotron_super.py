"""Frozen clinical extraction benchmark, defaulting to Beam NVFP4.

CPU access preflight -> per-profile CPU download, GPU benchmark, CPU cleanup.
CPU jobs hash/score/export; GPU stages serve local files. Packed mode overlaps
workers within the group budget; historical model profiles remain explicit.
"""
from __future__ import annotations

import argparse
import asyncio
import copy
from contextlib import contextmanager
import fcntl
from functools import wraps
import hashlib
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import tarfile
import time

from . import dense_benchmark as b
from .nemotron_super_recipes import (
    DEFAULT_PROFILES, LILICORR, PINS, PROFILES, arm_definitions, engine,
)

FORMAT = 'dense-nemotron-super/2'
STORAGE_POLICY = 'download-benchmark-cleanup/1'
DOWNLOAD_MEMORY = '128G'
MKSQUASHFS_ARGS = '-processors 8 -mem 8G'
STARTUP_TIMEOUT_SECONDS = 5400


def prepare(root, work, expected=None, hours=12, with_images=True, include_test=False,
            limit_articles=None, profiles=None, splits=None, selection_seed=42, article_ids=None, scheduler='serial', beam_repair=False,
            competition=False):
    if not 1 <= hours <= 72 or (limit_articles is not None and limit_articles < 1):
        raise ValueError('Invalid hours/article limit')
    selected = list(DEFAULT_PROFILES) if profiles is None else list(profiles)
    if not selected or len(set(selected)) != len(selected) or set(selected) - PROFILES.keys():
        raise ValueError('Select unique, supported profiles')
    if scheduler not in {'serial', 'packed'}:
        raise ValueError('Unknown scheduler')
    from .super_packing import plan as packing_plan, create_slots
    scheduling = packing_plan(selected) if scheduler == 'packed' else {'mode': 'serial'}
    definitions = {k: v for k, v in arm_definitions(with_images).items() if v['profile'] in selected}
    pixels = any(v['pixels'] for v in definitions.values())
    from .benchmark_selection import METHOD, article_features, select_articles, source_for
    splits = tuple(('train',) if splits is None else splits)
    if not splits or len(set(splits)) != len(splits) or not set(splits) <= {'train', 'validation', 'test'}:
        raise ValueError('Select unique train/validation/test splits')
    if 'test' in splits and not include_test:
        raise ValueError('Test selection requires --include-test')
    competition_selection = None
    if competition:
        from .competition_selection import validate_selection
        selection_path = root / 'configs/hipergator/nemotron-super/competition-50.json'
        competition_selection = validate_selection(root, selection_path)
        # Validation returns a receipt; keep the complete versioned selection.
        competition_selection = b.read(selection_path)
        if splits != ('train',) or include_test or limit_articles is not None or article_ids is not None:
            raise ValueError('Competition campaign requires its frozen fifty training articles without selection overrides')
        if set(selected) != {'beam-nvfp4-dflash2', 'beam-nvfp4-no-spec'} or scheduler != 'packed' or with_images:
            raise ValueError('Competition uses matched Beam DFlash2/no-spec TP4 arms, packed and text-only')
        beam_repair = True
        article_ids = competition_selection['article_ids']
        reference = root / competition_selection['reference']['path']
        expected = 50
    else:
        reference = root / 'configs/hipergator/nemotron-super/reference.jsonl.gz'
    # Legacy explicit-count callers can still use their historical packet.
    # Default launches must never silently fall back to the old 260 reference.
    if not reference.exists() and expected is None:
        raise ValueError('Missing current Super reference snapshot; refresh and commit it before launching')
    if reference.exists() and not competition:
        metadata = b.read(reference.with_name('reference-manifest.json'))
        if metadata['sha256'] != b.sha(reference):
            raise ValueError('Current reference snapshot checksum mismatch; refresh the reference packet')
        if expected is None:
            expected = metadata['articles']
    result = b.prepare(root, work, expected, download_media=pixels,
        splits=splits, make_archive=False, reference_path=reference if reference.exists() else None,
        freeze_images=pixels, figure_limit=48, article_selector=lambda rows: select_articles(
            root, rows, limit_articles, selection_seed, article_ids))
    m = b.read(work / 'manifest.json')
    shutil.copyfile(root / 'docs/DENSE_NEMOTRON_SUPER.md', work / 'code/docs/DENSE_NEMOTRON_SUPER.md')
    if (root / 'docs/SEMANTIC_BENCHMARK.md').exists():
        shutil.copyfile(root / 'docs/SEMANTIC_BENCHMARK.md', work / 'code/docs/SEMANTIC_BENCHMARK.md')
    m.update(format=FORMAT, git_head=os.environ.get('DENSE_SOURCE_REVISION') or m['git_head'],
             container=None, model=None, drafter=None,
             nemotron_super={'storage_policy': STORAGE_POLICY, 'hours': hours, 'arms': definitions, 'profiles': selected,
                 'scheduler': scheduling,
                 'with_images': pixels, 'input_policy': 'supported-modalities-once' if with_images else 'text-only',
                 'include_test': 'test' in splits, 'limit_articles': limit_articles,
                 'selection': {'splits': list(splits), 'seed': selection_seed, 'article_ids': article_ids,
                     'method': 'explicit-ids' if article_ids else METHOD if limit_articles else 'all-eligible',
                     'strata': ['split', 'clinical_category', 'figures', 'tables', 'source_length', 'reference_patients', 'species'],
                     'policy': 'Category-balanced diagnostic sample; not population prevalence. Reference metadata is used only for selection/reporting, never inference.'},
                 'evaluation_features': {aid: article_features(row, source_for(root, aid))
                     for aid, row in b.rows(work / 'reference/unified.jsonl').items()},
                 'recipes': {name: copy.deepcopy(PROFILES[name]) for name in selected},
                 'pins': {model: PINS[model] for name in selected
                          for model in [PROFILES[name]['model']] + ([PROFILES[name]['drafter']] if 'drafter' in PROFILES[name] else [])},
                 'engines': {p.get('engine', 'nemotron'): engine(p) for name in selected for p in [PROFILES[name]]},
                 'timing_policy': 'One active article per server; synthetic probes are outside article timing. '
                     + ('Budgeted parallel waves; replicas of each profile claim distinct articles. At most eight B200s.' if scheduler == 'packed'
                        else 'Configurations run sequentially, using at most eight B200s.')})
    if competition:
        from .competition_campaign import configuration
        m['competition'] = configuration(root, competition_selection)
    m['files'] = b.inventory(work, ['code', 'inputs', 'reference'])
    for name, definition in definitions.items():
        profile = PROFILES[definition['profile']]
        arm = work / 'arms' / name
        arm.mkdir(parents=True)
        for folder in ('code', 'inputs', 'reference'):
            shutil.copytree(work / folder, arm / folder, copy_function=os.link)
        am = copy.deepcopy(m)
        am.pop('nemotron_super')
        am.update(experiment_arm=name, arm_profile=definition, text_only=not definition['pixels'],
                  context=profile.get('context', m['context']),
                  capture_speculation_metrics=True,
                  model={'id': profile['model'], 'revision': PINS[profile['model']]['revision']},
                  drafter=({'id': profile['drafter'], 'revision': PINS[profile['drafter']]['revision']}
                           if 'drafter' in profile else None),
                  template_sha256=PINS[profile['model']].get('template_sha256'),
                  template_git_blob=PINS[profile['model']].get('git_blobs', {}).get('chat_template.jinja'),
                  container=engine(profile)['image'])
        am['extraction'].update(model_id=profile['model'], revision=am['model']['revision'],
            served_model=profile.get('served_model', 'nemotron-3.5-super-vl'), gpus_per_endpoint=profile['gpus'],
            tokenizer_contract=engine(profile)['tokenizer_contract'], temperature=profile['temperature'],
            top_p=profile.get('top_p', .95), reasoning_strength=profile.get('reasoning_effort', 'medium'),
            template_date=profile.get('template_date'), stop_token_ids=profile.get('stop_token_ids'),
            vision_temperature=profile['vision_temperature'], top_k=None, nemotron_thinking=True,
            max_output_tokens=32768, max_retry_tokens=32768, max_length_retry_tokens=49152,
            require_full_output_budget=False, min_output_tokens=16384, timeout_seconds=1200,
            staged_timeline=True, timeline_segment_batch_size=4, timeline_relation_batch_size=3,
            concurrency_per_endpoint=profile['concurrency'], summary_structured_output=False,
            max_figures=48 if definition['pixels'] else 0, focused_pixels=definition['pixels'])
        if profile.get('engine') == 'beam':
            am['extraction'].update(timeline_bounded_context=True, timeline_prefer_windows=beam_repair,
                timeline_scope_repair=beam_repair, bounded_document_stages=beam_repair,
                timeline_segment_batch_size=4 if beam_repair else 2,
                timeline_relation_batch_size=2,
                timeline_target_batch_size=8)
            if beam_repair:
                am['beam_repair_policy'] = 'scope-routing-bounded-documents/2'
            am['article_timeout_seconds'] = 14400 if beam_repair else 3600
            if competition:
                am['extraction'].update(timeline_sparse_relations=True, bounded_clinical_extraction=True,
                    literal_series_time_cells=True)
                am['beam_repair_policy'] = 'source-coverage-sparse-timeline-fhir/3'
                am['article_timeout_seconds'] = 3600
        if profile.get('engine') == 'mistral':
            am['article_timeout_seconds'] = 14400
            am['extraction'].update(timeline_bounded_context=True, timeline_prefer_windows=True,
                timeline_segment_batch_size=4, timeline_relation_batch_size=4, timeline_target_batch_size=16)
        from .pilot_extract import PilotConfig
        PilotConfig.model_validate(am['extraction'])
        if not definition['pixels']:
            am['scope'] = {**b.SCOPE, 'figures': 'caption attribution only; no pixels',
                'ehr_stages': ['measurement_series_extraction'],
                'unsupported': b.SCOPE['unsupported'] + ['figure pixels', 'figure EHR labels', 'figure privacy flags']}
        if profile.get('engine') == 'beam' and beam_repair:
            am['scope']['clinical'] = ('Live discovery over all source segments; oversized requests use bounded '
                'windows and model-reconciled patient routing. Original sources/omissions retained; '
                'semantic coverage unverified. Measurement rows have separate coverage receipts.')
        am['files'] = b.inventory(arm, ['code', 'inputs', 'reference'])
        b.write(arm / 'manifest.json', am)
        m['files'][f'arms/{name}/manifest.json'] = b.sha(arm / 'manifest.json')
    if scheduler == 'packed':
        create_slots(work, m)
    b.write(work / 'manifest.json', m)
    b.write(work / 'prepared.json', {'manifest_sha256': b.sha(work / 'manifest.json')})
    return {**result, 'archive': None, 'arms': list(definitions),
            'article_runs': len(definitions) * m['article_count'], 'test_evaluated': 'test' in m['splits']}


def check(work):
    m = b.check_snapshot(work)
    if (m.get('format') != FORMAT
            or b.read(work / 'prepared.json')['manifest_sha256'] != b.sha(work / 'manifest.json')):
        raise ValueError('Not an intact pinned Super campaign')
    plan = m['nemotron_super']
    if plan.get('scheduler', {}).get('mode') == 'packed':
        from .super_packing import plan as packing_plan
        if plan['scheduler'] != packing_plan(plan['profiles'], pipelined=plan['scheduler'].get('version', 1) >= 2):
            raise ValueError('Packed resource plan differs from frozen code')
    # Older frozen campaigns may contain both conditions. Keep inspection and
    # cleanup compatible while new preparation selects only one per profile.
    allowed_arms = {**arm_definitions(False), **arm_definitions(True)}
    for name, definition in plan['arms'].items():
        if definition != allowed_arms.get(name) or plan['recipes'][definition['profile']] != PROFILES[definition['profile']]:
            raise ValueError('Serving recipe differs from frozen code')
        b.check_snapshot(work / 'arms' / name)
    if any(PINS.get(model) != pin for model, pin in plan['pins'].items()):
        raise ValueError('Model pin differs from frozen code')
    expected_engines = {PROFILES[name].get('engine', 'nemotron'): engine(PROFILES[name]) for name in plan['profiles']}
    if plan['engines'] != expected_engines:
        raise ValueError('Container recipe differs from frozen code')
    return m


def active_jobs(work, phases=None):
    jobs = [j for p in (work / 'submissions').glob('*.json') for j in b.read(p)['jobs']
            if phases is None or j['phase'] in phases]
    if not jobs:
        return []
    result = subprocess.run(['squeue', '-h', '-u', os.environ['USER'], '-o', '%i'],
                             text=True, capture_output=True, check=True)
    return [j['id'] for j in jobs if str(j['id']) in result.stdout.split()]


def cancel(work, timeout_seconds=60):
    """Stop only this campaign, including a still-running bootstrap submission.

    Stop the bootstrap first so it cannot enqueue more jobs after we read its
    ledger. Re-read ledgers until all recorded jobs have left the user's queue.
    Frozen code, inputs, downloads and completed outputs are retained.
    """
    work = Path(work).resolve()
    bootstrap_file = Path(str(work) + '.bootstrap') / 'job-id.txt'

    def job_id(value):
        value = str(value).strip().split(';')[0]
        if not value.isascii() or not value.isdecimal() or int(value) < 1:
            raise ValueError('Invalid campaign Slurm job ID')
        return value

    bootstrap = {job_id(bootstrap_file.read_text())} if bootstrap_file.exists() else set()

    def recorded():
        ledgers = sorted((work / 'submissions').glob('*.json'))
        if not bootstrap and not ledgers:
            raise ValueError('No campaign job records found: ' + str(work))
        return bootstrap | {job_id(j['id']) for path in ledgers for j in b.read(path)['jobs']}

    requested = set()
    deadline = time.monotonic() + timeout_seconds
    while True:
        known = recorded()
        queue = subprocess.run(['squeue', '-h', '-u', os.environ['USER'], '-o', '%i'],
                               text=True, capture_output=True, check=True)
        # A bootstrap can finish and publish its last ledger during squeue.
        known.update(recorded())
        active = known.intersection(queue.stdout.split())
        if not active:
            return {'stopped': True, 'work': str(work),
                    'cancellation_requested': sorted(requested, key=int), 'remaining_active': []}
        targets = (bootstrap & active) or active
        new = targets - requested
        if new:
            subprocess.run(['scancel', *sorted(new, key=int)], check=True)
            requested.update(new)
        if time.monotonic() >= deadline:
            raise TimeoutError('Campaign jobs are still stopping; rerun cancel. Active IDs: ' + ', '.join(sorted(active, key=int)))
        time.sleep(.5)


def weights_path(work, model):
    from .muse_spark_recipe import DSPARK
    from .redhat_nemotron_recipe import DSPARK as REDHAT_DSPARK
    from .beam_recipe import DFLASH2
    key = ('beam-draft' if model == DFLASH2 else 'redhat-dspark' if model == REDHAT_DSPARK else
           'spark-draft' if model == DSPARK else 'lilicorr' if model == LILICORR else
           next(name for name, profile in PROFILES.items() if profile['model'] == model))
    return work / 'runtime/weights' / key


def runtime_owner(work, create=False):
    runtime = work / 'runtime'
    owner = {'work': str(work.resolve()), 'manifest_sha256': b.sha(work / 'manifest.json')}
    if runtime.is_symlink():
        raise ValueError('Runtime ownership mismatch')
    if create:
        runtime.mkdir(exist_ok=True)
    marker = runtime / 'owner.json'
    if marker.is_symlink() or (marker.exists() and b.read(marker) != owner) or (not create and not marker.exists()):
        raise ValueError('Runtime ownership mismatch')
    if create and not marker.exists():
        b.write(marker, owner)
    return runtime


@contextmanager
def runtime_lock(work, create=False, shared=False):
    runtime = runtime_owner(work, create=create)
    path = runtime / 'lifecycle.lock'
    if path.is_symlink():
        raise ValueError('Refusing symlink runtime lock')
    with path.open('a') as lock:
        try:
            fcntl.flock(lock, (fcntl.LOCK_SH if shared else fcntl.LOCK_EX) | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ValueError('A campaign download, worker or cleanup still owns the runtime') from None
        yield runtime


def exclusive_runtime(func):
    @wraps(func)
    def locked(work, *args, **kwargs):
        with runtime_lock(work, create=func.__name__ == 'setup'):
            return func(work, *args, **kwargs)
    return locked


def profile_models(profile):
    recipe = PROFILES[profile]
    return [recipe['model']] + ([recipe['drafter']] if 'drafter' in recipe else [])


def validate_runtime(work, arm_name=None):
    runtime = runtime_owner(work)
    record = b.read(work / 'runtime.json')
    plan = b.read(work / 'manifest.json')['nemotron_super']
    if (not record.get('ready') or record.get('engines') != plan['engines']
            or record.get('storage_policy') != STORAGE_POLICY):
        raise ValueError('CPU setup has not completed for staged model downloads')
    if arm_name is None:
        return record
    profile_name = plan['arms'][arm_name]['profile']
    current = b.read(runtime / 'active-profile.json')
    if current.get('profile') != profile_name:
        raise ValueError('Requested model is not the active downloaded profile')
    staged = b.read(runtime / 'profiles' / (profile_name + '.json'))
    if not staged.get('ready') or current.get('receipt_sha256') != b.sha(runtime / 'profiles' / (profile_name + '.json')):
        raise ValueError('CPU model download has not completed')
    for name, info in staged['files'].items():
        path = owned_path(work, work / name)
        stat = path.stat()
        if stat.st_size != info['bytes'] or stat.st_mtime_ns != info['mtime_ns']:
            raise ValueError('CPU-prepared artifact changed: ' + name)
    arm = work / 'arms' / arm_name
    if (arm / 'runtime').is_symlink() or not (arm / 'runtime').is_dir():
        raise ValueError('Missing campaign-owned arm runtime directory')
    local = b.read(arm / 'runtime.json')
    expected = {os.path.relpath(work / name, arm): info for name, info in staged['files'].items()}
    if not local.get('ready') or local.get('files') != expected:
        raise ValueError('Arm runtime differs from CPU-prepared artifact inventory')
    profile = PROFILES[profile_name]
    if local.get('image') != engine(profile)['image']:
        raise ValueError('Arm image differs from pinned recipe')
    required = [weights_path(work, model) / PINS[model].get('config_file', 'config.json') for model in profile_models(profile_name)]
    required.append(runtime / engine(profile)['sif'])
    if any(os.path.relpath(path, arm) not in local['files'] for path in required):
        raise ValueError('Arm runtime omits target/drafter/container')
    return record


def owned_path(work, path):
    """Check every ancestor before reading or deleting campaign runtime artifacts."""
    runtime = work / 'runtime'
    relative = path.relative_to(runtime)
    if '..' in relative.parts or path == runtime:
        raise ValueError('Runtime artifact escapes campaign ownership')
    for parent in [path, *path.parents]:
        if parent.is_symlink():
            raise ValueError('Runtime artifact escapes campaign ownership')
        if parent == runtime:
            break
    if not path.resolve().is_relative_to(runtime.resolve()):
        raise ValueError('Runtime artifact escapes campaign ownership')
    return path


def serving(work, name, shard=0):
    from .dense_benchmark_serving import ServingConfig
    definition = b.read(work / 'manifest.json')['nemotron_super']['arms'][name]
    profile = PROFILES[definition['profile']]
    runtime = engine(profile)
    cache = work / 'arms' / name / 'runtime/caches' / f'replica-{shard}'
    spec = copy.deepcopy(profile['speculation'])
    if 'drafter' in profile:
        spec['model'] = str(weights_path(work, profile['drafter']))
    extra_args = list(profile['extra_args'])
    if definition['profile'] in {'redhat-fp8-dspark', 'redhat-fp8-no-spec'}:
        from .redhat_nemotron_recipe import quantization_overrides
        extra_args += ['--hf-overrides', json.dumps(quantization_overrides(
            b.read(weights_path(work, profile['model']) / 'config.json')))]
    environment = dict(profile.get('environment', {}))
    if definition['profile'] == 'spark':
        environment['VLLM_RUST_FRONTEND_PATH'] = str(work / 'arms' / name / 'code/scripts/vllm_rust_frontend.py')
    # This path is deliberately explicit: the drafter is not the serving target.
    return ServingConfig(name=name, backend='vllm', version=runtime['version'],
        image_reference=runtime['image'], sif=str(work / 'runtime' / runtime['sif']),
        model_path=str(weights_path(work, profile['model'])), model_id=profile['model'],
        model_config_file=PINS[profile['model']].get('config_file', 'config.json'),
        served_model_name=profile.get('served_model', 'nemotron-3.5-super-vl'), container_python='/usr/bin/python3',
        replicas=1, tensor_parallel=profile['gpus'], expert_parallel=profile.get('expert_parallel', False),
        reasoning_parser=profile.get('reasoning_parser', 'nemotron_v3'),
        max_model_len=b.read(work / 'arms' / name / 'manifest.json')['context'],
        max_num_seqs=profile['max_num_seqs'], max_batched_tokens=profile['max_batched_tokens'],
        gpu_memory_utilization=profile['gpu_memory'], speculative_config=spec,
        kv_cache_dtype=profile.get('kv_cache_dtype', 'auto'),
        startup_timeout_seconds=STARTUP_TIMEOUT_SECONDS,
        extra_args=['--generation-config', 'vllm', '--chat-template',
                    str(weights_path(work, profile['model']) / 'chat_template.jinja')] + extra_args,
        # The target/draft snapshots share one read-only mount; caches are arm-local.
        bind_files={str(work / 'runtime/weights'): str(work / 'runtime/weights')},
        environment={'PYTHONDONTWRITEBYTECODE': '1', 'VLLM_WORKER_MULTIPROC_METHOD': 'spawn',
            'HF_HOME': str(cache / 'hf'), 'HF_HUB_CACHE': str(cache / 'hf/hub'),
            'HF_XET_CACHE': str(cache / 'hf/xet'), 'HF_MODULES_CACHE': str(cache / 'hf/modules'),
            'VLLM_CACHE_ROOT': str(cache / 'vllm'), 'TRITON_CACHE_DIR': str(cache / 'triton'),
            'TORCHINDUCTOR_CACHE_DIR': str(cache / 'inductor'), 'XDG_CACHE_HOME': str(cache / 'xdg'),
            **environment, 'VLLM_ENGINE_READY_TIMEOUT_S': str(STARTUP_TIMEOUT_SECONDS)},
        validation_note=profile['adaptation'] + ' Requires real B200 smoke before article execution.')


CPU_PROBE = '''
import hashlib, json, os, pathlib, subprocess, sys, vllm
from transformers import AutoTokenizer
from vllm.model_executor.models import ModelRegistry
from vllm.reasoning import ReasoningParserManager
base = pathlib.Path(vllm.__file__).parent
source_hashes = {}
for module, expected in [('entrypoints/serve/tokenize/serving.py', sys.argv[2]), ('model_executor/models/registry.py', sys.argv[3])]:
    digest = hashlib.sha256((base/module).read_bytes()).hexdigest() if (base/module).exists() else None
    source_hashes[module] = digest
    if expected:
        assert digest == expected, 'Upstream vLLM source identity mismatch'
ReasoningParserManager.get_reasoning_parser(sys.argv[4])
overrides = json.loads(sys.argv[5])
for path, family in json.loads(sys.argv[1]).items():
    if family == 'mistral':
        from vllm.transformers_utils.config import get_config
        config = get_config(path, trust_remote_code=True, config_format='mistral').to_dict()
    else:
        config = json.loads((pathlib.Path(path)/'config.json').read_text())
    if str(path) in overrides:
        from vllm.model_executor.layers.quantization.compressed_tensors.compressed_tensors import CompressedTensorsConfig
        from vllm.model_executor.layers.vocab_parallel_embedding import VocabParallelEmbedding
        quant = CompressedTensorsConfig.from_config(overrides[str(path)]['quantization_config'])
        embedding = VocabParallelEmbedding.__new__(VocabParallelEmbedding)
        assert quant.get_quant_method(embedding, 'language_model.model.embed_tokens') is None
    assert config['architectures'][0] in ModelRegistry.get_supported_archs()
    if config['architectures'] == ['LiLiCorrDraftModel']:
        assert config['block_size'] == 8 and config['dflash_config']['lilicorr_enabled']
        continue
    if config['architectures'] == ['MuseSparkDSparkModel']:
        assert config['block_size'] == 7 and config['model_type'] == 'muse_spark_dspark'
        continue
    if config['architectures'] == ['DFlash2BeamDraftModel']:
        assert config['num_hidden_layers'] == 5 and config['num_target_layers'] == 52
        assert config['dflash_config']['block_size'] == 16
        assert config['dflash_config']['target_layer_ids'] == [4, 16, 28, 40, 46]
        assert config['dflash_config']['selector_top_k'] == 16
        assert config['dflash_config']['conv_kernel_size'] == 2
        assert config['draft_vocab_size'] == config['vocab_size'] == 151936
        continue
    if config['architectures'] == ['DSparkDraftModel']:
        assert config['block_size'] == 8 and config['speculators_model_type'] == 'dspark'
        assert config['sample_from_anchor']
        assert config['aux_hidden_state_layer_ids'] == [17, 37, 59, 79, 87]
        assert config['enable_confidence_head'] and config['confidence_head_with_markov']
        assert config['transformer_layer_config']['model_type'] == 'qwen3'
        assert config['speculators_config']['verifier']['architectures'] == ['NemotronH_Omni_Reasoning_V3']
        # vLLM converts this generic Speculators checkpoint to Qwen3DSparkModel;
        # its plain DSparkDraftModel registry entry serves a different family.
        from vllm.transformers_utils.configs.speculators.base import SpeculatorsConfig
        converted = SpeculatorsConfig.extract_transformers_pre_trained_config(config)
        assert converted['model_type'] == 'qwen3' and converted['block_size'] == 8
        assert converted['architectures'] == ['Qwen3DSparkModel']
        assert converted['eagle_aux_hidden_state_layer_ids'] == [17, 37, 59, 79, 87]
        assert converted['target_layer_ids'] == [16, 36, 58, 78, 86]
        assert converted['sample_from_anchor'] and not converted['dspark_bonus_anchor']
        assert 'Qwen3DSparkModel' in ModelRegistry.get_supported_archs()
        continue
    t = AutoTokenizer.from_pretrained(path, local_files_only=True, trust_remote_code=True)
    modes = ([{'reasoning_effort': effort, 'current_date':'2026-10-15'} for effort in ['low','xhigh']]
             if family == 'spark' else
             [{'reasoning_effort': effort, 'allow_missing_thinking': False} for effort in ['low','max']]
             if family == 'beam' else [{'reasoning_effort': effort} for effort in ['low','high']]
             if family == 'mistral' else [{'enable_thinking': on} for on in [False, True]])
    rendered = [t.apply_chat_template([{'role':'user','content':'Say READY'}], tokenize=False,
                add_generation_prompt=True, **kw) for kw in modes]
    assert rendered[0] != rendered[1], 'Native reasoning modes must differ'
    if family == 'spark':
        os.environ['VLLM_USE_RUST_FRONTEND'] = '1'
        from vllm import envs
        help_text = subprocess.run([envs.VLLM_RUST_FRONTEND_PATH, 'serve', '--help'],
            check=True, text=True, capture_output=True, timeout=60).stdout
        assert '--engine-ready-timeout-secs' in help_text, 'Rust engine startup timeout is unsupported'
        assert t.convert_tokens_to_ids('<|eot|>') == 200008
        assert t.convert_tokens_to_ids('<|end_of_text|>') == 200001
        assert t.convert_tokens_to_ids('<|eom|>') not in [200001, 200008]
    elif family == 'beam':
        assert config['model_type'] == 'beam'
        assert 'Reasoning effort: 32' in rendered[0] and 'Reasoning effort: 512' in rendered[1]
        assert rendered[1].endswith('<|start|>assistant')
        assert t.convert_tokens_to_ids('<|return|>') == 151674
        assert t.convert_tokens_to_ids('<|call|>') == 151675
        assert t.convert_tokens_to_ids('<|end|>') not in [151674, 151675]
        assert json.loads((pathlib.Path(path)/'generation_config.json').read_text())['eos_token_id'] == [151674, 151675]
    elif family == 'mistral':
        assert '[MODEL_SETTINGS]' in rendered[1] and '"high"' in rendered[1]
    else:
        assert '<think>' in rendered[1]
    assert all(t.encode(x) for x in rendered)
print(json.dumps({'vllm_version':vllm.__version__, 'models':json.loads(sys.argv[1]), 'native_thinking':True,
                  'source_hashes':source_hashes, 'source_hashes_verified':bool(sys.argv[2])}))
'''


def validate_checkpoint(path, model, require_weights=False):
    pin = PINS[model]
    config_file = pin.get('config_file', 'config.json')
    config = b.read(path / config_file)
    if config_file == 'params.json':
        if config.get('n_layers') != 72 or not config.get('vision_encoder'):
            raise ValueError('Unexpected Mistral parameters')
    elif ('architecture' in pin and config.get('architectures') != [pin['architecture']]) or not config.get('architectures'):
        raise ValueError('Unexpected architecture: ' + model)
    if 'config_sha256' in pin and b.sha(path / config_file) != pin['config_sha256']:
        raise ValueError('Unexpected model config: ' + model)
    if 'template_sha256' in pin and b.sha(path / 'chat_template.jinja') != pin['template_sha256']:
        raise ValueError('Unexpected chat template: ' + model)
    for name, expected in pin.get('git_blobs', {}).items():
        data = (path / name).read_bytes()
        blob = hashlib.sha1(b'blob ' + str(len(data)).encode() + b'\0' + data).hexdigest()
        if blob != expected:
            raise ValueError('Pinned Git blob differs: ' + model + '/' + name)
    if require_weights and not list(path.glob('*.safetensors')):
        raise ValueError('No model weights downloaded: ' + model)


def download_environment(work):
    from huggingface_hub import get_token
    # Resolve credentials before switching to campaign-local caches. Never persist them.
    token = get_token()
    runtime = runtime_owner(work)
    cache = owned_path(work, runtime / 'download-cache')
    for path in (runtime / 'weights', runtime / 'apptainer-cache', runtime / 'apptainer-tmp', runtime / 'cpu-cache'):
        owned_path(work, path)
    os.environ.update(HF_HOME=str(cache), HF_HUB_CACHE=str(cache / 'hub'),
        HF_XET_CACHE=str(cache / 'xet'), HF_XET_CHUNK_CACHE_SIZE_BYTES='0',
        APPTAINER_CACHEDIR=str(runtime / 'apptainer-cache'), APPTAINER_TMPDIR=str(runtime / 'apptainer-tmp'))
    (runtime / 'apptainer-tmp').mkdir(exist_ok=True)
    return token, cache


@exclusive_runtime
def setup(work):
    m = check(work)
    if m['nemotron_super'].get('storage_policy') != STORAGE_POLICY:
        raise ValueError('Prepare a new campaign for staged downloads; old campaigns freeze their setup code')
    start = time.time()
    from huggingface_hub import snapshot_download
    token, cache = download_environment(work)
    metadata_patterns = ['params.json', 'config.json', 'chat_template.jinja', 'generation_config.json',
                         'hf_quant_config.json', 'tokenizer_config.json', 'processor_config.json',
                         '*.py', 'l4_200k_base', 'LICENSE*', 'USE_POLICY.md']
    # Access checks for every selected model stay small; no weights or images here.
    for model, pin in m['nemotron_super']['pins'].items():
        path = owned_path(work, weights_path(work, model))
        try:
            snapshot_download(repo_id=model, revision=pin['revision'], local_dir=path,
                cache_dir=cache / 'hub', allow_patterns=metadata_patterns, token=token)
        except Exception as exc:
            b.write(work / 'results/model-access-error.json', {'model_id': model, 'revision': pin['revision'],
                'error_type': type(exc).__name__, 'weights_download_started': False,
                'note': 'Check HF_TOKEN and approved access to gated checkpoints on the CPU setup job.'})
            raise
        validate_checkpoint(path, model)
    record = {'ready': True, 'files': {}, 'engines': m['nemotron_super']['engines'],
              'storage_policy': STORAGE_POLICY, 'setup_seconds': time.time() - start,
              'setup_started_unix': start}
    b.write(work / 'runtime.json', record)
    return record


def cleanup_profile(work, profile_name):
    """Caller owns the lifecycle lock; delete only this campaign's runtime data."""
    m = check(work)
    runtime = runtime_owner(work)
    names = m['nemotron_super']['profiles'] if profile_name is None else [profile_name]
    if any(name not in m['nemotron_super']['profiles'] for name in names):
        raise ValueError('Unknown campaign profile')
    marker = owned_path(work, runtime / 'active-profile.json')
    if profile_name is not None and marker.exists() and b.read(marker)['profile'] != profile_name:
        raise ValueError('Refusing cleanup of a different active profile')
    paths = {weights_path(work, model) for name in names for model in profile_models(name)}
    paths.update(runtime / engine(PROFILES[name])['sif'] for name in names)
    paths.update((runtime / engine(PROFILES[name])['sif']).with_suffix('.building.sif') for name in names)
    paths.update(runtime / folder for folder in (
        'download-cache', 'apptainer-cache', 'apptainer-tmp', 'cpu-cache'))
    # Check the full deletion set before the first mutation.
    for path in paths:
        owned_path(work, path)
    arm_caches = [work / 'arms' / name / 'runtime/caches'
                  for name, definition in m['nemotron_super']['arms'].items() if definition['profile'] in names]
    for path in arm_caches:
        for parent in [path, *path.parents]:
            if parent == work:
                break
            if parent.is_symlink():
                raise ValueError('Refusing symlink arm runtime')
    deleted = []
    for path in sorted(paths | set(arm_caches)):
        if path.exists():
            if path.is_dir():
                shutil.rmtree(path)
            else:
                path.unlink()
            deleted.append(str(path.relative_to(work)))
    if marker.exists():
        marker.unlink()
    receipt = {'profile': profile_name, 'deleted': deleted, 'cleaned_unix': time.time(),
               'note': 'Outputs, reasoning, model pins and hash inventories retained; runtime weights/caches/images removed.'}
    b.write(work / f'results/lifecycle/cleanup-{time.time_ns()}.json', receipt)
    return receipt


@exclusive_runtime
def _serial_cleanup(work, profile=None):
    # All-profile cleanup is a manual CPU operation, also supported for old campaigns.
    legacy = b.read(work / 'manifest.json')['nemotron_super'].get('storage_policy') != STORAGE_POLICY
    if (profile is None or legacy) and active_jobs(work):
        raise ValueError('Cancel/wait for all campaign jobs before cleaning the entire runtime')
    return cleanup_profile(work, profile)


def cleanup(work, profile=None):
    m = check(work)
    if m['nemotron_super'].get('scheduler', {}).get('mode') != 'packed':
        return _serial_cleanup(work, profile)
    if active_jobs(work):
        raise ValueError('Cancel/wait for all packed campaign jobs before cleanup')
    from .super_packing import slot
    profiles = m['nemotron_super']['profiles'] if profile is None else [profile]
    if any(p not in m['nemotron_super']['profiles'] for p in profiles):
        raise ValueError('Unknown campaign profile')
    return {'profiles': {p: _serial_cleanup(slot(work,p),p) for p in profiles
                         if (slot(work,p)/'runtime/owner.json').exists()}}


def build_image(work, sif, image):
    """Bound compression explicitly; host RAM/CPU detection ignores Slurm limits."""
    owned_path(work, sif)
    if sif.exists():
        subprocess.run(['apptainer', 'sif', 'list', str(sif)],
                       check=True, text=True, capture_output=True)
        return
    help_result = subprocess.run(['apptainer', 'build', '--help'],
                                 check=True, text=True, capture_output=True)
    if '--mksquashfs-args' not in help_result.stdout:
        raise RuntimeError('Load Apptainer 1.4+ with build --mksquashfs-args support; '
                           'refusing an unbounded container conversion on a Slurm CPU job')
    # A killed build must never leave a partial image at the final serving path.
    temporary = owned_path(work, sif.with_suffix('.building.sif'))
    if temporary.exists():
        temporary.unlink()
    try:
        subprocess.run(['apptainer', 'build', '--mksquashfs-args', MKSQUASHFS_ARGS,
                        str(temporary), image], check=True)
        subprocess.run(['apptainer', 'sif', 'list', str(temporary)],
                       check=True, text=True, capture_output=True)
        temporary.replace(sif)
    finally:
        if temporary.exists():
            temporary.unlink()


def reuse_packed_artifacts(work, profile_name):
    """Hard-link verified immutable artifacts from a finished sibling download.

    Unlinking one runtime cannot remove another runtime's hard links. Never
    share mutable caches, and never update a linked checkpoint via HF download.
    """
    if work.parent.name != 'profiles':
        return set()
    parent = work.parent.parent
    manifest = check(parent)
    from .super_packing import slot
    profile = PROFILES[profile_name]
    reused = set()
    for donor_name in manifest['nemotron_super']['profiles']:
        donor = slot(parent, donor_name)
        if donor == work or not (donor / 'runtime/active-profile.json').exists():
            continue
        with runtime_lock(donor, shared=True):
            marker = b.read(donor / 'runtime/active-profile.json')
            if not marker.get('ready'):
                continue
            dm = check(donor)
            arm = next(iter(dm['nemotron_super']['arms']))
            validate_runtime(donor, arm)
            dp = PROFILES[donor_name]
            if engine(dp) == engine(profile):
                source = donor / 'runtime' / engine(dp)['sif']
                target = work / 'runtime' / engine(profile)['sif']
                if not target.exists():
                    os.link(source, target)
            for model in set(profile_models(profile_name)) & set(profile_models(donor_name)):
                source, target = weights_path(donor, model), weights_path(work, model)
                if target.exists():
                    continue  # Resume verifies/downloads its own existing snapshot.
                if any(p.is_symlink() for p in source.rglob('*')):
                    raise ValueError('Refusing symlink checkpoint reuse')
                target.parent.mkdir(parents=True, exist_ok=True)
                temporary = target.with_name(target.name + '.linking')
                if temporary.exists():
                    shutil.rmtree(temporary)
                shutil.copytree(source, temporary, copy_function=os.link,
                                ignore=shutil.ignore_patterns('.cache'))
                temporary.rename(target)
                reused.add(model)
    return reused


@exclusive_runtime
def download(work, profile_name):
    m = check(work)
    validate_runtime(work)
    if profile_name not in m['nemotron_super']['profiles']:
        raise ValueError('Unknown campaign profile')
    start = time.time()
    runtime = runtime_owner(work)
    marker = owned_path(work, runtime / 'active-profile.json')
    # A canceled cleanup can leave the last model resident. Free it before staging another.
    if marker.exists() and b.read(marker)['profile'] != profile_name:
        cleanup_profile(work, b.read(marker)['profile'])
    b.write(marker, {'profile': profile_name, 'ready': False})
    from huggingface_hub import snapshot_download
    token, cache = download_environment(work)
    profile = PROFILES[profile_name]
    models_to_download = profile_models(profile_name)
    reused = reuse_packed_artifacts(work, profile_name)
    # Build the image before downloading the large target so OCI layers and
    # Apptainer conversion scratch do not overlap the full model on disk.
    selected_engine = engine(profile)
    sif = owned_path(work, runtime / selected_engine['sif'])
    build_image(work, sif, selected_engine['image'])
    for folder in ('apptainer-cache', 'apptainer-tmp'):
        path = owned_path(work, runtime / folder)
        if path.exists():
            shutil.rmtree(path)
    (runtime / 'apptainer-tmp').mkdir(exist_ok=True)
    for model in models_to_download:
        pin = m['nemotron_super']['pins'][model]
        path = owned_path(work, weights_path(work, model))
        if model in reused:
            validate_checkpoint(path, model, require_weights=True)
            continue
        # A resumed staging job must not let HF mutate a sibling's inode.
        for existing in path.rglob('*') if path.exists() else []:
            if existing.is_file() and existing.stat().st_nlink > 1:
                temporary = existing.with_name(existing.name + '.private')
                shutil.copy2(existing, temporary)
                temporary.replace(existing)
        snapshot_download(repo_id=model, revision=pin['revision'], local_dir=path,
            cache_dir=cache / 'hub', token=token,
            allow_patterns=['*.json', '*.jinja', '*.py', '*.safetensors', '*.model', '*.tiktoken', '*.txt',
                            'l4_200k_base', 'README.md', 'LICENSE*', 'USE_POLICY.md'])
        validate_checkpoint(path, model, require_weights=True)
        b.write(path / 'op2_snapshot.json', {'model_id': model, 'revision': pin['revision']})
    # Exercise the worker's file gate on CPU too, before publishing readiness.
    # Do not construct ServerGroup here: rendering it inspects GPU visibility.
    from .dense_benchmark_serving import validate_serving_artifacts
    serving_checks = {}
    for name, definition in m['nemotron_super']['arms'].items():
        if definition['profile'] == profile_name:
            config = serving(work, name)
            validate_serving_artifacts(config, work / 'arms' / name)
            serving_checks[name] = {'model_id': config.model_id, 'model_config_file': config.model_config_file}
    cpu_env = {'HF_HUB_OFFLINE': '1', 'TRANSFORMERS_OFFLINE': '1', 'CUDA_VISIBLE_DEVICES': '',
               'HF_HOME': str(runtime / 'cpu-cache/hf'), 'HF_MODULES_CACHE': str(runtime / 'cpu-cache/modules'),
               'VLLM_CACHE_ROOT': str(runtime / 'cpu-cache/vllm'), 'XDG_CACHE_HOME': str(runtime / 'cpu-cache/xdg'),
               'PYTHONDONTWRITEBYTECODE': '1'}
    engine_name = profile.get('engine', 'nemotron')
    if engine_name == 'spark':
        cpu_env['VLLM_USE_RUST_FRONTEND'] = '1'
    prefix = ['apptainer', 'exec', '--cleanenv', '--bind', f'{work}:{work}']
    for key, value in cpu_env.items():
        prefix += ['--env', f'{key}={value}']
    prefix += [str(sif), '/usr/bin/python3']
    models = {str(weights_path(work, model)): engine_name for model in models_to_download}
    overrides = {}
    if profile_name in {'redhat-fp8-dspark', 'redhat-fp8-no-spec'}:
        from .redhat_nemotron_recipe import quantization_overrides
        path = weights_path(work, profile['model'])
        overrides[str(path)] = quantization_overrides(b.read(path / 'config.json'))
    probe = subprocess.run(prefix + ['-c', CPU_PROBE, json.dumps(models),
        selected_engine['tokenizer_source_sha'] or '', selected_engine['registry_source_sha'] or '',
        profile.get('reasoning_parser', 'nemotron_v3'), json.dumps(overrides)], text=True, capture_output=True)
    b.write(work / f'results/setup-probe-{profile_name}.json',
            {'returncode': probe.returncode, 'stdout': probe.stdout, 'stderr': probe.stderr})
    probe.check_returncode()
    probes = {engine_name: json.loads(probe.stdout.strip().splitlines()[-1])}
    # Even CLI help constructs vLLM's DeviceConfig and needs a detected GPU in
    # these CUDA images. Validate flags in the GPU probe, never on a CPU node.
    files = [sif] + [p for model in models_to_download for p in weights_path(work, model).rglob('*')
                     if p.is_file() and '.cache' not in p.parts]
    records = {}
    for path in files:
        owned_path(work, path)
        records[str(path.relative_to(work))] = {'sha256': b.sha(path), 'bytes': path.stat().st_size,
                                              'mtime_ns': path.stat().st_mtime_ns}
    receipt = {'ready': True, 'profile': profile_name, 'files': records,
               'serving_artifact_checks': serving_checks,
               'download_started_unix': start, 'download_seconds': time.time() - start, 'cpu_probes': probes,
               'image_build': {'method': 'apptainer build', 'mksquashfs_args': MKSQUASHFS_ARGS,
                               'slurm_memory': DOWNLOAD_MEMORY}}
    record_path = runtime / 'profiles' / (profile_name + '.json')
    owned_path(work, record_path)
    b.write(record_path, receipt)
    b.write(work / f'results/lifecycle/download-{profile_name}-{time.time_ns()}.json', receipt)
    for name, definition in m['nemotron_super']['arms'].items():
        if definition['profile'] != profile_name:
            continue
        arm = work / 'arms' / name
        if (arm / 'runtime').is_symlink():
            raise ValueError('Refusing symlink arm runtime')
        (arm / 'runtime').mkdir(exist_ok=True)
        b.write(arm / 'runtime.json', {'ready': True,
            'files': {os.path.relpath(work / key, arm): info for key, info in records.items()},
            'image': engine(profile)['image'], 'model_revision': PINS[profile['model']]['revision'],
            'gpus': profile['gpus']})
    b.write(marker, {'profile': profile_name, 'ready': True, 'receipt_sha256': b.sha(record_path)})
    return {'ready': True, 'profile': profile_name, 'runtime_files': len(records),
            'download_seconds': receipt['download_seconds']}


async def smoke(endpoint, logs, config, pixels, context=b.CONTEXT):
    """Require identical live tokenize/generation prompt counts before real data."""
    import base64
    import struct
    import zlib
    import httpx
    from .client import APIClient
    from .config import APIConfig
    from .muse_spark_recipe import SPARK
    from .beam_recipe import TARGETS as BEAM_TARGETS
    kwargs = ({'reasoning_effort': 'low', 'current_date': config['template_date']}
              if config['model_id'] == SPARK else
              {'reasoning_effort': 'low', 'allow_missing_thinking': False}
              if config['model_id'] in BEAM_TARGETS else {'reasoning_effort': 'low'}
              if config['model_id'].startswith('mistralai/Mistral-Large-4-') else {'enable_thinking': False})
    # A synthetic 32x32 RGB PNG; no reference or patient information in probes.
    def chunk(kind, value):
        return struct.pack('!I', len(value)) + kind + value + struct.pack('!I', zlib.crc32(kind + value))
    png = (b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('!2I5B', 32, 32, 8, 2, 0, 0, 0))
           + chunk(b'IDAT', zlib.compress((b'\x00' + b'\xff' * 96) * 32)) + chunk(b'IEND', b''))
    messages = [{'role': 'user', 'content': 'Say READY.'}]
    cases = [messages]
    if pixels:
        cases.append([{'role': 'user', 'content': [{'type': 'text', 'text': 'Describe this image briefly.'},
            {'type': 'image_url', 'image_url': {'url': 'data:image/png;base64,' + base64.b64encode(png).decode()}}]}])
    results = []
    async with httpx.AsyncClient(timeout=600) as http:
        client = APIClient(APIConfig(model=config['served_model'], response_format='prompt_json',
            temperature=config['temperature'], vision_temperature=config['vision_temperature'],
            top_p=config['top_p'], seed=config['seed'],
            extra_body={'chat_template_kwargs': kwargs,
                        **({'stop_token_ids': config['stop_token_ids']} if config.get('stop_token_ids') else {})}), http=http)
        for prompt in cases:
            tokenize = {'model': config['served_model'], 'messages': prompt, 'add_generation_prompt': True,
                'add_special_tokens': False, 'chat_template_kwargs': kwargs}
            response = await http.post(endpoint.removesuffix('/v1') + '/tokenize', json=tokenize)
            response.raise_for_status()
            count = response.json()
            if (type(count.get('count')) is not int or count['count'] < 1
                    or not isinstance(count.get('tokens'), list) or len(count['tokens']) != count['count']
                    or type(count.get('max_model_len')) is not int or count['max_model_len'] < context):
                raise ValueError('Invalid exact tokenizer contract')
            body = client.body('case_context', prompt, 64)
            body.update(stream=False)
            body.pop('stream_options')
            response = await http.post(endpoint + '/chat/completions', json=body)
            response.raise_for_status()
            answer = response.json()
            if answer.get('usage', {}).get('prompt_tokens') != count['count'] or not answer.get('choices'):
                raise ValueError('Tokenize/generation prompt token counts differ')
            results.append({'pixels': isinstance(prompt[0]['content'], list), 'prompt_tokens': count['count'],
                'temperature': body['temperature'], 'top_p': body['top_p'],
                'chat_template_kwargs': kwargs, 'response': answer})
    b.write(logs / 'smoke.json', {'passed': True, 'outside_article_timing': True,
                                'required_context': context, 'cases': results})


async def reasoning_smoke(endpoint, logs, config, context=b.CONTEXT):
    """Require saved reasoning through the actual streaming extraction client.

    Uses the production template/sampling/stop settings, including Spark xhigh
    and Beam max. A bounded synthetic trace may end at the token cap; its text
    must still be returned separately from the answer and preserved verbatim.
    """
    import httpx
    from .pilot_extract import PilotRunner
    messages = [{'role': 'user', 'content': 'Compute 17 times 19 plus 23. Check the arithmetic, then give the final number.'}]
    receipt = {'passed': False, 'outside_article_timing': True, 'synthetic': True,
               'messages': messages, 'model_id': config['model_id'], 'revision': config['revision']}
    async with httpx.AsyncClient(timeout=600) as http:
        runner = PilotRunner(config, logs / 'reasoning-smoke-client', [endpoint], context, 'targeted', http=http)
        try:
            client = runner.clients[0]
            receipt['request'] = client.body('case_context', messages, 512)
            count, _, _ = await runner.token_count(client, messages)
            result = await client.complete_once(endpoint, 'case_context', messages, 512)
            receipt.update(response=result.response(), metrics=result.metrics(), expected_prompt_tokens=count)
            if result.error:
                raise ValueError('Reasoning streaming probe failed: ' + result.error)
            if result.prompt_tokens != count:
                raise ValueError('Reasoning tokenize/generation prompt token counts differ')
            if not result.reasoning_text or not result.reasoning_text.strip():
                raise ValueError('Reasoning capture failed: endpoint did not return separate reasoning text; inspect reasoning-smoke.json and the configured parser/template')
            receipt['passed'] = True
        except Exception as exc:
            receipt.update(error_type=type(exc).__name__, error=str(exc))
            raise
        finally:
            await runner.close()
            b.write(logs / 'reasoning-smoke.json', receipt)


async def worker(work, name):
    with runtime_lock(work, shared=True):
        return await _worker(work, name)


def gpu_probe(profile):
    """Runs inside the serving container with --nv and allocated GPU visibility.

    Keep CLI diagnostics in the worker's probe.json even when argument discovery
    fails. This does not load model weights or include smoke tokens in scoring.
    """
    flags = [arg.split('=')[0] for arg in profile['extra_args'] if arg.startswith('--')]
    return f'''
import json, subprocess, sys, torch, vllm
assert torch.cuda.device_count() == {profile['gpus']}, 'Unexpected GPU count'
assert all(torch.cuda.get_device_capability(i) == (10, 0) for i in range(torch.cuda.device_count())), 'Expected B200-class GPUs'
help_result = subprocess.run([sys.executable, '-m', 'vllm.entrypoints.cli.main', 'serve', '--help=all'],
                             text=True, capture_output=True, timeout=120)
print(help_result.stdout, flush=True)
print(help_result.stderr, file=sys.stderr, flush=True)
help_result.check_returncode()
for flag in {flags!r}:
    if flag not in help_result.stdout:
        raise ValueError('Pinned vLLM lacks recipe flag: ' + flag)
print(json.dumps({{'vllm': vllm.__version__, 'torch': torch.__version__,
                  'gpus': torch.cuda.device_count(), 'serving_flags_verified': True}}))
'''


async def _worker(work, name):
    m = check(work)
    if name not in m['nemotron_super']['arms']:
        raise ValueError('Unknown arm')
    arm = work / 'arms' / name
    definition = m['nemotron_super']['arms'][name]
    profile = PROFILES[definition['profile']]
    validate_runtime(work, name)
    async def startup(endpoint, logs):
        manifest = b.read(arm / 'manifest.json')
        config = manifest['extraction']
        await smoke(endpoint, logs, config, definition['pixels'], manifest['context'])
        await reasoning_smoke(endpoint, logs, config, manifest['context'])
    replica = int(os.environ.get('OP2_WORKER_REPLICA', '0'))
    return await b.worker(arm, replica, campaigns=[arm],
        serving_factory=lambda _work, shard, _logs: serving(work, name, shard), runtime_probe=gpu_probe(profile),
        startup_check=startup, budget_seconds=m['nemotron_super']['hours'] * 3600 - 120,
        article_timeout=b.read(arm / 'manifest.json').get('article_timeout_seconds', 3600),
        consume_remaining_budget=bool(m.get('competition')))


def status(work):
    m = check(work)
    packed = m['nemotron_super'].get('scheduler', {}).get('mode') == 'packed'
    from .super_packing import slot
    def arm_work(name, definition):
        child = slot(work, definition['profile']) / 'arms' / name
        return child if packed and (child / 'results').exists() else work / 'arms' / name
    reports = {name: b.status(arm_work(name, definition)) for name, definition in m['nemotron_super']['arms'].items()}
    return {'complete': all(not r['pending'] for r in reports.values()),
        'scheduler': m['nemotron_super'].get('scheduler', {'mode': 'serial'}),
        'arms': {name: {**r, 'pending': len(r['pending']), 'pending_sample': r['pending'][:5]}
                 for name, r in reports.items()}, 'report': str(work / 'summary/comparison.md'),
        'archive_available': (work / 'results.tar.gz.sha256').exists()}


def submit(work, account, qos, resume=False, dry_run=False):
    m = check(work)
    ledger = work / 'submissions'
    if ledger.exists() and list(ledger.glob('*.json')) and not resume:
        raise ValueError('Already submitted; use --resume after all campaign jobs terminate')
    if active_jobs(work):
        raise ValueError('Campaign jobs are still active')
    plan = m['nemotron_super']
    if plan.get('storage_policy') != STORAGE_POLICY:
        raise ValueError('Prepare a new campaign to use staged CPU downloads; resume uses the old frozen code')
    pending = [name for name in plan['arms'] if b.status(work / 'arms' / name)['pending']]
    if plan.get('scheduler', {}).get('mode') == 'packed':
        from .super_packing import collect, submit as packed_submit
        collect(work, m)
        pending = [name for name in plan['arms'] if b.status(work / 'arms' / name)['pending']]
        return packed_submit(work, m, account, qos, pending, dry_run)
    ready = False
    if resume and (work / 'runtime.json').exists():
        validate_runtime(work)
        ready = True
    phases = [('setup', None)] if pending and not ready else []
    for profile_name in plan['profiles']:
        arms = [name for name in pending if plan['arms'][name]['profile'] == profile_name]
        if arms:
            phases += [('download', profile_name)] + [('worker', name) for name in arms] + [('cleanup', profile_name)]
    # A canceled final cleanup can leave weights even when all articles completed.
    if not pending and (work / 'runtime/active-profile.json').exists():
        phases.append(('cleanup', b.read(work / 'runtime/active-profile.json')['profile']))
    phases.append(('aggregate', None))
    (work / 'logs').mkdir(exist_ok=True)
    epoch = str(time.time_ns())
    common = ['sbatch', '--parsable', '--hold', '--nodes=1', '--ntasks=1', '--no-requeue',
              '--account=' + account, '--qos=' + qos, '--chdir=' + str(work / 'code'),
              '--export=ALL,OP2_SUBMISSION_ID=' + epoch, '--signal=B:TERM@120']
    commands = []
    for phase, name in phases:
        gpu = phase == 'worker'
        profile = PROFILES[m['nemotron_super']['arms'][name]['profile']] if gpu else None
        cmd = common + ['--job-name=op2-super-' + (name if gpu else phase + ('-' + name if name else '')),
            '--cpus-per-task=' + str(profile.get('cpus', 8) if gpu else 8),
            '--partition=' + ('hpg-b200' if gpu else 'hpg-default'),
            '--mem=' + (profile['host_memory'] if gpu else DOWNLOAD_MEMORY if phase == 'download' else '32G'),
            '--time=' + (f'{m["nemotron_super"]["hours"]:02d}:00:00' if gpu else '08:00:00'),
            '--output=' + str(work / 'logs' / ((name if gpu else phase + ('-' + name if name else '')) + '-%j.log'))]
        if gpu:
            cmd += [f'--gres=gpu:b200:{profile["gpus"]}']
        commands.append(cmd + [str(work / 'code/scripts/dense_nemotron_super.sbatch'), phase, str(work), name or '-'])
    if dry_run:
        return {'submitted': False, 'commands_without_dependencies': commands,
                'dependency_policy': 'CPU access setup; per profile CPU download afterok previous cleanup, GPU arms afterok download/sequential afterany, CPU cleanup afterany download/workers; aggregate afterany all jobs',
                'maximum_simultaneous_gpus': max((PROFILES[m['nemotron_super']['arms'][name]['profile']]['gpus'] for name in pending), default=0)}
    ledger.mkdir(exist_ok=True)
    path = ledger / (epoch + '.json')
    jobs = []
    env = {k: v for k, v in os.environ.items() if not k.startswith('SBATCH_')}
    try:
        for (phase, name), cmd in zip(phases, commands):
            setup_ids = [j['id'] for j in jobs if j['phase'] == 'setup']
            cleanups = [j['id'] for j in jobs if j['phase'] == 'cleanup']
            deps = []
            if phase == 'download':
                if setup_ids:
                    deps.append('afterok:' + setup_ids[-1])
                if cleanups:
                    deps.append('afterok:' + cleanups[-1])
            elif phase == 'worker':
                download_job = next(j for j in reversed(jobs) if j['phase'] == 'download')
                deps.append('afterok:' + download_job['id'])
                previous = [j['id'] for j in jobs if j['phase'] == 'worker'
                            and plan['arms'][j['arm']]['profile'] == plan['arms'][name]['profile']]
                if previous:
                    deps.append('afterany:' + previous[-1])
            elif phase == 'cleanup':
                group = [j['id'] for j in jobs if (j['phase'] == 'download' and j['arm'] == name)
                         or (j['phase'] == 'worker' and plan['arms'][j['arm']]['profile'] == name)]
                if group:
                    deps.append('afterany:' + ':'.join(group))
            elif phase == 'aggregate' and jobs:
                deps.append('afterany:' + ':'.join(j['id'] for j in jobs))
            if deps:
                cmd[1:1] = ['--dependency=' + ','.join(deps), '--kill-on-invalid-dep=yes']
            result = subprocess.run(cmd, text=True, capture_output=True, check=True, env=env)
            jid = result.stdout.strip().split(';')[0]
            if not jid.isdigit():
                raise ValueError('Unrecognized sbatch response: ' + result.stdout)
            jobs.append({'id': jid, 'phase': phase, 'arm': name, 'argv': cmd})
            b.write(path, {'status': 'held', 'jobs': jobs})
        for job in reversed(jobs):
            subprocess.run(['scontrol', 'release', job['id']], check=True)
        b.write(path, {'status': 'released', 'jobs': jobs})
    except BaseException:
        for job in jobs:
            subprocess.run(['scancel', job['id']], check=False)
        b.write(path, {'status': 'submission_failed_cancel_requested', 'jobs': jobs})
        raise
    return {'jobs': jobs, 'ledger': str(path)}


def export(work):
    m = check(work)
    if m['nemotron_super'].get('scheduler', {}).get('mode') == 'packed':
        if active_jobs(work, {'setup', 'download', 'worker', 'cleanup'}):
            raise ValueError('Packed campaign lifecycle jobs are still active; export after they stop')
        # Aggregation can fail before collect (for example on a missing scoring
        # dependency). Diagnostic archives must still include child arm results.
        from .super_packing import collect
        collect(work, m)
    archive = work / 'results.tar.gz'
    with tarfile.open(archive, 'w:gz') as tf:
        def add(path):
            if path.exists():
                tf.add(path, arcname=str(Path(work.name) / path.relative_to(work)),
                    filter=lambda info: None if any(x in Path(info.name).parts for x in
                        ('__pycache__', '.venv', 'client-venv', '.cache')) or info.name.endswith(('.pyc', '.pyo')) else info)
        for name in ('manifest.json', 'prepared.json', 'runtime.json', 'code', 'inputs', 'reference',
                     'results', 'summary', 'logs', 'submissions'):
            add(work / name)
        for name in b.read(work / 'manifest.json')['nemotron_super']['arms']:
            for folder in ('manifest.json', 'runtime.json', 'code', 'inputs', 'reference', 'results', 'summary'):
                add(work / 'arms' / name / folder)
        m = b.read(work / 'manifest.json')
        if m['nemotron_super'].get('scheduler', {}).get('mode') == 'packed':
            from .super_packing import slot
            for profile in m['nemotron_super']['profiles']:
                child = slot(work,profile)
                for folder in ('manifest.json', 'prepared.json', 'code', 'inputs', 'reference', 'runtime.json', 'results', 'logs'):
                    add(child / folder)
                # Article results already live in the canonical parent arms.
                for name in b.read(child / 'manifest.json')['nemotron_super']['arms']:
                    for folder in ('manifest.json', 'code', 'inputs', 'reference', 'runtime.json'):
                        add(child / 'arms' / name / folder)
    b.checksum(archive)
    return {'archive': str(archive), 'bytes': archive.stat().st_size}


def aggregate(work):
    from .unified_scoring import score
    from .benchmark_reporting import throughput, paired_speculation
    def f2(counts):
        denominator = 5 * counts['tp'] + 4 * counts['fn'] + counts['fp']
        return 5 * counts['tp'] / denominator if denominator else None
    m = check(work)
    if active_jobs(work, {'setup', 'download', 'worker', 'cleanup'}):
        raise ValueError('Setup/download/GPU/cleanup jobs are still active')
    if m['nemotron_super'].get('scheduler', {}).get('mode') == 'packed':
        from .super_packing import collect
        collect(work, m)
    reports = {name: b.aggregate(work / 'arms' / name) for name in m['nemotron_super']['arms']}
    refs = b.rows(work / 'reference/unified.jsonl')
    predictions = {name: b.rows(work / 'arms' / name / 'summary/predictions.jsonl') for name in reports}
    receipts = {name: {aid: b.completed_attempt(work / 'arms' / name, aid)[1] for aid in refs} for name in reports}
    metrics, matched = {}, {}
    for scope in ('text', 'vision'):
        names = [name for name, definition in m['nemotron_super']['arms'].items() if definition['pixels'] == (scope == 'vision')]
        if not names:
            continue
        paired = {aid for aid in refs if all(receipts[name][aid] for name in names)}
        matched[scope] = {'completed_articles': len(paired), 'article_ids': sorted(paired), 'by_split': {}}
        metrics[scope] = {}
        for split in m['splits']:
            subset = {aid: ref for aid, ref in refs.items() if ref['split'] == split}
            common = {aid: ref for aid, ref in subset.items() if aid in paired}
            metrics[scope][split] = {}
            matched[scope]['by_split'][split] = {}
            for name in names:
                profile = PROFILES[m['nemotron_super']['arms'][name]['profile']]
                scores = score(subset, {aid: p for aid, p in predictions[name].items() if aid in subset})
                clinical = scores['overall']['clinical_items']
                seconds = [r['wall_seconds'] for aid, r in receipts[name].items() if r and aid in subset]
                metrics[scope][split][name] = {'clinical': {**clinical, 'f2': f2(clinical)},
                    'completed_articles': len(seconds), 'planned_articles': len(subset),
                    'quality_measured': bool(seconds),
                    'attribute_accuracy': scores['overall']['attribute_accuracy'], 'roster_recall': scores['roster']['recall'],
                    'article_seconds': b.stats(seconds), 'gpus_per_server': profile['gpus'],
                    'completed_article_gpu_seconds': sum(seconds) * profile['gpus'],
                    'throughput': throughput([{**r, 'gpus_per_server': profile['gpus']}
                        for aid, r in receipts[name].items() if r and aid in subset])}
                paired_seconds = sum(receipts[name][aid]['wall_seconds'] for aid in common)
                matched[scope]['by_split'][split][name] = {
                    'scores': score(common, {aid: p for aid, p in predictions[name].items() if aid in common}) if common else None,
                    'articles': len(common), 'seconds': paired_seconds, 'gpu_seconds': paired_seconds * profile['gpus'],
                    'throughput': throughput([{**receipts[name][aid], 'gpus_per_server': profile['gpus']} for aid in common])}
    result = {'format': FORMAT, 'complete': all(r['complete'] for r in reports.values()),
        'all_extractions_valid': all(r['complete'] and r['all_extraction_tasks_valid'] == r['expected']
                                    and not r['articles_with_ehr_errors'] for r in reports.values()),
        'scoring_policy': {'semantic_judge_enabled': False,
            'item_matching': 'one-to-one evidence overlap and fuzzy names',
            'attribute_errors_scored_separately': True,
            'reference_is_physician_gold': False},
        'reasoning_policy': {'streaming_capture_smoke_required': True,
            'saved_fields': ['reasoning', 'reasoning_content', 'thinking', 'reasoning_details'],
            'provenance': 'Endpoint-returned reasoning only; original call ledgers include messages, model revision, generation settings and response.',
            'fact_links': 'Task-level call references accompany extracted facts/citations; reasoning is not verified evidence.',
            'training_filter_applied': False},
        'test_evaluated': m['nemotron_super']['include_test'], 'metrics': metrics, 'matched': matched,
        'arms': {name: {'configuration': m['nemotron_super']['arms'][name],
            'recipe': PROFILES[m['nemotron_super']['arms'][name]['profile']],
            'report': report, 'measured_worker_gpu_seconds': report['summed_worker_seconds'] * PROFILES[m['nemotron_super']['arms'][name]['profile']]['gpus']}
                 for name, report in reports.items()},
        'interpretation': 'Pinned serving comparison. Beam speculation arms share the same target, sampling, context and concurrency; '
            'only the drafter configuration differs. Comparisons across other model families can differ in all those settings. '
            'Text arms share identical sources/captions; vision arms are reported separately. '
            'Full scores retain every planned reference, including failed/no predictions. Matched scores on partial runs may be selection-biased. '
            'Article GPU seconds exclude queue/setup/startup; worker GPU seconds include startup/probes/retries. '
            'Throughput uses summed article wall time and includes all task/repair output tokens; reasoning is a subset, never added again. '
            'Compare matched-article throughput to avoid partial-run workload bias. Worker goodput includes observed startup/retries. '
            'Use sacct for actual allocation costs, including hard-killed jobs with missing final receipts. '
            'One seed; annotation agreement only; no automatic model promotion.'}
    from .benchmark_selection import cohort_report
    cohorts = cohort_report(refs, predictions, receipts, m['nemotron_super'].get('evaluation_features', {}))
    result['scheduler'] = m['nemotron_super'].get('scheduler', {'mode': 'serial'})
    result['beam_speculation_comparison'] = paired_speculation(receipts, m['nemotron_super']['arms'])
    if m.get('competition'):
        from .competition_campaign import summarize, export_dataset
        result['competition'] = m['competition']
        result['fhir'] = {name: summarize([r for r in values.values() if r], len(refs))
                          for name, values in receipts.items()}
        result['all_fhir_stages_complete'] = all(r['complete'] for r in result['fhir'].values())
        result['release_ready'] = False
        result['fhir_datasets'] = {name: export_dataset(work, name, sorted(refs)) for name in reports}
        b.write(work / 'summary/competition-fhir.json', result['fhir'])
    if result['scheduler']['mode'] == 'packed':
        from .super_packing import capture_accounting
        capture_accounting(work)
    result['observed_worker_span_goodput'] = {
        name: {'span_seconds': report['observed_worker_span_seconds'],
               'documents_per_hour': report['completed'] * 3600 / report['observed_worker_span_seconds']
                   if report['observed_worker_span_seconds'] and not report['worker_goodput']['missing_worker_receipts'] else None,
               'note': 'First worker start to last worker end, including startup and observed overlap/gaps; inspect missing worker receipts and sacct.'}
        for name, report in reports.items()}
    b.write(work / 'summary/cohorts.json', cohorts)
    cohort_lines = ['# Results by article category and difficulty', '',
        'Evaluation metadata stays outside prompts. Categories overlap across dimensions. '
        'Counts are articles, not independent patients. Small groups are descriptive; '
        'a balanced diagnostic sample does not estimate population performance. '
        'Missing predictions remain in the planned denominator. Item F1 is annotation agreement, not semantic or clinical adjudication.', '',
        '| Split | Dimension | Category | Arm | Planned | Completed | Predictions | Nonempty pipeline-valid | Item F1 | Doc/h |',
        '|---|---|---|---|---:|---:|---:|---:|---:|---:|']
    for group in cohorts['groups']:
        for name, values in group['arms'].items():
            f1, rate = values['clinical']['f1'], values['throughput']['documents_per_hour']
            cohort_lines.append(f'| {group["split"]} | {group["dimension"]} | {group["category"]} | {name} | '
                f'{len(group["article_ids"])} | {values["completed"]} | {values["predictions"]} | {values["nonempty_pipeline_valid"]} | '
                f'{f"{f1:.4f}" if f1 is not None else "unavailable"} | {f"{rate:.2f}" if rate is not None else "unavailable"} |')
    (work / 'summary/cohorts.md').write_text('\n'.join(cohort_lines) + '\n')
    b.write(work / 'summary/comparison.json', result)
    lines = ['# Clinical model comparison', '', f'Complete: {result["complete"]}. Test evaluated: {result["test_evaluated"]}.', '']
    lines += ['Category and difficulty breakdowns, with article membership and coverage: [cohorts.md](cohorts.md) and cohorts.json.', '']
    if result['scheduler']['mode'] == 'packed':
        lines += ['Packed execution: GPU counts and independent replica counts are recorded in scheduler.resources. '
                  'Article throughput below divides by summed article seconds; it is not aggregate replica throughput. '
                  'See observed_worker_span_goodput in comparison.json for overlap-aware wall-time rates, '
                  'and slurm-accounting.json / worker host_memory telemetry for resource evidence.', '']
    if not result['complete']:
        lines += ['INCOMPLETE: this archive contains diagnostic/partial results. '
                  'Archive creation does not mean the benchmark succeeded.', '']
    lines += ['## Pipeline validity', '',
              'Completed means the article attempt finished, not that every task passed. '
              'Schema validity can include EMPTY rosters and excludes EHR-stage errors. '
              'Nonempty pipeline-valid requires patients, passing extraction tasks, and no EHR errors; '
              'neither establishes clinical accuracy.', '',
              '| Arm | Nonempty patient articles | Empty prediction artifacts | Nonempty pipeline-valid | Empty roster vs reference disagreements |',
              '|---|---:|---:|---:|---:|']
    for name, report in reports.items():
        q = report['quality_summary']
        lines.append(f'| {name} | {q["nonempty_patient_articles"]} | {q["empty_prediction_articles"]} | '
                     f'{q["nonempty_pipeline_valid_articles"]} | {len(q["empty_roster_reference_disagreements"])} |')
    lines += ['',
              '| Arm | Schema-valid, including empty / planned | Failed tasks | Partial tasks | Articles with EHR errors | Length-limited calls |',
              '|---|---:|---:|---:|---:|---:|']
    for name, report in reports.items():
        tasks = report['extraction_task_statuses']
        length = report['length_limited_calls']
        lines.append(f'| {name} | {report["all_extraction_tasks_valid"]} / {report["expected"]} | '
                     f'{tasks.get("failed", 0)} | {tasks.get("partial", 0)} | '
                     f'{report["articles_with_ehr_errors"]} | {length if length is not None else "unavailable"} |')
    lines.append('')
    lines += ['## Source and timeline coverage', '',
        'A valid sparse graph covers its selected requests. Unselected event pairs remain unassessed; '
        'lower graph workload or schema validity alone is not an accuracy improvement. Clinical coverage decisions are model judgments.', '',
        '| Arm | Candidate pairs | Unassessed pairs | Failed pairs | Retained / quarantined edges | Non-direct clinical facts quarantined |',
        '|---|---:|---:|---:|---:|---:|']
    for name, report in reports.items():
        audits = report['extraction_audits']
        t, clinical = audits['timeline'], audits['clinical']
        lines.append(f'| {name} | {t["candidate_pairs"]} | {t["unassessed_pairs"]} | {t["failed_candidate_pairs"]} | '
            f'{t["retained_edges"]} / {t["quarantined_edges"]} | {clinical["attribution_quarantined_facts"]} |')
    lines += ['', 'Per-article coverage, unresolved cells and missing-audit counts: arms/*/summary/extraction-audits.json.', '']
    lines += ['## Cost by task', '',
              'Output includes reasoning. Out-of-batch candidates remain auditable in original ledgers; '
              'they are not accepted facts. Missing historical breakdowns are unavailable.', '',
              '| Arm | Task | Calls | Input tokens | Output tokens | Length-limited | Out-of-batch candidates |',
              '|---|---|---:|---:|---:|---:|---:|']
    for name, report in reports.items():
        for task, usage in (report.get('task_usage') or {}).items():
            cells = ' | '.join(str(usage[k]) if usage[k] is not None else 'unavailable' for k in
                ('model_calls','prompt_tokens','completion_tokens','length_limited_calls','out_of_batch_candidates'))
            lines.append(f'| {name} | {task} | {cells} |')
    lines.append('')
    lines += ['## Tokens per completed article', '',
              'Includes every extraction, timeline, EHR, enabled terminology and repair call; input includes repeated prompts, '
              'not just unique source text. Output includes reasoning. Unknown usage is not zero.', '',
              '| Arm | Mean input tokens | Mean output tokens | Mean reasoning tokens | Mean calls |',
              '|---|---:|---:|---:|---:|']
    for name, report in reports.items():
        rates = report['throughput']
        values = [rates[k] for k in ('mean_input_tokens_per_article', 'mean_output_tokens_per_article',
                                     'mean_reasoning_tokens_per_article', 'mean_model_calls_per_article')]
        lines.append('| ' + name + ' | ' + ' | '.join(f'{v:,.1f}' if v is not None else 'unavailable' for v in values) + ' |')
    lines.append('')
    lines += ['## Speculative decoding diagnostics', '',
              '| Arm | Articles with counters / completed | Accepted draft tokens / drafted | Acceptance |',
              '|---|---:|---:|---:|']
    for name, report in reports.items():
        spec = report['speculation_summary']
        ratio = spec['acceptance_fraction']
        label = f'{100 * ratio:.2f}%' if ratio is not None else 'unavailable'
        counts = (f'{spec["accepted_tokens"]:.0f} / {spec["drafted_tokens"]:.0f}'
                  if spec['drafted_tokens'] is not None else 'unavailable')
        lines.append(f'| {name} | {spec["articles_measured"]} / {spec["articles_expected"]} | {counts} | {label} |')
    lines += ['', 'Counter differences exclude startup probes. Missing/reset/unsupported counters are unavailable; '
              'acceptance alone does not measure speedup. Compare an otherwise identical speculation-off arm.', '']
    for comparison in result['beam_speculation_comparison']:
        ratio = comparison['pipeline_speedup']
        label = f'{ratio:.3f}x' if ratio is not None else 'unavailable'
        lines += [f'Beam matched pipeline speedup for {comparison["candidate"]["arm"]} versus speculation off: '
                  f'{label} ({comparison["articles"]} matched articles). Greater than 1 is faster. '
                  'Compare matched quality and output lengths before selecting a winner.', '']
    for scope, splits in metrics.items():
        for split, arms in splits.items():
            lines += [f'## {scope}: {split}', '', '| Arm | Completed / planned | Precision | Recall | Item F1 | F2 | Attribute accuracy |',
                      '|---|---:|---:|---:|---:|---:|---:|']
            for name, values in arms.items():
                clinical = values['clinical']
                quality = ' | '.join(f'{clinical[k]:.4f}' if clinical[k] is not None else 'unavailable' for k in ('precision', 'recall', 'f1', 'f2'))
                attr = values['attribute_accuracy']
                if not values['quality_measured']:
                    quality = ' | '.join(['not measured'] * 4)
                    attr = None
                completed = f'{values["completed_articles"]} / {values["planned_articles"]}'
                lines.append(f'| {name} | {completed} | {quality} | {attr if attr is not None else "unavailable"} |')
            lines += ['', '| Arm | Output tok/s | Answer tok/s | Attempt/s | Attempt/h | Prediction/h | Nonempty valid/h | Output tok/GPU-s | GPUs/server | Scheduled replicas |',
                      '|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|']
            for name, values in arms.items():
                rates = values['throughput']
                cells = ' | '.join(f'{rates[k]:.4f}' if rates[k] is not None else 'unavailable' for k in (
                    'output_tokens_per_second', 'answer_tokens_per_second', 'documents_per_second',
                    'documents_per_hour', 'prediction_documents_per_hour',
                    'nonempty_pipeline_valid_documents_per_hour', 'output_tokens_per_gpu_second'))
                profile = m['nemotron_super']['arms'][name]['profile']
                replicas = result['scheduler'].get('resources', {}).get(profile, {}).get('replicas', 1)
                lines.append(f'| {name} | {cells} | {values["gpus_per_server"]} | {replicas} |')
            common_count = next(iter(matched[scope]['by_split'][split].values()))['articles']
            lines += ['', f'Matched completed articles in this split: {common_count}.', '']
            coverage_note = ('All planned articles in this split completed across these arms.'
                             if common_count == sum(ref['split'] == split for ref in refs.values())
                             else 'This subset may be selection-biased and excludes unfinished articles.')
            lines += ['These shared completed attempts may still contain extraction failures. ' + coverage_note, '',
                      '| Arm | Matched precision | Matched recall | Matched item F1 |',
                      '|---|---:|---:|---:|']
            for name, paired in matched[scope]['by_split'][split].items():
                clinical = paired['scores']['overall']['clinical_items'] if paired['scores'] else {}
                cells = ' | '.join(f'{clinical[k]:.4f}' if clinical.get(k) is not None else 'not measured'
                                   for k in ('precision', 'recall', 'f1'))
                lines.append(f'| {name} | {cells} |')
            lines.append('')
    lines += [result['interpretation'], '',
        'Item detection and attribute correctness are separate: an item match can still have a wrong assertion, value, or time. Semantic judge scores have not been applied.', '',
        'Full scores, matched-article throughput, worker goodput, reasoning capture and fact/citation/trace links: comparison.json and arms/*/summary.', '']
    lines += ['## Reasoning capture', '',
              '| Arm | Calls with text reasoning | Calls without text reasoning | Facts with returned reasoning links |',
              '|---|---:|---:|---:|']
    for name, report in reports.items():
        counts = report['reasoning_capture']
        returned = counts.get('returned_text', 0)
        linked = report['fact_reasoning_index']['facts_with_returned_reasoning_links']
        lines.append(f'| {name} | {returned} | {sum(counts.values()) - returned} | {linked} |')
    lines += ['', 'Original reasoning remains in extraction/attempts.jsonl and ehr/calls/attempts.jsonl under each article attempt; '
              'enabled terminology calls are under fhir/packet/coding-calls. '
              'summary/fact-reasoning.jsonl links facts and source citations to those calls. Synthetic reasoning-smoke.json traces are excluded from article scoring and distillation data. '
              'Returned traces and their facts remain unreviewed for training.', '']
    if m.get('competition'):
        from .competition_campaign import markdown
        lines += markdown(result['fhir'])
    (work / 'summary/comparison.md').write_text('\n'.join(lines))
    error_path = work / 'results/aggregate-error.json'
    if error_path.exists():
        b.write(error_path, {**b.read(error_path), 'resolved_by_aggregation_unix': time.time(),
                            'note': 'Historical aggregation error; a later summary was produced.'})
    files = b.inventory(work, ['results', 'summary', 'submissions'])
    for name in reports:
        files[f'arms/{name}/summary/integrity.json'] = b.sha(work / 'arms' / name / 'summary/integrity.json')
    if (work / 'runtime.json').exists():
        files['runtime.json'] = b.sha(work / 'runtime.json')
    b.write(work / 'summary/integrity.json', {'manifest_sha256': b.sha(work / 'manifest.json'), 'files': files})
    if result['complete']:
        verify(work, from_aggregate=True)
    export(work)
    return result


def verify(work, *, from_aggregate=False):
    m = check(work)
    if m['nemotron_super'].get('scheduler', {}).get('mode') == 'packed':
        live = active_jobs(work)
        own = os.environ.get('SLURM_JOB_ID') if from_aggregate else None
        aggregate_ids = {str(j['id']) for p in (work/'submissions').glob('*.json')
                         for j in b.read(p)['jobs'] if j['phase'] == 'aggregate'}
        if any(str(j) != own or own not in aggregate_ids for j in live):
            raise ValueError('Packed campaign jobs are still active')
        from .super_packing import collect
        if not from_aggregate:
            collect(work, m)
    incomplete = []
    for name in m['nemotron_super']['arms']:
        progress = b.status(work / 'arms' / name)
        if progress['pending']:
            incomplete.append(f'{name}: {progress["completed"]}/{progress["expected"]} completed, '
                              f'{len(progress["pending"])} pending; worker outcomes={progress["worker_outcomes"]}')
    if incomplete:
        raise ValueError('Incomplete campaign (finished attempts are not necessarily valid extractions):\n' +
                         '\n'.join(incomplete) + '\nInspect status and worker receipts before resuming. '
                         'Resume retains the frozen code, dataset and time budget.')
    for name in m['nemotron_super']['arms']:
        b.verify(work / 'arms' / name)
    integrity = b.read(work / 'summary/integrity.json')
    if integrity['manifest_sha256'] != b.sha(work / 'manifest.json'):
        raise ValueError('Campaign manifest changed after scoring')
    for name, digest in integrity['files'].items():
        if b.sha(work / name) != digest:
            raise ValueError('Scored campaign output changed: ' + name)
    result = b.read(work / 'summary/comparison.json')
    if not result['complete']:
        raise ValueError('Incomplete comparison')
    return {'verified': True, 'arms': list(m['nemotron_super']['arms']),
            **({'all_fhir_stages_complete': result['all_fhir_stages_complete'], 'release_ready': False}
               if m.get('competition') else {}),
            'test_evaluated': result['test_evaluated'], 'note': 'Execution integrity, not clinical correctness.'}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    for command in ('prepare', 'setup', 'download', 'cleanup', 'submit', 'worker', 'aggregate', 'status', 'verify', 'cancel', 'export', 'recipe', 'packing-preflight'):
        p = sub.add_parser(command)
        if command == 'recipe':
            continue
        p.add_argument('--work', type=Path, required=True)
        if command == 'prepare':
            p.add_argument('--root', type=Path, default=Path.cwd())
            p.add_argument('--expected', type=int, help='Optional exact completed snapshot count assertion')
            p.add_argument('--hours', type=int, default=12)
            modalities = p.add_mutually_exclusive_group()
            modalities.add_argument('--with-images', dest='with_images', action='store_true', default=True,
                help='Run each profile once with images if supported (default)')
            modalities.add_argument('--text-only', dest='with_images', action='store_false',
                help='Run each profile once without image pixels')
            p.add_argument('--include-test', action='store_true')
            p.add_argument('--splits', nargs='+', choices=['train', 'validation', 'test'], default=['train'])
            p.add_argument('--selection-seed', type=int, default=42)
            p.add_argument('--article-ids', nargs='+')
            p.add_argument('--limit-articles', type=int)
            p.add_argument('--profiles', choices=list(PROFILES), nargs='+')
            p.add_argument('--scheduler', choices=['serial', 'packed'], default='serial')
            p.add_argument('--beam-repair', action='store_true', help='Experimental windowed timelines and four-hour article timeout')
            p.add_argument('--competition', action='store_true', help='Frozen 50-article paired Beam/FHIR campaign')
        if command == 'packing-preflight':
            p.add_argument('--wave', type=int, required=True)
        if command == 'submit':
            p.add_argument('--account', default='cai5724'); p.add_argument('--qos', default='cai5724')
            p.add_argument('--resume', action='store_true'); p.add_argument('--dry-run', action='store_true')
        if command == 'worker':
            p.add_argument('--arm', required=True)
        if command in ('download', 'cleanup'):
            p.add_argument('--profile', choices=list(PROFILES), required=command == 'download')
    args = parser.parse_args(argv)
    if args.command == 'recipe':
        result = {'engines': {p.get('engine', 'nemotron'): engine(p) for p in PROFILES.values()},
                  'pins': PINS, 'profiles': PROFILES, 'default_profiles': list(DEFAULT_PROFILES),
            'default_arms': {k: v for k, v in arm_definitions().items() if v['profile'] in DEFAULT_PROFILES},
            'default_input_policy': 'supported-modalities-once',
                  'context_override': b.CONTEXT}
    else:
        work = args.work.resolve()
        if args.command == 'prepare':
            result = prepare(args.root.resolve(), work, args.expected, args.hours, args.with_images,
                             args.include_test, args.limit_articles, args.profiles, args.splits,
                             args.selection_seed, args.article_ids, args.scheduler, args.beam_repair, args.competition)
        elif args.command == 'packing-preflight':
            from .super_packing import preflight
            result = preflight(work, args.wave)
        elif args.command == 'submit':
            result = submit(work, args.account, args.qos, args.resume, args.dry_run)
        elif args.command in ('download', 'cleanup'):
            result = globals()[args.command](work, args.profile)
        elif args.command == 'worker':
            def terminate(signum, frame):
                raise KeyboardInterrupt('Slurm termination; preserve partial results for explicit resume')
            signal.signal(signal.SIGTERM, terminate)
            result = asyncio.run(worker(work, args.arm))
            if result['status'] != 'done':
                raise SystemExit('Worker incomplete; inspect receipts, then resume after all jobs terminate')
        elif args.command == 'aggregate':
            try:
                result = aggregate(work)
            except Exception as exc:
                b.write(work / 'results/aggregate-error.json', {'error_type': type(exc).__name__, 'error': str(exc),
                                                              'failed_unix': time.time()})
                export(work)
                raise
        else:
            result = globals()[args.command](work)
    print(json.dumps(result, indent=2))
    if args.command == 'aggregate' and not result['complete']:
        raise SystemExit('Campaign incomplete; diagnostic archive saved. Inspect logs and worker probe receipts before retrying.')


if __name__ == '__main__':
    main()
