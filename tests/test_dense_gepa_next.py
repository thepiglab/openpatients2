import asyncio
import copy
import json
import time
from pathlib import Path

import pytest

from openpatients2 import dense_benchmark as b, dense_gepa as g, dense_gepa_next as n
from openpatients2.dense_training_data import has_review_only


def plans():
    from test_dense_gepa import plan
    p = plan()
    p.update(hours=6, confirmation_reserve_seconds=100, concurrent_articles=2)
    return p, {'max_prompt_chars': 2400, 'shortlist': 2, 'selection_seeds': [42, 43],
        'gate_threshold': .35, 'rerank_reserve_seconds': 100,
        'max_common_field_regression': .01, 'minimum_grounded_gain': 0.,
        'minimum_valid_fraction_delta': -.01,
        'arms': ['compact', 'direct', 'additive', 'gated_additive']}


def row(aid='PMC1.1'):
    return {'article_id': aid, 'counts': {'tp': 9, 'fp': 1, 'fn': 2},
        'grounded_counts': {'tp': 9, 'fp': 1, 'fn': 2}, 'grounded_fbeta': g.fbeta(9,1,2),
        'fbeta': g.fbeta(9,1,2), 'precision': .9, 'recall': 9/11,
        'attributes': {'correct': 90, 'total': 100},
        'critical_attributes': {k: {'correct': 90, 'total': 100} for k in n.CRITICAL},
        'statuses': {'valid': 1}, 'aligned': {'p1:0': {'value:numeric_value': True}}, 'seconds': 1.}


def test_search_reward_penalizes_precision_above_old_floor_and_critical_loss():
    p, _ = plans(); c = row()
    lower = copy.deepcopy(c); lower['precision'] = .85
    assert n.reward(lower, c, p) < n.reward(c, c, p)
    lower = copy.deepcopy(c); lower['critical_attributes']['unit']['correct'] = 80
    assert n.reward(lower, c, p) < n.reward(c, c, p)
    lower = copy.deepcopy(c); lower['statuses'] = {'failed': 1}
    assert n.reward(lower, c, p) < n.reward(c, c, p)


def test_paired_diagnostics_separates_new_errors_old_regressions_and_losses():
    c = {'aligned': {'p:0': {'value:value': True}, 'p:1': {'unit:unit': True}}}
    a = {'aligned': {'p:0': {'value:value': False}, 'p:2': {'value:value': False}}}
    d = n.paired_diagnostics(c, a)
    assert d['lost_matches'] == d['new_matches'] == d['common_matches'] == 1
    assert d['fields']['value'] == {'previously_correct': 1, 'regressed': 1, 'new_correct': 0, 'new_total': 1}


def test_critical_error_changes_attribute_aware_f2_without_changing_item_match():
    ref = next(iter(b.rows(Path(__file__).parent/'fixtures/unified_scoring_reference.jsonl').values()))
    pred = {p['patient_id']: copy.deepcopy(p['sections'].get('observations', {})) for p in ref['patients']}
    good = n.diagnostics(ref, pred, 'observations')
    assert good['grounded_fbeta'] == 1
    for section in pred.values():
        for item in section.get('items', []):
            item['assertion'] = 'absent' if item['assertion'] != 'absent' else 'present'
    bad = n.diagnostics(ref, pred, 'observations')
    assert bad['grounded_fbeta'] < good['grounded_fbeta']


def test_gate_only_uses_source_and_output():
    section = {'coverage':'complete', 'items':[{'evidence':[{'quote':'10 mg'}]}]}
    assert n.source_gate({'text':'10 mg'}, section, 'valid') == 0
    assert n.source_gate({'text':'10 mg then 20 mg'}, section, 'valid') == .5
    assert n.source_gate({'text':'nothing'}, section, 'failed') == 1


def test_additive_merge_preserves_base_and_suppresses_conflicting_same_event():
    item = {'name':'Hemoglobin', 'numeric_value':10, 'subject':'index_patient',
            'time':{'text':'day one'}, 'evidence':[{'quote':'Hemoglobin was 10 on day one.'}]}
    base = {'items':[item], 'coverage':'complete', 'documentation_status':'documented'}
    conflict = dict(item, numeric_value=20)
    later = dict(item, numeric_value=11, time={'text':'day two'}, evidence=[{'quote':'11 on day two'}])
    original = copy.deepcopy(base)
    result, audit = n.merge_additions(base, {'items':[conflict,later], 'documentation_status':'documented'})
    assert base == original and result['items'][0] == item
    assert result['items'] == [item,later] and len(audit['duplicate_or_conflict'])==1


def test_hard_gates_reject_recall_gain_bought_with_old_field_corruption():
    p, extra = plans(); c = row(); a = copy.deepcopy(c)
    a.update(fbeta=.99, grounded_fbeta=.99)
    a['counts'] = a['grounded_counts'] = {'tp':10,'fp':1,'fn':1}
    a['aligned']['p1:0']['value:numeric_value'] = False
    gates = n.checks([c],[a],p,extra)
    assert not gates['passed'] and 'preserved_value' in gates['failed_checks']
    with pytest.raises(ValueError,match='coverage'): n.checks([c],[],p,extra)


