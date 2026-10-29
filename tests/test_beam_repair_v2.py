"""Regressions from the Oct 29 Beam run; synthetic sources, no accuracy claim."""
import copy
import json
import os
from pathlib import Path
import subprocess
from types import SimpleNamespace

import pytest

from openpatients2 import bounded_roster as br, bounded_series as bs, ehr_tasks
from openpatients2.benchmark_reporting import quality_summary, task_usage_summary, throughput
from openpatients2.dense_benchmark import ledger_metrics
from openpatients2.patient_context import check_patient_roster, discovery_messages
from openpatients2.pilot_extract import PilotRunner
from openpatients2.client import Completion
from openpatients2.staged_timeline import route_relation_scope
from test_pilot_extract import article, roster, setup_mock, input_file, run_pilot


def test_scope_routing_preserves_direction_unknown_ids_and_original_evidence():
    edges = [{'from_event_id':a,'to_event_id':b,'evidence':[{'quote':'unchanged'}]}
             for a,b in [('e2','e1'),('e3','e2'),('foreign','e1'),('e1','e1')]]
    value = {'edges':edges,'limitations':[]}; before=copy.deepcopy(value)
    routed,audit=route_relation_scope(value,{frozenset(('e1','e2'))},{'e1','e2','e3'})
    assert value==before
    assert routed['edges']==[edges[0],edges[2],edges[3]]
    assert audit[0]['candidate']==edges[1] and not audit[0]['accepted_as_fact']
    assert not audit[0]['evidence_validated']


async def test_wrong_batch_candidates_do_not_enter_atom_repair(tmp_path, monkeypatch):
    value=article()
    def respond(task,messages):
        return {'edges':[{'edge_id':'r1','from_event_id':'e2','to_event_id':'e3'}], 'limitations':[]}
    http,events=setup_mock(monkeypatch,value,responses=respond)
    async with http:
        runner=PilotRunner({'max_output_tokens':256,'max_retry_tokens':256,'max_repair_rounds':2},
            tmp_path/'run',['http://localhost:8000/v1'],4096,'targeted',http=http)
        result=await runner.call('timeline_relations',[{'role':'user','content':'fixture'}],lambda v:v,
            {'article_id':'PMC1.1'},0,value['segments'],
            candidate_scope=lambda v:route_relation_scope(v,{frozenset(('e1','e2'))},{'e1','e2','e3'}))
        await runner.close()
    assert result['status']=='valid' and result['data']['edges']==[]
    assert len(result['attempts'])==1 and len(result['scope_routing'])==1
    assert result['attempts'][0]['raw_candidate']['edges'][0]['edge_id']=='r1'


async def test_scope_routing_does_not_mask_bad_in_scope_citation(tmp_path, monkeypatch):
    value=article()
    def respond(task,messages):
        return {'edges':[{'edge_id':'r1','from_event_id':'e1','to_event_id':'e2',
                         'evidence':[{'segment_id':value['segments'][0]['segment_id'],'quote':'invented quote'}]}],
                'limitations':[]}
    http,_=setup_mock(monkeypatch,value,responses=respond)
    async with http:
        runner=PilotRunner({'max_output_tokens':256,'max_retry_tokens':256},tmp_path/'run',
            ['http://localhost:8000/v1'],4096,'direct',http=http)
        result=await runner.call('timeline_relations',[{'role':'user','content':'fixture'}],lambda v:v,
            {'article_id':'PMC1.1'},0,value['segments'],
            candidate_scope=lambda v:route_relation_scope(v,{frozenset(('e1','e2'))},{'e1','e2'}))
        await runner.close()
    assert result['status']=='failed' and 'nonliteral_quote' in str(result['errors'])


async def test_splittable_length_failure_does_not_burn_full_repair_budget(tmp_path,monkeypatch):
    value=article()
    http,_=setup_mock(monkeypatch,value,responses=lambda *a:Completion(content='{',finish_reason='length',
        prompt_tokens=10,completion_tokens=256,latency_seconds=.01))
    async with http:
        runner=PilotRunner({'max_output_tokens':256,'max_retry_tokens':256,'max_repair_rounds':2},
            tmp_path/'run',['http://localhost:8000/v1'],4096,'targeted',http=http)
        result=await runner.call('timeline_events',[{'role':'user','content':'fixture'}],lambda v:v,
            {'article_id':'PMC1.1'},0,value['segments'],split_on_length=True)
        await runner.close()
    assert result['status']=='failed' and len(result['attempts'])==1
    assert result['errors']==['incomplete_finish:length']


