"""Manual source-reviewed fact-to-phase corrections, annotator1 only."""
import json
from pathlib import Path
BASE=Path('benchmarks/dense500')
class Phase:
 def __init__(self,n):
  self.n=n;self.path=BASE/'drafts'/f'PMC{n}.1.json';self.a=json.loads(self.path.read_text());self.s=json.loads((BASE/'sources'/self.path.name).read_text());self.seg={s['segment_id']:s['text'] for s in self.s['segments']}
 def p(self,pid):return next(p for p in self.a['patients'] if p['patient_id']==pid)
 def ids(self,pid,nums):return [f'{pid}-f{i:03d}' for i in nums]
 def attach(self,pid,event,nums):
  p=self.p(pid);e=next(e for e in p['events'] if e['event_id']==f'{pid}-e{event:03d}');facts={f['fact_id']:f for f in p['facts']}
  for fid in self.ids(pid,nums):
   f=facts[fid];assert f['scoring']=='include',(self.n,fid)
   if fid not in e['fact_ids']:e['fact_ids'].append(fid)
   for row in f['evidence']:
    if row not in e['evidence']:e['evidence'].append(row)
 def new(self,pid,desc,nums,time=None,occurrence='occurred'):
  p=self.p(pid);number=len(p['events'])+1
  p['events'].append({'event_id':f'{pid}-e{number:03d}','description':desc,'occurrence':occurrence,'fact_ids':[],'time_text':time,'evidence':[]});self.attach(pid,number,nums)
  e=p['events'][-1]
  if time and not any(time in r['quote'] for r in e['evidence']):
   sid=next(r['segment_id'] for r in e['evidence'] if time in self.seg[r['segment_id']]);e['evidence'].append({'segment_id':sid,'quote':self.seg[sid]})
  return number
 def remove(self,pid,event,nums):
  e=self.p(pid)['events'][event-1];remove=set(self.ids(pid,nums));e['fact_ids']=[f for f in e['fact_ids'] if f not in remove]
  facts={f['fact_id']:f for f in self.p(pid)['facts']};e['evidence']=[]
  for fid in e['fact_ids']:
   for row in facts[fid]['evidence']:
    if row not in e['evidence']:e['evidence'].append(row)
 def save(self):
  self.path.write_text(json.dumps(self.a,ensure_ascii=False,indent=2)+'\n')
  for p in self.a['patients']:
   linked={fid for e in p['events'] for fid in e['fact_ids']};orphan=[f['fact_id'] for f in p['facts'] if f['scoring']=='include' and f['fact_id'] not in linked];print(self.n,p['patient_id'],'unlinked',orphan)

def first_three():
 b=Phase('12880940');b.attach('p1',3,[39]);b.attach('p1',4,[41,42]);b.new('p1','Clinical diagnosis and echocardiographic characterization of intramyocardial hematoma; precise examination date not specified',[34,35,36,37,48,49,50,51,52]);b.new('p1','Chest CT showed cardiac enlargement; further confirmatory MRI/angiography not pursued',[45,46,47,53]);b.attach('p2',2,[39]);b.attach('p2',4,list(range(42,50)));b.attach('p2',5,[50]);b.save()
 b=Phase('12811047')
 for pid in ['p1','p2']:
  p=b.p(pid);follow=[int(f['fact_id'].rsplit('f',1)[1]) for f in p['facts'] if f['fields'].get('time',{}).get('text')=='Follow-up'];b.remove(pid,1,follow)
 b.attach('p1',2,[73]);b.new('p1','Pretreatment thiamine result confirmed deficiency-related high-output circulatory state; result confirmation date not specified',[75,76]);b.attach('p1',5,[82,83]);b.attach('p2',5,[77,78]);b.new('p2','Low pretreatment thiamine result subsequently supported deficiency-related high-output circulatory state',[70,71]);b.save()
 b=Phase('12312621');b.attach('p1',5,[25]);b.attach('p1',6,[28]);b.new('p1','Subsequent follow-up potassium/renal improvement with continued CNI and no persistent hyperkalemia',[32,33,34,35,36],time='During follow-up');b.attach('p1',1,list(range(37,45))+[50]);b.new('p1','Table control creatinine measurement, sampling date not specified',[51]);b.attach('p2',4,[25]);b.attach('p2',1,list(range(31,38))+[43]);b.new('p2','Renal regression and table control creatinine, exact sampling date not specified',[30,44]);b.attach('p3',5,[25,28,29]);b.attach('p3',7,[37,47]);b.attach('p3',1,list(range(38,42))+[46]);b.save()
