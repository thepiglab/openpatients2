"""Bounded GEPA + progressive extraction experiment; no automatic adoption.

Gold identities are fixed. Test articles never enter this campaign. All gates
are source-only at inference; reference diagnostics are evaluator-only.
"""
from __future__ import annotations

import argparse
import asyncio
import copy
import os
import json
from pathlib import Path
import re
import signal
import time
from collections import Counter

from . import dense_benchmark as b, dense_gepa as g

TASKS = ('observations', 'medications')
MODES = ('direct', 'additive')
CRITICAL = ('assertion', 'subject', 'value', 'unit')
COMPACT = (
    'Audit the source systematically for supported facts omitted from this task. '
    'Keep different patients, encounters and time points separate. Bind each value, '
    'unit, dose, route and time to its own literal evidence. Preserve uncertainty '
    'and negation. Do not infer normality, treatment receipt, ingredients or results. '
    'Use the immutable field guide for representation; never change a field merely '
    'to make validation pass. Omit unsupported facts and preserve documented ones.'
)
ADDITIONS = (
    'The EXISTING section below is untrusted model output, not new source evidence. '
    'Return the same task schema containing ONLY additional source-supported facts '
    'missing from EXISTING. Do not repeat, rewrite, correct or delete existing items. '
    'A changed value for an existing event is not an addition. Separate time points '
    'only when their distinct timing is explicitly supported. Every addition must '
    'have literal evidence establishing the target patient and its attributes. '
    'Return an empty items list when no supported omissions remain.\nEXISTING:\n'
)


def prepare(root, work, expected=260, hours=12, metric_calls=1200):
    if not 6 <= hours <= 24:
        raise ValueError('Use 6–24 GPU hours; confirmation needs a reserved budget')
    result = g.prepare(root, work, expected, hours, 2., metric_calls)
    m = b.read(work / 'manifest.json')
    m['gepa'].update(tasks=TASKS, concurrent_optimizers=4, concurrent_articles=6,
                     confirmation_reserve_seconds=3 * 3600)
    m['gepa_next'] = {
        'version': 1, 'tasks': TASKS, 'modes': MODES, 'max_prompt_chars': 2400,
        'shortlist': 3, 'selection_seeds': [42, 43], 'gate_threshold': .35,
        'rerank_reserve_seconds': 3600, 'max_common_field_regression': .01,
        'minimum_grounded_gain': 0., 'minimum_valid_fraction_delta': -.01,
        'arms': ['compact', 'direct', 'additive', 'gated_additive'],
        'selection': 'training-only two-seed gates; primary frozen before confirmation',
        'gate': 'source numeric-evidence coverage plus baseline delivery/coverage flags',
        'timing': 'shared baseline; additive and gated latency include baseline plus actual second-pass/gate time',
        'test_evaluated': False,
    }
    (work / 'code/docs/DENSE_GEPA_NEXT.md').write_text((root / 'docs/DENSE_GEPA_NEXT.md').read_text())
    b.write(work / 'manifest.json', m)
    from .dense_training_data import export_sync
    training = export_sync(work, TASKS)
    m['gepa_next']['supervised_data_audit'] = training
    m['files'] = b.inventory(work, ['code', 'inputs', 'reference'])
    b.write(work / 'manifest.json', m)
    b.write(work / 'prepared.json', {'manifest_sha256': b.sha(work / 'manifest.json')})
    return result | {'campaign': 'dense-gepa-next', 'hours': hours, 'modes': MODES}


