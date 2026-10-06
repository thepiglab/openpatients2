"""Regressions from the October 5 campaign; no remote inference/downloads."""
import asyncio
from copy import deepcopy
import importlib.util
import json
from pathlib import Path
import time
from types import SimpleNamespace

import pytest

from openpatients2.bundle_optimization import BundleAdapter, ComponentBatchSampler, GroupedComponents, compact_reflection
from openpatients2.bundle_quality import score_bundle, validate_bundle_gold
from openpatients2.data import read_jsonl
from openpatients2.fidelity import matches
from openpatients2.pilot_extract import PilotRunner
from openpatients2.pilot_review import check_coverage
from openpatients2.prompt_payloads import compact_facts, timeline_retention
from openpatients2.targeted_repair import ItemRepair
from openpatients2.validation import validate
from test_pilot_extract import article, answer, demographics, setup_mock, input_file, run_pilot
from test_pilot_correctness import quote_only_timeline

ROOT = Path(__file__).resolve().parents[1]


async def test_coverage_alias_replay_uses_real_item_repair_and_keeps_retry_budget(tmp_path,monkeypatch,sections,row):
    bad = deepcopy(sections['observations']); good = deepcopy(bad)
    bad['items'] = bad['items'][:1]; good['items'] = good['items'][:1]
    bad['items'][0]['evidence'][0]['quote'] = 'fabricated quotation'
    http, events = setup_mock(monkeypatch,article(),responses=lambda *args:good)
    runner = PilotRunner({'max_output_tokens':256,'max_retry_tokens':256,'max_repair_rounds':2},
        tmp_path/'replay',['http://localhost:8000/v1'],8192,'targeted',http=http)
    def checker(value):
        checked = validate('observations',value,row['text'])
        if not checked.valid: raise ValueError(';'.join(checked.errors))
        return checked.data
    async with http:
        result = await runner.call('coverage_repair_observations',[{'role':'user','content':row['text']}],
            checker,{'article_id':'PMC1.1'},0,[],packet={'text':row['text']},
            repair_from={'raw_candidate':bad,'errors':['bad quote']})
    await runner.close()
    assert result['status']=='valid' and result['task']=='coverage_repair_observations'
    assert result['attempts'][0]['mode']=='repair' and result['attempts'][0]['item_repair']
    assert len([e for e in events if e[0]=='complete'])==1
    assert result['data']['items'][0]['numeric_value']==good['items'][0]['numeric_value']
    assert ItemRepair.create('unknown_auxiliary',bad,row['text'],[]) is None


def test_compaction_preserves_zero_negative_values_and_ids_without_evidence_duplication():
    rows=[{'review_id':'a','task':'observations','pointer':'/items/0','candidate':{
        'numeric_value':0,'assertion':'absent','unit':'mg/L','time':{'text':'day 2','evidence':[{'quote':'huge'}]},
        'evidence':[{'source_section':'s1','quote':'huge'}]},'evidence_spans':[{'segment_id':'s1','quote':'huge'}],
        'duplicated_source':'x'*100000}]
    before=deepcopy(rows); result=compact_facts(rows)
    assert result[0]['fact_id']=='a' and result[0]['source_segment_ids']==['s1']
    assert result[0]['value']=={'numeric_value':0,'assertion':'absent','unit':'mg/L','time':{'text':'day 2'}}
    assert len(json.dumps(result))<500 and rows==before


