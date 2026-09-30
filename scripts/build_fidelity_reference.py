"""Source-authored, partial clinical checklist. Not physician gold.

Freeze before the new comparison. These labels never enter generation prompts.
Patterns are only a reproducible screening aid; unmatched paraphrases need review.
"""
from pathlib import Path
import hashlib
import json
import re
import yaml
from openpatients2.article_tasks import check_article_task

ROOT=Path('runs/medical-fidelity-v1')
ARTICLES={a['pmcid']:a for l in (ROOT/'articles.jsonl').read_text().splitlines() if (a:=json.loads(l))}
EXPECTED={'PMC13314001':1,'PMC13314005':1,'PMC13294519':1,'PMC12773240':2,'PMC12802722':3,'PMC12285374':2,'PMC10998798':1,'PMC12807056':0,'PMC13304996':0}
CHECKS=[]


def rx(s):return {'regex':s}
def one(*values):return {'one_of':list(values)}
def add(a,p,task,description,pattern,block,kind='required',collection='items',category=None):
    article=ARTICLES[a];segment=next(s for s in article['segments'] if s['segment_id']==f'b{block:05}')
    CHECKS.append({'id':f'C{len(CHECKS)+1:04}','record_id':article['article_id']+':'+p,
        'task':task,'collection':collection,'description':description,'kind':kind,
        'category':category or task,'pattern':pattern,
        'source':[{'segment_id':segment['segment_id'],'quote':segment['text']}],
        'xml_sha256':article['xml_sha256'],'text_sha256':article['text_sha256']})


def age(a,p,n,b):
    add(a,p,'demographics',f'Age {n} years',{'subject':'index_patient','attribute':'age_at_presentation','numeric_value':n,'unit':one('year','years','yr','yrs','y')},b)
def fact(a,p,t,concept,b,description=None,**extra):
    field={'outcomes':'text_value','care_plans':'action_text'}.get(t,'name')
    add(a,p,t,description or concept,{'subject':'index_patient','assertion':'present',field:rx(concept),**extra},b)
def obs(a,p,concept,value,unit,b,comparison='=',description=None):
    pat={'subject':'index_patient','assertion':'present','name':rx(concept),'numeric_value':value,'comparator':one(comparison,None) if comparison=='=' else comparison}
    if unit:pat['unit']=rx(unit)
    add(a,p,'observations',description or f'{concept}: {comparison}{value} {unit or ""}',pat,b,category='numeric_values_units')
def forbidden(a,p,t,description,pattern,b):add(a,p,t,description,pattern,b,kind='forbidden',category='unsupported_or_wrong_patient')


# Original child case; cited mutations, malignant transformations and mean ages
# must not be assigned to this child.
a='PMC13314001';p='p1';age(a,p,11,8)
fact(a,p,'conditions',r'ameloblastic fibro.?dentinoma|\bAFD\b',10,verification_status=one('confirmed','unknown'))
fact(a,p,'procedures_devices',r'incisional biopsy|biopsy',10,action=one('completed','historical'))
fact(a,p,'procedures_devices',r'excision|resection',10,action=one('completed','historical'))
fact(a,p,'symptoms_function',r'swell',8)
add(a,p,'outcomes','No recurrence at one-year follow-up',{'subject':'index_patient','text_value':rx(r'no.*recurr|without.*recurr|recurrence.free'),'follow_up_duration_text':rx(r'1\s*year|one.year|12\s*month')},10)
forbidden(a,p,'demographics','Background mean age 15 is not this child',{'attribute':'age_at_presentation','numeric_value':15},4)
forbidden(a,p,'conditions','Literature malignant transformation is not present in child',{'subject':'index_patient','assertion':'present','verification_status':'confirmed','name':rx('sarcoma|malignant transformation')},18)
forbidden(a,p,'oncology','No source patient BRAF mutation result',{'subject':'index_patient','assertion':'present','name':rx('BRAF|V600E')},7)
CHECKS[-1]['collection']='biomarkers'

