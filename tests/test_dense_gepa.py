import asyncio
import copy
import json
import time
from pathlib import Path

import pytest

from openpatients2 import dense_gepa as g, dense_benchmark as b
from openpatients2.scoring.clinical import _prf


def test_fbeta_miss_weight_and_empty_cases():
    assert g.fbeta(3, 2, 1, 2) == pytest.approx(15 / 21)
    assert g.fbeta(3, 1, 2, 2) < g.fbeta(3, 2, 1, 2)
    assert g.fbeta(0, 0, 5) == 0
    assert g.fbeta(0, 3, 0) == 0
    assert g.fbeta(0, 0, 0) == 1
    assert _prf(0, 0, 275)['f1'] == 0
    assert _prf(0, 5, 0)['f1'] == 0
    assert _prf(0, 0, 0)['f1'] is None
    for beta in (0, -1, float('nan'), float('inf')):
        with pytest.raises(ValueError): g.fbeta(1, 1, 1, beta)


def test_partitions_are_article_disjoint_and_test_excluded():
    rows = [{'article_id': str(i), 'split': 'train'} for i in range(158)]
    rows += [{'article_id': 'v'+str(i), 'split': 'validation'} for i in range(59)]
    rows += [{'article_id': 'test', 'split': 'test'}]
    split = g.partition(rows)
    assert split == g.partition(rows[::-1])
    assert [len(split[k]) for k in ('train', 'search_validation', 'confirmation')] == [126, 32, 59]
    assert not set(split['train']) & set(split['search_validation'])
    assert 'test' not in sum(split.values(), [])


def plan():
    return {'beta': 2., 'search_seed': 42, 'concurrent_articles': 2, 'hours': 2,
        'max_metric_calls': 12, 'confirmation_reserve_seconds': 3600,
        'confirmation_seeds': [42, 43], 'partition': {'train': ['a', 'b'],
        'search_validation': ['c', 'd'], 'confirmation': ['v']},
        'precision_floor': .8, 'max_precision_drop': .01, 'max_attribute_drop': .01,
        'minimum_fbeta_gain': .005, 'scope': 'conditional'}


def metric(value=.8):
    return {'fbeta_article_macro': value, 'fbeta_micro': value, 'precision': .9,
            'attribute_accuracy': .9, 'critical_attributes': {'assertion': .99}}


def test_precision_and_attribute_gates_cannot_be_bought_with_recall():
    control = metric(); candidate = metric(.9)
    assert g.promotion(control, candidate, plan())
    for change in ({'precision': .85}, {'attribute_accuracy': .85},
                   {'critical_attributes': {'assertion': .95}}, {'fbeta_micro': .79}):
        assert not g.promotion(control, candidate | change, plan())


def test_real_matching_empty_predictions_and_review_only():
    ref = next(iter(b.rows(Path(__file__).parent / 'fixtures/unified_scoring_reference.jsonl').values()))
    full = {p['patient_id']: p['sections'].get('observations', {}) for p in ref['patients']}
    good = g.measure(ref, full, 'observations', 2, ['valid'] * len(full))
    missing = g.measure(ref, {}, 'observations', 2, ['failed'] * len(full))
    assert good['fbeta'] == 1
    assert good['reward'] > 0
    assert missing['counts']['fn'] > 0 and missing['fbeta'] == missing['reward'] == 0
    assert any(x['missing_reference_items'] for x in missing['feedback'])
    bad = g.measure(ref, full, 'observations', 2, ['failed'] * len(full))
    assert bad['reward'] == 0


async def test_real_adapter_uses_prompt_json_and_never_target_sections(tmp_path, monkeypatch):
    from test_pilot_extract import article, roster, setup_mock, empty_section
    from openpatients2.article_tasks import patient_packet
    from openpatients2.evidence_recovery import packet_segments
    from openpatients2.pilot_extract import PilotRunner
    a = article(); roster_data = roster(a)
    packet = patient_packet(a, roster_data, roster_data['patients'][0])
    b.write(tmp_path / 'manifest.json', {'gepa': plan()})
    b.write_rows(tmp_path / 'reference/unified.jsonl', [{'article_id': a['article_id'], 'patients': [
        {'patient_id': 'p1', 'sections': {'observations': empty_section('observations')}}]}])
    b.write(tmp_path / 'inputs' / a['article_id'] / 'gepa-packets.json', [{'packet': packet, 'segments': packet_segments(packet)}])
    http, events = setup_mock(monkeypatch, a, server_max=65536)
    import openpatients2.pilot_extract as pilot
    monkeypatch.setattr(pilot, 'PilotRunner', lambda *args, **kwargs: PilotRunner(*args, **kwargs, http=http))
    async with http:
        adapter = g.Adapter(tmp_path, 'observations', 'http://localhost:8000/v1', asyncio.get_running_loop(), time.monotonic()+30)
        first = await adapter.one(a['article_id'], {'instructions': 'Preserve all explicit values.'})
        second = await adapter.one(a['article_id'], {'instructions': 'Preserve all explicit values.'})
    assert first is second and first['reward'] == 1
    assert sum(e[0] == 'complete' for e in events) == 1
    assert 'sections' not in packet['patient_target']
    assert 'Preserve all explicit values.' in str(events)


