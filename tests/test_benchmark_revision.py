import asyncio
import copy
import gzip
import json
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from openpatients2 import dense_benchmark as b, dense_nemotron_super as s
from openpatients2 import semantic_benchmark as judge
from openpatients2.benchmark_selection import select_articles
from openpatients2 import nemotron_super_recipes as recipes
from openpatients2.speculation_metrics import parse, delta, COUNTERS
from openpatients2.evidence_recovery import resolve_timeline_spans
from openpatients2.pilot_extract import sources_for
from openpatients2.staged_timeline import extract
from test_dense_nemotron_super import campaign
from test_pilot_extract import article


def install_current(root):
    legacy = root / 'configs/hipergator/dense-benchmark/reference.jsonl.gz'
    target = root / 'configs/hipergator/nemotron-super/reference.jsonl.gz'
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(legacy.read_bytes())
    b.write(target.with_name('reference-manifest.json'), {'articles': 3, 'sha256': b.sha(target)})
    return target


def test_new_reference_and_default_train_do_not_fall_back(campaign, tmp_path):
    root = campaign[2]
    with pytest.raises(ValueError, match='Missing current'):
        s.prepare(root, tmp_path / 'missing', profiles=['fastino'])
    current = install_current(root)
    # Old snapshot can be stale/broken: current campaign must not read it.
    (root / 'configs/hipergator/dense-benchmark/reference.jsonl.gz').write_bytes(b'old')
    work = tmp_path / 'new'
    s.prepare(root, work, profiles=['fastino'], with_images=False)
    m = b.read(work / 'manifest.json')
    assert m['splits'] == {'train': 1}
    assert m['reference_selection']['source'] == 'explicit_snapshot'
    assert m['reference_selection']['snapshot_sha256'] == b.sha(current)
    assert m['extraction']['staged_timeline'] is False  # arm override only
    assert b.read(work / 'arms/fastino-text/manifest.json')['extraction']['staged_timeline']
    with pytest.raises(ValueError, match='requires --include-test'):
        s.prepare(root, tmp_path / 'test-denied', profiles=['fastino'], splits=['test'])
    s.prepare(root, tmp_path / 'test', profiles=['fastino'], splits=['test'], include_test=True, with_images=False)
    assert b.read(tmp_path / 'test/manifest.json')['splits'] == {'test': 1}
    with pytest.raises(ValueError, match='Expected 260'):
        s.prepare(root, tmp_path / 'stale-count', expected=260, profiles=['fastino'])


def test_selection_reproducible_and_explicit_ids_obey_split(campaign):
    root = campaign[2]
    rows = [{'article_id': f'PMC{100+i}.1', 'split': split} for i, split in enumerate(['train','validation','test'])]
    selected = select_articles(root, rows, 2)
    assert selected == select_articles(root, list(reversed(rows)), 2)
    assert len(set(selected)) == 2
    with pytest.raises(ValueError, match='absent'):
        select_articles(root, rows[:1], article_ids=['PMC102.1'])
    with pytest.raises(ValueError, match='not both'):
        select_articles(root, rows, 2, article_ids=['PMC100.1'])
    with pytest.raises(ValueError, match='between'):
        select_articles(root, rows, 4)


def test_redhat_ablation_preserves_recipe_except_draft(campaign, tmp_path):
    original = recipes.PROFILES['redhat-fp8-dspark']
    baseline = recipes.PROFILES['redhat-fp8-no-spec']
    assert {k:v for k,v in original.items() if k not in {'speculation','drafter','adaptation'}} == {
        k:v for k,v in baseline.items() if k not in {'speculation','drafter','adaptation'}}
    assert 'redhat-fp8-no-spec' not in recipes.DEFAULT_PROFILES
    work = tmp_path / 'ablation'
    s.prepare(campaign[2], work, expected=3, with_images=False, profiles=['redhat-fp8-dspark','redhat-fp8-no-spec'])
    target = s.weights_path(work, original['model'])
    b.write(target / 'config.json', b.read(Path(__file__).parent/'fixtures/redhat_nemotron_fp8_config.json'))
    enabled, disabled = [s.serving(work, n+'-text') for n in ['redhat-fp8-dspark','redhat-fp8-no-spec']]
    assert enabled.model_path == disabled.model_path
    assert disabled.speculative_config is None
    assert '--hf-overrides' in disabled.extra_args
    assert s.profile_models('redhat-fp8-no-spec') == [original['model']]


