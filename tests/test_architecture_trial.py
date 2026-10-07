"""Behavioral and integration checks; model semantics still require real evaluation."""
from copy import deepcopy
import json
from pathlib import Path
import pytest
import yaml

from openpatients2.architecture_trial import (
    blind_questions, check_indexed, Answers, gated_sections, check_reading,
    unit_chunks, verify_batch, additive_merge, ARMS)
from openpatients2.architecture_campaign import corrected_baseline
from openpatients2.component_trial import run_component_trial
from openpatients2.table_cells import materialize_selections, inventory, check_cell_batch
from openpatients2 import frontier_campaign as fc, controlled_campaign as cc
from test_component_trial import table_article, observation
from test_pilot_extract import roster, setup_mock, input_file, answer, demographics
from test_controlled_campaign import campaign, assignment, outputs


def test_blind_questions_hide_values_times_and_previous_reasoning():
    rows=[{'task':'observations','candidate':{'name':'sodium','numeric_value':987654,
        'time':{'text':'secret encounter'},'evidence':[{'quote':'secret rationale'}]}}]
    text=json.dumps(blind_questions(rows))
    assert 'sodium' in text
    assert not any(x in text for x in ('987654','secret encounter','secret rationale'))


def test_answer_contract_rejects_missing_duplicate_and_nonliteral_evidence():
    src=[{'segment_id':'s1','text':'Sodium 115 mmol/L'}]
    row={'question_id':'q0','answer':'115 mmol/L','status':'answered','evidence':[{'segment_id':'s1','quote':'115 mmol/L'}]}
    assert check_indexed({'answers':[row]},Answers,'answers',['q0'],src)
    for rows in ([],[row,row],[{**row,'evidence':[]}],[{**row,'evidence':[{'segment_id':'s1','quote':'140 mmol/L'}]}]):
        with pytest.raises(ValueError):check_indexed({'answers':rows},Answers,'answers',['q0'],src)


@pytest.mark.asyncio
async def test_uncertainty_expands_context_and_failed_expansion_cannot_accept():
    class Runner:
        def __init__(self):self.calls=[]
        async def call(self,task,messages,checker,identity,replica,segments):
            self.calls.append((task,identity['phase'],deepcopy(messages)))
            if identity['phase']=='expanded':return {'data':None,'status':'failed'}
            if task=='independent_source_answers':
                value={'answers':[{'question_id':'q0','answer':'Insufficient context','status':'unresolved','evidence':[]}]}
            else:value={'decisions':[{'question_id':'q0','support':'unresolved','reason':'Need context','evidence':[]}]}
            return {'data':checker(value),'status':'valid'}
    article={'segments':[{'segment_id':f's{i}','text':f'text {i}'} for i in range(8)]}
    row={'review_id':'f1','task':'observations','candidate':{'name':'sodium','numeric_value':999},'evidence_spans':[{'segment_id':'s3'}]}
    runner=Runner();decisions=await verify_batch(runner,article,{},[row],{'article_id':'a'},0)
    assert decisions[0]['context_expanded'] and decisions[0]['support']=='unresolved'
    assert [c[1] for c in runner.calls]==['local','local_reconcile','expanded']
    assert '999' not in json.dumps(runner.calls[0][2])
    assert '999' in json.dumps(runner.calls[1][2])


@pytest.mark.asyncio
async def test_reconciler_cannot_promote_unresolved_blind_answer():
    class Runner:
        async def call(self,task,messages,checker,identity,replica,segments):
            if task=='independent_source_answers':
                value={'answers':[{'question_id':'q0','answer':'unknown','status':'unresolved','evidence':[]}]}
            else:value={'decisions':[{'question_id':'q0','support':'explicit','reason':'unsupported agreement',
                'evidence':[{'segment_id':'s1','quote':'source'}]}]}
            return {'data':checker(value),'status':'valid'}
    row={'review_id':'f','task':'conditions','candidate':{'name':'cancer'},'evidence_spans':[]}
    result=await verify_batch(Runner(),{'segments':[{'segment_id':'s1','text':'source'}]}, {},[row],{},0)
    assert result[0]['support']=='unresolved'


def test_accepted_view_preserves_original_and_reindexes_without_dropping_neighbors():
    from test_pilot_extract import empty_section
    a=table_article();obs=observation(inventory(a)['cells'][0])
    section=empty_section('observations');section.update(documentation_status='documented',items=[obs,{**obs,'numeric_value':999}])
    sections={'observations':section};saved=deepcopy(sections)
    rows=[{'review_id':f'f{i}','task':'observations','pointer':f'/items/{i}','candidate':v} for i,v in enumerate(section['items'])]
    selected,excluded=gated_sections(sections,rows,[{'fact_id':'f0','support':'explicit'},{'fact_id':'f1','support':'contradicted'}])
    assert sections==saved and selected['observations']['items']==[obs]
    assert excluded[0]['candidate']['numeric_value']==999