async def test_real_gepa_search_freezes_candidate_before_two_seed_confirmation(tmp_path, monkeypatch):
    b.write(tmp_path / 'manifest.json', {'gepa': plan(), 'files': {}})
    b.write_rows(tmp_path / 'reference/unified.jsonl', [])
    seen = []
    async def fake_one(self, aid, candidate):
        seen.append((self.task, self.phase, aid, self.seed))
        score = .9 if candidate['instructions'] else .5
        return {'article_id': aid, 'reward': score, 'fbeta': score, 'precision': .9,
            'recall': score, 'counts': {'tp': 9 if candidate['instructions'] else 5, 'fp': 1, 'fn': 1},
            'attributes': {'correct': 9, 'total': 10},
            'critical_attributes': {k: {'correct': 9, 'total': 10} for k in ('assertion','subject','value','unit')},
            'seconds': 1., 'feedback': [], 'errors': []}
    async def fake_reflect(self, candidate, dataset): return {'instructions': 'Retain documented observations.'}
    monkeypatch.setattr(g.Adapter, 'one', fake_one)
    monkeypatch.setattr(g.Adapter, 'reflect', fake_reflect)
    slots = asyncio.Semaphore(64)
    await asyncio.gather(*(g.optimize_task(tmp_path, i, 'http://localhost:8000/v1',
        tmp_path / 'logs' / task, slots) for i, task in enumerate(g.TASKS)))
    assert (tmp_path / 'results/gepa/observations/done.json').exists()
    assert all(aid != 'v' for _, phase, aid, _ in seen if phase == 'search')
    for task in g.TASKS:
        assert [seed for t, phase, aid, seed in seen if phase == 'confirmation' and t == task] == [42,42,43,43]
    result = g.aggregate(tmp_path)
    assert result['complete']
    assert g.verify(tmp_path)['verified']
    (tmp_path / 'results/gepa/conditions/done.json').unlink()
    g.aggregate(tmp_path)
    assert result['tasks']['observations']['recommendation'] == 'full_pipeline_confirmation'
    with pytest.raises(ValueError, match='Incomplete'): g.verify(tmp_path)
    adapter = g.Adapter(tmp_path, 'observations', 'http://localhost:8000/v1', asyncio.get_running_loop(), time.monotonic()+30)
    from types import SimpleNamespace
    with pytest.raises(ValueError, match='Only training'):
        adapter.make_reflective_dataset({}, SimpleNamespace(trajectories=[{'article_id':'v'}]), ['instructions'])


def test_cpu_gpu_submission_boundary(tmp_path):
    b.write(tmp_path / 'manifest.json', {'files': {}, 'articles': []})
    result = b.submit(tmp_path, 'account', 'qos', 8, dry_run=True,
                      script_name='dense_gepa.sbatch', worker_shards=range(1))
    commands = result['commands_without_dependencies']
    assert len(commands) == 3
    assert sum('--gres=gpu:b200:1' in c for c in commands) == 1
    assert all('--partition=hpg-default' in c for c in (commands[0], commands[-1]))


def test_launcher_blocks_duplicate_before_any_submission(tmp_path):
    import os
    import subprocess
    script = tmp_path / 'scripts/run_dense_gepa.sh'
    script.parent.mkdir()
    script.write_text((Path(__file__).resolve().parents[1] / 'scripts/run_dense_gepa.sh').read_text())
    previous = tmp_path / 'old'
    bootstrap = tmp_path / 'old.bootstrap'; bootstrap.mkdir()
    (bootstrap / 'job-id.txt').write_text('12345\n')
    (tmp_path / 'runs').mkdir()
    (tmp_path / 'runs/dense-gepa-last-work.txt').write_text(str(previous))
    binary = tmp_path / 'bin'; binary.mkdir()
    queue = binary / 'squeue'; queue.write_text('#!/bin/sh\necho 12345\n'); queue.chmod(0o755)
    result = subprocess.run(['bash', str(script)], text=True, capture_output=True,
        env={**os.environ, 'PATH': str(binary)+':'+os.environ['PATH']})
    assert result.returncode == 1 and 'still active' in result.stderr
    assert not (tmp_path / 'runs/dense-gepa-launch.lock').exists()


async def test_batch_failure_cancels_siblings_before_return():
    settled = asyncio.Event()
    async def pending():
        try: await asyncio.sleep(30)
        finally: settled.set()
    async def fail():
        await asyncio.sleep(0)
        raise RuntimeError('server failed')
    with pytest.raises(RuntimeError, match='server failed'):
        await g.gather_cancel([pending(), fail()])
    assert settled.is_set()
