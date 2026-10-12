"""Eight-hour paired pipeline experiment. No optimizer and no test-set inference."""
from __future__ import annotations

import argparse
import asyncio
from collections import Counter
import copy
import os
from pathlib import Path
import shutil
import signal
import tarfile
import time

from . import dense_benchmark as b

ARMS = ('control', 'repairs')
GATE = {'minimum_f1_gain': 0.005, 'maximum_precision_drop': 0.01,
        'maximum_attribute_accuracy_drop': 0.01, 'maximum_roster_recall_drop': 0.0}


def arm_names(work):
    return tuple(b.read(work / 'manifest.json')['overnight']['arms'])


def prepare(root, work, expected=260, download_media=False, hours=8, plan='paired'):
    if not 1 <= hours <= 24:
        raise ValueError('Overnight hours must be between 1 and 24')
    if plan not in {'paired', 'ablation'}:
        raise ValueError('Unknown experiment plan')
    from .dense_ablation import ARM_CONFIGS, select_articles
    configs = ARM_CONFIGS if plan == 'ablation' else {'control': {}, 'repairs': {'overnight_repairs': True}}
    b.prepare(root, work, expected, download_media, splits=('train', 'validation'), make_archive=False,
              article_selector=select_articles if plan == 'ablation' else None)
    m = b.read(work / 'manifest.json')
    m['git_head'] = os.environ.get('DENSE_SOURCE_REVISION') or m['git_head']
    m['overnight'] = {'format': 'paired-pipeline/1', 'arms': list(configs), 'hours': hours, 'plan': plan,
        'article_timeout_seconds': 900, 'shutdown_reserve_seconds': 600,
        'selection': 'all completed train and validation articles; test excluded before input preparation',
        'order': 'article-ID SHA256 parity counterbalances arm order on the same GPU',
        'scheduling': 'dynamic locked article-pair queue; source-size/image priority; no GPU barriers',
        'promotion_gate': GATE, 'gepa': False,
        'limitations': ['Source overflows remain explicit failures; no source truncation.',
                       'Both arms use current code; only overnight_repairs differs.',
                       'Prior inspection makes this a development comparison, not blind evaluation.',
                       'No automatic test evaluation or prompt optimization.']}
    if plan == 'ablation':
        from .dense_ablation import GATES
        m['overnight'].update(format='component-ablation/1', promotion_gate=GATES,
            selection='24 diagnostic training articles and all validation articles; test excluded',
            order='SHA256 cyclic rotation and reversal of arms within each article',
            arm_configs=configs, limitations=['One component per arm; combinations remain untested.',
                'Diagnostic training selection includes known series regressions; not a blind sample.',
                'Source overflows stay explicit failures; no GEPA or test evaluation.'])
        shutil.copyfile(root / 'docs/DENSE_ABLATION.md', work / 'code/docs/DENSE_ABLATION.md')
    doc = root / 'docs/DENSE_OVERNIGHT.md'
    shutil.copyfile(doc, work / 'code/docs/DENSE_OVERNIGHT.md')
    m['files'] = b.inventory(work, ['code', 'inputs', 'reference'])
    for name in configs:
        arm = work / 'arms' / name
        arm.mkdir(parents=True)
        # Immutable hard links share disk bytes; inference writes only results.
        for folder in ('code', 'inputs', 'reference'):
            shutil.copytree(work / folder, arm / folder, copy_function=os.link)
        am = copy.deepcopy(m)
        am.pop('overnight')
        am['experiment_arm'] = name
        am['extraction'].update(configs[name])
        am['scope']['clinical'] += '; experiment arm=' + name
        am['files'] = b.inventory(arm, ['code', 'inputs', 'reference'])
        b.write(arm / 'manifest.json', am)
        m['files'][f'arms/{name}/manifest.json'] = b.sha(arm / 'manifest.json')
    b.write(work / 'manifest.json', m)
    b.write(work / 'prepared.json', {'manifest_sha256': b.sha(work / 'manifest.json')})
    return {'work': str(work), 'articles_per_arm': m['article_count'], 'splits': m['splits'],
            'arms': list(configs), 'hours_per_gpu': hours, 'test_inference': False}


