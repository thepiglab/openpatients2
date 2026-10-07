from copy import deepcopy
import gzip
import json
from pathlib import Path

import pytest

from openpatients2.ledger_evaluation import (
    counterfactual_fixtures, evaluate, paired_evaluate, preflight,
    score_counterfactual, training_feedback, validate_article_splits,
)

ROOT = Path(__file__).resolve().parents[1]


def reference(checks, article_ids=('PMC1.1',)):
    return {'checks': checks, 'articles': [
        {'article_id': aid, 'expected_count': 1, 'text_sha256': 'text', 'species': ['human'],
         'identities': [{'patient_id': 'p1', 'evidence': [{'segment_id': 'b00001',
             'quote': 'A 42-year-old woman was admitted for a documented clinical condition.'}]}]}
        for aid in article_ids], 'optimization_splits': {
            'train': [article_ids[0]], 'validation': list(article_ids[1:2]), 'test': list(article_ids[2:])}}


def check(pattern, id='c1', rid='PMC1.1:p1', task='conditions', kind='required'):
    return {'id': id, 'record_id': rid, 'task': task, 'collection': 'items', 'kind': kind,
            'category': task, 'pattern': pattern, 'text_sha256': 'text', 'xml_sha256': 'xml',
            'source': [{'segment_id': 'b00001', 'quote': 'Reviewed source proposition.'}]}


def patient(items, rid='PMC1.1:p1', task='conditions'):
    return {'source': {'record_id': rid, 'article_source': {'article_id': rid.split(':')[0],
            'text_sha256': 'text', 'xml_sha256': 'xml'}, 'patient_target': {'species': 'human',
            'identity_evidence': [{'segment_id': 'b00001',
                'quote': 'A 42-year-old woman was admitted for a documented clinical condition.'}]}},
            'sections': {task: {'items': items}}, 'quality': {task: {'status': 'valid'}}}


def test_typed_quantities_and_semantics_ignore_quotes_and_schema_acceptance():
    patterns = [
        {'name': 'recurrence', 'assertion': 'absent'},
        {'name': 'allergy', 'assertion': 'possible'},
        {'name': 'surgery', 'action': 'planned'},
        {'name': 'activity', 'numeric_value': 100., 'unit': 'mCi'},
    ]
    gold = reference([check(p, f'c{i}') for i, p in enumerate(patterns)])
    values = [
        {'name': 'recurrence', 'assertion': 'present'},
        {'name': 'allergy', 'assertion': 'present'},
        {'name': 'surgery', 'action': 'completed'},
        {'name': 'activity', 'numeric_value': 100., 'unit': 'MCi'},
    ]
    for value, correct in zip(values, patterns):
        value['evidence'] = [{'quote': json.dumps(correct)}]
    score = evaluate(gold, [patient(values)])
    assert score['summary']['matched_probes'] == 0
    assert score['summary']['matched_attributes'] == 5
    assert {d['kind'] for d in score['diagnostics']} == {'certainty_or_negation', 'planned_vs_completed', 'measurement'}
    assert score['schema']['reported_task_statuses'] == {'valid': 1}
    assert score['summary']['clinical_accuracy_established'] is False
    assert score['summary']['clinical_precision'] is None
    good = evaluate(gold, [patient(patterns)])
    assert good['summary']['matched_probes'] == 4
    assert good['dense_feedback_score'] > score['dense_feedback_score']


def test_partial_fields_cannot_be_joined_across_items_or_evidence():
    gold = reference([check({'name': 'RAI', 'dose_value': 100., 'dose_unit': 'mCi'}, task='medications')])
    pred = patient([{'name': 'RAI', 'dose_value': 100., 'dose_unit': None},
                    {'name': 'RAI', 'dose_value': 150., 'dose_unit': 'mCi'}], task='medications')
    score = evaluate(gold, [pred])
    assert score['summary']['matched_probes'] == 0
    assert score['summary']['matched_attributes'] == 2
    assert score['summary']['missing_required_propositions'] == 1


def test_checked_patient_misplacement_is_separate_from_omission():
    pattern = {'name': 'sodium', 'numeric_value': 115., 'unit': 'mmol/L'}
    gold = reference([check(pattern, task='observations', rid='PMC1.1:p2')])
    score = evaluate(gold, [patient([pattern], task='observations')])
    assert score['summary']['wrong_owner_errors'] == 1
    assert score['diagnostics'][0]['found_under_records'] == ['PMC1.1:p1']
    assert score['summary']['matched_probes'] == 0
    # An unrelated article never explains a wrong-patient score.
    score = evaluate(gold, [patient([pattern], rid='PMC2.1:p1', task='observations')])
    assert score['summary']['wrong_owner_errors'] == 0
    assert score['diagnostics'][0]['kind'] == 'missing_required_proposition'


