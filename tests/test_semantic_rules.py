"""Mechanical shared-rule checks apply identically to runtime output and reference annotations."""
import copy
import json
from pathlib import Path
import sys

import pytest

from openpatients2.semantic_rules import (case_context_target_errors, item_violations, record_violations,
                                          roster_order_errors, visual_question_marks)
from openpatients2.validation import validate

ROOT = Path(__file__).resolve().parents[1]
from openpatients2.dense_store import load as load_store
store = load_store()
SOURCE = 'A 67-year-old man was admitted. Creatinine was 1.2 mg/dL. He never smoked. Biopsy was not performed.'
EV = [{'quote': 'Creatinine was 1.2 mg/dL', 'source_section': None}]
TIME = {'text': None, 'relation': 'unknown', 'anchor': None, 'date_iso': None}


def rules(task, item, collection='items', text=SOURCE):
    return {(v.rule_id, v.severity) for v in item_violations(task, collection, item, text)}


def observation(**kw):
    item = {'subject': 'index_patient', 'assertion': 'present', 'temporality': 'current', 'time': dict(TIME),
            'evidence': EV, 'result_absent_reason': None, 'name': 'creatinine', 'kind': 'laboratory', 'status': 'resulted',
            'text_value': None, 'numeric_value': 1.2, 'comparator': '=', 'unit': 'mg/dL', 'flag': 'unknown',
            'reference_range_text': None, 'specimen': None, 'method': None, 'body_site': None}
    item.update(kw)
    return item


def test_item_rules_are_errors_or_warnings_by_mechanical_certainty():
    assert ('OBS-COMPARATOR', 'error') in rules('observations', observation(comparator='unknown'))
    assert not rules('observations', observation())
    assert ('TIME-LITERAL', 'error') in rules('observations', observation(time={**TIME, 'anchor': 'index hospital admission'}))
    assert not rules('observations', observation(time={**TIME, 'anchor': 'Admitted'}))   # case-insensitive literal
    assert ('OBS-FLAG', 'warning') in rules('observations', observation(numeric_value=None, text_value='Normal'))
    assert ('OBS-NOT-PERFORMED', 'error') in rules('observations', observation(status='not_performed', assertion='absent'))
    absent = {'assertion': 'absent', 'verification_status': 'confirmed', 'name': 'PE', 'evidence': [{'quote': 'PE was ruled out'}]}
    assert ('ASSERT-ABSENT', 'error') in rules('conditions', absent)
    assert not rules('conditions', {**absent, 'verification_status': 'refuted'})
    assert ('SOC-NEVER', 'error') in rules('social_exposures', {'status': 'never', 'assertion': 'absent', 'evidence': [{'quote': 'never smoked'}]})
    age = {'attribute': 'age_at_presentation', 'numeric_value': 67.0, 'unit': 'years', 'evidence': [{'quote': 'A 67-year-old man'}]}
    assert ('DEMO-AGE-TEXT', 'error') in rules('demographics', age)
    assert not rules('demographics', {**age, 'text_value': '67-year-old'})
    assert ('ONC-BENIGN', 'warning') in rules('oncology', {'name': 'hepatic adenoma', 'evidence': []}, 'tumors')
    assert not rules('oncology', {'name': 'adenocarcinoma', 'evidence': []}, 'tumors')
    assert ('MED-ROUTE', 'warning') in rules('medications', {'route': 'IV', 'evidence': [{'quote': 'oral amoxicillin'}]})
    assert ('EVID-NEGATION', 'warning') in rules('symptoms_function', {'assertion': 'absent', 'evidence': [{'quote': 'fever'}]})


def test_runtime_validator_reports_rule_ids():
    section = {'coverage': 'complete', 'limitations': [], 'documentation_status': 'documented', 'documentation_evidence': [],
               'items': [observation(comparator='unknown')]}
    checked = validate('observations', section, SOURCE)
    assert any('[OBS-COMPARATOR]' in e for e in checked.errors)
    section['items'][0]['comparator'] = '='
    assert validate('observations', section, SOURCE).valid


def test_case_context_and_vital_status_are_record_rules():
    context = {'case_kind': 'multi_patient', 'multiple_index_patients': True, 'subject_state': 'living_at_presentation'}
    assert case_context_target_errors(context)[0].startswith('[CTX-PER-PATIENT]')
    assert not case_context_target_errors({'case_kind': 'clinical_case', 'multiple_index_patients': False})
    died = {'items': [{'event': 'death', 'assertion': 'present', 'subject': 'index_patient'}]}
    found = {v.rule_id for v in record_violations({'case_context': {**context, 'case_kind': 'clinical_case',
                                                                     'multiple_index_patients': False}, 'outcomes': died})}
    assert found == {'CTX-VITAL-STATUS'}
    ok = {'case_context': {'case_kind': 'clinical_case', 'multiple_index_patients': False, 'subject_state': 'deceased_during_course'},
          'outcomes': died}
    assert not record_violations(ok)


def test_roster_numbering_follows_first_mention():
    segments = [{'segment_id': 's1', 'text': 'Case A first. Case B second.'}]
    a = {'patient_id': 'p1', 'identity_evidence': [{'segment_id': 's1', 'quote': 'Case A'}]}
    b = {'patient_id': 'p2', 'identity_evidence': [{'segment_id': 's1', 'quote': 'Case B'}]}
    assert not roster_order_errors([a, b], segments)
    assert roster_order_errors([{**b, 'patient_id': 'p1'}, {**a, 'patient_id': 'p2'}], segments)
    assert roster_order_errors([a, {**b, 'patient_id': 'p3'}], segments)