if __name__=='__main__':first_three()

def ivc():
 b=Phase('12832185');p=b.p('p1');facts={int(f['fact_id'].rsplit('f',1)[1]):f for f in p['facts']}
 f=facts[1];f['fields']['chief_complaint']='acute-onset left lower extremity swelling and pain';f['evidence'].append({'segment_id':'b00004','quote':'presented with acute-onset left lower extremity swelling and pain at a hospital along her long-haul trucking route.'})
 facts[19]['fields']['severity']='severe';facts[20]['fields']['body_site']='left calf';facts[21]['fields']['activity_context']='passive dorsiflexion of the left ankle'
 facts[41]['fields']['body_site']='IVC';facts[46]['fields']['laterality']='left'
 for i in [49,50]:facts[i]['fields']['laterality']='left'
 # Procedure location/indication are documented in the same actual-operative paragraph.
 for i in [44,45,47,49,50,51,52]:
  facts[i]['fields']['indication']='left lower extremity acute-on-chronic DVT' if i not in [49,50] else 'tight stenosis in the left common iliac vein'
  row={'segment_id':'b00009','quote':b.seg['b00009']}
  if row not in facts[i]['evidence']:facts[i]['evidence'].append(row)
 for i in [47,51,52]:facts[i]['fields'].update(body_site='left lower extremity',laterality='left')
 for i,site in [(24,'left common femoral vein'),(25,'azygos vein'),(26,'pulmonary vasculature'),(27,'thoracic aorta'),(28,'lung parenchyma'),(39,'left iliofemoral veins'),(40,'IVC and retroperitoneal/azygos veins')]:facts[i]['fields']['body_site']=site
 b.attach('p1',2,[8]);b.attach('p1',7,[5,11,42]);b.attach('p1',3,[24,25,26,27,28,29,30,31,32,39,40,41]);b.attach('p1',4,[44,46,47,48,49,50,51,52,53]);b.attach('p1',6,[55,56]);b.new('p1','Acute presentation with left-leg swelling/pain, examination and reported background exposures',[1,2,3,9,10,12,13,14,15,16,17,18,19,20,21,22]);b.save()

