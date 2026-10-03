import copy
import hashlib

import pytest
from pydantic import ValidationError

from openpatients2.extraction_contracts import (SourceSpan, FactEnvelope, CoverageUnit,
    field_support_report, coverage_report, span_errors)
from openpatients2.longitudinal import PatientTimeline, audit_timeline
from openpatients2.refinement_plan import GateIssue, RepairAttempt, RefinementBudget, plan_refinement
from openpatients2.corpus_policy import prioritize_article
from openpatients2.evidence_recovery import resolve_timeline_spans

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


def test_unique_span_resolution_derives_offsets_and_missing_provenance_only():
    value = timeline([edge('r1', 'admission', 'surgery', 2)])
    before = copy.deepcopy(value)
    for e in value['events']:
        for s in e['evidence'] + e['attribution_evidence']:
            s.update(start=999, end=1000)
            s.pop('source_id'); s.pop('segment_sha256')
    fixed, report = resolve_timeline_spans(value, 'PMCtest.1:jats', [{'segment_id':'b1','text':TEXT}])
    assert fixed == before and value != before
    assert len(report['recoveries']) == 6 and not report['unresolved']
    assert not report['clinical_content_changed'] and not report['clinical_entailment_verified']
    assert report['recoveries'][0]['source_sha256'] == HASH
    assert report['recoveries'][0]['quote_sha256'] == HASH
    assert audit(fixed)['structural_source_gates_passed']


@pytest.mark.parametrize('text,quote', [
    ('Patient 1: 60 mg. Patient 2: 60 mg.', '60 mg'),
    ('Day 1: improved. Day 5: improved.', 'improved'),
    ('a a a', 'a a'),
])
def test_unique_span_resolution_refuses_numeric_patient_time_and_overlapping_ambiguity(text, quote):
    value = {'events':[{'evidence':[{'segment_id':'b1','quote':quote,'start':0,'end':len(quote)}]}]}
    before = copy.deepcopy(value)
    fixed, report = resolve_timeline_spans(value, 'PMCtest.1:jats', [{'segment_id':'b1','text':text}])
    assert fixed == before == value
    assert report['unresolved'][0]['code'] == 'ambiguous_exact_quote'
    assert not report['resolved']


@pytest.mark.parametrize('changes,code', [
    ({'source_id':'PMCtest.1:pdf'}, 'conflicting_source_id'),
    ({'segment_sha256':'a'*64}, 'conflicting_source_digest'),
    ({'segment_id':'missing'}, 'unknown_source_segment'),
    ({'quote':'Surgery was performed 7 days later.'}, 'nonliteral_quote'),
    ({'quote':'Surgery was performed  2 days later.'}, 'nonliteral_quote'),
])
def test_unique_span_resolution_refuses_conflicts_and_never_rewrites_medical_claim(changes, code):
    original = span('Surgery was performed 2 days later.'); original.update(changes)
    value = {'events':[{'record_id':RID,'description':'Surgery','times':[{'text':'7 days later'}],
                       'evidence':[original]}], 'edges':[{'offset':{'lower':7,'unit':'day'}}]}
    before = copy.deepcopy(value)
    fixed, report = resolve_timeline_spans(value, 'PMCtest.1:jats', [{'segment_id':'b1','text':TEXT}])
    assert fixed == before == value
    assert report['unresolved'][0]['code'] == code


def test_unique_span_resolution_unicode_offsets_do_not_repair_wrong_patient_or_time():
    text = 'µ Patient 2 received 60 mg for 2 months.'
    value = {'record_id':RID,'events':[{'record_id':'PMCtest.1:p2','evidence':[
        {'segment_id':'b1','quote':'60 mg','start':999,'end':1004}],
        'times':[{'text':'2 days','evidence':[{'segment_id':'b1','quote':'2 months'}]}]}]}
    fixed, report = resolve_timeline_spans(value, 'PMCtest.1:jats', [{'segment_id':'b1','text':text}])
    assert fixed['events'][0]['record_id'] == 'PMCtest.1:p2'
    assert fixed['events'][0]['times'][0]['text'] == '2 days'
    assert fixed['events'][0]['evidence'][0]['start'] == text.index('60 mg')
    assert report['resolved'][0]['source_span'] == [text.index('60 mg'), text.index('60 mg')+5]
    assert not span_errors(SourceSpan(**fixed['events'][0]['evidence'][0]), {'PMCtest.1:jats':{'b1':text}})
    assert not report['clinical_entailment_verified']
    with pytest.raises(ValueError):
        resolve_timeline_spans(value, 'PMCtest.1:jats', [{'segment_id':'b1','text':text}]*2)


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


