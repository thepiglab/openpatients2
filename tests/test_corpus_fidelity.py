import copy
import gzip
import json
from pathlib import Path

import pytest

from openpatients2.corpus_fidelity import evaluate, validate_reference_sources


def fixture():
    checks = [{'id': 'c1', 'record_id': 'PMC1.1:p1', 'kind': 'required',
        'category': 'lab', 'task': 'observations', 'collection': 'items',
        'pattern': {'name': 'sodium', 'numeric_value': 115},
        'text_sha256': 'text', 'xml_sha256': 'xml',
        'source': [{'segment_id': 'b1', 'quote': 'sodium 115'}]}]
    reference = {'checks': checks, 'articles': [{'article_id': 'PMC1.1',
        'text_sha256': 'text', 'expected_count': 1, 'species': ['human'],
        'count_adjudication': 'source_checked', 'source': checks[0]['source']}]}
    patient = {'source': {'record_id': 'PMC1.1:p1', 'article_source': {
        'text_sha256': 'text', 'xml_sha256': 'xml'}},
        'sections': {'observations': {'items': [{'name': 'sodium', 'numeric_value': 115}]}},
        'companions': {}, 'quality': {'observations': 'valid'}}
    return reference, patient


def test_complete_item_required_not_cross_item_cooccurrence():
    reference, patient = fixture()
    patient['sections']['observations']['items'] = [{'name': 'sodium', 'numeric_value': 129},
        {'name': 'potassium', 'numeric_value': 115}]
    report = evaluate(reference, [patient])
    assert report['summary']['delivered']['matched'] == 0
    assert report['summary']['delivered']['missing'] == 1
    assert report['unreviewed_claims']['delivered']['items_outside_checklist'] == 2
    assert report['unreviewed_claims']['delivered']['unsupported_claims'] is None


def test_source_quote_cannot_satisfy_generated_claim():
    reference, patient = fixture()
    patient['sections']['observations']['items'] = [{'name': 'sodium', 'numeric_value': 129,
        'evidence': [{'name': 'sodium', 'numeric_value': 115}]}]
    assert not evaluate(reference, [patient])['checks'][0]['delivered']['matched']


def test_first_and_last_attempt_separate_from_delivered():
    reference, patient = fixture()
    task = {'task': 'observations', 'identity': {'article_id': 'PMC1.1', 'patient_id': 'p1'},
        'status': 'failed', 'data': None, 'attempts': [
            {'raw_candidate': {'items': [{'name': 'sodium', 'numeric_value': 115}]}},
            {'raw_candidate': {'items': [{'name': 'sodium', 'numeric_value': 129}]}}]}
    patient['sections']['observations'] = None
    report = evaluate(reference, [patient], task_rows=[task])
    assert report['summary']['first_pass']['matched'] == 1
    assert report['summary']['raw']['matched'] == 1
    assert report['summary']['delivered']['unscorable'] == 1
    assert report['schema']['reported_task_statuses'] == {'failed': 1}


def test_wrong_source_hash_unscorable():
    reference, patient = fixture()
    patient['source']['article_source']['text_sha256'] = 'changed'
    row = evaluate(reference, [patient])['checks'][0]
    assert row['delivered']['unscorable_reason'] == 'source_binding_failed'
    assert not row['delivered']['matched']


def test_missing_first_attempt_logs_cannot_borrow_successful_delivery():
    reference, patient = fixture()
    report = evaluate(reference, [patient])
    assert report['summary']['delivered']['matched'] == 1
    assert report['summary']['first_pass']['unscorable'] == 1
    assert report['summary']['first_pass']['matched'] == 0


def test_unavailable_forbidden_not_counted_safe():
    reference, _ = fixture(); reference['checks'][0]['kind'] = 'forbidden'
    s = evaluate(reference, [])['summary']['delivered']
    assert s['forbidden_unscorable'] == 1
    assert s['forbidden_violations'] == 0