def test_question_marks_only_inside_quoted_printed_text():
    assert not visual_question_marks('Flowchart nodes read “Unstable?” and "Signs of strangulation?".')
    assert visual_question_marks('Arrow points to the pancreas? or duodenum.')
    assert visual_question_marks('Then Mineralocorticoid excess? box')


def test_normalizer_keeps_route_spelling_and_drops_paraphrased_anchor():
    from openpatients2.clinical_normalization import normalize_clinical
    quote = 'intravenous vancomycin was started on admission'
    item = {'name': 'vancomycin', 'route': None, 'time': {'text': None, 'relation': 'after', 'anchor': 'index admission', 'date_iso': None},
            'evidence': [{'source_section': 's', 'quote': quote}]}
    value, audit = normalize_clinical('medications', {'items': [item]}, [{'segment_id': 's', 'text': quote}])
    assert value['items'][0]['route'] == 'intravenous'
    assert value['items'][0]['time']['anchor'] is None and value['items'][0]['time']['relation'] == 'after'
    assert {a['reason'] for a in audit} == {'time_anchor_not_literal_in_source', 'explicit_route_directly_attached_to_named_drug_in_own_evidence'}


@pytest.fixture
def dense(monkeypatch):
    monkeypatch.syspath_prepend(str(ROOT / 'benchmarks/dense500'))
    import importlib
    return importlib.import_module('validate'), importlib.import_module('schema_targets')


def test_annotation_and_materialized_target_share_the_rules_and_timeline_vocabulary(dense):
    validator, targets = dense
    root = ROOT / 'benchmarks/dense50'; aid = 'PMC13624771.1'
    annotation = json.loads(store.path(root, 'annotations', aid).read_text())
    source = json.loads(store.path(root, 'sources', aid).read_text())
    patient = annotation['patients'][0]
    patient['events'][0]['occurrence'] = 'not_occurred'
    if len(patient['events']) > 1:
        patient['temporal_relations'].append({'from_event_id': patient['events'][0]['event_id'], 'relation': 'unknown',
            'to_event_id': patient['events'][1]['event_id'], 'evidence': patient['events'][1]['evidence']})
    assert not validator.validate(annotation, source) and not validator.semantic_errors(annotation, source)
    target = targets.materialize(annotation, source)
    timeline = target['patients'][0]['companions']['timeline_v2']
    assert timeline['events'][0]['occurrence'] == 'not_occurred'   # copied, never remapped
    if len(patient['events']) > 1:
        assert any(e['relation'] == 'unknown' for e in timeline['edges'])
    bad = copy.deepcopy(annotation)
    obs = next(f for f in bad['patients'][0]['facts'] if f['task'] == 'observations' and f['fields'].get('numeric_value') is not None)
    obs['fields']['comparator'] = 'unknown'
    assert any('[OBS-COMPARATOR]' in e for e in validator.semantic_errors(bad, source))
    assert any('[OBS-COMPARATOR]' in e['error'] for e in targets.materialize(bad, source)['source_gate_errors'])
    patient['events'][0]['occurrence'] = 'uncertain'   # legacy spelling is no longer accepted
    assert any('occurrence' in e for e in validator.validate(annotation, source))


def test_review_only_facts_are_exported_as_flagged_items(dense):
    _, targets = dense
    root = ROOT / 'benchmarks/dense50'; aid = 'PMC13294519.1'
    annotation = json.loads(store.path(root, 'annotations', aid).read_text())
    source = json.loads(store.path(root, 'sources', aid).read_text())
    bundle = targets.materialize(annotation, source)['patients'][0]
    quarantined = bundle['reference_provenance']['quarantined_facts']
    assert quarantined
    exported = targets.with_review_only_items(bundle)
    flagged = [i for s in exported['sections'].values() for c in ('items', 'tumors', 'biomarkers', 'treatments')
               for i in s.get(c, []) if i.get('scoring') == 'review_only']
    flagged += [i for s in exported['sections'].values() for i in s.get('review_only_facts', [])]
    assert sorted(i['fact_id'] for i in flagged) == sorted(f['fact_id'] for f in quarantined)
    assert all(i['evidence'] for i in flagged)
    assert bundle['sections'] != exported['sections'] and 'scoring' not in json.dumps(bundle['sections'])


def test_series_round_trip_uses_reviewed_supplement_transcriptions(monkeypatch):
    monkeypatch.syspath_prepend(str(ROOT / 'benchmarks/dense500'))
    from reference_gates import media_gates
    root = ROOT / 'benchmarks/dense500'; aid = 'PMC12786310.1'
    if not store.path(root, 'measurement-series', aid).exists():
        pytest.skip('reference series not present')
    source = json.loads(store.path(root, 'sources', aid).read_text())
    annotation = json.loads(store.path(root, 'annotations', aid).read_text())
    errors, _ = media_gates(root, source, annotation, None)
    assert not [e for e in errors if e['gate'].startswith('ehr.measurement_series')]


def test_dangling_time_relation_and_never_wording():
    assert ('TIME-LITERAL', 'error') in rules('observations', observation(time={**TIME, 'relation': 'after'}))
    assert not rules('observations', observation(time={**TIME, 'relation': 'after', 'anchor': 'admitted'}))
    contact = {'status': 'never', 'assertion': 'present', 'evidence': [{'quote': 'no known contact with tuberculosis'}]}
    assert rules('social_exposures', contact) == {('SOC-NEVER', 'warning')}
    assert ('SOC-NEVER', 'error') in rules('social_exposures', {**contact, 'assertion': 'absent', 'evidence': [{'quote': 'He never smoked'}]})
    assert not rules('social_exposures', {**contact, 'evidence': [{'quote': 'lifelong non-smoker'}]})