def test_missing_delivery_and_source_binding_failures_keep_fixed_denominator():
    gold = reference([check({'name': 'meningioma'}),
                      check({'name': 'meningioma', 'laterality': 'bilateral'}, id='bad', kind='forbidden')])
    score = evaluate(gold, [])
    assert score['summary']['required_probes'] == 1
    assert score['summary']['unavailable_probes'] == 1
    assert score['summary']['forbidden_unavailable'] == 1
    assert score['summary']['unsupported_modifiers'] == 0
    p = patient([{'name': 'meningioma', 'laterality': 'bilateral'}])
    p['source']['article_source']['text_sha256'] = 'different'
    assert evaluate(gold, [p])['summary']['matched_probes'] == 0


def test_unreviewed_modifiers_do_not_become_unsupported_claims():
    gold = reference([check({'name': 'meningioma'})])
    bad = patient([{'name': 'meningioma', 'laterality': 'bilateral'}])
    score = evaluate(gold, [bad])
    assert score['summary']['unsupported_modifiers'] == 0
    gold['checks'].append(check({'name': 'meningioma', 'laterality': 'bilateral'}, id='bad', kind='forbidden'))
    score = evaluate(gold, [bad])
    assert score['summary']['unsupported_modifiers'] == 1
    assert score['diagnostics'][0]['basis'] == 'explicit_reviewed_forbidden_probe'


def test_repeated_measurements_bind_each_dose_to_its_own_time():
    first = {'name': 'RAI', 'dose_value': 100., 'dose_unit': 'mCi', 'time': {'text': '2023'}}
    second = {'name': 'RAI', 'dose_value': 150., 'dose_unit': 'mCi', 'time': {'text': '2024'}}
    gold = reference([check(first, task='medications'), check(second, id='c2', task='medications')])
    wrong = [{**first, 'time': {'text': '2024'}}, {**second, 'time': {'text': '2023'}}]
    score = evaluate(gold, [patient(wrong, task='medications')])
    assert score['summary']['matched_probes'] == 0
    assert score['summary']['temporal_link_errors'] == 2
    assert evaluate(gold, [patient([first, second], task='medications')])['summary']['matched_probes'] == 2


def test_reviewed_alternatives_stay_one_complete_item():
    pattern = {'one_of': [{'name': 'STAT6', 'interpretation': 'positive'},
                          {'name': 'STAT6', 'text_value': {'regex': 'positive'}}]}
    gold = reference([check(pattern)])
    assert evaluate(gold, [patient([{'name': 'STAT6', 'interpretation': 'positive'}])])['summary']['matched_probes'] == 1
    assert evaluate(gold, [patient([{'name': 'STAT6'}, {'text_value': 'positive'}])])['summary']['matched_probes'] == 0


def test_timeline_links_use_distinct_nodes_and_direction():
    gold = reference([])
    gold['bundle_gold'] = {'timelines': [{'record_id': 'PMC1.1:p1', 'text_sha256': 'text', 'nodes': [
        {'id': 'a', 'pattern': {'description': 'surgery'}, 'source': [{'segment_id': 's1'}]},
        {'id': 'b', 'pattern': {'description': 'followup'}, 'source': [{'segment_id': 's2'}]}],
        'edges': [{'from': 'a', 'to': 'b', 'relation': 'before'}]}]}
    p = patient([])
    p['companions'] = {'timeline_v2': {'events': [
        {'event_id': 'e1', 'description': 'surgery', 'evidence': [{'segment_id': 's1'}]},
        {'event_id': 'e2', 'description': 'followup', 'evidence': [{'segment_id': 's2'}]}],
        'edges': [{'from_event_id': 'e2', 'to_event_id': 'e1', 'relation': 'before'}]}}
    score = evaluate(gold, [p])
    assert score['summary']['reversed_temporal_links'] == 1
    assert score['summary']['matched_temporal_links'] == 0
    p['companions']['timeline_v2']['edges'][0].update(from_event_id='e1', to_event_id='e2')
    assert evaluate(gold, [p])['summary']['matched_temporal_links'] == 1
    p['source']['article_source']['text_sha256'] = 'wrong source'
    assert evaluate(gold, [p])['summary']['matched_temporal_links'] == 0


