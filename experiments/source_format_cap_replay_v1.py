"""Separate diagnostic: replay only length-truncated figure calls at 12,288 tokens."""
import asyncio,json,os
from pathlib import Path
from openpatients2.experiment import Experiment,BudgetExceeded
from openpatients2.data import write_json
from source_formats_v1 import ROOT,parsed,add_images,check_figures,FIGURES
from score_source_formats_v1 import ownership,whitespace_citations

async def run():
    os.environ['OPENROUTER_API_KEY']=Path('/tmp/op2-format-key').read_text().strip()
    cfg=json.loads((ROOT/'config.json').read_text());cfg['output']=str(ROOT/'cap-replay/calls')
    exp=Experiment(cfg);rosters=json.loads((ROOT/'rosters.json').read_text());sources=json.loads((ROOT/'sources.json').read_text())
    for pid,arms in json.loads((ROOT/'firecrawl/sources.json').read_text()).items():sources[pid].update(arms)
    gold=json.loads((ROOT/'figure-gold.json').read_text())['units'];results=[]
    selected=[]
    for p in list((ROOT/'outcomes').glob('*.json'))+list((ROOT/'firecrawl/outcomes').glob('*.json')):
        r=json.loads(p.read_text())
        if r['domain']=='figures' and 'incomplete_finish:length' in r['result'].get('errors',[]):selected.append((p,r))
    previous=ROOT/'cap-replay/protocol.json'
    if previous.exists() and not (previous.parent/'protocol-initial.json').exists():
        write_json(previous.parent/'protocol-initial.json',json.loads(previous.read_text()))
    write_json(previous,{'intervention':'Only max_tokens changes: 6144 to 12288. Complete new output required; no truncated salvage. Original scores retained.','source_outcomes':[str(p) for p,r in selected],'budget_file':cfg['budget_file'],'amendment':'Includes the Firecrawl arm after it completed; initial two replays resume from cache.'})
    try:
        for path,r in selected:
            signature=r['result']['metrics']['signature']
            base=ROOT/'firecrawl' if r['format']=='pdf_firecrawl' else ROOT
            candidates=list((base/('calls-vision' if r['format']=='pdf_pixels' else 'calls-text')/'attempts').glob(signature+'-*.json'))
            assert len(candidates)==1
            messages=json.loads(candidates[0].read_text())['request']
            if r['format']=='pdf_pixels':
                messages[-1]['content']=messages[-1]['content'][0]['text'];messages=add_images(messages,r['pmcid'])
            m=next(m for m in cfg['models'] if m['id']==r['model'])
            try:result=await exp.call(m,'roster',messages,parsed,{**r['result']['identity'],'intervention':'output_cap_12288'},max_tokens=12288,raw_text=True)
            except BudgetExceeded as e:result={'status':'budget_blocked','errors':[str(e)],'data':None,'metrics':{}}
            candidate=result.get('data');seg=sources[r['pmcid']]['pdf_plain' if r['format']=='pdf_pixels' else r['format']]
            fixed,changes=whitespace_citations(candidate,seg)
            known={p['patient_id'] for p in rosters[r['pmcid']]}
            strict,_=check_figures(candidate,seg,known,FIGURES[r['pmcid']]);recovered,_=check_figures(fixed,seg,known,FIGURES[r['pmcid']])
            local=[g for g in gold if g['pmcid']==r['pmcid']]
            counts={}
            for mode,values in [('raw',(candidate or {}).get('figures',[])),('strict',strict),('citation_recovery',recovered)]:
                scored=ownership(values,local);counts[mode]={'correct':sum(x['correct'] for x in scored),'units':len(scored),'wrong_patient':sum(x['wrong_patient'] for x in scored)}
            results.append({'original_outcome':str(path),'format':r['format'],'model':r['model'],'result':result,'scores':counts,'citation_changes':changes})
            write_json(ROOT/'cap-replay/results.json',results)
    finally:
        write_json(ROOT/'cap-replay/budget.json',exp.budget.report());await exp.close()

if __name__=='__main__':asyncio.run(run())