def test_discovery_negative_and_provisional_counts():
    reference, _ = fixture()
    reference['articles'].append({**reference['articles'][0], 'article_id': 'PMC2.1',
        'expected_count': 0, 'species': []})
    reference['articles'].append({**reference['articles'][0], 'article_id': 'PMC3.1',
        'count_adjudication': 'provisional_illustrative_case'})
    pred = {'articles': [{'article_id': 'PMC1.1', 'roster': {'patients': [{'species': 'human'}]}},
        {'article_id': 'PMC2.1', 'roster': {'patients': [{'species': 'nonhuman'}]}}]}
    s = evaluate(reference, [], discovery=pred)['discovery']['summary']
    assert s == {'definitive_articles': 2, 'count_matches': 1, 'unscorable': 0,
        'extra_patients': 1, 'missing_patients': 0, 'provisional_articles_excluded': 1}


def test_aggregate_figure_wrong_patient_is_violation():
    reference, _ = fixture()
    reference['figure_checks'] = [{'id': 'f1', 'article_id': 'PMC1.1',
        'figure_id': 'Fig1', 'panel': None, 'patient_ids': [], 'scope': 'aggregate'}]
    rows = [{'task': 'figure_attribution', 'identity': {'article_id': 'PMC1.1', 'figure_id': 'Fig1'},
        'data': {'assignments': [{'panel': None, 'patient_ids': ['p1'], 'scope': 'individual'}]}}]
    s = evaluate(reference, [], task_rows=rows)['figures']['caption']['summary']
    assert s['wrong_patient_assignments'] == 1 and s['matched'] == 0


def test_reference_literal_and_hash_binding():
    reference, _ = fixture()
    articles = {'PMC1.1': {'text_sha256': 'text', 'xml_sha256': 'xml',
        'segments': [{'segment_id': 'b1', 'text': 'sodium 115 mmol/L'}]}}
    import hashlib
    articles['PMC1.1']['text'] = '[b1] Article\nsodium 115 mmol/L'
    digest = hashlib.sha256(articles['PMC1.1']['text'].encode()).hexdigest()
    articles['PMC1.1']['text_sha256'] = digest
    reference['checks'][0]['text_sha256'] = digest
    reference['articles'][0]['text_sha256'] = digest
    assert validate_reference_sources(reference, articles)
    reference['checks'][0]['source'][0]['quote'] = 'sodium 129'
    with pytest.raises(ValueError, match='Nonliteral'): validate_reference_sources(reference, articles)


def test_real_fixture_source_gate_rosters_and_gold():
    from openpatients2.pilot_extract import PilotConfig, source_gate
    from openpatients2.patient_context import frozen_rosters
    root = Path(__file__).resolve().parents[1] / 'benchmarks' / 'corpus-correctness'
    with gzip.open(root / 'articles.jsonl.gz', 'rt') as f: articles = [json.loads(line) for line in f]
    reference = json.loads((root / 'reference.json').read_text())
    assert len(articles) == 20
    assert all(not source_gate(a, PilotConfig()) for a in articles)
    rosters = frozen_rosters(root / 'rosters.json', articles)
    assert sum(len(r['patients']) for r in rosters.values()) == 17
    assert validate_reference_sources(reference, articles)
    assert len(reference['checks']) == 51


def test_caption_and_pixel_ownership_are_independent():
    reference, _ = fixture()
    reference['figure_checks'] = [{'id': 'f1', 'article_id': 'PMC1.1',
        'figure_id': 'Fig1', 'panel': None, 'patient_ids': [], 'scope': 'aggregate'}]
    identity = {'article_id': 'PMC1.1', 'figure_id': 'Fig1'}
    caption = {'task': 'figure_attribution', 'identity': identity,
        'data': {'assignments': [{'panel': None, 'patient_ids': [], 'scope': 'aggregate'}]}}
    visual = {'identity': identity, 'attribution': {'data': {'assignments': [
        {'panel': None, 'patient_ids': ['p1'], 'scope': 'individual'}]}}}
    score = evaluate(reference, [], task_rows=[caption], visual_rows=[visual])['figures']
    assert score['caption']['summary']['matched'] == 1
    assert score['pixels']['summary']['wrong_patient_assignments'] == 1
