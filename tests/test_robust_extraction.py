import copy
import hashlib

import pytest
from pydantic import ValidationError

from openpatients2.extraction_contracts import (SourceSpan, FactEnvelope, CoverageUnit,
    field_support_report, coverage_report, span_errors)
from openpatients2.longitudinal import PatientTimeline, audit_timeline
from openpatients2.refinement_plan import GateIssue, RepairAttempt, RefinementBudget, plan_refinement
from openpatients2.corpus_policy import prioritize_article

TEXT = ('Patient 1 was admitted on 2020-03. Surgery was performed 2 days later. '
        'Discharge was 3 days after surgery, 5 days after admission. '
        'A conflicting table states 4 days after admission. A later letter states 4 months after admission.')
SOURCES = {'PMCtest.1:jats': {'b1': TEXT}}
RID = 'PMCtest.1:p1'
HASH = hashlib.sha256(TEXT.encode()).hexdigest()


def span(text=TEXT):
    start = TEXT.index(text)
    return dict(source_id='PMCtest.1:jats', segment_id='b1', segment_sha256=HASH,
                start=start, end=start+len(text), quote=text)


def event(eid):
    return dict(event_id=eid, record_id=RID, episode_id=None, kind='treatment', occurrence='occurred',
                description=eid, fact_ids=[], evidence=[span()], attribution_evidence=[span('Patient 1')], times=[])


def edge(eid, left, right, days=None):
    return dict(edge_id=eid, from_event_id=left, to_event_id=right, relation='before', evidence=[span()],
                offset=None if days is None else dict(text=f'{days} days', lower=days, upper=days, unit='day', precision='exact'))


def timeline(edges=None):
    return dict(schema_version='patient-timeline/2', record_id=RID,
                events=[event('admission'), event('surgery'), event('discharge')], edges=edges or [], limitations=[])


def audit(value):
    return audit_timeline(PatientTimeline.model_validate(value), SOURCES, {})


def codes(result):
    return {i['code'] for i in result['issues']}


def test_spans_pin_namespace_hash_unicode_and_offset():
    assert not span_errors(SourceSpan(**span('2 days later')), SOURCES)
    broken = span(); broken['source_id'] = 'PMCtest.1:pdf'
    assert span_errors(SourceSpan(**broken), SOURCES) == ['unknown_source_segment']
    assert 'source_digest_mismatch' in span_errors(SourceSpan(**span()), {'PMCtest.1:jats': {'b1': TEXT+' changed'}})
    broken = span('2 days later'); broken['start'] += 1; broken['end'] += 1
    assert 'nonliteral_span' in span_errors(SourceSpan(**broken), SOURCES)


def test_partial_calendar_date_retains_precision():
    value = timeline()
    value['events'][0]['times'] = [dict(text='2020-03', kind='calendar', precision='exact', calendar_value='2020-03', evidence=[span()])]
    assert audit(value)['structural_source_gates_passed']
    value['events'][0]['times'][0]['calendar_value'] = '2020-03-01'
    with pytest.raises(ValidationError): PatientTimeline.model_validate(value)


def test_age_and_planned_events_do_not_become_encounter_dates():
    value = timeline()
    value['events'][0].update(kind='plan', occurrence='occurred')
    with pytest.raises(ValidationError): PatientTimeline.model_validate(value)
    value['events'][0]['occurrence'] = 'planned'
    value['events'][0]['times'] = [dict(text='2020-03', kind='age', precision='exact', calendar_value='2020-03', evidence=[span()])]
    with pytest.raises(ValidationError): PatientTimeline.model_validate(value)


def test_wrong_patient_and_unknown_fact_are_gated():
    value = timeline(); value['events'][0]['record_id'] = 'PMCtest.1:p2'
    with pytest.raises(ValidationError): PatientTimeline.model_validate(value)
    value = timeline(); value['events'][0]['fact_ids'] = ['p2:f1']
    assert 'unknown_or_wrong_patient_fact' in codes(audit(value))


def test_order_and_equality_cycles_preserve_conflict():
    value = timeline([edge('r1', 'admission', 'surgery'), edge('r2', 'surgery', 'discharge'), edge('r3', 'discharge', 'admission')])
    before = copy.deepcopy(value)
    assert 'contradictory_temporal_order' in codes(audit(value))
    assert value == before
    value['edges'] = [edge('r1', 'admission', 'surgery'), {**edge('r2', 'admission', 'surgery'), 'relation':'same_time'}]
    assert 'contradictory_temporal_order' in codes(audit(value))


def test_noncyclic_but_incompatible_offsets():
    value = timeline([edge('r1', 'admission', 'surgery', 2), edge('r2', 'surgery', 'discharge', 3), edge('r3', 'admission', 'discharge', 4)])
    assert 'inconsistent_offsets_fixed' in codes(audit(value))
    value['edges'][-1]['offset'].update(lower=5, upper=5, text='5 days')
    result = audit(value)
    assert result['structural_source_gates_passed']
    # Literal numeric support cannot establish attachment or correctness.
    assert not result['clinical_timeline_verified']
    assert 'offset_normalization_unreviewed' in codes(result)


def test_numeric_offset_not_supported_by_literal_expression():
    value = timeline([edge('r1', 'admission', 'surgery', 2)])
    value['edges'][0]['offset'].update(lower=7, upper=7)
    assert 'offset_numbers_not_in_expression' in codes(audit(value))
    value['edges'][0]['offset'].update(lower=2, upper=2, unit='month')
    assert 'offset_unit_not_in_expression' in codes(audit(value))


