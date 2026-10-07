"""CPU-only, source-reviewed proposition probes for paired ledger comparisons.

The reviewed checklist is partial. Attribute matches are dense diagnostic feedback,
not clinical precision or an entailment judge. Unreviewed modifiers stay unreviewed.
Routing hypotheses must be materialized through the typed clinical schemas first.
"""
from __future__ import annotations

from collections import Counter
from copy import deepcopy
import random
import re

from .bundle_quality import align_patients, score_bundle, subset_reference, timeline_probes, validate_bundle_gold
from .corpus_fidelity import _items, _read, _records, _section, evaluate as checklist_evaluate
from .fidelity import _litre_spelling, matches, without_evidence

NOTICE = (
    "Finite source-reviewed proposition/attribute probes, not exhaustive clinical accuracy. "
    "Schema acceptance and literal evidence do not prove entailment. Missing pattern matches "
    "may need paraphrase adjudication; unchecked modifiers remain unreviewed. Synthetic "
    "regressions are software challenges, never additional clinical gold."
)
ANCHORS = {'name', 'substance', 'attribute', 'description', 'event', 'action_text'}


def _match(pattern, value, field=''):
    """Keep quantities exact and units case-sensitive, including existing regexes."""
    if isinstance(pattern, dict):
        if set(pattern) == {'regex'}:
            if not isinstance(value, str):
                return False
            if field in {'unit', 'dose_unit'}:
                return bool(re.fullmatch(_litre_spelling(pattern['regex']), _litre_spelling(value.strip())))
            return bool(re.search(pattern['regex'], value.strip(), re.I))
        if set(pattern) == {'one_of'}:
            return any(_match(p, value, field) for p in pattern['one_of'])
        return isinstance(value, dict) and all(k in value and _match(p, value[k], k) for k, p in pattern.items())
    if field in {'unit', 'dose_unit'} and isinstance(pattern, str) and isinstance(value, str):
        return _litre_spelling(pattern.strip()) == _litre_spelling(value.strip())
    return matches(pattern, value, field)


def _attributes(pattern, value, path=''):
    """Select one reviewed alternative; never assemble a match across items."""
    if isinstance(pattern, dict) and set(pattern) == {'one_of'}:
        options = [_attributes(p, value, path) for p in pattern['one_of']]
        return max(options, key=lambda rows: sum(r['matched'] for r in rows) / max(1, len(rows)), default=[])
    if isinstance(pattern, dict) and set(pattern) != {'regex'}:
        return [row for key, sub in pattern.items() for row in _attributes(
            sub, value.get(key) if isinstance(value, dict) else None, f'{path}.{key}' if path else key)]
    return [{'field': path, 'expected': pattern, 'actual': value, 'matched': _match(pattern, value, path.split('.')[-1])}]


def _anchor(pattern, item):
    if isinstance(pattern, dict) and set(pattern) == {'one_of'}:
        return any(_anchor(p, item) for p in pattern['one_of'])
    anchor = {k: p for k, p in pattern.items() if k in ANCHORS} if isinstance(pattern, dict) else {}
    # No reliable identity anchor: report omission, not an arbitrary item mismatch.
    return bool(anchor) and _match(anchor, item)


def _issue(field):
    if field in {'assertion', 'verification_status', 'occurrence'}:
        return 'certainty_or_negation'
    if field in {'action', 'action_status', 'temporality'}:
        return 'planned_vs_completed' if field != 'temporality' else 'temporal_link'
    if field.startswith('time.') or field in {'episode_id', 'time_text', 'follow_up_duration_text'}:
        return 'temporal_link'
    if field in {'numeric_value', 'dose_value', 'unit', 'dose_unit', 'comparator'}:
        return 'measurement'
    if field in {'subject', 'patient_id', 'record_id'}:
        return 'wrong_owner'
    return 'attribute_mismatch'


