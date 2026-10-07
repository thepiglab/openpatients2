import copy
import hashlib
import json

import pytest
from jsonschema import validate

from openpatients2.episode_ledger import (LedgerDeltaError, apply_ledger_delta,
    apply_ledger_delta_partial, build_ledger, ledger_messages, ledger_timeline,
    ledger_wire_schema, parse_ledger_delta, source_ordered_fact_aliases)
from openpatients2.longitudinal import relative_order


RID = 'PMCtest.1:p1'
SID = 'PMCtest.1:jats'
TEXT = ('Patient 1 received Drug X 5 mg in 2010. Patient 1 received Drug X 10 mg in 2015. '
        'The 2010 administration preceded the 2015 administration. Both evaluations occurred together. '
        'Patient 2 received Drug Y. A separate undated assessment was documented.')
SOURCES = {SID: {'s1': TEXT}}
ARTICLE = {'article_id': 'PMCtest.1', 'segments': [{'segment_id': 's1', 'text': TEXT}]}
FIRST = 'Patient 1 received Drug X 5 mg in 2010.'
SECOND = 'Patient 1 received Drug X 10 mg in 2015.'


def cite(quote):
    return {'segment_id': 's1', 'quote': quote}


def span(quote):
    start = TEXT.index(quote)
    return dict(source_id=SID, segment_id='s1', segment_sha256=hashlib.sha256(TEXT.encode()).hexdigest(),
                start=start, end=start + len(quote), quote=quote)


def fact(fid, quote, dose=5, year='2010', **updates):
    row = dict(review_id=fid, record_id=RID, task='medications', pointer='/items/0',
        candidate=dict(subject='index_patient', name='Drug X', action='administered', assertion='present',
            temporality='historical', dose_value=dose, time={'text': year, 'date_iso': None}, evidence=[cite(quote)]),
        evidence_spans=[span(quote)], field_support=[{'pointer': '/dose_value', 'evidence': [span(quote)]}])
    row.update(updates)
    return row


def ledger(rows=None):
    return build_ledger(RID, rows if rows is not None else [fact('immutable-a', FIRST),
        fact('immutable-b', SECOND, 10, '2015')], SOURCES)


def event(ref, quote, **updates):
    value = dict(event_ref=ref, occurrence_anchor=cite(quote), episode_anchor=None, kind='treatment',
        occurrence='occurred', description=quote, clinical_evidence=[cite(quote)], attribution_evidence=[cite(quote)], times=[])
    value.update(updates)
    return value


def link(ref, alias, quote):
    return dict(event_ref=ref, fact_alias=alias, evidence=[cite(quote)])


def edge(a, b, relation='before', quote='The 2010 administration preceded the 2015 administration.'):
    return dict(from_event_ref=a, to_event_ref=b, relation=relation, offset=None, evidence=[cite(quote)])


def delta(events=None, fact_links=None, edges=None, **updates):
    value = dict(schema_version='episode-ledger-delta/1', record_id=RID,
        events=events or [], fact_links=fact_links or [], edges=edges or [], limitations=[])
    value.update(updates)
    return value


def two_events():
    return apply_ledger_delta(ledger(), delta(events=[event('n1', FIRST), event('n2', SECOND)],
        fact_links=[link('n1', 'f1', FIRST), link('n2', 'f2', SECOND)]), SOURCES)[0]


def test_aliases_are_deterministic_patient_local_and_never_replace_source_fact_ids():
    rows = [fact('immutable-b', SECOND, 10, '2015'), fact('immutable-a', FIRST)]
    a = ledger(rows)
    assert a == ledger(list(reversed(rows)))
    assert [(f['alias'], f['fact_id']) for f in a['facts']] == [('f1', 'immutable-a'), ('f2', 'immutable-b')]
    other = build_ledger('PMCtest.1:p2', [fact('other-id', 'Patient 2 received Drug Y.', record_id='PMCtest.1:p2')], SOURCES)
    assert other['facts'][0]['alias'] == 'f1'
    assert other['facts'][0]['fact_id'] == 'other-id'


