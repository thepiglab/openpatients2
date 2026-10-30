"""Read-only coverage reporting for assembled extraction audit receipts.

Successful sparse requests establish coverage of their selected pairs only.
Neither source-window bookkeeping nor a valid graph establishes semantic recall.
"""
from collections import Counter
import hashlib
import json
from pathlib import Path


PAIR_COUNTS = (
    'total_unordered_pairs', 'candidate_pairs', 'unassessed_pairs',
    'successful_candidate_pairs', 'failed_candidate_pairs',
)
EDGE_COUNTS = ('locally_accepted_edges', 'retained_edges', 'quarantined_edges')
NOTES = [
    'A valid sparse timeline covers selected requests only. Unselected pairs remain unassessed, '
    'not negative; graph validity does not certify exhaustive temporal or clinical accuracy.',
    'Successful candidate pairs belong to valid requests; this does not mean every pair produced an edge. '
    'Local edge counts can contain duplicates and need not equal retained plus quarantined edges.',
    'Clinical decisions and quarantined candidates are counted per patient and extraction section, '
    'not as unique article segments or unique clinical facts. All segments attempted refers to the '
    'routed source packet, not whole-article recall. Coverage dispositions are model judgments.',
    'Null totals mean no applicable audit or a missing count in at least one applicable audit. '
    'Recorded counts and field coverage expose partial evidence; read errors make the report incomplete.',
]


def _count(value):
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


def _complete_sum(values):
    return sum(values) if values and all(v is not None for v in values) else None


def _timeline_summary(records):
    sparse = [r for r in records if isinstance(r.get('relation_candidate_audit'), dict)
              or str(r.get('relation_candidate_policy', '')).startswith('source-anchor-sparse-relations/')]
    counts = {key: [_count((r.get('relation_candidate_audit') or {}).get(key)) for r in sparse]
              for key in PAIR_COUNTS}
    counts.update({key: [_count((r.get('global_graph_audit') or {}).get(key)) for r in sparse]
                   for key in EDGE_COUNTS})
    return {
        'assembled_graphs': len(records), 'sparse_graphs': len(sparse),
        'non_sparse_graphs': len(records) - len(sparse),
        'graphs_without_sparse_audit': sum(not isinstance(r.get('relation_candidate_audit'), dict) for r in records),
        'graph_statuses': dict(Counter(r.get('status', 'unknown') for r in records)),
        'sparse_graph_statuses': dict(Counter(r.get('status', 'unknown') for r in sparse)),
        'policies': dict(Counter(r.get('relation_candidate_policy', 'unrecorded') for r in records)),
        **{key: _complete_sum(values) for key, values in counts.items()},
        'recorded_counts': {key: sum(v for v in values if v is not None)
                            if any(v is not None for v in values) else None for key, values in counts.items()},
        'metric_graph_counts': {key: sum(v is not None for v in values) for key, values in counts.items()},
        'graphs_with_unassessed_pairs': sum(v is not None and v > 0 for v in counts['unassessed_pairs']),
        'graphs_with_failed_candidate_pairs': sum(v is not None and v > 0 for v in counts['failed_candidate_pairs']),
        'graphs_with_assignment_gaps': sum((r.get('relation_candidate_audit') or {}).get(
            'all_candidates_assigned_to_leaf_requests') is False for r in sparse),
        'graphs_without_assignment_receipt': sum(not isinstance((r.get('relation_candidate_audit') or {}).get(
            'all_candidates_assigned_to_leaf_requests'), bool) for r in sparse),
        'semantic_coverage_verified': False,
        'coverage_scope': 'selected candidate requests; unselected event pairs remain unassessed',
    }


def _clinical_summary(records):
    audits = [r['audit'] for r in records]
    fields = ('source_decisions', 'cell_decisions', 'batches', 'attribution_quarantine', 'cell_linkage_audit')
    lists = {key: [a.get(key) if isinstance(a.get(key), list) else None for a in audits] for key in fields}
    rows = {key: [row for value in values if value is not None for row in value if isinstance(row, dict)]
            for key, values in lists.items()}
    counts = {key: _complete_sum([len(v) if v is not None else None for v in values])
              for key, values in lists.items()}
    return {
        'assembled_sections': len(records),
        'section_statuses': dict(Counter(r['status'] for r in records)),
        'task_counts': dict(Counter(r['audit'].get('task', 'unknown') for r in records)),
        'field_section_counts': {key: sum(v is not None for v in values) for key, values in lists.items()},
        'all_segments_attempted_sections': sum(a.get('all_segments_attempted') is True for a in audits),
        'incomplete_segment_attempt_sections': sum(a.get('all_segments_attempted') is False for a in audits),
        'unknown_segment_attempt_sections': sum(not isinstance(a.get('all_segments_attempted'), bool) for a in audits),
        'all_windows_valid_sections': sum(a.get('all_windows_valid') is True for a in audits),
        'invalid_window_sections': sum(a.get('all_windows_valid') is False for a in audits),
        'unknown_window_validity_sections': sum(not isinstance(a.get('all_windows_valid'), bool) for a in audits),
        'source_decision_count': counts['source_decisions'],
        'source_decisions': dict(Counter(r.get('disposition', 'unknown') for r in rows['source_decisions'])),
        'cell_decision_count': counts['cell_decisions'],
        'cell_decisions': dict(Counter(r.get('disposition', 'unknown') for r in rows['cell_decisions'])),
        'window_count': counts['batches'],
        'window_statuses': dict(Counter(r.get('status', 'unknown') for r in rows['batches'])),
        'cell_linkage_review_count': counts['cell_linkage_audit'],
        'attribution_quarantined_facts': counts['attribution_quarantine'],
        'attribution_quarantine_by_scope': dict(Counter(r.get('scope', 'unknown') for r in rows['attribution_quarantine'])),
        'sections_with_attribution_quarantine': sum(bool(v) for v in lists['attribution_quarantine']),
        'semantic_coverage_verified': False,
    }