a='PMC13314005';p='p1';age(a,p,62,8)
fact(a,p,'conditions',r'small.cell osteosarcoma|\bSCOS\b',12,verification_status='confirmed')
fact(a,p,'symptoms_function',r'swell',8)
fact(a,p,'procedures_devices',r'hemi.?mandibul|mandibul.*resection',12,action=one('completed','historical'))
fact(a,p,'procedures_devices',r'fibula|microvascular.*flap',12,action=one('completed','historical'))
add(a,p,'observations','PET showed no distant metastasis',{'subject':'index_patient','name':rx('PET|positron|metasta'),'text_value':rx('no|negative|absen')},9)
add(a,p,'oncology','SATB2 positive',{'subject':'index_patient','name':rx('SATB2'),'text_value':rx('positiv')},11,collection='biomarkers')
add(a,p,'outcomes','No recurrence at report writing',{'subject':'index_patient','text_value':rx('no.*recurr|without.*recurr|recurrence.free')},12)
forbidden(a,p,'oncology','No administered adjuvant chemotherapy or radiation',{'subject':'index_patient','assertion':'present','name':rx('chemotherap|radiotherap'),'action':one('administered','started','completed')},12);CHECKS[-1]['collection']='treatments'
forbidden(a,p,'conditions','Other literature case metastases are not confirmed in patient',{'subject':'index_patient','assertion':'present','verification_status':'confirmed','name':rx('metasta')},9)
forbidden(a,p,'demographics','Other literature patients must not supply age 8',{'attribute':'age_at_presentation','numeric_value':8},15)

a='PMC13294519';p='p1';age(a,p,29,3)
fact(a,p,'conditions','pneumothorax',3)
fact(a,p,'procedures_devices',r'chest tube|thoracostom',3,action=one('completed','device_present','historical'))
fact(a,p,'procedures_devices',r'laparotom',3,action=one('completed','historical'))
fact(a,p,'procedures_devices',r'hemicraniectom',6,action=one('completed','historical'))
fact(a,p,'medications','heparin',6,action=one('started','administered','current'))
fact(a,p,'medications','heparin',6,description='Heparin stopped/reversed',action=one('stopped','held'))
fact(a,p,'medications','protamine',6,action=one('started','administered'))
add(a,p,'observations','Midline shift 8 mm',{'subject':'index_patient','name':rx('midline|shift'),'numeric_value':8,'unit':rx('^mm$|millimeter')},6,category='numeric_values_units')
add(a,p,'outcomes','Three-week ICU stay / rehab transfer',{'subject':'index_patient','text_value':rx('rehab')},6)
add(a,p,'procedures_devices','Bullet aspiration failed rather than successful removal',{'subject':'index_patient','name':rx('bullet|fragment|aspiration'),'finding':rx('unsuccess|fail|retained|lodged|not.*remov')},5)
forbidden(a,p,'procedures_devices','No stent retriever procedure performed',{'subject':'index_patient','assertion':'present','name':rx('stentriever|stent.retriev|snare'),'action':'completed'},5)
forbidden(a,p,'conditions','Discussion possibility of PFO is not confirmed diagnosis',{'subject':'index_patient','assertion':'present','verification_status':'confirmed','name':rx('patent foramen|PFO')},2)

a='PMC12773240';age(a,'p1',40,3);age(a,'p2',71,3)
for c in ['hypertension','gout','anal fissure','bicuspid aortic']:
    fact(a,'p1','conditions',c,3)
fact(a,'p1','medications','acetazolamide',3,action=one('started','administered','historical','current'))
fact(a,'p1','procedures_devices',r'third ventriculostomy|\bETV\b',3,action=one('completed','historical'))
fact(a,'p1','symptoms_function','papilledema',3)
fact(a,'p1','symptoms_function',r'ptosis',3)
fact(a,'p2','conditions','atrial fibrillation',3)
fact(a,'p2','symptoms_function','memory',3)
obs(a,'p2','basilar|arter',8,'^mm$|millimeter',3,description='Basilar artery up to 8 mm')
forbidden(a,'p2','procedures_devices','ETV belonged to patient 1',{'subject':'index_patient','assertion':'present','name':rx('ventriculostomy|ETV'),'action':'completed'},3)
forbidden(a,'p2','medications','Acetazolamide belonged to patient 1',{'subject':'index_patient','assertion':'present','name':rx('acetazolamide'),'action':one('started','administered','current')},3)
forbidden(a,'p1','conditions','Atrial fibrillation belongs to patient 2',{'subject':'index_patient','assertion':'present','name':rx('atrial fibrillation')},3)

