import asyncio
import copy
import shutil
import tarfile
import time
import gzip
import json
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from openpatients2 import dense_benchmark as b, dense_fastino as f
from openpatients2.pilot_extract import PilotConfig, PilotRunner
from test_dense_benchmark import ROOT, campaign, finish
from test_pilot_extract import article


def fastino_config():
    return {**b.extraction_config(), 'model_id': f.MODEL, 'revision': f.REVISION,
            'temperature': 0.0, 'max_figures': 0, 'focused_pixels': False}


def fixture_campaign(tmp_path, n=3):
    work, refs, m = campaign(tmp_path, n)
    m.update(format='dense-fastino-benchmark/1', model={'id': f.MODEL, 'revision': f.REVISION},
             text_only=True, extraction=fastino_config(), fastino={'hours': 8})
    b.write(work / 'manifest.json', m)
    b.write(work / 'runtime/model-owner.json', {'work': str(work.resolve()), 'model': f.MODEL, 'revision': f.REVISION})
    for name in ('weights/test.safetensors', 'download-cache/file', 'vllm-0.30.0.sif'):
        path = work / 'runtime' / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('fixture')
    return work, refs, m


def test_prepare_selection_frozen_source_and_no_images(tmp_path, monkeypatch):
    root = tmp_path / 'repo'
    for folder in ('src', 'scripts', 'configs/hipergator/dense-benchmark', 'docs'):
        (root / folder).mkdir(parents=True)
    for name in ('pyproject.toml', 'uv.lock', 'README.md', 'LICENSE'):
        (root / name).write_text('fixture')
    for name in ('DENSE260_HIPERGATOR_BENCHMARK.md', 'DENSE_FASTINO.md'):
        (root / 'docs' / name).write_text('fixture')
    (root / 'configs/hipergator/dense-benchmark/chat_template.jinja').write_bytes(
        (ROOT / 'configs/hipergator/dense-benchmark/chat_template.jinja').read_bytes())
    ref = next(iter(b.rows(ROOT / 'tests/fixtures/unified_scoring_reference.jsonl').values()))
    refs = []
    for i, split in enumerate(('train', 'validation', 'test'), 1):
        source = article(); source['article_id'] = f'PMC{i}.1'
        b.write(root / 'benchmarks/dense/articles' / source['article_id'] / 'source.json', source)
        refs.append({**ref, 'article_id': source['article_id'], 'split': split,
                     'text_sha256': source['text_sha256'], 'completion': {'completed': True, 'checks': {'ok': True}}})
    frozen = root / 'configs/hipergator/dense-benchmark/reference.jsonl.gz'
    with gzip.open(frozen, 'wt') as out:
        out.write(''.join(json.dumps(r) + '\n' for r in refs))
    before = b.sha(frozen)
    def forbidden(*a, **kw):
        raise AssertionError('Text model must never acquire/freeze pixels')
    monkeypatch.setattr(b, 'freeze_media', forbidden)
    work = tmp_path / 'run'
    f.prepare(root, work, expected=3)
    m = f.check(work)
    assert m['splits'] == {'train': 1, 'validation': 1}
    assert m['drafter'] is None and m['text_only']
    assert m['fastino']['arms'] == list(f.ARMS)
    for arm in f.campaigns(work):
        am = b.check_snapshot(arm)
        assert am['extraction']['fastino_reasoning'] == arm.name
        assert PilotConfig.model_validate(am['extraction']).template_kwargs() == m['fastino']['reasoning_modes'][arm.name]
    assert not (work / 'inputs/PMC3.1').exists()
    assert b.sha(frozen) == before
    assert PilotConfig.model_validate(m['extraction']).template_kwargs() == {'enable_thinking': False}
    all_work = tmp_path / 'all'
    f.prepare(root, all_work, expected=3, include_test=True)
    assert f.check(all_work)['article_count'] == 3


@pytest.mark.parametrize('mode,kwargs', [('off', {'enable_thinking': False}),
    ('on', {'enable_thinking': True, 'medium_effort': False}),
    ('efficient', {'enable_thinking': True, 'medium_effort': True})])
