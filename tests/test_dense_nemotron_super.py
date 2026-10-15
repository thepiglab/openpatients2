import asyncio
import copy
import gzip
import json
from pathlib import Path
import subprocess
import sys
import tarfile
from types import SimpleNamespace

import httpx
import pytest

from openpatients2 import dense_benchmark as b, dense_nemotron_super as s
from openpatients2 import nemotron_super_recipes as r
from openpatients2.dense_benchmark_serving import render
from openpatients2.pilot_extract import PilotConfig, PilotRunner
from test_dense_benchmark import ROOT, finish
from test_pilot_extract import article


@pytest.fixture
def campaign(tmp_path):
    root = tmp_path / 'repo'
    for folder in ('src', 'scripts', 'configs/hipergator/dense-benchmark', 'docs'):
        (root / folder).mkdir(parents=True)
    for name in ('pyproject.toml', 'uv.lock', 'README.md', 'LICENSE'):
        (root / name).write_text('fixture')
    for name in ('DENSE260_HIPERGATOR_BENCHMARK.md', 'DENSE_NEMOTRON_SUPER.md'):
        (root / 'docs' / name).write_text('fixture')
    (root / 'configs/hipergator/dense-benchmark/chat_template.jinja').write_bytes(
        (ROOT / 'configs/hipergator/dense-benchmark/chat_template.jinja').read_bytes())
    reference = next(iter(b.rows(ROOT / 'tests/fixtures/unified_scoring_reference.jsonl').values()))
    refs = []
    for i, split in enumerate(('train', 'validation', 'test')):
        source = article(); source['article_id'] = f'PMC{100+i}.1'
        b.write(root / 'benchmarks/dense/articles' / source['article_id'] / 'source.json', source)
        refs.append({**reference, 'article_id': source['article_id'], 'split': split,
            'text_sha256': source['text_sha256'], 'completion': {'completed': True, 'checks': {'ok': True}}})
    with gzip.open(root / 'configs/hipergator/dense-benchmark/reference.jsonl.gz', 'wt') as out:
        out.write(''.join(json.dumps(ref) + '\n' for ref in refs))
    work = tmp_path / 'campaign'
    s.prepare(root, work, expected=3, profiles=['fastino', 'nvfp4', 'bf16', 'lilicorr'])
    return work, refs[:2], root


def test_frozen_selection_and_official_request_sampling(campaign, tmp_path):
    work, refs, root = campaign
    m = s.check(work)
    assert m['article_count'] == 2 and m['splits'] == {'train': 1, 'validation': 1}
    assert not list((work / 'inputs').rglob('*.png'))
    assert set(m['nemotron_super']['arms']) == {'fastino-text', 'bf16-text', 'nvfp4-text', 'lilicorr-text'}
    assert r.FASTINO in m['nemotron_super']['pins']
    assert not any('Lightning' in model for model in m['nemotron_super']['pins'])
    assert not (work / 'inputs/PMC102.1').exists()
    for name in m['nemotron_super']['arms']:
        am = b.read(work / 'arms' / name / 'manifest.json')
        cfg = PilotConfig.model_validate(am['extraction'])
        assert cfg.template_kwargs() == {'enable_thinking': True}
        async def inspect():
            runner = PilotRunner(cfg, tmp_path / name, ['http://localhost:8000/v1'], 65536, 'targeted')
            try:
                text = runner.clients[0].body('summary', [{'role': 'user', 'content': 'text'}], 128)
                pixels = runner.clients[0].body('summary', [{'role': 'user', 'content': [
                    {'type': 'image_url', 'image_url': {'url': 'data:image/png;base64,fixture'}}]}], 128)
                assert 'top_k' not in text and 'structured_outputs' not in text
                assert text['chat_template_kwargs'] == cfg.template_kwargs()
                assert text['temperature'] == (0 if name.startswith('fastino') else 1)
                assert pixels['temperature'] == (0 if name.startswith('fastino') else .6)
            finally:
                await runner.close()
        asyncio.run(inspect())
        with pytest.raises(ValueError, match='pinned recipe'):
            PilotConfig.model_validate({**am['extraction'], 'revision': b.REVISION})
    with pytest.raises(ValueError, match='Unsupported model'):
        PilotConfig(model_id=r.LILICORR)
    with pytest.raises(ValueError, match='Unsupported model'):
        PilotConfig(model_id='fastino/Fastino-Nemotron-3.5-Lightning-Healthcare')
    with pytest.raises(ValueError, match='supported profiles'):
        s.prepare(root, tmp_path / 'removed-profile', expected=3, profiles=['lightning'])
    with pytest.raises(ValueError, match='Frozen input/code changed'):
        (work / 'arms/nvfp4-text/manifest.json').write_text('{}')
        s.check(work)