a='PMC12802722'
for p,n,b in [('p1',54,4),('p2',78,48),('p3',54,49)]:
    age(a,p,n,b);fact(a,p,'conditions','ethylene glycol',b,verification_status='confirmed')
    fact(a,p,'medications','ethanol',b,action=one('started','administered','current'))
    forbidden(a,p,'medications','Fomepizole discussed but not documented as administered',{'subject':'index_patient','assertion':'present','name':rx('fomepizole'),'action':one('started','administered','current')},b)
fact(a,'p1','medications','alteplase',4,dose_value=73,dose_unit=rx('^mg$'))
fact(a,'p1','medications','ethanol',4,dose_value=10,dose_unit=rx('^g$|gram'))
fact(a,'p1','procedures_devices','hemodialysis|haemodialysis|dialysis',4,action=one('completed','historical'))
fact(a,'p1','procedures_devices','extubat',4,action=one('completed','removed','historical'))
add(a,'p1','outcomes','Symptom free at discharge',{'subject':'index_patient','text_value':rx('symptom.free|asymptomatic')},4)
fact(a,'p2','conditions','Parkinson',48)
fact(a,'p2','conditions','diabet',48)
fact(a,'p2','medications','thiamine',48,action=one('started','administered','current'))
fact(a,'p2','medications','pyridoxine',48,action=one('started','administered','current'))
fact(a,'p2','procedures_devices',r'continuous renal replacement|CRRT',48,action=one('completed','historical'))
add(a,'p2','outcomes','Died on day 10',{'subject':'index_patient','event':'death','time':{'text':rx('10th|10|tenth')}},48)
fact(a,'p3','conditions','stroke|infarct',49,temporality='historical')
fact(a,'p3','medications','clonazepam',49,action=one('started','administered','current'))
fact(a,'p3','medications','tiapride',49,action=one('started','administered','current'))
fact(a,'p3','medications',r'alteplase|rt.?PA|tissue plasminogen',49,dose_value=.9,dose_unit=rx('mg/kg'))
add(a,'p3','outcomes','Discharged home day 6',{'subject':'index_patient','event':'discharge','time':{'text':rx('6th|6|sixth')}},49)
forbidden(a,'p3','procedures_devices','Hemodialysis not indicated for patient 3',{'subject':'index_patient','assertion':'present','name':rx('hemodialysis|haemodialysis|CRRT'),'action':'completed'},49)
for p in ['p1','p3']:
    forbidden(a,p,'outcomes','Death belonged to patient 2',{'subject':'index_patient','assertion':'present','event':'death'},48)
forbidden(a,'p3','conditions','New current stroke must not be asserted confirmed',{'subject':'index_patient','assertion':'present','verification_status':'confirmed','temporality':'current','name':rx('acute.*stroke|acute.*infarct')},49)
# Shared-table columns, including thresholds and units. ABG collection time is
# inconsistent between caption and narrative, so these do not grade its timing.
for name,vals,unit,b in [
 ('^pH$|blood.*pH',[7.03,6.8,7.45],None,5),('PaCO2|pCO2|carbon dioxide partial',[21,24,29],'mmHg',7),
 ('bicarbonate|HCO3(?!.*std)',[5.9,3,20.2],'mmol/L',8),('base excess|^BE$',[-22.7,-27.6,-2.5],'mmol/L',10),
 ('anion gap|^AG$',[24.7,28,15.6],'mmol/L',11),('lactate|lactic acid',[15,15,1.4],'mmol/L',12),
 ('calcium|Ca2',[1.27,.84,.24],'mmol/L',13)]:
 for i,v in enumerate(vals):
  comp='<' if i==1 and b in [5,8] else '>' if i<2 and b==12 else '='
  obs(a,f'p{i+1}',name,v,unit,b,comp)
