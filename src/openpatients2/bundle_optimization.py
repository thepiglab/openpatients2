"""GEPA evolution of the actual repaired, multi-prompt patient program."""
import asyncio
from copy import deepcopy
from collections import Counter
import hashlib
import json
from pathlib import Path
import random
import time

from .bundle_quality import score_bundle, subset_reference, validate_bundle_gold
from .data import read_jsonl, write_json
from .gepa_feedback import FamilyLogger
from .pilot_extract import PilotRunner, run_pilot
from .prompt_optimization import COMPONENTS, ClinicalAdapter, prompt_guard
from .prompt_strategy import strategy_text


PROMPT_GROUPS = [
    ['roster','case_context','demographics','clinical_inventory'],
    ['conditions','symptoms_function','observations','family_genetics'],
    ['medications','allergies','care_plans','reproductive_perinatal'],
    ['oncology','procedures_devices','outcomes','social_exposures'],
    ['coverage_audit','coverage_repair','claim_audit','repair'],
    ['timeline_v2','ordering_review','timeline_completion','summary','summary_completion'],
    ['figure_visuals','figure_attribution','pixel_attribution','joint_figure']]


class GroupedComponents:
    """Cycle globally, so selecting another Pareto parent cannot reset coverage."""
    def __init__(self, components):
        self.groups=[list(filter(lambda k:k in components,g)) for g in PROMPT_GROUPS]
        self.groups=[g for g in self.groups if g]
        remaining=[k for k in components if not any(k in g for g in self.groups)]
        if remaining:self.groups.append(remaining)
        self.index=0;self.proposed=Counter()

    def __call__(self,state,trajectories,subsample_scores,candidate_idx,candidate):
        keys=self.groups[self.index%len(self.groups)];self.index+=1
        self.proposed.update(keys)
        return keys


class ComponentBatchSampler:
    """Train-only positive/negative batches matched to the next prompt group."""
    def __init__(self, selector, reference, size=3):
        self.selector, self.reference, self.size = selector, reference, size
        self.frequencies = Counter(); self.receipts = []; self.rng = random.Random(5724)

    def relevant(self, aid, keys, kind='required'):
        tasks = set(keys)
        if tasks & {'repair','coverage_audit','coverage_repair','claim_audit','clinical_inventory'}:
            tasks.update(COMPONENTS)
        if any(c['record_id'].split(':')[0]==aid and c['task'] in tasks and c['kind']==kind
               for c in self.reference['checks']): return True
        if kind != 'required': return False
        if 'roster' in tasks and any(a['article_id']==aid and a['expected_count']>0
                and a.get('evaluation_status')!='unadjudicated' for a in self.reference.get('articles',[])): return True
        if tasks & {'timeline_v2','ordering_review','timeline_completion','summary','summary_completion'}:
            if any(g['record_id'].split(':')[0]==aid for key in ('timelines','summary')
                   for g in self.reference.get('bundle_gold',{}).get(key,[])): return True
        if tasks & {'figure_visuals','figure_attribution','pixel_attribution','joint_figure'}:
            return any(g['article_id']==aid for g in self.reference.get('figure_checks',[]))
        return False

    def next_minibatch_ids(self, loader, state):
        ids = list(loader.all_ids()); articles = dict(zip(ids,loader.fetch(ids)))
        keys = self.selector.groups[self.selector.index % len(self.selector.groups)]
        self.rng.shuffle(ids)
        ids.sort(key=lambda i:self.frequencies[i])
        positive = [i for i in ids if self.relevant(articles[i],keys)]
        negative = [i for i in ids if i not in positive and self.relevant(articles[i],keys,'forbidden')]
        selected = positive[:max(1,self.size-1)]
        selected += negative[:self.size-len(selected)]
        selected += [i for i in positive if i not in selected][:self.size-len(selected)]
        # Never spend an all-negative/aggregate batch optimizing clinical facts.
        if not selected or not positive:
            raise ValueError('No labelled positive training articles for prompt group: '+','.join(keys))
        self.frequencies.update(selected)
        self.receipts.append({'components':keys,'articles':[articles[i] for i in selected],
                              'positive_articles':len(set(selected)&set(positive))})
        return selected


