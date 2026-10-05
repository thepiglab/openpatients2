import copy
import json
from pathlib import Path
import time

import pytest

from openpatients2.bundle_quality import (align_patients, score_bundle, subset_reference,
    timeline_probes, validate_bundle_gold)
from openpatients2.bundle_report import pooled_rate
from openpatients2.clinical_normalization import normalize_clinical
from openpatients2.corpus_pilot import prepare, campaign_phases, job_command
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


def test_identity_matching_handles_id_permutation_without_using_clinical_answers():
    ref=identity_reference();quotes=[i['evidence'][0]['quote'] for i in ref['articles'][0]['identities']]
    originals=[patient('p1','b',quotes[1]),patient('p2','a',quotes[0])]
    originals[0]['sections']={'demographics':{'items':[{'numeric_value':70}]}}  # deliberately wrong answer
    rows,receipt=align_patients(ref,originals)
    assert rows[0]['source']['record_id']=='PMC1.1:p2'
    assert rows[1]['source']['record_id']=='PMC1.1:p1'
    assert receipt[0]['identity_matched']==2
    assert originals[0]['source']['record_id']=='PMC1.1:p1'
    # Duplicated and uncertain identity cannot be resolved by an answer oracle.
    duplicate=copy.deepcopy(originals[0]);duplicate['source']['record_id']='PMC1.1:p3'
    _,receipt=align_patients(ref,originals+[duplicate])
    assert receipt[0]['identity_matched']==1 and receipt[0]['unmapped_or_ambiguous']==2


def test_failed_negative_discovery_is_not_a_correct_negative_case():
    ref={'checks':[],'articles':[{'article_id':'PMC1.1','text_sha256':'hash','expected_count':0,'species':[],'identities':[]}]}
    assert score_bundle(ref,[])['parts']['identity']['matched']==0
    rows={'articles':[{'article_id':'PMC1.1','text_sha256':'hash','status':'valid','roster':{'patients':[]}}]}
    assert score_bundle(ref,[],discovery=rows)['parts']['identity']['matched']==1
    rows['articles'][0]['status']='failed'
    assert score_bundle(ref,[],discovery=rows)['parts']['identity']['matched']==0


def course():
    return {'record_id':'PMC1.1:p1','nodes':[
        {'id':'a','pattern':{'description':{'regex':'tracheostomy'},'occurrence':'occurred'},'source':[{'segment_id':'s'}]},
        {'id':'b','pattern':{'description':{'regex':'surgery'},'occurrence':'occurred'},'source':[{'segment_id':'s'}]}],
        'edges':[{'from':'a','to':'b','relation':'before'}]}


def graph():
    return {'events':[{'event_id':'x','description':'Tracheostomy on day 55','occurrence':'occurred','evidence':[{'segment_id':'s'}]},
        {'event_id':'z','description':'Repair surgery on day 70','occurrence':'occurred','evidence':[{'segment_id':'s'}]}],
        'edges':[{'from_event_id':'x','to_event_id':'z','relation':'before'}]}


def test_course_checks_require_real_order_not_event_list_order_or_planned_care():
    g=graph();assert timeline_probes(course(),g)['relations_matched']==1
    g['events'].reverse();assert timeline_probes(course(),g)['relations_matched']==1
    g['edges']=[];assert timeline_probes(course(),g)['relations_matched']==0
    g['edges']=[{'from_event_id':'z','to_event_id':'x','relation':'before'}]
    tested=timeline_probes(course(),g)
    assert tested['reversed_relations']==1 and tested['relations_matched']==0
    g=graph();g['events'][1]['occurrence']='planned'
    assert timeline_probes(course(),g)['nodes_matched']==1
    g=graph();g['events']=[{**g['events'][0],'description':'Tracheostomy and surgery'}]
    assert timeline_probes(course(),g)['nodes_matched']==0  # cannot collapse distinct encounters


def test_transitive_before_relations_are_credited_but_cycles_are_not():
    g=graph();g['events'].append({'event_id':'middle','description':'Another step','occurrence':'occurred','evidence':[]})
    g['edges']=[{'from_event_id':'x','to_event_id':'middle','relation':'before'},
                {'from_event_id':'middle','to_event_id':'z','relation':'before'}]
    assert timeline_probes(course(),g)['relations_matched']==1
    g['edges'].append({'from_event_id':'z','to_event_id':'x','relation':'before'})
    assert timeline_probes(course(),g)['relations_matched']==0


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


