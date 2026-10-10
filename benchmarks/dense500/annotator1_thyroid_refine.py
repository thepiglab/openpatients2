"""Refine owned thyroid proposal only; parent canonical remains untouched."""
import json
from pathlib import Path
r=Path('benchmarks/dense50');n='PMC13612109.1';path=r/'annotator-1-proposals'/f'{n}.json';a=json.loads(path.read_text());p=a['patients'][0];s=json.loads((r/'sources'/f'{n}.json').read_text());seg={x['segment_id']:x['text'] for x in s['segments']};by={f['fact_id']:f for f in p['facts']};events={e['event_id']:e for e in p['events']}
for e in p['events']:
 e['fact_ids']=[i for i in e['fact_ids'] if i not in ['p1-f001','p1-f002','p1-f003','p1-f004']]
for i in range(1,5):by[f'p1-f{i:03d}']['notes']='Undated case-level descriptor. Header age, sex and hypertension are not anchored to2017; age at initial thyroidectomy and onset of hypertension are unreported.'
by['p1-f002']['clinical_statement']='The case header reports age72years; age at the2017thyroidectomy is unknown.'
by['p1-f004']['clinical_statement']='Documented hypertension history; onset and diagnosis date unreported.'
# The adjacent MRI and CT caption has no date, and is not itself an assertion of restaging timing.
if 'p1-e013' not in events:
 e=dict(event_id='p1-e013',description='Undated caption-documented sagittal MRI and CT assessment of C2',occurrence='occurred',fact_ids=[],time_text=None,evidence=[dict(segment_id='b00012',quote=seg['b00012'])]);p['events'].append(e);events[e['event_id']]=e
for i in [56,57]:
 fid=f'p1-f{i:03d}';events['p1-e010']['fact_ids']=[x for x in events['p1-e010']['fact_ids'] if x!=fid]
 if fid not in events['p1-e013']['fact_ids']:events['p1-e013']['fact_ids'].append(fid)
 by[fid]['notes']='Caption documents a performed examination/result; acquisition date and relation to restaging PET are unreported. Actual pixels reviewed separately; no timing inferred from layout.'
# Actual performed examinations across general procedures and observations share source evidence.
def add(task,sid,q,fields,eid,statement,notes):
 assert q in seg[sid]
 existing=next((f for f in p['facts'] if f['task']==task and f['fields'].get('name')==fields['name']),None)
 if existing:return
 fid=f'p1-f{len(p["facts"])+1:03d}';ev=dict(segment_id=sid,quote=q);p['facts'].append(dict(fact_id=fid,task=task,collection='items',fields=dict(subject='index_patient',assertion='present',**fields),evidence=[ev],clinical_statement=statement,certainty='supported',scoring='include',notes=notes));events[eid]['fact_ids'].append(fid)
def procedure(name,sid,q,eid,site,find=None,time=None,notes=''):
 f=dict(name=name,kind='procedure',action='completed',body_site=site)
 if find:f['finding']=find
 if time:f['time']=dict(text=time,relation='at',anchor=None)
 add('procedures_devices',sid,q,f,eid,'Performed '+name+'.','Cross-schema representation of the documented examination; not an independent repeat acquisition. Unreported dose, route and exact date remain unknown. '+notes)
procedure('Initial posttherapy planar RAI scintigraphy','b00011',seg['b00011'],'p1-e005','whole body','remnant neck uptake','2017')
procedure('Initial whole-body diagnostic RAI scintigraphy','b00003','A whole-body diagnostic RAI scintigraphy showed RAI uptake in a lytic C2 vertebral lesion and sub-centimetric lung nodules','p1-e006','whole body','RAI-avid C2 lesion and sub-centimetric lung nodules')
procedure('Initial whole-body FDG PET/CT','b00003','with moderate and low-grade [ ^(18) F]F-FDG uptake on whole body [ ^(18) F]F-FDG PET/computed tomography (CT).','p1-e006','whole body','moderate and low-grade uptake in C2 and lung lesions')
for year,eid in [('2023','p1-e007'),('2024','p1-e008')]:
 q='Subsequent post-RAI therapy scans in 2023 and 2024 demonstrate RAI-avid C2 vertebral lesion and lung nodules, with elevated Sr Tg levels.'
 procedure(year+' posttherapy planar RAI scintigraphy','b00011',seg['b00011'],eid,'whole body','RAI-avid C2 vertebral lesion and lung nodules',year)
 add('observations','b00011',seg['b00011'],dict(name=year+' posttherapy RAI scan result',kind='imaging',status='resulted',text_value='RAI-avid C2 vertebral lesion and lung nodules, with elevated Sr Tg levels',method='whole-body planar RAI scintigraphy, anterior and posterior views',body_site='C2 vertebra and lung nodules',time=dict(text=year,relation='at',anchor=None)),eid,year+' posttherapy scan had author-described C2 and pulmonary RAI uptake.','Caption explicitly describes both2023and2024scans. No Tg pixel value or guessed serum unit transferred into this clinical result.')
