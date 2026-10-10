import json,re
from pathlib import Path
BASE=Path('benchmarks/dense50')
DOMAINS=['case_context','demographics','conditions','symptoms_function','medications','allergies','procedures_devices','observations','oncology','family_genetics','reproductive_perinatal','social_exposures','outcomes','care_plans']
class Build:
 def __init__(self,aid):
  self.s=json.loads((BASE/'sources'/f'{aid}.json').read_text()); self.seg={s['segment_id']:s for s in self.s['segments']}; self.a={'schema_version':'dense-article-annotation/1','article_id':aid,'text_sha256':self.s['text_sha256'],'xml_sha256':self.s['xml_sha256'],'disposition':'individual_cases','patients':[],'figures':[],'source_coverage':[],'article_limitations':[],'annotation_provenance':{'annotator_model':'GPT-6.1 Sol','passes':['Complete original source and retained text read; original patient and segment ownership enumeration','Dense patient fact extraction including normal/negative tests, history, treatment changes and outcomes','Patient-bearing narrative, tables and captions reread for completeness, polarity and attribution','Event chronology, quantities, units and figure ownership checked','All exact evidence quotes and internal fact/event references validated locally'],'prior_predictions_consulted':False,'status':'draft'}}
 def patient(self,label,sid,quote):
  p={'patient_id':f'p{len(self.a["patients"])+1}','label':label,'species':'human','species_as_documented':'human patient','identity_evidence':[{'segment_id':sid,'quote':quote}],'facts':[],'events':[],'temporal_relations':[],'summary':[],'domain_coverage':{},'ambiguities':[]};self.a['patients'].append(p);self.p=p;return p
 def fact(self,task,sid,quote,statement=None,time=None,assertion='present',certainty='supported',scoring='include',notes='',**fields):
  assert quote in self.seg[sid]['text'],(sid,quote)
  fid=f'{self.p["patient_id"]}-f{len(self.p["facts"])+1:03d}'
  if task!='case_context':fields={'subject':'index_patient','assertion':assertion,**fields}
  if time:fields['time']={'text':time,'relation':'at','anchor':None}
  f={'fact_id':fid,'task':task,'collection':None if task=='case_context' else 'items','fields':fields,'evidence':[{'segment_id':sid,'quote':quote}],'clinical_statement':statement or quote,'certainty':certainty,'scoring':scoring,'notes':notes};self.p['facts'].append(f);return fid
 def event(self,description,fids,time=None,occurrence='occurred'):
  fs={f['fact_id']:f for f in self.p['facts']};ev=[]
  for fid in fids:
   for e in fs[fid]['evidence']:
    if e not in ev:ev.append(e)
  eid=f'{self.p["patient_id"]}-e{len(self.p["events"])+1:03d}';self.p['events'].append({'event_id':eid,'description':description,'occurrence':occurrence,'fact_ids':fids,'time_text':time,'evidence':ev});return eid
 def finish_patient(self):
  for d in DOMAINS:
   fs=[f for f in self.p['facts'] if f['task']==d];self.p['domain_coverage'][d]={'status':'documented' if fs else 'not_documented','note':f'{len(fs)} source-grounded facts retained.' if fs else 'No patient-specific documentation identified in retained source.'}
  groups=[('Presentation and history',['case_context','demographics','social_exposures','symptoms_function','family_genetics','reproductive_perinatal']),('Diagnostic workup and diagnoses',['conditions','observations','oncology']),('Treatment and care',['medications','procedures_devices','care_plans']),('Clinical course and outcome',['outcomes'])]
  for title,ds in groups:
   fs=[f for f in self.p['facts'] if f['task'] in ds]
   if fs:self.p['summary'].append({'text':title+': '+'; '.join(f['clinical_statement'] for f in fs),'fact_ids':[f['fact_id'] for f in fs]})
 def save(self,ownership):
  for s in self.s['segments']:
   sid=s['segment_id']; ids=ownership.get(sid,[])
   disp='figure_caption' if s['kind']=='figure_caption' else 'patient_facts' if ids else 'background'
   self.a['source_coverage'].append({'segment_id':sid,'disposition':disp,'patient_ids':ids,'note':'Reviewed original segment; patient attribution follows explicit case/column ownership.' if ids else 'Reviewed; background, explanatory definitions or administrative material.'})
  for fig in self.s.get('figures',[]):
   ce=[{'segment_id':sid,'quote':self.seg[sid]['text']} for sid in fig.get('caption_segment_ids',[])]
   self.a['figures'].append({'figure_id':fig['figure_id'],'caption_evidence':ce,'patient_ids':ownership.get(fig.get('caption_segment_ids',[None])[0],[]),'scope':'shared' if len(ownership.get(fig.get('caption_segment_ids',[None])[0],[]))>1 else 'individual','panels':[{'panel':None,'patient_ids':ownership.get(fig.get('caption_segment_ids',[None])[0],[]),'scope':'shared' if len(ownership.get(fig.get('caption_segment_ids',[None])[0],[]))>1 else 'individual','description_from_caption':fig['caption'],'modality':None,'body_part':None,'evidence':ce}],'caption_summary':fig['caption'],'visual_status':'not_inspected','visual_description':None,'attribution_notes':'Caption ownership only; no pixel or unstated panel inference.'})
  if self.a['figures']:self.a['article_limitations'].append('Figure pixels were not inspected; annotation covers captions only.')
  for p in self.a['patients']:
   for f in p['facts']:
    for e in f['evidence']:assert e['quote'] in self.seg[e['segment_id']]['text']
   for e in p['identity_evidence']:assert e['quote'] in self.seg[e['segment_id']]['text']
  (BASE/'drafts').mkdir(exist_ok=True)
  (BASE/'drafts'/f'{self.a["article_id"]}.json').write_text(json.dumps(self.a,ensure_ascii=False,indent=2)+'\n')
  print(self.a['article_id'],[(p['patient_id'],len(p['facts'])) for p in self.a['patients']])
