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
    s.prepare(root, work, expected=3, profiles=['fastino', 'nvfp4', 'bf16', 'lilicorr'], with_images=False,
              splits=['train', 'validation'])
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


def test_vision_replaces_text_for_all_three_nvidia_profiles(campaign, tmp_path, monkeypatch):
    _, _, root = campaign
    frozen = []
    monkeypatch.setattr(b, 'freeze_media', lambda *a, **kw: frozen.append(a[0]['article_id']) or {})
    work = tmp_path / 'vision'
    s.prepare(root, work, expected=3, with_images=True, profiles=['fastino', 'nvfp4', 'bf16', 'lilicorr'],
              splits=['train', 'validation'])
    m = s.check(work)
    assert len(m['nemotron_super']['arms']) == 4 and len(frozen) == 2
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
    assert [j['phase'] for j in result['jobs']] == ['setup'] + ['download', 'worker', 'cleanup'] * 4 + ['aggregate']
    assert '--dependency=afterok:1' in sbatch[1]  # first CPU download
    assert '--dependency=afterok:2' in sbatch[2]  # first GPU worker
    assert '--dependency=afterany:2:3' in sbatch[3]  # cleanup even if download/worker fails
    assert '--dependency=afterok:1,afterok:4' in sbatch[4]  # next download waits for successful deletion
    assert '--dependency=afterok:5' in sbatch[5]
    assert '--dependency=afterany:' + ':'.join(str(i) for i in range(1, 14)) in sbatch[13]
    assert calls[-14:] == [['scontrol', 'release', str(i)] for i in range(14, 0, -1)]
    for job in result['jobs']:
        if job['phase'] != 'worker':
            assert '--partition=hpg-default' in job['argv']
            assert not any(arg.startswith('--gres') for arg in job['argv'])
            assert ('--mem=128G' if job['phase'] == 'download' else '--mem=32G') in job['argv']
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
    with pytest.raises(ValueError, match='Incomplete campaign'):
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


@pytest.fixture
def cpu_runtime(campaign, monkeypatch):
    work, _, _ = campaign
    downloads = []
    native_sha = b.sha
    def download(**kwargs):
        downloads.append(kwargs)
        model = kwargs['repo_id']; pin = r.PINS[model]; path = kwargs['local_dir']
        b.write(path / 'config.json', {'architectures': [pin['architecture']]})
        if '*.safetensors' in kwargs['allow_patterns']:
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
        if '--help=all' in argv and '--nv' not in argv:
            raise AssertionError('vLLM CLI help must never run in CPU download jobs')
        if argv[:2] == ['apptainer', 'build'] and '--help' not in argv:
            Path(argv[-2]).write_bytes(b'fixture-container')
        stdout = ('--mksquashfs-args' if argv == ['apptainer', 'build', '--help'] else
                  help_text if '--help=all' in argv else '{"vllm_version":"pinned-nightly"}')
        return SimpleNamespace(returncode=0, stdout=stdout,
                               stderr='', check_returncode=lambda: None)
    monkeypatch.setitem(sys.modules, 'huggingface_hub', SimpleNamespace(snapshot_download=download, get_token=lambda: None))
    monkeypatch.setattr(subprocess, 'run', run)
    monkeypatch.setattr(b, 'sha', sha)
    monkeypatch.setenv('HF_HUB_CACHE', 'fixture-before-setup')
    monkeypatch.setenv('HF_XET_CHUNK_CACHE_SIZE_BYTES', 'fixture-before-setup')
    monkeypatch.setenv('HF_HOME', 'fixture-before-setup')
    monkeypatch.setenv('HF_XET_CACHE', 'fixture-before-setup')
    monkeypatch.setenv('APPTAINER_CACHEDIR', 'fixture-before-setup')
    monkeypatch.setenv('APPTAINER_TMPDIR', 'fixture-before-setup')
    assert s.setup(work)['ready']
    return work, downloads


def test_cpu_download_rejects_worker_artifact_mismatch_before_ready(cpu_runtime, monkeypatch):
    work, _ = cpu_runtime
    original_serving = s.serving
    def mismatched_config(*args, **kwargs):
        return original_serving(*args, **kwargs).model_copy(update={'model_config_file': 'params.json'})
    monkeypatch.setattr(s, 'serving', mismatched_config)
    with pytest.raises(ValueError, match='params.json'):
        s.download(work, 'fastino')
    assert b.read(work / 'runtime/active-profile.json')['ready'] is False
    assert not (work / 'runtime/profiles/fastino.json').exists()
    assert not (work / 'arms/fastino-text/runtime.json').exists()


