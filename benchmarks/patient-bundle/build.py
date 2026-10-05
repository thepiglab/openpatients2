"""Rebuild the bounded benchmark from source-reviewed fixtures, without downloads."""
import copy,gzip,hashlib,json,re
from pathlib import Path
from collections import Counter
ROOT=Path(__file__).resolve().parents[2];OUT=Path(__file__).parent

def read(path):
 opener=gzip.open if str(path).endswith('.gz') else open
 with opener(path,'rt') as f:return [json.loads(l) for l in f if l.strip()]
def save(name,rows):
 with gzip.GzipFile(str(OUT/name),'wb',mtime=0) as f:
  for row in rows:f.write((json.dumps(row,ensure_ascii=False)+'\n').encode())
def write(name,value):(OUT/name).write_text(json.dumps(value,indent=2,ensure_ascii=False)+'\n')
articles=read(ROOT/'benchmarks/corpus-correctness/articles.jsonl.gz')
extra=read(ROOT/'benchmarks/hipergator-k2/fixtures/articles.jsonl')
# A conference abstract is not an eligible full-text source. Keep it excluded,
# rather than silently changing the full-text gate to improve benchmark size.
extra=[a for a in extra if a['article_id']!='PMC12773240.1']
articles+=extra+read(OUT/'additional-articles.jsonl.gz');by={a['article_id']:a for a in articles}
rosters=json.loads((ROOT/'benchmarks/corpus-correctness/rosters.json').read_text())['articles']
for a in extra:
 old=json.loads((ROOT/'benchmarks/hipergator-k2/fixtures/rosters'/f"{a['article_id']}.json").read_text())
 assert old['xml_sha256']==a['xml_sha256']
 rosters.append({'article_id':a['article_id'],'text_sha256':a['text_sha256'],'review_status':'source_checked','roster':old['roster']})
ref=copy.deepcopy(json.loads((ROOT/'benchmarks/corpus-correctness/reference-v2.json').read_text()))
old=json.loads((ROOT/'benchmarks/hipergator-k2/fixtures/reference.json').read_text())
ref['checks']+=[c for c in old['checks'] if c['record_id'].split(':')[0] in by]
# Explicit identity/source review for the four new cases.
for aid,defs in {
 'PMC13542380.1':[('p1','male infant with left craniofacial microsomia','b00006','A male infant was born at 37 weeks and 5 days of gestation via cesarean section')],
 'PMC13570994.1':[('p1','70-year-old male with symptomatic Extent IV TAAA','b00021','A 70-year-old male patient presented with symptomatic 6.1 cm Extent IV TAAA.'),('p2','78-year-old male with aortoiliac aneurysms','b00025','A 78-year-old male patient presented with aortoiliac and large bilateral IIAAs.')],
 'PMC13612109.1':[('p1','72-year-old male with follicular thyroid carcinoma','b00003','A 72-year-old hypertensive male with low-risk follicular thyroid carcinoma')]
}.items():
 a=by[aid];patients=[]
 for pid,label,sid,quote in defs:
  assert quote in next(s['text'] for s in a['segments'] if s['segment_id']==sid)
  patients.append({'patient_id':pid,'label':label,'species':'human','species_as_documented':None,
   'identity_evidence':[{'segment_id':sid,'quote':quote}],
   'source_segment_ids':([s['segment_id'] for s in a['segments']] if len(defs)==1 else
    [s['segment_id'] for s in a['segments'] if int(s['segment_id'][1:]) in ({21,22,23,24} if pid=='p1' else {25,26})]),'attribution_limitations':[]})
 rosters.append({'article_id':aid,'text_sha256':a['text_sha256'],'review_status':'source_checked','roster':{'disposition':'individual_cases','reported_individual_count':len(patients),'roster_complete':True,'patients':patients,'cited_cases':[],'background_segment_ids':[],'unresolved_segment_ids':[],'figures':[],'limitations':['Source-reviewed identity; figure ownership is separately scored.']}})
# Extend source-bound identity labels to all added full-text articles.
known={a['article_id'] for a in ref['articles']}
for row in rosters:
 if row['article_id'] in known:continue
 a=by[row['article_id']];rs=row['roster'];source=[e for p in rs['patients'] for e in p['identity_evidence']]
 if not source:source=[{'segment_id':a['segments'][0]['segment_id'],'quote':a['segments'][0]['text']}]
 ref['articles'].append({'article_id':a['article_id'],'text_sha256':a['text_sha256'],'expected_count':len(rs['patients']),
  'disposition':rs['disposition'],'count_adjudication':'source_checked','species':[p['species'] for p in rs['patients']],
  'identities':[{'patient_id':p['patient_id'],'label':p['label'],'evidence':p['identity_evidence']} for p in rs['patients']],
  'source':source,'original_cases_only':True})