def evaluate(reference, patients):
    """Score accepted patient bundles, paths or JSONL against reviewed checks.

    Denominators are reviewed probes and their constrained fields, not unique
    independent clinical facts. Each probe uses one complete generated item.
    Failed/absent delivery remains in the recall denominator. No self-judge calls.
    """
    reference = _read(reference)
    records = _records(patients)
    rows = []
    diagnostics = []
    totals = Counter()
    seen = set()
    for check in reference['checks']:
        if check['id'] in seen:
            raise ValueError('Duplicate reviewed check ID')
        seen.add(check['id'])
        rid = check['record_id']
        patient = records.get(rid, {})
        source = patient.get('source', {}).get('article_source', {})
        bound = all(source.get(k) == check[k] for k in ('text_sha256', 'xml_sha256'))
        section = _section(patient, check['task'])
        available = bound and isinstance(section, dict)
        items = [without_evidence(i) for i in _items(section, check.get('collection'))] if available else []
        hits = [i for i, item in enumerate(items) if _match(check['pattern'], item)]
        row = {'id': check['id'], 'record_id': rid, 'kind': check['kind'], 'task': check['task'],
               'available': available, 'matched': bool(hits), 'item_indices': hits}
        rows.append(row)
        context = {'check_id': check['id'], 'record_id': rid, 'task': check['task'], 'source': check.get('source', [])}
        if check['kind'] == 'forbidden':
            totals['forbidden_probes'] += 1
            totals['forbidden_unavailable'] += not available
            totals['unsupported_modifiers'] += bool(hits)
            if hits:
                diagnostics.append({**context, 'kind': 'unsupported_modifier', 'pattern': check['pattern'],
                                    'item_indices': hits, 'basis': 'explicit_reviewed_forbidden_probe'})
            continue
        totals['required_probes'] += 1
        totals['matched_probes'] += bool(hits)
        totals['unavailable_probes'] += not available
        candidates = [(i, item) for i, item in enumerate(items) if _anchor(check['pattern'], item)]
        if hits:
            candidates = [(hits[0], items[hits[0]])]
        # Quantities distinguish repeated administrations before their time is
        # audited. Otherwise a dose/time tie can report a dose error instead of
        # the actual swapped episode year.
        def rank(candidate):
            attrs = _attributes(check['pattern'], candidate[1])
            return sum(a['matched'] * (3 if a['field'] in {'dose_value', 'numeric_value'} else 1)
                       for a in attrs)
        best = max(candidates, key=rank, default=None)
        attrs = _attributes(check['pattern'], best[1] if best else {})
        row['attributes'] = attrs
        totals['required_attributes'] += len(attrs)
        totals['matched_attributes'] += sum(a['matched'] for a in attrs) if best else 0
        if hits:
            continue
        totals['missing_required_propositions'] += 1
        if best:
            for attr in attrs:
                if not attr['matched']:
                    kind = _issue(attr['field'])
                    totals[kind + '_errors'] += 1
                    diagnostics.append({**context, 'kind': kind, **attr, 'item_index': best[0],
                                        'basis': 'reviewed_attribute_mismatch', 'entailment_established': False})
        else:
            # Same source and task only. Different article facts never explain this patient's omission.
            owners = []
            for other_id, other in records.items():
                if other_id == rid or other_id.split(':')[0] != rid.split(':')[0]:
                    continue
                other_source = other.get('source', {}).get('article_source', {})
                if not all(other_source.get(k) == check[k] for k in ('text_sha256', 'xml_sha256')):
                    continue
                if any(_match(check['pattern'], without_evidence(i)) for i in
                       _items(_section(other, check['task']), check.get('collection'))):
                    owners.append(other_id)
            kind = 'wrong_owner' if owners else 'missing_required_proposition'
            totals['wrong_owner_errors'] += bool(owners)
            diagnostics.append({**context, 'kind': kind, 'found_under_records': owners,
                                'basis': 'reviewed_patient_probe', 'entailment_established': False})
    temporal = []
    for gold in reference.get('bundle_gold', {}).get('timelines', []):
        patient = records.get(gold['record_id'], {})
        source = patient.get('source', {}).get('article_source', {})
        graph = _section(patient, 'timeline_v2') if source.get('text_sha256') == gold.get('text_sha256') else None
        temporal.append(timeline_probes(gold, graph))
    totals['required_temporal_links'] = sum(r['relations'] for r in temporal)
    totals['matched_temporal_links'] = sum(r['relations_matched'] for r in temporal)
    totals['reversed_temporal_links'] = sum(r['reversed_relations'] for r in temporal)
    for row in temporal:
        if row['relations_matched'] < row['relations']:
            diagnostics.append({'kind': 'temporal_link', **row, 'basis': 'reviewed_temporal_probe'})
    summary = dict(totals)
    for key in ('required_probes', 'matched_probes', 'unavailable_probes', 'required_attributes',
                'matched_attributes', 'missing_required_propositions', 'unsupported_modifiers',
                'forbidden_probes', 'forbidden_unavailable', 'wrong_owner_errors'):
        summary.setdefault(key, 0)
    recall = summary['matched_probes'] / summary['required_probes'] if summary['required_probes'] else None
    attr = summary['matched_attributes'] / summary['required_attributes'] if summary['required_attributes'] else None
    summary.update(proposition_probe_retention=recall, attribute_probe_retention=attr,
                   clinical_precision=None, clinical_accuracy_established=False)
    # Dense feedback preserves hard errors, but isn't a promotion threshold.
    weights = [(recall, .65), (attr, .25)]
    if totals['required_temporal_links']:
        weights.append((totals['matched_temporal_links'] / totals['required_temporal_links'], .10))
    active = [(value, weight) for value, weight in weights if value is not None]
    dense = sum(v*w for v, w in active) / sum(w for _, w in active) if active else 0.
    dense = max(0., dense - min(.5, .1 * totals['unsupported_modifiers'] + .05 * totals['reversed_temporal_links']))
    schema = checklist_evaluate(reference, patients)['schema']
    return {'schema_version': 'ledger-evaluation/1', 'summary': summary, 'checks': rows,
            'temporal': temporal, 'diagnostics': diagnostics, 'dense_feedback_score': dense,
            'schema': schema, 'notice': NOTICE}


