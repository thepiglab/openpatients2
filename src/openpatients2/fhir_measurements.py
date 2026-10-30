"""Evidence-gated mapping of literal table series points to R4 Observations.

Every numeric parse is independently recomputed; units stay literal and relative
times remain extensions. Neither a collection timestamp nor article publication
date is substituted for a patient's measurement date.
"""
from decimal import Decimal
import json
import math

from .measurement_series import Series, validate_series
from .provenance import json_digest


def series_entries(article, patient):
    for si, row in enumerate(patient.get('measurement_series', [])):
        for pi, point in enumerate(row.get('points', [])):
            series = {k: v for k, v in row.items() if k in Series.model_fields}
            series['points'] = [point]
            refs = {row.get('name_cell_id'), row.get('reference_range_cell_id'),
                    point.get('value_cell_id'), point.get('unit_cell_id'),
                    point.get('legend_cell_id'), point.get('time', {}).get('time_cell_id')}
            refs.update(e.get('cell_id') for e in row.get('ownership_evidence', []))
            cells = sorted((c for c in row.get('cells', []) if c.get('cell_id') in refs),
                           key=lambda c: c['cell_id'])
            fact = {'measurement_name': row.get('measurement_name'), 'series': series, 'cells': cells}
            key = json_digest({'article_id': article['article_id'], 'patient_id': patient['patient_id'],
                'identity_evidence': patient.get('identity_evidence'), 'task': 'measurement_series', 'fact': fact})
            yield {'fact_key': key, 'task': 'measurement_series', 'collection': 'points',
                'source_pointer': f'/measurement_series/{si}/points/{pi}', 'fact': fact}


def map_series_point(entry, article, patient, source, patient_ref):
    from .fhir_export import cc, code, extension
    fact = entry['fact']; series = fact['series']
    if series.get('scoring') != 'include':
        return None, 'series_review_only'
    if series.get('ownership') != 'individual' or series.get('patient_ids') != [patient['patient_id']]:
        return None, 'series_patient_ownership_unresolved'
    if series.get('measurement_kind') == 'medication_dose':
        return None, 'dose_series_requires_medication_mapping'
    if any(c.get('origin') not in {'article_table', 'article_text'} for c in fact['cells']):
        return None, 'series_supplement_evidence_requires_separate_validation'
    target = {'format': 'dense-measurement-series/1', 'article_id': article['article_id'],
        'source_text_sha256': article['text_sha256'], 'coverage': 'limited', 'cells': fact['cells'],
        'series': [series], 'excluded_cells': [], 'limitations': ['Per-point FHIR compilation, not coverage verification.']}
    try:
        validate_series(target, source, article)
    except (ValueError, KeyError, TypeError):
        return None, 'invalid_series_source_ownership_or_parse'
    point = series['points'][0]
    cells = {c['cell_id']: c for c in fact['cells']}
    raw = cells[point['value_cell_id']]['raw_text']
    result = {'resourceType': 'Observation', 'status': 'unknown', 'subject': {'reference': patient_ref},
        'code': cc(series['measurement_name']), 'extension': [extension('source-fact-key', entry['fact_key']),
            extension('source-fact', json.dumps(fact, ensure_ascii=False, sort_keys=True))]}
    category = {'laboratory': 'laboratory', 'drug_level': 'laboratory', 'vital_sign': 'vital-signs',
                'anthropometric': 'vital-signs', 'imaging_measurement': 'imaging'}.get(series['measurement_kind'])
    if category:
        result['category'] = [code('http://terminology.hl7.org/CodeSystem/observation-category', category)]
    parsed = point.get('parsed') or {}
    if point['parse_status'] == 'missing_cell':
        result['dataAbsentReason'] = code('http://terminology.hl7.org/CodeSystem/data-absent-reason', 'unknown')
    elif point['parse_status'] == 'parsed' and 'magnitude' in parsed and parsed.get('comparator') != 'unknown':
        number = Decimal(parsed['magnitude'])
        value = int(number) if number == number.to_integral_value() else float(number)
        # JSON serialization must not quietly round the source's numeric value.
        try:
            exact = math.isfinite(value) and Decimal(str(value)) == number
        except OverflowError:
            exact = False
        if not exact:
            result['valueString'] = raw
        else:
            quantity = {'value': value}
            unit = parsed.get('unit') or point.get('unit_text')
            if unit:
                quantity['unit'] = unit  # no inferred UCUM code or conversion
            if parsed.get('comparator') in {'<', '<=', '>', '>='}:
                quantity['comparator'] = parsed['comparator']
            result['valueQuantity'] = quantity
    else:
        result['valueString'] = raw
    if series.get('reference_range_cell_id'):
        result['referenceRange'] = [{'text': cells[series['reference_range_cell_id']]['raw_text']}]
    if point.get('time'):
        result['extension'].append(extension('reported-relative-time', json.dumps(point['time'], sort_keys=True)))
    return result, None
