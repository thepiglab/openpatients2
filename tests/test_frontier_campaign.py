"""Distributed scheduler safety and full-denominator aggregation regressions."""
from contextlib import asynccontextmanager
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from openpatients2 import frontier_campaign as fc
from openpatients2 import ledger_evaluation
from openpatients2.data import read_jsonl, write_json
from openpatients2.provenance import json_digest

ROOT = Path(__file__).resolve().parents[1]


def campaign(tmp_path, workers=4):
    cfg = yaml.safe_load((ROOT / 'configs/pilot/frontier.yaml').read_text())
    cfg.update(gpu_workers=workers, worker_cpus=32 // workers, worker_mem_gb=240 // workers)
    for key in ('engine_config', 'extraction_config', 'fixed_source', 'fixed_rosters',
                'fidelity_reference', 'tokenizer_metadata', 'chat_template'):
        cfg[key] = str(ROOT / cfg[key])
    work = tmp_path / 'campaign'; work.mkdir()
    result = {'work': str(work), 'root': str(ROOT), 'config': cfg,
              'config_sha256': json_digest(cfg), 'runtime': {}}
    write_json(work / 'frontier.json', result)
    write_json(work / 'source-owner.json', {'work': str(work), 'config_sha256': result['config_sha256'],
                                          'scope': 'corpus-pilot-sources/1'})
    return result


@pytest.mark.parametrize('workers', [4, 8])
def test_independent_workers_fit_global_allocation_and_cpu_stages_request_no_gpu(tmp_path, workers):
    c = campaign(tmp_path, workers)
    assert fc.validate_plan(c['config'])
    jobs = [fc.job_command(c, phase, shard) for phase, shard in fc.stages(c)]
    gpu = [j for j in jobs if '--gres=gpu:b200:1' in j]
    cpu = [j for j in jobs if '--gres=none' in j]
    assert len(gpu) == workers and len(cpu) == 5
    assert all('--nodes=1' in j and '--ntasks=1' in j for j in gpu)
    assert all(not any(a.startswith(('--nodelist', '--constraint', '--exclusive')) for a in j) for j in gpu)
    assert sum(int(next(a.split('=')[1] for a in j if a.startswith('--cpus-per-task'))) for j in gpu) == 32
    assert sum(int(next(a.split('=')[1][:-1] for a in j if a.startswith('--mem='))) for j in gpu) == 240
    assert all('--time=01:30:00' in j for j in gpu)
    assert all('--account=cai5724' in j and '--qos=cai5724' in j for j in jobs)
    assert '--cpus-per-task=32' in cpu[0] and '--mem=64G' in cpu[0]
    for j in cpu:
        assert not any(a.startswith('--gres=gpu') for a in j)


@pytest.mark.parametrize('field,value', [('worker_cpus', 9), ('worker_mem_gb', 63),
    ('gpu_workers', 16), ('gpu_minutes', 480), ('gpu_minutes', 59), ('gpu_minutes', 121),
    ('account', 'other'), ('qos', 'other'), ('sample_size', 20), ('workers', 33),
    ('separate_gepa', True), ('variants', ['ledger-delta'])])
def test_unsafe_or_unpaired_plans_are_rejected(tmp_path, field, value):
    c = campaign(tmp_path)
    c['config'][field] = value
    with pytest.raises(ValueError): fc.validate_plan(c['config'])


def slurm_mock(monkeypatch, failure=None):
    calls = []; counter = 100
    def run(args, env, path, audit):
        nonlocal counter
        calls.append(args)
        assert not any(k.startswith('SBATCH_') for k in env)
        if args[0] == 'sbatch' and '--test-only' not in args:
            counter += 1
            if failure == counter: raise RuntimeError('quota exceeded')
        return SimpleNamespace(stdout=f'{counter};cluster\n')
    monkeypatch.setattr('openpatients2.hipergator.slurm_command', run)
    return calls


def test_failure_tolerant_fanout_and_fanin_cleanup_dependencies(tmp_path, monkeypatch):
    c = campaign(tmp_path)
    monkeypatch.setenv('SBATCH_GRES', 'gpu:8')
    calls = slurm_mock(monkeypatch)
    result = fc.submit(c)
    submitted = [a for a in calls if a[0] == 'sbatch' and '--test-only' not in a]
    assert len(submitted) == 9 and all('--hold' in a for a in submitted)
    assert '--dependency=afterany:101' in submitted[1]
    assert '--dependency=afterany:102' in submitted[2]
    # All independent worker jobs share the one CPU download dependency.
    assert all('--dependency=afterany:103' in a for a in submitted[3:7])
    assert '--dependency=afterany:104:105:106:107' in submitted[7]
    # Cleanup waits for aggregate, download and ALL workers, including failures.
    assert '--dependency=afterany:108:103:104:105:106:107' in submitted[8]
    assert not any('afterok' in a for args in submitted for a in args)
    releases = [a[-1] for a in calls if a[:2] == ['scontrol', 'release']]
    assert releases == [str(i) for i in range(109, 100, -1)]
    assert len([a for a in calls if '--test-only' in a]) == len(submitted)
    assert result['status'] == 'released'
    with pytest.raises(ValueError, match='already submitted'): fc.submit(c)


def test_partial_submission_cancels_held_jobs_before_any_release(tmp_path, monkeypatch):
    c = campaign(tmp_path)
    calls = slurm_mock(monkeypatch, failure=105)
    with pytest.raises(RuntimeError, match='quota'): fc.submit(c)
    assert ['scancel', '101', '102', '103', '104'] in calls
    assert not any(a[:2] == ['scontrol', 'release'] for a in calls)
    ledger = fc.read_json(Path(c['work']) / 'frontier-jobs.json')
    assert ledger['rollback'] == 'cancellation_requested'


def test_release_failure_reholds_entire_dag_then_cancels(tmp_path, monkeypatch):
    c = campaign(tmp_path); calls = []; n = 100
    def run(args, *rest):
        nonlocal n
        calls.append(args)
        if args[0] == 'sbatch' and '--test-only' not in args: n += 1
        if args[:2] == ['scontrol', 'release'] and args[-1] == '107': raise RuntimeError('release failed')
        return SimpleNamespace(stdout=str(n))
    monkeypatch.setattr('openpatients2.hipergator.slurm_command', run)
    with pytest.raises(RuntimeError, match='release'): fc.submit(c)
    expected = [str(i) for i in range(101, 110)]
    assert ['scontrol', 'hold', *expected] in calls
    assert ['scancel', *expected] in calls


def test_shards_partition_all_articles_and_share_aligned_pixel_files(tmp_path, monkeypatch):
    c = campaign(tmp_path)
    work = Path(c['work']); (work / 'profile').mkdir(); (work / 'vision-assets').mkdir()
    rows = [{'article_id': f'PMC{i}.1'} for i in range(31)]
    import gzip
    with gzip.open(work / 'profile/sample.jsonl.gz', 'wt') as stream:
        for row in rows: stream.write(json.dumps(row) + '\n')
    media = {'figures': [{'article_id': 'PMC1.1', 'figure_id': 'f1',
                         'file': 'vision-assets/shared.image'}], 'complete': True}
    write_json(work / 'vision-assets/manifest.json', media)
    monkeypatch.setattr(fc, 'verify_cpu', lambda c: {'status': 'ready', 'hashes': {}})
    result = fc.prepare_shards(c)
    all_ids = [a for shard in result['shards'] for a in shard['articles']]
    assert len(all_ids) == len(set(all_ids)) == 31
    assert set(all_ids) == {a['article_id'] for a in rows}
    selected = [fc.read_json(s['media']) for s in result['shards']]
    assert sum(len(m['figures']) for m in selected) == 1
    assert next(m['figures'][0]['file'] for m in selected if m['figures']) == 'vision-assets/shared.image'
    for shard in result['shards']:
        assert {a['article_id'] for a in read_jsonl(shard['sample'])} == set(shard['articles'])
    assert 'shards.json' in fc.read_json(work / 'cpu.json')['hashes']


def test_shared_readers_coexist_and_block_owned_cleanup_lock(tmp_path):
    from openpatients2.hipergator import model_lock
    with fc.checkpoint_reader(tmp_path):
        with fc.checkpoint_reader(tmp_path):
            with pytest.raises(BlockingIOError):
                with model_lock(tmp_path): pass
    with model_lock(tmp_path): pass


def test_worker_model_mount_is_immutable_and_every_cache_and_port_is_isolated(tmp_path, monkeypatch):
    c = campaign(tmp_path); engine = {'work': str(Path(c['work']) / 'engine')}
    monkeypatch.setenv('CUDA_VISIBLE_DEVICES', 'GPU-allocated')
    def serving_config(*args):
        from openpatients2.serving import ServingConfig
        return ServingConfig(name='test', backend='vllm', version='0.30.0', image_reference='local',
            model_id='local', replicas=1, tensor_parallel=1,
            model_path=str(Path(engine['work']) / 'active-model/weights'),
            sif='/tmp/test.sif', max_model_len=65536, max_num_seqs=64, max_batched_tokens=32768,
            environment={'HF_HUB_OFFLINE': '1', 'XDG_CACHE_HOME': str(Path(engine['work']) / 'runtime-cache')})
    monkeypatch.setattr('openpatients2.glimmer_benchmark.serving_config', serving_config)
    cfg0 = fc.worker_serving_config(c, {**engine, 'config': {}}, {}, 0)
    cfg1 = fc.worker_serving_config(c, {**engine, 'config': {}}, {}, 1)
    assert cfg0.bind_files[engine['work']] == engine['work']
    assert cfg0.port != cfg1.port and cfg0.rpc_port != cfg1.rpc_port
    for key in ('HF_HOME', 'HF_HUB_CACHE', 'HF_XET_CACHE', 'HF_MODULES_CACHE', 'VLLM_CACHE_ROOT',
                'TRITON_CACHE_DIR', 'TORCHINDUCTOR_CACHE_DIR', 'XDG_CACHE_HOME'):
        assert cfg0.environment[key] != cfg1.environment[key]
        assert '/shards/000/runtime-cache/' in cfg0.environment[key]
        assert engine['work'] not in cfg0.environment[key]
    from openpatients2.serving import render
    command = render(cfg0, c['work'])[0]['argv']
    assert engine['work'] + ':' + engine['work'] + ':ro' in command
    assert '--tensor-parallel-size' in command and cfg0.gpus == 1


def test_paired_trial_config_preserves_media_and_caps_and_replaces_legacy_reviews(tmp_path):
    c = campaign(tmp_path)
    baseline = fc.trial_config(c, 'live-complete', 8)
    delta = fc.trial_config(c, 'ledger-delta', 8)
    audit = fc.trial_config(c, 'ledger-audit', 8)
    assert baseline['experimental_pipeline'] == 'original'
    assert delta['experimental_pipeline'] == audit['experimental_pipeline'] == 'evidence_ledger'
    assert not delta['ledger_audit'] and audit['ledger_audit']
    assert delta['ledger_delta_timeline'] and audit['ledger_delta_timeline']
    for key in ('max_output_tokens', 'max_retry_tokens', 'reasoning_strength', 'pixel_strategy',
                'focused_pixels', 'isolate_secondary_cases'):
        assert baseline[key] == delta[key] == audit[key]
    assert delta['pixel_strategy'] == 'staged' and delta['max_articles'] == 8
    for key in ('inventory_clinical_features', 'coverage_repair', 'audit_claims',
                'review_ordering', 'timeline_completion', 'summary_completion'):
        assert baseline[key] and not delta[key] and not audit[key]


@pytest.mark.asyncio
async def test_failed_cpu_preparation_never_starts_a_gpu_server(tmp_path, monkeypatch):
    c = campaign(tmp_path)
    monkeypatch.setenv('CUDA_VISIBLE_DEVICES', '0')
    monkeypatch.setattr(fc, 'verify_cpu', lambda c: (_ for _ in ()).throw(ValueError('CPU preparation is not ready')))
    calls = []
    monkeypatch.setattr('openpatients2.serving.ServerGroup', lambda *args: calls.append(args))
    with pytest.raises(ValueError, match='not ready'): await fc.run_worker(c, 0)
    assert calls == []


@pytest.mark.asyncio
async def test_worker_executes_all_pairs_with_endpoint_urls_and_shared_pixel_base(tmp_path, monkeypatch):
    c = campaign(tmp_path); work = Path(c['work']); engine = work / 'engine'; engine.mkdir()
    (work / 'shards/000').mkdir(parents=True)
    write_json(work / 'shards/000/media.json', {'figures': [], 'complete': True})
    write_json(work / 'shards.json', {'shards': [{'articles': ['PMC1.1'],
        'sample': str(work / 'shards/000/sample.jsonl.gz'), 'media': str(work / 'shards/000/media.json')}]})
    monkeypatch.setenv('CUDA_VISIBLE_DEVICES', '0')
    monkeypatch.setattr(fc, 'verify_cpu', lambda c: None)
    monkeypatch.setattr('openpatients2.hipergator.read_campaign', lambda w: {'work': str(engine)})
    monkeypatch.setattr(fc, 'verify_checkpoint', lambda e: ({}, engine / 'active-model'))
    monkeypatch.setattr(fc, 'worker_serving_config', lambda *a: {})
    monkeypatch.setattr(fc, 'probe_worker', lambda *a: None)
    events = []
    class Group:
        def __init__(self, *args): pass
        async def start(self, **kwargs): return [{'endpoint': 'http://127.0.0.1:8600/v1'}]
        def stop(self): events.append('stopped')
    @asynccontextmanager
    async def monitor(*args): yield
    async def warmup(*args): return {'controlled_prefix_reset': True}
    async def extract(config, sample, output, endpoints, context, arm, **kwargs):
        events.append((kwargs['seed'], config['experimental_pipeline'], config['ledger_audit']))
        assert endpoints == ['http://127.0.0.1:8600/v1']
        assert kwargs['image_manifest']['output_dir'] == str(work)
        assert kwargs.get('frozen_rosters') is None
        return {'outputs': {}, 'wall_seconds': 1}
    monkeypatch.setattr('openpatients2.serving.ServerGroup', Group)
    monkeypatch.setattr('openpatients2.gpu_telemetry.monitor', monitor)
    monkeypatch.setattr('openpatients2.corpus_pilot.trial_warmup', warmup)
    monkeypatch.setattr('openpatients2.pilot_extract.run_pilot', extract)
    result = await fc.run_worker(c, 0)
    assert result['status'] == 'completed' and len(result['trials']) == 6
    assert events[-1] == 'stopped'
    assert set(events[:-1]) == {(s, mode, audit) for s in (42, 43)
        for mode, audit in [('original', False), ('evidence_ledger', False), ('evidence_ledger', True)]}
    assert result['checkpoint_downloads'] == 0


def test_cleanup_refuses_queued_or_unaccounted_worker_even_with_terminal_receipts(tmp_path, monkeypatch):
    c = campaign(tmp_path); work = Path(c['work'])
    write_json(work / 'frontier-jobs.json', {'jobs': [{'phase': 'worker', 'id': str(i)} for i in range(1, 5)]})
    monkeypatch.setattr(fc.subprocess, 'run', lambda *a, **k: SimpleNamespace(stdout='1|COMPLETED\n2|FAILED\n3|CANCELLED by 42\n4|PENDING\n'))
    with pytest.raises(ValueError, match='terminal'): fc.require_workers_terminal(c)
    monkeypatch.setattr(fc.subprocess, 'run', lambda *a, **k: SimpleNamespace(stdout='1|COMPLETED\n2|FAILED\n3|TIMEOUT\n'))
    with pytest.raises(ValueError, match='terminal'): fc.require_workers_terminal(c)
    monkeypatch.setattr(fc.subprocess, 'run', lambda *a, **k: SimpleNamespace(stdout='1|COMPLETED\n2|FAILED\n3|TIMEOUT\n4|CANCELLED by 42\n1.batch|FAILED\n'))
    assert fc.require_workers_terminal(c) == {'1': 'COMPLETED', '2': 'FAILED', '3': 'TIMEOUT', '4': 'CANCELLED'}


def test_failed_workers_still_reach_owned_model_and_source_cleanup(tmp_path, monkeypatch):
    c = campaign(tmp_path); work = Path(c['work']); events = []
    engine = {'work': str(work / 'engine'), 'config': {'models': [{'name': 'glimmer-fp8'}]}}
    engine['config_sha256'] = json_digest(engine['config'])
    write_json(work / 'engine/campaign.json', engine)
    cache = work / 'shards/000/runtime-cache/triton'; cache.mkdir(parents=True)
    (cache / 'compiled.bin').write_text('cache')
    kept = work / 'shards/000/servers/deployment.json'; write_json(kept, {'deployment': 'review'})
    outside = tmp_path / 'outside'; outside.mkdir(); (outside / 'preserved').write_text('private')
    (cache / 'external').symlink_to(outside, target_is_directory=True)
    monkeypatch.setattr(fc, 'require_workers_terminal', lambda c: {'1': 'FAILED', '2': 'TIMEOUT', '3': 'FAILED', '4': 'FAILED'})
    def model_cleanup(*args):
        events.append('model'); return {'status': 'deleted'}
    def summarize(c):
        events.append('aggregate'); write_json(work / 'summary.json', {'status': 'failed'})
    def source_cleanup(c):
        events.append('sources'); return {'status': 'deleted'}
    monkeypatch.setattr('openpatients2.hipergator.cleanup', model_cleanup)
    monkeypatch.setattr(fc, 'aggregate', summarize)
    monkeypatch.setattr('openpatients2.pilot_cleanup.cleanup_sources', source_cleanup)
    assert fc.cleanup(c)['status'] == 'deleted'
    assert events == ['model', 'aggregate', 'sources']
    assert not cache.parent.exists() and kept.is_file() and (outside / 'preserved').read_text() == 'private'


def test_aggregate_scores_global_gold_once_per_pair_and_marks_missing_shards(tmp_path, monkeypatch):
    c = campaign(tmp_path); work = Path(c['work']); rows = []
    # Only one shard delivered; missing shards still contribute the full gold.
    for seed in (42, 43):
        for name in fc.VARIANTS:
            trial = work / 'shards/000/trials' / f'seed{seed}' / name
            trial.mkdir(parents=True)
            patient = {'source': {'record_id': 'PMC1.1:p1'}}
            (trial / 'patients.jsonl').write_text(json.dumps(patient) + '\n')
            write_json(trial / 'rosters.json', {'articles': [{'article_id': 'PMC1.1'}]})
            write_json(trial / 'visual-annotations.json', [])
            rows.append({'shard': 0, 'seed': seed, 'variant': name, 'status': 'completed', 'report': {
                'outputs': {'patients': str(trial / 'patients.jsonl'),
                            'predicted_rosters': str(trial / 'rosters.json'),
                            'visual_annotations': str(trial / 'visual-annotations.json')},
                'wall_seconds': 10, 'tokens': {'output_tokens': 100, 'reported_output_tokens': 100,
                                             'unknown_output_usage_calls': 0}}})
    write_json(work / 'shards/000/trials.json', rows)
    reference = fc.read_json(c['config']['fidelity_reference']); calls = []
    def score(gold, patients, **kwargs):
        calls.append((gold, patients, kwargs))
        assert gold == reference and len(gold['articles']) == 31
        assert len(patients) == 1
        return {'score': .1, 'clinical': {'matched': 1, 'required': len(gold['checks'])}}
    monkeypatch.setattr('openpatients2.bundle_quality.score_bundle', score)
    result = fc.aggregate(c)
    assert len(calls) == 6 and result['planned_trials'] == 24
    assert result['status'] == 'partial' and not result['paired_comparison_available']
    assert all(len(r['missing_shards']) == 3 and r['completion_tokens'] == 100 for r in result['results'])
    assert all(r['output_tokens_per_gpu_second'] == 10 for r in result['results'])
    assert len(result['paired_comparisons']) == 4
    assert all(set(pair['splits']) == {'train', 'validation', 'test'} and not pair['paired_comparison_available']
               for pair in result['paired_comparisons'])
    assert (work / 'SUMMARY.md').is_file()
    # Rebuilding after an interrupted CPU aggregate is safe and does not append.
    assert fc.aggregate(c)['received_trials'] == 6


def test_empty_failed_campaign_retains_all_gold_denominators(tmp_path):
    c = campaign(tmp_path)
    result = fc.aggregate(c)
    reference = fc.read_json(c['config']['fidelity_reference'])
    required = sum(r['kind'] == 'required' for r in reference['checks'])
    assert result['status'] == 'failed' and not result['paired_comparison_available']
    assert len(result['results']) == 6
    assert all(r['bundle_quality']['clinical']['required'] == required for r in result['results'])
    assert all(r['bundle_quality']['clinical']['matched'] == 0 for r in result['results'])


def test_export_keeps_ledger_audits_and_review_sources_and_excludes_model_and_caches(tmp_path):
    import tarfile
    c = campaign(tmp_path); work = Path(c['work'])
    kept = ['benchmark/articles.jsonl.gz', 'shards/000/trials/seed42/ledger-audit/source-ledgers/map.json',
            'shards/000/trials/seed42/ledger-audit/episode-ledgers/patient.json',
            'shards/000/trials/seed42/ledger-audit/tasks/clinical.json',
            'shards/000/trials/seed42/ledger-audit/patients.jsonl', 'vision-assets/review.image', 'logs/gpu.log']
    omitted = ['shards/000/runtime-cache/model.json', 'engine/active-model/weights/config.json',
               'engine/vllm.sif', 'client-venv/install.json', 'uv-cache/package.json',
               'shards/000/sample.jsonl.gz', 'shards/000/trials/seed42/ledger-audit/tasks/model.safetensors',
               'shards/000/trials/seed42/ledger-audit/tasks/.venv/runtime.json']
    for name in kept + omitted:
        path = work / name; path.parent.mkdir(parents=True, exist_ok=True); path.write_text('review')
    result = fc.export_results(c, tmp_path / 'results.tar.gz')
    with tarfile.open(result['output']) as archive:
        names = set(archive.getnames())
    assert set(kept) <= names and not set(omitted) & names
    assert result['uncompressed_bytes'] < 8_000_000_000
    with pytest.raises(ValueError, match='new archive'): fc.export_results(c, tmp_path / 'results.tar.gz')


def test_export_refuses_nested_symlinks_before_creating_any_archive(tmp_path):
    c = campaign(tmp_path); work = Path(c['work'])
    path = work / 'shards/000/trials/seed42/ledger-delta/source-ledgers'; path.mkdir(parents=True)
    outside = tmp_path / 'outside.json'; outside.write_text('private')
    (path / 'bad.json').symlink_to(outside)
    output = tmp_path / 'results.tar.gz'
    with pytest.raises(ValueError, match='escaped'): fc.export_results(c, output)
    assert not output.exists() and outside.read_text() == 'private'


def test_export_size_cap_is_checked_before_creating_archive(tmp_path, monkeypatch):
    c = campaign(tmp_path); work = Path(c['work'])
    write_json(work / 'summary.json', {'report': 'retained'})
    monkeypatch.setattr(fc, 'EXPORT_MAX_BYTES', 1)
    output = tmp_path / 'results.tar.gz'
    with pytest.raises(ValueError, match='8 GB'): fc.export_results(c, output)
    assert not output.exists()


@pytest.mark.parametrize('phase', ['prepare', 'setup', 'download', 'aggregate', 'cleanup'])
def test_cpu_stage_refuses_any_gpu_allocation(tmp_path, monkeypatch, phase):
    c = campaign(tmp_path)
    monkeypatch.setenv('SLURM_JOB_GPUS', '0')
    with pytest.raises(RuntimeError, match='CPU-only'): fc.stage(c, phase)