def test_global_prompt_groups_cover_every_operational_component_without_parent_resets():
    from openpatients2.bundle_optimization import GroupedComponents
    from openpatients2.prompt_optimization import COMPONENTS
    selector=GroupedComponents(COMPONENTS);seen=[]
    for i in range(7):seen+=selector(None,[],[],i,dict.fromkeys(COMPONENTS,''))
    assert set(seen)==set(COMPONENTS) and len(seen)==len(set(seen))==29
    assert all(selector.proposed[key]==1 for key in COMPONENTS)
    assert selector(None,[],[],0,{})==selector.groups[0]


def test_group_reflection_returns_all_requested_rewrites_with_the_actual_instructions(tmp_path,monkeypatch):
    import openpatients2.bundle_optimization as module
    seen=[]
    class Runner:
        def __init__(self,*args):pass
        async def close(self):pass
        async def call(self,task,messages,checker,identity,*args):
            seen.extend(messages)
            assert identity['optimization_components']==['summary','timeline_v2']
            return {'status':'valid','data':checker({'instructions':{
                'summary':'Preserve clinically important treatment changes.',
                'timeline_v2':'Connect source-supported treatment changes in relative order.'}})}
    monkeypatch.setattr(module,'PilotRunner',Runner)
    adapter=module.BundleAdapter([article()],tmp_path/'unused',{},['endpoint'],65536,tmp_path,{'checks':[]},time.monotonic()+30)
    trace=[{'Feedback':{'invoked_strategies':{'summary':'Actual original summary instructions.'}}}]
    changed=adapter.propose_new_texts({'summary':'','timeline_v2':''},
        {'summary':trace,'timeline_v2':trace},['summary','timeline_v2'])
    assert set(changed)=={'summary','timeline_v2'}
    assert 'Actual original summary instructions.' in seen[-1]['content']
    assert seen[-1]['content'].count('Actual original summary instructions.')==1


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


def test_new_benchmark_sources_labels_splits_and_slurm_allocations_are_valid(tmp_path):
    articles=list(read_jsonl(ROOT/'benchmarks/patient-bundle/articles.jsonl.gz'))
    reference=json.loads((ROOT/'benchmarks/patient-bundle/reference.json').read_text())
    assert validate_bundle_gold(reference,articles)
    assert len(articles)==31 and len(reference['checks'])==305
    train=subset_reference(reference,reference['optimization_splits']['train'])
    assert not set(reference['optimization_splits']['test']) & {a['article_id'] for a in train['articles']}
    assert 'optimization_splits' not in train
    bad=copy.deepcopy(reference);bad['checks'][0]['pattern']['invented_field']='x'
    with pytest.raises(ValueError,match='Impossible'):validate_bundle_gold(bad,articles)
    bad=copy.deepcopy(reference);bad['bundle_gold']['timelines'][0]['nodes'][0]['source'][0]['quote']='fabrication'
    with pytest.raises(ValueError,match='Nonliteral'):validate_bundle_gold(bad,articles)
    c=prepare(ROOT,tmp_path/'work',ROOT/'configs/pilot/overnight-bundle.yaml')
    assert campaign_phases(c)==['cpu','setup','download','bootstrap','gepa','gpu','cleanup','report','source-cleanup']
    for phase in ['cpu','setup','download','cleanup','report','source-cleanup']:assert '--gres=none' in job_command(c,phase)
    assert '--gres=gpu:b200:1' in job_command(c,'gepa')
    assert '--gres=gpu:b200:8' in job_command(c,'gpu') and '--nodes=1' in job_command(c,'gpu')
    assert '--cpus-per-task=32' in job_command(c,'gpu') and '--mem=250G' in job_command(c,'gpu')


def test_pooled_throughput_uses_wall_time_and_never_treats_missing_usage_as_zero():
    reports=[{'tokens':{'output_tokens':100,'wall_seconds':1}}, {'tokens':{'output_tokens':100,'wall_seconds':10}}]
    assert pooled_rate(reports)==pytest.approx(200/11)
    reports[1]['tokens']['output_tokens']=None
    assert pooled_rate(reports) is None


def test_glued_license_prose_is_not_invented_as_a_jurisdiction():
    assert license_identity('https://creativecommons.org/licenses/by/4.0/This') is None
    assert license_identity('https://creativecommons.org/licenses/by-nc-sa/3.0/us/')['jurisdiction']=='us'


