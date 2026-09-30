import copy
import json
from pathlib import Path
import pytest
from openpatients2.articles import parse_article, normalize_license, license_decision
from openpatients2.article_tasks import check_article_task, patient_packet, Timeline
from openpatients2.experiment import Budget, BudgetExceeded


XML = '''<article xmlns:xlink="http://www.w3.org/1999/xlink" article-type="case-report">
<front><article-meta><article-id pub-id-type="pmc">1</article-id><title-group><article-title>Two cases</article-title></title-group>
<permissions><license xlink:href="https://creativecommons.org/licenses/by-nc-sa/4.0/"/></permissions>
<abstract><p>Two patients were treated.</p></abstract></article-meta></front>
<body><sec id="s1"><title>Case 1</title><p id="p1">A woman received 5 mg. <xref ref-type="bibr">99</xref>Two weeks later she improved.</p>
<table-wrap id="T1"><label>Table 1</label><caption><p>Results</p></caption><table><thead><tr><th>Patient</th><th>BP</th></tr></thead>
<tbody><tr><td>Case 1</td><td>120/80 mmHg</td></tr><tr><td>Case 2</td><td>90/60 mmHg</td></tr></tbody></table></table-wrap></sec>
<sec><title>Case 2</title><p>A dog received 2 mg.</p></sec>
<fig id="F1"><label>Figure 1</label><caption><p>Case 1 radiograph.</p></caption><graphic xlink:href="fig1"/></fig>
<supplementary-material id="S1" xlink:href="values.csv"><caption><p>Blood pressure data.</p></caption></supplementary-material></body>
<back><ref-list><ref>WRONG PATIENT from references</ref></ref-list></back></article>'''


def article():
    return parse_article(XML, {'pmcid':'PMC1','version':1,'license_code':'CC BY-NC-SA',
        'is_pmc_openaccess':'yes','is_retracted':'no','xml_url':'https://pmc-oa-opendata.s3.amazonaws.com/PMC1.1/PMC1.1.xml',
        'media_urls':['https://pmc-oa-opendata.s3.amazonaws.com/PMC1.1/fig1.jpg','https://pmc-oa-opendata.s3.amazonaws.com/PMC1.1/values.csv']})


def roster(a):
    return {'disposition':'individual_cases','reported_individual_count':2,'roster_complete':True,
     'patients':[{'patient_id':'p1','label':'woman','species':'human','species_as_documented':'woman',
         'identity_evidence':[{'segment_id':'b00002','quote':'A woman received 5 mg.'}],
         'source_segment_ids':['b00002','b00003','b00006'],'attribution_limitations':[]},
         {'patient_id':'p2','label':'dog','species':'nonhuman','species_as_documented':'dog',
         'identity_evidence':[{'segment_id':'b00005','quote':'A dog received 2 mg.'}],
         'source_segment_ids':['b00004','b00005'],'attribution_limitations':[]}],
     'background_segment_ids':['b00001'],'unresolved_segment_ids':[],
     'figures':[{'figure_id':'F1','panel':None,'patient_ids':['p1'],'scope':'individual',
        'evidence':[{'segment_id':'b00006','quote':'Case 1 radiograph.'}]}],'limitations':[]}


def test_jats_keeps_rows_captions_supplements_and_drops_references():
    a=article()
    assert a['license']['allowed']
    assert len(a['segments'])==6
    assert 'WRONG PATIENT' not in a['text'] and '99' not in a['text']
    assert 'Patient | BP\nCase 1 | 120/80 mmHg' in a['text']
    assert a['figures'][0]['image_urls'][0].endswith('fig1.jpg')
    assert a['supplements'][0]['tabular_data_candidate']
    assert not a['supplements'][0]['content_inspected']


@pytest.mark.parametrize('code', ['CC BY-ND','CC BY-NC-ND',None,'all rights reserved'])
def test_license_rejects_unknown_and_nd(code):
    m={'license_code':code,'is_pmc_openaccess':True,'is_retracted':False}
    assert not license_decision(m,[])['allowed']


def test_license_conflicts_and_retractions_fail_closed():
    m={'license_code':'CC BY','is_pmc_openaccess':True,'is_retracted':False}
    statements=[{'type':'license','url':'https://creativecommons.org/licenses/by-nc/4.0/'}]
    assert not license_decision(m,statements)['allowed']
    assert not license_decision({**m,'is_retracted':None},[])['allowed']
    assert license_decision({**m,'license_code':'CC BY-SA'},[])['release_lane']=='sharealike_source_terms'


