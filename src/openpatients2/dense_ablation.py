"""Component-isolated follow-up to the dense overnight comparison. CPU analysis."""
from collections import Counter
import hashlib

from . import dense_benchmark as b

ARM_CONFIGS = {
    'control': {},
    'figures': {'repair_components': ['figures']},
    'series': {'repair_components': ['series']},
    'roster': {'repair_components': ['roster']},
    'summary_json': {'summary_structured_output': True},
}
# Source IDs are diagnostic selection, never extraction prompts or reference rosters.
DIAGNOSTICS = ('PMC12861484.1', 'PMC12480731.1', 'PMC12817846.1')
TARGETS = {'figures': 'panel_detection_f1', 'series': 'series_points_f1',
           'roster': 'roster_recall', 'summary_json': 'summary_claim_f1'}
GATES = {'minimum_target_gain': .005, 'maximum_clinical_f1_drop': .005,
         'maximum_precision_drop': .01, 'maximum_attribute_drop': .01,
         'maximum_roster_recall_drop': .01,
         'decision': 'promising component only; no automatic combined pipeline promotion'}


def select_articles(metadata):
    train = {r['article_id'] for r in metadata if r['split'] == 'train'}
    chosen = [aid for aid in DIAGNOSTICS if aid in train]
    ranked = sorted(train - set(chosen), key=lambda aid: hashlib.sha256(('ablation-v1:' + aid).encode()).hexdigest())
    chosen += ranked[:max(0, 24 - len(chosen))]
    return chosen + sorted(r['article_id'] for r in metadata if r['split'] == 'validation')


def decision(control, candidate, target, complete):
    if not complete:
        return {'recommendation': 'inconclusive', 'reason': 'Incomplete planned coverage'}
    thresholds = {target: GATES['minimum_target_gain'],
                  'clinical_f1': -GATES['maximum_clinical_f1_drop'],
                  'clinical_precision': -GATES['maximum_precision_drop'],
                  'attribute_accuracy': -GATES['maximum_attribute_drop']}
    if target != 'roster_recall':
        thresholds['roster_recall'] = -GATES['maximum_roster_recall_drop']
    checks = {key: control[key] is not None and candidate[key] is not None
              and candidate[key] - control[key] >= threshold - 1e-12 for key, threshold in thresholds.items()}
    return {'recommendation': 'promising_component' if all(checks.values()) else 'retain_control_and_review',
            'target': target, 'checks': checks}


