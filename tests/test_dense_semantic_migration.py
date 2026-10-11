"""The shared-rules data migration is literal, conservative and idempotent."""
import copy
import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
from openpatients2.dense_store import load as load_store
store = load_store()


def load(monkeypatch):
    monkeypatch.syspath_prepend(str(ROOT / 'benchmarks/dense500'))
    spec = importlib.util.spec_from_file_location('semantic_migration', ROOT / 'benchmarks/dense500/migrations/semantic_rules_2026_10.py')
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    return module


def fact(fid, task, fields, quote, collection='items', scoring='include', certainty='supported'):
    return {'fact_id': fid, 'task': task, 'collection': None if task == 'case_context' else collection, 'fields': fields,
            'evidence': [{'segment_id': 's1', 'quote': quote}], 'scoring': scoring, 'certainty': certainty}


TEXT = ('A 67-year-old man presented on admission. Creatinine was 1.2 mg/dL and CRP <5 mg/L. Chest X-ray: normal. '
        'He had no history of diabetes. Pulmonary embolism was ruled out. He never smoked. Biopsy was not performed. '
        'He died on day 5. A hepatic adenoma was seen.')


def annotation():
    facts = [
        fact('p1-f1', 'case_context', {'case_kind': 'multi_patient', 'multiple_index_patients': True,
                                       'subject_state': 'living_at_presentation'}, 'A 67-year-old man presented'),
        fact('p1-f2', 'demographics', {'attribute': 'age_at_presentation', 'numeric_value': 67.0, 'unit': 'years'}, 'A 67-year-old man'),
        fact('p1-f3', 'observations', {'name': 'creatinine', 'numeric_value': 1.2, 'unit': 'mg/dL',
                                       'time': {'text': None, 'relation': 'at', 'anchor': 'index hospital admission', 'date_iso': None}},
             'Creatinine was 1.2 mg/dL'),
        fact('p1-f4', 'observations', {'name': 'CRP', 'numeric_value': 5.0, 'unit': 'mg/L'}, 'CRP <5 mg/L'),
        fact('p1-f5', 'observations', {'name': 'chest X-ray', 'text_value': 'normal', 'flag': 'unknown'}, 'Chest X-ray: normal'),
        fact('p1-f6', 'conditions', {'name': 'diabetes', 'assertion': 'absent', 'verification_status': 'confirmed'}, 'no history of diabetes'),
        fact('p1-f7', 'conditions', {'name': 'PE', 'assertion': 'absent', 'verification_status': 'confirmed'}, 'Pulmonary embolism was ruled out'),
        fact('p1-f8', 'social_exposures', {'name': 'smoking', 'status': 'never', 'assertion': 'absent'}, 'He never smoked'),
        fact('p1-f9', 'observations', {'name': 'biopsy', 'status': 'not_performed', 'assertion': 'absent'}, 'Biopsy was not performed'),
        fact('p1-f10', 'outcomes', {'event': 'death', 'assertion': 'present', 'subject': 'index_patient', 'text_value': 'died'}, 'He died on day 5'),
        fact('p1-f11', 'oncology', {'name': 'hepatic adenoma', 'tumor_ref': 't1'}, 'A hepatic adenoma was seen', 'tumors'),
    ]
    events = [{'event_id': 'e1', 'occurrence': 'uncertain', 'fact_ids': [], 'evidence': []}]
    return {'patients': [{'patient_id': 'p1', 'species': 'human', 'facts': facts, 'events': events}], 'figures': []}


def run_all(module, data, tmp_path):
    source = {'segments': [{'segment_id': 's1', 'text': TEXT}], 'figures': []}
    ctx = module.Ctx(tmp_path, 'X', source)
    for migration in module.ANNOTATION_MIGRATIONS:
        migration(data, ctx)
    return ctx


def test_mechanical_and_unambiguous_migrations_then_idempotence(monkeypatch, tmp_path):
    module = load(monkeypatch)
    data = annotation(); ctx = run_all(module, data, tmp_path)
    fields = {f['fact_id']: f['fields'] for f in data['patients'][0]['facts']}
    assert fields['p1-f1']['case_kind'] == 'clinical_case' and fields['p1-f1']['multiple_index_patients'] is False
    assert fields['p1-f1']['subject_state'] == 'deceased_during_course'
    assert fields['p1-f2']['text_value'] == '67-year-old'
    assert fields['p1-f3']['comparator'] == '=' and fields['p1-f3']['time']['anchor'] is None
    assert 'comparator' not in fields['p1-f4']   # printed '<' -> a person decides
    assert fields['p1-f5']['flag'] == 'normal'
    assert fields['p1-f6']['verification_status'] == 'unknown' and fields['p1-f7']['verification_status'] == 'refuted'
    assert fields['p1-f8']['assertion'] == 'present' and fields['p1-f9']['assertion'] == 'present'
    assert data['patients'][0]['events'][0]['occurrence'] == 'unknown'
    assert {i['rule_id'] for i in ctx.review} == {'OBS-COMPARATOR', 'ONC-BENIGN'}
    before = copy.deepcopy(data); again = run_all(module, data, tmp_path)
    assert data == before and not again.changes
    assert {i['key'] for i in again.review} == {i['key'] for i in ctx.review}


def test_needs_review_resolutions_survive_reruns(monkeypatch, tmp_path):
    module = load(monkeypatch)
    item = {'key': 'OBS-FLAG:p1-f5:fields.flag', 'rule_id': 'OBS-FLAG'}
    module.write_needs_review(tmp_path, 'X', [item], True)
    path = store.path(tmp_path, 'needs-review', 'X')
    import json
    doc = json.loads(path.read_text()); doc['items'][0].update(resolution='kept negative: qualitative test', resolved_by='parent')
    path.write_text(json.dumps(doc))
    module.write_needs_review(tmp_path, 'X', [item], True)
    assert json.loads(path.read_text())['items'][0]['resolution'] == 'kept negative: qualitative test'
    module.write_needs_review(tmp_path, 'X', [], True)
    assert not path.exists()


