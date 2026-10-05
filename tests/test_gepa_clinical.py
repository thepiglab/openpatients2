import copy
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import sys
import threading
import time

import pytest

from openpatients2.gepa_feedback import FamilyLogger, feedback_checker, combine_reward, select_examples
from openpatients2.pilot_recovery import restore_article, bootstrap_paths
from openpatients2.pilot_extract import PilotConfig, PilotRunner, source_gate
from openpatients2.pilot_review import check_inventory, check_claim_audit
from openpatients2.targeted_repair import repair_snapshot, erased
from openpatients2.corpus_pilot import prepare, job_command
from openpatients2.overnight import heldout_summary
from test_pilot_extract import article, setup_mock

ROOT=Path(__file__).resolve().parents[1]


def test_legacy_source_recovery_reconstructs_exact_text_and_marks_unknown_supplements():
    a=article()
    packet={k:v for k,v in a.items() if k not in {'text','has_body_text','supplements','status'}}
    restored=restore_article(packet)
    assert restored['text']==a['text'] and not source_gate(restored,PilotConfig())
    assert restored['supplementary_manifest_status']=='unavailable_in_legacy_snapshot'
    from openpatients2.article_tasks import patient_packet
    from test_pilot_extract import roster
    r=roster(restored)
    assert patient_packet(restored,r,r['patients'][0])['article_source']['supplementary_manifest_status']=='unavailable_in_legacy_snapshot'
    broken=copy.deepcopy(packet);broken['segments'][0]['text']+=' Extra.'
    with pytest.raises(ValueError,match='hash mismatch'):restore_article(broken)
    packet['has_body_text']=False
    with pytest.raises(ValueError,match='source_body_incomplete'):restore_article(packet)


def test_real_concurrent_gepa_engines_do_not_replace_or_close_standard_streams(tmp_path):
    import gepa
    from gepa.core.adapter import EvaluationBatch
    barrier=threading.Barrier(2)
    original=(sys.stdout,sys.stderr)
    class Adapter:
        def __init__(self):self.first=True
        def evaluate(self,batch,candidate,capture_traces=False):
            if self.first:
                self.first=False;barrier.wait(timeout=5)
            time.sleep(.005)
            value=float(bool(candidate['prompt']))
            return EvaluationBatch(outputs=[value]*len(batch),scores=[value]*len(batch),
                trajectories=[{'Feedback':'Preserve source fidelity.'}]*len(batch) if capture_traces else None)
        def make_reflective_dataset(self,candidate,eval_batch,components_to_update):
            return {'prompt':eval_batch.trajectories}
        def propose_new_texts(self,*args):return {'prompt':'Preserve source fidelity.'}
    def run(i):
        folder=tmp_path/str(i)
        return gepa.optimize(seed_candidate={'prompt':''},trainset=['a','b'],valset=['c','d'],
            adapter=Adapter(),max_metric_calls=12,reflection_minibatch_size=2,
            run_dir=str(folder),logger=FamilyLogger(folder/'run_log.txt'))
    with ThreadPoolExecutor(max_workers=2) as pool:
        results=list(pool.map(run,[0,1]))
    assert all(max(r.val_aggregate_scores)==1 for r in results)
    assert (sys.stdout,sys.stderr)==original
    assert all((tmp_path/str(i)/'run_log.txt').stat().st_size>0 for i in range(2))


def test_source_feedback_is_bounded_literal_and_never_overrides_forbidden_checks():
    a=article();good={'fidelity':.8,'completeness':.7,'patient_ownership':1,
        'diagnostics':[{'kind':'missing','explanation':'Missing age','evidence':[
            {'segment_id':a['segments'][0]['segment_id'],'quote':'A 42-year-old woman'}]}], 'rationale':'Source checked'}
    assert feedback_checker(a)(good)==good
    bad=copy.deepcopy(good);bad['diagnostics'][0]['evidence'][0]['quote']='Invented'
    with pytest.raises(ValueError,match='Nonliteral'):feedback_checker(a)(bad)
    bad=copy.deepcopy(good);bad['diagnostics'][0]['evidence']=[]
    with pytest.raises(ValueError,match='requires literal'):feedback_checker(a)(bad)
    assert combine_reward(1,{'required':1,'forbidden_hits':['bad']},good,'valid')==0
    assert combine_reward(1,{'required':1,'forbidden_hits':[]},good,'failed')==0
    assert 0<combine_reward(.5,{'required':1,'forbidden_hits':[]},good,'valid')<1