def sym(b,s,q,n,time=None,**kw):return b.fact('symptoms_function',s,q,n,time=time,name=n,kind='sign',**kw)
def obs(b,s,q,n,time=None,**kw):return b.fact('observations',s,q,n+': '+kw.get('text_value',q),time=time,name=n,kind=kw.pop('kind','laboratory'),status='resulted',**kw)
def cond(b,s,q,n,time=None,**kw):return b.fact('conditions',s,q,n,time=time,name=n,verification_status=kw.pop('verification_status','confirmed'),**kw)
def med(b,s,q,n,time=None,**kw):return b.fact('medications',s,q,n+': '+q,time=time,name=n,action=kw.pop('action','administered'),**kw)
def proc(b,s,q,n,time=None,**kw):return b.fact('procedures_devices',s,q,n+': '+q,time=time,name=n,kind=kw.pop('kind','procedure'),action=kw.pop('action','completed'),**kw)
def out(b,s,q,event,time=None,**kw):return b.fact('outcomes',s,q,time=time,event=event,text_value=q,**kw)
def article_12802722():
 b=Build('PMC12802722.1'); owns={}
 for i,sid,age in [(1,'b00004',54),(2,'b00048',78),(3,'b00049',54)]:
  b.patient(f'Case {i}',sid,f'A {age}-year-old man');owns[sid]=[f'p{i}'];
  b.fact('case_context',sid,f'A {age}-year-old man',f'Case {i}: human clinical case',case_kind='clinical_case',species='human',index_subject_description=f'{age}-year-old man',care_setting='hospital')
  b.fact('demographics',sid,f'A {age}-year-old man',f'Age {age} years',attribute='age_at_presentation',numeric_value=age,unit='years')
  b.fact('demographics',sid,f'A {age}-year-old man','Documented man',attribute='documented_sex',text_value='man')
  start=len(b.p['facts'])
  if i==1:
   b.fact('social_exposures',sid,'a current smoker','Current smoker',domain='tobacco',name='smoking',status='current')
   cond(b,sid,'no major illnesses in his medical history','major illnesses in medical history',assertion='absent',role='history')
   sym(b,sid,'a sudden onset of slurred speech, nystagmus, and gait disturbance that started in the morning (9:30 a.m.)','Sudden slurred speech with nystagmus and gait disturbance',time='9:30 a.m.')
   for q,n in [('severe dysarthria','Severe dysarthria'),('horizontal and vertical gaze-directed nystagmus','Horizontal and vertical gaze-directed nystagmus'),('severe truncal and right-sided limb ataxia','Severe truncal and right limb ataxia'),('fully alert','Fully alert')]:sym(b,sid,q,n,time='On admission')
   sym(b,sid,'afebrile','Fever absent at admission',time='On admission',assertion='absent',name='fever') if False else b.fact('symptoms_function',sid,'afebrile','Afebrile on admission',time='On admission',assertion='absent',name='fever',kind='sign')
   obs(b,sid,'National Institutes of Health Stroke Scale (NIHSS): 4 points','NIHSS',time='On admission',kind='other',numeric_value=4,unit='points')
   obs(b,sid,'moderately hypertensive (165/90 mmHg)','Blood pressure',time='On admission',kind='vital',text_value='165/90',unit='mmHg',flag='high')
   obs(b,sid,'slightly tachycardic (103/min, sinus rhythm)','Heart rate',time='On admission',kind='vital',numeric_value=103,unit='/min',flag='high',text_value='sinus rhythm')
   obs(b,sid,'hemoglobin saturation of 98% on room air','Oxygen saturation',time='On admission',kind='vital',numeric_value=98,unit='%',method='Pulse oximetry',text_value='on room air')
   obs(b,sid,'Non-contrast cerebral computed tomography (NCCT) imaging showed no signs of bleeding or acute ischemic lesion.','Admission NCCT',time='On admission',kind='imaging',text_value='No bleeding or acute ischemic lesion',flag='negative')
   obs(b,sid,'CT angiography (CTA) imaging showed a hypoplastic vertebral artery on the left side, but no occlusion was observed on major intracranial arteries.','Admission CTA',time='On admission',kind='imaging',text_value='Hypoplastic left vertebral artery; no major intracranial artery occlusion')
   obs(b,sid,'His laboratory values showed no significant changes in routine laboratory parameters.','Routine laboratory screening',time='On admission',text_value='No significant changes',flag='normal')
   cond(b,sid,'a vertebrobasilar territory stroke was suspected','Vertebrobasilar territory stroke',verification_status='provisional',assertion='possible')
   med(b,sid,'IVT was started (1:40 p.m., door-to-needle time: 20 min, onset-to-needle-time: 250 min). A total dose of 73 mg of alteplase was administered.','alteplase',time='1:40 p.m.',dose_value=73,dose_unit='mg',route='intravenous',dose_text='A total dose of 73 mg',indication='suspected vertebrobasilar territory stroke')
   out(b,sid,"The patient's neurological status did not change during thrombolysis.",'other',time='during thrombolysis')
   for n in ['somnolence','agitation','hyperventilation']:sym(b,sid,'late in the afternoon (5 p.m.), somnolence, agitation, and hyperventilation were noticed.',n,time='5 p.m.')
   obs(b,sid,'cranial CT imaging was repeated to exclude intracranial hemorrhage, but it was negative again.','Repeat cranial CT',kind='imaging',text_value='Negative for intracranial hemorrhage',flag='negative')
   cond(b,sid,'Arterial blood gas (ABG) analysis disclosed severe metabolic acidosis','Severe metabolic acidosis',severity='severe')
   for n in ['sedation','endotracheal intubation','mechanical ventilation']:proc(b,sid,"The patient's agitation increased, requiring sedation, endotracheal intubation, and mechanical ventilation.",n)
   med(b,sid,'Intravenous sodium bicarbonate was administered to correct the acidosis.','sodium bicarbonate',route='intravenous',indication='acidosis')
   sym(b,sid,'the patient had appeared depressed in the past few weeks','Depressed mood reported by wife',time='in the past few weeks',kind='mental_status') if False else b.fact('symptoms_function',sid,'the patient had appeared depressed in the past few weeks','Wife reported depressed mood in past few weeks',name='depressed mood',kind='mental_status',time='in the past few weeks')
   sym(b,sid,'had mentioned suicidal thoughts several times','Suicidal thoughts reported by wife',time='in the past few weeks')
   cond(b,sid,'we suspected methanol or ethylene glycol poisoning','Methanol or ethylene glycol poisoning',verification_status='differential',assertion='possible',role='differential')
   med(b,sid,'an infusion of 10 g of 10% ethanol was started immediately.','10% ethanol',action='started',dose_value=10,dose_unit='g',dose_text='10 g of 10% ethanol',route='infusion')
   cond(b,sid,'a toxicological screening revealed an ethylene glycol serum concentration of 95.85 mg/dL','Ethylene glycol intoxication',diagnostic_basis='toxicological screening')
   proc(b,sid,'urgent hemodialysis was initiated.','Hemodialysis',indication='ethylene glycol intoxication')
   out(b,sid,'the patient was transferred to the intensive care unit of the internal medicine department','other',disposition='intensive care unit of internal medicine')
   out(b,sid,'Within 48 h, his renal function deteriorated','deterioration',time='Within 48 h',related_condition='renal dysfunction')
   obs(b,sid,'the examination of urine sediment was positive for calcium oxalate crystals.','Urine calcium oxalate crystals',specimen='urine sediment',text_value='calcium oxalate crystals',flag='positive')
   sym(b,sid,'The patient became febrile','Fever later in hospital course')
   obs(b,sid,'a chest X-ray showed pneumonia','Chest X-ray',kind='imaging',text_value='pneumonia')
   cond(b,sid,'a chest X-ray showed pneumonia','Pneumonia',role='complication')
   med(b,sid,'intravenous antibiotics were started.','antibiotics',action='started',route='intravenous',indication='pneumonia')
   out(b,sid,'His condition improved significantly in the following days.','improvement',time='in the following days')
   obs(b,sid,'Control NCCT imaging 24 h after rt-PA treatment showed no abnormalities.','Control NCCT',time='24 h after rt-PA treatment',kind='imaging',text_value='No abnormalities',flag='normal')
   obs(b,sid,'The control toxicology test on the 2nd day no longer confirmed the presence of ethylene glycol in the blood','Control ethylene glycol toxicology',time='on the 2nd day',specimen='blood',text_value='ethylene glycol absent',flag='negative')
   obs(b,sid,'the pH returned to normal','Follow-up pH',text_value='returned to normal',flag='normal')
   proc(b,sid,'hemodialysis could be discontinued.','Hemodialysis discontinued',action='removed')
   proc(b,sid,'Five days after his admission, mechanical ventilation was stopped, and the patient could be extubated.','Mechanical ventilation stopped and extubation',time='Five days after his admission',action='removed')
   b.fact('social_exposures',sid,'he admitted to drinking antifreeze liquid with suicidal intent (~0.5–1 dL) a few hours before his admission.','Antifreeze ingestion with suicidal intent',time='a few hours before his admission',domain='substance',name='antifreeze liquid',amount_text='~0.5–1 dL')
   out(b,sid,'On day 9, he was transferred to the psychiatric ward for further treatment.','other',time='On day 9',disposition='psychiatric ward')
   out(b,sid,'At discharge, he was symptom-free.','discharge',time='At discharge')
  if i==2:
   for n in ['hypertension','diabetes mellitus',"Parkinson's disease"]:cond(b,sid,"a history of hypertension, diabetes mellitus, and Parkinson's disease",n,role='history',temporality='historical')
   sym(b,sid,'an ictal onset of vertigo, unsteadiness, and speech disturbance.','Ictal vertigo, unsteadiness and speech disturbance')
   sym(b,sid,'he was asymptomatic in the morning on the day of admission','Asymptomatic in morning',time='in the morning on the day of admission',text_value='asymptomatic')
   sym(b,sid,'at noon (12:00), he was noted to have difficulties in standing and walking without assistance, and his speech was slurred.','Difficulty standing/walking and slurred speech',time='at noon (12:00)')
   b.fact('social_exposures',sid,'The patient denied alcohol consumption or taking sedatives.','Denied alcohol consumption',domain='alcohol',name='alcohol consumption',assertion='absent')
   med(b,sid,'The patient denied alcohol consumption or taking sedatives.','sedatives',assertion='absent',action='historical')
   for q,n in [('horizontal gaze-directed nystagmus','Horizontal gaze-directed nystagmus'),('right-sided horizontal gaze palsy','Right horizontal gaze palsy'),('severe dysarthria','Severe dysarthria'),('mild ataxia in all four extremities','Mild four-extremity ataxia'),('severe truncal ataxia causing an inability to walk','Severe truncal ataxia and inability to walk'),('vigilant and fully oriented','Vigilant and fully oriented')]:sym(b,sid,q,n,time='On arrival (3 p.m.)')
   obs(b,sid,'NIHSS: 5 points','NIHSS',numeric_value=5,unit='points',kind='other')
   obs(b,sid,'his vital parameters were in the normal range.','Baseline vital parameters',kind='vital',text_value='normal range',flag='normal')
   obs(b,sid,'NCCT and CTA imaging was negative.','Initial NCCT and CTA',kind='imaging',text_value='Negative',flag='negative')
   obs(b,sid,'Laboratory screening on admission (including renal function) did not reveal significant abnormalities','Admission laboratory screening including renal function',time='on admission',text_value='No significant abnormalities',flag='normal')
   med(b,sid,'thrombolysis was performed (onset-to-needle time: 194 min).','thrombolysis',route='intravenous',dose_text='onset-to-needle time: 194 min')
   out(b,sid,'After thrombolysis, his neurological symptoms did not improve.','other',time='After thrombolysis')
   out(b,sid,"a few hours later (9 p.m.), the patient's condition deteriorated rapidly and he lost consciousness",'deterioration',time='9 p.m.')
   for n in ['intubation','mechanical ventilation']:proc(b,sid,'requiring intubation and mechanical ventilation.',n,time='9 p.m.')
   for q,n in [('comatose','Comatose'),('tetraplegic','Tetraplegia'),('bilateral Babinski signs','Bilateral Babinski signs')]:sym(b,sid,q,n,time='At that time')
   for n in ['oculocephalic reflex','pupillary light reflex']:b.fact('symptoms_function',sid,'his oculocephalic and pupillary light reflexes were absent',n+' absent',name=n,kind='sign',assertion='absent')
   obs(b,sid,'NCCT and CTA imaging was repeated, but no signs of bleeding or large vessel occlusion were found.','Repeat NCCT and CTA',kind='imaging',text_value='No bleeding or large vessel occlusion',flag='negative')
   cond(b,sid,'ABG analysis revealed extremely severe metabolic acidosis','Extremely severe metabolic acidosis',severity='extremely severe')
   for n in ['fluid boluses','sodium bicarbonate']:med(b,sid,'Despite several fluid boluses and sodium bicarbonate administration, the acidosis and lactate levels did not improve',n)
   out(b,sid,'the acidosis and lactate levels did not improve','other',related_condition='metabolic acidosis')
   sym(b,sid,'the patient became hemodynamically unstable','Hemodynamic instability')
   med(b,sid,'requiring continuous catecholamine infusion to maintain blood pressure.','catecholamine',route='infusion',frequency='continuous',indication='maintain blood pressure')
   cond(b,sid,'An urgent toxicological screening proved ethylene glycol intoxication','Ethylene glycol intoxication')
   for q,n,v,u in [('a sodium level of 147 mMol/L','Sodium',147,'mMol/L'),('a potassium level of 5.9 mMol/L','Potassium',5.9,'mMol/L'),('a creatinine level of 1.69 mg/dL','Creatinine',1.69,'mg/dL'),('GFR of 38 ml/min/1.73 m2','GFR',38,'ml/min/1.73 m2')]:obs(b,sid,q,n,time='9 h after his admission',numeric_value=v,unit=u)
   out(b,sid,'The patient was transferred to the intensive care unit of the internal medicine department','other',disposition='ICU internal medicine')
   med(b,sid,'ethanol infusion was started immediately','ethanol',action='started',route='infusion')
   proc(b,sid,'continuous renal replacement therapy (CRRT)','Continuous renal replacement therapy')
   med(b,sid,'A continuous infusion of bicarbonate was maintained to alkalinize the urine','bicarbonate',route='infusion',frequency='continuous',indication='alkalinize urine')
   for n in ['thiamine','pyridoxine']:med(b,sid,'thiamine and pyridoxine were also given intravenously to facilitate the conversion of glycolate into less toxic metabolites.',n,route='intravenous',indication='facilitate conversion of glycolate into less toxic metabolites')
   b.fact('care_plans',sid,'Ethylene glycol levels and blood pH were monitored regularly.','Ethylene glycol and pH regular monitoring',action_text='Monitor ethylene glycol levels and blood pH regularly',plan_type='monitoring',action_status='completed')
   obs(b,sid,'When the pH returned to normal and ethylene glycol levels remained < 20 mg/dL','Follow-up pH and ethylene glycol',text_value='pH normal; ethylene glycol <20 mg/dL')
   med(b,sid,'ethanol was stopped.','ethanol',action='stopped')
   sym(b,sid,'the patient became completely anuric','Complete anuria')
   out(b,sid,'his renal function continued to deteriorate','deterioration',related_condition='renal dysfunction')
   obs(b,sid,'elevations in creatinine over the next 10 days to a peak of 6.2 mg/dL.','Peak creatinine',time='over the next 10 days',numeric_value=6.2,unit='mg/dL')
   obs(b,sid,'Urine analysis revealed calcium oxalate crystals.','Urine calcium oxalate crystals',specimen='urine',text_value='calcium oxalate crystals',flag='positive')
   cond(b,sid,'On the 5th day after admission, pneumonia developed with markedly elevated inflammatory markers','Pneumonia',time='On the 5th day after admission',role='complication')
   obs(b,sid,'markedly elevated inflammatory markers','Inflammatory markers',time='On the 5th day after admission',text_value='markedly elevated',flag='high')
   med(b,sid,'required the administration of broad-spectrum antibiotics.','broad-spectrum antibiotics',indication='pneumonia')
   proc(b,sid,'Despite the withdrawal of sedation, the patient remained comatose.','Sedation withdrawal',action='removed')
   out(b,sid,'Despite the withdrawal of sedation, the patient remained comatose.','functional_outcome')
   out(b,sid,'he died on the 10th day after admission.','death',time='on the 10th day after admission')
   sym(b,sid,"the patient's mood had been depressed lately",'Depressed mood reported retrospectively',time='lately')
   b.fact('symptoms_function',sid,'although he had not mentioned suicidal thoughts','Suicidal thoughts not mentioned',name='reported suicidal thoughts',kind='mental_status',assertion='absent',notes='Absence of reported ideation does not establish absence of intent.')
   b.fact('social_exposures',sid,'an empty bottle of antifreeze liquid was found in his garage.','Empty antifreeze bottle found in garage',domain='environment',name='antifreeze liquid bottle',notes='Ingestion amount and intent not confirmed by patient.')
  if i==3:
   cond(b,sid,'a suspicion of an epileptic seizure','Epileptic seizure',assertion='possible',verification_status='provisional')
   for n in ['hypertension','chronic alcoholism','alcohol intoxication','delirium','prior right-hemispheric ischemic stroke']:cond(b,sid,'His medical history was significant for hypertension, chronic alcoholism, alcohol intoxication, delirium, and prior right-hemispheric ischemic stroke.',n,role='history',temporality='historical')
   sym(b,sid,'the patient lost consciousness for a few minutes at 1:30 p.m. on the day of admission.','Loss of consciousness for a few minutes',time='at 1:30 p.m. on the day of admission')
   for q,n in [('confused and disoriented','Confusion and disorientation'),('his speech was unintelligible','Unintelligible speech'),('moderate-severe left-sided spastic hemiparesis','Moderate-severe left spastic hemiparesis due to prior stroke'),('Babinksi sign due to the previous stroke','Babinski sign due to prior stroke'),('severe mixed aphasia','Severe mixed aphasia'),('extreme agitation','Extreme agitation')]:sym(b,sid,q,n)
   obs(b,sid,'NIHSS: 7 points','NIHSS',numeric_value=7,unit='points',kind='other')
   for n in ['clonazepam','tiapride']:med(b,sid,'clonazepam and tiapride were administered intravenously.',n,route='intravenous',indication='agitation')
   b.fact('symptoms_function',sid,'No direct or indirect signs of convulsion were detected.','No observed signs of convulsion',name='convulsion signs',kind='sign',assertion='absent')
   obs(b,sid,'NCCT imaging showed chronic infarction in the right internal capsule, but neither hemorrhage nor hyperacute ischemic lesion was described.','NCCT',kind='imaging',text_value='Chronic right internal capsule infarction; no hemorrhage or hyperacute ischemic lesion')
   obs(b,sid,'CTA imaging showed no evidence of arterial occlusion or stenosis in the intracranial vessels.','CTA',kind='imaging',text_value='No intracranial arterial occlusion or stenosis',flag='negative')
   cond(b,sid,'mild hyponatremia (122 mMol/L)','Mild hyponatremia',time='On admission',severity='mild')
   med(b,sid,'rt-PA in the standard dose of 0.9 mg/kg (5:20 p.m., onset-to-needle time: 230 min).','rt-PA',time='5:20 p.m.',dose_value=0.9,dose_unit='mg/kg',route='intravenous')
   out(b,sid,'His neurological status did not change during the thrombolysis.','other',time='during the thrombolysis')
   cond(b,sid,'alcohol intoxication could not be excluded','Alcohol intoxication',verification_status='differential',assertion='possible')
   obs(b,sid,'the toxicological screening was negative for ethanol','Ethanol toxicological screening',specimen='blood',text_value='negative for ethanol',flag='negative')
   cond(b,sid,'it revealed ethylene glycol intoxication','Ethylene glycol intoxication')
   obs(b,sid,'An ABG analysis was obtained, but it showed physiological acid–base parameters','ABG acid-base parameters',text_value='physiological acid–base parameters',flag='normal')
   out(b,sid,'The patient was admitted to the intensive care unit of the internal medicine department','other',disposition='ICU internal medicine')
   med(b,sid,'intravenous ethanol was administered.','ethanol',route='intravenous')
   out(b,sid,'His renal function did not show any worsening','other',text_value='renal function did not worsen') if False else obs(b,sid,'His renal function did not show any worsening','Renal function course',text_value='No worsening')
   b.fact('procedures_devices',sid,'hemodialysis was not indicated.','Hemodialysis not indicated',name='hemodialysis',kind='procedure',action='unknown',assertion='absent',notes='Not indicated; no performed dialysis inferred.')
   obs(b,sid,'The next day, there was no detectable amount of ethylene glycol in his blood.','Follow-up ethylene glycol',time='The next day',specimen='blood',text_value='No detectable amount',flag='negative')
   out(b,sid,"The patient's aphasia and agitation gradually diminished.",'improvement')
   cond(b,sid,'Hyponatremia was considered to be a consequence of chronic alcoholism','Hyponatremia attributed to chronic alcoholism',verification_status='provisional')
   med(b,sid,'corrected slowly with saline infusions.','saline',route='infusion',indication='hyponatremia')
   sym(b,sid,'On the third day of hospitalization, he was able to walk with minor assistance and was able to take care of himself.','Walking with minor assistance and independent self-care',time='On the third day of hospitalization')
   b.fact('social_exposures',sid,'He denied consuming antifreeze intentionally.','Denied intentional antifreeze consumption',domain='substance',name='intentional antifreeze consumption',assertion='absent',notes='Intent denied; positive ethylene glycol is separately retained.')
   out(b,sid,'On the 6th day after admission, he was discharged home with residual neurological symptoms consistent with his previous condition.','discharge',time='On the 6th day after admission',disposition='home')
   b.p['ambiguities'].append({'evidence':[{'segment_id':'b00001','quote':'presented after an epileptic seizure'},{'segment_id':sid,'quote':'a suspicion of an epileptic seizure'}],'issue':'Abstract calls seizure definite, detailed case and discussion retain suspicion; do not infer confirmed epilepsy.'})
  # Every available numerical table row, including normal results, individually and at its own occurrence.
  for s in b.s['segments']:
   if s['kind']!='table_row' or s['segment_id'] in ['b00015','b00031']:continue
   parts=s['text'].splitlines()[-1].split(' | ')
   if len(parts)!=4:continue
   raw=parts[i].strip();label=parts[0];m=re.match(r'(.*?) \((.*?)\)$',label);n=m[1] if m else label;u=m[2] if m else None
   time='At 48 h' if 'At 48 h' in s['text'] else 'On admission'
   # ABG table timing contradicts narratives; preserve label without resolving actual time.
   kw={'text_value':raw,'method':'ABG' if s.get('table_id')=='T1' else None}
   if raw!='–':
    v=re.match(r'([<>]?)\s*([−-]?[0-9.]+)$',raw)
    if v:kw.update(numeric_value=float(v[2].replace('−','-')),comparator=v[1] or '=')
   else:kw.update(status='unknown') if False else None
   f=b.fact('observations',s['segment_id'],s['text'],f'{n} {raw}'+(f' {u}' if u else '')+f' ({time})',time=time,name=n,kind='laboratory',status='resulted' if raw!='–' else 'unknown',unit=u,**kw,**({'result_absent_reason':'not_reported'} if raw=='–' else {}))
   owns[s['segment_id']]=['p1','p2','p3']
  # Course events use stated temporal subsets, not a guessed total order.
  for time in dict.fromkeys(f['fields'].get('time',{}).get('text') for f in b.p['facts']):
   if time:
    ids=[f['fact_id'] for f in b.p['facts'] if f['fields'].get('time',{}).get('text')==time];b.event(f'Case {i}: documented clinical findings/actions at {time}',ids,time)
  obs(b,'b00055','none of our patients suffered intracranial hemorrhage after thrombolysis, nor did they have any other disadvantages from the administration of rt-PA.','Post-thrombolysis adverse effects',time='after thrombolysis',text_value='No intracranial hemorrhage or other disadvantages from rt-PA',flag='negative')
  if i in [1,2]:
   b.p['ambiguities'].append({'issue':'Table 1 labels ABG results on admission, while narrative places severe acidosis after clinical deterioration. Preserve both descriptions; exact ABG collection time unresolved.','evidence':[{'segment_id':'b00005','quote':'Arterial blood gas (ABG) parameters of the three cases on admission.'}]})
  b.finish_patient()
 owns.update({k:['p1','p2','p3'] for k in ['b00001','b00003','b00050','b00053','b00054','b00055','b00056','b00057','b00058','b00065']})
 b.a['article_limitations'].append('Tables retain all numeric rows; bold abnormal-value formatting is absent in normalized retained text, so unmarked rows are not classified from external reference ranges. GFR table units mL/min differ from narrative indexed units; both retained without conversion.')
 b.save(owns)