def diagnostics(reference, predictions, task, beta=2.):
    """Stable reference-item keys separate old-fact regressions from new coverage."""
    from .scoring.clinical import items_of, match_items, compare_attributes
    aligned = {}
    for patient in reference['patients']:
        pid = patient['patient_id']
        rr = items_of({'sections': {task: patient['sections'].get(task, {})}})
        pp = items_of({'sections': {task: predictions.get(pid) or {}}})
        for i, j in match_items(rr, pp):
            if rr[i].review_only:
                continue
            aligned[f'{pid}:{i}'] = {f'{group}:{field}': bool(ok)
                for group, values in compare_attributes(rr[i], pp[j]).items()
                for field, ok in values}
    score = g.measure(reference, predictions, task, beta, [])
    wrong = sum(any(not ok for key, ok in fields.items() if key.split(':')[0] in CRITICAL)
                for fields in aligned.values())
    c = score['counts']
    # A matched item with a critical attribute error is both an unusable prediction
    # and a missed correct fact. The ordinary item metric remains reported too.
    grounded = {'tp': c['tp'] - wrong, 'fp': c['fp'] + wrong, 'fn': c['fn'] + wrong}
    return {'aligned': aligned, 'grounded_counts': grounded,
            'grounded_fbeta': g.fbeta(**grounded, beta=beta)}


def paired_diagnostics(control, candidate):
    old, new = control['aligned'], candidate['aligned']
    common = old.keys() & new.keys()
    fields = {}
    for group in CRITICAL:
        correct = [(key, field) for key in common for field, ok in old[key].items()
                   if ok and field.split(':')[0] == group]
        regressions = sum(not new[key].get(field, False) for key, field in correct)
        recovered = [ok for key in new.keys() - old.keys() for field, ok in new[key].items()
                     if field.split(':')[0] == group]
        fields[group] = {'previously_correct': len(correct), 'regressed': regressions,
                         'new_correct': sum(recovered), 'new_total': len(recovered)}
    return {'lost_matches': len(old.keys() - new.keys()),
            'new_matches': len(new.keys() - old.keys()), 'common_matches': len(common),
            'fields': fields}


def reward(row, control, plan):
    """Dense search signal; hard corpus-level gates are still applied in reranking."""
    precision = lambda r: r['precision'] if r['precision'] is not None else (0. if r['counts']['fn'] else 1.)
    deficit = max(0., max(plan['precision_floor'], precision(control) - plan['max_precision_drop']) - precision(row))
    for group in CRITICAL:
        c, r = control['critical_attributes'][group], row['critical_attributes'][group]
        if c['total']:
            accuracy = r['correct'] / r['total'] if r['total'] else 0.
            deficit += max(0., c['correct'] / c['total'] - plan['max_attribute_drop'] - accuracy)
    ca, ra = control['attributes'], row['attributes']
    if ca['total']:
        deficit += max(0., ca['correct'] / ca['total'] - plan['max_attribute_drop'] -
                       (ra['correct'] / ra['total'] if ra['total'] else 0.))
    regression = paired_diagnostics(control, row)
    bad = sum(x['regressed'] for x in regression['fields'].values())
    total = sum(x['previously_correct'] for x in regression['fields'].values())
    valid = row['statuses'].get('valid', 0) / max(1, sum(row['statuses'].values())) if row['statuses'] else 1.
    return max(-2., .5 * (row['fbeta'] + row['grounded_fbeta']) - 2 * deficit -
               bad / max(1, total) - (1 - valid))


def source_gate(packet, section, status):
    """A cheap omission signal, not a claim of completeness or a gold-aware gate."""
    if status != 'valid' or not section or section.get('coverage') != 'complete':
        return 1.
    numbers = set(re.findall(r'\b\d+(?:\.\d+)?\b', packet['text']))
    evidence = ' '.join(str(e.get('quote', '')) for item in section.get('items', [])
                        for e in item.get('evidence', []))
    covered = set(re.findall(r'\b\d+(?:\.\d+)?\b', evidence))
    return len(numbers - covered) / len(numbers) if numbers else 0.


