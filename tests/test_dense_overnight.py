import copy
import gzip
import json
from pathlib import Path
import tarfile

import pytest

from openpatients2 import dense_benchmark as b, dense_overnight as o
from openpatients2.overnight_repairs import canonical_figure_identity, malformed_excerpt
from openpatients2.pilot_extract import PilotRunner
from openpatients2.client import Completion
from openpatients2.figure_visuals import validate_visuals
from openpatients2.targeted_repair import repair_snapshot, changed_accepted_atoms
from test_pilot_extract import article, roster, answer, setup_mock
from test_dense_benchmark import ROOT, finish


def prepared(tmp_path, monkeypatch):
    root = tmp_path / 'repo'
    for folder in ('src', 'scripts', 'configs/hipergator/dense-benchmark', 'docs'):
        (root / folder).mkdir(parents=True)
    for name in ('pyproject.toml', 'uv.lock', 'README.md', 'LICENSE'):
        (root / name).write_text('fixture')
    for name in ('DENSE260_HIPERGATOR_BENCHMARK.md', 'DENSE_OVERNIGHT.md'):
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
    with gzip.open(root / 'configs/hipergator/dense-benchmark/reference.jsonl.gz', 'wt') as f:
        f.write(''.join(json.dumps(r) + '\n' for r in refs))
    seen = []
    def media(source, folder, out, download=False):
        seen.append(source['article_id'])
        return {}
    monkeypatch.setattr(b, 'freeze_media', media)
    work = tmp_path / 'run'
    o.prepare(root, work, expected=3)
    assert seen == ['PMC1.1', 'PMC2.1']
    return work, refs[:2]


def test_prepare_excludes_test_and_changes_only_opt_in_flag(tmp_path, monkeypatch):
    work, refs = prepared(tmp_path, monkeypatch)
    m = b.check_snapshot(work)
    assert m['article_count'] == 2 and m['splits'] == {'train': 1, 'validation': 1}
    assert not (work / 'inputs/PMC3.1').exists()
    control, repairs = [b.check_snapshot(work / 'arms' / n) for n in o.ARMS]
    assert control['extraction'].pop('overnight_repairs') is False
    assert repairs['extraction'].pop('overnight_repairs') is True
    assert control['extraction'] == repairs['extraction']
    assert b.rows(work / 'reference/unified.jsonl').keys() == {'PMC1.1', 'PMC2.1'}
    assert m['overnight']['hours'] == 8
    assert not list(tmp_path.glob('*.inputs.tar.gz'))
    plan = b.submit(work, 'a', 'q', 8, dry_run=True, script_name='dense_overnight.sbatch')
    gpu = [c for c in plan['commands_without_dependencies'] if '--gres=gpu:b200:1' in c]
    cpu = [c for c in plan['commands_without_dependencies'] if '--gres=gpu:b200:1' not in c]
    assert all('--time=08:00:00' in c and c[-4].endswith('dense_overnight.sbatch') for c in gpu)
    assert len(cpu) == 2 and all('--partition=hpg-default' in c for c in cpu)


def test_paired_queue_locks_counterbalance_and_resume(tmp_path, monkeypatch):
    work, refs = prepared(tmp_path, monkeypatch)
    arms = [work / 'arms' / n for n in o.ARMS]
    a = b.paired_jobs(work, arms, 0)
    arm1, item1 = next(a)
    c = b.paired_jobs(work, arms, 1)
    arm2, item2 = next(c)
    assert item1['article_id'] != item2['article_id']
    assert item2['shard'] == 1
    next_arm, next_item = next(a)
    assert next_item['article_id'] == item1['article_id'] and next_arm != arm1
    a.close(); c.close()
    # Completed first arm is immutable; resume dispatches only its partner.
    reference = next(r for r in refs if r['article_id'] == item1['article_id'])
    finish(arm1, reference)
    jobs = list(b.paired_jobs(work, arms, 3))
    assert len(jobs) == 3
    assert (arm1, item1['article_id']) not in [(arm, item['article_id']) for arm, item in jobs]


