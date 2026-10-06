"""Source-bound development metrics for complete delivered patient bundles.

These finite probes are not a medical accuracy estimator. Identity alignment
uses only reviewed source identity, never the clinical answers being scored.
"""
from copy import deepcopy
import json
import re
from typing import get_args, get_origin, Literal
from .corpus_fidelity import evaluate, _rows, _section, _figure_arm
from .fidelity import matches, without_evidence


def subset_reference(reference, ids):
    ids = set(ids)
    result = deepcopy(reference)
    result['checks'] = [c for c in result['checks'] if c['record_id'].split(':')[0] in ids]
    for key in ('articles', 'figure_checks'):
        result[key] = [r for r in result.get(key, []) if r['article_id'] in ids]
    result['bundle_gold'] = {**result.get('bundle_gold', {}), **{
        key: [r for r in result.get('bundle_gold', {}).get(key, []) if r['record_id'].split(':')[0] in ids]
        for key in ('timelines', 'summary')}}
    result['bundle_gold']['pixels']=[r for r in result['bundle_gold'].get('pixels',[]) if r['article_id'] in ids]
    # Never hand the adapter even identifiers from the withheld split.
    result.pop('optimization_splits', None)
    return result


def validate_bundle_gold(reference, articles):
    from .corpus_fidelity import validate_reference_sources
    validate_reference_sources(reference, articles)
    from .schemas import TASK_MODELS
    from .longitudinal import PatientTimeline
    for check in reference['checks']:
        model=TASK_MODELS.get(check['task'],PatientTimeline if check['task']=='timeline_v2' else None)
        if model is None: raise ValueError('Unknown benchmark task')
        collection=check.get('collection')
        if collection:
            if collection not in model.model_fields: raise ValueError('Unknown benchmark collection')
            model=get_args(model.model_fields[collection].annotation)[0]
        def validate_pattern(pattern):
            if set(pattern)=={'one_of'}:
                if not pattern['one_of']: raise ValueError('Empty benchmark alternatives')
                for alternative in pattern['one_of']: validate_pattern(alternative)
                return
            for key,value in pattern.items():
                if key not in model.model_fields: raise ValueError('Impossible benchmark field: '+key)
                annotation=model.model_fields[key].annotation
                values=value.get('one_of',[]) if isinstance(value,dict) else [value]
                if get_origin(annotation) is Literal and any(isinstance(v,str) and v not in get_args(annotation) for v in values):
                    raise ValueError('Impossible benchmark enum: '+key)
        validate_pattern(check['pattern'])
    by = {a['article_id']: a for a in articles}
    for row in reference.get('articles',[]):
        if type(row['expected_count']) is not int or row['expected_count']!=len(row.get('identities',[])) or len(row['species'])!=row['expected_count']:
            raise ValueError('Incomplete source-checked identity labels')
    for row in reference.get('bundle_gold',{}).get('pixels',[]):
        a=by[row['article_id']]
        if row['text_sha256']!=a['text_sha256'] or row['figure_id'] not in {f['figure_key'] for f in a['figures']} or not re.fullmatch('[0-9a-f]{64}',row['image_sha256']):
            raise ValueError('Invalid source/pixel binding in gold')
    for row in [*reference.get('bundle_gold', {}).get('timelines', []),
                *reference.get('bundle_gold', {}).get('summary', [])]:
        article = by[row['record_id'].split(':')[0]]
        if row['text_sha256'] != article['text_sha256']: raise ValueError('Bundle gold source changed')
        spans = {s['segment_id']: s['text'] for s in article['segments']}
        entries = row.get('nodes', []) + row.get('edges', []) if 'nodes' in row else [row]
        for entry in entries:
            if not entry.get('source') or any(not e.get('quote') or e['quote'] not in spans.get(e['segment_id'], '') for e in entry['source']):
                raise ValueError('Nonliteral bundle gold evidence')
        if 'nodes' in row:
            ids = [n['id'] for n in row['nodes']]
            if len(ids) != len(set(ids)) or any({e['from'], e['to']} - set(ids) for e in row['edges']):
                raise ValueError('Invalid temporal gold references')
    splits = reference.get('optimization_splits')
    if splits:
        groups = [set(splits[k]) for k in ('train', 'validation', 'test')]
        if any(groups[i] & groups[j] for i in range(3) for j in range(i)) or set.union(*groups) != set(by):
            raise ValueError('Incomplete or overlapping article splits')
    return True


def identity_overlap(actual, expected):
    for a in actual:
        for e in expected:
            if a.get('segment_id') != e.get('segment_id'): continue
            aq, eq = a.get('quote', ''), e.get('quote', '')
            if min(len(aq), len(eq)) >= 32 and (aq in eq or eq in aq): return True
    return False