def dmd():
 b=Phase('12611218')
 def fact(pid,i):return next(f for f in b.p(pid)['facts'] if f['fact_id']==f'{pid}-f{i:03d}')
 def setf(pid,i,fields,sid=None):
  f=fact(pid,i);f['fields'].update(fields)
  if sid:
   row={'segment_id':sid,'quote':b.seg[sid]}
   if row not in f['evidence']:f['evidence'].append(row)
 for pid,sid in [('p1','b00002'),('p2','b00003'),('p3','b00006')]:
  setf(pid,1,{'multiple_index_patients':True},'b00007')
  if pid=='p2':setf(pid,1,{'chief_complaint':'severe dyspnea'},sid)
  for f in b.p(pid)['facts']:
   if f['task']=='medications' and f['fields']['name']=='GDMT':f['fields']['indication']='heart failure'
 b.attach('p1',6,[13]);b.new('p1','Year of guideline-directed HF therapy after STEMI',[14],time='for a year');b.remove('p1',7,[16]);b.new('p1','Dated ED severe dyspnea admission, short dobutamine course and discharge',[15,16,17],time='On November 5, 2020');b.remove('p1',1,[5]);b.new('p1','Preoperative end-stage cardiomyopathy evaluation, scoliosis imaging and transplant/LVAD deliberation',[5,20,21,22,23,36,37,38,39,40,41,46,47,48,49]);b.new('p1','Historical DMD/DCM diagnosis ages and motor/symptom state before full decompensation',[34,35,56,57]);b.attach('p1',2,[42,43,44,45,50]);b.new('p1','Successful postoperative extubation with specialist supervision',[25],time='4 hours postoperatively');b.new('p1','Reduced NIV dependence after implantation',[26]);b.new('p1','Early postoperative RV failure and severe tricuspid regurgitation',[51,52]);b.attach('p1',4,[54]);b.attach('p1',5,[30,31,32])
 b.attach('p2',1,[11,12,13]);b.new('p2','Prior recurrent HF admissions and historical DMD/DCM diagnosis ages',[10,42,43]);b.attach('p2',5,[21]);b.attach('p2',2,[22,50,51,52,53,57]);b.attach('p2',7,[27,29,30,58]);b.attach('p2',3,[35,36,37,38,39]);b.attach('p2',4,[61]);b.new('p2','Preoperative anthropometry, HF phenotype and hemodynamic assessment',[44,45,46,47,48,49,54,55,56])
 b.remove('p3',1,[5]);b.new('p3','Initial GDMT uptitration and discharge with conditional ICD assessment',[10,11,12]);b.new('p3','Subsequent severe-dyspnea ICU admission, pulmonary findings and dobutamine initiation',[13,14,15,16,17],time='on August 31, 2023');b.attach('p3',5,[19,20,23,28,29]);b.new('p3','Preoperative end-stage HF, anthropometry and hemodynamic assessment',[5,41,42,43,44,45,46,51,52,53]);b.new('p3','Historical DMD/DCM diagnosis ages and motor/symptom state before full decompensation',[39,40,60,61]);b.new('p3','LVAD insurance approval after application',[30],time='after eight days');b.attach('p3',2,[47,48,49,50,54]);b.attach('p3',3,[34,35]);b.attach('p3',4,[38,57]);b.new('p3','Mild early postoperative RV failure',[55]);
 # Explicit source-stated indication/body sites, without inferred medication route or generic pathology.
 setf('p1',12,{'indication':'ST-segment elevation myocardial infarction'},'b00002');setf('p1',16,{'indication':'heart failure with severe dyspnea'},'b00002');setf('p1',19,{'indication':'recurrent heart failure'},'b00002')
 for pid,i,sid in [('p2',23,'b00004'),('p3',31,'b00006')]:setf(pid,i,{'finding':'successful centrifugal HeartMate 3 LVAD implantation'},'b00026')
 setf('p2',16,{'indication':'cardiac arrest'},'b00004');setf('p2',20,{'body_site':'right chest'},'b00004');setf('p2',28,{'body_site':'left upper thorax'},'b00005');setf('p2',36,{'body_site':'right frontotemporoparietal lobes and thalamus'},'b00044');setf('p3',34,{'body_site':'duodenum'},'b00006');setf('p3',17,{'indication':'severe heart failure with dyspnea'},'b00006')
 # Paired different named measurements are numeric, retaining printed (possibly erroneous) units.
 for pid,i,vals in [('p1',48,(1.4,7)),('p2',55,(1.3,8)),('p3',52,(18,12.7))]:
  f=fact(pid,i);f['fields'].update(name='Preoperative TAPSE',numeric_value=vals[0],unit='cm',text_value=None);f['clinical_statement']=f'Preoperative TAPSE {vals[0]} cm, printed table units retained.'
  num=max(int(x['fact_id'].rsplit('f',1)[1]) for x in b.p(pid)['facts'])+1
  row=json.loads(json.dumps(f));row['fact_id']=f'{pid}-f{num:03d}';row['fields'].update(name='Preoperative RV lateral s′',numeric_value=vals[1],unit='cm/s');row['clinical_statement']=f'Preoperative RV lateral s′ {vals[1]} cm/s, printed table units retained.';b.p(pid)['facts'].append(row)
  e={'p1':12,'p2':13,'p3':14}[pid];b.attach(pid,e,[num])
 b.save()