def test_partial_scoring_complete_verification_export_and_tamper(tmp_path, monkeypatch):
    work, refs = prepared(tmp_path, monkeypatch)
    for name in o.ARMS:
        for ref in refs:
            if name == 'repairs' and ref['split'] == 'validation':
                continue
            path = finish(work / 'arms' / name, ref)
            r = b.read(path / 'receipt.json'); r['patients_extracted'] = len(ref['patients'])
            b.write(path / 'receipt.json', r)
    result = o.aggregate(work)
    assert not result['complete'] and result['completed_pairs'] == 1
    assert result['promotion']['recommendation'] == 'inconclusive'
    assert result['metrics']['validation']['repairs']['clinical_recall'] == 0
    with pytest.raises(ValueError, match='lack a completed attempt'):
        o.verify(work)
    path = finish(work / 'arms/repairs', refs[1], prediction=False)
    r = b.read(path / 'receipt.json'); r['patients_extracted'] = 0; b.write(path / 'receipt.json', r)
    result = o.aggregate(work)
    assert result['complete'] and result['completed_pairs'] == 2
    assert result['promotion']['recommendation'] == 'retain_control_and_review'
    assert o.verify(work)['verified']
    o.aggregate(work)  # integrity does not hash its own previous version
    assert o.verify(work)['verified']
    (work / 'runtime').mkdir(exist_ok=True)
    (work / 'runtime/secret-weight').write_text('not exported')
    output = o.export(work)
    with tarfile.open(output['archive']) as tf:
        names = tf.getnames()
        assert any('/arms/repairs/summary/' in n for n in names)
        assert not any('/runtime/' in n for n in names)
        dest = tmp_path / 'download'; tf.extractall(dest, filter='data')
    assert o.verify(dest / work.name)['verified']
    (work / 'summary/comparison.json').write_text('{}')
    with pytest.raises(ValueError, match='Scored campaign file changed'):
        o.verify(work)


def test_validation_gate_handles_tradeoffs_and_missing_values():
    control = dict(clinical_f1=.7, clinical_precision=.86, attribute_accuracy=.83, roster_recall=.9)
    good = {**control, 'clinical_f1': .706}
    assert o.promotion(control, good, True)['recommendation'] == 'repairs'
    for key in ('clinical_precision', 'attribute_accuracy', 'roster_recall'):
        assert o.promotion(control, {**good, key: control[key]-.02}, True)['recommendation'] != 'repairs'
    assert o.promotion(control, {**good, 'clinical_f1': None}, True)['recommendation'] != 'repairs'
    assert o.promotion(control, good, False)['recommendation'] == 'inconclusive'


def test_figure_normalization_does_not_accept_other_article_or_bad_evidence():
    a = article(); ident = {'article_id': a['article_id'], 'figure_id': 'F1'}
    raw = answer('figure_visuals', a); raw['figure_id'] = 'PMC1.1-F1'
    fixed, audit = canonical_figure_identity(raw, ident)
    assert audit and validate_visuals(fixed, a, 'F1', refined=True)
    assert raw['figure_id'] == 'PMC1.1-F1'
    for wrong in ('PMC2.1-F1', 'PMC1.1-F2', 'prefix-F1'):
        value, audit = canonical_figure_identity({**raw, 'figure_id': wrong}, ident)
        assert not audit
        with pytest.raises(ValueError, match='another figure'):
            validate_visuals(value, a, 'F1')
    fixed['panels'][0]['caption_claims'] = [{'text': 'Invented', 'evidence': [{'segment_id': 'fake', 'quote': 'Invented'}]}]
    with pytest.raises(ValueError):
        validate_visuals(fixed, a, 'F1', refined=True)


async def test_malformed_summary_retry_gets_bounded_raw_excerpt(tmp_path, monkeypatch):
    a = article(); calls = []
    def response(task, messages):
        calls.append(messages)
        if len(calls) == 1:
            return Completion(content='{"claims":[BROKEN', finish_reason='stop', prompt_tokens=10, completion_tokens=20)
        return answer(task, a)
    http, _ = setup_mock(monkeypatch, a, responses=response)
    runner = PilotRunner({'overnight_repairs': True, 'max_output_tokens': 256, 'max_retry_tokens': 256},
                         tmp_path / 'runner', ['http://localhost:8000/v1'], 8192, 'targeted', http=http)
    try:
        result = await runner.call('summary', [{'role': 'user', 'content': 'source'}], lambda x: x,
                                   {'article_id': a['article_id']}, 0, a['segments'])
        assert result['status'] == 'valid' and len(result['attempts']) == 2
        repair = calls[-1][-1]['content']
        assert 'BROKEN' in repair and 'JSON syntax diagnostic' in repair
        assert len(malformed_excerpt({'content': '[' + 'x'*100000})) < 6800
    finally:
        await runner.close(); await http.aclose()