def test_unknown_owner_other_patient_and_ambiguous_subject_are_quarantined():
    rows = [fact('other', FIRST, record_id='PMCtest.1:p2'), fact('missing', FIRST)]
    rows[1].pop('record_id')
    unknown = fact('uncertain', FIRST)
    unknown['candidate']['subject'] = 'unknown'
    a = ledger(rows + [unknown])
    assert not a['facts']
    assert len(a['quarantine']) == 3
    with pytest.raises(ValueError, match='unique'):
        ledger([fact('same', FIRST), fact('same', SECOND)])


def test_patient_scoped_fact_without_subject_retains_unresolved_attribution():
    row = fact('missing-subject', FIRST)
    row['candidate'].pop('subject')
    a = ledger([row])
    assert a['facts'][0]['subject_attribution'] == 'unresolved'
    assert not a['facts'][0]['patient_attribution_verified']
    assert a['fact_quarantine_summary'] == dict(input_facts=1, accepted_facts=1, quarantined_facts=0, reasons={})


def test_fact_source_spans_are_gated_separately_from_attribute_evidence():
    a = ledger()
    assert a['facts'][0]['clinical_evidence'] == [span(FIRST)]
    assert a['facts'][0]['attribute_evidence'][0]['pointer'] == '/dose_value'
    assert not a['facts'][0]['attribute_entailment_verified']
    bad = fact('bad', FIRST)
    bad['evidence_spans'][0]['segment_sha256'] = '0' * 64
    assert ledger([bad])['quarantine'][0]['reason'] == 'invalid_fact_source_evidence'


def test_event_and_fact_links_export_immutable_fact_ids_with_relative_order():
    a = two_events()
    a, report = apply_ledger_delta(a, delta(edges=[edge('e1', 'e2')]), SOURCES)
    graph = ledger_timeline(a)
    assert graph['events'][0]['fact_ids'] == ['immutable-a']
    assert graph['events'][1]['fact_ids'] == ['immutable-b']
    assert relative_order(graph)['before_pairs'] == [['e1', 'e2']]
    assert report['structural_source_gates_passed']
    assert not report['clinical_timeline_verified']
    assert not report['attribute_evidence_verified']


def test_additions_preserve_existing_nodes_edges_and_links_exactly():
    a = two_events()
    a, _ = apply_ledger_delta(a, delta(edges=[edge('e1', 'e2')]), SOURCES)
    before = copy.deepcopy(a)
    b, report = apply_ledger_delta(a, delta(events=[event('n1', 'A separate undated assessment was documented.', kind='test')]), SOURCES)
    assert a == before
    assert b['events'][:2] == before['events']
    assert b['fact_links'] == before['fact_links']
    assert b['edges'] == before['edges']
    assert report['accepted_event_refs'] == {'n1': 'e3'}
    rendered = relative_order(ledger_timeline(b))
    assert ['e1', 'e3'] in rendered['incomparable_pairs']
    assert rendered['order_unknown_event_ids'] == ['e3']


def test_later_fact_link_adds_association_without_mutating_accepted_event():
    a, _ = apply_ledger_delta(ledger(), delta(events=[event('n1', FIRST)]), SOURCES)
    accepted_event = copy.deepcopy(a['events'][0])
    b, _ = apply_ledger_delta(a, delta(fact_links=[link('e1', 'f1', FIRST)]), SOURCES)
    assert b['events'][0] == accepted_event
    assert a['fact_links'] == []
    assert ledger_timeline(b)['events'][0]['fact_ids'] == ['immutable-a']
    c, audit = apply_ledger_delta(b, delta(fact_links=[link('e1', 'f1', FIRST)]), SOURCES)
    assert c == b and audit['accepted_additions']['fact_links'] == 0


@pytest.mark.parametrize('proposal,code', [
    (delta(record_id='PMCtest.1:p2'), 'ledger_wrong_patient'),
    (delta(fact_links=[link('e1', 'f99', FIRST)]), 'unknown_fact_alias'),
    (delta(edges=[edge('e1', 'e99')]), 'unknown_event_ref'),
    (delta(events=[event('n1', FIRST)]), 'existing_occurrence_requires_fact_link'),
    (delta(fact_links=[link('e1', 'f2', FIRST)]), 'fact_link_outside_fact_evidence'),
    (delta(fact_links=[link('e1', 'f2', SECOND)]), 'fact_link_outside_event_evidence'),
])
def test_invalid_delta_is_atomic_and_never_repairs_unknown_references(proposal, code):
    a = two_events()
    before = copy.deepcopy(a)
    with pytest.raises(LedgerDeltaError, match=code):
        apply_ledger_delta(a, proposal, SOURCES)
    assert a == before


