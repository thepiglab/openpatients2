"""Mocked coverage/attribution gates for bounded clinical extraction.

These assert structural accounting and preservation, never clinical correctness
of the model's no-patient-fact or attribution judgments.
"""
from copy import deepcopy
import json

import pytest

from openpatients2 import bounded_clinical as bounded
from openpatients2.article_tasks import patient_packet
from openpatients2.client import Completion
from openpatients2.ehr_tasks import series_draft
from openpatients2.pilot_extract import PilotRunner
from openpatients2.schemas import TASK_MODELS
from test_pilot_extract import XML, article, empty_section, roster, setup_mock


def observation(sid='s1', quote='Glucose was 7.20 mmol/L.', *, value='7.20', number=7.2):
    return {'subject': 'index_patient', 'assertion': 'present', 'temporality': 'unknown',
        'time': {'text': None, 'relation': 'unknown', 'anchor': None, 'date_iso': None},
        'evidence': [{'quote': quote, 'source_section': sid}], 'result_absent_reason': None,
        'name': 'Glucose', 'kind': 'laboratory', 'status': 'resulted', 'text_value': value,
        'numeric_value': number, 'comparator': '=', 'unit': 'mmol/L', 'flag': 'unknown',
        'reference_range_text': None, 'specimen': None, 'method': None, 'body_site': None}


def section(items=(), task='observations'):
    value = empty_section(task)
    value.update(coverage='complete', limitations=[])
    if items:
        value.update(items=deepcopy(list(items)), documentation_status='documented')
    return value


def envelope(value=None, focus=('s1',), *, scopes=None, cells=()):
    value = section() if value is None else deepcopy(value)
    return {'extraction': value,
        'source_decisions': [{'segment_id': sid, 'disposition': 'no_patient_fact',
                             'reason': 'Authored mock review, not a verified absence.'} for sid in focus],
        'fact_scopes': [{'path': path, 'scope': scope, 'reason': 'Authored attribution fixture.'}
                       for path, scope in (scopes or {p: 'direct_patient' for p in bounded.fact_paths(value)}).items()],
        'cell_decisions': [{'cell_id': c['cell_id'], 'disposition': 'no_patient_fact',
                           'item_indices': [], 'reason': 'Authored review decision.'} for c in cells]}


def checker(task='observations'):
    return lambda value: TASK_MODELS[task].model_validate(value).model_dump()


def leaf(value, number='0', *, status='valid'):
    return {'status': status, 'identity': {'source_batch': number}, 'data': value,
            'errors': [] if status == 'valid' else ['incomplete_finish:length'],
            'attempts': [{'response': {'reasoning_text': 'Authored reasoning fixture.'}}]}


@pytest.mark.parametrize('mutation', ['missing', 'duplicate', 'unknown'])
def test_every_focus_segment_requires_exactly_one_known_decision(mutation):
    value = envelope(focus=('s1', 's2'))
    if mutation == 'missing':
        value['source_decisions'].pop()
    elif mutation == 'duplicate':
        value['source_decisions'][1] = deepcopy(value['source_decisions'][0])
    else:
        value['source_decisions'][1]['segment_id'] = 'not-in-the-window'
    with pytest.raises(ValueError, match='focus segment'):
        bounded.check_envelope(value, 'observations', checker(), {'s1', 's2'})


def test_extracted_segment_claim_requires_an_actual_fact_citation():
    value = envelope()
    value['source_decisions'][0]['disposition'] = 'extracted'
    with pytest.raises(ValueError, match='no citation'):
        bounded.check_envelope(value, 'observations', checker(), {'s1'})
    value = envelope(section([observation()]))
    value['source_decisions'][0]['disposition'] = 'extracted'
    assert bounded.check_envelope(value, 'observations', checker(), {'s1'}) == value