def merge_additions(base, additions):
    """Never edit baseline facts. Suppress same-name/same-time overlapping facts."""
    from .scoring.text import norm, evidence_spans, span_overlap
    out = copy.deepcopy(base)
    accepted, rejected = [], []
    for item in additions.get('items', []):
        duplicate = any(
            norm(item.get('name')) == norm(old.get('name')) and
            item.get('time') == old.get('time') and item.get('subject') == old.get('subject') and
            (item == old or span_overlap(evidence_spans(item), evidence_spans(old)) >= .5)
            for old in out.get('items', []))
        if duplicate:
            rejected.append(item)
        else:
            out.setdefault('items', []).append(copy.deepcopy(item)); accepted.append(item)
    if accepted:
        out['documentation_status'] = additions.get('documentation_status', out.get('documentation_status'))
        # An additive pass cannot certify exhaustive coverage.
        out['coverage'] = 'limited'
    return out, {'accepted': accepted, 'duplicate_or_conflict': rejected}


class MetricAdapter(g.Adapter):
    def __init__(self, *args, name='control', **kwargs):
        super().__init__(*args, **kwargs)
        self.result_root = self.work / 'results/gepa-next' / self.task / name
        self.max_prompt_chars = b.read(self.work / 'manifest.json')['gepa_next']['max_prompt_chars']
        self.reflection_constraints = (
            f'At most {self.max_prompt_chars} characters. Write general rules, not examples. '
            'Do not include any digits, case-specific facts, disease/drug examples or e.g. clauses. '
            'Critical fields and baseline precision are constraints, not a trade for recall. '
            'Respect the field guide over patterns in individual annotations.'
        )
        self.inflight = {}

    async def one(self, aid, candidate):
        # Deduplicate baseline work shared by both engines. Phase is part of the
        # key; confirmation can never reuse search evaluations.
        key = (self.phase, self.seed, aid, json.dumps(candidate, sort_keys=True))
        if key not in self.inflight:
            self.inflight[key] = asyncio.create_task(super().one(aid, candidate))
        try:
            return await asyncio.shield(self.inflight[key])
        except BaseException:
            if self.inflight[key].done():
                self.inflight.pop(key, None)
            raise

    async def close(self):
        tasks = list(self.inflight.values())
        for task in tasks:
            if not task.done(): task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    def measure_predictions(self, aid, predictions, statuses):
        return super().measure_predictions(aid, predictions, statuses) | {
            'predictions': predictions, **diagnostics(self.refs[aid], predictions, self.task, self.plan['beta'])}

    def make_reflective_dataset(self, candidate, eval_batch, components_to_update):
        result = super().make_reflective_dataset(candidate, eval_batch, components_to_update)
        for record, row in zip(result['instructions'], eval_batch.trajectories):
            record['Quality constraints'] = {
                'critical_attributes': row['critical_attributes'],
                'attribute_aware_f2': row['grounded_fbeta'],
                'reward': row['reward'],
                'baseline_comparison': row.get('paired_quality'),
                'objective': 'Improve F2 and attribute-aware F2 together; preserve baseline precision and correct fields.',
            }
        return result

    async def reflect(self, candidate, dataset):
        proposed = await asyncio.wait_for(super().reflect(candidate, dataset),
            timeout=max(1., self.deadline - time.monotonic()))
        if re.search(r'\d|\be\.g\.|\bfor example\b', proposed['instructions'], re.I):
            return dict(candidate)
        return proposed

    async def settle(self):
        # Shielded shared calls must stop before seeds/deadlines change at a
        # phase boundary. Keep completed successful evaluations for caching.
        pending = [t for t in self.inflight.values() if not t.done()]
        for task in pending: task.cancel()
        await asyncio.gather(*pending, return_exceptions=True)
        self.inflight = {k: t for k, t in self.inflight.items()
                         if not t.cancelled() and t.exception() is None}


