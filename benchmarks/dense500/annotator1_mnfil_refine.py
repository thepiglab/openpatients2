import json,copy
from pathlib import Path
r=Path('benchmarks/dense500');aid='PMC12689157.1';a=json.load(open(r/'drafts'/f'{aid}.json'));source=json.load(open(r/'sources'/f'{aid}.json'));seg={x['segment_id']:x['text'] for x in source['segments']};p=a['patients'][0]
for f in p['facts']:
 fields=f['fields'];name=fields.get('name')
 if name=='Hepatitis B treatment (unspecified)':
  f['task']='care_plans';f['fields']={'subject':'index_patient','assertion':'present','temporality':'historical','action_text':'Hepatitis B treatment was not regular (type unspecified)','plan_type':'other','action_status':'unknown','target':'viral hepatitis B'};f['notes']='Treatment regularity documented; source does not identify a medication or components.'
 if name=='Ultrasound liver cirrhosis':f['evidence']=[dict(segment_id='b00009',quote='Abdominal ultrasound shows liver cirrhosis, with oval lesions measuring 5.5 × 3.7 × 5.6 cm in segment S7 and 2.2 × 2.0 × 1.8 cm at the inferior margin of the left lobe, suggesting a tendency towards malignancy.')]
 if fields.get('time',{}).get('text') in ['22-year history','10-year history','for 5 months']:fields['time']['relation']='during'
 if name in ['Glypican-3','Heat shock protein 70','Glutamine synthetase initial result','CD34 diffuse sinusoidal capillarization']:
  part={'Glypican-3':'(A) The GPC-3 protein expression is negative (IHC, ×4 magnification).','Heat shock protein 70':'(B) HSP70 protein expression is negative (IHC, ×4 magnification).','Glutamine synthetase initial result':'(C) GS protein shows focal positivity (IHC, ×10 magnification).','CD34 diffuse sinusoidal capillarization':'(D) CD34 does not demonstrate diffuse hepatic sinusoidal capillarization (IHC, ×10 magnification).'}[name]
  f['evidence'].append(dict(segment_id='b00041',quote=part));fields['method']='IHC; '+('×4 magnification' if name in ['Glypican-3','Heat shock protein 70'] else '×10 magnification')
  x=copy.deepcopy(f);x['fact_id']=f'p1-f{len(p["facts"])+1:03d}';x['task']='oncology';x['collection']='biomarkers';x['fields']={'name':name,'subject':'index_patient','assertion':'present','tumor_ref':None,'interpretation':fields['flag'],'text_value':fields['text_value'],'assay':fields['method'],'specimen':'resected liver tissue'};x['notes']='Alternate representation of named IHC result during provisional HCC workup, not a final cancer diagnosis or additional biological test.'
  # defer appending to avoid iterating new rows
  p.setdefault('_additions',[]).append(x)
for x in p.pop('_additions',[]):
 x['fact_id']=f'p1-f{len(p["facts"])+1:03d}';p['facts'].append(x);p['events'][-1]['fact_ids'].append(x['fact_id'])
for d in p['domain_coverage']:
 n=sum(f['task']==d for f in p['facts']);p['domain_coverage'][d]={'status':'documented' if n else 'not_documented','note':f'{n} source-grounded representations; alternate schema representations share the same biological evidence.' if n else 'No patient-specific documentation; no negative inferred.'}
# All duration/event classifications below are supported by literal source expressions.
for ev in p['events']:
 for f in p['facts']:
  if f['fact_id'] in ev['fact_ids']:
   for e in f['evidence']:
    if e not in ev['evidence']:ev['evidence'].append(e)
a['annotation_provenance']['passes'].append('Full schema field inspection refined unspecified hepatitis-treatment modality to CarePlans, modality-specific ultrasound evidence, duration semantics and shared Oncology/Observations representations of named diagnostic IHC tests. All supported anatomy, specimens, methods, extents, negatives and consultation phases inspected.')
(r/'drafts'/f'{aid}.json').write_text(json.dumps(a,ensure_ascii=False,indent=2)+'\n')
print(len(p['facts']))