def test_context_only_fact_cannot_be_emitted_even_with_no_patient_fact_focus_decisions():
    value = envelope(section([observation('neighbor')]))
    with pytest.raises(ValueError, match='focus-support citation'):
        bounded.check_envelope(value, 'observations', checker(), {'s1'})
    value['extraction']['items'][0]['evidence'].append({'source_section': 's1', 'quote': 'Glucose was 7.20 mmol/L.'})
    checked = bounded.check_envelope(value, 'observations', checker(), {'s1'})
    assert checked['extraction']['items'][0]['evidence'] == value['extraction']['items'][0]['evidence']


@pytest.mark.parametrize('mutation', ['missing', 'duplicate', 'unknown'])
def test_every_fact_requires_exactly_one_attribution_scope(mutation):
    value = envelope(section([observation()]))
    if mutation == 'missing':
        value['fact_scopes'] = []
    elif mutation == 'duplicate':
        value['fact_scopes'] *= 2
    else:
        value['fact_scopes'][0]['path'] = '/items/99'
    with pytest.raises(ValueError, match='attribution scope'):
        bounded.check_envelope(value, 'observations', checker(), {'s1'})


@pytest.mark.parametrize('scope', ['universal_cohort', 'aggregate', 'study_protocol',
                                  'secondary_case', 'background', 'uncertain'])
def test_non_direct_facts_are_retained_verbatim_in_quarantine(scope):
    direct = observation()
    other = observation(value='<3.80', number=3.8, quote='Cohort glucose was <3.80 mmol/L.')
    other['comparator'] = '<'
    value = envelope(section([direct, other]), scopes={'/items/0': 'direct_patient', '/items/1': scope})
    before = deepcopy(value)
    accepted, quarantine = bounded.patient_only(value)
    assert value == before
    assert accepted['items'] == [direct]
    assert quarantine[0]['candidate'] == other and quarantine[0]['scope'] == scope
    assert quarantine[0]['clinical_entailment_verified'] is False
    only_excluded = envelope(section([other]), scopes={'/items/0': scope})
    accepted, _ = bounded.patient_only(only_excluded)
    assert accepted['items'] == [] and accepted['documentation_status'] == 'not_documented'


def test_merge_preserves_literal_notation_unknown_time_and_separate_source_visits():
    first = observation(value='7.20')
    second = observation('s2', value='7.20')
    inputs = [leaf(envelope(section([first]))), leaf(envelope(section([first, second]), ('s1', 's2')), '1')]
    before = deepcopy(inputs)
    data, quarantine, errors = bounded.merge('observations', inputs, checker())
    assert inputs == before and not quarantine and not errors
    assert data['items'] == [first, second]
    assert data['items'][0]['text_value'] == '7.20'
    assert data['items'][0]['time']['relation'] == 'unknown'


def test_failed_window_payload_cannot_be_promoted_and_remaining_facts_are_partial():
    supported = observation()
    failed_candidate = observation('s2', value='999', number=999)
    leaves = [leaf(envelope(section([supported]))),
              leaf(envelope(section([failed_candidate]), ('s2',)), '1', status='partial')]
    data, quarantine, errors = bounded.merge('observations', leaves, checker())
    assert data['items'] == [supported] and data['coverage'] == 'limited'
    assert errors == ['failed_source_window:1'] and not quarantine


def test_unresolved_source_cell_or_attribution_keeps_merge_incomplete():
    value = envelope(section([observation()]))
    value['source_decisions'][0]['disposition'] = 'unresolved'
    value['cell_decisions'] = [{'cell_id': 'r1-c1', 'disposition': 'unresolved',
                              'item_indices': [], 'reason': 'Patient linkage is ambiguous.'}]
    value['fact_scopes'][0]['scope'] = 'uncertain'
    data, quarantine, errors = bounded.merge('observations', [leaf(value)], checker())
    assert data['items'] == [] and data['coverage'] == 'limited' and len(quarantine) == 1
    assert set(errors) == {'unresolved_source_coverage:0', 'unresolved_cell_coverage:0',
                           'unresolved_patient_attribution:0'}