if __name__=='__main__':article_12802722()
def bio(b,s,q,n,interp,**kw):
 f=b.fact('oncology',s,q,n+': '+q,name=n,tumor_ref='t1',interpretation=interp,**kw);b.p['facts'][-1]['collection']='biomarkers';return f
def tumor(b,s,q,**kw):
 f=b.fact('oncology',s,q,q,tumor_ref='t1',name='MTOR-mutated eosinophilic renal cell carcinoma',**kw);b.p['facts'][-1]['collection']='tumors';return f
def cancer_tx(b,s,q,n,mod,**kw):
 f=b.fact('oncology',s,q,n+': '+q,tumor_ref='t1',name=n,modality=mod,action=kw.pop('action','given'),**kw);b.p['facts'][-1]['collection']='treatments';return f
def article_13582412():
 b=Build('PMC13582412.1');owns={}
 for i in [1,2]:
  sid=f'b000{9+i:02d}';ng=f'b000{19+i:02d}';fu=f'b000{21+i:02d}';tb=f'b000{13+i:02d}'
  q='65-year-old male' if i==1 else '55-year-old male';b.patient(f'Patient #{i} (UChicago)',sid,q)
  for s in [sid,ng,fu,tb]:owns[s]=[f'p{i}']
  b.fact('case_context',sid,q,'Original UChicago human clinical case',case_kind='clinical_case',species='human')
  b.fact('demographics',sid,q,q,attribute='age_at_presentation',numeric_value=65 if i==1 else 55,unit='years',certainty='source_conflict' if i==1 else 'supported',scoring='review_only' if i==1 else 'include',notes='Table lists 63/M instead of narrative 65 years.' if i==1 else '')
  b.fact('demographics',sid,q,'Documented male',attribute='documented_sex',text_value='male')
  sym(b,sid,'fatigue and low back pain' if i==1 else 'low back pain','Low back pain')
  if i==1:sym(b,sid,'fatigue and low back pain','Fatigue')
  for d in ['conditions','family_genetics']:
   b.fact(d,sid,'no other significant medical or family history' if i==1 else 'no other significant medical history or family history','No other significant '+('medical history' if d=='conditions' else 'family history'),assertion='absent',**({'name':'other significant medical history','verification_status':'confirmed','role':'history'} if d=='conditions' else {'name':'other significant family history','kind':'family_condition'}))
  mass='a left renal mass (8.5 cm in greatest dimension)' if i==1 else 'a right renal mass (13 cm in greatest dimension)'
  obs(b,sid,mass,'Renal mass',kind='imaging',numeric_value=8.5 if i==1 else 13,unit='cm',text_value='left' if i==1 else 'right',**({'certainty':'source_conflict','scoring':'review_only','notes':'Comparison table gives 12 cm; occurrence/method difference not explained.'} if i==2 else {}))
  if i==1:
   obs(b,sid,'small liver and lung nodules suspicious for metastasis.','Liver and lung nodules',kind='imaging',text_value='Small nodules suspicious for metastasis')
  else:obs(b,sid,'Initial imaging showed no evidence of metastasis.','Initial metastasis assessment',kind='imaging',text_value='No evidence of metastasis',flag='negative')
  surgery=proc(b,sid,'The patient subsequently underwent a radical nephrectomy.','Radical nephrectomy',body_site='kidney',laterality='left' if i==1 else 'right')
  tumor(b,sid,mass,primary_site='kidney',laterality='left' if i==1 else 'right',histology='eosinophilic renal cell carcinoma')
  for q,n in [('solid/nested and sheet-like architecture composed of eosinophilic tumor cells with pronounced cellular membrane and occasional perinuclear clearing','Solid/nested and sheet-like eosinophilic morphology with pronounced membrane and occasional perinuclear clearing'),('round nuclei with smooth nuclear membrane and prominent nucleoli','Round nuclei, smooth nuclear membrane and prominent nucleoli'),('up to 20 mitotic figures per 10 high-power fields','Mitotic figures'),('Tumor necrosis was also focally (5%) present','Tumor necrosis'),('loose nests and cords in a background of light-pink amorphous/mucoid material','Loose nests/cords in light-pink amorphous/mucoid material'),('binucleated tumor cells with rhabdoid morphology','Binucleated rhabdoid tumor cells'),('vacuolated tumor cells were seen','Vacuolated tumor cells'),('pseudotubular and pseudoglandular spaces','Pseudotubular and pseudoglandular spaces'),('some tumor cells having pseudonuclear inclusions','Pseudonuclear inclusions'),('Prominent hyalinized stroma','Prominent hyalinized stroma'),('psammomatous calcification','Psammomatous calcification'),('tumor cells with “blue granules”','Blue granules')]:obs(b,'b00012',q,n,kind='pathology',text_value=q)
  for q,n,it in [('positive for PAX8 (diffuse, > 90%)','PAX8','positive'),('CD117 (diffuse, > 90%; Fig. 1I)','CD117','positive'),('negative for vimentin','vimentin','negative'),('negative for CK7','CK7','negative'),('negative for pan-keratin AE1/AE3','pan-keratin AE1/AE3','negative'),('negative for Melan A, TFE3, and CD10','Melan A','negative'),('negative for Melan A, TFE3, and CD10','TFE3','negative'),('negative for Melan A, TFE3, and CD10','CD10','negative'),('retained SDHB expressions','SDHB','positive')]:bio(b,'b00013',q,n,it,assay='IHC',text_value=q)
  bio(b,'b00013','Tumor #1 showed focal (5–10%) CK20 expression.' if i==1 else 'Tumor #2 showed diffuse (> 90%) CK20 expression','CK20','positive',assay='IHC',text_value='focal (5–10%)' if i==1 else 'diffuse (>90%)')
  mut='c.7279_7280delinsAA, p.L2427K; VAF: 57%' if i==1 else 'c.4348_4368del, p.Y1450_W1456del; VAF: 27%'
  bio(b,ng,mut,'MTOR','pathogenic',gene='MTOR',alteration=mut,assay='NGS',text_value=mut)
  bio(b,ng,'a loss of chromosome 1 in copy number variant analysis','chromosome 1 copy number','positive',alteration='loss of chromosome 1',assay='copy number variant analysis')
  obs(b,ng,'Apart from the loss of chromosome 1, no significant copy number changes were observed.','Other copy number changes',text_value='No significant changes beyond chromosome 1 loss',flag='negative')
  bio(b,ng,'Tumor mutational burden was 3.0 mutations per megabase.' if i==1 else 'Tumor mutational burden was 1.2 mutations per megabase.','tumor mutational burden','unknown',numeric_value=3.0 if i==1 else 1.2,unit='mutations per megabase')
  bio(b,ng,'The tumor was microsatellite stable (MSS).','microsatellite status','unknown',text_value='microsatellite stable (MSS)')
  if i==1:
   b.fact('family_genetics',ng,'germline testing in 2022 by Invitae multi-gene panel','Invitae germline testing',time='in 2022',name='Invitae multi-gene panel',kind='genetic_test',origin='germline')
   b.fact('family_genetics',ng,'no pathogenic variant in all genes tested','No pathogenic germline variant in tested genes',name='pathogenic variant',kind='genetic_test',origin='germline',assertion='absent',interpretation='no pathogenic variant')
   b.fact('family_genetics',ng,'a variant of uncertain significance in BRCA1 (heterozygous; c.3518G > A; p.Ser1173Asn)','Heterozygous BRCA1 VUS',name='BRCA1 VUS',kind='genetic_test',gene='BRCA1',variant='c.3518G > A; p.Ser1173Asn',origin='germline',interpretation='variant of uncertain significance; heterozygous')
   stage=tumor(b,fu,'Tumor #1 was staged as pT3aN1 due to biopsy-confirmed metastasis in a retroperitoneal lymph node.',t_category='pT3a',n_category='N1',stage_context='pathologic',metastatic_sites=['retroperitoneal lymph node'])
   sunit=cancer_tx(b,fu,'first treated with sunitinib with a stable disease','sunitinib','systemic',response='stable disease')
   nivo=cancer_tx(b,fu,'followed by nivolumab, which led to a partial response.','nivolumab','systemic',response='partial response')
   obs(b,fu,'liver nodules were persistent and showed slight enlargement','Liver nodules follow-up',kind='imaging',text_value='persistent; slight enlargement')
   biopsy=proc(b,fu,'a targeted biopsy was performed and confirmed metastatic renal cell carcinoma.','Targeted liver biopsy',finding='metastatic renal cell carcinoma')
   tumor(b,fu,'a targeted biopsy was performed and confirmed metastatic renal cell carcinoma.',disease_extent='distant_metastatic',metastatic_sites=['liver'])
   ax=cancer_tx(b,fu,'Axitinib was then started','Axitinib','systemic')
   yt=cancer_tx(b,fu,'followed by hepatic arterial yttrium-90 microspheres for persistent liver-only disease.','hepatic arterial yttrium-90 microspheres','radiotherapy')
   rad=cancer_tx(b,fu,'received radiation for increasing periportal abdominal lymphadenopathy in 2022.','radiation','radiotherapy',time='in 2022',response=None)
   out(b,fu,'the patient has been alive with a stable disease 153 months after the initial diagnosis.','follow_up',time='153 months after the initial diagnosis',follow_up_duration_text='153 months',text_value='') if False else out(b,fu,'the patient has been alive with a stable disease 153 months after the initial diagnosis.','follow_up',time='153 months after the initial diagnosis',follow_up_duration_text='153 months')
   ev=[b.event('Radical nephrectomy',[surgery]),b.event('Sunitinib with stable disease',[sunit]),b.event('Nivolumab with partial response',[nivo]),b.event('Liver biopsy confirms metastasis',[biopsy]),b.event('Axitinib started',[ax]),b.event('Yttrium-90 for liver-only disease',[yt])]
   for x,y in zip(ev,ev[1:]):b.p['temporal_relations'].append({'from_event_id':x,'to_event_id':y,'relation':'before','evidence':[{'segment_id':fu,'quote':b.seg[fu]['text']}]})
   b.p['ambiguities'].append({'issue':'Age conflict: clinical narrative gives 65 years, comparison table 63/M; no explanation.','evidence':[{'segment_id':sid,'quote':'65-year-old male'},{'segment_id':tb,'quote':'63/M'}]})
  else:
   tumor(b,fu,'Tumor #2 was staged as pT3aNx as it showed invasion into renal sinus soft tissue and segmental branch of renal vein as tumor thrombi.',t_category='pT3a',n_category='Nx',stage_context='pathologic')
   for q,n in [('invasion into renal sinus soft tissue','Renal sinus soft tissue invasion'),('segmental branch of renal vein as tumor thrombi','Segmental renal vein tumor thrombi')]:obs(b,fu,q,n,kind='pathology',text_value=q)
   for q,n,v,u,t in [('an enlarged paracaval lymph node measuring 1.6 × 1 cm','Paracaval lymph node',None,'cm','3 months after the surgery'),('a 16 mm right rib lesion','Right rib lesion',16,'mm','3 months after the surgery'),('increased size of retrocaval adenopathy (1.6 × 1 cm → 3.3 × 1.9 cm)','Retrocaval adenopathy enlargement',None,'cm','4 months after the initial surveillance CT CAP'),('a growth of the right rib lesion (16 mm → 21 mm)','Right rib lesion growth',21,'mm','4 months after the initial surveillance CT CAP')]:obs(b,fu,q,n,kind='imaging',text_value=q,numeric_value=v,unit=u,time=t,method='CT Chest–Abdomen–Pelvis')
   tumor(b,fu,'consistent with clinicoradiologic recurrence/progression of the disease.',disease_status='progression',metastatic_sites=['right rib'],disease_extent='distant_metastatic')
   b.fact('care_plans',fu,'the systemic therapy is held off until further disease progression with greater tumor burden','Defer systemic therapy until further progression/greater tumor burden',action_text='Hold systemic therapy until further disease progression with greater tumor burden',plan_type='other',action_status='planned')
   cancer_tx(b,fu,'received localized radiation therapy to the right rib lesion due to pain associated with the lesion.','Localized right rib radiation','radiotherapy')
   sym(b,fu,'pain associated with the lesion','Right rib lesion pain')
   out(b,fu,'The patient is currently alive with stable disease 16 months after the initial diagnosis.','follow_up',time='16 months after the initial diagnosis',follow_up_duration_text='16 months')
   ev=[b.event('Radical nephrectomy',[surgery]),b.event('First surveillance CT identifies lesions',[f['fact_id'] for f in b.p['facts'] if f['fields'].get('time',{}).get('text')=='3 months after the surgery'],'3 months after the surgery'),b.event('Follow-up CT demonstrates progression',[f['fact_id'] for f in b.p['facts'] if f['fields'].get('time',{}).get('text')=='4 months after the initial surveillance CT CAP'],'4 months after the initial surveillance CT CAP')]
   for x,y in zip(ev,ev[1:]):b.p['temporal_relations'].append({'from_event_id':x,'to_event_id':y,'relation':'before','evidence':[{'segment_id':fu,'quote':b.seg[fu]['text']}]})
   b.p['ambiguities'].append({'issue':'Renal tumor size conflict/possible different assessments: narrative workup 13 cm, table 12 cm; cannot infer measurement method for table.','evidence':[{'segment_id':sid,'quote':'13 cm in greatest dimension'},{'segment_id':tb,'quote':'12'}]})
  b.fact('care_plans','b00026','mTOR inhibitor is being considered an option if the diseases progress.','mTOR inhibitor considered if disease progresses',assertion='conditional',action_text='Consider mTOR inhibitor if disease progresses',plan_type='other',action_status='planned')
  b.p['ambiguities'].append({'issue':'Discussion says current patients have not needed second-line treatment, conflicting with patient 1 multiple systemic agents and patient 2 systemic therapy held. Use detailed clinical follow-up for actual therapy.','evidence':[{'segment_id':'b00026','quote':'The patients in the current series responded well to first-line therapies given and have not needed a second-line therapy'}]})
  b.finish_patient()
 for s in ['b00001','b00006','b00007','b00012','b00013','b00026','b00028']:owns[s]=['p1','p2']
 b.a['article_limitations'].append('Three comparison-table literature patients are secondary cited cases, excluded from original current-series patient enumeration. Figure panels A–L are shared tumor examples without explicit per-patient assignment except panel L attributed to tumor 2.')
 b.save(owns)
 # Replace caption-only panel placeholder with stated panel ownership.
 path=BASE/'drafts'/'PMC13582412.1.json';a=json.loads(path.read_text());fig=a['figures'][0];fig['scope']='shared';fig['panels']=[]
 for panel,desc in [('A','solid/nested and sheet-like architecture; nuclear features'),('B','round nuclei and prominent nucleoli; frequent mitoses'),('C','tumor necrosis'),('D','loose nests/cords; rhabdoid and vacuolated cells'),('E','pseudotubular/pseudoglandular spaces and pseudonuclear inclusions'),('F','hyalinized stroma'),('G','psammomatous calcification'),('H','blue granules'),('I','CD117 diffuse positivity'),('J','vimentin negative'),('K','CK7 negative'),('L','Tumor 2 diffuse CK20 expression')]:
  fig['panels'].append({'panel':panel,'patient_ids':['p2'] if panel=='L' else ['p1','p2'],'scope':'individual' if panel=='L' else 'shared','description_from_caption':desc,'modality':'immunohistochemistry' if panel in 'IJKL' else 'histopathology','body_part':'kidney tumor','evidence':fig['caption_evidence']})
 path.write_text(json.dumps(a,ensure_ascii=False,indent=2)+'\n')
