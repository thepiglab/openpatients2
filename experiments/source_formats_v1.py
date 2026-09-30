"""Paired acquisition-format pilot; isolated from production and prior protocols.

Prepare before paid calls. Labels never enter prompts. PDF pixels are a separate
arm, not silently supplied to PDF-to-text or official-text arms.
"""
import argparse
import asyncio
import base64
import hashlib
import json
import os
from pathlib import Path
import re

import yaml
from openpatients2.data import write_json
from openpatients2.experiment import Experiment, BudgetExceeded
from openpatients2.figure_attribution import FigureReview, INSTRUCTION
from openpatients2.output_parser import parse_output
from openpatients2.prompts import messages_for
from openpatients2.schemas import wire_schema
from chunking_v1 import gate, source_text

ROOT = Path('runs/source-formats-v1')
ARTICLES = ['PMC12802722', 'PMC12285374', 'PMC10998798']
ARMS = ['jats', 'pmc_text', 'pdf_plain', 'pdf_layout']
UNITS = [('PMC12802722', 'p2', 'observations'),
         ('PMC12802722', 'p3', 'observations'),
         ('PMC10998798', 'p1', 'observations'),
         ('PMC12285374', 'p1', 'medications')]
FIGURES = {'PMC12802722':['F1'], 'PMC12285374':['F1','F2','F3','F4'],
           'PMC10998798':['Fig2','Fig3']}
VISION_PAGES = {'PMC12802722':[2,3,4,5], 'PMC12285374':[1,2,3,4],
                'PMC10998798':[2,3,4,5]}


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def tidy(text):
    # Preserve internal horizontal alignment and line breaks. No digit repair,
    # dehyphenation, OCR, inferred words or column reconstruction.
    return re.sub(r'\n{3,}', '\n\n', '\n'.join(x.rstrip() for x in text.splitlines())).strip()


