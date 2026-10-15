"""Frozen clinical extraction benchmark for Nemotron Super and Muse Spark Open.

CPU stages download/hash/score/export; Slurm GPU stages only serve local files.
Servers run sequentially; Spark reserves eight B200s, other profiles at most two.
"""
from __future__ import annotations

import argparse
import asyncio
import copy
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
    LILICORR, PINS, PROFILES, arm_definitions, engine,
)

FORMAT = 'dense-nemotron-super/2'


def prepare(root, work, expected=260, hours=12, with_images=False, include_test=False,
            limit_articles=None, profiles=None):
    if not 1 <= hours <= 72 or (limit_articles is not None and limit_articles < 1):
        raise ValueError('Invalid hours/article limit')
    selected = list(PROFILES) if profiles is None else list(profiles)
    if not selected or len(set(selected)) != len(selected) or set(selected) - PROFILES.keys():
        raise ValueError('Select unique, supported profiles')
    definitions = {k: v for k, v in arm_definitions(with_images).items() if v['profile'] in selected}
    pixels = any(v['pixels'] for v in definitions.values())
    result = b.prepare(root, work, expected, download_media=pixels,
        splits=None if include_test else ('train', 'validation'), make_archive=False,
        freeze_images=pixels, article_selector=(lambda rows: [r['article_id'] for r in
            sorted(rows, key=lambda r: r['article_id'])[:limit_articles]]) if limit_articles else None)
    m = b.read(work / 'manifest.json')
    shutil.copyfile(root / 'docs/DENSE_NEMOTRON_SUPER.md', work / 'code/docs/DENSE_NEMOTRON_SUPER.md')
    m.update(format=FORMAT, git_head=os.environ.get('DENSE_SOURCE_REVISION') or m['git_head'],
             container=None, model=None, drafter=None,
             nemotron_super={'hours': hours, 'arms': definitions, 'profiles': selected,
                 'with_images': pixels, 'include_test': include_test, 'limit_articles': limit_articles,
                 'recipes': {name: copy.deepcopy(PROFILES[name]) for name in selected},
                 'pins': {model: PINS[model] for name in selected
                          for model in [PROFILES[name]['model']] + ([PROFILES[name]['drafter']] if 'drafter' in PROFILES[name] else [])},
                 'engines': {p.get('engine', 'nemotron'): engine(p) for name in selected for p in [PROFILES[name]]},
                 'timing_policy': 'One active article per server; synthetic startup probe is outside article timing. Configurations run sequentially; Spark uses eight B200s, others at most two.'})
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
            concurrency_per_endpoint=profile['concurrency'], summary_structured_output=False,
            max_figures=12 if definition['pixels'] else 0, focused_pixels=definition['pixels'])
        from .pilot_extract import PilotConfig
        PilotConfig.model_validate(am['extraction'])
        if not definition['pixels']:
            am['scope'] = {**b.SCOPE, 'figures': 'caption attribution only; no pixels',
                'ehr_stages': ['measurement_series_extraction'],
                'unsupported': b.SCOPE['unsupported'] + ['figure pixels', 'figure EHR labels', 'figure privacy flags']}
        am['files'] = b.inventory(arm, ['code', 'inputs', 'reference'])
        b.write(arm / 'manifest.json', am)
        m['files'][f'arms/{name}/manifest.json'] = b.sha(arm / 'manifest.json')
    b.write(work / 'manifest.json', m)
    b.write(work / 'prepared.json', {'manifest_sha256': b.sha(work / 'manifest.json')})
    return {**result, 'archive': None, 'arms': list(definitions),
            'article_runs': len(definitions) * m['article_count'], 'test_evaluated': include_test}


def check(work):
    m = b.check_snapshot(work)
    if (m.get('format') != FORMAT
            or b.read(work / 'prepared.json')['manifest_sha256'] != b.sha(work / 'manifest.json')):
        raise ValueError('Not an intact pinned Super campaign')
    plan = m['nemotron_super']
    for name, definition in plan['arms'].items():
        if definition != arm_definitions(True).get(name) or plan['recipes'][definition['profile']] != PROFILES[definition['profile']]:
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


