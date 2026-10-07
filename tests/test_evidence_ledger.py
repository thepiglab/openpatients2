"""Source routing and repair invariants; no remote model or clinical truth claims."""
from copy import deepcopy
import json

import pytest

from openpatients2.evidence_ledger import (source_chunks, check_chunk_map, route_segments,
    subset_packet, check_attribute_audit, protect_unchallenged)
from openpatients2.article_tasks import patient_packet
from test_pilot_extract import article, roster, demographics, answer, setup_mock, input_file, run_pilot


def test_chunking_covers_every_character_and_retains_canonical_offsets():
    a={'article_id':'PMC1.1','text_sha256':'hash','segments':[
        {'segment_id':'table','heading':'At 48 h','text':('sodium 130 mmol/L\n'*200)},
        {'segment_id':'p2','heading':'Second patient','text':'Case 2 received a different dose.'}]}
    chunks=source_chunks(a,1024,100)
    for segment in a['segments']:
        seen=set()
        for c in chunks:
            assert sum(len(f['text']) for f in c['fragments'])<=1024
            for f in c['fragments']:
                if f['segment_id']!=segment['segment_id']:continue
                assert f['text']==segment['text'][f['start']:f['end']]
                assert f['heading']==segment['heading']
                seen.update(range(f['start'],f['end']))
        assert seen==set(range(len(segment['text'])))


def map_value(chunk,patient='p1'):
    f=chunk['fragments'][0];q={'segment_id':f['segment_id'],'quote':f['text']}
    return {'chunk_id':chunk['chunk_id'],'reviewed_segment_ids':list(dict.fromkeys(x['segment_id'] for x in chunk['fragments'])),
        'propositions':[{'patient_ids':[patient],'scope':'individual','tasks':['medications'],
            'statement':'Unreviewed source hypothesis','evidence':[q],'attribution_evidence':[q],
            'time_text':None,'assertion_as_documented':None}],'limitations':[]}


@pytest.mark.parametrize('defect',['patient','quote','coverage','task','time','ownership'])
def test_map_rejects_wrong_owner_hallucinated_witness_and_unaccounted_source(defect):
    a=article();r=roster(a);c=source_chunks(a)[0];v=map_value(c)
    if defect=='patient':v['propositions'][0]['patient_ids']=['p99']
    if defect=='quote':v['propositions'][0]['evidence'][0]['quote']='not in source'
    if defect=='coverage':v['reviewed_segment_ids']=[]
    if defect=='task':v['propositions'][0]['tasks']=['invented']
    if defect=='time':v['propositions'][0]['time_text']='three years later'
    if defect=='ownership':v['propositions'][0]['attribution_evidence']=[]
    with pytest.raises(ValueError):check_chunk_map(v,c,r)


def test_fragment_quote_cannot_leak_from_unsupplied_part_of_same_segment():
    a={'article_id':'PMC1.1','text_sha256':'hash','segments':[{'segment_id':'s1','text':'a'*2000+'SECRET'}]}
    c=source_chunks(a,1024,100)[0];v=map_value(c)
    v['propositions'][0]['evidence'][0]['quote']='SECRET'
    with pytest.raises(ValueError):check_chunk_map(v,c,{'patients':[{'patient_id':'p1'}]})


def test_routing_keeps_ambiguity_failure_context_and_safe_empty_domain_fallback():
    a={'segments':[{'segment_id':f's{i}','text':f'original {i}'} for i in range(10)]}
    patient={'patient_id':'p1','identity_evidence':[{'segment_id':'s0','quote':'original 0'}]}
    chunk={'chunk_id':'c1','fragments':[a['segments'][5]]}
    v=map_value(chunk);v['propositions'][0].update(patient_ids=[],scope='unresolved')
    selected,receipt=route_segments(a,patient,'medications',[(chunk,{'status':'valid','data':v})])
    assert selected=={'s0','s1','s4','s5','s6'} # neighbors do not recursively expand
    assert receipt['semantic_routing_verified'] is False
    full,_=route_segments(a,patient,'allergies',[(chunk,{'status':'valid','data':v})])
    assert len(full)==10
    selected,_=route_segments(a,patient,'medications',[(chunk,{'status':'failed','data':None})])
    assert len(selected)==10


def test_subset_packet_preserves_literal_source_and_original_provenance():
    a=article();r=roster(a);packet=patient_packet(a,r,r['patients'][0]);before=deepcopy(packet)
    selected={a['segments'][0]['segment_id']};view=subset_packet(packet,a,selected)
    assert view['article_source']==before['article_source'] and packet==before
    for span in view['packet_spans']:
        segment=next(s for s in a['segments'] if s['segment_id']==span['segment_id'])
        assert view['text'][span['start']:span['end']]==segment['text']


