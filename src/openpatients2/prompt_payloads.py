"""Compact references to unreviewed facts; primary source text remains intact."""
from copy import deepcopy


def compact_value(value):
    if isinstance(value, dict):
        return {k: compact_value(v) for k, v in value.items()
                if k not in {'evidence', 'attribution_evidence', 'documentation_evidence',
                             'field_supports', 'evidence_spans'} and v is not None}
    if isinstance(value, list):
        return [compact_value(v) for v in value]
    return value


def compact_facts(rows):
    """Stable IDs and clinical values, without copying source quotations per row."""
    return [{'fact_id': r['review_id'], 'task': r['task'],
             'value': compact_value(r['candidate']),
             'source_segment_ids': sorted({e.get('segment_id', e.get('source_section'))
                 for e in [*r.get('evidence_spans', []), *r['candidate'].get('evidence', [])]
                 if e.get('segment_id', e.get('source_section'))}),
             'semantic_review': 'unreviewed'} for r in rows]


def compact_graph(graph):
    if not graph:
        return graph
    result = compact_value(deepcopy(graph))
    for key in ('events', 'edges'):
        for row, original in zip(result.get(key, []), graph.get(key, [])):
            row['source_segment_ids'] = sorted({e['segment_id'] for e in original.get('evidence', [])})
    return result


def timeline_retention(previous, current):
    """Retain linked facts and source-gated events that lack clinical fact IDs.

    Linked encounters may be split into finer events. Unlinked events cannot
    silently disappear just because retaining fact IDs would not detect them.
    This is a structural retention gate, not a semantic accuracy claim.
    """
    old = (previous or {}).get('events', [])
    new = current.get('events', [])
    required = {fid for e in old for fid in e['fact_ids']}
    retained = {fid for e in new for fid in e['fact_ids']}
    if required - retained:
        raise ValueError('Timeline completion erased existing supported fact links')
    for event in old:
        if event['fact_ids']:
            continue
        if not any(all(candidate.get(k) == event.get(k) for k in
                       ('description', 'kind', 'occurrence')) and
                   compact_value(candidate.get('times', [])) == compact_value(event.get('times', []))
                   for candidate in new):
            raise ValueError('Timeline completion erased an existing unlinked event')
    return current
