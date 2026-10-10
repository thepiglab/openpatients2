"""Read-only compact review view; does not approve or modify annotations."""
import argparse,json
from pathlib import Path
p=argparse.ArgumentParser();p.add_argument('path',type=Path);p.add_argument('--patient');args=p.parse_args()
a=json.loads(args.path.read_text())
def without_quotes(x):
 if isinstance(x,list):return [without_quotes(y) for y in x]
 if isinstance(x,dict):return {k:([e['segment_id'] for e in v] if k in ('evidence','caption_evidence','identity_evidence') else without_quotes(v)) for k,v in x.items()}
 return x
print('ARTICLE',a['article_id'],'DISPOSITION',a['disposition'])
print('Display hides only index_patient subject, present assertion and empty/null field values; original JSON remains authoritative.')
for p in a['patients']:
 if args.patient and p['patient_id']!=args.patient:continue
 print('PATIENT',p['patient_id'],p['label'],p['species'],p.get('species_as_documented'))
 for f in p['facts']:
  fields={k:v for k,v in f['fields'].items() if v is not None and v!='' and not(k=='subject' and v=='index_patient') and not(k=='assertion' and v=='present')}
  print(f['fact_id'],f['task'],f['collection'],json.dumps(fields,ensure_ascii=False),f['certainty'],f['scoring'],[e['segment_id'] for e in f['evidence']])
 for key in ['events','temporal_relations','summary','domain_coverage','ambiguities']:
  print(key,json.dumps(without_quotes(p[key]),ensure_ascii=False))
print('figures',json.dumps(without_quotes(a['figures']),ensure_ascii=False))
print('article_limitations',a['article_limitations'])