def test_scalar_case_context_conflict_is_rejected_instead_of_last_write_wins():
    value = section(task='case_context')
    value.update(documentation_status='documented', case_kind='clinical_case', species='human',
                 care_setting='hospital', evidence=[{'quote': 'A woman presented.', 'source_section': 's1'}])
    conflicting = deepcopy(value); conflicting['care_setting'] = 'home'
    data, _, errors = bounded.merge('case_context',
        [leaf(envelope(value)), leaf(envelope(conflicting), '1')], checker('case_context'))
    assert data is None and 'conflicting_window_field:care_setting' in errors
    unknown = deepcopy(value); unknown['care_setting'] = None
    data, _, errors = bounded.merge('case_context',
        [leaf(envelope(unknown)), leaf(envelope(value), '1')], checker('case_context'))
    assert data['care_setting'] == 'hospital' and not errors


CELL = {'cell_id': 's1-c1', 'segment_id': 's1', 'column_index': 1, 'raw_text': '7.20'}


def cell_envelope():
    value = envelope(section([observation()]), cells=[CELL])
    value['cell_decisions'][0].update(disposition='extracted', item_indices=[0])
    return value


@pytest.mark.parametrize('mutation', ['missing', 'duplicate', 'unknown'])
def test_value_cells_require_exactly_one_known_coverage_decision(mutation):
    value = cell_envelope()
    if mutation == 'missing':
        value['cell_decisions'] = []
    elif mutation == 'duplicate':
        value['cell_decisions'] *= 2
    else:
        value['cell_decisions'][0]['cell_id'] = 'foreign-cell'
    with pytest.raises(ValueError, match='focus value cell'):
        bounded.check_envelope(value, 'observations', checker(), {'s1'}, [CELL])


@pytest.mark.parametrize('indices', [[], [0, 0], [-1], [1]])
def test_extracted_cells_require_nonempty_unique_in_range_item_indices(indices):
    value = cell_envelope(); value['cell_decisions'][0]['item_indices'] = indices
    with pytest.raises(ValueError):
        bounded.check_envelope(value, 'observations', checker(), {'s1'}, [CELL])


@pytest.mark.parametrize('mutation', ['wrong_row', 'missing_literal', 'unextracted_item'])
def test_cell_claim_must_have_literal_value_evidence_in_its_source_row(mutation):
    value = cell_envelope()
    if mutation == 'wrong_row':
        value['extraction']['items'][0]['evidence'][0]['source_section'] = 's2'
    elif mutation == 'missing_literal':
        value['extraction']['items'][0]['evidence'][0]['quote'] = 'Glucose was normal.'
    else:
        value['cell_decisions'][0]['disposition'] = 'no_patient_fact'
    with pytest.raises(ValueError):
        bounded.check_envelope(value, 'observations', checker(), {'s1'}, [CELL])
    value = cell_envelope()
    assert bounded.check_envelope(value, 'observations', checker(), {'s1'}, [CELL]) == value


@pytest.mark.parametrize('raw,text,number,unit,comparator,matched', [
    ('7.20', '7.20', 7.2, 'mmol/L', '=', True),
    ('7.20 mmol/L', '7.20', 7.2, 'mmol/L', '=', True),
    ('7.20 mmol/L', '7.20 mmol/L', None, 'mmol/L', '=', True),
    ('7.20 mmol/L', '7.20 mmol/L', None, 'mg/dL', '=', False),
    ('7.20', '7.20 mmol/L', 7.2, 'mg/dL', '=', False),
    ('7.20 mmol/L', '7.20', 7.2, 'mg/dL', '=', False),
    ('7.20', '8.40', 7.2, 'mmol/L', '=', False),
    ('7.20', '7.20', 8.4, 'mmol/L', '=', False),
    ('<7.20', '<7.20', 7.2, 'mmol/L', '<', True),
    ('<7.20', '7.20', 7.2, 'mmol/L', '=', False),
    ('7.20', '7.20', 7.2, 'mmol/L', 'unknown', False),
    ('7.20e3', '7.20e3', 7200.0, None, '=', True),
    ('7.20 × 109/L', '7.20 × 109/L', None, None, '=', True),
    ('7.20 × 109/L', '7.20 × 109/L', 7.2e9, '/L', '=', False),
    ('negative', 'negative', None, None, 'unknown', True),
    ('negative', 'negative', 0.0, None, 'unknown', False),
    ('120/80', '120/80', None, None, 'unknown', True),
    ('120/80', '120', 120.0, 'mmHg', '=', False),
    ('—', '0', 0.0, 'mmol/L', '=', False),
])
def test_cell_correspondence_checks_result_fields_without_conversions_or_notation_guesses(raw, text, number, unit, comparator, matched):
    item = observation(quote='Glucose | ' + raw, value=text, number=number)
    item.update(unit=unit, comparator=comparator)
    cell = {**CELL, 'raw_text': raw}
    before = deepcopy(item)
    actual, _ = bounded.cell_item_correspondence(cell, item)
    assert actual is matched and item == before