def test_approximate_and_calendar_units_not_forced_to_days():
    value = timeline([edge('r1', 'admission', 'surgery', 2), edge('r2', 'surgery', 'discharge', 3), edge('r3', 'admission', 'discharge', 4)])
    value['edges'][-1]['offset']['precision'] = 'approximate'
    assert audit(value)['structural_source_gates_passed']
    value['edges'][-1]['offset'].update(precision='exact', unit='month', text='4 months')
    assert 'mixed_calendar_units_not_combined' in codes(audit(value))
    assert 'inconsistent_offsets_fixed' not in codes(audit(value))


def test_disconnected_events_are_unordered_not_sorted_as_article_prose():
    result = audit(timeline())
    assert result['unordered_event_ids'] == ['admission', 'discharge', 'surgery']
    assert not result['synthetic_dates_generated']


def fact():
    return FactEnvelope(fact_id='f1', record_id=RID, task='observations', collection='items',
        value={'name':'pressure', 'numeric_value':0, 'negated':False}, field_support=[], origin='table', supersedes_fact_id=None)


def test_field_evidence_required_even_for_zero_and_false():
    f = fact(); result = field_support_report(f, SOURCES)
    assert {x['pointer'] for x in result['issues']} == {'/name', '/numeric_value', '/negated'}
    value = f.model_dump()
    value['field_support'] = [{'pointer': '/missing', 'evidence':[span()], 'role':'value'}]
    with pytest.raises(ValidationError): FactEnvelope.model_validate(value)
    value['value'] = {'items':['a']}; value['field_support'][0]['pointer'] = '/items/-1'
    with pytest.raises(ValidationError): FactEnvelope.model_validate(value)


def test_coverage_denominator_and_patient_binding():
    unit = CoverageUnit(unit_id='row1', kind='table_row', source_span=SourceSpan(**span()), record_ids=[RID],
        disposition='extracted', fact_ids=['f1'], duplicate_of=None, rationale='Row extracted')
    report = coverage_report([unit], ['row1', 'row2'], [fact()], SOURCES)
    assert codes(report) == {'missing_disposition'}
    unit.record_ids = ['PMCtest.1:p2']
    assert 'fact_patient_mismatch' in codes(coverage_report([unit], ['row1'], [fact()], SOURCES))


def test_duplicate_coverage_cannot_hide_uninspected_source():
    kwargs = dict(kind='paragraph', source_span=SourceSpan(**span()), record_ids=[], fact_ids=[], rationale='Check')
    a = CoverageUnit(unit_id='a', disposition='duplicate', duplicate_of='b', **kwargs)
    b = CoverageUnit(unit_id='b', disposition='duplicate', duplicate_of='a', **kwargs)
    assert 'invalid_duplicate_chain' in codes(coverage_report([a,b], ['a','b'], [], SOURCES))


def issue(target='f1', stage='schema'):
    return GateIssue(issue_id=target+stage, target_id=target, stage=stage, code='bad_field', severity='block',
                     explanation='Needs source-specific repair', source_unit_ids=['b1'])


def attempt(outcome='improved', digest='b'*64, tokens=100):
    return RepairAttempt(record_id=RID, source_digest=HASH, target_id='f1', stage='schema',
                         candidate_digest=digest, remaining_issue_codes=['bad_field'], outcome=outcome, tokens=tokens)


def test_repairs_budgeted_and_stopped_on_no_progress_or_patient_mixup():
    result = plan_refinement(RID, HASH, [issue()], [attempt('unchanged')])
    assert not result['jobs'] and result['held'][0]['reason'] == 'no_progress_or_regression'
    other = attempt(); other.record_id = 'PMCtest.1:p2'
    with pytest.raises(ValueError): plan_refinement(RID, HASH, [issue()], [other])
    result = plan_refinement(RID, HASH, [issue(), issue('f2')], [], RefinementBudget(max_calls=1))
    assert len(result['jobs']) == 1 and len(result['held']) == 1
    result = plan_refinement(RID, HASH, [issue()], [attempt()], RefinementBudget(max_total_tokens=100))
    assert not result['jobs']


def test_rights_block_cannot_be_fixed_by_model_and_one_call_per_target():
    result = plan_refinement(RID, HASH, [issue(stage='rights'), issue('f2')], [])
    assert not result['jobs'] and len(result['held']) == 2
    result = plan_refinement(RID, HASH, [issue(), issue(stage='attribution')], [])
    assert len(result['jobs']) == 1 and result['jobs'][0]['stage'] == 'attribution'
    assert result['jobs'][0]['accepted_neighbors'] == 'frozen'
    assert not result['automatic_clinical_edits']


def article():
    return {'article_id':'PMCtest.1', 'xml_sha256':HASH, 'title':'Research observations', 'article_type':'research-article',
        'license':{'allowed':True, 'code':'CC BY-NC-SA', 'raw_code':'CC BY-NC-SA', 'statements':[]},
        'segments':[{'segment_id':'b1', 'kind':'paragraph', 'heading':'Results', 'text':TEXT}], 'figures':[]}


def test_noncase_reports_and_animals_are_not_dropped_by_priority():
    value = article()
    result = prioritize_article(value)
    assert result['route'] == 'exploration_screen' and result['species_filter'] == 'none'
    value['title'] = 'Veterinary case report'
    assert prioritize_article(value)['route'] == 'priority_screen'
    value['license']['raw_code'] = 'CC BY-ND'
    assert prioritize_article(value)['route'] == 'quarantine'