class SearchAdapter(MetricAdapter):
    def __init__(self, *args, baseline, mode, **kwargs):
        super().__init__(*args, **kwargs)
        self.baseline, self.mode = baseline, mode
        self.controls = {}
        self.gates = {}

    async def one(self, aid, candidate):
        # Only one seed/phase is active per adapter. Baseline seed/phase is set by
        # the stage orchestrator before concurrent engines start.
        control = await self.baseline.one(aid, g.SEED)
        self.controls[(aid, self.seed)] = control
        if self.mode == 'gated':
            packets = b.read(self.work / 'inputs' / aid / 'gepa-packets.json')
            status = 'valid' if not any(k != 'valid' and v for k, v in control['statuses'].items()) else 'partial'
            scores = [source_gate(r['packet'], control['predictions'].get(r['packet']['patient_target']['patient_id']), status)
                      for r in packets]
            threshold = b.read(self.work / 'manifest.json')['gepa_next']['gate_threshold']
            self.gates[(aid, self.seed)] = any(v >= threshold for v in scores)
        return await super().one(aid, candidate)

    def measure_predictions(self, aid, predictions, statuses):
        row = super().measure_predictions(aid, predictions, statuses)
        control = self.controls[(aid, self.seed)]
        row['reward'] = reward(row, control, self.plan)
        row['paired_quality'] = paired_diagnostics(control, row)
        if self.mode == 'gated': row['gate_triggered'] = self.gates[(aid, self.seed)]
        return row

    async def extract_patient(self, runner, aid, packet, segments, candidate):
        if self.mode == 'direct':
            return await super().extract_patient(runner, aid, packet, segments, candidate)
        control = self.controls[(aid, self.seed)]
        pid = packet['patient_target']['patient_id']
        base = control['predictions'].get(pid)
        if self.mode == 'gated' and not self.gates[(aid, self.seed)]:
            return {'data': copy.deepcopy(base), 'status': 'valid', 'errors': [],
                    'attempts': [{'metrics': {'http_status': 200}, 'errors': [], 'mode': 'gate_skip'}]}
        messages = runner.clinical_messages(self.task, packet)
        messages.append({'role': 'user', 'content': ADDITIONS + json.dumps(base, ensure_ascii=False)})
        result = await runner.call(self.task, messages, runner.clinical_checker(self.task, packet, segments),
            {'article_id': aid, 'patient_id': pid, 'record_id': packet['record_id'], 'pass': 'additions'},
            0, segments, packet=packet)
        # Transport is checked by the parent; never disguise operational failures.
        if base is not None and result['data'] is not None:
            merged, audit = merge_additions(base, result['data'])
            try:
                result['data'] = runner.clinical_checker(self.task, packet, segments)(merged)
            except (ValueError, TypeError, KeyError) as exc:
                result['data'] = copy.deepcopy(base)
                result['status'] = 'partial'
                result['errors'] = [*result['errors'], 'additive_merge_rejected:' + str(exc)[:1000]]
            b.write(runner.output / f'additive-{pid}.json', {'merge': audit, 'result': result['data']})
        elif base is not None:
            result['data'] = copy.deepcopy(base)  # preserve delivery failure status
        return result


def summary(rows, beta):
    s = g.summarize(rows, beta)
    counts = Counter()
    for row in rows: counts.update(row['grounded_counts'])
    s['grounded_fbeta_micro'] = g.fbeta(**counts, beta=beta)
    s['grounded_fbeta_article_macro'] = sum(r['grounded_fbeta'] for r in rows) / len(rows)
    statuses = Counter()
    for r in rows: statuses.update(r['statuses'])
    s['statuses'] = dict(statuses)
    s['valid_fraction'] = statuses['valid'] / sum(statuses.values()) if statuses else 1.
    s['critical_support'] = {k: sum(r['critical_attributes'][k]['total'] for r in rows) for k in CRITICAL}
    return s