async def test_token_counted_completion_batches_keep_source_and_every_hint(tmp_path,monkeypatch):
    a=article(); hints=[]; sources=[]
    def reply(task,messages):
        if task=='demographics':
            value=demographics(a)
            value['items'][1]['evidence'][0]['quote']='A 42-year-old woman'
            return value
        if task=='summary_completion':
            sources.append(messages[1]['content'])
            hints.extend(json.loads(messages[-1]['content'].split('DELIVERED_FACTS:\n')[1]))
        return answer('summary' if task=='summary_completion' else task,a)
    http,_=setup_mock(monkeypatch,a,responses=reply)
    original=PilotRunner.token_count
    async def counted(self,client,messages):
        _,mode,maximum=await original(self,client,messages)
        texts=[m['content'] for m in messages if isinstance(m.get('content'),str)]
        tail=next((t.split('DELIVERED_FACTS:\n')[1] for t in texts if 'DELIVERED_FACTS:\n' in t),None)
        return (8000 if tail and len(json.loads(tail))>1 else 10),mode,maximum
    monkeypatch.setattr(PilotRunner,'token_count',counted)
    async with http:
        report=await run_pilot({'summary_completion':True},input_file(tmp_path,[a]),tmp_path/'run',
            ['http://localhost:8000/v1'],8192,'targeted',http=http)
    assert report['task_statuses_by_domain']['summary_completion']['valid']>=2
    assert len(hints)==len({h['fact_id'] for h in hints})>=2
    assert all(json.dumps(a['segments'][0]['text']) in text for text in sources)
    assert all(h['semantic_review']=='unreviewed' for h in hints)


async def test_completion_cannot_erase_an_unlinked_source_gated_event(tmp_path,monkeypatch):
    a=article()
    def reply(task,messages):
        return quote_only_timeline(a) if task=='timeline_v2' else answer(
            'timeline_v2' if task=='timeline_completion' else task,a)
    http,_=setup_mock(monkeypatch,a,responses=reply)
    async with http:
        report=await run_pilot({'timeline_completion':True},input_file(tmp_path,[a]),tmp_path/'run',
            ['http://localhost:8000/v1'],8192,'targeted',http=http)
    saved=json.loads((tmp_path/'run/patients.jsonl').read_text())
    assert saved['companions']['timeline_v2']['events'][0]['description']=='Received a documented dose'
    assert report['task_statuses_by_domain']['timeline_completion']=={'failed':1}


def test_linked_event_can_split_distinct_encounters_but_links_cannot_disappear():
    old={'events':[{'fact_ids':['dose2023','dose2024'],'description':'Merged administrations'}]}
    new={'events':[{'fact_ids':['dose2023'],'description':'First administration'},
                   {'fact_ids':['dose2024'],'description':'Second administration'}]}
    assert timeline_retention(old,new)==new
    with pytest.raises(ValueError,match='erased existing supported fact links'):
        timeline_retention(old,{'events':new['events'][:1]})


def test_cross_domain_coverage_is_uncertain_without_hiding_other_decisions():
    rows=[{'review_id':'marker','task':'oncology'},{'review_id':'lab','task':'observations'}]
    features=[{'inventory_index':0,'task':'observations'},{'inventory_index':1,'task':'observations'}]
    candidate={'decisions':[{'inventory_index':i,'status':'represented','fact_ids':[f],
        'rationale':'Compare source'} for i,f in enumerate(['marker','lab'])],'limitations':[]}
    result=check_coverage(candidate,features,rows)
    assert [d['status'] for d in result['decisions']]==['uncertain','represented']
    assert candidate['decisions'][0]['status']=='represented'
    candidate['decisions'][0]['fact_ids']=['another_patient']
    with pytest.raises(ValueError):check_coverage(candidate,features,rows)


def test_reviewed_gold_preserves_historical_scores_and_binds_equivalences_to_patient_marker():
    spec=importlib.util.spec_from_file_location('gold_review',ROOT/'scripts/review_bundle_reference.py')
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    generated=module.reviewed_reference(ROOT)
    reviewed=json.loads((ROOT/'benchmarks/patient-bundle/reference-reviewed.json').read_text())
    original=json.loads((ROOT/'benchmarks/patient-bundle/reference.json').read_text())
    assert generated==reviewed and len(original['checks'])==305
    assert validate_bundle_gold(reviewed,list(read_jsonl(ROOT/'benchmarks/patient-bundle/articles.jsonl.gz')))
    checks={c['id']:c for c in reviewed['checks']}
    pattern=checks['C0163']['pattern']
    assert matches(pattern,{'subject':'index_patient','name':'CD34','interpretation':'positive','text_value':None})
    assert not matches(pattern,{'subject':'index_patient','name':'STAT6','interpretation':'positive'})
    assert not matches(pattern,{'subject':'family_member','name':'CD34','interpretation':'positive'})
    assert checks['B0071']['pattern']['assertion']=='possible'
    assert matches(checks['C0166']['pattern']['name'],'SFT')
    assert 'R20261006-laterality' in checks
    disputed=next(a for a in reviewed['articles'] if a['article_id']=='PMC13575834.1')
    assert disputed['evaluation_status']=='unadjudicated'
    score=score_bundle({'checks':[],'articles':[disputed]},[])
    assert score['identities'][0]['excluded_from_identity_score']
    assert 'identity' not in score['parts']
    # Neither including nor excluding an unadjudicated patient can incur a
    # hidden identity penalty against independently labelled negative cases.
    negative={'article_id':'PMC1.1','text_sha256':'hash','expected_count':0,'species':[],'identities':[]}
    discovery={'articles':[{'article_id':'PMC1.1','text_sha256':'hash','status':'valid','roster':{'patients':[]}}]}
    ref={'checks':[],'articles':[negative,disputed]}
    provisional={'source':{'article_source':{'article_id':disputed['article_id']}}}
    assert score_bundle(ref,[provisional],discovery=discovery)['score']==1.