async def test_missing_visual_inventory_never_calls_attribution(tmp_path, monkeypatch):
    a = article(); runner = PilotRunner({'overnight_repairs': True, 'pixel_strategy': 'staged'},
        tmp_path / 'runner', ['http://localhost:8000/v1'], 65536, 'targeted')
    called = []
    async def call(task, *args, **kwargs):
        called.append(task)
        return {'status': 'failed', 'data': None, 'attempts': [], 'errors': ['fixture']}
    monkeypatch.setattr(runner, 'call', call)
    monkeypatch.setattr(runner, 'pixels', lambda *a: [])
    row = {'article_id': a['article_id'], 'figure_id': 'F1', 'pixel_provenance': {}}
    try:
        result = await runner.visual(row, a, roster(a), 0)
        assert called == ['figure_visuals']
        assert result['status'] == 'incomplete'
        assert result['attribution']['blocked_by'] == 'figure_visuals'
        assert list((runner.output / 'tasks').glob('*.json'))
    finally:
        await runner.close()


def test_valid_series_neighbors_survive_repair():
    good = {'measurement_name': 'valid', 'points': [{'value_cell_id': 'C1'}]}
    bad = {'measurement_name': 'invalid', 'points': []}
    def checker(value):
        if any(not s['points'] for s in value['series']):
            raise ValueError('No points')
        return value
    protected = repair_snapshot('measurement_series_extraction', {'series': [good, bad]}, checker)
    assert protected['series'] == [good]
    assert changed_accepted_atoms('measurement_series_extraction', protected, {'series': []})
    assert not changed_accepted_atoms('measurement_series_extraction', protected, {'series': [good]})


@pytest.mark.parametrize('mode', ['done', 'timeout', 'budget'])
async def test_worker_budget_timeout_and_cpu_work_separation(tmp_path, monkeypatch, mode):
    import asyncio
    from contextlib import asynccontextmanager
    from types import SimpleNamespace
    import httpx
    from openpatients2 import dense_benchmark_serving, dense_benchmark_telemetry
    work, refs = prepared(tmp_path, monkeypatch)
    (work / 'runtime').mkdir(exist_ok=True)
    b.write(work / 'runtime.json', {'files': {}})
    monkeypatch.setenv('SLURM_JOB_ID', 'fixture')
    monkeypatch.setenv('CUDA_VISIBLE_DEVICES', '0')
    monkeypatch.setenv('OP2_SUBMISSION_ID', 'fixture')
    events = []
    config = SimpleNamespace(sif='fixture.sif', port=1234, gpus=1)
    monkeypatch.setattr(b, 'serving', lambda *args: config)
    class Socket:
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def bind(self, address): pass
        def getsockname(self): return ('127.0.0.1', 1234)
    monkeypatch.setattr(b.socket, 'socket', Socket)
    class Group:
        def __init__(self, cfg, *args):
            self.config = cfg
            self.commands = [{'argv': ['apptainer', 'fixture.sif']}]
            self.processes = [SimpleNamespace(poll=lambda: None)]
        async def start(self):
            events.append('start')
            return [{'endpoint': 'http://localhost:1234/v1'}]
        def stop(self): events.append('stop')
    monkeypatch.setattr(dense_benchmark_serving, 'ServerGroup', Group)
    @asynccontextmanager
    async def monitor(path): yield
    monkeypatch.setattr(dense_benchmark_telemetry, 'monitor', monitor)
    monkeypatch.setattr(b.subprocess, 'run', lambda *args, **kwargs: SimpleNamespace(
        stdout='probe', stderr='', returncode=0, check_returncode=lambda: None))
    class HTTP:
        def __init__(self, **kwargs): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        async def get(self, *args): return SimpleNamespace(text='metrics', raise_for_status=lambda: None)
    monkeypatch.setattr(httpx, 'AsyncClient', HTTP)
    # Scoring, preparation, archiving and setup must never be invoked by a GPU worker.
    def cpu_only(*args, **kwargs): raise AssertionError('CPU-only work ran on GPU worker')
    for name in ('aggregate', 'prepare', 'export', 'setup'):
        monkeypatch.setattr(b, name, cpu_only)
    async def run(campaign, item, endpoint, attempt):
        events.append(('article', campaign.name, item['article_id']))
        done = False
        try:
            if mode == 'timeout':
                await asyncio.sleep(.1)
            done = True
        finally:
            receipt = {'article_id': item['article_id'], 'status': 'done' if done else 'error',
                       'wall_seconds': .01, 'model_calls': 1}
            b.write(attempt / 'receipt.json', receipt)
        return receipt
    monkeypatch.setattr(b, 'run_article', run)
    result = await b.worker(work, 0, campaigns=[work / 'arms' / n for n in o.ARMS],
                            budget_seconds=0 if mode == 'budget' else 60,
                            article_timeout=.01 if mode == 'timeout' else 5)
    assert events.count('start') == events.count('stop') == 1
    assert result['status'] == {'done': 'done', 'timeout': 'errors', 'budget': 'budget_exhausted'}[mode]
    assert len(result['articles']) == (0 if mode == 'budget' else 4)
    assert not any(p.is_dir() for p in (work / 'runtime/article-claims/fixture').iterdir())
    if mode == 'timeout':
        assert not list(b.paired_jobs(work, [work / 'arms' / n for n in o.ARMS], 1))
    if mode == 'timeout':
        receipts = list((work / 'arms').glob('*/results/articles/*/attempt-*/receipt.json'))
        assert len(receipts) == 4
        assert all(b.read(p)['error_type'] == 'TimeoutError' for p in receipts)