def article_13542385():
 b=Build('PMC13542385.1');b.patient('12-year-old male with VATER syndrome','b00004','12-year-old male');s='b00004'
 b.fact('case_context',s,'12-year-old male','Human surgical clinical case',case_kind='clinical_case',species='human',care_setting='hospital')
 b.fact('demographics',s,'12-year-old male','Age 12 years',attribute='age_at_presentation',numeric_value=12,unit='years');b.fact('demographics',s,'12-year-old male','Documented male',attribute='documented_sex',text_value='male')
 for q,n in [('a diagnosis of VATER syndrome','VATER syndrome'),('a complete bilateral cleft lip and palate','Complete bilateral cleft lip and palate'),('a large oronasal fistula intraorally','Large intraoral oronasal fistula'),('his missing premaxilla','Missing premaxilla'),('missing anterior teeth #7, 8, 9, and 10','Missing anterior teeth #7,8,9,10'),('a supernumerary tooth #56 adjacent to the defect','Supernumerary tooth #56'),('bilateral posterior crossbite','Bilateral posterior crossbite'),('maxillary hypoplasia and negative overjet','Maxillary hypoplasia'),('maxillary hypoplasia and negative overjet','Negative overjet')]:cond(b,s,q,n)
 old=proc(b,s,'his premaxilla surgically removed by a previous surgeon during his primary lip repair.','Premaxilla removal during primary lip repair',action='historical',temporality='historical')
 sym(b,s,'significant liquid passage into his nose during eating and drinking.','Significant nasal liquid passage with eating and drinking',activity_context='eating and drinking')
 obs(b,s,'The patient was in mixed dentition','Dentition',kind='other',text_value='mixed dentition')
 proc(b,s,'did not have any orthodontic preparation','Orthodontic preparation',action='historical',assertion='absent')
 plans=[]
 for n in ['anterior iliac crest bone graft','extraction of supernumerary tooth #56','oronasal fistula closure']:
  plans.append(proc(b,s,'planned for surgical reconstruction with anterior iliac crest bone graft, extraction of supernumerary tooth #56, and oronasal fistula closure.',n,action='planned',temporality='future'))
 b.fact('care_plans',s,'This was not addressed preoperatively due to concerns that maxillary expansion would widen the cleft gap leading to larger and more difficult bone graft.','Posterior crossbite correction deferred preoperatively',action_text='Defer maxillary expansion preoperatively due to risk of widening cleft gap',plan_type='other',action_status='completed',target='bilateral posterior crossbite')
 proc(b,s,'After bone graft and skeletal maturity, the patient will be planned for maxillary advancement to address his maxillary hypoplasia and negative overjet.','Maxillary advancement',action='planned',temporality='future',time='After bone graft and skeletal maturity',indication='maxillary hypoplasia and negative overjet')
 graft=proc(b,'b00005','A 2 cm wide cortical block was harvested from the medial aspect of the left iliac crest using a reciprocating saw and osteotome.','Cortical block graft harvest',body_site='medial left iliac crest',laterality='left',finding='2 cm wide cortical block')
 obs(b,'b00005','A 2 cm wide cortical block','Harvested cortical block width',kind='other',numeric_value=2,unit='cm')
 proc(b,'b00005','cancellous bone was harvested from the iliac crest using bone curettes.','Iliac crest cancellous bone harvest',body_site='iliac crest')
 proc(b,'b00005','the site was irrigated, and the hip surgical site was then closed with appropriate multilayer closure.','Donor-site irrigation and multilayer hip closure',body_site='hip donor site')
 proc(b,'b00006',"Vertical incisions were made medial to the maxillary canines and were extended to the anterior aspect of the patient's midline alveolar fistula bilaterally.",'Bilateral maxillary incisions',body_site='maxilla',laterality='bilateral')
 proc(b,'b00006','Subperiosteal dissection was done to expose the lateral segments of the maxilla and the bony edges of the cleft defect were identified up to the piriform rim.','Subperiosteal dissection to piriform rim',body_site='maxilla')
 proc(b,'b00007','The nasal mucosa was separated from the buccal and palatal mucosa. The nasal mucosa was then closed to form a watertight seal.','Nasal mucosal separation and watertight closure')
 proc(b,'b00007','The cortical block was then shaped and then perforations made through the block to aid in vascular ingrowth and integration.','Cortical graft shaping and perforation')
 med(b,'b00007','A Rh-BMP2 (Infuse-Medtronic) soaked collagen sponge was then placed against the closed nasal mucosal layer as well as the closed palatal mucosa.','Rh-BMP2 (Infuse-Medtronic) soaked collagen sponge',route='local application',indication='osteogenesis and graft incorporation')
 inset=proc(b,'b00007','The cortical block was gently inserted into the cleft defect with a mallet to achieve primary stability without excessive force','Cortical block insertion into cleft defect',finding='primary stability')
 proc(b,'b00007','the cancellous bone harvested from the ilium was packed into any defects','Cancellous bone packing into residual defects')
 med(b,'b00007','an additional Rh-BMP2 covered the anterior and inferior aspects of the graft.','additional Rh-BMP2',route='local application',indication='osteogenesis and graft incorporation')
 out(b,'b00007','No complications noted','complication',assertion='absent')
 proc(b,'b00007','the patient was turned over to anesthesia for extubating and transportation to the PACU.','Extubation and PACU transfer',action='planned',notes='Transfer to anesthesia occurred; sentence expresses purpose, without separately documenting completed extubation.')
 out(b,'b00008','an unremarkable hospital stay','other')
 dis=out(b,'b00008','discharged on post-operative day 1.','discharge',time='on post-operative day 1')
 b.fact('care_plans','b00008','Post-operative instructions were given for 6 weeks of soft diet','Soft diet instructions for 6 weeks',action_text='Soft diet for 6 weeks',plan_type='counseling',action_status='recommended')
 med(b,'b00008','1 week of post-operative antibiotics with Augmentin.','Augmentin',action='planned',duration_text='1 week',indication='postoperative antibiotics')
 for t in ['At 1 week','3-months post-op']:proc(b,'b00008','At 1 week and 3-months post-op, the patient had photographic and radiographic evaluation','Photographic and radiographic follow-up',time=t)
 obs(b,'b00008','At 13-month post-op, the patient had additional radiographs taken (Figure 7) all demonstrating stable bony fill after the operation.','13-month radiographic assessment',time='At 13-month post-op',kind='imaging',text_value='stable bony fill')
 obs(b,'b00008','Final measurements of bone graft from Figure 7 at 13 months were 4.95 mm×13.05 mm at the most central portion of the graft.','Central graft dimensions',time='at 13 months',kind='imaging',text_value='4.95 mm×13.05 mm',unit='mm')
 out(b,'b00001','13-month follow-up demonstrating bone consolidation and fistula closure.','follow_up',time='13-month follow-up',follow_up_duration_text='13 months')
 b.fact('care_plans','b00020','good bone volume is present but may be insufficient for dental implant placement.','Bone volume may be insufficient for dental implants',action_text='Dental implant feasibility uncertain due to possible insufficient bone volume',plan_type='other',action_status='unknown',assertion='possible')
 for s,n,t in [('b00021','Missing premaxillary segment and oronasal fistula',None),('b00022','Preprocedure CT: missing premaxillary segment','pre-procedure'),('b00024','CT: cortical block placement at premaxilla','at 1 week post procedure'),('b00025','CT: bony fill of premaxillary segment','at 3 months post procedure'),('b00026','Photograph: bilateral class III malocclusion','3 months post-op'),('b00027','CT: continued bony fill of premaxillary segment','at 13 months post procedure')]:obs(b,s,b.seg[s]['text'],n,time=t,kind='imaging',text_value=b.seg[s]['text'].split('\n',1)[-1],notes='Caption-documented finding; pixels not inspected.')
 ev=[b.event('Historical primary lip repair with premaxilla removal',[old]),b.event('Reconstructive surgery planned',plans,occurrence='planned'),b.event('Nonvascularized iliac graft harvest and inset',[graft,inset]),b.event('Discharge',[dis],'on post-operative day 1')]
 for x,y in zip(ev,ev[1:]):b.p['temporal_relations'].append({'from_event_id':x,'to_event_id':y,'relation':'before','evidence':[{'segment_id':'b00004','quote':b.seg['b00004']['text']},{'segment_id':'b00008','quote':b.seg['b00008']['text']}]})
 b.p['ambiguities'].append({'issue':'Extraction of supernumerary #56 is planned but not explicitly documented as completed in operative technique. Do not infer completion. Caption Figure 3 says ileum, narrative donor site is iliac crest/ilium; preserve donor narrative without intestinal graft inference.','evidence':[{'segment_id':'b00004','quote':'extraction of supernumerary tooth #56'},{'segment_id':'b00023','quote':'block harvested from the ileum'}]})
 b.finish_patient();owns={f'b000{i:02d}':['p1'] for i in [1,3,4,5,6,7,8,17,19,20,21,22,23,24,25,26,27]};b.a['article_limitations'].append('Literature review cases are secondary reports and not enumerated as original patients. Specific VATER organ-system malformations, medications doses/routes, and postoperative completed tooth extraction are not documented.');b.save(owns)
 path=BASE/'drafts'/'PMC13542385.1.json';a=json.loads(path.read_text())
 for f in a['figures']:
  sid=f['caption_evidence'][0]['segment_id'];cap=f['caption_summary'];
  if 'CT scan' in cap:
   f['panels']=[{'panel':p,'patient_ids':['p1'],'scope':'individual','description_from_caption':view+' CT view; '+cap.split('Scans taken')[-1],'modality':'CT','body_part':'maxilla','evidence':f['caption_evidence']} for p,view in [('upper left','coronal'),('upper right','sagittal'),('lower left','axial'),('lower right','panoramic')]]
  else:f['panels'][0]['modality']='photograph' if 'photograph' in cap else None;f['panels'][0]['body_part']='maxilla'
 path.write_text(json.dumps(a,ensure_ascii=False,indent=2)+'\n')