def checks(control_rows, rows, plan, extra):
    pairs = {x['article_id']: x for x in control_rows}
    if not rows or len(control_rows) != len(pairs) or len(rows) != len(pairs) or {x['article_id'] for x in rows} != set(pairs):
        raise ValueError('Paired comparison coverage mismatch')
    c, r = summary(control_rows, plan['beta']), summary(rows, plan['beta'])
    paired = [paired_diagnostics(pairs[x['article_id']], x) for x in rows]
    result = {
        'macro_f2': r['fbeta_article_macro'] >= c['fbeta_article_macro'] + plan['minimum_fbeta_gain'],
        'micro_f2': r['fbeta_micro'] >= c['fbeta_micro'],
        'precision': r['precision'] >= max(plan['precision_floor'], c['precision'] - plan['max_precision_drop']),
        'attributes': r['attribute_accuracy'] >= c['attribute_accuracy'] - plan['max_attribute_drop'],
        'grounded_f2': r['grounded_fbeta_micro'] >= c['grounded_fbeta_micro'] + extra['minimum_grounded_gain'],
        'valid_delivery': r['valid_fraction'] >= c['valid_fraction'] + extra['minimum_valid_fraction_delta'],
    }
    for k in CRITICAL:
        result[k] = (not c['critical_support'][k] or r['critical_attributes'][k] >=
                     c['critical_attributes'][k] - plan['max_attribute_drop'])
        denominator = sum(x['fields'][k]['previously_correct'] for x in paired)
        numerator = sum(x['fields'][k]['regressed'] for x in paired)
        result['preserved_' + k] = numerator / max(1, denominator) <= extra['max_common_field_regression']
    return {'passed': all(result.values()), 'checks': result,
            'failed_checks': [k for k, ok in result.items() if not ok],
            'common_fact_diagnostics': paired}


async def evolve(adapter, work, deadline):
    import gepa
    from gepa.utils import TimeoutStopCondition
    from .gepa_feedback import FamilyLogger
    out = adapter.result_root
    out.mkdir(parents=True, exist_ok=True)
    adapter.deadline = deadline
    def run():
        return gepa.optimize(seed_candidate={'instructions': COMPACT},
            trainset=adapter.plan['partition']['train'], valset=adapter.plan['partition']['search_validation'],
            adapter=adapter, candidate_selection_strategy='pareto', reflection_minibatch_size=3,
            max_metric_calls=adapter.plan['max_metric_calls'], cache_evaluation=True,
            stop_callbacks=[TimeoutStopCondition(max(1, deadline - time.monotonic() - 900))],
            seed=42, run_dir=str(out / 'optimizer'), logger=FamilyLogger(out / 'optimizer.log'))
    try:
        result = await asyncio.to_thread(run)
        candidates = result.candidates
        scores = result.val_aggregate_scores
        receipt = {'metric_calls': result.total_metric_calls, 'stop': 'optimizer_returned'}
    except TimeoutError:
        # Boundary inside a rollout, not just between GEPA iterations. Keep only
        # fully evaluated inner candidates, then use reserved time for selection.
        groups = {}
        inner = set(adapter.plan['partition']['search_validation'])
        for row in adapter.cache.values():
            if row['article_id'] in inner:
                groups.setdefault(json.dumps(row['candidate'], sort_keys=True), {})[row['article_id']] = row
        complete = [(json.loads(k), list(v.values())) for k, v in groups.items() if set(v) == inner]
        candidates = [c for c, _ in complete]
        scores = [sum(r['reward'] for r in rows) / len(rows) for _, rows in complete]
        receipt = {'stop': 'search_deadline', 'metric_calls': adapter.sequence}
    b.write(out / 'search.json', receipt | {'candidates': candidates, 'scores': scores})
    ranked = sorted(range(len(candidates)), key=lambda i: (-scores[i], len(candidates[i]['instructions'])))
    unique = []
    for i in ranked:
        if candidates[i] not in unique: unique.append(candidates[i])
    return unique[:b.read(work / 'manifest.json')['gepa_next']['shortlist']] or [{'instructions': COMPACT}]



