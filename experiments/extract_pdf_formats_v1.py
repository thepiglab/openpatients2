"""Offline PDF extraction, run with the bundled pypdf runtime. No OCR or downloads."""
import hashlib, json, time
from pathlib import Path
import pypdf
import pdfplumber

ROOT=Path('runs/source-formats-v1')
rows=[]
for path in sorted((ROOT/'downloads').glob('*.pdf')):
 reader=pypdf.PdfReader(path)
 for mode in ['plain','layout']:
  start=time.monotonic()
  pages=[p.extract_text(extraction_mode=mode) or '' for p in reader.pages]
  target=ROOT/'sources'/f'{path.stem}.{"pdf_plain" if mode=="plain" else "pypdf_layout"}.json'
  target.write_text(json.dumps(pages,ensure_ascii=False,indent=2)+'\n')
  rows.append({'source':str(path),'sha256':hashlib.sha256(path.read_bytes()).hexdigest(),'mode':mode,'pypdf_version':pypdf.__version__,'page_count':len(pages),'words':sum(len(t.split()) for t in pages),'extraction_seconds':time.monotonic()-start,'output':str(target)})
(ROOT/'pdf-extraction.json').write_text(json.dumps(rows,indent=2)+'\n')
plumber=[]
for path in sorted((ROOT/'downloads').glob('*.pdf')):
 start=time.monotonic()
 with pdfplumber.open(path) as doc:
  pages=[p.extract_text(layout=True,x_tolerance=1,x_density=4,y_density=10) or '' for p in doc.pages]
 target=ROOT/'sources'/f'{path.stem}.pdf_layout.json'
 target.write_text(json.dumps(pages,ensure_ascii=False,indent=2)+'\n')
 plumber.append({'pmcid':path.stem.split('.')[0],'engine':'pdfplumber','version':pdfplumber.__version__,
                 'mode':'layout=True,x_tolerance=1,x_density=4,y_density=10','seconds':time.monotonic()-start,'pages':len(pages)})
(ROOT/'pdfplumber-extraction.json').write_text(json.dumps(plumber,indent=2)+'\n')
for r in rows+plumber: print(r)