def test_mismatched_cell_link_is_unresolved_with_raw_decision_and_values_retained():
    item = observation(quote='Glucose | 7.20 | 8.40', value='7.20', number=7.2)
    cell = {**CELL, 'raw_text': '8.40'}
    value = envelope(section([item]), cells=[cell])
    value['cell_decisions'][0].update(disposition='extracted', item_indices=[0])
    original = deepcopy(value)
    checked = bounded.check_envelope(value, 'observations', checker(), {'s1'}, [cell])
    assert value == original and checked['extraction'] == original['extraction']
    assert checked['cell_decisions'][0]['disposition'] == 'unresolved'
    assert checked['cell_decisions'][0]['item_indices'] == []
    audit = checked['cell_linkage_audit'][0]
    assert audit['cell'] == cell and audit['original_decision'] == original['cell_decisions'][0]
    assert audit['measurement_fields_changed'] is False
    data, _, errors = bounded.merge('observations', [leaf(checked)], checker())
    assert data['items'] == [item] and data['coverage'] == 'limited'
    assert errors == ['unresolved_cell_coverage:0']


def test_one_item_cannot_claim_two_time_columns_even_when_values_are_equal():
    cells = [CELL, {**CELL, 'cell_id': 's1-c2', 'column_index': 2}]
    value = envelope(section([observation(quote='Glucose | 7.20 | 7.20')]), cells=cells)
    for decision in value['cell_decisions']:
        decision.update(disposition='extracted', item_indices=[0])
    checked = bounded.check_envelope(value, 'observations', checker(), {'s1'}, cells)
    assert all(d['disposition'] == 'unresolved' for d in checked['cell_decisions'])
    assert len(checked['cell_linkage_audit']) == 2
    assert all(row['issues'][0]['reason'] == 'one_item_claimed_for_multiple_value_cells'
               for row in checked['cell_linkage_audit'])


def test_distinct_cells_preserve_equal_observations_after_quarantine_changes_item_indices():
    cells = [CELL, {**CELL, 'cell_id': 's1-c2', 'column_index': 2}]
    repeated = observation(quote='Glucose | 7.20 | 7.20')
    foreign = observation(quote='Glucose | 7.20 | 7.20', value='99', number=99)
    value = envelope(section([foreign, repeated, repeated]), cells=cells,
        scopes={'/items/0': 'background', '/items/1': 'direct_patient', '/items/2': 'direct_patient'})
    for index, decision in enumerate(value['cell_decisions'], 1):
        decision.update(disposition='extracted', item_indices=[index])
    checked = bounded.check_envelope(value, 'observations', checker(), {'s1'}, cells)
    data, quarantine, errors = bounded.merge('observations', [leaf(checked)], checker())
    assert data['items'] == [repeated, repeated] and not errors
    assert len(quarantine) == 1 and quarantine[0]['candidate'] == foreign


def test_partition_keeps_every_whole_segment_once_and_bounds_table_batches():
    segments = [{'segment_id': f's{i}', 'kind': 'paragraph', 'text': str(i) * 20} for i in range(7)]
    segments += [{'segment_id': f'r{i}', 'kind': 'table_row', 'text': f'Analyte {i} | 7.20'} for i in range(11)]
    segments += [{'segment_id': 'oversized', 'kind': 'paragraph', 'text': 'x' * 20000}]
    before = deepcopy(segments)
    groups = bounded.partition(segments, max_chars=10000)
    assert [s for group in groups for s in group] == before and segments == before
    assert all(len(group) <= 4 for group in groups if group[0]['kind'] == 'table_row')
    assert groups[-1] == [segments[-1]] and len(groups[-1][0]['text']) == 20000