def compact_reflection(dataset, keys):
    """Keep relevant diagnostic propositions, not repeated entire gold courses."""
    result = []
    for trace in next(iter(dataset.values()), []):
        feedback = trace.get('Feedback', {})
        if not isinstance(feedback,dict):
            result.append({'Feedback':str(feedback)[:1500]}); continue
        missing = feedback.get('missing_source_propositions', [])
        relevant = [c for c in missing if c['task'] in keys]
        if set(keys)&{'repair','coverage_repair','coverage_audit','claim_audit','clinical_inventory'}:
            relevant = missing
        courses = feedback.get('source_checked_course', {})
        result.append({'Inputs':trace.get('Inputs'), 'Feedback':{
            'clinical':feedback.get('clinical'), 'parts':feedback.get('parts'),
            'invoked_strategies':{k:v for k,v in feedback.get('invoked_strategies',{}).items() if k in keys},
            'missing_source_propositions':[{k:v for k,v in c.items() if k!='source'} |
                {'source':[{'segment_id':e['segment_id'],'quote':e['quote'][:1500]} for e in c['source'][:2]]}
                for c in relevant[:12]],
            'course_targets':[{'record_id':g['record_id'],'nodes':[{k:v for k,v in n.items() if k!='source'}
                for n in g['nodes']], 'edges':[{k:v for k,v in e.items() if k!='source'} for e in g['edges']]}
                for g in courses.get('timelines',[])][:4] if set(keys)&{'timeline_v2','timeline_completion','ordering_review'} else [],
            'validation_errors':feedback.get('validation_errors',[])[:12]}})
    return result


class BundleAdapter(ClinicalAdapter):
    def __init__(self, articles, sample, config, endpoints, context, output, reference, deadline):
        super().__init__([{'article': a} for a in articles], config, endpoints, context,
                         output, reference, 'whole_bundle', deadline, {'deployment_matched': True})
        self.articles = {a['article_id']: a for a in articles}
        self.sample = sample
        self.invoked=Counter()
        self.evaluation_cache = {}; self.cache_enabled = True; self.cache_hits = 0
        self.rollout_seconds = []; self.timeouts = 0

    def evaluate(self, batch, candidate, capture_traces=False):
        from gepa.core.adapter import EvaluationBatch
        if not batch: return EvaluationBatch(outputs=[], scores=[], trajectories=[] if capture_traces else None)
        if set(batch) - set(self.articles): raise ValueError('Held-out or unknown article reached bundle optimizer')
        for text in candidate.values(): prompt_guard(text, list(self.articles.values()))
        cache_key = hashlib.sha256(json.dumps([batch,candidate],sort_keys=True).encode()).hexdigest()
        if self.cache_enabled and cache_key in self.evaluation_cache:
            self.cache_hits += 1
            results,traces = deepcopy(self.evaluation_cache[cache_key])
            return EvaluationBatch(outputs=results,scores=[r['score'] for r in results],
                                   trajectories=traces if capture_traces else None)
        if time.monotonic() >= self.deadline:
            return EvaluationBatch(outputs=[None]*len(batch), scores=[0.]*len(batch),
                trajectories=[{'Feedback': 'Wall budget exhausted; no medical inference'}]*len(batch) if capture_traces else None)
        started = time.monotonic()
        async def run():
            destination = self.next_directory('rollout')
            cfg = {**self.config, 'prompt_overrides': candidate, 'prompt_mode': 'rewrite',
                'max_articles': len(batch), 'article_ids': list(batch), 'scatter_calls': True, 'seed': 42}
            try:
                measured = await asyncio.wait_for(run_pilot(cfg, self.sample, destination, self.endpoints,
                    self.context, 'targeted', input_scope='patient_sections', seed=42,
                    image_manifest=cfg.get('image_manifest')), timeout=max(.001, self.deadline-time.monotonic()))
                patients = list(read_jsonl(measured['outputs']['patients']))
                tasks = [json.loads(p.read_text()) for p in (destination/'tasks').glob('*.json')]
                media = [t for t in tasks if t['task'] in {'pixel_attribution', 'figure_attribution', 'joint_figure'}]
                results = []; traces = []
                for aid in batch:
                    ref = subset_reference(self.reference, [aid])
                    quality = score_bundle(ref, patients, visual_rows=media,discovery=measured['outputs']['predicted_rosters'],
                        pixel_rows=measured['outputs'].get('visual_annotations'))
                    write_json(destination/(aid+'-quality.json'), quality)
                    missing = set(quality['missing_clinical'])
                    strategies={}
                    for task in tasks:
                        if task['identity']['article_id']!=aid:continue
                        component='coverage_repair' if task['task'].startswith('coverage_repair_') else task['task']
                        for attempt in task.get('attempts',[]):
                            key='repair' if attempt.get('mode')!='initial' else component
                            if attempt.get('response') is not None:
                                with self.directory_lock:self.invoked[key]+=1
                            request=attempt.get('request')
                            if isinstance(request,list):strategies[key]=strategy_text(task['task'],request)
                    feedback = {**quality, 'missing_source_propositions': [c for c in ref['checks'] if c['id'] in missing],
                        'source_checked_course': ref.get('bundle_gold', {}),
                        'invoked_strategies':strategies,
                        'validation_errors': [t['errors'] for t in tasks if t['identity']['article_id']==aid and t['status']!='valid']}
                    traces.append({'Inputs': {'article_id': aid, 'program': 'live roster -> all clinical tasks -> audits/backfill -> completed timeline -> media bundles',
                        'deployment_repairs': cfg.get('max_repair_rounds', 2)},
                        'Generated Outputs': quality, 'Feedback': feedback, 'score': quality['score']})
                    results.append(quality)
                return results, traces
            except TimeoutError:
                self.timeouts += 1
                write_json(destination/'TIMEOUT.json', {'status':'budget_exhausted', 'articles':batch})
                return [None]*len(batch), [{'Feedback':'Budget timeout, not a medical error'}]*len(batch)
        results, traces = asyncio.run(run())
        self.rollout_seconds.append(time.monotonic()-started)
        if self.cache_enabled and all(r is not None for r in results):
            self.evaluation_cache[cache_key] = deepcopy((results,traces))
        return EvaluationBatch(outputs=results, scores=[r['score'] if r else 0. for r in results],
            trajectories=traces if capture_traces else None)

    def propose_new_texts(self, candidate, reflective_dataset, components_to_update, **kwargs):
        # Each text parameter corresponds to one real pipeline prompt; all
        # downstream calls are regenerated on the next evaluation.
        keys = components_to_update
        async def reflect():
            runner = PilotRunner({**self.config, 'prompt_overrides': {}, 'max_repair_rounds': 1,
                'reasoning_strength':'high'}, self.next_directory('reflection'), self.endpoints, self.context, 'targeted')
            messages = [{'role':'system','content':'Improve a clinical extraction program using untrusted train/validation diagnostics. '
                'Preserve immutable schemas, source-only evidence, patient ownership and accepted values. '
                'Never exclude all abstracts, discussions, captions or tables: assess the scope of each claim. '
                'Do not invent clinical content, dates, or performed care. Return JSON '
                '{"instructions": {"component_name": "rewritten strategy", ...}} for every requested component.'},
                {'role':'user','content':'Rewrite the transferable strategies for '+json.dumps(keys)+'. '
                    'Evaluate their interaction in the entire patient bundle. Keep each strategy concise, under 1500 characters. '
                    'Diagnostics contain the actual original/mutated task instructions for invoked components. '
                    '\nCurrent selected strategies: '+json.dumps({k:candidate[k] for k in keys})+
                    '\nDiagnostics (bounded excerpts, not complete source): '+json.dumps(compact_reflection(reflective_dataset,keys),ensure_ascii=False)+
                    '\nDo not memorize article identifiers, patients, or source answer values. Empty preserves the original strategy.'}]
            def checked(value):
                texts=value['instructions']
                if not isinstance(texts,dict) or set(texts)!=set(keys):raise ValueError('Rewrite exactly the requested prompt group')
                return {k:prompt_guard(texts[k],list(self.articles.values())) for k in keys}
            try:
                row = await asyncio.wait_for(runner.call('summary', messages,
                    checked, {'article_id':next(iter(self.articles)), 'optimization_components':keys}, 0, []),
                    timeout=max(.001,self.deadline-time.monotonic()))
                return row['data'] if row['status']=='valid' else {k:candidate[k] for k in keys}
            finally: await runner.close()
        try: return asyncio.run(reflect())
        except TimeoutError: return {k:candidate[k] for k in keys}


