"""Compact read-only image annotation view with a deduplicated citation appendix."""
import argparse,json
from pathlib import Path
p=argparse.ArgumentParser();p.add_argument('path',type=Path);args=p.parse_args()
citations=[]
def compact(value):
    if isinstance(value,list):return [compact(x) for x in value]
    if isinstance(value,dict):
        if set(value)=={'segment_id','quote'}:
            if value not in citations:citations.append(value)
            return {'citation':citations.index(value)+1}
        return {k:compact(v) for k,v in value.items() if k!='assets'}
    return value
value=json.loads(args.path.read_text())
for f in value['figures']:
    print(json.dumps(compact(f),ensure_ascii=False))
for i,c in enumerate(citations,1):print('CITATION',i,json.dumps(c,ensure_ascii=False))