async def workload(work, endpoint, logs):
    manifest = b.read(work / 'manifest.json'); plan, extra = manifest['gepa'], manifest['gepa_next']
    # Include actual server startup in the allocation cap, then reserve shutdown.
    receipt_start = time.monotonic()
    seconds = plan['hours'] * 3600 - 1200
    final_deadline = receipt_start + seconds
    if os.environ.get('SLURM_JOB_END_TIME', '').isdigit():
        final_deadline = min(final_deadline, time.monotonic() + int(os.environ['SLURM_JOB_END_TIME']) - time.time() - 180)
    search_deadline = final_deadline - plan['confirmation_reserve_seconds'] - extra['rerank_reserve_seconds']
    slots = asyncio.Semaphore(64)
    controls, adapters = {}, {}
    for task in TASKS:
        args = (work, task, endpoint, asyncio.get_running_loop(), search_deadline)
        controls[task] = MetricAdapter(*args)
        controls[task].patient_slots = slots
        for mode in MODES:
            a = SearchAdapter(*args, baseline=controls[task], mode=mode, name=mode)
            a.patient_slots = slots; adapters[(task, mode)] = a
    gated_adapters = {}
    for task in TASKS:
        a = SearchAdapter(work, task, endpoint, asyncio.get_running_loop(), search_deadline,
                          baseline=controls[task], mode='gated', name='gated_additive')
        a.patient_slots = slots; gated_adapters[task] = a
    all_adapters = [*controls.values(), *adapters.values(), *gated_adapters.values()]
    try:
        b.write(logs / 'progress.json', {'phase': 'search', 'engines': 4})
        choices = await g.gather_cancel(evolve(a, work, search_deadline) for a in adapters.values())
        await asyncio.gather(*(a.settle() for a in all_adapters))
        shortlists = dict(zip(adapters, choices))
        # Inner reranking reuses seed-42 evaluations and adds fresh seed-43 runs.
        # No confirmation data is read by selection.
        selected = {}
        for a in all_adapters: a.deadline = final_deadline - plan['confirmation_reserve_seconds']
        b.write(logs / 'progress.json', {'phase': 'training_rerank'})
        async def select(task):
            out = work / 'results/gepa-next' / task
            rows_by_seed = {}; candidates = [('compact', 'direct', {'instructions': COMPACT})]
            for mode in MODES:
                candidates += [(f'{mode}-{i}', mode, c) for i, c in enumerate(shortlists[(task, mode)])]
            for seed in extra['selection_seeds']:
                controls[task].seed = seed
                control = await controls[task].batch(plan['partition']['search_validation'], g.SEED)
                rows_by_seed[seed] = {'control': control}
                # One stage/seed per adapter avoids cache races; both tasks run concurrently.
                for name, mode, candidate in candidates:
                    a = adapters[(task, mode)]; a.seed = seed
                    rows = await a.batch(plan['partition']['search_validation'], candidate)
                    if mode == 'additive':
                        rows = [dict(r, incremental_seconds=r['seconds'], seconds=r['seconds'] + c['seconds']) for r, c in zip(rows, control)]
                    rows_by_seed[seed][name] = rows
            entries = {}
            for name, mode, candidate in candidates:
                gates = {str(s): checks(rows_by_seed[s]['control'], rows_by_seed[s][name], plan, extra)
                         for s in extra['selection_seeds']}
                score = sum(summary(rows_by_seed[s][name], plan['beta'])['fbeta_article_macro'] for s in extra['selection_seeds']) / 2
                entries[name] = {'candidate': candidate, 'mode': mode, 'gates': gates,
                                 'passed': all(v['passed'] for v in gates.values()), 'score': score}
            selected[task] = {'compact': entries['compact']}
            for mode in MODES:
                pool = [v for n, v in entries.items() if n.startswith(mode + '-')]
                selected[task][mode] = max(pool, key=lambda v: (v['passed'], v['score'], -len(v['candidate']['instructions'])))
            eligible = [(n, v) for n, v in selected[task].items() if v['passed']]
            primary = max(eligible, key=lambda nv: (nv[1]['score'], -len(nv[1]['candidate']['instructions'])))[0] if eligible else 'control'
            b.write(out / 'selection.json', {'primary': primary, 'arms': selected[task], 'all_candidates': entries,
                'selection_basis': 'training-only two-seed gates; no outer confirmation access'})
            b.write(out / 'inner-trials.json', rows_by_seed)
        await g.gather_cancel(select(task) for task in TASKS)
        for a in all_adapters:
            a.phase = 'confirmation'; a.deadline = final_deadline; a.cache.clear()
        b.write(logs / 'progress.json', {'phase': 'confirmation'})
        async def confirm(task):
            out = work / 'results/gepa-next' / task; trials = []
            for seed in plan['confirmation_seeds']:
                controls[task].seed = seed
                control = await controls[task].batch(plan['partition']['confirmation'], g.SEED)
                trials.append({'seed': seed, 'arm': 'control', 'rows': control})
                b.write(out / 'confirmation.json', trials)
                names = ['compact', 'direct', 'additive', 'gated_additive']
                if seed % 2: names.reverse()
                for name in names:
                    spec = selected[task]['additive' if name == 'gated_additive' else name]
                    a = gated_adapters[task] if name == 'gated_additive' else adapters[(task, spec['mode'])]
                    a.seed = seed
                    rows = await a.batch(plan['partition']['confirmation'], spec['candidate'])
                    if spec['mode'] == 'additive':
                        rows = [dict(r, incremental_seconds=r['seconds'], seconds=r['seconds']+c['seconds']) for r, c in zip(rows, control)]
                    trials.append({'seed': seed, 'arm': name, 'rows': rows})
                    b.write(out / 'confirmation.json', trials)
            b.write(out / 'done.json', {'complete': True})
        await g.gather_cancel(confirm(task) for task in TASKS)
    finally:
        await asyncio.gather(*(a.close() for a in all_adapters))