def article_13612109():
 b=Build('PMC13612109.1');s='b00003';b.patient('72-year-old male with follicular thyroid carcinoma',s,'A 72-year-old hypertensive male')
 b.fact('case_context',s,'A 72-year-old hypertensive male','Human metastatic thyroid cancer case',case_kind='clinical_case',species='human')
 b.fact('demographics',s,'A 72-year-old hypertensive male','Age 72 years',attribute='age_at_presentation',numeric_value=72,unit='years');b.fact('demographics',s,'A 72-year-old hypertensive male','Documented male',attribute='documented_sex',text_value='male')
 cond(b,s,'hypertensive male','Hypertension',role='comorbidity');cond(b,s,'cardiac comorbidities','Cardiac comorbidities (unspecified)',role='comorbidity')
 f=b.fact('oncology',s,'low-risk follicular thyroid carcinoma','Low-risk follicular thyroid carcinoma',name='follicular thyroid carcinoma',tumor_ref='t1',histology='follicular carcinoma',primary_site='thyroid');b.p['facts'][-1]['collection']='tumors'
 thy=proc(b,s,'underwent total thyroidectomy and 30 mCi RAI ablation in 2017','Total thyroidectomy',time='in 2017',body_site='thyroid')
 ab=cancer_tx(b,s,'30 mCi RAI ablation in 2017','RAI ablation','radiotherapy',time='in 2017')
 med(b,s,'30 mCi RAI ablation in 2017','RAI',dose_value=30,dose_unit='mCi',time='in 2017')
 sym(b,s,'Four years later, he presented with cervical neck pain','Cervical neck pain',time='Four years later')
 obs(b,s,'elevated serum thyroglobulin (Sr Tg) of 264.1 ng/mL','Serum thyroglobulin',time='Four years later',numeric_value=264.1,unit='ng/mL',flag='high',specimen='serum')
 obs(b,s,'A whole-body diagnostic RAI scintigraphy showed RAI uptake in a lytic C2 vertebral lesion and sub-centimetric lung nodules','Whole-body diagnostic RAI scintigraphy',kind='imaging',text_value='RAI uptake in lytic C2 vertebral lesion and sub-centimetric lung nodules')
 obs(b,s,'moderate and low-grade [ ^(18) F]F-FDG uptake on whole body [ ^(18) F]F-FDG PET/computed tomography (CT).','Initial whole-body FDG PET/CT',kind='imaging',text_value='Moderate and low-grade uptake in C2 and lung lesions')
 eb=cancer_tx(b,s,'He received EBRT to the C2 vertebra','EBRT to C2 vertebra','radiotherapy')
 for q,t,v in [('100 mCi (2023)','2023',100),('150 mCi (2024','2024',150)]:
  cancer_tx(b,s,q,'RAI','radiotherapy',time=t);med(b,s,q,'RAI',time=t,dose_value=v,dose_unit='mCi')
 sym(b,s,'One year posttreatment, he had mild persisting neck pain','Mild persistent neck pain',time='One year posttreatment',severity='mild')
 obs(b,s,'an abrupt rise in Sr Tg 2568 ng/mL','Serum thyroglobulin',time='One year posttreatment',numeric_value=2568,unit='ng/mL',flag='high')
 obs(b,s,'anti-thyroglobulin antibody (ATA) of 23.9 IU/mL (normal < 4.1 IU/mL)','Anti-thyroglobulin antibody',time='One year posttreatment',numeric_value=23.9,unit='IU/mL',reference_range_text='normal < 4.1 IU/mL')
 obs(b,s,'negative RAI scintigraphy ( Fig. 1G, H ) despite adequate iodine restriction','Follow-up RAI scintigraphy',time='One year posttreatment',kind='imaging',text_value='Negative despite adequate iodine restriction',flag='negative')
 b.fact('social_exposures',s,'adequate iodine restriction','Adequate iodine restriction before scan',domain='diet',name='iodine restriction')
 cond(b,s,'qualifying as a case of thyroglobulin elevated negative iodine scintigraphy syndrome.','Thyroglobulin elevated negative iodine scintigraphy syndrome')
 cond(b,'b00009','progressive RAIR disease','Radioiodine-refractory thyroid carcinoma')
 for q,n,v in [('moderate uptake in the C2 lesion (SUV _(max) : 8.5','Restaging FDG PET C2 SUVmax',8.5),('low-grade uptake in two lung nodules (SUV _(max) : 3.1–4.3','Restaging FDG PET lung SUVmax',None)]:obs(b,s,q,n,kind='imaging',numeric_value=v,text_value=q)
 for n in ['surgical debridement with fixation of the C2 lesion','repeat EBRT','tyrosine kinase inhibitor (TKI)']:
  b.fact('care_plans',s,'the therapeutic options considered were surgical debridement with fixation of the C2 lesion, repeat EBRT, and addition of tyrosine kinase inhibitor (TKI) with continued TSH suppression.','Considered '+n,action_text='Consider '+n,plan_type='other',action_status='unknown',assertion='possible',notes='Considered option only; not documented performed.')
 proc(b,s,'the patient denied surgery.','C2 debridement/fixation surgery',action='declined')
 cancer_tx(b,s,'Empirical RAI was ruled out, as structural disease with potential morbidity was evident.','Empirical RAI','radiotherapy',action='unknown',assertion='absent',notes='Ruled out as an option; distinguish prior completed RAI cycles.')
 b.fact('care_plans',s,'continued TSH suppression','Continue TSH suppression',action_text='Continue TSH suppression',plan_type='other',action_status='planned',target='thyroid cancer')
 b.fact('care_plans','b00009','considered TRT as an interim approach, aiming to delay the introduction of TKI and reduce treatment-related morbidity.','TRT interim approach to defer TKI',action_text='Use TRT to delay TKI and reduce treatment morbidity',plan_type='other',action_status='completed')
 sym(b,'b00009','relatively preserved performance status','Relatively preserved performance status')
 out(b,'b00009','continued disease progression despite EBRT','deterioration',related_condition='thyroid carcinoma')
 obs(b,s,'Staging PET/CT with [ ^(68) Ga]Ga-DOTATATE showed low somatostatin receptor (SSTR) expression (Krenning score 2) in two out of three lesions','DOTATATE PET/CT',kind='imaging',text_value='Low SSTR expression, Krenning 2, in two of three lesions')
 obs(b,s,'[ ^(68) Ga]Ga-prostate specific membrane antigen (PSMA)-11 PET/CT revealed moderate PSMA expression in all lesions (miPSMA score 2','PSMA-11 PET/CT',kind='imaging',text_value='Moderate PSMA expression in all lesions, miPSMA 2')
 tx=cancer_tx(b,s,'He was treated with one cycle of 6.6GBq [ ^(177) Lu]Lu-PSMA-617.','[ ^(177) Lu]Lu-PSMA-617','radiotherapy')
 med(b,s,'He was treated with one cycle of 6.6GBq [ ^(177) Lu]Lu-PSMA-617.','[ ^(177) Lu]Lu-PSMA-617',dose_value=6.6,dose_unit='GBq',duration_text='one cycle')
 follow=out(b,s,'On 6 weekly follow-ups, he reported a significant reduction of neck pain','improvement',time='On 6 weekly follow-ups',related_condition='neck pain')
 b.fact('symptoms_function',s,'no clinical signs of radiculopathy','No clinical radiculopathy signs',time='On 6 weekly follow-ups',name='radiculopathy signs',kind='sign',assertion='absent')
 obs(b,s,'Sr. Tg (249 ng/mL)','Follow-up serum thyroglobulin',time='On 6 weekly follow-ups',numeric_value=249,unit='ng/mL')
 obs(b,s,'ATA (9.52 IU/mL, normal < 4.11 IU/mL)','Follow-up anti-thyroglobulin antibody',time='On 6 weekly follow-ups',numeric_value=9.52,unit='IU/mL',reference_range_text='normal < 4.11 IU/mL')
 for sid in ['b00004','b00005','b00006']:
  row=b.seg[sid]['text'].splitlines()[-1].split(' | ');loc=row[0]
  for n,val in zip(['[18F]F-FDG','[68Ga]Ga-DOTATATE','[68Ga]Ga-PSMA'],row[1:]):obs(b,sid,b.seg[sid]['text'],n+' SUVmax '+loc,kind='imaging',numeric_value=float(val.split(' ')[0]),text_value=val,body_site=loc,method=n+' PET',notes='Exact table values retained; narrative/caption differences not harmonized.')
 obs(b,'b00011','First posttherapy scan (2017) showing remnant neck uptake.','2017 posttherapy planar RAI scan',time='2017',kind='imaging',text_value='remnant neck uptake')
 obs(b,'b00011','Whole-body diagnostic scan in 2025 showing no RAI-avid foci and a significant increase in Sr Tg.','2025 diagnostic RAI scan',time='in 2025',kind='imaging',text_value='No RAI-avid foci; significant Sr Tg increase',flag='negative')
 for q,n in [('Sagittal T1-weighted MRI shows a hyperintense lytic lesion in the C2 vertebral body','C2 MRI hyperintense lytic lesion'),('CT image reveals osteolytic destruction of the C2 vertebra.','C2 CT osteolytic destruction')]:obs(b,'b00012',q,n,kind='imaging',text_value=q,notes='Caption finding only; pixels not inspected.')
 ev=[b.event('Total thyroidectomy and initial RAI',[thy,ab],'in 2017'),b.event('EBRT to C2',[eb]),b.event('Lu-PSMA cycle',[tx]),b.event('Clinical response follow-up',[follow],'On 6 weekly follow-ups')]
 for x,y in zip(ev,ev[1:]):b.p['temporal_relations'].append({'from_event_id':x,'to_event_id':y,'relation':'before','evidence':[{'segment_id':s,'quote':b.seg[s]['text']}]})
 b.p['ambiguities']=[{'issue':'Chronology: narrative says four years after 2017 then RAI in 2023/2024, one year later; do not infer an unreported initial metastatic workup year. Follow-up phrase "6 weekly" is ambiguous between six-week interval and six weekly visits.','evidence':[{'segment_id':s,'quote':'Four years later'},{'segment_id':s,'quote':'On 6 weekly follow-ups'}]},{'issue':'PET values/count conflict: C2 FDG narrative/caption 8.5 vs table 8.60; lung narrative SUV 3.1–4.3 vs table 3.89/2.50; DOTATATE narrative 2 of 3 lesions vs table reports all 3. Caption Fig 1 Sr Tg unit ng/dL vs narrative ng/mL. Preserve original measurements without conversions.','evidence':[{'segment_id':'b00004','quote':b.seg['b00004']['text']},{'segment_id':s,'quote':'SUV _(max) : 3.1–4.3'},{'segment_id':'b00011','quote':'values (ng/dL)'}]}]
 b.finish_patient();owns={f'b000{i:02d}':['p1'] for i in [1,3,4,5,6,9,11,12,13]};b.save(owns)
 path=BASE/'drafts'/'PMC13612109.1.json';a=json.loads(path.read_text())
 mappings=[ [('A','2017 remnant neck uptake','RAI planar scintigraphy'),('B','2017 remnant neck uptake','RAI planar scintigraphy'),('C–F','2023/2024 RAI-avid C2 and lung lesions','RAI planar scintigraphy'),('G','2025 no RAI-avid foci','RAI planar scintigraphy'),('H','2025 no RAI-avid foci','RAI planar scintigraphy')], [('A','C2 hyperintense lytic lesion','T1-weighted MRI'),('B','C2 osteolytic destruction','CT'),('C','C2 FDG SUVmax 8.5','FDG PET'),('D','C2 SSTR Krenning 2 SUVmax 9.8','DOTATATE PET'),('E','C2 PSMA miPSMA 2 SUVmax 19.2','PSMA PET')], [('A, B','C2 moderate and solitary lung nodule low FDG uptake','FDG PET MIP'),('C, D','C2 and lung nodule mild SSTR Krenning 2','DOTATATE PET MIP'),('E, F','C2 and two lung nodules moderate miPSMA 2','PSMA PET MIP')]]
 for f,m in zip(a['figures'],mappings):f['panels']=[{'panel':p,'patient_ids':['p1'],'scope':'individual','description_from_caption':d,'modality':mod,'body_part':'C2 vertebra' if 'C2' in d and 'lung' not in d else None,'evidence':f['caption_evidence']} for p,d,mod in m]
 path.write_text(json.dumps(a,ensure_ascii=False,indent=2)+'\n')