def test_upper_bound_wording_takes_less_or_equal():
    fever = observation(name='fever', numeric_value=38.0, unit='°C', evidence=[{'quote': 'fever up to 38°C'}])
    assert rules('observations', fever) == {('OBS-COMPARATOR', 'warning')}
    assert not rules('observations', {**fever, 'comparator': '<='})
    peak = observation(name='peak creatinine', numeric_value=6.2, evidence=[{'quote': 'to a peak of 6.2 mg/dL'}])
    assert not rules('observations', peak)   # a measured peak stated as a value stays '='


def test_positional_and_patient_name_panel_labels_warn_on_sidecars():
    from openpatients2.semantic_rules import panel_label_issue, panel_label_violations
    assert panel_label_issue('upper right') == panel_label_issue('Left image') == panel_label_issue('Main image') == 'positional'
    assert panel_label_issue('Case 1') == 'patient_name'
    assert panel_label_issue('A') is None and panel_label_issue(None) is None and panel_label_issue('B2') is None
    found = panel_label_violations([{'panel': 'A'}, {'panel': 'lower left'}, {'panel': 'Patient 2'}], 'FIG1')
    assert [(v.rule_id, v.severity, v.path) for v in found] == [('FIG-LABELS', 'warning', 'FIG1/panels/1'),
                                                                ('FIG-LABELS', 'warning', 'FIG1/panels/2')]


def test_reference_gates_report_positional_panel_labels_as_warnings(tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(ROOT / 'benchmarks/dense500'))
    from reference_gates import _panel_label_warnings
    figure = {'figure_id': 'F1', 'panels': [{'panel': 'left'}, {'panel': 'A'}]}
    visual = {'figures': [{'figure_id': 'F1', 'analysis': {'visual': {'panels': [{'panel': 'left'}, {'panel': 'A'}]}}}]}
    store.path(tmp_path, 'figure-ehr-labels', 'X', mkdir=True).write_text(json.dumps({'figures': [figure]}))
    warnings = []
    _panel_label_warnings(tmp_path, 'X', visual, warnings)
    assert len(warnings) == 1 and warnings[0]['gate'] == 'figure.FIG-LABELS'
    assert 'visual-submissions, figure-ehr-labels' in warnings[0]['detail']


def test_references_must_point_to_existing_facts_and_events():
    from openpatients2.semantic_rules import annotation_violations, reference_errors
    patient = {'patient_id': 'p1', 'facts': [{'fact_id': 'p1-f1'}, {'fact_id': 'p1-f2', 'alternate_representation_of': 'p1-f1'}],
               'events': [{'event_id': 'e1', 'fact_ids': ['p1-f1']}],
               'temporal_relations': [{'from_event_id': 'e1', 'to_event_id': 'e1'}],
               'summary': [{'fact_ids': ['p1-f2']}], 'ambiguities': [{'fact_ids': ['p1-f1']}]}
    assert not reference_errors(patient)
    broken = copy.deepcopy(patient)
    broken['facts'][1]['alternate_representation_of'] = 'p1-f030'   # removed duplicate
    broken['events'][0]['fact_ids'].append('p1-f9'); broken['temporal_relations'][0]['to_event_id'] = 'e9'
    broken['summary'][0]['fact_ids'] = ['p2-f1']; broken['ambiguities'][0]['fact_ids'] = ['p1-f7']
    found = [(v.rule_id, v.severity, v.path) for v in reference_errors(broken)]
    assert found == [('REF-INTEGRITY', 'error', 'p1-f2'), ('REF-INTEGRITY', 'error', 'e1'), ('REF-INTEGRITY', 'error', 'p1'),
                     ('REF-INTEGRITY', 'error', 'p1/summary/0'), ('REF-INTEGRITY', 'error', 'p1/ambiguities/0')]
    source = {'segments': [{'segment_id': 's1', 'text': 'x'}]}
    assert any(v.rule_id == 'REF-INTEGRITY' for v in annotation_violations({'patients': [broken]}, source))


def test_one_date_iso_rule_for_runtime_and_reference():
    from openpatients2.semantic_rules import date_iso_errors
    assert not date_iso_errors('2025-05-20', 'May 20, 2025', 'on May 20, 2025 he')
    assert not date_iso_errors('2025-05-20', '20th May 2025') and not date_iso_errors('2025-05-20', 'on 2025-05-20')
    assert date_iso_errors('2025-05-20', '05/20/2025') and date_iso_errors('2025-05-01', 'May 2025')
    assert date_iso_errors('2025-02-30', 'February 30, 2025') and date_iso_errors('2025-05-21', 'May 20, 2025')
    text = 'On May 20, 2025 creatinine was 1.2 mg/dL.'
    obs = observation(evidence=[{'quote': text, 'source_section': None}],
                      time={'text': 'May 20, 2025', 'relation': 'at', 'anchor': None, 'date_iso': '2025-05-20'})
    section = {'coverage': 'complete', 'limitations': [], 'documentation_status': 'documented',
               'documentation_evidence': [], 'items': [obs]}
    assert validate('observations', section, text).valid and not rules('observations', obs, text=text)
    bad = {**obs, 'time': {**obs['time'], 'text': '05/20/2025'}}
    assert ('TIME-LITERAL', 'error') in rules('observations', bad, text=text)


