import json
from pathlib import Path
from schema_targets import materialize
r=Path('benchmarks/dense500');aid='PMC13587162.1';a=json.load(open(r/'drafts'/f'{aid}.json'));s=json.load(open(r/'sources'/f'{aid}.json'));seg={x['segment_id']:x['text'] for x in s['segments']};p=a['patients'][0];fs={f['fact_id']:f for f in p['facts']};q={}
def assign(nums,text):
 assert text in seg['b00005'],text
 for n in nums:q[f'p1-f{n:03d}']=[text]
assign([1],'The patient was a 13-year-old boy who presented with the primary complaint of abdominal pain. The symptoms began 1 day prior to his emergency room visit on September 2, 2024.')
assign([2,3],'The patient was a 13-year-old boy')
assign([4,5],'His relevant medical history includes chronic rhinitis, a deviated septum, and prior admissions for respiratory infections, specifically bronchiolitis and pneumonia, at 3 years of age.')
assign([6,7,8],'prior admissions for respiratory infections, specifically bronchiolitis and pneumonia, at 3 years of age')
assign([9],'In August 2024, occult blood was detected during the national student health examination system.')
assign([10],'the finding was not considered serious at the time, and the patient did not seek medical attention.')
assign([11],'The symptoms began 1 day prior to his emergency room visit on September 2, 2024.');q['p1-f011'].append('During the recent acute presentation, the patient reported abdominal pain localized around the umbilical area.')
assign([12,13,14],'The patient had no fever, diarrhea, or other gastrointestinal complaints.')
assign([15,16],'Upon physical examination, diffuse abdominal tenderness was observed, without tenderness over the costovertebral angles.')
assign([17,18,19,20],"The patient's height and weight were 152.2 cm (12.5 percentile), and 45.1 kg (21.2 percentile), respectively.")
assign([21,22],'blood pressure, 128/62 mmHg');assign([23],'pulse rate, 78 beats per minute');assign([24],'respiratory rate, 22 times per minute');assign([25],'body temperature, 36.4 ℃')
assign([26,27],'An initial erect abdominal radiograph revealed tiny high-density lesions in both kidneys (Fig. 1).')
labs=['white blood cell count, 9,200/µL (segmental neutrophils, 46.0%; lymphocytes, 38.0%)','segmental neutrophils, 46.0%','lymphocytes, 38.0%','hemoglobin, 12.5 g/dL','hematocrit, 37.7%','platelets, 348,000/µL','albumin, 4.3 g/dL','total protein, 6.6 g/dL','total bilirubin, 0.2 mg/dL','aspartate aminotransferase, 22 IU/L','alanine aminotransferase, 17 IU/L','blood urea nitrogen, 12.3 mg/dL','creatinine (Cr), 0.67 mg/dL','sodium, 139 mmol/L','potassium, 3.9 mmol/L','uric acid, 8.5 mg/dL','total calcium, 10.5 mg/dL','phosphorus, 3.2 mg/dL','The erythrocyte sedimentation rate was 13 mm/hr','the C-reactive protein level was 0.01 mg/dL']
for n,text in enumerate(labs,28):assign([n],text)
start=seg['b00005'].index('Laboratory findings included the following:');end=seg['b00005'].index('The urinalysis results were as follows:');assign([48],seg['b00005'][start:end].strip())
ur=['specific gravity, 1.022','pH, 6.5','protein, 1+','The urine protein to Cr ratio was 0.13 mg/mg Cr.','The random urine calcium to Cr ratio was 187.6 mg/g.','The urine N-acetyl-beta-D-glucosaminidase-to-Cr ratio was 14.89 IU/g','the urine β2-microglobulin level was 0.30 mg/dL.','red blood cells, many/high-power field (HPF)','white blood cells, 20–29/HPF']
for n,text in enumerate(ur,49):assign([n],text)
assign([58],'The urinalysis results were as follows: specific gravity, 1.022; pH, 6.5; protein, 1+; red blood cells, many/high-power field (HPF); and white blood cells, 20–29/HPF.')
assign([59],'The urine protein to Cr ratio was 0.13 mg/mg Cr. The random urine calcium to Cr ratio was 187.6 mg/g. The urine N-acetyl-beta-D-glucosaminidase-to-Cr ratio was 14.89 IU/g, and the urine β2-microglobulin level was 0.30 mg/dL.')
ct='Computed tomography of the kidney and bladder revealed a stone measuring 0.5×0.8 cm in the right distal ureter with hydronephrosis as well as multiple small calyceal stones in both kidneys (up to 0.5×0.6 cm) (Fig. 2).'
assign(range(60,71),ct);q['p1-f060'].insert(0,'An additional non-contrast computed tomography scan was performed to confirm the diagnosis.')
collection="The patient's 24-hour urine collection, performed shortly after admission and prior to extracorporeal shock wave lithotripsy (ESWL), was analyzed."
assign([71],collection);assign([72],'The 24-hour urine citric acid level was 109 mg/day, which is below the normal range (>150 mg/day)');assign([73],'while the calcium level was 77.76 mg/day, within the normal range.')
q['p1-f072'].append(collection);q['p1-f073'].append(collection)
assign([74],'After ESWL for bilateral renal stones, the retrieved stone fragments were subjected to composition analysis using the wet chemical method.')
composition='The analysis identified calcium, oxalate, uric acid, phosphate, and magnesium, indicating a mixed-type stone, primarily composed of calcium oxalate and calcium phosphate'
assign(range(75,82),composition)
for n in range(75,82):q[f'p1-f{n:03d}'].insert(0,'the retrieved stone fragments were subjected to composition analysis using the wet chemical method.')
follow='After 3 weeks, follow-up laboratory findings showed a further increase in serum calcium to 13.0 mg/dL (normal range, 8.6–10.2 mg/dL), with alkaline phosphatase elevated to 2,232 IU/L (normal range, 276–855 IU/L).'
assign([82,83],follow);assign([84],'The intact parathyroid hormone (iPTH) level was markedly elevated at 296.2 pg/mL (normal range, 10–65 pg/mL)');assign([85],'the 25-hydroxy vitamin D level was low at 13.5 ng/mL (normal range, 30–100 ng/mL).')
for n in [84,85]:q[f'p1-f{n:03d}'].insert(0,'After 3 weeks, follow-up laboratory findings')
assign([87],follow);q['p1-f087']+=[q['p1-f084'][-1],q['p1-f085'][-1]]
assign([88,89],'Technetium-99m sestamibi parathyroid scintigraphy identified a parathyroid mass at the anterior portion of the right thyroid gland (Fig. 3).')
assign([93,94,95],'Genetic testing for the MEN1 gene revealed no mutations.')
assign([96,97],'The patient was transferred to another hospital for surgical treatment')
assign([98],'a calcium-sensing receptor agonist, such as cinacalcet, was administered while awaiting surgery.')
assign([99],'Eventually, the patient underwent a successful parathyroidectomy')
assign([100,101,102],'leading to normalization of calcium and iPTH levels postoperatively.')
for f in p['facts']:
 fid=f['fact_id'];v=f['fields'];task=f['task']
 if fid in q:f['evidence']=[{'segment_id':'b00005','quote':text} for text in q[fid]]
 if task=='case_context':stmt='A 13-year-old boy presented to the emergency room with abdominal pain.'
 elif task=='demographics':stmt='Age at presentation was 13 years.' if v['attribute']=='age_at_presentation' else 'Documented sex: boy.'
 elif task=='conditions':stmt=('History of ' if v.get('role')=='history' else 'Source diagnosis: ')+v['name']+'.'
 elif task=='care_plans':stmt='No medical attention sought after screening.' if v['assertion']=='absent' else 'Surgical treatment was planned at transfer.'
 elif task=='symptoms_function':stmt=('No ' if v['assertion']=='absent' else '')+v['name']+'.';stmt='Periumbilical abdominal pain began 1 day before the ER visit.' if fid=='p1-f011' else stmt
 elif task=='observations':
  val=v.get('text_value')
  if v.get('numeric_value') is not None:val=str(v['numeric_value'])+(' '+v['unit'] if v.get('unit') else '')+(('; '+val) if val else '')
  stmt=v['name']+': '+str(val)+'.'
 elif task=='procedures_devices':stmt=v['name']+' was completed'+(': '+v['finding'] if v.get('finding') else '')+'.'
 elif task=='medications':stmt='A calcium-sensing receptor agonist was administered while awaiting surgery; cinacalcet was named as an example, with exact agent uncertain.'
 elif task=='family_genetics':stmt='MEN1 genetic testing identified no mutations.'
 elif task=='outcomes':stmt=v['text_value']+'.'
 else:stmt=f['clinical_statement']
 f['clinical_statement']=stmt
