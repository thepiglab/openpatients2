"""Compact read-only view of all populated runtime fields and structural decisions."""
import argparse,json
from pathlib import Path
p=argparse.ArgumentParser();p.add_argument('path',type=Path);p.add_argument('--patient');args=p.parse_args();t=json.loads(args.path.read_text())
def compact(x):
 if isinstance(x,dict):return {k:compact(v) for k,v in x.items() if k not in ['evidence','attribution_evidence'] and v is not None and v!=[]}
 if isinstance(x,list):return [compact(v) for v in x]
 return x
print('ARTICLE',t['article_id']);print('ROSTER count/completeness',t['roster']['reported_individual_count'],t['roster']['roster_complete'])
print('View omits only null/empty fields and evidence quoted elsewhere. Unknown enums remain visible.')
for p in t['patients']:
 if args.patient and p['source']['patient_target']['patient_id']!=args.patient:continue
 print('PATIENT',p['source']['record_id'])
 for task,section in p['sections'].items():
  print('SECTION',task,section['documentation_status'],section['coverage'])
  if task=='case_context':print(json.dumps(compact(section),ensure_ascii=False))
  for col in ['items','tumors','biomarkers','treatments']:
   for i,x in enumerate(section.get(col,[])):print(col,i,json.dumps(compact(x),ensure_ascii=False))
 for e in p['companions']['timeline_v2']['events']:print('EVENT',json.dumps(compact(e),ensure_ascii=False))
 for e in p['companions']['timeline_v2']['edges']:print('EDGE',json.dumps(compact(e),ensure_ascii=False))
 for c in p['companions']['summary']['claims']:print('SUMMARY',c['text'])
 print('LIMITS',p['companions']['summary']['limitations'])
for f in t['figure_attribution']:print('FIGURE',json.dumps(compact(f),ensure_ascii=False))
