"""All vocabularies in these tests are handcrafted format fixtures, not releases."""
import asyncio
import copy
import csv
import json
import sqlite3
from pathlib import Path
import pytest
import yaml
import httpx

from openpatients2.vocabulary import Catalog,build_catalog,SNOMED,LOINC,ICD10CM,normalize_term,file_sha
from openpatients2.coding import (load_policy,link_patient,link_file,validate_coded_patient,accepted_mappings,
                                mapping_request,apply_decisions,prepare_requests,review_template,_issues)
from openpatients2.coding_api import CodingRunConfig,validate_selection,infer_requests,profile_requests,request_is_valid
from openpatients2.ontology_demo import create_demo_catalog,DEMO_SYSTEM,candidate_transport,run_ontology_demo
from openpatients2.demo import fixture_sections,empty_section
from openpatients2.client import APIClient
from openpatients2.config import APIConfig
from openpatients2.data import read_jsonl
from openpatients2.knowledge_graph import export_graph
from openpatients2.warehouse import build_index,query_cohort,compile_cohort
from test_warehouse import exported


def dump(path,rows):
    Path(path).write_text(''.join(json.dumps(r)+'\n' for r in rows));return str(path)


@pytest.fixture
def catalog(tmp_path):
    db,policy=create_demo_catalog(tmp_path/'vocab')
    with Catalog(db) as c:yield c,load_policy(policy),db


def test_catalog_exact_hierarchy_and_lexical(catalog):
    c,_,_=catalog
    assert c.search(DEMO_SYSTEM,' LUNG  ADENOCARCINOMA ')[0]['code']=='lung'
    assert c.ancestors(DEMO_SYSTEM,'lung')==['root']
    assert c.search(DEMO_SYSTEM,'lung')[0]['retrieval']=='lexical_candidate'
    assert c.search('not-installed','anything')==()
    assert c.lookup(DEMO_SYSTEM,'fake-code') is None
    assert c.lookup(DEMO_SYSTEM,'lung','other-version') is None


def test_catalog_ambiguous_exact_truncation(catalog):
    c,_,_=catalog
    found=c.search(DEMO_SYSTEM,'biopsy',1)
    assert len(found)==1 and found[0]['exact_truncated']
    assert len(c.search(DEMO_SYSTEM,'biopsy'))>=2


@pytest.mark.parametrize('text,normalized',[(' HER2+ ','her2+'),('HER2-','her2-'),('Left Lung','left lung'),('25.0 mg','25.0 mg')])
def test_normalization_does_not_erase_semantics(text,normalized):
    assert normalize_term(text)==normalized


def test_mapping_keeps_every_original_field(catalog):
    c,p,_=catalog;row=exported();row['generations']={'conditions':{'reasoning_text':'teacher thoughts'}}
    coded=link_patient(row,c,p)
    assert row.get('terminology') is None
    for k in row:assert coded[k]==row[k]
    accepted=list(accepted_mappings(coded,c));assert accepted
    assert any(m['domain']=='family_genetics' and m['fact']['subject']=='family_member' for m,_ in accepted)
    assert any(m['domain']=='procedures_devices' and m['status']=='ambiguous' for m in coded['terminology']['mappings'])


@pytest.mark.parametrize('change',['code','version','display','fact','source','status','id','issues','duplicate'])
def test_catalog_and_occurrence_tamper_rejected(catalog,change):
    c,p,_=catalog;row=link_patient(exported(),c,p);m=row['terminology']['mappings'][0]
    if change=='code':m['candidates'][0]['code']='INVENTED'
    elif change=='version':m['version']='new'
    elif change=='display':m['candidates'][0]['display']='wrong'
    elif change=='fact':m['fact']['assertion']='absent'
    elif change=='source':m['source_hash']='bad'
    elif change=='status':m['status']='trusted'
    elif change=='id':m['mapping_id']='random'
    elif change=='issues':m['candidates'][0]['issues']=[];m['candidates'][0]['active']=False
    elif change=='duplicate':row['terminology']['mappings'].append(copy.deepcopy(m))
    with pytest.raises(ValueError):validate_coded_patient(row,c)


def test_empty_section_no_placeholder_mapping(catalog):
    c,p,_=catalog;row=exported();row['sections']['conditions']=empty_section('conditions')
    linked=link_patient(row,c,p)
    assert not any(m['domain']=='conditions' for m in linked['terminology']['mappings'])


def test_absent_catalog_is_explicit(catalog):
    c,_,_=catalog;linked=link_patient(exported(),c,load_policy())
    assert set(m['status'] for m in linked['terminology']['mappings'])=={'catalog_unavailable'}


