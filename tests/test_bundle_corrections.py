"""Regressions from the October 5 campaign; no remote inference/downloads."""
import asyncio
from copy import deepcopy
import importlib.util
import json
from pathlib import Path
import time
from types import SimpleNamespace

import pytest

from openpatients2.data import read_jsonl
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
