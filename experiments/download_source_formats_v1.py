"""Bounded downloads from previously resolved, versioned PMC manifests only."""
import urllib.request, json, hashlib, time
from pathlib import Path
root=Path('runs/source-formats-v1'); manifest=[];availability=[]
(root/'downloads').mkdir(parents=True,exist_ok=True)
for pmcid in ['PMC12802722','PMC12285374','PMC10998798','PMC12773240']:
 m=json.loads(Path(f'runs/attribution-v1/metadata/{pmcid}.json').read_text())['records'][0]
 if m.get('license_code') not in {'CC0','CC BY','CC BY-SA','CC BY-NC','CC BY-NC-SA'} or m.get('is_retracted') is not False:
  raise ValueError('Source license/retraction gate failed')
 availability.append({'pmcid':pmcid,'version':m['version'],'text_available':bool(m.get('text_url')),'pdf_available':bool(m.get('pdf_url'))})
 for fmt in ['text','pdf']:
  url=m.get(fmt+'_url')
  if not url: continue
  path=root/'downloads'/f'{pmcid}.1.{"txt" if fmt=="text" else fmt}'
  if path.exists():data=path.read_bytes()
  else:
   for attempt in range(3):
    try:
     with urllib.request.urlopen(url,timeout=40) as response:data=response.read(12_000_001)
     break
    except Exception:
     if attempt==2:raise
     time.sleep(2)
  if len(data)>12_000_000:raise ValueError('File size limit')
  assert (fmt!='pdf' or data.startswith(b'%PDF'))
  md5=hashlib.md5(data).hexdigest()
  assert url.split('md5=')[-1]==md5
  if sum(r['bytes'] for r in manifest)+len(data)>32_000_000:raise ValueError('Total download limit')
  path.write_bytes(data)
  row={'pmcid':pmcid,'version':m['version'],'format':fmt,'url':url,'path':str(path),'bytes':len(data),'sha256':hashlib.sha256(data).hexdigest(),'md5':md5,'license_code':m['license_code'],'metadata_source':f'runs/attribution-v1/metadata/{pmcid}.json'}
  manifest.append(row);print(pmcid,fmt,len(data),flush=True)
(root/'download-manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
(root/'availability.json').write_text(json.dumps(availability,indent=2)+'\n')
