import json
import os
from pathlib import Path
import httpx
import yaml
c=yaml.safe_load(Path('configs/experiments/article-unconstrained.yaml').read_text())
m=c['models'][0]
key=os.environ['OPENROUTER_API_KEY']
r=httpx.post(c['endpoint']+'/chat/completions',headers={'Authorization':'Bearer '+key},
    json={'model':m['id'],'messages':[{'role':'user','content':'Reply with OK.'}],'max_tokens':8,**m['extra_body']},timeout=20)
print(r.status_code)
if r.is_error:
    print(r.text.replace(key,'[redacted]')[:2000])
else:
    print('usage',r.json().get('usage'))