def test_group_batches_have_relevant_positive_cases_and_no_withheld_labels():
    from gepa.core.data_loader import ListDataLoader
    selector=GroupedComponents(['observations','medications'])
    ref={'checks':[{'record_id':'pos:p1','task':'observations','kind':'required'},
                   {'record_id':'neg:p1','task':'observations','kind':'forbidden'}]}
    sampler=ComponentBatchSampler(selector,ref)
    loader=ListDataLoader(['aggregate','neg','pos'])
    ids=sampler.next_minibatch_ids(loader,SimpleNamespace(i=0))
    assert 'pos' in loader.fetch(ids) and 'aggregate' not in loader.fetch(ids)
    assert sampler.receipts[0]['components']==['observations']


def test_bundle_rollout_timeout_is_inside_operation_and_not_cached(tmp_path,monkeypatch):
    import openpatients2.bundle_optimization as module
    async def slow(*args,**kwargs):await asyncio.sleep(.2)
    monkeypatch.setattr(module,'run_pilot',slow)
    a=article(); adapter=BundleAdapter([a],tmp_path/'sample',{},['endpoint'],65536,tmp_path,
        {'checks':[]},time.monotonic()+.02)
    start=time.monotonic();evaluation=adapter.evaluate([a['article_id']],{'summary':''})
    assert time.monotonic()-start<.15 and evaluation.outputs==[None]
    assert adapter.timeouts==1 and not adapter.evaluation_cache


def test_missing_fresh_confirmation_is_partial_and_unavailable_not_medical_zero(tmp_path,monkeypatch):
    import gepa
    import openpatients2.bundle_optimization as module
    a=article(); reference={'optimization_splits':{'train':[a['article_id']],
        'validation':[a['article_id']],'test':[]},'checks':[],'articles':[]}
    sample=input_file(tmp_path,[a]); path=tmp_path/'ref.json';path.write_text(json.dumps(reference))
    monkeypatch.setattr(module,'validate_bundle_gold',lambda *args:True)
    original=module.BundleAdapter.evaluate;calls=[]
    def evaluate(self,batch,candidate,capture_traces=False):
        from gepa.core.adapter import EvaluationBatch
        calls.append((self.deadline,self.cache_enabled))
        quality={'score':.8,'clinical':{'matched':3,'unscorable':0,'forbidden_violations':0,'forbidden_unscorable':0}}
        outputs=[quality] if len(calls)<3 else [None]
        return EvaluationBatch(outputs=outputs,scores=[.8] if len(calls)<3 else [0.])
    monkeypatch.setattr(module.BundleAdapter,'evaluate',evaluate)
    def optimize(**kwargs):
        assert not any(kwargs['seed_candidate'].values())
        assert kwargs['adapter'].deadline<calls[0][0]
        assert kwargs['cache_evaluation'] is True
        assert isinstance(kwargs['batch_sampler'],ComponentBatchSampler)
        return SimpleNamespace(candidates=[kwargs['seed_candidate'],dict.fromkeys(module.COMPONENTS,'New strategy')],
            val_aggregate_scores=[.8,.9],total_metric_calls=2)
    monkeypatch.setattr(gepa,'optimize',optimize)
    prompts,report=module.optimize_bundle(sample,tmp_path/'out',{},['endpoint'],65536,path,
        {'observations':'Weaker independent rewrite'},seconds=30,calls=8)
    assert not any(prompts.values()) and report['status']=='partial'
    assert report['confirmation_score'] is None and report['confirmation_validation_facts'] is None
    assert not report['clinical_nonregression_gate_passed'] and report['original_is_frontier_seed']
    assert calls[-1][1] is False  # fresh inference, not search cache


