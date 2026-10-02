"""Offline Glimmer comparison; stream the archive without extracting weights/caches."""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import csv
import hashlib
import json
from pathlib import Path
import tarfile

from openpatients2.data import write_json
from openpatients2.fidelity import evaluate, review_sample, summarize
from openpatients2.hpg_eval import fixtures, legacy_validator

AUDIT_ARMS = ('glimmer-fp8/meta_medium', 'glimmer-fp8/meta_medium_dflash',
              'glimmer-nvfp4/meta_xhigh', 'glimmer-nf4/meta_xhigh')


def error_category(message):
    if 'temporal expression must occur literally' in message: return 'nonliteral time evidence'
    if 'quote must be a literal' in message or 'quote not found' in message or 'quote not literal' in message:
        return 'nonliteral source evidence'
    if 'Duplicate object key' in message: return 'duplicate JSON keys'
    if 'No complete parseable object' in message: return 'no parseable final JSON'
    if 'incomplete_finish' in message: return 'truncated generation'
    if 'schema' in message or 'is not one of' in message: return 'schema/enum'
    return message[:160]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('archive', type=Path)
    parser.add_argument('--output', type=Path, default=Path('runs/glimmer-comparison-20261002'))
    args = parser.parse_args(); args.output.mkdir(parents=True, exist_ok=True)
    fixture_path = Path('benchmarks/hipergator-k2/fixtures')
    manifest, articles, packets, requests, reference = fixtures(fixture_path)
    expected = {(r['identity']['article_id'] + ':' + r['identity']['patient_id'], r['task']): r for r in requests}
    predictions = defaultdict(dict); initial_predictions = defaultdict(dict)
    tasks = defaultdict(list); pairs = defaultdict(set); reports = {}; secondary = {}; cell_reports = {}
    selected_tasks = []; secondary_tasks = []; failed_logs = []; summary = campaign = None
    with tarfile.open(args.archive, 'r|gz') as archive:
        for member in archive:
            if not member.isfile(): continue
            parts = member.name.split('/')
            if member.name.startswith('/') or any(p in {'.', '..'} for p in parts) or '\\' in member.name:
                raise ValueError('Unsafe archive member path')
            if member.name in {'campaign.json', 'summary.json'}:
                value = json.load(archive.extractfile(member))
                if member.name == 'campaign.json': campaign = value
                else: summary = value
                continue
            if len(parts) < 3 or parts[0] != 'results': continue
            if parts[2] == 'layouts':
                if parts[-1].endswith('.log') and 'servers' in parts and ('dspark' in parts[3] or 'dcp' in parts[3]):
                    text = archive.extractfile(member).read().decode('utf-8', errors='replace')
                    target = args.output / 'failure-logs' / '/'.join(parts[1:]); target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_text(text); failed_logs.append(str(target))
                elif parts[-1] == 'report.json' and len(parts) == 6:
                    cell_reports['/'.join(parts[1:5])] = json.load(archive.extractfile(member))
                continue
            arm = parts[2]
            if not (arm == 'matched' or arm.startswith('meta_')) or not member.name.endswith('.json'): continue
            key = parts[1] + '/' + arm
            if len(parts) == 4 and parts[-1] == 'report.json': reports[key] = json.load(archive.extractfile(member))
            elif len(parts) == 5 and parts[3:] == ['secondary', 'report.json']:
                secondary[key] = json.load(archive.extractfile(member))
            elif len(parts) == 4 and parts[-1].startswith('PMC'):
                patient = json.load(archive.extractfile(member)); rid = patient['source']['record_id']
                predictions[key].setdefault(rid, {'raw': {}, 'delivered': {}})['patient'] = {
                    'companions': {k: {'status': v['status']} for k, v in patient.get('companions', {}).items()},
                    'vision': patient.get('vision'), 'figure_attribution': patient.get('figure_attribution')}
            elif len(parts) == 5 and parts[3] == 'tasks':
                task = json.load(archive.extractfile(member))
                if task['task'] in {'roster', 'figure_attribution'}:
                    if key in AUDIT_ARMS:
                        secondary_tasks.append({'model_arm': key, **task})
                    continue
                rid = task['identity']['article_id'] + ':' + task['identity']['patient_id']; pair = rid, task['task']
                if pair not in expected or pair in pairs[key]: raise ValueError('Unexpected/duplicate task: ' + str(pair))
                pairs[key].add(pair)
                if task['attempts'][0]['messages_sha256'] != expected[pair]['messages_sha256']:
                    raise ValueError('Frozen first prompt changed: ' + str(pair))
                pred = predictions[key].setdefault(rid, {'raw': {}, 'delivered': {}})
                pred['raw'][task['task']] = task['raw']; pred['delivered'][task['task']] = task['data']
                initial = initial_predictions[key].setdefault(rid, {'raw': {}, 'delivered': {}})
                initial['raw'][task['task']] = task['attempts'][0]['candidate']
                initial['delivered'][task['task']] = task['attempts'][0]['candidate'] if not task['attempts'][0]['errors'] else None
                tasks[key].append({'record_id': rid, 'task': task['task'], 'status': task['status'], 'errors': task['errors'],
                    'first_errors': task['attempts'][0]['errors'], 'attempts': len(task['attempts']),
                    'finish_reason': (task['attempts'][-1].get('response') or {}).get('finish_reason')})
                if key in AUDIT_ARMS:
                    selected_tasks.append({'model_arm': key, **task})
    if summary is None or campaign is None or summary['failed_models']: raise ValueError('Missing/incomplete core campaign')
    if campaign['fixture_manifest_sha256'] != hashlib.sha256((fixture_path / 'manifest.json').read_bytes()).hexdigest():
        raise ValueError('Campaign used different fixtures')
    results = {}; all_scores = {}; first_scores = {}
    for key in sorted(predictions):
        if len(pairs[key]) != 176 or len(predictions[key]) != 11: raise ValueError('Incomplete arm: ' + key)
        scores = evaluate(reference, predictions[key]); all_scores[key] = scores
        counts = {mode: summarize(scores, mode) for mode in ('raw', 'delivered')}
        if counts != reports[key]['scores']: raise ValueError('Saved/recomputed scores differ: ' + key)
        first = evaluate(reference, initial_predictions[key]); first_scores[key] = first
        results[key] = {'automatic': counts, 'first_attempt': {mode: summarize(first, mode) for mode in ('raw', 'delivered')},
            'valid_tasks': sum(t['status'] == 'valid' for t in tasks[key]), 'invalid_tasks': sum(t['status'] != 'valid' for t in tasks[key]),
            'first_attempt_valid_tasks': sum(not t['first_errors'] for t in tasks[key]),
            'final_errors': dict(Counter(error_category(e) for t in tasks[key] for e in t['errors'])),
            'first_errors': dict(Counter(error_category(e) for t in tasks[key] for e in t['first_errors'])),
            'by_article': {aid: {mode: summarize([r for r in scores if r['record_id'].split(':')[0] == aid], mode)
                         for mode in ('raw', 'delivered')} for aid in sorted({r['record_id'].split(':')[0] for r in scores})},
            'tokens': reports[key]['tokens'], 'generation_settings': reports[key]['generation_settings']}
    audit, aliases, availability = review_sample(reference, {k: predictions[k] for k in AUDIT_ARMS})
    reverse = {v: k for k, v in aliases.items()}
    for row in audit: row['model'] = reverse[row['model_alias']]
    legacy, _ = legacy_validator(fixture_path); salvage = []
    for task in selected_tasks:
        raw = task['raw']
        if task['status'] == 'valid' or task['task'] in {'summary', 'timeline'} or not isinstance(raw, dict): continue
        items = raw.get('items', [])
        if not isinstance(items, list): continue
        rid = task['identity']['article_id'] + ':' + task['identity']['patient_id']
        checks = [legacy.validate(task['task'], {**raw, 'items': [item]}, packets[rid]['text']) for item in items]
        salvage.append({'model_arm': task['model_arm'], 'record_id': rid, 'task': task['task'], 'items': len(items),
            'individually_validator_valid': sum(c.valid for c in checks), 'errors': task['errors'],
            'per_item_errors': [c.errors for c in checks],
            'notice': 'Diagnostic only; no export was salvaged, and structural/source validation is not full clinical correctness.'})
    for name, value in [('comparison.json', {'results': results, 'fixture_sha256': campaign['fixture_manifest_sha256'],
        'all_prompt_hashes_match': True, 'all_scores_reproduced': True, 'failed_logs': failed_logs}),
        ('predictions.json', predictions), ('scores.json', all_scores), ('first-scores.json', first_scores),
        ('task-statuses.json', tasks), ('selected-tasks.json', selected_tasks), ('secondary.json', secondary),
        ('secondary-tasks.json', secondary_tasks),
        ('cells.json', cell_reports), ('claim-audit-pending.json', audit), ('audit-availability.json', availability),
        ('item-quarantine-diagnostic.json', salvage), ('campaign-summary.json', summary)]:
        write_json(args.output / name, value)
    with (args.output / 'metrics.csv').open('w') as out:
        writer = csv.writer(out); writer.writerow(['arm', 'raw_facts', 'delivered_facts', 'valid_tasks', 'invalid_tasks', 'first_valid_tasks'])
        for key, r in results.items(): writer.writerow([key, r['automatic']['raw']['matched'], r['automatic']['delivered']['matched'],
            r['valid_tasks'], r['invalid_tasks'], r['first_attempt_valid_tasks']])
    print(json.dumps({'arms_reproduced': len(results), 'primary_tasks': sum(map(len, tasks.values())), 'audit_claims': len(audit),
        'speed_cells': len(cell_reports), 'failed_logs_saved': len(failed_logs), 'output': str(args.output)}))


if __name__ == '__main__': main()