new_checks=[]
def fact(rid,task,sid,pattern,description,*,kind='required',collection='items',category=None,quote=None):
 a=by[rid.split(':')[0]];text=next(s['text'] for s in a['segments'] if s['segment_id']==sid)
 if quote:assert quote in text
 c={'id':f'B{len(new_checks)+1:04d}','record_id':rid,'task':task,'collection':collection,'pattern':pattern,
  'description':description,'kind':kind,'category':category or task,'source':[{'segment_id':sid,'quote':quote or text}],
  'text_sha256':a['text_sha256'],'xml_sha256':a['xml_sha256']};new_checks.append(c);return c['id']
rx=lambda text:{'regex':text}
# Every operational clinical family has at least one independent source probe.
# Case context is scored as a section, using reviewed patient identity evidence.
for row in rosters:
 for patient in row['roster']['patients']:
  e=patient['identity_evidence'][0];species=patient['species']
  if species=='unknown':continue
  fact(row['article_id']+':'+patient['patient_id'],'case_context',e['segment_id'],
   {'species':species,'case_kind':{'one_of':['clinical_case','multi_patient','animal'] if species=='nonhuman' else ['clinical_case','multi_patient']}},
   'Individually described clinical patient and source-supported species',collection=None,quote=e['quote'])
# New observations use separate magnitude and unit fields; no invented conversion.
rid='PMC13542380.1:p1'
fact(rid,'demographics','b00006',{'attribute':'documented_sex','text_value':rx(r'\bmale\b')},'Male infant')
fact(rid,'reproductive_perinatal','b00006',{'event':'delivery','text_value':rx('cesarean|caesarean')},'Cesarean birth')
fact(rid,'observations','b00006',{'name':rx('weight'),'numeric_value':3190,'unit':rx('g')},'Birth weight 3190 g')
fact(rid,'observations','b00006',{'name':rx('length'),'numeric_value':51,'unit':rx('cm')},'Birth length 51 cm')
fact(rid,'family_genetics','b00006',{'assertion':'absent','name':rx('congenital|anomal')},'No known family history of congenital anomalies')
fact(rid,'conditions','b00006',{'assertion':'present','name':rx('lateral facial cleft|Tessier')},'Left lateral facial cleft')
fact(rid,'procedures_devices','b00006',{'name':rx('intubat'),'action':{'one_of':['completed','historical']}},'Intubation after birth')
fact(rid,'procedures_devices','b00012',{'name':rx('excis|resect'),'action':{'one_of':['completed','historical']}},'Excision of protruding mandibular remnant/tooth')
fact(rid,'procedures_devices','b00013',{'name':rx('tongue|adhesion'),'action':'completed'},'Partial release of tongue adhesion')
fact(rid,'procedures_devices','b00013',{'name':rx('complete.*tongue|tongue.*complete'),'action':'completed'},'Do not claim complete release of tongue adhesion',kind='forbidden')
for pid,age,sid in [('p1',70,'b00021'),('p2',78,'b00025')]:
 rid='PMC13570994.1:'+pid
 fact(rid,'demographics',sid,{'attribute':'age_at_presentation','numeric_value':age},f'Age {age} years')
 fact(rid,'conditions',sid,{'name':rx('hypertension'),'assertion':'present'},'Hypertension')
 fact(rid,'social_exposures',sid,{'domain':'tobacco','name':rx('smok|cigarette')},'Cigarette smoking')
 if pid=='p1':
  fact(rid,'conditions',sid,{'name':rx('hyperlipid'),'assertion':'present'},'Hyperlipidemia')
  fact(rid,'conditions',sid,{'name':rx('obesity|obese'),'assertion':'present'},'Morbid obesity')
  fact(rid,'observations',sid,{'name':rx('TAAA|aneurysm|diameter'),'numeric_value':6.1,'unit':'cm'},'TAAA 6.1 cm')
  fact(rid,'symptoms_function',sid,{'name':rx('claudication'),'assertion':'absent'},'No claudication')
 else:
  fact(rid,'conditions',sid,{'name':rx('cardiomyopathy'),'assertion':'present'},'Ischemic cardiomyopathy')
  for number,name in [(3.7,'infrarenal|aort'),(4.9,'right'),(4.1,'left')]:
   fact(rid,'observations',sid,{'name':rx(name),'numeric_value':number,'unit':'cm'},f'Aneurysm dimension {number} cm, {name}')
  fact(rid,'observations',sid,{'numeric_value':6.1,'unit':'cm'},'6.1 cm belongs to patient 1, not patient 2',kind='forbidden',category='wrong_patient')
