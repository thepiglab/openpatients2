"""Offline primary scoring plus explicitly secondary whitespace-only repair.

No model calls. Repair changes citation whitespace only, with the original quote,
source offsets and exact replacement retained; clinical fields cannot change.
"""
from collections import defaultdict
from copy import deepcopy
import json
from pathlib import Path
import re
from statistics import mean, median

from openpatients2.data import write_json
from openpatients2.fidelity import evaluate, summarize, without_evidence
from source_formats_v1 import ROOT, FIGURES, check_figures
from chunking_v1 import gate


def whitespace_citations(candidate, segments):
    value=deepcopy(candidate); changes=[]
    texts={s['segment_id']:s['text'] for s in segments}
    labels={label:s['segment_id'] for s in segments for label in
            [f"[{s['segment_id']}]",f"[{s['segment_id']}] {s['heading']}".strip()]}
    def visit(x,path=''):
        if isinstance(x,dict):
            if isinstance(x.get('quote'),str):
                if x.get('source_section') not in texts and x.get('source_section') in labels:
                    changes.append({'path':path+'/source_section','original':x['source_section'],
                                    'replacement':labels[x['source_section']],'kind':'exact_displayed_segment_label'})
                    x['source_section']=labels[x['source_section']]
                text=texts.get(x.get('source_section',x.get('segment_id')),'')
                quote=x['quote']
                if quote and quote not in text and quote.strip():
                    parts=re.split(r'\s+',quote.strip())
                    match=re.search(r'\s+'.join(re.escape(p) for p in parts),text)
                    if match:
                        replacement=match.group()
                        assert ' '.join(quote.split())==' '.join(replacement.split())
                        changes.append({'path':path,'original':quote,'replacement':replacement,
                                        'segment_id':x.get('source_section',x.get('segment_id')),
                                        'span':[match.start(),match.end()]})
                        x['quote']=replacement
            for k,v in x.items():visit(v,path+'/'+k)
        elif isinstance(x,list):
            for n,v in enumerate(x):visit(v,path+'/'+str(n))
    visit(value)
    assert without_evidence(candidate)==without_evidence(value)
    return value,changes


def stats(xs):
    if not xs:return None
    xs=sorted(xs);pos=.95*(len(xs)-1);lo=int(pos)
    return {'n':len(xs),'mean':mean(xs),'median':median(xs),'p95':xs[lo]+(xs[min(lo+1,len(xs)-1)]-xs[lo])*(pos-lo),'sum':sum(xs)}


def panel_key(panel):
    return str(panel).lower().removeprefix('panel').strip(' ().') if panel else None


def ownership(rows,gold):
    assigned=[]
    for g in gold:
        found=[a for r in rows if r.get('figure_id')==g['figure_id'] for a in r.get('assignments',[])
               if panel_key(a.get('panel')) in {None,panel_key(g['panel'])}]
        a=found[0] if len(found)==1 else None
        correct=bool(a and set(a.get('patient_ids',[]))==set(g['patient_ids']) and
                     a.get('scope') not in {'unresolved','aggregate'} and
                     (g['scope']!='shared' or a.get('scope')=='shared'))
        wrong=bool(a and a.get('patient_ids') and set(a['patient_ids'])!=set(g['patient_ids']))
        assigned.append({'gold':g,'prediction':a,'correct':correct,'wrong_patient':wrong})
    return assigned


