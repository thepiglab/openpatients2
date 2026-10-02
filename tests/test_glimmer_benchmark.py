import copy
import json
from pathlib import Path

import httpx
import pytest

from openpatients2 import hipergator as hpg
from openpatients2 import glimmer_benchmark as glimmer
from openpatients2.hpg_eval import Replay, fixtures, sampling_body
from openpatients2.serving import render

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / 'configs/hipergator/glimmer.yaml'
FIXTURES = ROOT / 'benchmarks/hipergator-k2/fixtures'


def test_glimmer_serial_cpu_gpu_cleanup_chain_and_pinned_variants(tmp_path):
    plan = hpg.prepare(CONFIG, ROOT, tmp_path / 'campaign')
    jobs = plan['jobs']
    assert len(jobs) == 11
    assert [j['stage'] for j in jobs] == ['setup'] + ['download', 'gpu', 'cleanup'] * 3 + ['report']
    for i, job in enumerate(jobs):
        args = job['command']
        assert '--nodes=1' in args
        if job['stage'] == 'gpu':
            assert '--gres=gpu:b200:8' in args and '--cpus-per-task=32' in args and '--mem=250G' in args
        else:
            assert '--gres=none' in args and '--partition=hpg-b200' not in args
        if i:
            kind = 'afterok' if jobs[i-1]['stage'] == 'cleanup' else 'afterany'
            assert f'--dependency={kind}:{jobs[i-1]["id"]}' in args
    campaign = hpg.read_campaign(tmp_path / 'campaign')
    assert {m['name'] for m in campaign['config']['models']} == {'glimmer-fp8', 'glimmer-nvfp4', 'glimmer-nf4'}
    assert all(len(m['revision']) == 40 for m in campaign['config']['models'])
    assert campaign['config']['fixtures'] == 'benchmarks/hipergator-k2/fixtures'
    assert not (tmp_path / 'campaign/active-model').exists()


@pytest.mark.parametrize('level', glimmer.LEVELS)
def test_corrected_meta_template_renders_one_real_reasoning_directive(level):
    import jinja2 as jinja
    source = (ROOT / 'configs/hipergator/glimmer/meta-models-chat_template.jinja').read_text()
    template = jinja.Environment().from_string(source)
    _, _, _, requests, _ = fixtures(FIXTURES)
    for request in requests:
        rendered = template.render(messages=request['messages'], bos_token='<|begin_of_text|>',
                                   reasoning_strength=level, add_generation_prompt=True)
        assert rendered.count('Reasoning strength:') == 1
        assert f'Reasoning strength: {level}.' in rendered
        assert rendered.endswith('<|start|>assistant')
    rendered = template.render(messages=[{'role': 'system', 'content': 'Reasoning effort: low.'}],
        bos_token='', reasoning_strength='high', add_generation_prompt=True)
    assert rendered.count('Reasoning strength:') == 1 and 'Reasoning strength: high' not in rendered


def test_sampling_alias_top_k_and_unconstrained_replay(tmp_path):
    config = hpg.load_campaign(CONFIG, ROOT); model = config['models'][0]
    arm = config['arms']['meta_xhigh']
    body = sampling_body(model, arm)
    assert body == {'chat_template_kwargs': {'reasoning_strength': 'xhigh'}, 'skip_special_tokens': False, 'top_k': 64}
    replay = Replay(FIXTURES, tmp_path, model, arm, ['http://127.0.0.1:8000/v1'], 8, 30, 65536)
    request = replay.requests[0]
    sent = replay.clients[0].body(request['task'], request['messages'], arm['max_tokens'])
    assert sent['temperature'] == 1 and sent['top_p'] == .95 and sent['top_k'] == 64
    assert sent['seed'] == 42 and sent['messages'] == request['messages']
    assert not any(k in sent for k in ['response_format', 'structured_outputs', 'tools'])
    assert 'reasoning_effort' not in sent['chat_template_kwargs']


