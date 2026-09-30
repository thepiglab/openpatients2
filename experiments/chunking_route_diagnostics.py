"""Small, budgeted endpoint diagnostics; never include credentials in artifacts."""
import asyncio
import json
from pathlib import Path
import httpx
from openpatients2.experiment import Budget
from openpatients2.data import write_json

async def main():
    root = Path('runs/chunking-v1')
    budget = Budget(root/'budget.sqlite', 9)
    key = Path('/tmp/op2-chunking-key').read_text().strip()
    settings = json.loads((root/'calls/experiment-config.json').read_text())['models']
    async with httpx.AsyncClient(timeout=60) as client:
        for model in settings:
            if model['id'] not in {'cohere/command-a-plus','meta/muse-spark-1.2-contributor'}: continue
            body = {'model':model['id'],'messages':[{'role':'user','content':'A patient sodium is 143 mmol/L. Return exactly {"sodium":143}.'}],
                    'max_tokens':2048,'temperature':0, 'stream':False, **model['extra_body']}
            charge = budget.reserve('nonstream-diagnostic:'+model['id'], (131072*model['max_input_per_million']+2048*model['max_output_per_million'])/1e6)
            try:
                response = await client.post('https://openrouter.ai/api/v1/chat/completions',json=body,headers={'Authorization':'Bearer '+key})
                data = response.json()
                budget.settle(charge, data.get('usage',{}).get('cost'))
                write_json(root/'route-diagnostics'/f"{model['id'].replace('/', '--')}.json", {'http_status':response.status_code,'request':body,'response':data})
                print(model['id'],response.status_code,json.dumps(data)[:2500],flush=True)
            except httpx.HTTPError as e:
                budget.settle(charge,None)
                print(model['id'],type(e).__name__,flush=True)
    budget.db.close()

if __name__ == '__main__': asyncio.run(main())