@pytest.mark.parametrize('edges', [
    [edge('e1', 'e2'), edge('e2', 'e1')],
    [edge('e1', 'e2'), edge('e1', 'e2', 'same_time')],
    [edge('e1', 'e2', 'after'), edge('e1', 'e2')],
])
def test_strict_cycles_and_order_through_ties_are_rejected_without_dropping_accepted_links(edges):
    a = two_events()
    with pytest.raises(LedgerDeltaError, match='contradictory_temporal_order'):
        apply_ledger_delta(a, delta(edges=edges), SOURCES)
    assert len(a['fact_links']) == 2 and a['edges'] == []


def test_explicit_ties_and_unknown_order_are_preserved_without_dates():
    a = two_events()
    a, _ = apply_ledger_delta(a, delta(edges=[edge('e1', 'e2', 'same_time', 'Both evaluations occurred together.')]), SOURCES)
    result = relative_order(ledger_timeline(a))
    assert result['same_time_groups'] == [['e1', 'e2']]
    assert result['before_pairs'] == []
    assert not result['synthetic_dates_generated']


def test_same_drug_different_years_or_doses_cannot_be_collapsed_into_one_event():
    broad = FIRST + ' ' + SECOND
    with pytest.raises(LedgerDeltaError, match='distinct_administrations_require_separate_events'):
        apply_ledger_delta(ledger(), delta(events=[event('n1', broad)],
            fact_links=[link('n1', 'f1', FIRST), link('n1', 'f2', SECOND)]), SOURCES)
    a = two_events()
    assert a['events'][0]['occurrence_anchor'] != a['events'][1]['occurrence_anchor']
    assert a['events'][0]['episode_id'] is None


def test_planned_fact_cannot_become_occurred_by_linking():
    planned = fact('planned', FIRST)
    planned['candidate']['action'] = 'planned'
    with pytest.raises(LedgerDeltaError, match='fact_occurrence_conflict'):
        apply_ledger_delta(ledger([planned]), delta(events=[event('n1', FIRST)],
            fact_links=[link('n1', 'f1', FIRST)]), SOURCES)


def test_unique_quote_resolution_rejects_ambiguous_and_invented_quotes():
    a = ledger()
    for quote, code in [('Patient 1', 'ambiguous_exact_quote'), ('Patient 1 received 90 mg.', 'unresolved_exact_quote')]:
        with pytest.raises(LedgerDeltaError, match=code):
            apply_ledger_delta(a, delta(events=[event('n1', quote)]), SOURCES)
    with pytest.raises(LedgerDeltaError, match='occurrence_anchor_outside_clinical_evidence'):
        apply_ledger_delta(a, delta(events=[event('n1', FIRST, clinical_evidence=[cite(SECOND)])]), SOURCES)


def test_literal_calendar_precision_is_validated_without_inference():
    time = dict(text='2010', kind='calendar', precision='exact', calendar_value='2010-01-01', evidence=[cite(FIRST)])
    with pytest.raises(ValueError, match='precision'):
        apply_ledger_delta(ledger(), delta(events=[event('n1', FIRST, times=[time])]), SOURCES)


def test_ordinary_json_schema_and_prompt_batching_deliver_global_event_index():
    a = two_events()
    value = delta(edges=[edge('e1', 'e2')])
    validate(value, ledger_wire_schema())
    assert parse_ledger_delta(json.dumps(value)).model_dump() == value
    with pytest.raises(ValueError, match='Duplicate JSON key'):
        parse_ledger_delta('{"record_id":"p1","record_id":"p2"}')
    messages = ledger_messages(ARTICLE, {'patient_id': 'p1'}, a, ['f1'], phase='edges', event_ids=['e1'])
    payload = json.loads(messages[1]['content'].split('SOURCE_JSON:\n')[1].split('\nTASK:\n')[0])
    assert [f['fact_alias'] for f in payload['fact_registry']] == ['f1']
    assert len(payload['accepted_event_index']) == 2
    assert payload['target_event_ids'] == ['e1']
    assert 'immutable-a' not in messages[1]['content']
    assert 'return events=[] and fact_links=[]' in messages[1]['content']
    assert payload['segments'][0]['text'] == TEXT


