import json
from pathlib import Path
from openpatients2.longitudinal import relative_order
root=Path('benchmarks/dense500');aid='PMC12477045.1';a=json.load(open(root/'schema-drafts'/f'{aid}.json'))
a['roster'].update(reported_individual_count=3,roster_complete=True)
a['roster']['limitations']=[x for x in a['roster']['limitations'] if not x.startswith('Reported distinct count and roster')]
for p in a['patients']:
 t=p['companions']['timeline_v2']
 for e in t['events']:
  d=e['description']
  e['kind']='history' if d.startswith(('Preexisting','Symptom onset','Hypertension history','Routine examination')) else 'presentation' if d.startswith(('Current presentation','Undated admission')) else 'test' if d.startswith(('Initial outpatient','Initial laboratory','Table laboratory')) else 'diagnosis' if d.startswith('Renal biopsy') else 'treatment' if d.startswith(('Initial conservative','Initial BCD','Self-discontinued','BCD reinitiated','Switched','Monthly daratumumab')) else 'outcome' if d.startswith(('Response after','Post-discontinuation','Complete response')) else 'follow_up'
  for time in e['times']:
   text=time['text'];time.update(kind='calendar' if any(x in text for x in ['2023','2024','2018']) else 'relative' if 'after discontinuation' in text or 'of initiating' in text else 'duration',precision='range' if 'over' in text or 'within' in text else 'exact',calendar_value='2018' if text=='in 2018' else None)
 p['relative_timeline']=relative_order(t)
(root/'schema-submissions'/f'{aid}.json').write_text(json.dumps(a,ensure_ascii=False,indent=2)+'\n')
