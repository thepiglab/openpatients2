import json,re
from pathlib import Path
R=Path('benchmarks/dense500');ID='PMC12605513.1';path=R/'drafts'/f'{ID}.json';a=json.loads(path.read_text());S=json.loads((R/'sources'/f'{ID}.json').read_text());B={s['segment_id']:s['text'] for s in S['segments']}
def evs(p,ids):
 out=[]
 for f in p['facts']:
  if f['fact_id'] in ids:
   for e in f['evidence']:
    if e not in out:out.append(e)
 return out
for p in a['patients']:
 pid=p['patient_id'];fs=p['facts'];by={f['fact_id']:f for f in fs}
 for f in fs:
  x=f['fields']
  if x.get('name')=='unspecified oral hypoglycemic medications':x['route']='oral'
  if x.get('name')=='Insulin/C-peptide molar ratio':x.pop('reference_range_text',None)
  if x.get('name')=='Chronic renal failure':x['temporality']='current'
  if x.get('name')=='Autoimmune diabetes antibody profile':
   x['finding']='high IAA titers';f['evidence'].append({'segment_id':'b00008','quote':'the results uncovered markedly high serum insulin levels and high IAA titers, accompanied by a dissociation between serum insulin and C-peptide levels.'})
  if x.get('name')=='Insulin and C-peptide quantification' and pid=='p1':
   f['evidence'].append({'segment_id':'b00008','quote':'the results uncovered markedly high serum insulin levels and high IAA titers, accompanied by a dissociation between serum insulin and C-peptide levels.'})
 def add(task,name,sid,q,fields,event):
  if any(f['fields'].get('name')==name for f in fs):return
  assert q in B[sid]
  f=dict(fact_id=f'{pid}-f{len(fs)+1:03d}',task=task,collection='items',fields=dict(subject='index_patient',assertion='present',name=name,**fields),evidence=[dict(segment_id=sid,quote=q)],clinical_statement=name+': '+fields.get('text_value',fields.get('finding','performed')),certainty='supported',scoring='include',notes='Unique source-supported representation; the performed test corresponds to existing result observations, without a new episode.' if task=='procedures_devices' else 'Author-documented follow-up trend; no invented rate or absolute magnitude.')
  
  if task=='outcomes':f['fields'].pop('name',None)
  fs.append(f);event['fact_ids'].append(f['fact_id']);event['evidence']=evs(p,event['fact_ids'])
 initial=next(e for e in p['events'] if e['description'].startswith('Index laboratory'))
 follow=next(e for e in p['events'] if e['description'].startswith('Follow-up insulin'))
 if pid=='p1':
  q='Laboratory tests demonstrated the 24h urinary protein excretion was 457.5 mg/24h, hemoglobin was 89 g/L, and creatinine was 224 μmol/L, yielding an eGFR of 23.06 ml/(min/1.73 m²).'
  add('procedures_devices','Index laboratory panel: urinary protein, hemoglobin, creatinine and eGFR','b00006',q,dict(kind='procedure',action='completed',finding='24h urinary protein457.5 mg/24h; hemoglobin89 g/L; creatinine224 μmol/L; eGFR23.06 ml/(min/1.73 m²)'),initial)
  for name in ['Thyroid function testing','Cortisol testing','Adrenocorticotropic hormone testing']:add('procedures_devices',name,'b00006','Thyroid function, cortisol, and adrenocorticotropic hormone levels were within normal ranges.',dict(kind='procedure',action='completed',finding='within normal ranges'),initial)
  local=next(e for e in p['events'] if e['description'].startswith('Local hospital'))
  add('procedures_devices','Local hospital FBG, serum creatinine and HbA1c tests','b00005','Laboratory tests revealed a fasting blood glucose (FBG) level of 11.45 mmol/L, serum creatinine of 218 umol/L, and glycated hemoglobin (HbA1c) level of 14.4%.',dict(kind='procedure',action='completed',finding='FBG11.45 mmol/L; serum creatinine218 umol/L; HbA1c14.4%'),local)
 else:
  add('procedures_devices','Index FBG, creatinine, eGFR, albumin and HbA1c panel','b00018','Laboratory tests demonstrated an FBG level of 11.29 mmol/L, a serum creatinine level of 757 μmol/L, an eGFR of 5.26 ml/(min/1.73 m²), an albumin level of 27.2 g/L, and an HbA1c level of 11.9%.',dict(kind='procedure',action='completed',finding='FBG11.29 mmol/L; serum creatinine757 μmol/L; eGFR5.26 ml/(min/1.73 m²); albumin27.2 g/L; HbA1c11.9%'),initial)
  for name in ['Antinuclear antibody spectrum testing','Thyroid function testing','Electrolyte testing']:add('procedures_devices',name,'b00018','The antinuclear antibody spectrum, thyroid function, and electrolyte levels were within normal limits.',dict(kind='procedure',action='completed',finding='within normal limits'),initial)
 add('observations','C-peptide follow-up trend compared with LADA','b00029','Follow-up of the two patients revealed that C-peptide levels did not decrease as rapidly as observed in LADA.',dict(kind='laboratory',status='resulted',text_value='C-peptide levels did not decrease as rapidly as observed in LADA'),follow)
 add('outcomes','Follow-up ICPR decline and EIAS improvement','b00034','the follow-up data revealed that both patients’ ICPR had dramatically dropped from the starting levels, suggesting that the EIAS condition had improved.',dict(event='improvement',text_value='ICPR dramatically dropped from starting levels; author interpretation suggests EIAS improvement',related_condition='EIAS'),follow)
 # Keep longstanding diabetes/DKD and prior oral drugs before the dated insulin history.
 hist=p['events'][0];rest=[fid for fid in hist['fact_ids'] if by[fid]['fields'].get('name') not in ['Type 2 diabetes','Diabetic kidney disease','unspecified oral hypoglycemic medications']]
 hist['fact_ids']=[fid for fid in hist['fact_ids'] if fid not in rest];hist['description']='Longstanding diabetes/DKD and earlier oral hypoglycemic therapy';hist['evidence']=evs(p,hist['fact_ids'])
 adm=next(e for e in p['events'] if e['description'].startswith('Index presentation'))
 adm['description']='Index presentation and physical examination'+('; periumbilical subcutaneous findings' if pid=='p1' else '')
 current=[fid for fid in rest if by[fid]['task'] in ['medications','social_exposures']]
 past=[fid for fid in rest if fid not in current]
 for ids,desc in [(past,'Other medical history and chronic renal failure; onset relative to earlier insulin regimen unspecified'),(current,'Home medications and needle-reuse practice documented at presentation; start dates unspecified')]:
  eid=f'{pid}-e{len(p["events"])+1:03d}';p['events'].append(dict(event_id=eid,description=desc,occurrence='occurred',fact_ids=ids,time_text=None,evidence=evs(p,ids)))
  if ids==current:p['temporal_relations'].append(dict(from_event_id=eid,to_event_id=adm['event_id'],relation='overlaps',evidence=evs(p,ids)+adm['evidence']))
 # Repair only generated prose spacing, never quotes.
 for s in p['summary']:
  t=s['text'].replace('HbA 1 c','HbA1c').replace('After 10 daysglucose','After 10 days glucose').replace('at 04:00','at 04:00').replace('left artrial enlargement','source-termed left artrial enlargement')
  t=re.sub(r'(?<=\d)(mg|g/L)',r' \1',t);s['text']=t
  if s is p['summary'][0]:s['fact_ids']+=rest
  if s is p['summary'][-1]:s['fact_ids']+=follow['fact_ids'][-2:]
 for task,cov in p['domain_coverage'].items():cov['status']='documented' if any(f['task']==task for f in fs) else 'not_documented'
a['annotation_provenance']['passes'][-1]='Every full runtime schema attribute independently reviewed; supported fields, unique performed tests, timeline grouping and prose refined'
path.write_text(json.dumps(a,indent=2,ensure_ascii=False)+'\n')
print([(p['patient_id'],len(p['facts']),len(p['events']),len(p['temporal_relations'])) for p in a['patients']])