for name,vals,unit,b in [
 ('sodium',[139,143,122],'mmol/L',16),('potassium',[4.9,4.8,5.5],'mmol/L',17),
 ('creatinine',[.72,.95,.53],'mg/dL',19),('C.reactive|^CRP$',[1.12,2.3,9.2],'mg/L',25),
 ('white.*(?:blood|cell)|^WBC$',[10.75,8.43,12.47],r'G/L|10\^9/L|K/[uµμ]L',26),
 ('ha?emoglobin',[170,150,148],'g/L',27),('ethylene glycol|^EG$',[95.85,159,16.54],'mg/dL',30),
 ('sodium',[149,152,135],'mmol/L',32),('potassium',[5.4,5.9,3.9],'mmol/L',33),
 ('creatinine',[1.69,2.56,.57],'mg/dL',35),('C.reactive|^CRP$',[51.9,124,42.3],'mg/L',41),
 ('ha?emoglobin',[141,152,129],'g/L',43),('ethylene glycol|^EG$',[0,0,0],'mg/dL',46)]:
 for i,v in enumerate(vals):
  obs(a,f'p{i+1}',name,v,unit,b,description=f'{name}: {v} {unit}, '+('48 hours' if b>=32 else 'admission'))
  if b in [32,35]:
   add(a,f'p{i+1}','observations',f'{name} {v}: retain 48-hour timing',{'subject':'index_patient','name':rx(name),'numeric_value':v,'time':{'text':rx('48|two days|2 days|2nd day|second day')}},b,category='temporal')
for i,other_values in enumerate([[143,122,152,135],[139,122,149,135],[139,143,149,152]]):
 for value in other_values:
  forbidden(a,f'p{i+1}','observations',f'Wrong-patient sodium {value}',{'subject':'index_patient','name':rx('sodium'),'numeric_value':value},16)

a='PMC12285374';age(a,'p1',50,4);age(a,'p2',55,5)
for p,b in [('p1',4),('p2',6)]:
 fact(a,p,'conditions',r'solitary fibrous|\bSFT\b',b,verification_status='confirmed')
 for marker in ['CD34','STAT.?6']:
  add(a,p,'oncology',f'{marker} positive',{'subject':'index_patient','name':rx(marker),'text_value':rx('positiv')},b,collection='biomarkers')
 forbidden(a,p,'medications','Background imatinib recommendation is not treatment',{'subject':'index_patient','assertion':'present','name':rx('imatinib'),'action':one('started','administered','current')},8)
fact(a,'p1','procedures_devices','pancreatectomy',4,action=one('completed','historical'))
fact(a,'p1','procedures_devices','splenectomy',4,action=one('completed','historical'))
fact(a,'p2','procedures_devices','Hartman',6,action=one('completed','historical'))
fact(a,'p2','procedures_devices','transfus',6,action=one('completed','historical'))
for p,months,b in [('p1',36,4),('p2',24,6)]:
 add(a,p,'outcomes',f'No recurrence at {months} months',{'subject':'index_patient','text_value':rx('no.*recurr|without.*recurr|recurrence.free'),'follow_up_duration_text':rx(str(months)+r'.*month')},b)
forbidden(a,'p2','procedures_devices','Pancreatectomy belongs to patient 1',{'subject':'index_patient','assertion':'present','name':rx('pancreatectomy'),'action':'completed'},4)
forbidden(a,'p1','procedures_devices','Hartmann procedure belongs to patient 2',{'subject':'index_patient','assertion':'present','name':rx('Hartman'),'action':'completed'},6)

a='PMC10998798';p='p1'
fact(a,p,'conditions',r'Dirofilaria repens|D\.\s*repens|dirofilariosis',61,verification_status=one('confirmed','unknown'))
add(a,p,'demographics','Cat is male, not the female parasites',{'subject':'index_patient','attribute':one('documented_sex','sex_assigned_at_birth'),'text_value':rx('^male$|intact male')},5)
obs(a,p,'temperature',38.5,'°C|ºC|Celsius|^C$',14)
for name,val,unit,b in [('white.*(?:blood|cell)|WBC',15.25,r'K/[uµμ]l|10\^9/L',16),('neutrophil',11.37,r'K/[uµμ]l|10\^9/L',17),
 ('ha?emoglobin',9.3,'g/dl',24),('platelet|plateles',368,r'K/[uµμ]l|10\^9/L',29),('glucose',155,'mg/dl',34),
 ('creatinine',.8,'mg/dl',36),('calcium',10.2,'mg/dl',37),('alanine|ALT',50,'U/l',39),('amyloid',37.3,'[uµμ]g/ml|mg/L',46),
 ('total protein',8.4,'g/dl',48),('albumin$',3.3,'g/dl',49),('^globulin|total globulin',5.6,'g/dl',50),('gamma globulin',3.,'g/dl',54)]:obs(a,p,name,val,unit,b)