def leaf(value):
    return {'data':check_patient_roster(roster(value),value),'status':'valid'}


def test_roster_reconciliation_cannot_drop_duplicate_or_invent_candidates():
    a=article(); windows=[leaf(a),leaf(a)]
    mapping={'groups':[{'candidate_ids':['0:p1','1:p1'],
                        'identity_evidence':windows[0]['data']['patients'][0]['identity_evidence']}],
             'unresolved_candidate_ids':[], 'limitations':[]}
    assert len(br.assemble(a,windows,mapping)['patients'])==1
    for ids in [['0:p1'],['0:p1','0:p1'],['0:p1','2:p1']]:
        bad=copy.deepcopy(mapping);bad['groups'][0]['candidate_ids']=ids
        with pytest.raises(ValueError):br.assemble(a,windows,bad)
    bad=copy.deepcopy(mapping);bad['groups'][0]['identity_evidence']=[]
    with pytest.raises(ValueError,match='Merging candidates requires'):
        br.assemble(a,windows,bad)
    bad=copy.deepcopy(mapping);bad['groups'][0]['identity_evidence'][0]['quote']='fabricated identity'
    with pytest.raises(ValueError):br.assemble(a,windows,bad)
    bad=copy.deepcopy(mapping);bad['unresolved_candidate_ids']=['1:p1']
    with pytest.raises(ValueError,match='Unresolved patient'):br.assemble(a,windows,bad)
    windows[1]['data']['patients'][0]['species']='nonhuman'
    with pytest.raises(ValueError,match='species'):br.assemble(a,windows,mapping)


@pytest.mark.parametrize('failed_leaf',[False,True])
async def test_windowed_roster_covers_all_segments_or_blocks(tmp_path,monkeypatch,failed_leaf):
    a=article(); calls=[]
    async def fits(r,t,m,replica):
        payload=json.loads(m[-1]['content'].split('SOURCE_JSON:\n')[1].split('\nTASK:\n')[0])
        return len(payload['segments'])==1
    monkeypatch.setattr(br,'fits',fits)
    class Runner:
        config=SimpleNamespace(refinement_policy='source_aware',isolate_secondary_cases=False)
        output=tmp_path;results=[];windowed_roster_articles=set()
        async def call(self,task,messages,checker,identity,replica,segments,**kwargs):
            calls.extend(s['segment_id'] for s in segments)
            failed=failed_leaf and segments==a['segments'][1:]
            if segments==a['segments'][:1]:
                v=roster({**a,'segments':segments})
            else:
                v={'disposition':'no_patient_data','reported_individual_count':0,'roster_complete':True,
                   'patients':[],'background_segment_ids':[s['segment_id'] for s in segments],
                   'unresolved_segment_ids':[],'limitations':[]}
            return {'task':task,'identity':identity,'status':'failed' if failed else 'valid',
                    'data':None if failed else checker(v),'errors':['fixture'] if failed else [],'attempts':[]}
    runner=Runner();result=await br.discover(runner,a,0)
    assert sorted(calls)==sorted(s['segment_id'] for s in a['segments'])
    assert result['discovery_coverage']['all_segments_attempted']
    assert (result['status']=='failed')==failed_leaf
    if not failed_leaf:assert len(result['data']['patients'])==1
    else:assert result['data'] is None


def test_window_discovery_patient_routing_keeps_unresolved_and_audits_omissions():
    a=article()
    original=a['segments'][0]
    a['segments']=[{**original,'segment_id':f'b{i}','text':f'Original block {i}.'} for i in range(6)]
    patient={'patient_id':'p1','source_segment_ids':['b0'],
             'identity_evidence':[{'segment_id':'b0','quote':'Original block 0.'}]}
    r={'unresolved_segment_ids':['b5']}
    before=copy.deepcopy(a)
    view,target,audit=br.routed_patient_view(a,r,patient)
    assert a==before
    assert set(target['source_segment_ids'])=={'b0','b1','b4','b5'}
    assert set(audit['omitted_segment_ids'])=={'b2','b3'}
    assert not audit['semantic_coverage_verified']
    assert view['segments']==[s for s in a['segments'] if s['segment_id'] in target['source_segment_ids']]


