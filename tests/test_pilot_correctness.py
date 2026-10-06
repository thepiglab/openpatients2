"""Fixed comparisons, independent discovery and literal timeline recovery."""
import copy
import json

import pytest

from openpatients2.longitudinal import PatientTimeline, audit_timeline
from openpatients2.patient_context import (check_patient_roster, discovery_messages,
                                          frozen_rosters, patient_view)
from openpatients2.pilot_extract import PilotRunner, prepare_rosters, sources_for, timeline_wire_schema
from openpatients2.schemas import TASK_MODELS
from openpatients2.targeted_repair import erased
from test_pilot_extract import article, answer, input_file, roster, run_pilot, setup_mock


def reviewed_file(tmp_path, value):
    path = tmp_path / 'rosters.json'
    path.write_text(json.dumps({'schema_version': 'fixed-rosters/1', 'articles': [{
        'article_id': value['article_id'], 'text_sha256': value['text_sha256'],
        'review_status': 'source_checked', 'roster': roster(value)}]}))
    return path


def test_discovery_ignores_bad_figure_decisions_without_weakening_identity():
    value = article(); predicted = roster(value)
    predicted['figures'] = [{'figure_id': 'fabricated', 'patient_ids': ['p99']}]
    checked = check_patient_roster(predicted, value)
    assert checked['patients'] == predicted['patients']
    assert checked['figures'][0]['scope'] == 'unresolved'
    assert predicted['figures'][0]['figure_id'] == 'fabricated'
    prompt = discovery_messages(value)[-1]['content']
    schema = json.loads(prompt.split('\nSCHEMA:\n')[1])
    assert 'figures' not in schema['properties']
    predicted['patients'][0]['identity_evidence'][0]['quote'] = 'fabricated person'
    with pytest.raises(ValueError):
        check_patient_roster(predicted, value)


@pytest.mark.parametrize('change', ['hash', 'duplicate', 'missing', 'review'])
def test_frozen_roster_rejects_changed_or_incomplete_source_binding(tmp_path, change):
    value = article(); path = reviewed_file(tmp_path, value)
    assert frozen_rosters(path, [value])[value['article_id']]['patients']
    document = json.loads(path.read_text())
    if change == 'hash': document['articles'][0]['text_sha256'] = '0' * 64
    if change == 'duplicate': document['articles'] *= 2
    if change == 'missing': document['articles'] = []
    if change == 'review': document['articles'][0]['review_status'] = 'unreviewed'
    path.write_text(json.dumps(document))
    with pytest.raises(ValueError):
        frozen_rosters(path, [value])


def test_compact_context_keeps_exact_mixed_tables_captions_and_unresolved_blocks():
    from test_articles import article as multi_article, roster as multi_roster
    value = multi_article(); predicted = multi_roster(value)
    predicted['patients'][0]['source_segment_ids'] = ['b00002']
    predicted['unresolved_segment_ids'] = ['b00005']  # mixed patient context must remain visible
    view, target, audit = patient_view(value, predicted, predicted['patients'][0])
    retained = {s['segment_id'] for s in view['segments']}
    assert {'b00002', 'b00003', 'b00004', 'b00005', 'b00006'} <= retained
    assert 'b00001' in retained  # shared context is visible, not attributed as individual facts
    assert 'b00001' in audit['shared_context_segment_ids']
    assert set(target['source_segment_ids']) == retained
    original = {s['segment_id']: s['text'] for s in value['segments']}
    assert all(s['text'] == original[s['segment_id']] for s in view['segments'])
    assert view['text_sha256'] == value['text_sha256']
    assert audit['semantic_coverage_verified'] is False


def test_whole_timeline_retry_cannot_hide_time_or_event_deletion_in_a_nonempty_list():
    before = {'events': [{'event_id':'e1','times':[{'text':'2 months','kind':'duration'}]},
                         {'event_id':'e2','times':[]}]}
    reordered = {'events':list(reversed(copy.deepcopy(before['events'])))}
    assert erased(before,reordered)==[]
    reordered['events'][1]['times']=[]
    assert erased(before,reordered)==['/events/0/times/0']
    assert erased(before,{'events':[before['events'][1]]})==['/events/0']


