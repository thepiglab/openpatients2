import copy
import gzip
import hashlib
import json
from pathlib import Path

import pytest

from openpatients2.competition_selection import (
    CRITERIA, build, choose, exclusion_reasons, features, freeze, validate_selection,
)


def article(aid='PMC1.1', split='train', category='cardiac'):
    source = {'text': 'A patient has serial laboratory results.', 'text_sha256': 'source-hash',
              'title': 'An individual clinical case', 'segments': [{'kind': 'table_row', 'text': '1 mmol/L'}],
              'figures': [], 'license': {'code': 'CC BY', 'allowed': True, 'restrictive_prose': False}}
    row = {'article_id': aid, 'split': split, 'title': source['title'], 'primary_stratum': category,
           'text_sha256': 'source-hash', 'completion': {'completed': True},
           'species': {'article_has_non_human_patient': False},
           'patients': [{'species': 'human', 'label': 'A 42-year-old patient', 'sections': {
               'observations': {'items': [{'name': 'sodium', 'numeric_value': 120+i} for i in range(8)]},
               'conditions': {'items': [{'name': 'hyponatremia'}]},
               'medications': {'items': []}}}],
           'measurement_series': {'series': [{'points': [{}, {}, {}]}]}}
    return row, source


def put_corpus(root):
    rows = []
    for i, category in enumerate(['cardiac', 'endocrine', 'renal'], 1):
        row, source = article(f'PMC{i}.1', category=category); rows.append(row)
        path = root / 'benchmarks/dense/articles' / row['article_id'] / 'source.json'
        path.parent.mkdir(parents=True); path.write_text(json.dumps(source))
    path = root / 'data/dense_clinical_cases_v1/data/train.jsonl'
    path.parent.mkdir(parents=True); path.write_text(''.join(json.dumps(row)+'\n' for row in rows))
    return path


def test_selection_rejects_heldout_extreme_ambiguous_and_restricted_sources():
    row, source = article()
    f = features(row, source)
    assert not exclusion_reasons(row, source, f)
    row['split'] = 'test'
    source['license']['code'] = 'CC BY-NC'
    row['patients'][0]['label'] = 'Unidentified cohort patient'
    f.update(source_characters=40001, table_rows=61, patients=10, numeric_observations=0)
    assert set(exclusion_reasons(row, source, f)) >= {
        'held_out_split', 'outside_cc_by_release_scope', 'ambiguous_patient_identity_label',
        'source_over_40000_characters', 'table_rows_over_60', 'patient_count_outside_1_to_3',
        'fewer_than_8_numeric_observations',
    }


def test_selection_is_order_independent_and_ignores_prediction_scores():
    candidates = []
    for i in range(12):
        row, source = article(f'PMC{i+1}.1', category=['cardiac', 'renal', 'endocrine', 'neurology'][i % 4])
        candidates.append({'article_id': row['article_id'], 'features': features(row, source), 'model_score': i})
    expected = [a['article_id'] for a in choose(candidates, 10)]
    altered = copy.deepcopy(list(reversed(candidates)))
    for a in altered: a['model_score'] = 1e10
    assert [a['article_id'] for a in choose(altered, 10)] == expected
    assert len(expected) == len(set(expected)) == 10
    with pytest.raises(ValueError, match='Insufficient'):
        choose(candidates, 13)


def test_freeze_is_reproducible_and_pins_sources_without_freezing_corpus_growth(tmp_path):
    corpus = put_corpus(tmp_path)
    folder = tmp_path / 'configs'; manifest = folder/'competition.json'; reference = folder/'reference.jsonl.gz'
    first = freeze(tmp_path, manifest, reference, count=3)
    first_bytes = reference.read_bytes()
    assert freeze(tmp_path, manifest, reference, count=3) == first
    assert reference.read_bytes() == first_bytes
    assert validate_selection(tmp_path, manifest)['article_count'] == 3
    # The upstream corpus can grow after the selected subset has been frozen.
    corpus.write_text(corpus.read_text()+'\n')
    assert validate_selection(tmp_path, manifest)['article_ids'] == first['article_ids']
    source = tmp_path / first['articles'][0]['source_path']
    source.write_text(source.read_text()+' ')
    with pytest.raises(ValueError, match='Source file changed'):
        validate_selection(tmp_path, manifest)


def test_reference_hash_and_heldout_checks(tmp_path):
    put_corpus(tmp_path)
    manifest = tmp_path/'configs/competition.json'; reference = tmp_path/'configs/ref.jsonl.gz'
    frozen = freeze(tmp_path, manifest, reference, count=3)
    rows = [json.loads(line) for line in gzip.decompress(reference.read_bytes()).decode().splitlines()]
    rows[0]['split'] = 'validation'
    reference.write_bytes(gzip.compress(('\n'.join(json.dumps(r) for r in rows)+'\n').encode(), mtime=0))
    with pytest.raises(ValueError, match='reference changed'):
        validate_selection(tmp_path, manifest)
    frozen['reference']['sha256'] = hashlib.sha256(reference.read_bytes()).hexdigest()
    manifest.write_text(json.dumps(frozen))
    with pytest.raises(ValueError, match='Held-out article'):
        validate_selection(tmp_path, manifest)


def test_frozen_competition_catalog_has_50_training_articles_and_diverse_measurements():
    root = Path(__file__).resolve().parents[1]
    m = json.loads((root/'configs/hipergator/nemotron-super/competition-50.json').read_text())
    assert len(m['article_ids']) == len(set(m['article_ids'])) == m['article_count'] == 50
    assert m['articles_with_series'] >= 20
    assert len(m['counts']['specialty_family']) >= 20
    assert m['totals']['numeric_observations'] >= 2000
    for a in m['articles']:
        assert a['split'] == 'train' and a['license']['code'] == 'CC BY'
        assert a['features']['source_characters'] <= CRITERIA['max_source_characters']
        assert 1 <= a['features']['patients'] <= 3
    with gzip.open(root/m['reference']['path'], 'rt') as f:
        rows = [json.loads(line) for line in f]
    assert {r['article_id'] for r in rows} == set(m['article_ids'])
    assert all(r['split'] == 'train' for r in rows)
