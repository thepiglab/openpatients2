import json
from pathlib import Path
from openpatients2.longitudinal import relative_order
r=Path('benchmarks/dense500');aid='PMC12825434.1';a=json.load(open(r/'schema-drafts'/f'{aid}.json'));a['roster'].update(reported_individual_count=1,roster_complete=True);a['roster']['limitations']=[x for x in a['roster']['limitations'] if not x.startswith('Reported distinct count and roster')]
kinds=['history','history','test','presentation','test','test','test','diagnosis','diagnosis','test','treatment','outcome','outcome','outcome','test','test','diagnosis','treatment','treatment','test','treatment','treatment','treatment','outcome','treatment','outcome','test','treatment','other','outcome','treatment','test','outcome','outcome','follow_up','other']
for p in a['patients']:
 t=p['companions']['timeline_v2']
 for e,k in zip(t['events'],kinds):
  e['kind']=k
  for tm in e['times']:tm.update(kind='relative',precision='approximate' if 'short-term' in tm['text'] or 'following days' in tm['text'] else 'exact',calendar_value=None)
 p['relative_timeline']=relative_order(t)
(r/'schema-submissions'/f'{aid}.json').write_text(json.dumps(a,ensure_ascii=False,indent=2)+'\n')
