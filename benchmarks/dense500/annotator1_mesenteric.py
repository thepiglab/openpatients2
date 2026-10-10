import json
from pathlib import Path
R=Path('benchmarks/dense500');ID='PMC12754322.1';P=R/'drafts'/f'{ID}.json';a=json.loads(P.read_text());S=json.loads((R/'sources'/f'{ID}.json').read_text());B={s['segment_id']:s['text'] for s in S['segments']}
def ev(n,q):assert q in B[f'b{n:05d}'];return dict(segment_id=f'b{n:05d}',quote=q)
def tim(q,rel,anchor):return dict(text=q,relation=rel,anchor=anchor)
def add(p,task,name,n,q,**fields):
 fid=f"{p['patient_id']}-f{max(int(f['fact_id'].split('-f')[1]) for f in p['facts'])+1:03d}"
 p['facts'].append(dict(fact_id=fid,task=task,collection='items',fields=dict(subject='index_patient',assertion='present',name=name,**fields),evidence=[ev(n,q)],clinical_statement=name,certainty='supported',scoring='include',notes=''))
 return fid
H=[]
for index,p in enumerate(a['patients']):
 pid=p['patient_id'];n=17+index;chief=7+index;hist=11+index;cap=24+index;postcap=26+index;duration='15-month history' if index==0 else '12-month history';f={x['fact_id']:x for x in p['facts']};first=p['facts'][0];first['fields']['case_kind']='multi_patient';first['fields']['chief_complaint']='postprandial abdominal discomfort, bloating, diarrhea, early satiety and weight loss' if index==0 else 'vague upper abdominal pain, weight loss, early satiety and diarrhea'
 for ff in p['facts']:
  x=ff['fields'];name=x.get('name','')
  if ff['task']=='conditions' and x.get('role') in ('history','comorbidity'):x['temporality']='historical'
  if ff['task']=='symptoms_function':
   if x.get('assertion')=='absent':ff['clinical_statement']='No '+name.lower()+'.'
   elif name in ('postprandial abdominal discomfort','bloating','diarrhea','early satiety','Weight loss','vague upper abdominal pain'):
    x['time']=tim(duration,'during','symptom history preceding gastroenterology referral');ff['evidence'].append(ev(chief,B[f'b{chief:05d}']));ff['notes']='Duration describes symptoms developing before referral and still present then; it does not assert they ended before referral.'
  if name=='Abdominal examination':x.update(method='physical examination',body_site='abdomen')
  if name in ('Signs of peritonitis','Palpable abdominal masses'):x['body_site']='abdomen'
  if 'SMA' in name and ff['task'] in ('observations','procedures_devices'):x['body_site']='superior mesenteric artery' if 'origin' not in name and 'ostial' not in name else 'superior mesenteric artery origin/ostium'
  if name=='Celiac origin calcification':x['body_site']='celiac trunk origin'
  if name=='Post-stenting completion angiography':x.update(method='completion angiography',body_site='superior mesenteric artery',time=tim('following stenting','after','SMA stenting'));ff['evidence'].append(ev(postcap,'Completion angiography following stenting (arrow) of the superior mesenteric artery.'))
  if name=='SMA ostial calcification on axial view':x['method']='CT angiography, axial view'
  if name=='SMA calcification extent':x.update(method='abdominal CT, sagittal reconstruction',body_site='superior mesenteric artery');ff['notes']='Figure 2A/B original abdominal CT phase; not a new CTA measurement.'
  if name=='Post-prandial pain at follow-up':x.update(body_site='abdomen',activity_context='post-prandial',time=tim('11 months post-endovascular intervention' if index==0 else '6 months post endovascular intervention','after','endovascular intervention'));ff['clinical_statement']='Post-prandial pain had resolved at the documented follow-up.'
  if name=='Weight regained at follow-up':ff['clinical_statement']=f"Approximately {5 if index==0 else 8} kg was regained {11 if index==0 else 6} months after intervention."
  if name=='SMA stenting':x.update(body_site='superior mesenteric artery',indication='chronic mesenteric ischemia due to SMA atherosclerosis')
  if name=='Percutaneous angioplasty':x.update(body_site='superior mesenteric artery',indication='chronic mesenteric ischemia due to SMA atherosclerosis')
  if name=='Gastroenterology registrar re-review of earlier abdominal CT':x.update(body_site='abdomen',indication='investigation of unexplained abdominal symptoms')
  if ff['task']=='outcomes' and x.get('event')=='deterioration':x['time']=tim('preceding 15 months' if index==0 else 'past year','during','pre-referral symptom course')
  if name in ('Upper endoscopy','Lower endoscopy','Abdominal CT workup'):x.update(temporality='historical',time=tim('preceding 15 months','during','prior gastrointestinal workup'));ff['evidence'].append(ev(9,'The aforementioned symptoms had worsened progressively over the preceding 15 months with extensive evaluations including upper and lower endoscopies and abdominal CT scans, all of which were non-diagnostic.'))
  if name=='Abdominal CT workup':x.update(body_site='abdomen',indication='non-specific abdominal pain');ff['evidence'].append(ev(17,'which had been performed months earlier as part of the initial work-up for non-specific abdominal pain.'))
  if name=='Prior GP GI investigations':x['time']=tim('past year','during','pre-referral symptom course');ff['evidence'].append(ev(10,'The patient reported similarly progressive symptoms for the past year.'))
 if index==0:
  f['p1-f013']['fields']['time']=tim('over a decade','during','GCA inactivity before current evaluation');f['p1-f014']['fields']['time']=tim('over a decade','during','historical GCA course');f['p1-f014']['clinical_statement']='No corticosteroid therapy during the more-than-decade inactive GCA history.'
  f['p1-f021']['fields'].update(specimen='feces',text_value='mildly elevated (250 mg/kg)',comparator='=')
  f['p1-f021']['clinical_statement']='Fecal calprotectin was mildly elevated at 250 mg/kg.'
  f['p1-f029']['fields'].update(name='SMA occluded segment length, suspected wording',method='CT angiography',body_site='superior mesenteric artery');f['p1-f029']['notes']='Suspected in the initial CTA sentence; same study is described more definitively in figure-associated prose/caption. No progression or serial scan inferred.'
  f['p1-f030']['fields'].update(method='CT angiography, sagittal reconstruction',body_site='superior mesenteric artery distal to calcification');f['p1-f030']['notes']='Definite figure-description wording for same CTA episode as suspected wording; no independent pixel length measurement.'
  f['p1-f032']['fields']['method']='PET-CT';f['p1-f033']['fields']['method']='rheumatologist clinical examination'
  ctaq='CT angiography was then performed to assess the mesenteric vasculature, confirming high-grade stenosis of the SMA with suspected 1.5 cm portion of the vessel showing occlusion (Figure 1C and D).'
  petid=add(p,'procedures_devices','PET-CT assessment for GCA reactivation',17,'a review of the patient by a rheumatologist, alongside a positron emission tomography (PET-CT) were done to assess for the presence of clinical and/or tomographic signs of reactivation of underlying GCA.',kind='procedure',action='completed',indication='assess GCA reactivation',finding='negative for GCA reactivation');p['facts'][-1]['evidence'].append(ev(17,'The PET-CT and the clinical exam were both negative for GCA reactivation.'))
  originalid='p1-f012'
 else:
  ctaq='CT angiography confirmed high-grade stenosis of the SMA (Figure 2C and D).';petid=None
  originalid=add(p,'procedures_devices','Earlier abdominal CT',18,'The abdominal CT performed for investigation of the patient´s symptoms several months prior was re-reviewed by the gastroenterology registrar',kind='procedure',action='historical',temporality='historical',body_site='abdomen',indication='investigation of gastrointestinal symptoms',time=tim('several months prior','before','gastroenterology CT re-review'))
 ctaid=add(p,'procedures_devices','Dedicated CT angiography',n,ctaq,kind='procedure',action='completed',body_site='mesenteric vasculature',indication='assess mesenteric vasculature',finding='high-grade SMA stenosis')
 angiogramid=add(p,'procedures_devices','Post-stenting completion angiography',20,'The patients underwent successful percutaneous angioplasty with stenting, demonstrated as completion angiograms in Figure 3 (Case 1) and Figure 4 (Case 2).',kind='procedure',action='completed',body_site='superior mesenteric artery',time=tim('following stenting','after','SMA stenting'),finding='patent SMA with good distal contrast filling');p['facts'][-1]['evidence'].append(ev(postcap,'Completion angiography following stenting (arrow) of the superior mesenteric artery.'))
 for name,site in [('Celiac calcification on dedicated CTA','celiac trunk'),('SMA calcification on dedicated CTA','superior mesenteric artery')]:
  q='Figure 1C and D (CT angiography protocol) show extensive calcifications at both the celiac trunk and SMA' if index==0 else 'Figure 2C and D (CT angiography protocol) show extensive calcifications at both the celiac trunk and SMA (Figure 2C, sagittal reconstruction).'
  add(p,'observations',name,n,q,kind='imaging',status='resulted',text_value='extensive calcification',method='CT angiography, sagittal reconstruction',body_site=site)
 # Rebuild an evidenced partial timeline with historical onset separated from referral/current course.
 oldfacts={x['fact_id']:x for x in p['facts']};p['events']=[];p['temporal_relations']=[];classes={}
 def event(label,kind,ids,q=None,n=chief,time=None):
  eid=f'{pid}-e{len(p["events"])+1:03d}';evidence=[ev(n,q)] if q else list({(e['segment_id'],e['quote']):e for fid in ids for e in oldfacts[fid]['evidence']}.values())
  p['events'].append(dict(event_id=eid,description=label,occurrence='occurred',fact_ids=ids,time_text=time,evidence=evidence));classes[eid]=kind;return eid
 def edge(fr,to,rel,evidence):p['temporal_relations'].append(dict(from_event_id=fr,to_event_id=to,relation=rel,evidence=evidence))
 medical=event('Pre-existing medical history', 'history',['p1-f013','p1-f014','p1-f015','p1-f016'] if index==0 else ['p2-f010','p2-f011','p2-f012','p2-f013'],n=hist,q=B[f'b{hist:05d}'],time='over a decade' if index==0 else '13 years earlier')
 symptomids=[f'{pid}-f{i:03d}' for i in (range(4,9) if index==0 else range(4,8))]
 onset=event('Onset/history of progressive symptoms before gastroenterology referral','history',symptomids,n=chief,q=B[f'b{chief:05d}'],time=duration)
 referral=event('Gastroenterology referral with ongoing symptoms and documented demographics','presentation',[f'{pid}-f001',f'{pid}-f002',f'{pid}-f003'],n=chief,q=B[f'b{chief:05d}'])
 progressive=event('Progressively worsening pre-referral symptom course','outcome',['p1-f009'] if index==0 else ['p2-f008'],n=9+index,q=B[f'b{9+index:05d}'],time='preceding 15 months' if index==0 else 'past year')
 prior=event('Earlier non-diagnostic gastrointestinal workup and abdominal CT','test',['p1-f010','p1-f011','p1-f012'] if index==0 else ['p2-f009',originalid])
 exam=event('Reported family history and abdominal examination findings','test',['p1-f017','p1-f018','p1-f019','p1-f020'] if index==0 else ['p2-f014','p2-f015','p2-f016','p2-f017'])
 labs=event('Laboratory assessment','test',['p1-f021','p1-f022'] if index==0 else ['p2-f018'])
 reread=event('Gastroenterologist re-review found previously unreported mesenteric calcification','test',['p1-f023','p1-f024','p1-f025','p1-f026'] if index==0 else ['p2-f019','p2-f020','p2-f021','p2-f022','p2-f025'])
 ctaids=['p1-f027','p1-f028','p1-f029','p1-f030'] if index==0 else ['p2-f023','p2-f024'];ctaids+=[ctaid]+[x['fact_id'] for x in p['facts'] if 'calcification on dedicated CTA' in x['fields'].get('name','')]
 cta=event('Dedicated CTA established high-grade SMA stenosis','test',ctaids)
 gca=event('Rheumatology examination and PET-CT assessed GCA reactivation','test',['p1-f031','p1-f032','p1-f033','p1-f034',petid]) if index==0 else None
 diag=event('Chronic mesenteric ischemia attributed to SMA atherosclerosis','diagnosis',['p1-f035'] if index==0 else ['p2-f026'])
 vasc=event('Referral to vascular surgery for endovascular management','plan',['p1-f036'] if index==0 else ['p2-f027'])
 treatment=event('Successful percutaneous angioplasty with SMA stenting','treatment',['p1-f037','p1-f038'] if index==0 else ['p2-f028','p2-f029'])
 completion=event('Post-stent completion angiogram showed patent SMA and distal filling','test',['p1-f039',angiogramid] if index==0 else ['p2-f030',angiogramid],time='following stenting')
 response=event('Prompt sustained improvement and full gastrointestinal symptom resolution','outcome',['p1-f042'] if index==0 else ['p2-f033'])
 followup=event('Documented weight regain and post-prandial pain resolution','follow_up',['p1-f040','p1-f041'] if index==0 else ['p2-f031','p2-f032'],time='11 months post-endovascular intervention' if index==0 else '6 months post endovascular intervention')
 edge(medical,onset,'before',[ev(hist,B[f'b{hist:05d}']),ev(chief,B[f'b{chief:05d}'])]);edge(onset,referral,'before',[ev(chief,B[f'b{chief:05d}'])]);edge(onset,progressive,'before',[ev(chief,B[f'b{chief:05d}']),ev(9+index,B[f'b{9+index:05d}'])]);edge(progressive,prior,'overlaps',[ev(9+index,B[f'b{9+index:05d}'])]);edge(prior,reread,'before',[ev(n,'which had been performed months earlier as part of the initial work-up for non-specific abdominal pain.' if index==0 else 'The abdominal CT performed for investigation of the patient´s symptoms several months prior was re-reviewed by the gastroenterology registrar')]);edge(reread,cta,'before',[ev(2,'Subsequent dedicated vascular imaging confirmed significant mesenteric artery stenosis.'),ev(n,ctaq)])
 edge(cta,diag,'before',[ev(n,ctaq),ev(19,B['b00019'])]);edge(diag,vasc,'before',[ev(19,B['b00019']),ev(20,'Both patients were referred to the vascular surgery service for further management and endovascular stenting.')]);edge(vasc,treatment,'before',[ev(20,'Both patients were referred to the vascular surgery service for further management and endovascular stenting. The patients underwent successful percutaneous angioplasty with stenting, demonstrated as completion angiograms in Figure 3 (Case 1) and Figure 4 (Case 2).')]);edge(treatment,completion,'before',[ev(postcap,'Completion angiography following stenting (arrow) of the superior mesenteric artery.')]);edge(treatment,response,'before',[ev(22,'Both patients in this report underwent successful percutaneous stenting and experienced prompt and sustained improvement, including weight gain and full resolution of gastrointestinal symptoms.')]);edge(treatment,followup,'before',[ev(20,'Case 1 had regained approximately 5 kg 11 months post-endovascular intervention with resolution of post-prandial pain.' if index==0 else 'Case 2 had regained approximately 8 kg 6 months post endovascular intervention with resolution of post-prandial pain.')])
 if gca:edge(medical,gca,'before',[ev(11,B['b00011']),ev(17,'Given the patient’s prior history of GCA, a review of the patient by a rheumatologist, alongside a positron emission tomography (PET-CT) were done to assess for the presence of clinical and/or tomographic signs of reactivation of underlying GCA.')])
 H.append({'classifications':classes})
 for summary in p['summary']:
  if index==0 and 'later CTA caption' in summary['text']:summary['text']=summary['text'].replace('A 1.5 cm SMA occlusion was initially suspected and directly described in later CTA caption;','A 1.5 cm SMA occluded segment was described as suspected in the initial CTA wording and more definitively in the same-study figure description;')
  if index==0:summary['text']=summary['text'].replace('mildly elevated fecal calprotectin','mildly elevated fecal calprotectin (250 mg/kg)')
 p['summary'][1]['fact_ids'] += [x['fact_id'] for x in p['facts'] if x['fact_id'] not in set(fid for su in p['summary'] for fid in su['fact_ids']) and x['fact_id']!=angiogramid]
 p['summary'][2]['fact_ids'].append(angiogramid)
 p['ambiguities'].append(dict(issue='Suspected versus definite SMA occlusion wording refers to one CTA episode' if index==0 else 'No specific identities of earlier GP investigations are reported',evidence=[ev(17,'suspected 1.5 cm portion of the vessel showing occlusion'),ev(24,'a 1.5 cm portion of the SMA distal to the calcification showing vascular occlusion')] if index==0 else [ev(10,'Multiple GI investigations through her general practitioner were unrevealing.')],must_not_infer='Serial change or independent measured pixel length.' if index==0 else 'Upper/lower endoscopy for Case 2 or specific excluded gastrointestinal diagnoses.'))