async def test_ehr_series_actual_repair_is_opt_in_and_keeps_literal_values(tmp_path, monkeypatch):
    from openpatients2 import ehr_pipeline, ehr_tasks
    a = article(); a['figures'] = []
    a['segments'] = [
        {'segment_id': 'b1', 'kind': 'paragraph', 'text': 'Labs of the first patient.'},
        {'segment_id': 'b2', 'kind': 'table_row', 'table_id': 'T1',
         'text': 'T\nTest | Day 1 | Day 3\nCRP | 12.5 | 3.1'}]
    r = roster(a)
    point = lambda c, t: {'value_cell_id': c, 'time_cell_id': t, 'time_text': None,
        'unit_cell_id': None, 'unit_text': None, 'offset_value': None, 'offset_unit': None,
        'offset_anchor_text': None, 'offset_basis': None}
    good = {'series': [{'name_cell_id': 'b2-c0', 'measurement_name': 'CRP', 'measurement_kind': 'laboratory',
        'ownership': 'individual', 'patient_ids': ['p1'],
        'ownership_evidence': [{'segment_id': 'b1', 'cell_id': None, 'quote': 'first patient'}],
        'reference_range_cell_id': None, 'points': [point('b2-c1', 'b2-h1'), point('b2-c2', 'b2-h2')]}]}
    bad = copy.deepcopy(good); bad['series'][0]['ownership_evidence'][0]['cell_id'] = 'b2-c0'
    monkeypatch.setattr(ehr_pipeline, 'pilot_outputs', lambda path: ([], {a['article_id']: r}, []))
    monkeypatch.setattr(ehr_pipeline, 'unified_from_pipeline',
                        lambda article, **kwargs: {'article_id': a['article_id'], 'outputs': kwargs['outputs']})
    source = tmp_path / 'source.jsonl'; b.write_rows(source, [a])
    for enabled in (False, True):
        calls = []
        def response(task, messages):
            calls.append(task)
            return bad if len(calls) == 1 else good
        http, _ = setup_mock(monkeypatch, a, responses=response)
        output = tmp_path / str(enabled)
        try:
            report = await ehr_pipeline.run_ehr_stages({'measurement_series_extraction': True,
                'extraction_config': {'overnight_repairs': enabled, 'max_repair_rounds': 2,
                                     'max_output_tokens': 256, 'max_retry_tokens': 256}},
                source, tmp_path / 'ignored', output, ['http://localhost:8000/v1'], 8192, http=http)
        finally:
            await http.aclose()
        assert len(calls) == (2 if enabled else 1)
        assert bool(report['articles'][0]['errors']) is not enabled
        if enabled:
            row = next(iter(b.rows(output / 'data/unified.jsonl').values()))
            series = row['outputs']['measurement_series_extraction']['series'][0]
            assert [p['parsed']['magnitude'] for p in series['points']] == ['12.5', '3.1']


def test_resume_ignores_stale_claims_and_sizes_gpu_pool_to_pending_pairs(tmp_path, monkeypatch):
    work, refs = prepared(tmp_path, monkeypatch)
    arms = [work / 'arms' / n for n in o.ARMS]
    old = work / 'runtime/article-claims/old' / refs[0]['article_id']
    old.mkdir(parents=True)
    for arm in arms:
        finish(arm, refs[1])
    monkeypatch.setenv('OP2_SUBMISSION_ID', 'new')
    jobs = list(b.paired_jobs(work, arms, 0))
    assert len(jobs) == 2 and old.exists()
    assert {item['article_id'] for _, item in jobs} == {refs[0]['article_id']}
    b.write(work / 'runtime.json', {'ready': True})
    plan = b.submit(work, 'a', 'q', 8, resume=True, dry_run=True,
                    script_name='dense_overnight.sbatch', campaigns=arms)
    commands = plan['commands_without_dependencies']
    assert len(commands) == 2  # one worker plus CPU aggregation
    assert sum('--gres=gpu:b200:1' in c for c in commands) == 1
    namespaces = {arg for c in commands for arg in c if arg.startswith('--export=ALL,OP2_SUBMISSION_ID=')}
    assert len(namespaces) == 1
