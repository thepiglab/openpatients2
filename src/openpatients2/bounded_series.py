"""Measurement requests partitioned by table rows, with explicit cell coverage.

Never rewrite a value, time or unit. Headers/legends are context, not new focus
values. Failed leaves remain visible and successful leaves can still be saved.
"""
import copy
from collections import defaultdict

from . import ehr_tasks
from .measurement_series import validate_series
from .staged_timeline import evidence_ids, fits, replaced, source_window, splittable_failure


def cell_groups(draft):
    groups = defaultdict(list)
    for cell in draft['cells']:
        if cell['role'] in {'column_header', 'context'}:
            continue
        key = (cell.get('table_id'), cell.get('supplement_id'), cell.get('transcription_id'),
               cell.get('segment_id') or cell['cell_id'].rsplit('-c', 1)[0])
        groups[key].append(cell)
    return list(groups.values())


async def extract(runner, article, roster, annotation, draft, transcriptions):
    leaves = []
    context_cells = [c for c in draft['cells'] if c['role'] in {'column_header', 'context'}]
    groups = cell_groups(draft)

    async def batch(number, rows):
        focus = [c for row in rows for c in row]
        focus_ids = {c['cell_id'] for c in focus}
        tables = {c.get('table_id') for c in focus}
        # Include original legends and table headers. A null table context can
        # qualify any row, so it is retained rather than guessed away.
        context = [c for c in context_cells if c.get('table_id') is None or c.get('table_id') in tables]
        local_draft = {**draft, 'cells': context + focus}
        view = article
        messages = ehr_tasks.series_messages(view, roster, local_draft)
        if not await fits(runner, 'measurement_series_extraction', messages, 0):
            identities = [p['identity_evidence'] for p in roster['patients']]
            wanted = {c['segment_id'] for c in local_draft['cells'] if c.get('segment_id')}
            wanted |= evidence_ids(identities, article['segments'])
            # Whole narrative blocks explicitly referring to these table IDs
            # retain ownership/legend context in addition to adjacent blocks.
            wanted.update(s['segment_id'] for s in article['segments']
                          if any(set(r.get('target_ids', [])) & (tables - {None})
                                 for r in s.get('cross_references', [])))
            view = source_window(article, wanted, {})
            messages = ehr_tasks.series_messages(view, roster, local_draft)
        messages[-1]['content'] += ('\nOnly the supplied rows are in scope. Headers and legends are context. '
            'Select only supported serial measurements; series=[] is valid. Do not turn cohort rows into '
            'patients. Missing ownership/time stays unresolved/null. Other batches cover other rows.')
        if len(rows) > 1 and not await fits(runner, 'measurement_series_extraction', messages, 0):
            mid = len(rows) // 2
            await batch(number+'.0', rows[:mid]); await batch(number+'.1', rows[mid:])
            return

        def check(value):
            checked = ehr_tasks.check_series(value, article, annotation, local_draft, transcriptions)
            visible = {s['segment_id'] for s in view['segments']}
            if any(e.get('segment_id') and e['segment_id'] not in visible
                   for s in checked['series'] for e in s['ownership_evidence']):
                raise ValueError('Ownership evidence is outside the supplied source window')
            if any(p['value_cell_id'] not in focus_ids for s in checked['series'] for p in s['points']):
                raise ValueError('Measurement value is outside the focus rows')
            return checked

        result = await runner.call('measurement_series_extraction', messages, check,
            {'article_id': article['article_id'], 'row_batch': number,
             'focus_cell_ids': sorted(focus_ids), 'context_cell_ids': [c['cell_id'] for c in context],
             'included_segment_ids': [s['segment_id'] for s in view['segments']],
             'source_scope': 'whole_article' if view is article else 'whole_segment_window'},
            0, view['segments'], split_on_length=len(rows) > 1)
        if len(rows) > 1 and splittable_failure(result):
            replaced(runner, result)
            mid = len(rows) // 2
            await batch(number+'.0', rows[:mid]); await batch(number+'.1', rows[mid:])
            return
        leaves.append(result)

    for i in range(0, len(groups), 16):
        await batch(str(i//16), groups[i:i+16])
    document = ehr_tasks.check_series({'series': []}, article, annotation, draft, transcriptions)
    for leaf in leaves:
        if leaf['status'] != 'valid':
            continue
        for series in leaf['data']['series']:
            series = copy.deepcopy(series)
            sid = 's' + str(len(document['series'])+1)
            series['series_id'] = sid
            for i, point in enumerate(series['points'], 1):
                point['point_id'] = f'{sid}-p{i}'
            document['series'].append(series)
    validate_series(document, article, annotation, transcriptions=transcriptions)
    audit = {'policy': 'whole-row-batches/1', 'all_batches_valid': all(r['status']=='valid' for r in leaves),
             'all_focus_cells_attempted': {c['cell_id'] for g in groups for c in g} ==
                 {c for r in leaves for c in r['identity']['focus_cell_ids']},
             'semantic_coverage_verified': False,
             'batches': [{k: r[k] for k in ('identity', 'status', 'errors')} for r in leaves]}
    if not audit['all_batches_valid']:
        document['limitations'].append('Some row batches failed; successful batches retained. See coverage receipt.')
    return document, audit
