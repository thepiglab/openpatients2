import json
from pathlib import Path
root=Path('benchmarks/dense500');aid='PMC12477045.1';path=root/'drafts'/f'{aid}.json';a=json.load(open(path))
maps={'p1':{96:10,104:97,105:100,106:103,107:34,108:35,109:35,110:50,111:52,112:54,113:53,114:36,115:69,116:71,117:73},'p2':{105:103,107:104,108:37,109:38,110:38,111:39},'p3':{97:93,98:96,100:26,101:27,102:27,103:45,104:28}}
for p in a['patients']:
 pid=p['patient_id'];facts={f['fact_id']:f for f in p['facts']};mapping={f'{pid}-f{x:03d}':f'{pid}-f{y:03d}' for x,y in maps[pid].items()}
 for remove,keep in mapping.items():
  if remove not in facts:continue
  f,k=facts[remove],facts[keep]
  for e in f['evidence']:
   if e not in k['evidence']:k['evidence'].append(e)
  k['notes']=(k.get('notes','')+' Duplicate technique/action representations consolidated; shared table evidence is not an additional test occasion or an invented table-test date.').strip()
  if 'aspiration light microscopy' in f['fields']['name']:k['fields']['finding']='Bone marrow examination: source cytology/immunophenotyping results; Table1 additionally documents aspiration light microscopy. Same documented results, no added marrow occasion.'
 p['facts']=[f for f in p['facts'] if f['fact_id'] not in mapping]
 for e in p['events']:e['fact_ids']=[x for x in e['fact_ids'] if x not in mapping]
 for s in p['summary']:s['fact_ids']=[x for x in s['fact_ids'] if x not in mapping]
 for f in p['facts']:
  z=f['fields'];name=z.get('name','')
  if f['task']=='observations' and name=='Vital signs':z.update(flag='unknown',text_value='stable vital signs as reported; not inferred normal')
  if f['task']=='observations' and z.get('unit')=='+':
   if z.get('numeric_value') in [1,2,3]:z['text_value']=str(int(z['numeric_value']))+'+'
  if pid=='p2' and name=='Saphenous vein varicosity surgery':z['time'].update(relation='before',anchor='index presentation')
  if pid=='p3' and f['fact_id']=='p3-f007':z.update(assertion='absent',action_text='No further evaluation/treatment sought in2018',action_status='unknown');f['clinical_statement']='No further evaluation/treatment sought after2018 screening';f['notes']='Documented nonpursuit of care, no fabricated plan or refusal.'
  tm=z.get('time',{}).get('text','') or ''
  if 'after discontinuation' in tm:z['time'].update(relation='after',anchor='BCD treatment discontinuation')
  if 'of initiating the Dd regimen' in tm:z['time'].update(relation='after',anchor='Dd regimen initiation')
  if tm=='for over 1 year':z['time'].update(relation='during',anchor='follow-up after Dd treatment')
  if pid=='p1' and name in ['Serum creatinine','Table Serum creatinine, umol/L','24-hour urine protein','Table 24 h total protein, mg']:
   f.update(certainty='source_conflict',scoring='review_only');f['notes']+=' Narrative/table discrepancy; both numerical candidates quarantined, neither selected as definitive. Table phase unreported, potentially same initial result.'
 for task,v in p['domain_coverage'].items():
  n=sum(f['task']==task for f in p['facts']);v['note']=f'{n} source representations retained; source_conflict/review_only candidates quarantined where indicated.' if n else v['note']
 p['ambiguities'].append({'issue':'Duplicate named-test technique/action representations consolidated; table/prose shared results do not add examinations. Distinct outpatient, post-BCD, relapse and follow-up tests remain separate.','evidence':[],'must_not_infer':'Row count is not a count of unique examinations; table phase remains unreported.'})
a['article_limitations'].append('Both p1 narrative/table creatinine49.44/49.94 and24hourprotein1341.63/1340 numerical candidates are source_conflict/review_only, so no conflicting number is selected as definitive. Source table phase remains unreported.')
path.write_text(json.dumps(a,ensure_ascii=False,indent=2)+'\n')
print([(p['patient_id'],len(p['facts'])) for p in a['patients']])
