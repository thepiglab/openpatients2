"""Apply requested parent corrections to unsigned IDH draft and visuals."""
import json,re
from pathlib import Path
r=Path('benchmarks/dense500');aid='PMC12880940.1';a=json.load(open(r/'drafts'/f'{aid}.json'));s=json.load(open(r/'sources'/f'{aid}.json'));seg={x['segment_id']:x['text'] for x in s['segments']}
def ev(sid,q):assert q in seg[sid];return dict(segment_id=sid,quote=q)
for p in a['patients']:
 pid=p['patient_id'];one=pid=='p1';sid='b00004' if one else 'b00005';d={f['fact_id']:f for f in p['facts']};es={e['event_id']:e for e in p['events']}
 f=d[pid+'-f038'];f['task']='care_plans';f['fields']=dict(subject='index_patient',assertion='present',action_text='Conservative management',plan_type='other',action_status='completed');f['clinical_statement']='Conservative management was delivered; components and treatment modality not specified.';f['notes']='Source does not specify components; no positive nonpharmacologic classification. Improvement remains a separate outcome.'
 f=d[pid+('-f053' if one else '-f050')];q='the patient and his family did not request follow-up cardiac MRI, LV angiography, and other examinations' if one else 'the patient refused to undergo further cardiac magnetic resonance imaging (MRI) examination.';sid2='b00010' if one else 'b00006';f['evidence'].append(ev(sid2,q));f['notes']='Confirmatory MRI not pursued/refused as explicitly documented; neither case being diagnosed by MRI alone would not establish nonperformance.'
 if not one:
  for n in ['051','052']:
   f=d[pid+'-f'+n];f['fields']['flag']='unknown';f['notes']='Source labels the84/60mmHg pair low; component retained numerically without a separate systolic/diastolic threshold interpretation.'
 def event(desc,ids,time=None):
  eid=f'{pid}-e{len(p["events"])+1:03d}';evidence=[]
  for fid in ids:
   for e in d[fid]['evidence']:
    if e not in evidence:evidence.append(e)
  p['events'].append(dict(event_id=eid,description=desc,occurrence='occurred',fact_ids=ids,time_text=time,evidence=evidence));return eid
 def before(x,y,sid,q):
  row=dict(from_event_id=x,to_event_id=y,relation='before',evidence=[ev(sid,q)])
  if not any(e['from_event_id']==x and e['to_event_id']==y and e['relation']=='before' for e in p['temporal_relations']):p['temporal_relations'].append(row)
 # Management begins before its initial response; no assumed drug/device regimen.
 tx=es[pid+('-e003' if one else '-e002')];tx['description']='Conservative management (components unspecified)';tx['fact_ids']=[pid+'-f038'];tx['evidence']=d[pid+'-f038']['evidence']
 response=event('Initial symptom improvement' if one else 'Condition improved following conservative management',[pid+'-f039'])
 before(tx['event_id'],response,'b00004' if one else 'b00006',seg['b00004' if one else 'b00006'])
 before(response,pid+('-e004' if one else '-e005'),'b00004' if one else 'b00006',seg['b00004' if one else 'b00006'])
 if one:
  onset1=event('Diarrhea onset before presentation; duration at admission documented as for26days',[pid+'-f007'],'for 26 days')
  onset2=event('Fever onset within the last7days before admission; no symptom end asserted',[pid+'-f008'],'during the last 7 days')
  q='was admitted with diarrhea that had persisted for 26 days and fever that persisted during the last 7 days.'
 else:
  hist=event('Preexisting diabetes documented in admission history',[pid+'-f004'])
  before(hist,pid+'-e001','b00005','A 69-year-old woman with diabetes was admitted to our hospital due to chest pain for 10 days and sudden loss of consciousness for 4 h.')
  onset1=event('Chest-pain onset before presentation; duration at admission documented as for10days',[pid+'-f005'],'for 10 days')
  onset2=event('Sudden loss-of-consciousness onset before admission; duration at admission documented as for4h',[pid+'-f006'],'for 4 h')
  q='was admitted to our hospital due to chest pain for 10 days and sudden loss of consciousness for 4 h.'
  es[pid+'-e001']['fact_ids'].remove(pid+'-f004')
 before(onset1,onset2,sid,q);before(onset2,pid+'-e001',sid,q)
 for fid in ([pid+'-f007',pid+'-f008'] if one else [pid+'-f005',pid+'-f006']):es[pid+'-e001']['fact_ids'].remove(fid)
 # Explicit post-discharge death establishes occurrence of discharge, but no discharge date.
 dsid='b00004' if one else 'b00006';dq='The patient died after one week post-discharge.' if one else 'the patient died approximately 1 week after discharge from the hospital.'
 fid=f'{pid}-f{len(p["facts"])+1:03d}';df=dict(fact_id=fid,task='outcomes',collection='items',fields=dict(subject='index_patient',assertion='present',event='discharge',text_value='Discharged from hospital before subsequent death; discharge date unstated.',disposition='discharged from hospital'),evidence=[ev(dsid,dq)],clinical_statement='Hospital discharge occurred before the documented one-week post-discharge death.',certainty='supported',scoring='include',notes='Source explicitly places death after discharge; exact discharge day and destination not reported.');p['facts'].append(df);d[fid]=df
 de=event('Hospital discharge; exact discharge day not reported',[fid]);death=pid+('-e005' if one else '-e003');before(de,death,dsid,dq);before(pid+('-e007' if one else '-e005'),de,dsid,seg[dsid])
 # Rebuild literal event evidence after annotation enrichment without changing stable IDs.
 for e in p['events']:
  for fid in e['fact_ids']:
   for v in d[fid]['evidence']:
    if v not in e['evidence']:e['evidence'].append(v)
 for dom in p['domain_coverage']:
  n=sum(f['task']==dom for f in p['facts']);p['domain_coverage'][dom]=dict(status='documented' if n else 'not_documented',note=f'{n} source-grounded representations.' if n else 'No patient-specific documentation; no negative inferred.')
 # Put history/onsets first and actual management before its response in the record list.
 order=([pid+'-e006',onset1,onset2,pid+'-e001',pid+'-e008',pid+'-e002',pid+'-e009',tx['event_id'],response,pid+'-e004',pid+'-e007',de,death] if one else [hist,onset1,onset2,pid+'-e001',pid+'-e004',tx['event_id'],response,pid+'-e005',de,death])
 em={e['event_id']:e for e in p['events']};assert len(order)==len(em);p['events']=[em[x] for x in order]
 p['summary'][-1]['fact_ids'].append(fid)
 # Temporal onset labels are descriptive, with raw literal duration strings retained.
 for e in p['events']:e['description']=e['description'].replace('for26days','for 26 days').replace('last7days','last 7 days').replace('for10days','for 10 days').replace('for4h','for 4 h')