async def test_assessment_uses_real_client_body_without_schema_constrained_decoding(tmp_path):
    runner=PilotRunner({},tmp_path/'runner',['http://localhost:8000/v1'],65536,'targeted')
    try:
        body=runner.clients[0].body('gepa_assessment',[{'role':'user','content':'Review source.'}],256)
        assert body['max_tokens']==256
        assert 'response_format' not in body and 'structured_outputs' not in body
    finally:await runner.close()


def test_example_selection_balances_articles_and_prioritizes_errors():
    rows=[{'article':{'article_id':aid},'row':{'identity':{'n':n},'attempts':[{'errors':['bad'] if n==1 else []}]}}
        for aid in ('a','b','c') for n in range(4)]
    selected=select_examples(rows,{'a','b','c'},3)
    assert {r['article']['article_id'] for r in selected}=={'a','b','c'}
    assert all(r['row']['attempts'][0]['errors'] for r in selected)


def test_invalid_audit_registry_can_be_corrected_without_freezing_extra_ids():
    a=article();sid=a['segments'][0]['segment_id'];quote=a['segments'][0]['text']
    valid={'decisions':[{'fact_id':'known','decision':'supported','rationale':'Literal','evidence':[
        {'segment_id':sid,'quote':quote}]}], 'possible_missing_facts':[], 'limitations':[]}
    invalid=copy.deepcopy(valid);invalid['decisions'].append({**invalid['decisions'][0],'fact_id':'extra'})
    checker=lambda v:check_claim_audit(v,a,[{'review_id':'known'}])
    assert erased(repair_snapshot('claim_audit',invalid,checker),valid)==[]
    assert erased(repair_snapshot('claim_audit',valid,checker),{**valid,'decisions':[]})


async def test_repair_replay_starts_at_saved_failure_and_exercises_repair_supplement(tmp_path,monkeypatch):
    a=article();sid=a['segments'][0]['segment_id'];seen=[]
    bad={'features':[{'task':'demographics','feature_as_documented':'42 years','state':'present',
        'episode_as_documented':'day 4','evidence':[{'segment_id':sid,'quote':'A 42-year-old woman'}]}],'limitations':[]}
    good=copy.deepcopy(bad);good['features'][0]['episode_as_documented']=None
    def reply(task,messages):
        seen.append(messages);return good
    http,_=setup_mock(monkeypatch,a,responses=reply)
    original=copy.deepcopy(bad)
    async with http:
        runner=PilotRunner({'refinement_policy':'source_aware','max_repair_rounds':0,
            'max_output_tokens':256,'max_retry_tokens':256,'prompt_overrides':{'repair':'Correct only unsupported timing.'}},
            tmp_path/'replay',['http://localhost:8000/v1'],8192,'targeted',http=http)
        try:
            result=await runner.call('clinical_inventory',[{'role':'user','content':'Immutable source.'}],
                lambda v:check_inventory(v,a),{'article_id':a['article_id']},0,a['segments'],
                repair_from={'raw_candidate':bad,'errors':['Nonliteral episode']})
        finally:await runner.close()
    assert result['status']=='valid' and result['attempts'][0]['mode']=='repair'
    assert any(m['content']=='Correct only unsupported timing.' for m in seen[0])
    assert bad==original and result['data']==good


def test_heldout_denominators_cover_pre_gepa_bootstrap_rows():
    fidelity={'checks':[{'record_id':'PMC1.1:p1','kind':'required','delivered':{'matched':True,'available':True}},
        {'record_id':'PMC2.1:p1','kind':'required','delivered':{'matched':False,'available':False}}]}
    assert heldout_summary(fidelity,{'PMC1.1'})=={'required':1,'matched':1}
    assert heldout_summary(fidelity,{'PMC2.1'})=={'required':1,'matched':0}