def test_native_request_and_tokenizer_use_identical_kwargs(tmp_path, mode, kwargs):
    cfg = {**fastino_config(), 'fastino_reasoning': mode}
    seen = []
    def tokenize(request):
        body = json.loads(request.content)
        assert body['chat_template_kwargs'] == kwargs
        seen.append(body)
        return httpx.Response(200, json={'count': 10, 'tokens': [1]*10, 'max_model_len': 65536})
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(tokenize)) as http:
            runner = PilotRunner(cfg, tmp_path, ['http://localhost:8000/v1'], 65536, 'targeted', http=http)
            try:
                messages = [{'role': 'user', 'content': 'Extract the text'}]
                client = runner.clients[0]
                body = client.body('summary', messages, 128)
                assert body['temperature'] == 0 and body['chat_template_kwargs'] == kwargs
                assert 'structured_outputs' not in body and 'response_format' not in body
                await runner.token_count(client, messages)
            finally:
                await runner.close()
    asyncio.run(run())
    assert len(seen) == 1
    with pytest.raises(ValueError, match='revision'):
        PilotConfig.model_validate({**cfg, 'revision': b.REVISION})
    with pytest.raises(ValueError, match='text-only'):
        PilotConfig.model_validate({**cfg, 'max_figures': 1})
    assert PilotConfig().template_kwargs() == {'reasoning_strength': 'medium'}


def test_serving_and_cpu_gpu_dag(tmp_path, monkeypatch):
    from openpatients2.dense_benchmark_serving import render
    monkeypatch.setenv('CUDA_VISIBLE_DEVICES', '3')
    work, refs, _ = fixture_campaign(tmp_path, 4)
    cfg = f.serving(work, 1, work)
    argv = render(cfg, str(work))[0]['argv']
    assert cfg.reasoning_parser == 'nemotron_v3' and cfg.speculative_config is None
    assert cfg.tensor_parallel == 1 and cfg.model_id == f.MODEL
    assert '--dtype' in argv and '--speculative-config' not in argv
    assert not any('glimmer' in arg.lower() for arg in argv)
    plan = b.submit(work, 'a', 'q', 8, dry_run=True, campaigns=[work],
                    script_name='dense_fastino.sbatch', gpu_memory='128G')
    gpu = [c for c in plan['commands_without_dependencies'] if '--gres=gpu:b200:1' in c]
    cpu = [c for c in plan['commands_without_dependencies'] if '--gres=gpu:b200:1' not in c]
    assert len(gpu) == 4 and len(cpu) == 2
    assert all('--mem=128G' in c and '--time=08:00:00' in c for c in gpu)
    assert all('--partition=hpg-default' in c for c in cpu)


def test_complete_export_cleanup_and_partial_preservation(tmp_path):
    work, refs, _ = fixture_campaign(tmp_path)
    finish(work, refs[0], seconds=11)
    result = f.aggregate(work)
    assert not result['complete'] and result['clinical_f2'] == pytest.approx(5/13)
    assert (work / 'runtime/weights/test.safetensors').exists()
    assert (work / 'results.tar.gz').exists()
    for ref in refs[1:]:
        finish(work, ref, seconds=20)
    result = f.aggregate(work)
    assert result['complete'] and result['clinical_f2'] == 1
    assert result['article_seconds']['sum'] == 51
    assert b.verify(work)['verified']
    assert not (work / 'runtime/weights').exists()
    assert not (work / 'runtime/download-cache').exists()
    assert not (work / 'runtime/vllm-0.30.0.sif').exists()
    assert (work / 'cleanup.json').exists()
    # Idempotent CPU cleanup and verification after weights have been removed.
    f.cleanup(work)
    assert b.verify(work)['verified']