async def test_bounded_document_mode_runs_through_real_pilot(tmp_path,monkeypatch):
    a=article();http,_=setup_mock(monkeypatch,a)
    async with http:
        report=await run_pilot({'bounded_document_stages':True,'max_articles':1},input_file(tmp_path,[a]),
            tmp_path/'run',['http://localhost:8000/v1'],4096,'targeted',http=http)
    assert report['patients_extracted']==1
    assert json.loads((tmp_path/'run/coverage/PMC1.1-roster.json').read_text())['all_segments_attempted']


async def test_reconciliation_request_uses_registered_schema_and_keeps_identity(tmp_path,monkeypatch):
    a=article()
    for s in a['segments']:s['text']='A 42-year-old woman received 5 mg.'
    def respond(task,messages):
        if task=='roster':
            payload=json.loads(messages[-1]['content'].split('SOURCE_JSON:\n')[1].split('\nTASK:\n')[0])
            return roster({**a,'segments':payload['segments']})
        assert task=='roster_reconcile'
        return {'groups':[{'candidate_ids':['0:p1','1:p1'],
                           'identity_evidence':[{'segment_id':a['segments'][0]['segment_id'],
                                                 'quote':'A 42-year-old woman'}]}],
                'unresolved_candidate_ids':[],'limitations':[]}
    async def fits(r,t,m,i):
        payload=json.loads(m[-1]['content'].split('SOURCE_JSON:\n')[1].split('\nTASK:\n')[0])
        return len(payload['segments'])==1
    monkeypatch.setattr(br,'fits',fits)
    http,events=setup_mock(monkeypatch,a,responses=respond)
    async with http:
        runner=PilotRunner({'bounded_document_stages':True,'max_output_tokens':256,'max_retry_tokens':256},
            tmp_path/'run',['http://localhost:8000/v1'],4096,'targeted',http=http)
        result=await br.discover(runner,a,0)
        await runner.close()
    assert result['status']=='valid' and len(result['data']['patients'])==1
    assert any(e[:2]==('complete','roster_reconcile') for e in events)


async def test_series_batches_retain_all_cells_headers_and_partial_failures(tmp_path,monkeypatch):
    a={'article_id':'PMC1.1','text_sha256':'test','segments':[
        {'segment_id':'intro','kind':'paragraph','text':'The woman had serial lab tests.'},
        *[{'segment_id':f'b{i}','kind':'table_row','table_id':'T1','text':
           f'Table 1\nLabs\nTest | Day 1 | Day 2\nTest{i} | {i+1} | {i+2}'} for i in range(20)]]}
    annotation={'article_id':a['article_id'],'text_sha256':'test','patients':[]}
    draft=ehr_tasks.series_draft(a);calls=[]
    async def fits(*a):return True
    monkeypatch.setattr(bs,'fits',fits)
    class Runner:
        async def call(self,task,messages,checker,identity,replica,segments,**kwargs):
            calls.append(identity)
            assert identity['context_cell_ids']  # headers retained in BOTH batches
            checked=checker({'series':[]})
            return {'identity':identity,'status':'valid' if len(calls)==1 else 'failed',
                    'data':checked,'errors':[] if len(calls)==1 else ['bad time cell']}
    document,audit=await bs.extract(Runner(),a,{'patients':[]},annotation,draft,{})
    assert len(calls)==2 and audit['all_focus_cells_attempted'] and not audit['all_batches_valid']
    assert document['cells']==draft['cells']  # no numeric value is rewritten/dropped
    focus=[c for r in calls for c in r['focus_cell_ids']]
    assert len(focus)==len(set(focus)) and set(focus)=={c['cell_id'] for c in draft['cells'] if c['grid_row']=='data'}


