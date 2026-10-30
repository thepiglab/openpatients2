"""Source-window clinical extraction with explicit coverage and attribution receipts.

Coverage decisions are model judgments, not proof of recall. Original source,
responses, non-patient candidates and failed leaves remain available for review.
"""
import asyncio
import copy
import json
from typing import Literal

from pydantic import Field, create_model

from .data import write_json
from .evidence_ledger import subset_packet
from .evidence_recovery import packet_segments
from .provenance import json_digest
from .schemas import StrictModel, TASK_MODELS
from .staged_timeline import fits, replaced, source_window, splittable_failure


class SourceDecision(StrictModel):
    segment_id: str
    disposition: Literal['extracted', 'no_patient_fact', 'unresolved']
    reason: str = Field(min_length=1)


class FactScope(StrictModel):
    path: str
    scope: Literal['direct_patient', 'universal_cohort', 'aggregate', 'study_protocol',
                   'secondary_case', 'background', 'uncertain']
    reason: str = Field(min_length=1)


class CellDecision(StrictModel):
    cell_id: str
    disposition: Literal['extracted', 'no_patient_fact', 'unresolved']
    item_indices: list[int]
    reason: str = Field(min_length=1)


def envelope_model(task):
    return create_model('ClinicalWindow_' + task, __base__=StrictModel,
        extraction=(TASK_MODELS[task], ...), source_decisions=(list[SourceDecision], ...),
        fact_scopes=(list[FactScope], ...), cell_decisions=(list[CellDecision], ...))


def fact_paths(value):
    result = {}
    for key in ('items', 'tumors', 'biomarkers', 'treatments'):
        for i, item in enumerate(value.get(key, [])):
            result[f'/{key}/{i}'] = item
    if 'case_kind' in value and value.get('evidence'):
        result['/'] = value
    return result


def partition(segments, *, max_segments=6, max_chars=10000):
    """Whole source units, never clipped text. Keep table rows in small groups."""
    groups, batch, size = [], [], 0
    for segment in segments:
        length = len(segment['text'])
        table = segment.get('kind') == 'table_row'
        width = min(max_segments, 4) if table else max_segments
        if batch and (len(batch) >= width or size + length > max_chars
                      or (batch[-1].get('kind') == 'table_row') != table):
            groups.append(batch); batch, size = [], 0
        batch.append(segment); size += length
    if batch:
        groups.append(batch)
    return groups


def check_envelope(value, task, checker, focus, cells=()):
    checked = envelope_model(task).model_validate(value).model_dump()
    checked['extraction'] = checker(checked['extraction'])
    decisions = checked['source_decisions']
    ids = [r['segment_id'] for r in decisions]
    if len(ids) != len(set(ids)) or set(ids) != set(focus):
        raise ValueError('Every focus segment needs exactly one coverage decision')
    scopes = [r['path'] for r in checked['fact_scopes']]
    if len(scopes) != len(set(scopes)) or set(scopes) != set(fact_paths(checked['extraction'])):
        raise ValueError('Every extracted fact needs exactly one attribution scope')
    # A claim to have extracted from a segment needs at least one literal fact
    # citation there. This does not prove that every eligible fact was found.
    for d in decisions:
        if d['disposition'] == 'extracted':
            def cited(v):
                if isinstance(v, dict):
                    return (v.get('source_section') == d['segment_id'] and bool(v.get('quote'))) or any(cited(x) for x in v.values())
                return isinstance(v, list) and any(cited(x) for x in v)
            if not cited(checked['extraction']):
                raise ValueError('Extracted coverage decision has no citation in ' + d['segment_id'])
    decisions = checked['cell_decisions']
    ids = [r['cell_id'] for r in decisions]
    by_id = {c['cell_id']: c for c in cells}
    if len(ids) != len(set(ids)) or set(ids) != set(by_id):
        raise ValueError('Every focus value cell needs exactly one coverage decision')
    items = checked['extraction'].get('items', [])
    for d in decisions:
        if len(d['item_indices']) != len(set(d['item_indices'])):
            raise ValueError('Duplicate cell item index')
        if d['disposition'] != 'extracted':
            if d['item_indices']:
                raise ValueError('Unextracted cell cannot claim extracted items')
            continue
        if not d['item_indices']:
            raise ValueError('Extracted cell requires at least one item index')
        cell = by_id[d['cell_id']]
        for index in d['item_indices']:
            if not 0 <= index < len(items):
                raise ValueError('Unknown cell item index')
            item = items[index]
            if not any(e.get('source_section') == cell['segment_id'] and cell['raw_text'] in e.get('quote','')
                       for e in item.get('evidence', [])):
                raise ValueError('Cell extraction lacks literal evidence for its source row and value')
    return checked