def test_layouts_use_disjoint_b200s_and_native_parser_and_drafter(tmp_path, monkeypatch):
    monkeypatch.setenv('CUDA_VISIBLE_DEVICES', ','.join('GPU-' + str(i) for i in range(8)))
    hpg.prepare(CONFIG, ROOT, tmp_path / 'campaign'); campaign = hpg.read_campaign(tmp_path / 'campaign')
    work = Path(campaign['work'])
    hpg.write_json(work / 'setup.json', {'sif': '/tmp/test.sif', 'container_python': '/usr/bin/python3'})
    patch = work / 'patch.py'; patch.write_text('synthetic')
    hpg.write_json(work / 'dspark-patch.json', {'overlay_file': str(patch),
        'source_file': '/usr/local/lib/python3.12/site-packages/vllm/utils.py', 'patched_sha256': hpg.sha256(patch)})
    model = campaign['config']['models'][0]
    for layout in campaign['config']['layouts']:
        cfg = glimmer.serving_config(campaign, model, layout)
        commands = render(cfg, str(work))
        assert len(commands) == layout['replicas']
        assert sorted(d for c in commands for d in c['devices']) == ['GPU-' + str(i) for i in range(8)]
        for c in commands:
            args = c['argv']
            assert '--reasoning-parser' in args and 'muse_glimmer' in args and '--chat-template' in args
            assert '--tool-call-parser' in args and '--language-model-only' in args
            assert '--enable-expert-parallel' not in args and '--data-parallel-size' not in args
            assert '--disable-log-requests' not in args and '--quantization' not in args
            if layout.get('speculation'):
                spec = json.loads(args[args.index('--speculative-config')+1])
                assert spec['method'] == layout['speculation']
                assert spec['num_speculative_tokens'] == (16 if layout['speculation'] == 'dspark' else 15)
                assert spec['model'].startswith(str(work / 'active-model'))
            if layout.get('speculation') == 'dspark':
                assert any(':ro' in a and str(patch) in a for a in args)
            if layout.get('decode_context_parallel'):
                assert args[args.index('--decode-context-parallel-size')+1] == '2'


def test_quantization_specific_layout_skips_are_explicit():
    config = hpg.load_campaign(CONFIG, ROOT)
    models = {m['name']: m for m in config['models']}
    for layout in config['layouts']:
        reason = glimmer.layout_skip(models['glimmer-nf4'], layout)
        assert (reason is None) == (layout['name'] == 'dp8-tp1')
    tp8 = next(l for l in config['layouts'] if l['name'] == 'dp1-tp8')
    assert '128-element blocks' in glimmer.layout_skip(models['glimmer-fp8'], tp8)
    assert glimmer.layout_skip(models['glimmer-nvfp4'], tp8) is None


def test_dspark_overlay_changes_only_the_documented_line():
    source = 'before\n    target_inner = target_language_model.model\nafter\n'
    expected = 'before\n    target_inner = getattr(target_language_model, "model", target_language_model)\nafter\n'
    assert glimmer.patch_dspark_source(source) == expected
    with pytest.raises(ValueError, match='ambiguous'): glimmer.patch_dspark_source(source * 2)
    with pytest.raises(ValueError, match='Expected stock'): glimmer.patch_dspark_source(expected)


def test_throughput_workload_has_all_domains_and_same_frozen_prompts():
    _, _, _, requests, _ = fixtures(FIXTURES)
    subset = glimmer.throughput_requests(requests, 32)
    assert len(subset) == 32 and {r['task'] for r in subset} == {r['task'] for r in requests}
    assert all(r in requests for r in subset)
    assert subset == glimmer.throughput_requests(list(reversed(requests)), 32)
    with pytest.raises(ValueError, match='omit'): glimmer.throughput_requests(requests, 16)


def test_metrics_count_all_gpus_and_do_not_promote_truncations_or_missing_usage():
    def row(tokens, valid=True, finish='stop'):
        return {'metrics': {'prompt_tokens': 200, 'completion_tokens': tokens, 'reasoning_tokens': None,
            'latency_seconds': 2., 'ttft_seconds': .1, 'ttfa_seconds': 1., 'error': None},
            'response': {'finish_reason': finish, 'content': '{}' if finish == 'stop' else ''}, 'valid': valid, 'errors': []}
    result = glimmer.cell_metrics([row(100), row(300, False)], 2., 8)
    assert result['aggregate_output_tokens_per_second'] == 200 and result['output_tokens_per_gpu_second'] == 25
    assert result['valid_tasks_per_second'] == .5 and result['invalid_tasks'] == 1
    assert result['reasoning_tokens'] is None and result['eligible_for_selection']
    result = glimmer.cell_metrics([row(None)], 2., 8)
    assert result['aggregate_output_tokens_per_second'] is None and not result['eligible_for_selection']
    result = glimmer.cell_metrics([row(100, False, 'length')], 2., 8)
    assert not result['eligible_for_selection']