def test_prompt_delivers_only_original_evidence_blocks_and_neighbors():
    article = {**ARTICLE, 'segments': [{'segment_id': f'b{i}', 'text': f'Unrelated original block {i}'}
                                     for i in range(8)] + ARTICLE['segments']}
    messages = ledger_messages(article, {'patient_id': 'p1'}, ledger(), ['f1'])
    payload = json.loads(messages[1]['content'].split('SOURCE_JSON:\n')[1].split('\nTASK:\n')[0])
    assert payload['segments'] == [article['segments'][-2], article['segments'][-1]]
    assert not payload['source_selection']['complete_article_delivered']
    assert payload['source_selection']['article_segments'] == 9
    with pytest.raises(ValueError, match='Unknown fact batch alias'):
        ledger_messages(article, {}, ledger(), ['f99'])


def test_source_mutation_cannot_change_accepted_occurrence_provenance():
    a = two_events()
    changed = {SID: {'s1': TEXT + ' Additional source.'}}
    with pytest.raises(LedgerDeltaError, match='source_digest_mismatch'):
        apply_ledger_delta(a, delta(), changed)
    assert a['events'][0]['evidence'] == [span(FIRST)]


@pytest.mark.parametrize('proposal,scope,code', [
    (delta(edges=[edge('e1', 'e2')]), {'phase': 'events'}, 'edge_additions_outside_edge_phase'),
    (delta(events=[event('n1', 'A separate undated assessment was documented.')]),
     {'phase': 'edges'}, 'event_or_fact_additions_outside_event_phase'),
    (delta(fact_links=[link('e1', 'f1', FIRST)]), {'phase': 'edges'}, 'event_or_fact_additions_outside_event_phase'),
    (delta(fact_links=[link('e2', 'f2', SECOND)]), {'phase': 'events', 'fact_aliases': ['f1']}, 'fact_alias_outside_delivered_batch'),
    (delta(edges=[edge('e1', 'e2')]), {'phase': 'edges', 'event_ids': []}, 'edge_outside_target_event_batch'),
    (delta(edges=[edge('e1', 'e2')]), {'phase': 'edges', 'available_segment_ids': ['absent']}, 'unresolved_exact_quote'),
])
def test_request_scopes_reject_cross_phase_and_undelivered_references(proposal, scope, code):
    a = two_events()
    before = copy.deepcopy(a)
    with pytest.raises(LedgerDeltaError, match=code):
        apply_ledger_delta(a, proposal, SOURCES, **scope)
    assert a == before


def test_request_scope_restricts_only_new_witnesses_and_audits_old_full_source():
    a = two_events()
    b, report = apply_ledger_delta(a, delta(edges=[edge('e1', 'e2')]), SOURCES,
        phase='edges', fact_aliases=[], event_ids=['e1'], available_segment_ids=['s1'])
    assert b['events'] == a['events']
    assert report['structural_source_gates_passed']


def test_episode_citations_remain_compatible_with_existing_format_recovery():
    from openpatients2.evidence_recovery import recover_citations
    value = delta(events=[event('n1', FIRST)], fact_links=[link('n1', 'f1', FIRST)])
    value['events'][0]['occurrence_anchor']['segment_id'] = '[s1]'
    recovered, audit = recover_citations(value, ARTICLE['segments'])
    assert audit and recovered['events'][0]['occurrence_anchor'] == cite(FIRST)
    a, _ = apply_ledger_delta(ledger(), recovered, SOURCES, phase='events',
        fact_aliases=['f1'], available_segment_ids=['s1'])
    assert ledger_timeline(a)['events'][0]['fact_ids'] == ['immutable-a']


