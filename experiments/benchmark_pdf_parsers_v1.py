"""Small paired local parser timing; no network, OCR or model downloads.

Run with bundled Python plus temporary pdf-inspector on PYTHONPATH.
One excluded warm-up, then five rotating parser-order repetitions per document.
"""
import importlib.metadata,json,platform,time
from pathlib import Path
from statistics import median
import pypdf,pdfplumber,pdf_inspector
ROOT=Path('runs/source-formats-v1')
def plain(path):return [p.extract_text(extraction_mode='plain') for p in pypdf.PdfReader(path).pages]
def layout(path):
 with pdfplumber.open(path) as doc:return [p.extract_text(layout=True,x_tolerance=1,x_density=4,y_density=10) for p in doc.pages]
def fire(path):return [p.markdown for p in pdf_inspector.extract_pages_markdown(str(path)).pages]
engines={'pypdf':plain,'pdfplumber':layout,'pdf-inspector':fire};rows=[]
for path in sorted((ROOT/'downloads').glob('*.pdf')):
 samples={k:[] for k in engines};counts={}
 for name,fn in engines.items():fn(path)
 names=list(engines)
 for repeat in range(5):
  order=names[repeat%3:]+names[:repeat%3]
  for name in order:
   start=time.perf_counter();pages=engines[name](path);samples[name].append(time.perf_counter()-start);counts[name]=len(pages)
 for name,values in samples.items():rows.append({'pmcid':path.stem.split('.')[0],'parser':name,'version':importlib.metadata.version(name),'seconds':values,'median_seconds':median(values),'pages':counts[name]})
(ROOT/'parser-timing.json').write_text(json.dumps({'platform':platform.platform(),'machine':platform.machine(),'python':platform.python_version(),'method':__doc__,'measurements':rows},indent=2)+'\n')
for r in rows:print(r['pmcid'],r['parser'],round(r['median_seconds']*1000,2),'ms')
