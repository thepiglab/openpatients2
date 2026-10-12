import copy
import json
from pathlib import Path
import time

import httpx
import pytest

from openpatients2.measurements import observation_measurements
from openpatients2.patient_context import isolate_cited_cases,discovery_messages
from openpatients2.pilot_extract import PilotConfig,sources_for,run_pilot as full_run
from openpatients2.pilot_review import (check_inventory,check_claim_audit,check_coverage,
    check_ordering,panel_aligned_attribution)
from test_measurements import extraction
from test_pilot_extract import article,roster,answer,input_file,setup_mock,demographics
from test_refinement import bound_graph

ROOT=Path(__file__).resolve().parents[1]


@pytest.mark.parametrize('number,unit',[(.27,'mg/dL'),(121,'mmol/L'),(6.3,'mmol/L'),(13.1,'cm')])
def test_numeric_only_text_value_binds_unique_literal_unit(number,unit):
    section,segments=extraction(f'Result {number} {unit}.',text_value=str(number),numeric_value=number,unit=unit)
    row=observation_measurements(section,segments)[0]
    assert row['status']=='matched_model_fields'
    assert row['measurement']['unit']==unit
    assert row['measurement']['raw_text'] in segments[0]['text']
    section['items'][0]['unit']='mg/L' if unit!='mg/L' else 'g/L'
    assert observation_measurements(section,segments)[0]['status']=='conflict'


def test_duplicate_numeric_unit_candidates_are_not_guessed():
    section,segments=extraction('Sodium 115 mmol/L; other 115 mg/L.',text_value='115')
    assert observation_measurements(section,segments)[0]['measurement'] is None


def test_optional_bad_cited_case_does_not_erase_primary_patients():
    a=article(); r=roster(a)
    r['cited_cases']=[{'patient_id':'c1','label':'prior patient','species':'human',
        'species_as_documented':'woman','identity_evidence':[{'segment_id':a['segments'][0]['segment_id'],'quote':'fabricated'}],
        'source_segment_ids':[a['segments'][0]['segment_id']], 'origin_reference_ids':[], 'limitations':[]}]
    checked,q=isolate_cited_cases(r,a)
    assert checked['patients']==r['patients'] and checked['cited_cases']==[] and len(q)==1
    r['patients'][0]['identity_evidence'][0]['quote']='fabricated primary'
    with pytest.raises(ValueError): isolate_cited_cases(r,a)


def test_discovery_exposes_markers_without_rewriting_evidence():
    a=article();a['segments'][0]['cross_references']=[{'rid':'ref1'}]
    a['segments'][0]['text_with_reference_markers']='Original [ref1]'
    msg=discovery_messages(a,refined=True,isolated=True)[1]['content']
    payload=json.loads(msg.split('SOURCE_JSON:\n')[1].split('\nTASK:\n')[0])
    assert payload['segments'][0]['text']==a['segments'][0]['text']
    assert payload['segments'][0]['cross_references']==[{'rid':'ref1'}]


def test_order_review_only_changes_edges_and_rejects_cycle_and_foreign_event():
    a,g=bound_graph(); _,sources=sources_for(a)
    v={'record_id':g['record_id'],'edges':g['edges'],'limitations':[]}
    assert check_ordering(v,g,sources,{})['edges']==g['edges']
    v['edges'].append({**g['edges'][0],'edge_id':'loop','from_event_id':'e7','to_event_id':'e1'})
    with pytest.raises(ValueError): check_ordering(v,g,sources,{})
    v['edges']=[{**g['edges'][0],'to_event_id':'foreign'}]
    with pytest.raises(ValueError): check_ordering(v,g,sources,{})
    v['edges']=[];v['events']=g['events']
    with pytest.raises(ValueError): check_ordering(v,g,sources,{})