def weights_path(work, model):
    from .muse_spark_recipe import DSPARK
    from .redhat_nemotron_recipe import DSPARK as REDHAT_DSPARK
    key = ('redhat-dspark' if model == REDHAT_DSPARK else
           'spark-draft' if model == DSPARK else 'lilicorr' if model == LILICORR else
           next(name for name, profile in PROFILES.items() if profile['model'] == model))
    return work / 'runtime/weights' / key


def validate_runtime(work, arm_name=None):
    runtime = work / 'runtime'
    owner = {'work': str(work.resolve()), 'manifest_sha256': b.sha(work / 'manifest.json')}
    if runtime.is_symlink() or b.read(runtime / 'owner.json') != owner:
        raise ValueError('Runtime ownership mismatch')
    record = b.read(work / 'runtime.json')
    expected_engines = b.read(work / 'manifest.json')['nemotron_super']['engines']
    if not record.get('ready') or record.get('engines') != expected_engines:
        raise ValueError('CPU setup has not completed for the pinned images')
    for name, info in record['files'].items():
        path = work / name
        if path.is_symlink() or not path.resolve().is_relative_to(runtime.resolve()):
            raise ValueError('Runtime artifact escapes campaign ownership')
        stat = path.stat()
        if stat.st_size != info['bytes'] or stat.st_mtime_ns != info['mtime_ns']:
            raise ValueError('CPU-prepared artifact changed: ' + name)
    if arm_name is not None:
        arm = work / 'arms' / arm_name
        if (arm / 'runtime').is_symlink() or not (arm / 'runtime').is_dir():
            raise ValueError('Missing campaign-owned arm runtime directory')
        local = b.read(arm / 'runtime.json')
        expected = {os.path.relpath(work / name, arm): info for name, info in record['files'].items()}
        if not local.get('ready') or any(expected.get(name) != info for name, info in local['files'].items()):
            raise ValueError('Arm runtime differs from CPU-prepared artifact inventory')
        profile = PROFILES[b.read(arm / 'manifest.json')['arm_profile']['profile']]
        if local.get('image') != engine(profile)['image']:
            raise ValueError('Arm image differs from pinned recipe')
        required = [weights_path(work, profile['model']) / 'config.json', runtime / engine(profile)['sif']]
        if 'drafter' in profile:
            required.append(weights_path(work, profile['drafter']) / 'config.json')
        if any(os.path.relpath(path, arm) not in local['files'] for path in required):
            raise ValueError('Arm runtime omits target/drafter/container')
    return record


def serving(work, name, shard=0):
    from .dense_benchmark_serving import ServingConfig
    definition = b.read(work / 'manifest.json')['nemotron_super']['arms'][name]
    profile = PROFILES[definition['profile']]
    runtime = engine(profile)
    cache = work / 'arms' / name / 'runtime/caches'
    spec = copy.deepcopy(profile['speculation'])
    if 'drafter' in profile:
        spec['model'] = str(weights_path(work, profile['drafter']))
    # This path is deliberately explicit: the drafter is not the serving target.
    return ServingConfig(name=name, backend='vllm', version=runtime['version'],
        image_reference=runtime['image'], sif=str(work / 'runtime' / runtime['sif']),
        model_path=str(weights_path(work, profile['model'])), model_id=profile['model'],
        served_model_name=profile.get('served_model', 'nemotron-3.5-super-vl'), container_python='/usr/bin/python3',
        replicas=1, tensor_parallel=profile['gpus'], expert_parallel=profile.get('expert_parallel', False),
        reasoning_parser=profile.get('reasoning_parser', 'nemotron_v3'), max_model_len=b.read(work / 'manifest.json')['context'],
        max_num_seqs=profile['max_num_seqs'], max_batched_tokens=profile['max_batched_tokens'],
        gpu_memory_utilization=profile['gpu_memory'], speculative_config=spec,
        extra_args=['--generation-config', 'vllm', '--chat-template',
                    str(weights_path(work, profile['model']) / 'chat_template.jinja')] + profile['extra_args'],
        # The target/draft snapshots share one read-only mount; caches are arm-local.
        bind_files={str(work / 'runtime/weights'): str(work / 'runtime/weights')},
        environment={'PYTHONDONTWRITEBYTECODE': '1', 'VLLM_WORKER_MULTIPROC_METHOD': 'spawn',
            'HF_HOME': str(cache / 'hf'), 'HF_HUB_CACHE': str(cache / 'hf/hub'),
            'HF_XET_CACHE': str(cache / 'hf/xet'), 'HF_MODULES_CACHE': str(cache / 'hf/modules'),
            'VLLM_CACHE_ROOT': str(cache / 'vllm'), 'TRITON_CACHE_DIR': str(cache / 'triton'),
            'TORCHINDUCTOR_CACHE_DIR': str(cache / 'inductor'), 'XDG_CACHE_HOME': str(cache / 'xdg'),
            **profile.get('environment', {})},
        validation_note=profile['adaptation'] + ' Context capped at 65,536 for the project comparison; requires real B200 smoke.')


