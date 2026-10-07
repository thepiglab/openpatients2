"""Lossless inventory of canonical table rows; explicit cells remain source data.

Only simple rectangular rows are interpreted. Complex spans/headers stay in the
unresolved inventory, never silently disappear or become fabricated measurements.
"""
from __future__ import annotations

from copy import deepcopy
from decimal import Decimal
import json
import re
from typing import Literal

from pydantic import Field

from .measurements import parse_measurement
from .provenance import json_digest
from .schemas import StrictModel, Observation


class CellDecision(StrictModel):
    cell_id: str
    status: Literal['extracted', 'not_clinical', 'unresolved']
    observation: Observation | None
    reason: str = Field(min_length=1)


class CellBatch(StrictModel):
    cells: list[CellDecision]


class CellSelection(StrictModel):
    cell_id: str
    status: Literal['extracted', 'not_clinical', 'unresolved']
    name: str | None
    kind: Literal['vital', 'laboratory', 'imaging', 'pathology', 'microbiology', 'physiologic', 'other']
    reason: str = Field(min_length=1)


class CellSelections(StrictModel):
    cells: list[CellSelection]


def materialize_selections(value, cells, article, patient, clinical_checker):
    """Model supplies semantics; CPU owns patient column, result and citations."""
    selections = CellSelections.model_validate(value).model_dump()
    by_id = {c['cell_id']: c for c in cells}
    rows = []
    for decision in selections['cells']:
        cell = by_id.get(decision['cell_id']); obs = None
        if decision['status'] == 'extracted' and cell is not None:
            scalar = cell['measurement']
            obs = {'subject': 'index_patient', 'assertion': 'present', 'temporality': 'unknown',
                'time': {'text': cell['time_header'], 'relation': 'at' if cell['time_header'] else 'unknown',
                         'anchor': None, 'date_iso': None},
                'evidence': [{'source_section': cell['segment_id'], 'quote': cell['source_quote']}],
                'name': decision['name'] or '', 'kind': decision['kind'], 'status': 'resulted',
                'result_absent_reason': None, 'numeric_value': float(scalar['magnitude']) if scalar else None,
                'text_value': cell['raw_value'], 'comparator': scalar['comparator'] if scalar else 'unknown',
                'unit': cell['raw_unit'] or (scalar['unit'] if scalar else None), 'flag': 'unknown',
                'reference_range_text': None, 'specimen': None, 'method': None, 'body_site': None}
        rows.append({'cell_id': decision['cell_id'], 'status': decision['status'],
                     'observation': obs, 'reason': decision['reason']})
    return check_cell_batch({'cells': rows}, cells, article, patient, clinical_checker)


def inventory(article):
    cells=[]; unresolved=[]; preserved=[]
    for s in article['segments']:
        if not s.get('kind','').startswith('table'):continue
        preserved.append(deepcopy(s))
        if s.get('kind')!='table_row':continue
        lines=s['text'].splitlines()
        bars=[i for i,line in enumerate(lines) if '|' in line]
        if len(bars)<2 or any('[rowspan=' in line or '[colspan=' in line for line in lines):
            unresolved.append({'segment_id':s['segment_id'],'reason':'nonrectangular_or_unresolved_header','text':s['text']});continue
        header=[v.strip() for v in lines[bars[0]].split('|')]
        row=[v.strip() for v in lines[bars[-1]].split('|')]
        if len(header)!=len(row) or len(row)<2 or len(bars)!=2:
            unresolved.append({'segment_id':s['segment_id'],'reason':'ambiguous_table_grid','text':s['text']});continue
        context=lines[bars[0]+1:bars[-1]]
        unit_match=re.search(r'\(([^()]*)\)\s*$',row[0])
        unit=unit_match.group(1).strip() if unit_match else None
        for column in range(1,len(row)):
            raw=row[column]; scalar=parse_measurement(raw)
            # A scalar in the cell and an explicit row unit are separate source
            # fields; no scale conversion and no guessing unavailable values.
            cell={'article_id':article['article_id'],'segment_id':s['segment_id'],
                'table_id':s.get('table_id'),'column_index':column,'column_header':header[column],
                'row_header':row[0],'time_header':context[-1] if context else None,
                'raw_value':raw,'raw_unit':unit,'measurement':scalar.model_dump() if scalar else None,
                'source_quote':s['text'],'source_text_sha256':article['text_sha256'],
                'patient_attribution_verified':False}
            cell['cell_id']='cell-'+json_digest(cell)[:20];cells.append(cell)
    return {'schema_version':'table-cell-inventory/1','article_id':article['article_id'],
        'cells':cells,'unresolved_rows':unresolved,'original_table_segments':preserved,
        'all_table_segments_retained':True,'clinical_completeness_verified':False}


def patient_scope(cell, article, patient):
    """Resolve explicit Case/Patient N only; never equate p1 with Case 1."""
    def numbers(text):
        return set(re.findall(r'\b(?:case|patient)\s*#?\s*(\d+)\b',text or '',re.I))
    column=numbers(cell['column_header'])
    if len(column)!=1:return 'unresolved'
    by_id={s['segment_id']:s for s in article['segments']}
    identities=set()
    for q in patient.get('identity_evidence',[]):
        segment=by_id.get(q['segment_id'],{})
        if q.get('quote') and q['quote'] in segment.get('text',''):
            identities.update(numbers(segment.get('heading')))
    label=numbers(patient.get('label'))
    # Identity headings ground the generated label; conflicts are not repaired
    # through patient ordinal assumptions. General/non-case tables remain open.
    if len(identities)!=1 or (label and label!=identities):return 'unresolved'
    return 'target' if column==identities else 'other_patient'


