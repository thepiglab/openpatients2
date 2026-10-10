"""Field-by-field source proposal from parent canonical; do not edit canonical."""
import json
from pathlib import Path
r=Path('benchmarks/dense50');aid='PMC13612109.1';a=json.loads((r/'annotations'/f'{aid}.json').read_text());s=json.loads((r/'sources'/f'{aid}.json').read_text());seg={x['segment_id']:x['text'] for x in s['segments']};p=a['patients'][0];facts={f['fact_id']:f for f in p['facts']}
def edit(i,**kw):facts[f'p1-f{i:03d}']['fields'].update(kw)
def evidence(i,sid,q):
 assert q in seg[sid];v={'segment_id':sid,'quote':q}
 if v not in facts[f'p1-f{i:03d}']['evidence']:facts[f'p1-f{i:03d}']['evidence'].append(v)
def new(task,sid,q,fields,eid,statement,scoring='include',notes=''):
 assert q in seg[sid];fid=f'p1-f{len(p["facts"])+1:03d}';v={'segment_id':sid,'quote':q};f=dict(fact_id=fid,task=task,collection='items',fields=dict(subject='index_patient',assertion='present',**fields),clinical_statement=statement,evidence=[v],certainty='supported' if scoring=='include' else 'source_conflict',scoring=scoring,notes=notes);p['facts'].append(f)
 if eid:
  e=next(e for e in p['events'] if e['event_id']==eid);e['fact_ids'].append(fid)
  if v not in e['evidence']:e['evidence'].append(v)
 return fid
edit(1,index_subject_description='72-year-old hypertensive man with follicular thyroid carcinoma and later radioiodine-refractory metastatic disease',chief_complaint='cervical neck pain with elevated serum thyroglobulin after prior thyroid cancer treatment',multiple_index_patients=False,subject_state='living_at_presentation');evidence(1,'b00003',seg['b00003'])
for i,kw in {7:{'indication':'follicular thyroid carcinoma'},9:{'indication':'thyroid cancer ablation'},10:{'body_site':'cervical neck'},12:{'method':'whole-body diagnostic RAI scintigraphy','body_site':'C2 vertebra and lung nodules'},13:{'method':'whole-body [18F]F-FDG PET/CT','body_site':'C2 vertebra and lung nodules'},16:{'indication':'iodine-avid metastatic thyroid carcinoma'},18:{'indication':'iodine-avid metastatic thyroid carcinoma'},19:{'body_site':'neck'},20:{'specimen':'serum'},22:{'method':'RAI scintigraphy','body_site':'whole body'},24:{'role':'primary_diagnosis','diagnostic_basis':'elevated serum thyroglobulin with negative radioiodine scintigraphy despite iodine restriction'},25:{'body_site':'thyroid carcinoma with C2 and pulmonary metastases','role':'primary_diagnosis'},26:{'method':'restaging [18F]F-FDG PET/CT','body_site':'C2 vertebral lesion'},27:{'method':'restaging [18F]F-FDG PET/CT','body_site':'lung nodules'},28:{'target':'C2 vertebral lesion'},29:{'target':'C2 vertebral lesion'},30:{'target':'progressive radioiodine-refractory thyroid cancer'},31:{'body_site':'C2 vertebra','indication':'lytic metastatic C2 lesion'},37:{'method':'[68Ga]Ga-DOTATATE PET/CT','body_site':'C2 and pulmonary lesions'},38:{'method':'[68Ga]Ga-PSMA-11 PET/CT','body_site':'C2 and pulmonary lesions'},40:{'indication':'radioiodine-refractory thyroid carcinoma with moderate PSMA expression'},41:{'related_condition':'neck pain'},43:{'specimen':'serum'},54:{'method':'whole-body planar RAI scintigraphy, anterior and posterior views','body_site':'neck remnant'},55:{'method':'whole-body diagnostic RAI scintigraphy, anterior and posterior views','body_site':'whole body'},56:{'method':'sagittal T1-weighted MRI','body_site':'C2 vertebral body'},57:{'method':'sagittal CT','body_site':'C2 vertebra'}}.items():edit(i,**kw)
for i in [16,18]:evidence(i,'b00003','A whole-body diagnostic RAI scintigraphy showed RAI uptake in a lytic C2 vertebral lesion and sub-centimetric lung nodules')
evidence(40,'b00003','[ ^(68) Ga]Ga-prostate specific membrane antigen (PSMA)-11 PET/CT revealed moderate PSMA expression in all lesions (miPSMA score 2')
# Do not assign a treatment response to the preceding RAI courses or an invented primary.
edit(39,response='significant reduction of neck pain and serial reduction of serum thyroglobulin/ATA');evidence(39,'b00003','On 6 weekly follow-ups, he reported a significant reduction of neck pain, no clinical signs of radiculopathy, and serial reduction of Sr. Tg (249 ng/mL) and ATA (9.52 IU/mL, normal < 4.11 IU/mL).')
for i in [54,55]:evidence(i,'b00011',seg['b00011'])
for i in [56,57]:evidence(i,'b00012',seg['b00012'])
for f in p['facts']:
 t=f['fields'].get('time')
 if t and t['text'] in ['Four years later','One year posttreatment']:t['relation']='after'