def article_12285374():
 b=Build('PMC12285374.1');owns={}
 for i in [1,2]:
  s='b00004' if i==1 else 'b00005';t=s if i==1 else 'b00006';age=50 if i==1 else 55;b.patient(f'Case {i}',s,f'A {age}-year-old male')
  b.fact('case_context',s,f'A {age}-year-old male','Human mesocolon solitary fibrous tumor case',case_kind='clinical_case',species='human')
  b.fact('demographics',s,f'A {age}-year-old male',f'Age {age}',attribute='age_at_presentation',numeric_value=age,unit='years');b.fact('demographics',s,f'A {age}-year-old male','Documented male',attribute='documented_sex',text_value='male')
  if i==1:
   sym(b,s,'a gradually progressive abdominal lump over two months.','Gradually progressive abdominal lump',time='over two months')
   sym(b,s,'intermittent episodes of non-bilious vomiting','Intermittent non-bilious vomiting')
   sym(b,s,'lost 8 kg of weight','Weight loss 8 kg',numeric_value=8,unit='kg')
   cond(b,s,'bilateral craniotomy for meningioma in 2009 and 2014','Meningioma',role='history',temporality='historical')
   for year in ['2009','2014']:proc(b,s,'He underwent bilateral craniotomy for meningioma in 2009 and 2014.','Bilateral craniotomy',time=year,action='historical',laterality='bilateral',indication='meningioma')
   obs(b,s,'an 8×10 cm firm to hard palpable mass was detected in the left hypochondrium.','Physical examination abdominal mass',kind='other',text_value='8×10 cm, firm to hard, left hypochondrium',unit='cm')
   obs(b,s,'a hypoechoic solid mass was found in the pancreatic body and tail.','Abdominal ultrasound',kind='imaging',text_value='hypoechoic solid mass pancreatic body/tail',method='USG')
   obs(b,s,'a heterogeneously enhancing mass (16.7 cm craniocaudal, 12.3 cm anteroposterior, 13.4 cm transverse) with lobulated margins and central necrotic components occupying the epigastric and left hypochondriac and left lumbar region','CECT abdominal mass',kind='imaging',text_value='16.7 cm craniocaudal ×12.3 cm anteroposterior ×13.4 cm transverse; heterogeneous enhancement; lobulated; central necrosis; epigastric/left hypochondriac/left lumbar',method='CECT')
   obs(b,s,'An upper gastrointestinal endoscopy showed an extrinsic impression over the body of the stomach.','Upper GI endoscopy',kind='other',text_value='Extrinsic impression over gastric body')
   for n in ['solid pseudo epithelial neoplasm','gastrointestinal stromal tumor (GIST)','neuroendocrine tumor (NET)']:cond(b,s,'The differential diagnosis included solid pseudo epithelial neoplasm, gastrointestinal stromal tumor (GIST), and neuroendocrine tumor (NET).',n,verification_status='differential',assertion='possible',role='differential')
   proc(b,s,'A core biopsy was recommended to confirm the diagnosis.','Core biopsy',action='planned')
   bx=proc(b,s,'the core biopsy revealed an SFT','Core biopsy',finding='solitary fibrous tumor')
   for n in ['CD34','STAT-6']:bio(b,s,'cluster of differentiation-34 [CD34] and signal transducer and activator of transcription 6 [STAT-6] Positive',n,'positive',assay='IHC',specimen='core biopsy')
   obs(b,s,'A large tumor in the lesser sac which appeared to arise from the pancreas involving the transverse colon, mesentery of the transverse colon, and the splenic vein was detected intra-operatively.','Intraoperative tumor extent',kind='other',text_value='Large lesser-sac tumor, apparent pancreatic origin, involving transverse colon/mesentery and splenic vein')
   obs(b,s,'The tumor was free of the splenic artery, portal, and superior mesenteric vein.','Intraoperative uninvolved vessels',kind='other',text_value='Free of splenic artery, portal and superior mesenteric vein',flag='negative')
   ops=[]
   for n in ['distal pancreatectomy','splenectomy','segmental colonic resection','colo-colic anastomosis']:ops.append(proc(b,s,'The patient underwent distal pancreatectomy and splenectomy with segmental colonic resection and colo-colic anastomosis',n))
   for q,n in [('spindle-shaped tumor cells with elongated nuclei','Spindle tumor cells with elongated nuclei'),('some areas of myxoid degeneration','Myxoid degeneration'),('minimal mitoses','Minimal mitoses')]:obs(b,s,q,n,kind='pathology',text_value=q)
   f=b.fact('oncology',s,'seen growing from the transverse colon mesentery','Solitary fibrous tumor of transverse colon mesentery',tumor_ref='t1',name='solitary fibrous tumor',primary_site='transverse colon mesentery');b.p['facts'][-1]['collection']='tumors'
   for n in ['CD34','STAT-6']:bio(b,s,'tumor cells were positive for CD34 and STAT-6',n,'positive',assay='IHC',specimen='resection specimen')
   for n in ['smooth muscle actin','Ki67','CD117']:bio(b,s,'The tumor cells were negative for smooth muscle actin, Ki67, and cluster of differentiation 117 (CD117).',n,'negative',assay='IHC',specimen='resection specimen',certainty='source_conflict' if n=='Ki67' else 'supported',scoring='review_only' if n=='Ki67' else 'include',notes='Caption describes occasional Ki-67-positive nuclei.' if n=='Ki67' else '')
   for q,n in [('closely packed cells with little intervening collagenous stroma','Closely packed cells with little intervening stroma'),('oval to slightly elongated bland nuclei with fine pale chromatin and inconspicuous nucleoli','Bland oval/elongated nuclei with fine pale chromatin and inconspicuous nucleoli'),('scant to moderate, pale eosinophilic with indistinct borders','Scant/moderate pale eosinophilic cytoplasm with indistinct borders'),('prominent hyalinised (keloid-like) collagenous stroma with relatively few tumor cells','Hypocellular hyalinised collagenous areas')]:obs(b,'b00013',q,n,kind='pathology',text_value=q,notes='Caption-documented; pixels not inspected.')
   bio(b,'b00013','Ki-67 labels occasional tumor cell nuclei indicating low proliferative activity within the tumor','Ki-67','positive',assay='IHC',text_value='occasional positive nuclei, low proliferative activity',certainty='source_conflict',scoring='review_only',notes='Main narrative calls Ki67 negative.')
   b.p['ambiguities'].append({'issue':'Main text calls Ki67 negative; figure 2E caption states occasional positive nuclei/low proliferative activity. No zero index inferred. Apparent pancreatic origin intraoperatively differs from final mesocolon origin; preserve assessment context.','evidence':[{'segment_id':s,'quote':'negative for smooth muscle actin, Ki67'},{'segment_id':'b00013','quote':'Ki-67 labels occasional tumor cell nuclei'}]})
   no=out(b,s,'There was no tumor recurrence observed at the 36-month follow-up.','recurrence',assertion='absent',time='at the 36-month follow-up',follow_up_duration_text='36 months')
   ev=[b.event('Diagnostic core biopsy',[bx]),b.event('Definitive resections and anastomosis',ops),b.event('No recurrence at follow-up',[no],'at the 36-month follow-up')]
  else:
   for n in ['abdominal fullness','constipation','anorexia']:sym(b,s,'abdominal fullness associated with constipation and anorexia for three months',n,time='for three months')
   sym(b,s,'a 9 kg weight loss','Weight loss 9 kg',numeric_value=9,unit='kg')
   obs(b,s,'A firm palpable non-tender mass occupying the hypogastric and umbilical regions of the abdomen with side-to-side mobility','Clinical abdominal mass',kind='other',text_value='firm, palpable, non-tender, hypogastric/umbilical, side-to-side mobility')
   obs(b,s,'On rectal examination, there was fullness at the 6 óclock position, and normal mucosa was felt.','Rectal examination',kind='other',text_value='fullness at 6 óclock, normal mucosa')
   obs(b,s,'A USG of the abdomen revealed a large mixed echogenic mass in the hypogastric region.','Abdominal ultrasound',kind='imaging',text_value='Large mixed echogenic hypogastric mass',method='USG')
   obs(b,s,'a large heterogeneous, enhancing (size: 18 cm craniocaudal, 15 cm anteroposterior, 12 cm transverse), well-circumscribed mass with lost fat planes and left lateral sigmoid colon','Abdominal/pelvic CECT',kind='imaging',text_value='18 cm craniocaudal ×15 cm AP ×12 cm transverse; heterogeneous enhancing, well circumscribed; lost fat planes with left lateral sigmoid colon',method='CECT',notes='Source syntax "lost fat planes and left lateral sigmoid colon" is awkward; no invasion confirmed.')
   obs(b,s,'The bowel loops were displaced peripherally and the urinary bladder anteriorly.','CECT organ displacement',kind='imaging',text_value='Bowel peripheral, urinary bladder anterior')
   obs(b,s,'Colonoscopy revealed a normal rectosigmoid mucosa.','Colonoscopy',kind='other',text_value='normal rectosigmoid mucosa',flag='normal')
   for n in ['GIST','soft tissue sarcoma']:cond(b,t,'GIST and soft tissue sarcoma were considered in the differential diagnosis.',n,assertion='possible',verification_status='differential',role='differential')
   obs(b,t,'Intra-operatively, a large tumor was seen in the rectosigmoid mesentery.','Intraoperative tumor location',kind='other',text_value='Large tumor rectosigmoid mesentery')
   ops=[proc(b,t,'The patient underwent a rectosigmoid resection and Hartman’s procedure','Rectosigmoid resection'),proc(b,t,'The patient underwent a rectosigmoid resection and Hartman’s procedure','Hartman’s procedure')]
   med(b,t,'The patient required multiple packed red blood cell transfusions during surgery.','packed red blood cells',time='during surgery',dose_text='multiple transfusions')
   for q,n in [('an encapsulated tumor','Encapsulated tumor'),('hypercellular and hypocellular areas with hemangiopericytomatous patterns','Hypercellular/hypocellular areas with hemangiopericytomatous pattern')]:obs(b,t,q,n,kind='pathology',text_value=q)
   for n in ['CD34','STAT6']:bio(b,t,'the tumor was positive for CD34 and STAT6 with 2% Ki67',n,'positive',assay='IHC')
   bio(b,t,'2% Ki67','Ki67','positive',assay='IHC',numeric_value=2,unit='%')
   f=b.fact('oncology',t,'a large tumor was seen in the rectosigmoid mesentery','Solitary fibrous tumor of rectosigmoid mesentery',name='solitary fibrous tumor',tumor_ref='t1',primary_site='rectosigmoid mesentery');b.p['facts'][-1]['collection']='tumors'
   obs(b,'b00015','Cells arranged in fascicles separated by collagen','Histologic fascicles separated by collagen',kind='pathology',text_value='Cells arranged in fascicles separated by collagen',notes='Caption-documented, pixels not inspected.')
   no=out(b,t,'At the 24 months follow-up, no local or systemic recurrence was noted.','recurrence',assertion='absent',time='At the 24 months follow-up',follow_up_duration_text='24 months')
   ev=[b.event('Rectosigmoid resection and Hartman procedure',ops),b.event('No local or systemic recurrence',[no],'At the 24 months follow-up')]
  out(b,t,'The post-operative course was uneventful.','other')
  cancer_tx(b,t,'The patient did not receive any adjuvant therapy.' if i==1 else 'The patient did not receive adjuvant therapy.','adjuvant therapy','unknown',action='unknown',assertion='absent')
  for n in ['carcinoembryonic antigen','cancer antigen 19-9 (CA 19-9)']:
   b.fact('observations','b00008','evaluation of pre-operative tumor markers such as the carcinoembryonic antigen or cancer antigen 19-9 (CA 19-9) was not performed',n+' testing not performed',name=n,kind='laboratory',status='not_performed',result_absent_reason='not_performed',time='pre-operative')
  out(b,'b00010','were followed up along with their imaging reports via phone calls and instant messaging platforms.','follow_up',disposition='remote follow-up by phone and instant messaging')
  b.fact('care_plans','b00010','we planned for close long-term follow- up of our patients.','Close long-term follow-up planned',action_text='Close long-term follow-up',plan_type='follow_up',action_status='planned')
  for x,y in zip(ev,ev[1:]):b.p['temporal_relations'].append({'from_event_id':x,'to_event_id':y,'relation':'before','evidence':[{'segment_id':t,'quote':b.seg[t]['text']}]})
  b.finish_patient();owns[s]=[f'p{i}'];owns[t]=[f'p{i}']
 for sid in ['b00001','b00002','b00008','b00010']:owns[sid]=['p1','p2']
 owns.update({'b00012':['p1'],'b00013':['p1'],'b00014':['p2'],'b00015':['p2']});b.a['article_limitations'].append('Literature Table 1 is mentioned but not included in retained source segments. No inference of malignant histology from size alone; cited literature aggregate statistics are excluded.');b.save(owns)
 path=BASE/'drafts'/'PMC12285374.1.json';a=json.loads(path.read_text());maps=[[('A','CECT heterogeneously enhancing lobulated centrally necrotic mass','CT'),('B','Distal pancreas, spleen and resected colon specimen','gross specimen')],[('A','Cellular area morphology ×400','H&E'),('B','Hypocellular hyalinised collagenous area ×400','H&E'),('C','CD34 diffuse cytoplasmic stain ×400','IHC'),('D','STAT6 nuclear/some cytoplasmic stain ×400','IHC'),('E','Occasional Ki-67 tumor nuclei, low proliferation ×400','IHC')],[('A','Pelvic heterogeneous enhancing mass','CT'),('B','Rectosigmoid specimen with adjacent tumor','gross specimen')],[('A','Fascicles separated by collagen ×100','H&E'),('B','Hemangiopericytomatous cellular lesion ×400','histology'),('C','STAT6 nuclear positivity ×400','IHC'),('D','CD34 tumor positivity ×400','IHC'),('E','Low Ki67 index ×400','IHC')]]
 for f,m in zip(a['figures'],maps):f['panels']=[{'panel':p,'patient_ids':f['patient_ids'],'scope':'individual','description_from_caption':d,'modality':mod,'body_part':None,'evidence':f['caption_evidence']} for p,d,mod in m]
 path.write_text(json.dumps(a,ensure_ascii=False,indent=2)+'\n')
