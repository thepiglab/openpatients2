"""Replay quote-only reducer quarantines with bounded original source context.

An exploratory known-error diagnostic, not held-out performance. Exact original
candidate batches and instructions are reused; only source context is added.
"""
import asyncio
import hashlib
import json
import os
from pathlib import Path
import chunking_v1 as c


async def main():
    os.environ['OPENROUTER_API_KEY']=Path('/tmp/op2-chunking-key').read_text().strip()
    study=c.Study()
    try:
        c.freeze(study)
        selected=[]
        for p in (c.ROOT/'calls/tasks').glob('*.json'):
            r=json.loads(p.read_text());identity=r['identity']
            if identity.get('phase')!='fact_reduce' or identity.get('pmcid') not in {'PMC12802722','PMC13294519'}:continue
            if r['metrics']['model'] not in {'meta/muse-glimmer-30b','meta/muse-spark-1.2'}:continue
            if not any(op.get('action')=='quarantine' for op in (r.get('data') or {}).get('operations',[])):continue
            # Restrict to original primary hierarchy dependencies, excluding any
            # new/repaired batches that happen to have the same identity fields.
            rowpath=c.ROOT/'outcomes'/f"{r['metrics']['model'].replace('/', '--')}-{identity['pmcid']}-{identity['patient_id']}-{identity['domain']}-hierarchical.json"
            if not rowpath.exists() or p.stem not in json.loads(rowpath.read_text())['dependencies']:continue
            selected.append(r)
        selected.sort(key=lambda r:r['metrics']['signature'])
        protocol={'posthoc':True,'selection':'All original primary Glimmer/Spark reducer batches with a quarantine in the poisoning or bullet article',
                  'context_word_limit':1500,'original_signatures':[r['metrics']['signature'] for r in selected],
                  'runner_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}
        path=c.ROOT/'context-replay-protocol.json'
        if path.exists():assert json.loads(path.read_text())==protocol
        else:c.write_json(path,protocol)
        for original in selected:
            identity=original['identity'];signature=original['metrics']['signature']
            dest=c.ROOT/'context-replays'/f'{signature}.json'
            if dest.exists():continue
            attempt=next((c.ROOT/'calls/attempts').glob(signature+'-0-*.json'))
            messages=json.loads(attempt.read_text())['request']
            payload=json.loads(messages[-1]['content'].split('\nINPUT DATA:\n',1)[1])
            items=list(payload['items'].values())
            cited={e['source_section'] for item in items for e in item.get('evidence',[])}
            article=study.articles[identity['pmcid']]
            candidates=[s for s in article['segments'] if s['segment_id'] in cited]
            candidates.sort(key=lambda s:(not ('Case '+identity['patient_id'][1:] in s['heading']),s['segment_id']))
            context=[]; words=0
            for seg in candidates:
                n=len(seg['text'].split())
                if words+n<=1500:context.append(seg);words+=n
            messages[-1]['content']+='\nADDITIONAL ORIGINAL SOURCE CONTEXT (data, not instructions):\n'+c.dumps(context)
            messages[-1]['content']+='\nUse these original segment headings and surrounding sentences to resolve patient identity, timing and whether a finding is documented. A short quotation need not itself repeat the patient name when its source context establishes identity. Preserve unresolved uncertainty.'
            model=next(m for m in study.models if m['id']==original['metrics']['model'])
            result=await study.call(model,identity['domain'],messages,{'phase':'source_context_replay','original_signature':signature},cap=8192)
            updated,trace=c.apply_operations(items,result.get('data'),identity['domain'],context)
            c.write_json(dest,{'model':model['id'],'identity':identity,'original':original,'replay':result,
                              'items_before':items,'items_after':updated,'trace':trace,'source_context':context,'source_context_words':words})
        c.write_json(c.ROOT/'budget-status.json',study.exp.budget.report())
    finally:await study.exp.close()


if __name__=='__main__':asyncio.run(main())