def test_prompt_keeps_target_patient_identity_witness_outside_fact_neighborhood():
    article = {**ARTICLE, 'segments': [{'segment_id': f'b{i}', 'text': f'Original block {i}'}
                                     for i in range(8)] + ARTICLE['segments']}
    patient = {'patient_id': 'p1', 'identity_evidence': [{'segment_id': 'b0', 'quote': 'Original block 0'}]}
    messages = ledger_messages(article, patient, ledger(), ['f1'])
    payload = json.loads(messages[1]['content'].split('SOURCE_JSON:\n')[1].split('\nTASK:\n')[0])
    assert payload['source_selection']['selected_segment_ids'] == ['b0', 'b1', 'b7', 's1']


@pytest.mark.parametrize('task,kind,quote,extra', [
    ('symptoms_function', 'test', 'Examination showed no spasticity.', {'name': 'spasticity'}),
    ('observations', 'test', 'Histopathology showed no melanin pigment.', {'name': 'melanin pigment'}),
    ('observations', 'test', 'Vascular markers were negative.', {'name': 'vascular markers'}),
    ('conditions', 'diagnosis', 'The evaluation ruled out melanoma.',
     {'name': 'melanoma', 'verification_status': 'refuted'}),
])
def test_completed_evaluations_can_link_absent_findings_and_refuted_diagnoses(task, kind, quote, extra):
    sources = {SID: {'s1': quote}}
    row = dict(review_id='negative-finding', record_id=RID, task=task,
        candidate=dict(subject='index_patient', assertion='absent', temporality='current',
                       evidence=[cite(quote)], **extra))
    a = build_ledger(RID, [row], sources)
    b, audit = apply_ledger_delta(a, delta(events=[event('n1', quote, kind=kind)],
        fact_links=[link('n1', 'f1', quote)]), sources)
    assert ledger_timeline(b)['events'][0]['fact_ids'] == ['negative-finding']
    assert b['events'][0]['occurrence'] == 'occurred'
    assert b['facts'][0]['value']['assertion'] == 'absent'
    assert audit['structural_source_gates_passed']
    assert not b['facts'][0]['clinical_entailment_verified']


@pytest.mark.parametrize('action', ['planned', 'ordered', 'declined', 'cancelled'])
def test_partial_application_preserves_planned_and_cancelled_treatment_guards(action):
    planned = fact('immutable-b', SECOND, 10, '2015')
    planned['candidate']['action'] = action
    a = ledger([fact('immutable-a', FIRST), planned])
    before = copy.deepcopy(a)
    b, audit = apply_ledger_delta_partial(a, delta(events=[event('n1', FIRST), event('n2', SECOND)],
        fact_links=[link('n1', 'f1', FIRST), link('n2', 'f2', SECOND)]), SOURCES)
    assert a == before
    assert len(b['events']) == 2
    assert [(x['event_id'], x['fact_alias']) for x in b['fact_links']] == [('e1', 'f1')]
    assert audit['rejected_additions'] == {'events': 0, 'fact_links': 1, 'edges': 0}
    assert b['quarantine'][-1]['reason'] == 'fact_occurrence_conflict'


@pytest.mark.parametrize('task,assertion,temporality', [
    ('medications', 'absent', 'historical'),
    ('observations', 'conditional', 'current'),
    ('observations', 'present', 'future'),
])
def test_occurrence_guards_still_reject_negated_interventions_conditional_and_future_facts(task, assertion, temporality):
    row = fact('guarded', FIRST, task=task)
    row['candidate'].update(assertion=assertion, temporality=temporality)
    with pytest.raises(LedgerDeltaError, match='fact_occurrence_conflict'):
        apply_ledger_delta(ledger([row]), delta(events=[event('n1', FIRST)],
            fact_links=[link('n1', 'f1', FIRST)]), SOURCES)


