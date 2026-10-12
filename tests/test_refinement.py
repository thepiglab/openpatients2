import copy
import json
import gzip
from pathlib import Path

import pytest

from openpatients2.clinical_normalization import normalize_clinical
from openpatients2.evidence_recovery import resolve_timeline_spans
from openpatients2.longitudinal import relative_order, partial_timeline, canonical_timeline_ids
from openpatients2.pilot_extract import sources_for, timeline_messages
from openpatients2.targeted_repair import repair_snapshot, erased, ItemRepair, changed_accepted_atoms
from test_pilot_correctness import quote_only_timeline
from test_pilot_extract import article


def test_unbound_time_quarantine_preserves_measurement_and_audit():
    quote = 'Drainage fluid creatinine was 0.27 mg/dL.'
    original = {'items':[{'numeric_value':.27, 'unit':'mg/dL', 'specimen':'drainage fluid',
        'evidence':[{'quote':quote, 'source_section':'b1'}],
        'time':{'text':'at seven months', 'relation':'at', 'anchor':None, 'date_iso':None}}]}
    result, audit = normalize_clinical('observations', original, [{'segment_id':'b1','text':quote}])
    assert result['items'][0]['time']['text'] is None and original['items'][0]['time']['text']
    assert result['items'][0]['numeric_value'] == .27 and result['items'][0]['specimen'] == 'drainage fluid'
    assert audit[0]['before']['text'] == 'at seven months' and audit[0]['status'] == 'quarantined_association'
    original['items'][0]['evidence'][0]['quote'] = 'invented source'
    assert normalize_clinical('observations', original, [{'segment_id':'b1','text':quote}])[0] == original


def test_route_is_copied_only_from_explicit_bound_medication_phrase():
    quote = 'Treatment was IV flucloxacillin.'
    value = {'items':[{'name':'IV flucloxacillin','route':None,'evidence':[{'quote':quote,'source_section':'b1'}]}]}
    result, audit = normalize_clinical('medications',value,[{'segment_id':'b1','text':quote}])
    assert result['items'][0]['route'] == 'IV' and result['items'][0]['name'] == 'IV flucloxacillin'
    assert audit and value['items'][0]['route'] is None


def bound_graph():
    source = article(); raw = quote_only_timeline(source)
    kinds = ['history','presentation','test','treatment','adverse_event','treatment','follow_up','other']
    raw['events'] = [{**copy.deepcopy(raw['events'][0]), 'event_id':f'e{i+1}', 'kind':kind}
                     for i,kind in enumerate(kinds)]
    span = raw['events'][0]['evidence'][0]
    raw['edges'] = [{'edge_id':f'edge{i}', 'from_event_id':f'e{i}', 'to_event_id':f'e{i+1}',
        'relation':'before','offset':None,'evidence':[span]} for i in range(1,7)]
    graph, _ = resolve_timeline_spans(raw, 'PMC1.1:jats', source['segments'])
    return source, graph


def test_relative_clinical_sequence_needs_no_dates_and_keeps_disconnected_event_unknown():
    _, graph = bound_graph(); result = relative_order(graph)
    assert ['e1','e7'] in result['before_pairs'] and len(result['before_pairs']) == 21
    assert result['order_unknown_event_ids'] == ['e8']
    assert ['e1','e8'] in result['incomparable_pairs']
    assert result['topological_layers'][0] == [['e1'],['e8']]
    assert result['layers_are_simultaneous'] is False and not result['synthetic_dates_generated']
    assert not any(e['times'] for e in result['events'])
    graph['events'].reverse()
    assert set(map(tuple,relative_order(graph)['before_pairs'])) == set(map(tuple,result['before_pairs']))
    prompt = timeline_messages(article(), {}, 'PMC1.1:p1', {}, refined=True)[-1]['content']
    assert 'relative clinical sequence' in prompt and 'offset=null' in prompt


def test_only_explicit_same_time_edges_make_simultaneous_groups_and_cycles_are_rejected():
    _, graph = bound_graph(); graph['edges'] = [dict(graph['edges'][0], relation='same_time')]
    result = relative_order(graph)
    assert result['same_time_groups'] == [['e1','e2']] and not result['before_pairs']
    graph['edges'].append(dict(graph['edges'][0],edge_id='bad',relation='before'))
    with pytest.raises(ValueError,match='contradictory'): relative_order(graph)


def test_partial_graph_retains_events_and_quarantines_conflicting_edges_or_foreign_patient():
    source, graph = bound_graph(); _, sources = sources_for(source)
    graph['edges'].append({**graph['edges'][0],'edge_id':'reverse','from_event_id':'e7','to_event_id':'e1'})
    graph['events'][-1]['record_id'] = 'PMC1.1:p2'
    partial, quarantined = partial_timeline(graph,sources,{},'PMC1.1:p1')
    assert len(partial['events']) == 7 and partial['edges'] == []
    assert any(q['reason'] == 'combined_graph_conflict' for q in quarantined)
    assert any(q['kind'] == 'event' for q in quarantined)
    canonical, mapping = canonical_timeline_ids(partial)
    assert len(mapping) == 7 and canonical['events'][0]['event_id'] == 'e1'