def status(work):
    m = b.read(work / 'manifest.json')
    arms = {name: b.status(work / 'arms' / name) for name in arm_names(work)}
    compact = {name: {**r, 'pending': len(r['pending']), 'pending_sample': r['pending'][:8]}
               for name, r in arms.items()}
    return {'arms': compact, 'complete': all(not r['pending'] for r in arms.values()),
            'hours_per_gpu': m['overnight']['hours'],
            'archive_available': (work / 'results.tar.gz.sha256').exists(),
            'report': str(work / 'summary/comparison.md') if (work / 'summary/comparison.md').exists() else None}


def promotion(control, repairs, complete):
    if not complete:
        return {'recommendation': 'inconclusive', 'reason': 'Both arms must complete every planned article.'}
    def metric(row, name):
        return row[name]
    checks = {}
    for key, threshold in [('clinical_f1', GATE['minimum_f1_gain']),
                           ('clinical_precision', -GATE['maximum_precision_drop']),
                           ('attribute_accuracy', -GATE['maximum_attribute_accuracy_drop']),
                           ('roster_recall', -GATE['maximum_roster_recall_drop'])]:
        a, z = metric(control, key), metric(repairs, key)
        checks[key] = a is not None and z is not None and z - a >= threshold - 1e-12
    return {'recommendation': 'repairs' if all(checks.values()) else 'retain_control_and_review',
            'checks': checks, 'basis': 'predeclared validation-only gate; no automatic deployment'}


def score_metrics(scores):
    overall = scores['overall']
    # The compact overall roster field is F1 only; recall comes from the detail.
    return {'clinical_precision': overall['clinical_items']['precision'],
            'clinical_recall': overall['clinical_items']['recall'],
            'clinical_f1': overall['clinical_items']['f1'],
            'attribute_accuracy': overall['attribute_accuracy'],
            'roster_recall': scores['roster']['recall'],
            'timeline_relation_f1': overall['timeline_relation_f1'],
            'panel_detection_f1': overall['panel_detection_f1'],
            'series_points_f1': overall['series_points_f1']}


