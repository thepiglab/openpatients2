"""Whole-segment discovery fallback, with checked identity reconciliation.

No source block is silently removed. Window decisions and candidate-to-patient
mapping are retained; failed windows/reconciliation block a complete roster.
Model identity judgments remain unreviewed, even when literal citations pass.
"""
import asyncio
import json

from .article_tasks import SYSTEM, Citation
from .data import write_json
from .patient_context import check_patient_roster, discovery_messages, isolate_cited_cases
from .provenance import json_digest
from .staged_timeline import fits, replaced, splittable_failure
from .schemas import StrictModel


class IdentityGroup(StrictModel):
    candidate_ids: list[str]
    identity_evidence: list[Citation]


class Reconciliation(StrictModel):
    groups: list[IdentityGroup]
    unresolved_candidate_ids: list[str]
    limitations: list[str]


def unique(values):
    return list({json.dumps(v, sort_keys=True): v for v in values}.values())


def assemble(article, windows, mapping):
    mapping = Reconciliation.model_validate(mapping).model_dump()
    candidates = {f'{i}:{p["patient_id"]}': p for i, r in enumerate(windows) for p in r['data']['patients']}
    if not isinstance(mapping, dict) or set(mapping) != {'groups', 'limitations', 'unresolved_candidate_ids'}:
        raise ValueError('Reconciliation requires groups, limitations and unresolved_candidate_ids')
    if mapping['unresolved_candidate_ids'] != []:
        raise ValueError('Unresolved patient identities; do not invent a merge or duplicate patient')
    groups = mapping['groups']
    if not isinstance(groups, list) or not isinstance(mapping['limitations'], list):
        raise ValueError('Reconciliation groups/limitations must be lists')
    assigned = []
    patients = []
    positions = {s['segment_id']: i for i, s in enumerate(article['segments'])}
    for group in groups:
        if (not isinstance(group, dict) or set(group) != {'candidate_ids', 'identity_evidence'}
                or not isinstance(group['candidate_ids'], list) or not group['candidate_ids']):
            raise ValueError('Each group needs candidate_ids and identity_evidence')
        ids = group['candidate_ids']
        if any(not isinstance(k, str) or k not in candidates for k in ids):
            raise ValueError('Unknown reconciliation candidate')
        assigned.extend(ids)
        source = [candidates[k] for k in ids]
        species = {p['species'] for p in source} - {'unknown'}
        if len(species) > 1:
            raise ValueError('Cannot merge conflicting patient species')
        if len(source) > 1 and not group['identity_evidence']:
            raise ValueError('Merging candidates requires explicit source identity evidence')
        evidence = unique([e for p in source for e in p['identity_evidence']] + group['identity_evidence'])
        patients.append({**source[0], 'species': next(iter(species), 'unknown'),
            'identity_evidence': evidence,
            'source_segment_ids': sorted(set(s for p in source for s in p['source_segment_ids']) |
                                         {e['segment_id'] for e in evidence}, key=lambda s: positions.get(s, -1)),
            'attribution_limitations': unique([s for p in source for s in p['attribution_limitations']] +
                                             ['Window identity reconciliation is model-generated, not adjudicated.'])})
    if len(assigned) != len(set(assigned)) or set(assigned) != set(candidates):
        raise ValueError('Every discovery candidate must be assigned exactly once; no silent loss/duplication')
    segments = {s['segment_id']: s['text'] for s in article['segments']}
    def first(p):
        return min((positions.get(e['segment_id'], -1), segments.get(e['segment_id'], '').find(e['quote']))
                   for e in p['identity_evidence'])
    patients.sort(key=first)
    for i, patient in enumerate(patients, 1):
        patient['patient_id'] = f'p{i}'
    uncertain = any(r['data']['disposition']=='uncertain' or not r['data']['roster_complete'] for r in windows)
    aggregate = any(r['data']['disposition']=='aggregate_only' for r in windows)
    value = {'disposition': 'individual_cases' if patients else 'uncertain' if uncertain else
             'aggregate_only' if aggregate else 'no_patient_data',
             'reported_individual_count': None, 'roster_complete': not uncertain, 'patients': patients,
             'cited_cases': unique([c for r in windows for c in r['data'].get('cited_cases', [])]),
             'background_segment_ids': sorted({s for r in windows for s in r['data']['background_segment_ids']}),
             'unresolved_segment_ids': sorted({s for r in windows for s in r['data']['unresolved_segment_ids']}),
             'limitations': unique([s for r in windows for s in r['data']['limitations']] + mapping['limitations'] +
                 ['Every source segment scanned in bounded windows; cross-window identity/semantic coverage unverified.'])}
    return check_patient_roster(value, article)