def summarize(completed_paths):
    """Summarize ``(article_id, split, attempt_path)`` entries without changing files.

    Use assembled task receipts, never their child requests or repeated raw attempts.
    Clinical ``coverage/*.json`` files supply a fallback when the assembled task is
    absent; their normal embedded copies are counted once. Duplicate article inputs
    are rejected because they would double-count one benchmark's completed attempt.
    """
    articles, timeline, clinical, errors, seen = [], [], [], [], set()
    for article_id, split, attempt in completed_paths:
        if article_id in seen:
            raise ValueError('Duplicate completed article: ' + str(article_id))
        seen.add(article_id)
        attempt = Path(attempt)
        article_timeline, article_clinical = [], {}

        def error(path, message):
            errors.append({'article_id': article_id, 'split': split,
                           'file': str(path.relative_to(attempt)), 'error': message})

        def read(path):
            try:
                value = json.loads(path.read_text())
                if not isinstance(value, dict):
                    raise ValueError('Expected a JSON object')
                return value
            except (OSError, ValueError) as exc:
                error(path, type(exc).__name__ + ': ' + str(exc))
                return None

        def clinical_receipt(audit, status, path):
            if not isinstance(audit, dict) or audit.get('policy') != 'clinical-source-windows/1':
                return
            key = (json.dumps(audit.get('identity', {}), sort_keys=True), audit.get('task'))
            signature = hashlib.sha256(json.dumps(audit, sort_keys=True).encode()).hexdigest()
            if key in article_clinical:
                if article_clinical[key]['signature'] != signature:
                    error(path, 'Conflicting clinical audit copies; assembled task receipt retained')
                return
            compact = {key: audit[key] for key in ('task', 'all_segments_attempted', 'all_windows_valid') if key in audit}
            for field, disposition in (('source_decisions', 'disposition'), ('cell_decisions', 'disposition'),
                                       ('batches', 'status'), ('attribution_quarantine', 'scope'),
                                       ('cell_linkage_audit', 'status')):
                if isinstance(audit.get(field), list):
                    compact[field] = [{disposition: row.get(disposition, 'unknown')} if isinstance(row, dict)
                                      else {} for row in audit[field]]
            article_clinical[key] = {'audit': compact, 'status': status, 'signature': signature}

        task_dir = attempt / 'extraction/tasks'
        if not task_dir.is_dir():
            error(task_dir, 'Task directory is missing; audit availability is unknown')
        for path in sorted(task_dir.glob('*.json')):
            task = read(path)
            if task is None or task.get('superseded_by_split') or task.get('status') == 'replaced_by_split':
                continue
            if task.get('task') == 'timeline_v2':
                # Raw attempts can be very large. Keep only count receipts across articles.
                compact = {key: task[key] for key in ('status', 'relation_candidate_policy') if key in task}
                for field, keys in (
                    ('relation_candidate_audit', (*PAIR_COUNTS, 'all_candidates_assigned_to_leaf_requests')),
                    ('global_graph_audit', EDGE_COUNTS),
                ):
                    if isinstance(task.get(field), dict):
                        compact[field] = {key: task[field][key] for key in keys if key in task[field]}
                article_timeline.append(compact)
            clinical_receipt(task.get('coverage_audit'), task.get('status', 'unknown'), path)
        for path in sorted((attempt / 'extraction/coverage').glob('*.json')):
            audit = read(path)
            clinical_receipt(audit, 'unavailable', path)
        section_records = list(article_clinical.values())
        article_errors = [e for e in errors if e['article_id'] == article_id]
        articles.append({'article_id': article_id, 'split': split,
                         'timeline': _timeline_summary(article_timeline),
                         'clinical': _clinical_summary(section_records),
                         'read_error_count': len(article_errors)})
        timeline.extend(article_timeline)
        clinical.extend(section_records)
    return {'schema_version': 'extraction-audits/1', 'completed_articles': len(articles),
            'articles_with_sparse_audits': sum(a['timeline']['sparse_graphs'] > 0 for a in articles),
            'articles_with_clinical_audits': sum(a['clinical']['assembled_sections'] > 0 for a in articles),
            'timeline': _timeline_summary(timeline), 'clinical': _clinical_summary(clinical),
            'per_article': articles, 'read_errors': errors, 'audit_files_readable': not errors,
            'semantic_coverage_verified': False, 'notes': NOTES.copy()}