procedure('2025 whole-body diagnostic RAI scintigraphy','b00011',seg['b00011'],'p1-e009','whole body','no RAI-avid foci','in 2025')
procedure('Restaging FDG PET/CT','b00003','Restaging [ ^(18) F]F-FDG-PET/CT showed moderate uptake in the C2 lesion','p1-e010','C2 vertebra and lungs',notes='Actual test is unambiguous; differing numerical results remain separately review-only.')
procedure('Staging DOTATATE PET/CT','b00003','Staging PET/CT with [ ^(68) Ga]Ga-DOTATATE showed low somatostatin receptor (SSTR) expression','p1-e010','metastatic lesions',notes='Actual test is unambiguous; lesion-count ambiguity remains separately review-only.')
procedure('PSMA-11 PET/CT','b00003','[ ^(68) Ga]Ga-prostate specific membrane antigen (PSMA)-11 PET/CT revealed moderate PSMA expression in all lesions','p1-e010','metastatic lesions')
procedure('Sagittal T1-weighted MRI','b00012','( A ) Sagittal T1-weighted MRI shows a hyperintense lytic lesion in the C2 vertebral body (orange arrow).','p1-e013','C2 vertebral body','hyperintense lytic lesion',notes='Caption-only examination timing unknown; not anchored to the restaging PET encounter.')
procedure('Sagittal CT view of C2','b00012','Sagittal views demonstrating a metastatic lytic lesion in the C2 vertebra. ( A ) Sagittal T1-weighted MRI shows a hyperintense lytic lesion in the C2 vertebral body (orange arrow). ( B ) CT image reveals osteolytic destruction of the C2 vertebra.','p1-e013','C2 vertebra','osteolytic destruction',notes='Caption-only examination timing and whether acquired separately from PET/CT unknown; no whole-body acquisition extent inferred.')
for e in p['events']:
 e['fact_ids']=list(dict.fromkeys(e['fact_ids']));e['evidence']=list({(x['segment_id'],x['quote']):x for f in p['facts'] if f['fact_id'] in e['fact_ids'] for x in f['evidence']}.values())
p['summary'][0]['text']='The case describes a man aged72years in its header, with hypertension and unspecified cardiac comorbidities; his age at the2017operation is unreported. Low-risk follicular thyroid carcinoma was treated with total thyroidectomy and30mCi RAI in2017, with remnant neck uptake on the posttherapy scan. Four years later, neck pain and serum thyroglobulin264.1ng/mL accompanied iodine-avid lytic C2 and sub-centimetric lung lesions. C2 EBRT and RAI100mCi in2023and150mCi in2024 were given.'
p['summary'][3]['text'] += ' The2025image footer prints Tg2813 without a unit, whereas the narrative high-Tg phase reports2568ng/mL. This is an apparent discrepancy with sample dates unreported, and may represent different samples within the year.'
for issue in p['ambiguities']:
 if 'Actual Figure1' in issue['issue']:issue['issue']='Actual Figure1 prints Tg64/462/494/2813 for2017/2023/2024/2025 without units. Caption labelsng/dL while the narrative usesng/mL. Printed2025Tg2813 versus narrative2568 is an apparent discrepancy: sampling dates are unreported, so these may be different samples within the year. Neither values nor units are corrected. Actual Figure3 has uncaptained G/H Post Therapy Scan panels; tracer, timing and specific ownership unresolved.'
p['ambiguities'].append(dict(issue='Header72-year age and hypertension are undated descriptors; age at2017 and hypertension onset are unknown. MRI/CT caption acquisition dates and temporal relationship to restaging PET are unreported, with no forced order.',evidence=[dict(segment_id='b00003',quote='A 72-year-old hypertensive male'),dict(segment_id='b00012',quote=seg['b00012'])]))
for d in p['domain_coverage']:
 count=sum(f['task']==d for f in p['facts']);p['domain_coverage'][d]['note']=f'{count} source-grounded representations after field/phase audit.' if count else 'No patient-specific documentation; no negative inferred.'
a['annotation_provenance']['passes'].append('Parent-requested chronology correction: header age/context/HTN left timeless, unknown age at2017; caption MRI/CT separated as undated tests. Actual named imaging procedures represented with shared source evidence; Tg apparent discrepancy does not assert identical sampling dates.')
path.write_text(json.dumps(a,ensure_ascii=False,indent=2)+'\n');print(len(p['facts']),len(p['events']))
vp=r/'visual-submissions'/f'{n}.json';v=json.loads(vp.read_text())
for f in v['figures']:
 if f['figure_id']=='FI2560003-1':
  note='Caption ng/dL and narrative ng/mL units differ; actual footer has no unit. Printed2025Tg2813 versus narrative2568 is an apparent discrepancy: sample dates are unreported and may differ within the year.'
  f['limitations']=[note];f['analysis']['visual']['limitations']=[note]
 if f['figure_id']=='FI2560003-2':
  b=next(x for x in f['analysis']['visual']['panels'] if x['panel']=='B');b['detailed_description']='Second panel labelled B. A grayscale sagittal CT view shows head, neck, torso and pelvis, with the legs not fully visible, and a bright irregular upper-cervical vertebral contour. Footer reads CT. No arrow, lesion number or measurement is printed. Acquisition extent is not inferred from the displayed reconstruction.'
vp.write_text(json.dumps(v,ensure_ascii=False,indent=2)+'\n')