def prepare():
    if (ROOT/'protocol.json').exists():
        raise ValueError('Protocol already frozen')
    articles = {a['pmcid']:a for a in json.loads(Path('runs/attribution-v1/figure-articles.json').read_text())}
    rosters = json.loads(Path('runs/attribution-v1/figure-rosters.json').read_text())
    sources = {}; audit = []; pages = []
    for pid in ARTICLES:
        a = articles[pid]
        assert a['license']['allowed']
        sources[pid] = {'jats':[{k:s[k] for k in ['segment_id','heading','text']} for s in a['segments']]}
        raw = (ROOT/'downloads'/f'{pid}.1.txt').read_text()
        marker = re.search(r'\x9f=+\x9f', raw)
        if not marker: raise ValueError('Unrecognized official TXT body boundary')
        body = raw[marker.end():]
        reference = re.search(r'(?im)^references\s*$',body)
        if not reference: raise ValueError('Unrecognized TXT references boundary')
        body = body[:reference.start()]
        sources[pid]['pmc_text'] = [{'segment_id':f'txt-{i+1}', 'heading':'', 'text':tidy(t)}
            for i,t in enumerate(re.split(r'\n\s*\n',body.strip())) if t.strip()]
        for mode in ['pdf_plain','pdf_layout']:
            texts = json.loads((ROOT/'sources'/f'{pid}.1.{mode}.json').read_text())
            sources[pid][mode] = [{'segment_id':f'page-{i+1}','heading':f'PDF page {i+1}', 'text':tidy(t)} for i,t in enumerate(texts)]
        for mode,segments in sources[pid].items():
            audit.append({'pmcid':pid,'format':mode,'segments':len(segments),
                          'words':sum(len(s['text'].split()) for s in segments),
                          'text_bytes':len(source_text(segments).encode()),
                          'bibliography_included':mode.startswith('pdf'),
                          'font_code_occurrences':sum(len(re.findall(r'/[a-z]+\.tnum',s['text'])) for s in segments)})
        for n in VISION_PAGES[pid]:
            path=ROOT/'pages'/f'{pid}-{n}.jpg'
            pages.append({'pmcid':pid,'page':n,'path':str(path),'sha256':sha(path),'bytes':path.stat().st_size,'render_dpi':120})
    write_json(ROOT/'sources.json',sources)
    write_json(ROOT/'source-audit.json',audit)
    write_json(ROOT/'pages.json',pages)
    write_json(ROOT/'rosters.json',{pid:[{k:p[k] for k in ['patient_id','label','species']} for p in rosters[pid]['patients']] for pid in ARTICLES})
    write_json(ROOT/'figure-targets.json',{pid:[{'figure_id':f['figure_key'],'label':f['label']} for f in articles[pid]['figures'] if f['figure_key'] in FIGURES[pid]] for pid in ARTICLES})
    original=json.loads(Path('runs/medical-fidelity-v1/reference.json').read_text())
    checks=[c for c in original['checks'] if (c['record_id'].split('.')[0],c['record_id'].split(':')[1],c['task']) in UNITS]
    write_json(ROOT/'reference.json',{**original,'checks':checks})
    write_json(ROOT/'figure-gold.json',json.loads(Path('runs/attribution-v1/figure-gold.json').read_text()))
    models=yaml.safe_load(Path('configs/experiments/medical-fidelity-v1.yaml').read_text())['models'][:4]
    config={'output':str(ROOT/'calls'),'budget_file':str(ROOT/'budget.sqlite'),'budget_usd':3.95,
        'endpoint':'https://openrouter.ai/api/v1','api_key_env':'OPENROUTER_API_KEY','models':models,
        'concurrency':6,'validation_retries':0,'retry_failed':False,'request_deadline_seconds':240}
    write_json(ROOT/'config.json',config)
    paths=[Path(__file__),ROOT/'config.json',ROOT/'sources.json',ROOT/'rosters.json',ROOT/'figure-targets.json',ROOT/'reference.json',ROOT/'figure-gold.json',ROOT/'pages.json',
           Path('src/openpatients2/experiment.py'),Path('src/openpatients2/validation.py'),Path('experiments/chunking_v1.py')]
    write_json(ROOT/'protocol.json',{'hashes':{str(p):sha(p) for p in paths},'arms':ARMS,
       'clinical_units':UNITS,'clinical_output_cap':16384,'figure_output_cap':6144,
       'vision_models':['google/gemma-4-31b-it','meta/muse-spark-1.2'],
       'vision_arm':'pdf_plain plus four source-selected 120-DPI PDF page images; identical text and schema',
       'clinical_checks':len(checks),'figure_units':21,
       'scope':'3 selected articles, fixed reference rosters and figure ID/label inventory only; no JATS clinical facts/captions supplied to TXT/PDF arms; no patient discovery score',
       'cleaning':'JATS uses current pipeline segments; official TXT removes metadata header and bibliography at explicit boundaries; PDF retains all pages because two-column reference boundaries are ambiguous. This is an acquisition pipeline comparison, not a pure serialization ablation.',
       'endpoints_excluded':'Spark contributor and Cohere failed earlier route diagnostics; no renewed paid retry',
       'evaluation':'Frozen partial medical checklist raw and item-gated; 21 figure ownership units; exact segment-local evidence gate; source/physician review status: assistant-audited, not physician validated. Single generation per cell; no repair or cherry picking.'})


def parsed(value):
    if isinstance(value,dict) and set(value)=={'narrative'}:
        return parse_output(value['narrative']).value
    return value


def check_figures(candidate, segments, known, targets):
    good=[];bad=[];seen=set();texts={s['segment_id']:s['text'] for s in segments}
    for value in candidate.get('figures',[]) if isinstance(candidate,dict) else []:
        try:
            r=FigureReview.model_validate(value).model_dump()
            if r['figure_id'] not in targets or r['figure_id'] in seen:raise ValueError('Unknown or duplicate figure')
            seen.add(r['figure_id'])
            for a in r['assignments']:
                if set(a['patient_ids'])-known:raise ValueError('Unknown patient')
                for e in a['evidence']:
                    if not e['quote'] or e['quote'] not in texts.get(e['segment_id'],''):raise ValueError('Nonliteral or missing segment evidence')
            good.append(r)
        except (ValueError,TypeError,KeyError) as e:
            bad.append({'candidate':value,'error':str(e)[:2000]})
    return good,bad


def add_images(messages,pid):
    images=[]
    for p in json.loads((ROOT/'pages.json').read_text()):
        if p['pmcid']!=pid:continue
        path=Path(p['path']);assert sha(path)==p['sha256']
        images.extend([{'type':'text','text':f'PDF page {p["page"]} (page-{p["page"]})'},
                       {'type':'image_url','image_url':{'url':'data:image/jpeg;base64,'+base64.b64encode(path.read_bytes()).decode()}}])
    messages[-1]['content']=[{'type':'text','text':messages[-1]['content']},*images]
    return messages