# Separate source caption claims from actual independent pixel reference.
a['figures'].sort(key=lambda f:f['figure_id'])
for f in a['figures']:
 f['attribution_notes']+=' Caption layer is source text only; actual original image separately inspected in visual-submissions.'
 for panel in f['panels']:
  if panel['panel']=='C':panel['description_from_caption']=panel['description_from_caption'].replace('with1.5cm','with 1.5 cm')
for row in a['source_coverage']:
 if row['disposition']=='figure_caption':row['note']='Caption/source attribution reviewed; actual pixels separately inspected and documented in visual target.'
a['article_limitations']=[
'Caption-derived author findings and independently inspected image pixels are documented in separate layers. All four original JPEGs were inspected.',
'No exact original CT dates, calendar timeline, stenosis percentages, named routine laboratory panels, or specific excluded GI disorders are reported.',
'Patient 1 suspected and more definite 1.5 cm SMA occlusion wording refers to the same CTA episode; no serial progression or pixel length measurement inferred.',
'Only patient 1 explicitly underwent upper and lower endoscopies. Patient 2 earlier investigations remain unspecified.',
'No explicit same-patient prior-report links or family/genetic linkage between index patients. Cited CMI cases and management recommendations are external background.',
'Source follow-up is approximately5kg regained at11months for Case1 and approximately8kg at6months for Case2. No antiplatelet regimen or later outcomes are documented. Readable image device-label text is preserved only in the visual target without assuming axis/model semantics.'
]
a['annotation_provenance']['passes'] += ['Independent annotator1-resume re-read all27 original units/retainedtext and raw XML empty table/supplement inventory/all21 reference contexts','Actual all four original small JPEGs viewed; lettered CT panels and readable angiogram device labels inspected','Dense site/method/specimen/temporal-field enrichment and independent unique procedure review','Rebuilt partial timeline separating remote history, symptom onset, progressive course, diagnostic phases, referral, treatment and response']
P.write_text(json.dumps(a,indent=2,ensure_ascii=False)+'\n');(R/'annotator1_mesenteric_events.json').write_text(json.dumps(H,indent=2)+'\n')
print(sum(len(x['facts']) for x in a['patients']),'facts',sum(len(x['events']) for x in a['patients']),'events',sum(len(x['temporal_relations']) for x in a['patients']),'edges')