add(a,p,'observations','D. repens PCR positive',{'subject':'index_patient','name':rx(r'PCR|Dirofilaria repens|D\. repens'),'text_value':rx('positiv|detect')},61)
add(a,p,'observations','D. immitis testing negative',{'subject':'index_patient','name':rx('immitis|heartworm'),'text_value':rx('negativ|not detect|no.*detect')},60)
forbidden(a,p,'demographics','Two female parasites are not the cat sex',{'subject':'index_patient','attribute':'documented_sex','text_value':'female'},5)
forbidden(a,p,'conditions','D. immitis not the confirmed infection',{'subject':'index_patient','assertion':'present','verification_status':'confirmed','name':rx('immitis')},60)


def main():
    reference=ROOT/'reference.json'
    if reference.exists():raise ValueError('Reference is frozen; never overwrite after seeing test outputs')
    roster_dir=ROOT/'reference-rosters';roster_dir.mkdir(exist_ok=True)
    cases=[]
    for aid,expected in EXPECTED.items():
        article=ARTICLES[aid]
        old=json.loads(Path(f'runs/article-pilot/roster-final/meta--muse-glimmer-30b/{aid}.1-roster.json').read_text())['data']
        roster=check_article_task('roster',old,article)
        assert len(roster['patients'])==expected
        # This is source-reviewed identity scaffolding, not a discovery score.
        (roster_dir/(article['article_id']+'.json')).write_text(json.dumps({'xml_sha256':article['xml_sha256'],'roster':roster,
            'review_method':'Agent checked original case count, identity and species against source; roster scaffold from prior pilot; not physician adjudication.'},indent=2)+'\n')
        cases.extend({'record_id':article['article_id']+':'+p['patient_id'],'article_id':article['article_id'],'patient_id':p['patient_id'],'label':p['label'],'species':p['species']} for p in roster['patients'])
    data={'version':'source-fidelity-checklist/1','reviewer':'Codex agent, not a clinician',
        'design':'Partial source-authored factual checklist and targeted forbidden claims; freeze before fresh comparison outputs. Some sources were seen in previous pilot; not blinded or held-out validation.',
        'precision_notice':'Checklist recall is not full clinical recall; forbidden probes are not exhaustive hallucination precision. Semantic matches require adjudication of unmatched paraphrases.',
        'articles':EXPECTED,'cases':cases,'checks':CHECKS}
    reference.write_text(json.dumps(data,indent=2,ensure_ascii=False)+'\n')
    (ROOT/'reference.sha256').write_text(hashlib.sha256(reference.read_bytes()).hexdigest()+'\n')
    c=yaml.safe_load(Path('configs/experiments/article-clinical.yaml').read_text())
    spark=yaml.safe_load(Path('configs/experiments/fidelity-spark-smoke.yaml').read_text())['models'][0]
    for model in c['models']:model['context_length']=131072
    c['models'].append(spark)
    c.update(input=str(ROOT/'articles.jsonl'),output=str(ROOT/'comparison'),reference_rosters=str(roster_dir),
        article_ids=list(EXPECTED),concurrency=8,article_concurrency=8,retry_failed=False)
    Path('configs/experiments/medical-fidelity-v1.yaml').write_text(yaml.safe_dump(c,sort_keys=False))
    print({'cases':len(cases),'checks':len(CHECKS),'required':sum(x['kind']=='required' for x in CHECKS),'forbidden':sum(x['kind']=='forbidden' for x in CHECKS),'reference_sha256':hashlib.sha256(reference.read_bytes()).hexdigest()})


if __name__=='__main__':main()
