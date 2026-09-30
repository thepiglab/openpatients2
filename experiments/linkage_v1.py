"""Explicit identity vs citation/similarity: controlled probes plus one real pair.

The real prior publication is a licensed English abstract excerpt from Korean
publisher JATS, not an invented PMC record. It stays reference-only: this script
does not certify publisher retraction status or export it as a clinical seed.
"""
import argparse
import asyncio
import copy
import hashlib
import json
import os
from pathlib import Path
from openpatients2.case_links import link_messages, validate_link, bind_link
from openpatients2.experiment import Experiment
from openpatients2.output_parser import parse_output
from openpatients2.articles import parse_article, _clean
from openpatients2.pmc_media import parsed_xml, local_name, xml_text
from openpatients2.provenance import json_digest
from openpatients2.data import write_json

ROOT=Path('runs/attribution-v1')


def patient(pid, text, segment):
    return {'patient_id':pid,'label':text,'species':'unknown','species_as_documented':None,
            'identity_evidence':[{'segment_id':segment,'quote':text}], 'source_segment_ids':[segment],
            'attribution_limitations':[]}


def synthetic_article(aid, paragraphs, patients, refs=None):
    segments=[{'segment_id':f'b{i:05}', 'text':text, 'heading':heading, 'kind':'paragraph',
               'cross_references':[{'ref_type':'bibr','target_ids':['R1'],'label':'1'}] if callout else [],
               'text_with_reference_markers':text.replace('[1]','[BIBR:R1 1]') if callout else None}
              for i,(heading,text,callout) in enumerate(paragraphs,1)]
    a={'article_id':aid,'pmcid':None,'pmid':None,'doi':'10.9999/'+aid,'title':'Synthetic mechanism probe '+aid,
       'segments':segments,'references':refs or [],'xml_sha256':json_digest(segments),
       'license':{'allowed':True,'code':'CC0','reason':'authored_synthetic_fixture'},'synthetic_fixture':True}
    r={'patients':[patient(pid,text,sid) for pid,text,sid in patients]}
    return a,r