new('conditions','b00003','low-risk follicular thyroid carcinoma',dict(name='Follicular thyroid carcinoma, initially described as low-risk',verification_status='confirmed',role='primary_diagnosis',body_site='thyroid',temporality='historical'), 'p1-e001','Initial follicular thyroid carcinoma was explicitly described as low-risk; this is not a TNM stage.',notes='Cross-schema diagnosis representation. No tumor grade, TNM or formal risk system inferred.')
new('observations','b00003','low-risk follicular thyroid carcinoma',dict(name='Initial thyroid carcinoma risk classification',kind='other',status='resulted',text_value='low-risk',body_site='thyroid',temporality='historical'),'p1-e001','Source-described initial low-risk classification, before subsequent metastases.',notes='No formal risk system, pathology method, grade or TNM stage is specified.')
new('oncology','b00003','underwent total thyroidectomy and 30 mCi RAI ablation in 2017',dict(tumor_ref='t1',name='Total thyroidectomy',modality='surgery',action='given',time=dict(text='in 2017',relation='at',anchor=None)), 'p1-e001','Total thyroidectomy was given in2017 for the documented follicular thyroid carcinoma.',notes='Same actual surgery evidence as general Procedures, not a second operation.')
new('procedures_devices','b00003','He received EBRT to the C2 vertebra',dict(name='External beam radiotherapy to C2',kind='procedure',action='completed',body_site='C2 vertebra',indication='iodine-avid lytic C2 metastatic lesion'),'p1-e002','Actual EBRT to the C2 lesion; repeat EBRT remains only considered.',notes='Same cancer radiation course as Oncology f014; no dose/fractions inferred.')
for name,q,method,eid in [('Low SSTR expression on DOTATATE PET','While [ ^(68) Ga]Ga-DOTATATE PET/CT showed low-level SSTR expression','[68Ga]Ga-DOTATATE PET/CT','p1-e010'),('Moderate concordant PSMA uptake','[ ^(68) Ga]Ga-PSMA-11 PET/CT revealed moderate and concordant PSMA uptake across all lesions','[68Ga]Ga-PSMA-11 PET/CT','p1-e010')]:
 new('observations','b00009',q,dict(name=name,kind='imaging',status='resulted',method=method,body_site='metastatic lesions',text_value='low-level SSTR expression' if 'SSTR' in name else 'moderate and concordant PSMA uptake across all lesions'),eid,'Author-described qualitative receptor imaging result, without resolving disputed DOTATATE lesion count.')
 new('oncology','b00009',q,dict(tumor_ref='t1',name='SSTR expression' if 'SSTR' in name else 'PSMA expression',assay=method,text_value='low-level expression' if 'SSTR' in name else 'moderate concordant uptake across all lesions',interpretation='unknown'),eid,'Receptor imaging used to select theranostic treatment.',notes='Imaging-based expression, not tissue staining or a somatic gene alteration; shared observation evidence.')
# Scores are explicitly printed/source-defined; unknown units, no fabricated threshold.
for name,sid,q,value,method,site in [('miPSMA score across lesions','b00003','miPSMA score 2',2,'[68Ga]Ga-PSMA-11 PET/CT','metastatic lesions'),('C2 Krenning score','b00012','Krenning score 2',2,'[68Ga]Ga-DOTATATE PET','C2 vertebral lesion')]:
 new('observations',sid,q,dict(name=name,kind='imaging',status='resulted',numeric_value=value,text_value=q,method=method,body_site=site),'p1-e010',q)
new('observations','b00012','SUV _(max) : 9.8',dict(name='Caption C2 DOTATATE SUVmax',kind='imaging',status='resulted',numeric_value=9.8,method='[68Ga]Ga-DOTATATE PET',body_site='C2 vertebral lesion'),'p1-e010','Caption rounds C2 DOTATATE SUVmax to9.8; table reports9.83.',notes='Preserved caption precision without treating rounding as a distinct clinical encounter.')
for e in p['events']:
 if e['event_id']=='p1-e001':e['fact_ids'] += ['p1-f001','p1-f002','p1-f003','p1-f004']
 if e['event_id']=='p1-e012':e['fact_ids'].append('p1-f005')
 for f in p['facts']:
  if f['fact_id'] in e['fact_ids']:
   for x in f['evidence']:
    if x not in e['evidence']:e['evidence'].append(x)
# Add a dated scan phase link only for the two source-stated years; no synthetic2021.
for d in p['domain_coverage']:
 n=sum(f['task']==d for f in p['facts']);p['domain_coverage'][d]['note']=f'{n} source-grounded representations after full-field audit.' if n else 'No patient-specific documentation; no negative inferred.'
p['ambiguities'].append(dict(issue='Actual Figure1 image prints Tg64/462/494/2813 for2017/2023/2024/2025 without units. Caption labelsng/dL; narrative usesng/mL and reports2568 for the high-Tg negative-RAI phase. Printed values stay separately visual, without choosing a resolved clinical value. Actual Figure3 includes uncaptained G/H Post Therapy Scan panels; tracer, timing and specific ownership unresolved.',evidence=[dict(segment_id='b00011',quote='values (ng/dL)'),dict(segment_id='b00003',quote='an abrupt rise in Sr Tg 2568 ng/mL'),dict(segment_id='b00013',quote=seg['b00013'])]))
a['annotation_provenance']['passes'].append('Full field-by-field source audit and actual three-asset inspection: initial low-risk classification, general and Oncology cross-schema therapy, imaging method/site, receptor biomarker/observation and phase links. Literature Assadi cardiac-arrest death remains background. New pixel discrepancies isolated in visual reference and limitation.')
(r/'annotator-1-proposals'/f'{aid}.json').write_text(json.dumps(a,ensure_ascii=False,indent=2)+'\n');print(aid,len(p['facts']))
