import copy
import json
from pathlib import Path
import time

import httpx
import pytest

from openpatients2.corpus_pilot import prepare,job_command,trial_warmup
from openpatients2.measurements import observation_measurements
from openpatients2.patient_context import isolate_cited_cases,discovery_messages
from openpatients2.pilot_extract import PilotConfig,sources_for,run_pilot as full_run
from openpatients2.pilot_review import (check_inventory,check_claim_audit,check_coverage,
    check_ordering,panel_aligned_attribution)
from openpatients2.prompt_optimization import split_articles,reward,prompt_guard,ClinicalAdapter
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


def test_gepa_reward_penalizes_missing_and_forbidden_without_counting_evidence_text():
    required={'id':'r','record_id':'case','task':'observations','kind':'required','collection':'items',
        'pattern':{'name':'Sodium','numeric_value':115},'description':'source sodium','source':[]}
    forbidden={**required,'id':'b','kind':'forbidden','pattern':{'numeric_value':135}}
    assert reward('observations',{'items':[{'name':'Sodium','numeric_value':115}]},'valid',[required,forbidden])[0]==1
    assert reward('observations',{'items':[{'name':'Sodium','numeric_value':135}]},'valid',[required,forbidden])[0]==0
    assert reward('observations',{'items':[{'evidence':{'name':'Sodium','numeric_value':115}}]},'valid',[required])[0]==.2
    assert reward('summary',{},'valid',[])[1]['reward_basis']=='structural_only'


def test_gepa_splits_and_prompt_guard():
    split=split_articles([f'PMC{i}' for i in range(20)])
    assert not set(split['train']) & set(split['test'])
    assert not set(split['validation']) & set(split['test'])
    assert sum(len(split[k]) for k in ('train','validation','test'))==20
    with pytest.raises(ValueError):prompt_guard('Always output facts for PMC13587181',[])
    with pytest.raises(ValueError):prompt_guard('x'*6001,[])


def test_overnight_plan_requests_cpu_downloads_and_one_eight_gpu_node(tmp_path):
    c=prepare(ROOT,tmp_path/'campaign',ROOT/'configs/pilot/overnight.yaml')
    assert len(c['config']['variants'])==15 and len(c['config']['matrix'])==5
    assert c['config']['holdout_sample_size']==48
    assert '--gres=none' in job_command(c,'cpu')
    assert '--gres=gpu:b200:8' in job_command(c,'gpu') and '--nodes=1' in job_command(c,'gpu')
    assert '--time=12:00:00' in job_command(c,'gpu')
    assert c['config']['gpu_budget_seconds']==36000
    strengths={v['overrides'].get('reasoning_strength','medium') for v in c['config']['variants']}
    assert strengths=={'low','medium','high','xhigh'}


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