def test_cpu_setup_only_metadata_then_downloads_one_profile_and_worker_dispatch(cpu_runtime, monkeypatch):
    work, downloads = cpu_runtime
    assert {d['repo_id'] for d in downloads} == set(s.check(work)['nemotron_super']['pins'])
    assert not any('*.safetensors' in d['allow_patterns'] for d in downloads)
    assert not list((work / 'runtime/weights').rglob('*.safetensors'))
    assert not list((work / 'runtime').glob('*.sif'))
    downloads.clear()
    assert s.download(work, 'lilicorr')['ready']
    assert b.read(work / 'runtime/profiles/lilicorr.json')['serving_artifact_checks'] == {
        'lilicorr-text': {'model_id': r.NVFP4, 'model_config_file': 'config.json'}}
    assert [d['repo_id'] for d in downloads] == [r.NVFP4, r.LILICORR]
    assert all(d['revision'] == r.PINS[d['repo_id']]['revision'] for d in downloads)
    s.validate_runtime(work, 'lilicorr-text')
    with pytest.raises(ValueError, match='active downloaded profile'):
        s.validate_runtime(work, 'bf16-text')
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
    assert dispatched['runtime_probe'] == s.gpu_probe(r.PROFILES['lilicorr'])
    checks = []
    async def token_smoke(*args):
        checks.append('tokenization')
    async def trace_smoke(*args):
        checks.append('reasoning')
    monkeypatch.setattr(s, 'smoke', token_smoke)
    monkeypatch.setattr(s, 'reasoning_smoke', trace_smoke)
    asyncio.run(dispatched['startup_check']('http://localhost:8000/v1', work))
    assert checks == ['tokenization', 'reasoning']
    (s.weights_path(work, r.NVFP4) / 'weights.safetensors').write_bytes(b'changed')
    with pytest.raises(ValueError, match='artifact changed'):
        s.validate_runtime(work, 'lilicorr-text')


@pytest.mark.parametrize('profile_name', list(r.PROFILES))
def test_gpu_probe_validates_flags_for_every_recipe(profile_name, monkeypatch, capsys):
    profile = r.PROFILES[profile_name]
    monkeypatch.setitem(sys.modules, 'torch', SimpleNamespace(__version__='fixture', cuda=SimpleNamespace(
        device_count=lambda: profile['gpus'], get_device_capability=lambda i: (10, 0))))
    monkeypatch.setitem(sys.modules, 'vllm', SimpleNamespace(__version__='fixture'))
    calls = []
    def run(argv, **kwargs):
        calls.append((argv, kwargs))
        return subprocess.CompletedProcess(argv, 0, stdout=' '.join(profile['extra_args']), stderr='diagnostic')
    monkeypatch.setattr(subprocess, 'run', run)
    exec(s.gpu_probe(profile), {})
    captured = capsys.readouterr()
    assert json.loads(captured.out.splitlines()[-1])['serving_flags_verified']
    assert 'diagnostic' in captured.err
    assert calls == [([sys.executable, '-m', 'vllm.entrypoints.cli.main', 'serve', '--help=all'],
                      {'text': True, 'capture_output': True, 'timeout': 120})]


@pytest.mark.parametrize('failure', ['no_gpu', 'cli_failure', 'missing_flag'])
def test_gpu_probe_fails_closed_and_preserves_diagnostics(failure, monkeypatch, capsys):
    profile = r.PROFILES['nvfp4']
    monkeypatch.setitem(sys.modules, 'torch', SimpleNamespace(__version__='fixture', cuda=SimpleNamespace(
        device_count=lambda: 0 if failure == 'no_gpu' else profile['gpus'],
        get_device_capability=lambda i: (10, 0))))
    monkeypatch.setitem(sys.modules, 'vllm', SimpleNamespace(__version__='fixture'))
    calls = []
    def run(argv, **kwargs):
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 1 if failure == 'cli_failure' else 0,
                                           stdout='usage', stderr='device error')
    monkeypatch.setattr(subprocess, 'run', run)
    error = {'no_gpu': AssertionError, 'cli_failure': subprocess.CalledProcessError, 'missing_flag': ValueError}[failure]
    with pytest.raises(error):
        exec(s.gpu_probe(profile), {})
    captured = capsys.readouterr()
    if failure == 'no_gpu':
        assert not calls
    else:
        assert 'device error' in captured.err and 'usage' in captured.out