def test_inventory_and_audits_require_literal_evidence_and_complete_registries():
    a=article();quote=a['segments'][0]['text'];sid=a['segments'][0]['segment_id']
    inventory={'features':[{'task':'medications','feature_as_documented':'5 mg', 'state':'present',
        'episode_as_documented':None,'evidence':[{'segment_id':sid,'quote':quote}]}],'limitations':[]}
    assert check_inventory(inventory,a)==inventory
    inventory['features'][0]['episode_as_documented']='day 4'
    with pytest.raises(ValueError):check_inventory(inventory,a)
    facts=[{'review_id':'fact1','task':'medications'}]
    claim={'decisions':[{'fact_id':'fact1','decision':'supported','rationale':'source',
        'evidence':[{'segment_id':sid,'quote':quote}]}],'possible_missing_facts':[],'limitations':[]}
    assert check_claim_audit(claim,a,facts)==claim
    claim['decisions']=[]
    with pytest.raises(ValueError):check_claim_audit(claim,a,facts)
    coverage={'decisions':[{'inventory_index':0,'status':'represented','fact_ids':['fact1'],'rationale':'same quantity'}],'limitations':[]}
    features=[{'inventory_index':0,'task':'medications'}]
    assert check_coverage(coverage,features,facts)==coverage
    coverage['decisions'][0]['fact_ids']=['wrong_patient_fact']
    with pytest.raises(ValueError):check_coverage(coverage,features,facts)


def test_composite_panel_attribution_requires_each_pixel_panel():
    a=article();r=roster(a)
    visual={'panels':[{'panel':'left'},{'panel':'top right'},{'panel':'bottom right'}]}
    attribution=answer('pixel_attribution',a)
    with pytest.raises(ValueError,match='panel'):panel_aligned_attribution(attribution,visual,a,r,'F1')
    attribution['assignments']=[{**attribution['assignments'][0],'panel':p['panel']} for p in visual['panels']]
    assert panel_aligned_attribution(attribution,visual,a,r,'F1')


async def test_full_audits_leave_clinical_facts_immutable_and_index_unknown_order(tmp_path,monkeypatch):
    a=article()
    def reply(task,messages):
        if task=='demographics':
            d=demographics(a);d['items']=d['items'][:1];return d
        if task=='clinical_inventory':return {'features':[],'limitations':[]}
        if task=='claim_audit':
            payload=json.JSONDecoder().raw_decode(messages[1]['content'])[0]
            return {'decisions':[{'fact_id':r['fact_id'],'decision':'uncertain','rationale':'mock',
                'evidence':[]} for r in payload['facts']], 'possible_missing_facts':[],'limitations':[]}
        if task=='ordering_review':return {'record_id':'PMC1.1:p1','edges':[],'limitations':[]}
        return answer(task,a)
    http,_=setup_mock(monkeypatch,a,responses=reply)
    async with http:
        r=await full_run({'max_output_tokens':256,'max_retry_tokens':256,
            'isolate_secondary_cases':True,'inventory_clinical_features':True,'audit_claims':True,'review_ordering':True},
            input_file(tmp_path,[a]),tmp_path/'run',['http://localhost:8000/v1'],8192,'targeted',http=http)
    p=json.loads(Path(r['outputs']['patients']).read_text())
    assert p['sections']['demographics']['items'][0]['numeric_value']==42
    assert p['experimental_reviews']['claim_audits'][0]['data']['decisions'][0]['decision']=='uncertain'
    assert p['patient_state_index']['facts'][0]['time_association']=='unknown'
    assert r['outputs']['predicted_rosters'] and r['task_statuses_by_domain']['claim_audit']['valid']==1
    assert p['clinical_accuracy_verified'] is False


async def test_cached_generated_rosters_do_not_repeat_discovery_or_claim_manual_gold(tmp_path,monkeypatch):
    a=article();cached=tmp_path/'cached.json'
    cached.write_text(json.dumps({'schema_version':'predicted-rosters/1','articles':[{
        'article_id':a['article_id'],'text_sha256':a['text_sha256'],'status':'valid','roster':roster(a)}]}))
    http,events=setup_mock(monkeypatch,a)
    async with http:
        r=await full_run({'max_output_tokens':256,'max_retry_tokens':256},input_file(tmp_path,[a]),
            tmp_path/'run',['http://localhost:8000/v1'],8192,'targeted',http=http,cached_rosters=cached)
    assert 'roster' not in {e[1] for e in events if e[0]=='complete'}
    assert r['conditioned_on_cached_generated_rosters'] and not r['conditioned_on_frozen_rosters']