def test_by_time_is_an_upper_bound():
    by = observation(time={'text': 'by day 3', 'relation': 'at', 'anchor': None, 'date_iso': None},
                     evidence=[{'quote': 'Creatinine was 1.2 mg/dL by day 3'}])
    assert rules('observations', by) == {('TIME-LITERAL', 'warning')}
    assert not rules('observations', {**by, 'time': {**by['time'], 'relation': 'before'}})


def test_time_only_extra_quote_is_borrowed_time():
    def item(quotes, text='By Day 3'):
        return {'name': 'creatinine', 'assertion': 'present', 'evidence': [{'quote': q} for q in quotes],
                'time': {'text': text, 'relation': 'before', 'anchor': None, 'date_iso': None}}
    borrowed = item(['Creatinine rose to 2.1 mg/dL', 'By Day 3'])
    assert ('NO-BORROWING', 'warning') in rules('symptoms_function', borrowed)
    neighbour = item(['Creatinine rose to 2.1 mg/dL', 'By Day 3 the rash had resolved and fever subsided'])
    assert ('NO-BORROWING', 'warning') in rules('symptoms_function', neighbour)
    own = item(['By Day 3 creatinine rose to 2.1 mg/dL', 'Creatinine peaked later'])
    assert ('NO-BORROWING', 'warning') not in rules('symptoms_function', own)
    table = item(['Creatinine | 2.1', 'Day | By Day 3 | Day 5'])
    assert ('NO-BORROWING', 'warning') not in rules('symptoms_function', table)
    assert ('NO-BORROWING', 'warning') not in rules('symptoms_function', item(['By Day 3 creatinine rose']))


def test_series_legend_cells_round_trip_in_both_pipelines(monkeypatch):
    """Regression (PMC12510625.1 shape): a cell_status legend cell from a table footnote is a valid cell id in
    the reference validator, the reference_gates round trip and the runtime extraction check."""
    from openpatients2 import ehr_tasks
    from openpatients2.measurement_series import legend_cells, validate_series
    footnote = ('IM = intramuscular; PO = by mouth. a Emergency or as needed dose. b Patient refusal indicated by '
                '“-.” c Medication discontinuation indicated by “d/c.”')
    source = {'article_id': 'A', 'text_sha256': 'h', 'segments': [
        {'segment_id': 't1', 'kind': 'table_row', 'table_id': 'T1', 'text': 'Drug | Day 1 | Day 2 | Day 3\nHaloperidol | 5 IM^(a) | -^(b) | d/c^(c)'},
        {'segment_id': 'f1', 'kind': 'table_footnote', 'table_id': 'T1', 'text': footnote}]}
    annotation = {'article_id': 'A', 'text_sha256': 'h', 'patients': [{'patient_id': 'p1'}]}
    legends = {c['raw_text']: c['cell_id'] for c in legend_cells(source)}
    assert legends[footnote] == 'f1-fn' and 'b Patient refusal indicated by “-.”' in legends
    draft = ehr_tasks.series_draft(source)
    statuses = {'t1-c1': ('prn_or_emergency', 'a Emergency or as needed dose.'),
                't1-c2': ('refused', 'b Patient refusal indicated by “-.”'),
                't1-c3': ('discontinued', 'c Medication discontinuation indicated by “d/c.”')}
    # Runtime: the model selects the draft's own legend cells.
    output = {'series': [{'name_cell_id': 't1-c0', 'measurement_name': 'Haloperidol', 'measurement_kind': 'medication_dose',
                          'ownership': 'individual', 'patient_ids': ['p1'],
                          'ownership_evidence': [{'segment_id': 't1', 'cell_id': None, 'quote': 'Haloperidol'}],
                          'reference_range_cell_id': None,
                          'points': [{'value_cell_id': cid, 'time_cell_id': f't1-h{cid[-1]}', 'time_text': f'Day {cid[-1]}',
                                      'unit_cell_id': None, 'unit_text': None, 'offset_value': None, 'offset_unit': None,
                                      'offset_anchor_text': None, 'offset_basis': None,
                                      'cell_status': status, 'legend_cell_id': legends[legend]}
                                     for cid, (status, legend) in statuses.items()]}]}
    doc = ehr_tasks.check_series(output, source, annotation, draft)
    assert [p['cell_status'] for p in doc['series'][0]['points']] == ['prn_or_emergency', 'refused', 'discontinued']
    # Reference: annotator-defined legend cells (own ids) round-trip through the gate's draft (extra article_text cells).
    reference = {**doc, 'cells': [c for c in doc['cells'] if not c['cell_id'].startswith('f1-fn')]}
    ref_ids = {}
    for status, legend in statuses.values():
        cid = f'f1-leg-{status}'; ref_ids[legend] = cid
        reference['cells'].append({**next(c for c in doc['cells'] if c['raw_text'] == legend), 'cell_id': cid})
    for p in reference['series'][0]['points']:
        p['legend_cell_id'] = ref_ids[statuses[p['value_cell_id']][1]]
    validate_series(reference, source, annotation)
    gate_draft = ehr_tasks.series_draft(source, None, [c for c in reference['cells']
                                                       if c['origin'] in ('supplement_transcription', 'article_text')])
    ehr_tasks.check_series(ehr_tasks.output_from_sidecar('measurement_series_extraction', reference), source, annotation, gate_draft)
    without = ehr_tasks.series_draft(source, None, [])
    with pytest.raises(ValueError, match='Unknown cell'):
        ehr_tasks.check_series(ehr_tasks.output_from_sidecar('measurement_series_extraction', reference), source, annotation, without)