def test_counter_reset_and_absence_not_fake_zero():
    assert parse('vllm:spec_decode_num_draft_tokens_total{model_name="x"} 100\n') == {COUNTERS[0]:100}
    assert not delta({'counters':{}}, {'counters':{}})['available']
    assert not delta({'counters':dict(zip(COUNTERS,[100,10]))}, {'counters':dict(zip(COUNTERS,[10,2]))})['available']
    r = delta({'counters':dict(zip(COUNTERS,[100,10]))}, {'counters':dict(zip(COUNTERS,[200,35]))})
    assert r['acceptance_fraction'] == .25


def tiny_judge_packet(tmp_path):
    work, out = tmp_path/'work', tmp_path/'judge'
    source = article()
    ref = b.rows(Path(__file__).parent/'fixtures/unified_scoring_reference.jsonl')
    row = copy.deepcopy(next(iter(ref.values())))
    row.update(article_id=source['article_id'], split='train', text_sha256=source['text_sha256'])
    # One reference, two duplicate predictions: matching must not double credit.
    item = {'name':'high blood pressure', 'assertion':'present', 'evidence':[]}
    row['patients'] = [{'patient_id':'p1', 'label':'index patient', 'sections':{'conditions':{'items':[item]}}}]
    prediction = copy.deepcopy(row)
    prediction['patients'][0]['sections']['conditions']['items'] = [copy.deepcopy(item), copy.deepcopy(item)]
    b.write(work/'manifest.json', {'articles':[{'article_id':source['article_id']}]})
    b.write_rows(work/'reference/unified.jsonl',[row])
    b.write_rows(work/'summary/predictions.jsonl',[prediction])
    b.write_rows(work/'inputs'/source['article_id']/'source.jsonl',[source])
    judge.prepare(work,out)
    return work,out,source


def verdict(req, source):
    seg=source['segments'][0]
    return {'prediction_supported':True, 'source_evidence':[{'segment_id':seg['segment_id'],'quote':seg['text']}],
        'decisions':[{'reference_index':i,'verdict':'equivalent','reference_supported':True,'explanation':'Same supported claim.'} for i in req['reference_indices']]}


def test_judge_incomplete_no_score_blinding_and_one_to_one(tmp_path):
    _,out,source=tiny_judge_packet(tmp_path)
    assert judge.score(out)['arms']['single']['semantic_fact_agreement'] is None
    reqs=[json.loads(x) for x in (out/'requests.jsonl').read_text().splitlines()]
    assert len(reqs)==2
    msg=judge.messages(out,reqs[0])
    assert 'generation_trace' not in msg[1]['content'] and '"arm"' not in msg[1]['content']
    cfg={'model':'judge','revision':'pin'}; b.write(out/'judge.json',cfg)
    b.write_rows(out/'judgments.jsonl',[{'request_id':r['id'],'valid':True,'judge_sha256':judge.digest(cfg),'decision':verdict(r,source)} for r in reqs])
    result=judge.score(out)
    assert result['complete']
    assert result['arms']['single']['semantic_fact_agreement']=={'tp':1,'fp':1,'fn':0,'precision':.5,'recall':1.,'f1':2/3}
    assert 'unreviewed' in result['calibration']
    bad=verdict(reqs[0],source);bad['source_evidence'][0]['quote']='invented quotation'
    with pytest.raises(ValueError,match='literal'):
        judge.validate(bad,reqs[0],source)
    bad=verdict(reqs[0],source);bad['decisions']*=2
    with pytest.raises(ValueError,match='duplicate'):
        judge.validate(bad,reqs[0],source)
    bad=verdict(reqs[0],source);bad['decisions'][0]['verdict']='contradictory'
    assert judge.validate(bad,reqs[0],source)['decisions'][0]['verdict']=='contradictory'