def cell_messages(article, patient, cells, *, selections=False):
    source_ids={c['segment_id'] for c in cells}
    source_ids.update(q['segment_id'] for q in patient.get('identity_evidence',[]))
    tables={c['table_id'] for c in cells}
    segments=[s for s in article['segments'] if s['segment_id'] in source_ids or
        (s.get('table_id') in tables and s.get('kind')=='table_footnote')]
    messages = [{'role':'system','content':
        'Extract the target patient observations from EVERY supplied table cell. '
        'Cells preserve original patient columns and encounter/time headers. '
        'Return one decision per cell_id. Do not copy another column, reference range, '
        'or aggregate into a patient result. Preserve numeric magnitude, comparator, '
        'unit spelling and time header; never convert units. If time_header is given, '
        'use it literally in observation.time.text. Distinguish not reported from negative. '
        'For each extracted observation cite the complete source_quote with its segment_id '
        'as source_section. Subject must be index_patient. Unknown semantics or ambiguous '
        'ownership must stay unresolved. A table value alone does not establish a high/low '
        'flag, specimen, diagnosis, or clinical significance.'},
        {'role':'user','content':'TARGET:\n'+json.dumps(patient,ensure_ascii=False)+
         '\nSOURCE:\n'+json.dumps(segments,ensure_ascii=False)+
         '\nCELLS:\n'+json.dumps(cells,ensure_ascii=False)+
         '\nSCHEMA:\n'+json.dumps(CellBatch.model_json_schema())}]
    if selections:
        messages[0]['content'] = (
            'Classify EVERY supplied cell for the target patient. Return cell_id, status, name, kind, reason. '
            'Do not generate values, units, times or quotes: code preserves those immutable source fields. '
            'Select extracted only for an actual clinical result in the target patient column. '
            'Missing values, ambiguous ownership or unknown semantics must be unresolved. '
            'Do not treat reference ranges or aggregate results as patient results. '
            'Use a source-grounded clinical name; no inferred diagnoses or normality flags.')
        messages[1]['content'] = messages[1]['content'].split('\nSCHEMA:\n')[0] + '\nSCHEMA:\n' + json.dumps(CellSelections.model_json_schema())
    return messages


def check_cell_batch(value, cells, article, patient, clinical_checker):
    """Validate independently so one bad cell cannot erase good neighbors."""
    if not isinstance(value,dict) or set(value)!={'cells'} or not isinstance(value['cells'],list):
        raise ValueError('Expected cells array')
    supplied={c['cell_id']:c for c in cells}; grouped={key:[] for key in supplied}; quarantine=[]
    for raw in value['cells']:
        key=raw.get('cell_id') if isinstance(raw,dict) else None
        if key not in grouped:
            quarantine.append({'cell_id':key,'reason':'unknown_cell','candidate':raw});continue
        grouped[key].append(raw)
    accepted=[]; decisions=[]
    for key, raw_rows in grouped.items():
        try:
            if len(raw_rows)!=1:raise ValueError('missing_or_duplicate_cell')
            decision=CellDecision.model_validate(raw_rows[0]).model_dump(); cell=supplied[key]
            if decision['status']!='extracted':
                if decision['observation'] is not None:raise ValueError('Unresolved cell cannot supply a result')
                decisions.append(decision);continue
            obs=decision['observation']
            if obs is None or patient_scope(cell,article,patient)!='target':raise ValueError('Unresolved patient column')
            if obs['subject']!='index_patient':raise ValueError('Wrong table observation subject')
            if not obs['name'].strip():raise ValueError('Missing clinical name')
            # Older responses followed Evidence's heading contract. Resolve only
            # the exact full row and that row's actual heading, never arbitrary text.
            heading=next((s.get('heading') for s in article['segments'] if s['segment_id']==cell['segment_id']),None)
            for q in obs['evidence']:
                if heading and q['source_section']==heading and q['quote']==cell['source_quote']:
                    q['source_section']=cell['segment_id']
            if not any(q['source_section']==cell['segment_id'] and q['quote']==cell['source_quote'] for q in obs['evidence']):
                raise ValueError('Table result needs complete row/header evidence')
            scalar=cell['measurement']
            if scalar:
                if obs['numeric_value'] is None or Decimal(str(obs['numeric_value']))!=Decimal(scalar['magnitude']):
                    raise ValueError('Table cell magnitude changed')
                if obs['comparator']!=scalar['comparator']:raise ValueError('Table cell comparator changed')
                unit=cell['raw_unit'] or scalar['unit']
                if obs['unit']!=unit:raise ValueError('Table cell unit changed')
            elif obs['numeric_value'] is not None:
                raise ValueError('Unparsed cell cannot become a guessed numeric value')
            elif obs['text_value']!=cell['raw_value'] or cell['raw_value'].strip().lower() in {'','-','–','—','na','n/a','not reported'}:
                raise ValueError('Unparsed or missing cell needs an unresolved decision, not a guessed result')
            if cell['time_header'] and obs['time']['text']!=cell['time_header']:
                raise ValueError('Table encounter header changed')
            section={'coverage':'limited','limitations':['Table-cell augmentation only'],
                     'documentation_status':'documented','documentation_evidence':obs['evidence'],'items':[obs]}
            checked=clinical_checker(section);obs=checked['items'][0]
            accepted.append({'cell_id':key,'observation':obs});decisions.append({**decision,'observation':obs})
        except (ValueError,KeyError,TypeError) as error:
            quarantine.append({'cell_id':key,'reason':str(error),'candidate':raw_rows})
    return {'accepted':accepted,'decisions':decisions,'quarantine':quarantine,
            'expected_cell_ids':list(supplied),'semantic_accuracy_verified':False}