def test_source_inventory_accounts_for_excluded_units_and_rejects_invented_time():
    a=table_article();chunk=unit_chunks(a)[0];fragment=chunk['fragments'][0]
    data={'encounters':[{'encounter_id':'e1','description':'presentation','phase':'presentation',
        'evidence':[{'segment_id':s['segment_id'],'quote':s['text']} for s in chunk['fragments']],
        'attribution_evidence':[{'segment_id':fragment['segment_id'],'quote':fragment['text']}],
        'time_text':None,'state_changes':[],'actions':[],'tasks':['demographics']}],
        'dispositions':[{'unit_id':s['unit_id'],'status':'clinical','encounter_ids':['e1'],'reason':'source'} for s in chunk['fragments']]}
    assert check_reading(data,chunk)
    bad=deepcopy(data);bad['dispositions'].pop()
    with pytest.raises(ValueError,match='Every'):check_reading(bad,chunk)
    bad=deepcopy(data);bad['encounters'][0]['time_text']='invented three months'
    with pytest.raises(ValueError,match='literal'):check_reading(bad,chunk)


def test_compiler_owns_numeric_fields_and_refuses_missing_or_other_patient_cells():
    a=table_article();p=roster(a)['patients'][0];cells=inventory(a)['cells']
    decisions={'cells':[{'cell_id':c['cell_id'],'status':'extracted','name':'Sodium','kind':'laboratory','reason':'Source'} for c in cells]}
    result=materialize_selections(decisions,cells,a,p,lambda s:s)
    assert len(result['accepted'])==1
    obs=result['accepted'][0]['observation']
    assert (obs['numeric_value'],obs['unit'],obs['flag'])==(139,'mmol/L','unknown')
    assert obs['evidence'][0]['quote']==cells[0]['source_quote']
    bad=deepcopy(decisions);bad['cells'][0]['numeric_value']=999
    with pytest.raises(ValueError):materialize_selections(bad,cells,a,p,lambda s:s)
    missing=deepcopy(cells[:1]);missing[0].update(raw_value='–',measurement=None)
    assert not materialize_selections({'cells':decisions['cells'][:1]},missing,a,p,lambda s:s)['accepted']


def test_legacy_heading_recovery_requires_exact_full_cell_row():
    a=table_article();p=roster(a)['patients'][0];cell=inventory(a)['cells'][0]
    heading=next(s['heading'] for s in a['segments'] if s['segment_id']==cell['segment_id'])
    obs=observation(cell);obs['evidence'][0]['source_section']=heading
    value={'cells':[{'cell_id':cell['cell_id'],'status':'extracted','observation':obs,'reason':'source'}]}
    assert len(check_cell_batch(value,[cell],a,p,lambda s:s)['accepted'])==1
    value['cells'][0]['observation']['evidence'][0]['quote']='139'
    assert not check_cell_batch(value,[cell],a,p,lambda s:s)['accepted']


def architecture_campaign(tmp_path):
    c=campaign(tmp_path)
    c['config'].update(campaign_kind='clinical-architecture',gpu_minutes=120,
                       variants=['live-complete',*ARMS])
    return c


def test_architecture_plan_retains_independent_gpu_resources_and_32_trials(tmp_path):
    c=architecture_campaign(tmp_path);assert fc.validate_plan(c['config'])
    assert fc.variants(c)==('live-complete',*ARMS)
    jobs=[fc.job_command(c,p,s) for p,s in fc.stages(c)]
    gpu=[j for j in jobs if '--gres=gpu:b200:1' in j]
    assert len(gpu)==4 and all('--time=02:00:00' in j for j in gpu)
    assert all('--cpus-per-task=8' in j and '--mem=60G' in j for j in gpu)
    result=fc.aggregate(c)
    assert result['planned_trials']==32 and result['status']=='failed'
    assert (Path(c['work'])/'ARCHITECTURE.md').exists()
    with pytest.raises(ValueError):cc.validate_plan({**c['config'],'gpu_minutes':240})


