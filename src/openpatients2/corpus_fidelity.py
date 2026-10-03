"""Finite source checklist screening, separate from schema and clinical accuracy.

Each pattern must match one complete generated item. Quoted evidence is excluded.
Missing/unscorable delivery is distinct from forbidden content being absent;
unmatched generated claims require manual review, never automatic rejection.
"""
from __future__ import annotations
from collections import Counter
import hashlib
import json
from pathlib import Path

from .fidelity import matches, without_evidence, validate_reference


def validate_reference_sources(reference, articles):
    """Fail before model calls when a source hash or literal gold quote changed."""
    if not isinstance(articles, dict):
        articles = {a['article_id']: a for a in articles}
    for article in articles.values():
        rendered = '\n\n'.join(f'[{s["segment_id"]}] {s.get("heading") or "Article"}\n{s["text"]}' for s in article['segments'])
        if article.get('text') != rendered or hashlib.sha256(rendered.encode()).hexdigest() != article.get('text_sha256'):
            raise ValueError('Canonical article text/hash changed')
    validate_reference(reference, articles)
    from .schemas import TASK_MODELS
    for check in reference['checks']:
        for alternative in check.get('semantic_alternatives', []):
            if (check['kind'] != 'required' or alternative.get('task') not in TASK_MODELS
                    or alternative.get('collection') not in TASK_MODELS[alternative['task']].model_fields
                    or not isinstance(alternative.get('pattern'), dict)):
                raise ValueError('Invalid representation-aware reference alternative')
    for row in reference.get('articles', []) + reference.get('figure_checks', []):
        article = articles[row['article_id']]
        if row.get('text_sha256') != article['text_sha256']:
            raise ValueError('Reference source changed')
        segments = {s['segment_id']: s['text'] for s in article['segments']}
        if not row.get('source') or any(not e.get('quote') or
                e['quote'] not in segments.get(e['segment_id'], '') for e in row['source']):
            raise ValueError('Nonliteral reference evidence')
    return True


def _read(value):
    if isinstance(value, (str, Path)):
        path = Path(value)
        if path.is_dir():
            nested = path / 'patients'
            folder = nested if nested.is_dir() else path
            return [json.loads(p.read_text()) for p in sorted(folder.glob('*.json'))
                    if p.name not in {'report.json', 'reference.json', 'manifest.json'}]
        if path.suffix == '.jsonl':
            return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
        return json.loads(path.read_text())
    return value


def _rows(value, key=None):
    value = _read(value)
    if value is None:
        return []
    if isinstance(value, dict):
        if key and isinstance(value.get(key), list):
            return value[key]
        if key == 'articles' and isinstance(value.get('predictions'), dict):
            return _rows(value['predictions'], 'articles')
        if key == 'articles' and isinstance(value.get('predictions'), list):
            return value['predictions']
        return [value]
    return value


def _records(patients):
    result = {}
    for patient in _rows(patients, 'patients'):
        source = patient.get('source') or {}
        rid = source.get('record_id')
        if not rid and source.get('article_source') and source.get('patient_target'):
            rid = source['article_source'].get('article_id', '') + ':' + source['patient_target'].get('patient_id', '')
        if not rid or 'sections' not in patient:
            continue
        if rid in result:
            raise ValueError('Duplicate patient record: ' + rid)
        result[rid] = patient
    return result


def _section(patient, task):
    if task in (patient.get('sections') or {}):
        return patient['sections'][task]
    value = (patient.get('companions') or {}).get(task)
    # New exports store companion data directly; legacy exports used wrappers.
    return value.get('data') if isinstance(value, dict) and 'status' in value and 'data' in value else value


def _raw(task, first=True):
    attempts = task.get('attempts') or []
    if attempts:
        last = attempts[0] if first else attempts[-1]
        # First original full candidate, never a repair fragment or attempt union.
        return last.get('raw_candidate')
    return None


def _items(section, collection):
    if not isinstance(section, dict):
        return []
    if collection is None:
        return [section]
    items = section.get(collection)
    return items if isinstance(items, list) else []


