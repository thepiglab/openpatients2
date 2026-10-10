import json
from pathlib import Path
from openpatients2.longitudinal import relative_order
R=Path('benchmarks/dense500');ID='PMC12595977.1';T=json.loads((R/'schema-drafts'/f'{ID}.json').read_text());P=T['patients'][0];timeline=P['companions']['timeline_v2']
kinds=['history','presentation','diagnosis','treatment','presentation','test','treatment','treatment','treatment','treatment','treatment','treatment','treatment','treatment','treatment','treatment','outcome','treatment','outcome','outcome','outcome','outcome','test']
for e,k in zip(timeline['events'],kinds):
 e['kind']=k
 for tm in e['times']:
  tm['precision']='approximate' if tm['text'] in ['a seven-year history','in approximately five weeks','Hospitalization lasted two months'] else 'exact' if tm['text'] in ['for 12 days','for one week','for an additional 12 days','during the first week','10 days after admission'] else 'unknown'
  tm['kind']='duration' if tm['text'] in ['a seven-year history','for 12 days','for one week','for an additional 12 days','10 days after admission','Hospitalization lasted two months'] else 'relative' if tm['text'] in ['in approximately five weeks','during the first week','until discharge'] else 'order_only'
  
  if e['event_id']=='p1-e022':tm['kind']='relative'
  tm['calendar_value']=None
P['relative_timeline']=relative_order(timeline)
T['roster']['reported_individual_count']=1;T['roster']['roster_complete']=True
T['roster']['limitations']=[s for s in T['roster']['limitations'] if not s.startswith('Reported distinct count and roster completeness await')]
(R/'schema-submissions'/f'{ID}.json').write_text(json.dumps(T,indent=2,ensure_ascii=False)+'\n')
for e in timeline['events']:print(e['event_id'],e['kind'],e['description'],[{k:v for k,v in x.items() if k!='evidence'} for x in e['times']])
print('ROSTER',T['roster']);print('FIGS',T['figure_attribution']);print('SUMMARY',P['companions']['summary']['paragraphs'] if 'paragraphs' in P['companions']['summary'] else P['companions']['summary'])
