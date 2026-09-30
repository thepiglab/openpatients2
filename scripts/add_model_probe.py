import asyncio,json
from pathlib import Path
import httpx

async def main():
    mid='meta/muse-spark-1.2-contributor'
    async with httpx.AsyncClient(timeout=30) as c:
        r=await c.get('https://openrouter.ai/api/v1/models');r.raise_for_status()
        model=next(m for m in r.json()['data'] if m['id']==mid)
        e=await c.get('https://openrouter.ai/api/v1/models/'+mid+'/endpoints');e.raise_for_status()
        p=Path('runs/article-pilot/research')
        (p/'spark-model.json').write_text(json.dumps(model,indent=2))
        (p/'spark-endpoints.json').write_text(json.dumps(e.json(),indent=2))
        print({k:model.get(k) for k in ['id','context_length','pricing','architecture','supported_parameters']})
        for ep in e.json()['data']['endpoints']:
            print({k:ep.get(k) for k in ['name','tag','pricing','supported_parameters','context_length']})
asyncio.run(main())