def test_attribute_review_requires_exact_fact_coverage_scalar_pointer_and_witness():
    rows=[{'review_id':'f1','candidate':{'laterality':'bilateral'}}]
    source=[{'segment_id':'s1','text':'Bilateral craniotomy for meningioma.'}]
    v={'checked_fact_ids':['f1'],'challenges':[{'fact_id':'f1','pointer':'/laterality',
        'verdict':'unsupported','reason':'Surgery laterality is not disease laterality.',
        'evidence':[{'segment_id':'s1','quote':source[0]['text']}]}],'limitations':[]}
    assert check_attribute_audit(v,rows,source)==v
    for bad in [dict(v,checked_fact_ids=[]),dict(v,checked_fact_ids=['f1','f1'])]:
        with pytest.raises(ValueError):check_attribute_audit(bad,rows,source)
    bad=deepcopy(v);bad['challenges'][0]['pointer']=''
    with pytest.raises(ValueError):check_attribute_audit(bad,rows,source)


def test_attribute_repair_preserves_unchallenged_measurements_even_if_reordered():
    old={'items':[{'name':'sodium','numeric_value':115,'unit':'mmol/L'}, {'name':'meningioma','laterality':'bilateral'}]}
    rows=[{'review_id':'f1','pointer':'/items/0','candidate':old['items'][0]},
          {'review_id':'f2','pointer':'/items/1','candidate':old['items'][1]}]
    new={'items':[{'name':'meningioma','laterality':'unknown'},old['items'][0]]}
    challenges=[{'fact_id':'f2','pointer':'/laterality'}]
    assert protect_unchallenged(old,new,rows,challenges)==new
    bad=deepcopy(new);bad['items'][1]['numeric_value']=130
    with pytest.raises(ValueError):protect_unchallenged(old,bad,rows,challenges)
    bad=deepcopy(new);bad['items'][0]['name']='glioma'
    with pytest.raises(ValueError):protect_unchallenged(old,bad,rows,challenges)


@pytest.mark.parametrize('pointer', ['/values/-1', '/values/01', '/values/99', '/absent', '/values/0/evidence/0/quote'])
def test_invalid_or_provenance_challenges_are_repairable_validation_errors(pointer):
    rows=[{'review_id':'f1','candidate':{'values':[{'evidence':[{'quote':'source'}]}]}}]
    value={'checked_fact_ids':['f1'],'challenges':[{'fact_id':'f1','pointer':pointer,
        'verdict':'uncertain','reason':'Review this attribute','evidence':[]}],'limitations':[]}
    with pytest.raises(ValueError):check_attribute_audit(value,rows,[])


async def test_new_pipeline_runs_source_map_once_and_exports_ledger_timeline(tmp_path,monkeypatch):
    a=article();calls=[]
    def reply(task,messages):
        calls.append(task)
        if task=='ledger_chunk_map':
            chunk=json.loads(messages[1]['content'].split('SOURCE_CHUNK: ')[1].split('\nSCHEMA:')[0])
            return map_value(chunk)
        if task=='demographics':return demographics(a)
        if task=='ledger_attribute_audit':
            facts=json.loads(messages[1]['content'].split('FACTS_TO_CHALLENGE: ')[1].split('\nSCHEMA:')[0])
            return {'checked_fact_ids':[f['fact_id'] for f in facts],'challenges':[],'limitations':[]}
        if task in {'ledger_events','ledger_edges'}:
            source=json.loads(messages[1]['content'].split('SOURCE_JSON:\n')[1].split('\nTASK:\n')[0])
            delta={'schema_version':'episode-ledger-delta/1','record_id':source['record_id'],
                   'events':[],'fact_links':[],'edges':[],'limitations':[]}
            if task=='ledger_events' and source['fact_registry']:
                q={'segment_id':a['segments'][0]['segment_id'],'quote':'A 42-year-old woman'}
                delta['events']=[{'event_ref':'n1','occurrence_anchor':q,'episode_anchor':None,
                    'kind':'presentation','occurrence':'occurred','description':'Woman presented',
                    'clinical_evidence':[q],'attribution_evidence':[q],'times':[]}]
                delta['fact_links']=[{'event_ref':'n1','fact_alias':f['fact_alias'],
                    'evidence':f['clinical_evidence']} for f in source['fact_registry']]
            return delta
        return answer(task,a)
    http,_=setup_mock(monkeypatch,a,responses=reply)
    async with http:
        report=await run_pilot({'experimental_pipeline':'evidence_ledger','ledger_audit':True},
            input_file(tmp_path,[a]),tmp_path/'run',['http://localhost:8000/v1'],8192,'targeted',http=http)
    assert calls.count('ledger_chunk_map')==1
    assert 'timeline_v2' not in calls and 'ledger_events' in calls and 'ledger_edges' in calls
    assert 'ledger_attribute_audit' in calls and not any(t.startswith('ledger_repair_') for t in calls)
    patient=json.loads((tmp_path/'run/patients.jsonl').read_text())
    graph=patient['companions']['timeline_v2']
    assert graph['events'] and graph['events'][0]['fact_ids']
    assert patient['timeline_audit']['structural_source_gates_passed']
    assert not patient['clinical_accuracy_verified']
    assert report['experimental_pipeline']=='evidence_ledger'
    assert patient['experimental_reviews']['evidence_ledger']['episode_assembly']['failed_delta_calls']==0