def test_event_classification_lives_in_the_annotation(dense, monkeypatch):
    validator, targets = dense
    import importlib
    schema_review = importlib.import_module('schema_review')
    root = ROOT / 'benchmarks/dense50'
    for aid in store.matching_ids(root, 'annotations', 'PMC*'):   # read-only: first article with a timed event
        annotation = json.loads(store.path(root, 'annotations', aid).read_text())
        source = json.loads(store.path(root, 'sources', aid).read_text())
        if any(e.get('time_text') for e in annotation['patients'][0]['events']):
            baseline = targets.materialize(annotation, source)
            if not baseline['source_gate_errors'] and not validator.validate(annotation, source):
                break
    else:
        pytest.skip('no clean dense50 article with a timed event')
    event = next(e for e in annotation['patients'][0]['events'] if e.get('time_text'))
    event.update(kind='follow_up', time_kind='relative', time_precision='approximate')
    assert not validator.validate(annotation, source)
    target = targets.materialize(annotation, source)
    got = next(e for e in target['patients'][0]['companions']['timeline_v2']['events'] if e['event_id'] == event['event_id'])
    assert got['kind'] == 'follow_up' and got['times'][0]['kind'] == 'relative' and got['times'][0]['precision'] == 'approximate'
    # Unclassified events keep the old defaults, so existing annotations materialize unchanged.
    others = [e for e in target['patients'][0]['companions']['timeline_v2']['events'] if e['event_id'] != event['event_id']]
    assert others == [e for e in baseline['patients'][0]['companions']['timeline_v2']['events'] if e['event_id'] != event['event_id']]
    # A submission patched to disagree with the annotation is rejected; matching it passes the classification check.
    patched = copy.deepcopy(target)
    pe = next(e for e in patched['patients'][0]['companions']['timeline_v2']['events'] if e['event_id'] == event['event_id'])
    pe['kind'] = 'other'
    from openpatients2.longitudinal import relative_order
    patched['patients'][0]['relative_timeline'] = relative_order(patched['patients'][0]['companions']['timeline_v2'])
    with pytest.raises(ValueError, match='set in the annotation'):
        schema_review.validate_submission(patched, annotation, source)
    # Invalid classification values are annotation errors.
    event.update(kind='visit', time_kind='calendar', time_calendar_value='1999')
    errors = validator.validate(annotation, source)
    assert any('kind' in e for e in errors) and any('time_calendar_value' in e for e in errors)


def test_merge_prefers_annotation_classification_over_old_submission(monkeypatch):
    monkeypatch.syspath_prepend(str(ROOT / 'benchmarks/dense500'))
    import importlib.util
    spec = importlib.util.spec_from_file_location('semantic_migration_merge', ROOT / 'benchmarks/dense500/migrations/semantic_rules_2026_10.py')
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    def target(kind, tkind):
        return {'roster': {'reported_individual_count': 1, 'roster_complete': True, 'limitations': [], 'patients': [{'patient_id': 'p1'}]},
                'figure_attribution': [], 'patients': [{'source': {'patient_target': {'patient_id': 'p1'}}, 'vision': {},
                'companions': {'timeline_v2': {'schema_version': 'patient-timeline/2', 'record_id': 'r', 'edges': [], 'limitations': [],
                    'events': [{'event_id': 'e1', 'kind': kind, 'times': [{'kind': tkind, 'precision': 'unknown', 'calendar_value': None}]},
                               {'event_id': 'e2', 'kind': kind, 'times': []}]}}}]}
    monkeypatch.setattr(module, 'copy', copy)
    import openpatients2.longitudinal as lg
    monkeypatch.setattr(lg, 'relative_order', lambda timeline: [])
    old, regenerated = target('test', 'relative'), target('treatment', 'order_only')
    annotation = {'patients': [{'events': [{'event_id': 'e1', 'kind': 'treatment', 'time_kind': 'order_only'}, {'event_id': 'e2'}]}]}
    merged = module.merge_submission(old, regenerated, annotation)['patients'][0]['companions']['timeline_v2']['events']
    assert merged[0]['kind'] == 'treatment' and merged[0]['times'][0]['kind'] == 'order_only'   # annotation wins
    assert merged[1]['kind'] == 'test'                                                          # fallback to old edit


def test_negation_word_on_a_present_item_warns():
    afebrile = {'name': 'temperature', 'kind': 'sign', 'assertion': 'present', 'text_value': 'afebrile',
                'evidence': [{'quote': 'He was afebrile'}]}
    assert ('EVID-NEGATION', 'warning') in rules('symptoms_function', afebrile)
    assert ('EVID-NEGATION', 'warning') in rules('symptoms_function', {**afebrile, 'name': 'Asymptomatic', 'text_value': None})
    fever = {**afebrile, 'name': 'fever', 'assertion': 'absent', 'text_value': None}
    assert not rules('symptoms_function', fever)                        # the negated finding, quote keeps the word
    normal = {**afebrile, 'name': 'abdominal examination', 'text_value': 'unremarkable', 'evidence': [{'quote': 'abdomen unremarkable'}]}
    assert ('EVID-NEGATION', 'warning') not in rules('symptoms_function', normal)   # scoped normal exam stays present