# Rebuild event citations from relevant fact clauses; keep exact time support.
for e in p['events']:
 e['evidence']=[]
 for fid in e['fact_ids']:
  for ev in fs[fid]['evidence']:
   if ev not in e['evidence']:e['evidence'].append(ev)
 assert not e['time_text'] or any(e['time_text'] in ev['quote'] for ev in e['evidence'])
# Narrow prior broad ordering evidence to source clauses for the linked events.
ees={e['event_id']:e for e in p['events']}
for edge in p['temporal_relations']:
 if any(ev['segment_id']=='b00005' and len(ev['quote'])>2000 for ev in edge['evidence']):
  evidence=[]
  for eid in [edge['from_event_id'],edge['to_event_id']]:
   for ev in ees[eid]['evidence']:
    if ev['segment_id']=='b00005' and ev not in evidence:evidence.append(ev)
  if edge['from_event_id']=='p1-e017':evidence.insert(0,{'segment_id':'b00005','quote':'These findings support a metabolic etiology, consistent with the diagnosis of pHPT.'})
  edge['evidence']=evidence
for fig in a['figures']:
 fid=fig['figure_id'];callout=q['p1-f026'][0] if fid.startswith('f1') else ct if fid.startswith('f2') else q['p1-f088'][0]
 for panel in fig['panels']:panel['evidence']=[{'segment_id':fig['caption_evidence'][0]['segment_id'],'quote':panel['description_from_caption']},{'segment_id':'b00005','quote':callout}]
for ambiguity in p['ambiguities']:
 ambiguity['evidence']=[ev for ev in ambiguity['evidence'] if not(ev['segment_id']=='b00005' and len(ev['quote'])>2000)]
 if 'Reported normal' in ambiguity['issue']:ambiguity['evidence']+=[{'segment_id':'b00005','quote':x} for x in [q['p1-f072'][0],q['p1-f073'][0],q['p1-f009'][0],follow]]
(r/'drafts'/f'{aid}.json').write_text(json.dumps(a,ensure_ascii=False,indent=2)+'\n');(r/'schema-drafts'/f'{aid}.json').write_text(json.dumps(materialize(a,s),ensure_ascii=False,indent=2)+'\n');print('largest statement',max(len(f['clinical_statement']) for f in p['facts']),'largest b5 quote',max(len(ev['quote']) for f in p['facts'] for ev in f['evidence'] if ev['segment_id']=='b00005'))