def oncology_small():
 b=Phase('12718700')
 def f(pid,num):return next(x for x in b.p(pid)['facts'] if x['fact_id']==f'{pid}-f{num:03d}')
 for pid,sid,complaint,event in [('p1','b00018','fatigue and anemia',2),('p2','b00019','weight loss with severely impaired oral intake at referral',1),('p3','b00021','abdominal pressure and discomfort',1)]:
  f(pid,1)['fields']['chief_complaint']=complaint;f(pid,1)['evidence'].append({'segment_id':sid,'quote':b.seg[sid]});b.attach(pid,event,[1,2,3])
  for x in b.p(pid)['facts']:
   if x['task']=='conditions' and 'duodenal adenocarcinoma' in x['fields']['name'].lower():x['fields']['body_site']='duodenum'
 f('p1',24)['fields'].update(body_site='pancreas');f('p3',28)['fields'].update(body_site='duodenum');f('p3',25)['fields']['response']='Tumor shrinkage after two courses with persistent stenosis; subsequent resection pathology showed no residual tumor or nodal metastases.'
 f('p3',25)['evidence'].append({'segment_id':'b00022','quote':b.seg['b00022']})
 # Cross-schema oncology entries preserve actions; supportive bypass is not a tumor resection.
 for pid,num,event,name,action,modality in [('p2',24,4,'pancreaticoduodenectomy','declined','surgery'),('p2',25,5,'gastrojejunostomy to restore oral intake','given','supportive'),('p3',34,7,'pancreaticoduodenectomy with lymph node dissection','declined','surgery')]:
  num_new=max(int(x['fact_id'].rsplit('f',1)[1]) for x in b.p(pid)['facts'])+1;old=f(pid,num)
  row={'fact_id':f'{pid}-f{num_new:03d}','task':'oncology','collection':'treatments','fields':{'subject':'index_patient','assertion':'present','tumor_ref':'t1','name':name,'modality':modality,'action':action},'clinical_statement':old['clinical_statement'],'evidence':old['evidence'],'certainty':'supported','scoring':'include','notes':'Cross-schema representation of source-described cancer management; intent unreported.'};b.p(pid)['facts'].append(row);b.attach(pid,event,[num_new])
 b.remove('p2',8,[30,31,32]);b.attach('p2',2,[30]);b.attach('p2',3,[31]);b.new('p2','Reported partial response in present-case table; response assessment date not specified',[32]);b.p('p2')['events'][7]['description']='Reported alive status in present-case table; endpoint date not specified'
 b.save()
 b=Phase('12754280')
 for pid in ['p1','p2']:
  f=b.p(pid)['facts'][0];f['evidence'].append({'segment_id':'b00011','quote':b.seg['b00011']});b.attach(pid,2,[1,2,3])
  for row in b.p(pid)['facts']:
   if row['task']=='conditions' and row['fields']['name']=='Hepatocellular carcinoma':row['fields']['body_site']='liver'
 b.save()

def nickel():
 b=Phase('12766325')
 for pid,sid in [('p1','b00005'),('p2','b00006'),('p3','b00007')]:
  facts={int(f['fact_id'].rsplit('f',1)[1]):f for f in b.p(pid)['facts']};facts[1]['fields']['chief_complaint']='referral for evaluation and treatment of known unruptured cerebral aneurysm';facts[1]['evidence'].append({'segment_id':sid,'quote':b.seg[sid]})
  for f in facts.values():
   if f['task']=='medications' and f['fields']['name'] in ['Dual antiplatelet therapy','Single antiplatelet agent','Single antiplatelet therapy']:
    f['fields']['indication']='peri-/postprocedural aneurysm endovascular treatment';row={'segment_id':sid,'quote':b.seg[sid]}
    if row not in f['evidence']:f['evidence'].append(row)
  site={'p1':'right internal carotid–posterior communicating artery junction','p2':'left vertebral artery aneurysm neck','p3':'right internal carotid artery aneurysm neck'}[pid]
  main={'p1':21,'p2':21,'p3':19}[pid];facts[main]['fields'].update(body_site=site,laterality='left' if pid=='p2' else 'right',indication='preventive treatment of unruptured cerebral aneurysm');facts[main]['evidence'].append({'segment_id':sid,'quote':b.seg[sid]})
 b.attach('p1',3,[17,18,19,20,22,29]);b.attach('p1',6,[24]);b.attach('p1',4,[27,28]);b.attach('p2',6,[15,22]);b.attach('p2',3,[17,18,19,20,23,28]);b.attach('p2',4,[26,27]);b.attach('p3',3,[15,16,17,18,21,26]);b.attach('p3',4,[24,25]);b.new('p3','Institutional approval and patient/family consent for off-label flow-diverter treatment',[20],time='prior to treatment');b.save()

