"""Dense EG source-to-full-field proposal; parent canonical read only."""
import json,copy
from pathlib import Path
r=Path('benchmarks/dense50');n='PMC12802722.1';a=json.loads((r/'annotations'/f'{n}.json').read_text());s=json.loads((r/'sources'/f'{n}.json').read_text());seg={x['segment_id']:x['text'] for x in s['segments']}
for p,sid in zip(a['patients'],['b00004','b00048','b00049']):
 pid=p['patient_id'];one=pid=='p1';two=pid=='p2';by={f['fact_id']:f for f in p['facts']};events={e['event_id']:e for e in p['events']}
 def add(task,q,fields,eid,statement,sid2=None,notes=''):
  sid2=sid2 or sid;assert q in seg[sid2];fid=f'{pid}-f{len(p["facts"])+1:03d}';row=dict(fact_id=fid,task=task,collection='items',fields=dict({'subject':'index_patient','assertion':'present'},**fields),evidence=[dict(segment_id=sid2,quote=q)],clinical_statement=statement,certainty='supported',scoring='include',notes=notes);p['facts'].append(row)
  if eid:events[eid]['fact_ids'].append(fid)
  return row
 def evidence(row,sid2,q):
  assert q in seg[sid2];v=dict(segment_id=sid2,quote=q)
  if v not in row['evidence']:row['evidence'].append(v)
 context=p['facts'][0];context['fields'].update(chief_complaint='sudden slurred speech, nystagmus and gait disturbance' if one else 'sudden vertigo, unsteadiness and slurred speech' if two else 'brief loss of consciousness followed by confusion, unintelligible speech and agitation',multiple_index_patients=True,subject_state='living_at_presentation');evidence(context,sid,seg[sid])
 # Preserve documented qualitative severity/site, actual test method and explicit specimen; no laboratory range inference.
 for f in p['facts']:
  d=f['fields'];name=d.get('name','')
  if f['task']=='symptoms_function':
   if name.startswith(('Severe ','Extreme ')):d['severity']='severe' if name.startswith('Severe ') else 'extreme'
   if name.startswith('Mild '):d['severity']='mild'
   if name.startswith('Moderate-severe '):d.update(severity='moderate-severe',body_site='left side')
   if name=='Severe truncal and right limb ataxia':d['body_site']='trunk and right-sided limbs'
   if name=='Mild four-extremity ataxia':d['body_site']='all four extremities'
   if name=='Severe truncal ataxia and inability to walk':d['body_site']='trunk'
   if name=='Bilateral Babinski signs':d['body_site']='bilateral';d['text_value']='bilateral Babinski signs'
  if f['task']=='conditions' and name=='prior right-hemispheric ischemic stroke':d['body_site']='right cerebral hemisphere'
  if f['task']=='conditions' and name=='Pneumonia':d['body_site']='lungs'
  if f['task']=='observations':
   if d.get('method')=='ABG':d['method']='arterial blood gas analysis';d['specimen']='arterial blood'
   if name in ['Admission NCCT','Control NCCT','NCCT']:d.update(method='non-contrast cerebral CT',body_site='brain')
   if name=='Repeat cranial CT':d.update(method='cranial CT',body_site='brain')
   if name=='Admission CTA':d.update(method='CT angiography',body_site='left vertebral artery and major intracranial arteries')
   if name=='CTA':d.update(method='CT angiography',body_site='intracranial vessels')
   if name in ['Initial NCCT and CTA','Repeat NCCT and CTA']:d.update(method='non-contrast cerebral CT and CT angiography',body_site='brain and intracranial vessels')
   if name=='Chest X-ray':d.update(method='chest X-ray',body_site='chest')
   if name=='Urine calcium oxalate crystals':d['method']='urine sediment examination' if one else 'urine analysis';d['specimen']='urine sediment' if one else 'urine'
   if name=='ABG acid-base parameters':d.update(method='arterial blood gas analysis',specimen='arterial blood');evidence(f,'b00005','Arterial blood gas (ABG) parameters of the three cases on admission.')
   if name=='Ethanol toxicological screening':d['method']='toxicological examination'
   if name=='EG' and not d.get('time'):d.update(method='toxicological screening',specimen='serum');evidence(f,sid,seg[sid])
   if name=='Control ethylene glycol toxicology':d['method']='control toxicology test'
  if f['task']=='procedures_devices':
   if name in ['endotracheal intubation','intubation']:d.update(body_site='airway',indication='increasing agitation requiring sedation' if one else 'loss of consciousness with coma')
   if name=='mechanical ventilation':d['indication']='required after sedation/intubation' if one else 'loss of consciousness with coma'
   if name=='sedation':d['indication']='increasing agitation'
   if name=='hemodialysis' and pid=='p3':d['indication']='not indicated because renal function did not worsen'
  if f['task']=='medications':
   if name=='10% ethanol':d['indication']='suspected methanol or ethylene glycol poisoning';evidence(f,sid,'Based on the metabolic acidosis, we suspected methanol or ethylene glycol poisoning, and an infusion of 10 g of 10% ethanol was started immediately.')
   if name=='thrombolysis':d.update(name='rt-PA thrombolysis',indication='presumptive acute stroke');evidence(f,'b00055','There were no exclusion criteria in any of our cases, and since our presumptive diagnosis was acute stroke and the patients had disabling symptoms, thrombolysis was performed.');evidence(f,'b00055','none of our patients suffered intracranial hemorrhage after thrombolysis, nor did they have any other disadvantages from the administration of rt-PA.')
   if name=='rt-PA':d['indication']='presumptive acute stroke';evidence(f,'b00055','our presumptive diagnosis was acute stroke')
   if name=='ethanol' and d.get('action')!='stopped':d['indication']='ethylene glycol intoxication';evidence(f,sid,seg[sid])
   if name=='sodium bicarbonate' and two:d['indication']='metabolic acidosis';evidence(f,sid,'Despite several fluid boluses and sodium bicarbonate administration, the acidosis and lactate levels did not improve')
   if name=='saline':d['regimen']='slow correction with saline infusions';evidence(f,sid,'Hyponatremia was considered to be a consequence of chronic alcoholism and was corrected slowly with saline infusions.')
  t=d.get('time')
  if t and t['text'] and any(x in t['text'] for x in ['after','before']):t['relation']='after' if 'after' in t['text'] else 'before'
 def proc(name,q,eid,site=None,indication=None,finding=None):
  fields=dict(name=name,kind='procedure',action='completed')
  if site:fields['body_site']=site
  if indication:fields['indication']=indication
  if finding:fields['finding']=finding
  return add('procedures_devices',q,fields,eid,'Actual '+name+'.',notes='Same performed examination/intervention as existing observation or medication; not a separate repeat test. Unreported doses and assay technique remain unknown.')
 initial=pid+('-e004' if one else '-e003');tox=pid+('-e007' if one else '-e006');abg=pid+('-e006' if one else '-e005' if two else '-e006')
 if one:
  proc('Admission non-contrast cerebral CT','Non-contrast cerebral computed tomography (NCCT) imaging showed no signs of bleeding or acute ischemic lesion.',initial,'brain',finding='no bleeding or acute ischemic lesion')
  proc('Admission CT angiography','CT angiography (CTA) imaging showed a hypoplastic vertebral artery on the left side, but no occlusion was observed on major intracranial arteries.',initial,'left vertebral artery and intracranial arteries',finding='hypoplastic left vertebral artery; no major intracranial occlusion')
  proc('Repeat cranial CT','Due to the disturbance of consciousness, cranial CT imaging was repeated to exclude intracranial hemorrhage, but it was negative again.','p1-e006','brain','exclude intracranial hemorrhage','negative for hemorrhage')
  proc('Control cerebral NCCT','Control NCCT imaging 24 h after rt-PA treatment showed no abnormalities.','p1-e012','brain',finding='no abnormalities')
  proc('Chest X-ray','The patient became febrile, and a chest X-ray showed pneumonia; therefore, intravenous antibiotics were started.','p1-e011','chest',finding='pneumonia')
  q='Arterial blood gas (ABG) analysis disclosed severe metabolic acidosis';proc('Arterial blood gas analysis',q,abg,finding='severe metabolic acidosis')
  proc('Serum ethylene-glycol toxicological screening','Within the next hours, a toxicological screening revealed an ethylene glycol serum concentration of 95.85 mg/dL',tox,finding='ethylene glycol detected')
  proc('Control toxicology testing','The control toxicology test on the 2nd day no longer confirmed the presence of ethylene glycol in the blood','p1-e013',finding='ethylene glycol not present')
  for name,value in [('Narrative renal-course GFR starting value',90),('Narrative renal-course GFR later value',45)]:
   add('observations','Within 48 h, his renal function deteriorated [from a glomerular filtration rate (GFR) of 90 mL/min/1.73 m2 to 45 mL/min/1.73 m2 GFR]',dict(name=name,kind='laboratory',status='resulted',numeric_value=value,unit='mL/min/1.73 m2'),'p1-e009',name+' was '+str(value)+' mL/min/1.73 m2 in the stated deterioration course.',notes='Two numeric endpoints of one narrative trend, not invented separate dated encounters. Baseline sampling time is unreported. Table uses mL/min and baseline >90; no unit conversion or reconciliation.')
 else:
  if two:
   proc('Initial cerebral NCCT and CTA','NCCT and CTA imaging was negative.',initial,'brain and intracranial vessels',finding='negative')
   proc('Repeat cerebral NCCT and CTA','NCCT and CTA imaging was repeated, but no signs of bleeding or large vessel occlusion were found.','p2-e005','brain and intracranial vessels',finding='no bleeding or large vessel occlusion')
   proc('Arterial blood gas analysis','ABG analysis revealed extremely severe metabolic acidosis',abg,finding='extremely severe metabolic acidosis')
   proc('Serum ethylene-glycol toxicological screening','An urgent toxicological screening proved ethylene glycol intoxication with a serum ethylene glycol level of 159 mg/dL.',tox,finding='ethylene glycol detected')
   add('conditions','At that time, he was comatose and tetraplegic and showed bilateral Babinski signs, and his oculocephalic and pupillary light reflexes were absent, resembling an acute basilar artery occlusion or brainstem hemorrhage.',dict(name='Acute basilar artery occlusion or brainstem hemorrhage',assertion='possible',verification_status='differential',role='differential',body_site='basilar artery or brainstem'),'p2-e005','The deteriorated examination resembled basilar occlusion or brainstem hemorrhage; repeat imaging was negative.',notes='Source-described resemblance/differential, not a confirmed additional diagnosis.')
  else:
   proc('Cerebral NCCT','NCCT imaging showed chronic infarction in the right internal capsule, but neither hemorrhage nor hyperacute ischemic lesion was described.',initial,'brain',finding='chronic right internal capsule infarction; no hemorrhage or hyperacute ischemic lesion')
   proc('Intracranial CT angiography','CTA imaging showed no evidence of arterial occlusion or stenosis in the intracranial vessels.',initial,'intracranial vessels',finding='no arterial occlusion or stenosis')
   proc('Arterial blood gas analysis','An ABG analysis was obtained, but it showed physiological acid–base parameters',abg,finding='physiological acid-base parameters')
   proc('Blood toxicological examination','blood was sent for toxicological examination.',tox,finding='ethanol negative; ethylene glycol detected')
   evidence(p['facts'][-1],sid,'Although the toxicological screening was negative for ethanol, it revealed ethylene glycol intoxication, with an ethylene-glycol serum level of 16.54 mg/dL (6:35 p.m.).')
   add('social_exposures','Recent alcohol consumption was not reported.',dict(domain='alcohol',name='recent alcohol consumption, not reported',assertion='unknown',status='unknown'),'p3-e002','Recent alcohol consumption was not reported; this does not prove absence.')
  add('conditions','our presumptive diagnosis was acute stroke',dict(name='Acute stroke suspected at thrombolysis decision',assertion='possible',verification_status='provisional',role='primary_diagnosis'),initial,'Acute stroke was the presumptive diagnosis before ethylene glycol confirmation.','b00055',notes='The original presumptive diagnosis remains separate from confirmed poisoning and, forCase3, historical stroke.')
 if pid!='p3':add('conditions','Later, both patients had evidence of nephrotoxicity, which is also a characteristic feature of ethylene glycol intoxication, demonstrated by elevated creatinine levels and decreased GFR values.',dict(name='Renal impairment/nephrotoxicity',verification_status='confirmed',body_site='kidneys',role='complication',diagnostic_basis='elevated creatinine and decreased GFR'),pid+('-e009' if one else '-e012'),'Authors explicitly document nephrotoxicity in Cases1and2.','b00056',notes='Shared source statement explicitly applies to first two patients; not assigned to Case3 with stable renal function.')
 add('observations','There were no exclusion criteria in any of our cases',dict(name='Thrombolysis eligibility assessment',kind='other',status='resulted',text_value='no exclusion criteria'),initial,'Authors state that all three cases had no exclusion criteria for IVT.','b00055')
 # Connect context, symptom/exam details and all new representations to supported phases.
 for e in p['events']:
  if e['event_id']==initial and pid+'-f001' not in e['fact_ids']:e['fact_ids'].append(pid+'-f001')
  e['evidence']=list({(v['segment_id'],v['quote']):v for f in p['facts'] if f['fact_id'] in e['fact_ids'] for v in f['evidence']}.values())
 for d in p['domain_coverage']:
  count=sum(f['task']==d for f in p['facts']);p['domain_coverage'][d]['note']=f'{count} source-grounded representations after full-field audit; cross-schema representations share evidence.' if count else 'No patient-specific documentation; no negative inferred.'
a['article_limitations'].append('Actual timeline pixels inspected separately. Figure-only exact times, Case2sepsis/day2–9label and Case3DNT-versus-bodyONT terminology retained in visual reference; not forced into literal clinical source segments.')
a['annotation_provenance']['passes'].append('Full source-to-field omission pass across65units: chief complaints, setting evidence, qualitative severity/site, diagnostic tests and ABG specimen/method, poisoning therapy indications, explicitly unknown recentalcohol report, source-described stroke differential and actual nephrotoxicity, narrative indexed GFR endpoints. Parent canonical280facts retained. No invented acute stroke, suicide intent inCases2/3, unreported assay methods or table sampling chronology.')
(r/'annotator-1-proposals'/f'{n}.json').write_text(json.dumps(a,ensure_ascii=False,indent=2)+'\n');print([(p['patient_id'],len(p['facts']),len(p['events'])) for p in a['patients']])
