"""Typed supplement facts preserve distinct provenance and honest completion gates."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
from openpatients2.dense_store import load as load_store
store = load_store()
AID = 'PMC-clinical-test.1'


@pytest.fixture
def clinical(tmp_path, monkeypatch):
    tmp_path = tmp_path / 'clinical'; tmp_path.mkdir()
    monkeypatch.syspath_prepend(str(ROOT / 'benchmarks/dense500'))
    import supplement_review as m
    monkeypatch.setattr(m, 'pdf_page_count', lambda path: 1)
    data = b'%PDF-1.4\nSynthetic test only'; path = tmp_path / 'table.pdf'; path.write_bytes(data)
    url = 'https://example.invalid/table.pdf'
    original = dict(url=url,filename='table.pdf',original_manifest_url='s3://table.pdf',md5=hashlib.md5(data).hexdigest())
    s = dict(article_id=AID,text_sha256='source-sha',license={'code':'CC BY'},segments=[],figures=[],
             supplements=[dict(supplement_id='SM1',assets=[original])])
    a = dict(article_id=AID,text_sha256='source-sha',patients=[dict(patient_id=p,label=p,species='human',identity_evidence=[],facts=[])
                                                           for p in ('p1','p2')])
    page = dict(page=1,status='inspected',inspection_method='rendered_page',contents='First patient: Day1 06:00 glucose3.6;Day2 -',
                clinical_relevance='clinical_data',clinical_relevance_notes='Synthetic table',patient_ids=['p1'],rights_checked=True,
                rights_basis='source_license',rights_exceptions=[],limitations=[])
    asset = {**original,'sha256':hashlib.sha256(data).hexdigest(),'bytes':len(data),'path':str(path),
             'source_license':s['license'],'media_type':'application/pdf','page_count':1,'pages':[page]}
    def tx(tid,text,kind='clinical_data',row='06:00',col='Day1'):
        return dict(transcription_id=tid,asset_url=url,asset_sha256=asset['sha256'],page=1,panel=None,
                    table_label='Table1',row_label=row,column_label=col,kind=kind,text=text,visual_cue=None)
    fields = dict(subject='index_patient',assertion='present',temporality='unknown',time=dict(text='Day1',relation='during',anchor='hospitalization',date_iso=None),
                  result_absent_reason=None,name='Glucose',kind='laboratory',status='resulted',text_value='3.6',numeric_value=3.6,
                  comparator='=',unit='mmol/L',flag='unknown',reference_range_text=None,specimen=None,method=None,body_site=None)
    ownership = [dict(basis='supplement',transcription_id='header',segment_id=None,quote='First patient')]
    def fact(fid,tid,quote,values):
        return dict(fact_id=fid,task='observations',collection='items',fields=values,
                    evidence=[dict(transcription_id=tid,quote=quote)],ownership='individual',patient_ids=['p1'],
                    ownership_evidence=deepcopy(ownership),certainty='supported',scoring='include',notes='Synthetic only')
    missing = deepcopy(fields);missing.update(assertion='unknown',status='unknown',result_absent_reason='not_reported',
                                             numeric_value=None,text_value=None,comparator='unknown')
    missing['time']['text']='Day2'
    payload = dict(format=m.CLINICAL_FORMAT,coverage='complete',transcriptions=[tx('cell','3.6'),tx('missing','-',col='Day2'),
                    tx('day1','Day1',kind='context'),tx('day2','Day2',kind='context',col='Day2'),
                    tx('header','First patient',kind='context',row=None,col=None)],
                   facts=[fact('SM1-f001','cell','3.6',fields),fact('SM1-f002','missing','-',missing)],limitations=[])
    for f,tid,quote in zip(payload['facts'],('day1','day2'),('Day1','Day2')):
        f['evidence'].append(dict(transcription_id=tid,quote=quote))
    t = dict(format=m.DOCUMENT_FORMAT,article_id=AID,source_text_sha256='source-sha',supplement_id='SM1',status='inspected',
             document_classification='clinical_data',contents_summary='Synthetic clinical table',assets=[asset],limitations=[],clinical_data=payload)
    def save():
        for kind,x,sid in [('sources',s,None),('annotations',a,None),('document-submissions',t,'SM1')]:
            store.path(tmp_path,kind,AID,sid=sid,mkdir=True).write_text(json.dumps(x))
    save();return m,tmp_path,s,a,t,save


def test_clinical_page_without_typed_rows_cannot_complete_or_sign(clinical):
    m,r,s,a,t,save=clinical;t.pop('clinical_data');save()
    assert not m.validate_submission(t,s,a)['complete']
    with pytest.raises(ValueError,match='missing typed'):
        m.sign_review(r,AID,'SM1',role='agent',reviewer='Claude test agent',notes='Synthetic fixture inspection notes for tests')


def test_production_fields_provenance_missing_cells_and_separate_export(clinical,monkeypatch):
    m,_,s,a,t,_=clinical;original=deepcopy((s,a));assert m.validate_submission(t,s,a)['complete']
    bundle=m.supplemental_clinical_bundle(t,a);patient=bundle['patients'][0]
    rows=patient['sections']['observations']['items'];assert len(rows)==2
    assert rows[0]['numeric_value']==3.6 and rows[0]['unit']=='mmol/L'
    assert rows[0]['evidence'][0]==dict(quote='3.6',source_section='SM1:transcription:cell')
    assert rows[1]['assertion']=='unknown' and rows[1]['flag']=='unknown'
    assert rows[1]['numeric_value'] is None and rows[1]['result_absent_reason']=='not_reported'
    import export as exporter
    records=exporter.supplemental_clinical_records([(t,[{'role':'agent'},{'role':'parent'}])],a,'test')
    assert exporter.patient_supplemental_clinical(records,'p1')[0]['patient']['sections']==patient['sections']
    assert exporter.patient_supplemental_clinical(records,'p2')==[]
    assert (s,a)==original


@pytest.mark.parametrize('change',['asset','hash','page','panel','quote','transcription_id','duplicate_transcription','duplicate_fact','main_id',
                                  'patient','ownership','ownership_quote','missing_field','extra_field','missing_cell_result','missing_assertion','uncovered','context_only','time'])
def test_reject_wrong_provenance_schema_and_coverage(clinical,change):
    m,_,s,a,t,_=clinical;c=t['clinical_data'];f=c['facts'][0];tx=c['transcriptions'][0]
    if change=='asset':tx['asset_url']='other'
    elif change=='hash':tx['asset_sha256']='0'*64
    elif change=='page':tx['page']=2
    elif change=='panel':tx['panel']='A'
    elif change=='quote':f['evidence'][0]['quote']='3.7'
    elif change=='transcription_id':f['evidence'][0]['transcription_id']='unknown'
    elif change=='duplicate_transcription':c['transcriptions'].append(deepcopy(tx))
    elif change=='duplicate_fact':c['facts'].append(deepcopy(f))
    elif change=='main_id':a['patients'][0]['facts']=[dict(fact_id=f['fact_id'])]
    elif change=='patient':f['patient_ids']=['p2']
    elif change=='ownership':f['ownership']='unresolved'
    elif change=='ownership_quote':f['ownership_evidence'][0]['quote']='Second patient'
    elif change=='missing_field':f['fields'].pop('method')
    elif change=='extra_field':f['fields']['made_up']=True
    elif change=='missing_cell_result':c['facts'][1]['fields'].update(status='resulted',result_absent_reason=None,text_value='negative',flag='negative')
    elif change=='missing_assertion':c['facts'][1]['fields']['assertion']='absent'
    elif change=='uncovered':c['facts'].pop()
    elif change=='time':f['fields']['time']['text']='Day1; 06:00'
    else:f['evidence']=[dict(transcription_id='header',quote='First patient')]
    with pytest.raises(ValueError):m.validate_submission(t,s,a)


def test_unresolved_rows_remain_article_only_no_similarity_assignment(clinical):
    m,_,s,a,t,_=clinical
    for f in t['clinical_data']['facts']:
        f.update(ownership='unresolved',patient_ids=[],ownership_evidence=[],scoring='review_only',certainty='uncertain')
        f['fields']['subject']='unknown'
    assert m.validate_submission(t,s,a)['complete']
    bundle=m.supplemental_clinical_bundle(t,a)
    assert bundle['patients']==[] and len(bundle['unassigned_or_review_only_facts'])==2


def test_limited_coverage_stays_incomplete(clinical):
    m,r,s,a,t,save=clinical;t['clinical_data'].update(coverage='limited',limitations=['Unresolved extraction scope']);save()
    assert not m.validate_submission(t,s,a)['complete']
    with pytest.raises(ValueError,match='cannot receive completed'):
        m.sign_review(r,AID,'SM1',role='agent',reviewer='Claude test reviewer',notes='Synthetic fixture inspection notes for tests')


@pytest.mark.parametrize('change',['transcription','numeric','coordinate','ownership','coverage'])
def test_dual_receipts_bind_every_clinical_payload_change(clinical,change):
    m,r,_,_,t,save=clinical
    for role in ('agent','parent'):m.sign_review(r,AID,'SM1',role=role,reviewer=f'Claude synthetic {role}',notes='Synthetic provenance test only')
    assert len(m.reviewed_supplement_targets(r,AID))==1
    c=t['clinical_data']
    if change=='transcription':c['transcriptions'][0]['text']='3.6 extra'
    elif change=='numeric':c['facts'][0]['fields']['numeric_value']=3.7
    elif change=='coordinate':c['transcriptions'][0]['column_label']='Day3'
    elif change=='ownership':c['facts'][0]['notes']='Changed attribution interpretation'
    else:c['limitations']=['Changed clinical scope']
    save()
    with pytest.raises(ValueError,match='stale'):m.reviewed_supplement_targets(r,AID)


def test_image_clinical_table_gate_and_pixel_ownership(clinical,monkeypatch):
    m,_,s,a,t,_=clinical
    # Joint visual/schema validation is separately covered by the image tests.
    monkeypatch.setattr(m,'validate_joint',lambda target,*args:target)
    original=s['supplements'][0]['assets'][0];original['kind']='image'
    t['format']=m.FORMAT;t['analysis']={'visual':{'panels':[{'panel':None,'image_kind':'table','charts':[]}]},
                                     'attribution':{'assignments':[{'panel':None,'patient_ids':[],'scope':'unresolved'}]}}
    for tx in t['clinical_data']['transcriptions']:tx.update(page=None,visual_cue='Printed table cell')
    without=deepcopy(t);without.pop('clinical_data');assert not m.validate_submission(without,s,a)['complete']
    with pytest.raises(ValueError,match='image attribution'):m.validate_submission(t,s,a)
    for f in t['clinical_data']['facts']:
        f.update(ownership='unresolved',patient_ids=[],ownership_evidence=[],scoring='review_only');f['fields']['subject']='unknown'
    assert m.validate_submission(t,s,a)['complete']

# Reuse the isolated release fixture; all receipts below are synthetic test data.
from test_dense_reference_release import release, full_target


def test_full_export_dual_gate_and_immutable_main_sections(clinical,release,full_target):
    m,_,_,_,t,_=clinical
    exporter,validator,root,out,aid,_=release
    _,_,target,annotation,source=full_target
    source['supplements']=[dict(supplement_id='SM1',assets=[{k:t['assets'][0][k] for k in
                         ('url','filename','md5','original_manifest_url')}])]
    t.update(article_id=aid,source_text_sha256=source['text_sha256'])
    t['assets'][0]['source_license']=source['license']
    store.path(root, 'sources', aid, mkdir=True).write_text(json.dumps(source))
    store.path(root,'document-submissions',aid,sid='SM1',mkdir=True).write_text(json.dumps(t))
    store.path(root, 'schema-submissions', aid, mkdir=True).write_text(json.dumps(target))
    for role in ('agent','parent'):
        receipt=dict(article_id=aid,status='schema_source_reviewed',role=role,reviewer=f'Claude synthetic {role}',inspection_notes='Synthetic fixture inspection notes for tests',annotation_sha256=validator.digest(annotation),
                     target_sha256=validator.digest(target),source_text_sha256=source['text_sha256'])
        store.receipt(root,'schema',role,aid,mkdir=True).write_text(json.dumps(receipt))
    exporter.main()
    assert not (out/'legacy'/'supplement_clinical.jsonl').read_text()
    for role in ('agent','parent'):
        m.sign_review(root,aid,'SM1',role=role,reviewer=f'Claude synthetic {role}',notes='Synthetic export test only')
    exporter.main()
    rows=[json.loads(x) for x in (out/'legacy'/'patients.jsonl').read_text().splitlines()]
    assert rows[0]['sections']==target['patients'][0]['sections']
    assert len(rows[0]['supplemental_clinical'][0]['patient']['sections']['observations']['items'])==2
    supplementary=json.loads((out/'legacy'/'supplement_clinical.jsonl').read_text())
    assert supplementary['main_article_sections_unchanged']
    assert supplementary['clinical_data']['transcriptions'][0]['column_label']=='Day1'
    article=json.loads((out/'legacy'/'article_bundles.jsonl').read_text())
    assert article['supplemental_clinical'][0]['supplement_id']=='SM1'
    catalog={r['article_id']:r for r in map(json.loads,(out/'legacy'/'manifest.jsonl').read_text().splitlines())}
    assert catalog[aid]['source_supplements'][0]['content_inspected']
    assert json.loads(store.path(root, 'sources', aid).read_text())==source