def test_missing_media_is_not_removed_from_the_objective_and_pixels_require_the_original_hash():
    reference=json.loads((ROOT/'benchmarks/patient-bundle/reference.json').read_text())
    gold=reference['bundle_gold']['pixels'][0]
    ref=subset_reference(reference,[gold['article_id']])
    absent=score_bundle(ref,[])
    assert absent['parts']['figure_ownership']['required']>0
    assert absent['parts']['figure_ownership']['matched']==0
    panels=[{'panel':label,'image_kind':gold['image_kind'],'has_chart':False} for label in gold['panel_labels']]
    row={'identity':{'article_id':gold['article_id'],'figure_id':gold['figure_id']},
         'pixels_inspected':True,'pixel_provenance':{'sha256':gold['image_sha256']},
         'visual':{'data':{'panels':panels}}}
    assert score_bundle(ref,[],pixel_rows=[row])['pixels'][0]['matched']
    row['visual']['data']['panels'][1]['panel']=panels[0]['panel']
    assert not score_bundle(ref,[],pixel_rows=[row])['pixels'][0]['matched']
    row['pixel_provenance']['sha256']='0'*64
    assert not score_bundle(ref,[],pixel_rows=[row])['pixels'][0]['available']
    assert score_bundle(ref,[],pixel_rows=[row])['parts']['pixel_inventory']['required']==1


def test_media_ownership_prefers_usable_pixels_independent_of_task_file_order():
    ref=identity_reference();quote=ref['articles'][0]['identities'][0]['evidence'][0]['quote']
    ref['figure_checks']=[{'id':'f1','article_id':'PMC1.1','figure_id':'F1','panel':None,
        'patient_ids':['p1'],'scope':'individual'}]
    def task(name,ids):return {'task':name,'identity':{'article_id':'PMC1.1','figure_id':'F1'},
        'data':{'assignments':[{'panel':None,'patient_ids':ids,'scope':'individual'}]}}
    pixels=task('pixel_attribution',['p1']);caption=task('figure_attribution',['p2'])
    for rows in [[pixels,caption],[caption,pixels]]:
        result=score_bundle(ref,[patient('p1','a',quote)],visual_rows=rows)
        assert result['parts']['figure_ownership']['matched']==1
    failed=task('pixel_attribution',[]);failed['data']=None
    assert score_bundle(ref,[patient('p1','a',quote)],visual_rows=[pixels,failed])['parts']['figure_ownership']['matched']==1


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


