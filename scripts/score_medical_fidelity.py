"""No network calls. Screen frozen checks and prepare masked factual review."""
from pathlib import Path
import hashlib
import json
from collections import defaultdict
from openpatients2.fidelity import load_predictions,evaluate,summarize,validate_reference,review_sample

ROOT=Path('runs/medical-fidelity-v1')
ref=ROOT/'reference.json';reference=json.loads(ref.read_text())
assert hashlib.sha256(ref.read_bytes()).hexdigest()==(ROOT/'reference.sha256').read_text().strip()
articles={a['article_id']:a for l in (ROOT/'articles.jsonl').read_text().splitlines() if (a:=json.loads(l))}
validate_reference(reference,articles)
predictions={p.name.replace('--','/'):load_predictions(p) for p in (ROOT/'comparison').iterdir() if p.is_dir() and '--' in p.name}
output={'scoring_version':2,'reference_sha256':hashlib.sha256(ref.read_bytes()).hexdigest(),'models':{},'details':{}}
misses=[]
for model,records in predictions.items():
    rows=evaluate(reference,records);output['details'][model]=rows
    output['models'][model]={'records':len(records),**{mode:summarize(rows,mode) for mode in ['raw','delivered']},
        'by_category':{category:{mode:summarize([r for r in rows if r['category']==category],mode) for mode in ['raw','delivered']} for category in sorted({r['category'] for r in rows})}}
    checks={c['id']:c for c in reference['checks']}
    for row in rows:
        if row['kind']=='required' and row['raw']['available'] and not row['raw']['matched'] or row['kind']=='forbidden' and row['raw']['matched']:
            c=checks[row['check_id']];section=records[row['record_id']]['raw'][c['task']]
            misses.append({'model':model,**row,'description':c['description'],'pattern':c['pattern'],
                'task':c['task'],'collection':c['collection'],'candidates':section.get(c['collection'],[])})
(ROOT/'automatic-scores.json').write_text(json.dumps(output,indent=2)+'\n')
(ROOT/'checklist-review.json').write_text(json.dumps(misses,indent=2,ensure_ascii=False)+'\n')
audit,aliases,availability=review_sample(reference,predictions)
# Never overwrite manual adjudication files.
for name,data in [('claim-audit-pending.json',audit),('model-aliases.json',aliases),('audit-availability.json',availability)]:
    (ROOT/name).write_text(json.dumps(data,indent=2,ensure_ascii=False)+'\n')
print(json.dumps({model:{mode:(d[mode]['matched'],d[mode]['required_checks'],d[mode]['checks_with_parseable_section']) for mode in ['raw','delivered']} for model,d in output['models'].items()},indent=2))
print('Unmatched/forbidden review cells:',len(misses),'sampled audit claims:',len(audit))