def test_judge_resumes_and_records_returned_reasoning(tmp_path,monkeypatch):
    _,out,source=tiny_judge_packet(tmp_path)
    reqs=[json.loads(x) for x in (out/'requests.jsonl').read_text().splitlines()]
    actual=httpx.AsyncClient;calls=[]
    def handle(request):
        calls.append(json.loads(request.content))
        return httpx.Response(200,json={'choices':[{'finish_reason':'stop','message':{'content':json.dumps(verdict(reqs[0],source)), 'reasoning_content':'returned judge trace'}}]})
    monkeypatch.setattr(httpx,'AsyncClient',lambda **kw:actual(transport=httpx.MockTransport(handle),**kw))
    kwargs=dict(base_url='http://localhost:8001/v1',model='judge',revision='pin',max_requests=1)
    assert not asyncio.run(judge.run(out,**kwargs))['complete']
    assert asyncio.run(judge.run(out,**kwargs))['complete']
    assert len(calls)==2
    assert 'reasoning_content' in judge.ledger(out)[0]['response']['choices'][0]['message']
    assert asyncio.run(judge.run(out,**kwargs))['complete'] and len(calls)==2
    with pytest.raises(ValueError,match='settings changed'):
        asyncio.run(judge.run(out,**{**kwargs,'revision':'different'}))


@pytest.mark.parametrize('cycle',[False,True])
def test_staged_timeline_full_source_and_cross_batch_edges(tmp_path,cycle):
    article={'article_id':'PMC1.1','segments':[{'segment_id':'s1','text':'Patient was diagnosed.'},
        {'segment_id':'s2','text':'Then patient was treated.'}]}
    rid='PMC1.1:p1'; _,sources=sources_for(article)
    class Runner:
        config=SimpleNamespace(timeline_segment_batch_size=1,timeline_relation_batch_size=1,refinement_policy='source_aware')
        output=tmp_path;arm='targeted';results=[]
        async def call(self,task,messages,checker,identity,replica,segments,**kwargs):
            assert all(s['text'] in messages[-1]['content'] for s in article['segments'])
            if task=='timeline_events':
                sid=identity['focus_segments'][0];seg=next(s for s in segments if s['segment_id']==sid)
                ev={'event_id':'e1','record_id':rid,'episode_id':None,'kind':'other','occurrence':'occurred',
                    'description':seg['text'],'fact_ids':[],'evidence':[{'segment_id':sid,'quote':seg['text']}],
                    'attribution_evidence':[{'segment_id':sid,'quote':seg['text']}],'times':[]}
                value={'schema_version':'patient-timeline/2','record_id':rid,'events':[ev],'edges':[],'limitations':[]}
            else:
                focus=identity['focus_event_ids'][0]
                edges=[]
                if focus=='e1' or cycle:
                    edges=[{'edge_id':'edge1','from_event_id':focus,'to_event_id':'e2' if focus=='e1' else 'e1',
                        'relation':'before','offset':None,'evidence':[{'segment_id':'s2','quote':segments[1]['text']}]}]
                value={'edges':edges,'limitations':[]}
            value,_=resolve_timeline_spans(value,'PMC1.1:jats',segments)
            return {'task':task,'identity':identity,'status':'valid','data':checker(value),'attempts':[]}
    (tmp_path/'tasks').mkdir()
    result=asyncio.run(extract(Runner(),article,{'patient_id':'p1'},{},[],{'record_id':rid,'article_id':'PMC1.1'},0,sources))
    assert len(result['data']['events'])==2
    assert len(result['stage_coverage'])==4
    assert result['status']==('partial' if cycle else 'valid')
    assert len(result['data']['edges'])==(0 if cycle else 1)


def test_expanded_figure_packet_not_silently_capped_at_twelve(tmp_path):
    source=article()
    source['figures']=[{'figure_key':f'F{i}','image_urls':[]} for i in range(13)]
    with pytest.raises(ValueError,match='More than 12'):
        b.freeze_media(source,tmp_path,tmp_path/'old')
    result=b.freeze_media(source,tmp_path,tmp_path/'new',max_figures=48)
    assert result['missing_image']==13
    from openpatients2.pilot_extract import PilotConfig
    assert PilotConfig(max_figures=48).max_figures==48