CPU_PROBE = '''
import hashlib, json, pathlib, sys, vllm
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
for path, family in json.loads(sys.argv[1]).items():
    config = json.loads((pathlib.Path(path)/'config.json').read_text())
    assert config['architectures'][0] in ModelRegistry.get_supported_archs()
    if config['architectures'] == ['LiLiCorrDraftModel']:
        assert config['block_size'] == 8 and config['dflash_config']['lilicorr_enabled']
        continue
    if config['architectures'] == ['MuseSparkDSparkModel']:
        assert config['block_size'] == 7 and config['model_type'] == 'muse_spark_dspark'
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
             if family == 'spark' else [{'enable_thinking': on} for on in [False, True]])
    rendered = [t.apply_chat_template([{'role':'user','content':'Say READY'}], tokenize=False,
                add_generation_prompt=True, **kw) for kw in modes]
    assert rendered[0] != rendered[1], 'Native reasoning modes must differ'
    if family == 'spark':
        assert t.convert_tokens_to_ids('<|eot|>') == 200008
        assert t.convert_tokens_to_ids('<|end_of_text|>') == 200001
        assert t.convert_tokens_to_ids('<|eom|>') not in [200001, 200008]
    else:
        assert '<think>' in rendered[1]
    assert all(t.encode(x) for x in rendered)
print(json.dumps({'vllm_version':vllm.__version__, 'models':json.loads(sys.argv[1]), 'native_thinking':True,
                  'source_hashes':source_hashes, 'source_hashes_verified':bool(sys.argv[2])}))
'''


def validate_checkpoint(path, model, require_weights=False):
    pin = PINS[model]
    config = b.read(path / 'config.json')
    if ('architecture' in pin and config.get('architectures') != [pin['architecture']]) or not config.get('architectures'):
        raise ValueError('Unexpected architecture: ' + model)
    if 'config_sha256' in pin and b.sha(path / 'config.json') != pin['config_sha256']:
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


