import copy
import json
import pytest
from openpatients2.ehr_seeds import seed_patient, export_seeds
from openpatients2.demo import example_record, fixture_sections, SOURCE
from openpatients2.schemas import TASK_MODELS
from openpatients2.validation import validate, iter_objects


def patient():
    source=example_record()
    source.update(article_source={'article_id':'PMC1.1','license':{'allowed':True,'code':'CC BY'},'supplements':[]},
        patient_target={'patient_id':'p1','species':'human'},
        packet_spans=[{'segment_id':'b00001','start':0,'end':len(SOURCE)}])
    quote='Osimertinib 80 mg orally daily was started for the lung cancer.'
    event={'event_id':'e1','description':'Osimertinib treatment','kind':'treatment','time_text':None,
        'relation':'unknown','anchor_event_id':None,'offset_min':None,'offset_max':None,'unit':None,
        'precision':'unknown','evidence':[{'segment_id':'b00001','quote':quote}]}
    sections=fixture_sections()
    for _, obj in iter_objects(sections):
        if 'quote' in obj and 'source_section' in obj:obj['source_section']='b00001'
    return {'source':source,'sections':sections,'model':{'model_id':'fixture'},
        'complete_for_scope':True,'roster_complete':True,'expected_tasks':list(TASK_MODELS),
        'companions':{'timeline':{'status':'valid','data':{'events':[event],'limitations':[]}},
                      'summary':{'status':'valid','data':{'claims':[],'limitations':[]}}}}


def test_seed_retains_literal_support_timeline_and_distinct_visual_evidence():
    p=patient()
    annotation={'article_id':'PMC1.1','model':'fixture','figure_id':'F1','pixel_provenance':{'sha256':'pixels'},
        'source_license':{'code':'CC BY'},'annotation':{'status':'valid','data':{
            'pixel_observations':[{'patient_ids':['p1'],'visible_description':'radiograph'}],'caption_claims':[]}}}
    result=seed_patient(p,[annotation,{**annotation,'model':'different-model'}])
    assert result['synthetic_events']==[] and len(result['visual_findings'])==1
    assert result['events'][0]['candidate_fact_ids']
    assert all(f['source_license']=='CC BY' for f in result['facts'])
    for fact in result['facts']:
        for evidence in fact['support']:
            assert SOURCE[evidence['packet_start']:evidence['packet_end']]==evidence['quote']
            assert evidence['segment_spans'][0]['segment_id']=='b00001'
    assert result==seed_patient(p,[annotation])


def test_seed_rejects_tampered_source_or_ineligible_license():
    p=patient();p['source']['text']+=' altered'
    with pytest.raises(ValueError,match='digest'):seed_patient(p)
    p=patient();p['source']['article_source']['license']['allowed']=False
    with pytest.raises(ValueError,match='eligible'):seed_patient(p)


def test_default_seed_export_does_not_mislabel_partial_task_scope(tmp_path):
    p=patient();p['expected_tasks']=['demographics'];p['sections']={'demographics':p['sections']['demographics']}
    source=tmp_path/'patients.jsonl';source.write_text(json.dumps(p)+'\n')
    report=export_seeds(str(source),str(tmp_path/'seeds.jsonl'))
    assert report['written']==0 and report['skipped']==1
    report=export_seeds(str(source),str(tmp_path/'partial.jsonl'),include_partial=True)
    assert report['written']==1
    assert not json.loads((tmp_path/'partial.jsonl').read_text())['quality']['all_clinical_tasks_valid']


def test_clinical_time_is_literal_and_weight_cannot_be_age():
    p=patient();d=p['sections']['demographics']
    d['items'][0].update(attribute='age_other',numeric_value=24.,unit='kg')
    result=validate('demographics',d,SOURCE)
    assert not result.valid and 'Age requires a time unit' in str(result.errors)
    medications=p['sections']['medications'];medications['items'][0]['time']['text']='on admission'
    result=validate('medications',medications,SOURCE)
    assert not result.valid and 'temporal expression' in str(result.errors)


def test_explicit_hybrid_vision_keeps_unresolved_annotations_for_assigned_figure():
    p=patient();p['source']['figure_assignments']=[{'figure_id':'F1','patient_ids':['p1']}]
    a={'article_id':'PMC1.1','model':'vision-only','figure_id':'F1','pixel_provenance':{'sha256':'pixels'},
       'source_license':{'code':'CC BY'},'annotation':{'status':'valid','data':{
        'pixel_observations':[{'patient_ids':[],'visible_description':'a chart'}],'caption_claims':[]}}}
    assert not seed_patient(p,[a])['figure_annotations']
    seed=seed_patient(p,[a],visual_model='vision-only')
    assert not seed['visual_findings']
    assert seed['figure_annotations'][0]['patient_association']=='roster_candidate_only'
    assert seed['figure_annotations'][0]['model_id']=='vision-only'


def test_seed_rechecks_companion_evidence_not_only_saved_status():
    p=patient();p['companions']['timeline']['data']['events'][0]['evidence'][0]['quote']='Invented clinical course'
    with pytest.raises(ValueError,match='nonliteral'):seed_patient(p)


def test_old_license_approval_is_rechecked_even_for_partial_export(tmp_path):
    p=patient();p['source']['article_source']['license']['statements']=[{'type':'license','text':'CCBY-NC-ND'}]
    with pytest.raises(ValueError,match='eligible'):seed_patient(p)
    source=tmp_path/'patients.jsonl';source.write_text(json.dumps(p)+'\n')
    report=export_seeds(str(source),str(tmp_path/'seeds.jsonl'),include_partial=True)
    assert report['written']==0 and report['skipped']==1
    assert report['skipped_records'][0]['reason']=='license_ineligible_under_current_policy'