rid='PMC13612109.1:p1';sid='b00003'
fact(rid,'demographics',sid,{'attribute':'age_at_presentation','numeric_value':72},'Age 72 years')
fact(rid,'conditions',sid,{'name':rx('follicular thyroid|thyroid carcinoma'),'assertion':'present'},'Follicular thyroid carcinoma')
for name,num,unit in [('thyroglobulin|\\bTg\\b',264.1,'ng/mL'),('thyroglobulin|\\bTg\\b',2568,'ng/mL'),('thyroglobulin|\\bTg\\b',249,'ng/mL'),('antibody|ATA',23.9,'IU/mL'),('antibody|ATA',9.52,'IU/mL')]:
 fact(rid,'observations',sid,{'name':rx(name),'numeric_value':num,'unit':unit},f'{name}: {num} {unit}')
for dose in [30,100,150]:fact(rid,'medications',sid,{'name':rx('RAI|iodine|radioiodine'),'dose_value':dose,'dose_unit':'mCi'},f'RAI {dose} mCi')
fact(rid,'medications',sid,{'name':rx('PSMA|lutetium'),'dose_value':6.6,'dose_unit':'GBq'},'Lu-PSMA617 6.6 GBq')
fact(rid,'procedures_devices',sid,{'name':rx('thyroidectomy'),'action':{'one_of':['completed','historical']}},'Total thyroidectomy')
fact(rid,'procedures_devices',sid,{'name':rx('EBRT|external.*beam|radiotherapy'),'action':{'one_of':['completed','historical']}},'EBRT to C2 vertebra')
fact(rid,'procedures_devices',sid,{'name':rx('fixation|debridement'),'action':'completed'},'Declined fixation is not completed surgery',kind='forbidden',category='planned_vs_performed')
# Extra typed-field probes for previously reviewed case material.
fact('PMC13549756.1:p2','medications','b00009',{'name':rx('flucloxacillin'),'route':{'one_of':['IV','intravenous']}},'IV flucloxacillin route survives repairs',category='medication_route')
fact('PMC13549756.1:p2','allergies','b00009',{'substance':rx('milk'),'assertion':'present'},'Milk sensitization/allergy signal')
fact('PMC13549756.1:p2','care_plans','b00009',{'action_text':rx('exclusion|avoid'),'action_status':{'one_of':['recommended','planned']}},'Continued allergen-food exclusion recommended')
fact('PMC13549756.1:p2','social_exposures','b00009',{'subject':{'one_of':['family_member','mother']},'domain':'diet','name':rx('vegan')},'Maternal vegan diet belongs to mother, not infant')
# Correct an impossible old oncology action probe against the actual schema.
for check in ref['checks']:
 if check['id']=='C0018':
  check['pattern']['action']={'one_of':['given','ongoing']}
  check['label_correction']='Oncology treatment uses given/ongoing, not medication/procedure action enums; source explicitly says no adjuvant chemotherapy/radiotherapy.'
ref['checks']+=new_checks;ref['schema_version']='source-fidelity-reference/3'
ref['source_review']='Expanded finite source-checked development labels; no physician adjudication or exhaustive precision/recall claim. Prior pilot articles are identified, not called blinded external validation.'
# Temporal/summary gold is based on source propositions, never output event IDs.
timelines=[];summary=[]
def course(rid,nodes,edges):
 a=by[rid.split(':')[0]];ns=[]
 for nid,pattern,sids,occ in nodes:
  evidence=[{'segment_id':sid,'quote':next(s['text'] for s in a['segments'] if s['segment_id']==sid)} for sid in sids]
  ns.append({'id':nid,'pattern':{'description':rx(pattern),'occurrence':occ},'source':evidence})
  if occ=='occurred': summary.append({'record_id':rid,'pattern':rx(pattern),'source':evidence,'text_sha256':a['text_sha256']})
 timelines.append({'record_id':rid,'text_sha256':a['text_sha256'],'nodes':ns,'edges':[{'from':x,'to':y,'relation':'before','source':list({json.dumps(e,sort_keys=True):e for n in ns if n['id'] in {x,y} for e in n['source']}.values())} for x,y in edges]})