def validate_article_splits(splits, article_ids=None):
    """Reject patient-level splitting, duplicates, omissions, or PMC-version leakage."""
    seen = {}
    supplied = set()
    for split in ('train', 'validation', 'test'):
        values = splits.get(split)
        if not isinstance(values, list) or any(not isinstance(v, str) or ':' in v for v in values):
            raise ValueError('Splits must contain article IDs, never patient record IDs')
        for aid in values:
            canonical = re.sub(r'\.\d+$', '', aid)
            if canonical in seen:
                raise ValueError('Overlapping or duplicate article split: ' + aid)
            seen[canonical] = split
            supplied.add(aid)
    if article_ids is not None and supplied != set(article_ids):
        raise ValueError('Article split coverage mismatch')
    return {name: list(splits[name]) for name in ('train', 'validation', 'test')}


def paired_evaluate(reference, baseline, candidate, *, splits=None, bootstrap=1000, seed=42,
                    baseline_discovery=None, candidate_discovery=None):
    """Paired article/label deltas with the standard bundle score as a control.

    Withheld split labels are never returned as an optimizer training dataset.
    CIs resample articles, retaining all their patients/checks together. They
    describe this development checklist, not an external population estimate.
    """
    reference = _read(reference)
    splits = validate_article_splits(splits or reference['optimization_splits'],
                                    [r['article_id'] for r in reference.get('articles', [])])
    result = {'schema_version': 'paired-ledger-evaluation/1', 'splits': {}, 'notice': NOTICE,
              'evaluation_unit': 'article', 'test_notice': 'Existing development exposure is not new blinded clinical gold.'}
    for name, ids in splits.items():
        subset = subset_reference(reference, ids)
        ba, _ = align_patients(subset, baseline, baseline_discovery)
        ca, _ = align_patients(subset, candidate, candidate_discovery)
        before, after = evaluate(subset, ba), evaluate(subset, ca)
        bchecks = {r['id']: r for r in before['checks']}
        required = [r for r in after['checks'] if r['kind'] == 'required']
        gains = [r['id'] for r in required if r['matched'] and not bchecks[r['id']]['matched']]
        losses = [r['id'] for r in required if not r['matched'] and bchecks[r['id']]['matched']]
        grouped = {}
        for row in required:
            aid = row['record_id'].split(':')[0]
            grouped.setdefault(aid, []).append(int(row['matched']) - int(bchecks[row['id']]['matched']))
        samples = []
        rng = random.Random(seed)
        groups = sorted(grouped)
        if len(groups) >= 2:
            for _ in range(bootstrap):
                changes = [d for aid in rng.choices(groups, k=len(groups)) for d in grouped[aid]]
                samples.append(sum(changes) / len(changes))
        samples.sort()
        interval = [samples[int(.025*(len(samples)-1))], samples[int(.975*(len(samples)-1))]] if samples else None
        result['splits'][name] = {'baseline': before, 'candidate': after,
            'standard_bundle_baseline': score_bundle(subset, baseline, discovery=baseline_discovery),
            'standard_bundle_candidate': score_bundle(subset, candidate, discovery=candidate_discovery),
            'paired_gained': gains, 'paired_lost': losses, 'net_probe_gain': len(gains)-len(losses),
            'article_bootstrap_95_ci_retention_delta': interval, 'annotated_articles': len(groups)}
    return result