def test_real_gepa_calls_adapter_and_keeps_validation_candidates(tmp_path):
    import gepa
    from gepa.core.adapter import EvaluationBatch
    class Adapter:
        def evaluate(self,batch,candidate,capture_traces=False):
            score=float(candidate['observations']=='Preserve specimen and unit.')
            return EvaluationBatch(outputs=[candidate for _ in batch],scores=[score for _ in batch],
                trajectories=[{'Feedback':'Missing specimen and unit'} for _ in batch] if capture_traces else None)
        def make_reflective_dataset(self,candidate,eval_batch,components_to_update):
            return {k:eval_batch.trajectories for k in components_to_update}
        def propose_new_texts(self,candidate,reflective_dataset,components_to_update):
            return {'observations':'Preserve specimen and unit.'}
    result=gepa.optimize(seed_candidate={'observations':''},trainset=['a','b'],valset=['c','d'],
        adapter=Adapter(),max_metric_calls=12,reflection_minibatch_size=2,seed=42,run_dir=str(tmp_path/'gepa'))
    assert max(result.val_aggregate_scores)==1 and len(result.candidates)>=2
    assert result.total_metric_calls>=6


@pytest.mark.parametrize('unit,status',[('10^4/mL','matched_model_fields'),('/mL','conflict'),('10^9/mL','conflict')])
def test_numeric_mantissa_only_retains_source_exponent_and_checks_model_scale(unit,status):
    section,segments=extraction('BAL WBC 1.6×10^(4)/mL.',name='BAL WBC',text_value='1.6',numeric_value=1.6,unit=unit)
    row=observation_measurements(section,segments)[0]
    from decimal import Decimal
    assert Decimal(row['measurement']['magnitude'])==Decimal('16000')
    assert row['measurement']['power_of_ten']==4 and row['status']==status


async def test_coverage_backfill_adds_supported_fact_and_preserves_first_attempt(tmp_path,monkeypatch):
    a=article();sid=a['segments'][0]['segment_id'];quote='A 42-year-old woman'
    def reply(task,messages):
        if task=='clinical_inventory':return {'features':[{'task':'demographics',
            'feature_as_documented':'Age 42 years','state':'present','episode_as_documented':None,
            'evidence':[{'segment_id':sid,'quote':quote}]}],'limitations':[]}
        if task=='coverage_audit':return {'decisions':[{'inventory_index':0,'status':'missing','fact_ids':[],
            'rationale':'Age omitted'}],'limitations':[]}
        if task=='coverage_repair_demographics':
            d=demographics(a);d['items']=d['items'][:1];return d
        return answer(task,a)
    http,_=setup_mock(monkeypatch,a,responses=reply)
    async with http:
        r=await full_run({'max_output_tokens':256,'max_retry_tokens':256,
            'inventory_clinical_features':True,'coverage_repair':True},input_file(tmp_path,[a]),
            tmp_path/'run',['http://localhost:8000/v1'],8192,'targeted',http=http)
    p=json.loads(Path(r['outputs']['patients']).read_text())
    assert p['sections']['demographics']['items'][0]['numeric_value']==42
    saved=[json.loads(x.read_text()) for x in (tmp_path/'run/tasks').glob('*.json')]
    original=next(x for x in saved if x['task']=='demographics')
    assert original['data']['items']==[]
    assert next(x for x in saved if x['task']=='coverage_repair_demographics')['status']=='valid'
    assert p['experimental_reviews']['source_grounded_backfill_requested']
    assert p['patient_state_index']['facts'][0]['time_association']=='unknown'


def test_tp2_token_rates_use_all_eight_physical_gpus(tmp_path):
    from openpatients2.pilot_extract import PilotRunner
    r=PilotRunner({'gpus_per_endpoint':2},tmp_path/'runner',
        [f'http://localhost:{8000+i}/v1' for i in range(4)],65536,'targeted')
    report=r.token_report([],10)
    assert report['all_gpu_count']==8 and report['output_tokens_per_gpu_second']==0
    import asyncio
    asyncio.run(r.close())