# Read and annotate the complete birth-to-follow-up course.
a=by['PMC13542380.1'];find=lambda token:next(s['segment_id'] for s in a['segments'] if token in s['text'])
reint=find('day 14');trach=find('day 55');follow=find('3-month follow-up')
course('PMC13542380.1:p1',[('birth','birth|born|cesarean',['b00006'],'occurred'),('reintubation','reintubat',[reint],'occurred'),('tracheostomy','tracheostom',[trach],'occurred'),('surgery','excis|commissure|adhesion.*releas|releas.*adhesion',['b00012','b00013','b00014'],'occurred'),('followup','follow.up|months',[follow],'occurred')],[('birth','reintubation'),('reintubation','tracheostomy'),('tracheostomy','surgery'),('surgery','followup')])
course('PMC13612109.1:p1',[('thyroidectomy','thyroidectom',['b00003'],'occurred'),('rai100','100.*mCi|mCi.*100',['b00003'],'occurred'),('rai150','150.*mCi|mCi.*150',['b00003'],'occurred'),('lu','PSMA.*617|6.6.*GBq',['b00003'],'occurred'),('response','249|reduction.*pain|pain.*reduc',['b00003'],'occurred')],[('thyroidectomy','rai100'),('rai100','rai150'),('rai150','lu'),('lu','response')])
for pid,sid,proc in [('p1','b00021','b00022'),('p2','b00025','b00026')]:
 course('PMC13570994.1:'+pid,[('presentation','present|aneurysm|TAAA',[sid],'occurred'),('procedure','stent|repair|BIS2T',[proc],'occurred'),('followup','follow.up|patent|endoleak',[('b00024' if pid=='p1' else 'b00026')],'occurred')],[('presentation','procedure'),('procedure','followup')])
# Label additional clinically important orders using existing authored checks.
for rid,entries in {
 'PMC13314001.1:p1':[('biopsy','biopsy','b00010'),('excision','excis|resect','b00010'),('followup','follow.up|recurrence','b00010')],
 'PMC13549756.1:p2':[('presentation','present|lethargy|vomit','b00009'),('therapy','fluclox|antibiotic|fluid','b00009'),('discharge','discharg','b00009'),('followup','11 months|review','b00009')]
}.items():
 course(rid,[(nid,pattern,[sid],'occurred') for nid,pattern,sid in entries],[(entries[i][0],entries[i+1][0]) for i in range(len(entries)-1)])
# Treatment failure and two-patient surgical follow-up, reviewed in full source.
course('PMC13612135.1:p1',[
 ('initial','abiraterone',['b00003'],'occurred'),('presentation','137|rising.*PSA|PSA.*ris',['b00003'],'occurred'),
 ('lu','three.*cycles|3.*cycles|Lu.*PSMA',['b00006'],'occurred'),('progression','progress.*PSA|PSA.*progress',['b00006'],'occurred'),
 ('gi','abdominal pain|blood in stool',['b00007'],'occurred'),('biopsy','biopsy|adenocarcinoma',['b00007'],'occurred')],
 [('initial','presentation'),('presentation','lu'),('lu','progression'),('progression','gi'),('gi','biopsy')])
course('PMC12285374.1:p1',[('presentation','abdominal.*lump|present',['b00004'],'occurred'),('biopsy','core.*biopsy',['b00004'],'occurred'),('surgery','pancreatectomy|splenectomy',['b00004'],'occurred'),('followup','36.*month|follow.up',['b00004'],'occurred')],[('presentation','biopsy'),('biopsy','surgery'),('surgery','followup')])
course('PMC12285374.1:p2',[('presentation','abdominal fullness|present',['b00005'],'occurred'),('surgery','Hartman|rectosigmoid.*resect',['b00006'],'occurred'),('followup','24.*month|follow.up',['b00006'],'occurred')],[('presentation','surgery'),('surgery','followup')])
for pid,sid,nodes in [
 ('p1','b00004',[('exposure','drinking.*antifreeze|antifreeze.*ingest'),('presentation','slurred|stroke alert|present'),('thrombolysis','alteplase|thrombolysis'),('acidosis','metabolic acidosis'),('dialysis','hemodialysis'),('extubation','extubat')]),
 ('p2','b00048',[('presentation','vertigo|present'),('thrombolysis','thrombolysis'),('deterioration','lost consciousness|deteriorat|intubat'),('crrt','CRRT|renal replacement'),('pneumonia','pneumonia'),('death','died|death')]),
 ('p3','b00049',[('presentation','seizure|lost consciousness|present'),('thrombolysis','rt.PA|thrombolysis'),('toxicology','ethylene.*glycol|toxicolog'),('ethanol','ethanol'),('discharge','discharg')])]:
 course('PMC12802722.1:'+pid,[(nid,pattern,[sid],'occurred') for nid,pattern in nodes],[(nodes[i][0],nodes[i+1][0]) for i in range(len(nodes)-1)])