def training_feedback(reference, patients):
    """Expose only training-source diagnostics to a GEPA reflection adapter."""
    reference = _read(reference)
    splits = validate_article_splits(reference['optimization_splits'],
                                    [r['article_id'] for r in reference['articles']])
    return evaluate(subset_reference(reference, splits['train']), patients)


def routing_objective(reference, article, patients, chunk_results):
    """Measure original source-witness retention, separately from extraction.

    Gold is consulted after routing, never inserted into map requests. Selecting
    the whole article can retain all witnesses, so source reduction is reported
    separately and must not substitute for clinical fidelity. The caller must
    align live patient IDs using reviewed identity evidence before calling;
    missing aligned patients remain failed probes in the fixed denominator.
    """
    from .evidence_ledger import route_segments
    from .schemas import TASK_MODELS
    reference = _read(reference)
    by = {p['patient_id']: p for p in patients}
    routes = {}
    probes = []
    for check in reference['checks']:
        aid, pid = check['record_id'].split(':', 1)
        if aid != article['article_id'] or check['kind'] != 'required' or check['task'] not in TASK_MODELS:
            continue
        if any(check[k] != article[k] for k in ('text_sha256', 'xml_sha256')):
            raise ValueError('Routing objective reference source changed')
        key = (pid, check['task'])
        if key not in routes:
            if pid not in by:
                routes[key] = (set(), {'mode': 'missing_aligned_patient', 'route_hypotheses': 0,
                                      'patient_available': False})
            else:
                routes[key] = route_segments(article, by[pid], check['task'], chunk_results)
        selected, receipt = routes[key]
        witnesses = {q['segment_id'] for q in check['source']}
        probes.append({'check_id': check['id'], 'record_id': check['record_id'],
                       'task': check['task'], 'retained': witnesses <= selected,
                       'omitted_witness_segments': sorted(witnesses - selected)})
    return {'required_witness_probes': len(probes), 'retained_witness_probes': sum(p['retained'] for p in probes),
            'probes': probes, 'routes': [{'patient_id': pid, 'task': task,
                'source_segment_fraction': len(selected) / max(1, len(article['segments'])), **receipt}
                for (pid, task), (selected, receipt) in sorted(routes.items())],
            'clinical_entailment_established': False,
            'notice': 'Original segment retention is a routing surrogate, not proposition accuracy or new gold.'}


