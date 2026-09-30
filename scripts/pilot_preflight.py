import os
"""Small, read-only network probes. No weights, datasets or credentials saved."""
import asyncio
import json
from pathlib import Path
import httpx

ROOT = Path('runs/article-pilot/research')
MODELS = ['meta/muse-glimmer-30b', 'thinkingmachines/inkling', 'google/gemma-4-31b-it']

async def main():
    ROOT.mkdir(parents=True, exist_ok=True)
    async with httpx.AsyncClient(timeout=40) as c:
        r = await c.get('https://openrouter.ai/api/v1/models'); r.raise_for_status()
        models = [x for x in r.json()['data'] if x['id'] in MODELS]
        (ROOT/'models.json').write_text(json.dumps(models, indent=2))
        for m in models:
            print(json.dumps({k:m.get(k) for k in ['id','context_length','pricing','architecture','supported_parameters']}), flush=True)
            e = await c.get('https://openrouter.ai/api/v1/models/'+m['id']+'/endpoints')
            if e.is_success:
                (ROOT/(m['id'].replace('/','--')+'-endpoints.json')).write_text(json.dumps(e.json(),indent=2))
        key = os.environ['OPENROUTER_API_KEY']
        r = await c.get('https://openrouter.ai/api/v1/key', headers={'Authorization': 'Bearer '+key})
        print('key_status', r.status_code)
        if r.is_success:
            d = r.json()['data']
            safe = {k:d.get(k) for k in ['limit','limit_remaining','usage','usage_daily','is_free_tier']}
            (ROOT/'budget-status-check.json').write_text(json.dumps(safe,indent=2)); print(safe)
        r = await c.get('https://api.github.com/repos/sparkcpark/synthetic_hospital/git/trees/main',params={'recursive':'1'})
        if r.is_success:
            d=r.json(); (ROOT/'synthetic-hospital-tree.json').write_text(json.dumps(d,indent=2))
            print('synthetic_hospital_sha',d.get('sha'))
            print('ontology_files',[x['path'] for x in d['tree'] if 'ontology' in x['path'] and x['path'].endswith('.py')])

asyncio.run(main())