@pytest.mark.parametrize('splits', [
    {'train': ['PMC1.1:p1'], 'validation': [], 'test': []},
    {'train': ['PMC1.1'], 'validation': ['PMC1.2'], 'test': []},
    {'train': ['PMC1.1', 'PMC1.1'], 'validation': [], 'test': []},
])
def test_article_splits_reject_patient_and_version_leakage(splits):
    with pytest.raises(ValueError):
        validate_article_splits(splits)


def test_paired_deltas_separate_article_disjoint_splits():
    aids = ('PMC1.1', 'PMC2.1', 'PMC3.1')
    pattern = {'name': 'allergy', 'assertion': 'possible'}
    gold = reference([check(pattern, f'c{i}', rid=aid+':p1') for i, aid in enumerate(aids)], aids)
    baseline = [patient([pattern], rid=aid+':p1') for aid in aids]
    candidate = deepcopy(baseline)
    candidate[1]['sections']['conditions']['items'] = []
    scored = paired_evaluate(gold, baseline, candidate, bootstrap=20)
    assert scored['splits']['train']['paired_lost'] == []
    assert scored['splits']['validation']['paired_lost'] == ['c1']
    assert scored['splits']['test']['paired_lost'] == []
    assert scored['splits']['validation']['article_bootstrap_95_ci_retention_delta'] is None
    assert scored['splits']['validation']['standard_bundle_baseline']['clinical']['matched'] == 1
    assert scored['splits']['validation']['standard_bundle_candidate']['clinical']['matched'] == 0
    feedback = training_feedback(gold, candidate)
    assert [r['id'] for r in feedback['checks']] == ['c0']
    assert 'PMC2.1' not in json.dumps(feedback)
    assert 'PMC3.1' not in json.dumps(feedback)


def test_cpu_preflight_validates_existing_reviewed_gold_and_canonical_sources():
    gold = json.loads((ROOT/'benchmarks/patient-bundle/reference-reviewed.json').read_text())
    articles = []
    for name in ('articles.jsonl.gz', 'additional-articles.jsonl.gz'):
        with gzip.open(ROOT/'benchmarks/patient-bundle'/name, 'rt') as stream:
            articles.extend(json.loads(line) for line in stream if line.strip())
    report = preflight(gold, articles)
    assert report['passed'] and report['model_calls'] == 0
    assert report['required_probes'] == 259
    assert report['forbidden_probes'] == 44
    assert report['split_articles'] == {'train': 15, 'validation': 7, 'test': 9}


@pytest.mark.parametrize('fixture', counterfactual_fixtures(), ids=lambda row: row['name'])
def test_synthetic_regression_challenges_are_not_clinical_gold(fixture):
    good = score_counterfactual(fixture, fixture['good'])
    bad = score_counterfactual(fixture, fixture['bad'])
    assert good['matched'] == good['required']
    assert bad['matched'] < bad['required']
    assert not good['clinical_gold'] and not fixture['clinical_gold']
    # Object/claim ordering and unrelated background do not change proposition retention.
    reordered = list(reversed(deepcopy(fixture['good'])))
    reordered.append({'record_id': 'challenge:background', 'task': 'conditions', 'item': {'name': 'unrelated'}})
    assert score_counterfactual(fixture, reordered)['matched'] == good['matched']
    # A literal mention of the expected value inside evidence cannot repair a typed error.
    bad_quoted = deepcopy(fixture['bad'])
    for row in bad_quoted:
        row['item']['evidence'] = [{'quote': fixture['source'] + json.dumps(fixture['expected'])}]
    assert score_counterfactual(fixture, bad_quoted)['matched'] == bad['matched']


def test_synthetic_controls_enforce_one_to_one_matching():
    fixture = counterfactual_fixtures()[0]
    fixture['expected'] *= 2
    assert score_counterfactual(fixture, fixture['good'])['matched'] == 1


def test_attribute_repair_challenge_only_unlocks_named_scalar_and_preserves_evidence():
    from openpatients2.evidence_ledger import protect_unchallenged
    atom = {'name': 'meningioma', 'laterality': 'bilateral', 'assertion': 'present',
            'time': {'text': '2009'}, 'evidence': [{'segment_id': 's1', 'quote': 'Bilateral craniotomy for meningioma.'}]}
    old = {'items': [atom]}
    rows = [{'review_id': 'f1', 'pointer': '/items/0', 'candidate': atom}]
    challenges = [{'fact_id': 'f1', 'pointer': '/laterality'}]
    good = {'items': [{**deepcopy(atom), 'laterality': 'unknown'}]}
    assert protect_unchallenged(old, good, rows, challenges) == good
    mutations = [
        lambda item: item.update(name='glioma'),
        lambda item: item.update(assertion='absent'),
        lambda item: item['time'].update(text='2014'),
        lambda item: item['evidence'][0].update(quote='meningioma'),
        lambda item: item.update(evidence=[]),
    ]
    for mutate in mutations:
        bad = deepcopy(good)
        mutate(bad['items'][0])
        with pytest.raises(ValueError):
            protect_unchallenged(old, bad, rows, challenges)
    with pytest.raises(ValueError):
        protect_unchallenged(old, {'items': []}, rows, challenges)


