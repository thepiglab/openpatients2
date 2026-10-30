"""Literal time projection is opt-in, audited, and never a value/unit repair."""
from copy import deepcopy
import json
from types import SimpleNamespace

import pytest

from openpatients2 import bounded_series, ehr_tasks


def fixtures():
    source = {'article_id': 'PMC1.1', 'text_sha256': 'authored-fixture', 'segments': [
        {'segment_id': 'intro', 'kind': 'paragraph', 'text': 'The woman had serial glucose tests.'},
        {'segment_id': 't1', 'kind': 'table_row', 'table_id': 'T1',
         'text': 'Analyte | Baseline (0 h) | At 6 h — follow-up\nGlucose (mmol/L) | 7.20 | 8.40'}]}
    annotation = {'article_id': 'PMC1.1', 'text_sha256': 'authored-fixture',
                  'patients': [{'patient_id': 'p1'}]}
    registry = {'patients': [{'patient_id': 'p1', 'label': 'woman',
                             'identity_evidence': [{'segment_id': 'intro', 'quote': 'The woman'}]}]}
    draft = ehr_tasks.series_draft(source)
    candidate = {'series': [{'name_cell_id': 't1-c0', 'measurement_name': 'Glucose',
        'measurement_kind': 'laboratory', 'ownership': 'individual', 'patient_ids': ['p1'],
        'ownership_evidence': [{'segment_id': 'intro', 'cell_id': None, 'quote': 'The woman'}],
        'reference_range_cell_id': None,
        'points': [{'value_cell_id': f't1-c{i}', 'time_cell_id': f't1-h{i}',
                    'time_text': text, 'unit_cell_id': 't1-c0', 'unit_text': 'mmol/L',
                    'offset_value': None, 'offset_unit': None, 'offset_anchor_text': None,
                    'offset_basis': None}
                   for i, text in [(1, 'at baseline'), (2, 'six hours later')]]}]}
    return source, registry, annotation, draft, candidate


async def run_fixture(tmp_path, monkeypatch, candidate, *, enabled):
    source, registry, annotation, draft, _ = fixtures()
    calls = []
    async def fits(*args):
        return True
    monkeypatch.setattr(bounded_series, 'fits', fits)

    class Runner:
        output = tmp_path
        config = SimpleNamespace(literal_series_time_cells=enabled)
        async def call(self, task, messages, checker, identity, replica, segments, **kwargs):
            # Run production check_series and validate_series through the actual
            # bounded-series checker; only the model transport is simulated.
            try:
                data = checker(candidate)
                status, errors = 'valid', []
            except ValueError as exc:
                data, status, errors = None, 'failed', [str(exc)]
            result = {'task_key': 'fixture-series-batch', 'identity': identity, 'status': status,
                      'data': data, 'errors': errors,
                      'attempts': [{'raw_candidate': deepcopy(candidate),
                                    'response': {'content': json.dumps(candidate)}}]}
            calls.append({'messages': messages, 'result': result})
            return result

    doc, audit = await bounded_series.extract(Runner(), source, registry, annotation, draft, {})
    return doc, audit, calls


async def test_enabled_projection_copies_exact_selected_headers_and_retains_original_candidate(tmp_path, monkeypatch):
    _, _, _, draft, candidate = fixtures()
    original = deepcopy(candidate)
    doc, audit, calls = await run_fixture(tmp_path, monkeypatch, candidate, enabled=True)
    assert candidate == original
    assert audit['all_batches_valid'] and audit['all_focus_cells_attempted']
    assert audit['semantic_coverage_verified'] is False
    points = doc['series'][0]['points']
    assert [p['time']['time_text'] for p in points] == ['Baseline (0 h)', 'At 6 h — follow-up']
    assert [p['time']['time_cell_id'] for p in points] == ['t1-h1', 't1-h2']
    assert all(p['time']['offset_value'] is None and p['time']['offset_unit'] is None for p in points)
    assert [p['parsed']['raw_text'] for p in points] == ['7.20', '8.40']
    assert [p['parsed']['magnitude'] for p in points] == ['7.20', '8.40']
    assert all((p['unit_text'], p['unit_cell_id']) == ('mmol/L', 't1-c0') for p in points)
    assert doc['cells'] == draft['cells']
    result = calls[0]['result']
    assert result['attempts'][0]['raw_candidate'] == original
    assert json.loads(result['attempts'][0]['response']['content']) == original
    assert result['literal_cell_projections'] == [
        {'time_cell_id': 't1-h1', 'original_time_text': 'at baseline',
         'literal_time_text': 'Baseline (0 h)', 'policy': 'selected-cell-literal-projection/1'},
        {'time_cell_id': 't1-h2', 'original_time_text': 'six hours later',
         'literal_time_text': 'At 6 h — follow-up', 'policy': 'selected-cell-literal-projection/1'}]
    saved = json.loads((tmp_path / 'tasks' / 'fixture-series-batch.json').read_text())
    assert saved['literal_cell_projections'] == result['literal_cell_projections']
    assert saved['attempts'][0]['raw_candidate'] == original
    assert 'copies that cell verbatim' in calls[0]['messages'][-1]['content']


