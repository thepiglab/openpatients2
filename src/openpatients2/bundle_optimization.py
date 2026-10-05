"""GEPA evolution of the actual repaired, multi-prompt patient program."""
import asyncio
from collections import Counter
import json
from pathlib import Path
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


class BundleAdapter(ClinicalAdapter):
    def __init__(self, articles, sample, config, endpoints, context, output, reference, deadline):
        super().__init__([{'article': a} for a in articles], config, endpoints, context,
                         output, reference, 'whole_bundle', deadline, {'deployment_matched': True})
        self.articles = {a['article_id']: a for a in articles}
        self.sample = sample
        self.invoked=Counter()

    def evaluate(self, batch, candidate, capture_traces=False):
        from gepa.core.adapter import EvaluationBatch
        if not batch: return EvaluationBatch(outputs=[], scores=[], trajectories=[] if capture_traces else None)
        if set(batch) - set(self.articles): raise ValueError('Held-out or unknown article reached bundle optimizer')
        for text in candidate.values(): prompt_guard(text, list(self.articles.values()))
        if time.monotonic() >= self.deadline:
            return EvaluationBatch(outputs=[None]*len(batch), scores=[0.]*len(batch),
                trajectories=[{'Feedback': 'Wall budget exhausted; no medical inference'}]*len(batch) if capture_traces else None)
        async def run():
            destination = self.next_directory('rollout')
            cfg = {**self.config, 'prompt_overrides': candidate, 'prompt_mode': 'rewrite',
                'max_articles': len(batch), 'article_ids': list(batch), 'scatter_calls': True, 'seed': 42}
            try:
                measured = await asyncio.wait_for(run_pilot(cfg, self.sample, destination, self.endpoints,
                    self.context, 'targeted', input_scope='patient_sections', seed=42,
                    image_manifest=cfg.get('image_manifest')), timeout=max(1, self.deadline-time.monotonic()))
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
                write_json(destination/'TIMEOUT.json', {'status':'budget_exhausted', 'articles':batch})
                return [None]*len(batch), [{'Feedback':'Budget timeout, not a medical error'}]*len(batch)
        results, traces = asyncio.run(run())
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
                'Do not invent clinical content, dates, or performed care. Return JSON '
                '{"instructions": {"component_name": "rewritten strategy", ...}} for every requested component.'},
                {'role':'user','content':'Rewrite the transferable strategies for '+json.dumps(keys)+'. '
                    'Evaluate their interaction in the entire patient bundle. Keep each strategy concise, under 1500 characters. '
                    'Diagnostics contain the actual original/mutated task instructions for invoked components. '
                    'Other current strategies: '+json.dumps({k:v for k,v in candidate.items() if v})+
                    '\nCurrent selected strategies: '+json.dumps({k:candidate[k] for k in keys})+
                    '\nDiagnostics: '+json.dumps(next(iter(reflective_dataset.values()),[]),ensure_ascii=False)+
                    '\nDo not memorize article identifiers, patients, or source answer values. Empty preserves the original strategy.'}]
            def checked(value):
                texts=value['instructions']
                if not isinstance(texts,dict) or set(texts)!=set(keys):raise ValueError('Rewrite exactly the requested prompt group')
                return {k:prompt_guard(texts[k],list(self.articles.values())) for k in keys}
            try:
                row = await asyncio.wait_for(runner.call('summary', messages,
                    checked, {'article_id':next(iter(self.articles)), 'optimization_components':keys}, 0, []),
                    timeout=max(1,self.deadline-time.monotonic()))
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
    adapter = BundleAdapter(scoped,sample,config,endpoints,context,output,
        subset_reference(reference,allowed),time.monotonic()+seconds)
    seed = {c:independent.get(c,'') for c in COMPONENTS}
    original = dict.fromkeys(COMPONENTS,'')
    baseline = adapter.evaluate(split['validation'],original,False)
    baseline_score = sum(baseline.scores)/len(baseline.scores)
    # GEPA's Pareto search may merge multi-prompt candidates, while using the
    # same full program and held-out validation groups for every candidate.
    search_deadline = adapter.deadline - min(600, seconds/4)
    selector=GroupedComponents(COMPONENTS)
    result = gepa.optimize(seed_candidate=seed, trainset=split['train'],valset=split['validation'],
        adapter=adapter, max_metric_calls=calls,reflection_minibatch_size=3,skip_perfect_score=False,
        module_selector=selector,seed=5724,run_dir=str(output/'gepa'),
        logger=FamilyLogger(output/'gepa/run_log.txt'),candidate_selection_strategy='pareto',
        batch_sampler='epoch_shuffled',use_merge=True,max_merge_invocations=8,
        stop_callbacks=lambda state:time.monotonic()>=search_deadline,raise_on_exception=True)
    best = max(range(len(result.candidates)),key=lambda i:result.val_aggregate_scores[i])
    confirmation = adapter.evaluate(split['validation'],result.candidates[best],False)
    complete = all(r is not None for r in [*baseline.outputs,*confirmation.outputs])
    counts = lambda rows,key:sum(r['clinical'][key] for r in rows if r)
    safe = (complete and counts(confirmation.outputs,'matched')>=counts(baseline.outputs,'matched')
        and counts(confirmation.outputs,'forbidden_violations')<=counts(baseline.outputs,'forbidden_violations')
        and counts(confirmation.outputs,'forbidden_unscorable')<=counts(baseline.outputs,'forbidden_unscorable'))
    confirmed_score=sum(confirmation.scores)/len(confirmation.scores)
    selected = result.candidates[best] if safe and confirmed_score>baseline_score else original
    report = {'status':'completed','metric_calls':result.total_metric_calls,'baseline_validation_score':baseline_score,
        'candidate_validation_scores':result.val_aggregate_scores,'best_index':best,
        'selected_original':selected==original,'selected_validation_score':confirmed_score if selected!=original else baseline_score,
        'confirmation_score':confirmed_score,'confirmation_complete':complete,'clinical_nonregression_gate_passed':safe,
        'baseline_validation_facts':counts(baseline.outputs,'matched'),
        'confirmation_validation_facts':counts(confirmation.outputs,'matched'),
        'baseline_forbidden_unscorable':counts(baseline.outputs,'forbidden_unscorable'),
        'confirmation_forbidden_unscorable':counts(confirmation.outputs,'forbidden_unscorable'),
        'candidate_count':len(result.candidates),'wall_budget_exhausted':time.monotonic()>=adapter.deadline,
        'components':COMPONENTS,'component_changes':{c:sum(x[c]!=seed[c] for x in result.candidates) for c in COMPONENTS},
        'prompt_groups':selector.groups,'component_proposals':dict(selector.proposed),'component_invocations':dict(adapter.invoked),
        'test_labels_exposed_to_optimizer':False,'full_program_regenerated':True,'frozen_intermediates':False,
        'deployed_repairs':config.get('max_repair_rounds',2),'objective':'finite_source_checked_patient_bundle',
        'automatic_production_promotion':False}
    write_json(output/'candidates.json',result.candidates);write_json(output/'best-prompts.json',selected)
    write_json(output/'report.json',report);write_json(output/'splits.json',split)
    return selected,report