def test_attribute_repair_preserves_unreviewed_atom_multiplicity_and_nulls():
    from openpatients2.evidence_ledger import protect_unchallenged
    atom = {'name': 'RAI', 'dose_value': 100., 'dose_unit': 'mCi', 'route': None,
            'evidence': [{'segment_id': 's1', 'quote': 'RAI 100 mCi.'}]}
    old = {'items': [deepcopy(atom), deepcopy(atom)]}
    rows = [{'review_id': f'f{i}', 'pointer': f'/items/{i}', 'candidate': deepcopy(atom)} for i in range(2)]
    assert protect_unchallenged(old, deepcopy(old), rows, []) == old
    with pytest.raises(ValueError):
        protect_unchallenged(old, {'items': [deepcopy(atom)]}, rows, [])
    bad = deepcopy(old)
    del bad['items'][0]['route']
    with pytest.raises(ValueError):
        protect_unchallenged(old, bad, rows, [])


def test_routing_objective_exposes_semantic_map_omissions_without_changing_gold():
    from openpatients2.ledger_evaluation import routing_objective
    from openpatients2.evidence_ledger import route_segments
    article = {'article_id': 'PMC1.1', 'text_sha256': 'text', 'xml_sha256': 'xml',
               'segments': [{'segment_id': f's{i}', 'text': f'Original segment {i}'} for i in range(7)]}
    target = {'patient_id': 'p1', 'identity_evidence': [{'segment_id': 's0'}]}
    c = check({'name': 'missing downstream observation'}, task='observations')
    c['source'] = [{'segment_id': 's6', 'quote': 'Original segment 6'}]
    gold = reference([c])
    snapshot = deepcopy(gold)
    chunk = {'chunk_id': 'c1', 'fragments': article['segments']}
    result = {'status': 'valid', 'data': {'propositions': [{'tasks': ['observations'],
        'patient_ids': ['p1'], 'scope': 'individual',
        'evidence': [{'segment_id': 's0'}], 'attribution_evidence': [{'segment_id': 's0'}]}]}}
    measured = routing_objective(gold, article, [target], [(chunk, result)])
    assert measured['retained_witness_probes'] == 0
    assert measured['probes'][0]['omitted_witness_segments'] == ['s6']
    assert gold == snapshot
    assert not measured['clinical_entailment_established']
    # An invalid map falls back to original passages and preserves gold witnesses.
    measured = routing_objective(gold, article, [target], [(chunk, {'status': 'failed'})])
    assert measured['retained_witness_probes'] == 1
    assert measured['routes'][0]['source_segment_fraction'] == 1
    # No route hits is safe full-source fallback, reported as uncompressed.
    assert route_segments(article, target, 'conditions', [(chunk, result)])[1]['mode'] == 'full_article_no_route_hits'
    missing = routing_objective(gold, article, [], [(chunk, result)])
    assert missing['required_witness_probes'] == 1
    assert missing['retained_witness_probes'] == 0
    assert missing['routes'][0]['mode'] == 'missing_aligned_patient'
    assert missing['routes'][0]['source_segment_fraction'] == 0


def test_objective_adequacy_does_not_confuse_synthetic_controls_with_direct_gold():
    from openpatients2.ledger_evaluation import objective_adequacy
    gold = json.loads((ROOT/'benchmarks/patient-bundle/reference-reviewed.json').read_text())
    report = objective_adequacy(gold)
    assert not report['search_enabled_in_this_campaign']
    assert report['synthetic_fixture_count_in_gold'] == 0
    assert set(report['families']) == {'source_map', 'attribute', 'episode'}
    assert report['families']['attribute']['surrogate_label_minima_met']
    assert not report['families']['attribute']['direct_prompt_objective_established']
    assert report['families']['attribute']['direct_prompt_verdict_labels'] == 0
    episode = report['families']['episode']
    assert episode['groups']['train']['direct_reviewed_temporal_relations'] == 24
    assert episode['groups']['validation']['direct_reviewed_temporal_relations'] == 14
    assert not episode['surrogate_label_minima_met']
    assert not episode['screening_gates']['train_has_three_reviewed_hard_negatives']