def test_incomplete_cli_exports_diagnostics_but_exits_unsuccessfully(campaign, capsys):
    work, refs, _ = campaign
    with pytest.raises(SystemExit, match='Campaign incomplete'):
        s.main(['aggregate', '--work', str(work)])
    assert (work / 'results.tar.gz').exists()
    assert (work / 'results.tar.gz.sha256').exists()
    result = json.loads(capsys.readouterr().out)
    metrics = result['metrics']['text']['train']['nvfp4-text']
    assert not metrics['quality_measured']
    assert metrics['completed_articles'] == 0 and metrics['planned_articles'] == 1
    report = (work / 'summary/comparison.md').read_text()
    assert 'INCOMPLETE' in report and '| nvfp4-text | 0 / 1 | not measured |' in report
    for name in s.check(work)['nemotron_super']['arms']:
        for ref in refs:
            finish(work / 'arms' / name, ref)
    s.main(['aggregate', '--work', str(work)])
    assert json.loads(capsys.readouterr().out)['complete']
    assert 'not measured' not in (work / 'summary/comparison.md').read_text()


def test_cleanup_preserves_outputs_reasoning_and_hashes_and_resume_downloads_again(cpu_runtime, monkeypatch):
    work, downloads = cpu_runtime
    s.download(work, 'fastino')
    arm = work / 'arms/fastino-text'
    output = arm / 'results/fixture-response.json'
    b.write(output, {'reasoning': 'fixture rationale', 'citation': 'fixture text'})
    b.write(arm / 'runtime/caches/hf/fixture.json', {})
    receipt = b.read(arm / 'runtime.json')
    assert any(name.endswith('weights.safetensors') for name in receipt['files'])
    cleaned = s.cleanup(work, 'fastino')
    assert cleaned['deleted'] and not s.weights_path(work, r.FASTINO).exists()
    assert not (arm / 'runtime/caches').exists()
    assert not (work / 'runtime/active-profile.json').exists()
    assert b.read(output)['reasoning'] == 'fixture rationale'
    assert b.read(arm / 'runtime.json') == receipt
    assert list((work / 'results/lifecycle').glob('download-fastino-*.json'))
    # Resume does not require artifacts intentionally removed by cleanup.
    with monkeypatch.context() as context:
        context.setattr(subprocess, 'run', lambda *a, **kw: SimpleNamespace(stdout=''))
        resumed = s.submit(work, 'a', 'q', resume=True, dry_run=True)
    assert [c[-3] for c in resumed['commands_without_dependencies']][:3] == ['download', 'worker', 'cleanup']
    downloads.clear()
    s.download(work, 'fastino')
    assert [d['repo_id'] for d in downloads] == [r.FASTINO]
    s.validate_runtime(work, 'fastino-text')


def test_new_profile_reclaims_canceled_cleanup_and_no_weights_overlap(cpu_runtime):
    work, downloads = cpu_runtime
    s.download(work, 'fastino')
    s.download(work, 'bf16')
    assert not s.weights_path(work, r.FASTINO).exists()
    assert s.weights_path(work, r.BF16).exists()
    assert len(list((work / 'runtime/weights').rglob('*.safetensors'))) == 1
    with pytest.raises(ValueError, match='different active profile'):
        s.cleanup(work, 'fastino')
    s.validate_runtime(work, 'bf16-text')


def test_partial_download_failure_is_cleanable_before_next_model(cpu_runtime, monkeypatch):
    work, downloads = cpu_runtime
    def fail(**kwargs):
        path = kwargs['local_dir']
        (path / 'partial.safetensors.incomplete').write_bytes(b'partial')
        raise OSError('fixture disk full')
    hub = sys.modules['huggingface_hub']
    native_download = hub.snapshot_download
    monkeypatch.setattr(hub, 'snapshot_download', fail)
    with pytest.raises(OSError, match='disk full'):
        s.download(work, 'fastino')
    s.cleanup(work, 'fastino')
    assert not s.weights_path(work, r.FASTINO).exists()
    monkeypatch.setattr(hub, 'snapshot_download', native_download)
    s.download(work, 'bf16')
    s.validate_runtime(work, 'bf16-text')


def test_lifecycle_lock_and_symlink_guards_prevent_destructive_cleanup(cpu_runtime, tmp_path):
    work, _ = cpu_runtime
    s.download(work, 'fastino')
    with s.runtime_lock(work):
        with pytest.raises(ValueError, match='still owns the runtime'):
            s.cleanup(work, 'fastino')
        with pytest.raises(ValueError, match='still owns the runtime'):
            s.download(work, 'bf16')
    external = tmp_path / 'external'; external.mkdir()
    (external / 'keep').write_text('keep')
    weights = s.weights_path(work, r.FASTINO)
    import shutil
    shutil.rmtree(weights)
    weights.symlink_to(external, target_is_directory=True)
    with pytest.raises(ValueError, match='escapes campaign ownership'):
        s.cleanup(work, 'fastino')
    assert (external / 'keep').read_text() == 'keep'