def evaluate(reference, patients, *, task_rows=None, discovery=None, visual_rows=None):
    """Return JSON-ready checks, summary, discovery, figures, schema, unreviewed.

    Patients accepts exported bundle dictionaries, JSONL, or a patients folder.
    Task rows supply first_pass and its raw alias (first original attempt);
    delivery always uses exported data. No attempt unions or oracle selections.
    Discovery accepts predicted-rosters/1 or a report with articles[].roster.
    Figure checks inspect attribution task assignments, not pixel correctness.
    """
    reference = _read(reference)
    records = _records(patients)
    task_index = {}
    statuses = Counter()
    for row in _rows(task_rows, 'tasks'):
        identity = row.get('identity') or {}
        aid = identity.get('article_id'); pid = identity.get('patient_id')
        statuses[row.get('status', 'unknown')] += 1
        if aid and pid:
            task_index[(aid + ':' + pid, row.get('task'))] = row
    result = []; covered = {mode: set() for mode in ('first_pass', 'raw', 'delivered')}
    for check in reference['checks']:
        rid = check['record_id']; patient = records.get(rid, {})
        source = (patient.get('source') or {}).get('article_source') or {}
        bound = source.get('text_sha256') == check['text_sha256'] and source.get('xml_sha256') == check['xml_sha256']
        row = {k: check[k] for k in ('id', 'record_id', 'kind', 'category', 'task')}
        for mode in ('first_pass', 'raw', 'delivered'):
            task = task_index.get((rid, check['task']))
            section = _raw(task, first=True) if mode != 'delivered' and task is not None else (_section(patient, check['task']) if mode == 'delivered' else None)
            available = bound and isinstance(section, dict)
            hits = [i for i, item in enumerate(_items(section, check.get('collection')))
                    if available and matches(check['pattern'], without_evidence(item))]
            row[mode] = {'available': available, 'matched': bool(hits), 'item_indices': hits,
                'unscorable_reason': None if available else ('source_binding_failed' if patient and not bound else 'missing_or_unparseable_section')}
            if check['kind'] == 'required':
                covered[mode].update((rid, check['task'], check.get('collection'), i) for i in hits)
        # Keep the historical strict score untouched. Explicitly reviewed
        # proposition alternatives live in a separate versioned readout.
        alternatives = []
        alternative_available = False
        for alternative in check.get('semantic_alternatives', []):
            section = _section(patient, alternative['task'])
            alternative_available |= bound and isinstance(section, dict)
            for i, item in enumerate(_items(section, alternative['collection'])):
                if bound and matches(alternative['pattern'], without_evidence(item)):
                    alternatives.append({'task': alternative['task'], 'collection': alternative['collection'], 'item_index': i})
        row['representation_aware_delivered'] = {
            'available': row['delivered']['available'] or alternative_available,
            'matched': row['delivered']['matched'] or bool(alternatives),
            'alternative_locations': alternatives}
        result.append(row)
    summaries = {}
    unreviewed = {}
    for mode in ('first_pass', 'raw', 'delivered'):
        required = [r for r in result if r['kind'] == 'required']; forbidden = [r for r in result if r['kind'] == 'forbidden']
        summaries[mode] = {'required': len(required), 'matched': sum(r[mode]['matched'] for r in required),
            'missing': sum(r[mode]['available'] and not r[mode]['matched'] for r in required),
            'unscorable': sum(not r[mode]['available'] for r in required),
            'forbidden': len(forbidden), 'forbidden_violations': sum(r[mode]['matched'] for r in forbidden),
            'forbidden_unscorable': sum(not r[mode]['available'] for r in forbidden),
            'checklist_delivery_fraction': sum(r[mode]['matched'] for r in required) / len(required) if required else None}
        claims = set()
        for rid, patient in records.items():
            tasks = set(patient.get('sections') or {}) | set(patient.get('companions') or {})
            for task in tasks:
                row = task_index.get((rid, task))
                section = _raw(row, first=True) if mode != 'delivered' and row is not None else (_section(patient, task) if mode == 'delivered' else None)
                for collection in ('items', 'tumors', 'biomarkers', 'treatments', 'claims', 'events'):
                    claims.update((rid, task, collection, i) for i in range(len(_items(section, collection))))
        unreviewed[mode] = {'generated_items': len(claims), 'items_matching_required_check': len(claims & covered[mode]),
            'items_outside_checklist': len(claims - covered[mode]), 'unsupported_claims': None,
            'reason': 'Claims outside this finite checklist need manual source adjudication; pattern hits also do not establish full correctness.'}
    if not task_rows:
        for patient in records.values():
            for status in (patient.get('quality') or {}).values():
                statuses[status.get('status', 'unknown') if isinstance(status, dict) else str(status)] += 1
    required = [r['representation_aware_delivered'] for r in result if r['kind'] == 'required']
    representation = {'reference_schema_version': reference.get('schema_version'), 'required': len(required),
        'matched': sum(r['matched'] for r in required),
        'missing': sum(r['available'] and not r['matched'] for r in required),
        'unscorable': sum(not r['available'] for r in required),
        'definition': 'Strict match or an explicitly reviewed equivalent representation; no evidence-text search, '
                      'cross-item union, inference, or relaxation of forbidden checks. Not clinical accuracy.'}
    return {'schema_version': 'corpus-fidelity/1', 'raw_definition': 'first original raw_candidate; raw aliases first_pass; final repair patches excluded', 'checks': result, 'summary': summaries,
        'representation_aware': representation,
        'discovery': _discovery(reference, discovery), 'figures': _figures(reference, visual_rows, task_rows),
        'schema': {'reported_task_statuses': dict(statuses), 'clinical_accuracy_established': False},
        'unreviewed_claims': unreviewed,
        'warning': 'Finite checklist screening only. Missing matches can reflect paraphrases. Schema validity, literal citations and checklist hits are not clinical accuracy.'}


