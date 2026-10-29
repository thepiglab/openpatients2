"""Bounded timeline outputs with explicit source/target coverage and assembly.

The Beam mode first tries complete source context. When it will not fit, it
uses whole cited/focus segments and adjacent context, without shortening any
segment. Every source segment remains an event focus. Relation target batches
cover every event pair once, allowing either direction; scoped context is recorded, not called complete
semantic coverage. A singleton that cannot fit still produces a failed receipt.
"""
import asyncio
import copy
import hashlib
import json
import httpx

from .longitudinal import PatientTimeline, audit_timeline, partial_timeline


def route_relation_scope(value, allowed_pairs, event_ids):
    """Exclude known, differently owned pairs without repairing their identity.

    All pairs have their own request. This is request routing, NOT acceptance of
    an extra edge as a fact. Original candidates stay in the ledger and audit.
    Unknown IDs, self-links, malformed edges and in-scope evidence errors still
    reach the checker and must fail normally.
    """
    routed = copy.deepcopy(value)
    excluded = []
    if not isinstance(routed, dict) or not isinstance(routed.get('edges'), list):
        return routed, excluded
    kept = []
    for edge in routed['edges']:
        a, b = (edge.get('from_event_id'), edge.get('to_event_id')) if isinstance(edge, dict) else (None, None)
        if (isinstance(a, str) and isinstance(b, str) and a != b
                and a in event_ids and b in event_ids and frozenset((a, b)) not in allowed_pairs):
            excluded.append({'reason': 'pair_owned_by_another_batch', 'candidate': edge,
                             'evidence_validated': False, 'accepted_as_fact': False})
        else:
            kept.append(edge)
    routed['edges'] = kept
    return routed, excluded


def evidence_ids(value, segments):
    """Locate literal evidence, including old citations that name a heading."""
    ids = set()
    if isinstance(value, dict):
        quote = value.get('quote')
        if quote:
            sid = value.get('segment_id', value.get('source_section'))
            exact = [s for s in segments if s['segment_id'] == sid and quote in s['text']]
            ids.update(s['segment_id'] for s in exact or segments if quote in s['text'])
        for child in value.values():
            ids.update(evidence_ids(child, segments))
    elif isinstance(value, list):
        for child in value:
            ids.update(evidence_ids(child, segments))
    return ids


def source_window(article, focus, patient):
    segments = article['segments']
    wanted = set(focus) | evidence_ids(patient.get('identity_evidence', []), segments)
    # Context neighbours help retain pronouns and table headings. They are not
    # proof of attribution or chronology, and never become new focus segments.
    for i, seg in enumerate(segments):
        if seg['segment_id'] in focus:
            wanted.update(s['segment_id'] for s in segments[max(0, i-1):i+2])
    return {**article, 'segments': [s for s in segments if s['segment_id'] in wanted]}


async def fits(runner, task, messages, replica):
    try:
        async with runner.slots[replica]:
            count, _, maximum = await runner.token_count(runner.clients[replica],
                runner.prepared_messages(task, messages))
        # Leave the full initial output budget; call() remains the final
        # authority and accounts for repair prompts and retry token budgets.
        return count + runner.config.context_safety_tokens + runner.config.max_output_tokens <= min(runner.context, maximum)
    except (httpx.HTTPError, ValueError, TypeError):
        return True  # call() records the actual tokenizer/transport failure


def splittable_failure(result):
    text = json.dumps(result.get('errors', []))
    return 'context_overflow' in text or 'incomplete_finish:length' in text


def replaced(runner, result):
    # Keep failed attempts/reasoning in the aggregate ledger but do not merge
    # their partial payload or count them as an uncovered leaf request.
    result['superseded_by_split'] = True
    if result.get('task_key'):
        from .data import write_json
        write_json(runner.output / 'tasks' / (result['task_key'] + '.json'), result)
    return {**result, 'data': None, 'status': 'replaced_by_split'}