def test_no_naked_official_alias_dictionary(tmp_path):
    path=tmp_path/'terms.csv'
    path.write_text('domain,field,source_text,system,code,label,version,reviewed\nconditions,name,a,http://snomed.info/sct,1,A,test,true\n')
    from openpatients2.warehouse import load_terms
    with pytest.raises(ValueError,match='pinned catalog'):load_terms(path)


def make_clinical_index(tmp_path,catalog,assertion='present',subject='index_patient'):
    c,p,db=catalog;row=exported()
    row['sections']['conditions']['items'][0].update(assertion=assertion,subject=subject)
    linked=link_patient(row,c,p);source=dump(tmp_path/'coded.jsonl',[linked]);index=tmp_path/'index.sqlite'
    build_index(source,str(index),catalog_path=str(db));return index,linked


def query_spec(subject=None,assertion=None,version='synthetic-fixture-v1'):
    where=[{'field':'name','op':'concept_descendant','value':{'system':DEMO_SYSTEM,'code':'root','version':version}}]
    if subject:where.append({'field':'subject','value':subject})
    if assertion:where.append({'field':'assertion','value':assertion})
    return {'source_kinds':['synthetic_fixture'],'criteria':[{'id':'dx','domain':'conditions','where':where}]}


@pytest.mark.parametrize('assertion,subject',[('present','index_patient'),('absent','index_patient'),('possible','index_patient'),('present','family_member')])
def test_coded_cohort_respects_assertion_and_subject(tmp_path,catalog,assertion,subject):
    db,_=make_clinical_index(tmp_path,catalog,assertion,subject)
    result=query_cohort(str(db),query_spec(),str(tmp_path/'cohort.jsonl'))
    assert result['returned_records']==int(assertion=='present' and subject=='index_patient')
    explicit=query_cohort(str(db),query_spec(subject,assertion),str(tmp_path/'explicit.jsonl'))
    assert explicit['returned_records']==1


def test_hierarchy_version_and_query_evidence(tmp_path,catalog):
    db,_=make_clinical_index(tmp_path,catalog)
    assert query_cohort(str(db),query_spec(version='wrong'),str(tmp_path/'wrong.jsonl'))['returned_records']==0
    path=tmp_path/'right.jsonl';query_cohort(str(db),query_spec(),str(path));row=next(read_jsonl(path))
    assert row['matches'][0]['evidence'] and row['matches'][0]['concepts']
    spec=query_spec();del spec['criteria'][0]['where'][0]['value']['version']
    with pytest.raises(ValueError):compile_cohort(spec)


def test_coded_output_requires_catalog(tmp_path,catalog):
    c,p,_=catalog;path=dump(tmp_path/'in.jsonl',[link_patient(exported(),c,p)])
    index=tmp_path/'bad.sqlite'
    with pytest.raises(ValueError,match='catalog'):build_index(path,str(index))
    assert not index.exists()


def test_model_never_generates_arbitrary_code(catalog):
    c,p,_=catalog;row=link_patient(exported(),c,p)
    m=next(x for x in row['terminology']['mappings'] if x['status']=='ambiguous');request=mapping_request(m)
    with pytest.raises(ValueError,match='offered'):
        validate_selection({'decision':'select','candidate_id':'IMAGINARY','relation':'equivalent','rationale':'x'},request)
    assert validate_selection({'decision':'abstain','candidate_id':None,'relation':None,'rationale':'insufficient specificity'},request)['decision']=='abstain'
    with pytest.raises(ValueError):validate_selection({'decision':'abstain','candidate_id':'x','relation':None,'rationale':'x'},request)


@pytest.mark.parametrize('kind',['inactive_concept','semantic_domain_mismatch','conflicting_laterality','undocumented_laterality'])
def test_hard_conflicts_block_selection(kind):
    with pytest.raises(ValueError):
        validate_selection({'decision':'select','candidate_id':'1','relation':'equivalent','rationale':'x'},
                           {'candidates':[{'candidate_id':'1','issues':[kind]}]})