def test_normalized_schema_enums_are_provenance_review_not_literal_prose():
    f = FactEnvelope(fact_id='f1',record_id=RID,task='medications',collection='items',
        value={'subject':'index_patient','assertion':'present','temporality':'historical',
               'action':'administered','name':'Surgery','dose_value':2,
               'time':{'text':'2 days later','relation':'after','anchor':'Surgery'}},
        field_support=[{'pointer':pointer,'role':'value','evidence':[span(quote)]} for pointer,quote in
                       [('/name','Surgery'),('/dose_value','2 days later'),('/time/text','2 days later'),
                        ('/time/anchor','Surgery')]], origin='article_text',supersedes_fact_id=None)
    result = field_support_report(f,SOURCES)
    assert result['literal_field_gates_passed']
    assert result['required_literal_pointers'] == ['/dose_value','/name','/time/anchor','/time/text']
    assert {x['pointer'] for x in result['normalized_fields']} == {
        '/subject','/assertion','/temporality','/action','/time/relation'}
    assert not result['normalized_provenance_gates_passed']
    assert not result['field_provenance_gates_passed']
    assert not result['clinical_entailment_verified'] and not result['patient_attribution_verified']
    assert any(x['code']=='missing_subject_attribution_evidence' for x in result['review_issues'])
    # Evidence containing "2" cannot establish a 7 mg dose, nor that this is
    # medication rather than a temporal phrase. Both remain visible to review.
    f.value['dose_value'] = 7
    assert 'field_value_not_literal_in_evidence' in codes(field_support_report(f,SOURCES))


def test_enum_exemptions_are_schema_specific_and_identity_support_is_role_specific():
    f = FactEnvelope(fact_id='f1',record_id=RID,task='medications',collection='items',
        value={'subject':'index_patient','name':'present','unrecognized_enum':'historical','dose_value':20},
        field_support=[{'pointer':'/subject','role':'value','evidence':[span('Patient 1')]},
                       {'pointer':'/dose_value','role':'value','evidence':[span('2 days later')]}],
        origin='article_text',supersedes_fact_id=None)
    report = field_support_report(f,SOURCES)
    assert {x['pointer'] for x in report['issues']} == {'/name','/unrecognized_enum','/dose_value'}
    assert report['review_issues'][0]['code'] == 'missing_subject_attribution_evidence'
    f.field_support[0].role = 'subject'
    report = field_support_report(f,SOURCES)
    assert not report['review_issues']
    assert not report['patient_attribution_verified']


def test_local_registry_metadata_needs_link_review_not_literal_matching():
    f = FactEnvelope(fact_id='f1',record_id=RID,task='oncology',collection='tumors',
        value={'tumor_ref':'t1','name':'Surgery','laterality':'left'},
        field_support=[{'pointer':'/name','role':'value','evidence':[span('Surgery')]}],
        origin='article_text',supersedes_fact_id=None)
    report = field_support_report(f,SOURCES)
    assert report['literal_field_gates_passed'] and report['required_literal_pointers'] == ['/name']
    assert {x['code'] for x in report['review_issues']} == {'registry_link_unreviewed','normalized_enum_provenance_unreviewed'}


def test_invalid_provenance_blocks_even_normalized_enum_and_boolean_review_stays_open():
    broken = span(); broken['segment_sha256'] = 'a'*64
    f = FactEnvelope(fact_id='f1',record_id=RID,task='medications',collection='items',
        value={'assertion':'present','negated':False,'numeric_value':0},
        field_support=[{'pointer':'/assertion','role':'negation','evidence':[broken]},
                       {'pointer':'/negated','role':'negation','evidence':[span()]}],
        origin='article_text',supersedes_fact_id=None)
    report = field_support_report(f,SOURCES)
    assert {'source_digest_mismatch','missing_field_evidence'} <= codes(report)
    assert any(x['code']=='boolean_clinical_claim_unreviewed' for x in report['review_issues'])


def test_field_support_cannot_borrow_valid_quotes_from_another_article_patient():
    f = FactEnvelope(fact_id='f1',record_id='PMCother.1:p1',task='medications',collection='items',
        value={'name':'Surgery'}, field_support=[{'pointer':'/name','role':'value','evidence':[span('Surgery')]}],
        origin='article_text',supersedes_fact_id=None)
    report = field_support_report(f,SOURCES)
    assert 'fact_source_article_mismatch' in codes(report)
    assert not report['literal_field_gates_passed']


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