def test_one_combined_worker_per_profile_then_cleanup_before_next_download(campaign, tmp_path, monkeypatch):
    monkeypatch.setattr(b, 'freeze_media', lambda *a, **kw: {})
    work = tmp_path / 'staged-vision'
    s.prepare(campaign[2], work, expected=3, profiles=['nvfp4', 'bf16'], with_images=True)
    calls = []
    def run(argv, **kwargs):
        calls.append(argv)
        return SimpleNamespace(stdout=str(sum(c[0] == 'sbatch' for c in calls)))
    monkeypatch.setattr(subprocess, 'run', run)
    jobs = s.submit(work, 'a', 'q')['jobs']
    assert [(j['phase'], j['arm']) for j in jobs[:6]] == [
        ('setup', None), ('download', 'nvfp4'), ('worker', 'nvfp4-vision'),
        ('cleanup', 'nvfp4'), ('download', 'bf16'), ('worker', 'bf16-vision')]
    assert '--dependency=afterok:2' in jobs[2]['argv']
    assert '--dependency=afterany:2:3' in jobs[3]['argv']
    assert '--dependency=afterok:1,afterok:4' in jobs[4]['argv']


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


def test_cleanup_old_campaign_keeps_frozen_receipts_and_refuses_active_jobs(cpu_runtime, monkeypatch, tmp_path):
    work, _ = cpu_runtime
    s.download(work, 'fastino')
    s.download(work, 'bf16')
    # Emulate the pre-staging format, including the old all-model runtime receipt.
    manifest = b.read(work / 'manifest.json')
    manifest['nemotron_super'].pop('storage_policy')
    b.write(work / 'manifest.json', manifest)
    b.write(work / 'prepared.json', {'manifest_sha256': b.sha(work / 'manifest.json')})
    b.write(work / 'runtime/owner.json', {'work': str(work.resolve()), 'manifest_sha256': b.sha(work / 'manifest.json')})
    original_receipt = {'ready': True, 'files': {'fixture-weights': {}}, 'engines': manifest['nemotron_super']['engines']}
    b.write(work / 'runtime.json', original_receipt)
    b.write(work / 'submissions/old.json', {'jobs': [{'id': '100', 'phase': 'setup'}]})
    with monkeypatch.context() as context:
        context.setattr(subprocess, 'run', lambda *a, **kw: SimpleNamespace(stdout='100\n'))
        with pytest.raises(ValueError, match='Cancel/wait'):
            s.cleanup(work)
        assert s.weights_path(work, r.BF16).exists()
    neighbor = tmp_path / 'other-campaign'; neighbor.mkdir()
    (neighbor / 'weights.safetensors').write_text('keep')
    with monkeypatch.context() as context:
        context.setattr(subprocess, 'run', lambda *a, **kw: SimpleNamespace(stdout='999\n'))
        s.cleanup(work)
        assert not list((work / 'runtime/weights').rglob('*.safetensors'))
        assert (neighbor / 'weights.safetensors').read_text() == 'keep'
        assert b.read(work / 'runtime.json') == original_receipt
        assert s.check(work)['article_count'] == 2


def test_completed_profile_is_not_redownloaded_on_resume(cpu_runtime, monkeypatch, campaign):
    work, _ = cpu_runtime
    s.download(work, 'fastino')
    for ref in campaign[1]:
        finish(work / 'arms/fastino-text', ref)
    s.cleanup(work, 'fastino')
    monkeypatch.setattr(subprocess, 'run', lambda *a, **kw: SimpleNamespace(stdout=''))
    plan = s.submit(work, 'a', 'q', resume=True, dry_run=True)
    assert all(cmd[-1] != 'fastino' and cmd[-1] != 'fastino-text' for cmd in plan['commands_without_dependencies'])
    assert any(cmd[-3:] == ['download', str(work), 'nvfp4'] for cmd in plan['commands_without_dependencies'])


