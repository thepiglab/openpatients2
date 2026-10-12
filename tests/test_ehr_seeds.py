import copy
import json
import pytest
from openpatients2.schemas import TASK_MODELS
from openpatients2.validation import validate, iter_objects
from synthetic_fixtures import SOURCE, example_record, fixture_sections


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


def test_clinical_time_is_literal_and_weight_cannot_be_age():
    p=patient();d=p['sections']['demographics']
    d['items'][0].update(attribute='age_other',numeric_value=24.,unit='kg')
    result=validate('demographics',d,SOURCE)
    assert not result.valid and 'Age requires a time unit' in str(result.errors)
    medications=p['sections']['medications'];medications['items'][0]['time']['text']='on admission'
    result=validate('medications',medications,SOURCE)
    assert not result.valid and 'temporal expression' in str(result.errors)


