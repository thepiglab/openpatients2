"""Single-node Glimmer precision, reasoning and serving-layout gauntlet.

Uses the K2 campaign's ownership, scheduler and frozen clinical evaluation. The
layout workload is deliberately separate from the full quality benchmark.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
from collections import Counter
from pathlib import Path

import httpx
import jsonschema

from .data import write_json
from .hpg_eval import Replay, check_candidate, fixtures, stats
from .provenance import json_digest
from .serving import ServingConfig, ServerGroup

LEVELS = ('low', 'medium', 'high', 'xhigh')


def validate_config(config, root):
    from .hipergator import sha256, gpu_count
    template = root / config['chat_template']
    if template.is_symlink() or sha256(template) != config['chat_template_sha256']:
        raise ValueError('Glimmer template differs from the pinned corrected Meta template')
    if config['quality_layout'] != 'dp8-tp1':
        raise ValueError('Quality runs must use the fixed non-speculative TP1/DP8 control')
    tuning = config.get('benchmark_mode') == 'fp8_tuning'
    if tuning:
        if len(config['models']) != 1 or config['models'][0]['id'] != 'RedHatAI/Muse-Glimmer-30B-FP8-block':
            raise ValueError('The focused tuning campaign uses only the Red Hat FP8 verifier')
        if len({a['seed'] for a in config['arms'].values()}) < 3:
            raise ValueError('Tuning needs at least three independent quality seeds')
        if any(a['reasoning_effort'] != 'medium' or a['max_tokens'] != 32768 or a['retry_tokens'] != 32768
               for a in config['arms'].values()):
            raise ValueError('Keep medium reasoning and equal 32k budgets during throughput tuning')
    elif {a['reasoning_effort'] for n, a in config['arms'].items() if n != 'matched'} != set(LEVELS):
        raise ValueError('Benchmark all four published Glimmer reasoning strengths')
    layouts = config['layouts']
    names = set()
    for layout in layouts:
        name = layout['name']
        if name in names or not name.replace('-', '').isalnum(): raise ValueError('Invalid layout name')
        names.add(name)
        if gpu_count(layout) != 8: raise ValueError('All Glimmer layouts must use the same eight allocated GPUs')
        if layout['tensor_parallel'] % layout.get('decode_context_parallel', 1):
            raise ValueError('DCP must divide TP; it does not request additional GPUs')
        if layout.get('speculation') not in (None, 'dflash', 'dspark'):
            raise ValueError('Unknown Glimmer speculation method')
        if layout.get('kv_cache_dtype', 'auto') not in ('auto', 'fp8_e4m3'):
            raise ValueError('Unknown KV precision experiment')
        if layout.get('max_batched_tokens', config['max_batched_tokens']) < config['max_num_seqs'] * 16:
            raise ValueError('Prefill budget must fit the expanded DFlash warmup batch')
    if config['quality_layout'] not in names: raise ValueError('Missing quality control layout')
    for arm in config['arms'].values():
        if arm['reasoning_effort'] not in LEVELS: raise ValueError('Unsupported Glimmer reasoning strength')
    for name, arm in config['arms'].items():
        if name != 'matched' and (arm['temperature'], arm['top_p'], arm['top_k']) != (1., .95, 64):
            raise ValueError('Use the publisher sampling recipe for every reasoning sweep arm')
    for model in config['models']:
        if gpu_count(model) != 8 or model['expert_parallel']:
            raise ValueError('Glimmer is dense; use eight GPUs and independent DP replicas without EP')
        model['capabilities'] = {'vision': True}
        if model['reasoning_profile']['template_kwarg'] != 'reasoning_strength':
            raise ValueError('Glimmer needs reasoning_strength, not reasoning_effort, in template kwargs')
    sweep = config['throughput']
    if any(type(sweep[k]) is not int or sweep[k] < 1 for k in ('subset_requests', 'copies', 'repetitions')):
        raise ValueError('Throughput workload sizes must be positive integers')
    if any(type(c) is not int or c < 1 or c > config['max_num_seqs'] for c in sweep['concurrency_per_replica']):
        raise ValueError('Concurrency must fit the per-replica scheduler capacity')
    if sweep['arm'] not in config['arms']: raise ValueError('Unknown throughput sampling arm')
    drafter = json.loads((root / config['drafter_metadata']).read_text())
    if drafter['revision'] != config['drafter_revision']: raise ValueError('DFlash revision mismatch')
    if drafter['config']['block_size'] != 16:
        raise ValueError('The Meta DFlash head requires its trained 16-position block')
    if any(l.get('speculation') == 'dspark' for l in layouts):
        dspark = json.loads((root / config['dspark_metadata']).read_text())
        if dspark['revision'] != config['dspark_revision']: raise ValueError('DSpark revision mismatch')
        if dspark['config']['sample_from_anchor'] is not False or dspark['config']['block_size'] != 16:
            raise ValueError('This DSpark checkpoint requires its trained 16-token non-anchor layout')


def setup_plugins(campaign, sif, interpreter):
    """Install the official BNB extension on CPU; leave the shared SIF immutable."""
    from .hipergator import container_check, sha256
    work = Path(campaign['work']); config = campaign['config']
    if not config['container_plugins']:
        write_json(work / 'container-plugins-manifest.json', {})
        # Even --help constructs DeviceConfig in this CUDA build. Inspect the
        # installed source without importing vLLM or constructing its parser;
        # actual CLI parsing and GPU compatibility are checked in the GPU stage.
        code = '''import importlib.metadata as m,json
from pathlib import Path
d = m.distribution("vllm")
assert d.version == "0.30.0", d.version
root = Path(d.locate_file("vllm"))
source = "\\n".join(p.read_text() for p in root.rglob("*.py"))
flags = ["calculate_kv_scales", "enforce_eager", "max_num_batched_tokens", "speculative_config", "limit_mm_per_prompt"]
missing = [f for f in flags if f not in source and f.replace("_", "-") not in source]
assert not missing, missing
print(json.dumps({"vllm":d.version,"source_options":flags,"check":"source-only; GPU startup validates CLI"}))
'''
        container_check(['apptainer', 'exec', '--cleanenv', str(sif), interpreter, '-c', code],
            work / 'serving-cli-source.json', 'CPU serving source compatibility check')
        return  # Focused FP8/DFlash needs neither BNB packages nor a DSpark overlay.
    dest = work / 'container-plugins'
    subprocess.run(['uv', 'pip', 'install', '--python', sys.executable, '--target', str(dest),
                    '--no-deps', '--only-binary=:all:', *config['container_plugins']], check=True)
    manifest = {str(p.relative_to(dest)): sha256(p) for p in dest.rglob('*') if p.is_file() and p.suffix != '.pyc'}
    write_json(work / 'container-plugins-manifest.json', manifest)
    code = ('import importlib.metadata as m,json; '
            'print(json.dumps({k:m.version(k) for k in ["vllm-bnb-plugin","bitsandbytes"]})); '
            'assert m.version("vllm-bnb-plugin")=="0.0.3"; assert m.version("bitsandbytes")=="0.50.2"')
    container_check(['apptainer', 'exec', '--cleanenv', '--bind', f'{work}:{work}',
                     '--env', 'PYTHONPATH=' + str(dest), str(sif), interpreter, '-c', code],
                    work / 'container-plugins.json', 'CPU extension package check')
    # Read stock code from the image, apply only the documented target-unwrapping
    # fix, and bind that file for DSpark processes. No container rebuild or GPU install.
    source_code = ('import importlib.metadata as m,json; '
        'p=m.distribution("vllm").locate_file("vllm/v1/worker/gpu/spec_decode/dspark/utils.py"); '
        'print(json.dumps({"path":str(p),"source":p.read_text()}))')
    result = container_check(['apptainer', 'exec', '--cleanenv', str(sif), interpreter, '-c', source_code],
                             work / 'dspark-source.json', 'CPU DSpark compatibility source check')
    source = json.loads(result.stdout.strip().splitlines()[-1])
    patched = patch_dspark_source(source['source'])
    dest = work / 'container-patches/dspark-utils.py'; dest.parent.mkdir(exist_ok=True); dest.write_text(patched)
    write_json(work / 'dspark-patch.json', {'source_file': source['path'], 'overlay_file': str(dest),
        'source_sha256': hashlib.sha256(source['source'].encode()).hexdigest(), 'patched_sha256': sha256(dest),
        'change': 'target_inner = getattr(target_language_model, "model", target_language_model)',
        'reference': 'https://huggingface.co/abstract-extraordinary/Muse-Glimmer-30B-DSpark'})


def patch_dspark_source(source):
    original = '    target_inner = target_language_model.model\n'
    replacement = '    target_inner = getattr(target_language_model, "model", target_language_model)\n'
    if source.count(original) != 1:
        raise ValueError('Expected stock vLLM 0.30 DSpark source; refusing an ambiguous compatibility patch')
    return source.replace(original, replacement)


def template_probe(campaign, model):
    """Render and tokenize frozen prompts on CPU inside the actual serving image."""
    from .hipergator import container_check, active_path
    work = Path(campaign['work']); config = campaign['config']; path = active_path(campaign)
    source = Path(campaign['root']) / config['chat_template']
    target = path / 'chat_template.jinja'; shutil.copyfile(source, target)
    # Mount the fixtures explicitly: the checkout may live outside this campaign.
    fixture_path = Path(campaign['root']) / config['fixtures']
    setup = json.loads((work / 'setup.json').read_text())
    code = r'''
import hashlib,json,re,sys
from pathlib import Path
from transformers import AutoTokenizer
weights,template,requests,arms,context=sys.argv[1:]
tok=AutoTokenizer.from_pretrained(weights,local_files_only=True)
tok.chat_template=Path(template).read_text()
rows=[json.loads(l) for l in Path(requests).read_text().splitlines()]
out={}
for name,arm in json.loads(arms).items():
 counts=[]
 for row in rows:
  kwargs={'reasoning_strength':arm['reasoning_effort']}
  rendered=tok.apply_chat_template(row['messages'],tokenize=False,add_generation_prompt=True,**kwargs)
  directives=re.findall(r'Reasoning strength:\s*(low|medium|high|xhigh)\b',rendered)
  assert directives==[arm['reasoning_effort']], (name,directives)
  assert rendered.endswith('<|start|>assistant')
  count=len(tok.apply_chat_template(row['messages'],tokenize=True,add_generation_prompt=True,**kwargs))
  assert count+arm['retry_tokens']<=int(context), (name,count,arm['retry_tokens'])
  counts.append(count)
 out[name]={'requests_checked':len(counts),'max_prompt_tokens':max(counts),'reasoning_strength':arm['reasoning_effort']}
print(json.dumps({'template_sha256':hashlib.sha256(Path(template).read_bytes()).hexdigest(),'arms':out}))
'''
    probe = container_check(['apptainer', 'exec', '--cleanenv', '--bind', f'{work}:{work}',
        '--bind', f'{fixture_path}:{fixture_path}', setup['sif'], setup['container_python'], '-c', code,
        str(path / 'weights'), str(target), str(fixture_path / 'requests.jsonl'),
        json.dumps(config['arms']), str(config['max_model_len'])],
        work / 'results' / model['name'] / 'template-probe.json', 'CPU chat template/token budget check')
    checked = json.loads(probe.stdout.strip().splitlines()[-1])
    if checked['template_sha256'] != config['chat_template_sha256']:
        raise ValueError('Serving template hash mismatch')


def layout_skip(model, layout):
    if model['quantization'] == 'bitsandbytes':
        if layout['tensor_parallel'] != 1:
            return 'The official BNB loader rejects prequantized checkpoints with TP>1.'
        if layout.get('speculation'):
            return 'Speculative decoding with the BNB extension is outside the verified native Glimmer recipe.'
    if model['quantization'] == 'compressed-tensors' and layout['tensor_parallel'] == 8:
        return 'TP8 divides the 19,968-wide FFN into 2,496 columns, splitting FP8 128-element blocks.'
    if layout.get('speculation') and not model.get('test_dflash'):
        return 'No assistant downloaded for this checkpoint.'
    return None


def serving_config(campaign, model, layout):
    from .hipergator import active_path, cache_environment
    work = Path(campaign['work']); config = campaign['config']; path = active_path(campaign)
    setup = json.loads((work / 'setup.json').read_text())
    extra = ['--model-impl', 'vllm', '--chat-template-content-format', 'string', '--generation-config', 'vllm',
             '--chat-template', str(path / 'chat_template.jinja'), '--language-model-only',
             '--enable-auto-tool-choice', '--tool-call-parser', 'muse_glimmer',
             '--compilation-config', '{"cudagraph_mode":"PIECEWISE"}']
    if layout.get('enforce_eager'): extra += ['--enforce-eager']
    if layout.get('kv_cache_dtype') == 'fp8_e4m3': extra += ['--calculate-kv-scales']
    if layout.get('vision'):
        extra.remove('--language-model-only')
        extra += ['--limit-mm-per-prompt', '{"image":1,"video":0}']
    if model['quantization'] == 'bitsandbytes': extra += ['--load-format', 'bitsandbytes']
    speculation = None
    binds = {}
    method = layout.get('speculation')
    if method:
        # DFlash's first slot re-presents the accepted anchor (15 predictions).
        # This DSpark head samples after the anchor and predicts all 16 slots.
        speculation = {'model': str(path / ('dspark' if method == 'dspark' else 'drafter')), 'method': method,
                       'num_speculative_tokens': 16 if method == 'dspark' else 15,
                       'draft_sample_method': 'probabilistic'}
    if method == 'dspark':
        patch = json.loads((work / 'dspark-patch.json').read_text())
        from .hipergator import sha256
        if sha256(patch['overlay_file']) != patch['patched_sha256']: raise ValueError('DSpark overlay changed')
        binds = {patch['overlay_file']: patch['source_file']}
    return ServingConfig(name=model['name'] + '-' + layout['name'], backend='vllm', version=config['engine_version'],
        image_reference=config['image'], sif=setup['sif'], model_path=str(path / 'weights'), model_id=model['id'],
        container_python=setup['container_python'], replicas=layout['replicas'], tensor_parallel=layout['tensor_parallel'],
        data_parallel=1, expert_parallel=False, reasoning_parser='muse_glimmer',
        # Let config.json choose modelopt_mixed / FP8 / BNB. ModelOpt's producer
        # name "modelopt" is NOT the correct forced runtime method for mixed FP4.
        quantization=None, decode_context_parallel=layout.get('decode_context_parallel', 1),
        speculative_config=speculation, max_model_len=config['max_model_len'],
        max_num_seqs=layout.get('max_num_seqs', config['max_num_seqs']),
        startup_wave_size=config['startup_wave_size'],
        max_batched_tokens=layout.get('max_batched_tokens', config['max_batched_tokens']),
        gpu_memory_utilization=config['gpu_memory_utilization'],
        kv_cache_dtype=layout.get('kv_cache_dtype', 'auto'), prefix_cache=layout.get('prefix_cache', True), extra_args=extra, bind_files=binds,
        environment={**cache_environment(path), 'HF_HUB_OFFLINE': '1', 'TRANSFORMERS_OFFLINE': '1', 'HF_DATASETS_OFFLINE': '1',
            'PYTHONPATH': str(work / 'container-plugins'), 'PYTHONDONTWRITEBYTECODE': '1',
            'VLLM_CACHE_ROOT': str(path / 'runtime-cache/vllm'), 'TRITON_CACHE_DIR': str(path / 'runtime-cache/triton'),
            'TORCHINDUCTOR_CACHE_DIR': str(path / 'runtime-cache/inductor'), 'VLLM_WORKER_MULTIPROC_METHOD': 'spawn'},
        experimental=True, validation_note='Native vLLM recipe; each B200 layout requires on-cluster smoke and measurement')


def gpu_probe(campaign, model, cfg):
    from .hipergator import container_check, sha256, active_path
    work = Path(campaign['work']); setup = json.loads((work / 'setup.json').read_text())
    if sha256(cfg.sif) != setup['sha256']: raise ValueError('Container changed after CPU setup')
    manifest = json.loads((work / 'container-plugins-manifest.json').read_text())
    for name, expected in manifest.items():
        if sha256(work / 'container-plugins' / name) != expected: raise ValueError('Container extension changed: ' + name)
    code = ('import json,torch,vllm; from vllm.model_executor.models import ModelRegistry; '
            'from vllm.reasoning import ReasoningParserManager; '
            'from vllm.plugins import load_general_plugins; load_general_plugins(); '
            'assert vllm.__version__==' + repr(cfg.version) + '; '
            'assert "MuseGlimmerForConditionalGeneration" in ModelRegistry.get_supported_archs(); '
            'ReasoningParserManager.get_reasoning_parser("muse_glimmer"); '
            'assert torch.cuda.device_count()==' + str(cfg.replicas*cfg.tensor_parallel) + '; '
            'assert all(torch.cuda.get_device_capability(i)==(10,0) for i in range(torch.cuda.device_count())); '
            'print(json.dumps({"vllm":vllm.__version__,"torch":torch.__version__, '
            '"gpus":[torch.cuda.get_device_name(i) for i in range(torch.cuda.device_count())]}))')
    if model['quantization'] == 'bitsandbytes':
        code += '; from vllm.model_executor.layers.quantization import get_quantization_config; get_quantization_config("bitsandbytes")'
    args = ['apptainer', 'exec', '--nv', '--cleanenv', '--bind', f'{work}:{work}',
            '--env', 'CUDA_VISIBLE_DEVICES=' + os.environ['CUDA_VISIBLE_DEVICES']]
    for k, v in cfg.environment.items(): args += ['--env', k + '=' + v]
    args += [cfg.sif, cfg.container_python, '-c', code]
    container_check(args, work / 'results' / model['name'] / 'container-probe.json', 'Glimmer GPU architecture/parser/plugin check')


async def smoke(endpoints, model, config, output):
    """Real generation on every replica; final-channel parsing at every level."""
    from .hpg_eval import sampling_body
    jobs = [(e, 'low') for e in endpoints] + [(endpoints[0], l) for l in LEVELS[1:]]
    async def call(client, endpoint, level):
        arm = {'reasoning_effort': level, 'top_k': 64}
        body = {'model': 'clinical-extractor', 'messages': [{'role': 'system', 'content': 'You are a helpful assistant.'},
            {'role': 'user', 'content': 'Return only this JSON object in your final answer: {"ok":true}'}],
            'temperature': 1., 'top_p': .95, 'seed': 42, 'max_tokens': 8192, **sampling_body(model, arm)}
        response = await client.post(endpoint + '/chat/completions', json=body); response.raise_for_status()
        value = response.json(); choice = value['choices'][0]
        content = choice['message'].get('content') or ''
        attempt_path = output.parent / 'smoke-attempts' / (json_digest({'endpoint': endpoint, 'level': level}) + '.json')
        write_json(attempt_path, {'endpoint': endpoint, 'reasoning_strength': level, 'request': body, 'response': value})
        from .output_parser import parse_output
        try:
            if choice['finish_reason'] != 'stop' or parse_output(content).value != {'ok': True}:
                raise ValueError('final answer/termination mismatch')
        except ValueError as exc:
            raise ValueError(f'Smoke failed for {endpoint}/{level}: {exc}; response: {attempt_path}') from exc
        return {'endpoint': endpoint, 'reasoning_strength': level, 'response': value}
    async with httpx.AsyncClient(timeout=config['request_timeout_seconds']) as client:
        rows = await asyncio.gather(*(call(client, e, l) for e, l in jobs))
    write_json(output, {'status': 'passed', 'checks': rows})


def throughput_requests(requests, n):
    """Stratify across all extraction domains, patients and companion tasks."""
    selected = []
    for task in sorted({r['task'] for r in requests}):
        group = [r for r in requests if r['task'] == task]
        # Include a short and a long actual source prompt for every domain.
        group.sort(key=lambda r: (sum(len(m['content']) for m in r['messages']), json_digest(r['identity'])))
        for row in (group[0], group[-1]):
            if row not in selected: selected.append(row)
    if n < len(selected): raise ValueError('Throughput subset would omit a domain/length stratum')
    remainder = [r for r in requests if r not in selected]
    remainder.sort(key=lambda r: json_digest(r['identity'] | {'task': r['task']}))
    return (selected + remainder)[:n]


async def throughput_cell(campaign, model, layout, endpoints, concurrency, output):
    config = campaign['config']; sweep = config['throughput']; arm = config['arms'][sweep['arm']]
    replay = Replay(Path(campaign['root']) / config['fixtures'], output, model, arm, endpoints,
                    concurrency, config['request_timeout_seconds'], config['max_model_len'])
    subset = throughput_requests(replay.requests, sweep['subset_requests'])
    # Preserve workload order, seeds and prompt hashes across all layouts/precisions.
    workload = [(request, copy_index) for copy_index in range(sweep['copies']) for request in subset]
    write_json(output / 'workload.json', {'signature': json_digest(workload), 'requests': workload,
        'arm': arm, 'replica_assignment': 'request ordinal modulo replicas; no patient affinity',
        'prefix_cache': layout.get('prefix_cache', True),
        'cache_protocol': 'Warm repeated prompts when enabled; disabled-cache control uses the same requests.'})
    metrics = []; rounds = []
    try:
        # Exact tokenizer/context guards, outside the measured wall interval.
        for request in subset:
            count = await replay.token_count(replay.clients[0], request['messages'])
            if count + arm['max_tokens'] > config['max_model_len']: raise ValueError('Throughput context guard failed')
        # Populate the same prefixes on the same replicas for every cell. Quality
        # already warmed the control layout; explicit warmup removes that advantage.
        warm_slots = [asyncio.Semaphore(concurrency) for _ in endpoints]
        async def warm(i, request):
            replica = i % len(endpoints)
            async with warm_slots[replica]:
                response = await replay.clients[replica].complete_once(endpoints[replica], request['task'],
                                                                      request['messages'], arm['max_tokens'])
                return {'identity': request['identity'], 'task': request['task'],
                        'response': response.response(), 'metrics': response.metrics()}
        warmup = await asyncio.gather(*(warm(i, r) for i, r in enumerate(subset)))
        write_json(output / 'warmup.json', warmup)
        if any(r['metrics'].get('error') for r in warmup): raise RuntimeError('Throughput warmup API failure')
        before = await server_metrics(endpoints)
        write_json(output / 'server-metrics-before.json', before)
        for repetition in range(sweep['repetitions']):
            slots = [asyncio.Semaphore(concurrency) for _ in endpoints]
            completed = 0
            async def call(ordinal, request, copy_index):
                nonlocal completed
                replica = ordinal % len(endpoints)
                async with slots[replica]:
                    client = replay.clients[replica]
                    messages = request['messages']
                    # APIClient has an immutable per-cell config; use a request-scoped
                    # client to attach the same ordinal seed on every topology.
                    from .client import APIClient
                    scoped = APIClient(client.config.model_copy(update={'seed': sweep['seed'] + ordinal}),
                                       http=client.http, schema_overrides=client.schema_overrides)
                    response = await scoped.complete_once(endpoints[replica], request['task'], messages, arm['max_tokens'])
                    valid = False; errors = []
                    if response.error or response.finish_reason not in {'stop', 'eos'} or not response.content.strip():
                        errors = [response.error or 'incomplete_or_empty_final:' + str(response.finish_reason)]
                    else:
                        try:
                            candidate = replay.parser.parse_output(response.content).value
                            check_candidate(request, candidate, replay.articles, replay.packets, replay.legacy)
                            valid = True
                        except (ValueError, TypeError, KeyError, jsonschema.ValidationError) as exc:
                            # Clinical/JSON errors are measurement outcomes, not retries.
                            errors = [str(exc)[:4000]]
                    completed += 1
                    if completed % 16 == 0:
                        print(json.dumps({'phase': 'throughput', 'model': model['name'], 'layout': layout['name'],
                            'concurrency': concurrency, 'repetition': repetition, 'completed': completed,
                            'total': len(workload)}), flush=True)
                    return {'ordinal': ordinal, 'replica': replica, 'identity': request['identity'], 'task': request['task'],
                            'messages_sha256': request['messages_sha256'], 'copy': copy_index, 'repetition': repetition,
                            'valid': valid, 'errors': errors, 'response': response.response(), 'metrics': response.metrics()}
            started = time.monotonic()
            rows = await asyncio.gather(*(call(i, r, c) for i, (r, c) in enumerate(workload)))
            wall = time.monotonic() - started
            metrics.extend(rows)
            measured = cell_metrics(rows, wall, 8)
            rounds.append(measured)
            write_json(output / f'repetition-{repetition}.json', {'wall_seconds': wall, 'requests': rows, 'metrics': measured})
        result = {'status': 'completed', 'model': model['name'], 'layout': layout, 'concurrency_per_replica': concurrency,
                  'workload_sha256': json_digest(workload), 'sampling': arm, 'rounds': rounds,
                  **cell_metrics(metrics, sum(r['wall_seconds'] for r in rounds), 8)}
        after = await server_metrics(endpoints)
        write_json(output / 'server-metrics-after.json', after)
        result['speculative_counter_deltas'] = speculative_counter_deltas(before, after)
        write_json(output / 'report.json', result)
        return result
    finally:
        await replay.close()


async def server_metrics(endpoints):
    async with httpx.AsyncClient(timeout=20) as client:
        async def get(endpoint):
            result = await client.get(endpoint.removesuffix('/v1') + '/metrics'); result.raise_for_status()
            return result.text
        return await asyncio.gather(*(get(e) for e in endpoints))


def speculative_counter_deltas(before, after):
    from prometheus_client.parser import text_string_to_metric_families
    def counters(text):
        return {(s.name, tuple(sorted(s.labels.items()))): s.value
                for f in text_string_to_metric_families(text) for s in f.samples
                if 'spec_decode' in s.name and (f.type == 'counter' or s.name.endswith('_count'))
                and not s.name.endswith('_created')}
    rows = []
    for replica, (old, new) in enumerate(zip(before, after, strict=True)):
        baseline = counters(old)
        for key, value in counters(new).items():
            delta = value - baseline.get(key, 0)
            rows.append({'replica': replica, 'metric': key[0], 'labels': dict(key[1]), 'delta': delta if delta >= 0 else None})
    return rows  # Never guess an acceptance denominator from mismatched counters.


def cell_metrics(rows, wall, gpus):
    totals = {}
    for label, key in [('input', 'prompt_tokens'), ('output', 'completion_tokens'), ('reasoning', 'reasoning_tokens')]:
        values = [r['metrics'].get(key) for r in rows]
        totals[label] = sum(values) if values and all(type(v) is int for v in values) else None
    completed = sum(not r['metrics'].get('error') and r['response'].get('finish_reason') in {'stop', 'eos'}
                    and bool(r['response'].get('content', '').strip()) for r in rows)
    output = totals['output']; valid = sum(r['valid'] for r in rows)
    return {'wall_seconds': wall, 'requests': len(rows), 'completed_nonempty_requests': completed,
            'valid_tasks': valid, 'invalid_tasks': len(rows) - valid, 'gpu_count': gpus,
            'input_tokens': totals['input'], 'output_tokens': output, 'reasoning_tokens': totals['reasoning'],
            'aggregate_input_tokens_per_second': totals['input'] / wall if totals['input'] is not None and wall else None,
            'aggregate_output_tokens_per_second': output / wall if output is not None and wall else None,
            'output_tokens_per_gpu_second': output / wall / gpus if output is not None and wall else None,
            'valid_tasks_per_second': valid / wall if wall else None,
            'eligible_for_selection': bool(rows) and completed / len(rows) >= .95 and output is not None,
            'request_latency_seconds': stats(r['metrics']['latency_seconds'] for r in rows),
            'ttft_seconds': stats(r['metrics']['ttft_seconds'] for r in rows if r['metrics'].get('ttft_seconds') is not None),
            'ttfa_seconds': stats(r['metrics']['ttfa_seconds'] for r in rows if r['metrics'].get('ttfa_seconds') is not None),
            'errors': dict(Counter(e for r in rows for e in r['errors'])),
            'notice': 'Full natural completions and application validation, no retries. Includes returned reasoning tokens in output usage. '
                      'Warm prefix cache; fixed real extraction workload. Startup/warmup excluded. Not an article/hour estimate.'}


async def gpu_gauntlet(campaign, model):
    if campaign['config'].get('benchmark_mode') == 'fp8_tuning':
        from .glimmer_tuning import gpu_tuning
        return await gpu_tuning(campaign, model)
    from .hipergator import active_path, model_lock, check_owner
    if not os.environ.get('SLURM_JOB_ID') or not os.environ.get('CUDA_VISIBLE_DEVICES'):
        raise RuntimeError('Glimmer gauntlet requires a Slurm allocation with eight visible B200s')
    work = Path(campaign['work']); config = campaign['config']; path = active_path(campaign)
    result_root = work / 'results' / model['name']; started = time.monotonic()
    if any(result_root.glob('*/report.json')):
        raise ValueError('Glimmer quality/throughput needs a fresh campaign; existing measurements are not overwritten')
    cells = []; failures = []; quality = {}
    with model_lock(work):
        check_owner(path, campaign, model)
        ready = json.loads((path / 'ready.json').read_text()); audit = json.loads((result_root / 'download.json').read_text())
        if ready != {'campaign': campaign['id'], 'model': model['name'], 'audit_sha256': json_digest(audit)}:
            raise ValueError('Model download verification did not complete')
        for asset in audit['files']:
            stat = (path / asset['folder'] / asset['file']).stat()
            if stat.st_size != asset['bytes'] or stat.st_mtime_ns != asset['mtime_ns']:
                raise ValueError('Checkpoint changed after download: ' + asset['file'])
        control = next(l for l in config['layouts'] if l['name'] == config['quality_layout'])
        gpu_probe(campaign, model, serving_config(campaign, model, control))
        monitor_log = (result_root / 'gpu-utilization.csv').open('w')
        monitor = subprocess.Popen(['nvidia-smi', '--query-gpu=timestamp,index,name,utilization.gpu,memory.used,power.draw',
                                    '--format=csv', '-l', '1'], stdout=monitor_log, stderr=subprocess.STDOUT)
        try:
            # Quality first: later experimental layout failures cannot erase it.
            ordered = [control] + [l for l in config['layouts'] if l != control]
            for layout in ordered:
                skip = layout_skip(model, layout)
                layout_root = result_root / 'layouts' / layout['name']
                if skip:
                    write_json(layout_root / 'status.json', {'status': 'skipped', 'reason': skip})
                    continue
                write_json(result_root / 'progress.json', {'phase': 'starting', 'layout': layout['name'], 'time_unix': time.time()})
                cfg = serving_config(campaign, model, layout)
                group = ServerGroup(cfg, campaign['work'], str(layout_root / 'servers'))
                layout_start = time.monotonic()
                try:
                    await group.start(config['startup_timeout_seconds'])
                    endpoints = [c['endpoint'] for c in group.commands]
                    await smoke(endpoints, model, config, layout_root / 'smoke.json')
                    if layout == control:
                        for name, arm in config['arms'].items():
                            write_json(result_root / 'progress.json', {'phase': 'quality', 'arm': name, 'time_unix': time.time()})
                            replay = Replay(Path(campaign['root']) / config['fixtures'], result_root / name, model, arm,
                                endpoints, config['concurrency_per_replica'], config['request_timeout_seconds'], config['max_model_len'])
                            try:
                                quality[name] = await replay.run()
                                if name != 'matched': await replay.secondary()
                            finally: await replay.close()
                        await article_lengths(campaign, endpoints[0], result_root)
                    elif layout.get('speculation') and layout['tensor_parallel'] == 1:
                        name = config['speculation_quality_arm'] + '_' + layout['speculation']
                        arm = config['arms'][config['speculation_quality_arm']]
                        deployment_model = {**model, 'deployment': layout}
                        replay = Replay(Path(campaign['root']) / config['fixtures'], result_root / name,
                            deployment_model, arm, endpoints, config['concurrency_per_replica'],
                            config['request_timeout_seconds'], config['max_model_len'])
                        try:
                            quality[name] = await replay.run()
                            await replay.secondary()
                        finally: await replay.close()
                    for concurrency in config['throughput']['concurrency_per_replica']:
                        write_json(result_root / 'progress.json', {'phase': 'throughput', 'layout': layout['name'],
                            'concurrency_per_replica': concurrency, 'time_unix': time.time()})
                        cell = await throughput_cell(campaign, model, layout, endpoints, concurrency,
                                                     layout_root / f'concurrency-{concurrency}')
                        cells.append(cell)
                    async with httpx.AsyncClient(timeout=20) as client:
                        for i, endpoint in enumerate(endpoints):
                            response = await client.get(endpoint.removesuffix('/v1') + '/metrics')
                            response.raise_for_status(); (layout_root / f'server-{i}-metrics.txt').write_text(response.text)
                    write_json(layout_root / 'status.json', {'status': 'completed', 'allocated_seconds': time.monotonic() - layout_start})
                except Exception as exc:
                    failure = {'status': 'failed', 'layout': layout['name'], 'experimental': layout.get('experimental', False),
                               'error_type': type(exc).__name__, 'error': str(exc)}
                    failures.append(failure); write_json(layout_root / 'status.json', failure)
                    print(json.dumps(failure), flush=True)
                    if layout == control: raise  # No trustworthy quality sweep without the control.
                finally:
                    primary = sys.exception()
                    try: group.stop()
                    except Exception as exc:
                        write_json(layout_root / 'server-cleanup-error.json', {'error': str(exc)})
                        # Continuing could put two layouts on the same devices.
                        if primary is None: raise
            eligible = [c for c in cells if c['eligible_for_selection']]
            best = max(eligible, key=lambda c: (c['valid_tasks_per_second'], c['aggregate_output_tokens_per_second'])) if eligible else None
            result = {'status': 'completed' if not any(not f['experimental'] for f in failures) else 'partial',
                'model': model['name'], 'gpu_count': 8, 'gpu_stage_seconds': time.monotonic() - started,
                'quality_arms': {a: {'tasks': r['tasks'], 'valid_tasks': r['valid_tasks'], 'scores': r['scores']} for a, r in quality.items()},
                'layout_failures': failures, 'best_measured_layout': None if best is None else {
                    k: best[k] for k in ('layout', 'concurrency_per_replica', 'aggregate_output_tokens_per_second',
                                        'output_tokens_per_gpu_second', 'valid_tasks_per_second')},
                'selection_basis': 'Highest application-valid tasks/second among cells with >=95% complete nonempty responses; '
                    'partial checklist and stochastic two-repeat measurement, not complete medical precision.'}
            write_json(result_root / 'progress.json', {'phase': result['status'], 'time_unix': time.time()})
            return result
        finally:
            monitor.terminate()
            try: monitor.wait(timeout=10)
            except subprocess.TimeoutExpired: monitor.kill(); monitor.wait(timeout=10)
            monitor_log.close()


async def article_lengths(campaign, endpoint, result_root):
    _, articles, _, _, _ = fixtures(Path(campaign['root']) / campaign['config']['fixtures'])
    rows = []
    async with httpx.AsyncClient(timeout=60) as client:
        for aid, article in articles.items():
            response = await client.post(endpoint.removesuffix('/v1') + '/tokenize',
                json={'model': 'clinical-extractor', 'prompt': article['text'], 'add_special_tokens': False})
            response.raise_for_status()
            rows.append({'article_id': aid, 'words': len(article['text'].split()), 'model_tokens': response.json()['count']})
    write_json(result_root / 'article-lengths.json', {'articles': rows, 'words': stats(r['words'] for r in rows),
        'model_tokens': stats(r['model_tokens'] for r in rows), 'notice': 'Nine frozen articles, not a population length estimate.'})


def report_gauntlet(campaign):
    if campaign['config'].get('benchmark_mode') == 'fp8_tuning':
        from .glimmer_tuning import report_tuning
        return report_tuning(campaign)
    from .hipergator import active_path
    work = Path(campaign['work']); config = campaign['config']; models = []; failed = []; experiments = []
    lines = ['# Glimmer B200 gauntlet', '', 'Same nine PMC articles, eleven cases, 176 primary tasks, 161 required/36 forbidden checks.', '',
        '| Checkpoint | Arm | Supported required facts raw / delivered | Forbidden raw / delivered | Valid / invalid tasks | First-pass valid |',
        '| --- | --- | --- | --- | --- | --- |']
    def read(path): return json.loads(path.read_text()) if path.exists() else {'status': 'missing'}
    for model in config['models']:
        root = work / 'results' / model['name']
        row = {'model': model, **{s: read(root / f'{s}.json') for s in ('download', 'gpu', 'cleanup')},
               'arms': {}, 'layouts': {}}
        arm_names = list(config['arms'])
        if model.get('test_dflash'):
            arm_names += [config['speculation_quality_arm'] + '_' + s for s in ('dflash', 'dspark')]
        for arm_name in arm_names:
            arm = read(root / arm_name / 'report.json')
            row['arms'][arm_name] = arm
            if 'scores' not in arm: continue
            raw, delivered = arm['scores']['raw'], arm['scores']['delivered']
            lines.append(f"| {model['name']} | {arm_name} | {raw['matched']} / {delivered['matched']} of 161 | "
                f"{raw['forbidden_violations']} / {delivered['forbidden_violations']} of 36 | "
                f"{arm['valid_tasks']} / {arm['tasks']-arm['valid_tasks']} | {arm['first_attempt_valid_tasks']} |")
        for layout in config['layouts']:
            dest = root / 'layouts' / layout['name']
            row['layouts'][layout['name']] = {'status': read(dest / 'status.json'), 'cells': {
                str(c): read(dest / f'concurrency-{c}' / 'report.json') for c in config['throughput']['concurrency_per_replica']}}
        models.append(row)
        for name, layout in row['layouts'].items():
            if layout['status']['status'] == 'failed':
                experiments.append({'model': model['name'], 'layout': name, **layout['status']})
        if (row['gpu']['status'] != 'completed' or row['cleanup']['status'] != 'deleted'
                or any('scores' not in row['arms'][a] for a in config['arms'])): failed.append(model['name'])
    lines += ['', '## Serving throughput (all eight GPUs)', '',
        '| Checkpoint | Layout | In-flight / replica | Output tokens/s | Tokens/GPU-second | Valid tasks/s | Valid / invalid |',
        '| --- | --- | ---: | ---: | ---: | ---: | --- |']
    def shown(v): return f'{v:.2f}' if v is not None else 'unavailable'
    for row in models:
        for name, layout in row['layouts'].items():
            for concurrency, cell in layout['cells'].items():
                if cell['status'] != 'completed': continue
                lines.append(f"| {row['model']['name']} | {name} | {concurrency} | {shown(cell['aggregate_output_tokens_per_second'])} | "
                    f"{shown(cell['output_tokens_per_gpu_second'])} | {shown(cell['valid_tasks_per_second'])} | "
                    f"{cell['valid_tasks']} / {cell['invalid_tasks']} |")
    lines += ['', '## Deployment outcomes', '']
    for row in models:
        lines.append(f"- {row['model']['name']}: GPU {row['gpu']['status']}; cleanup {row['cleanup']['status']}.")
        for name, layout in row['layouts'].items():
            status = layout['status']
            if status['status'] != 'completed': lines.append(f"  - {name}: {status['status']}; {status.get('reason', status.get('error', 'no report'))}")
    lines += ['', 'Failed/incomplete checkpoints: ' + (', '.join(failed) or 'none'), '',
        'Quality uses TP1/DP8 without speculation at every precision. Matched is the earlier T=0/top_p=1, low-effort, '
        '8k/16k-cap comparison control; all four Meta arms use T=1/top_p=.95/top_k=64 and equal 32k caps. No schema-constrained decoding. '
        'Additional medium_dflash/medium_dspark arms repeat the full quality test with speculation when the corresponding smoke passes.', '',
        'Layout sweeps use the same real prompt subset, natural output budgets, seeds and two repetitions; reasoning is medium. '
        'Rates include all replicas and returned reasoning tokens. Prefix cache is warm; startup, warmup and queue waiting are excluded. '
        'Latency/TTFT/TTFA distributions and repeat-level rates are in each cell report. Overall GPU stage seconds include initialization and all arms/layouts.', '',
        'Clinical checks are a partial development checklist, not full medical accuracy. Unsupported facts outside the checklist require '
        'the saved claim-review forms and source adjudication. Text/captions and patient-specific figure attribution are evaluated; '
        'image pixels are not. Vision-capable records say not_evaluated_text_only; no pixel interpretation fields are fabricated.']
    summary = {'models': models, 'failed_models': failed, 'failed_records': len(failed),
        'failed_layouts': experiments,
        'historical_scores': read(Path(campaign['root']) / config['fixtures'] / 'historical-scores.json'),
        'weight_storage_exists': active_path(campaign).exists(), 'fixture_manifest_sha256': campaign['fixture_manifest_sha256']}
    write_json(work / 'summary.json', summary); (work / 'SUMMARY.md').write_text('\n'.join(lines) + '\n')
    return {'summary': str(work / 'summary.json'), 'markdown': str(work / 'SUMMARY.md'), 'failed_records': len(failed)}