def test_source_ordered_batches_preserve_aliases_and_use_document_order_and_span_offsets():
    sources = {SID: {'b10': 'Early finding. Later finding.', 'b2': 'Final finding.'}}
    def row(fid, segment, quote):
        return dict(review_id=fid, record_id=RID, task='observations',
            candidate={'subject': 'index_patient', 'evidence': [{'segment_id': segment, 'quote': quote}]})
    a = build_ledger(RID, [row('a-final', 'b2', 'Final finding.'),
        row('b-later', 'b10', 'Later finding.'), row('z-early', 'b10', 'Early finding.')], sources)
    before = copy.deepcopy(a)
    assert source_ordered_fact_aliases(a, sources) == ['f3', 'f2', 'f1']
    article = {'segments': [{'segment_id': 'b2'}, {'segment_id': 'b10'}]}
    assert source_ordered_fact_aliases(a, sources, article=article) == ['f1', 'f3', 'f2']
    assert a == before
    assert [(f['alias'], f['fact_id']) for f in a['facts']] == [
        ('f1', 'a-final'), ('f2', 'b-later'), ('f3', 'z-early')]


def test_distinct_event_semantics_can_share_an_occurrence_anchor():
    proposals = [event('n1', FIRST), event('n2', FIRST, kind='test', description='Treatment monitoring evaluation')]
    b, audit = apply_ledger_delta(ledger(), delta(events=proposals), SOURCES)
    assert len(b['events']) == 2
    assert b['events'][0]['occurrence_anchor'] == b['events'][1]['occurrence_anchor']
    assert audit['accepted_event_refs'] == {'n1': 'e1', 'n2': 'e2'}


def test_partial_exact_duplicate_proposal_reuses_existing_event_without_mutating_it():
    a, _ = apply_ledger_delta(ledger(), delta(events=[event('n1', FIRST)]), SOURCES)
    before = copy.deepcopy(a)
    b, audit = apply_ledger_delta_partial(a, delta(events=[event('n2', FIRST), event('n3', SECOND)],
        fact_links=[link('n2', 'f1', FIRST), link('n3', 'f2', SECOND)]), SOURCES)
    assert a == before
    assert b['events'][0] == before['events'][0]
    assert audit['accepted_event_refs'] == {'n2': 'e1', 'n3': 'e2'}
    assert audit['reused_event_refs'] == {'n2': 'e1'}
    assert audit['accepted_additions'] == {'events': 1, 'fact_links': 2, 'edges': 0}
    assert audit['rejected_additions'] == {'events': 0, 'fact_links': 0, 'edges': 0}


def test_partial_exact_duplicate_within_batch_maps_both_refs_safely():
    b, audit = apply_ledger_delta_partial(ledger(), delta(events=[event('n1', FIRST), event('n2', FIRST)],
        fact_links=[link('n2', 'f1', FIRST)]), SOURCES)
    assert len(b['events']) == 1
    assert audit['accepted_event_refs'] == {'n1': 'e1', 'n2': 'e1'}
    assert audit['reused_event_refs'] == {'n2': 'e1'}
    assert b['fact_links'][0]['event_id'] == 'e1'


def test_partial_invalid_neighboring_link_preserves_good_events_links_and_edges():
    a = ledger()
    before = copy.deepcopy(a)
    b, audit = apply_ledger_delta_partial(a, delta(events=[event('n1', FIRST), event('n2', SECOND)],
        fact_links=[link('n1', 'f1', FIRST), link('n1', 'f2', FIRST), link('n2', 'f2', SECOND)],
        edges=[edge('n1', 'n2')]), SOURCES)
    assert a == before
    assert audit['accepted_additions'] == {'events': 2, 'fact_links': 2, 'edges': 1}
    assert audit['rejected_additions'] == {'events': 0, 'fact_links': 1, 'edges': 0}
    assert b['quarantine'][-1]['reason'] == 'fact_link_outside_fact_evidence'
    assert relative_order(ledger_timeline(b))['before_pairs'] == [['e1', 'e2']]
    assert audit['structural_source_gates_passed']