def test_valid_graph_atoms_are_protected_without_freezing_invalid_edges_or_metadata():
    source, graph = bound_graph(); _, sources = sources_for(source)
    from openpatients2.longitudinal import PatientTimeline, audit_timeline
    def checker(candidate):
        parsed = PatientTimeline.model_validate(candidate)
        if not audit_timeline(parsed,sources,{})['structural_source_gates_passed']: raise ValueError('bad graph')
    graph['edges'][0]['to_event_id'] = 'nonexistent'
    graph['limitations'] = ['untrusted metadata']
    protected = repair_snapshot('timeline_v2',graph,checker)
    assert len(protected['events']) == 8 and len(protected['edges']) == 5
    fixed = copy.deepcopy(graph); fixed['edges'].pop(0); fixed['limitations'] = []
    assert erased(protected, fixed) == []
    fixed['events'][0]['description'] = 'A changed clinical claim'
    assert changed_accepted_atoms('timeline_v2',protected,fixed) == ['/events/0']
    fixed['events'].pop()
    assert erased(protected, fixed)


def test_targeted_batches_rotate_without_mutating_accepted_neighbors():
    plan = ItemRepair.__new__(ItemRepair)
    plan.pending = [{'index':i,'item':{'name':str(i)},'errors':['bad']} for i in range(12)]
    plan.policy = 'source_aware'; plan.visits = {}; plan._active = None
    plan.instruction(); first = [v['index'] for v in plan._active]
    plan.instruction(); second = [v['index'] for v in plan._active]
    assert first == list(range(8)) and second[:4] == [8,9,10,11]
    plan.policy = 'legacy'; plan.instruction()
    assert [v['index'] for v in plan._active] == first


def test_mathml_powers_survive_and_stripped_superscript_citations_stay_empty():
    from openpatients2.articles import _clean
    from openpatients2.pmc_media import parsed_xml
    text = _clean(parsed_xml('<p xmlns:m="http://www.w3.org/1998/Math/MathML">'
        'WBC 40.6×<m:msup><m:mn>10</m:mn><m:mn>9</m:mn></m:msup>/L.'
        '<sup><xref ref-type="bibr">3</xref></sup> Done.</p>'))
    assert text == 'WBC 40.6×10^(9)/L. Done.'


def test_caption_measurements_use_same_preserved_math_notation_as_body():
    from test_articles import XML
    from openpatients2.articles import parse_article
    value = parse_article(XML.replace('Case 1 radiograph.','WBC 40.6×10<sup>9</sup>/L.'),
        {'pmcid':'PMC1','version':1,'license_code':'CC BY-NC-SA','is_pmc_openaccess':True,'is_retracted':False})
    assert value['figures'][0]['caption'] == 'WBC 40.6×10^(9)/L.'
    assert 'WBC 40.6×10^(9)/L.' in value['text']


def test_refined_visuals_hold_ambiguous_labels_and_keep_independently_valid_panels():
    from test_figure_visuals import chart_figure
    from openpatients2.figure_visuals import validate_visuals, partial_visuals
    value = chart_figure(); bad = copy.deepcopy(value['panels'][0]); bad['panel'] = 'b'
    bad['detailed_description'] = 'B? or D? label is visible.'
    value['panels'].append(bad)
    assert validate_visuals(value,{'segments':[]},'F1')
    with pytest.raises(ValueError,match='Ambiguous'): validate_visuals(value,{'segments':[]},'F1',refined=True)
    partial, audit = partial_visuals(value,{'segments':[]},'F1')
    assert [p['panel'] for p in partial['panels']] == ['a']
    assert any(q['kind'] == 'visual_panel' and q['candidate']['panel'] == 'b' for q in audit)
    value['panels'][1]['panel'] = 'a'
    assert partial_visuals(value,{'segments':[]},'F1')[0] is None


def test_cited_cases_are_source_bound_link_candidates_and_excluded_from_primary_roster():
    from test_articles import article as multi_article, roster
    from openpatients2.patient_context import check_patient_roster, discovery_messages
    value = multi_article(); predicted = roster(value)
    predicted['cited_cases'] = [{'label':'Prior published patient', 'species':'human',
        'identity_evidence':[{'segment_id':'b00002','quote':'A woman received 5 mg.'}],
        'source_segment_ids':['b00002'],'origin_reference_ids':[], 'attribution_limitations':['Prior case candidate']}]
    data = check_patient_roster(predicted,value)
    assert len(data['patients']) == 2 and len(data['cited_cases']) == 1
    predicted['cited_cases'][0]['origin_reference_ids'] = ['invented']
    with pytest.raises(ValueError,match='reference'): check_patient_roster(predicted,value)
    assert 'PRIMARY VERSUS CITED CASES' in discovery_messages(value,refined=True)[-1]['content']


async def test_pixel_priority_reaches_late_figure_without_bypassing_rights(tmp_path):
    import httpx
    from test_pilot_media import article as media_article, source, PNG
    from openpatients2.pilot_media import prepare_media
    values = [media_article(1,5),media_article(2,1)]
    values[0]['figures'][4]['rights_statements'] = ['Third party copyright']
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(200,content=PNG))) as client:
        result = await prepare_media(source(tmp_path,values),tmp_path/'media',max_figures=2,http=client,
            priority_figures=[{'article_id':'PMC1.1','figure_id':'F4'}])
    assert result['figures'][0]['figure_id'] == 'F4' and result['figures'][0]['status'] == 'asset_rights_review'
    assert result['figures'][1]['figure_id'] == 'F0' and result['figures'][1]['status'] == 'ready'