def test_vision_adds_all_three_nvidia_profiles(campaign, tmp_path, monkeypatch):
    _, _, root = campaign
    frozen = []
    monkeypatch.setattr(b, 'freeze_media', lambda *a, **kw: frozen.append(a[0]['article_id']) or {})
    work = tmp_path / 'vision'
    s.prepare(root, work, expected=3, with_images=True, profiles=['fastino', 'nvfp4', 'bf16', 'lilicorr'])
    m = s.check(work)
    assert len(m['nemotron_super']['arms']) == 7 and len(frozen) == 2
    assert {name for name in m['nemotron_super']['arms'] if name.endswith('-vision')} == {
        'nvfp4-vision', 'bf16-vision', 'lilicorr-vision'}
    assert b.read(work / 'arms/nvfp4-vision/manifest.json')['text_only'] is False
    assert b.read(work / 'arms/fastino-text/manifest.json')['text_only']
    cfg = b.read(work / 'arms/fastino-text/manifest.json')['extraction']
    with pytest.raises(ValueError, match='text-only'):
        PilotConfig.model_validate({**cfg, 'max_figures': 1})


def test_render_correct_target_drafter_topology_and_offline_flags(campaign, monkeypatch):
    work, _, _ = campaign
    monkeypatch.setenv('CUDA_VISIBLE_DEVICES', '3,5')
    for name in ['fastino-text', 'nvfp4-text', 'bf16-text', 'lilicorr-text']:
        cfg = s.serving(work, name)
        commands = render(cfg, str(work / 'arms' / name))
        argv = commands[0]['argv']
        assert '--generation-config' in argv and cfg.reasoning_parser == 'nemotron_v3'
        assert commands[0]['devices'] == (['3', '5'] if name in ['fastino-text', 'bf16-text'] else ['3'])
        assert 'HF_HUB_OFFLINE=1' in argv and 'TRANSFORMERS_OFFLINE=1' in argv
        if name == 'fastino-text':
            assert cfg.model_id == r.FASTINO and '--speculative-config' not in argv
            continue
        spec = json.loads(argv[argv.index('--speculative-config')+1])
        if name.startswith('lilicorr'):
            assert spec['method'] == 'dflash' and spec['num_speculative_tokens'] == 7
            assert spec['model'].endswith('/weights/lilicorr')
            assert cfg.model_path.endswith('/weights/nvfp4') and cfg.model_id == r.NVFP4
            assert cfg.max_num_seqs == 1
        else:
            assert spec == {'method': 'mtp', 'num_speculative_tokens': 5}


def test_slurm_dag_serializes_gpu_arms_and_releases_downstream_first(campaign, monkeypatch):
    work, _, _ = campaign
    calls = []
    def run(argv, **kwargs):
        calls.append(argv)
        if argv[0] == 'sbatch':
            return SimpleNamespace(stdout=str(sum(c[0] == 'sbatch' for c in calls)) + ';cluster\n')
        return SimpleNamespace(stdout='')
    monkeypatch.setattr(subprocess, 'run', run)
    plan = s.submit(work, 'a', 'q', dry_run=True)
    assert not calls
    assert [next(x for x in c if x.startswith('--gres')) for c in plan['commands_without_dependencies'] if any(x.startswith('--gres') for x in c)] == [
        '--gres=gpu:b200:2', '--gres=gpu:b200:1', '--gres=gpu:b200:2', '--gres=gpu:b200:1']
    result = s.submit(work, 'a', 'q')
    sbatch = [c for c in calls if c[0] == 'sbatch']
    assert '--dependency=afterok:1' in sbatch[1]
    assert '--dependency=afterok:1,afterany:2' in sbatch[2]
    assert '--dependency=afterok:1,afterany:3' in sbatch[3]
    assert '--dependency=afterok:1,afterany:4' in sbatch[4]
    assert '--dependency=afterany:1:2:3:4:5' in sbatch[5]
    assert calls[-6:] == [['scontrol', 'release', str(i)] for i in range(6, 0, -1)]
    assert result['jobs'][-1]['phase'] == 'aggregate'
    # Resume queues only unfinished arms, and cannot overlap a live campaign.
    for ref in campaign[1]:
        finish(work / 'arms/nvfp4-text', ref)
    monkeypatch.setattr(subprocess, 'run', lambda *a, **kw: SimpleNamespace(stdout=''))
    resumed = s.submit(work, 'a', 'q', resume=True, dry_run=True)
    assert all('nvfp4-text' not in c for c in resumed['commands_without_dependencies'])
    monkeypatch.setattr(subprocess, 'run', lambda *a, **kw: SimpleNamespace(stdout='2\n'))
    with pytest.raises(ValueError, match='still active'):
        s.submit(work, 'a', 'q', resume=True)


