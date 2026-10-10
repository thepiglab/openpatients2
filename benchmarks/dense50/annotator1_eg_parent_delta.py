import json,re,copy,xml.etree.ElementTree as ET
from pathlib import Path
r=Path('benchmarks/dense50');aid='PMC12802722.1';path=r/'annotator-1-proposals'/f'{aid}.json';a=json.load(open(path));s=json.load(open(r/'sources'/f'{aid}.json'));sg={x['segment_id']:x['text'] for x in s['segments']}
def ev(sid,q):assert q in sg[sid],(sid,q);return dict(segment_id=sid,quote=q)
def unique(es):return list({(e['segment_id'],e['quote']):e for e in es}.values())
# Every replaced paragraph is narrowed to hand-checked source sentences and local context.
ctx={
'p1-f001': ['A 54-year-old man, who was a current smoker with no major illnesses in his medical history, was transferred to our department by the paramedics as a stroke alert, with a sudden onset of slurred speech, nystagmus, and gait disturbance that started in the morning (9:30 a.m.).'],
'p2-f001': ["A 78-year-old man with a history of hypertension, diabetes mellitus, and Parkinson's disease was admitted as a thrombolysis candidate with an ictal onset of vertigo, unsteadiness, and speech disturbance."],
'p3-f001': ['A 54-year-old man was transported to our department with a suspicion of an epileptic seizure.','According to his mother, the patient lost consciousness for a few minutes at 1:30 p.m. on the day of admission.','After that, he was confused and disoriented, and his speech was unintelligible.','In addition to the moderate-severe left-sided spastic hemiparesis and Babinksi sign due to the previous stroke, severe mixed aphasia and extreme agitation were found during the physical examination (NIHSS: 7 points).'],
'p1-f076': ['Within the next hours, a toxicological screening revealed an ethylene glycol serum concentration of 95.85 mg/dL, with toxic levels defined as >20 mg/dL and “possibly fatal” levels defined as >30 mg/dL (Table 2).'],
'p2-f089': ['An urgent toxicological screening proved ethylene glycol intoxication with a serum ethylene glycol level of 159 mg/dL.'],
'p3-f064': ['Although the toxicological screening was negative for ethanol, it revealed ethylene glycol intoxication, with an ethylene-glycol serum level of 16.54 mg/dL (6:35 p.m.).']}
for p in a['patients']:
 pid=p['patient_id'];sid={'p1':'b00004','p2':'b00048','p3':'b00049'}[pid];sentences=re.split(r'(?<=[.!?])\s+(?=[A-Z])',sg[sid]);facts={f['fact_id']:f for f in p['facts']}
 for f in p['facts']:
  if not any(len(e['quote'])>2000 for e in f['evidence']):continue
  short=[e for e in f['evidence'] if len(e['quote'])<=2000];quotes=ctx.get(f['fact_id'])
  if quotes is None:
   local=[e['quote'] for e in short if e['segment_id']==sid];quotes=[t for t in sentences if any(q in t for q in local)]
   assert quotes,(f['fact_id'],short)
   tm=f['fields'].get('time',{}).get('text')
   if tm and tm not in ' '.join(quotes):
    if pid=='p1' and tm=='On admission':quotes.insert(0,'On admission (1:20 p.m.)')
    elif pid=='p2' and tm=='On arrival (3 p.m.)':quotes.insert(0,'On arrival (3 p.m.)')
    elif pid=='p2' and tm=='At that time':quotes.insert(0,"a few hours later (9 p.m.), the patient's condition deteriorated rapidly and he lost consciousness, requiring intubation and mechanical ventilation.")
    else:assert tm in sg[sid],(f['fact_id'],tm);quotes.insert(0,tm)
  f['evidence']=unique(short+[ev(sid,q) for q in quotes])
 # Explicit relative clock anchors; historical occurrence is distinct from later report.
 for f in p['facts']:
  tm=f['fields'].get('time')
  if not tm:continue
  t=tm['text'].lower()
  if 'rt-pa' in t or 'thrombolysis' in t:tm['anchor']='rt-PA thrombolysis';tm['relation']='during' if 'during' in t else 'after' if 'after' in t else tm['relation']
  elif 'admission' in t or 'arrival' in t or 'hospitalization' in t or t in ['at 48 h','within 48 h','on the 2nd day','on day 9','the next day']:tm['anchor']='index hospital admission'
  elif t=='at that time':tm['anchor']='9 p.m. clinical deterioration'
 for n in ([31,32] if pid=='p1' else [63] if pid=='p2' else [13,14]):
  f=facts[f'{pid}-f{n:03}'];f['fields']['temporality']='historical'
  if pid=='p3':f['fields']['time']=dict(text='due to the previous stroke',relation='before',anchor='index hospital admission');f['evidence'].append(ev(sid,'In addition to the moderate-severe left-sided spastic hemiparesis and Babinksi sign due to the previous stroke, severe mixed aphasia and extreme agitation were found during the physical examination (NIHSS: 7 points).'));f['notes']='Pre-existing prior-stroke deficits persisted at the index examination; before refers to historical onset, not resolution before admission.'
  else:f['fields']['time'].update(relation='before',anchor='index hospital admission');f['notes']='Retrospective report of pre-admission mood/ideation history, learned during or after care; no symptom resolution before admission asserted.'
  f['evidence']=unique(f['evidence'])
 # Regenerate event evidence from the reviewed facts, avoiding copied entire cases.
 for e in p['events']:e['evidence']=unique([q for fid in e['fact_ids'] for q in facts[fid]['evidence']])
 for issue in p['ambiguities']:
  if any(len(q['quote'])>2000 for q in issue['evidence']):
   bodyq={'p1':'Arterial blood gas (ABG) analysis disclosed severe metabolic acidosis (pH: 7.03, PaCO2 21 mmHg, serum bicarbonate 5.9 mMol/L, base excess −22.7 mMol/L, anion gap: 24.7 mMol/L) (Table 1).','p2':'ABG analysis revealed extremely severe metabolic acidosis (pH < 6.8), an unmeasurable high lactate level (>15 mMol/L), low serum bicarbonate (< 3 mMol/L) level, and a wide anion gap (28 mMol/L) (Table 1).','p3':'An ABG analysis was obtained, but it showed physiological acid–base parameters (Table 1).'}[pid]
   issue['evidence']=[q for q in issue['evidence'] if len(q['quote'])<=2000]+[ev(sid,bodyq)]+[ev(sid,q) for q in ctx[{'p1':'p1-f076','p2':'p2-f089','p3':'p3-f064'}[pid]]]
 # Timeline edge evidence needs local clauses, not entire case paragraphs.
 for e in p['temporal_relations']:
  if any(len(q['quote'])>2000 for q in e['evidence']):
   es={v['event_id']:v for v in p['events']};e['evidence']=unique(es[e['from_event_id']]['evidence']+es[e['to_event_id']]['evidence'])