def aggregate(work):
    if b.read(work / 'manifest.json')['overnight'].get('plan') == 'ablation':
        from .dense_ablation import aggregate as ablation_aggregate
        return ablation_aggregate(work)
    from .unified_scoring import score
    m = b.check_snapshot(work)
    reports = {name: b.aggregate(work / 'arms' / name) for name in ARMS}
    refs = b.rows(work / 'reference/unified.jsonl')
    predictions = {name: b.rows(work / 'arms' / name / 'summary/predictions.jsonl') for name in ARMS}
    completed = {name: {a['article_id']: b.completed_attempt(work / 'arms' / name, a['article_id'])[1]
                        for a in m['articles']} for name in ARMS}
    paired = [aid for aid in refs if all(completed[n][aid] for n in ARMS)]
    paired_set = set(paired)
    out = work / 'summary'
    out.mkdir(exist_ok=True)
    split_scores, metrics = {}, {}
    for split in ('train', 'validation'):
        subset = {aid: r for aid, r in refs.items() if r['split'] == split}
        split_scores[split] = {name: score(subset, {k: v for k, v in predictions[name].items() if k in subset})
                               for name in ARMS}
        metrics[split] = {name: score_metrics(s) for name, s in split_scores[split].items()}
        b.write(out / f'comparison-{split}.json', split_scores[split])
    paired_refs = {aid: refs[aid] for aid in paired}
    paired_scores = {name: score(paired_refs, {k: v for k, v in predictions[name].items() if k in paired_set})
                     for name in ARMS}
    b.write(out / 'scores-completed-pairs.json', paired_scores)
    timing = []
    for aid in refs:
        row = {'article_id': aid, 'split': refs[aid]['split'], 'both_completed': aid in paired_set}
        for name in ARMS:
            r = completed[name][aid]
            row[name + '_seconds'] = r['wall_seconds'] if r else None
            row[name + '_patients'] = r['patients_extracted'] if r else None
        row['seconds_delta_repairs_minus_control'] = (row['repairs_seconds'] - row['control_seconds']
                                                       if row['both_completed'] else None)
        timing.append(row)
    b.write_rows(out / 'paired-article-timing.jsonl', timing)
    complete = all(r['complete'] for r in reports.values())
    workers = [b.read(p) for p in (work / 'results/workers').glob('*/job-*/receipt.json')]
    result = {'format': 'paired-pipeline-result/1', 'complete': complete,
        'planned_articles_per_arm': len(refs), 'completed_pairs': len(paired), 'splits': m['splits'],
        'metrics': metrics, 'promotion': promotion(metrics['validation']['control'], metrics['validation']['repairs'], complete),
        'article_seconds': {n: reports[n]['article_seconds'] for n in ARMS},
        'paired_seconds_delta': b.stats([r['seconds_delta_repairs_minus_control'] for r in timing if r['both_completed']]),
        'summed_worker_seconds': sum(w['worker_seconds'] for w in workers),
        'worker_states': dict(Counter(w['status'] for w in workers)),
        'observed_worker_span_seconds': max(w['ended_unix'] for w in workers) - min(w['started_unix'] for w in workers) if workers else None,
        'interpretation': 'Full-arm scores count incomplete articles as missing. Completed-pair scores exclude them and may be biased. '
                          'No promotion on partial coverage. Timing includes repairs/EHR; excludes queue/startup. '
                          'Shared startup/worker time is recorded only at campaign level; do not sum arm throughput.',
        'test_evaluated': False, 'gepa_run': False}
    b.write(out / 'comparison.json', result)
    lines = ['# Overnight paired pipeline comparison', '',
             f'Complete: {complete}. Completed pairs: {len(paired)}/{len(refs)}. Test articles were excluded.', '',
             f"Recommendation: **{result['promotion']['recommendation']}** (validation-only gate).", '',
             '| Validation metric | Control | Repairs | Change (percentage points) |', '|---|---:|---:|---:|']
    for key, a in metrics['validation']['control'].items():
        z = metrics['validation']['repairs'][key]
        fmt = lambda x: 'unavailable' if x is None else f'{100*x:.2f}%'
        delta = 'unavailable' if a is None or z is None else f'{100*(z-a):+.2f}'
        lines.append(f'| {key} | {fmt(a)} | {fmt(z)} | {delta} |')
    lines += ['', '## Timing', '']
    for n in ARMS:
        s = result['article_seconds'][n]
        lines.append(f"- {n}: {s['n']} completed articles; mean {s['mean']} seconds, median {s['median']}, p95 {s['p95']}.")
    lines += ['', result['interpretation'], '', '## Scope', '',
              'Same pinned FP8/DFlash serving configuration and seed in both arms. Repairs are experimental; '
              'evidence validators remain enforced. Oversized sources and unsupported supplements remain explicit misses. '
              'No GEPA or automatic test evaluation was performed.', '',
              'See arms/*/summary for full and per-article scores, failures, and timing; '
              'summary/paired-article-timing.jsonl for paired latency and summary/scores-completed-pairs.json for partial-run diagnostics.', '']
    (out / 'comparison.md').write_text('\n'.join(lines))
    files = b.inventory(work, ['arms', 'results', 'summary', 'submissions', 'logs'])
    if (work / 'runtime.json').exists():
        files['runtime.json'] = b.sha(work / 'runtime.json')
    b.write(out / 'integrity.json', {'manifest_sha256': b.sha(work / 'manifest.json'), 'files': files})
    return result


