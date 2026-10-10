import json
from pathlib import Path
from openpatients2.longitudinal import relative_order
R=Path('benchmarks/dense500');ID='PMC12605513.1';T=json.loads((R/'schema-drafts'/f'{ID}.json').read_text())
kinds={'p1':['history','treatment','presentation','test','treatment','presentation','test','test','treatment','test','diagnosis','outcome','test','test','test','test','test','test','diagnosis','treatment','outcome','treatment','outcome','outcome','history','history','treatment'],'p2':['history','treatment','treatment','presentation','test','test','test','diagnosis','treatment','outcome','test','test','test','test','test','diagnosis','treatment','treatment','outcome','treatment','outcome','outcome','history','history']}
for p in T['patients']:
 pid=p['source']['patient_target']['patient_id'];tl=p['companions']['timeline_v2'];assert len(kinds[pid])==len(tl['events'])
 for e,k in zip(tl['events'],kinds[pid]):
  e['kind']=k
  for tm in e['times']:
   tm['kind']='relative' if tm['text'].startswith('After ') or 'prior to admission' in tm['text'] or 'before admission' in tm['text'] or 'years ago' in tm['text'] else 'order_only'
   tm['precision']='approximate' if 'year' in tm['text'] or 'months' in tm['text'] else 'exact' if tm['text'].startswith('After ') or 'One week' in tm['text'] else 'unknown';tm['calendar_value']=None
 p['relative_timeline']=relative_order(tl)
T['roster']['reported_individual_count']=2;T['roster']['roster_complete']=True;T['roster']['limitations']=[x for x in T['roster']['limitations'] if not x.startswith('Reported distinct count and roster completeness await')]
for p in T['patients']:
 pid=p['source']['patient_target']['patient_id'];p['vision']['figure_attribution']=[f for f in T['figure_attribution'] if any(pid in x['patient_ids'] for x in f['assignments'])]
(R/'schema-submissions'/f'{ID}.json').write_text(json.dumps(T,indent=2,ensure_ascii=False)+'\n')
print('Reviewed2patients/28sections:',[(p['source']['patient_target']['patient_id'],len(p['companions']['timeline_v2']['events']),len(p['companions']['timeline_v2']['edges'])) for p in T['patients']])