def pacemaker():
 b=Phase('12754810')
 def fact(pid,num):return next(f for f in b.p(pid)['facts'] if f['fact_id']==f'{pid}-f{num:03d}')
 def add(pid,task,fields,statement,evidence,collection='items'):
  num=max(int(x['fact_id'].rsplit('f',1)[1]) for x in b.p(pid)['facts'])+1;b.p(pid)['facts'].append({'fact_id':f'{pid}-f{num:03d}','task':task,'collection':collection,'fields':{'subject':'index_patient','assertion':'present',**fields},'clinical_statement':statement,'evidence':evidence,'certainty':'supported','scoring':'include','notes':''});return num
 for pid,sid,complaint in [('p1','b00004','progressive swelling and pain of the left upper limb'),('p2','b00014','five-day progressive left upper-limb swelling')]:
  f=fact(pid,1);f['fields']['chief_complaint']=complaint;f['evidence'].append({'segment_id':sid,'quote':b.seg[sid]});b.attach(pid,2,[1,2,3])
  for f in b.p(pid)['facts']:
   if f['task']=='conditions' and f['fields'].get('role')=='differential':f['fields'].update(body_site='left upper limb',laterality='left');f['evidence'].append({'segment_id':'b00005' if pid=='p1' else 'b00015','quote':b.seg['b00005' if pid=='p1' else 'b00015']})
   if f['task']=='medications' and f['fields']['name'] in ['enoxaparin','apixaban','rivaroxaban']:
    f['fields']['indication']='left upper-limb DVT';sid2='b00012' if pid=='p1' else 'b00024';f['evidence'].append({'segment_id':sid2,'quote':b.seg[sid2]})
  check=45 if pid=='p1' else 42;fact(pid,check)['fields']['kind']='other'
  i=29 if pid=='p1' else 31;vals=(153,108) if pid=='p1' else (130,74)
  for component,val in zip(['Systolic','Diastolic'],vals):
   num=add(pid,'observations',{'name':component+' blood pressure','kind':'vital','status':'resulted','numeric_value':val,'text_value':fact(pid,i)['fields']['text_value']},component+' component of printed BP pair; source supplies no unit.',fact(pid,i)['evidence']);b.attach(pid,2,[num])
  hist=41 if pid=='p1' else 39
  b.remove(pid,2,[hist]);b.attach(pid,5,[hist])
  if pid=='p1':b.remove(pid,2,[42]);b.attach(pid,5,[42])
  for bound,val in zip(['lower','upper'],[45,48] if pid=='p1' else [65,70]):
   num=add(pid,'observations',{'name':'Previous LVEF reported range '+bound+' bound','kind':'imaging','status':'historical','numeric_value':val,'unit':'%','text_value':fact(pid,hist)['fields']['text_value'],'method':'echocardiogram','time':fact(pid,hist)['fields']['time']},'Explicit '+bound+' bound of documented previous LVEF range.',fact(pid,hist)['evidence']);b.attach(pid,5,[num])
 # Presentation severity/location documented rather than only contained in item names.
 for i,site in [(14,'left axilla'),(15,'left brachial area'),(16,'left upper limb'),(17,'left upper limb'),(18,'left upper limb')]:fact('p1',i)['fields']['body_site']=site
 fact('p1',17)['fields']['severity']='marked';fact('p2',20)['fields']['body_site']='left upper arm';fact('p2',21)['fields']['body_site']='right upper arm';fact('p1',56)['fields']['kind']='other'
 for extent in [1,2]:
  num=add('p1','observations',{'name':f'Pacemaker-site hematoma extent{extent}','kind':'other','status':'resulted','numeric_value':2,'unit':'cm','body_site':'pacemaker site','text_value':'2 x 2 cm'},f'Hematoma extent{extent} 2 cm; axes unnamed in source.',fact('p1',19)['evidence']);b.attach('p1',2,[num])
 b.attach('p1',3,[53]);b.new('p1','Transfer to district hospital, cardiology review and inpatient screening/clinical PE assessment',[48,49,54,55,56,57,58]);b.attach('p1',2,[61,62,63]);b.new('p2','Hematology consultation, tumor-marker screen and actual CT malignancy/venous workup',[45,47,48,49,62]);b.new('p2','Interventional radiology review of organized thrombus/collaterals and decision against acute intervention',[51,52,53]);b.attach('p2',2,[58,59,60,61])
 ev=[{'segment_id':'b00014','quote':b.seg['b00014']},{'segment_id':'b00026','quote':b.seg['b00026']}]
 num=add('p2','oncology',{'tumor_ref':'t1','name':'CALR-positive essential thrombocythaemia','disease_extent':'hematologic'},'Documented ET; source explicitly identifies ET as a myeloproliferative neoplasm.',ev,'tumors');b.attach('p2',5,[num])
 num=add('p2','oncology',{'tumor_ref':'t1','name':'CALR','gene':'CALR','interpretation':'positive','text_value':'CALR-positive','origin':'unknown'},'CALR-positive ET; no specific variant, assay or origin stated.',ev,'biomarkers');b.attach('p2',5,[num])
 num=add('p2','oncology',{'tumor_ref':'t1','name':'hydroxycarbamide','modality':'systemic','action':'ongoing'},'Ongoing hydroxycarbamide for ET; dose increased after DVT to reduce vascular complications, exact dose unreported.',ev,'treatments');b.attach('p2',7,[num])
 num=add('p2','oncology',{'tumor_ref':'t1','name':'aspirin for ET','modality':'supportive','action':'ongoing'},'Source-described aspirin use for ET; dose unreported.',ev,'treatments');b.attach('p2',2,[num])
 b.save()