def aggregate(work):
    from .dense_overnight import arm_names, score_metrics
    from .unified_scoring import score
    m = b.check_snapshot(work)
    names = arm_names(work)
    reports = {n: b.aggregate(work / 'arms' / n) for n in names}
    task_diagnostics = {}
    for n in names:
        statuses, summary_errors = {}, Counter()
        scored_attempts = {item['article_id']: b.completed_attempt(work / 'arms' / n, item['article_id'])[0]
                           for item in m['articles']}
        for path in (work / 'arms' / n / 'results/articles').glob('*/attempt-*/**/tasks/*.json'):
            # Only the scored completed attempt, not an interrupted older retry.
            aid = path.relative_to(work / 'arms' / n / 'results/articles').parts[0]
            chosen = scored_attempts.get(aid)
            if chosen is None or chosen not in path.parents:
                continue
            row = b.read(path)
            statuses.setdefault(row['task'], Counter())[row['status']] += 1
            if row['task'] == 'summary':
                for error in row.get('errors', []):
                    kind = ('json_parse' if 'parseable object' in error else
                            'provider_http' if error.startswith('http_') else
                            'other_validation_or_budget')
                    summary_errors[kind] += 1
        task_diagnostics[n] = {'task_statuses': {t: dict(c) for t, c in statuses.items()},
                               'terminal_summary_errors': dict(summary_errors)}
    reference = b.rows(work / 'reference/unified.jsonl')
    metrics = {}
    out = work / 'summary'; out.mkdir(exist_ok=True)
    b.write(out / 'task-diagnostics.json', task_diagnostics)
    for split in ('train', 'validation'):
        metrics[split] = {}
        for n in names:
            scores = b.read(work / 'arms' / n / 'summary' / f'scores-{split}.json')
            metrics[split][n] = {**score_metrics(scores), 'summary_claim_f1': scores['overall']['summary_claim_f1']}
    completed = {n: {aid: b.completed_attempt(work / 'arms' / n, aid)[1] for aid in reference} for n in names}
    timing, paired = [], {}
    changes = {}
    control_articles = b.read(work / 'arms/control/summary/scores-per-article.json')
    for n in names:
        if n == 'control':
            continue
        ids = [aid for aid in reference if completed['control'][aid] and completed[n][aid]]
        paired[n] = len(ids)
        refs = {aid: reference[aid] for aid in ids}
        scores = {}
        for arm in ('control', n):
            predictions = b.rows(work / 'arms' / arm / 'summary/predictions.jsonl')
            scores[arm] = score(refs, {aid: row for aid, row in predictions.items() if aid in refs})
        b.write(out / f'completed-pairs-{n}.json', scores)
        article_scores = b.read(work / 'arms' / n / 'summary/scores-per-article.json')
        target = TARGETS[n]
        # Overall per-article roster exposes F1 rather than recall; identify the
        # diagnostic field explicitly instead of mislabelling it as target recall.
        field = 'roster_f1' if n == 'roster' else target
        rows = []
        for aid in reference:
            a, z = control_articles[aid][field], article_scores[aid][field]
            rows.append({'article_id': aid, 'split': reference[aid]['split'], 'field': field,
                         'control': a, 'candidate': z, 'delta': z-a if a is not None and z is not None else None})
        changes[n] = rows
        b.write(out / f'per-article-{n}.json', rows)
    for aid in reference:
        row = {'article_id': aid, 'split': reference[aid]['split']}
        for n in names:
            receipt = completed[n][aid]
            row[n] = {'seconds': receipt['wall_seconds'], 'patients': receipt['patients_extracted']} if receipt else None
        timing.append(row)
    b.write_rows(out / 'article-timing.jsonl', timing)
    complete = all(r['complete'] for r in reports.values())
    decisions = {n: decision(metrics['validation']['control'], metrics['validation'][n], TARGETS[n], complete)
                 for n in names if n != 'control'}
    workers = [b.read(p) for p in (work / 'results/workers').glob('*/job-*/receipt.json')]
    result = {'format': 'component-ablation-result/1', 'complete': complete,
              'completed_pairs': sum(all(completed[n][aid] for n in names) for aid in reference),
              'completed_control_pairs': paired, 'planned_articles_per_arm': len(reference),
              'splits': m['splits'], 'metrics': metrics, 'component_decisions': decisions,
              'promotion': {'recommendation': 'review_components' if complete else 'inconclusive'},
              'article_seconds': {n: reports[n]['article_seconds'] for n in names},
              'operational_error_attempts': {n: reports[n]['operational_error_attempts'] for n in names},
              'summed_worker_seconds': sum(r['worker_seconds'] for r in workers),
              'observed_worker_span_seconds': max(r['ended_unix'] for r in workers)-min(r['started_unix'] for r in workers) if workers else None,
              'worker_states': dict(Counter(r['status'] for r in workers)),
              'test_evaluated': False, 'gepa_run': False}
    b.write(out / 'comparison.json', result)
    lines = ['# Component ablation', '', f'Complete: {complete}. {len(reference)} articles per arm. Test excluded.', '',
             '| Arm | Validation target | Control | Candidate | Decision |', '|---|---|---:|---:|---|']
    fmt = lambda v: 'unavailable' if v is None else f'{100*v:.2f}%'
    for n, d in decisions.items():
        target = TARGETS[n]
        lines.append(f"| {n} | {target} | {fmt(metrics['validation']['control'][target])} | {fmt(metrics['validation'][n][target])} | {d['recommendation']} |")
    lines += ['', 'Each candidate changes one component. Promising components are not a validated combination. '
              'Full planned references remain in the score denominator; completed-pair reports are diagnostic only. '
              'Training diagnostics were selected partly from observed failures. Validation has already been inspected in earlier runs. '
              'These are development agreement scores, not blind clinical accuracy.', '', '## Per-article validation changes', '']
    for n, rows in changes.items():
        comparable = [r for r in rows if r['split'] == 'validation' and r['delta'] is not None]
        up = sum(r['delta'] > 1e-12 for r in comparable); down = sum(r['delta'] < -1e-12 for r in comparable)
        lines.append(f'- {n}: {up} improved, {down} worsened, {len(comparable)-up-down} unchanged; '
                     'see per-article JSON including unavailable scores. Counts are unweighted.')
    lines += ['', '## Article timing (startup and queue excluded)', '']
    for n in names:
        s = result['article_seconds'][n]
        lines.append(f"- {n}: mean {s['mean']}s, median {s['median']}s, p95 {s['p95']}s ({s['n']} completed).")
    lines += ['', '## Summary task outcomes', '']
    for n in names:
        d = task_diagnostics[n]
        lines.append(f"- {n}: {d['task_statuses'].get('summary', {})}; terminal errors {d['terminal_summary_errors']}.")
    lines += ['', 'Shared worker time, startup, telemetry and DFlash counters live at the campaign root. '
              'An unsupported structured-output request remains an explicit summary failure; no silent fallback. '
              'Inspect raw attempts before adopting summary_json. No GEPA or test-set evaluation ran.', '']
    (out / 'comparison.md').write_text('\n'.join(lines))
    files = b.inventory(work, ['arms', 'results', 'summary', 'submissions', 'logs'])
    if (work / 'runtime.json').exists():
        files['runtime.json'] = b.sha(work / 'runtime.json')
    b.write(out / 'integrity.json', {'manifest_sha256': b.sha(work / 'manifest.json'), 'files': files})
    return result