a['annotation_provenance']['passes'].append('Parent-requested source recheck: unspecified conservative-management modality moved to CarePlans; refusal/non-request evidence for confirmatory MRI; pair-level BP low versus unknown component flags; symptom-onset partial chronology, preexisting diabetes history and undated discharges before one-week post-discharge deaths; management separated from response. All20original units and114final clinical representations reread.')
# Visual corrections after actual reinspection of all six assets.
v=json.load(open(r/'visual-submissions'/f'{aid}.json'))
def clean(x):
 if isinstance(x,str):
  for phrase in [', with a lower continuous strip',', with a continuous lower strip',', with a long lower rhythm strip']:x=x.replace(phrase,'')
  return x.replace('three white arrows point','multiple white arrows point')
 if isinstance(x,list):return [clean(t) for t in x]
 if isinstance(x,dict):return {k:clean(t) for k,t in x.items()}
 return x
v=clean(v)
for f in v['figures']:
 n=int(f['figure_id'][1]);vv=f['analysis']['visual']
 if n in [1,4,6]:
  for p in vv['panels']:
   p['detailed_description']+=' Six limb-lead rows are visible on the left and six V1–V6 rows on the right; the bottom traces belong to aVF and V6.'
   p['pixel_observations'].append('Six limb-lead rows at left and six chest-lead rows at right; bottom row is aVF/V6.')
  vv['detailed_description']+=' Six rows per column; bottom row is aVF/V6.'
 if n not in [2,3,5]:continue
 sid={2:'b00014',3:'b00015',5:'b00017'}[n];text=seg[sid];markers=list(re.finditer(r'\(([A-D])\)',text));clauses={m.group(1):text[m.start():markers[i+1].start() if i+1<len(markers) else len(text)].strip() for i,m in enumerate(markers)}
 intro=text[text.index('\n')+1:markers[0].start()].strip()
 for p in vv['panels']:
  q=clauses[p['panel']];ce=ev(sid,q)
  p['caption_claims']=[dict(value=q,as_documented=q,basis='caption',visual_cue=None,evidence=[ce],certainty='clear')]
  if intro:p['caption_claims'].append(dict(value=intro,as_documented=intro,basis='caption',visual_cue=None,evidence=[ev(sid,intro)],certainty='clear'))
  p['clinical_significance']=[dict(statement='Authors describe this panel as: '+q,role='disease_course' if n==3 else 'diagnostic_evidence',clinical_fact_status='unreviewed_annotation',basis='caption',visual_cue=None,evidence=[ce],certainty='clear')]
  if n==3:
   q2='Whether the echo-free cavity at the apical cap location in Case 1 is a myocardial dissection or a pseudoaneurysm is controversial.'
   p['clinical_significance'].append(dict(statement='The body leaves Case1 apical myocardial dissection versus pseudoaneurysm controversial without confirmatory imaging.',role='differential_diagnosis',clinical_fact_status='unreviewed_annotation',basis='article',visual_cue=None,evidence=[ev('b00010',q2),ev('b00010','the patient and his family did not request follow-up cardiac MRI, LV angiography, and other examinations')],certainty='uncertain'))
  if n==5 and p['panel'] in ['A','C','D']:
   q2='intramyocardial dissection hematoma in the basal segment of the inferior posterior wall of the LV, pericardial effusion (Fig. 6).'
   p['clinical_significance'].append(dict(statement='Narrative basal inferior-posterior LV location and figure callout differ from this caption panel location; relationship remains unresolved.',role='differential_diagnosis',clinical_fact_status='unreviewed_annotation',basis='article',visual_cue=None,evidence=[ev('b00006',q2),ce],certainty='uncertain'))
 # Text-only companion panel descriptions retain matching panel scope.
 af=next(af for af in a['figures'] if af['figure_id']==f['figure_id'])
 for ap in af['panels']:
  if ap['panel'] in clauses:ap['description_from_caption']=clauses[ap['panel']];ap['evidence']=[ev(sid,clauses[ap['panel']]),ev(sid,intro)]
(r/'drafts'/f'{aid}.json').write_text(json.dumps(a,ensure_ascii=False,indent=2)+'\n');(r/'visual-submissions'/f'{aid}.json').write_text(json.dumps(v,ensure_ascii=False,indent=2)+'\n')
print([(p['patient_id'],len(p['facts']),len(p['events']),len(p['temporal_relations'])) for p in a['patients']])