def test_training_review_only_is_never_silently_removed():
    assert has_review_only({'items':[{'scoring':'review_only'}]})
    assert not has_review_only({'items':[]})


async def test_real_additive_adapter_reuses_baseline_and_preserves_separate_calls(tmp_path,monkeypatch):
    from test_pilot_extract import article, roster, setup_mock, empty_section
    from openpatients2.article_tasks import patient_packet
    from openpatients2.evidence_recovery import packet_segments
    from openpatients2.pilot_extract import PilotRunner
    import openpatients2.pilot_extract as pilot
    a=article(); packet=patient_packet(a,roster(a),roster(a)['patients'][0]); aid=a['article_id']
    p,extra=plans(); b.write(tmp_path/'manifest.json',{'gepa':p,'gepa_next':extra})
    b.write_rows(tmp_path/'reference/unified.jsonl',[{'article_id':aid,'patients':[{'patient_id':'p1','sections':{'observations':empty_section('observations')}}]}])
    b.write(tmp_path/'inputs'/aid/'gepa-packets.json',[{'packet':packet,'segments':packet_segments(packet)}])
    http,events=setup_mock(monkeypatch,a,server_max=65536)
    monkeypatch.setattr(pilot,'PilotRunner',lambda *args,**kwargs:PilotRunner(*args,**kwargs,http=http))
    args=(tmp_path,'observations','http://localhost:8000/v1',asyncio.get_running_loop(),time.monotonic()+30)
    baseline=n.MetricAdapter(*args)
    direct=n.SearchAdapter(*args,baseline=baseline,mode='direct',name='direct')
    additive=n.SearchAdapter(*args,baseline=baseline,mode='additive',name='additive')
    async with http:
        x,y=await asyncio.gather(direct.one(aid,{'instructions':n.COMPACT}),additive.one(aid,{'instructions':n.COMPACT}))
        assert await baseline.one(aid,g.SEED) is await baseline.one(aid,g.SEED)
    assert sum(e[0]=='complete' for e in events)==3
    assert x['grounded_fbeta']==y['grounded_fbeta']==1
    assert 'EXISTING:' in str(events)
    assert len(list((tmp_path/'results/gepa-next/observations').glob('*/calls/*/evaluation.json')))==3
    await asyncio.gather(*(a.close() for a in (baseline,direct,additive)))


async def test_pipeline_freezes_selection_before_outer_and_reports_all_arms(tmp_path,monkeypatch):
    p,extra=plans(); p['partition']={'train':['PMC1.1'],'search_validation':['PMC2.1'],'confirmation':['PMC3.1']}
    b.write(tmp_path/'manifest.json',{'gepa':p,'gepa_next':extra,'files':{}})
    b.write_rows(tmp_path/'reference/unified.jsonl',[])
    for aid in ['PMC1.1','PMC2.1','PMC3.1']:
        b.write(tmp_path/'inputs'/aid/'gepa-packets.json',[])
    seen=[]
    async def fake_one(self,aid,candidate):
        if aid=='PMC3.1':
            assert (tmp_path/'results/gepa-next'/self.task/'selection.json').exists()
        seen.append((self.task,self.phase,self.seed,aid))
        return row(aid) | {'predictions':{}, 'candidate':candidate,'reward':.7}
    async def fake_evolve(adapter,work,deadline):
        await adapter.one('PMC1.1',{'instructions':n.COMPACT})
        return [{'instructions':n.COMPACT}]
    monkeypatch.setattr(n.MetricAdapter,'one',fake_one)
    monkeypatch.setattr(n,'evolve',fake_evolve)
    await n.workload(tmp_path,'http://localhost:8000/v1',tmp_path/'results/workers/0/job-1')
    report=n.aggregate(tmp_path)
    assert report['complete'] and g.verify(tmp_path)['verified']
    assert len(report['tasks'])==2
    assert all(t['primary']=='control' for t in report['tasks'].values())
    assert not any(aid=='PMC3.1' for _,phase,_,aid in seen if phase=='search')
    trials=b.read(tmp_path/'results/gepa-next/observations/confirmation.json')
    assert len(trials)==10
    (tmp_path/'results/gepa-next/observations/done.json').unlink()
    assert not n.aggregate(tmp_path)['complete']
    with pytest.raises(ValueError,match='Incomplete'):g.verify(tmp_path)