def aggregate(work):
    m = b.check_snapshot(work); plan, extra = m['gepa'], m['gepa_next']
    report = {'complete': True, 'test_evaluated': False, 'scope': plan['scope'], 'tasks': {}}
    lines = ['# Progressive GEPA campaign', '', 'Fixed annotated identities; test excluded. No automatic adoption.',
             'Gated arm runs live; second-pass latency includes its shared baseline cost.', '']
    proposed = {}
    for task in TASKS:
        out = work / 'results/gepa-next' / task
        trials = b.read(out / 'confirmation.json') if (out / 'confirmation.json').exists() else []
        labels = {(s, a) for s in plan['confirmation_seeds'] for a in ['control', *extra['arms']]}
        complete = ((out / 'done.json').exists() and len(trials) == len(labels)
                    and {(t['seed'], t['arm']) for t in trials} == labels)
        expected = set(plan['partition']['confirmation'])
        complete = complete and all(len(t['rows']) == len(expected) and {r['article_id'] for r in t['rows']} == expected for t in trials)
        report['complete'] &= complete
        if not complete:
            report['tasks'][task] = {'complete': False}; lines.append(f'## {task}: INCOMPLETE'); continue
        selection = b.read(out / 'selection.json')
        metrics = {f'{t["seed"]}:{t["arm"]}': summary(t['rows'], plan['beta']) for t in trials}
        by_label = {(t['seed'], t['arm']): t['rows'] for t in trials}
        arms = {}
        for arm in extra['arms']:
            gates = {str(s): checks(by_label[(s, 'control')], by_label[(s, arm)], plan, extra) for s in plan['confirmation_seeds']}
            paired = [{'seed': t['seed'], 'arm': 'control' if t['arm']=='control' else 'optimized', 'rows': t['rows']}
                      for t in trials if t['arm'] in ('control', arm)]
            arms[arm] = {'gates': gates, 'passed': all(v['passed'] for v in gates.values()),
                         'paired_fbeta_delta': g.paired_interval(paired),
                         'gate_trigger_rate': (sum(r.get('gate_triggered', False) for t in trials if t['arm']==arm for r in t['rows']) /
                                               (2 * len(expected))) if arm == 'gated_additive' else None}
        primary = selection['primary']
        decision = 'full_pipeline_confirmation' if primary != 'control' and arms[primary]['passed'] else 'retain_control'
        report['tasks'][task] = {'complete': True, 'primary': primary, 'recommendation': decision, 'metrics': metrics, 'arms': arms}
        if decision == 'full_pipeline_confirmation': proposed[task] = selection['arms'][primary]
        lines += [f'## {task}: {decision} (preselected: {primary})', '',
                  '| Arm | Seed | Article F2 | Grounded micro F2 | Precision | Recall | Mean / median / p95 seconds | Failed gates |',
                  '|---|---:|---:|---:|---:|---:|---|---|']
        for t in trials:
            s, arm = t['seed'], t['arm']; v = metrics[f'{s}:{arm}']; tm = v['seconds']
            failures = ', '.join(arms[arm]['gates'][str(s)]['failed_checks']) if arm != 'control' else '—'
            lines.append(f'| {arm} | {s} | {v["fbeta_article_macro"]:.4f} | {v["grounded_fbeta_micro"]:.4f} | {v["precision"]:.4f} | {v["recall"]:.4f} | {tm["mean"]:.1f} / {tm["median"]:.1f} / {tm["p95"]:.1f} | {failures or "none"} |')
    workers = [b.read(p) for p in (work / 'results/workers').glob('*/job-*/receipt.json')]
    report['summed_worker_seconds'] = sum(w['worker_seconds'] for w in workers)
    lines += ['', f'Complete: {report["complete"]}. Worker hours: {report["summed_worker_seconds"]/3600:.2f}.',
              'Times are article-task latency under concurrent load, not full-article pipeline throughput.',
              'Non-primary arms are exploratory. Full live-roster confirmation is required before adoption.']
    b.write(work / 'summary/comparison.json', report)
    b.write(work / 'summary/proposed-strategies.json', {'strategies': proposed, 'requires_full_pipeline_confirmation': True})
    (work / 'summary/comparison.md').write_text('\n'.join(lines)+'\n')
    b.write(work / 'summary/integrity.json', {'manifest_sha256': b.sha(work / 'manifest.json'),
            'files': b.inventory(work, ['results', 'summary', 'submissions', 'logs'])})
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    for name in ('prepare', 'submit', 'setup', 'worker', 'aggregate', 'status', 'verify', 'export'):
        p = sub.add_parser(name); p.add_argument('--work', type=Path, required=True)
        if name == 'prepare':
            p.add_argument('--root', type=Path, default=Path.cwd()); p.add_argument('--hours', type=int, default=12)
            p.add_argument('--expected', type=int, default=260); p.add_argument('--metric-calls', type=int, default=1200)
        if name == 'submit':
            p.add_argument('--account', default='cai5724'); p.add_argument('--qos', default='cai5724'); p.add_argument('--dry-run', action='store_true')
        if name == 'worker': p.add_argument('--shard', type=int, default=0)
    a = parser.parse_args(argv); work = a.work.resolve()
    if a.command == 'prepare': result = prepare(a.root.resolve(), work, a.expected, a.hours, a.metric_calls)
    elif a.command == 'submit': result = b.submit(work, a.account, a.qos, b.read(work/'manifest.json')['gepa']['hours'], dry_run=a.dry_run, script_name='dense_gepa_next.sbatch', worker_shards=range(1))
    elif a.command == 'setup': result = b.setup(work)
    elif a.command == 'worker':
        if a.shard != 0: raise ValueError('Exactly one GPU worker')
        def terminate(*_): raise KeyboardInterrupt('Slurm termination; partial results preserved')
        signal.signal(signal.SIGTERM, terminate)
        result = asyncio.run(b.worker(work, 0, workload=lambda endpoint, logs: workload(work, endpoint, logs)))
    elif a.command == 'aggregate':
        aggregate(work); b.export(work)
        # The Slurm stdout log is in the integrity inventory. Do not append a
        # JSON report after freezing it, which would make on-cluster verify fail.
        return
    elif a.command == 'verify': result = g.verify(work)
    elif a.command == 'export': result = b.export(work)
    else:
        result = {'work': str(work), 'summary': b.read(work/'summary/comparison.json') if (work/'summary/comparison.json').exists() else None,
                  'progress': [b.read(p) for p in (work/'results/workers').glob('*/job-*/progress.json')]}
    print(json.dumps(result, indent=2))