def article_13618765():
 b=Build('PMC13618765.1');s='b00004';b.patient('39-year-old female physiotherapist',s,'A 39-year-old female physiotherapist')
 b.fact('case_context',s,'A 39-year-old female physiotherapist presented to our clinic','Human outpatient IAWS clinical case',case_kind='clinical_case',species='human',care_setting='clinic')
 b.fact('demographics',s,'A 39-year-old female physiotherapist','Age 39 years',attribute='age_at_presentation',numeric_value=39,unit='years');b.fact('demographics',s,'A 39-year-old female physiotherapist','Documented female',attribute='documented_sex',text_value='female')
 b.fact('social_exposures',s,'female physiotherapist','Occupation physiotherapist',domain='occupation',name='physiotherapist')
 for n in ['left shoulder pain','axillary swelling']:sym(b,s,'left shoulder pain and axillary swelling for three days.',n,time='for three days')
 cond(b,s,'There was no history of trauma to the shoulder or upper extremity.','Shoulder or upper extremity trauma',assertion='absent',role='history')
 for n in ['neck pain','paresthesia']:b.fact('symptoms_function',s,'The patient did not report any neck pain or paresthesia related to her left shoulder symptoms.',n+' not reported',name=n,kind='symptom',assertion='absent')
 for n in ['surgical intervention','radiotherapy']:proc(b,'b00003','without any history of surgical intervention, trauma, or radiotherapy',n,action='historical',assertion='absent')
 obs(b,s,'Systemic laboratory tests were performed and were within normal limits.','Systemic laboratory tests',text_value='within normal limits',flag='normal')
 cord=obs(b,s,'a cord-like and firm fibrous structure extending from the left axilla to the medial aspect of the arm','Physical examination fibrous cord',kind='other',text_value='Firm cord from left axilla to medial arm',body_site='left axilla and medial arm')
 b.fact('symptoms_function',s,'There was no tenderness to palpation.','No palpation tenderness',name='tenderness to palpation',kind='sign',assertion='absent')
 for q,n in [('flexion to 170 degrees','Left shoulder active flexion'),('abduction to 170 degrees','Left shoulder active abduction')]:obs(b,s,q,n,kind='physiologic',numeric_value=170,unit='degrees',body_site='left shoulder')
 obs(b,s,'Visual Analog Scale (VAS) pain intensity of 5 (0 = no pain; 10 = very severe pain)','Baseline VAS',kind='other',numeric_value=5,reference_range_text='0 = no pain; 10 = very severe pain')
 obs(b,s,'Doppler ultrasonography did not detect blood flow within the cords, and no pathology suggestive of thrombosis was observed.','Doppler ultrasonography',kind='imaging',text_value='No blood flow within cords; no pathology suggestive of thrombosis',flag='negative')
 for n in ['sedimentation rate (ESR)','C-reactive protein (CRP)','complete blood count']:obs(b,s,'Routine blood tests, including sedimentation rate (ESR), C-reactive protein (CRP), and complete blood count, were within normal limits.',n,text_value='within normal limits',flag='normal')
 for n in ['chest X-ray','mammography','breast ultrasound']:obs(b,s,'chest X-ray, mammography, and breast ultrasound showed no pathological findings.',n,kind='imaging',text_value='No pathological findings',flag='normal')
 cond(b,s,'The patient was diagnosed with IAWS','Idiopathic axillary web syndrome')
 tx=med(b,s,'was prescribed acemetacin 60 mg b.i.d. as a non-steroidal anti-inflammatory drug treatment.','acemetacin',action='ordered',dose_value=60,dose_unit='mg',frequency='b.i.d.',indication='IAWS')
 plan=proc(b,s,'A 15-session physical therapy program was planned, consisting of therapeutic ultrasound, transcutaneous electrical nerve stimulation (TENS), shoulder range of motion exercises, stretching and strengthening exercises, and massage.','15-session physical therapy',action='planned',kind='nonpharmacologic_therapy')
 performed=[]
 for n in ['shoulder range of motion exercises','stretching exercises','strengthening exercises','TENS','therapeutic ultrasound','massage']:performed.append(proc(b,'b00008','we applied a total of 15 sessions including shoulder range of motion, stretching and strengthening exercises, TENS, therapeutic ultrasound, and massage.',n,kind='nonpharmacologic_therapy'))
 out(b,s,'Following the treatment, her symptoms dramatically improved','improvement',time='Following the treatment')
 obs(b,s,'the patient’s shoulder range of motion was entirely restored','Posttreatment shoulder range of motion',time='Following the treatment',kind='physiologic',text_value='entirely restored')
 obs(b,s,'VAS score was 1.','Posttreatment VAS',time='Following the treatment',kind='other',numeric_value=1)
 out(b,'b00008','Although the fibrotic band was not completely resolved after treatment, a significant reduction in symptoms was achieved.','improvement',time='after treatment',notes='Incomplete cord resolution immediately after treatment coexists with complete resolution at two months.')
 resolution=out(b,s,'At two months of follow-up, the fibrotic cord had completely resolved','recovery',time='At two months of follow-up',follow_up_duration_text='two months')
 out(b,'b00001','After two months of follow-up, the patient’s symptoms completely resolved.','recovery',time='After two months of follow-up')
 obs(b,'b00006','In our case, a reactive lymph node measuring 23x8 mm was observed in the axillary region.','Reactive axillary lymph node',kind='imaging',text_value='23x8 mm reactive lymph node',unit='mm',body_site='axilla')
 obs(b,'b00006','The skin and subcutaneous tissue appeared normal on imaging.','Skin and subcutaneous tissue imaging',kind='imaging',text_value='normal',flag='normal')
 obs(b,'b00006','No pathology was found in the subcutaneous tissue, and no hyperechoic or hypoechoic appearance corresponding to the chordae reported in previous studies was detected.','Subcutaneous tissue/cord imaging',kind='imaging',text_value='No subcutaneous pathology or hyperechoic/hypoechoic cord appearance',flag='negative')
 ev=[b.event('Baseline fibrotic cord finding',[cord]),b.event('Acemetacin ordered and PT planned',[tx,plan],occurrence='planned'),b.event('15-session PT performed',performed),b.event('Cord resolved at two months',[resolution],'At two months of follow-up')]
 for x,y in zip(ev,ev[1:]):b.p['temporal_relations'].append({'from_event_id':x,'to_event_id':y,'relation':'before','evidence':[{'segment_id':s,'quote':b.seg[s]['text']},{'segment_id':'b00008','quote':'we applied a total of 15 sessions'}]})
 b.p['ambiguities'].append({'issue':'Acemetacin route/duration not documented; physical therapy completion supported by discussion, though initially planned. No histologic proof of thrombosed cords inferred from background. Incomplete band resolution after treatment precedes complete resolution at two months.','evidence':[{'segment_id':'b00008','quote':'Although the fibrotic band was not completely resolved after treatment'},{'segment_id':s,'quote':'At two months of follow-up, the fibrotic cord had completely resolved'}]})
 b.finish_patient();b.a['article_limitations'].append('Previously reported patients and their outcome scores in discussion are secondary cases, excluded. Literature table mentioned but absent from retained segments.');b.save({f'b000{i:02d}':['p1'] for i in [1,3,4,6,8,10,11]})
