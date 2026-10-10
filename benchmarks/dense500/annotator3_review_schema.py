"""Review structural companions of this annotator's own generated schema targets.
Clinical values are compared to source-linked draft facts, never rewritten here.
"""
import json,copy,sys
from pathlib import Path
sys.path[:0]=['src','benchmarks/dense500']
from openpatients2.schemas import TASK_MODELS
from openpatients2.article_tasks import Roster,Summary,Vision
from openpatients2.figure_attribution import FigureReview
from openpatients2.longitudinal import PatientTimeline,relative_order
NEW='PMC12765201.1 PMC12747812.1 PMC12784445.1 PMC12813956.1 PMC12531118.1 PMC12800717.1 PMC12754322.1 PMC12134816.1 PMC12884857.1 PMC13344486.1 PMC12808518.1 PMC12408327.1 PMC12825684.1 PMC12736919.1 PMC12588875.1 PMC12611588.1 PMC12661979.1 PMC12831504.1 PMC12781543.1 PMC12881035.1 PMC12790446.1 PMC12952432.1'.split()
OLD='PMC10998798.1 PMC13524801.1 PMC13549756.1 PMC13314005.1 PMC13314001.1 PMC13618778.1 PMC13612135.1 PMC13624887.1'.split()
# Reviewed principal purpose of each event, in original source event order.
KINDS={
'PMC12765201.1':[['history','presentation','treatment','test','treatment','treatment','adverse_event','diagnosis','treatment','test','outcome','adverse_event','follow_up','other','follow_up']],
'PMC12747812.1':[['history','presentation','adverse_event','test','test','treatment','outcome','adverse_event','presentation','treatment','test','outcome','diagnosis','follow_up','test','history','test']],
'PMC12952432.1':[['history','history','presentation','test','adverse_event','diagnosis','treatment','outcome','outcome','outcome']],
'PMC12784445.1':[['presentation','test','test','diagnosis','test','plan','treatment','outcome','history','plan','treatment','adverse_event','outcome']],
'PMC12813956.1':[['presentation','plan','outcome','follow_up','follow_up','adverse_event','adverse_event','follow_up','treatment','follow_up','history','history','history','follow_up']],
'PMC12531118.1':[['history','presentation','test','treatment','test','test','treatment','follow_up','plan','history','test']],
'PMC12800717.1':[['presentation','diagnosis','other','treatment','adverse_event','follow_up','treatment','adverse_event','outcome','follow_up','plan'],['history']],
'PMC12754322.1':[['presentation','test','test','treatment','follow_up','test'],['presentation','test','test','treatment','follow_up']],
'PMC12134816.1':[['adverse_event','diagnosis','treatment','adverse_event','treatment','follow_up','history','follow_up'],['presentation','diagnosis','adverse_event','follow_up','adverse_event','follow_up','adverse_event','follow_up'],['presentation','diagnosis','adverse_event','treatment','follow_up','follow_up','treatment','follow_up'],['presentation','diagnosis','diagnosis','treatment','follow_up','follow_up']],
'PMC12884857.1':[['presentation','treatment','adverse_event','adverse_event','treatment','adverse_event','outcome','diagnosis','treatment','treatment','outcome']],
'PMC13344486.1':[['history','history','history','presentation','diagnosis','treatment','adverse_event','adverse_event','follow_up','outcome','follow_up']],
'PMC12808518.1':[['presentation','follow_up','diagnosis','follow_up'],['history','history','presentation','diagnosis','follow_up','diagnosis','treatment','follow_up'],['history','presentation','adverse_event','treatment','follow_up','adverse_event','follow_up']],
'PMC12408327.1':[['history','history','history','presentation','treatment','follow_up','follow_up','adverse_event','treatment','follow_up','treatment','outcome','follow_up','follow_up'],['history','history','history','presentation','treatment','follow_up','follow_up','follow_up','follow_up','follow_up','follow_up'],['history','history','history','history','presentation','adverse_event','follow_up','adverse_event','outcome']],
'PMC12825684.1':[['history','history','presentation','test','treatment','adverse_event','diagnosis','diagnosis','treatment','treatment','outcome','treatment','treatment','test','outcome','follow_up','follow_up']],
'PMC12736919.1':[['history','history','presentation','treatment','outcome','follow_up','outcome']],
'PMC12588875.1':[['history','presentation','presentation','treatment','outcome','test','test','test','diagnosis','outcome']],
'PMC12611588.1':[['presentation','follow_up','test','diagnosis','treatment','treatment','treatment','treatment','follow_up','test'],['presentation','diagnosis','treatment','treatment','treatment','follow_up'],['presentation','treatment','follow_up','diagnosis','treatment','treatment','follow_up']],
'PMC12661979.1':[['history','history','history','history','adverse_event','outcome','treatment','adverse_event','follow_up'],['history','presentation','treatment','follow_up','treatment','treatment','adverse_event','outcome','follow_up'],['history','history','presentation','treatment','follow_up','treatment','adverse_event','follow_up'],['history','presentation','treatment','outcome','follow_up','treatment','outcome','treatment','follow_up','follow_up']],
'PMC12831504.1':[['history','presentation','test','treatment','treatment','treatment','follow_up','outcome','outcome','follow_up','plan'],['treatment']],
'PMC12781543.1':[['history','presentation','test','test','treatment','test','outcome','follow_up']],
'PMC12881035.1':[['history','treatment','follow_up'],['history','test','treatment','follow_up'],['test','treatment','follow_up']],
'PMC12790446.1':[['presentation','outcome','presentation','diagnosis','treatment','treatment','outcome'],['history','presentation','diagnosis','treatment','treatment'],['presentation','test','diagnosis','treatment','outcome']],
'PMC10998798.1':[['presentation','treatment','diagnosis','test','test','test','test','test']],
'PMC13524801.1':[['treatment','test','diagnosis']],
'PMC13549756.1':[['history','history','treatment','presentation','test','treatment','outcome','follow_up','follow_up','follow_up'],['history','history','presentation','test','treatment','outcome','follow_up','plan'],['history','history','presentation','test','treatment','test','treatment','follow_up','follow_up']],
'PMC13314005.1':[['presentation','diagnosis','treatment','treatment','follow_up','presentation']],
'PMC13314001.1':[['presentation','diagnosis','treatment','follow_up']],
'PMC13618778.1':[['presentation','diagnosis','treatment','test','follow_up','treatment','treatment','treatment','test','test','plan','plan','history','history','follow_up','test']],
'PMC13612135.1':[['history','test','treatment','diagnosis','test','outcome','test']],
'PMC13624887.1':[['history','presentation','other']],
}
# Literal time phrase classifications from source meanings, not text heuristics.
TIMES={
 'the previous week':('relative','exact'),
'five years earlier':('relative','exact'),'149 days after registration':('relative','exact'),'postoperative day (POD) 23':('relative','exact'),'POD 26':('relative','exact'),'POD 115':('relative','exact'),'by POD 143':('relative','range'),'more than five years after transplantation':('relative','approximate'),
'10 weeks':('duration','exact'),'Upon discharge':('order_only','unknown'),'Three months later':('relative','exact'),'day one of hospitalization':('relative','exact'),'more than one year':('duration','approximate'),
'five years prior to admission':('relative','exact'),'for two days':('duration','exact'),'6 Hours':('relative','exact'),'Twelve hours after admission':('relative','exact'),'Six hours later':('relative','exact'),'over the next two days':('duration','exact'),'72 hours after admission':('relative','exact'),'Twelve hours later':('relative','exact'),
'A first initial physical examination':('order_only','unknown'),'Birth':('relative','exact'),'2 months after discharge':('relative','exact'),'20 months of age':('age','exact'),'over the last 2 months':('duration','exact'),'5 months of age':('age','exact'),'October 2022':('calendar','exact'),'Before treatment':('order_only','unknown'),'At the three-month follow-up':('relative','exact'),'after two cycles of therapy':('relative','exact'),
'Two weeks prior to admission':('relative','exact'),'On day 4 of therapy':('relative','exact'),'Nine days later':('relative','exact'),'a total of nine months':('duration','exact'),'On day 13, after the initiation of heparin therapy':('relative','exact'),'After six months of rivaroxaban therapy':('relative','exact'),
'48 hours post admission':('relative','exact'),'Four days later':('relative','exact'),'two days later':('relative','exact'),'several days later':('relative','approximate'),
'October 2021':('calendar','exact'),'March 2022':('calendar','exact'),'July 2023':('calendar','exact'),'February 2024':('calendar','exact'),'May 2024':('calendar','exact'),'Over the following months':('relative','approximate'),'After three months of therapy':('relative','exact'),
'Initially':('order_only','unknown'),'Before‐CR':('order_only','unknown'),'After‐CR':('order_only','unknown'),
'postoperative day 1':('relative','exact'),'postoperative day 3':('relative','exact'),'postoperative day 7':('relative','exact'),'by discharge':('order_only','unknown'),'by month 3 after discharge':('relative','exact'),'three nights prior':('relative','exact'),
'preceding 15 months':('duration','exact'),'past year':('duration','approximate'),'11 months':('duration','exact'),'6 months':('duration','exact'),
'Seven months after the initiation of treatment':('relative','exact'),'nine days later':('relative','exact'),'seven months after the initial PTE':('relative','exact'),'Approximately two months after initiating unfractionated heparin':('relative','approximate'),'After approximately three months of rivaroxaban therapy':('relative','approximate'),'One month prior to admission':('relative','exact'),'Five days after starting steroid treatment':('relative','exact'),'On day 6 after the initiation of treatment':('relative','exact'),'Three months after initiating heparin therapy':('relative','exact'),'approximately two weeks later':('relative','approximate'),'After two months of treatment':('relative','exact'),'three months after starting rivaroxaban':('relative','exact'),
'Twenty minutes into the case':('relative','exact'),'after 5 minutes':('relative','exact'),'After 5 minutes':('relative','exact'),'1 hour later':('relative','exact'),'On POD 3':('relative','exact'),'POD 5':('relative','exact'),
'5 years prior':('relative','exact'),'first pregnancy':('order_only','unknown'),'second pregnancy':('order_only','unknown'),'over 48 hours':('duration','exact'),'On the same day of her admission':('relative','exact'),'post-operative period':('order_only','unknown'),'During her ICU stay':('order_only','unknown'),'Following her ICU stay':('order_only','unknown'),'3 months post-initial testing':('relative','exact'),
'4-month history':('duration','exact'),'After three months':('relative','exact'),'roughly around 10 years ago':('relative','approximate'),'4 years ago':('relative','exact'),'after a few years':('relative','approximate'),'a couple of hours prior':('relative','approximate'),'Three years later':('relative','exact'),
'since the age of 4':('age','exact'),'for 8 years':('duration','exact'),'1-day history':('duration','exact'),'After 2 weeks':('relative','exact'),'During the next 2 weeks':('duration','exact'),'after 2 months of nintedanib treatment':('relative','exact'),'Thereafter':('order_only','unknown'),'3 months later':('relative','exact'),'5 months':('duration','exact'),'1 year ago':('relative','exact'),'4-week history':('duration','exact'),'Two weeks later':('relative','exact'),'Six months later':('relative','exact'),'1 month':('duration','exact'),'3 months':('duration','exact'),'1 year':('duration','exact'),'since the age of 6':('age','exact'),'2 months ago':('relative','exact'),'2-day history':('duration','exact'),'4 weeks after HPIV infection':('relative','exact'),'4 weeks later':('relative','exact'),
'several years earlier':('relative','approximate'),'over the past three months':('duration','exact'),'two- to three-day history':('duration','range'),'the following morning':('relative','exact'),'for two days':('duration','exact'),'for three days':('duration','exact'),'one-month follow-up':('relative','exact'),'six-month follow-up':('relative','exact'),
'completed 26 months earlier':('relative','exact'),'Baseline (Week 0)':('relative','exact'),'Weeks 1–16':('relative','range'),'26 August 2025':('calendar','exact'),'Week 16':('relative','exact'),
'over the years':('duration','approximate'),'progressive five‐day history':('duration','exact'),'On admission':('order_only','unknown'),'within 48 h':('relative','range'),'day 6 of symptom onset':('relative','exact'),'second day of admission':('relative','exact'),
'lasting over 2 years':('duration','approximate'),'in 6 months':('relative','exact'),'over 4 years':('duration','exact'),'for 4 years':('duration','exact'),
'at birth':('relative','exact'),'day 3 of life':('postnatal_age','exact'),'At 2 years of age':('age','exact'),'day 4 after the initiation of feeds':('relative','exact'),'postoperative day 28':('relative','exact'),'after four weeks':('relative','exact'),'Since the surgery':('order_only','unknown'),'At 3 years of age':('age','exact'),'two months following ingestion':('relative','exact'),'five days later':('relative','exact'),'over the subsequent three months':('duration','exact'),'At age 10':('age','exact'),'postoperative day three':('relative','exact'),'day 12':('relative','exact'),'At 11 years':('age','exact'),'nearly two months':('duration','approximate'),'approximately six weeks':('duration','approximate'),'five months after ingesting':('relative','exact'),'Over the subsequent four months':('duration','exact'),'postoperative day 10':('relative','exact'),'two months after surgery':('relative','exact'),'At 6 years':('age','exact'),'35 weeks´ gestation':('gestational_age','exact'),'for 28 days':('duration','exact'),'after 24 days':('relative','exact'),'three months later':('relative','exact'),'At 23 years':('age','exact'),
'for more than three years':('duration','approximate'),'one-day history':('duration','exact'),'24-48 hours':('relative','range'),'by day 4':('relative','range'),'day 11':('relative','exact'),'30-90 days':('duration','range'),
'six years prior':('relative','exact'),'6th postoperative day':('relative','exact'),'12-month clinical follow-up':('relative','exact'),
'approximately one year prior':('relative','approximate'),'Approximately 1.5 years after surgery':('relative','approximate'),'three years prior':('relative','exact'),'immediately prior':('order_only','unknown'),'16-month interval':('relative','exact'),'18 months after SG':('relative','exact'),
'The following day':('relative','exact'),'the same day':('relative','exact'),'day 2':('relative','exact'),'day 8':('relative','exact'),'one month ago':('relative','exact'),'day 5':('relative','exact'),
'July 2023':('calendar','exact'),'During the surgical procedure':('order_only','unknown'),'around 9 am':('calendar','approximate'),'around 12 pm':('calendar','approximate'),
'3 months of age':('age','exact'),'9 months of age':('age','exact'),'a few days after discharge':('relative','approximate'),'9 Mo':('age','exact'),'12 Mo':('age','exact'),'20 Mo':('age','exact'),'7 months':('age','exact'),'7 Mo':('age','exact'),'11 Mo':('age','exact'),'Once on the ward':('order_only','unknown'),'3 months postdischarge':('relative','exact'),'5 Mo':('age','exact'),'8 Mo':('age','exact'),
'2 months':('duration','exact'),'After 6 months':('relative','exact'),'until manuscript writing':('order_only','unknown'),'two weeks':('duration','exact'),'during follow-up':('order_only','unknown'),'50 h in 20 days':('duration','exact'),'After 50 h':('relative','exact'),'three-month evaluation':('relative','exact'),'Week 1':('relative','exact'),'Week 2':('relative','exact'),'Week 3':('relative','exact'),
'for 1 year':('duration','exact'),'At 12 months':('relative','exact'),'every 8 weeks':('duration','exact'),'At 16 months':('relative','exact'),'since birth':('relative','exact')}
SPECIMENS={'PMC12765201.1':{'FIG3'},'PMC13314005.1':{'Fig3','Fig4','Fig5','Fig6'},'PMC13314001.1':{'Fig3','Fig4','Fig5','Fig6','Fig7'},'PMC12611588.1':{'jpr370062-fig-0002'}}