def setup(work):
    m = check(work)
    start = time.time()
    runtime = work / 'runtime'
    runtime.mkdir(exist_ok=True)
    owner = {'work': str(work.resolve()), 'manifest_sha256': b.sha(work / 'manifest.json')}
    marker = runtime / 'owner.json'
    if runtime.is_symlink() or (marker.exists() and b.read(marker) != owner):
        raise ValueError('Runtime ownership mismatch')
    b.write(marker, owner)
    cache = runtime / 'download-cache'
    for path in [cache, runtime / 'weights'] + [runtime / e['sif'] for e in m['nemotron_super']['engines'].values()]:
        if path.is_symlink():
            raise ValueError('Runtime artifacts must be campaign-owned')
    from huggingface_hub import get_token, snapshot_download
    # Resolve the user's credential before switching to private campaign caches.
    # It stays in memory; no token or credential file enters a result artifact.
    token = get_token()
    os.environ.update(HF_HOME=str(cache), HF_XET_CACHE=str(cache / 'xet'),
        APPTAINER_CACHEDIR=str(runtime / 'apptainer-cache'), APPTAINER_TMPDIR=str(runtime / 'apptainer-tmp'))
    (runtime / 'apptainer-tmp').mkdir(exist_ok=True)
    # Validate every small config/access gate before any target weights download.
    metadata_patterns = ['config.json', 'chat_template.jinja', 'generation_config.json',
                         'hf_quant_config.json', 'tokenizer_config.json', 'processor_config.json',
                         '*.py', 'l4_200k_base', 'LICENSE*', 'USE_POLICY.md']
    for model, pin in m['nemotron_super']['pins'].items():
        path = weights_path(work, model)
        if path.is_symlink():
            raise ValueError('Refusing symlink weights')
        try:
            snapshot_download(repo_id=model, revision=pin['revision'], local_dir=path,
                cache_dir=cache / 'hub', allow_patterns=metadata_patterns, token=token)
        except Exception as exc:
            b.write(work / 'results/model-access-error.json', {'model_id': model, 'revision': pin['revision'],
                'error_type': type(exc).__name__, 'weights_download_started': False,
                'note': 'Check HF_TOKEN and approved access to gated checkpoints on the CPU setup job.'})
            raise
        validate_checkpoint(path, model)
    for model, pin in m['nemotron_super']['pins'].items():
        path = weights_path(work, model)
        snapshot_download(repo_id=model, revision=pin['revision'], local_dir=path,
            cache_dir=cache / 'hub', token=token,
            allow_patterns=['*.json', '*.jinja', '*.py', '*.safetensors', '*.model', '*.tiktoken', '*.txt',
                            'l4_200k_base', 'README.md', 'LICENSE*', 'USE_POLICY.md'])
        validate_checkpoint(path, model, require_weights=True)
        b.write(path / 'op2_snapshot.json', {'model_id': model, 'revision': pin['revision']})
    cpu_env = {'HF_HUB_OFFLINE': '1', 'TRANSFORMERS_OFFLINE': '1', 'CUDA_VISIBLE_DEVICES': '',
               'HF_HOME': str(runtime / 'cpu-cache/hf'), 'HF_MODULES_CACHE': str(runtime / 'cpu-cache/modules'),
               'VLLM_CACHE_ROOT': str(runtime / 'cpu-cache/vllm'), 'XDG_CACHE_HOME': str(runtime / 'cpu-cache/xdg'),
               'PYTHONDONTWRITEBYTECODE': '1'}
    sifs, probes = [], {}
    for engine_name, selected_engine in m['nemotron_super']['engines'].items():
        sif = runtime / selected_engine['sif']; sifs.append(sif)
        if not sif.exists():
            subprocess.run(['apptainer', 'pull', str(sif), selected_engine['image']], check=True)
        prefix = ['apptainer', 'exec', '--cleanenv', '--bind', f'{work}:{work}']
        for key, value in cpu_env.items():
            prefix += ['--env', f'{key}={value}']
        prefix += [str(sif), '/usr/bin/python3']
        profiles = [PROFILES[name] for name in m['nemotron_super']['profiles']
                    if PROFILES[name].get('engine', 'nemotron') == engine_name]
        models = {str(weights_path(work, model)): engine_name for profile in profiles
                  for model in [profile['model']] + ([profile['drafter']] if 'drafter' in profile else [])}
        probe = subprocess.run(prefix + ['-c', CPU_PROBE, json.dumps(models),
            selected_engine['tokenizer_source_sha'] or '', selected_engine['registry_source_sha'] or '',
            profiles[0].get('reasoning_parser', 'nemotron_v3')], text=True, capture_output=True)
        b.write(work / f'results/setup-probe-{engine_name}.json', {'returncode': probe.returncode, 'stdout': probe.stdout, 'stderr': probe.stderr})
        probe.check_returncode()
        probes[engine_name] = json.loads(probe.stdout.strip().splitlines()[-1])
        help_result = subprocess.run(prefix + ['-m', 'vllm.entrypoints.cli.main', 'serve', '--help=all'],
                                     text=True, capture_output=True)
        (work / f'results/vllm-serve-help-{engine_name}.txt').write_text(help_result.stdout + help_result.stderr)
        help_result.check_returncode()
        for profile in profiles:
            for flag in (arg.split('=')[0] for arg in profile['extra_args'] if arg.startswith('--')):
                if flag not in help_result.stdout:
                    raise ValueError('Pinned vLLM lacks recipe flag: ' + flag)
    files = sifs + [p for p in (runtime / 'weights').rglob('*') if p.is_file() and '.cache' not in p.parts]
    records = {str(p.relative_to(work)): {'sha256': b.sha(p), 'bytes': p.stat().st_size,
                                        'mtime_ns': p.stat().st_mtime_ns} for p in files}
    for name, definition in m['nemotron_super']['arms'].items():
        profile = PROFILES[definition['profile']]
        sif = runtime / engine(profile)['sif']
        arm = work / 'arms' / name
        if (arm / 'runtime').is_symlink():
            raise ValueError('Refusing symlink arm runtime')
        (arm / 'runtime').mkdir(exist_ok=True)
        model_dirs = [weights_path(work, profile['model'])]
        if 'drafter' in profile:
            model_dirs.append(weights_path(work, profile['drafter']))
        arm_files = {os.path.relpath(work / name_, arm): info for name_, info in records.items()
                     if work / name_ == sif or any((work / name_).is_relative_to(d) for d in model_dirs)}
        b.write(arm / 'runtime.json', {'ready': True, 'files': arm_files, 'image': engine(profile)['image'],
            'model_revision': PINS[profile['model']]['revision'], 'gpus': profile['gpus']})
    b.write(work / 'runtime.json', {'ready': True, 'files': records, 'engines': m['nemotron_super']['engines'],
        'setup_seconds': time.time() - start, 'setup_started_unix': start, 'cpu_probes': probes})
    return {'ready': True, 'runtime_files': len(records), 'engines': m['nemotron_super']['engines']}