def test_governed_time_quotes_are_not_borrowing():
    from openpatients2.semantic_rules import borrowed_time_quote
    def item(name, evidence, text):
        return {'name': name, 'evidence': evidence, 'time': {'text': text, 'relation': 'at', 'anchor': None, 'date_iso': None}}
    caption = item('BUN', [{'segment_id': 't1', 'quote': 'BUN | 2.5 mmol/L | 2.1 - 8.5 mmol/L'},
                           {'segment_id': 't1', 'quote': 'Laboratory results for the patient on the day of surgery'}], 'on the day of surgery')
    assert not borrowed_time_quote(caption)                               # table caption in the row's segment
    other_row = item('Neutrophil count', [{'segment_id': 'r2', 'quote': 'Neutrophil count | 12.5'},
                                          {'segment_id': 'r1', 'quote': 'Blood investigations on Day 3, at deterioration'}], 'Day 3')
    assert borrowed_time_quote(other_row) and not borrowed_time_quote(other_row, {'r1': 'TAB2', 'r2': 'TAB2'})
    ddlt = item('Deceased donor liver transplantation', [{'segment_id': 'b3', 'quote': 'he eventually underwent DDLT 149 days after registration.'},
                                                          {'segment_id': 'b1', 'quote': 'HCC secondary to alcoholic cirrhosis'}], '149 days after registration')
    assert not borrowed_time_quote(ddlt)                                  # abbreviation by initials
    ef = item('Left ventricular ejection fraction', [{'segment_id': 'b1', 'quote': 'LVEF | 45%'},
                                                     {'segment_id': 'b9', 'quote': 'After eight months, EF improved to 55%'}], 'After eight months')
    assert not borrowed_time_quote(ef)
    learned = item('pulmonary embolism', [{'segment_id': 'b1', 'quote': 'acute pulmonary embolism (PE) was suspected'},
                                          {'segment_id': 'b2', 'quote': 'On day 7 the PE resolved'}], 'On day 7')
    assert not borrowed_time_quote(learned)                               # 'long form (ABBR)' learned from own quotes
    anaphor = item('ptosis', [{'segment_id': 'b4', 'quote': 'He had left ptosis and diplopia.'},
                              {'segment_id': 'b4', 'quote': 'These signs resolved by day 10.'}], 'by day 10')
    assert not borrowed_time_quote(anaphor)
    neighbour = item('ptosis', [{'segment_id': 'b4', 'quote': 'He had left ptosis.'},
                                {'segment_id': 'b5', 'quote': 'By day 10 the rash had faded.'}], 'By day 10')
    assert borrowed_time_quote(neighbour)



def test_table_footnote_and_figure_caption_govern_their_rows_and_panels():
    from openpatients2.semantic_rules import borrowed_time_quote, segment_tables_of, time_in_other_sentence
    def item(name, evidence, text):
        return {'name': name, 'evidence': evidence, 'time': {'text': text, 'relation': 'at', 'anchor': None, 'date_iso': None}}
    groups = segment_tables_of([{'segment_id': 'row', 'table_id': 'tbl1', 'text': 'Patient 1 | Nickel | ++'},
                                {'segment_id': 'foot', 'table_id': 'tbl1', 'kind': 'table_footnote', 'text': 'Readings at Day 4 (96 hour).'},
                                {'segment_id': 'cap', 'figure_id': 'fig2', 'kind': 'figure_caption', 'text': '...'},
                                {'segment_id': 'b9', 'text': 'Running text.'}])
    assert groups == {'row': 'tbl1', 'foot': 'tbl1', 'cap': 'figure:fig2'}
    patch = item('nickel sulfate', [{'segment_id': 'row', 'quote': 'Nickel | ++'},
                                    {'segment_id': 'foot', 'quote': 'Readings at Day 4 (96 hour).'}], 'Day 4 (96 hour)')
    assert not borrowed_time_quote(patch, groups) and borrowed_time_quote(patch, {})
    caption = item('erosions', [{'segment_id': 'cap', 'quote': 'Presentation after 3 doses of methotrexate. '
                                 'A, Ulceration on the jaw. B, Extensive erosions on the upper extremities.'}],
                   'after 3 doses of methotrexate')
    assert not time_in_other_sentence(caption, groups)
    running = item('erosions', [{'segment_id': 'b9', 'quote': 'She was seen after 3 doses of methotrexate. '
                                 'Extensive erosions were later noted on the upper extremities.'}], 'after 3 doses of methotrexate')
    assert time_in_other_sentence(running, groups)

def test_british_spellings_and_medical_combining_forms_are_not_glued():
    from openpatients2.glued_text import find_glued
    for word in ('haematologic', 'oesophageal', 'tumour', 'paediatric', 'anaesthetised', 'chromoendoscopic', 'esophagoscopic'):
        assert not find_glued(word), word
    assert [g.segmentation for g in find_glued('aftersevenmonths')] == ['after seven months']


