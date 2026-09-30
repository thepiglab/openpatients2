"""Miniature file-format fixtures; no official terminology release is bundled."""
import csv
import json
from pathlib import Path
import pytest
import yaml
from openpatients2.vocabulary import Catalog,build_catalog,ICD10CM,SNOMED,LOINC,file_sha
from openpatients2.coding import _issues,link_patient,load_policy
from test_warehouse import exported


def csvfile(path,rows,delimiter=','):
    with Path(path).open('w',newline='',encoding='utf-8') as f:
        w=csv.DictWriter(f,fieldnames=list(rows[0]),delimiter=delimiter);w.writeheader();w.writerows(rows)
    return str(path)


def build(tmp_path,releases,**extra):
    config=tmp_path/'import.yaml';config.write_text(yaml.safe_dump({'releases':releases,**extra}))
    out=tmp_path/'vocab.sqlite';build_catalog(str(config),str(out));return Catalog(out)


def icd_release(tmp_path):
    path=tmp_path/'synthetic-tabular.xml'
    path.write_text('''<ICD10CM.tabular><chapter><name>TEST</name><section><name>TEST-SECTION</name>
    <diag><name>A00</name><desc>Fixture infection</desc><useAdditionalCode><note>Fixture sequencing instruction.</note></useAdditionalCode>
    <diag><name>A00.0</name><desc>Fixture infection specific</desc><inclusionTerm><note>Fixture exact synonym</note></inclusionTerm></diag></diag>
    <diag><name>B00</name><desc>Fixture neoplasm</desc><diag><name>B00.0</name><desc>lung adenocarcinoma</desc></diag></diag>
    </section></chapter></ICD10CM.tabular>''')
    return {'kind':'icd10cm','version':'SYNTHETIC-FORMAT-TEST','source_url':'urn:test:NOT-CMS','path':str(path)}


def loinc_release(tmp_path):
    rows=[{'LOINC_NUM':'TEST-LOINC-1','COMPONENT':'Glucose','PROPERTY':'MCnc','TIME_ASPCT':'Pt',
           'SYSTEM':'Ser/Plas','SCALE_TYP':'Qn','METHOD_TYP':'','STATUS':'ACTIVE',
           'LONG_COMMON_NAME':'Fixture glucose in serum','SHORTNAME':'Fixture glucose serum',
           'RELATEDNAMES2':'FixtureKeyword;not an exact synonym'},
          {'LOINC_NUM':'TEST-LOINC-2','COMPONENT':'Glucose','PROPERTY':'MCnc','TIME_ASPCT':'Pt',
           'SYSTEM':'Urine','SCALE_TYP':'Qn','METHOD_TYP':'Test method','STATUS':'DEPRECATED',
           'LONG_COMMON_NAME':'Fixture glucose in urine','SHORTNAME':'Urine glucose',
           'RELATEDNAMES2':'FixtureKeyword'}]
    return {'kind':'loinc','version':'TEST-NOT-A-RELEASE','source_url':'urn:test:NOT-LOINC',
            'license_acknowledged':True,'path':csvfile(tmp_path/'loinc.csv',rows)}


def snomed_release(tmp_path):
    # Intentionally non-SCTID TEST-* identifiers prevent mistaking fixtures for a vocabulary.
    concepts=[{'id':'TEST-SCT-ROOT','effectiveTime':'20260101','active':'1','moduleId':'TEST-MODULE','definitionStatusId':'x'},
              {'id':'TEST-SCT-LUNG','effectiveTime':'20260101','active':'1','moduleId':'TEST-MODULE','definitionStatusId':'x'},
              {'id':'TEST-SCT-OFF','effectiveTime':'20260101','active':'0','moduleId':'TEST-MODULE','definitionStatusId':'x'}]
    desc=[]
    for i,(code,term,typ) in enumerate([('TEST-SCT-ROOT','Neoplasm (disorder)','900000000000003001'),
                       ('TEST-SCT-LUNG','Lung adenocarcinoma (disorder)','900000000000003001'),
                       ('TEST-SCT-LUNG','lung adenocarcinoma','900000000000013009'),
                       ('TEST-SCT-LUNG','Fixture alternate name','900000000000013009')]):
        desc.append({'id':f'D{i}','effectiveTime':'20260101','active':'1','moduleId':'TEST-MODULE',
                     'conceptId':code,'languageCode':'en','typeId':typ,'term':term,'caseSignificanceId':'x'})
    lang=[{'id':'LANG1','active':'1','refsetId':'TEST-DIALECT','referencedComponentId':'D2','acceptabilityId':'900000000000548007'}]
    rel=[{'id':'REL1','active':'1','sourceId':'TEST-SCT-LUNG','destinationId':'TEST-SCT-ROOT','typeId':'116680003','characteristicTypeId':'900000000000011006'}]
    return {'kind':'snomed','version':'TEST-SCT-EDITION','source_url':'urn:test:NOT-SNOMED','license_acknowledged':True,
            'concepts':csvfile(tmp_path/'concepts.txt',concepts,'\t'),
            'descriptions':csvfile(tmp_path/'descriptions.txt',desc,'\t'),
            'language':csvfile(tmp_path/'language.txt',lang,'\t'),'dialect_refset':'TEST-DIALECT',
            'relationships':csvfile(tmp_path/'relationships.txt',rel,'\t')}


def test_icd_import_retains_hierarchy_instructions_synonyms(tmp_path):
    with build(tmp_path,[icd_release(tmp_path)]) as cat:
        assert cat.lookup(ICD10CM,'A00')['selectable'] is False
        c=cat.search(ICD10CM,'Fixture exact synonym')[0]
        assert c['code']=='A00.0'
        assert c['attributes']['instructions'][0]['kind']=='useAdditionalCode'
        assert cat.ancestors(ICD10CM,'A00.0')==['A00']
        assert 'coding_instructions_not_evaluated' in _issues('conditions',{},c)