async def smoke(endpoint, logs, config, pixels):
    """Require identical live tokenize/generation prompt counts before real data."""
    import base64
    import struct
    import zlib
    import httpx
    from .client import APIClient
    from .config import APIConfig
    from .muse_spark_recipe import SPARK
    kwargs = ({'reasoning_effort': 'low', 'current_date': config['template_date']}
              if config['model_id'] == SPARK else {'enable_thinking': False})
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
                    or type(count.get('max_model_len')) is not int or count['max_model_len'] < b.CONTEXT):
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
    b.write(logs / 'smoke.json', {'passed': True, 'outside_article_timing': True, 'cases': results})


async def worker(work, name):
    m = check(work)
    if name not in m['nemotron_super']['arms']:
        raise ValueError('Unknown arm')
    arm = work / 'arms' / name
    definition = m['nemotron_super']['arms'][name]
    profile = PROFILES[definition['profile']]
    validate_runtime(work, name)
    probe = ('import json,torch,vllm; '
        f'assert torch.cuda.device_count()=={profile["gpus"]}; '
        'assert all(torch.cuda.get_device_capability(i)==(10,0) for i in range(torch.cuda.device_count())); '
        'print(json.dumps({"vllm":vllm.__version__,"torch":torch.__version__,"gpus":torch.cuda.device_count()}))')
    async def startup(endpoint, logs):
        await smoke(endpoint, logs, b.read(arm / 'manifest.json')['extraction'], definition['pixels'])
    return await b.worker(arm, 0, campaigns=[arm],
        serving_factory=lambda _work, shard, _logs: serving(work, name, shard), runtime_probe=probe,
        startup_check=startup, budget_seconds=m['nemotron_super']['hours'] * 3600 - 120,
        article_timeout=1800)