def objective_adequacy(reference):
    """Inventory reviewed labels for focused future prompt optimization on CPU.

    This does not enable a search. The current architecture pilot should run
    paired arms first. New prompt families have no direct map/audit verdict gold;
    downstream finite probes can supply only a bounded surrogate objective.
    """
    reference = _read(reference)
    from .schemas import TASK_MODELS
    splits = validate_article_splits(reference['optimization_splits'],
                                    [r['article_id'] for r in reference['articles']])
    families = {}
    critical = {'assertion', 'verification_status', 'subject', 'laterality', 'body_site',
                'action', 'action_status', 'time.text', 'numeric_value', 'dose_value', 'unit', 'dose_unit'}
    for family in ('source_map', 'attribute', 'episode'):
        groups = {}
        for split, ids in splits.items():
            subset = subset_reference(reference, ids)
            checks = subset['checks']
            if family == 'source_map':
                checks = [c for c in checks if c['task'] in TASK_MODELS]
            if family == 'attribute':
                checks = [c for c in checks if c['kind'] == 'forbidden' or
                          any(a['field'] in critical for a in _attributes(c['pattern'], {}))]
            if family == 'episode':
                timelines = subset.get('bundle_gold', {}).get('timelines', [])
                groups[split] = {'required_probes': sum(len(t['nodes']) + len(t['edges']) for t in timelines),
                    'reviewed_hard_negative_probes': 0, 'articles': len({t['record_id'].split(':')[0] for t in timelines}),
                    'diversity': ['event_identity', 'relative_order'] if timelines else [],
                    'direct_reviewed_temporal_relations': sum(len(t['edges']) for t in timelines)}
            else:
                groups[split] = {'required_probes': sum(c['kind'] == 'required' for c in checks),
                    'reviewed_hard_negative_probes': sum(c['kind'] == 'forbidden' for c in checks),
                    'articles': len({c['record_id'].split(':')[0] for c in checks}),
                    'diversity': sorted({c['task'] for c in checks})}
        gates = {'train_has_five_articles': groups['train']['articles'] >= 5,
                 'validation_has_three_articles': groups['validation']['articles'] >= 3,
                 'train_has_ten_required_probes': groups['train']['required_probes'] >= 10,
                 'validation_has_five_required_probes': groups['validation']['required_probes'] >= 5,
                 'train_has_three_reviewed_hard_negatives': groups['train']['reviewed_hard_negative_probes'] >= 3,
                 'train_has_two_probe_domains': len(groups['train']['diversity']) >= 2}
        families[family] = {'groups': groups, 'screening_gates': gates,
            'surrogate_label_minima_met': all(gates.values()),
            'direct_prompt_verdict_labels': 0,
            'direct_prompt_objective_established': False,
            'missing_annotation': {
                'source_map': 'Reviewed map proposition ownership/domain/omission judgments; source coverage alone can be saturated.',
                'attribute': 'Reviewed candidate-field challenges, including correct fields the auditor must leave untouched.',
                'episode': 'Reviewed wrong episode/fact links, merged occurrences and unsupported temporal edges.'}[family]}
    return {'schema_version': 'ledger-objective-adequacy/1', 'families': families,
            'search_enabled_in_this_campaign': False, 'decision': 'paired_architecture_first',
            'synthetic_fixture_count_in_gold': 0,
            'future_search_scope': 'One invoked family at a time, exact component keys and schema-compatible mutations; '
                'train-only reflection, validation selection, frozen paired baseline and untouched test. '
                'Sample unsaturated saved failures and keep a small fixed metric-call budget.',
            'notice': NOTICE}


def preflight(reference, articles):
    """Validate existing gold, original sources and disjoint groups on CPU."""
    reference = _read(reference)
    validate_bundle_gold(reference, articles)
    splits = validate_article_splits(reference['optimization_splits'], [r['article_id'] for r in reference['articles']])
    return {'passed': True, 'required_probes': sum(c['kind'] == 'required' for c in reference['checks']),
            'forbidden_probes': sum(c['kind'] == 'forbidden' for c in reference['checks']),
            'split_articles': {k: len(v) for k, v in splits.items()}, 'model_calls': 0, 'notice': NOTICE}