def article_13624890():
 b=Build('PMC13624890.1');s='b00004';b.patient('68-year-old man with genital LSA',s,'A 68-year-old man')
 b.fact('case_context',s,'A 68-year-old man','Human dermatologic clinical case',case_kind='clinical_case',species='human')
 b.fact('demographics',s,'A 68-year-old man','Age 68 years',attribute='age_at_presentation',numeric_value=68,unit='years');b.fact('demographics',s,'A 68-year-old man','Documented man',attribute='documented_sex',text_value='man')
 cond(b,s,'a previous diagnosis of genital lichen sclerosus et atrophicus (LSA)','Genital lichen sclerosus et atrophicus',role='history',temporality='historical')
 sym(b,s,'a recent-onset genital erosion refractory to topical treatment.','Recent genital erosion refractory to topical treatment',time='recent-onset')
 med(b,s,'refractory to topical treatment','Unspecified topical treatment',action='historical',route='topical',notes='Name/dose not stated; treatment failure retained.')
 obs(b,s,'an erosive and slightly hyperkeratotic plaque measuring 15×8 mm, located just next to the urethral meatus','Dermatologic plaque',kind='other',text_value='15×8 mm erosive slightly hyperkeratotic plaque next to urethral meatus',unit='mm',body_site='adjacent urethral meatus')
 sym(b,s,'a whitish and sclerotic appearance of the penis','Whitish sclerotic penis appearance')
 b.fact('symptoms_function',s,'There were no other lesions in the intertriginous areas.','No other intertriginous lesions',name='other intertriginous lesions',kind='sign',assertion='absent')
 b.fact('family_genetics',s,'Familiar history of similar eruptions was excluded','No family history of similar eruptions',name='similar eruptions',kind='family_condition',assertion='absent',subject='family_member')
 b.fact('family_genetics','b00006','without any family history of oral or cutaneous lesions','No family history of oral or cutaneous lesions',name='oral or cutaneous lesions',kind='family_condition',assertion='absent',subject='family_member')
 for n in ['serology for sexually transmitted infectious diseases','human papillomavirus test','direct microscopic examination for fungi']:obs(b,s,'serology for sexually transmitted infectious diseases, human papillomavirus test, and direct microscopic examination for fungi were negative.',n,text_value='negative',flag='negative',kind='microbiology' if n!='serology for sexually transmitted infectious diseases' else 'laboratory')
 cond(b,s,'In the suspicion of a squamous cell carcinoma arising on LSA','Squamous cell carcinoma arising on LSA',verification_status='provisional',assertion='possible')
 bx=proc(b,s,'two punch biopsies were performed.','Two punch biopsies',body_site='penile plaque/whitish sclerotic surrounding area')
 for q,n in [('intense papillary edema, producing a dermolytic subepidermal bulla','Papillary edema with dermolytic subepidermal bulla'),('homogeneous sclerotic collagen with patchy lymphocytic infiltrates in the upper reticular dermis','Homogeneous sclerotic collagen/patchy lymphocytes in upper reticular dermis')]:obs(b,s,q,n,kind='pathology',specimen='whitish sclerotic area surrounding erosion',text_value=q)
 cond(b,s,'confirmed the classical histopathological features of LSA','Histologically confirmed lichen sclerosus et atrophicus',diagnostic_basis='punch biopsy of whitish sclerotic area')
 for q,n in [('epidermal hyperplasia','Erosion biopsy epidermal hyperplasia'),('suprabasal clefting','Erosion biopsy suprabasal clefting'),('“dilapidated brick wall” acantholysis','Dilapidated brick-wall acantholysis')]:obs(b,s,q,n,kind='pathology',specimen='erosion punch biopsy',text_value=q)
 obs(b,s,'intraepidermal intercellular C3 and IgG deposition with a “net-like” pattern','Direct immunofluorescence',kind='pathology',method='DIF',text_value='intraepidermal intercellular C3/IgG net-like deposition',flag='positive',specimen='erosion biopsy')
 for n in ['anti-Dsg1 antibodies','anti-Dsg3 antibodies']:obs(b,s,'specific anti-skin autoantibodies, particularly anti-Dsg1 and anti-Dsg3 antibodies, was negative',n,text_value='negative',flag='negative')
 obs(b,s,'as was indirect immunofluorescence.','Indirect immunofluorescence',text_value='negative',flag='negative',notes='"as was" refers to preceding negative autoantibody result.')
 dx=cond(b,s,'A diagnosis of PAD arising in the context of LSA was proposed for this case.','Papular acantholytic dyskeratosis arising in LSA',verification_status='provisional')
 b.fact('symptoms_function','b00005','the characteristic presentation of multiple papules is lacking','Multiple papules absent',name='multiple papules',kind='sign',assertion='absent')
 cond(b,'b00006','suggesting a possible pemphigus vulgaris','Pemphigus vulgaris',assertion='possible',verification_status='differential',role='differential')
 obs(b,'b00006','intraepidermal eosinophilic abscesses, which were absent in our case.','Intraepidermal eosinophilic abscesses',kind='pathology',text_value='absent',flag='negative')
 obs(b,'b00007','the presence of dyskeratosis associated with prominent suprabasal intraepidermal acantholysis','Dyskeratosis and prominent suprabasal intraepidermal acantholysis',kind='pathology',text_value='present')
 obs(b,'b00007','the absence of prominent inflammation','Prominent inflammation',kind='pathology',text_value='absent',flag='negative')
 cond(b,'b00007','excluded the diagnosis of pemphigus in our case.','Pemphigus',assertion='absent',verification_status='refuted')
 tx=med(b,'b00009','topical treatment with diclofenac sodium 3% gel','diclofenac sodium 3% gel',route='topical',dose_text='3% gel')
 out(b,'b00009','was well-tolerated by the patient','other',related_condition='diclofenac sodium treatment tolerability')
 end=out(b,'b00009','led to complete remission of the disease.','recovery',related_condition='papular acantholytic dyskeratosis')
 ev=[b.event('Two diagnostic punch biopsies',[bx]),b.event('Proposed PAD diagnosis',[dx]),b.event('Topical diclofenac treatment',[tx]),b.event('Complete remission',[end])]
 for x,y in zip(ev,ev[1:]):b.p['temporal_relations'].append({'from_event_id':x,'to_event_id':y,'relation':'before','evidence':[{'segment_id':s,'quote':b.seg[s]['text']},{'segment_id':'b00009','quote':b.seg['b00009']['text']}]})
 b.p['ambiguities'].append({'issue':'PAD diagnosis explicitly proposed in atypical single-plaque presentation. Histologic resemblance to Hailey-Hailey disease does not establish HHD or a patient ATP2C1/ATP2A2 variant. Prior topical treatment and duration/frequency of diclofenac are unstated.','evidence':[{'segment_id':s,'quote':'A diagnosis of PAD arising in the context of LSA was proposed'},{'segment_id':s,'quote':'which was indistinguishable from HHD'}]})
 b.finish_patient();b.save({f'b000{i:02d}':['p1'] for i in [4,5,6,7,9,10,12,13]})
 path=BASE/'drafts'/'PMC13624890.1.json';a=json.loads(path.read_text());maps=[[('A','Whitish discoloration and sclerotic penis appearance','clinical photograph'),('B','Erosive slightly hyperkeratotic glans lesion','clinical photograph')],[('A','Papillary edema/subepidermal bulla and sclerotic collagen with patchy lymphocytes','histopathology'),('B','Epidermal hyperplasia, suprabasal clefting, brick-wall acantholysis; inset panoramic same specimen','histopathology'),('C','Net-like intercellular epidermal IgG','DIF')]]
 for f,m in zip(a['figures'],maps):f['panels']=[{'panel':p,'patient_ids':['p1'],'scope':'individual','description_from_caption':d,'modality':mod if mod!='clinical photograph' else None,'body_part':'penis','evidence':f['caption_evidence']} for p,d,mod in m]
 path.write_text(json.dumps(a,ensure_ascii=False,indent=2)+'\n')
def article_13624771():
 b=Build('PMC13624771.1');s='b00003';b.patient('82-year-old woman undergoing gastric ESD',s,'An 82-year-old woman')
 b.fact('case_context',s,'An 82-year-old woman','Human gastric endoscopic procedure case',case_kind='clinical_case',species='human')
 b.fact('demographics',s,'An 82-year-old woman','Age 82 years',attribute='age_at_presentation',numeric_value=82,unit='years');b.fact('demographics',s,'An 82-year-old woman','Documented woman',attribute='documented_sex',text_value='woman')
 obs(b,s,'a 20-mm depressed lesion','Depressed gastric lesion',kind='other',numeric_value=20,unit='mm',text_value='depressed lesion')
 obs(b,'b00007','A 20-mm lesion was located at the greater curvature of the lower gastric body.','Gastric lesion location',kind='imaging',text_value='Greater curvature of lower gastric body',body_site='greater curvature lower gastric body',notes='Caption localization; lesion histology unstated.')
 esd=proc(b,s,'ESD was performed using an EG-840TP with the device set to down-angulation direction.','Endoscopic submucosal dissection',body_site='greater curvature lower gastric body')
 proc(b,s,'ESD was performed using an EG-840TP with the device set to down-angulation direction.','EG-840TP therapeutic endoscope with AttachBend',kind='device',action='device_present',finding='device set to down-angulation')
 proc(b,s,'the device was covered with a transparent film','Transparent film covering of external endoscope device',kind='device',action='device_present',indication='smooth surface for insertion')
 proc(b,s,'even after multiple reinsertions','Repeated endoscope reinsertions')
 for n in ['pharyngeal injuries','esophageal injuries']:out(b,s,'no pharyngeal or esophageal injuries were observed.','complication',assertion='absent',related_condition=n)
 obs(b,s,'Combining the down-angulation of the second bend with the up-angulation of the scope created an S-shaped configuration that maintained several centimeters of the shaft in continuous mucosal contact','Endoscope positioning',kind='other',text_value='Second bend down-angulation plus scope up-angulation; S-shaped shaft with several centimeters continuous mucosal contact')
 out(b,s,'This setup significantly enhanced the scope stability and minimized the effects of respiratory movements.','other')
 flap=proc(b,s,'A mucosal flap was created on the oral side','Oral-side mucosal flap creation')
 inc=proc(b,s,'followed by a circumferential incision.','Circumferential mucosal incision')
 dissect=proc(b,s,'Submucosal dissection was performed in the forward view.','Forward-view submucosal dissection')
 resection=out(b,s,'The lesion was successfully resected en bloc without adverse events.','other',text_value='') if False else out(b,s,'The lesion was successfully resected en bloc without adverse events.','recovery')
 out(b,s,'without adverse events.','complication',assertion='absent')
 ev=[b.event('Mucosal flap creation',[flap]),b.event('Circumferential incision',[inc]),b.event('Forward-view submucosal dissection',[dissect]),b.event('Successful en bloc resection',[resection])]
 for x,y in zip(ev,ev[1:]):b.p['temporal_relations'].append({'from_event_id':x,'to_event_id':y,'relation':'before','evidence':[{'segment_id':s,'quote':'A mucosal flap was created on the oral side, followed by a circumferential incision. Submucosal dissection was performed in the forward view. The lesion was successfully resected en bloc without adverse events.'}]})
 b.p['ambiguities'].append({'issue':'No pathology or oncologic diagnosis, follow-up duration, sedative regimen, or prior history documented. En bloc removal alone does not prove cancer or curative margins. Device example Figure 1 and comparison Figure 3A are not asserted as actual patient-specific pretreatment findings.','evidence':[{'segment_id':s,'quote':'a 20-mm depressed lesion'},{'segment_id':'b00008','quote':'Comparison of scope positioning with and without the use of the novel device.'}]})
 b.finish_patient();b.a['article_limitations'].append('Linked video not inspected; narrative and figure captions only.');b.save({'b00002':['p1'],'b00003':['p1'],'b00004':['p1'],'b00007':['p1'],'b00008':['p1']})
 path=BASE/'drafts'/'PMC13624771.1.json';a=json.loads(path.read_text());f=a['figures'][0];f['scope']='background';f['patient_ids']=[];f['attribution_notes']='Device demonstration; no patient attribution in caption.';f['panels']=[{'panel':p,'patient_ids':[],'scope':'background','description_from_caption':d,'modality':None,'body_part':None,'evidence':f['caption_evidence']} for p,d in [('left','Control lever mounted on endoscope handle'),('upper right','Device attached in up-angulation'),('lower right','Device attached in down-angulation')]]
 f=a['figures'][1];f['panels'][0]['body_part']='greater curvature of lower gastric body';f['panels'][0]['modality']=None
 f=a['figures'][2];f['scope']='unresolved';f['attribution_notes']='Panel B matches described patient setup; panel A conventional comparison not proven performed on this patient.';f['panels']=[{'panel':'A','patient_ids':[],'scope':'background','description_from_caption':'Conventional scope tip floats, limited mucosal contact','modality':None,'body_part':'stomach','evidence':f['caption_evidence']},{'panel':'B','patient_ids':['p1'],'scope':'individual','description_from_caption':'Device down-angulation plus upward scope angulation increases mucosal contact and stable horizontal view','modality':None,'body_part':'stomach','evidence':f['caption_evidence']}]
 path.write_text(json.dumps(a,ensure_ascii=False,indent=2)+'\n')
