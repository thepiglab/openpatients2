import asyncio
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from openpatients2 import dense_benchmark as b, dense_nemotron_super as s
from openpatients2 import nemotron_super_recipes as r, redhat_nemotron_recipe as redhat
from openpatients2.dense_benchmark_serving import render
from openpatients2.pilot_extract import PilotConfig
from test_dense_nemotron_super import campaign


def test_redhat_default_arm_freezes_target_and_draft_and_preserves_profiles(campaign, tmp_path, monkeypatch):
    work = tmp_path / 'all-profiles'
    s.prepare(campaign[2], work, expected=3)
    m = s.check(work)
    assert m['nemotron_super']['profiles'][-1] == 'redhat-fp8-dspark'
    assert len(m['nemotron_super']['arms']) == 6
    assert {redhat.FP8, redhat.DSPARK} <= m['nemotron_super']['pins'].keys()
    arm = b.read(work / 'arms/redhat-fp8-dspark-text/manifest.json')
    assert arm['model'] == {'id': redhat.FP8, 'revision': redhat.PINS[redhat.FP8]['revision']}
    assert arm['drafter'] == {'id': redhat.DSPARK, 'revision': redhat.PINS[redhat.DSPARK]['revision']}
    assert arm['container'] == r.VLLM_IMAGE
    monkeypatch.setenv('CUDA_VISIBLE_DEVICES', '2,7')
    cfg = s.serving(work, 'redhat-fp8-dspark-text')
    command = render(cfg, str(work / 'arms/redhat-fp8-dspark-text'))[0]
    assert command['devices'] == ['2', '7']
    assert cfg.model_id == redhat.FP8 and cfg.model_path.endswith('/weights/redhat-fp8-dspark')
    assert cfg.tensor_parallel == 2 and cfg.expert_parallel and cfg.reasoning_parser == 'nemotron_v3'
    assert cfg.max_model_len == 65536 and cfg.max_num_seqs == 128 and cfg.max_batched_tokens == 16384
    assert cfg.gpu_memory_utilization == .89
    spec = json.loads(command['argv'][command['argv'].index('--speculative-config') + 1])
    assert spec == {'method': 'dspark', 'num_speculative_tokens': 8,
        'draft_tensor_parallel_size': 1, 'draft_sample_method': 'greedy',
        'rejection_sample_method': 'standard', 'enable_adaptive_verification': False,
        'model': str(work / 'runtime/weights/redhat-dspark')}
    assert '--enable-prefix-caching' in command['argv'] and '--enable-expert-parallel' in command['argv']
    assert json.loads(cfg.extra_args[cfg.extra_args.index('--compilation-config') + 1]) == {'max_cudagraph_capture_size': 192}
    for flag in ('--block-size', '--prefix-match-unit'):
        assert cfg.extra_args[cfg.extra_args.index(flag) + 1] == '64'
    assert '--enable-mamba-fine-grained-prefix-cache' not in command['argv']
    plan = s.submit(work, 'account', 'qos', dry_run=True)
    job = next(cmd for cmd in plan['commands_without_dependencies'] if 'redhat-fp8-dspark-text' in cmd)
    assert '--gres=gpu:b200:2' in job and '--mem=200G' in job
    assert plan['maximum_simultaneous_gpus'] == 8