def table_article():
    rows = ''.join(f'<tr><td>Analyte {i}</td><td>{i}.20</td><td>{i}.40</td></tr>' for i in range(9))
    table = ('<table-wrap id="T1"><caption><p>Values for the patient.</p></caption><table>'
        '<thead><tr><th>Analyte (mmol/L)</th><th>Day 1</th><th>Day 2</th></tr></thead>'
        '<tbody>' + rows + '</tbody></table><table-wrap-foot><fn><p>Serum values in mmol/L.</p>'
        '</fn></table-wrap-foot></table-wrap>')
    return article(XML.replace('</body>', table + '</body>'))


def prompt_list(messages, marker, following):
    text = messages[-1]['content']
    return json.loads(text.split(marker, 1)[1].split(following, 1)[0])


@pytest.mark.parametrize('fail_one_window', [False, True])
async def test_observation_windows_cover_every_row_and_value_and_keep_headers(tmp_path, monkeypatch, fail_one_window):
    source = table_article(); registry = roster(source); patient = registry['patients'][0]
    packet = patient_packet(source, registry, patient)
    calls = []

    async def fits(*args):
        return True
    monkeypatch.setattr(bounded, 'fits', fits)
    failed_sid = next(s['segment_id'] for s in source['segments'] if s['kind'] == 'table_row')

    class Runner:
        output = tmp_path; arm = 'targeted'
        def __init__(self):
            self.schemas = {}; self.results = []
        def clinical_messages(self, task, packet):
            return [{'role': 'user', 'content': packet['text'] + '\nReturn only JSON matching SCHEMA:{}'}]
        def clinical_checker(self, task, packet, segments):
            return checker(task)
        async def call(self, task, messages, check, identity, replica, segments, **kwargs):
            focus = prompt_list(messages, '\nFOCUS_SEGMENTS:\n', '\nFOCUS_VALUE_CELLS:')
            cells = prompt_list(messages, '\nFOCUS_VALUE_CELLS:\n', '\nSCHEMA:')
            calls.append({'identity': identity, 'cells': cells, 'segments': segments, 'messages': messages})
            failed = fail_one_window and failed_sid in focus
            value = envelope(focus=focus, cells=cells)
            return {'identity': identity, 'status': 'failed' if failed else 'valid',
                'data': None if failed else check(value), 'errors': ['transport_failure'] if failed else [],
                'attempts': [{'response': {'reasoning_text': 'Saved mock reasoning.'}}]}

    runner = Runner()
    result = await bounded.extract(runner, 'observations', packet, source, patient,
        {'article_id': source['article_id'], 'record_id': 'PMC1.1:p1'}, 0)
    focus = [sid for c in calls for sid in c['identity']['focus_segment_ids']]
    assert len(focus) == len(set(focus)) == len(source['segments'])
    assert set(focus) == {s['segment_id'] for s in source['segments']}
    cells = [c['cell_id'] for call in calls for c in call['cells']]
    expected_cells = [c['cell_id'] for c in series_draft(source)['cells'] if c['role'] == 'value']
    assert len(cells) == len(set(cells)) == 18 and set(cells) == set(expected_cells)
    headers = {s['segment_id'] for s in source['segments'] if s['kind'] in {'table_header', 'table_footnote'}}
    table_calls = [c for c in calls if c['cells']]
    assert len(table_calls) == 3
    assert all(headers <= set(c['identity']['included_segment_ids']) for c in table_calls)
    assert all('Day 1' in c['messages'][-1]['content'] and 'Day 2' in c['messages'][-1]['content'] for c in table_calls)
    assert result['coverage_audit']['all_segments_attempted'] is True
    assert result['coverage_audit']['all_windows_valid'] is not fail_one_window
    assert result['status'] == ('partial' if fail_one_window else 'valid')
    assert result['coverage_audit']['semantic_coverage_verified'] is False
    assert result['clinical_accuracy_verified'] is False
    assert len(result['attempts']) == len(calls)
    assert all(a['response']['reasoning_text'] for a in result['attempts'])
    assert (tmp_path / 'coverage' / 'PMC1.1-p1-observations.json').is_file()