def patient_only(envelope):
    data = copy.deepcopy(envelope['extraction'])
    scopes = {r['path']: r for r in envelope['fact_scopes']}
    quarantine = [{'candidate': copy.deepcopy(v), **scopes[p], 'clinical_entailment_verified': False}
                  for p, v in fact_paths(data).items() if scopes[p]['scope'] != 'direct_patient']
    if '/' in scopes and scopes['/']['scope'] != 'direct_patient':
        return None, quarantine
    for key in ('items', 'tumors', 'biomarkers', 'treatments'):
        if key in data:
            data[key] = [v for i, v in enumerate(data[key]) if scopes[f'/{key}/{i}']['scope'] == 'direct_patient']
    has_facts = bool(fact_paths(data))
    if has_facts:
        data['documentation_status'] = 'documented'
    elif data['documentation_status'] == 'documented':
        data.update(documentation_status='not_documented', documentation_evidence=[])
    if quarantine:
        data['limitations'].append('Non-direct patient candidates retained separately in attribution quarantine.')
    return data, quarantine


def merge(task, leaves, checker):
    """Union exact facts. Scalar disagreements stay partial; never last-write-wins."""
    parts, quarantine, errors = [], [], []
    for leaf in leaves:
        if leaf['status'] != 'valid':
            errors.append('failed_source_window:' + str(leaf['identity']['source_batch']))
            continue
        part, excluded = patient_only(leaf['data']); quarantine.extend(excluded)
        if part is not None:
            parts.append(part)
        if any(d['disposition'] == 'unresolved' for d in leaf['data']['source_decisions']):
            errors.append('unresolved_source_coverage:' + str(leaf['identity']['source_batch']))
        if any(d['disposition'] == 'unresolved' for d in leaf['data'].get('cell_decisions', [])):
            errors.append('unresolved_cell_coverage:' + str(leaf['identity']['source_batch']))
        if any(d['scope'] == 'uncertain' for d in leaf['data']['fact_scopes']):
            errors.append('unresolved_patient_attribution:' + str(leaf['identity']['source_batch']))
    if not parts:
        return None, quarantine, errors or ['no_valid_patient_section']
    result = copy.deepcopy(parts[0])
    for key in ('items', 'tumors', 'biomarkers', 'treatments', 'documentation_evidence', 'limitations', 'evidence'):
        if key in result:
            result[key] = list({json.dumps(v, sort_keys=True): v for p in parts for v in p.get(key, [])}.values())
    excluded_keys = {'items', 'tumors', 'biomarkers', 'treatments', 'documentation_evidence',
                     'limitations', 'evidence', 'coverage', 'documentation_status'}
    for key in result.keys() - excluded_keys:
        values = [p[key] for p in parts if p.get(key) is not None and p.get(key) != 'unknown']
        if len({json.dumps(v, sort_keys=True) for v in values}) > 1:
            # Conflicting scalar case context cannot be accepted as a patient fact.
            return None, quarantine, errors + ['conflicting_window_field:' + key]
        result[key] = values[0] if values else result[key]
    if fact_paths(result):
        result['documentation_status'] = 'documented'
    elif all(p['documentation_status'] == 'not_applicable' for p in parts):
        result['documentation_status'] = 'not_applicable'
    elif any(p['documentation_status'] == 'explicitly_unknown' for p in parts):
        result['documentation_status'] = 'explicitly_unknown'
    else:
        result.update(documentation_status='not_documented', documentation_evidence=[])
    result['coverage'] = 'limited' if errors or any(p['coverage'] == 'limited' for p in parts) else 'complete'
    if result['coverage'] == 'limited':
        result['limitations'].append('Some source windows or coverage decisions remain unresolved; see coverage audit.')
    try:
        return checker(result), quarantine, errors
    except (ValueError, TypeError, KeyError) as exc:
        return None, quarantine, errors + ['assembled_clinical_section_invalid:' + str(exc)]