async def extract(runner, article, patient, facts, rows, identity, replica, sources):
    from .pilot_extract import timeline_messages, timeline_relations_wire_schema
    rid = identity['record_id']
    shell = {'schema_version': 'patient-timeline/2', 'record_id': rid,
             'events': [], 'edges': [], 'limitations': []}
    segments = article['segments']
    bounded = getattr(runner.config, 'timeline_bounded_context', False)
    width = runner.config.timeline_segment_batch_size
    batches = [segments[i:i + width] for i in range(0, len(segments), width)] or [[]]

    def check_graph(value):
        graph = PatientTimeline.model_validate(value)
        if graph.record_id != rid:
            raise ValueError('timeline_wrong_patient')
        audit = audit_timeline(graph, sources, facts)
        if not audit['structural_source_gates_passed']:
            raise ValueError('; '.join(i['code'] for i in audit['issues'] if i['severity'] == 'block'))
        return graph.model_dump()

    async def events(number, batch):
        focus = {s['segment_id'] for s in batch}
        def build(view):
            local_rows = rows
            local_facts = facts
            if view is not article:
                included = {s['segment_id'] for s in view['segments']}
                local_rows = [r for r in rows if evidence_ids(r.get('candidate', {}), segments) & included]
                local_facts = {r['review_id']: facts[r['review_id']] for r in local_rows if r['review_id'] in facts}
            messages = timeline_messages(view, patient, rid, local_facts, local_rows, refined=True)
            messages[-1]['content'] += ('\nEVENT STAGE: Return events only, with edges=[]. '
            'Review every focus segment for this patient; use the complete source above for context. '
            'Emit an event only when the FIRST segment in article order that directly documents '
            'that event is in FOCUS_SEGMENTS, and make that segment its first evidence entry. '
            'Do not repeat events primarily documented in other batches. Return events=[] when '
            'these focus segments contain no events for this patient. Preserve source-backed '
            'events even when no fact ID exists. kind=plan cannot have occurrence=occurred; '
            'documented performed monitoring is a test/follow_up/other event, not a completed plan. '
            '\nFOCUS_SEGMENTS:\n' + json.dumps(sorted(focus)))
            if view is not article:
                messages[-1]['content'] += ('\nSOURCE WINDOW: only the listed whole segments are supplied. '
                    'Other focus batches cover the rest of the article. Do not guess missing context; '
                    'record unresolved attribution or timing in limitations.')
            return messages

        view = article
        messages = build(view)
        if bounded and (getattr(runner.config, 'timeline_prefer_windows', False)
                        or not await fits(runner, 'timeline_events', messages, replica)):
            view = source_window(article, focus, patient)
            messages = build(view)
            if len(batch) > 1 and not await fits(runner, 'timeline_events', messages, replica):
                mid = len(batch) // 2
                return (await events(str(number)+'.0', batch[:mid]) + await events(str(number)+'.1', batch[mid:]))

        def checker(value):
            graph = check_graph(value)
            if graph['edges']:
                raise ValueError('Event stage requires edges=[]')
            if any(e['evidence'][0]['segment_id'] not in focus for e in graph['events']):
                raise ValueError('Event first evidence must belong to its focus segment batch')
            return graph

        result = await runner.call('timeline_events', messages, checker,
            {**identity, 'timeline_stage': 'events', 'batch': number, 'focus_segments': sorted(focus),
             'source_scope': 'whole_article' if view is article else 'whole_segment_window',
             'included_segment_ids': [s['segment_id'] for s in view['segments']]},
            replica, segments, partial_builder=lambda v: partial_timeline(v, sources, facts, rid),
            split_on_length=bounded and len(batch) > 1 and getattr(runner.config, 'timeline_scope_repair', False))
        if bounded and len(batch) > 1 and splittable_failure(result):
            mid = len(batch) // 2
            return [replaced(runner, result)] + await events(str(number)+'.0', batch[:mid]) + await events(str(number)+'.1', batch[mid:])
        return [result]

    stages = [s for group in await asyncio.gather(*(events(i, batch) for i, batch in enumerate(batches))) for s in group]
    graph = copy.deepcopy(shell)
    seen = set()
    for stage in stages:
        for event in (stage.get('data') or {}).get('events', []):
            # Collapse only byte-equivalent content, never semantic near-duplicates.
            content = {k: v for k, v in event.items() if k != 'event_id'}
            key = json.dumps(content, sort_keys=True, ensure_ascii=False)
            if key in seen:
                continue
            seen.add(key)
            graph['events'].append({**event, 'event_id': 'e' + str(len(graph['events']) + 1)})
        graph['limitations'].extend((stage.get('data') or {}).get('limitations', []))

    async def relations(number, focus, targets):
        pairs = [[a, b] for a in focus for b in targets if positions[a] < positions[b]] if bounded else []
        if bounded and not pairs:
            return []
        allowed_pairs = {frozenset(pair) for pair in pairs}
        schema = timeline_relations_wire_schema()
        relevant = [e for e in graph['events'] if e['event_id'] in set(focus + targets)]
        registry = [{k: e[k] for k in ('event_id', 'description', 'kind', 'occurrence', 'evidence', 'times')}
                    for e in relevant]
        def build(view):
            local_facts = {fid: facts[fid] for e in relevant for fid in e['fact_ids'] if fid in facts} if bounded else facts
            if getattr(runner.config, 'timeline_scope_repair', False):
                # Do not inherit the whole-timeline prompt's request to emit
                # events/all relations. The registry is context, not a request
                # to enumerate its Cartesian product.
                from .article_tasks import SYSTEM
                from .patient_context import minimal_segments
                messages = [{'role': 'system', 'content': SYSTEM}, {'role': 'user', 'content':
                    'SOURCE_JSON:\n' + json.dumps({'article_id': article['article_id'],
                        'patient': patient, 'segments': minimal_segments(view['segments'])}, ensure_ascii=False) +
                    '\nTASK:\nDecide only source-supported temporal relations for the listed patient and candidate pairs. '
                    'Each event is an unreviewed candidate. Evidence must support the patient and BOTH endpoints. '
                    'Do not copy unrelated patients, infer order from clinical practice, or equate a plan with a performed action. '
                    'Omit unknown/unsupported relations rather than enumerating unknown edges. '
                    'Evidence supplies only segment_id and an exact unique quote; code resolves the '
                    'source hash and offsets. A time phrase must be literal in its own cited evidence. '}]
            else:
                messages = timeline_messages(view, patient, rid, local_facts, [], refined=True)
                messages[-1]['content'] = messages[-1]['content'].split('\nSCHEMA:\n')[0]
            ownership = ('Consider only the CANDIDATE_PAIRS, each an UNORDERED pair. Either event may be '
                'from_event_id; choose direction from source evidence, including during. Pair ordering is '
                'only bookkeeping and conveys NO chronology. ' if bounded else
                'For each FOCUS_EVENT_ID consider relations to each TARGET_EVENT_ID. '
                'Every edge from_event_id must be a focus ID and to_event_id a target ID. ')
            messages[-1]['content'] += ('\nRELATION STAGE: Return only edges and limitations matching the schema. '
            'Do not rewrite or return events. ' + ownership +
            'Return only directly supported relations; an empty edges list is valid. '
            'Do not emit an edge for every candidate pair. Use offset=null unless the '
            'quoted relation evidence contains the literal duration expression. '
            'Do not infer order from IDs, source paragraph order or medical plausibility. '
            'Use after where needed; cite primary source evidence for the relation and both events. '
            '\nEVENT_REGISTRY:\n' + json.dumps(registry, ensure_ascii=False) +
            '\nFOCUS_EVENT_IDS:\n' + json.dumps(focus) + '\nTARGET_EVENT_IDS:\n' + json.dumps(targets) +
            ('\nCANDIDATE_PAIRS:\n' + json.dumps(pairs) if bounded else '') +
            '\nSCHEMA:\n' + json.dumps(schema))
            if view is not article:
                messages[-1]['content'] += '\nSource is a window of whole evidence/identity/adjacent segments. Leave unsupported relations unresolved.'
            return messages
        view = article
        messages = build(view)
        if bounded and (getattr(runner.config, 'timeline_prefer_windows', False)
                        or not await fits(runner, 'timeline_relations', messages, replica)):
            view = source_window(article, evidence_ids(relevant, segments), patient)
            messages = build(view)
            if (len(focus) > 1 or len(targets) > 1) and not await fits(runner, 'timeline_relations', messages, replica):
                return await split_relations(number, focus, targets)

        def checker(value):
            if not isinstance(value, dict) or set(value) != {'edges', 'limitations'}:
                raise ValueError('Relation stage returns only edges and limitations')
            if bounded and any(frozenset((e.get('from_event_id'), e.get('to_event_id'))) not in allowed_pairs for e in value['edges']):
                raise ValueError('Relation pair is outside its candidate batch')
            if not bounded and any(e.get('from_event_id') not in focus for e in value['edges']):
                raise ValueError('Relation source event is outside its focus batch')
            if not bounded and any(e.get('to_event_id') not in targets for e in value['edges']):
                raise ValueError('Relation target event is outside its target batch')
            checked = check_graph({**graph, **value})
            return {k: checked[k] for k in ('edges', 'limitations')}

        result = await runner.call('timeline_relations', messages, checker,
            {**identity, 'timeline_stage': 'relations', 'batch': number, 'focus_event_ids': focus,
             'target_event_ids': targets, 'source_scope': 'whole_article' if view is article else 'whole_segment_window',
             'candidate_event_pairs': pairs if bounded else None,
             'included_segment_ids': [s['segment_id'] for s in view['segments']]}, replica, segments,
            candidate_scope=(lambda value: route_relation_scope(value, allowed_pairs, set(positions)))
                if bounded and getattr(runner.config, 'timeline_scope_repair', False) else None,
            split_on_length=bounded and (len(focus) > 1 or len(targets) > 1)
                and getattr(runner.config, 'timeline_scope_repair', False))
        if bounded and (len(focus) > 1 or len(targets) > 1) and splittable_failure(result):
            return [replaced(runner, result)] + await split_relations(number, focus, targets)
        return [result]

    async def split_relations(number, focus, targets):
        if len(targets) >= len(focus) and len(targets) > 1:
            mid = len(targets) // 2
            return await relations(str(number)+'.0', focus, targets[:mid]) + await relations(str(number)+'.1', focus, targets[mid:])
        mid = len(focus) // 2
        return await relations(str(number)+'.0', focus[:mid], targets) + await relations(str(number)+'.1', focus[mid:], targets)

    width = runner.config.timeline_relation_batch_size
    ids = [e['event_id'] for e in graph['events']]
    positions = {eid: i for i, eid in enumerate(ids)}
    target_width = runner.config.timeline_target_batch_size if bounded else max(1, len(ids))
    links = [s for group in await asyncio.gather(*(relations(f'{i // width}.{j // target_width}',
        ids[i:i + width], ids[j:j + target_width]) for i in range(0, len(ids), width)
        for j in range(i if bounded else 0, len(ids), target_width))) for s in group]
    stages.extend(links)
    seen = set()
    for stage in links:
        for edge in (stage.get('data') or {}).get('edges', []):
            key = json.dumps({k: v for k, v in edge.items() if k != 'edge_id'}, sort_keys=True)
            if key not in seen:
                seen.add(key)
                graph['edges'].append({**edge, 'edge_id': 'edge' + str(len(graph['edges']) + 1)})
        graph['limitations'].extend((stage.get('data') or {}).get('limitations', []))
    errors = [f'{s["task"]} batch {s["identity"]["batch"]}: {s["status"]}' for s in stages if s['status'] not in {'valid', 'replaced_by_split'}]
    quarantine = []
    try:
        graph = check_graph(graph)
    except (ValueError, TypeError, KeyError) as exc:
        errors.append('assembled_timeline_invalid:' + str(exc))
        graph, quarantine = partial_timeline(graph, sources, facts, rid)
    if graph is not None and errors:
        graph['limitations'].append('Some staged timeline calls failed or were partial; coverage is incomplete.')
    result = {'task': 'timeline_v2', 'identity': identity, 'arm': runner.arm, 'replica': replica,
        'status': 'valid' if not errors else 'partial' if graph is not None else 'failed',
        'data': graph, 'errors': errors, 'quarantine': quarantine,
        'attempts': [a for s in stages for a in s['attempts']],
        'stage_coverage': [{'task': s['task'], 'identity': s['identity'], 'status': s['status']} for s in stages],
        'context_policy': ('whole_segment_windows' if bounded and getattr(runner.config, 'timeline_prefer_windows', False)
                           else 'full_source_then_whole_segment_windows' if bounded else 'full_source'),
        'relation_candidate_policy': 'each_unordered_pair_once_either_edge_direction' if bounded else 'all_events',
        'semantic_coverage_verified': False,
        'semantic_review': 'unreviewed', 'clinical_accuracy_verified': False,
        'refinement_policy': runner.config.refinement_policy}
    key = hashlib.sha256((rid + ':staged-timeline/1').encode()).hexdigest()
    (runner.output / 'tasks' / (key + '.json')).write_text(json.dumps(result, ensure_ascii=False))
    runner.results.append(result)
    return result
