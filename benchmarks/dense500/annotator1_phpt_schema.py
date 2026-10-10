import json
from pathlib import Path
from openpatients2.longitudinal import relative_order
r=Path('benchmarks/dense500');aid='PMC13587162.1';a=json.load(open(r/'schema-drafts'/f'{aid}.json'));a['roster'].update(reported_individual_count=1,roster_complete=True);a['roster']['limitations']=[x for x in a['roster']['limitations'] if not x.startswith('Reported distinct count and roster')]
kinds=['history','history','history','history','presentation','test','test','test','test','test','treatment','test','test','test','test','test','diagnosis','test','test','other','treatment','treatment','outcome']
for p in a['patients']:
 t=p['companions']['timeline_v2']
 for e,kind in zip(t['events'],kinds):
  e['kind']=kind
  for tm in e['times']:
   text=tm['text'];tm.update(kind='calendar' if '2024' in text else 'order_only' if text=='at 3 years of age' else 'relative',precision='approximate' if 'shortly' in text else 'exact',calendar_value=None)
 p['relative_timeline']=relative_order(t)
(r/'schema-submissions'/f'{aid}.json').write_text(json.dumps(a,ensure_ascii=False,indent=2)+'\n')