async def discover(runner, article, replica):
    leaves = []
    async def scan(segments, number):
        view = {**article, 'segments': segments}
        messages = discovery_messages(view, refined=runner.config.refinement_policy=='source_aware',
                                      isolated=runner.config.isolate_secondary_cases)
        if len(segments) != len(article['segments']):
            messages[-1]['content'] += ('\nThis is a source window. Classify every supplied block; '
                'do not invent identities for ambiguous table rows. Global reconciliation follows. '
                'roster_complete applies only to this window. Keep ambiguous blocks unresolved.')
        async def split():
            mid = len(segments)//2
            groups = await asyncio.gather(scan(segments[:mid], number+'.0'), scan(segments[mid:], number+'.1'))
            return [r for group in groups for r in group]
        if len(segments)>1 and not await fits(runner, 'roster', messages, replica):
            return await split()
        quarantine = []
        def check(value):
            nonlocal quarantine
            if runner.config.isolate_secondary_cases:
                checked, quarantine = isolate_cited_cases(value, view)
                return checked
            return check_patient_roster(value, view)
        result = await runner.call('roster', messages, check,
            {'article_id': article['article_id'], 'source_batch': number,
             'focus_segment_ids': [s['segment_id'] for s in segments]}, replica, segments,
            split_on_length=len(segments)>1)
        if quarantine:
            result['secondary_case_quarantine'] = quarantine
            write_json(runner.output/'tasks'/(result['task_key']+'.json'), result)
        if len(segments)>1 and splittable_failure(result):
            replaced(runner, result)
            return await split()
        return [result]

    leaves = await scan(article['segments'], '0')
    coverage = {'policy': 'whole-segment-discovery/1', 'source_text_sha256': article['text_sha256'],
                'batches': [{k:r[k] for k in ('identity','status','errors')} for r in leaves],
                'all_batches_valid': all(r['status']=='valid' for r in leaves),
                'all_segments_attempted': {s['segment_id'] for s in article['segments']} ==
                    {s for r in leaves for s in r['identity']['focus_segment_ids']},
                'semantic_coverage_verified': False, 'identity_reconciliation': None}
    if len(leaves)==1:
        result = leaves[0]
    elif not coverage['all_batches_valid'] or not coverage['all_segments_attempted']:
        result = {'task':'roster', 'identity':{'article_id':article['article_id']}, 'status':'failed',
                  'data':None, 'attempts':[], 'errors':['incomplete_discovery_window_coverage']}
    else:
        candidates = {f'{i}:{p["patient_id"]}': p for i,r in enumerate(leaves) for p in r['data']['patients']}
        if len(candidates) <= 1:
            mapping = {'groups':[{'candidate_ids':[k], 'identity_evidence':[]} for k in candidates],
                       'unresolved_candidate_ids':[], 'limitations':[]}
            result = {'task':'roster', 'identity':{'article_id':article['article_id']}, 'status':'valid',
                      'data':assemble(article, leaves, mapping), 'attempts':[], 'errors':[]}
            coverage['identity_reconciliation'] = mapping
        else:
            ids = {e['segment_id'] for p in candidates.values() for e in p['identity_evidence']}
            payload = {k: {f:p[f] for f in ('label','species','identity_evidence','attribution_limitations')}
                       for k,p in candidates.items()}
            messages = [{'role':'system','content':SYSTEM}, {'role':'user','content':
                'Reconcile candidate patient mentions from separate windows of ONE article. Assign EVERY '
                'candidate ID exactly once. Merge only with source evidence of the SAME individual, never '
                'because age/sex/disease match. Keep distinct mother/fetus/case identities separate. '
                'For each multi-candidate group provide literal identity_evidence linking the mentions. '
                'If identity is uncertain, list those candidate IDs in unresolved_candidate_ids; do not '
                'invent a merge or duplicate patients. Do not add patients '
                'or medical facts. Return {"groups":[{"candidate_ids":["window:p1"], '
                '"identity_evidence":[{"segment_id":"...","quote":"..."}]}],'
                '"unresolved_candidate_ids":[],"limitations":[]}.' +
                '\nCANDIDATES:\n'+json.dumps(payload,ensure_ascii=False)+'\nSOURCE:\n'+
                json.dumps([s for s in article['segments'] if s['segment_id'] in ids],ensure_ascii=False)}]
            result = await runner.call('roster_reconcile', messages, lambda value: assemble(article,leaves,value),
                {'article_id':article['article_id']}, replica, article['segments'])
            result = {**result, 'task':'roster'}
            coverage['identity_reconciliation'] = {'task_key':result.get('task_key'),'status':result['status']}
    result['discovery_coverage'] = coverage
    if len(leaves)>1:
        runner.windowed_roster_articles.add(article['article_id'])
        runner.results.append(result)
        write_json(runner.output/'tasks'/(json_digest([article['article_id'],'assembled-roster'])+'.json'),result)
    write_json(runner.output/'coverage'/(article['article_id']+'-roster.json'),coverage)
    return result


def routed_patient_view(article, roster, patient):
    """Use windowed discovery's assignments; background is retained in source/audit."""
    from .staged_timeline import source_window
    focus = set(patient['source_segment_ids']) | set(roster['unresolved_segment_ids'])
    focus.update(s['segment_id'] for s in article['segments'] if s.get('kind')=='abstract')
    view = source_window(article, focus, patient)
    selected = {s['segment_id'] for s in view['segments']}
    audit = {'policy':'window-discovery-patient-routing/1', 'retained_segment_ids':sorted(selected),
             'omitted_segment_ids':[s['segment_id'] for s in article['segments'] if s['segment_id'] not in selected],
             'unresolved_blocks_retained':True, 'semantic_coverage_verified':False,
             'source_text_sha256':article['text_sha256'], 'omissions_require_discovery_review':True}
    return view, {**patient,'source_segment_ids':[s['segment_id'] for s in view['segments']]}, audit
