"""Deterministic, bounded relation candidates; never a chronology inference.

The candidate universe is patient-local. Source proximity and shared literal
anchors are retrieval signals, not evidence that a relation exists. Unselected
pairs remain explicitly unassessed rather than becoming negative labels.
"""
from collections import defaultdict
from copy import deepcopy
from bisect import bisect_left
import hashlib
import json
import re


POLICY = 'source-anchor-sparse-relations/1'


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def canonical_events(stages):
    """Collapse only identical content modulo list order and local event IDs.

    In particular, identical descriptions at different visits/source spans are
    distinct. No fuzzy matching, inferred episodes, or fact deletion occurs.
    Original task payloads and this mapping retain all local identities.
    """
    events, mapping, seen = [], [], {}
    for stage in stages:
        for original in (stage.get('data') or {}).get('events', []):
            content = deepcopy({k: v for k, v in original.items() if k != 'event_id'})
            for field in ('fact_ids', 'evidence', 'attribution_evidence', 'times'):
                content[field] = sorted(content.get(field, []), key=lambda x: json.dumps(x, sort_keys=True))
            key = digest(content)
            if key not in seen:
                seen[key] = 'e' + str(len(events) + 1)
                events.append({**deepcopy(original), 'event_id': seen[key]})
            mapping.append({'task_key': stage.get('task_key'), 'batch': stage['identity'].get('batch'),
                            'original_event_id': original.get('event_id'), 'event_id': seen[key],
                            'content_sha256': key})
    return events, {'policy': 'exact-event-content/1', 'mapping': mapping,
                    'original_events': len(mapping), 'canonical_events': len(events),
                    'semantic_merges': 0}


