"""Exploratory, manually located column-region fallback on two difficult pages.

This tests feasibility, not an automatic layout detector. No gold values or
patient labels are used to reconstruct text; only two page-midpoint boundaries.
"""
import argparse,asyncio,json,os,time
from pathlib import Path
import source_formats_v1 as primary
from openpatients2.data import write_json

BASE=primary.ROOT
EXT=BASE/'firecrawl-regions'

def prepare():
    import pdf_inspector
    if (EXT/'protocol.json').exists():raise ValueError('Already frozen')
    EXT.mkdir(exist_ok=True)
    sources=json.loads((BASE/'firecrawl/sources.json').read_text());regions=[]
    for pid,number in [('PMC12802722',4),('PMC10998798',3)]:
        # Page MediaBox dimensions were checked in the PDF renderer. These
        # sources use an A4-like 595.276 x 841.89 page with two columns.
        boxes=[[0,0,297.5,842],[297.5,0,595.5,842]]
        start=time.perf_counter()
        result=pdf_inspector.extract_text_in_regions(str(BASE/'downloads'/f'{pid}.1.pdf'),[(number-1,boxes)])
        texts=result[0].regions
        segments=sources[pid]['pdf_firecrawl']; replacement=[]
        for s in segments:
            if s['segment_id']!=f'page-{number}':replacement.append(s);continue
            for n,r in enumerate(texts):
                replacement.append({'segment_id':f'page-{number}-column-{n+1}','heading':f'PDF page {number}, column {n+1}', 'text':primary.tidy(r.text)})
        sources[pid]['pdf_firecrawl']=replacement
        regions.append({'pmcid':pid,'page':number,'boxes_pdf_points_top_left':boxes,'seconds':time.perf_counter()-start,'manual_layout_selection':True,'needs_ocr':[r.needs_ocr for r in texts]})
    sources={p:{'pdf_firecrawl_regions':x['pdf_firecrawl']} for p,x in sources.items()}
    write_json(EXT/'sources.json',sources);write_json(EXT/'regions.json',regions)
    for name in ['config.json','rosters.json','figure-targets.json','reference.json','figure-gold.json','pages.json']:
        write_json(EXT/name,json.loads((BASE/name).read_text()))
    config=json.loads((EXT/'config.json').read_text());config['models']=[m for m in config['models'] if m['id']=='meta/muse-spark-1.2'];write_json(EXT/'config.json',config)
    protocol=json.loads((BASE/'protocol.json').read_text())
    files=[Path(__file__),Path('experiments/source_formats_v1.py'),EXT/'sources.json',EXT/'config.json',EXT/'rosters.json']
    protocol.update(hashes={str(p):primary.sha(p) for p in files},arms=['pdf_firecrawl_regions'],clinical_units=primary.UNITS[:3],figure_units=0,
        amendment='Post-hoc feasibility test after inspecting a merged Firecrawl table: replace two difficult PDF pages with separately extracted left/right columns. Same full article, same prompt; Spark only; three clinical units; no figure task. Manually selected geometry, not general automatic routing.')
    write_json(EXT/'protocol.json',protocol)

if __name__=='__main__':
    ap=argparse.ArgumentParser();ap.add_argument('mode',choices=['prepare','run']);ap.add_argument('--key-file');a=ap.parse_args()
    if a.mode=='prepare':prepare()
    else:
        if a.key_file:os.environ['OPENROUTER_API_KEY']=Path(a.key_file).read_text().strip()
        primary.ROOT=EXT;primary.ARMS=['pdf_firecrawl_regions'];primary.UNITS=primary.UNITS[:3];primary.ARTICLES=[]
        asyncio.run(primary.run('text'))