def test_cleanup_refuses_active_jobs_and_external_paths(tmp_path, monkeypatch):
    work, refs, _ = fixture_campaign(tmp_path / 'campaign', 1)
    finish(work, refs[0])
    b.write(work / 'submissions/first.json', {'jobs': [{'id': '123', 'phase': 'worker'}]})
    monkeypatch.setenv('USER', 'tester')
    monkeypatch.setattr(f.subprocess, 'run', lambda *a, **kw: SimpleNamespace(stdout='123\n'))
    with pytest.raises(ValueError, match='still active'):
        f.aggregate(work)
    monkeypatch.setattr(f.subprocess, 'run', lambda *a, **kw: SimpleNamespace(stdout=''))
    f.aggregate(work)
    outside = tmp_path / 'outside'; outside.mkdir(); (outside / 'keep').write_text('keep')
    (work / 'runtime/weights').symlink_to(outside, target_is_directory=True)
    with pytest.raises(ValueError, match='campaign-owned'):
        f.cleanup(work)
    assert (outside / 'keep').read_text() == 'keep'
    (work / 'runtime/weights').unlink()
    (work / 'results.tar.gz').write_text('corrupt')
    with pytest.raises(ValueError, match='checksum mismatch'):
        f.cleanup(work)


def test_text_only_article_never_passes_image_manifest(tmp_path, monkeypatch):
    from openpatients2 import pilot_extract, ehr_pipeline
    work, refs, manifest = fixture_campaign(tmp_path, 1)
    source = work / 'inputs' / refs[0]['article_id'] / 'source.jsonl'
    b.write_rows(source, [article()])
    seen = []
    class Runner:
        def __init__(self, *a, **kw):
            pass
        async def run(self, path, image_manifest=None):
            seen.append(image_manifest)
            return {'selected_articles': 1, 'eligible_articles': 1, 'patients_extracted': 1,
                    'task_statuses': {'valid': 1}, 'articles': [{}], 'valid_tasks': 1, 'task_count': 1}
        async def close(self):
            pass
    async def ehr(cfg, source, extraction, output, endpoints, context, *, image_manifest):
        assert not cfg['figure_ehr_label'] and not cfg['figure_privacy_flags']
        assert cfg['measurement_series_extraction']
        seen.append(image_manifest)
        b.write_rows(output / 'data/unified.jsonl', refs)
        return {'articles': [{'errors': {}}]}
    monkeypatch.setattr(pilot_extract, 'PilotRunner', Runner)
    monkeypatch.setattr(ehr_pipeline, 'run_ehr_stages', ehr)
    receipt = asyncio.run(b.run_article(work, manifest['articles'][0], 'http://localhost/v1', work / 'attempt'))
    assert receipt['status'] == 'done' and seen == [None, None]


def test_cpu_setup_pins_download_and_records_artifacts(tmp_path, monkeypatch):
    import sys
    work, _, _ = fixture_campaign(tmp_path, 1)
    calls = []
    def download(**kwargs):
        assert kwargs['repo_id'] == f.MODEL and kwargs['revision'] == f.REVISION
        assert str(kwargs['cache_dir']).startswith(str(work / 'runtime/download-cache'))
        assert 'HF_XET_CACHE' in f.os.environ
        weights = kwargs['local_dir']
        b.write(weights / 'config.json', {'architectures': ['NemotronHForCausalLM'], 'max_position_embeddings': 262144})
        (weights / 'chat_template.jinja').write_text('native fixture')
    monkeypatch.setitem(sys.modules, 'huggingface_hub', SimpleNamespace(snapshot_download=download))
    monkeypatch.setattr(f, 'TEMPLATE_SHA', b.hashlib.sha256(b'native fixture').hexdigest())
    def run(argv, **kwargs):
        calls.append(argv)
        assert '--nv' not in argv
        compile(argv[argv.index('-c')+1], '<cpu-probe>', 'exec')
        return SimpleNamespace(returncode=0, stdout='verified', stderr='', check_returncode=lambda: None)
    monkeypatch.setattr(f.subprocess, 'run', run)
    # setup changes these for its private download; keep this test hermetic.
    monkeypatch.setenv('HF_HOME', '')
    monkeypatch.setenv('HF_XET_CACHE', '')
    result = f.setup(work)
    assert result['ready'] and len(calls) == 1
    rt = b.read(work / 'runtime.json')
    assert rt['model_revision'] == f.REVISION
    assert 'runtime/weights/test.safetensors' in rt['files']
    assert not any('drafter' in p for p in rt['files'])
    assert b.read(work / 'results/setup-probe.json')['returncode'] == 0