# Recover patient-cell bold markers directly from the raw XML; no external reference ranges.
t2=ET.parse(r/'raw-xml'/f'{aid}.xml').find(".//table-wrap[@id='T2']");bold=[];phase=None
for row in t2.findall('.//tbody/tr'):
 cells=row.findall('td')
 if len(cells)==1:phase='On admission' if 'On admission' in ''.join(cells[0].itertext()) else 'At 48 h';continue
 name=''.join(cells[0].itertext()).strip().split(' (')[0]
 for i,c in enumerate(cells[1:]):
  if c.find('.//bold') is None:continue
  val=''.join(c.itertext()).strip();p=a['patients'][i]
  matches=[f for f in p['facts'] if f['task']=='observations' and f['fields'].get('name')==name and f['fields'].get('text_value')==val and (f['fields'].get('time',{}).get('text')==phase or name=='EG' and phase=='On admission')]
  assert len(matches)==1,(i,name,val,phase,len(matches))
  f=matches[0];f['fields']['text_value']=val+'; marked abnormal in original Table 2 (bold)';f['fields']['flag']='unknown';f['evidence'].append(ev('b00047','Abnormal values are bolded.'));f['notes']=(f['notes']+' Original XML Table 2 patient cell is bold; foot explicitly defines bold as abnormal. Direction is unstated and is not inferred from reference ranges.').strip();bold.append(f['fact_id'])
# Narrative explicitly supports sodium low for Case 3, unlike directional inference from bold.
f=next(f for f in a['patients'][2]['facts'] if f['fact_id']=='p3-f050');f['fields']['flag']='low';f['evidence'].append(ev('b00049','On admission, laboratory parameters were significant for mild hyponatremia (122 mMol/L) (Table 2).'));f['notes']+=' Low direction supported by narrative hyponatremia.'
f=next(f for f in a['patients'][1]['facts'] if f['fact_id']=='p2-f070');f['fields']['assertion']='unknown';f['notes']='Table dash means result not reported; no affirmative measured result or normal value asserted.'
a['article_limitations'][0]='Raw Table 2 XML bold patient-cell formatting was personally inspected and preserved as marked-abnormal text with the explicit table foot. The runtime flag enum has no generic abnormal value; direction remains unknown unless narrative supports it. Unbolded values are not automatically normal. GFR table mL/min and narrative indexed units remain distinct.'
a['annotation_provenance']['passes'].append('Independent source/raw-T1-T2/all312 representations re-read; original T2 bold markers recovered without direction inference, histories/relative anchors corrected, copied full-case evidence condensed; actual F1 named Case regions independently inspected')
path.write_text(json.dumps(a,ensure_ascii=False,indent=2)+'\n');print('marked cells',len(bold),bold)