def test_image_conversion_scratch_is_removed_before_large_weights(cpu_runtime, monkeypatch):
    work, _ = cpu_runtime
    hub = sys.modules['huggingface_hub']
    original_download = hub.snapshot_download
    def download(**kwargs):
        if '*.safetensors' in kwargs['allow_patterns']:
            assert (work / 'runtime' / r.engine(r.PROFILES['fastino'])['sif']).exists()
            assert not (work / 'runtime/apptainer-cache').exists()
            assert list((work / 'runtime/apptainer-tmp').iterdir()) == []
        return original_download(**kwargs)
    b.write(work / 'runtime/apptainer-cache/fixture.json', {'layer': 'temporary layer'})
    b.write(work / 'runtime/apptainer-tmp/fixture.json', {'scratch': 'temporary build'})
    monkeypatch.setattr(hub, 'snapshot_download', download)
    s.download(work, 'fastino')


def test_scores_and_export_verify_after_every_model_has_been_deleted(cpu_runtime, campaign):
    work, _ = cpu_runtime
    for profile in s.check(work)['nemotron_super']['profiles']:
        s.download(work, profile)
        for ref in campaign[1]:
            finish(work / 'arms' / (profile + '-text'), ref)
        s.cleanup(work, profile)
    assert not list((work / 'runtime/weights').rglob('*.safetensors'))
    assert s.aggregate(work)['complete']
    assert s.verify(work)['verified']
    with tarfile.open(work / 'results.tar.gz') as archive:
        names = archive.getnames()
        assert sum('/results/lifecycle/download-' in name for name in names) == 4
        assert sum('/results/lifecycle/cleanup-' in name for name in names) == 4
        assert not any(name.endswith('.safetensors') for name in names)


def test_container_build_bounds_compression_and_publishes_only_valid_image(cpu_runtime, monkeypatch):
    work, _ = cpu_runtime
    sif = work / 'runtime' / r.engine(r.PROFILES['fastino'])['sif']
    image = r.engine(r.PROFILES['fastino'])['image']
    calls = []
    def run(argv, **kwargs):
        calls.append(argv)
        if argv == ['apptainer', 'build', '--help']:
            return SimpleNamespace(stdout='--mksquashfs-args')
        if argv[:2] == ['apptainer', 'build']:
            assert argv[2:4] == ['--mksquashfs-args', '-processors 8 -mem 8G']
            assert argv[-1] == image
            assert not sif.exists()
            Path(argv[-2]).write_bytes(b'completed image')
        if argv[:3] == ['apptainer', 'sif', 'list']:
            assert not sif.exists()
            assert Path(argv[-1]).read_bytes() == b'completed image'
        return SimpleNamespace(stdout='')
    monkeypatch.setattr(subprocess, 'run', run)
    s.build_image(work, sif, image)
    assert sif.read_bytes() == b'completed image'
    assert not sif.with_suffix('.building.sif').exists()
    assert len(calls) == 3


@pytest.mark.parametrize('failure', ['build', 'validation'])
def test_failed_image_is_not_published_and_no_weights_download(cpu_runtime, monkeypatch, failure):
    work, downloads = cpu_runtime
    downloads.clear()
    sif = work / 'runtime' / r.engine(r.PROFILES['fastino'])['sif']
    def run(argv, **kwargs):
        if argv == ['apptainer', 'build', '--help']:
            return SimpleNamespace(stdout='--mksquashfs-args')
        if argv[:2] == ['apptainer', 'build']:
            Path(argv[-2]).write_bytes(b'partial image')
            if failure == 'build':
                raise subprocess.CalledProcessError(137, argv)
        if argv[:3] == ['apptainer', 'sif', 'list']:
            raise subprocess.CalledProcessError(1, argv)
        return SimpleNamespace(stdout='')
    monkeypatch.setattr(subprocess, 'run', run)
    with pytest.raises(subprocess.CalledProcessError):
        s.download(work, 'fastino')
    assert downloads == []
    assert not sif.exists() and not sif.with_suffix('.building.sif').exists()
    s.cleanup(work, 'fastino')


def test_old_apptainer_fails_before_image_or_weights_download(cpu_runtime, monkeypatch):
    work, downloads = cpu_runtime
    downloads.clear()
    calls = []
    def run(argv, **kwargs):
        calls.append(argv)
        return SimpleNamespace(stdout='Apptainer 1.3 build options')
    monkeypatch.setattr(subprocess, 'run', run)
    with pytest.raises(RuntimeError, match='Load Apptainer 1.4'):
        s.download(work, 'fastino')
    assert calls == [['apptainer', 'build', '--help']]
    assert downloads == []


def test_cleanup_reclaims_image_left_by_hard_kill(cpu_runtime):
    work, _ = cpu_runtime
    temporary = (work / 'runtime' / r.engine(r.PROFILES['fastino'])['sif']).with_suffix('.building.sif')
    temporary.write_bytes(b'partial file from a killed process')
    s.cleanup(work, 'fastino')
    assert not temporary.exists()