def watchman():
 b=Phase('12811543');p=b.p('p1');facts={int(f['fact_id'].rsplit('f',1)[1]):f for f in p['facts']}
 def setf(i,fields,sid=None):
  facts[i]['fields'].update(fields)
  if sid:
   row={'segment_id':sid,'quote':b.seg[sid]}
   if row not in facts[i]['evidence']:facts[i]['evidence'].append(row)
 setf(1,{'chief_complaint':'LAAC evaluation because of oral-anticoagulation intolerance and reluctance; intermittent palpitations and nocturnal dyspnea'},'b00012')
 setf(6,{'body_site':'tricuspid valve'});setf(7,{'body_site':'mitral valve'});setf(34,{'specimen':'serum'},'b00014');setf(36,{'flag':'high'},'b00014');setf(80,{'specimen':'serum','flag':'high'},'b00017');setf(82,{'specimen':'feces'});setf(83,{'specimen':'urine'})
 setf(49,{'body_site':'left atrial appendage','laterality':'left','indication':'alternative stroke prevention for atrial fibrillation with oral-anticoagulation intolerance/reluctance'},'b00012');setf(49,{},'b00015')
 for i in [54,55]:setf(i,{'indication':'post-LAAC antithrombotic treatment'},'b00017')
 setf(69,{'indication':'device-related thrombosis'},'b00017');setf(76,{'action':'ordered','indication':'continued anticoagulation after first DRT resolution'},'b00017');facts[76]['clinical_statement']='Continuation of warfarin advised after first thrombus resolution; future adherence not established by this instruction.'
 setf(88,{'route':'oral'},'b00018');setf(95,{'indication':'recurrent device-related thrombosis'},'b00018')
 for i in [65,92]:setf(i,{'body_site':'left atrial appendage occluder surface'},'b00015')
 for label,val in [('Systolic',138),('Diastolic',82)]:
  num=max(int(f['fact_id'].rsplit('f',1)[1]) for f in p['facts'])+1;p['facts'].append({'fact_id':f'p1-f{num:03d}','task':'observations','collection':'items','fields':{'subject':'index_patient','assertion':'present','name':label+' blood pressure','kind':'vital','status':'resulted','numeric_value':val,'unit':'mmHg','text_value':'138/82'},'clinical_statement':label+' component of source138/82mmHg BP.','evidence':facts[24]['evidence'],'certainty':'supported','scoring':'include','notes':''});b.attach('p1',7,[num])
 b.attach('p1',7,[1,2,3,16,17,41,62]);b.attach('p1',6,[6,7,18,21,63]);b.attach('p1',1,[23,52,53]);b.remove('p1',7,[51,52,53]);b.attach('p1',2,[66,67,68]);b.attach('p1',3,[70,71]);b.attach('p1',4,[75]);b.attach('p1',15,[76]);b.attach('p1',9,[84,85]);b.attach('p1',5,[91,93,94,96]);b.new('p1','Reported postprocedural surveillance without peri-device leak or ischemic stroke; exact assessment date unstated',[101,102]);b.new('p1','Post-intensification assessment limitation: pulmonary venous CTA not obtained',[104]);b.save()

