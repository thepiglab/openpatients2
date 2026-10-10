"""Sequential dense original-source annotation for thoracobiliary fistulae."""
from annotator1_build import Build
import json
from pathlib import Path
phase_metadata={}
b=Build('PMC12702475.1');own={f'b{i:05d}':['p1','p2'] for i in [1,5,46,48,50,51]};panels={}
for i in range(7,25):own[f'b{i:05d}']=['p1']
for i in range(26,45):own[f'b{i:05d}']=['p2']
for i in range(54,57):own[f'b{i:05d}']=['p1']
for i in range(57,61):own[f'b{i:05d}']=['p2']
for case,age,sex,start in [(1,50,'male','b00007'),(2,36,'female','b00026')]:
 b.patient(f'Case{case}: {age}-year-old {sex} with postoperative thoracobiliary fistula',start,f'A {age}-year-old '+('male' if case==1 else 'Afghan female'));p=b.p;phases={};current='presentation';kinds={};times={}
 def phase(key,kind,time=None):
  global current
  current=key;phases.setdefault(key,[]);kinds[key]=kind;times[key]=time
 def add(fn,*args,**kw):
  fid=fn(*args,**kw);phases[current].append(fid);return fid
 def fact(task,s,q,statement,**kw):return add(b.fact,task,s,q,statement,**kw)
 def sym(s,q,name,**kw):return add(b.sym,s,q,name,**kw)
 def obs(s,q,name,**kw):return add(b.obs,s,q,name,**kw)
 def cond(s,q,name,**kw):return add(b.cond,s,q,name,**kw)
 def med(s,q,name,**kw):return add(b.med,s,q,name,**kw)
 def proc(s,q,name,**kw):return add(b.proc,s,q,name,**kw)
 def out(s,q,event,**kw):return add(b.out,s,q,event,**kw)
 def table(sid,name,value,unit=None,specimen=None,flag=None):
  line=b.seg[sid]['text'].splitlines()[-1];parts=line.split(' | ');ref=parts[-1] if len(parts)>2 else ''
  kw=dict(numeric_value=value,unit=unit,text_value=parts[1],reference_range_text=ref or None)
  if specimen:kw['specimen']=specimen
  if flag:kw['flag']=flag
  return obs(sid,line,name,**kw)
 phase('presentation','presentation','August 7, 2024' if case==1 else 'February 10, 2025')
 context=fact('case_context',start,f'A {age}-year-old '+('male' if case==1 else 'Afghan female'),'Index human surgical case',case_kind='clinical_case',species='human',multiple_index_patients=True,subject_state='living_at_presentation',index_subject_description=f'{age}-year-old {sex} with persistent postoperative thoracobiliary fistula symptoms',care_setting='Hepatobiliary Unit, Shaikh Zayed Hospital, Lahore',chief_complaint='chronic biliptysis and progressive symptoms after prior hepatobiliary surgery' if case==1 else 'two-month dyspnea, dry cough, intermittent low-grade fever and right pleuritic chest pain')
 p['facts'][-1]['evidence']+=[dict(segment_id='b00001',quote='two patients treated at the Hepatobiliary Unit, Shaikh Zayed Hospital, Lahore.'),dict(segment_id=start,quote=b.seg[start]['text'])]
 fact('demographics',start,f'A {age}-year-old '+('male' if case==1 else 'Afghan female'),'Age at current reported case presentation',attribute='age_at_presentation',numeric_value=age,unit='years')
 fact('demographics',start,f'A {age}-year-old '+('male' if case==1 else 'Afghan female'),'Documented sex',attribute='documented_sex',text_value=sex)
 obs(start,'He presented to Shaikh Zayed Hospital on August 7, 2024, for further evaluation and management.' if case==1 else 'presented to Shaikh Zayed Hospital on February 10, 2025','Current presentation to reporting hospital',kind='other',text_value='Shaikh Zayed Hospital; August7,2024' if case==1 else 'Shaikh Zayed Hospital; February10,2025',time=times['presentation'])
 if case==1:
  phase('prior_hepatectomy','history','March 2022')
  cond(start,'with no significant past medical history','Significant prior medical history',assertion='absent',verification_status='unknown',role='history',temporality='historical',notes='Prior-history statement belongs before the2022procedure; does not negate subsequent hydatid disease or complications.')
  cond(start,'a large hydatid cyst involving the right hepatic lobe','Hydatid cyst',body_site='right hepatic lobe',laterality='right',role='history',temporality='historical',severity='large')
  proc(start,'underwent a right hemihepatectomy in March 2022 at a private facility for a large hydatid cyst involving the right hepatic lobe.','Right hemihepatectomy',time='March 2022',action='historical',temporality='historical',body_site='right hepatic lobe',laterality='right',indication='large right hepatic hydatid cyst')
  phase('old_injury_repair','adverse_event')
  cond(start,'The surgery was complicated by an iatrogenic diaphragmatic injury','Iatrogenic diaphragmatic injury',role='complication',temporality='historical',body_site='diaphragm')
  proc(start,'which was repaired intraoperatively with the placement of a chest tube.','Intraoperative diaphragmatic injury repair',action='historical',temporality='historical',body_site='diaphragm',indication='iatrogenic diaphragmatic injury')
  proc(start,'which was repaired intraoperatively with the placement of a chest tube.','Chest tube placement during prior operation',action='historical',temporality='historical',kind='device')
  phase('old_recovery_discharge','outcome','postoperative day 13')
  out(start,'The patient had an uneventful recovery','recovery',notes='AfterMarch2022hemihepatectomy, not the2024fistula repair.')
  out(start,'was discharged on postoperative day 13.','discharge',time='postoperative day 13',notes='Clock anchored toMarch2022hemihepatectomy, not2024currentrepair.')
  phase('symptom_onset','presentation','Approximately six weeks later')
  sym(start,'Approximately six weeks later, he developed a persistent cough with sputum production and intermittent fever.','Persistent cough',time='Approximately six weeks later',kind='symptom')
  sym(start,'Approximately six weeks later, he developed a persistent cough with sputum production and intermittent fever.','Sputum production',time='Approximately six weeks later',kind='symptom')
  sym(start,'Approximately six weeks later, he developed a persistent cough with sputum production and intermittent fever.','Intermittent fever',time='Approximately six weeks later',kind='symptom')
  phase('intervening_procedures','treatment','Over the next two years')
  proc(start,'Over the next two years, he underwent multiple interventional procedures (records unavailable)','Multiple prior interventional procedures',action='historical',temporality='historical',time='Over the next two years',notes='Records unavailable; specific technique/count/agents/results not invented.')
  out(start,'but continued to suffer from progressive symptoms','deterioration',text_value='progressive symptoms despite multiple interventions')
  phase('presentation','presentation','August 7, 2024')
  for q,n in [('chronic biliptysis','Chronic biliptysis'),('copious amounts of bile-stained sputum requiring frequent expectoration','Copious bile-stained sputum with frequent expectoration'),('loss of appetite','Loss of appetite')]:sym(start,q,n,kind='symptom')
  sym(start,'social embarrassment','Social embarrassment due to frequent bile expectoration',kind='quality_of_life')
  phase('weight_course','history')
  sym(start,'unintentional weight loss of 9 kg (from 65 kg to 56 kg)','Unintentional weight loss',kind='symptom',numeric_value=9,unit='kg')
  obs(start,'from 65 kg to 56 kg','Previously reported weight',kind='physiologic',numeric_value=65,unit='kg',text_value='from65kg; date unstated',notes='Weight-loss starting value, not current presentation weight.')
  obs(start,'from 65 kg to 56 kg','Current reported weight',kind='physiologic',numeric_value=56,unit='kg',text_value='to56kg')
  phase('presentation','presentation','August 7, 2024')
  for q,n,kw in [('thin and emaciated','Thin/emaciated appearance',{}),('reduced air entry','Reduced right lower lung air entry',dict(body_site='right lower lung zone')),('basal crepitations over the right lower lung zone','Right basal crepitations',dict(body_site='right lower lung zone')),('a scaphoid, non-tender abdomen','Scaphoid abdomen',dict(body_site='abdomen')),('a reverse L-shaped surgical scar','ReverseLshaped surgical scar',dict(body_site='abdomen'))]:sym('b00008',q,n,**kw)
  sym('b00008','non-tender abdomen','Abdominal tenderness',assertion='absent',body_site='abdomen')
  sym('b00008','no organomegaly','Organomegaly',assertion='absent',body_site='abdomen')
  obs('b00008','BMI 19 kg/m²','Body mass index',kind='physiologic',numeric_value=19,unit='kg/m²')
  phase('laboratory','test')
  cond('b00008','mild anemia (Hb 9.8 g/dl)','Anemia',severity='mild')
  labrows=[(10,'Hemoglobin',9.8,'g/dl','low'),(11,'Total leukocyte count',7.9,'10^9/L','normal'),(12,'Platelets',220,'10^9/L','normal'),(13,'INR',1.1,None,None),(14,'Total bilirubin',2.1,'mg/dl','high'),(15,'Direct bilirubin',1.1,'mg/dl','high'),(16,'Alkaline phosphatase',252,'U/L','high'),(17,'ALT',51,'U/L',None),(18,'AST',75,'U/L',None),(19,'Albumin',3,'g/dl',None)]
  for sn,name,v,u,flag in labrows:table(f'b{sn:05d}',name,v,u,flag=flag)
  for sn,name in [(20,'Anti-HCV'),(21,'HbsAg'),(22,'Anti-HIV')]:
   ss=f'b{sn:05d}';obs(ss,b.seg[ss]['text'].splitlines()[-1],name,text_value='Negative',flag='negative',method='viral serology')
  f=table('b00023','Sputum bilirubin',1.1,'mg/dl',specimen='sputum',flag='high');p['facts'][-1]['evidence'].append(dict(segment_id='b00008',quote='Sputum bilirubin was elevated at 1.1 mg/dl.'))
  phase('imaging','test')
  med('b00024','A CT scan of the chest, abdomen, and pelvis with IV contrast','Unspecified IV contrast',route='intravenous',indication='CT chest/abdomen/pelvis',notes='Agent identity anddose notstated; noiodinatedagenttype inferred.')
  ct='A CT scan of the chest, abdomen, and pelvis with IV contrast revealed consolidation of the right middle and lower lung lobes and absence of the right hepatic lobe, as shown in Figure 1.'
  obs('b00024',ct,'Right middle/lower lung consolidation',kind='imaging',method='CT chest, abdomen and pelvis with IV contrast',body_site='right middle/lower lung lobes',text_value='consolidation')
  obs('b00024',ct,'Post-hemihepatectomy right hepatic lobe absence',kind='imaging',method='CT chest, abdomen and pelvis with IV contrast',body_site='right hepatic lobe',text_value='absent right hepatic lobe')
  obs('b00024','MRCP demonstrated a prominent left biliary system with a stricture at the confluence','Prominent left biliary system',kind='imaging',method='MRCP',body_site='left biliary system',text_value='prominent')
  obs('b00024','MRCP demonstrated a prominent left biliary system with a stricture at the confluence','Biliary confluence stricture',kind='imaging',method='MRCP',body_site='biliary confluence',text_value='stricture')
  for q,n,site in [('patchy consolidation (arrow) in the right upper/middle lung lobe','CT caption upper/middle consolidation','right upper/middle lung lobe'),('marked consolidation and cystic lucencies in the right middle/lower lobe','CT caption consolidation and cystic lucencies','right middle/lower lung lobes'),('basilar consolidation and pleural thickening in the right lower lobe (RLL)','CT caption basal consolidation/pleural thickening','right lower lobe'),('superior migration of bowel loops (arrow) into the liver bed defect','CT caption superior bowel-loop migration','liver bed defect')]:obs('b00054',q,n,kind='imaging',method='CT with IV contrast',body_site=site,text_value=q,notes='Caption extension only; no diaphragmatic hernia inferred.')
  phase('ercp_confirmation','diagnosis')
  proc('b00024','ERCP confirmed the presence of a thoracobiliary fistula','ERCP',indication='evaluate thoracobiliary fistula',finding='confirmed thoracobiliary fistula')
  obs('b00055','ERCP showing the bronchial tree, biliary tree, and their fistulous communication (arrows)','ERCP fistulous communication',kind='imaging',method='ERCP',body_site='bronchial and biliary trees',text_value='fistulous communication')
  cond('b00001','The first case developed a bronchobiliary fistula after a right hemihepatectomy for a hydatid cyst','Postoperative bronchobiliary fistula',role='primary_diagnosis',body_site='biliary system and bronchial tree',diagnostic_basis='ERCP and operative communication')
  phase('repair','treatment')
  for q,n,site in [('exploratory laparotomy','Exploratory laparotomy','abdomen'),('excision of the fistulous tract','Fistulous tract excision','biliary-bronchial fistulous tract'),('repair of both hepatic and diaphragmatic defects','Hepatic fistulous defect repair','liver'),('repair of both hepatic and diaphragmatic defects','Diaphragmatic defect repair','diaphragm')]:proc('b00024',q,n,body_site=site,indication='bronchobiliary fistula')
  obs('b00024','a fistulous communication originating from the posterior surface of segment 4, traversing the diaphragm into the right bronchial system','Intraoperative tract origin and course',kind='other',method='intraoperative inspection',body_site='liver/diaphragm/right bronchial system',text_value='posteriorsegment4→diaphragm→rightbronchialsystem',certainty='source_conflict',scoring='review_only',notes='Figure3caption saysposterior right hepatic remnant; anatomical nomenclature disagreement retained without choosingcorrectlobe.')
  obs('b00056',b.seg['b00056']['text'],'Figure3operative tract origin',kind='other',method='operative image caption',text_value='posterior surface of right hepatic remnant',certainty='source_conflict',scoring='review_only',notes='Body namessegment4whilecaptionrighthepaticremnant afterrighthemihepatectomy; no reconcilinganatomicalinference.')
  obs('b00024','confirmed by the presence of bile and air bubbles with colored dye leakage.','Operative communication confirmation',kind='other',method='intraoperative inspection and colored dye leakage',text_value='bile and air bubbles with colored dye leakage')
  phase('current_recovery','outcome')
  out('b00001','The first patient recovered smoothly','recovery',notes='Current definitive repair course; no new discharge date reported.')
  out('b00001','In both cases, removing the fistulous tract and repairing the liver and diaphragmatic fistulous openings provided full relief of symptoms.','improvement',text_value='full relief of symptoms after fistulous tract excision/repair')
  p['ambiguities'].append(dict(issue='Currentrepair discharge date and follow-up interval unstated. POD13 belongs to priorMarch2022hemihepatectomy. Interveningprocedure recordsunavailable. Bodysegment4versusFigure3righthepaticremnant anatomical nomenclature conflict staysreview-only.',evidence=[dict(segment_id='b00007',quote='discharged on postoperative day 13.'),dict(segment_id='b00024',quote='posterior surface of segment 4'),dict(segment_id='b00056',quote='posterior surface of the right hepatic remnant')]))
 else:
  fact('demographics',start,'Farsi-speaking','Documented language',attribute='language',text_value='Farsi')
  fact('social_exposures',start,'A 36-year-old Afghan female','Afghan descriptor as reported',domain='other',name='Afghan as documented',status='unknown',notes='Nationality/geographic descriptor retained without assigninginferredethnicity.')
  fact('social_exposures',start,'with a history provided by her husband due to a language barrier (Farsi-speaking)','Husband provided history due to language barrier',domain='access_to_care',name='Language barrier requiring husband-provided history',status='current')
  phase('prior_conditions','history')
  for name in ['typhoid','malaria','hepatitis A']:cond(start,'previous episodes of typhoid, malaria, and hepatitis A',name,role='history',temporality='historical')
  phase('cholecystectomy_injury','history','five years ago')
  proc(start,'cholecystectomy five years ago at a rural hospital in Afghanistan','Cholecystectomy',action='historical',temporality='historical',body_site='gallbladder',time='five years ago',notes='Indication not stated.')
  cond(start,'complicated by a common bile duct injury','Common bile duct injury',role='complication',temporality='historical',body_site='common bile duct')
  phase('old_hj','history','January 18, 2020')
  proc(start,'she underwent a Roux-en-Y hepaticojejunostomy on January 18, 2020, in Kabul.','Roux-en-Y hepaticojejunostomy',action='historical',temporality='historical',time='January 18, 2020',body_site='hepaticojejunostomy',indication='common bile duct injury')
  out(start,'Her postoperative course had been uneventful until the recent onset of symptoms.','recovery',notes='Historical courseafterhepaticojejunostomy, notpost2025repair.')
  phase('symptom_course','presentation','the past two months')
  for q,n,kw in [('shortness of breath','Shortness of breath',{}),('dry cough','Dry cough',{}),('intermittent low-grade fever','Intermittent low-grade fever',dict(severity='low-grade')),('right-sided pleuritic chest pain','Right pleuritic chest pain',dict(body_site='right chest'))]:sym(start,q,n,kind='symptom',time='the past two months',**kw)
  phase('other_symptoms','presentation')
  sym(start,'significant weight loss (35 kg)','Weight loss',kind='symptom',numeric_value=35,unit='kg',notes='Starting/current weightsnotreported; noBMIcalculated.')
  sym(start,'pruritus','Pruritus',kind='symptom');sym(start,'skin rash','Skin rash',body_site='skin')
  phase('symptom_course','presentation','the past two months')
  med(start,'she had received multiple courses of antibiotics over the preceding two months.','Unspecified antibiotics',action='historical',temporality='historical',duration_text='over the preceding two months',notes='Names,doses,routes andindications notstated; not assignedgeneral stabilizationrecommendations.')
  phase('prior_drainage','treatment')
  cond(start,'a massive right-sided pleural effusion necessitating tube thoracostomy','Right pleural effusion',body_site='right pleural space',laterality='right',severity='massive')
  proc(start,'a massive right-sided pleural effusion necessitating tube thoracostomy','Tube thoracostomy',body_site='right pleural space',laterality='right',indication='massive right pleural effusion')
  obs(start,'continuously draining over 1000 ml/day of greenish fluid (bile)','Chest tube output rate',kind='physiologic',numeric_value=1000,comparator='>',unit='ml/day',text_value='over1000ml/daygreenishbile',body_site='right chest tube')
  phase('presentation','presentation','February 10, 2025')
  for q,n,kw in [('alert','Alert mental status',dict(kind='mental_status')),('oriented','Oriented mental status',dict(kind='mental_status')),('jaundiced','Jaundice',{}),('mild respiratory distress','Respiratory distress',dict(severity='mild')),('decreased breath sounds on the right hemithorax','Decreased right breath sounds',dict(body_site='right hemithorax')),('splenomegaly','Splenomegaly',dict(body_site='spleen')),('previous surgical scars','Prior abdominal surgical scars',dict(body_site='abdomen'))]:sym('b00027',q,n,**kw)
  sym('b00027','with no tenderness','Abdominal tenderness',assertion='absent',body_site='abdomen')
  proc('b00027','A functioning chest tube was in place, with ongoing bile-stained output.','Functioning right chest tube',kind='device',action='device_present',body_site='right hemithorax',laterality='right',finding='ongoing bile-stained output')
  p['facts'][-1]['evidence'].append(dict(segment_id=start,quote='massive right-sided pleural effusion necessitating tube thoracostomy'))
  phase('laboratory','test')
  cond('b00027','anemia (Hb 10.2 g/dL)','Anemia')
  obs('b00027','normal renal function','Renal function assessment',kind='laboratory',text_value='normal renal function',flag='normal')
  labrows=[(29,'Hemoglobin',10.2,'g/dl','low'),(30,'Total leukocyte count',8.2,'10^9/L',None),(31,'Platelets',394,'10^9/L',None),(32,'INR',1.1,None,None),(33,'Total bilirubin',3.34,'mg/dl',None),(34,'Direct bilirubin',2.1,'mg/dl',None),(35,'Alkaline phosphatase',929,'U/L','high'),(36,'ALT',79,'U/L','high'),(37,'AST',37,'U/L',None),(38,'Albumin',2.8,'g/dl',None),(39,'Serum creatinine',0.6,'mg/dl','normal')]
  for sn,name,v,u,flag in labrows:table(f'b{sn:05d}',name,v,u,specimen='serum' if sn==39 else None,flag=flag)
  for sn,name in [(40,'Anti-HCV'),(41,'HbsAg'),(42,'Anti-HIV')]:
   ss=f'b{sn:05d}';obs(ss,b.seg[ss]['text'].splitlines()[-1],name,text_value='Negative',flag='negative',notes='Method/specimen unstated inCase2table; noadoptCase1serologylabelasCase2method.')
  table('b00043','Pleural fluid bilirubin',12.9,'mg/dl',specimen='pleural fluid',flag='high');p['facts'][-1]['evidence'].append(dict(segment_id='b00027',quote='Pleural fluid bilirubin was markedly elevated at 12.9 mg/dL.'))
  phase('imaging','test')
  for q,n,site in [('an enlarged liver with wavy contours','Enlarged liver with wavy contours','liver'),('intrahepatic biliary dilatation','Intrahepatic biliary dilatation','intrahepatic biliary system'),('communication between the right hepatic segments (VII/VIII) and the pleural cavity','Possible liver-pleural communication','right hepatic segmentsVII/VIII and pleural cavity'),('right-sided hydropneumothorax','Right hydropneumothorax','right pleural space'),('basal consolidation','Basal consolidation','lung base'),('splenomegaly with venous collaterals','Splenomegaly with venous collaterals','spleen'),('signs of early cirrhosis','Early cirrhosis imaging signs','liver')]:obs('b00044',q,n,kind='imaging',method='ultrasound and CT imaging',body_site=site,text_value=q,notes='Ultrasound/CTattributedjointly inbody; noassignspecificmodality perfinding.')
  cond('b00044','signs of early cirrhosis','Possible early cirrhosis on imaging',verification_status='provisional',body_site='liver',diagnostic_basis='ultrasound/CT imaging signs')
  cond('b00044','right-sided hydropneumothorax','Right hydropneumothorax',body_site='right pleural space',laterality='right',diagnostic_basis='ultrasound/CT imaging')
  for q,n,site in [('stenosis of hepaticojejunostomy','Hepaticojejunostomy stenosis','hepaticojejunostomy'),('cholangitic abscesses in the right hepatic lobe','Right hepatic cholangitic abscesses','right hepatic lobe'),('some of which appeared to be communicating with the pleural space','Possible hepatic abscess-pleural communication','right hepatic lobe and pleural space')]:obs('b00044',q,n,kind='imaging',method='MRCP',body_site=site,text_value=q)
  obs('b00057','compressed lung parenchyma','CT caption lung compression',kind='imaging',method='chest CT',body_site='right lung',text_value='compressed lung parenchyma')
  obs('b00058','a segment VII/VIII heterogeneous density area (arrow) possibly communicating with the adjacent pleural cavity.','CT caption segmentVII/VIII heterogeneous area with possible pleural communication',kind='imaging',method='CT abdomen and pelvis with IV contrast',body_site='hepatic segmentsVII/VIII and adjacent pleural cavity',text_value='heterogeneousdensityarea; possiblepleuralcommunication')
  obs('b00059','prominence of intrahepatic biliary channels','MRCP caption intrahepatic channel prominence',kind='imaging',method='MRCP',body_site='intrahepatic biliary channels',text_value='prominence')
  med('b00058','CT abdomen and pelvis with IV contrast','Unspecified IV contrast',route='intravenous',indication='CT abdomen/pelvis',notes='Agent identity/dose unstated; noassumed agenttype.')
  phase('ercp_abstract','test')
  proc('b00001','Both patients were evaluated with advanced imaging (CT, magnetic resonance cholangiopancreatography (MRCP), and endoscopic retrograde cholangiopancreatography (ERCP))','ERCP evaluation',notes='Abstractexplicitbothpatients; Case2bodydoesnotreportindividualERCPfinding ordate, nofindinginferred.')
  phase('repair','treatment')
  for q,n,site in [('exploratory laparotomy','Exploratory laparotomy','abdomen'),('excision of the biliopleural fistula','Biliopleural fistula excision','biliopleural fistula'),('revision of the hepaticojejunostomy','Hepaticojejunostomy revision','hepaticojejunostomy'),('primary repair of the diaphragmatic defect','Primary diaphragmatic repair','diaphragm')]:proc('b00044',q,n,body_site=site,indication='biliopleural fistula with hepaticojejunostomy stenosis')
  for q,n,site in [('a fistulous tract extending from segment VII of the liver through the diaphragm into the pleural cavity','Operative fistula course','liver segmentVII/diaphragm/pleural cavity'),('a strictured hepaticojejunostomy','Operative hepaticojejunostomy stricture','hepaticojejunostomy'),('a cholestatic cirrhotic liver','Operative cholestatic cirrhotic liver','liver'),('dense intra-abdominal adhesions','Dense intra-abdominal adhesions','abdomen')]:obs('b00044',q,n,kind='other',method='intraoperative examination',body_site=site,text_value=q)
  cond('b00001','the second developed a biliopleural fistula following hepaticojejunostomy stenosis, performed for a common bile duct injury.','Biliopleural fistula',role='primary_diagnosis',body_site='liver and pleural cavity',diagnostic_basis='imaging and operative tract fromsegmentVIIthroughdiaphragm')
  cond('b00044','a cholestatic cirrhotic liver','Cirrhosis',body_site='liver',diagnostic_basis='intraoperative finding')
  cond('b00044','a cholestatic cirrhotic liver','Cholestasis',body_site='liver',diagnostic_basis='operative finding')
  phase('hepatic_decompensation','adverse_event')
  cond('b00044','developed hepatic decompensation attributed to longstanding cholestasis and evolving cirrhosis.','Postoperative hepatic decompensation',role='complication',body_site='liver',diagnostic_basis='clinical postoperative course')
  out('b00044','developed hepatic decompensation attributed to longstanding cholestasis and evolving cirrhosis.','complication',related_condition='hepatic decompensation',cause_as_documented='longstanding cholestasis and evolving cirrhosis')
  proc('b00044','the patient required intensive care','Postoperative intensive care',kind='nonpharmacologic_therapy',indication='postoperative hepatic decompensation')
  phase('postoperative_support','treatment')
  for q,n in [('supportive care','Supportive care'),('aggressive chest physiotherapy','Aggressive chest physiotherapy'),('incentive spirometry','Incentive spirometry')]:proc('b00044',q,n,kind='nonpharmacologic_therapy')
  phase('icu_transfer','outcome','postoperative day 12')
  out('b00044','was transferred out of the ICU on postoperative day 12','other',time='postoperative day 12',disposition='transferred out of ICU')
  phase('discharge','outcome','the 25th postoperative day')
  out('b00044','discharged on the 25th postoperative day.','discharge',time='the 25th postoperative day')
  phase('current_recovery','outcome')
  out('b00001','the second experienced transient hepatic decompensation (due to longstanding cholestasis), which improved with supportive care.','improvement',related_condition='hepatic decompensation',cause_as_documented='supportive care')
  out('b00001','In both cases, removing the fistulous tract and repairing the liver and diaphragmatic fistulous openings provided full relief of symptoms.','improvement',text_value='full relief of symptoms after repair')
  p['ambiguities'].append(dict(issue='Afghan is retained as sourcegeographicdescriptor, notinferredethnicity.2monthsymptoms/antibioticcourses and35kgweightloss do notsupplystartingweight/BMI. ERCP explicitlybothabstract/discussion butCase2finding notdescribed. AST37U/Lwithref<37retained no assumednormality. Cirrhosis imagingprovisional distinguished fromoperativefinding/postopdecompensation.',evidence=[dict(segment_id=start,quote='A 36-year-old Afghan female'),dict(segment_id='b00037',quote='AST | 37 U/L | <37 U/L'),dict(segment_id='b00001',quote='Both patients were evaluated with advanced imaging')]))
 # Both-series statements are individually applicable only where explicitly asserted of both.
 phase('repair','treatment')
 proc('b00050','Both of the patients in our series had a successful transabdominal fistula excision, which prevented needless pulmonary resection','Transabdominal fistula excision',indication='thoracobiliary fistula',body_site='abdomen',finding='successful fistula excision')
 proc('b00050','which prevented needless pulmonary resection','Pulmonary resection',assertion='absent',action='unknown',body_site='lung',notes='Explicitly avoided/notperformed, notplanned lungresection.')
 # Caption/sourcefield pass: procedures have clinical diagnostic indications when explicit, noagents inferred.
 for f in p['facts']:
  if f['fields'].get('time'):
   t=f['fields']['time']['text'].lower()
   if 'later' in t or 'postoperative' in t:f['fields']['time']['relation']='after'
   elif 'over ' in t or 'past two months' in t:f['fields']['time']['relation']='during'
 events={}
 descriptions={'weight_course':'Reported weight-loss course with65kgstarting/56kgfinalweights; startingdateunstated','other_symptoms':'Additional reportedweightloss/pruritus/rash withonsetintervalunstated','presentation':'Current hospital presentation and examination','prior_hepatectomy':'March2022righthemihepatectomy forhydatid cyst','old_injury_repair':'Prior operative diaphragmatic injury, repair andchest tube','old_recovery_discharge':'Prior2022postoperative recovery/POD13discharge','symptom_onset':'Delayed respiratory symptom onset after prior surgery','intervening_procedures':'Multiple intervening procedures withunavailable records andpersistent symptoms','prior_conditions':'Historical typhoid/malaria/hepatitisA episodes','cholecystectomy_injury':'Historical cholecystectomy complicated byCBDinjury','old_hj':'January2020RouxHJ andinitialuneventful course','symptom_course':'Two-month symptomaticcourse andempirical antibioticcourses','prior_drainage':'Pre-presentation pleural effusion drainage andhigh-volume biliousoutput','laboratory':'Current blood/viral andsputum-or-pleural-fluid laboratory assessment','imaging':'CT/US/MRCP anatomic workup andcaption details','ercp_confirmation':'ERCP confirmed biliary-bronchial communication','ercp_abstract':'ERCP evaluation reported inboth-patientabstract; individualCase2result unstated','repair':'Definitive abdominal fistula excision andrepair withoperative findings','hepatic_decompensation':'Postoperative hepatic decompensation/intensive care','postoperative_support':'Supportivecare/chestphysiotherapy/incentivespirometry','icu_transfer':'Transferred out ofICU onPOD12','discharge':'Discharged onPOD25','current_recovery':'Reported symptomatic relief/current postoperative recovery'}
 for key,ids in phases.items():events[key]=b.event(descriptions[key],ids,time=times[key])
 phase_metadata[p['patient_id']]={events[key]:kinds[key] for key in events}
 sequence=['prior_hepatectomy','old_injury_repair','old_recovery_discharge','symptom_onset','intervening_procedures','presentation','laboratory','imaging','ercp_confirmation','repair','current_recovery'] if case==1 else ['cholecystectomy_injury','old_hj','symptom_course','prior_drainage','presentation','laboratory','imaging','repair','hepatic_decompensation','postoperative_support','icu_transfer','discharge']
 # Sparse phase order supported by case prose; do notorder exactlab draws againstCTorMRCP.
 pairs=[('prior_hepatectomy','old_injury_repair'),('old_injury_repair','old_recovery_discharge'),('old_recovery_discharge','symptom_onset'),('symptom_onset','intervening_procedures'),('intervening_procedures','presentation'),('imaging','repair'),('ercp_confirmation','repair'),('repair','current_recovery')] if case==1 else [('cholecystectomy_injury','old_hj'),('old_hj','symptom_course'),('prior_drainage','presentation'),('imaging','repair'),('repair','hepatic_decompensation'),('hepatic_decompensation','postoperative_support'),('repair','icu_transfer'),('icu_transfer','discharge')]
 for frm,to in pairs:b.before(events[frm],events[to],start if frm in ['prior_hepatectomy','old_injury_repair','old_recovery_discharge','symptom_onset','intervening_procedures','cholecystectomy_injury','old_hj','prior_drainage'] else 'b00024' if case==1 and to=='repair' else 'b00001' if case==1 else 'b00044',b.seg[start if frm in ['prior_hepatectomy','old_injury_repair','old_recovery_discharge','symptom_onset','intervening_procedures','cholecystectomy_injury','old_hj','prior_drainage'] else 'b00024' if case==1 and to=='repair' else 'b00001' if case==1 else 'b00044']['text'])
 def ids(keys):return [fid for k in keys for fid in phases.get(k,[]) if next(f for f in p['facts'] if f['fact_id']==fid)['scoring']=='include']
 if case==1:
  b.summary('A50-year-old man developed persistent cough, sputum and intermittent fever about6weeks after March2022right hemihepatectomy forhydatid cyst complicated bydiaphragmatic injury. Despite multiple undocumented procedures over2years, copious biliptysis, appetite loss and9kgweight loss persisted. The priorPOD13discharge belongs to2022surgery. He presented August7,2024thin withright basal respiratory findings andnon-tender abdomen withoutorganomegaly.',ids(['presentation','weight_course','prior_hepatectomy','old_injury_repair','old_recovery_discharge','symptom_onset','intervening_procedures']))
  b.summary('Workup showed mildanemia, normalWBC/platelets, raisedALP/bilirubin andsputum bilirubin, negativeviralserology, CTconsolidation/rightlobectomy anatomy andMRCPbiliarystricture. ERCPconfirmed bronchobiliarycommunication. Abdominal fistulaexcision andhepatic/diaphragmaticrepair avoided pulmonaryresection andprovided symptomrelief withsmoothrecovery; currentdischarge date/followup notgiven. Bodysegment4andcaptionrightremnant originclaims remain unresolved.',ids(['laboratory','imaging','ercp_confirmation','repair','current_recovery']))
 else:
  b.summary('A36-year-old Farsi-speaking Afghan woman, whosehusband suppliedhistory, presented February10,2025after2monthsdyspnea/drycough/lowgradefever/rightpleuriticpain. Priorcholecystectomy causedCBDinjury treated withJanuary18,2020RouxHJ. A rightchesttube for massiveeffusion drained>1000ml/daybile;35kgweightloss/pruritus/rash andmultipleantibioticcourses were reported. Shewasalert/oriented/jaundiced withmildrespiratorydistress, splenomegaly andnontenderabdomen.',ids(['presentation','other_symptoms','prior_conditions','cholecystectomy_injury','old_hj','symptom_course','prior_drainage']))
  b.summary('Anemia, raisedALP/ALT andmarkedpleuralbilirubin accompanied normalrenalfunction andnegativeviralscreening. Imaging showedright hydropneumothorax, biliarydilatation/HJstenosis andrighthepaticabscesses withpossiblepleuralcommunication. Definitiveabdominal excision/HJrevision/diaphragmrepair revealedsegmentVIItract, stricturedHJ, cirrhoticcholestaticliver andadhesions. Postoperativehepaticdecompensation improved withsupportivecare/chestphysiotherapy/spirometry; ICUtransfer occurredPOD12anddischargePOD25. Fullsymptomrelief wasreported, longtermfollowupunstated.',ids(['laboratory','imaging','ercp_abstract','repair','hepatic_decompensation','postoperative_support','icu_transfer','discharge','current_recovery']))
 b.finish()