def test_nested_license_url_and_contradictory_prose_fail_closed():
    m={'license_code':'CC BY-NC-SA','is_pmc_openaccess':True,'is_retracted':False}
    statement={'type':'license','url':None,'text':'https://creativecommons.org/licenses/by-nc-sa/4.0/ This is a Creative Commons Attribution-Non Commercial-No Derivatives License (CCBY-NC-ND). The work cannot be changed in any way.'}
    result=license_decision(m,[statement])
    assert not result['allowed'] and result['reason']=='conflicting_license_statements'
    assert result['statement_codes']==['CC BY-NC-ND','CC BY-NC-SA']
    assert not license_decision(m,[{'type':'license','text':'No Derivatives', 'url':None}])['allowed']
    assert license_decision(m,[{'type':'license','text':'CC BY-NC-SA 4.0'}])['allowed']


def test_license_recheck_cannot_rehabilitate_acquisition_rejection():
    from openpatients2.articles import recheck_license
    rejected={'allowed':False,'reason':'retracted_or_retraction_status_unknown','code':'CC BY'}
    assert recheck_license(rejected)==rejected
    old={'allowed':True,'code':'CC BY','statements':[{'type':'license','text':'CCBY-NC-ND'}]}
    assert not recheck_license(old)['allowed']


def test_packet_preserves_patient_identity_and_source_offsets():
    a=article(); r=roster(a)
    check_article_task('roster',r,a)
    record=patient_packet(a,r,r['patients'][0],scope='localized')
    assert 'A dog' not in record['text']
    assert record['article_source']['license']['code']=='CC BY-NC-SA'
    for span in record['packet_spans']:
        block=next(x for x in a['segments'] if x['segment_id']==span['segment_id'])
        assert record['text'][span['start']:span['end']]==block['text']
    assert record['cluster_id']=='PMC1'


def test_missing_blocks_unknown_patients_and_fabricated_quotes_fail():
    a=article(); r=roster(a)
    r['patients'][0]['source_segment_ids'].remove('b00003')
    fixed=check_article_task('roster',r,a)
    assert 'b00003' in fixed['unresolved_segment_ids']
    packet=patient_packet(a,fixed,fixed['patients'][0])
    assert 'Case 1 | 120/80 mmHg' in packet['text']
    r=roster(a); r['figures'][0]['patient_ids']=['p9']
    with pytest.raises(ValueError): check_article_task('roster',r,a)
    r=roster(a); r['patients'][0]['identity_evidence'][0]['quote']='She was healthy.'
    with pytest.raises(ValueError,match='nonliteral'): check_article_task('roster',r,a)


def test_temporal_graph_cannot_cycle_or_invent_numeric_time():
    base={'description':'treatment','kind':'treatment','time_text':None,'relation':'unknown',
          'anchor_event_id':None,'offset_min':None,'offset_max':None,'unit':None,'precision':'unknown',
          'evidence':[{'segment_id':'b00002','quote':'A woman received 5 mg.'}]}
    e1={**base,'event_id':'e1','anchor_event_id':'e2'}
    e2={**base,'event_id':'e2','anchor_event_id':'e1'}
    with pytest.raises(ValueError,match='Cyclic'): Timeline.model_validate({'events':[e1,e2],'limitations':[]})
    with pytest.raises(ValueError,match='Offsets require'):
        Timeline.model_validate({'events':[{**base,'event_id':'e1','offset_min':2.,'offset_max':2.}], 'limitations':[]})


def test_budget_survives_restart_and_keeps_unknown_charges(tmp_path):
    p=tmp_path/'budget.sqlite'; b=Budget(p,1)
    ident=b.reserve('request',.8); b.settle(ident,None); b.db.close()
    b=Budget(p,1)
    with pytest.raises(BudgetExceeded): b.reserve('request2',.3)
    b.settle(ident,.1)
    b.reserve('request2',.3)
    assert b.report()['accounted_usd']==pytest.approx(.4)
    b.db.close()


def test_nested_figure_preserves_inline_text_order():
    xml=XML.replace('A dog received 2 mg.','Before <italic>emphasis</italic><fig id="nested"><caption><p>Nested caption</p></caption></fig> after.')
    a=parse_article(xml,article()['metadata'])
    assert 'Before emphasis after.' in a['text']


def test_table_group_time_survives_on_following_rows():
    xml=XML.replace('<tr><td>Case 1</td>', '<tr><td colspan="2">At 48 h</td></tr><tr><td>Case 1</td>')
    a=parse_article(xml,article()['metadata'])
    row=next(s for s in a['segments'] if 'Case 1 | 120/80' in s['text'])
    assert 'At 48 h' in row['text']


def test_adjacent_block_elements_do_not_fuse_clinical_words():
    xml=XML.replace('<p>A dog received 2 mg.</p>','<list><list-item><p>A dog received 2 mg.</p><p>It improved.</p></list-item></list>')
    a=parse_article(xml,article()['metadata'])
    assert 'A dog received 2 mg. It improved.' in a['text']