def status(work):
    m = check(work)
    reports = {name: b.status(work / 'arms' / name) for name in m['nemotron_super']['arms']}
    return {'complete': all(not r['pending'] for r in reports.values()),
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
    pending = [name for name in m['nemotron_super']['arms'] if b.status(work / 'arms' / name)['pending']]
    if resume and (work / 'runtime.json').exists():
        for name in pending:
            validate_runtime(work, name)
    phases = ([('setup', None)] if pending and (not resume or not (work / 'runtime.json').exists()) else [])
    phases += [('worker', name) for name in pending] + [('aggregate', None)]
    (work / 'logs').mkdir(exist_ok=True)
    epoch = str(time.time_ns())
    common = ['sbatch', '--parsable', '--hold', '--nodes=1', '--ntasks=1', '--no-requeue',
              '--account=' + account, '--qos=' + qos, '--chdir=' + str(work / 'code'),
              '--export=ALL,OP2_SUBMISSION_ID=' + epoch, '--signal=B:TERM@120']
    commands = []
    for phase, name in phases:
        gpu = phase == 'worker'
        profile = PROFILES[m['nemotron_super']['arms'][name]['profile']] if gpu else None
        cmd = common + ['--job-name=op2-super-' + (name or phase),
            '--cpus-per-task=' + str(profile.get('cpus', 8) if gpu else 8),
            '--partition=' + ('hpg-b200' if gpu else 'hpg-default'),
            '--mem=' + (profile['host_memory'] if gpu else '32G'),
            '--time=' + (f'{m["nemotron_super"]["hours"]:02d}:00:00' if gpu else '08:00:00'),
            '--output=' + str(work / 'logs' / ((name or phase) + '-%j.log'))]
        if gpu:
            cmd += [f'--gres=gpu:b200:{profile["gpus"]}']
        commands.append(cmd + [str(work / 'code/scripts/dense_nemotron_super.sbatch'), phase, str(work), name or '-'])
    if dry_run:
        return {'submitted': False, 'commands_without_dependencies': commands,
                'dependency_policy': 'CPU setup afterok; GPU arms sequential afterany; CPU aggregate afterany all jobs',
                'maximum_simultaneous_gpus': max((PROFILES[m['nemotron_super']['arms'][name]['profile']]['gpus'] for name in pending), default=0)}
    ledger.mkdir(exist_ok=True)
    path = ledger / (epoch + '.json')
    jobs = []
    env = {k: v for k, v in os.environ.items() if not k.startswith('SBATCH_')}
    try:
        for (phase, name), cmd in zip(phases, commands):
            setup_ids = [j['id'] for j in jobs if j['phase'] == 'setup']
            previous = [j['id'] for j in jobs if j['phase'] == 'worker']
            deps = []
            if phase == 'worker':
                if setup_ids:
                    deps.append('afterok:' + ':'.join(setup_ids))
                if previous:
                    deps.append('afterany:' + previous[-1])
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
    check(work)
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
    b.checksum(archive)
    return {'archive': str(archive), 'bytes': archive.stat().st_size}


def aggregate(work):
    from .unified_scoring import score
    from .benchmark_reporting import throughput
    def f2(counts):
        denominator = 5 * counts['tp'] + 4 * counts['fn'] + counts['fp']
        return 5 * counts['tp'] / denominator if denominator else None
    m = check(work)
    if active_jobs(work, {'setup', 'worker'}):
        raise ValueError('Setup/GPU jobs are still active')
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
        'scoring_policy': {'semantic_judge_enabled': False,
            'item_matching': 'one-to-one evidence overlap and fuzzy names',
            'attribute_errors_scored_separately': True,
            'reference_is_physician_gold': False},
        'test_evaluated': m['nemotron_super']['include_test'], 'metrics': metrics, 'matched': matched,
        'arms': {name: {'configuration': m['nemotron_super']['arms'][name],
            'recipe': PROFILES[m['nemotron_super']['arms'][name]['profile']],
            'report': report, 'measured_worker_gpu_seconds': report['summed_worker_seconds'] * PROFILES[m['nemotron_super']['arms'][name]['profile']]['gpus']}
                 for name, report in reports.items()},
        'interpretation': 'Publisher-recipe comparison, not an isolated quantization/speculation ablation. '
            'Sampling, GPU counts, engine concurrency and client concurrency differ by published recipe. '
            'LiLiCorr is the SAME NVFP4 target with a separate drafter, not an independent target model. '
            'Text arms share identical sources/captions; vision arms are reported separately. '
            'Full scores retain every planned reference, including failed/no predictions. Matched scores on partial runs may be selection-biased. '
            'Article GPU seconds exclude queue/setup/startup; worker GPU seconds include startup/probes/retries. '
            'Throughput uses summed article wall time and includes all task/repair output tokens; reasoning is a subset, never added again. '
            'Compare matched-article throughput to avoid partial-run workload bias. Worker goodput includes observed startup/retries. '
            'Use sacct for actual allocation costs, including hard-killed jobs with missing final receipts. '
            'One seed; annotation agreement only; no automatic model promotion.'}
    b.write(work / 'summary/comparison.json', result)
    lines = ['# Nemotron Super and Muse Spark Open comparison', '', f'Complete: {result["complete"]}. Test evaluated: {result["test_evaluated"]}.', '']
    for scope, splits in metrics.items():
        for split, arms in splits.items():
            lines += [f'## {scope}: {split}', '', '| Arm | Precision | Recall | Item F1 | F2 | Attribute accuracy |',
                      '|---|---:|---:|---:|---:|---:|']
            for name, values in arms.items():
                clinical = values['clinical']
                quality = ' | '.join(f'{clinical[k]:.4f}' if clinical[k] is not None else 'unavailable' for k in ('precision', 'recall', 'f1', 'f2'))
                attr = values['attribute_accuracy']
                lines.append(f'| {name} | {quality} | {attr if attr is not None else "unavailable"} |')
            lines += ['', '| Arm | Output tok/s | Answer tok/s | Doc/s | Doc/h | Output tok/GPU-s | GPUs |',
                      '|---|---:|---:|---:|---:|---:|---:|']
            for name, values in arms.items():
                rates = values['throughput']
                cells = ' | '.join(f'{rates[k]:.4f}' if rates[k] is not None else 'unavailable' for k in (
                    'output_tokens_per_second', 'answer_tokens_per_second', 'documents_per_second',
                    'documents_per_hour', 'output_tokens_per_gpu_second'))
                lines.append(f'| {name} | {cells} | {values["gpus_per_server"]} |')
            lines += ['', f'Matched completed articles in this scope: {matched[scope]["completed_articles"]}.', '']
    lines += [result['interpretation'], '',
        'Item detection and attribute correctness are separate: an item match can still have a wrong assertion, value, or time. Semantic judge scores have not been applied.', '',
        'Full scores, matched-article throughput, worker goodput, reasoning capture and fact/citation/trace links: comparison.json and arms/*/summary.', '']
    (work / 'summary/comparison.md').write_text('\n'.join(lines))
    files = b.inventory(work, ['results', 'summary', 'submissions'])
    for name in reports:
        files[f'arms/{name}/summary/integrity.json'] = b.sha(work / 'arms' / name / 'summary/integrity.json')
    if (work / 'runtime.json').exists():
        files['runtime.json'] = b.sha(work / 'runtime.json')
    b.write(work / 'summary/integrity.json', {'manifest_sha256': b.sha(work / 'manifest.json'), 'files': files})
    if result['complete']:
        verify(work)
    export(work)
    return result