def sweep_fixture(tmp_path):
    work, refs, m = fixture_campaign(tmp_path, 2)
    m['created_unix'] = time.time()
    m['fastino'].update(arms=list(f.ARMS), include_test=False,
        reasoning_modes={mode: PilotConfig.model_validate({**fastino_config(), 'fastino_reasoning': mode}).template_kwargs() for mode in f.ARMS})
    for mode in f.ARMS:
        arm = work / 'arms' / mode
        shutil.copytree(work / 'reference', arm / 'reference')
        am = copy.deepcopy(m)
        am['files'] = {}
        am['extraction']['fastino_reasoning'] = mode
        b.write(arm / 'manifest.json', am)
        m['files'][f'arms/{mode}/manifest.json'] = b.sha(arm / 'manifest.json')
    b.write(work / 'manifest.json', m)
    return work, refs


def test_sweep_scores_matched_timing_export_and_cleanup(tmp_path):
    work, refs = sweep_fixture(tmp_path)
    for mode, seconds in [('off', 10), ('efficient', 15), ('on', 25)]:
        for ref in refs:
            if not (mode == 'on' and ref == refs[-1]):
                finish(work / 'arms' / mode, ref, seconds=seconds)
    report = f.aggregate(work)
    assert not report['complete'] and report['completed_article_triplets'] == 1
    assert report['scores_completed_triplets']['validation']['on'] is None
    assert report['metrics']['validation']['on']['clinical_f2'] == 0
    assert f.status(work)['arms']['on']['pending'] == 1
    assert (work / 'runtime/weights').exists()
    with pytest.raises(ValueError, match='lack a completed attempt'):
        f.verify(work)
    finish(work / 'arms/on', refs[-1], seconds=25)
    report = f.aggregate(work)
    assert report['complete'] and report['completed_article_triplets'] == 2
    assert report['paired_timing']['validation']['speedup_vs_off']['on'] == .4
    assert report['article_seconds']['efficient']['mean'] == 15
    assert all(report['metrics']['validation'][m]['clinical_f2'] == 1 for m in f.ARMS)
    assert f.verify(work)['verified']
    assert not (work / 'runtime/weights').exists()
    assert b.read(work / 'cleanup.json')['cpu_aggregate_export_cleanup_seconds'] > 0
    with tarfile.open(work / 'results.tar.gz') as archive:
        names = archive.getnames()
        assert any(n.endswith('arms/on/summary/scores-validation.json') for n in names)
        assert any(n.endswith('summary/comparison.json') for n in names)
        assert not any('/runtime/' in n for n in names)
    (work / 'arms/on/summary/scores-validation.json').write_text('{}')
    with pytest.raises(ValueError, match='Scored output changed'):
        f.verify(work)


def test_sweep_submit_and_worker_dispatch_all_modes(tmp_path, monkeypatch):
    work, refs = sweep_fixture(tmp_path)
    seen = {}
    def submit(*args, **kwargs):
        seen['submit'] = kwargs
        return {'submitted': False}
    async def worker(*args, **kwargs):
        seen['worker'] = kwargs
        return {'status': 'done'}
    monkeypatch.setattr(b, 'submit', submit)
    monkeypatch.setattr(b, 'worker', worker)
    f.main(['submit', '--work', str(work), '--dry-run'])
    f.main(['worker', '--work', str(work), '--shard', '0'])
    assert seen['submit']['campaigns'] == seen['worker']['campaigns'] == f.campaigns(work)
    assert seen['worker']['serving_factory'] == f.serving
    # No additional model profile or Glimmer worker is submitted.
    assert seen['worker']['runtime_probe'] == f.RUNTIME_PROBE


def test_other_campaign_job_does_not_block_or_get_cancelled(tmp_path, monkeypatch):
    work, _ = sweep_fixture(tmp_path)
    b.write(work / 'submissions/own.json', {'jobs': [{'id': '123', 'phase': 'worker'}]})
    seen = []
    def run(argv, **kwargs):
        seen.append(argv)
        return SimpleNamespace(stdout='999\n')  # unrelated current run
    monkeypatch.setenv('USER', 'tester')
    monkeypatch.setattr(f.subprocess, 'run', run)
    f.assert_idle(work)
    assert len(seen) == 1 and seen[0][0] == 'squeue'
