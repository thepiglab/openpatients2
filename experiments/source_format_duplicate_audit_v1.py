"""Offline diagnostic; collapse only identical keys in a complete JSON response.

Conflicting duplicate values, malformed JSON and unfinished responses are never
recovered. Original primary outcomes are preserved without modification.
"""
import json
from pathlib import Path
from openpatients2.data import write_json
from openpatients2.fidelity import evaluate,summarize
from source_formats_v1 import ROOT,FIGURES,check_figures
from chunking_v1 import gate
from score_source_formats_v1 import whitespace_citations,ownership


def identical_duplicates(text,finish_reason):
    if finish_reason not in {'stop','eos'}:raise ValueError('Incomplete response')
    duplicates=[]
    def pairs(rows):
        result={}
        for key,value in rows:
            if key in result:
                if json.dumps(result[key],sort_keys=True)!=json.dumps(value,sort_keys=True):raise ValueError('Conflicting duplicate key')
                duplicates.append({'key':key,'value':value})
            result[key]=value
        return result
    value=json.loads(text,object_pairs_hook=pairs,parse_constant=lambda s:(_ for _ in ()).throw(ValueError('Non-finite JSON')))
    if not duplicates:raise ValueError('Not an identical-duplicate recovery')
    return value,duplicates


def run():
    reference=json.loads((ROOT/'reference.json').read_text());sources=json.loads((ROOT/'sources.json').read_text())
    for pid,x in json.loads((ROOT/'firecrawl/sources.json').read_text()).items():sources[pid].update(x)
    rosters=json.loads((ROOT/'rosters.json').read_text());gold=json.loads((ROOT/'figure-gold.json').read_text())['units'];out=[]
    for folder in [ROOT/'outcomes',ROOT/'firecrawl/outcomes']:
        for path in folder.glob('*.json'):
            r=json.loads(path.read_text())
            if r['result']['status']=='valid':continue
            response=(r['result'].get('attempt_responses') or [{}])[-1]
            if response.get('error'):continue
            try:candidate,duplicates=identical_duplicates(response.get('content',''),response.get('finish_reason'))
            except (ValueError,TypeError):continue
            segments=sources[r['pmcid']]['pdf_plain' if r['format']=='pdf_pixels' else r['format']]
            fixed,changes=whitespace_citations(candidate,segments)
            if r['domain']=='figures':
                strict,_=check_figures(candidate,segments,{p['patient_id'] for p in rosters[r['pmcid']]},FIGURES[r['pmcid']])
                recovered,_=check_figures(fixed,segments,{p['patient_id'] for p in rosters[r['pmcid']]},FIGURES[r['pmcid']])
                local=[g for g in gold if g['pmcid']==r['pmcid']]
                scores={k:{'correct':sum(a['correct'] for a in ownership(v,local)),'units':len(local)} for k,v in [('raw',candidate.get('figures',[])),('strict',strict),('citation_recovery',recovered)]}
            else:
                strict,_=gate(r['domain'],candidate,segments);recovered,_=gate(r['domain'],fixed,segments)
                ref={'checks':[c for c in reference['checks'] if c['record_id']==r['record_id'] and c['task']==r['domain']]}
                scores={k:summarize(evaluate(ref,{r['record_id']:{'raw':{r['domain']:v}}}),'raw') for k,v in [('raw',candidate),('strict',strict),('citation_recovery',recovered)]}
            out.append({'source_outcome':str(path),'model':r['model'],'format':r['format'],'domain':r['domain'],'record_id':r['record_id'],
                        'candidate':candidate,'identical_duplicates':duplicates,'citation_changes':changes,'scores':scores})
    write_json(ROOT/'identical-duplicate-audit.json',out)
    for r in out:print(r['model'],r['format'],r['domain'],r['scores'])


if __name__=='__main__':run()