def test_review_workflow_and_model_not_approved(tmp_path,catalog):
    c,p,db=catalog;row=link_patient(exported(),c,p)
    m=next(x for x in row['terminology']['mappings'] if x['status']=='ambiguous')
    source=dump(tmp_path/'in.jsonl',[row]);sel={'decision':'select','candidate_id':m['candidates'][0]['candidate_id'],'relation':'equivalent','rationale':'fixture rationale'}
    proposal={'mapping_id':m['mapping_id'],'request_digest':m['request_digest'],'status':'ok','selection':sel,'reasoning_text':'returned test reasoning'}
    proposals=dump(tmp_path/'proposals.jsonl',[proposal]);new=tmp_path/'proposed.jsonl'
    apply_decisions(source,str(new),db,proposals=proposals);modified=next(read_jsonl(new))
    nm=next(x for x in modified['terminology']['mappings'] if x['mapping_id']==m['mapping_id'])
    assert nm['status']=='model_proposed' and nm['model_proposals'][0]['reasoning_text']
    assert not any(x['mapping_id']==m['mapping_id'] for x,_ in accepted_mappings(modified,c))
    reviews=dump(tmp_path/'reviews.jsonl',[{**sel,'mapping_id':m['mapping_id'],'request_digest':m['request_digest'],
        'reviewed':True,'reviewer':'TEST_FIXTURE','reviewed_at':'2026-09-27T00:00:00Z'}])
    out=tmp_path/'final.jsonl';apply_decisions(str(new),str(out),db,reviews=reviews)
    assert any(x['mapping_id']==m['mapping_id'] for x,_ in accepted_mappings(next(read_jsonl(out)),c))
    broader=json.loads(Path(reviews).read_text());broader['relation']='broader';dump(reviews,[broader])
    out2=tmp_path/'broader.jsonl';apply_decisions(str(new),str(out2),db,reviews=reviews)
    assert not any(x['mapping_id']==m['mapping_id'] for x,_ in accepted_mappings(next(read_jsonl(out2)),c))


def test_proposal_does_not_overwrite_exact_mapping(tmp_path,catalog):
    c,p,db=catalog;row=link_patient(exported(),c,p);m=row['terminology']['mappings'][0]
    prop={'mapping_id':m['mapping_id'],'request_digest':m['request_digest'],'status':'ok',
          'selection':{'decision':'abstain','candidate_id':None,'relation':None,'rationale':'do not erase exact'}}
    source=dump(tmp_path/'in.jsonl',[row]);proposals=dump(tmp_path/'p.jsonl',[prop]);out=tmp_path/'out.jsonl'
    apply_decisions(source,str(out),db,proposals=proposals)
    nm=next(read_jsonl(out))['terminology']['mappings'][0]
    assert nm['selected_candidate_id']==m['selected_candidate_id'] and nm['status']=='exact_unique'


def test_stale_review_fails_atomically(tmp_path,catalog):
    c,p,db=catalog;row=link_patient(exported(),c,p);m=row['terminology']['mappings'][0]
    source=dump(tmp_path/'in.jsonl',[row]);path=dump(tmp_path/'p.jsonl',[{'mapping_id':m['mapping_id'],'request_digest':'stale'}]);out=tmp_path/'out.jsonl'
    with pytest.raises(ValueError,match='Stale'):apply_decisions(source,str(out),db,proposals=path)
    assert not out.exists()


def test_graph_no_reasoning_no_causality(tmp_path,catalog):
    c,p,db=catalog;row=link_patient(exported(),c,p);row['generations']={'a':{'reasoning_text':'SECRET_REASONING'}}
    source=dump(tmp_path/'in.jsonl',[row]);out=tmp_path/'graph';report=export_graph(source,db,str(out))
    text=(out/'nodes.jsonl').read_text()+(out/'edges.jsonl').read_text()
    assert 'SECRET_REASONING' not in text
    assert report['counts']['nodes_SourceCase']==1 and report['counts']['edges_is_a']>0
    edges=list(read_jsonl(out/'edges.jsonl'));assert all(e['type']!='causes' for e in edges)
    assert any(e['type']=='supported_by' for e in edges)
    assert any(e['type']=='linked_to_tumor' for e in edges)


def test_link_file_parallel_and_immutable(tmp_path,catalog):
    _,_,db=catalog;source=dump(tmp_path/'input.jsonl',[exported()]);policy=tmp_path/'policy.yaml'
    policy.write_text(yaml.safe_dump({'rules':{'conditions':{'field':'name','systems':[DEMO_SYSTEM]}},'workers':2}))
    out=tmp_path/'output.jsonl';assert link_file(source,str(out),db,str(policy))['records']==1
    with pytest.raises(ValueError):link_file(source,str(out),db,str(policy))


def test_request_template_defaults_and_integrity(tmp_path,catalog):
    c,p,_=catalog;source=dump(tmp_path/'in.jsonl',[link_patient(exported(),c,p)])
    requests=tmp_path/'req.jsonl';assert prepare_requests(source,str(requests))['requests']>0
    request=next(read_jsonl(requests));request_is_valid(request);request['source_text']='wrong'
    with pytest.raises(ValueError):request_is_valid(request)
    out=tmp_path/'review.jsonl';review_template(source,str(out));assert all(not r['reviewed'] for r in read_jsonl(out))