async def test_disabled_projection_still_rejects_nonliteral_time_and_preserves_raw_output(tmp_path, monkeypatch):
    *_, candidate = fixtures()
    before = deepcopy(candidate)
    doc, audit, calls = await run_fixture(tmp_path, monkeypatch, candidate, enabled=False)
    assert candidate == before
    assert doc['series'] == [] and not audit['all_batches_valid']
    assert calls[0]['result']['status'] == 'failed'
    assert 'time_text must be literal' in calls[0]['result']['errors'][0]
    assert 'literal_cell_projections' not in calls[0]['result']
    assert 'copies that cell verbatim' not in calls[0]['messages'][-1]['content']


@pytest.mark.parametrize('time_text', ['six hours later', None])
@pytest.mark.parametrize('enabled', [False, True])
async def test_projection_never_fabricates_an_unknown_time_cell(tmp_path, monkeypatch, time_text, enabled):
    *_, candidate = fixtures()
    for point in candidate['series'][0]['points']:
        point.update(time_cell_id='foreign-cell', time_text=time_text)
    before = deepcopy(candidate)
    doc, audit, calls = await run_fixture(tmp_path, monkeypatch, candidate, enabled=enabled)
    assert candidate == before
    assert doc['series'] == [] and not audit['all_batches_valid']
    assert calls[0]['result']['status'] == 'failed'
    assert 'Unknown cell' in calls[0]['result']['errors'][0]
    assert 'literal_cell_projections' not in calls[0]['result']


async def test_projection_does_not_guess_time_when_no_cell_was_selected(tmp_path, monkeypatch):
    *_, candidate = fixtures()
    for point in candidate['series'][0]['points']:
        point.update(time_cell_id=None, time_text=None)
    doc, audit, calls = await run_fixture(tmp_path, monkeypatch, candidate, enabled=True)
    assert audit['all_batches_valid']
    assert all(p['time']['time_cell_id'] is None and p['time']['time_text'] is None
               for p in doc['series'][0]['points'])
    assert 'literal_cell_projections' not in calls[0]['result']


async def test_selected_known_headers_with_null_text_are_copied_but_no_offset_is_inferred(tmp_path, monkeypatch):
    *_, candidate = fixtures()
    for point in candidate['series'][0]['points']:
        point['time_text'] = None
    before = deepcopy(candidate)
    doc, audit, calls = await run_fixture(tmp_path, monkeypatch, candidate, enabled=True)
    assert candidate == before and audit['all_batches_valid']
    assert [p['time']['time_text'] for p in doc['series'][0]['points']] == ['Baseline (0 h)', 'At 6 h — follow-up']
    assert all(p['time']['offset_value'] is None for p in doc['series'][0]['points'])
    assert all(p['original_time_text'] is None for p in calls[0]['result']['literal_cell_projections'])


async def test_projection_does_not_repair_a_nonliteral_unit(tmp_path, monkeypatch):
    *_, candidate = fixtures()
    candidate['series'][0]['points'][0]['unit_text'] = 'mg/dL'
    before = deepcopy(candidate)
    doc, audit, calls = await run_fixture(tmp_path, monkeypatch, candidate, enabled=True)
    assert candidate == before and doc['series'] == [] and not audit['all_batches_valid']
    assert 'unit_text must be literal' in calls[0]['result']['errors'][0]
    assert all(p['original_time_text'] in {'at baseline', 'six hours later'}
               for p in calls[0]['result']['literal_cell_projections'])
