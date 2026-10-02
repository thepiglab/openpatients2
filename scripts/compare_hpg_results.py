"""Offline comparison: read the results archive without extracting its contents."""
import argparse
from collections import Counter, defaultdict
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import tarfile

from openpatients2.fidelity import evaluate, load_predictions, review_sample, summarize, validate_reference


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('archive', type=Path)
    parser.add_argument('--output', type=Path, default=Path('runs/hpg-comparison-20261001'))
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    fixtures = Path('benchmarks/hipergator-k2/fixtures')
    reference_bytes = (fixtures / 'reference.json').read_bytes()
    reference = json.loads(reference_bytes)
    articles = {r['article_id']: r for r in map(json.loads, (fixtures / 'articles.jsonl').read_text().splitlines())}
    validate_reference(reference, articles)
    requests = { (r['identity']['article_id'] + ':' + r['identity']['patient_id'], r['task']): r
                 for r in map(json.loads, (fixtures / 'requests.jsonl').read_text().splitlines())}
    predictions = defaultdict(dict)
    tasks = defaultdict(list)
    reports = {}
    secondary = {}
    summary = None
    seen_pairs = defaultdict(set)
    integrity = []
    # Sequential decompression avoids repeated seeks through a large gzip file.
    with tarfile.open(args.archive, 'r|gz') as archive:
        for member in archive:
            if not member.isfile() or not member.name.endswith('.json'):
                continue
            parts = member.name.split('/')
            if len(parts) == 2 and parts[-1] == 'summary.json':
                summary = json.load(archive.extractfile(member))
                continue
            if len(parts) < 5 or parts[1] != 'results' or parts[3] not in {'matched', 'ifm_high', 'ifm_medium', 'ifm_low'}:
                continue
            key = parts[2] + '/' + parts[3]
            if len(parts) == 5 and parts[-1] == 'report.json':
                reports[key] = json.load(archive.extractfile(member))
            elif len(parts) == 6 and parts[4:] == ['secondary', 'report.json']:
                secondary[key] = json.load(archive.extractfile(member))
            elif len(parts) == 5 and parts[-1].startswith('PMC'):
                patient = json.load(archive.extractfile(member))
                rid = patient['source']['record_id']
                predictions[key].setdefault(rid, {'raw': {}, 'delivered': {}})['patient'] = patient
            elif len(parts) == 6 and parts[4] == 'tasks':
                task = json.load(archive.extractfile(member))
                if task['task'] in {'roster', 'figure_attribution'}:
                    continue
                rid = task['identity']['article_id'] + ':' + task['identity']['patient_id']
                pair = (rid, task['task'])
                if pair not in requests or pair in seen_pairs[key]:
                    raise ValueError('Unexpected/duplicate primary task: ' + str(pair))
                seen_pairs[key].add(pair)
                initial = task['attempts'][0]
                if initial.get('messages_sha256') != requests[pair]['messages_sha256']:
                    raise ValueError('First prompt differs from frozen comparison: ' + str(pair))
                pred = predictions[key].setdefault(rid, {'raw': {}, 'delivered': {}})
                pred['raw'][task['task']] = task['raw']
                pred['delivered'][task['task']] = task['data']
                tasks[key].append({'record_id': rid, 'task': task['task'], 'status': task['status'],
                    'errors': task['errors'], 'attempts': len(task['attempts']),
                    'finish_reason': (task['attempts'][-1].get('response') or {}).get('finish_reason'),
                    'boundary_recoveries': sum(bool(a.get('answer_boundary_recovery')) for a in task['attempts'])})
    if summary is None or summary['failed_records']:
        raise ValueError('Campaign summary missing/incomplete')
    results = {}
    scores = {}
    for key in sorted(predictions):
        if len(seen_pairs[key]) != 176:
            raise ValueError('Incomplete primary outputs: ' + key)
        rows = evaluate(reference, predictions[key]); scores[key] = rows
        counts = {mode: summarize(rows, mode) for mode in ['raw', 'delivered']}
        for mode in counts:
            if counts[mode] != reports[key]['scores'][mode]:
                raise ValueError('Saved/recomputed score mismatch: ' + key)
        results[key] = {'source': 'K2 archive', 'counts': counts,
            'valid_tasks': sum(t['status'] == 'valid' for t in tasks[key]), 'invalid_tasks': sum(t['status'] != 'valid' for t in tasks[key]),
            'boundary_recovered_attempts': sum(t['boundary_recoveries'] for t in tasks[key]),
            'failures': Counter(e.split(':')[0][:150] for t in tasks[key] for e in t['errors']),
            'by_article': {aid: {mode: summarize([r for r in rows if r['record_id'].split(':')[0] == aid], mode)
                                for mode in ['raw', 'delivered']} for aid in sorted({r['record_id'].split(':')[0] for r in rows})},
            'generation_settings': reports[key]['generation_settings'], 'tokens': reports[key]['tokens']}
        integrity.append({'arm': key, 'patients': len(predictions[key]), 'primary_tasks': len(tasks[key]), 'scores_reproduced': True})
    old = json.loads(Path('reports/medical-fidelity-results.json').read_text())
    for model, d in old['models'].items():
        key = 'historical/' + model
        predictions[key] = load_predictions(Path('runs/medical-fidelity-v1/comparison') / model.replace('/', '--'))
        scores[key] = evaluate(reference, predictions[key])
        results[key] = {'source': 'Previous hosted fidelity study', 'counts': d['automatic'], 'adjudicated_counts': d['adjudicated'],
            'valid_tasks': d['tasks']['valid'], 'invalid_tasks': d['tasks']['total'] - d['tasks']['valid'],
            'claim_audit': d['claim_audit'], 'by_article': d['by_article']}
    selected = ['k2-375b-nvfp4/ifm_low', 'k2-375b-nvfp4/ifm_medium', 'k2-32b-nvfp4/ifm_high',
                'k2-mova-36b-fp8/ifm_low', 'k2-7b-fp8/matched']
    audit, aliases, availability = review_sample(reference, {k: predictions[k] for k in selected})
    reverse_aliases = {v: k for k, v in aliases.items()}
    for item in audit:
        item['model'] = reverse_aliases[item['model_alias']]
    # Review the best-delivery small-model arms separately, keeping the initial
    # fixed sample and its identifiers intact for the source review.
    extra, extra_aliases, extra_availability = review_sample(reference, {
        k: predictions[k] for k in ['k2-mova-36b-fp8/ifm_medium', 'k2-7b-fp8/ifm_high']})
    reverse_extra = {v: k for k, v in extra_aliases.items()}
    for item in extra:
        item['model'] = reverse_extra[item['model_alias']]
    audit.extend(extra)
    availability.extend(extra_availability)
    for name, obj in [('comparison.json', {'reference_sha256': hashlib.sha256(reference_bytes).hexdigest(),
                                         'integrity': integrity, 'results': results, 'secondary': secondary}),
                      ('predictions.json', predictions), ('scores.json', scores), ('claim-audit-pending.json', audit),
                      ('audit-availability.json', availability), ('task-statuses.json', tasks),
                      ('campaign-status.json', {
                          'failed_models': summary['failed_models'],
                          'weight_storage_exists': summary['weight_storage_exists'],
                          'models': [{
                              'model': row['model'],
                              'download_status': row['download'].get('status'),
                              'gpu_status': row['gpu'].get('status'),
                              'cleanup_status': row['cleanup'].get('status'),
                              'throughput': row['throughput'],
                          } for row in summary['models']],
                      })]:
        (args.output / name).write_text(json.dumps(obj, ensure_ascii=False, indent=2) + '\n')
    print('Reproduced 16 K2 arms; audit sample:', len(audit), 'claims. No archive contents extracted.')


if __name__ == '__main__':
    main()