async def test_series_length_splits_keep_parent_traces_and_complete_child_coverage(tmp_path,monkeypatch):
    a={'article_id':'PMC1.1','text_sha256':'test','segments':[
        {'segment_id':f'b{i}','kind':'table_row','table_id':'T1',
         'text':f'Table 1\nLabs\nTest | Day 1 | Day 2\nTest{i} | 1 | 2'} for i in range(3)]}
    draft=ehr_tasks.series_draft(a);calls=[]
    async def fits(*a):return True
    monkeypatch.setattr(bs,'fits',fits)
    class Runner:
        async def call(self,task,messages,checker,identity,replica,segments,**kwargs):
            fail=len(identity['focus_cell_ids'])>3
            assert kwargs['split_on_length']==fail
            r={'task':task,'identity':identity,'status':'failed' if fail else 'valid',
               'data':None if fail else checker({'series':[]}),
               'errors':['incomplete_finish:length'] if fail else [],
               'attempts':[{'response':{'reasoning_text':'retained fixture trace'}}]}
            calls.append(r);return r
    doc,audit=await bs.extract(Runner(),a,{'patients':[]},
        {'article_id':a['article_id'],'text_sha256':a['text_sha256'],'patients':[]},draft,{})
    assert audit['all_batches_valid'] and audit['all_focus_cells_attempted']
    assert len(audit['batches'])==3
    assert all(r.get('superseded_by_split') for r in calls if r['status']=='failed')
    assert all(r['attempts'][0]['response']['reasoning_text'] for r in calls)
    assert doc['cells']==draft['cells']


def test_reporting_separates_empty_ehr_failure_and_true_nonempty_valid():
    base={'has_prediction':True,'all_extraction_tasks_valid':True,'patients_extracted':1,
          'ehr_errors':{},'wall_seconds':1}
    rows=[base,{**base,'patients_extracted':0},{**base,'ehr_errors':{'series':'failed'}},
          {'has_prediction':True,'wall_seconds':1,'all_extraction_tasks_valid':True}]
    q=quality_summary(rows,{'a':{'patients':[{}]}},{'a':{'patients':[]}})
    assert q['schema_valid_including_empty']==4 and q['nonempty_pipeline_valid_articles']==1
    assert q['empty_prediction_articles']==1 and q['pipeline_validity_unavailable']==1
    assert q['empty_roster_reference_disagreements']==['a']
    assert throughput(rows)['nonempty_pipeline_valid_documents']==1


def test_task_token_breakdown_preserves_unknown_usage_and_scope_counts(tmp_path):
    path=tmp_path/'calls.jsonl'
    rows=[{'task':'timeline_relations','response':{},'metrics':{'prompt_tokens':10,'completion_tokens':20,
           'reasoning_tokens':15,'finish_reason':'length'},'scope_routing':[{'candidate':{}}]},
          {'task':'roster','response':{},'metrics':{'prompt_tokens':5,'completion_tokens':8}},
          {'task':'roster','response':None,'errors':['context_overflow']}]
    path.write_text('\n'.join(map(json.dumps,rows)))
    receipt=ledger_metrics([path]);summary=task_usage_summary([receipt])
    assert summary['timeline_relations']['out_of_batch_candidates']==1
    assert summary['timeline_relations']['length_limited_calls']==1
    assert summary['roster']['reasoning_tokens'] is None
    assert sum(r['completion_tokens'] for r in summary.values())==receipt['completion_tokens']==28
    assert task_usage_summary([{}]) is None


def test_diagnostic_launcher_pins_training_failures_and_preserves_gpu_budget(tmp_path):
    scripts=tmp_path/'scripts';scripts.mkdir()
    root=Path(__file__).resolve().parents[1]
    (scripts/'run_dense_beam.sh').write_text((root/'scripts/run_dense_beam.sh').read_text())
    (scripts/'run_dense_nemotron_super.sh').write_text('env | sort\n')
    env={**os.environ,'DENSE_INCLUDE_TEST':'1','DENSE_SUPER_PROFILES':'spark','DENSE_SUPER_ARTICLE_IDS':'wrong'}
    env.pop('DENSE_HOURS',None)
    output=subprocess.check_output(['bash',str(scripts/'run_dense_beam.sh'),'diagnostic'],env=env,text=True)
    cfg=dict(line.split('=',1) for line in output.splitlines() if '=' in line)
    assert cfg['DENSE_SUPER_PROFILES']=='beam-nvfp4-dflash2' and cfg['DENSE_SUPER_BEAM_REPAIR']=='1'
    assert cfg['DENSE_SUPER_SPLITS']=='train' and cfg['DENSE_INCLUDE_TEST']=='0'
    assert len(cfg['DENSE_SUPER_ARTICLE_IDS'].split())==12
    assert cfg['DENSE_SUPER_LIMIT_ARTICLES']=='' and cfg['DENSE_HOURS']=='12'
    assert 'PMC12779367.1' in cfg['DENSE_SUPER_ARTICLE_IDS']
