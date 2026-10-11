"""Per-patient case_context follows the article-derived packet rule (CTX-PER-PATIENT)."""
from openpatients2.dense import normalize


def test_rule_rejects_per_patient_multi_patient():
    from openpatients2.dense.validate import case_context_errors
    fact = lambda **fields: {'patients': [{'patient_id': 'p1', 'facts': [{'fact_id': 'p1-f1', 'task': 'case_context', 'fields': fields}]}]}
    assert case_context_errors(fact(case_kind='clinical_case', multiple_index_patients=False)) == []
    assert case_context_errors(fact(case_kind='multi_patient', multiple_index_patients=True))
    assert case_context_errors(fact(case_kind='clinical_case'))  # null/absent is not false
    from openpatients2.unified_record import case_context_warnings
    assert case_context_warnings({'patients': [{'patient_id': 'p1', 'sections': {'case_context': {'case_kind': 'multi_patient',
                                                                                                  'multiple_index_patients': True}}}]})


def test_normalize_fixes_only_case_context_fields():
    a = {'patients': [{'patient_id': 'p1', 'species': 'nonhuman', 'facts': [
        {'fact_id': 'p1-f1', 'task': 'case_context', 'fields': {'case_kind': 'multi_patient', 'multiple_index_patients': True}},
        {'fact_id': 'p1-f2', 'task': 'conditions', 'fields': {'name': 'x'}}]}]}
    new, changes = normalize.migrate_case_context(a)
    assert new['patients'][0]['facts'][0]['fields'] == {'case_kind': 'animal', 'multiple_index_patients': False}
    assert [c['fact_id'] for c in changes] == ['p1-f1'] and a['patients'][0]['facts'][0]['fields']['case_kind'] == 'multi_patient'
    assert normalize.diff_paths(a, new) == ['/patients/0/facts/0/fields/case_kind', '/patients/0/facts/0/fields/multiple_index_patients']
