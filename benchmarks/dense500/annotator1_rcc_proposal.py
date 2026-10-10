"""Full-field audit source proposal; parent canonical is read-only."""
import json,copy
from pathlib import Path
r=Path('benchmarks/dense50');aid='PMC13582412.1';a=json.loads((r/'annotations'/f'{aid}.json').read_text());s=json.loads((r/'sources'/f'{aid}.json').read_text());seg={x['segment_id']:x['text'] for x in s['segments']}
for p in a['patients']:
 pid=p['patient_id'];one=pid=='p1';sid='b00010' if one else 'b00011';molsid='b00020' if one else 'b00021';stage_sid='b00022' if one else 'b00023';hist_eid='p1-e014' if one else 'p2-e010';ngs_eid='p1-e013' if one else 'p2-e009';stage_eid='p1-e008' if one else 'p2-e005';pres_eid='p1-e007' if one else 'p2-e004';side='left' if one else 'right';kidney=side+' renal tumor';f={x['fact_id']:x for x in p['facts']}
 def evidence(row,sid,q):
  assert q in seg[sid];e=dict(segment_id=sid,quote=q)
  if e not in row['evidence']:row['evidence'].append(e)
 def add(task,sid,q,fields,eid,statement,collection='items',notes=''):
  assert q in seg[sid];fid=f'{pid}-f{len(p["facts"])+1:03d}';row=dict(fact_id=fid,task=task,collection=collection,fields=dict(subject='index_patient',assertion='present',**fields),clinical_statement=statement,evidence=[dict(segment_id=sid,quote=q)],certainty='supported',scoring='include',notes=notes);p['facts'].append(row);f[fid]=row
  e=next(e for e in p['events'] if e['event_id']==eid);e['fact_ids'].append(fid);return row
 context=p['facts'][0];context['fields'].update(index_subject_description='Male with '+side+' MTOR-mutated eosinophilic renal cell carcinoma',chief_complaint='fatigue and low back pain' if one else 'low back pain',multiple_index_patients=True,subject_state='living_at_presentation');evidence(context,sid,seg[sid]);evidence(context,'b00007','Clinical data were obtained through review of electronic medical records.')
 # Institution is the pathology review location; do not infer the original clinical setting.
 for row in p['facts']:
  fields=row['fields'];name=fields.get('name','')
  if name=='Low back pain':fields['body_site']='low back'
  if name=='Renal mass':fields.update(body_site=side+' kidney',name='Renal mass greatest dimension');evidence(row,sid,seg[sid])
  if name=='Liver and lung nodules':fields['body_site']='liver and lung'
  if name=='Initial metastasis assessment':fields['method']='initial imaging; modality unstated'
  if name=='Radical nephrectomy':fields['indication']='renal mass';evidence(row,sid,seg[sid])
  if name=='Targeted liver biopsy':fields.update(body_site='liver nodules',indication='persistent slightly enlarging liver nodules');evidence(row,'b00022',seg['b00022'])
  if name=='Liver nodules follow-up':fields['body_site']='liver'
  if name=='Right rib lesion pain':fields['body_site']='right rib'
  if name.startswith(('Paracaval lymph node','initial paracaval')):fields['body_site']='paracaval lymph node'
  if name.startswith(('Retrocaval adenopathy','later retrocaval')):fields['body_site']='retrocaval lymph nodes'
  if name in ['Right rib lesion','Right rib lesion growth']:fields['body_site']='right rib'
  if name=='Renal sinus soft tissue invasion':fields.update(body_site='renal sinus soft tissue',specimen=kidney,method='histopathologic review')
  if name=='Segmental renal vein tumor thrombi':fields.update(body_site='segmental branch of renal vein',specimen=kidney,method='histopathologic review')
  if row['task']=='medications':fields['indication']='metastatic renal cell carcinoma' if name!='yttrium-90 microspheres' else 'persistent liver-only metastatic renal cell carcinoma'
  if row['task']=='oncology' and row['collection']=='biomarkers':
   if fields.get('assay')=='IHC':fields['specimen']=kidney;evidence(row,'b00008','The hematoxylin and eosin (H&E) and IHC slides were reviewed by two expert genitourinary pathologists.')
   elif name in ['MTOR','chromosome 1 copy number','tumor mutational burden','microsatellite status']:
    fields['specimen']='formalin-fixed, paraffin-embedded tumor block';fields['assay']='UChicago Medicine OncoPlus hybrid-capture NGS panel' if name!='chromosome 1 copy number' else 'copy number variant analysis using CNV Kit14'
    evidence(row,'b00009','A representative formalin-fixed, paraffin-embedded block was selected for NGS in the UChicago Medicine OncoPlus panel, a hybrid-capture panel targeting 1005 cancer-associated genes, of which 168 genes are clinically reported.')
    if name=='chromosome 1 copy number':evidence(row,'b00009','CNV Kit14 software, with additional in-house intrarun normalization and comparison with a pooled cohort of clinical controls, was used to calculate copy number results.')
  if name=='Other copy number changes':fields.update(specimen='formalin-fixed, paraffin-embedded tumor block',method='copy number variant analysis using CNV Kit14');evidence(row,'b00009','CNV Kit14 software, with additional in-house intrarun normalization and comparison with a pooled cohort of clinical controls, was used to calculate copy number results.')
  time=fields.get('time')
  if time and 'after' in time['text']:time['relation']='after'
 # Retrospective IHC review is undated: not forced into surgery or a2024encounter.
 hist_ids=[x['fact_id'] for x in p['facts'] if x['task']=='observations' and x['fields'].get('kind')=='pathology' and x['fields'].get('name') not in ['Renal sinus soft tissue invasion','Segmental renal vein tumor thrombi']]
 hist_ids += [x['fact_id'] for x in p['facts'] if x['task']=='oncology' and x['collection']=='biomarkers' and x['fields'].get('assay')=='IHC']
 p['events'].append(dict(event_id=hist_eid,description='Undated retrospective tumor morphology and immunohistochemical review',occurrence='occurred',fact_ids=hist_ids,time_text=None,evidence=[dict(segment_id='b00008',quote=seg['b00008'])]))
 for row in p['facts']:
  if row['task']=='observations' and row['fields'].get('kind')=='pathology' and row['scoring']=='include':row['fields'].update(body_site=side+' kidney',specimen=kidney,method='histopathologic slide review');evidence(row,'b00008','The hematoxylin and eosin (H&E) and IHC slides were reviewed by two expert genitourinary pathologists.')
 add('conditions',sid,'left renal mass' if one else 'right renal mass',dict(name='MTOR-mutated eosinophilic renal cell carcinoma',verification_status='confirmed',role='primary_diagnosis',body_site=side+' kidney',laterality=side,diagnostic_basis='tumor morphology, IHC and tumor sequencing'),hist_eid,'The documented primary renal cell carcinoma also belongs in Conditions, with shared histologic/molecular evidence.')
 cond=p['facts'][-1];evidence(cond,'b00006','two cases of MTOR-mutated eosinophilic RCC with loss of chromosome 1');evidence(cond,molsid,seg[molsid])
 neph=add('oncology',sid,'The patient subsequently underwent a radical nephrectomy.',dict(tumor_ref='t1',name=side.capitalize()+' radical nephrectomy',modality='surgery',action='given'),pid+'-e001','Actual renal cancer surgery represented in Oncology and general Procedures with shared evidence.',collection='treatments')
 evidence(neph,sid,seg[sid])
 # IHC and genetic test results cross into general Observations without creating separate tests.
 originals=list(p['facts'])
 for row in originals:
  fields=row['fields'];name=fields.get('name')
  if row['task']!='oncology' or row['collection']!='biomarkers' or row['scoring']!='include':continue
  flags={'positive':'positive','negative':'negative','pathogenic':'unknown','unknown':'unknown'}
  text=fields.get('text_value') or fields.get('alteration') or fields.get('interpretation')
  nf=dict(name=name+' result',kind='pathology' if fields.get('assay')=='IHC' else 'laboratory',status='resulted',text_value=text,numeric_value=fields.get('numeric_value'),unit=fields.get('unit'),specimen=fields.get('specimen'),method=fields.get('assay'),flag=flags.get(fields.get('interpretation'),'unknown'))
  if fields.get('assay')=='IHC':nf['body_site']=side+' kidney'
  target=hist_eid if fields.get('assay')=='IHC' else ngs_eid
  x=add('observations',row['evidence'][0]['segment_id'],row['evidence'][0]['quote'],nf,target,'General observation representation of '+name+' with shared molecular/IHC evidence.',notes='Shared test result with Oncology, not an independent biological finding.');x['evidence']=copy.deepcopy(row['evidence'])
 if one:
  for name in ['Invitae multi-gene panel: pathogenic variant assessment','BRCA1 VUS']:
   old=next(x for x in originals if x['fields'].get('name')==name);fields=old['fields'];x=add('observations','b00020',old['evidence'][0]['quote'],dict(name=name,kind='laboratory',status='resulted',text_value=fields.get('interpretation'),method='Invitae germline multi-gene panel'), 'p1-e012','General observation of explicitly documented germline-panel result; not a new inherited disease.',notes='Shared evidence with FamilyGenetics, specimen unreported.');x['evidence']=copy.deepcopy(old['evidence']);evidence(x,'b00020',seg['b00020'])
  add('procedures_devices','b00022','biopsy-confirmed metastasis in a retroperitoneal lymph node',dict(name='Retroperitoneal lymph node biopsy',kind='procedure',action='completed',body_site='retroperitoneal lymph node',finding='metastatic renal cell carcinoma'),'p1-e008','The source explicitly describes a biopsy-confirmed nodal metastasis, without an exact biopsy date.')
  add('observations','b00022','a targeted biopsy was performed and confirmed metastatic renal cell carcinoma.',dict(name='Liver nodule biopsy pathology',kind='pathology',status='resulted',specimen='liver nodule biopsy',body_site='liver',method='targeted biopsy',text_value='metastatic renal cell carcinoma'),'p1-e004','Liver biopsy confirmed metastatic RCC, separate from earlier nodal staging.')
  add('procedures_devices','b00022','hepatic arterial yttrium-90 microspheres for persistent liver-only disease',dict(name='Hepatic arterial yttrium-90 microsphere treatment',kind='procedure',action='completed',body_site='liver',indication='persistent liver-only metastatic disease'),'p1-e006','Actual liver-directed therapy has general procedure, medication and Oncology representations with shared evidence.')
  add('procedures_devices','b00022','received radiation for increasing periportal abdominal lymphadenopathy in 2022',dict(name='Radiation to periportal abdominal lymphadenopathy',kind='procedure',action='completed',body_site='periportal abdominal lymph nodes',indication='increasing lymphadenopathy',time=dict(text='in 2022',relation='at',anchor=None)),'p1-e009','Actual2022periportal radiation; no dose/fractions specified.')
 else:
  add('procedures_devices','b00023','the patient received localized radiation therapy to the right rib lesion due to pain associated with the lesion',dict(name='Localized right rib radiation therapy',kind='procedure',action='completed',body_site='right rib lesion',laterality='right',indication='pain associated with right rib lesion'),'p2-e006','Actual pain-directed local rib radiation also represented in general Procedures.')
 # Link all previously orphaned known-phase claims and all supporting literal contexts.
 for e in p['events']:
  if e['event_id']==pres_eid:e['fact_ids'].append(pid+'-f001')
  if one and e['event_id']=='p1-e004':e['fact_ids']+=['p1-f045','p1-f047']
  if not one and e['event_id']=='p2-e003':e['fact_ids'].append('p2-f045')
  for row in p['facts']:
   if row['fact_id'] in e['fact_ids']:
    for v in row['evidence']:
     if v not in e['evidence']:e['evidence'].append(v)
 for d in p['domain_coverage']:
  n=sum(x['task']==d for x in p['facts']);p['domain_coverage'][d]['note']=f'{n} source-grounded representations; shared cross-schema results are not new independent findings.' if n else 'No patient-specific documentation; no negative inferred.'
a['annotation_provenance']['passes'].append('Source-to-full-field audit across28original units: anatomy, chief complaint, clinical versus pathology setting, IHC/NGS specimens/methods and general result representations; original cancer diagnoses and named actual cancer therapies in both clinical domains. All phase-known facts linked while retrospective slide/test timing remains undated. Shared rare morphology/panel ownership, initial-stage/later-progression and literature-only cases preserved.')
(r/'annotator-1-proposals'/f'{aid}.json').write_text(json.dumps(a,ensure_ascii=False,indent=2)+'\n');print([(x['patient_id'],len(x['facts']),len(x['events'])) for x in a['patients']])
