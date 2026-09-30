"""Secondary: one bounded local repair pass, retaining each chunk's context.

No full article is supplied. Only schema/citation failures trigger repair; valid
neighbors remain byte-for-byte unchanged. Full map regeneration is limited to
unparseable sections, without repeating their summary generation.
"""
import asyncio
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import chunking_v1 as c
from chunking_parallel_v1 import Study


async def repair(study, model, unit):
    pmcid,pid,task=unit
    path=c.ROOT/'outcomes'/f"{model['id'].replace('/', '--')}-{pmcid}-{pid}-{task}-sections_repair.json"
    if path.exists(): return
    maps=await study.map_unit(model,unit,'sections')
    if model['id'] in study.circuit or model['id'] in study.exp.blocked_models:
        row={**maps,'strategy':'sections_repair','skip_reason':'Unavailable route'}
        row['repaired_maps']=row.pop('maps');c.write_json(path,row);return
    source_chunks=c.chunks(study.articles[pmcid]['segments'],'sections')
    async def one_chunk(n,original):
        trace=[]
        output=deepcopy(original); seen=source_chunks[n]
        if not original['rejected']:
            return output,trace
        candidate=original.get('candidate')
        if not isinstance(candidate,dict):
            messages=study.messages(pmcid,pid,task,seen)
            messages[-1]['content'] += '\nFORMAT RECOVERY: For this call only, return the task section directly. No outer section or summary wrapper. All task fields are required.'
            r=await study.call(model,task,messages,{'phase':'chunk_regenerate','pmcid':pmcid,'patient_id':pid,'domain':task,'chunk':n})
            value=r.get('data')
            if isinstance(value,dict) and isinstance(value.get('section'),dict):value=value['section']
            output['candidate']=value
            output['delivered'],output['rejected']=c.gate(task,value,seen)
            if r['metrics'].get('signature'):output['dependencies'].append(r['metrics']['signature'])
            trace.append({'kind':'regenerate','chunk':n,'success':output['delivered'] is not None})
        else:
            items=candidate['items']; valid={}; failed=[]
            for i,item in enumerate(items):
                passed,errors=c.gate(task,c.section([item]),seen)
                if passed and passed['items']:valid[i]=deepcopy(item)
                else:failed.append({'index':i,'item':item,'errors':errors})
            for start in range(0,len(failed),8):
                batch=failed[start:start+8]
                msg=study.reduce_messages(
                    'Repair only these failed clinical facts from the SOURCE CHUNK. Preserve supported values, units, negation, patient assignment and time relationships. '
                    'Fix citations by using actual literal source substrings and exact segment IDs. A time.text must occur literally in that fact\'s citations; '
                    'do not invent a time or attach another fact\'s time. Do not erase clinical content merely to pass validation. '
                    'If a fact is unsupported, explicitly quarantine it with a source-based reason. Valid neighboring facts are frozen outside this request. '
                    'Return {"repairs":[{"index":0,"decision":"replace|quarantine","item":<full corrected item or null>,"reason":"source-based explanation"}]}. '
                    'Use each provided index at most once. Item schema is in: '+c.dumps(c.wire_schema(task)),
                    {'patient_id':pid,'registry':study.rosters[pmcid],'source_segments':seen,'failed':batch})
                r=await study.call(model,task,msg,{'phase':'chunk_local_repair','pmcid':pmcid,'patient_id':pid,'domain':task,'chunk':n,'batch':start},cap=8192)
                if r['metrics'].get('signature'):output['dependencies'].append(r['metrics']['signature'])
                requested={f['index'] for f in batch}; touched=set()
                for op in (r.get('data') or {}).get('repairs',[]):
                    index=op.get('index'); applied=False
                    if isinstance(index,int) and index in requested and index not in touched and op.get('reason'):
                        touched.add(index)
                        if op.get('decision')=='replace':
                            passed,errors=c.gate(task,c.section([op.get('item')]),seen)
                            if passed and passed['items']:valid[index]=op['item'];applied=True
                        elif op.get('decision')=='quarantine':applied=True
                    trace.append({'kind':'repair','chunk':n,'operation':op,'applied':applied,'before':[items[index]] if isinstance(index,int) and 0<=index<len(items) else []})
            output['delivered']=c.section([valid[i] for i in sorted(valid)])
            assert all(any(item==v for v in output['delivered']['items']) for item in (original.get('delivered') or {}).get('items',[])), 'A valid neighbor was lost'
        return output,trace
    results=await asyncio.gather(*(one_chunk(n,r) for n,r in enumerate(maps['maps'])))
    outputs=[r[0] for r in results];trace=[t for r in results for t in r[1]]
    row={**study.base(model,unit,'sections_repair'),**c.union(outputs),'repaired_maps':outputs,'trace':trace}
    c.write_json(path,row)


async def main():
    os.environ['OPENROUTER_API_KEY']=Path('/tmp/op2-chunking-key').read_text().strip()
    study=Study();study.exp.slots=asyncio.Semaphore(8)
    try:
        c.freeze(study)
        protocol={'secondary_posthoc':True,'purpose':'Measure whether chunk-local targeted repair recovers validation losses without restoring full-article context',
                  'source_strategy':'sections','rounds':1,'failed_items_per_call':8,'scheduling':'Independent chunks parallel; shared 8-request semaphore',
                  'generation':'8192 cap for repairs; 16384 regeneration only for unparseable maps; original summaries retained',
                  'safety':'All valid neighbors frozen; rejected facts are explicitly retained in traces. Validation is not medical entailment.',
                  'runner_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}
        p=c.ROOT/'repair-protocol.json'
        if p.exists():assert json.loads(p.read_text())==protocol
        else:c.write_json(p,protocol)
        study.circuit.add('cohere/command-a-plus');study.exp.blocked_models['meta/muse-spark-1.2-contributor']='Account privacy restriction'
        jobs=asyncio.Semaphore(8)
        async def one(m,u):
            async with jobs:await repair(study,m,u)
        await asyncio.gather(*(one(m,u) for u in c.UNITS for m in study.models))
        c.write_json(c.ROOT/'budget-status.json',study.exp.budget.report())
    finally:await study.exp.close()


if __name__=='__main__':asyncio.run(main())
