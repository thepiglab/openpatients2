"""Prepare human-readable source audits; no LLM judge and no automatic verdicts."""
import json
import re
from pathlib import Path
from openpatients2.fidelity import without_evidence

ROOT=Path('runs/chunking-v1')


def delta(before, after):
    keys=set(before or {})|set(after or {})
    return {k:{'before':(before or {}).get(k),'after':(after or {}).get(k)} for k in sorted(keys) if (before or {}).get(k)!=(after or {}).get(k) and k!='evidence'}


def run():
    edits=json.loads((ROOT/'normalized-applied-edits.json').read_text())
    compact=[]
    for n,e in enumerate(edits):
        op=e['operation'];new=op.get('replacement',op.get('item'))
        compact.append({'audit_index':n,'model':e['model'],'strategy':e['strategy'],'record_id':e['record_id'],'task':e['task'],
            'action':op.get('action',op.get('decision')),'reason':op.get('reason'),
            'changes':[delta(b,new) for b in e['before']], 'before_evidence':[b.get('evidence',[]) for b in e['before']],
            'after_evidence':(new or {}).get('evidence',[]),'verdict':None,'rationale':None})
    (ROOT/'edit-audit-compact.json').write_text(json.dumps(compact,indent=2,ensure_ascii=False)+'\n')
    articles={a['article_id']:a for l in Path('runs/medical-fidelity-v1/articles.jsonl').read_text().splitlines() if (a:=json.loads(l))}
    sample=json.loads((ROOT/'normalized-summary-audit-sample.json').read_text())
    for r in sample:
        a=articles[r['record_id'].split(':')[0]]
        ids={e['segment_id'] for e in r['claim']['evidence']}
        r['source_segments']=[s for s in a['segments'] if s['segment_id'] in ids]
    (ROOT/'summary-audit-with-source.json').write_text(json.dumps(sample,indent=2,ensure_ascii=False)+'\n')
    paths={p.name:p for p in (ROOT/'outcomes').glob('*.json')}
    paths.update({p.name:p for p in (ROOT/'outcomes-normalized').glob('*.json')})
    flags=[]
    for path in paths.values():
        row=json.loads(path.read_text());article=articles[row['record_id'].split(':')[0]]
        segments={s['segment_id']:s for s in article['segments']}
        target=int(row['patient_id'][1:])
        for index,item in enumerate((row.get('delivered') or {}).get('items',[])):
            if item.get('subject')!='index_patient':continue
            cited=[segments[e['source_section']] for e in item.get('evidence',[]) if e.get('source_section') in segments]
            assigned=[]; table_values=[]
            for s in cited:
                if s['kind']=='table_row' and row['pmcid']=='PMC12802722':
                    cells=s['text'].split('\n')[-1].split('|')
                    if len(cells)==4 and (m:=re.fullmatch(r'\s*([<>]?)\s*([−-]?\d+(?:\.\d+)?)\s*',cells[target])):
                        table_values.append({'segment_id':s['segment_id'],'numeric_value':float(m[2].replace('−','-')),'comparator':m[1] or '='})
                elif s['kind'] not in {'table_row','table_footnote'}:
                    m=re.search(r'\bCase\s+([123])\s*$',s['heading'],re.I)
                    assigned.append(int(m[1]) if m else None)
            reasons=[]
            if cited and len(assigned)==len(cited) and all(n is not None and n!=target for n in assigned):
                reasons.append('All citations are prose under another original case heading')
            if table_values and item.get('numeric_value') is not None and not any(item['numeric_value']==v['numeric_value'] and item.get('comparator')==v['comparator'] for v in table_values):
                reasons.append('Value/comparator does not match any cited table cell in the target patient column; review possible narrative support separately')
            if reasons:
                flags.append({'model':row['model'],'strategy':row['strategy'],'record_id':row['record_id'],'task':row['task'],'item_index':index,'item':item,'reasons':reasons,'table_targets':table_values})
    (ROOT/'posthoc-attribution-flags.json').write_text(json.dumps(flags,indent=2,ensure_ascii=False)+'\n')
    print('Applied edits:',len(compact),'Summary claims:',len(sample),'Posthoc attribution flags:',len(flags))


if __name__=='__main__':run()