@pytest.mark.asyncio
async def test_smoke_rejects_reasoning_only_answer(tmp_path, monkeypatch):
    async def handler(request):
        return httpx.Response(200, json={'choices': [{'finish_reason': 'stop',
            'message': {'content': '', 'reasoning_content': 'I think {"ok":true}'}}]})
    original = httpx.AsyncClient
    monkeypatch.setattr(glimmer.httpx, 'AsyncClient', lambda **kw: original(transport=httpx.MockTransport(handler), **kw))
    config = hpg.load_campaign(CONFIG, ROOT)
    with pytest.raises(ValueError, match='Smoke failed'):
        await glimmer.smoke(['http://127.0.0.1:8000/v1'], config['models'][0], config, tmp_path / 'smoke.json')
    assert not (tmp_path / 'smoke.json').exists()
    assert len(list((tmp_path / 'smoke-attempts').glob('*.json'))) == 4


@pytest.mark.asyncio
async def test_throughput_replays_real_answers_with_fixed_seeds_and_all_replica_usage(tmp_path, monkeypatch):
    from collections import Counter
    from openpatients2.provenance import json_digest
    config = hpg.load_campaign(CONFIG, ROOT)
    config['throughput']['copies'] = 1
    _, _, _, requests, _ = fixtures(FIXTURES)
    subset = glimmer.throughput_requests(requests, 32)
    saved = ROOT / 'runs/medical-fidelity-v1/comparison/meta--muse-glimmer-30b'
    answers = {}
    for request in subset:
        identity = request['identity']
        packet = json.loads((saved / (identity['article_id'] + '-' + identity['patient_id'] + '.json')).read_text())
        task = request['task']
        answer = packet['generations'][task] if task not in ('summary', 'timeline') else packet['companions'][task]['attempt_responses'][-1]
        answers[request['messages_sha256']] = answer
    calls = []; generated = Counter()
    def handler(request):
        replica = request.url.port - 8000
        if request.url.path == '/metrics':
            return httpx.Response(200, text='# TYPE vllm:spec_decode_num_accepted_tokens counter\n'
                f'vllm:spec_decode_num_accepted_tokens{{model_name="clinical-extractor"}} {generated[replica] * 4}\n')
        body = json.loads(request.content)
        if request.url.path == '/tokenize': return httpx.Response(200, json={'count': 1000})
        assert body['chat_template_kwargs'] == {'reasoning_strength': 'medium'}
        assert (body['temperature'], body['top_p'], body['top_k'], body['max_tokens']) == (1., .95, 64, 32768)
        assert 'response_format' not in body and 'structured_outputs' not in body
        assert body['skip_special_tokens'] is False
        calls.append((replica, body)); generated[replica] += 1
        answer = answers[json_digest(body['messages'])]
        event = {'choices': [{'index': 0, 'delta': {'content': answer['content'],
            'reasoning_content': answer.get('reasoning_text') or ''}, 'finish_reason': answer['finish_reason']}],
            'usage': {'prompt_tokens': 1000, 'completion_tokens': 200}}
        return httpx.Response(200, text='data: ' + json.dumps(event) + '\n\ndata: [DONE]\n\n')
    original = httpx.AsyncClient
    monkeypatch.setattr(glimmer.httpx, 'AsyncClient', lambda **kw: original(transport=httpx.MockTransport(handler), **kw))
    endpoints = [f'http://127.0.0.1:{8000+i}/v1' for i in range(8)]
    result = await glimmer.throughput_cell({'root': str(ROOT), 'config': config}, config['models'][0],
        config['layouts'][0], endpoints, 8, tmp_path / 'cell')
    assert len(calls) == 32 + 32 * 2  # Warmup then two repeats; no repair calls.
    assert set(generated) == set(range(8))
    for repetition in range(2):
        measured = calls[32 + repetition * 32:32 + (repetition + 1) * 32]
        assert sorted(body['seed'] for _, body in measured) == list(range(5724, 5756))
        assert {json_digest(body['messages']) for _, body in measured} == set(answers)
    assert result['requests'] == 64 and 0 < result['valid_tasks'] < 64
    assert result['input_tokens'] == 64000 and result['output_tokens'] == 12800
    assert result['aggregate_output_tokens_per_second'] == 12800 / result['wall_seconds']
    assert result['output_tokens_per_gpu_second'] == result['aggregate_output_tokens_per_second'] / 8
    assert sum(row['delta'] for row in result['speculative_counter_deltas']) == 64 * 4
    assert result['reasoning_tokens'] is None