def test_reflection_does_not_copy_whole_bundle_evidence_or_unrelated_strategies():
    trace={'Feedback':{'invoked_strategies':{'summary':'Actual strategy','medications':'irrelevant'},
        'source_checked_course':{'timelines':[]},'missing_source_propositions':[],
        'pixels':[{'text':'x'*100000}],'validation_errors':['bad quote']}}
    result=compact_reflection({'summary':[trace]},['summary'])
    encoded=json.dumps(result)
    assert 'Actual strategy' in encoded and 'irrelevant' not in encoded and len(encoded)<1000


@pytest.mark.parametrize('fails',[False,True])
async def test_joint_search_reuses_one_gpu_stage_and_records_original_fallback(tmp_path,monkeypatch,fails):
    import openpatients2.bundle_optimization as module
    from openpatients2.corpus_pilot import optimize_joint_program
    seen=[]
    def optimize(sample,destination,config,endpoints,context,reference,independent,**options):
        seen.append((endpoints,context,independent,options))
        if fails: raise ValueError('Optimization failed')
        return {'summary':'Source-grounded strategy'},{'status':'completed','selected_original':False}
    monkeypatch.setattr(module,'optimize_bundle',optimize)
    campaign={'work':str(tmp_path),'config':{'fidelity_reference':'gold.json',
        'joint_gepa_seconds':5400,'joint_gepa_calls':512}}
    report=await optimize_joint_program(campaign,{},['one_endpoint'],65536,{'summary':'Independent'})
    folder=tmp_path/'prompt-optimization/joint-program'
    selected=json.loads((folder/'best-prompts.json').read_text())
    assert report['allocated_gpus']==1 and seen[0][0]==['one_endpoint']
    assert seen[0][-1]=={'seconds':5400,'calls':512}
    assert json.loads((tmp_path/'progress.json').read_text())['allocated_gpus']==1
    if fails:
        assert report['status']=='failed' and report['confirmation_score'] is None
        assert report['selected_original'] and not any(selected.values())
    else: assert selected=={'summary':'Source-grounded strategy'}


async def test_missing_joint_receipts_stop_before_eight_gpu_start(tmp_path,monkeypatch):
    import openpatients2.corpus_pilot as module
    monkeypatch.setattr(module,'verify_cpu',lambda campaign:None)
    folder=tmp_path/'prompt-optimization';folder.mkdir()
    for name in ['report.json','best-prompts.json']:(folder/name).write_text('{}')
    campaign={'work':str(tmp_path),'config':{'separate_gepa':True,'joint_gepa_in_separate_stage':True}}
    with pytest.raises(ValueError,match='Joint GEPA stage did not produce final receipts'):
        await module.gpu(campaign)


async def test_source_license_is_rechecked_and_acquisition_receipt_retained(tmp_path,monkeypatch):
    a=article(); original=deepcopy(a['license']);a['license']['jurisdictions']=['This']
    http,_=setup_mock(monkeypatch,a)
    async with http:
        report=await run_pilot({},input_file(tmp_path,[a]),tmp_path/'run',
            ['http://localhost:8000/v1'],8192,'targeted',http=http)
    row=report['articles'][0]
    bundle=json.loads((tmp_path/'run/patients.jsonl').read_text())
    assert 'This' not in row['source_license']['jurisdictions']
    assert row['acquisition_license']['jurisdictions']==['This']
    assert row['source_license']['allowed'] and row['source_license']['code']==original['code']
    assert bundle['source']['article_source']['license']==row['source_license']