async def extract(runner, task, packet, article, patient, identity, replica):
    full_segments = packet_segments(packet)
    available = {s['segment_id'] for s in full_segments}
    segments = [s for s in article['segments'] if s['segment_id'] in available]
    call_task = 'clinical_window_' + task
    runner.schemas[call_task] = envelope_model(task).model_json_schema()
    leaves = []
    from .ehr_tasks import series_draft
    cells = series_draft(article, [])['cells'] if task == 'observations' else []

    async def run_batch(number, batch, whole=False):
        focus = {s['segment_id'] for s in batch}
        view = article if whole else source_window(article, focus, patient)
        # Original table headers preserve column/unit/time meaning; a header
        # must not become a patient's measurement merely because it is visible.
        table_ids = {s.get('table_id') for s in batch if s.get('kind') == 'table_row'}
        wanted = {s['segment_id'] for s in view['segments']} | {
            s['segment_id'] for s in article['segments'] if s.get('kind') == 'table_caption'
            or s.get('kind') in {'table_header', 'table_footnote'} and s.get('table_id') in table_ids}
        wanted &= available
        local_packet = packet if whole else subset_packet(packet, article, wanted)
        local_segments = packet_segments(local_packet)
        focus_cells = [c for c in cells if c['role'] == 'value' and c.get('segment_id') in focus]
        messages = runner.clinical_messages(task, local_packet)
        messages[-1]['content'] = messages[-1]['content'].split('\nReturn only JSON matching SCHEMA:')[0]
        messages[-1]['content'] += (
            '\nWINDOW CONTRACT: Return {extraction, source_decisions, fact_scopes, cell_decisions} matching the final schema. '
            'extraction has the usual task schema. Only emit facts directly supported in FOCUS_SEGMENTS; '
            'other supplied blocks are identity/header/context. Review every focus block, every named analyte '
            'and each measurement/time column, not just the first rows. coverage refers only to this window. '
            'For EACH focus segment report extracted, no_patient_fact with a reason, or unresolved. '
            'For EACH item (/items/0 etc.; oncology uses /tumors/N, /biomarkers/N, /treatments/N; '
            'documented case_context uses /), report its attribution scope. Never assign cohort averages, '
            'protocols, generic reference ranges or secondary cases to the index patient. Even universal '
            'cohort statements are universal_cohort, not direct_patient. Unknown individual linkage is uncertain. '
            'Missing timestamps never justify dropping a measurement; retain time unknown. '
            'Only direct_patient facts enter the patient record; all others remain in the audit. '
            'For every FOCUS_VALUE_CELL, give a cell_decision with the zero-based extraction.items indices '
            'covering that value, or explicitly no_patient_fact/unresolved with empty item_indices and a reason. '
            'An extracted cell needs an item citation containing that literal value in its source row. '
            'Missing cells/dashes are not zeros. If no focus cells, cell_decisions=[]. '
            '\nFOCUS_SEGMENTS:\n' + json.dumps(sorted(focus)) +
            '\nFOCUS_VALUE_CELLS:\n' + json.dumps([{k:c[k] for k in ('cell_id','segment_id','column_index','raw_text')} for c in focus_cells]) +
            '\nSCHEMA:\n' + json.dumps(runner.schemas[call_task]))
        async def split():
            mid = len(batch)//2
            return await run_batch(number+'.0', batch[:mid]) + await run_batch(number+'.1', batch[mid:])
        if len(batch) > 1 and not await fits(runner, call_task, messages, replica):
            return await split()
        result = await runner.call(call_task, messages,
            lambda v: check_envelope(v, task, runner.clinical_checker(task, local_packet, local_segments), focus, focus_cells),
            {**identity, 'source_batch': number, 'focus_segment_ids': sorted(focus),
             'included_segment_ids': sorted(wanted)}, replica, local_segments,
            split_on_length=len(batch)>1)
        if len(batch)>1 and splittable_failure(result):
            replaced(runner, result)
            return await split()
        return [result]

    if task == 'observations':
        groups = partition(segments)
        leaves = [r for group in await asyncio.gather(*(run_batch(str(i), batch) for i,batch in enumerate(groups))) for r in group]
    else:
        leaves = await run_batch('0', segments, whole=True)
    data, quarantine, errors = merge(task, leaves, runner.clinical_checker(task, packet, full_segments))
    covered = {sid for r in leaves for sid in r['identity']['focus_segment_ids']}
    if covered != available:
        errors.append('source_window_coverage_incomplete')
    audit = {'policy': 'clinical-source-windows/1', 'task': task, 'identity': identity,
             'source_text_sha256': article['text_sha256'], 'all_segments_attempted': covered == available,
             'all_windows_valid': all(r['status']=='valid' for r in leaves),
             'semantic_coverage_verified': False, 'attribution_quarantine': quarantine,
             'source_decisions': [d for r in leaves if r['status']=='valid' for d in r['data']['source_decisions']],
             'cell_decisions': [d for r in leaves if r['status']=='valid' for d in r['data']['cell_decisions']],
             'batches': [{k:r[k] for k in ('identity','status','errors')} for r in leaves]}
    key = json_digest([identity, task, 'assembled-clinical-windows'])
    result = {'task': task, 'task_key': key, 'identity': identity, 'arm': runner.arm, 'replica': replica,
              'status': 'valid' if data is not None and not errors else 'partial' if data is not None else 'failed',
              'data': data, 'errors': errors, 'attempts': [v for r in leaves for v in r['attempts']],
              'quarantine': quarantine, 'coverage_audit': audit, 'clinical_accuracy_verified': False}
    runner.results.append(result)
    write_json(runner.output/'tasks'/(key+'.json'), result)
    write_json(runner.output/'coverage'/(identity['record_id'].replace(':','-')+'-'+task+'.json'), audit)
    return result