def test_failed_experiments_and_missing_quality_are_visible_in_report(tmp_path):
    hpg.prepare(CONFIG, ROOT, tmp_path / 'campaign'); campaign = hpg.read_campaign(tmp_path / 'campaign')
    root = Path(campaign['work']) / 'results/glimmer-fp8'
    hpg.write_json(root / 'layouts/dp8-tp1-dspark/status.json', {'status': 'failed', 'error': 'Synthetic DSpark load failure'})
    result = glimmer.report_gauntlet(campaign)
    assert result['failed_records'] == 3
    summary = json.loads((Path(campaign['work']) / 'summary.json').read_text())
    assert summary['failed_layouts'][0]['layout'] == 'dp8-tp1-dspark'
    text = (Path(campaign['work']) / 'SUMMARY.md').read_text()
    assert 'Synthetic DSpark load failure' in text and 'missing' in text


def test_template_change_fails_before_jobs_or_downloads(tmp_path):
    import yaml
    config = yaml.safe_load(CONFIG.read_text()); config['chat_template_sha256'] = '0' * 64
    path = tmp_path / 'bad.yaml'; path.write_text(yaml.safe_dump(config))
    with pytest.raises(ValueError, match='template differs'):
        hpg.prepare(path, ROOT, tmp_path / 'campaign')
    assert not (tmp_path / 'campaign').exists()


@pytest.mark.parametrize('corrupt_assistant', [False, True])
def test_cpu_download_owns_both_assistants_and_deletes_them_even_after_failure(tmp_path, monkeypatch, corrupt_assistant):
    import hashlib
    import os
    hpg.prepare(CONFIG, ROOT, tmp_path / 'campaign'); campaign = hpg.read_campaign(tmp_path / 'campaign')
    model = copy.deepcopy(campaign['config']['models'][0])
    content = b'TEST'
    # Tiny synthetic inventories exercise the actual download/hash/ownership
    # paths without fetching weights or touching the real published metadata.
    inventory = {'files': [{'rfilename': 'model.safetensors', 'size': 4,
        'lfs': {'sha256': hashlib.sha256(content).hexdigest()}}], 'expected_bytes': 4}
    for folder, key, name in [('weights', 'metadata', model['id']),
                             ('drafter', 'drafter_metadata', 'synthetic/dflash'),
                             ('dspark', 'dspark_metadata', 'synthetic/dspark')]:
        metadata = tmp_path / (folder + '.json')
        hpg.write_json(metadata, {**inventory, 'model_id': name, 'revision': 'a' * 40})
        if folder == 'weights': model[key] = str(metadata)
        else: campaign['config'][key] = str(metadata)
    model['expected_bytes'] = 4
    campaign['config']['disk_headroom_gb'] = 0
    for key in ('SLURM_JOB_GPUS', 'SLURM_STEP_GPUS', 'SLURM_GPUS_ON_NODE'):
        monkeypatch.delenv(key, raising=False)
    for key in ('HF_HOME', 'HF_HUB_CACHE', 'HF_XET_CACHE', 'HF_MODULES_CACHE',
                'HF_XET_CHUNK_CACHE_SIZE_BYTES', 'XDG_CACHE_HOME', 'HF_HUB_OFFLINE',
                'TRANSFORMERS_OFFLINE', 'CUDA_VISIBLE_DEVICES'):
        monkeypatch.setenv(key, '')
    hpg.write_json(Path(campaign['work']) / 'setup.json', {'status': 'ready'})
    monkeypatch.setattr(glimmer, 'template_probe', lambda *args: None)
    downloaded = []
    def download(**kwargs):
        assert os.environ['CUDA_VISIBLE_DEVICES'] == ''
        dest = Path(kwargs['local_dir']); dest.mkdir()
        assert kwargs['allow_patterns'] == ['model.safetensors']
        downloaded.append(dest.name)
        (dest / 'model.safetensors').write_bytes(b'FAIL' if corrupt_assistant and dest.name == 'dspark' else content)
    if corrupt_assistant:
        with pytest.raises(ValueError, match='SHA256'): hpg.download(campaign, model, download)
        assert not (hpg.active_path(campaign) / 'ready.json').exists()
    else:
        result = hpg.download(campaign, model, download)
        assert result['downloaded_bytes'] == 12
        audit = json.loads((Path(campaign['work']) / 'results' / model['name'] / 'download.json').read_text())
        assert {row['folder'] for row in audit['files']} == {'weights', 'drafter', 'dspark'}
    assert downloaded == ['weights', 'drafter', 'dspark']
    with pytest.raises(ValueError, match='Previous checkpoint'): hpg.download(campaign, model, download)
    keep = Path(campaign['work']) / 'results' / 'keep.json'; keep.write_text('KEEP')
    hpg.cleanup(campaign, model)
    assert not hpg.active_path(campaign).exists() and keep.read_text() == 'KEEP'
