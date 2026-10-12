"""Per-article store layout: one path map for every per-article file."""
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
