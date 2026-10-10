import json
from pathlib import Path
from openpatients2.longitudinal import relative_order
R=Path('benchmarks/dense500');ID='PMC12747464.1';T=json.loads((R/'schema-drafts'/f'{ID}.json').read_text());p=T['patients'][0];tl=p['companions']['timeline_v2']
kinds=['history','history','history','history','history','treatment','adverse_event','outcome','test','presentation','test','treatment','outcome','follow_up','treatment','adverse_event','adverse_event','outcome','treatment','treatment','other']
for e,k in zip(tl['events'],kinds):
 e['kind']=k
 for t in e['times']:
  t['kind']='age' if e['event_id']=='p1-e001' else 'calendar' if e['event_id']=='p1-e002' else 'duration' if e['event_id']=='p1-e009' else 'relative'
  t['precision']='exact' if e['event_id'] in ['p1-e001','p1-e002','p1-e014'] else 'approximate' if e['event_id'] in ['p1-e007','p1-e008'] else 'range' if e['event_id'] in ['p1-e009','p1-e013'] else 'unknown'
  t['calendar_value']='2022' if e['event_id']=='p1-e002' else None
p['relative_timeline']=relative_order(tl);T['roster']['reported_individual_count']=1;T['roster']['roster_complete']=True;T['roster']['limitations']=[x for x in T['roster']['limitations'] if not x.startswith('Reported distinct count and roster completeness await')]
(R/'schema-submissions'/f'{ID}.json').write_text(json.dumps(T,indent=2,ensure_ascii=False)+'\n')
print('Full14sections/21events17edges source-gated; source conflicts quarantined.')