def export(work):
    archive = work / 'results.tar.gz'
    with tarfile.open(archive, 'w:gz') as tf:
        for name in ('manifest.json', 'prepared.json', 'runtime.json', 'code', 'inputs', 'reference', 'arms',
                     'results', 'summary', 'logs', 'submissions'):
            path = work / name
            if path.exists():
                tf.add(path, arcname=work.name + '/' + name,
                       filter=lambda info: None if '__pycache__' in Path(info.name).parts else info)
    b.checksum(archive)
    return {'archive': str(archive), 'bytes': archive.stat().st_size}


def verify(work):
    b.check_snapshot(work)
    for name in arm_names(work):
        b.verify(work / 'arms' / name)
    integrity = b.read(work / 'summary/integrity.json')
    if integrity['manifest_sha256'] != b.sha(work / 'manifest.json'):
        raise ValueError('Campaign manifest changed after scoring')
    for path, digest in integrity['files'].items():
        if b.sha(work / path) != digest:
            raise ValueError('Scored campaign file changed: ' + path)
    result = b.read(work / 'summary/comparison.json')
    if not result['complete']:
        raise ValueError('Incomplete comparison; inspect worker and arm receipts')
    return {'verified': True, 'completed_pairs': result['completed_pairs'], 'test_evaluated': False,
            'recommendation': result['promotion']['recommendation']}


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest='command', required=True)
    for command in ('prepare', 'submit', 'setup', 'worker', 'aggregate', 'status', 'verify', 'export'):
        a = sub.add_parser(command)
        a.add_argument('--work', type=Path, required=True)
        if command == 'prepare':
            a.add_argument('--root', type=Path, default=Path.cwd())
            a.add_argument('--expected', type=int, default=260)
            a.add_argument('--download-media', action='store_true')
            a.add_argument('--hours', type=int, default=8)
            a.add_argument('--plan', choices=('paired', 'ablation'), default='paired')
        if command == 'submit':
            a.add_argument('--account', default='cai5724'); a.add_argument('--qos', default='cai5724')
            a.add_argument('--resume', action='store_true'); a.add_argument('--dry-run', action='store_true')
        if command == 'worker':
            a.add_argument('--shard', type=int, required=True)
    args = p.parse_args(argv)
    work = args.work.resolve()
    if args.command == 'prepare':
        result = prepare(args.root.resolve(), work, args.expected, args.download_media, args.hours, args.plan)
    elif args.command == 'submit':
        m = b.check_snapshot(work)
        result = b.submit(work, args.account, args.qos, m['overnight']['hours'], args.resume, args.dry_run,
                          script_name='dense_overnight.sbatch', campaigns=[work / 'arms' / n for n in arm_names(work)])
    elif args.command == 'setup':
        result = b.setup(work)
    elif args.command == 'worker':
        def terminate(signum, frame):
            raise KeyboardInterrupt('Slurm termination; preserve partial results')
        signal.signal(signal.SIGTERM, terminate)
        plan = b.read(work / 'manifest.json')['overnight']
        result = asyncio.run(b.worker(work, args.shard, campaigns=[work / 'arms' / n for n in arm_names(work)],
            budget_seconds=plan['hours']*3600 - plan['shutdown_reserve_seconds'],
            article_timeout=plan['article_timeout_seconds']))
        if result['status'] != 'done':
            raise SystemExit('Partial worker outcome: ' + result['status'] + '; CPU aggregation will export partial results.')
    elif args.command == 'aggregate':
        try:
            result = aggregate(work)
        except Exception as exc:
            b.write(work / 'summary/aggregation-error.json', {'error': repr(exc)})
            export(work)
            raise
        export(work)
        if not result['complete']:
            raise SystemExit('Partial comparison exported. No promotion; inspect status and receipts.')
    else:
        result = globals()[args.command](work)
    import json
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
