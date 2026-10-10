import json
from pathlib import Path
from openpatients2.longitudinal import relative_order
R=Path('benchmarks/dense500');I='PMC12754322.1';T=json.loads((R/'schema-drafts'/f'{I}.json').read_text());H=json.loads((R/'annotator1_mesenteric_events.json').read_text())
for p,h in zip(T['patients'],H):
 tl=p['companions']['timeline_v2']
 for e in tl['events']:
  e['kind']=h['classifications'][e['event_id']]
  for t in e['times']:
   t['kind']='duration' if t['text'] in ('over a decade','15-month history','12-month history','preceding 15 months','past year') else 'relative';t['precision']='unknown';t['calendar_value']=None
 p['relative_timeline']=relative_order(tl)
T['roster']['reported_individual_count']=2;T['roster']['roster_complete']=True;T['roster']['limitations']=[x for x in T['roster']['limitations'] if not x.startswith('Reported distinct count and roster completeness await')]
(R/'schema-submissions'/f'{I}.json').write_text(json.dumps(T,indent=2,ensure_ascii=False)+'\n');print('Reviewed classifications,35events28edges,two original patients')
