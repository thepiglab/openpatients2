"""Small stratified convenience sample, not a population length survey."""
import asyncio
import json
from pathlib import Path
import httpx
from openpatients2.corpus import search_candidates, acquire, length_report
from openpatients2.data import write_json

STRATA = {
 'single': ('"case report"[Title] AND 2023[pdat]',3),
 'multiple': ('("two cases"[Title] OR "three cases"[Title]) AND 2023[pdat]',3),
 'animal': ('(dog[Title] OR cat[Title]) AND "case report"[Title] AND 2023[pdat]',2),
 'noncommercial': ('"case report"[Title] AND "cc by-nc-sa license"[filter] AND 2023[pdat]',2),
 'aggregate_control': ('"meta-analysis"[Title] AND 2023[pdat]',1),
 'non_case_tag': ('"patient"[Title] AND 2023[pdat] NOT "case reports"[Publication Type]',1),
}

async def main():
    root = Path('runs/article-pilot'); root.mkdir(parents=True,exist_ok=True)
    searches = {}
    for name,(query,n) in STRATA.items():
        searches[name] = await search_candidates(query,n)
        print(name,searches[name]['total'],searches[name]['pmcids'],flush=True)
        await asyncio.sleep(.4)
    write_json(root/'sample-selection.json', searches)
    ids=[pid for s in searches.values() for pid in s['pmcids']]
    result=await acquire(ids,str(root/'articles.jsonl'),max_articles=20,max_total_bytes=20_000_000)
    print(result)
    write_json(root/'lengths.json',length_report(str(root/'articles.jsonl')))
    # Read only selected code from a pinned Synthetic Hospital commit.
    research=root/'research'
    tree=json.loads((research/'synthetic-hospital-tree.json').read_text())
    async with httpx.AsyncClient(timeout=40) as c:
        for path in ['etl/stages/s05_ontology.py','etl/ontology/icd10.py','etl/ontology/snomed.py','etl/ontology/loinc.py','etl/ontology/sapbert_embedder.py']:
            r=await c.get(f'https://raw.githubusercontent.com/sparkcpark/synthetic_hospital/{tree["sha"]}/{path}')
            if r.is_success and len(r.content)<200_000:
                (research/('synthetic-hospital-'+Path(path).name)).write_text(r.text)

asyncio.run(main())