def _discovery(reference, predictions):
    rows = {}
    for row in _rows(predictions, 'articles'):
        rows[row.get('article_id') or (row.get('identity') or {}).get('article_id')] = row
    results = []
    for gold in reference.get('articles', []):
        pred = rows.get(gold['article_id'], {})
        roster = pred.get('roster', pred.get('data'))
        if isinstance(roster, dict) and 'data' in roster:
            roster = roster['data']
        available = isinstance(roster, dict) and isinstance(roster.get('patients'), list)
        if pred.get('text_sha256') is not None and pred['text_sha256'] != gold['text_sha256']:
            available = False
        patients = roster['patients'] if available else []
        results.append({'article_id': gold['article_id'], 'available': available,
            'expected_count': gold['expected_count'], 'predicted_count': len(patients) if available else None,
            'missing_count': max(0, gold['expected_count'] - len(patients)) if available else gold['expected_count'],
            'extra_count': max(0, len(patients) - gold['expected_count']) if available else None,
            'count_match': available and len(patients) == gold['expected_count'],
            'species_multiset_match': available and Counter(p.get('species') for p in patients) == Counter(gold['species']),
            'provisional': gold.get('count_adjudication') != 'source_checked',
            'individual_identity_accuracy': 'unreviewed; counts and species do not adjudicate identities'})
    scored = [r for r in results if not r['provisional']]
    return {'articles': results, 'summary': {'definitive_articles': len(scored), 'count_matches': sum(r['count_match'] for r in scored),
        'unscorable': sum(not r['available'] for r in scored), 'extra_patients': sum(r['extra_count'] or 0 for r in scored),
        'missing_patients': sum(r['missing_count'] for r in scored), 'provisional_articles_excluded': len(results) - len(scored)}}


def _figures(reference, visual_rows, task_rows):
    caption = [r for r in _rows(task_rows, 'tasks') if r.get('task') == 'figure_attribution']
    pixels = [r for r in _rows(task_rows, 'tasks') if r.get('task') == 'pixel_attribution']
    for row in _rows(visual_rows, 'figures'):
        if row.get('attribution') is not None:
            pixels.append({**row['attribution'], 'identity': row.get('identity', row)})
        elif row.get('task') == 'pixel_attribution':
            pixels.append(row)
    return {'caption': _figure_arm(reference, caption), 'pixels': _figure_arm(reference, pixels)}


def _figure_arm(reference, rows):
    index = {}
    for row in rows:
        identity = row.get('identity') or row
        if identity.get('article_id') and identity.get('figure_id'):
            index[(identity['article_id'], identity['figure_id'])] = row.get('data', row)
    results = []
    for gold in reference.get('figure_checks', []):
        value = index.get((gold['article_id'], gold['figure_id'])) or {}
        assignments = value.get('assignments')
        available = isinstance(assignments, list) and bool(assignments)
        selected = [r for r in assignments or [] if gold.get('panel') is None or r.get('panel') == gold['panel']]
        actual = set(p for r in selected for p in r.get('patient_ids', []))
        expected = set(gold['patient_ids'])
        results.append({'id': gold['id'], 'article_id': gold['article_id'], 'figure_id': gold['figure_id'],
            'available': available, 'matched': available and bool(selected) and actual == expected and all(r.get('scope') == gold['scope'] for r in selected),
            'wrong_patient_ids': sorted(actual - expected), 'missing_patient_ids': sorted(expected - actual),
            'pixel_accuracy_adjudicated': False})
    return {'checks': results, 'summary': {'required': len(results), 'matched': sum(r['matched'] for r in results),
        'unscorable': sum(not r['available'] for r in results), 'wrong_patient_assignments': sum(bool(r['wrong_patient_ids']) for r in results)}}