async def test_staged_timeline_uses_real_client_schema_and_preserves_ledger(tmp_path,monkeypatch):
    from test_pilot_extract import setup_mock
    from openpatients2.pilot_extract import PilotRunner
    from openpatients2.client import Completion
    value=article();seg=value['segments'][0];rid='PMC1.1:p1'
    def response(task,messages):
        if task=='timeline_events':
            content={'schema_version':'patient-timeline/2','record_id':rid,'events':[
                {'event_id':'local','record_id':rid,'episode_id':None,'kind':'treatment','occurrence':'occurred',
                 'description':'A documented dose was received.','fact_ids':[],
                 'evidence':[{'segment_id':seg['segment_id'],'quote':seg['text']}],
                 'attribution_evidence':[{'segment_id':seg['segment_id'],'quote':seg['text']}],'times':[]}],
                'edges':[],'limitations':[]}
        elif task=='timeline_relations':content={'edges':[],'limitations':[]}
        else:raise AssertionError(task)
        return Completion(content=json.dumps(content),finish_reason='stop',reasoning_text='Returned trace',
            reasoning_fields={'reasoning_content':'Returned trace'},reasoning_field_seen=True,
            prompt_tokens=10,completion_tokens=100,reasoning_tokens=20)
    http,events=setup_mock(monkeypatch,value,responses=response)
    runner=PilotRunner({'max_output_tokens':256,'max_retry_tokens':256,'staged_timeline':True},
        tmp_path/'extract',['http://localhost:8000/v1'],8192,'targeted',http=http)
    try:
        async with http:
            _,sources=sources_for(value)
            result=await extract(runner,value,{'patient_id':'p1'},{},[],{'article_id':'PMC1.1','record_id':rid},0,sources)
        assert result['status']=='valid'
        assert {a['task'] for a in result['attempts']}=={'timeline_events','timeline_relations'}
        assert all(a['response']['reasoning_status']=='returned_text' for a in result['attempts'])
        assert all(a['ledger_sequence'] is not None for a in result['attempts'])
    finally:await runner.close()


def test_timeline_atom_repair_cannot_rewrite_accepted_neighbor():
    from openpatients2.targeted_repair import TimelineAtomRepair
    original={'events':[{'event_id':'accepted','dose':5},{'event_id':'bad','dose':-1}], 'edges':[]}
    def check(value):
        if any(e['dose']<0 for e in value['events']):raise ValueError('invalid dose')
        return value
    plan=TimelineAtomRepair.create('timeline_events',original,check)
    assert plan and list(plan.pending)==['bad']
    with pytest.raises(ValueError,match='unrequested'):
        plan.apply({'events':[{'event_id':'accepted','dose':0}]})
    assert plan.output()['events']==[original['events'][0]]
    plan.apply({'events':[{'event_id':'bad','dose':7}]})
    assert plan.output()['events']==[{'event_id':'accepted','dose':5},{'event_id':'bad','dose':7}]
    assert not plan.pending and original['events'][1]['dose']==-1


async def test_real_timeline_repair_merges_only_failed_atom(tmp_path,monkeypatch):
    from test_pilot_extract import setup_mock
    from openpatients2.pilot_extract import PilotRunner
    from openpatients2.longitudinal import PatientTimeline,audit_timeline
    value=article(); seg=value['segments'][0]; rid='PMC1.1:p1'; _,sources=sources_for(value)
    good={'event_id':'good','record_id':rid,'episode_id':None,'kind':'treatment','occurrence':'occurred',
          'description':'The woman received a documented dose.','fact_ids':[],
          'evidence':[{'segment_id':seg['segment_id'],'quote':seg['text']}],
          'attribution_evidence':[{'segment_id':seg['segment_id'],'quote':seg['text']}],'times':[]}
    bad={**copy.deepcopy(good),'event_id':'bad','kind':'plan'}
    shell={'schema_version':'patient-timeline/2','record_id':rid,'edges':[],'limitations':[]}
    calls=[]
    def response(task,messages):
        calls.append(messages)
        if len(calls)==1:return {**shell,'events':[good,bad]}
        assert 'Repair ONLY' in messages[-1]['content']
        return {**shell,'events':[{**bad,'kind':'treatment'}]}
    http,_=setup_mock(monkeypatch,value,responses=response)
    runner=PilotRunner({'max_output_tokens':256,'max_retry_tokens':256,'max_repair_rounds':2},
        tmp_path/'extract',['http://localhost:8000/v1'],8192,'targeted',http=http)
    def checker(v):
        parsed=PatientTimeline.model_validate(v)
        assert audit_timeline(parsed,sources,{})['structural_source_gates_passed']
        return parsed.model_dump()
    try:
        async with http:
            result=await runner.call('timeline_events',[{'role':'user','content':'source'}],checker,
                {'article_id':'PMC1.1','record_id':rid},0,value['segments'])
        assert result['status']=='valid' and len(calls)==2
        assert [e['event_id'] for e in result['data']['events']]==['good','bad']
        assert result['data']['events'][0]['description']==good['description']
    finally:await runner.close()