ref['bundle_gold']={'version':'patient-bundle-gold/1','timelines':timelines,'summary':summary,
 'scope':'Finite manually source-checked nodes and relations. Event order is not source paragraph order. No exact dates are required.'}
# Two limited native pixel inventories were manually inspected in the Oct 5 review.
# These labels do not adjudicate free descriptions or clinical diagnostic claims.
ref['bundle_gold']['pixels']=[
 {'article_id':'PMC13542380.1','figure_id':'fig1-10556656251395572','image_sha256':'e9e6ba7b70e70933302a48c8e36bc1dac136fa4e2a7d61bae6b1f18a4a3e95b7','panel_labels':['A','B'],'image_kind':'clinical_photograph','has_chart':False},
 {'article_id':'PMC13612109.1','figure_id':'FI2560003-1','image_sha256':'dee9cb48a6de126783d6e2336dfab786c2f61a6292dc8e0124230b29dad5f2d7','panel_labels':list('ABCDEFGH'),'image_kind':'medical_imaging','has_chart':False}]
for g in ref['bundle_gold']['pixels']:g['text_sha256']=by[g['article_id']]['text_sha256'];g['review_record']='reports/GEPA_CLINICAL_20261005.json'
# Add explicit caption ownership gold for newly reviewed single-case figures.
for aid,pids in [('PMC13542380.1',['p1']),('PMC13612109.1',['p1'])]:
 a=by[aid]
 for fig in a['figures'][:2]:
  fid=fig['figure_key'];caption=fig['caption'];segment=next(s for s in a['segments'] if caption in s['text'] or (s['kind']=='caption' and fid in s.get('figure_ids',[]))) if caption else None
  if not segment:continue
  ref.setdefault('figure_checks',[]).append({'id':'BF'+str(len(ref['figure_checks'])+1),'article_id':aid,'figure_id':fid,'text_sha256':a['text_sha256'],'source':[{'segment_id':segment['segment_id'],'quote':segment['text']}], 'panel':None,'patient_ids':pids,'scope':'individual'})
ref['figure_checks']=ref['figure_checks'][-4:]+ref['figure_checks'][:-4]
# Stratify positive/negative article groups; never split patients/panels/seeds.
positive=[r['article_id'] for r in rosters if r['roster']['patients']];negative=[r['article_id'] for r in rosters if not r['roster']['patients']];splits={k:[] for k in ['train','validation','test']}
for lane in [positive,negative]:
 lane.sort(key=lambda aid:hashlib.sha256(('bundle5724:'+aid).encode()).hexdigest());n=len(lane);cut=max(1,n//2);second=cut+max(1,n//4)
 for group,ids in [('train',lane[:cut]),('validation',lane[cut:second]),('test',lane[second:])]:splits[group]+=ids
splits['scope']='Article-group-disjoint, stratified patient-bearing/negative development examples; earlier pilot exposure is not external blinding.'
ref['optimization_splits']=splits
save('articles.jsonl.gz',articles);write('rosters.json',{'schema_version':'fixed-rosters/1','articles':rosters});write('reference.json',ref)
write('manifest.json',{'articles':len(articles),'patients':sum(len(r['roster']['patients']) for r in rosters),'checks':len(ref['checks']),'kinds':dict(Counter(c['kind'] for c in ref['checks'])),'tasks':dict(Counter(c['task'] for c in ref['checks'])),'timeline_nodes':sum(len(t['nodes']) for t in timelines),'timeline_edges':sum(len(t['edges']) for t in timelines),'summary_probes':len(summary),'splits':splits,'excluded':'PMC12773240.1 is abstract-only','fresh_network_downloads':0})
print(json.dumps(json.loads((OUT/'manifest.json').read_text()),indent=2))