def verify(work):
    m = check(work)
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
            'test_evaluated': result['test_evaluated'], 'note': 'Execution integrity, not clinical correctness.'}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    for command in ('prepare', 'setup', 'submit', 'worker', 'aggregate', 'status', 'verify', 'export', 'recipe'):
        p = sub.add_parser(command)
        if command == 'recipe':
            continue
        p.add_argument('--work', type=Path, required=True)
        if command == 'prepare':
            p.add_argument('--root', type=Path, default=Path.cwd())
            p.add_argument('--expected', type=int, default=260)
            p.add_argument('--hours', type=int, default=12)
            p.add_argument('--with-images', action='store_true')
            p.add_argument('--include-test', action='store_true')
            p.add_argument('--limit-articles', type=int)
            p.add_argument('--profiles', choices=list(PROFILES), nargs='+')
        if command == 'submit':
            p.add_argument('--account', default='cai5724'); p.add_argument('--qos', default='cai5724')
            p.add_argument('--resume', action='store_true'); p.add_argument('--dry-run', action='store_true')
        if command == 'worker':
            p.add_argument('--arm', required=True)
    args = parser.parse_args(argv)
    if args.command == 'recipe':
        result = {'engines': {p.get('engine', 'nemotron'): engine(p) for p in PROFILES.values()},
                  'pins': PINS, 'profiles': PROFILES, 'context_override': b.CONTEXT}
    else:
        work = args.work.resolve()
        if args.command == 'prepare':
            result = prepare(args.root.resolve(), work, args.expected, args.hours, args.with_images,
                             args.include_test, args.limit_articles, args.profiles)
        elif args.command == 'submit':
            result = submit(work, args.account, args.qos, args.resume, args.dry_run)
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
                b.write(work / 'results/aggregate-error.json', {'error_type': type(exc).__name__, 'error': str(exc)})
                export(work)
                raise
        else:
            result = globals()[args.command](work)
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