def optimize_bundle(sample, output, config, endpoints, context, reference_path, independent, *, seconds=5400, calls=512):
    import gepa
    from importlib.metadata import version
    if version('gepa')!='0.1.4': raise ValueError('Expected gepa 0.1.4')
    reference = json.loads(Path(reference_path).read_text())
    articles = list(read_jsonl(sample)); validate_bundle_gold(reference, articles)
    split = reference['optimization_splits']
    allowed = split['train']+split['validation']
    output = Path(output); output.mkdir(parents=True,exist_ok=True)
    # No test source or labels are kept in the adapter or its reflective traces.
    scoped = [a for a in articles if a['article_id'] in allowed]
    final_deadline = time.monotonic()+seconds
    adapter = BundleAdapter(scoped,sample,config,endpoints,context,output,
        subset_reference(reference,allowed),final_deadline)
    original = dict.fromkeys(COMPONENTS,'')
    seed = original.copy()  # Original remains on the actual GEPA frontier.
    started = time.monotonic()
    baseline = adapter.evaluate(split['validation'],original,False)
    baseline_seconds = time.monotonic()-started
    available = lambda evaluation: bool(evaluation.outputs) and all(r is not None for r in evaluation.outputs)
    baseline_score = sum(baseline.scores)/len(baseline.scores) if available(baseline) else None
    # GEPA's Pareto search may merge multi-prompt candidates, while using the
    # same full program and held-out validation groups for every candidate.
    # Enforced by wait_for inside each rollout/reflection, not only a stop callback.
    reserve = min(seconds/2, max(min(600,seconds/4),baseline_seconds*2.5))
    search_deadline = final_deadline-reserve
    adapter.deadline = search_deadline
    selector=GroupedComponents(COMPONENTS)
    sampler=ComponentBatchSampler(selector,subset_reference(reference,split['train']))
    result = None
    if baseline_score is not None and time.monotonic()<search_deadline:
        result = gepa.optimize(seed_candidate=seed, trainset=split['train'],valset=split['validation'],
        adapter=adapter, max_metric_calls=calls,skip_perfect_score=False,
        module_selector=selector,seed=5724,run_dir=str(output/'gepa'),
        logger=FamilyLogger(output/'gepa/run_log.txt'),candidate_selection_strategy='pareto',
        batch_sampler=sampler,use_merge=True,max_merge_invocations=8,cache_evaluation=True,
        stop_callbacks=lambda state:time.monotonic()>=search_deadline,raise_on_exception=True)
    candidates = result.candidates if result else [original]
    scores = result.val_aggregate_scores if result else [baseline_score]
    best = max(range(len(candidates)),key=lambda i:scores[i] if scores[i] is not None else -1)
    adapter.deadline = final_deadline
    adapter.cache_enabled = False  # Confirmation must run fresh inference.
    control = adapter.evaluate(split['validation'],original,False)
    confirmation = control if candidates[best]==original else adapter.evaluate(split['validation'],candidates[best],False)
    complete = available(baseline) and available(control) and available(confirmation)
    counts = lambda rows,key:sum(r['clinical'][key] for r in rows if r)
    safe = (complete and counts(confirmation.outputs,'matched')>=max(counts(baseline.outputs,'matched'),counts(control.outputs,'matched'))
        and counts(confirmation.outputs,'unscorable')<=counts(control.outputs,'unscorable')
        and counts(confirmation.outputs,'forbidden_violations')<=counts(control.outputs,'forbidden_violations')
        and counts(confirmation.outputs,'forbidden_unscorable')<=counts(control.outputs,'forbidden_unscorable'))
    confirmed_score=sum(confirmation.scores)/len(confirmation.scores) if available(confirmation) else None
    control_score=sum(control.scores)/len(control.scores) if available(control) else None
    selected = candidates[best] if safe and confirmed_score>max(baseline_score,control_score) else original
    report = {'status':'completed' if complete else 'partial','metric_calls':result.total_metric_calls if result else 0,'baseline_validation_score':baseline_score,
        'candidate_validation_scores':scores,'best_index':best,'original_is_frontier_seed':True,
        'independent_warm_start_used':False,
        'selected_original':selected==original,'selected_validation_score':confirmed_score if selected!=original else control_score,
        'confirmation_score':confirmed_score,'confirmation_complete':complete,'clinical_nonregression_gate_passed':safe,
        'fresh_control_score':control_score,'fresh_control_complete':available(control),
        'baseline_validation_facts':counts(baseline.outputs,'matched') if available(baseline) else None,
        'confirmation_validation_facts':counts(confirmation.outputs,'matched') if available(confirmation) else None,
        'baseline_forbidden_unscorable':counts(baseline.outputs,'forbidden_unscorable') if available(baseline) else None,
        'confirmation_forbidden_unscorable':counts(confirmation.outputs,'forbidden_unscorable') if available(confirmation) else None,
        'candidate_count':len(candidates),'wall_budget_exhausted':time.monotonic()>=adapter.deadline,
        'confirmation_reserve_seconds':reserve,'baseline_wall_seconds':baseline_seconds,
        'cache_hits':adapter.cache_hits,'rollout_timeouts':adapter.timeouts,'training_batches':sampler.receipts,
        'search_started':result is not None,
        'components':COMPONENTS,'component_changes':{c:sum(x[c]!=seed[c] for x in candidates) for c in COMPONENTS},
        'prompt_groups':selector.groups,'component_proposals':dict(selector.proposed),'component_invocations':dict(adapter.invoked),
        'test_labels_exposed_to_optimizer':False,'full_program_regenerated':True,'frozen_intermediates':False,
        'deployed_repairs':config.get('max_repair_rounds',2),'objective':'finite_source_checked_patient_bundle',
        'automatic_production_promotion':False}
    write_json(output/'candidates.json',candidates);write_json(output/'best-prompts.json',selected)
    write_json(output/'report.json',report);write_json(output/'splits.json',split)
    return selected,report