def mock_architecture_reply(a, *, false_accept=False, corrupt_real=False):
    fixtures=json.loads(Path('benchmarks/architecture/gate-probes.json').read_text())
    def reply(task,messages):
        if task=='demographics':return demographics(a)
        if task=='table_observations':
            cells=json.loads(messages[1]['content'].split('\nCELLS:\n')[1].split('\nSCHEMA:\n')[0])
            return {'cells':[{'cell_id':c['cell_id'],'status':'extracted','name':'Sodium','kind':'laboratory','reason':'Source'} for c in cells]}
        if task in {'independent_source_answers','independent_source_reconcile','encounter_reading'}:
            data=json.loads(messages[1]['content'].split('\nSCHEMA:\n')[0]);s=data['source'][0]
            evidence=[{'segment_id':q['segment_id'],'quote':q['text']} for q in data['source']]
            if task=='independent_source_answers':
                return {'answers':[{'question_id':q['question_id'],'answer':'Source-based answer','status':'answered','evidence':evidence} for q in data['questions']]}
            if task=='independent_source_reconcile':
                rows=[]
                for c in data['candidates']:
                    fixture=next((f for f in fixtures if f['source']==data['source'] and f['candidate']==c['value']),None)
                    good=fixture['expected_accept'] if fixture else not corrupt_real
                    rows.append({'question_id':c['question_id'],'support':'explicit' if good or false_accept else 'contradicted',
                                 'reason':'Authored test verdict','evidence':evidence})
                return {'decisions':rows}
            return {'encounters':[{'encounter_id':'e1','description':'Authored patient encounter','phase':'presentation',
                'evidence':evidence,'attribution_evidence':evidence,'time_text':None,'state_changes':[],'actions':[],'tasks':['demographics']}],
                'dispositions':[{'unit_id':s['unit_id'],'status':'clinical','encounter_ids':['e1'],'reason':'source'} for s in data['source']]}
        return answer(task,a)
    return reply


@pytest.mark.asyncio
@pytest.mark.parametrize('component',ARMS)
async def test_real_runner_architecture_arms_use_same_corrected_baseline_and_export_audits(tmp_path,monkeypatch,component):
    a=table_article();http,events=setup_mock(monkeypatch,a,responses=mock_architecture_reply(a))
    sample=input_file(tmp_path,[a]);config={'max_articles':1,'max_output_tokens':256,'max_retry_tokens':256}
    base=tmp_path/'baseline'
    async with http:
        report=await corrected_baseline(config,sample,base,['http://localhost:8000/v1'],8192,'targeted',http=http,seed=42)
        assert report['gate_qualification']['qualified']
        original=(base/'patients.jsonl').read_text();baseline=json.loads(original)
        assert baseline['sections']['observations']['items'][0]['numeric_value']==139
        count=len([e for e in events if e[0]=='complete']);assert report['model_calls']==count
        assert report['tokens']['output_tokens']==20*count
        assert (base/'raw-baseline/attempts.jsonl').exists()
        out=tmp_path/component
        result=await run_component_trial(config,sample,out,['http://localhost:8000/v1'],8192,
                                         baseline_dir=base,component=component,seed=42,http=http)
    assert (base/'patients.jsonl').read_text()==original
    delivered=json.loads((out/'patients.jsonl').read_text())
    assert delivered['vision']==baseline['vision']
    assert (out/'rosters.json').read_bytes()==(base/'rosters.json').read_bytes()
    assert result['model_calls']==len([e for e in events if e[0]=='complete'])-count
    receipt=json.loads(next((out/'architecture').glob('patient-*.json')).read_text())
    assert receipt['gate_applied'] and receipt['decisions']
    if component=='encounter-state':
        assert receipt['reading']['chunks']
        assert delivered['companions']['summary'] is None
    assert not delivered['clinical_accuracy_verified']


@pytest.mark.asyncio
async def test_unqualified_gate_cannot_filter_even_when_real_fact_is_challenged(tmp_path,monkeypatch):
    a=table_article();http,_=setup_mock(monkeypatch,a,responses=mock_architecture_reply(a,false_accept=True))
    sample=input_file(tmp_path,[a]);config={'max_articles':1,'max_output_tokens':256,'max_retry_tokens':256}
    base=tmp_path/'baseline'
    async with http:
        baseline=await corrected_baseline(config,sample,base,['http://localhost:8000/v1'],8192,'targeted',http=http,seed=42)
        assert not baseline['gate_qualification']['qualified']
        # Change reply after qualification: the gate now challenges every real fact.
        http2,_=setup_mock(monkeypatch,a,responses=mock_architecture_reply(a,corrupt_real=True))
        async with http2:
            await run_component_trial(config,sample,tmp_path/'candidate',['http://localhost:8000/v1'],8192,
                 baseline_dir=base,component='source-verification',seed=42,http=http2)
    original=json.loads((base/'patients.jsonl').read_text());delivered=json.loads((tmp_path/'candidate/patients.jsonl').read_text())
    assert delivered['sections']==original['sections']
    receipt=json.loads(next((tmp_path/'candidate/architecture').glob('patient-*.json')).read_text())
    assert not receipt['gate_applied'] and receipt['excluded_candidates']
    assert receipt['candidate_sections']==original['sections']