def test_dangling_relation_contact_wording_and_positional_labels(monkeypatch, tmp_path):
    module = load(monkeypatch)
    data = {'patients': [{'patient_id': 'p1', 'species': 'human', 'events': [], 'facts': [
        fact('p1-f1', 'observations', {'name': 'x', 'numeric_value': 1.2, 'comparator': '=',
             'time': {'text': None, 'relation': 'at', 'anchor': 'index admission', 'date_iso': None}}, 'Creatinine was 1.2 mg/dL'),
        fact('p1-f2', 'social_exposures', {'name': 'TB contact', 'status': 'never', 'assertion': 'absent'}, 'He had no history of diabetes')]}],
        'figures': [{'figure_id': 'F1', 'panels': [{'panel': 'upper right'}, {'panel': 'A'}]}]}
    source = {'segments': [{'segment_id': 's1', 'text': TEXT}], 'figures': [{'figure_id': 'F1', 'caption': '(A) CT, upper right inset.'}]}
    ctx = module.Ctx(tmp_path, 'X', source)
    for migration in module.ANNOTATION_MIGRATIONS:
        migration(data, ctx)
    facts = {f['fact_id']: f['fields'] for f in data['patients'][0]['facts']}
    assert facts['p1-f1']['time'] == {'text': None, 'relation': 'unknown', 'anchor': None, 'date_iso': None}
    assert facts['p1-f2']['assertion'] == 'absent'          # not flipped without never-wording
    keys = {i['key'] for i in ctx.review}
    assert 'SOC-NEVER:p1-f2:fields.status' in keys
    assert 'FIG-LABELS:F1:figures.panels[0].panel' in keys and 'FIG-LABELS:F1:figures.panels[1].panel' not in keys


def test_resolved_items_are_not_reopened(monkeypatch, tmp_path):
    module = load(monkeypatch)
    item = {'key': 'FIG-LABELS:F1:figures.panels[0].panel', 'rule_id': 'FIG-LABELS'}
    module.write_needs_review(tmp_path, 'X', [item], True)
    import json
    path = store.path(tmp_path, 'needs-review', 'X'); doc = json.loads(path.read_text())
    doc['items'][0].update(resolution='label printed on pixels', resolved_by='parent'); path.write_text(json.dumps(doc))
    again = module.write_needs_review(tmp_path, 'X', [item], True)
    assert [i for i in again['items'] if not i['resolution']] == []


def test_age_text_is_copied_verbatim_with_unicode_hyphens(monkeypatch, tmp_path):
    from openpatients2.semantic_rules import item_violations
    module = load(monkeypatch)
    text = 'A 67‐year‐old man and his 8‑year‑old Son were seen.'
    age = {'attribute': 'age_at_presentation', 'numeric_value': 67.0, 'unit': 'years'}
    data = {'patients': [{'patient_id': 'p1', 'species': 'human', 'events': [], 'facts': [
        fact('p1-f1', 'demographics', dict(age), 'A 67‐year‐old man'),
        fact('p1-f2', 'demographics', {**age, 'text_value': '67-year-old'}, 'A 67‐year‐old man'),
        fact('p1-f3', 'demographics', {**age, 'numeric_value': 8.0, 'text_value': '8-YEAR-OLD'}, '8‑year‑old Son'),
        fact('p1-f4', 'demographics', {**age, 'text_value': 'sixty-seven'}, 'A 67‐year‐old man')]}], 'figures': []}
    def rules(f):
        return {(v.rule_id, v.severity) for v in item_violations('demographics', 'items',
                {**f['fields'], 'evidence': f['evidence']}, text)}
    facts = {f['fact_id']: f for f in data['patients'][0]['facts']}
    assert rules(facts['p1-f2']) == {('DEMO-AGE-TEXT', 'error')}      # ASCII hyphen vs U+2010 in the quote
    assert rules(facts['p1-f4']) == {('DEMO-AGE-TEXT', 'warning')}    # not in the evidence at all: a person decides
    ctx = module.Ctx(tmp_path, 'X', {'segments': [{'segment_id': 's1', 'text': text}], 'figures': []})
    module.m03_age_text(data, ctx)
    assert facts['p1-f1']['fields']['text_value'] == '67‐year‐old'
    assert facts['p1-f2']['fields']['text_value'] == '67‐year‐old'
    assert facts['p1-f3']['fields']['text_value'] == '8‑year‑old'
    assert all(f['fields']['text_value'] in f['evidence'][0]['quote'] for f in facts.values() if f['fact_id'] != 'p1-f4')
    assert not rules(facts['p1-f1']) and not rules(facts['p1-f2']) and not rules(facts['p1-f3'])
    assert [i['target'] for i in ctx.review] == ['p1-f4']
    again = module.Ctx(tmp_path, 'X', ctx.source); module.m03_age_text(data, again)
    assert not again.changes
    # The runtime normalizer applies the same verbatim copy.
    from openpatients2.clinical_normalization import normalize_clinical
    item = {'attribute': 'age_at_presentation', 'numeric_value': 67.0, 'text_value': '67-year-old',
            'evidence': [{'source_section': 's1', 'quote': 'A 67‐year‐old man'}]}
    value, audit = normalize_clinical('demographics', {'items': [item]}, [{'segment_id': 's1', 'text': text}])
    assert value['items'][0]['text_value'] == '67‐year‐old'
    assert audit[0]['reason'] == 'age_text_copied_verbatim_from_own_evidence'