def counterfactual_fixtures():
    """Synthetic software controls derived from reviewed failure modes.

    These fixtures are not in reference-reviewed.json and must not enter a gold
    denominator or train/validation/test splits. Typed field changes are explicit;
    a lexical quote hit is deliberately insufficient to pass the bad cases.
    """
    rid = 'challenge:p1'
    def fact(task, item, owner=rid):
        return {'record_id': owner, 'task': task, 'item': item}
    def case(name, source, expected, good, bad):
        return {'name': name, 'label_scope': 'synthetic_software_regression', 'clinical_gold': False,
                'source': source, 'expected': expected, 'good': good, 'bad': bad}
    men = {'name': 'meningioma', 'laterality': 'unknown'}
    allergy = {'substance': "cow's milk protein", 'assertion': 'possible'}
    rai1 = {'name': 'RAI', 'dose_value': 100., 'dose_unit': 'mCi', 'time': {'text': '2023'}}
    rai2 = {'name': 'RAI', 'dose_value': 150., 'dose_unit': 'mCi', 'time': {'text': '2024'}}
    absent = {'name': 'recurrence', 'assertion': 'absent'}
    planned = {'name': 'surgery', 'assertion': 'present', 'action': 'planned'}
    sodium = {'name': 'sodium', 'numeric_value': 115., 'unit': 'mmol/L'}
    expected = [fact('medications', rai1), fact('medications', rai2)]
    return [
        case('procedure_laterality_not_lesion', 'He underwent bilateral craniotomy for meningioma in 2009 and 2014.',
             [fact('conditions', men)], [fact('conditions', men)], [fact('conditions', {**men, 'laterality': 'bilateral'})]),
        case('allergy_concern_not_confirmed', 'Formula was started due to concerns regarding cow’s milk protein allergy.',
             [fact('allergies', allergy)], [fact('allergies', allergy)], [fact('allergies', {**allergy, 'assertion': 'present'})]),
        case('repeated_RAI_dose_time', 'Two cycles of RAI with 100 mCi (2023) and 150 mCi (2024).', expected, deepcopy(expected),
             [fact('medications', {**rai1, 'time': {'text': '2024'}}), fact('medications', {**rai2, 'time': {'text': '2023'}})]),
        case('patient_swap', 'Patient 1 had sodium 129 mmol/L. Patient 2 had sodium 115 mmol/L.',
             [fact('observations', sodium, 'challenge:p2')], [fact('observations', sodium, 'challenge:p2')], [fact('observations', sodium)]),
        case('unit_prefix_case', 'The administered activity was 100 mCi.', [fact('medications', rai1)], [fact('medications', rai1)],
             [fact('medications', {**rai1, 'dose_unit': 'MCi'})]),
        case('negative_and_planned', 'No recurrence was found. Surgery is planned.', [fact('conditions', absent), fact('procedures_devices', planned)],
             [fact('conditions', absent), fact('procedures_devices', planned)],
             [fact('conditions', {**absent, 'assertion': 'present'}), fact('procedures_devices', {**planned, 'action': 'completed'})]),
    ]


def score_counterfactual(fixture, predictions):
    """One-to-one exact typed proposition screening for synthetic controls only."""
    assigned = {}
    def augment(index, seen):
        for j, candidate in enumerate(predictions):
            if j in seen or not _match(fixture['expected'][index], without_evidence(candidate)):
                continue
            seen.add(j)
            if j not in assigned or augment(assigned[j], seen):
                assigned[j] = index
                return True
        return False
    hits = sum(augment(i, set()) for i in range(len(fixture['expected'])))
    return {'matched': hits, 'required': len(fixture['expected']), 'clinical_gold': False,
            'label_scope': 'synthetic_software_regression'}