def test_calendar_value_uses_the_shared_literal_date_parser():
    from openpatients2.semantic_rules import calendar_values
    from openpatients2.longitudinal import TimeExpression, annotation_event_errors
    assert calendar_values('October 26, 2022') == {'2022-10-26', '2022'}
    assert '2022-10' in calendar_values('in October 2022') and '2022-10-26' not in calendar_values('in October 2022')
    assert not calendar_values('10/26/2022') - {'2022'}                  # numeric non-ISO: year only
    event = {'event_id': 'e1', 'occurrence': 'occurred', 'time_text': 'October 26, 2022', 'time_kind': 'calendar',
             'time_calendar_value': '2022-10-26'}
    assert not annotation_event_errors(event)
    assert annotation_event_errors({**event, 'time_calendar_value': '2022-10-27'})
    assert annotation_event_errors({**event, 'time_text': 'in October 2022'}) and not annotation_event_errors(
        {**event, 'time_text': 'in October 2022', 'time_calendar_value': '2022-10'})
    quote = 'surgery on October 26, 2022'
    span = {'source_id': 'A.1:jats', 'segment_id': 's1', 'segment_sha256': 'a' * 64, 'start': 0, 'end': len(quote), 'quote': quote}
    expression = {'text': 'October 26, 2022', 'kind': 'calendar', 'precision': 'exact', 'calendar_value': '2022-10-26',
                  'evidence': [span]}
    TimeExpression.model_validate(expression)                            # runtime timeline accepts the same value
    with pytest.raises(ValueError, match='Calendar normalization'):
        TimeExpression.model_validate({**expression, 'calendar_value': '2022-10-27'})


def test_uneventful_outcome_is_exempt_and_gate_verdict_is_explicit(monkeypatch):
    course = {'event': 'recovery', 'assertion': 'present', 'text_value': 'Postoperative course uneventful',
              'evidence': [{'quote': 'Postoperative course uneventful'}]}
    assert ('EVID-NEGATION', 'warning') not in rules('outcomes', course)
    assert ('EVID-NEGATION', 'warning') in rules('outcomes', {**course, 'text_value': 'asymptomatic at follow-up'})
    assert ('EVID-NEGATION', 'warning') in rules('procedures_devices', {**course, 'name': 'uneventful surgery'})
    monkeypatch.syspath_prepend(str(ROOT / 'benchmarks/dense500'))
    from reference_gates import verdict
    assert verdict({'article_id': 'A', 'passed': True, 'errors': [], 'warnings': [{}, {}]}) == 'A: PASSED (2 warnings, non-blocking)'
    assert verdict({'article_id': 'A', 'passed': False, 'errors': [{}], 'warnings': [{}]}).startswith('A: FAILED (1 blocking error')


def test_time_in_another_sentence_of_one_quote():
    from openpatients2.semantic_rules import time_in_other_sentence
    span = ('One month later, he underwent radioiodine ablation. On follow-up three months post-RAI, he had become '
            'euthyroid. Notably, a repeat echocardiogram showed normal chamber sizes.')
    item = {'name': 'repeat echocardiogram follow-up', 'evidence': [{'quote': span}],
            'time': {'text': 'three months post-RAI', 'relation': 'after', 'anchor': None, 'date_iso': None}}
    assert time_in_other_sentence(item)
    assert ('NO-BORROWING', 'warning') in rules('observations', {**observation(), **item})
    same = {**item, 'evidence': [{'quote': 'Three months post-RAI, a repeat echocardiogram was normal. He was well.'}],
            'time': {**item['time'], 'text': 'months post-RAI'}}
    assert not time_in_other_sentence(same)
    scoping = {**item, 'evidence': [{'quote': 'On admission: vital signs were stable. A repeat echocardiogram was normal.'}],
               'time': {**item['time'], 'text': 'On admission'}}
    assert not time_in_other_sentence(scoping)
    anaphor = {**item, 'name': 'headache', 'evidence': [{'quote': 'She had severe headache. These symptoms began after exercise.'}],
               'time': {**item['time'], 'text': 'after exercise'}}
    assert not time_in_other_sentence(anaphor)
    caption = {**item, 'name': 'malocclusion', 'evidence': [{'quote': 'Figure 6. Photograph taken 3 months post-op. Malocclusion is visible.'}],
               'time': {**item['time'], 'text': '3 months post-op'}}
    assert not time_in_other_sentence(caption)


def test_glued_text_in_sidecars_and_printed_text_is_exempt():
    from openpatients2.semantic_rules import sidecar_glued_violations
    visual = {'figures': [{'analysis': {'visual': {'detailed_description': 'Follow-up to2years; legend reads "Type1orType2Diabetes".',
                                                   'panels': [{'general_description': 'Timestamp and gridlines on a printout.'}]}}}]}
    found = [v.message for v in sidecar_glued_violations('visual', visual)]
    assert len(found) == 1 and 'to2years' in found[0]
    manifest = {'files': [{'machine': {'peek': 'aftersevenmonths'}, 'review': {'notes': 'Video recorded aftersevenmonths of therapy'}}]}
    assert [v.rule_id for v in sidecar_glued_violations('supplement_manifest', manifest)] == ['GLUED-TEXT']   # machine block skipped


def test_letter_digit_and_punctuation_glue_warn_but_notation_does_not():
    from openpatients2.glued_text import find_glued, warn_worthy
    def warned(text, source=None):
        return [g.token for g in find_glued(text, source) if warn_worthy(g)]
    assert warned('Patient1 had fever') == ['Patient1'] and warned('Case1: 28-year-old') == ['Case1']
    assert warned('a diameter35mm lesion') == ['diameter35mm'] and warned('seen on day1–4') == ['day1']
    assert warned('HST,CPAP titration') == ['HST,CPAP']
    for notation in ('VO2peak was 20', 'HbA1c 7%', 'MgSO4 given', 'T1 and T2', '8Fr sheath', 'IL6 level', '1,000 cells'):
        assert not warned(notation), notation
    assert warned('reached Jun2022', 'It started in Jun 2022.') == ['Jun2022']   # spaced form attested in source


