"""Source-grounded event enrichment for this annotator's own recent drafts."""
import json
from pathlib import Path
from benchmarks.dense500.annotator1_build import Build
BASE=Path('benchmarks/dense500')
IDS=['PMC12880940.1','PMC12811047.1','PMC12312621.1','PMC12611218.1','PMC12754810.1','PMC12766325.1','PMC12832185.1','PMC12786310.1','PMC12811543.1']
for aid in IDS:
 b=Build(aid);path=BASE/'drafts'/f'{aid}.json';b.a=json.loads(path.read_text())
 for p in b.a['patients']:
  b.p=p
  def pick(fn):return [f['fact_id'] for f in p['facts'] if fn(f)]
  def fields(name,task=None):return pick(lambda f:f['fields'].get('name')==name and (task is None or f['task']==task))
  def ev(desc,fn,time=None,occ='occurred'):
   ids=pick(fn)
   if not ids:return None
   if any(e['description']==desc for e in p['events']):return next(e['event_id'] for e in p['events'] if e['description']==desc)
   return b.event(desc,ids,time,occ)
  def existing(desc):return next(e['event_id'] for e in p['events'] if e['description']==desc)
  def order(x,y,s,q=None):
   if x and y:b.before(x,y,s,q or b.seg[s]['text'])
  ev('Documented background clinical history',lambda f:f['fields'].get('subject')=='index_patient' and (f['fields'].get('role')=='history' or f['fields'].get('action')=='historical' or f['fields'].get('temporality')=='historical'))
  remove={e['event_id'] for e in p['events'] if e['description']=='Recommended or conditional future care'}
  p['events']=[e for e in p['events'] if e['event_id'] not in remove]
  p['temporal_relations']=[r for r in p['temporal_relations'] if r['from_event_id'] not in remove and r['to_event_id'] not in remove]
  remap={e['event_id']:f'{p["patient_id"]}-e{i:03d}' for i,e in enumerate(p['events'],1)}
  for e in p['events']:e['event_id']=remap[e['event_id']]
  for r in p['temporal_relations']:r['from_event_id']=remap[r['from_event_id']];r['to_event_id']=remap[r['to_event_id']]
  plans=pick(lambda f:(f['task']=='care_plans' and f['fields'].get('action_status') in ['planned','recommended','scheduled']) or (f['task']=='medications' and f['fields'].get('action')=='planned'))
  for fid in plans:
   fact=next(f for f in p['facts'] if f['fact_id']==fid)
   desc='Care plan: '+fact['clinical_statement']
   if not any(e['description']==desc for e in p['events']):b.event(desc,[fid],time=fact['fields'].get('time',{}).get('text'),occurrence='uncertain' if fact['fields'].get('assertion')=='possible' else 'planned')
  for fact in p['facts']:
   if fact['fields'].get('action')=='declined' or fact['fields'].get('action_status')=='declined':
    desc='Decision to decline: '+fact['clinical_statement']
    if not any(e['description']==desc for e in p['events']):b.event(desc,[fact['fact_id']])
  if aid=='PMC12880940.1' and p['patient_id']=='p2':
   ev('Cardiac ECG/echo workup and clinical diagnoses',lambda f:f['task'] in ['observations','conditions'] and any(e['segment_id']=='b00006' for e in f['evidence']))
  if aid=='PMC12811047.1':
   for e in p['events']:
    if e['description']=='First-month symptomatic improvement':
     ids=pick(lambda f:f['fields'].get('time',{}).get('text')==('One month after starting thiamine replacement' if p['patient_id']=='p1' else 'Within one month'))
     e['fact_ids']=sorted(set(e['fact_ids']+ids))
    if e['description']=='Later improvement and stenosis reassessment':
     ids=pick(lambda f:f['fields'].get('time',{}).get('text') in ['Follow-up','After eight months','After three months'])
     e['fact_ids']=sorted(set(e['fact_ids']+ids))
  if aid=='PMC12611218.1':
   if p['patient_id']=='p1':
    x=ev('STEMI/atrial fibrillation treated with thrombectomy',lambda f:f['fields'].get('name') in ['ST-elevation myocardial infarction','Atrial fibrillation','Thrombectomy'])
    y=ev('Recurrent heart failure led to dobutamine dependence',lambda f:f['fields'].get('name')=='dobutamine' or f['fields'].get('event')=='readmission')
    order(x,y,'b00002');order(y,existing('LVAD implantation'),'b00002')
    ev('Complete inotrope withdrawal documented in table',lambda f:any(e['segment_id']=='b00033' for e in f['evidence']),'POD 64')
    ev('Late imaging follow-up at 56 months',lambda f:f['fields'].get('name')=='Follow-up chest radiography','August 2025 (56 months postoperatively)')
   elif p['patient_id']=='p2':
    x=ev('Cardiac arrest, E-CPR, intubation and VA-ECMO',lambda f:f['fields'].get('name') in ['Cardiac arrest','E-CPR','Endotracheal intubation','VA-ECMO'],'On hospital day 15')
    order(x,existing('LVAD implantation'),'b00004')
    ev('Right pneumothorax required chest tube',lambda f:f['fields'].get('name') in ['Right pneumothorax','Chest tube placement'])
    ev('Postoperative RV and respiratory complications',lambda f:f['fields'].get('name') in ['RV dysfunction','Tracheostomy','Home ventilator','Left upper pneumothorax','Closed thoracostomy','Tubing-site bleeding'])
    ev('Complete inotrope withdrawal documented in table',lambda f:any(e['segment_id']=='b00033' for e in f['evidence']),'POD 51')
    ev('Low INR period with bleeding/recurrent thoracostomy',lambda f:f['fields'].get('name')=='Average INR','approximately one month from POD 90')
   else:
    x=ev('Shock/arrest required difficult cannulation, E-CPR and ECMO',lambda f:f['fields'].get('name') in ['Shock','Recurrent cardiac arrest','ECMO cannulation','E-CPR','Intubation','ECMO'])
    order(x,existing('LVAD implantation'),'b00006')
    ev('Postoperative extubation',lambda f:f['fields'].get('name')=='Extubation','2 days postoperatively')
    ev('Warfarin held during ulcer healing',lambda f:f['fields'].get('name')=='warfarin')
    ev('Complete inotrope withdrawal documented in table',lambda f:any(e['segment_id']=='b00033' for e in f['evidence']),'POD 5')
    ev('Reported late follow-up without late complication',lambda f:any(e['segment_id']=='b00036' for e in f['evidence']),'>1 year' if '>1 year' in b.seg['b00036']['text'] else '>1 year' if '>1 year' in b.seg['b00036']['text'].replace(' ','') else None)
  if aid=='PMC12312621.1':
   x=ev('Initial potassium-lowering treatment and RAAS withdrawal',lambda f:f['task']=='medications' and f['fields'].get('name') in ['calcium gluconate','short acting insulin','glucose','ventolin','insulin glucose infusion','salbutamol inhaler','sodium polystyrene sulfonate','ACEi','ramipril','spironolactone','candesartan'] and f['fields'].get('action') in ['started','administered','stopped'] and (f['fields'].get('name') not in ['ACEi','ramipril','spironolactone','candesartan'] or f['fields'].get('action')=='stopped') and not (f['fields'].get('name')=='insulin glucose infusion' and f['fields'].get('action')=='stopped'))
   e=next(e for e in p['events'] if e['event_id']==x)
   e['fact_ids']=pick(lambda f:f['task']=='medications' and f['fields'].get('name') in ['calcium gluconate','short acting insulin','glucose','ventolin','insulin glucose infusion','salbutamol inhaler','sodium polystyrene sulfonate','ACEi','ramipril','spironolactone','candesartan'] and f['fields'].get('action') in ['started','administered','stopped'] and (f['fields'].get('name') not in ['ACEi','ramipril','spironolactone','candesartan'] or f['fields'].get('action')=='stopped') and not (f['fields'].get('name')=='insulin glucose infusion' and f['fields'].get('action')=='stopped'))
   if p['patient_id']=='p1':
    y=ev('Persistent potassium elevation after IGI termination',lambda f:f['fields'].get('name')=='Potassium after IGI termination','at the 32nd hour of hospitalization');z=ev('SZC added as treatment escalation',lambda f:f['fields'].get('name')=='sodium zirconium cyclosilicate');order(x,y,'b00006');order(y,z,'b00006');order(z,existing('Potassium improvement with treatment'),'b00006')
   elif p['patient_id']=='p2':
    order(x,existing('Potassium improvement with treatment'),'b00007');ev('One-week potassium improvement',lambda f:f['fields'].get('name')=='Later potassium','1 week later')
   else:
    y=ev('Discharge with dietary/potassium-binder recommendation',lambda f:f['fields'].get('event')=='discharge' or f['task']=='care_plans' and f['fields'].get('action_status')=='recommended')
    z=ev('Home follow-up without recurrent hyperkalemia and improved creatinine',lambda f:any(e['segment_id']=='b00008' for e in f['evidence']) and f['fields'].get('name')=='Follow-up creatinine' or f['task']=='outcomes' and ('followed-up at home' in f['clinical_statement'] or 'No recurrent hyperkalemia' in f['clinical_statement']))
    order(existing('Potassium improvement with treatment'),y,'b00008');order(y,z,'b00008')
  if aid=='PMC12754810.1':
   x=ev('Therapeutic enoxaparin bridge',lambda f:f['fields'].get('name')=='enoxaparin')
   order(x,existing('DOAC started at discharge'),'b00012' if p['patient_id']=='p1' else 'b00024')
   if p['patient_id']=='p2':ev('Hydroxycarbamide dose increased after thrombotic episode',lambda f:f['fields'].get('name')=='hydroxycarbamide' and 'increased' in f['evidence'][0]['quote'])
  if aid=='PMC12766325.1' and p['patient_id']=='p1':
   ev('Next-day asymptomatic DWI lesion',lambda f:f['fields'].get('name')=='Postoperative DWI MRI','the following day')
   x=ev('Postprocedural reduction to one antiplatelet agent',lambda f:f['fields'].get('name')=='Single antiplatelet agent','after the procedure');order(existing('Endovascular aneurysm treatment'),x,'b00005')
  if aid=='PMC12786310.1':
   x=ev('Post-thrombolysis unfractionated-heparin infusion',lambda f:f['fields'].get('name')=='Unfractionated heparin','12 h after completion of thrombolysis')
   y=ev('Enoxaparin anticoagulation phase',lambda f:f['fields'].get('name')=='enoxaparin (Clexane)','At 24 h post-thrombolysis')
   z=ev('Day4 oral rivaroxaban and renal recovery',lambda f:f['fields'].get('name')=='rivaroxaban (Xarelto)' or f['task']=='outcomes' and 'renal function' in f['clinical_statement'],'starting on day 4')
   order(existing('Alteplase systemic thrombolysis'),x,'b00023');order(x,y,'b00023');order(y,z,'b00023');order(z,existing('Discharge on anticoagulation'),'b00026')
   ev('Antibiotic course with negative infection evaluation',lambda f:f['fields'].get('name') in ['Amoxicillin–Clavulanic acid (Amoxiplus)','Day3procalcitonin','Ward chest radiograph','Repeated viral tests'])
   ev('Predischarge echo improvement',lambda f:f['fields'].get('name')=='Predischarge TTE')
  if aid=='PMC12811543.1':
   ev('Baseline symptoms, congestive examination and workup',lambda f:f['task'] in ['symptoms_function','observations','family_genetics'] and any(e['segment_id'] in ['b00012','b00013','b00014','b00015','b00032'] for e in f['evidence']))
   x=ev('Initial postimplant DAPT and heart-failure medications',lambda f:f['task']=='medications' and f['fields'].get('name') in ['aspirin','clopidogrel','metoprolol','sacubitril/valsartan','dapagliflozin','torasemide','spironolactone','Panangin'] and f['fields'].get('action') in ['started','current'])
   order(existing('LAAC evaluation and implantation'),x,'b00017');order(x,existing('Early device thrombosis'),'b00017')
   y=ev('November anemia, occult blood positivity and renal dysfunction',lambda f:f['fields'].get('time',{}).get('text')=='In November 2023','In November 2023')
   z=ev('Transient bleeding/renal abnormalities resolved',lambda f:f['task']=='outcomes' and 'November anemia/renal' in f['clinical_statement'],'within 1 month')
   order(existing('Warfarin treatment for first DRT'),y,'b00017');order(y,z,'b00017')
   ev('Long-term adherence problems, irregular INR and melena',lambda f:f['fields'].get('name') in ['Melena','warfarin'] and ('poor adherence' in str(f['evidence']) or f['fields'].get('name')=='Melena') or f['task']=='care_plans' and 'Irregular INR' in f['clinical_statement'])
 b.a['annotation_provenance']['passes'].append('Expanded events from existing exact-evidence facts to represent clinical phases, escalation, follow-up and conditional future care; partial supported order only, locally revalidated.')
 path.write_text(json.dumps(b.a,ensure_ascii=False,indent=2)+'\n');print(aid,[(p['patient_id'],len(p['events'])) for p in b.a['patients']])