def test_new_overnight_configuration_has_live_bootstrap_and_cpu_cleanup(tmp_path):
    c=prepare(ROOT,tmp_path/'work',ROOT/'configs/pilot/overnight-gepa-clinical.yaml')
    assert len(bootstrap_paths(c['work'],c['config']))==5
    assert c['config']['gepa_context']==131072
    assert sum(p['seconds'] for p in c['config']['gepa_profiles'])==10800
    assert '--gres=gpu:b200:1' in job_command(c,'gepa')
    assert '--time=04:00:00' in job_command(c,'gepa')
    assert '--gres=none' in job_command(c,'download') and '--gres=none' in job_command(c,'source-cleanup')
    assert '--gres=gpu:b200:8' in job_command(c,'gpu')
    assert json.loads((Path(c['work'])/'pilot.json').read_text())['config']['extraction_config'].endswith('glimmer-gepa-clinical.yaml')


def test_real_gepa_parallel_proposals_use_clinical_feedback_without_test_labels(tmp_path,monkeypatch):
    import openpatients2.prompt_optimization as module
    from openpatients2.client import APIClient, Completion
    from test_pilot_recovery import examples
    from openpatients2.pilot_review import inventory_messages
    from test_pilot_extract import roster
    from openpatients2.data import read_jsonl
    monkeypatch.setattr(module,'COMPONENTS',['clinical_inventory'])
    sample,trial,reference=examples(tmp_path,['clinical_inventory'])
    articles={a['article_id']:a for a in read_jsonl(sample)}
    for path in (trial/'tasks').glob('*.json'):
        row=json.loads(path.read_text());a=articles[row['identity']['article_id']]
        row['attempts'][0]['request']=inventory_messages(a,roster(a)['patients'][0])
        path.write_text(json.dumps(row))
    reflections=[];assessments=[]
    async def count(self,client,messages):return 100,'mock_exact',65536
    async def complete(self,endpoint,task,messages,cap):
        if task=='summary':
            reflections.append(messages);value={'instructions':'Preserve documented age.'}
        elif task=='gepa_assessment':
            assessments.append(messages)
            candidate=json.loads(messages[-1]['content'].split('CANDIDATE_JSON:\n')[1].split('\nVALIDATION_AND_FINITE_CHECKLIST:')[0])
            value={'fidelity':1,'completeness':1 if candidate['features'] else .2,
                'patient_ownership':1,'rationale':'Documented patient age','diagnostics':[]}
        else:
            aid=json.loads(messages[1]['content'].split('\nTASK:')[0])['segments'][0]['segment_id']
            has=any(m.get('content')=='ADDITIONAL TASK INSTRUCTIONS:\nPreserve documented age.' for m in messages)
            value={'features':[{'task':'demographics','feature_as_documented':'42 years',
                'state':'present','episode_as_documented':None,'evidence':[{'segment_id':aid,'quote':'A 42-year-old woman'}]}]
                if has else [],'limitations':[]}
        return Completion(content=json.dumps(value),finish_reason='stop',prompt_tokens=100,
            completion_tokens=30,reasoning_tokens=0,latency_seconds=.01)
    monkeypatch.setattr(PilotRunner,'token_count',count)
    monkeypatch.setattr(APIClient,'complete_once',complete)
    winners,report=module.optimize_prompts(sample,[trial],tmp_path/'optimization',
        {'max_output_tokens':256,'max_retry_tokens':256},['http://localhost:8000/v1'],65536,reference,
        seconds=30,calls=32,options={'semantic_feedback':True,'proposals':2,'reflection_reasoning':'high'})
    assert winners['clinical_inventory']=='Preserve documented age.'
    assert report['components'][0]['validation_gain']>0
    assert assessments and reflections
    for aid in report['splits']['test']:
        assert aid not in json.dumps(reflections) and aid not in json.dumps(assessments)
    assert report['clinical_accuracy_established'] is False