async def test_dynamic_envelope_schema_reaches_real_api_client_and_attempt_ledger(tmp_path, monkeypatch):
    source = article(); registry = roster(source); patient = registry['patients'][0]
    packet = patient_packet(source, registry, patient)

    def reply(task, messages):
        assert task == 'clinical_window_observations'
        schema = json.loads(messages[-1]['content'].split('\nSCHEMA:\n', 1)[1])
        assert set(schema['required']) == {'extraction', 'source_decisions', 'fact_scopes', 'cell_decisions'}
        focus = prompt_list(messages, '\nFOCUS_SEGMENTS:\n', '\nFOCUS_VALUE_CELLS:')
        return envelope(focus=focus)

    http, events = setup_mock(monkeypatch, source, responses=reply)
    async with http:
        runner = PilotRunner({'max_output_tokens': 256, 'max_retry_tokens': 256, 'max_repair_rounds': 0},
            tmp_path / 'run', ['http://localhost:8000/v1'], 4096, 'targeted', http=http)
        try:
            assert 'clinical_window_observations' not in runner.clients[0].schema_overrides
            result = await bounded.extract(runner, 'observations', packet, source, patient,
                {'article_id': source['article_id'], 'record_id': 'PMC1.1:p1'}, 0)
            assert 'clinical_window_observations' in runner.clients[0].schema_overrides
        finally:
            await runner.close()
    assert result['status'] == 'valid' and any(e[0] == 'complete' for e in events)
    attempts = [json.loads(line) for line in (tmp_path / 'run' / 'attempts.jsonl').read_text().splitlines()]
    assert len(attempts) == 1
    assert attempts[0]['response']['content'] and attempts[0]['task'] == 'clinical_window_observations'
    assert attempts[0]['metrics']['prompt_tokens'] == 10
    assert attempts[0]['metrics']['completion_tokens'] == 20


async def test_length_failure_splits_whole_source_and_keeps_failed_parent_attempt(tmp_path, monkeypatch):
    source = article(); registry = roster(source); patient = registry['patients'][0]
    packet = patient_packet(source, registry, patient)
    seen = []
    def reply(task, messages):
        focus = prompt_list(messages, '\nFOCUS_SEGMENTS:\n', '\nFOCUS_VALUE_CELLS:')
        seen.append(focus)
        if len(focus) > 1:
            return Completion(content='{"extraction":', finish_reason='length', prompt_tokens=10, completion_tokens=20)
        return envelope(focus=focus)
    http, _ = setup_mock(monkeypatch, source, responses=reply)
    async with http:
        runner = PilotRunner({'max_output_tokens': 256, 'max_retry_tokens': 256, 'max_repair_rounds': 1},
            tmp_path / 'run', ['http://localhost:8000/v1'], 4096, 'targeted', http=http)
        try:
            result = await bounded.extract(runner, 'observations', packet, source, patient,
                {'article_id': source['article_id'], 'record_id': 'PMC1.1:p1'}, 0)
        finally:
            await runner.close()
    assert result['status'] == 'valid' and len(seen) == 3
    assert sorted(sid for focus in seen[1:] for sid in focus) == sorted(seen[0])
    tasks = [json.loads(p.read_text()) for p in (tmp_path / 'run' / 'tasks').glob('*.json')]
    parent = next(row for row in tasks if row.get('superseded_by_split'))
    assert parent['attempts'][0]['response']['content'] == '{"extraction":'
    assert parent['errors'] == ['incomplete_finish:length']
    attempts = [json.loads(line) for line in (tmp_path / 'run' / 'attempts.jsonl').read_text().splitlines()]
    assert len(attempts) == 3 and sum(a['metrics']['completion_tokens'] for a in attempts) == 60
