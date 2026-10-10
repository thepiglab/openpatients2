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
    annotation = json.loads((root / 'annotations' / f'{aid}.json').read_text())
    source = json.loads((root / 'sources' / f'{aid}.json').read_text())
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
    annotation = json.loads((root / 'annotations' / f'{aid}.json').read_text())
    source = json.loads((root / 'sources' / f'{aid}.json').read_text())
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
    if not (root / 'measurement-series' / f'{aid}.json').exists():
        pytest.skip('reference series not present')
    source = json.loads((root / 'sources' / f'{aid}.json').read_text())
    annotation = json.loads((root / 'annotations' / f'{aid}.json').read_text())
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
    (tmp_path / 'figure-ehr-labels').mkdir(); (tmp_path / 'figure-ehr-labels' / 'X.json').write_text(json.dumps({'figures': [figure]}))
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
