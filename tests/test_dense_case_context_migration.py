"""Per-patient case_context follows the article-derived packet rule after migration."""
import importlib.util
import json
from pathlib import Path
import shutil
import sys
import time

import pytest

ROOT = Path(__file__).resolve().parents[1]
from openpatients2.dense_store import load as load_store
store = load_store()
R = ROOT / 'benchmarks/dense500'


@pytest.fixture
def migration(monkeypatch):
    monkeypatch.syspath_prepend(str(R))
    spec = importlib.util.spec_from_file_location('dense_migrate_cc', R / 'migrate_case_context.py')
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    return module


def copy_article(tmp_path, aid):
    root = tmp_path / 'dense500'
    for folder in ('sources', 'annotations', 'drafts', 'reviews', 'schema-submissions', 'schema-agent-reviews', 'schema-parent-reviews'):
        if store.path(R, folder, aid).exists():
            shutil.copyfile(store.path(R, folder, aid), store.path(root, folder, aid, mkdir=True))
    return root


def test_rule_rejects_per_patient_multi_patient(monkeypatch):
    monkeypatch.syspath_prepend(str(R))
    from validate import case_context_errors
    fact = lambda **fields: {'patients': [{'patient_id': 'p1', 'facts': [{'fact_id': 'p1-f1', 'task': 'case_context', 'fields': fields}]}]}
    assert case_context_errors(fact(case_kind='clinical_case', multiple_index_patients=False)) == []
    assert case_context_errors(fact(case_kind='multi_patient', multiple_index_patients=True))
    assert case_context_errors(fact(case_kind='clinical_case'))  # null/absent is not false
    from openpatients2.unified_record import case_context_warnings
    assert case_context_warnings({'patients': [{'patient_id': 'p1', 'sections': {'case_context': {'case_kind': 'multi_patient',
                                                                                                  'multiple_index_patients': True}}}]})


def test_migration_changes_only_case_context_and_validates(migration, tmp_path):
    from validate import case_context_errors, validate, digest
    from schema_review import validate_submission
    from review_record import save_review
    candidates = [aid for aid in store.article_ids(R, 'annotations')
                  if migration.nonconforming(json.loads(store.path(R, 'annotations', aid).read_text()))
                  and store.path(R, 'schema-submissions', aid).exists()]
    if not candidates:
        pytest.skip('no unmigrated reference article left to exercise')
    aid = candidates[0]
    root = copy_article(tmp_path, aid)
    before = json.loads(store.path(root, 'annotations', aid).read_text())
    with pytest.raises(ValueError, match='case_context_packet_rule'):
        save_review(root, before, corrections=[], assessment={})
    report = migration.run(apply=True, roots=[root], now=time.time() + 10 ** 7)
    entry = report['articles'][0]
    if entry['skipped_reason']:
        pytest.skip(entry['skipped_reason'])
    after = json.loads(store.path(root, 'annotations', aid).read_text())
    source = json.loads(store.path(root, 'sources', aid).read_text())
    assert validate(after, source) == [] and case_context_errors(after) == []
    assert all(p.endswith(('/case_kind', '/multiple_index_patients')) for p in migration.diff_paths(before, after))
    submission = json.loads(store.path(root, 'schema-submissions', aid).read_text())
    validate_submission(submission, after, source)
    assert all(any(r.match(p) for r in migration.ALLOWED_SUBMISSION_PATHS) for p in entry['changed_submission_paths'])
    review = json.loads(store.path(root, 'reviews', aid).read_text())
    assert review['annotation_sha256'] != digest(after)  # receipts go stale; nothing re-signed
    for patient in after['patients']:
        for f in patient['facts']:
            if f['task'] == 'case_context':
                assert f['fields']['multiple_index_patients'] is False and f['fields'].get('case_kind') != 'multi_patient'
