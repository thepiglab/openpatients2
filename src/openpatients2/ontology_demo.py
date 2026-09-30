"""End-to-end demo with LOCAL identifiers and mock model responses only."""
from __future__ import annotations
import csv
import json
from pathlib import Path
import httpx
import yaml
from .demo import run_demo
from .vocabulary import build_catalog
from .coding import link_file,prepare_requests,apply_decisions,review_template
from .coding_api import CodingRunConfig,infer_requests
from .client import APIClient
from .config import APIConfig
from .data import read_jsonl,write_json
from .warehouse import build_index,query_cohort
from .knowledge_graph import export_graph

DEMO_SYSTEM='urn:openpatients2:demo:clinical-concepts'


def create_demo_catalog(out):
    out=Path(out);out.mkdir(parents=True,exist_ok=True)
    path=out/'local-demo.csv'
    # These identifiers/descriptions are authored fixtures, NOT real medical codes.
    rows=[('root','malignant neoplasm','',[]),('lung','lung adenocarcinoma','root',[]),
          ('breast','breast cancer','root',[]),('cough','cough','',[]),
          ('biopsy-lung','lung biopsy','',['biopsy']),('biopsy-other','other biopsy','',['biopsy'])]
    with path.open('w',newline='',encoding='utf-8') as f:
        w=csv.DictWriter(f,fieldnames=['code','display','parent','active','aliases']);w.writeheader()
        for code,display,parent,aliases in rows:w.writerow(dict(code=code,display=display,parent=parent,active='true',aliases=json.dumps(aliases)))
    config=out/'catalog.yaml';config.write_text(yaml.safe_dump({'releases':[{'kind':'local','system':DEMO_SYSTEM,
       'version':'synthetic-fixture-v1','source_url':'urn:openpatients2:hand-authored-fixtures','path':str(path)}]}))
    db=out/'catalog.sqlite';build_catalog(str(config),str(db))
    rules={x:{'field':'name','systems':[DEMO_SYSTEM]} for x in ('conditions','oncology_tumors','family_genetics','symptoms_function','procedures_devices')}
    policy=out/'policy.yaml';policy.write_text(yaml.safe_dump({'rules':rules,'workers':2}))
    return db,policy


def candidate_transport(calls=None):
    calls=calls if calls is not None else []
    async def handler(request):
        body=json.loads(request.content);r=json.loads(body['messages'][-1]['content']);calls.append(body)
        selection={'decision':'select','candidate_id':r['candidates'][0]['candidate_id'],
                   'relation':'equivalent','rationale':'Synthetic test decision; not an actual clinical model.'}
        chunks=[{'choices':[{'delta':{'reasoning':'Synthetic reasoning fixture; no clinical validity.'},'finish_reason':None}]},
                {'choices':[{'delta':{'content':json.dumps(selection)},'finish_reason':None}]},
                {'choices':[{'delta':{},'finish_reason':'stop'}]}]
        return httpx.Response(200,text=''.join('data: '+json.dumps(c)+'\n\n' for c in chunks)+'data: [DONE]\n\n',headers={'content-type':'text/event-stream'})
    return httpx.MockTransport(handler)


async def run_ontology_demo(destination):
    out=Path(destination)
    if out.exists():raise ValueError('Choose a fresh demo directory')
    out.mkdir(parents=True)
    extraction=await run_demo(str(out/'text-demo'))
    cat,policy=create_demo_catalog(out/'vocabulary')
    source=out/'text-demo/extraction/patients.jsonl';coded=out/'coded.jsonl'
    linked=link_file(str(source),str(coded),str(cat),str(policy))
    requests=out/'requests.jsonl';prepared=prepare_requests(str(coded),str(requests))
    api=APIConfig(model='mock-selector',model_id='NOT_A_MODEL',revision='mock-fixture',endpoints=['http://mock.invalid/v1'])
    cfg=CodingRunConfig(input=str(requests),output=str(out/'selector'),api=api,require_token_profile=False)
    async with httpx.AsyncClient(transport=candidate_transport()) as http:
        inference=await infer_requests(cfg,APIClient(api,http))
    proposed=out/'proposed.jsonl';apply_decisions(str(coded),str(proposed),str(cat),proposals=inference['proposals'])
    review_template(str(proposed),str(out/'review-template.jsonl'))
    reviewed=out/'fixture-reviews.jsonl'
    # This automatically generated approval is DEMO ONLY and identifies its author.
    # Real CLI review templates remain unapproved until a person adjudicates them.
    with reviewed.open('w') as f:
        for row in read_jsonl(proposed):
            for m in row['terminology']['mappings']:
                if m['status']!='model_proposed':continue
                f.write(json.dumps({'mapping_id':m['mapping_id'],'request_digest':m['request_digest'],
                   'reviewed':True,'reviewer':'SYNTHETIC_TEST_NOT_CLINICIAN','reviewed_at':'2026-09-27T00:00:00Z',
                   'decision':'select','candidate_id':m['selected_candidate_id'],'relation':'equivalent',
                   'rationale':'Hand-authored software fixture approval, not clinical validation.'})+'\n')
    final=out/'reviewed.jsonl';apply_decisions(str(proposed),str(final),str(cat),reviews=str(reviewed))
    db=out/'clinical.sqlite';build_index(str(final),str(db),catalog_path=str(cat))
    spec={'name':'LOCAL demo cancer descendants only','source_kinds':['synthetic_fixture'],
          'criteria':[{'id':'cancer','domain':'conditions','where':[{'field':'name','op':'concept_descendant',
              'value':{'system':DEMO_SYSTEM,'version':'synthetic-fixture-v1','code':'root'}}]}]}
    cohort=query_cohort(str(db),spec,str(out/'cohort.jsonl'))
    graph=export_graph(str(final),str(cat),str(out/'graph'))
    result={'mode':'SYNTHETIC_FIXTURES_MOCK_HTTP_ONLY','mapping':linked,'prepared':prepared,
            'selector':inference,'cohort':cohort,'graph':graph,
            'licensed_terminology_content_bundled':False,'gpu_benchmark':None,'clinical_accuracy':None}
    # Avoid presenting synthetic-response speed as model throughput.
    result['selector']['new_requests_per_second']=None
    result['selector']['elapsed_seconds']=None
    write_json(out/'verification.json',result);return result
