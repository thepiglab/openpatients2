"""Per-article store layout: one path map and the sha256-verified migration from the folder-per-kind layout."""
import hashlib
import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
from openpatients2.dense_store import load as load_store
store = load_store()


def test_kinds_map_to_fixed_files_inside_the_article_folder(tmp_path):
    a = 'PMC1.1'
    assert store.path(tmp_path, 'annotations', a) == tmp_path / 'articles/PMC1.1/annotation.json'
    assert store.path(tmp_path, 'drafts', a).name == 'annotation_draft.json'
    assert store.path(tmp_path, 'reviews', a) == tmp_path / 'articles/PMC1.1/receipts/annotation.parent.json'
    # Legacy receipt folder names and kind+role resolve to the same receipt.
    assert store.path(tmp_path, 'schema-agent-reviews', a) == store.receipt(tmp_path, 'schema', 'agent', a)
    assert store.receipt(tmp_path, 'supplement', 'parent', a, 'SM1').name == 'supplement.SM1.parent.json'
    assert store.path(tmp_path, 'document-submissions', a, sid='SM1').as_posix().endswith('supplements/SM1.document.json')
    for bad in (dict(kind='unknown-kind'), dict(kind='supplement', role='agent'), dict(kind='schema', role='boss')):
        with pytest.raises(ValueError):
            store.path(tmp_path, aid=a, **bad)
    with pytest.raises(ValueError):
        store.path(tmp_path, 'sources', '../x')


def test_listing_by_kind_and_supplement(tmp_path):
    store.path(tmp_path, 'sources', 'PMC2.1', mkdir=True).write_text('{}')
    store.path(tmp_path, 'annotations', 'PMC3.1', mkdir=True).write_text('{}')
    store.path(tmp_path, 'document-submissions', 'PMC3.1', sid='S1', mkdir=True).write_text('{}')
    store.path(tmp_path, 'document-submission-drafts', 'PMC3.1', sid='S2', mkdir=True).write_text('{}')
    store.receipt(tmp_path, 'supplement', 'agent', 'PMC3.1', 'S1', mkdir=True).write_text('{}')
    assert store.article_ids(tmp_path, 'sources') == ['PMC2.1'] and store.article_ids(tmp_path, 'annotations') == ['PMC3.1']
    assert store.supplement_ids(tmp_path, 'document-submissions', 'PMC3.1') == ['S1']
    assert store.supplement_ids(tmp_path, 'supplement-agent-reviews', 'PMC3.1') == ['S1']
    assert store.article_ids(tmp_path, 'document-submissions') == ['PMC3.1']
    assert store.matching_ids(tmp_path, 'annotations', 'PMC3*') == ['PMC3.1']
    assert store.root_of(store.path(tmp_path, 'reviews', 'PMC3.1')) == tmp_path


def test_migration_moves_every_file_unchanged_and_is_idempotent(tmp_path):
    spec = importlib.util.spec_from_file_location('per_article_layout', ROOT / 'benchmarks/dense500/migrations/per_article_layout.py')
    mig = importlib.util.module_from_spec(spec); spec.loader.exec_module(mig)
    a = 'PMC9.1'
    old = {'sources/PMC9.1.json': {}, 'source-text/PMC9.1.txt': 'text', 'raw-xml/PMC9.1.xml': '<x/>',
           'annotations/PMC9.1.json': {'a': 1}, 'schema-parent-reviews/PMC9.1.json': {'r': 1},
           'visual-submissions/PMC9.1.supplements.SM1.json': {'supplement_id': 'SM1'},
           'document-submission-drafts/PMC9.1.supplements.S2.json': {'supplement_id': 'S2'},
           'supplement-agent-reviews/PMC9.1.S2.json': {'r': 2}, 'video-inspections/PMC9.1.VID1.agent.json': {'v': 1}}
    digests = {}
    for rel, value in old.items():
        p = tmp_path / rel; p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(value if isinstance(value, str) else json.dumps(value))
        digests[rel] = hashlib.sha256(p.read_bytes()).hexdigest()
    (tmp_path / 'acquisition').mkdir(); (tmp_path / 'acquisition/keep.json').write_text('{}')
    moves, errors = mig.plan(tmp_path)
    assert not errors and len(moves) == len(old)
    mig.apply(tmp_path, moves)
    expected = {'sources/PMC9.1.json': store.path(tmp_path, 'sources', a),
                'source-text/PMC9.1.txt': store.path(tmp_path, 'source-text', a),
                'raw-xml/PMC9.1.xml': store.path(tmp_path, 'raw-xml', a),
                'annotations/PMC9.1.json': store.path(tmp_path, 'annotations', a),
                'schema-parent-reviews/PMC9.1.json': store.receipt(tmp_path, 'schema', 'parent', a),
                'visual-submissions/PMC9.1.supplements.SM1.json': store.path(tmp_path, 'supplement-visual-submissions', a, sid='SM1'),
                'document-submission-drafts/PMC9.1.supplements.S2.json': store.path(tmp_path, 'document-submission-drafts', a, sid='S2'),
                'supplement-agent-reviews/PMC9.1.S2.json': store.receipt(tmp_path, 'supplement', 'agent', a, 'S2'),
                'video-inspections/PMC9.1.VID1.agent.json': store.path(tmp_path, 'video-inspections', a, role='agent', sid='VID1')}
    for rel, new in expected.items():
        assert hashlib.sha256(new.read_bytes()).hexdigest() == digests[rel]
        assert not (tmp_path / rel).exists() and not (tmp_path / rel).parent.exists()
    assert (tmp_path / 'acquisition/keep.json').exists()
    assert mig.plan(tmp_path) == ([], [])


def test_migration_refuses_unmapped_files(tmp_path):
    spec = importlib.util.spec_from_file_location('per_article_layout2', ROOT / 'benchmarks/dense500/migrations/per_article_layout.py')
    mig = importlib.util.module_from_spec(spec); spec.loader.exec_module(mig)
    (tmp_path / 'annotations').mkdir(); (tmp_path / 'annotations/notes.txt').write_text('x')
    (tmp_path / 'mystery').mkdir(); (tmp_path / 'mystery/PMC1.1.json').write_text('{}')
    moves, errors = mig.plan(tmp_path)
    assert len(errors) == 2