async def run(phase):
    protocol=json.loads((ROOT/'protocol.json').read_text())
    assert all(sha(p)==h for p,h in protocol['hashes'].items()),'Frozen protocol changed'
    config=json.loads((ROOT/'config.json').read_text());config['output']=str(ROOT/('calls-'+phase))
    exp=Experiment(config)
    sources=json.loads((ROOT/'sources.json').read_text());rosters=json.loads((ROOT/'rosters.json').read_text())
    targets=json.loads((ROOT/'figure-targets.json').read_text())
    outcome_dir=ROOT/'outcomes';outcome_dir.mkdir(exist_ok=True)
    models=[m for m in config['models'] if phase!='vision' or m['id'] in protocol['vision_models']]
    arms=['pdf_pixels'] if phase=='vision' else ARMS
    async def cell(model,pid,patient,task,arm):
        segments=sources[pid]['pdf_plain' if arm=='pdf_pixels' else arm]
        identity={'pmcid':pid,'patient_id':patient,'domain':task,'format':arm}
        if task=='figures':
            system=INSTRUCTION.replace('ONLY the requested figure','all requested figures')
            msgs=[{'role':'system','content':system}, {'role':'user','content':json.dumps({'patients':rosters[pid], 'requested_figures':targets[pid], 'source':source_text(segments)},ensure_ascii=False)+'\nReturn {"figures": [one object per requested figure]}. Each object uses this schema:\n'+json.dumps(FigureReview.model_json_schema())}]
            cap=6144
        else:
            record={'record_id':pid+'.1:'+patient,'source_kind':'published_article_excerpt','text':source_text(segments),
                    'patient_target':{'patient_id':patient,'patient_registry':rosters[pid]}}
            msgs=messages_for(record,task,'source-formats-v1')
            msgs[-1]['content']+='\nReturn the complete section, not a summary. Use the displayed segment IDs as source_section. For PDF or TXT tables, quote the actual row and its separate column/time headers; they need not be repeated in each row. Do not fabricate a new combined quote.\nSCHEMA:\n'+json.dumps(wire_schema(task))
            cap=16384
        if arm=='pdf_pixels':
            msgs[-1]['content']+='\nPDF page images accompany the exact same extracted text. Use them to resolve layout and labels. Copy evidence from the extracted text when available; image-only evidence must identify its page and will require visual review before export.'
            msgs=add_images(msgs,pid)
        try:
            result=await exp.call(model,'roster',msgs,parsed,identity,max_tokens=cap,raw_text=True)
        except BudgetExceeded as e:
            result={'status':'budget_blocked','errors':[str(e)],'data':None,'metrics':{}}
        candidate=result.get('data')
        if task=='figures':
            delivered,rejected=check_figures(candidate,segments,{p['patient_id'] for p in rosters[pid]},FIGURES[pid])
        else:delivered,rejected=gate(task,candidate,segments)
        row={'model':model['id'],**identity,'record_id':pid+'.1:'+str(patient),'candidate':candidate,
             'delivered':delivered,'rejected':rejected,'result':result}
        path=outcome_dir/f'{model["id"].replace("/","--")}--{pid}--{patient or "article"}--{task}--{arm}.json'
        write_json(path,row)
    try:
        units=UNITS+[(p,None,'figures') for p in ARTICLES]
        for pid,patient,task in units:
            await asyncio.gather(*(cell(m,pid,patient,task,arm) for arm in arms for m in models))
            write_json(ROOT/('budget-'+phase+'.json'),exp.budget.report())
    finally:
        write_json(ROOT/('budget-'+phase+'.json'),exp.budget.report());await exp.close()


if __name__=='__main__':
    ap=argparse.ArgumentParser();ap.add_argument('mode',choices=['prepare','text','vision']);ap.add_argument('--key-file')
    args=ap.parse_args()
    if args.key_file:os.environ['OPENROUTER_API_KEY']=Path(args.key_file).read_text().strip()
    if args.mode=='prepare':prepare()
    else:asyncio.run(run(args.mode))
