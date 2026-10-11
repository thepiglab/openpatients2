import copy
import json
from pathlib import Path
import time

import pytest

from openpatients2.clinical_normalization import normalize_clinical
from openpatients2.data import read_jsonl
from openpatients2.license_policy import license_identity
from openpatients2.prompts import messages_for
from openpatients2.prompt_strategy import rewrite_strategy, strategy_text
from test_pilot_extract import article, roster, answer, setup_mock, input_file, run_pilot

ROOT=Path(__file__).resolve().parents[1]


def identity_reference():
    return {'checks':[], 'articles':[{'article_id':'PMC1.1','text_sha256':'hash','expected_count':2,
        'species':['human','human'],'identities':[
            {'patient_id':'p1','evidence':[{'segment_id':'a','quote':'The first patient was a seventy-year-old man.'}]},
            {'patient_id':'p2','evidence':[{'segment_id':'b','quote':'The second patient was a seventy-eight-year-old man.'}]}]}]}


def patient(pid,sid,quote):
    return {'source':{'record_id':'PMC1.1:'+pid, 'article_source':{'article_id':'PMC1.1','text_sha256':'hash'},
        'patient_target':{'species':'human','identity_evidence':[{'segment_id':sid,'quote':quote}]}},'sections':{},'companions':{}}


def course():
    return {'record_id':'PMC1.1:p1','nodes':[
        {'id':'a','pattern':{'description':{'regex':'tracheostomy'},'occurrence':'occurred'},'source':[{'segment_id':'s'}]},
        {'id':'b','pattern':{'description':{'regex':'surgery'},'occurrence':'occurred'},'source':[{'segment_id':'s'}]}],
        'edges':[{'from':'a','to':'b','relation':'before'}]}


def graph():
    return {'events':[{'event_id':'x','description':'Tracheostomy on day 55','occurrence':'occurred','evidence':[{'segment_id':'s'}]},
        {'event_id':'z','description':'Repair surgery on day 70','occurrence':'occurred','evidence':[{'segment_id':'s'}]}],
        'edges':[{'from_event_id':'x','to_event_id':'z','relation':'before'}]}


def test_rewritten_prompts_preserve_source_schema_and_pixels():
    messages=messages_for({'record_id':'r','text':'Original source'},'medications')
    original=copy.deepcopy(messages);new=rewrite_strategy('medications',messages,'Keep documented drug doses.')
    prefix='\nEXTRACTION_TASK: medications\n';guide='OUTPUT FIELD GUIDE'
    assert new[1]['content'].split(prefix)[0]==original[1]['content'].split(prefix)[0]
    assert new[1]['content'].split(guide)[1]==original[1]['content'].split(guide)[1]
    assert messages==original
    assert strategy_text('medications',new)=='Keep documented drug doses.'
    assert 'Original source' not in strategy_text('medications',original)
    aux=[{'role':'system','content':'Guard'},{'role':'user','content':'{"source":"unchanged"}\nTASK:\nOld strategy\nSCHEMA:\n{"required":["x"]}'}]
    new=rewrite_strategy('summary',aux,'New strategy')
    assert 'Old strategy' not in new[1]['content'] and 'New strategy' in new[1]['content']
    assert new[1]['content'].split('\nTASK:')[0]==aux[1]['content'].split('\nTASK:')[0]
    assert new[1]['content'].split('\nSCHEMA:')[1]==aux[1]['content'].split('\nSCHEMA:')[1]
    image=[{'role':'system','content':'Old visual strategy'},{'role':'user','content':[
        {'type':'text','text':'caption and immutable schema'}, {'type':'image_url','image_url':{'url':'data:image/png;base64,abc'}}]}]
    assert rewrite_strategy('figure_visuals',image,'Read visible labels')[1]==image[1]


@pytest.mark.parametrize('quote,expected',[
    ('IV flucloxacillin and oral prednisolone were given.','IV'),
    ('IV antibiotics and flucloxacillin were given.',None),
    ('IV flucloxacillin then oral flucloxacillin.',None),
    ('IV flucloxacillinase was discussed.',None)])
def test_route_recovery_attaches_only_to_the_named_drug(quote,expected):
    original={'items':[{'name':'flucloxacillin','route':None,'evidence':[{'source_section':'s','quote':quote}]}]}
    value,audit=normalize_clinical('medications',original,[{'segment_id':'s','text':quote}])
    assert value['items'][0]['route']==expected
    assert original['items'][0]['route'] is None
    assert bool(audit)==(expected is not None)


def test_glued_license_prose_is_not_invented_as_a_jurisdiction():
    assert license_identity('https://creativecommons.org/licenses/by/4.0/This') is None
    assert license_identity('https://creativecommons.org/licenses/by-nc-sa/3.0/us/')['jurisdiction']=='us'


async def test_completion_and_scattering_execute_real_source_gates_without_schema_decoding(tmp_path,monkeypatch):
    a=article();seen=[]
    def respond(task,messages):
        seen.append(task)
        return answer({'timeline_completion':'timeline_v2','summary_completion':'summary'}.get(task,task),a)
    http,_=setup_mock(monkeypatch,a,responses=respond)
    async with http:
        report=await run_pilot({'timeline_completion':True,'summary_completion':True,'scatter_calls':True},
            input_file(tmp_path,[a]),tmp_path/'run',['http://localhost:8000/v1','http://localhost:8001/v1'],8192,'targeted',http=http)
    assert 'timeline_completion' in seen and 'summary_completion' in seen
    tasks=[json.loads(p.read_text()) for p in (tmp_path/'run/tasks').glob('*.json')]
    assert {t['replica'] for t in tasks}=={0,1}
    assert all(t['status']=='valid' for t in tasks)
    assert report['task_statuses_by_domain']['timeline_completion']=={'valid':1}
    patient=json.loads((tmp_path/'run/patients.jsonl').read_text())
    assert patient['companions']['summary'] and patient['timeline_audit']['structural_source_gates_passed']


async def test_failed_completion_preserves_an_existing_supported_fact_link(tmp_path,monkeypatch):
    from test_pilot_correctness import quote_only_timeline
    from test_pilot_extract import demographics
    a=article()
    def respond(task,messages):
        if task=='demographics':
            value=demographics(a);value['items']=value['items'][:1];return value
        if task=='timeline_v2':
            payload=json.JSONDecoder().raw_decode(messages[1]['content'].split('SOURCE_JSON:\n',1)[1])[0]
            value=quote_only_timeline(a);value['events'][0]['fact_ids']=payload['known_fact_ids'][:1]
            assert value['events'][0]['fact_ids']
            return value
        if task=='timeline_completion':return answer('timeline_v2',a)  # tries to erase the link
        return answer(task,a)
    http,_=setup_mock(monkeypatch,a,responses=respond)
    async with http:
        report=await run_pilot({'timeline_completion':True},input_file(tmp_path,[a]),tmp_path/'run',
            ['http://localhost:8000/v1'],8192,'targeted',http=http)
    bundle=json.loads((tmp_path/'run/patients.jsonl').read_text())
    assert bundle['companions']['timeline_v2']['events'][0]['fact_ids']
    assert bundle['timeline_audit']['structural_source_gates_passed']
    assert report['task_statuses_by_domain']['timeline_completion']=={'failed':1}
    receipt=next(json.loads(p.read_text()) for p in (tmp_path/'run/tasks').glob('*.json')
        if json.loads(p.read_text())['task']=='timeline_completion')
    assert any('erased existing supported fact links' in error for error in receipt['errors'])


