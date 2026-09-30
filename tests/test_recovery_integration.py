import copy
import json
from pathlib import Path

import httpx
import pytest

from openpatients2.client import APIClient, Completion
from openpatients2.data import read_jsonl
from openpatients2.demo import transport
from openpatients2.evidence_recovery import recover_citations, segment_errors
from openpatients2.output_parser import parse_output, OutputParseError
from openpatients2.pipeline import run_pipeline
from openpatients2.targeted_repair import ItemRepair
from openpatients2.validation import validate


def test_duplicate_recovery_is_opt_in_audited_and_type_strict():
    text='{"items":[{"value":143,"value":143}]}'
    with pytest.raises(OutputParseError):parse_output(text)
    parsed=parse_output(text,recover_identical_duplicates=True)
    assert parsed.value=={'items':[{'value':143}]} and len(parsed.transformations)==1
    assert parse_output('```json\n'+text+'\n```',recover_identical_duplicates=True).value==parsed.value
    for text in ['{"v":1,"v":true}', '{"v":1,"v":1.0}', '{"v":1,"v":2}',
                 '{"v":{"a":1},"v":{"a":true}}']:
        with pytest.raises(OutputParseError):parse_output(text,recover_identical_duplicates=True)
    with pytest.raises(OutputParseError):parse_output('{"v":1,"v":1}',finish_reason='length',recover_identical_duplicates=True)


def test_citation_recovery_is_segment_local_and_keeps_numbers_times_and_units():
    segments=[{'segment_id':'a','heading':'Case 1','text':'Sodium\t143\nmg/L'},
              {'segment_id':'b','heading':'Case 2','text':'Sodium\t122\nmg/L'}]
    good={'numeric_value':143,'unit':'mg/L','time':{'text':'Day 1'},
          'evidence':[{'source_section':'[a] Case 1','quote':'Sodium 143 mg/L'}]}
    fixed,audit=recover_citations(good,segments)
    assert fixed['evidence'][0]=={'source_section':'a','quote':segments[0]['text']}
    assert len(audit)==2 and fixed['time']==good['time'] and fixed['numeric_value']==143
    assert not segment_errors(fixed,segments)
    for sid,quote in [('a','Sodium 122 mg/L'), ('b','Sodium 143 mg/L'), ('missing','Sodium 143 mg/L')]:
        item={'evidence':[{'source_section':sid,'quote':quote}]}
        assert recover_citations(item,segments)==(item,[])
        assert segment_errors(item,segments)


def test_citation_recovery_rejects_ambiguous_whitespace_matches():
    item={'evidence':[{'segment_id':'a','quote':'X 1'}]}
    segments=[{'segment_id':'a','text':'X\t1 and X\n1'}]
    assert recover_citations(item,segments)==(item,[])


def two_items(sections):
    data=copy.deepcopy(sections['medications'])
    data['items']=[copy.deepcopy(data['items'][0]),copy.deepcopy(data['items'][0])]
    data['items'][1]['evidence'][0]['quote']='fabricated quotation'
    return data


def test_targeted_repair_freezes_neighbors_and_refuses_field_erasure(sections,row):
    candidate=two_items(sections)
    plan=ItemRepair.create('medications',candidate,row['text'],[])
    assert len(plan.accepted)==1 and len(plan.pending)==1
    fixed=copy.deepcopy(sections['medications']);fixed['items']=fixed['items'][:1]
    erased=copy.deepcopy(fixed);erased['items'][0]['dose_value']=None
    plan.apply(erased)
    assert len(plan.pending)==1
    assert 'Refused field erasure' in str(plan.trace[-1]['errors'])
    assert plan.accepted[0]==candidate['items'][0]
    plan.apply(fixed)
    assert not plan.pending and len(plan.output()['items'])==2
    assert validate('medications',plan.output(),row['text']).valid
    assert plan.output()['items'][0]==candidate['items'][0]


def test_targeted_repair_cannot_drop_or_add_items(sections,row):
    plan=ItemRepair.create('medications',two_items(sections),row['text'],[])
    for reply in [{'items':[]},{'items':[{},{}]},None]:
        plan.apply(reply)
        assert len(plan.pending)==1
    assert plan.output()['coverage']=='limited'
    assert len(plan.output()['items'])==1
    assert plan.audit()['pending'][0]['item']['evidence'][0]['quote']=='fabricated quotation'


@pytest.mark.parametrize('repair_succeeds',[True,False])
async def test_runtime_repairs_only_failed_items_and_exports_partial_honestly(config,sections,repair_succeeds):
    config.tasks=['case_context','medications']
    calls=[];good=transport()
    async def handler(request):
        body=json.loads(request.content)
        if 'EXTRACTION_TASK: medications' not in body['messages'][-1]['content']:
            return await good.handle_async_request(request)
        calls.append(body)
        data=two_items(sections) if len(calls)==1 else copy.deepcopy(sections['medications'])
        if len(calls)>1:
            data['items']=data['items'][:1]
            if not repair_succeeds:data['items'][0]['evidence'][0]['quote']='still fabricated'
        return httpx.Response(200,text='data: '+json.dumps({'choices':[{'delta':{'content':json.dumps(data)},'finish_reason':'stop'}]})+'\n\ndata: [DONE]\n\n')
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        report=await run_pipeline(config,client=APIClient(config.api,http))
    assert len(calls)==2 and 'TARGETED ITEM REPAIR' in calls[1]['messages'][-1]['content']
    p=next(read_jsonl(Path(config.output)/'patients.jsonl'))
    assert p['quality']['medications']['status']==('valid' if repair_succeeds else 'partial')
    assert p['complete_for_scope'] is repair_succeeds
    assert len(p['sections']['medications']['items'])==(2 if repair_succeeds else 1)
    assert p['sections']['medications']['items'][0]==sections['medications']['items'][0]
    assert report['attempts']==3
    attempts=list(read_jsonl(Path(config.output)/'attempts.jsonl'))
    assert any('fabricated quotation' in a['generation']['content'] for a in attempts)


async def test_article_call_uses_same_targeted_repair_and_persists_audit(tmp_path,sections,row):
    from test_article_experiment import config
    from openpatients2.experiment import Experiment
    cfg=config(tmp_path);cfg['validation_retries']=1;cfg['models'][0]['context_length']=100000
    exp=Experiment(cfg);calls=[]
    async def complete(endpoint,task,messages,cap):
        calls.append(messages)
        data=two_items(sections) if len(calls)==1 else {**sections['medications'],'items':sections['medications']['items'][:1]}
        return Completion(content=json.dumps(data),finish_reason='stop',usage={'cost':0.001})
    exp.clients['fixture'].complete_once=complete
    def check(value):
        checked=validate('medications',value,row['text'])
        if not checked.valid:raise ValueError(';'.join(checked.errors))
        return checked.data
    try:
        r=await exp.call(cfg['models'][0],'medications',[{'role':'user','content':row['text']}],check,{},
                         max_tokens=1000,clinical_record=row,source_segments=[])
        assert r['status']=='valid' and len(r['data']['items'])==2
        assert r['recovery'][-1]['item_repair']['initially_accepted_items']==1
        assert 'TARGETED ITEM REPAIR' in calls[-1][-1]['content']
    finally:await exp.close()
