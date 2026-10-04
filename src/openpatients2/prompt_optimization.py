"""Real GEPA adapter for immutable clinical tasks; experimental prompt supplements.

Only train/validation article groups reach GEPA. Test articles and their labels
never enter reflection. Structural-only rewards are explicitly not clinical
fidelity. Neither validators nor source/schema/system contracts are optimized.
"""
from __future__ import annotations
import asyncio
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
import copy
import hashlib
import json
from pathlib import Path
import re
import time

from .article_tasks import check_article_task, SYSTEM
from .corpus_fidelity import _items
from .data import read_jsonl, write_json
from .evidence_recovery import packet_segments
from .fidelity import matches, without_evidence
from .figure_attribution import validate_figure_review
from .figure_visuals import validate_visuals, JointFigureAnalysis
from .longitudinal import PatientTimeline, audit_timeline
from .pilot_extract import PilotRunner, sources_for, safe_messages
from .pilot_review import (check_ordering, check_claim_audit, check_inventory, check_coverage,
                           panel_aligned_attribution)
from .patient_context import isolate_cited_cases
from .provenance import json_digest
from .schemas import TASK_MODELS

COMPONENTS = [*TASK_MODELS, 'summary','timeline_v2','roster','figure_attribution',
    'figure_visuals','pixel_attribution','joint_figure','ordering_review','clinical_inventory',
    'coverage_audit','claim_audit','coverage_repair','repair']