def prepare():
    if (ROOT/'link-protocol.json').exists():raise ValueError('Already frozen')
    cases=[]
    def pair(name,source_text,target_text,relation,sp='p1',tp='p1',target_extra=None,source_extra=None):
        b,rb=synthetic_article(name+'-A',[('Case 1',target_text,False)]+(target_extra or []),
                               [('p1',target_text,'b00001')]+([('p2',target_extra[0][1],'b00002')] if target_extra else []))
        ref={'reference_id':'R1','identifiers':{'pmcid':[],'pmid':[],'doi':[b['doi']]}}
        a,ra=synthetic_article(name+'-B',[('Case report',source_text,True)]+(source_extra or []),
                               [('p1',source_text,'b00001')]+([('p2',source_extra[0][1],'b00002')] if source_extra else []),[ref])
        cases.append({'case_id':name,'source':a,'target':b,'source_roster':ra,'target_roster':rb,
                      'expected':{'relations':relation,'source_patient_id':sp,'target_patient_id':tp},'kind':'synthetic_mechanism_probe'})
    pair('explicit_followup','This 51-year-old man is the same patient described at age 49 in our previous report [1]. He returned two years later with recurrent weakness.',
         'Case 1 was a 49-year-old man treated for a duodenal tumor.', ['explicit_same_patient'])
    pair('similar_case','Our 49-year-old man had a duodenal tumor. A similar case in another patient was described previously [1].',
         'A 49-year-old man underwent resection of a duodenal tumor.', ['similar_case','cited_case_mention'],sp=None,tp='p1')
    pair('literature_only_mention','Our index case is a 60-year-old woman. For comparison, case 2 from the earlier report [1] was a 49-year-old man with renal failure; he is not our index patient.',
         'Case 1 was a 25-year-old woman with a rash.', ['cited_case_mention'],sp=None,tp='p2',
         target_extra=[('Case 2','Case 2 was a 49-year-old man with renal failure.',False)])
    pair('ambiguous_prior_case','Our patient is one of the two patients described in the previous report [1], but the case number is unavailable.',
         'Case 1 was a 40-year-old man with migraine.', ['uncertain'],sp=None,tp=None,
         target_extra=[('Case 2','Case 2 was a 40-year-old man with migraine.',False)])
    pair('cohort_overlap','This cohort contains the same 30 patients as our prior report [1]. Individual linkage keys are unavailable.',
         'Thirty participants received treatment; only aggregate outcomes are provided.', ['cohort_overlap'],sp=None,tp=None)
    cases[-1]['source_roster']['patients']=[];cases[-1]['target_roster']['patients']=[]
    pair('species_contradiction','The present patient is a 5-year-old dog. The manuscript calls it the same patient reported in [1], although the earlier report describes a human.',
         'Case 1 was a 5-year-old human boy.', ['uncertain'],sp=None,tp=None)
    pair('method_citation','Our patient is a 49-year-old man with a tumor. We used the staining protocol from the prior case report [1]; that paper does not describe our patient.',
         'A 49-year-old man with a tumor underwent immunohistochemical staining.', ['background_citation'],sp=None,tp=None)
    pair('explicit_wrong_case_trap','Case 1 is a new patient, a 49-year-old man. Case 2 is the same patient designated Case 1 in our earlier report [1].',
         'Case 1 was a 70-year-old woman treated for heart failure.', ['explicit_same_patient'],sp='p2',tp='p1',
         source_extra=[('Case 2','Case 2 is a 71-year-old woman returning one year after her first heart failure admission.',False)])
    # Real follow-up: current full JATS, prior licensed English abstract only.
    metadata=json.loads((ROOT/'metadata/PMC12784145.json').read_text())['records'][0]
    source=parse_article((ROOT/'xml/PMC12784145.1.xml').read_text(),metadata)
    xml=(ROOT/'original-case.xml').read_text();doc=parsed_xml(xml)
    abstract=next(x for x in doc.iter() if local_name(x.tag)=='abstract')
    text=_clean(abstract);license_text=xml_text(next(x for x in doc.iter() if local_name(x.tag)=='license'))
    target={'article_id':'doi:10.4166/kjg.2018.72.1.28','pmcid':None,'pmid':'30049175','doi':'10.4166/kjg.2018.72.1.28',
            'title':'Case of an Inflammatory Myofibroblastic Tumor of the Duodenum',
            'source_url':'https://synapse.koreamed.org/articles/1099108',
            'xml_sha256':hashlib.sha256(xml.encode()).hexdigest(), 'segments':[{'segment_id':'abstract-en','text':text}],
            'references':[], 'source_coverage':'English abstract excerpt only',
            'retraction_status':'not_independently_verified', 'clinical_export_allowed':False,
            'license':{'allowed':True,'code':'CC BY-NC','statements':[{'type':'license','text':license_text}],
                       'verification':'Publisher JATS explicit CC BY-NC 3.0; license approval only, not PMC acquisition approval'}}
    case=next(s for s in source['segments'] if s['heading']=='CASE REPORT')
    ra={'patients':[patient('p1',case['text'][:case['text'].index('.')+1],case['segment_id'])]}
    rb={'patients':[patient('p1','We encountered a case of a 49-year-old male with a duodenal IMT.','abstract-en')]}
    cases.append({'case_id':'real_duodenal_followup','source':source,'target':target,'source_roster':ra,'target_roster':rb,
                  'expected':{'relations':['explicit_same_patient'],'source_patient_id':'p1','target_patient_id':'p1'},
                  'kind':'real_published_pair_abstract_target','limitation':'Not a production enrichment export; prior source retraction status not independently verified.'})
    write_json(ROOT/'link-cases.json',cases)
    config=json.loads((ROOT/'figure-config.json').read_text());config['output']=str(ROOT/'links');config['concurrency']=6
    write_json(ROOT/'link-config.json',config)
    paths=[Path(__file__),Path('src/openpatients2/case_links.py'),ROOT/'link-cases.json',ROOT/'link-config.json']
    write_json(ROOT/'link-protocol.json',{'files':{str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in paths},
        'n_synthetic':8,'n_real':1,'models':[m['id'] for m in config['models']],
        'gold_method':'Assistant-authored controls; real identity explicitly declared by the source; no physician adjudication',
        'primary_outcome':'false identity claims; exact original/local target mapping for identity and literature mentions',
        'secondary_outcome':'relationship classification; source/target IDs for non-identity methods/cohort links are not scored'})


async def run():
    protocol=json.loads((ROOT/'link-protocol.json').read_text())
    assert all(hashlib.sha256(Path(p).read_bytes()).hexdigest()==h for p,h in protocol['files'].items())
    exp=Experiment(json.loads((ROOT/'link-config.json').read_text()));outcomes=[]
    async def cell(case,model):
        a,b,ra,rb=[case[k] for k in ['source','target','source_roster','target_roster']]
        def check(v):
            parsed=parse_output(v['narrative']).value if 'narrative' in v else v
            return validate_link(parsed,a,b,ra,rb)
        result=await exp.call(model,'roster',link_messages(a,b,ra,rb),check,{'case_id':case['case_id'],'phase':'citation_link'},
                              max_tokens=2500,raw_text=True)
        edge=bind_link(result['data'],a,b,ra,rb,method=model['id']) if result['status']=='valid' else None
        row={'case_id':case['case_id'],'model':model['id'],'kind':case['kind'],'result':result,'edge':edge}
        outcomes.append(row);write_json(ROOT/'link-outcomes.json',outcomes)
    try:
        cases=json.loads((ROOT/'link-cases.json').read_text())
        await asyncio.gather(*(cell(c,m) for c in cases for m in exp.config['models']))
        write_json(ROOT/'link-metrics.json',exp.metrics)
    finally:
        write_json(ROOT/'link-budget-status.json',exp.budget.report());await exp.close()


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('mode',choices=['prepare','run']);p.add_argument('--key-file');a=p.parse_args()
    if a.key_file:os.environ['OPENROUTER_API_KEY']=Path(a.key_file).read_text().strip()
    if a.mode=='prepare':prepare()
    else:asyncio.run(run())