def test_real_gepa_clinical_adapter_improves_gold_and_never_reflects_test_labels(tmp_path,monkeypatch):
    import gzip
    from openpatients2.article_tasks import patient_packet
    from openpatients2.client import APIClient,Completion
    from openpatients2.prompts import messages_for
    from openpatients2.prompt_optimization import optimize_prompts
    from openpatients2.pilot_extract import PilotRunner
    articles=[]; patients=[]; tasks=[]; references=[]
    for i in range(1,9):
        a=article();a['article_id']=f'PMC{i}.1';a['pmcid']=f'PMC{i}'
        r=roster(a);packet=patient_packet(a,r,r['patients'][0])
        articles.append(a);patients.append({'source':packet})
        request=messages_for(packet,'demographics')
        tasks.append({'task':'demographics','identity':{'article_id':a['article_id'],
            'record_id':packet['record_id'],'patient_id':'p1'},'status':'valid',
            'attempts':[{'request':request,'errors':[]}]})
        references.append({'id':f'r{i}','record_id':packet['record_id'],'task':'demographics','kind':'required',
            'collection':'items','pattern':{'attribute':'age_at_presentation','numeric_value':42},
            'description':'Age 42','source':[]})
    sample=tmp_path/'sample.jsonl.gz'
    with gzip.open(sample,'wt') as out:
        for a in articles:out.write(json.dumps(a)+'\n')
    trial=tmp_path/'trial';(trial/'tasks').mkdir(parents=True)
    (trial/'review').mkdir()
    (trial/'patients.jsonl').write_text(''.join(json.dumps(p)+'\n' for p in patients))
    (trial/'rosters.json').write_text(json.dumps({'articles':[{'article_id':a['article_id'],
        'status':'valid','roster':roster(a)} for a in articles]}))
    for i,r in enumerate(tasks):(trial/'tasks'/f'{i}.json').write_text(json.dumps(r))
    reference=tmp_path/'reference.json';reference.write_text(json.dumps({'checks':references}))
    reflections=[]
    async def count(self,client,messages):return 10,'mock_exact',8192
    async def complete(self,endpoint,task,messages,cap):
        if task=='summary':
            reflections.append(messages)
            value={'instructions':'Preserve exact age.'}
        else:
            value=answer('demographics',articles[0])
            if any(m.get('content')=='ADDITIONAL TASK INSTRUCTIONS:\nPreserve exact age.' for m in messages):
                value=demographics(articles[0]);value['items']=value['items'][:1]
        return Completion(content=json.dumps(value),finish_reason='stop',prompt_tokens=10,completion_tokens=20,
            reasoning_tokens=0,latency_seconds=.01)
    monkeypatch.setattr(PilotRunner,'token_count',count)
    monkeypatch.setattr(APIClient,'complete_once',complete)
    winners,report=optimize_prompts(sample,[trial],tmp_path/'optimization',
        {'max_output_tokens':256,'max_retry_tokens':256},['http://localhost:8000/v1'],8192,reference,calls=24,seconds=30)
    assert winners['demographics']=='Preserve exact age.'
    score=next(r for r in report['components'] if r['component']=='demographics')
    assert min(score['scores'])==.2 and max(score['scores'])==1
    for aid in report['splits']['test']:
        assert aid not in json.dumps(reflections)
    assert report['automatic_production_promotion'] is False
    # Simulate termination after GEPA saved engine state but before the family
    # was recorded as complete. Resume the actual engine, retaining old traces.
    optimization=tmp_path/'optimization'
    components=json.loads((optimization/'components.json').read_text())
    next(c for c in components if c['component']=='demographics')['status']='failed'
    (optimization/'components.json').write_text(json.dumps(components))
    preserved={p:p.read_bytes() for p in (optimization/'demographics').glob('rollout-*/attempts.jsonl')}
    resumed,resume_report=optimize_prompts(sample,[trial],optimization,
        {'max_output_tokens':256,'max_retry_tokens':256},['http://localhost:8000/v1'],8192,reference,
        calls=48,seconds=30,resume=True)
    row=next(c for c in resume_report['components'] if c['component']=='demographics')
    assert row['resumed_engine_checkpoint'] and resumed['demographics']=='Preserve exact age.'
    assert preserved and all(p.read_bytes()==data for p,data in preserved.items())


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


@pytest.mark.parametrize('body,expected',[({'success':False},False),({'success':True},True),(True,True)])
async def test_cache_reset_receipt_requires_server_success_not_just_http_200(monkeypatch,body,expected):
    original=httpx.AsyncClient
    def handle(req):
        if req.url.path=='/reset_prefix_cache':return httpx.Response(200,json=body)
        assert json.loads(req.content)['chat_template_kwargs']['reasoning_strength']=='high'
        return httpx.Response(200,json={'choices':[]})
    monkeypatch.setattr(httpx,'AsyncClient',lambda **kwargs:original(transport=httpx.MockTransport(handle),**kwargs))
    result=await trial_warmup(['http://localhost:8000/v1'],{'served_model':'clinical-extractor','reasoning_strength':'high'},42)
    assert result['controlled_prefix_reset'] is expected and result['all_replicas_warmed']


def test_comparison_distinguishes_missing_gold_and_optimization_coverage(tmp_path):
    from openpatients2.overnight import write_comparison
    (tmp_path/'gpu.json').write_text(json.dumps({'deferred':[{'reason':'time'}]}))
    result={'gpu':'partial','cleanup':'deleted','source_cleanup':'deleted','cells':[{
        'cell':{'context':65536,'prefill':32768,'tensor_parallel':2,'speculation':'dflash'},
        'arm':'baseline','source_set':'new-sources','report':{'tokens':{},
        'task_statuses_by_domain':{'clinical':{'valid':3,'partial':1,'failed':2}}}}]}
    info=write_comparison({'work':str(tmp_path)},result)
    text=(tmp_path/'COMPARISON.md').read_text()
    assert info['status']=='missing' and 'unadjudicated' in text
    assert '3 / 1 / 2' in text and 'Deferred trials/layouts: 1' in text


def test_tp2_token_rates_use_all_eight_physical_gpus(tmp_path):
    from openpatients2.pilot_extract import PilotRunner
    r=PilotRunner({'gpus_per_endpoint':2},tmp_path/'runner',
        [f'http://localhost:{8000+i}/v1' for i in range(4)],65536,'targeted')
    report=r.token_report([],10)
    assert report['all_gpu_count']==8 and report['output_tokens_per_gpu_second']==0
    import asyncio
    asyncio.run(r.close())