def test_submit_failure_cancels_only_new_jobs(campaign, monkeypatch):
    work, _, _ = campaign
    calls = []
    def run(argv, **kwargs):
        calls.append(argv)
        if argv[0] == 'sbatch':
            if len(calls) == 3:
                raise subprocess.CalledProcessError(1, argv)
            return SimpleNamespace(stdout=str(100 + len(calls)))
        return SimpleNamespace(stdout='')
    monkeypatch.setattr(subprocess, 'run', run)
    with pytest.raises(subprocess.CalledProcessError):
        s.submit(work, 'a', 'q')
    assert [c for c in calls if c[0] == 'scancel'] == [['scancel', '101'], ['scancel', '102']]
    assert not any(c[0] == 'scontrol' for c in calls)


def test_scores_missing_predictions_matched_timing_and_safe_export(campaign):
    work, refs, _ = campaign
    for name in s.check(work)['nemotron_super']['arms']:
        finish(work / 'arms' / name, refs[0], seconds=10)
    partial = s.aggregate(work)
    assert not partial['complete'] and partial['matched']['text']['completed_articles'] == 1
    assert partial['metrics']['text']['validation']['nvfp4-text']['clinical']['recall'] == 0
    with pytest.raises(ValueError, match='lack a completed attempt'):
        s.verify(work)
    for name in s.check(work)['nemotron_super']['arms']:
        finish(work / 'arms' / name, refs[1], seconds=20, prediction=name != 'bf16-text')
    # Exclusion must hold even after actual runtime files exist under each arm.
    (work / 'runtime').mkdir(exist_ok=True)
    (work / 'runtime/model.safetensors').write_text('private runtime')
    for name in s.check(work)['nemotron_super']['arms']:
        path = work / 'arms' / name / 'runtime/caches'
        path.mkdir(parents=True)
        (path / 'private-cache').write_text('private')
    result = s.aggregate(work)
    assert result['complete'] and s.verify(work)['verified']
    assert result['metrics']['text']['validation']['bf16-text']['clinical']['recall'] == 0
    assert result['matched']['text']['by_split']['validation']['bf16-text']['gpu_seconds'] == 40
    with tarfile.open(work / 'results.tar.gz') as archive:
        assert not any('/runtime/' in name or name.endswith('.safetensors') for name in archive.getnames())
    s.aggregate(work)  # integrity inventories must not hash their own old receipt
    (work / 'arms/bf16-text/summary/scores.json').write_text('{}')
    with pytest.raises(ValueError, match='Scored output changed'):
        s.verify(work)


def test_live_smoke_rejects_token_count_mismatch(campaign, tmp_path, monkeypatch):
    work, _, _ = campaign
    cfg = b.read(work / 'arms/nvfp4-text/manifest.json')['extraction']
    real_client = httpx.AsyncClient
    requests = []
    def handle(request):
        body = json.loads(request.content); requests.append(body)
        if request.url.path == '/tokenize':
            return httpx.Response(200, json={'count': 10, 'tokens': [1]*10, 'max_model_len': 65536})
        return httpx.Response(200, json={'usage': {'prompt_tokens': 11}, 'choices': [{'message': {'content': 'READY'}}]})
    monkeypatch.setattr(httpx, 'AsyncClient', lambda **kw: real_client(transport=httpx.MockTransport(handle), **kw))
    with pytest.raises(ValueError, match='counts differ'):
        asyncio.run(s.smoke('http://localhost:8000/v1', tmp_path, cfg, pixels=False))
    assert requests[0]['chat_template_kwargs'] == requests[1]['chat_template_kwargs']