async def test_discovery_only_does_not_generate_clinical_or_pixel_tasks(tmp_path, monkeypatch):
    value = article(); http, events = setup_mock(monkeypatch, value, seed=43)
    async with http:
        report = await prepare_rosters({'max_output_tokens': 256, 'max_retry_tokens': 256},
            input_file(tmp_path, [value]), tmp_path / 'discovery', ['http://localhost:8000/v1'],
            8192, seed=43, http=http)
    assert [e[1] for e in events if e[0] == 'complete'] == ['roster']
    assert report['seed'] == 43 and report['patients_extracted'] == 0
    predicted = json.loads((tmp_path / 'discovery' / 'rosters.json').read_text())
    assert predicted['articles'][0]['roster']['patients'][0]['species'] == 'human'


async def test_frozen_comparison_has_equal_clinical_counts_and_no_discovery_calls(tmp_path, monkeypatch):
    value = article(); frozen = reviewed_file(tmp_path, value)
    http, events = setup_mock(monkeypatch, value, seed=44)
    path = input_file(tmp_path, [value]); reports = []
    async with http:
        for scope in ('whole_article', 'patient_sections'):
            reports.append(await run_pilot({}, path, tmp_path / scope, ['http://localhost:8000/v1'],
                8192, 'targeted', frozen_rosters=frozen, input_scope=scope, seed=44, http=http))
    tasks = [e[1] for e in events if e[0] == 'complete']
    assert 'roster' not in tasks
    assert reports[0]['task_count'] == reports[1]['task_count'] == len(TASK_MODELS) + 3
    assert all(r['conditioned_on_frozen_rosters'] and r['seed'] == 44 for r in reports)
    compact = json.loads((tmp_path / 'patient_sections' / 'patients.jsonl').read_text())
    assert compact['context_selection']['retained_segment_ids']
    assert compact['source']['article_source']['text_sha256'] == value['text_sha256']


def quote_only_timeline(value):
    span = {'segment_id': value['segments'][0]['segment_id'], 'quote': value['segments'][0]['text']}
    return {'schema_version': 'patient-timeline/2', 'record_id': 'PMC1.1:p1', 'events': [{
        'event_id': 'e1', 'record_id': 'PMC1.1:p1', 'episode_id': None, 'kind': 'treatment',
        'occurrence': 'occurred', 'description': 'Received a documented dose', 'fact_ids': [],
        'evidence': [span], 'attribution_evidence': [span], 'times': []}], 'edges': [], 'limitations': []}


@pytest.mark.parametrize('arm', ['direct', 'targeted'])
async def test_runner_derives_timeline_offsets_without_changing_clinical_content(tmp_path, monkeypatch, arm):
    value = article(); raw = quote_only_timeline(value)
    http, _ = setup_mock(monkeypatch, value, responses=lambda task, messages: raw)
    runner = PilotRunner({'max_output_tokens': 256, 'max_retry_tokens': 256}, tmp_path / arm,
        ['http://localhost:8000/v1'], 8192, arm, http=http)
    _, sources = sources_for(value)
    def checked(candidate):
        graph = PatientTimeline.model_validate(candidate)
        assert audit_timeline(graph, sources, {})['structural_source_gates_passed']
        return graph.model_dump()
    async with http:
        result = await runner.call('timeline_v2', [{'role': 'user', 'content': 'fixture'}], checked,
            {'article_id': value['article_id'], 'record_id': 'PMC1.1:p1'}, 0, value['segments'])
    await runner.close()
    assert result['status'] == 'valid' and len(result['attempts']) == 1
    attempt = result['attempts'][0]
    assert attempt['raw_candidate'] == raw
    assert attempt['span_resolution']['recoveries']
    event = result['data']['events'][0]
    assert event['description'] == raw['events'][0]['description']
    assert event['evidence'][0]['start'] == 0 and event['evidence'][0]['source_id'] == 'PMC1.1:jats'
    schema = timeline_wire_schema()['$defs']['SourceSpan']
    assert set(schema['properties']) == {'segment_id', 'quote'}


async def test_span_recovery_does_not_accept_a_wrong_patient(tmp_path, monkeypatch):
    value = article(); raw = quote_only_timeline(value)
    raw['events'][0]['record_id'] = 'PMC1.1:p2'
    http, _ = setup_mock(monkeypatch, value, responses=lambda task, messages: raw)
    runner = PilotRunner({'max_output_tokens': 256, 'max_retry_tokens': 256}, tmp_path / 'bad',
        ['http://localhost:8000/v1'], 8192, 'direct', http=http)
    async with http:
        result = await runner.call('timeline_v2', [{'role': 'user', 'content': 'fixture'}],
            lambda candidate: PatientTimeline.model_validate(candidate).model_dump(),
            {'article_id': value['article_id']}, 0, value['segments'])
    await runner.close()
    assert result['status'] == 'failed'
    assert 'Cross-patient' in result['errors'][0]