def test_loinc_keywords_are_not_exact_and_axes_retained(tmp_path):
    with build(tmp_path,[loinc_release(tmp_path)]) as cat:
        c=cat.search(LOINC,'FixtureKeyword')[0]
        assert c['retrieval']=='lexical_candidate'
        assert c['attributes']['SYSTEM']=='Ser/Plas'
        assert 'specimen_not_documented' in _issues('observations',{},c)
        assert 'loinc_axes_require_adjudication' in _issues('observations',{'specimen':'serum'},c)
        assert cat.lookup(LOINC,'TEST-LOINC-2')['active'] is False
        assert not any(x['code']=='TEST-LOINC-2' for x in cat.search(LOINC,'glucose'))


def test_snomed_snapshot_dialect_hierarchy_and_inactive(tmp_path):
    with build(tmp_path,[snomed_release(tmp_path)]) as cat:
        c=cat.search(SNOMED,'lung adenocarcinoma')[0]
        assert c['display']=='lung adenocarcinoma'
        assert c['attributes']['semantic_tag']=='disorder'
        assert c['attributes']['display_type']=='preferred'
        assert cat.ancestors(SNOMED,c['code'])==['TEST-SCT-ROOT']
        assert cat.lookup(SNOMED,'TEST-SCT-OFF')['active'] is False
        assert not cat.search(SNOMED,'Fixture alternate name')
        row=link_patient(exported(),cat,load_policy())
        assert any(m['system']==SNOMED and m['domain']=='conditions' and m['status']=='exact_unique' for m in row['terminology']['mappings'])


def test_snomed_snapshot_not_full(tmp_path):
    r=snomed_release(tmp_path);p=Path(r['concepts']);lines=p.read_text().splitlines();p.write_text('\n'.join(lines+[lines[1]])+'\n')
    with pytest.raises(ValueError,match='Snapshot'):build(tmp_path,[r])
    assert not (tmp_path/'vocab.sqlite').exists()


@pytest.mark.parametrize('kind',['snomed','loinc'])
def test_license_acknowledgement_required(tmp_path,kind):
    r=snomed_release(tmp_path) if kind=='snomed' else loinc_release(tmp_path)
    r['license_acknowledged']=False
    with pytest.raises(ValueError,match='license'):build(tmp_path,[r])


def test_crosswalk_rules_are_not_auto_equivalence(tmp_path):
    s=snomed_release(tmp_path);i=icd_release(tmp_path)
    rows=[{'active':'1','refsetId':'6011000124106','referencedComponentId':'TEST-SCT-LUNG',
           'mapGroup':'1','mapPriority':'1','mapRule':'IFA TEST-CONDITION','mapAdvice':'Fixture rule needs context','mapTarget':'B00.0'}]
    maps=[{'path':csvfile(tmp_path/'map.txt',rows,'\t'),'source_version':s['version'],'target_version':i['version']}]
    with build(tmp_path,[s,i],snomed_icd10_maps=maps) as cat:
        values=cat.crosswalk_candidates('TEST-SCT-LUNG')
        assert values[0]['status']=='rule_not_evaluated' and values[0]['mapRule']=='IFA TEST-CONDITION'
        assert cat.crosswalk_candidates('B00.0')==[]


@pytest.mark.parametrize('change',['version','sha','duplicate','kind'])
def test_bad_catalog_config_rejected(tmp_path,change):
    r=icd_release(tmp_path)
    if change=='version':r['version']='latest'
    elif change=='sha':r['expected_sha256']={'path':'bad'}
    elif change=='kind':r['kind']='imaginary'
    releases=[r,r] if change=='duplicate' else [r]
    with pytest.raises(ValueError):build(tmp_path,releases)


def test_catalog_build_does_not_overwrite(tmp_path):
    r=icd_release(tmp_path)
    with build(tmp_path,[r]):pass
    with pytest.raises(ValueError,match='immutable'):build(tmp_path,[r])


def test_loinc_missing_axis_headers_rejected(tmp_path):
    r=loinc_release(tmp_path);Path(r['path']).write_text('LOINC_NUM\nFAKE\n')
    with pytest.raises(ValueError,match='Missing terminology columns'):build(tmp_path,[r])

@pytest.mark.parametrize('problem',['cycle','dangling'])
def test_bad_local_hierarchy_rejected(tmp_path,problem):
    path=csvfile(tmp_path/'local.csv',[{'code':'a','display':'A','active':'true','parent':'b'},
                                    {'code':'b','display':'B','active':'true','parent':'a' if problem=='cycle' else 'missing'}])
    with pytest.raises(ValueError,match='cycle|dangling'):
        build(tmp_path,[{'kind':'local','version':'test','system':'urn:test','source_url':'urn:test','path':path}])


def test_no_false_unique_from_low_candidate_limit(tmp_path):
    from openpatients2.coding import link_patient,load_policy
    path=csvfile(tmp_path/'local.csv',[{'code':'a','display':'lung adenocarcinoma','active':'true'},
                                    {'code':'b','display':'lung adenocarcinoma','active':'true'}])
    with build(tmp_path,[{'kind':'local','version':'test','system':'urn:test','source_url':'urn:test','path':path}]) as cat:
        policy=load_policy();policy['candidate_limit']=1;policy['rules']={'conditions':{'field':'name','systems':['urn:test']}}
        row=link_patient(exported(),cat,policy)
        assert row['terminology']['mappings'][0]['status']!='exact_unique'