def test_redhat_native_sampling_and_live_text_pixel_smoke(campaign, tmp_path, monkeypatch):
    monkeypatch.setattr(b, 'freeze_media', lambda *a, **kw: {})
    work = tmp_path / 'redhat-only'
    s.prepare(campaign[2], work, expected=3, with_images=True, profiles=['redhat-fp8-dspark'])
    assert set(s.check(work)['nemotron_super']['arms']) == {'redhat-fp8-dspark-text', 'redhat-fp8-dspark-vision'}
    config = b.read(work / 'arms/redhat-fp8-dspark-text/manifest.json')['extraction']
    cfg = PilotConfig.model_validate(config)
    assert cfg.template_kwargs() == {'enable_thinking': True} and cfg.concurrency_per_endpoint == 64
    for changes in ({'revision': b.REVISION}, {'top_k': 64}, {'temperature': 0}, {'vision_temperature': None}):
        with pytest.raises(ValueError, match='pinned recipe'):
            PilotConfig.model_validate({**config, **changes})
    with pytest.raises(ValueError, match='drafter'):
        PilotConfig(model_id=redhat.DSPARK)
    requests = []
    real_client = httpx.AsyncClient
    def handle(request):
        body = json.loads(request.content); requests.append((request.url.path, body))
        if request.url.path == '/tokenize':
            return httpx.Response(200, json={'count': 10, 'tokens': [1] * 10, 'max_model_len': 65536})
        return httpx.Response(200, json={'usage': {'prompt_tokens': 10}, 'choices': [{'message': {'content': 'READY'}}]})
    monkeypatch.setattr(httpx, 'AsyncClient', lambda **kw: real_client(transport=httpx.MockTransport(handle), **kw))
    asyncio.run(s.smoke('http://localhost:8000/v1', tmp_path, config, pixels=True))
    generations = [body for path, body in requests if path.endswith('/chat/completions')]
    assert [body['temperature'] for body in generations] == [1, .6]
    assert all(body['top_p'] == .95 and body['chat_template_kwargs'] == {'enable_thinking': False}
               and 'top_k' not in body and 'stop_token_ids' not in body for body in generations)
    assert b.read(tmp_path / 'smoke.json')['passed']


def test_redhat_metadata_pins_and_setup_ownership(campaign, tmp_path, monkeypatch):
    work = tmp_path / 'setup-redhat'
    s.prepare(campaign[2], work, expected=3, profiles=['redhat-fp8-dspark'])
    native_sha = b.sha
    calls = []
    def download(**kwargs):
        calls.append(kwargs)
        model = kwargs['repo_id']; path = kwargs['local_dir']
        b.write(path / 'config.json', {'architectures': [redhat.PINS[model]['architecture']]})
        if model == redhat.FP8:
            (path / 'chat_template.jinja').write_text('fixture')
        if '*.safetensors' in kwargs['allow_patterns']:
            (path / 'weights.safetensors').write_bytes(b'fixture')
    def sha(path):
        path = Path(path)
        if path.name in ('config.json', 'chat_template.jinja') and 'weights' in path.parts:
            model = redhat.DSPARK if path.parent.name == 'redhat-dspark' else redhat.FP8
            return redhat.PINS[model]['config_sha256' if path.name == 'config.json' else 'template_sha256']
        return native_sha(path)
    def run(argv, **kwargs):
        if argv[:2] == ['apptainer', 'build'] and '--help' not in argv:
            Path(argv[-2]).write_bytes(b'fixture image')
        stdout = ('--mksquashfs-args' if argv == ['apptainer', 'build', '--help'] else
                  ' '.join(redhat.PROFILE['extra_args']) if '--help=all' in argv else '{"vllm_version":"fixture"}')
        return SimpleNamespace(returncode=0, stdout=stdout, stderr='', check_returncode=lambda: None)
    monkeypatch.setitem(sys.modules, 'huggingface_hub', SimpleNamespace(snapshot_download=download, get_token=lambda: None))
    monkeypatch.setattr(b, 'sha', sha)
    monkeypatch.setattr(s.subprocess, 'run', run)
    for name in ('HF_HOME', 'HF_HUB_CACHE', 'HF_XET_CACHE', 'HF_XET_CHUNK_CACHE_SIZE_BYTES', 'APPTAINER_CACHEDIR', 'APPTAINER_TMPDIR'):
        monkeypatch.setenv(name, 'fixture')
    assert s.setup(work)['ready']
    assert [call['repo_id'] for call in calls] == [redhat.FP8, redhat.DSPARK]
    assert s.download(work, 'redhat-fp8-dspark')['ready']
    assert [call['repo_id'] for call in calls] == [redhat.FP8, redhat.DSPARK] * 2
    assert all(call['revision'] == redhat.PINS[call['repo_id']]['revision'] for call in calls)
    assert all('*.safetensors' not in call['allow_patterns'] for call in calls[:2])
    s.validate_runtime(work, 'redhat-fp8-dspark-text')
    draft = s.weights_path(work, redhat.DSPARK)
    (draft / 'weights.safetensors').write_bytes(b'changed')
    with pytest.raises(ValueError, match='artifact changed'):
        s.validate_runtime(work, 'redhat-fp8-dspark-text')
    monkeypatch.setattr(b, 'sha', native_sha)
    with pytest.raises(ValueError, match='model config'):
        s.validate_checkpoint(draft, redhat.DSPARK)
