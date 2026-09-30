"""Small frozen attribution ablation: whole text, linked context, linked context + pixels.

No model weights, ontology or bulk image download. Reference labels are an
assistant source audit, not physician gold or a representative accuracy sample.
"""
import argparse
import asyncio
import hashlib
import json
import os
from pathlib import Path
import yaml
from openpatients2.articles import parse_article
from openpatients2.article_tasks import check_article_task
from openpatients2.figure_attribution import figure_messages, validate_figure_review, bind_figure_review
from openpatients2.experiment import Experiment
from openpatients2.vision import fetch_pixels
from openpatients2.data import write_json

ROOT = Path('runs/attribution-v1')
SELECTED = [('PMC12285374','F1'),('PMC12285374','F2'),('PMC12285374','F3'),('PMC12285374','F4'),
            ('PMC10998798','Fig2'),('PMC10998798','Fig3'),('PMC12802722','F1')]


def sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def prepare():
    if (ROOT/'figure-protocol.json').exists():raise ValueError('Protocol already frozen')
    old={a['pmcid']:a for a in map(json.loads,Path('runs/medical-fidelity-v1/articles.jsonl').read_text().splitlines())}
    articles=[];rosters={}
    for pid in dict(SELECTED):
        m=json.loads((ROOT/'metadata'/f'{pid}.json').read_text())['records'][0]
        a=parse_article((ROOT/'xml'/f'{pid}.1.xml').read_text(),m);a['status']='eligible'
        assert a['xml_sha256']==old[pid]['xml_sha256'] and a['text']==old[pid]['text']
        ref=json.loads(Path(f'runs/medical-fidelity-v1/reference-rosters/{pid}.1.json').read_text())
        rosters[pid]=check_article_task('roster',ref['roster'],a);articles.append(a)
    write_json(ROOT/'figure-articles.json',articles);write_json(ROOT/'figure-rosters.json',rosters)
    gold=[]
    for pid,fid in SELECTED:
        if pid=='PMC12285374':
            patient='p1' if fid in {'F1','F2'} else 'p2'
            for panel in ('AB' if fid in {'F1','F3'} else 'ABCDE'):
                gold.append({'pmcid':pid,'figure_id':fid,'panel':panel,'patient_ids':[patient],
                             'scope':'individual','subject':'patient' if panel=='A' and fid in {'F1','F3'} else 'patient_specimen'})
        elif pid=='PMC10998798':
            for panel in ('ABCD' if fid=='Fig2' else 'AB'):
                external=fid=='Fig3' and panel=='B'
                gold.append({'pmcid':pid,'figure_id':fid,'panel':panel,'patient_ids':[] if external else ['p1'],
                             'scope':'external' if external else 'individual',
                             'subject':'external_patient' if external else 'organism_from_patient'})
        else:
            gold.append({'pmcid':pid,'figure_id':fid,'panel':None,'patient_ids':['p1','p2','p3'],
                         'scope':'shared','subject':'patient'})
    write_json(ROOT/'figure-gold.json',{'review_method':'assistant audit of source captions and body figure references before model calls',
                                     'physician_reviewed':False,'units':gold})
    model_config=yaml.safe_load(Path('configs/experiments/medical-fidelity-v1.yaml').read_text())
    models=model_config['models'][:4]
    config={'output':str(ROOT/'figures'), 'budget_file':str(ROOT/'budget.sqlite'), 'budget_usd':4.5,
            'endpoint':'https://openrouter.ai/api/v1','api_key_env':'OPENROUTER_API_KEY','models':models,
            'concurrency':8,'validation_retries':0,'retry_failed':False,'request_deadline_seconds':150}
    write_json(ROOT/'figure-config.json',config)
    paths=[Path(__file__), Path('src/openpatients2/figure_attribution.py'),Path('src/openpatients2/experiment.py'),
           ROOT/'figure-articles.json',ROOT/'figure-rosters.json',ROOT/'figure-gold.json',ROOT/'figure-config.json']
    write_json(ROOT/'figure-protocol.json',{'files':{str(p):sha(p) for p in paths},'strategies':['whole_text','focused_text','focused_pixels'],
       'selected_figures':SELECTED,'models':[m['id'] for m in models], 'output_cap':4096,
       'denominators':'21 panel/shared-figure units in 7 figures across 3 articles; repeated per strategy/model',
       'coverage_limitations':'Known patient rosters; focused case set; no physician review; not a patient discovery or visual diagnostic accuracy benchmark',
       'unsupported_routes':'Contributor privacy block and Cohere tool-generation errors already recorded in chunking-v1; no paid retry here'})


async def run():
    protocol=json.loads((ROOT/'figure-protocol.json').read_text())
    assert all(sha(p)==s for p,s in protocol['files'].items()), 'Frozen protocol changed'
    exp=Experiment(json.loads((ROOT/'figure-config.json').read_text()))
    articles={a['pmcid']:a for a in json.loads((ROOT/'figure-articles.json').read_text())}
    rosters=json.loads((ROOT/'figure-rosters.json').read_text())
    outcomes=[]
    try:
        # One figure in memory at a time; at most 8 in-flight model requests.
        for pid,fid in SELECTED:
            a=articles[pid];r=rosters[pid];f=next(f for f in a['figures'] if f['figure_key']==fid)
            pixels=info=None
            if f['rights_statements'] or f['reuse_status']=='asset_rights_review':
                raise ValueError('Asset rights exception: '+fid)
            try:pixels,info=await fetch_pixels(f['image_urls'][0],max_bytes=4_000_000)
            except Exception as e:info={'fetch_error':type(e).__name__}
            async def cell(m, strategy):
                if strategy=='focused_pixels' and pixels is None:
                    return {'pmcid':pid,'figure_id':fid,'model':m['id'],'strategy':strategy,'status':'pixel_fetch_failed'}
                msgs=figure_messages(a,r,fid,focused=strategy!='whole_text',pixels=pixels if strategy=='focused_pixels' else None)
                result=await exp.call(m,'figure_attribution',msgs,lambda v:validate_figure_review(v,a,r,fid),
                                     {'pmcid':pid,'figure_id':fid,'strategy':strategy},max_tokens=4096)
                review=bind_figure_review(result['data'],a,r,method=m['id']+':'+strategy,
                                         pixel_provenance=info if strategy=='focused_pixels' else None) if result['status']=='valid' else None
                return {'pmcid':pid,'figure_id':fid,'model':m['id'],'strategy':strategy,'result':result,
                        'review':review,'pixel_provenance':info if strategy=='focused_pixels' else None}
            outcomes.extend(await asyncio.gather(*(cell(m,s) for s in protocol['strategies'] for m in exp.config['models'])))
            del pixels
            write_json(ROOT/'figure-outcomes.json',outcomes)
        write_json(ROOT/'figure-metrics.json',exp.metrics)
    finally:
        write_json(ROOT/'budget-status.json',exp.budget.report());await exp.close()


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('mode',choices=['prepare','run']);parser.add_argument('--key-file')
    args=parser.parse_args()
    if args.key_file:os.environ['OPENROUTER_API_KEY']=Path(args.key_file).read_text().strip()
    if args.mode=='prepare':prepare()
    else:asyncio.run(run())