def run():
    reference=json.loads((ROOT/'reference.json').read_text())
    gold=json.loads((ROOT/'figure-gold.json').read_text())['units']
    sources=json.loads((ROOT/'sources.json').read_text());rosters=json.loads((ROOT/'rosters.json').read_text())
    extensions=['firecrawl','firecrawl-regions']
    for ext in extensions:
        if (ROOT/ext/'sources.json').exists():
            for pid,arms in json.loads((ROOT/ext/'sources.json').read_text()).items():sources[pid].update(arms)
    groups=defaultdict(list)
    paths=list((ROOT/'outcomes').glob('*.json'))+[p for ext in extensions for p in (ROOT/ext/'outcomes').glob('*.json')]
    for p in sorted(paths):
        r=json.loads(p.read_text());groups[(r['model'],r['format'])].append(r)
    output=[];details=[];repairs=[];audit=[]
    for (model,fmt),rows in groups.items():
        scores={k:[] for k in ['raw','strict','whitespace']};figures={k:[] for k in scores}
        metrics=[];cells=[]
        for r in rows:
            seg=sources[r['pmcid']]['pdf_plain' if fmt=='pdf_pixels' else fmt]
            normalized,changes=whitespace_citations(r['candidate'],seg)
            repairs += [{'model':model,'format':fmt,'domain':r['domain'],'record_id':r['record_id'],**c} for c in changes]
            metric=r['result'].get('metrics',{})
            if metric.get('attempts'):metrics.append(metric)
            if r['domain']=='figures':
                repaired,rejected=check_figures(normalized,seg,{p['patient_id'] for p in rosters[r['pmcid']]},FIGURES[r['pmcid']])
                localgold=[g for g in gold if g['pmcid']==r['pmcid']]
                for mode,pred in [('raw',(r['candidate'] or {}).get('figures',[])),('strict',r['delivered']),('whitespace',repaired)]:
                    a=ownership(pred,localgold);figures[mode]+=a
                    audit += [{'model':model,'format':fmt,'channel':mode,**v} for v in a]
            else:
                repaired,rejected=gate(r['domain'],normalized,seg)
                ref={**reference,'checks':[c for c in reference['checks'] if (c['record_id'],c['task'])==(r['record_id'],r['domain'])]}
                for mode,pred in [('raw',r['candidate']),('strict',r['delivered']),('whitespace',repaired)]:
                    these=evaluate(ref,{r['record_id']:{'raw':{r['domain']:pred}}});scores[mode]+=these
                    cells.append({'record_id':r['record_id'],'domain':r['domain'],'channel':mode,**summarize(these,'raw')})
                    for c in these:
                        details.append({'model':model,'format':fmt,'channel':mode,**c})
            write_json(ROOT/'secondary'/(model.replace('/','--')+'--'+r['pmcid']+'--'+str(r['patient_id'])+'--'+r['domain']+'--'+fmt+'.json'),
                       {'candidate':normalized,'delivered':repaired,'rejected':rejected,'citation_changes':changes})
        output.append({'model':model,'format':fmt,'completed_cells':len(rows),'expected_cells':3 if fmt=='pdf_firecrawl_regions' else 7,
            'parseable_calls':sum(r['result']['status']=='valid' for r in rows),
            'clinical':{k:summarize(v,'raw') for k,v in scores.items()},'clinical_cells':cells,
            'figures':{k:{'units':len(v),'correct':sum(a['correct'] for a in v),'wrong_patient':sum(a['wrong_patient'] for a in v)} for k,v in figures.items()},
            'cost_usd':sum(m['cost_usd'] for m in metrics),'input_tokens':stats([m['prompt_tokens'] for m in metrics]),
            'output_tokens':stats([m['completion_tokens'] for m in metrics]),
            'call_latency_seconds':stats([m['latency_seconds'] for m in metrics]),
            'citation_whitespace_changes':0,
            'status_counts':{status:sum(r['result']['status']==status for r in rows) for status in {r['result']['status'] for r in rows}}})
    for row in output:
        row['citation_whitespace_changes']=sum(r['model']==row['model'] and r['format']==row['format'] for r in repairs)
    write_json(ROOT/'scores.json',output);write_json(ROOT/'check-audit.json',details)
    write_json(ROOT/'figure-audit.json',audit);write_json(ROOT/'whitespace-repairs.json',repairs)
    for r in output:
        print(r['model'],r['format'],r['completed_cells'],'clinical',
              '/'.join(str(r['clinical'][k]['matched']) for k in ['raw','strict','whitespace']),
              'figures','/'.join(str(r['figures'][k]['correct']) for k in ['raw','strict','whitespace']),
              'USD',round(r['cost_usd'],4))


if __name__=='__main__':run()