def test_real_gepa_regenerates_program_and_excludes_test_sources_and_labels(tmp_path,monkeypatch):
    import openpatients2.bundle_optimization as module
    from openpatients2.data import write_json
    from test_pilot_extract import demographics
    articles=[];reference={'checks':[],'articles':[],'optimization_splits':{
        'train':['PMC1.1','PMC2.1','PMC3.1','PMC4.1'],
        'validation':['PMC5.1','PMC6.1'],'test':['PMC7.1','PMC8.1']}}
    for i in range(1,9):
        a=article();a['article_id']=f'PMC{i}.1';articles.append(a)
        span={'segment_id':a['segments'][0]['segment_id'],'quote':a['segments'][0]['text']}
        reference['articles'].append({'article_id':a['article_id'],'text_sha256':a['text_sha256'],
            'expected_count':1,'species':['human'],'count_adjudication':'source_checked','disposition':'individual_cases',
            'identities':[{'patient_id':'p1','evidence':[span]}],'source':[span]})
        reference['checks'].append({'id':f'c{i}','record_id':a['article_id']+':p1','task':'demographics','collection':'items',
            'pattern':{'attribute':'age_at_presentation','numeric_value':42},'kind':'required','category':'age',
            'description':'Documented age','source':[span],'text_sha256':a['text_sha256'],'xml_sha256':a['xml_sha256']})
    sample=input_file(tmp_path,articles);refpath=tmp_path/'reference.json';write_json(refpath,reference)
    executions=[];reflections=[];by={a['article_id']:a for a in articles}
    async def pipeline(cfg,sample,destination,endpoints,context,arm,**kwargs):
        assert not kwargs.get('frozen_rosters') and not kwargs.get('cached_rosters')
        assert cfg['max_repair_rounds']==2 and cfg['prompt_mode']=='rewrite' and cfg['scatter_calls']
        assert not set(cfg['article_ids']) & set(reference['optimization_splits']['test'])
        executions.append(dict(cfg));destination=Path(destination);destination.mkdir(parents=True)
        (destination/'tasks').mkdir();patients=[];predicted=[]
        for aid in cfg['article_ids']:
            a=by[aid];target=roster(a)['patients'][0];target['identity_evidence']=reference['articles'][int(aid[3:-2])-1]['source']
            section=demographics(a);section['items']=section['items'][:1] if cfg['prompt_overrides']['demographics'] else []
            patients.append({'source':{'record_id':aid+':p1','article_source':a,'patient_target':target},
                'sections':{'demographics':section},'companions':{}})
            predicted.append({'article_id':aid,'text_sha256':a['text_sha256'],'status':'valid','roster':roster(a)})
        patients_path=destination/'patients.jsonl';patients_path.write_text(''.join(json.dumps(p)+'\n' for p in patients))
        rosters_path=destination/'rosters.json';write_json(rosters_path,{'articles':predicted})
        return {'outputs':{'patients':str(patients_path),'predicted_rosters':str(rosters_path)}}
    def propose(self,candidate,dataset,components_to_update,**kwargs):
        assert set(components_to_update)=={'demographics','case_context'}
        reflections.append(json.dumps(dataset));return {
            'demographics':'Retain documented age with exact source evidence.',
            'case_context':'Preserve source-supported patient identity and species.'}
    monkeypatch.setattr(module,'run_pilot',pipeline);monkeypatch.setattr(module,'COMPONENTS',['demographics','case_context'])
    monkeypatch.setattr(module.BundleAdapter,'propose_new_texts',propose)
    prompts,report=module.optimize_bundle(sample,tmp_path/'optimization',{'max_repair_rounds':2},
        ['http://localhost:8000/v1'],65536,refpath,{},seconds=30,calls=32)
    assert prompts['demographics'] and not report['selected_original']
    assert report['baseline_validation_facts']==0 and report['confirmation_validation_facts']==2
    assert report['full_program_regenerated'] and report['clinical_nonregression_gate_passed']
    assert all(report['component_proposals'][key]>0 for key in ['demographics','case_context'])
    assert len(executions)>3 and reflections
    assert all('PMC7.1' not in text and 'PMC8.1' not in text for text in reflections)


def test_family_rollouts_use_deployment_repair_policy(tmp_path,monkeypatch):
    from openpatients2.prompt_optimization import ClinicalAdapter
    from openpatients2.pilot_extract import PilotRunner
    from openpatients2.pilot_review import inventory_messages
    a=article();p=roster(a)['patients'][0];sid=a['segments'][0]['segment_id'];calls=[]
    bad={'features':[{'task':'demographics','feature_as_documented':'42 years','state':'present',
        'episode_as_documented':'day 4','evidence':[{'segment_id':sid,'quote':'A 42-year-old woman'}]}],'limitations':[]}
    good=copy.deepcopy(bad);good['features'][0]['episode_as_documented']=None
    def respond(task,messages):calls.append(messages);return bad if len(calls)==1 else good
    http,_=setup_mock(monkeypatch,a,responses=respond)
    init=PilotRunner.__init__
    def wrapper(self,*args,**kwargs):kwargs['http']=http;init(self,*args,**kwargs)
    monkeypatch.setattr(PilotRunner,'__init__',wrapper)
    e={'article':a,'patient':{'source':{'patient_target':p}},'request':inventory_messages(a,p),'facts':[],
        'row':{'task':'clinical_inventory','identity':{'article_id':a['article_id'],'record_id':a['article_id']+':p1'},
            'attempts':[{'errors':['Nonliteral episode']}]} }
    adapter=ClinicalAdapter([e],{'max_repair_rounds':2,'max_output_tokens':256,'max_retry_tokens':256,'prompt_mode':'rewrite'},
        ['http://localhost:8000/v1'],8192,tmp_path/'family',{'checks':[]},'clinical_inventory',time.monotonic()+30,
        {'deployment_matched':True})
    result=adapter.evaluate([e],{'clinical_inventory':''},True)
    assert result.outputs==[good] and len(calls)==2
    assert result.scores==[0]  # no structural-only medical reward
    tasks=[json.loads(p.read_text()) for p in (tmp_path/'family').rglob('tasks/*.json')]
    assert tasks[0]['status']=='valid' and tasks[0]['attempts'][1]['mode']=='repair'