def split_articles(article_ids, seed=5724):
    # Article-level grouping keeps all patients, panels and repeated seeds together.
    ids = sorted(set(article_ids),key=lambda x:hashlib.sha256(f'{seed}:{x}'.encode()).hexdigest())
    if len(ids)<8: raise ValueError('Need at least eight independent articles for GEPA splits')
    a, b = max(1,len(ids)//2), max(1,len(ids)//4)
    return {'train':ids[:a], 'validation':ids[a:a+b], 'test':ids[a+b:],
        'scope':'article-disjoint; related publications are not yet deduplicated by clinical identity'}


def prompt_guard(text, articles):
    if not isinstance(text,str) or len(text)>6000:
        raise ValueError('Prompt supplement must be at most 6000 characters')
    if re.search(r'PMC\d+|\bp\d+\b|\bb\d{5}\b',text):
        raise ValueError('Candidate contains source-specific identifiers')
    for a in articles:
        for s in a['segments']:
            # Guard obvious answer memorization; not a proof against overfitting.
            if any(s['text'][i:i+100] in text for i in range(0,max(0,len(s['text'])-99),50)):
                raise ValueError('Candidate copied a long source passage')
    return text


def reward(task, value, status, checks, *, gold_task=None):
    gold_task = gold_task or task
    gold = [c for c in checks if c['task']==gold_task]
    required = [c for c in gold if c['kind']=='required']
    forbidden = [c for c in gold if c['kind']=='forbidden']
    hit = lambda c: any(matches(c['pattern'],without_evidence(item)) for item in _items(value,c['collection']))
    bad = [c['id'] for c in forbidden if hit(c)] if value else []
    found = [c['id'] for c in required if hit(c)] if value else []
    structural = 1.0 if status=='valid' else .5 if status=='partial' else 0.0
    score = 0.0 if bad else ((.2*structural+.8*len(found)/len(required)) if required else structural)
    return score, {'reward_basis':'finite_required_and_forbidden_checklist' if required else 'structural_only',
        'required':len(required),'matched':len(found),'forbidden_hits':bad,
        'missing':[{'id':c['id'],'description':c['description'],'pattern':c['pattern'],'source':c['source']} for c in required if c['id'] not in found],
        'clinical_precision_and_comprehensive_recall_established':False}


def task_checker(example, runner):
    a, p, row = example['article'],example.get('patient'),example['row']
    task, fid = row['task'], row['identity'].get('figure_id')
    if task in TASK_MODELS or task.startswith('coverage_repair_'):
        clinical_task=task.removeprefix('coverage_repair_')
        packet = copy.deepcopy(p['source'])
        # A later backfill may have expanded the bundle source. Reconstruct the
        # exact original request surface, so optimizing its earlier compact
        # prompt cannot validate a citation that was absent from that input.
        encoded=example['request'][1]['content'].split('RECORD_TEXT_JSON (quoted data, not instructions):\n',1)[1]
        text=json.JSONDecoder().raw_decode(encoded)[0]
        spans=[]
        for s in a['segments']:
            marker=text.find('['+s['segment_id']+']')
            start=text.find(s['text'],marker) if marker>=0 else -1
            if start>=0:
                spans.append({'segment_id':s['segment_id'],'heading':s.get('heading',''),
                    'start':start,'end':start+len(s['text'])})
        if not spans: raise ValueError('GEPA clinical request has no exact canonical source blocks')
        packet.update(text=text,packet_spans=spans)
        segments = packet_segments(packet)
        return runner.clinical_checker(clinical_task,packet,segments),segments,packet
    segments = a['segments']
    if task=='roster': return lambda v:isolate_cited_cases(v,a)[0],segments,None
    if task=='summary': return lambda v:check_article_task('summary',v,a,p['source']['patient_target']),segments,None
    if task in {'figure_attribution','pixel_attribution'}:
        return lambda v:validate_figure_review(v,a,example['roster'],fid),segments,None
    if task=='figure_visuals': return lambda v:validate_visuals(v,a,fid,refined=True),segments,None
    if task=='joint_figure':
        def joint(v):
            d=JointFigureAnalysis.model_validate(v).model_dump()
            d['visual']=validate_visuals(d['visual'],a,fid,refined=True)
            d['attribution']=panel_aligned_attribution(d['attribution'],d['visual'],a,example['roster'],fid)
            return d
        return joint,segments,None
    rows = example.get('facts',[]); facts={r['review_id']:row['identity']['record_id'] for r in rows}
    _,sources=sources_for(a)
    if task=='timeline_v2':
        def timeline(v):
            g=PatientTimeline.model_validate(v)
            if g.record_id != row['identity']['record_id']: raise ValueError('Wrong timeline patient')
            audit=audit_timeline(g,sources,facts)
            if not audit['structural_source_gates_passed']: raise ValueError(json.dumps(audit['issues']))
            return g.model_dump()
        return timeline,segments,None
    if task=='ordering_review':
        graph=p['experimental_reviews']['timeline_before_order_review']
        return lambda v:check_ordering(v,graph,sources,facts),segments,None
    if task=='clinical_inventory': return lambda v:check_inventory(v,a),segments,None
    # Recover the exact batch registry from the already saved input, never from
    # the attempted output or from held-out labels.
    request=example['request']; payload=json.JSONDecoder().raw_decode(request[1]['content'])[0]
    if task=='claim_audit':
        ids={r['fact_id'] for r in payload['facts']}; batch=[r for r in rows if r['review_id'] in ids]
        return lambda v:check_claim_audit(v,a,batch),segments,None
    if task=='coverage_audit': return lambda v:check_coverage(v,payload['inventory'],rows),segments,None
    raise ValueError('Unknown GEPA task '+task)


class ClinicalAdapter:
    def __init__(self, examples, config, endpoints, context, output, reference, component, deadline):
        self.examples=examples; self.config=config; self.endpoints=endpoints; self.context=context
        self.output=Path(output); self.reference=reference; self.component=component
        self.number=0; self.deadline=deadline
        # GEPA can resume its engine checkpoint. Keep previous rollout traces
        # immutable even when interruption happened before a state save.
        previous=[int(p.name.rsplit('-',1)[1]) for p in self.output.glob('*-*')
                  if p.name.startswith(('rollout-','reflection-')) and p.name.rsplit('-',1)[1].isdigit()]
        self.number=max(previous,default=-1)+1

    def evaluate(self,batch,candidate,capture_traces=False):
        from gepa.core.adapter import EvaluationBatch
        if time.monotonic()>=self.deadline:
            return EvaluationBatch(outputs=[None for _ in batch],scores=[0.0 for _ in batch],
                trajectories=[{'Feedback':'Component wall budget exhausted; not a medical error'} for _ in batch]
                if capture_traces else None)
        async def run():
            destination=self.output/f'rollout-{self.number:05d}'; self.number+=1
            cfg={**self.config,'prompt_overrides':candidate, 'seed':42,
                 'max_repair_rounds':2 if self.component=='repair' else 0}
            runner=PilotRunner(cfg,destination,self.endpoints,self.context,'targeted')
            async def one(index,e):
                checker,segments,packet=task_checker(e,runner)
                r=await runner.call(e['row']['task'],e['request'],checker,e['row']['identity'],
                    index%len(self.endpoints),segments,packet=packet)
                checks=[c for c in self.reference['checks'] if c['record_id']==e['row']['identity'].get('record_id')]
                score,feedback=reward(e['row']['task'],r['data'],r['status'],checks,gold_task=e['row']['task'].removeprefix('coverage_repair_'))
                trace={'Inputs':{'task':e['row']['task'],'target':e['row']['identity'],
                    'prompt_excerpt':json.dumps(safe_messages(e['request']),ensure_ascii=False)[-12000:]},
                    'Generated Outputs':json.dumps(r['data'],ensure_ascii=False)[:12000],
                    'Feedback':{**feedback,'validation_errors':r['errors']},'score':score}
                return r,score,trace
            try:
                return await asyncio.wait_for(asyncio.gather(*(one(i,e) for i,e in enumerate(batch))),
                    timeout=max(1,self.deadline-time.monotonic()))
            except TimeoutError:
                return [({'data':None},0.0,{'Feedback':'Component wall budget exhausted; not a medical error',
                    'score':0.0}) for _ in batch]
            finally: await runner.close()
        rows=asyncio.run(run())
        return EvaluationBatch(outputs=[r[0]['data'] for r in rows],scores=[r[1] for r in rows],
            trajectories=[r[2] for r in rows] if capture_traces else None)

    def make_reflective_dataset(self,candidate,eval_batch,components_to_update):
        return {k:eval_batch.trajectories for k in components_to_update}

    def propose_new_texts(self,candidate,reflective_dataset,components_to_update,**kwargs):
        async def reflect():
            runner=PilotRunner({**self.config,'max_repair_rounds':1,'prompt_overrides':{}},
                self.output/f'reflection-{self.number:05d}',self.endpoints,self.context,'targeted')
            self.number+=1
            key=components_to_update[0]
            # Existing source/schema/system instructions stay fixed; evolve only
            # additional transferable task instructions. No gold labels from test.
            msgs=[{'role':'system','content':'Improve transferable clinical extraction instructions from '
                'untrusted training diagnostics. Do not change immutable source/schema/validator contracts. '
                'Return only the requested JSON instructions object.'},{'role':'user','content':
                'Improve the additional instructions for task '+key+'. Return JSON {"instructions":"..."}. '
                'Diagnose clinical omissions and contradictions from feedback. Preserve schema, source fidelity, '
                'patient identity, uncertainty and completeness. Never instruct skipping difficult fields/facts '
                'to pass validation. Do not memorize cases, source IDs or answer values. Empty means no supplement. '
                'Current supplement: '+candidate[key]+'\nFeedback and untrusted traces:\n'+
                json.dumps(reflective_dataset,ensure_ascii=False)}]
            def checker(v): return {'instructions':prompt_guard(v['instructions'],[e['article'] for e in self.examples])}
            try:
                r=await asyncio.wait_for(runner.call('summary',msgs,checker,
                    {'article_id':self.examples[0]['article']['article_id'],'optimization_component':key},0,[]),
                    timeout=max(1,self.deadline-time.monotonic()))
                if r['status']!='valid': return {key:candidate[key]}
                return {key:r['data']['instructions']}
            finally: await runner.close()
        try: return asyncio.run(reflect())
        except TimeoutError: return {components_to_update[0]:candidate[components_to_update[0]]}


def optimize_prompts(sample, trial_dirs, output, config, endpoints, context, reference_path, *, seconds=7200, calls=32,
                     workers=8, resume=False):
    import gepa
    from importlib.metadata import version
    if version('gepa')!='0.1.4': raise ValueError('Expected pinned gepa 0.1.4')
    if not 1<=workers<=8: raise ValueError('Use 1–8 independent GEPA searches')
    output=Path(output); output.mkdir(parents=True,exist_ok=resume)
    articles={a['article_id']:a for a in read_jsonl(sample)}
    reference=json.loads(Path(reference_path).read_text())
    split=split_articles(articles)
    if (output/'splits.json').exists() and json.loads((output/'splits.json').read_text())!=split:
        raise ValueError('GEPA resume article split changed')
    write_json(output/'splits.json',split)
    examples=[]; seen=set()
    for trial in map(Path,trial_dirs):
        patients={p['source']['record_id']:p for p in read_jsonl(trial/'patients.jsonl')}
        discovery=json.loads((trial/'rosters.json').read_text())
        rosters={r['article_id']:r['roster'] for r in discovery['articles'] if r['status']=='valid'}
        reviews={p.stem:json.loads(p.read_text()) for p in (trial/'review').glob('*.json')}
        for path in sorted((trial/'tasks').glob('*.json')):
            row=json.loads(path.read_text()); identity=row['identity']; aid=identity['article_id']
            if aid in split['test'] or not row['attempts'] or row['task'] not in COMPONENTS and not row['task'].startswith('coverage_repair_'): continue
            signature=(aid,identity.get('record_id'),identity.get('figure_id'),identity.get('batch'),row['task'])
            if signature in seen: continue
            seen.add(signature)
            request=copy.deepcopy(row['attempts'][0]['request'])
            # Sanitized pixel requests have hashes instead of URLs. Reconstruct
            # exact prepared bytes from the corresponding image manifest.
            if any(isinstance(m['content'],list) for m in request):
                manifest=json.loads(Path(config['image_manifest']).read_text())
                pixel=next((r for r in manifest.get('figures',[]) if r['article_id']==aid and r['figure_id']==identity.get('figure_id')),None)
                if not pixel or pixel['status']!='ready': continue
                # Reuse the production ownership/size/hash guard without clients.
                helper=object.__new__(PilotRunner); helper.config=type('Bounds',(),{'max_image_bytes':8_000_000})()
                pixel={**pixel,'_manifest_base':str(Path(config['image_manifest']).parent.parent)}
                try: data=helper.pixels(pixel,articles[aid])
                except (ValueError,KeyError,OSError): continue
                for m in request:
                    if isinstance(m['content'],list):
                        for b in m['content']:
                            if b.get('type')=='image_url': b['image_url']={'url':data}
            rid=identity.get('record_id'); patient=patients.get(rid)
            facts=reviews.get(json_digest(rid),[]) if rid else []
            examples.append({'article':articles[aid],'patient':patient,'roster':rosters.get(aid),
                'row':row,'request':request,'facts':[r for r in facts if r['task'] in TASK_MODELS]})
    # image_manifest is runner-compatible and will be removed from overrides.
    clean_config={k:v for k,v in config.items() if k!='image_manifest'}
    manifest={'gepa_version':version('gepa'),'reference_sha256':json_digest(reference),'splits':split,
        'config_sha256':json_digest(clean_config),
        'examples_sha256':json_digest([{'identity':e['row']['identity'],'task':e['row']['task'],
            'request':safe_messages(e['request']),'source_hash':e['article']['text_sha256']} for e in examples])}
    manifest_path=output/'input-manifest.json'
    if manifest_path.exists() and json.loads(manifest_path.read_text())!=manifest:
        raise ValueError('GEPA resume inputs/configuration changed')
    write_json(manifest_path,manifest)
    # Each family evolves only its own supplement against immutable bootstrap
    # examples. Candidate -> evaluation -> reflection remains sequential INSIDE
    # one search; different families do not depend on each other's candidates.
    deadline=time.monotonic()+seconds; winners={}; reports={}
    prior=json.loads((output/'components.json').read_text()) if (output/'components.json').exists() else []
    prior_winners=json.loads((output/'best-prompts.json').read_text()) if (output/'best-prompts.json').exists() else {}
    for info in prior:
        if info['component'] in COMPONENTS and info['status'] in {'completed','insufficient_disjoint_examples'}:
            if info['status']=='completed':
                instruction=prior_winners.get(info['component'],info.get('selected_instruction'))
                if instruction is None: continue
                winners[info['component']]=prompt_guard(instruction,list(articles.values()))
            reports[info['component']]={**info,'reused_from_checkpoint':True}
    pending=[c for c in COMPONENTS if c not in reports]
    waves=max(1,(len(pending)+workers-1)//workers)
    share=seconds/waves
    def search(component):
        eligible=[e for e in examples if e['row']['task']==component or (component=='coverage_repair' and e['row']['task'].startswith('coverage_repair_')) or (component=='repair' and any(a['errors'] for a in e['row']['attempts']))]
        train=[e for e in eligible if e['article']['article_id'] in split['train']][:8]
        val=[e for e in eligible if e['article']['article_id'] in split['validation']][:4]
        info={'component':component,'train_examples':len(train),'validation_examples':len(val)}
        if not train or not val:
            info['status']='insufficient_disjoint_examples'
        elif time.monotonic()>=deadline:
            info['status']='deferred_time_budget'
        else:
            component_deadline=min(deadline,time.monotonic()+share)
            adapter=ClinicalAdapter(eligible,clean_config,endpoints,context,output/component,reference,component,component_deadline)
            try:
                engine_resumed=(output/component/'gepa/gepa_state.bin').exists()
                result=gepa.optimize(seed_candidate={component:''},trainset=train,valset=val,adapter=adapter,
                    max_metric_calls=calls,reflection_minibatch_size=min(3,len(train)),skip_perfect_score=False,
                    module_selector='round_robin',seed=5724,run_dir=str(output/component/'gepa'),
                    stop_callbacks=lambda state:time.monotonic()>=component_deadline,raise_on_exception=True)
                best=max(range(len(result.candidates)),key=lambda i:result.val_aggregate_scores[i])
                instruction=result.candidates[best][component]
                instruction=prompt_guard(instruction,[e['article'] for e in eligible])
                info.update(status='completed',scores=result.val_aggregate_scores,
                    candidate_count=len(result.candidates),metric_calls=result.total_metric_calls,
                    stopped_by_component_wall_budget=time.monotonic()>=component_deadline,
                    best_index=best,selected_instruction=instruction,
                    resumed_engine_checkpoint=engine_resumed)
                write_json(output/component/'candidates.json',result.candidates)
            except Exception as exc:
                info.update(status='failed',error_type=type(exc).__name__,error=str(exc))
        return info
    # Only the coordinator writes shared receipts, avoiding racing .tmp files.
    # Eight concurrent searches can batch on ONE B200 instead of reserving
    # eight replicas during small validation sets and single reflection calls.
    write_json(output/'progress.json',{'phase':'optimizing','workers':workers,'replicas':len(endpoints),
        'finished_components':len(reports),'total_components':len(COMPONENTS),'pending':pending,'time_unix':time.time()})
    with ThreadPoolExecutor(max_workers=workers,thread_name_prefix='gepa-family') as pool:
        futures={pool.submit(search,c):c for c in pending}
        for future in as_completed(futures):
            info=future.result(); component=info['component']; reports[component]=info
            if info['status']=='completed': winners[component]=info['selected_instruction']
            ordered=[reports[c] for c in COMPONENTS if c in reports]
            write_json(output/'components.json',ordered); write_json(output/'best-prompts.json',winners)
            write_json(output/'progress.json',{'phase':'optimizing','component':component,'status':info['status'],
                'workers':workers,'replicas':len(endpoints),'finished_components':len(reports),
                'total_components':len(COMPONENTS),'pending':[c for c in pending if c not in reports], 'time_unix':time.time()})
    ordered=[reports[c] for c in COMPONENTS]
    report={'status':'completed' if all(r['status']=='completed' for r in ordered) else 'partial',
        'gepa_version':version('gepa'),'components':ordered,'splits':split,'parallel_searches':workers,
        'test_labels_exposed_to_optimizer':False,'optimized_nonempty_components':sum(bool(v) for v in winners.values()),'automatic_production_promotion':False,
        'clinical_accuracy_established':False,'reward_notice':'Clinical tasks with gold use finite facts + forbidden checks; others structural only.'}
    write_json(output/'report.json',report)
    return winners,report