def align_patients(reference, patients, discovery=None):
    rows = _rows(patients, 'patients'); aligned = []; receipt = []
    rosters = {r['article_id']:r for r in _rows(discovery,'articles')} if discovery is not None else {}
    for gold in reference.get('articles', []):
        originals = [p for p in rows if p.get('source', {}).get('article_source', {}).get('article_id') == gold['article_id']]
        if gold.get('evaluation_status')=='unadjudicated':
            receipt.append({'article_id':gold['article_id'],'expected':gold['expected_count'],
                'produced':len(originals),'identity_matched':0,'unmapped_or_ambiguous':len(originals),
                'negative_case_correct':None,'id_mapping':{},'excluded_from_identity_score':True})
            continue
        expected = gold.get('identities', [])
        choices = []
        for p in originals:
            source = p.get('source', {})
            evidence = source.get('patient_target', {}).get('identity_evidence', [])
            bound = source.get('article_source', {}).get('text_sha256') == gold['text_sha256']
            choices.append([i for i, e in enumerate(expected) if bound and identity_overlap(evidence, e['evidence'])
                and source.get('patient_target',{}).get('species')==gold['species'][i]])
        assigned = {}
        for index, candidates in enumerate(choices):
            if len(candidates) == 1 and sum(candidates[0] in c for c in choices) == 1:
                assigned[index] = expected[candidates[0]]['patient_id']
                p = deepcopy(originals[index]); rid = gold['article_id'] + ':' + assigned[index]
                p['source']['record_id'] = rid
                aligned.append(p)
        roster=rosters.get(gold['article_id'],{})
        negative_valid=(roster.get('status')=='valid' and roster.get('text_sha256')==gold['text_sha256']
            and isinstance(roster.get('roster',{}),dict) and roster['roster'].get('patients')==[])
        receipt.append({'article_id': gold['article_id'], 'expected': gold['expected_count'],
            'produced': len(originals), 'identity_matched': len(assigned),
            'unmapped_or_ambiguous': len(originals) - len(assigned),
            'negative_case_correct': negative_valid and not originals if not expected else None,
            'id_mapping': {originals[i]['source']['record_id']: gold['article_id'] + ':' + pid for i, pid in assigned.items()}})
    return aligned, receipt


def reachability(graph):
    edges = {e['event_id']: set() for e in graph.get('events', [])}
    for e in graph.get('edges', []):
        left, right = e['from_event_id'], e['to_event_id']
        if left not in edges or right not in edges: continue
        if e['relation'] == 'before': edges[left].add(right)
        elif e['relation'] == 'after': edges[right].add(left)
        elif e['relation'] == 'same_time':
            # Simultaneous events do not establish a strict ordering.
            continue
    for start in edges:
        seen = set(); pending = list(edges[start])
        while pending:
            node = pending.pop()
            if node in seen: continue
            seen.add(node); pending.extend(edges[node] - seen)
        edges[start] = seen
    return edges


def timeline_probes(gold, graph):
    graph = graph or {}; mapping = {}; ambiguous = []
    for node in gold['nodes']:
        candidates = [e['event_id'] for e in graph.get('events', [])
            if matches(node['pattern'], without_evidence(e)) and
            any(s.get('segment_id') == t['segment_id'] for s in e.get('evidence', []) for t in node['source'])]
        if len(candidates) == 1: mapping[node['id']] = candidates[0]
        elif candidates: ambiguous.append(node['id'])
    # A single broad event cannot stand in for several different encounters.
    duplicates = {v for v in mapping.values() if list(mapping.values()).count(v) > 1}
    mapping = {k: v for k, v in mapping.items() if v not in duplicates}
    paths = reachability(graph); correct = reversed_order = 0
    for edge in gold['edges']:
        left, right = mapping.get(edge['from']), mapping.get(edge['to'])
        if left is None or right is None: continue
        correct += right in paths.get(left, set()) and left not in paths.get(right, set())
        reversed_order += left in paths.get(right, set())
    return {'record_id': gold['record_id'], 'nodes': len(gold['nodes']), 'nodes_matched': len(mapping),
        'relations': len(gold['edges']), 'relations_matched': correct, 'reversed_relations': reversed_order,
        'missing_nodes': [n['id'] for n in gold['nodes'] if n['id'] not in mapping], 'ambiguous_nodes': ambiguous}