def plan(events, segments, record_id, *, neighbors=16, pairs_per_event_budget=32, bridge_matches=4):
    """Return at most budget*N unique unordered candidates and a replay audit.

    Local neighbors: bounded source-proximity retrieval.
    Shared anchors: adjacent members of exact-evidence/calendar/episode groups,
    rather than each group's complete Cartesian product.
    Calendar bridges: representatives of consecutive explicit calendar anchors.
    Clinical bridges: source-near presentation/diagnosis/treatment/outcome events
    even when many laboratory-value events intervene in the source registry.
    Relative bridges: exact phrases following after/before/during/following/until
    in a literal time expression, matched to an event description.

    Ambiguous bridge matches are bounded and the omitted matches are recorded.
    Returned pair ordering is bookkeeping and must not become temporal order.
    """
    if neighbors < 0 or pairs_per_event_budget < 1 or bridge_matches < 1:
        raise ValueError('Sparse timeline bounds must be positive')
    ids = [e['event_id'] for e in events]
    if len(ids) != len(set(ids)) or any(e.get('record_id') != record_id for e in events):
        raise ValueError('Sparse candidates require unique events belonging to one patient')
    positions = {s['segment_id']: i for i, s in enumerate(segments)}
    by_id = {e['event_id']: e for e in events}
    ordinal = {eid: i for i, eid in enumerate(ids)}
    def source_key(e):
        return min(((positions.get(s.get('segment_id'), len(segments)), s.get('start', 0))
                    for s in e.get('evidence', [])), default=(len(segments), 0)) + (ordinal[e['event_id']],)
    ordered = sorted(events, key=source_key)
    pairs = {}
    skipped = defaultdict(int)
    limit = pairs_per_event_budget * len(events)

    def propose(a, b, reason):
        if a == b:
            return
        a, b = sorted((a, b), key=ordinal.__getitem__)
        key = (a, b)
        if key in pairs:
            if reason not in pairs[key]:
                pairs[key].append(reason)
        elif len(pairs) < limit:
            pairs[key] = [reason]
        else:
            skipped[reason] += 1

    anchors = defaultdict(list)
    calendar = defaultdict(list)
    for e in ordered:
        eid = e['event_id']
        # Episode IDs are model candidates, not validated episode identities.
        if e.get('episode_id'):
            anchors[('episode_candidate', e['episode_id'])].append(eid)
        for span in e.get('evidence', []):
            anchors[('exact_source_span', digest(span))].append(eid)
        for time in e.get('times', []):
            value = time.get('calendar_value')
            if time.get('kind') == 'calendar' and value:
                calendar[value].append(eid)
                anchors[('literal_calendar_anchor', value)].append(eid)

    # Literal relative phrases can refer back across distant source windows.
    # Use an inverted token index to avoid an all-events scan for every event.
    words = defaultdict(set)
    normalized = {}
    def norm(value):
        return ' '.join(re.findall(r"[\w]+", value.casefold()))
    for e in events:
        normalized[e['event_id']] = norm(e.get('description', ''))
        for token in set(normalized[e['event_id']].split()):
            words[token].add(e['event_id'])
    bridge_audit = []
    for e in ordered:
        for time in e.get('times', []):
            phrase = time.get('text', '')
            match = re.search(r'\b(?:after|before|during|following|until)\s+(.+)', phrase, re.I)
            if not match or not any(phrase in s.get('quote', '') for s in time.get('evidence', [])):
                continue
            anchor = norm(re.sub(r'^(?:the|a|an)\s+', '', match[1].strip(), flags=re.I))
            tokens = anchor.split()
            if not tokens or len(anchor) < 4:
                continue
            candidates = set.intersection(*(words[t] for t in tokens)) - {e['event_id']}
            candidates = sorted((c for c in candidates if anchor in normalized[c]), key=ordinal.__getitem__)
            # Keep source-near matches first; neither proximity nor selection
            # resolves ambiguous repeated treatments into a true identity.
            candidates.sort(key=lambda c: (abs(source_key(by_id[c])[0] - source_key(e)[0]), ordinal[c]))
            chosen = candidates[:bridge_matches]
            for target in chosen:
                propose(e['event_id'], target, 'literal_relative_phrase_bridge')
            if candidates:
                bridge_audit.append({'event_id': e['event_id'], 'literal_time': phrase,
                    'anchor_phrase': anchor, 'selected_matches': chosen,
                    'unassessed_matches': candidates[bridge_matches:], 'identity_verified': False})

    # Consecutive explicitly dated representatives expose long-distance date
    # bridges. Precision and order are checked by the model, not inferred here.
    representatives = [calendar[k][0] for k in sorted(calendar)]
    for a, b in zip(representatives, representatives[1:]):
        propose(a, b, 'neighboring_literal_calendar_anchor')
    for members in anchors.values():
        members = list(dict.fromkeys(members))
        for a, b in zip(members, members[1:]):
            propose(a, b, 'shared_literal_or_episode_anchor')
    core_kinds = {'presentation', 'diagnosis', 'treatment', 'adverse_event', 'outcome', 'follow_up'}
    clinical = defaultdict(list)
    for i, e in enumerate(ordered):
        if e.get('kind') in core_kinds:
            clinical[e['kind']].append(i)
    for i, e in enumerate(ordered):
        for kind in sorted(clinical):
            members = clinical[kind]
            cut = bisect_left(members, i)
            near = members[max(0, cut-1):cut+2]
            # First non-self member on each side; this is retrieval only.
            before = [j for j in near if j < i]
            after = [j for j in near if j > i]
            for j in before[-1:] + after[:1]:
                propose(e['event_id'], ordered[j]['event_id'], 'source_near_clinical_anchor')
    for i, e in enumerate(ordered):
        for other in ordered[max(0, i-neighbors):i]:
            propose(e['event_id'], other['event_id'], 'source_local_neighbor')

    candidates = [{'pair': list(pair), 'retrieval_reasons': reasons} for pair, reasons in pairs.items()]
    total = len(events) * (len(events)-1) // 2
    audit = {'policy': POLICY, 'record_id': record_id, 'event_ids': ids,
             'event_registry_sha256': digest(events), 'source_segments_sha256': digest(segments),
             'parameters': {'neighbors': neighbors, 'pairs_per_event_budget': pairs_per_event_budget,
                            'bridge_matches': bridge_matches},
             'total_unordered_pairs': total, 'candidate_pairs': len(candidates),
             'unassessed_pairs': total-len(candidates), 'pair_budget': limit,
             'candidates': candidates, 'relative_bridges': bridge_audit,
             'budget_excluded_proposals_by_reason': dict(skipped),
             'unassessed_definition': 'All unordered event-ID pairs not listed in candidates; not negative labels.',
             'candidate_order_is_chronology': False, 'semantic_coverage_verified': False,
             'selection_is_relation_evidence': False}
    return [c['pair'] for c in candidates], audit
