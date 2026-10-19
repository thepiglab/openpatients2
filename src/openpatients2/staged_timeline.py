"""Bounded timeline outputs, with full source context and deterministic assembly."""
import asyncio
import copy
import hashlib
import json

from .longitudinal import PatientTimeline, audit_timeline, partial_timeline


async def extract(runner, article, patient, facts, rows, identity, replica, sources):
    from .pilot_extract import timeline_messages, timeline_relations_wire_schema
    rid = identity['record_id']
    shell = {'schema_version': 'patient-timeline/2', 'record_id': rid,
             'events': [], 'edges': [], 'limitations': []}
    segments = article['segments']
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
        messages = timeline_messages(article, patient, rid, facts, rows, refined=True)
        messages[-1]['content'] += ('\nEVENT STAGE: Return events only, with edges=[]. '
            'Review every focus segment for this patient; use the complete source above for context. '
            'Emit an event only when the FIRST segment in article order that directly documents '
            'that event is in FOCUS_SEGMENTS, and make that segment its first evidence entry. '
            'Do not repeat events primarily documented in other batches. Return events=[] when '
            'these focus segments contain no events for this patient. Preserve source-backed '
            'events even when no fact ID exists. kind=plan cannot have occurrence=occurred; '
            'documented performed monitoring is a test/follow_up/other event, not a completed plan. '
            '\nFOCUS_SEGMENTS:\n' + json.dumps(sorted(focus)))

        def checker(value):
            graph = check_graph(value)
            if graph['edges']:
                raise ValueError('Event stage requires edges=[]')
            if any(e['evidence'][0]['segment_id'] not in focus for e in graph['events']):
                raise ValueError('Event first evidence must belong to its focus segment batch')
            return graph

        return await runner.call('timeline_events', messages, checker,
            {**identity, 'timeline_stage': 'events', 'batch': number, 'focus_segments': sorted(focus)},
            replica, segments, partial_builder=lambda v: partial_timeline(v, sources, facts, rid))

    stages = list(await asyncio.gather(*(events(i, batch) for i, batch in enumerate(batches))))
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

    async def relations(number, focus):
        messages = timeline_messages(article, patient, rid, facts, [], refined=True)
        # Keep primary source intact, but request only the relation delta.
        schema = timeline_relations_wire_schema()
        messages[-1]['content'] = messages[-1]['content'].split('\nSCHEMA:\n')[0]
        registry = [{k: e[k] for k in ('event_id', 'description', 'kind', 'occurrence', 'evidence', 'times')}
                    for e in graph['events']]
        messages[-1]['content'] += ('\nRELATION STAGE: Return only edges and limitations matching the schema. '
            'Do not rewrite or return events. For each FOCUS_EVENT_ID consider source-supported relations '
            'to any event in the registry. Every edge from_event_id must be a focus ID. '
            'Do not infer order from IDs, source paragraph order or medical plausibility. '
            'Use after where needed; cite primary source evidence for the relation and both events. '
            '\nEVENT_REGISTRY:\n' + json.dumps(registry, ensure_ascii=False) +
            '\nFOCUS_EVENT_IDS:\n' + json.dumps(focus) + '\nSCHEMA:\n' + json.dumps(schema))

        def checker(value):
            if not isinstance(value, dict) or set(value) != {'edges', 'limitations'}:
                raise ValueError('Relation stage returns only edges and limitations')
            if any(e.get('from_event_id') not in focus for e in value['edges']):
                raise ValueError('Relation source event is outside its focus batch')
            checked = check_graph({**graph, **value})
            return {k: checked[k] for k in ('edges', 'limitations')}

        return await runner.call('timeline_relations', messages, checker,
            {**identity, 'timeline_stage': 'relations', 'batch': number, 'focus_event_ids': focus}, replica, segments)

    width = runner.config.timeline_relation_batch_size
    ids = [e['event_id'] for e in graph['events']]
    links = await asyncio.gather(*(relations(i // width, ids[i:i + width]) for i in range(0, len(ids), width)))
    stages.extend(links)
    seen = set()
    for stage in links:
        for edge in (stage.get('data') or {}).get('edges', []):
            key = json.dumps({k: v for k, v in edge.items() if k != 'edge_id'}, sort_keys=True)
            if key not in seen:
                seen.add(key)
                graph['edges'].append({**edge, 'edge_id': 'edge' + str(len(graph['edges']) + 1)})
        graph['limitations'].extend((stage.get('data') or {}).get('limitations', []))
    errors = [f'{s["task"]} batch {s["identity"]["batch"]}: {s["status"]}' for s in stages if s['status'] != 'valid']
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
        'semantic_review': 'unreviewed', 'clinical_accuracy_verified': False,
        'refinement_policy': runner.config.refinement_policy}
    key = hashlib.sha256((rid + ':staged-timeline/1').encode()).hexdigest()
    (runner.output / 'tasks' / (key + '.json')).write_text(json.dumps(result, ensure_ascii=False))
    runner.results.append(result)
    return result