def test_cpu_shards_retain_gate_fixtures_and_export_them(tmp_path,monkeypatch):
    import gzip,tarfile
    from openpatients2.data import write_json
    c=architecture_campaign(tmp_path);work=Path(c['work'])
    (work/'profile').mkdir();(work/'vision-assets').mkdir()
    with gzip.open(work/'profile/sample.jsonl.gz','wt') as f:
        for i in range(31):f.write(json.dumps({'article_id':f'PMC{i}.1'})+'\n')
    write_json(work/'vision-assets/manifest.json',{'figures':[]})
    monkeypatch.setattr(fc,'verify_cpu',lambda c:{'status':'ready','hashes':{}})
    fc.prepare_shards(c)
    assert 'benchmark/gate-probes.json' in json.loads((work/'cpu.json').read_text())['hashes']
    write_json(work/'shards/000/trials/seed42/encounter-state/architecture/patient.json',{'kept':True})
    write_json(work/'shards/000/trials/seed42/raw-baseline-building/report.json',{'interrupted':True})
    fc.aggregate(c)
    archive=fc.export_results(c,tmp_path/'review.tar.gz')
    with tarfile.open(archive['output']) as f:
        names=f.getnames()
        assert 'benchmark/gate-probes.json' in names and 'architecture-summary.json' in names
        assert 'shards/000/trials/seed42/encounter-state/architecture/patient.json' in names
        assert 'shards/000/trials/seed42/raw-baseline-building/report.json' in names


def test_column_shuffle_preserves_identity_and_result():
    a=table_article();p=roster(a)['patients'][0]
    for s in a['segments']:
        if s['kind']=='table_row':
            s['text']=s['text'].replace('Case 1 | Case 2','Case 2 | Case 1').replace('139 | 143','143 | 139')
    cells=inventory(a)['cells']
    assert cells[0]['column_header']=='Case 2'
    rows={'cells':[{'cell_id':c['cell_id'],'status':'extracted','name':'Sodium','kind':'laboratory','reason':'Source'} for c in cells]}
    result=materialize_selections(rows,cells,a,p,lambda s:s)
    assert len(result['accepted'])==1 and result['accepted'][0]['observation']['numeric_value']==139


def test_attempted_aborted_action_preserves_separate_outcome_and_rejects_plan_completion():
    a=table_article();chunk=unit_chunks(a)[0];s=chunk['fragments'][0]
    evidence=[{'segment_id':q['segment_id'],'quote':q['text']} for q in chunk['fragments']]
    encounter={'encounter_id':'e','description':'test','phase':'intervention','time_text':None,
               'evidence':evidence,'attribution_evidence':evidence,'tasks':['procedures_devices'],
               'state_changes':[], 'actions':[{'name':'retrieval','initiation':'started','completion':'aborted','outcome':'failed','evidence':evidence}]}
    value={'encounters':[encounter],'dispositions':[{'unit_id':x['unit_id'],'status':'clinical','encounter_ids':['e'],'reason':'test'} for x in chunk['fragments']]}
    checked=check_reading(value,chunk)
    assert checked['encounters'][0]['actions'][0]['outcome']=='failed'
    value['encounters'][0]['actions'][0]['initiation']='planned'
    with pytest.raises(ValueError,match='non-started'):check_reading(value,chunk)


@pytest.mark.asyncio
async def test_projection_sees_only_its_chunk_and_identity_context():
    from openpatients2.architecture_trial import project_reading
    from test_pilot_extract import empty_section
    a=table_article();p=roster(a)['patients'][0];original=deepcopy(a)
    a['segments'].append({'segment_id':'last','heading':'Other','kind':'paragraph','text':'UNRELATED_SENTINEL'})
    chunk=unit_chunks(original)[0]
    reading={'chunks':[{'chunk':chunk,'data':{'encounters':[{'tasks':['demographics']}],
        'dispositions':[{'status':'clinical'}]},'status':'valid'}]}
    class Runner:
        schemas={k:v.model_json_schema() for k,v in __import__('openpatients2.schemas',fromlist=['TASK_MODELS']).TASK_MODELS.items()}
        def clinical_checker(self,*args):return lambda v:v
        async def call(self,task,messages,checker,identity,replica,segments,**kwargs):
            assert 'UNRELATED_SENTINEL' not in json.dumps(messages)
            assert identity['chunk_id']==chunk['chunk_id']
            return {'data':empty_section(task),'status':'valid','errors':[]}
    results=await project_reading(Runner(),a,roster(a),p,reading,{'article_id':a['article_id']},0)
    assert results['demographics']['chunk_calls']==1
    assert results['conditions']['data'] is None
    assert results['observations']['chunk_calls']==1 # Tables bypass model routing exclusions