def test_real_prepare_keeps_annotations_and_split_boundaries(tmp_path):
    root=Path(__file__).resolve().parents[1]
    reference=root/'configs/hipergator/dense-benchmark/reference.jsonl.gz'
    digest=b.sha(reference)
    work=tmp_path/'next'
    n.prepare(root,work,hours=6,metric_calls=100)
    m=b.check_snapshot(work)
    assert b.sha(reference)==digest and m['media']=={}
    train=[json.loads(l) for l in (work/'inputs/training-data/train.jsonl').read_text().splitlines()]
    ev=[json.loads(l) for l in (work/'inputs/training-data/eval.jsonl').read_text().splitlines()]
    assert train and ev
    assert {r['article_id'] for r in train} <= set(m['gepa']['partition']['train'])
    assert {r['article_id'] for r in ev} <= set(m['gepa']['partition']['search_validation'])
    assert not {r['article_id'] for r in train} & {r['article_id'] for r in ev}
    assert all('assistant'==r['completion'][0]['role'] for r in train)
    submission=b.submit(work,'account','qos',12,dry_run=True,script_name='dense_gepa_next.sbatch',worker_shards=range(1))
    commands=submission['commands_without_dependencies']
    assert sum('--gres=gpu:b200:1' in c for c in commands)==1
    assert '--time=12:00:00' in commands[1]


async def test_live_gate_skips_inference_but_preserves_baseline(tmp_path):
    from types import SimpleNamespace
    p,extra=plans()
    b.write(tmp_path/'manifest.json',{'gepa':p,'gepa_next':extra})
    b.write_rows(tmp_path/'reference/unified.jsonl',[])
    adapter=n.SearchAdapter(tmp_path,'observations','http://localhost:8000/v1',
        asyncio.get_running_loop(),time.monotonic()+10,baseline=None,mode='gated',name='gated')
    base={'coverage':'complete','items':[]}
    adapter.controls[('PMC1.1',42)]={'predictions':{'p':base}}
    adapter.gates[('PMC1.1',42)]=False
    # No runner methods exist: any accidental model/checker call fails this test.
    result=await adapter.extract_patient(SimpleNamespace(),'PMC1.1',
        {'patient_target':{'patient_id':'p'}},[],g.SEED)
    assert result['data']==base and result['data'] is not base
    assert result['attempts'][0]['mode']=='gate_skip'


async def test_phase_boundary_cancels_shielded_work_before_seed_changes(tmp_path):
    p,extra=plans();b.write(tmp_path/'manifest.json',{'gepa':p,'gepa_next':extra})
    b.write_rows(tmp_path/'reference/unified.jsonl',[])
    adapter=n.MetricAdapter(tmp_path,'observations','http://localhost:8000/v1',
        asyncio.get_running_loop(),time.monotonic()+10)
    settled=asyncio.Event()
    async def pending():
        try: await asyncio.sleep(20)
        finally: settled.set()
    task=asyncio.create_task(pending());adapter.inflight['pending']=task
    await asyncio.sleep(0)
    await adapter.settle()
    assert settled.is_set() and task.cancelled() and not adapter.inflight


async def test_actual_gepa_engine_accepts_new_adapter_and_shortlist(tmp_path,monkeypatch):
    p,extra=plans();b.write(tmp_path/'manifest.json',{'gepa':p,'gepa_next':extra})
    b.write_rows(tmp_path/'reference/unified.jsonl',[])
    adapter=n.MetricAdapter(tmp_path,'observations','http://localhost:8000/v1',
        asyncio.get_running_loop(),time.monotonic()+2000)
    async def fake_one(aid,candidate):
        return row(aid) | {'reward':.9 if candidate['instructions']=='General evidence rules.' else .5,
                          'feedback':[], 'errors':[], 'candidate':candidate}
    async def fake_reflect(candidate,dataset): return {'instructions':'General evidence rules.'}
    monkeypatch.setattr(adapter,'one',fake_one)
    monkeypatch.setattr(adapter,'reflect',fake_reflect)
    shortlist=await n.evolve(adapter,tmp_path,time.monotonic()+2000)
    assert shortlist[0]['instructions']=='General evidence rules.'
    assert b.read(adapter.result_root/'search.json')['metric_calls']>0


async def test_search_deadline_preserves_only_complete_inner_candidates(tmp_path,monkeypatch):
    import gepa
    p,extra=plans();b.write(tmp_path/'manifest.json',{'gepa':p,'gepa_next':extra})
    b.write_rows(tmp_path/'reference/unified.jsonl',[])
    adapter=n.MetricAdapter(tmp_path,'observations','http://localhost:8000/v1',
        asyncio.get_running_loop(),time.monotonic()+2000)
    adapter.cache={'c':row('c')|{'candidate':{'instructions':'Complete.'},'reward':.5},
                   'd':row('d')|{'candidate':{'instructions':'Complete.'},'reward':.5},
                   'incomplete':row('c')|{'candidate':{'instructions':'Unfinished.'},'reward':1.}}
    def timeout(**kwargs): raise TimeoutError('deadline')
    monkeypatch.setattr(gepa,'optimize',timeout)
    assert await n.evolve(adapter,tmp_path,time.monotonic()+2000)==[{'instructions':'Complete.'}]


def test_aggregate_cli_does_not_mutate_its_log_after_integrity_freeze(tmp_path,monkeypatch,capsys):
    called=[]
    monkeypatch.setattr(n,'aggregate',lambda work:called.append('aggregate'))
    monkeypatch.setattr(b,'export',lambda work:called.append('export'))
    n.main(['aggregate','--work',str(tmp_path)])
    assert called==['aggregate','export'] and capsys.readouterr().out==''