def test_live_smoke_routes_pixel_sampling(campaign, tmp_path, monkeypatch):
    work, _, _ = campaign
    cfg = b.read(work / 'arms/nvfp4-text/manifest.json')['extraction']
    real_client = httpx.AsyncClient
    temperatures = []
    def handle(request):
        body = json.loads(request.content)
        if request.url.path == '/tokenize':
            return httpx.Response(200, json={'count': 10, 'tokens': [1]*10, 'max_model_len': 65536})
        temperatures.append(body['temperature'])
        return httpx.Response(200, json={'usage': {'prompt_tokens': 10}, 'choices': [{'message': {'content': 'READY'}}]})
    monkeypatch.setattr(httpx, 'AsyncClient', lambda **kw: real_client(transport=httpx.MockTransport(handle), **kw))
    asyncio.run(s.smoke('http://localhost:8000/v1', tmp_path, cfg, pixels=True))
    assert temperatures == [1, .6]
    assert b.read(tmp_path / 'smoke.json')['passed']


def test_cpu_setup_pins_local_snapshots_and_worker_dispatch(campaign, monkeypatch):
    work, _, _ = campaign
    downloads = []
    native_sha = b.sha
    def download(**kwargs):
        downloads.append(kwargs)
        model = kwargs['repo_id']; pin = r.PINS[model]; path = kwargs['local_dir']
        b.write(path / 'config.json', {'architectures': [pin['architecture']]})
        (path / 'weights.safetensors').write_bytes(b'fixture-weights')
        if model != r.LILICORR:
            (path / 'chat_template.jinja').write_text('fixture-template')
    def sha(path):
        path = Path(path)
        if path.name == 'chat_template.jinja' and 'weights' in path.parts:
            model = r.LILICORR if path.parent.name == 'lilicorr' else r.PROFILES[path.parent.name]['model']
            return r.PINS[model]['template_sha256']
        return native_sha(path)
    help_text = ' '.join(arg for p in r.PROFILES.values() for arg in p['extra_args']) + ' --generation-config --chat-template'
    def run(argv, **kw):
        if argv[:2] == ['apptainer', 'pull']:
            Path(argv[2]).write_bytes(b'fixture-container')
        return SimpleNamespace(returncode=0, stdout=help_text if '--help=all' in argv else '{"vllm_version":"pinned-nightly"}',
                               stderr='', check_returncode=lambda: None)
    monkeypatch.setitem(sys.modules, 'huggingface_hub', SimpleNamespace(snapshot_download=download, get_token=lambda: None))
    monkeypatch.setattr(subprocess, 'run', run)
    monkeypatch.setattr(b, 'sha', sha)
    monkeypatch.setenv('HF_HOME', 'fixture-before-setup')
    monkeypatch.setenv('HF_XET_CACHE', 'fixture-before-setup')
    monkeypatch.setenv('APPTAINER_CACHEDIR', 'fixture-before-setup')
    monkeypatch.setenv('APPTAINER_TMPDIR', 'fixture-before-setup')
    assert s.setup(work)['ready']
    assert (work / 'arms/lilicorr-text/runtime').is_dir()
    assert {d['repo_id'] for d in downloads} == set(s.check(work)['nemotron_super']['pins'])
    assert all(d['revision'] == r.PINS[d['repo_id']]['revision'] for d in downloads)
    assert all('*.py' in d['allow_patterns'] for d in downloads)
    s.validate_runtime(work, 'lilicorr-text')
    dispatched = {}
    async def worker(arm, shard, **kwargs):
        dispatched.update(arm=arm, shard=shard, **kwargs)
        return {'status': 'done'}
    monkeypatch.setattr(b, 'worker', worker)
    assert asyncio.run(s.worker(work, 'lilicorr-text'))['status'] == 'done'
    cfg = dispatched['serving_factory'](dispatched['arm'], 0, work)
    assert cfg.model_id == r.NVFP4 and cfg.speculative_config['model'].endswith('/weights/lilicorr')
    assert dispatched['campaigns'] == [work / 'arms/lilicorr-text']
    assert dispatched['startup_check'] is not None
    # Refuse modified downloads before reserving a GPU on resume/worker startup.
    (s.weights_path(work, r.NVFP4) / 'weights.safetensors').write_bytes(b'changed')
    with pytest.raises(ValueError, match='artifact changed'):
        s.validate_runtime(work, 'lilicorr-text')


def test_gpu_seconds_account_for_tensor_parallelism(campaign):
    work, refs, _ = campaign
    arm = work / 'arms/bf16-text'
    for ref in refs:
        folder = finish(arm, ref, seconds=10)
        receipt = b.read(folder / 'receipt.json')
        receipt['gpus_per_server'] = 2
        b.write(folder / 'receipt.json', receipt)
    report = b.aggregate(arm)
    assert report['output_tokens_per_gpu_article_second'] == 5
