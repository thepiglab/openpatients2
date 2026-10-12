from copy import deepcopy
import gzip
import json
from pathlib import Path

import pytest


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


