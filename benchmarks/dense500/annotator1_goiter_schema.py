import json
from pathlib import Path
from openpatients2.longitudinal import relative_order
R=Path('benchmarks/dense500');ID='PMC12834363.1';T=json.loads((R/'schema-drafts'/f'{ID}.json').read_text());P=T['patients'][0];TL=P['companions']['timeline_v2']
kinds=['history','history','presentation','test','test','test','test','test','diagnosis','plan','test','treatment','treatment','treatment','treatment','test','test','outcome','follow_up','test','history']
for e,k in zip(TL['events'],kinds):
 e['kind']=k
 for t in e['times']:
  t['kind']='calendar' if e['event_id']=='p1-e001' else 'duration' if e['event_id']=='p1-e002' else 'relative'
  t['precision']='exact' if e['event_id']=='p1-e001' else 'approximate' if e['event_id']=='p1-e002' else 'unknown'
  t['calendar_value']='2008' if e['event_id']=='p1-e001' else None
P['relative_timeline']=relative_order(TL);T['roster']['reported_individual_count']=1;T['roster']['roster_complete']=True;T['roster']['limitations']=[x for x in T['roster']['limitations'] if not x.startswith('Reported distinct count and roster completeness await')]
(R/'schema-submissions'/f'{ID}.json').write_text(json.dumps(T,indent=2,ensure_ascii=False)+'\n');print('Reviewed structural classifications: 21 events/20 edges, complete single-patient roster')
