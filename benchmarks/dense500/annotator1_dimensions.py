"""Source-grounded extent fields for own recent drafts; do not infer anatomical axes."""
import json,copy
from pathlib import Path
from benchmarks.dense500.validate import validate
ROOT=Path('benchmarks/dense500')
ASSIGNED={
 'PMC12831519.1':[('p2','b00051','10 cm × 7.8 cm × 12 cm',[10,7.8,12],'cm')],
 'PMC12852039.1':[('p1','b00015','80 × 45 mm',[80,45],'mm'),('p1','b00017','40 × 37 mm',[40,37],'mm')],
 'PMC12817612.1':[('p1','b00005','10 x 7 x 12 cm',[10,7,12],'cm')],
 'PMC12821429.1':[('p1','b00013','3.3 × 3 × 2 cm',[3.3,3,2],'cm'),('p2','b00019','7 × 5 cm',[7,5],'cm'),('p2','b00024','2.5 × 7 cm',[2.5,7],'cm')],
}
for aid,rows in ASSIGNED.items():
 path=ROOT/'drafts'/f'{aid}.json';a=json.loads(path.read_text());s=json.loads((ROOT/'sources'/f'{aid}.json').read_text())
 for pid,sid,expression,values,unit in rows:
  p=next(p for p in a['patients'] if p['patient_id']==pid)
  base=next(f for f in p['facts'] if f['task']=='observations' and any(e['segment_id']==sid and expression in e['quote'] for e in f['evidence']) and 'extent ' not in f['fields']['name'])
  added=[]
  for j,val in enumerate(values,1):
   name=base['fields']['name']+f' extent {j}'
   existing=next((f for f in p['facts'] if f['fields'].get('name')==name),None)
   if existing:added.append(existing['fact_id']);continue
   f=copy.deepcopy(base);f['fact_id']=f'{pid}-f{max(int(x["fact_id"].split("-f")[1]) for x in p["facts"])+1:03d}'
   f['fields']={k:v for k,v in base['fields'].items() if k in ['subject','assertion','temporality','time','name','kind','status','method','body_site','specimen']}
   f['fields'].update(name=name,numeric_value=float(val),unit=unit,text_value=expression)
   f['clinical_statement']=f'{base["fields"]["name"]}, source extent {j}: {val} {unit}; full source expression {expression}.'
   f['notes']='Each explicitly printed magnitude retained separately in source order. Extent numbering has no anatomical axis meaning; no length/width/depth, volume or interval change inferred. Full expression and stage-specific original evidence retained.'
   p['facts'].append(f);added.append(f['fact_id'])
  for event in p['events']:
   if base['fact_id'] in event['fact_ids']:event['fact_ids']+= [x for x in added if x not in event['fact_ids']]
  for summary in p['summary']:
   if base['fact_id'] in summary['fact_ids']:summary['fact_ids']+=[x for x in added if x not in summary['fact_ids']]
  p['domain_coverage']['observations']['note']=str(sum(f['task']=='observations' for f in p['facts']))+' source-grounded facts retained; unlabelled dimensions split into extent magnitudes with full original expression.'
 errors=validate(a,s);assert not errors,errors
 if 'Split explicit multidimensional measurements into individual magnitudes/units, retaining complete expressions and unlabelled source order' not in a['annotation_provenance']['passes']:a['annotation_provenance']['passes'].append('Split explicit multidimensional measurements into individual magnitudes/units, retaining complete expressions and unlabelled source order')
 path.write_text(json.dumps(a,ensure_ascii=False,indent=2)+'\n')
 print(aid,[(p['patient_id'],len(p['facts'])) for p in a['patients']])