def test_table_heading_governs_time_in_runtime_packets():
    from openpatients2.semantic_rules import borrowed_time_quote, segment_tables_of
    packet = [{'segment_id': 'b38', 'text': 'Table 2\nBlood investigations on Day 3\nParameter | Result'},
              {'segment_id': 'b40', 'text': 'Table 2\nBlood investigations on Day 3\nNeutrophil count | 12.5'}]
    item = {'name': 'Neutrophil count', 'time': {'text': 'Day 3', 'relation': 'at', 'anchor': None, 'date_iso': None},
            'evidence': [{'source_section': 'b40', 'quote': 'Neutrophil count | 12.5'},
                         {'source_section': 'b38', 'quote': 'Blood investigations on Day 3'}]}
    assert borrowed_time_quote(item) and not borrowed_time_quote(item, segment_tables_of(packet))
    cell = {'name': 'Sulfasalazine', 'time': {'text': 'during overlap', 'relation': 'during', 'anchor': None, 'date_iso': None},
            'evidence': [{'segment_id': 'b12', 'quote': 'Sulfasalazine (stopped after <4 weeks)'},
                         {'segment_id': 'b12', 'quote': 'Conventional DMARD use during overlap'}]}
    assert not borrowed_time_quote(cell, {'b12': 'T1'})                  # column heading of the same table row


def test_roster_block_in_the_annotation_is_the_single_source(dense):
    validator, targets = dense
    import importlib
    schema_review = importlib.import_module('schema_review')
    root = ROOT / 'benchmarks/dense50'
    for aid in store.matching_ids(root, 'annotations', 'PMC*'):     # read-only: a clean single-patient article
        annotation = json.loads(store.path(root, 'annotations', aid).read_text())
        source = json.loads(store.path(root, 'sources', aid).read_text())
        if len(annotation['patients']) == 1 and 'roster' not in annotation and not validator.validate(annotation, source):
            baseline = targets.materialize(annotation, source)
            if not baseline['source_gate_errors']:
                break
    else:
        pytest.skip('no clean single-patient dense50 article')
    segment = source['segments'][0]
    annotation['roster'] = {'reported_individual_count': None, 'roster_complete': True, 'evidence': []}
    assert not validator.validate(annotation, source)
    target = targets.materialize(annotation, source)
    assert target['roster']['roster_complete'] is True
    assert not any('await explicit schema review' in x for x in target['roster']['limitations'])
    patched = copy.deepcopy(target); patched['roster']['roster_complete'] = False
    with pytest.raises(ValueError, match='roster block'):
        schema_review.validate_submission(patched, annotation, source)
    # A count needs literal evidence stating the number.
    annotation['roster'] = {'reported_individual_count': 1, 'roster_complete': True, 'evidence': []}
    assert any('literal evidence quote' in e for e in validator.validate(annotation, source))
    annotation['roster'] = {'reported_individual_count': 97, 'roster_complete': False,
                            'evidence': [{'segment_id': segment['segment_id'], 'quote': segment['text'][:40]}]}
    assert any('literal evidence quote' in e for e in validator.validate(annotation, source))   # quote does not state 97
    from validate import roster_block_errors
    two = {'patients': [{}, {}], 'roster': {'reported_individual_count': 2, 'roster_complete': True,
                                            'evidence': [{'segment_id': 'b1', 'quote': 'We describe two patients'}]}}
    assert not roster_block_errors(two)
    assert roster_block_errors({**two, 'patients': [{}]})                # complete roster contradicts the count
    assert roster_block_errors({**two, 'roster': {**two['roster'], 'extra': 1}})


def test_merge_keeps_old_roster_only_without_annotation_block(monkeypatch):
    monkeypatch.syspath_prepend(str(ROOT / 'benchmarks/dense500'))
    import importlib.util
    spec = importlib.util.spec_from_file_location('semantic_migration_roster', ROOT / 'benchmarks/dense500/migrations/semantic_rules_2026_10.py')
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    import openpatients2.longitudinal as lg
    monkeypatch.setattr(lg, 'relative_order', lambda timeline: [])
    def target(count, complete):
        return {'roster': {'reported_individual_count': count, 'roster_complete': complete, 'limitations': [], 'patients': []},
                'figure_attribution': [], 'patients': []}
    old, regenerated = target(2, True), target(None, False)
    assert module.merge_submission(old, regenerated, {'patients': []})['roster']['reported_individual_count'] == 2
    merged = module.merge_submission(old, target(3, True), {'patients': [], 'roster': {'reported_individual_count': 3}})
    assert merged['roster']['reported_individual_count'] == 3


def test_singular_case_statement_supports_roster_count_one(monkeypatch):
    monkeypatch.syspath_prepend(str(ROOT / 'benchmarks/dense500'))
    from validate import roster_block_errors
    def block(quote, count=1):
        return {'patients': [{}], 'roster': {'reported_individual_count': count, 'roster_complete': True,
                                              'evidence': [{'segment_id': 'b1', 'quote': quote}]}}
    for quote in ('We present a 67-year-old woman with fever', 'We report a case of Kikuchi disease', 'a patient with',
                  'one patient', 'We describe an 8-month-old infant'):
        assert not roster_block_errors(block(quote)), quote
    assert roster_block_errors(block('Patients with this disease often present late'))
    assert roster_block_errors({**block('We present a 67-year-old woman', 2), 'patients': [{}, {}]})   # singular != 2
