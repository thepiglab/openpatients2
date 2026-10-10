import json
from pathlib import Path
r=Path('benchmarks/dense500');aid='PMC13587162.1';path=r/'drafts'/f'{aid}.json';a=json.loads(path.read_text());p=a['patients'][0];fs={f['fact_id']:f for f in p['facts']};s=json.load(open(r/'sources'/f'{aid}.json'));seg={x['segment_id']:x['text'] for x in s['segments']};full={'segment_id':'b00005','quote':seg['b00005']}
fs['p1-f010']['fields']['assertion']='absent';fs['p1-f010']['clinical_statement']='No medical attention sought after August 2024 screening.'
fs['p1-f011']['fields']['time'].update(relation='before',anchor='emergency room visit on September 2, 2024');fs['p1-f011']['fields']['text_value']='primary complaint; onset 1 day before ER visit on September 2, 2024'
fs['p1-f051']['fields']['text_value']='1+'
fs['p1-f060']['evidence']=[full]
fs['p1-f004']['fields'].pop('body_site',None);fs['p1-f005']['fields']['body_site']='septum'
fs['p1-f075']['fields']['name']='Retrieved stone-fragment composition analysis by wet chemical method';fs['p1-f075']['fields']['finding']='calcium, oxalate, uric acid, phosphate and magnesium; mixed type, chiefly calcium oxalate/calcium phosphate'
for fid in ['p1-f071','p1-f072','p1-f073']:
 fs[fid]['fields']['time']={'text':'shortly after admission','relation':'after','anchor':'admission'}
 if full not in fs[fid]['evidence']:fs[fid]['evidence'].append(full)
for fid in ['p1-f082','p1-f083','p1-f084','p1-f085','p1-f087']:
 fs[fid]['fields']['time']={'text':'After 3 weeks','relation':'after','anchor':'preceding clinical course; exact time-zero not specified'}
 if full not in fs[fid]['evidence']:fs[fid]['evidence'].append(full)
fs['p1-f098']['fields']['regimen']='Interim calcium-sensing receptor agonist; cinacalcet named as an example, exact administered agent uncertain.'
fs['p1-f098']['fields']['time']={'text':'while awaiting surgery','relation':'during','anchor':'awaiting parathyroidectomy'}
for fid in ['p1-f100','p1-f101','p1-f102']:
 fs[fid]['fields']['time']={'text':'postoperatively','relation':'after','anchor':'parathyroidectomy'}
for fid in ['p1-f104','p1-f105']:fs[fid]['fields']['time'].update(relation='after',anchor='scintigraphy study; exact time-zero not specified')
# Plain readable labels; no new fact, date, specimen or drug specificity added.
labels=['Chronic rhinitis and deviated septum history','Respiratory infection admissions at age 3','Student screening occult blood; no subsequent medical attention','Abdominal pain onset 1 day before ER visit','Emergency room presentation on September 2, 2024','Initial erect abdominal radiograph','Initial laboratory measurements','Initial urinalysis, urinary ratios and markers','Additional noncontrast kidney/bladder CT and stone diagnosis','Post-admission 24-hour urine collection before ESWL','ESWL for bilateral renal stones','Post-ESWL retrieved stone composition analysis','Three-week follow-up biochemical tests','Parathyroid scintigraphy mass localization','Scintigraphy early 15-minute uptake phase','Scintigraphy delayed 2-hour uptake phase','Primary hyperparathyroidism source diagnosis','Author-qualified renal function/size assessment; exact phase unknown','MEN1 genetic evaluation','Transfer for planned surgical treatment','Interim calcium-sensing receptor agonist while awaiting surgery','Successful parathyroidectomy','Postoperative calcium/iPTH normalization']
for e,label in zip(p['events'],labels):
 e['description']=label
 for fid in e['fact_ids']:
  for ev in fs[fid]['evidence']:
   if ev not in e['evidence']:e['evidence'].append(ev)
p['summary'][0]['text']='A 13-year-old boy with chronic rhinitis, a deviated septum and respiratory infection admissions at age 3 had occult blood detected during student screening in August 2024, without seeking subsequent care. Periumbilical abdominal pain began 1 day before his September 2, 2024 ER visit. He had diffuse tenderness but no fever, diarrhea, other gastrointestinal complaints or costovertebral angle tenderness. Radiograph and noncontrast CT showed bilateral calyceal stones and a right distal ureteral stone measuring 0.5×0.8 cm with hydronephrosis. Pre-ESWL 24-hour urine calcium was normal at 77.76 mg/day; citrate was low at 109 mg/day.'
p['summary'][1]['text']='ESWL preceded wet chemical analysis of retrieved stone fragments, showing a mixed stone chiefly composed of calcium oxalate and calcium phosphate. Three-week follow-up calcium 13 mg/dL, alkaline phosphatase 2,232 IU/L and iPTH 296.2 pg/mL were high; 25-hydroxy vitamin D 13.5 ng/mL was low. Sestamibi identified a parathyroid mass anterior to the right thyroid, supporting the source diagnosis of primary hyperparathyroidism. MEN1 gene testing found no mutations. He transferred for surgical treatment and received an interim calcium-sensing receptor agonist, with cinacalcet mentioned as an example rather than a definite ingredient, before successful parathyroidectomy and postoperative calcium/iPTH normalization. Exact postoperative values, histology and long-term outcome were unstated.'
a['article_limitations']=['One original pediatric index patient; introductory/cohort cases and generic medication/genetic syndromes remain background.','All 16 original source units and retained text read. No categorical renal dysfunction exclusion, adenoma/cancer, family history, unstated specimen/assay, drug route/dose, long-term cure or complete stone clearance inferred.','Source-reported stone dimensions retain first/second dimension order, without invented axes. Serial sestamibi phases and three-week follow-up retain raw relative clocks; exact time-zero is unspecified. Initial and follow-up calcium values are separate.','Cinacalcet is an example in administered-class wording; exact bridge ingredient is uncertain. Normal 24-hour urine calcium does not support a hypercalciuria diagnosis. Caption-grounded clinical figures and separately inspected pixels remain distinct evidence layers.']
path.write_text(json.dumps(a,ensure_ascii=False,indent=2)+'\n')
