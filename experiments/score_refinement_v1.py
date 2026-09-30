"""Offline paired scoring; no model calls and no model-as-judge gold labels."""
from collections import defaultdict
import csv
import hashlib
import json
from pathlib import Path
import re

from openpatients2.fidelity import evaluate, summarize, without_evidence

ROOT = Path('runs/refinement-v1')
OLD = Path('runs/medical-fidelity-v1')


def probe_hits(probe, candidate):
    if not isinstance(candidate, dict):
        return None
    hits = []
    for i, item in enumerate(candidate.get('items', [])):
        if not isinstance(item, dict):
            continue
        name = str(item.get('name', ''))
        index = item.get('subject') == 'index_patient'
        if probe == 'anti_xa_as_result':
            hit = index and re.search(r'anti.?xa', name, re.I) and item.get('status') == 'resulted'
        elif probe == 'wrong_patient_transfusion':
            hit = index and re.search(r'transfus|packed.*(?:red|blood)|red.*blood.*cell|\bPRBC', name, re.I) and item.get('assertion') == 'present'
        elif probe == 'dialysis_declined':
            hit = index and re.search('dialysis|CRRT', name, re.I) and item.get('action') == 'declined'
        elif probe == 'fissure_status_inferred':
            hit = index and re.search('anal fissure', name, re.I) and item.get('clinical_status') not in {'unknown', None}
        else:
            raise ValueError(probe)
        if hit:
            hits.append(i)
    return hits


def cost_nodes(outcome, arm):
    """Distinct dependencies for a standalone arm; annotation costs added once."""
    results = [outcome['seed_result']]
    results += outcome['auxiliary'].get('projection_calls', [])
    results += outcome['auxiliary'].get('batch_context_calls', [])
    resolution = outcome['auxiliary'].get('disambiguation')
    if resolution:
        results.append(resolution['result'])
    if arm in {'whole_retry', 'local_repair', 'semantic_repair'}:
        results += outcome['arms'][arm]['extra'].get('calls', [])
    if arm == 'semantic_repair':
        results += outcome['arms']['local_repair']['extra'].get('calls', [])
    if arm in {'retry_local', 'retry_semantic'}:
        results += outcome['arms'][arm]['extra'].get('dependency_calls', [])
        results += outcome['arms'][arm]['extra'].get('calls', [])
    return results