def test_partial_bad_event_and_its_dependencies_cannot_discard_independent_valid_event():
    malformed = event('n1', FIRST)
    malformed['kind'] = 'invented-kind'
    b, audit = apply_ledger_delta_partial(ledger(), delta(events=[malformed, event('n2', SECOND)],
        fact_links=[link('n1', 'f1', FIRST), link('n2', 'f2', SECOND)],
        edges=[edge('n1', 'n2')]), SOURCES)
    assert audit['accepted_event_refs'] == {'n2': 'e1'}
    assert audit['accepted_additions'] == {'events': 1, 'fact_links': 1, 'edges': 0}
    assert audit['rejected_additions'] == {'events': 1, 'fact_links': 1, 'edges': 1}
    assert [q['reason'] for q in b['quarantine']] == [
        'invalid_ledger_addition', 'unknown_event_ref', 'unknown_event_ref']


@pytest.mark.parametrize('conflicting', [edge('e2', 'e1'), edge('e1', 'e2', 'same_time')])
def test_partial_edges_keep_valid_order_and_quarantine_cycle_or_contradictory_tie(conflicting):
    a = two_events()
    b, audit = apply_ledger_delta_partial(a, delta(edges=[edge('e1', 'e2'), conflicting]), SOURCES,
        phase='edges', event_ids=['e1'])
    assert len(b['edges']) == 1
    assert b['events'] == a['events'] and b['fact_links'] == a['fact_links']
    assert audit['rejected_additions']['edges'] == 1
    assert b['quarantine'][-1]['reason'] == 'contradictory_temporal_order'
    assert audit['structural_source_gates_passed']


def test_partial_distinct_administration_guard_rejects_only_conflicting_link():
    broad = FIRST + ' ' + SECOND
    b, audit = apply_ledger_delta_partial(ledger(), delta(events=[event('n1', broad)],
        fact_links=[link('n1', 'f1', FIRST), link('n1', 'f2', SECOND)]), SOURCES)
    assert len(b['fact_links']) == 1
    assert audit['rejected_additions']['fact_links'] == 1
    assert b['quarantine'][-1]['reason'] == 'distinct_administrations_require_separate_events'


def test_partial_duplicate_event_refs_are_ambiguous_and_never_rebind_dependent_links():
    b, audit = apply_ledger_delta_partial(ledger(), delta(events=[event('n1', FIRST), event('n1', SECOND)],
        fact_links=[link('n1', 'f1', FIRST)]), SOURCES)
    assert not b['events'] and not b['fact_links']
    assert audit['accepted_event_refs'] == {}
    assert [q['reason'] for q in b['quarantine']] == ['duplicate_event_ref', 'duplicate_event_ref', 'unknown_event_ref']


def test_partial_api_preserves_request_scope_source_and_owner_gates():
    a = two_events()
    b, audit = apply_ledger_delta_partial(a, delta(fact_links=[link('e1', 'f1', FIRST), link('e2', 'f2', SECOND)],
        edges=[edge('e1', 'e2')]), SOURCES, phase='events', fact_aliases=['f1'])
    assert b['events'] == a['events'] and b['fact_links'] == a['fact_links'] and not b['edges']
    assert [q['reason'] for q in audit['quarantined_additions']] == [
        'fact_alias_outside_delivered_batch', 'edge_additions_outside_edge_phase']
    c, report = apply_ledger_delta_partial(a, delta(edges=[edge('e1', 'e2')]), SOURCES,
        phase='edges', available_segment_ids=['undelivered'])
    assert not c['edges'] and report['quarantined_additions'][0]['reason'] == 'unresolved_exact_quote'
    changed = {SID: {'s1': TEXT + ' Mutated original source.'}}
    with pytest.raises(LedgerDeltaError, match='source_digest_mismatch'):
        apply_ledger_delta_partial(a, delta(), changed)
    with pytest.raises(LedgerDeltaError, match='ledger_wrong_patient'):
        apply_ledger_delta_partial(a, delta(record_id='PMCtest.1:p2'), SOURCES)
    wrong_owner = ledger()
    wrong_owner['facts'][0]['record_id'] = 'PMCtest.1:p2'
    d, report = apply_ledger_delta_partial(wrong_owner, delta(events=[event('n1', FIRST)],
        fact_links=[link('n1', 'f1', FIRST)]), SOURCES)
    assert len(d['events']) == 1 and not d['fact_links']
    assert report['quarantined_additions'][0]['reason'] == 'unknown_or_wrong_patient_fact'