def pe():
 b=Phase('12786310');p=b.p('p1');facts={int(f['fact_id'].rsplit('f',1)[1]):f for f in p['facts']}
 def setf(i,fields,sid=None):
  facts[i]['fields'].update(fields)
  if sid:
   row={'segment_id':sid,'quote':b.seg[sid]}
   if row not in facts[i]['evidence']:facts[i]['evidence'].append(row)
 def add(task,fields,statement,evidence,collection='items'):
  num=max(int(f['fact_id'].rsplit('f',1)[1]) for f in p['facts'])+1;p['facts'].append({'fact_id':f'p1-f{num:03d}','task':task,'collection':collection,'fields':{'subject':'index_patient','assertion':'present',**fields},'clinical_statement':statement,'evidence':evidence,'certainty':'supported','scoring':'include','notes':''});return num
 def move(nums,desc,time=None):
  accepted=[i for i in nums if facts[i]['scoring']=='include'];b.remove('p1',2,nums);return b.new('p1',desc,accepted,time=time)
 setf(1,{'chief_complaint':'progressive exertional dyspnea and fatigue culminating in transient severe dyspnea/chest pain with home desaturation'},'b00010')
 for i in range(44,60):setf(i,{'method':'Mindray BC-6200 hematology analyzer at Central Laboratory'},'b00007')
 for i in range(60,64):setf(i,{'method':'ACL TOP 550 coagulation analyzer at Central Laboratory'},'b00007')
 for i in range(64,83):setf(i,{'method':'Stat Profile PRIME analyzer; arterial gas analysis at FiO2 21%'},'b00006')
 for i in list(range(83,92))+list(range(93,99)):setf(i,{'method':'Beckman Coulter AU680 biochemistry analyzer at Central Laboratory','specimen':'serum'},'b00007')
 for i in [116,117,118]:setf(i,{'method':'PATHFAST analyzer','specimen':'peripheral venous blood in heparinized syringes'},'b00006')
 for i in list(range(124,135))+list(range(139,144))+[146,147]:
  if facts[i]['task']=='observations':setf(i,{'method':'POCUS-TTE using Versana Active ultrasound system'},'b00008')
 for i,site in [(124,'right ventricle'),(125,'left ventricle'),(126,'pulmonary trunk'),(127,'tricuspid valve'),(128,'right ventricle and right atrium'),(129,'tricuspid annulus'),(131,'right atrium'),(132,'IVC'),(133,'interventricular septum and left ventricle'),(134,'right atrium/tricuspid orifice'),(139,'IVC'),(140,'IVC'),(141,'right ventricle'),(142,'right ventricle'),(143,'right atrium'),(144,'left popliteal vein'),(148,'bilateral pulmonary arteries')]:setf(i,{'body_site':site})
 setf(149,{'method':'CTPA on GE8-slice IMA660 scanner with intravenous Omnipaque350','body_site':'main and lobar pulmonary arteries'},'b00009')
 setf(164,{'method':'follow-up POCUS-TTE','body_site':'right ventricle and right atrium'},'b00020')
 for i in [174,176,177]:setf(i,{'indication':'anticoagulation following systemic thrombolysis for PE'},'b00023')
 for i in [186,187]:setf(i,{'indication':'post-discharge PE anticoagulation'},'b00026')
 setf(170,{'indication':'inflammatory status/severe acute pathology despite no definite infectious source'},'b00022')
 for i,ingredient in [(12,'acetylsalicylic acid'),(13,'diosmin and hesperidin'),(14,'pentoxifylline'),(15,'atorvastatin'),(170,'amoxicillin and clavulanic acid'),(176,'enoxaparin'),(177,'rivaroxaban'),(183,'atorvastatin')]:setf(i,{'ingredient_as_documented':ingredient})
 # Correct home/history ownership before expanding distinct ED stages.
 b.remove('p1',2,list(range(4,26)));b.attach('p1',7,[8,10,11]);b.attach('p1',1,list(range(18,26)))
 ecg=move(list(range(32,37)),'Admission ECG with acute RV-overload pattern',time='at 12:20')
 labs=move(list(range(37,99)),'ED arterial gas, CBC, serum biochemistry and coagulation workup; exact sampling clock varies by test')
 biomarkers=move([116,117,118],'ED point-of-care cardiac biomarkers',time='at 12:38')
 risk=move(list(range(99,116)),'Reported ED clinical probability and PE risk-score assessments; assessment clock unstated')
 cardiac=move(list(range(124,135))+list(range(139,144))+[146,147],'POCUS-TTE right-heart strain and mobile RA thrombus assessment',time='at 14:00')
 venous=move([135,136,137,138,144,145],'Venous ultrasound: asymptomatic unstable left popliteal/femoral/tibial DVT')
 ct=move([148,149,150,151,152],'CTPA-confirmed bilateral PE and anatomical/clinical severity assessment',time='at 17:08 p.m.')
 screen=b.new('p1','Focused bleeding-risk evaluation before systemic thrombolysis',[154,155,156,157,158,159],time='Before administering systemic thrombolysis')
 safety=b.new('p1','Safety surveillance during and immediately after thrombolytic infusion: no major bleeding',[162],time='during and immediately after the infusion')
 inpatient=b.new('p1','Interventional Cardiology hospitalization with antihypertensive diuresis and continuing statin therapy',[166,182,183])
 b.attach('p1',3,[153,161]);b.attach('p1',4,[164,165]);b.attach('p1',5,[167,186]);b.attach('p1',2,[168,179,180,181]);b.attach('p1',10,[171]);b.attach('p1',8,[175]);b.attach('p1',6,[190,191]);
 # Additional exact field representations use the same evidence, never independent invented encounters.
 for label,val in [('Systolic',139),('Diastolic',104)]:
  num=add('observations',{'name':'ED '+label.lower()+' blood pressure','kind':'vital','status':'resulted','numeric_value':val,'unit':'mmHg','text_value':'139/104','time':facts[28]['fields']['time']},label+' component of source139/104mmHg BP.',facts[28]['evidence']);b.attach('p1',2,[num])
 for name in ['myocardial infarction','PE','aortic dissection']:
  num=add('conditions',{'name':'Initial '+name+' differential','assertion':'possible','verification_status':'differential','role':'differential'},'Source initially considered '+name+' before confirmatory workup.',[{'segment_id':'b00004','quote':b.seg['b00004']}]);b.attach('p1',2,[num])
 num=add('procedures_devices',{'name':'CT pulmonary angiography','kind':'procedure','action':'completed','body_site':'pulmonary arteries','laterality':'bilateral','indication':'confirm PE and assess vascular involvement'},'Actual17:08CTPA with intravenous contrast.',[{'segment_id':'b00017','quote':b.seg['b00017']},{'segment_id':'b00009','quote':b.seg['b00009']}]);b.attach('p1',ct,[num])
 num=add('medications',{'name':'Omnipaque350','ingredient_as_documented':'Omnipaque350','action':'administered','route':'intravenous','indication':'CTPA contrast'},'Source names IV Omnipaque350 contrast, not a numeric dose or volume.',[{'segment_id':'b00009','quote':b.seg['b00009']}]);b.attach('p1',ct,[num])
 for name,val in [('amoxicillin',1000),('clavulanic acid',200)]:
  num=add('medications',{'name':name+' component of Amoxiplus','ingredient_as_documented':name,'action':'administered','route':'oral','dose_value':val,'dose_unit':'mg','dose_text':'1000/200 mg combination','frequency':'every12h','duration_text':'from first day of admission until discharge','regimen':'coadministered1000/200mg amoxicillin/clavulanic acid'},'Component quantity of source-reported oral1000/200mg combination; original combined regimen retained.',facts[170]['evidence']);b.attach('p1',11,[num])
 for name,val in [('diosmin',450),('hesperidin',50)]:
  num=add('medications',{'name':name+' component of Detralex','ingredient_as_documented':name,'action':'historical','dose_value':val,'dose_unit':'mg','dose_text':'Detralex500mg combination with diosmin450mg and hesperidin50mg','frequency':'every12h','regimen':'diosmin450mg plus hesperidin50mg'},'Explicit component amount in chronic Detralex regimen.',facts[13]['evidence']);b.attach('p1',7,[num])
 b.save()