def run(folder,aid):
 root=Path('benchmarks')/folder;d=json.loads((root/('annotations' if folder=='dense50' else 'drafts')/f'{aid}.json').read_text());s=json.loads((root/'sources'/f'{aid}.json').read_text());a=json.loads((root/'schema-drafts'/f'{aid}.json').read_text());seg={x['segment_id']:x for x in s['segments']}
 if a['source_text_sha256']!=d['text_sha256']:raise ValueError('stale source')
 from schema_targets import digest
 if a['annotation_sha256']!=digest(d):raise ValueError('stale annotation')
 for p,dp,kinds in zip(a['patients'],d['patients'],KINDS[aid]):
  facts={f['fact_id']:f for f in dp['facts']};sections=p['sections'];accepted={f['fact_id'] for f in dp['facts'] if f['scoring']=='include'};mapped={x['fact_id'] for x in p['reference_provenance']['fact_pointers']};assert accepted==mapped
  for link in p['reference_provenance']['fact_pointers']:
   target=sections[link['task']]
   if link['pointer']!='/':
    for part in link['pointer'].strip('/').split('/'):target=target[int(part)] if isinstance(target,list) else target[part]
   for k,v in facts[link['fact_id']]['fields'].items():
    if isinstance(v,dict):
     for sub,value in v.items():assert target[k][sub]==value,(aid,link['fact_id'],k,sub)
    else:assert target[k]==v,(aid,link['fact_id'],k)
   # Any completed clinical attribute is null/unknown/empty unless another compatible fact supplied it.
   linked=[facts[x['fact_id']]['fields'] for x in p['reference_provenance']['fact_pointers'] if x['task']==link['task'] and x['pointer']==link['pointer']]
   supplied=set().union(*(set(x) for x in linked))
   meta={'coverage','limitations','documentation_status','documentation_evidence','evidence'}
   for k,v in target.items():
    if k not in supplied|meta:assert v is None or v=='unknown' or v==[] or (k=='time' and v=={'text':None,'relation':'unknown','anchor':None,'date_iso':None}),(aid,link['fact_id'],k,v)
  for task,v in sections.items():TASK_MODELS[task].model_validate(v)
  assert len(kinds)==len(p['companions']['timeline_v2']['events']),(aid,len(kinds),len(p['companions']['timeline_v2']['events']))
  for e,kind in zip(p['companions']['timeline_v2']['events'],kinds):
   # Clinical chronology/evidence/fact references are retained without carrying state forward.
   e['kind']=kind
   if kind=='plan' and e['occurrence']=='occurred':e['occurrence']='planned'
   for t in e['times']:
    assert t['text'] in TIMES,(aid,t['text']);t['kind'],t['precision']=TIMES[t['text']];t['calendar_value']=None
    if aid=='PMC13549756.1' and t['text']=='11 months':t['kind']='age'
  timeline=p['companions']['timeline_v2']
  PatientTimeline.model_validate(timeline);p['relative_timeline']=relative_order(timeline);Summary.model_validate(p['companions']['summary']);Vision.model_validate(p['vision']['caption_only'])
 for f in a['figure_attribution']:
  for panel in f['assignments']:
   if f['figure_id'] in SPECIMENS.get(aid,set()):panel['subject']='patient_specimen'
   if aid=='PMC10998798.1' and (f['figure_id']=='Fig2' or f['figure_id']=='Fig3' and panel['panel']=='a'):panel['subject']='organism_from_patient'
  FigureReview.model_validate(f)
 for p in a['patients']:
  pid=p['source']['patient_target']['patient_id'];p['vision']['figure_attribution']=copy.deepcopy([f for f in a['figure_attribution'] if any(pid in x['patient_ids'] for x in f['assignments'])])
 roster=a['roster'];roster['roster_complete']=True;roster['reported_individual_count']=None if aid in ['PMC13524801.1','PMC12831504.1'] else len(d['patients']);roster['limitations']=[x for x in roster['limitations'] if not x.startswith('Reported distinct count and roster completeness await')];roster['limitations'].append('Roster completeness refers to the retained individually described original cases; literature cases and unenumerated aggregates are kept outside the local patient roster.')
 def cite(sid,label,species='human'):
  return {'label':label,'species':species,'identity_evidence':[{'segment_id':sid,'quote':seg[sid]['text']}],'source_segment_ids':[sid],'origin_reference_ids':list(dict.fromkeys(r for x in seg[sid].get('cross_references',[]) if x['ref_type']=='bibr' for r in x['target_ids'])),'attribution_limitations':['External literature/comparison subject; no identity inferred with an original local patient.']}
 if aid=='PMC12881035.1':roster['cited_cases']=[cite('b00028','Gevers et al. three previously published obese patients, collective summary only'),cite('b00029','Khaoudy et al. 27-year-old female'),cite('b00030','Bonanomi et al. 34-year-old female'),cite('b00031','Dantas et al. 47-year-old female'),cite('b00032','Dauleh et al. 17-year-old female with Prader-Willi syndrome')]
 if aid=='PMC13314005.1':roster['cited_cases']=[cite(f'b{n:05}',label) for n,label in [(15,'Giangaspero et al. 8-year-old female'),(16,'Kim et al. 25-year-old male'),(17,'Sethi et al. 18-year-old male'),(18,'Uma et al. 28-year-old female'),(19,'Harazono et al. 26-year-old female'),(20,'Selvakumar et al. 37-year-old male')]]
 if aid=='PMC10998798.1':roster['cited_cases']=[cite('b00072','External canine clinical comparison with D. immitis microfilariae','nonhuman')]
 if aid=='PMC12808518.1':roster['cited_cases']=[cite('b00021','Previously reported patient with MALT lymphoma eight years after cutaneous AL amyloidoma excision')]
 for c in roster['cited_cases']:
  for sid in c['source_segment_ids']:
   if sid in roster['unresolved_segment_ids']:roster['unresolved_segment_ids'].remove(sid)
 # Receipt contract permits only count/completeness roster edits directly.
 original_roster=json.loads((root/'schema-drafts'/f'{aid}.json').read_text())['roster'];original_roster['roster_complete']=roster['roster_complete'];original_roster['reported_individual_count']=roster['reported_individual_count'];a['roster']=original_roster
 Roster.model_validate(a['roster'])
 a['review_status']='annotator_structural_review_pending_receipt'
 target=root/'schema-submissions'/f'{aid}.json';target.parent.mkdir(exist_ok=True);target.write_text(json.dumps(a,ensure_ascii=False,indent=2)+'\n');print(folder,aid,'reviewed',len(a['patients']),'patients; all accepted fact fields preserved')
if __name__=='__main__':
 for folder,ids in [('dense500',NEW),('dense50',OLD)]:
  for aid in ids:run(folder,aid)
