"""Link extracted facts to preserved endpoint reasoning without inventing rationales."""
from __future__ import annotations

import json

from .targeted_repair import canonical_clinical_task


def link_fact_reasoning(rows, task_results, model):
    """Attach task-level ledger references to facts with the same patient/task.

    Repairs may retain facts from earlier calls; every related call stays visible.
    These links do not assert which sentence of a trace explains any given fact.
    """
    calls = {}
    for task in task_results:
        name = task['task']
        canonical = canonical_clinical_task(name) or {
            'summary_completion': 'summary', 'timeline_completion': 'timeline_v2',
            'timeline_events': 'timeline_v2', 'timeline_relations': 'timeline_v2',
            'ordering_review': 'timeline_v2'}.get(name, name)
        for attempt in task.get('attempts', []):
            sequence = attempt.get('ledger_sequence')
            if sequence is None:
                continue
            identity = attempt.get('identity') or task.get('identity') or {}
            response = attempt.get('response') or {}
            key = (identity.get('record_id'), canonical)
            calls.setdefault(key, {})[sequence] = {
                'ledger': 'attempts.jsonl', 'ledger_sequence': sequence,
                'task': name, 'mode': attempt.get('mode'),
                'messages_sha256': attempt.get('messages_sha256'),
                'response_id': response.get('response_id'),
                'reasoning_status': response.get('reasoning_status', 'unknown_legacy' if attempt.get('response') is not None else 'no_response'),
                'finish_reason': response.get('finish_reason'),
                'validation_errors': attempt.get('errors', []),
            }
    for row in rows:
        linked = calls.get((row['record_id'], row['task']), {})
        row['generation_trace'] = {
            'model': dict(model), 'scope': 'patient_task_calls', 'fact_specific_reasoning': False,
            'calls': [linked[k] for k in sorted(linked)],
            'note': 'Read endpoint-returned reasoning in each referenced ledger response. A task can emit multiple facts; repairs can retain earlier facts. Reasoning is not source evidence or a verified explanation.',
        }
    return rows


def export_fact_index(work, output, completed):
    """CPU-side fact/citation/trace join; no reasoning text duplicated per fact."""
    counts = {'facts': 0, 'facts_with_call_links': 0, 'facts_with_returned_reasoning_links': 0}
    with output.open('w') as stream:
        for article_id, split, attempt in completed:
            for review in sorted((attempt / 'extraction/review').glob('*.json')):
                for row in json.loads(review.read_text()):
                    trace = row.get('generation_trace') or {}
                    linked = [{**call, 'ledger': str((attempt / 'extraction' / call['ledger']).relative_to(work))}
                              for call in trace.get('calls', [])]
                    counts['facts'] += 1
                    counts['facts_with_call_links'] += bool(linked)
                    counts['facts_with_returned_reasoning_links'] += any(
                        c.get('reasoning_status') in {'returned_text', 'returned_nontext'} for c in linked)
                    stream.write(json.dumps({**row, 'article_id': article_id, 'split': split,
                        'source_review': str(review.relative_to(work)),
                        'generation_trace': {**trace, 'calls': linked, 'ledger_path_base': 'arm_root'},
                        'training_eligible': None, 'semantic_correctness': 'unreviewed'}, ensure_ascii=False) + '\n')
    return {**counts, 'artifact': str(output.relative_to(work)),
            'scope': 'clinical facts, summary claims and timeline objects in extraction/review; EHR/vision call traces remain in their original ledgers',
            'training_filter_applied': False}
