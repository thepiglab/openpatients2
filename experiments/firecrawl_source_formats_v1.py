"""User-requested additional Rust parser arm; shares the primary study budget.

Run prepare with PYTHONPATH pointing to the temporary pdf-inspector wheel.
Only native extract_pages_markdown is used; no OCR models or hosted Firecrawl.
"""
import argparse
import asyncio
import importlib.metadata
import json
import os
from pathlib import Path
from statistics import median
import time

import source_formats_v1 as primary
from openpatients2.data import write_json

BASE=primary.ROOT
EXT=BASE/'firecrawl'


def prepare():
    import pdf_inspector
    if (EXT/'protocol.json').exists():raise ValueError('Extension protocol already frozen')
    EXT.mkdir(exist_ok=True)
    sources={};metrics=[]
    for pid in primary.ARTICLES:
        path=BASE/'downloads'/f'{pid}.1.pdf';times=[]
        for n in range(6):
            start=time.perf_counter();r=pdf_inspector.extract_pages_markdown(str(path));times.append(time.perf_counter()-start)
        sources[pid]={'pdf_firecrawl':[{'segment_id':f'page-{p.page+1}','heading':f'PDF page {p.page+1}',
                                     'text':primary.tidy(p.markdown)} for p in r.pages]}
        write_json(EXT/f'{pid}.pages.json',[{'page':p.page+1,'markdown':p.markdown,'needs_ocr':p.needs_ocr,'ocr_reason':p.ocr_reason} for p in r.pages])
        metrics.append({'pmcid':pid,'engine':'pdf-inspector','version':importlib.metadata.version('pdf-inspector'),
                        'function':'extract_pages_markdown','ocr_used':False,'pdf_sha256':primary.sha(path),
                        'first_seconds':times[0],'warm_five_seconds':times[1:],'warm_median_seconds':median(times[1:]),
                        'pages':len(r.pages),'pages_needing_ocr':r.pages_needing_ocr,'pages_with_tables':r.pages_with_tables,
                        'pages_with_columns':r.pages_with_columns,'is_complex':r.is_complex})
    write_json(EXT/'sources.json',sources);write_json(EXT/'parser-metrics.json',metrics)
    for name in ['config.json','rosters.json','figure-targets.json','reference.json','figure-gold.json','pages.json']:
        write_json(EXT/name,json.loads((BASE/name).read_text()))
    config=json.loads((EXT/'config.json').read_text());assert config['budget_file']==str(BASE/'budget.sqlite')
    protocol=json.loads((BASE/'protocol.json').read_text())
    paths=[Path(__file__),Path('experiments/source_formats_v1.py'),EXT/'sources.json',EXT/'config.json',EXT/'rosters.json',EXT/'figure-targets.json',EXT/'reference.json',EXT/'figure-gold.json']
    protocol.update(hashes={str(p):primary.sha(p) for p in paths},arms=['pdf_firecrawl'],
        amendment='Additional arm requested by user during primary run. Identical prompts, fixed targets, output limits and scoring; page Markdown from pdf-inspector 1.25.2, all pages retained. Shared original $3.95 ledger.',
        source_documentation='https://github.com/firecrawl/pdf-inspector/blob/main/docs/python.md')
    write_json(EXT/'protocol.json',protocol)
    print(json.dumps(metrics,indent=2))


if __name__=='__main__':
    ap=argparse.ArgumentParser();ap.add_argument('mode',choices=['prepare','run']);ap.add_argument('--key-file');args=ap.parse_args()
    if args.mode=='prepare':prepare()
    else:
        if args.key_file:os.environ['OPENROUTER_API_KEY']=Path(args.key_file).read_text().strip()
        primary.ROOT=EXT;primary.ARMS=['pdf_firecrawl']
        asyncio.run(primary.run('text'))