def test_mock_selector_reasoning_and_resume(tmp_path,catalog):
    async def go():
        c,p,_=catalog;source=dump(tmp_path/'in.jsonl',[link_patient(exported(),c,p)]);requests=tmp_path/'req.jsonl'
        prepare_requests(source,str(requests));calls=[]
        cfg=CodingRunConfig(input=str(requests),output=str(tmp_path/'run'),api=APIConfig(model='mock',revision='fixture',endpoints=['http://mock.invalid/v1']),require_token_profile=False)
        async with httpx.AsyncClient(transport=candidate_transport(calls)) as http:
            client=APIClient(cfg.api,http);report=await infer_requests(cfg,client);n=len(calls)
            assert report['counts']['ok']>0 and report['usage']['completion_tokens'] is None
            proposal=next(read_jsonl(report['proposals']));assert proposal['reasoning_text'] and not proposal['mapping_approved']
            report2=await infer_requests(cfg,client);assert len(calls)==n and report2['counts']['resumed']>0
    asyncio.run(go())


def test_missing_profile_rejected_before_api(tmp_path):
    cfg=CodingRunConfig(input=str(tmp_path/'absent'),api=APIConfig(revision='pinned'))
    with pytest.raises(ValueError,match='code-profile'):asyncio.run(infer_requests(cfg))


def test_profile_overflow_rejected_before_api(tmp_path,catalog):
    class Tokenizer:
        def apply_chat_template(self,*a,**kw):return [1]*4096
    c,p,_=catalog;source=dump(tmp_path/'in.jsonl',[link_patient(exported(),c,p)]);req=tmp_path/'req.jsonl';prepare_requests(source,str(req))
    cfg=CodingRunConfig(input=str(req),api=APIConfig(revision='pinned'),max_model_len=4096,token_profile=str(tmp_path/'profile.jsonl'))
    profile_requests(cfg,'local-tokenizer',cfg.token_profile,tokenizer=Tokenizer())
    with pytest.raises(ValueError,match='overflow'):asyncio.run(infer_requests(cfg))


def test_full_ontology_demo(tmp_path):
    result=asyncio.run(run_ontology_demo(str(tmp_path/'demo')))
    assert result['cohort']['returned_records']==1 and result['clinical_accuracy'] is None
    assert result['graph']['counts']['edges_denotes']>0


def test_worker_exception_does_not_deadlock_queue(tmp_path,catalog):
    async def go():
        c,p,_=catalog;row=link_patient(exported(),c,p);source=dump(tmp_path/'in.jsonl',[row]);req=tmp_path/'req.jsonl';prepare_requests(source,str(req))
        cfg=CodingRunConfig(input=str(req),output=str(tmp_path/'run'),api=APIConfig(revision='fixture'),require_token_profile=False)
        class Broken(APIClient):
            async def complete_once(self,*args,**kwargs):raise RuntimeError('mock worker failure')
        async with httpx.AsyncClient(transport=candidate_transport()) as http:
            with pytest.raises(ExceptionGroup):
                await asyncio.wait_for(infer_requests(cfg,Broken(cfg.api,http)),timeout=2)
    asyncio.run(go())


def test_selector_truncation_and_invalid_candidate_preserved(tmp_path,catalog):
    async def go():
        c,p,_=catalog;source=dump(tmp_path/'in.jsonl',[link_patient(exported(),c,p)]);req=tmp_path/'req.jsonl';prepare_requests(source,str(req))
        for finish in ('length','stop'):
            def handle(request):
                data={'choices':[{'delta':{'reasoning':'Partial mock trace','content':'{"decision":"select","candidate_id":"nonexistent","relation":"equivalent","rationale":"x"}'},'finish_reason':finish}]}
                return httpx.Response(200,text='data: '+json.dumps(data)+'\n\ndata: [DONE]\n\n',headers={'content-type':'text/event-stream'})
            cfg=CodingRunConfig(input=str(req),output=str(tmp_path/finish),api=APIConfig(revision='fixture',endpoints=['http://mock.invalid/v1']),require_token_profile=False)
            async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as http:
                report=await infer_requests(cfg,APIClient(cfg.api,http))
            result=next(read_jsonl(report['proposals']))
            assert result['status']=='error' and result['reasoning_text']=='Partial mock trace'
            assert result['selection'] is None
    asyncio.run(go())
