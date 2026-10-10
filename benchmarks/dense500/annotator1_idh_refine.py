import json,copy
from pathlib import Path
ROOT=Path('benchmarks/dense500');ID='PMC12880940.1';path=ROOT/'drafts'/f'{ID}.json';a=json.loads(path.read_text());s=json.load(open(ROOT/'sources'/f'{ID}.json'));seg={x['segment_id']:x['text'] for x in s['segments']}
def ev(sid,q):
 assert q in seg[sid],(sid,q)
 return {'segment_id':sid,'quote':q}
def add(p,fields,task,evidence,event):
 f={'fact_id':f"{p['patient_id']}-f{len(p['facts'])+1:03d}",'task':task,'collection':'items','fields':{'subject':'index_patient','assertion':'present',**fields},'clinical_statement':fields.get('name',fields.get('text_value','')),'evidence':copy.deepcopy(evidence),'certainty':'supported','scoring':'include','notes':'Cross-schema representation of the same source-documented event; not an additional independent acquisition.' if task=='procedures_devices' else ''}
 p['facts'].append(f);e=next(e for e in p['events'] if e['event_id'].endswith(event));e['fact_ids'].append(f['fact_id']);return f
for ix,p in enumerate(a['patients']):
 f={x['fact_id']:x for x in p['facts']};pid=p['patient_id'];sid='b00004' if ix==0 else 'b00005';c=p['facts'][0];c['fields'].update(multiple_index_patients=True,subject_state='living_at_presentation',chief_complaint='diarrhea for26 days and fever during last7 days' if ix==0 else 'chest pain for10 days and sudden loss of consciousness for4 h');c['evidence']=[ev(sid,seg[sid].split('Physical examination')[0].strip())]
 for fact in p['facts']:
  fields=fact['fields'];t=fields.get('time')
  if t and 'later' in (t['text'] or ''):t['relation']='after'
  if fields.get('name')=='MRI diagnostic confirmation':fields['kind']='imaging';fields['method']='cardiac MRI';fields['body_site']='heart'
 if ix==0:
  for n,site,severity in [(10,'bilateral lower limbs','moderate'),(11,'bilateral pulmonary bases',None),(12,'periumbilical region','mild'),(19,'lips',None)]:
   f[f'{pid}-f{n:03d}']['fields']['body_site']=site
   if severity:f[f'{pid}-f{n:03d}']['fields']['severity']=severity
  for n in [13,14,15]:f[f'{pid}-f{n:03d}']['fields']['body_site']='fifth intercostal space, lateral to left midclavicular line';f[f'{pid}-f{n:03d}']['evidence']=[ev(sid,'The apical impulse was palpable at the fifth intercostal space, 1.0 cm lateral to the left midclavicular line, spanning approximately 3.0 cm.')]
  f['p1-f018']['fields'].update(body_site='skin and sclerae')
  f['p1-f020']['fields']['diagnostic_basis']='potassium2.73 mmol/L'
  for n in [23,24]:f[f'p1-f{n:03d}']['fields']['method']='blood culture'
  for n in list(range(25,34))+[41,45]+list(range(48,53)):
   ff=f[f'p1-f{n:03d}'];ff['fields']['body_site']='left ventricle' if n!=25 and n!=41 else 'heart'
   if n in [32,33]:ff['fields']['method']='TEE as documented';ff['evidence']=[ev('b00004','Reexamination of TEE after 12 days revealed a dilated LV (58 mm), an LV ejection fraction of approximately 33 %, LV intramyocardial dissection hematoma hat has expanded compared with the first examination, LV apical pseudoaneurysm, and LV apical thrombosis (Fig. 3).')]
   if n==45:ff['fields']['method']='chest CT'
   if n>=48:ff['fields']['method']='echocardiography'
  for n in [36,37]:f[f'p1-f{n:03d}']['fields']['body_site']='left ventricle'
  for n in [6,42,46,47]:f[f'p1-f{n:03d}']['fields']['body_site']='coronary arteries' if n==6 else 'left ventricle' if n==47 else 'heart'
  # Historical stenting/illnesses are not newly occurring at admission.
  p['events'][0]['fact_ids']=[i for i in p['events'][0]['fact_ids'] if i not in ['p1-f004','p1-f005','p1-f006']]
  for n,name,event in [(25,'Initial electrocardiography','001'),(26,'Initial transthoracic echocardiography','001'),(30,'Repeat echocardiography (TEE as documented)','002'),(41,'Deterioration electrocardiography','004'),(45,'Chest computed tomography','009')]:
   orig=f[f'p1-f{n:03d}'];fields={'name':name,'kind':'procedure','action':'completed','body_site':'heart' if n in [25,41] else 'chest' if n==45 else 'left ventricle','finding':orig['fields'].get('text_value')}
   if orig['fields'].get('time'):fields['time']=copy.deepcopy(orig['fields']['time'])
   evidence=orig['evidence'] if n not in [26] else [ev('b00004','Transthoracic echocardiography (TTE) showed a dilated left ventricle (LV, 53 mm).')]
   add(p,fields,'procedures_devices',evidence,event)
 else:
  for n,site in [(5,'chest'),(11,'extremities'),(25,'heart')]:f[f'p2-f{n:03d}']['fields']['body_site']=site
  for n in [30,31,32,33,34,42,43,44,45,46,47,48,49]:
   ff=f[f'p2-f{n:03d}'];ff['fields']['body_site']='heart' if n==30 else 'left ventricular inferior posterior basal wall' if n==33 else 'pericardium' if n==34 else 'left ventricle';ff['fields']['method']='ECG' if n==30 else 'TEE as documented' if n in [31,32,33,34] else 'echocardiography'
   if n in [31,32,33,34]:ff['evidence']=[ev('b00006','TEE revealed LV systolic function was impaired, with an ejection fraction of approximately 45 %, and intramyocardial dissection hematoma in the basal segment of the inferior posterior wall of the LV, pericardial effusion (Fig. 6).')]
  for n in [35,36]:f[f'p2-f{n:03d}']['fields']['body_site']='myocardium'
  f['p2-f040']['fields']['body_site']='heart'
  for name,val in [('Systolic blood pressure',84),('Diastolic blood pressure',60)]:add(p,{'name':name,'kind':'vital','status':'resulted','numeric_value':val,'unit':'mmHg','flag':'low'},'observations',[ev('b00005','Blood pressure was low (84/60 mmHg)')],'001')
  for n,name in [(30,'Electrocardiography'),(31,'Echocardiography (TEE as documented)')]:
   orig=f[f'p2-f{n:03d}'];add(p,{'name':name,'kind':'procedure','action':'completed','body_site':'heart','finding':orig['fields'].get('text_value')},'procedures_devices',orig['evidence'],'004')
  f['p2-f022']['fields']['body_site']='limbs'
  f['p2-f021']['fields']['body_site']='cornea'
for i,v in enumerate(a['article_limitations']):
 if 'Figure pixels not inspected' in v:a['article_limitations'][i]='All6 actual figures inspected into separate visual submission. Body callouts Fig5/6 are reversed relative to assets/captions; no corrected clinical modality inferred from source inconsistency.'
path.write_text(json.dumps(a,ensure_ascii=False,indent=2)+'\n')
print([(p['patient_id'],len(p['facts']),len(p['events'])) for p in a['patients']])
