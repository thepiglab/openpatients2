import copy
import hashlib

import jsonschema
import pytest

from openpatients2.article_profile import prepare, result_schema, validate_record, evidence_candidates


def fixture():
    article = {'article_id': 'PMC1.1', 'title': 'Original cases',
        'segments': [{'segment_id': 'a', 'heading': 'Abstract: Results', 'text': 'We describe four children.'},
                     {'segment_id': 'b', 'heading': 'Results', 'text': 'Café follow-up.'}],
        'patients': [{'patient_id': 'GOLD_SECRET'}], 'primary_stratum': 'GOLD_SPECIALTY'}
    request = prepare(article, scope='full_text', field_ids=['individual_case_count'])
    probabilities = {k: float(k == 'two_to_four') for k in request['request']['questions']['individual_case_count']['criteria']}
    record = {'schema_version': 'article-profile/1', 'article_id': article['article_id'],
        'source_sha256': request['source_sha256'], 'catalog_sha256': request['catalog_sha256'], 'input_scope': 'full_text',
        'model': {'model_id': 'fixture', 'revision': 'fixture-revision', 'serving_revision': 'fixture-serving',
                  'template_sha256': '0'*64, 'calibration_id': None},
        'decisions': {'individual_case_count': {'label': 'two_to_four', 'probabilities': probabilities,
                      'evidence': [evidence_candidates(request['request']['state'])['a']]}},
        'elapsed_seconds': 0.1, 'input_tokens': 100, 'semantic_review': 'unreviewed'}
    return article, request, record


def test_source_only_questions_and_strict_schema():
    article, request, record = fixture()
    assert 'GOLD_' not in str(request)
    schema = result_schema(['individual_case_count'])
    jsonschema.Draft202012Validator.check_schema(schema)
    jsonschema.validate(record, schema)
    assert validate_record(record, request) == record
    record['decisions']['individual_case_count']['label'] = 'invented_band'
    with pytest.raises(jsonschema.ValidationError): jsonschema.validate(record, schema)
    with pytest.raises(ValueError, match='labels'): validate_record(record, request)


def test_abstract_scope_excludes_fulltext_and_preserves_unknown():
    article, _, _ = fixture()
    request = prepare(article, field_ids=['original_patient_cases'])
    assert [s['segment_id'] for s in request['request']['state']['segments']] == ['a']
    assert 'insufficient_evidence' in request['request']['questions']['original_patient_cases']['criteria']
    assert 'four' in str(request)
    assert 'Café' not in str(request)


@pytest.mark.parametrize('change', ['missing', 'nan', 'sum', 'source', 'quote', 'offset', 'no_evidence'])
def test_rejects_incomplete_or_unsupported_records(change):
    _, request, record = fixture()
    answer = record['decisions']['individual_case_count']
    if change == 'missing': record['decisions'].clear()
    if change == 'nan': answer['probabilities']['two_to_four'] = float('nan')
    if change == 'sum': answer['probabilities']['two_to_four'] = 0.8
    if change == 'source': record['source_sha256'] = 'a'*64
    if change == 'quote': answer['evidence'][0]['quote'] = 'We describe five children.'
    if change == 'offset': answer['evidence'][0]['end'] = 999
    if change == 'no_evidence': answer['evidence'].clear()
    with pytest.raises(ValueError): validate_record(record, request)


def test_abstentions_are_explicit_and_never_fabricate_citations():
    _, request, record = fixture()
    answer = record['decisions']['individual_case_count']
    answer.update(label='insufficient_evidence', evidence=[])
    answer['probabilities'] = {k: float(k == 'insufficient_evidence') for k in answer['probabilities']}
    assert validate_record(record, request)['model']['calibration_id'] is None


def test_unicode_offsets_and_multilabel_options_are_separate_decisions():
    article, _, _ = fixture()
    request = prepare(article, scope='full_text', field_ids=['specialties'])
    assert 'specialties.oncology' in request['request']['questions']
    assert 'specialties.hematology' in request['request']['questions']
    assert all(len(q['criteria']) == 4 for q in request['request']['questions'].values())
    candidate = evidence_candidates(request['request']['state'])['b']
    assert candidate['end'] == len('Café follow-up.')
    assert candidate['segment_sha256'] == hashlib.sha256('Café follow-up.'.encode()).hexdigest()


def test_bad_fields_and_ambiguous_segments_fail_closed():
    article, _, _ = fixture()
    for fields in ([], ['unknown'], ['study_design','study_design']):
        with pytest.raises(ValueError): prepare(article, field_ids=fields)
    article['segments'].append(copy.deepcopy(article['segments'][0]))
    with pytest.raises(ValueError, match='unique'): prepare(article)