def run():
    reference = json.loads((OLD/'reference.json').read_text())
    assert hashlib.sha256((OLD/'reference.json').read_bytes()).hexdigest() == (OLD/'reference.sha256').read_text().strip()
    probes = json.loads((ROOT/'semantic-probes.json').read_text())
    assert hashlib.sha256((ROOT/'semantic-probes.json').read_bytes()).hexdigest() == (ROOT/'semantic-probes.sha256').read_text().strip()
    checks = {c['id']: c for c in reference['checks']}
    buckets = defaultdict(lambda: {'rows': [], 'cells': [], 'nodes': {}, 'annotations': set(), 'probes': []})
    review_queue = {}; semantic_changes = []; preservation = []
    for path in sorted((ROOT/'outcomes').glob('*.json')):
        row = json.loads(path.read_text())
        ref = {**reference, 'checks': [c for c in reference['checks']
                                     if c['record_id'] == row['record_id'] and c['task'] == row['task']]}
        for arm, output in row['arms'].items():
            key = (row['model'], row['extraction'], arm); bucket = buckets[key]
            pred = {row['record_id']: {'raw': {row['task']: output['candidate']},
                                      'delivered': {row['task']: output['delivered']}}}
            scores = evaluate(ref, pred)
            bucket['rows'] += scores
            delivered_items = (output['delivered'] or {}).get('items', [])
            material_keys = [json.dumps(without_evidence(item), sort_keys=True) for item in delivered_items]
            bucket['cells'].append({'pmcid': row['pmcid'], 'patient_id': row['patient_id'], 'task': row['task'],
                                    'candidate_items': len((output['candidate'] or {}).get('items', [])),
                                    'delivered_items': len((output['delivered'] or {}).get('items', [])),
                                    'section_delivered': output['delivered'] is not None,
                                    'material_duplicate_excess': len(material_keys)-len(set(material_keys)),
                                    'raw': summarize(scores, 'raw'), 'delivered': summarize(scores, 'delivered')})
            for result in cost_nodes(row, arm):
                metric = result.get('metrics', {})
                if metric.get('signature'): bucket['nodes'][metric['signature']] = metric
            bucket['annotations'].update(row['auxiliary'].get('annotation_paths', []))
            for probe in probes:
                if (probe['pmcid'], probe['patient_id'], probe['task']) != (row['pmcid'], row['patient_id'], row['task']):
                    continue
                bucket['probes'].append({'id': probe['id'], 'record_id': row['record_id'], 'task': row['task'],
                    'raw_hits': probe_hits(probe['id'], output['candidate']),
                    'delivered_hits': probe_hits(probe['id'], output['delivered'])})
            for score in scores:
                if score['kind'] == 'required' and score['raw']['available'] and not score['raw']['matched']:
                    check = checks[score['check_id']]
                    candidate = output['candidate']
                    signature = hashlib.sha256(json.dumps([check['id'], candidate], sort_keys=True).encode()).hexdigest()[:16]
                    entry = review_queue.setdefault(signature, {'review_id': signature, 'check': check, 'candidate': candidate, 'cells': []})
                    entry['cells'].append({'model': row['model'], 'extraction': row['extraction'], 'arm': arm})
            if arm in {'semantic_repair', 'retry_semantic'}:
                for entry in output['extra'].get('trace', []):
                    if entry['applied']:
                        signature = hashlib.sha256(json.dumps([row['record_id'], row['task'], entry], sort_keys=True).encode()).hexdigest()[:16]
                        semantic_changes.append({'audit_id': signature, 'model': row['model'], 'extraction': row['extraction'],
                            'record_id': row['record_id'], 'task': row['task'], 'arm': arm, **entry})
            if arm in {'local_repair', 'retry_local'}:
                for entry in output['extra'].get('trace', []):
                    preservation.append({'model': row['model'], 'extraction': row['extraction'],
                        'record_id': row['record_id'], 'task': row['task'], 'arm': arm, 'decision': entry['decision'],
                        'erased_fields': entry.get('erased_fields', []), 'entry': entry})
    expected = 56 if (ROOT/'extension-protocol.json').exists() else 30
    result = {'method': 'Frozen typed-field matcher; source synonyms can cause misses. Agent semantic review is separate. Not medical precision/recall.',
              'expected_units_per_cell': 8, 'expected_cells': expected, 'models': {}, 'complete': len(buckets) == expected}
    for (model, mode, arm), bucket in buckets.items():
        for annotation in bucket['annotations']:
            saved = json.loads(Path(annotation).read_text())
            for call in [saved['result'], *saved.get('recovery_calls', [])]:
                node = call.get('metrics', {})
                if node.get('signature'): bucket['nodes'][node['signature']] = node
        nodes = list(bucket['nodes'].values())
        score = {'raw': summarize(bucket['rows'], 'raw'), 'delivered': summarize(bucket['rows'], 'delivered'),
                 'units': len(bucket['cells']), 'sections_delivered': sum(c['section_delivered'] for c in bucket['cells']),
                 'candidate_items': sum(c['candidate_items'] for c in bucket['cells']),
                 'delivered_items': sum(c['delivered_items'] for c in bucket['cells']),
                 'material_duplicate_excess': sum(c['material_duplicate_excess'] for c in bucket['cells']),
                 'calls': len(nodes), 'reported_cost_usd': sum(n['cost_usd'] for n in nodes),
                 'sum_call_latency_seconds': sum(n['latency_seconds'] for n in nodes),
                 'probe_violations_raw': sum(bool(p['raw_hits']) for p in bucket['probes']),
                 'probe_violations_delivered': sum(bool(p['delivered_hits']) for p in bucket['probes']),
                 'probe_unavailable_raw': sum(p['raw_hits'] is None for p in bucket['probes']),
                 'probe_unavailable_delivered': sum(p['delivered_hits'] is None for p in bucket['probes']),
                 'probes': bucket['probes'], 'cells': bucket['cells']}
        result['models'].setdefault(model, {}).setdefault(mode, {})[arm] = score
        if score['units'] != 8: result['complete'] = False
    for name, value in [('scores.json', result), ('checklist-review-queue.json', list(review_queue.values())),
                        ('semantic-change-audit.json', semantic_changes), ('local-preservation-audit.json', preservation)]:
        (ROOT/name).write_text(json.dumps(value, indent=2, ensure_ascii=False)+'\n')
    with (ROOT/'combinations.csv').open('w', newline='') as f:
        writer = csv.writer(f)
        writer.writerow(['model', 'extraction', 'postprocessor', 'units', 'required_checks', 'raw_matches',
                         'delivered_matches', 'raw_unavailable_checks', 'delivered_sections', 'delivered_items',
                         'semantic_probe_violations_delivered', 'semantic_probes_unavailable_delivered',
                         'calls_including_prerequisites', 'reported_cost_usd', 'material_duplicate_excess'])
        for model, modes in result['models'].items():
            for mode, arms in modes.items():
                for arm, s in arms.items():
                    writer.writerow([model, mode, arm, s['units'], s['raw']['required_checks'], s['raw']['matched'],
                        s['delivered']['matched'], s['raw']['required_checks']-s['raw']['checks_with_parseable_section'],
                        s['sections_delivered'], s['delivered_items'], s['probe_violations_delivered'],
                        s['probe_unavailable_delivered'], s['calls'], s['reported_cost_usd'], s['material_duplicate_excess']])
    for model, modes in result['models'].items():
        for mode, arms in modes.items():
            for arm, s in arms.items():
                print(model, mode, arm, 'units', s['units'], 'raw', s['raw']['matched'],
                      'delivered', s['delivered']['matched'], '/', s['raw']['required_checks'],
                      'probes', s['probe_violations_delivered'], 'cost', round(s['reported_cost_usd'], 3))
    print('Complete:', result['complete'], 'distinct miss reviews:', len(review_queue), 'semantic edits:', len(semantic_changes))


if __name__ == '__main__':
    run()
