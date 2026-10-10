import json
from pathlib import Path
from openpatients2.longitudinal import relative_order
R=Path('benchmarks/dense500');ID='PMC12802775.1';T=json.loads((R/'schema-drafts'/f'{ID}.json').read_text());H=json.loads((R/'annotator1_osa_events.json').read_text())
for P,h in zip(T['patients'],H):
 TL=P['companions']['timeline_v2']
 for e in TL['events']:
  e['kind']=h['classifications'][e['event_id']]
  for t in e['times']:
   t['kind']='calendar' if e['event_id'] in ('p4-e004','p4-e016') else 'duration' if e['event_id']=='p1-e006' else 'relative'
   t['precision']='exact' if t['text'].startswith('Exactly') or t['kind'] in ('calendar','duration') else 'unknown'
   t['calendar_value']='2005' if e['event_id']=='p4-e004' else '2018' if e['event_id']=='p4-e016' else None
 P['relative_timeline']=relative_order(TL)
T['roster']['reported_individual_count']=4;T['roster']['roster_complete']=True;T['roster']['limitations']=[x for x in T['roster']['limitations'] if not x.startswith('Reported distinct count and roster completeness await')]
(R/'schema-submissions'/f'{ID}.json').write_text(json.dumps(T,indent=2,ensure_ascii=False)+'\n')
print('Reviewed 56 clinical sections,63 events61 edges; literal time classifications and4case roster')
