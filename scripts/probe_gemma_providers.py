import asyncio,json,os,time
from pathlib import Path
import httpx
from openpatients2.experiment import Budget

async def main():
    key=os.environ['OPENROUTER_API_KEY']
    b=Budget(Path('runs/article-pilot/comparison/budget.sqlite'),25)
    async def one(provider):
        charge=b.reserve('gemma-provider-probe:'+provider,.15); started=time.monotonic()
        try:
            async with httpx.AsyncClient(timeout=25) as c:
                r=await c.post('https://openrouter.ai/api/v1/chat/completions',headers={'Authorization':'Bearer '+key},json={
                    'model':'google/gemma-4-31b-it','messages':[{'role':'user','content':'Reply with exactly {"ok":true}.'}],
                    'max_tokens':64,'temperature':0,'reasoning':{'enabled':False},
                    'provider':{'only':[provider],'allow_fallbacks':False,'max_price':{'prompt':.15,'completion':.5}}})
                d=r.json(); b.settle(charge,d.get('usage',{}).get('cost'))
                result={'provider':provider,'status':r.status_code,'elapsed':time.monotonic()-started,'response':d}
        except Exception as e:
            b.settle(charge,None);result={'provider':provider,'error':type(e).__name__}
        Path('runs/article-pilot/research/gemma-probe-'+provider.replace('/','--')+'.json').write_text(json.dumps(result,indent=2))
        print({k:v for k,v in result.items() if k!='response'},flush=True)
    await asyncio.gather(*(one(p) for p in ['deepinfra/turbo','crusoe/bf16','coreweave/fp4']))
    b.db.close()
asyncio.run(main())