# Explicit panel labels only; body figures read source-owned, pixels remain separatefile.
for sid,labels in [('b00054',['A','B','C'])]:panels[sid]=[(lab,b.seg[sid]['text'],'CT with IV contrast','chest/upper abdomen',['p1'],'individual') for lab in labels]
b.a['article_limitations']+=['Case1multiple interveningprocedure recordsunavailable; no exactcurrent postoperative discharge/followup interval. Case2individualERCPresult notgiven despiteboth-patientabstract claim. GeneralrecommendedIVfluids/antibiotics/nutrition inb00049are background, notpatientorders. Prior cited reports do not explicitly identify a same-patient follow-up.','Case1tractorigin segment4 versuscaptionrighthepaticremnant retained assourceconflict. No anatomical repair ofreporting adopted.']
b.a['annotation_provenance']['passes'].append('Additional full-field source-to-output pass: documented contexts/sites/laterality/lab methods/specimens/medicationstatus, bothcompleteclinicalphases andcaption ownership reviewed. OriginalJATSdiscussion bibr links checked: both-series explicit claims accepted, reference-associated general recommendations notpromoted to indexpatients.')
b.save(own,panels)
# Stable phase classifications authored separately for actual full-runtime inspection.
Path('benchmarks/dense500/annotator1_tbf_phase_metadata.json').write_text(json.dumps(phase_metadata))
