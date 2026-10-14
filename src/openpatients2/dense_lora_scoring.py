"""CPU scoring of a fixed inner-validation diagnostic, never the held-out test."""
from collections import Counter
import json
import re

from . import dense_benchmark as b, dense_gepa as g, dense_gepa_next as n


def final_answer(text):
    # Generation starts after '<|start|>assistant'. Never score the self channel.
    matches = list(re.finditer(r'(?:^|<\|start\|>assistant)\s+to=user<\|message\|>(.*?)<\|eot\|>', text, re.S))
    if len(matches) != 1 or text[matches[0].end():].strip():
        raise ValueError('Missing/ambiguous complete ATEM final answer')
    return matches[0].group(1)


def score(work):
    from .output_parser import parse_output
    from .validation import validate
    path = work/'tokenized/preview.jsonl'
    if not path.exists(): return {'complete': False, 'reason': 'CPU setup did not produce diagnostic subset'}
    expected = [json.loads(line) for line in path.read_text().splitlines()]
    identity = lambda r: (r['article_id'], r['patient_id'], r['task'])
    if not expected or len({identity(r) for r in expected}) != len(expected):
        raise ValueError('Empty/duplicate diagnostic subset')
    refs = b.rows(work/'reference/unified.jsonl')
    packets = {aid: {r['packet']['patient_target']['patient_id']: r for r in
                    b.read(work/'inputs'/aid/'gepa-packets.json')} for aid in {r['article_id'] for r in expected}}
    plan = b.read(work/'manifest.json')['lora']
    report = {'scope': 'fixed eligible inner-validation subset, not whole-article or full-corpus benchmark',
              'expected_examples': len(expected), 'test_evaluated': False, 'runs': {}}
    for name in ['baseline', *[t['name'] for t in plan['trials']]]:
        path = work/'results'/name/'generations.jsonl'
        generated = [json.loads(l) for l in path.read_text().splitlines()] if path.exists() else []
        lookup = {identity(r): r for r in generated}
        if len(lookup) != len(generated) or set(lookup) - {identity(r) for r in expected}:
            raise ValueError('Duplicate/unexpected generation identity')
        scored = []
        for row in expected:
            aid, pid, task = identity(row); output = lookup.get(identity(row)); data = {}; status = 'missing'; error = None
            try:
                if output is None: raise ValueError('No generation')
                parsed = parse_output(final_answer(output['text']))
                packet = packets[aid][pid]
                checked = validate(task, parsed.value, packet['packet']['text'],
                                   segments=packet['segments'], target_patient=True, strict_time=True)
                if not checked.valid: raise ValueError('; '.join(checked.errors))
                data = checked.data; status = 'valid'
            except (ValueError, TypeError, KeyError) as exc:
                error = str(exc)[:2000]
                if output is not None: status = 'invalid'
            # Only selected patients/tasks enter the denominator; unselected
            # patients are not silently counted as model misses.
            reference = refs[aid] | {'patients': [p for p in refs[aid]['patients'] if p['patient_id'] == pid]}
            if len(reference['patients']) != 1: raise ValueError('Reference patient missing/duplicated')
            metrics = g.measure(reference, {pid: data}, task, 2., [status])
            metrics.update(n.diagnostics(reference, {pid: data}, task))
            metrics.pop('feedback'); metrics.pop('aligned')
            scored.append({k: row[k] for k in ('article_id', 'patient_id', 'task')} | metrics |
                          {'error': error, 'seconds': output['seconds'] if output else None})
        tasks = {}
        for task in ('observations', 'medications'):
            rows = [r for r in scored if r['task'] == task]; counts = Counter(); grounded = Counter(); statuses = Counter()
            for r in rows:
                counts.update(r['counts']); grounded.update(r['grounded_counts']); statuses.update(r['statuses'])
            tp, fp, fn = [counts[k] for k in ('tp', 'fp', 'fn')]
            correct = sum(r['attributes']['correct'] for r in rows); total = sum(r['attributes']['total'] for r in rows)
            tasks[task] = {'examples': len(rows), 'counts': dict(counts), 'statuses': dict(statuses),
                'f2': g.fbeta(tp, fp, fn), 'grounded_f2': g.fbeta(*(grounded[k] for k in ('tp','fp','fn'))),
                'precision': tp/(tp+fp) if tp+fp else 0., 'recall': tp/(tp+fn) if tp+fn else 1.,
                'attribute_accuracy': correct/total if total else None,
                'critical_attributes': {k: {'correct': sum(r['critical_attributes'][k]['correct'] for r in rows),
                                          'total': sum(r['critical_attributes'][k]['total'] for r in rows)} for k in n.CRITICAL},
                'seconds_per_patient_task': b.stats([r['seconds'] for r in rows if r['seconds'] is not None])}
        report['runs'][name] = {'generated': len(generated), 'complete': len(generated) == len(expected), 'tasks': tasks,
            'hit_token_limit': sum(r.get('hit_token_limit', False) for r in generated),
            'examples': scored}
    b.write(work/'summary/generation-scores.json', report)
    return report