def score_bundle(reference, patients, *, visual_rows=None, discovery=None, pixel_rows=None):
    aligned, identities = align_patients(reference, patients,discovery)
    scored = evaluate(reference, aligned, visual_rows=visual_rows)
    by = {p['source']['record_id']: p for p in aligned}
    timelines = [timeline_probes(g, _section(by.get(g['record_id'], {}), 'timeline_v2'))
                 for g in reference.get('bundle_gold', {}).get('timelines', [])]
    summaries = []
    for g in reference.get('bundle_gold', {}).get('summary', []):
        value = _section(by.get(g['record_id'], {}), 'summary')
        text = json.dumps(without_evidence(value), ensure_ascii=False) if value else ''
        summaries.append({'record_id': g['record_id'], 'matched': matches(g['pattern'], text), 'available': bool(value)})
    clinical = scored['summary']['delivered']
    parts = {'clinical': (clinical['matched'], clinical['required'], .60),
        'identity': (sum(r['identity_matched'] if r['expected'] else r['negative_case_correct'] for r in identities
                         if not r.get('excluded_from_identity_score')),
                     sum(r['expected'] or 1 for r in identities if not r.get('excluded_from_identity_score')), .15),
        'temporal_nodes': (sum(r['nodes_matched'] for r in timelines), sum(r['nodes'] for r in timelines), .08),
        'temporal_relations': (sum(r['relations_matched'] for r in timelines), sum(r['relations'] for r in timelines), .08),
        'summary': (sum(r['matched'] for r in summaries), len(summaries), .04)}
    figure = {}
    # See evaluate's separate figure arms; ownership is scored on generated tasks.
    if reference.get('figure_checks'):
        mapping = {k: v.split(':')[-1] for r in identities for k, v in r['id_mapping'].items()}
        media = deepcopy(_rows(visual_rows, 'tasks')) if visual_rows is not None else []
        # Match delivered ownership: usable pixel/joint output overrides the
        # caption-only assignment. Filesystem enumeration must not select it.
        media = sorted((r for r in media if r.get('data') is not None),
            key=lambda r: {'figure_attribution':0,'pixel_attribution':1,'joint_figure':2}.get(r.get('task'),0))
        for row in media:
            aid = (row.get('identity') or {}).get('article_id')
            data = row.get('data') or {}
            if 'attribution' in data: data = data['attribution']; row['data'] = data
            for a in data.get('assignments', []):
                a['patient_ids'] = [mapping.get(aid + ':' + pid, '__unmapped__' + pid) for pid in a['patient_ids']]
        figure = _figure_arm(reference, media)
        fs = figure['summary']; parts['figure_ownership'] = (fs['matched'], fs['required'], .03)
    pixels=[]
    annotations={(r['identity']['article_id'],r['identity']['figure_id']):r for r in _rows(pixel_rows,'figures')} if pixel_rows is not None else {}
    for gold in reference.get('bundle_gold',{}).get('pixels',[]):
        row=annotations.get((gold['article_id'],gold['figure_id']),{})
        available=row.get('pixels_inspected') is True and row.get('pixel_provenance',{}).get('sha256')==gold['image_sha256']
        panels=(row.get('visual',{}).get('data') or {}).get('panels',[]) if available else []
        labels=[(p.get('panel') or '').strip('() ').upper() for p in panels]
        matched=(bool(panels) and len(labels)==len(set(labels)) and set(labels)==set(gold['panel_labels'])
            and all(p.get('image_kind')==gold['image_kind'] and p.get('has_chart') is gold['has_chart'] for p in panels))
        pixels.append({'article_id':gold['article_id'],'figure_id':gold['figure_id'],'available':available,
            'matched':matched,'actual_panel_labels':labels,'free_description_accuracy_adjudicated':False})
    if pixels:parts['pixel_inventory']=(sum(p['matched'] for p in pixels),len(pixels),.02)
    active = {k: v for k, v in parts.items() if v[1]}
    raw = sum(hit / total * weight for hit, total, weight in active.values()) / sum(v[2] for v in active.values()) if active else 0.
    penalty = min(.5, .1 * clinical['forbidden_violations'] + .05 * sum(t['reversed_relations'] for t in timelines)
                  + .05 * sum(r['unmapped_or_ambiguous'] for r in identities
                              if not r.get('excluded_from_identity_score')))
    return {'score': max(0., raw - penalty), 'clinical': clinical, 'parts': {
        k: {'matched': h, 'required': n, 'weight': w} for k, (h, n, w) in active.items()},
        'identities': identities, 'timelines': timelines, 'summaries': summaries, 'figures': figure,'pixels':pixels,
        'missing_clinical': [c['id'] for c in scored['checks'] if c['kind'] == 'required' and not c['delivered']['matched']],
        'forbidden_hits': [c['id'] for c in scored['checks'] if c['kind'] == 'forbidden' and c['delivered']['matched']],
        'notice': 'Finite source-reviewed development probes; no self-judge reward or exhaustive clinical accuracy claim.'}
