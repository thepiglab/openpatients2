import copy
import json

import pytest

from openpatients2.extraction_audits import summarize


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value))


def sparse(record_id, status='valid'):
    return {'task': 'timeline_v2', 'identity': {'record_id': record_id}, 'status': status,
        'relation_candidate_policy': 'source-anchor-sparse-relations/1',
        'relation_candidate_audit': {'total_unordered_pairs': 45, 'candidate_pairs': 20,
            'unassessed_pairs': 25, 'successful_candidate_pairs': 18, 'failed_candidate_pairs': 2,
            'all_candidates_assigned_to_leaf_requests': True},
        'global_graph_audit': {'locally_accepted_edges': 12, 'retained_edges': 8, 'quarantined_edges': 3}}


def clinical(record_id):
    return {'policy': 'clinical-source-windows/1', 'task': 'observations',
        'identity': {'record_id': record_id}, 'all_segments_attempted': True, 'all_windows_valid': False,
        'source_decisions': [{'segment_id': 's1', 'disposition': 'extracted'},
                             {'segment_id': 's2', 'disposition': 'unresolved'}],
        'cell_decisions': [{'cell_id': 'c1', 'disposition': 'extracted'},
                           {'cell_id': 'c2', 'disposition': 'unresolved'}],
        'batches': [{'status': 'valid'}, {'status': 'failed'}],
        'cell_linkage_audit': [{'status': 'unresolved', 'measurement_fields_changed': False}],
        'attribution_quarantine': [{'scope': 'aggregate'}, {'scope': 'uncertain'}]}


def test_sparse_summary_exposes_unassessed_pairs_even_for_valid_graphs_without_double_counting(tmp_path):
    attempt = tmp_path / 'attempt'
    one, two = sparse('p1'), sparse('p2', 'partial')
    one['relation_candidate_audit'].update(successful_candidate_pairs=20, failed_candidate_pairs=0)
    for name, task in [('one', one), ('two', two), ('child', {**one, 'task': 'timeline_relations'}),
                       ('replaced', {**one, 'superseded_by_split': True})]:
        write(attempt / 'extraction/tasks' / (name + '.json'), task)
    files_before = {str(p): p.read_bytes() for p in attempt.rglob('*.json')}
    report = summarize([('a1', 'train', attempt)])
    timeline = report['timeline']
    assert timeline['assembled_graphs'] == timeline['sparse_graphs'] == 2
    assert timeline['sparse_graph_statuses'] == {'valid': 1, 'partial': 1}
    assert timeline['total_unordered_pairs'] == 90
    assert timeline['candidate_pairs'] == 40
    assert timeline['unassessed_pairs'] == 50
    assert timeline['successful_candidate_pairs'] == 38
    assert timeline['failed_candidate_pairs'] == 2
    assert timeline['retained_edges'] == 16
    assert timeline['quarantined_edges'] == 6
    assert timeline['graphs_with_unassessed_pairs'] == 2
    assert timeline['graphs_with_failed_candidate_pairs'] == 1
    assert not report['semantic_coverage_verified']
    assert 'selected' in timeline['coverage_scope']
    assert report['per_article'][0]['split'] == 'train'
    assert files_before == {str(p): p.read_bytes() for p in attempt.rglob('*.json')}


def test_missing_counts_and_legacy_graphs_do_not_become_zero_or_full_coverage(tmp_path):
    write(tmp_path / 'extraction/tasks/one.json', sparse('p1'))
    two = sparse('p2'); del two['relation_candidate_audit']['unassessed_pairs']
    two['relation_candidate_audit']['failed_candidate_pairs'] = True  # bool is not a count.
    two['relation_candidate_audit']['all_candidates_assigned_to_leaf_requests'] = False
    del two['global_graph_audit']
    write(tmp_path / 'extraction/tasks/two.json', two)
    write(tmp_path / 'extraction/tasks/legacy.json', {'task': 'timeline_v2', 'status': 'valid'})
    result = summarize([('a', 'train', tmp_path)])['timeline']
    assert result['assembled_graphs'] == 3
    assert result['sparse_graphs'] == 2
    assert result['graphs_without_sparse_audit'] == 1
    assert result['unassessed_pairs'] is None
    assert result['failed_candidate_pairs'] is None
    assert result['retained_edges'] is None
    assert result['recorded_counts']['unassessed_pairs'] == 25
    assert result['metric_graph_counts']['unassessed_pairs'] == 1
    assert result['graphs_with_assignment_gaps'] == 1


def test_clinical_coverage_counts_assembled_audit_once_and_preserves_unknown_attribution(tmp_path):
    audit = clinical('p1')
    write(tmp_path / 'extraction/tasks/one.json', {'task': 'observations', 'status': 'partial',
                                                'coverage_audit': audit})
    write(tmp_path / 'extraction/coverage/p1-observations.json', audit)
    write(tmp_path / 'extraction/tasks/child.json', {'task': 'clinical_window_observations', 'status': 'valid',
                                                  'data': {'attribution_quarantine': audit['attribution_quarantine']}})
    report = summarize([('a', 'train', tmp_path)])
    result = report['clinical']
    assert result['assembled_sections'] == 1
    assert result['section_statuses'] == {'partial': 1}
    assert result['all_segments_attempted_sections'] == 1
    assert result['invalid_window_sections'] == 1
    assert result['source_decisions'] == {'extracted': 1, 'unresolved': 1}
    assert result['cell_decision_count'] == 2
    assert result['cell_linkage_review_count'] == 1
    assert result['window_count'] == 2
    assert result['window_statuses'] == {'valid': 1, 'failed': 1}
    assert result['attribution_quarantined_facts'] == 2
    assert result['attribution_quarantine_by_scope'] == {'aggregate': 1, 'uncertain': 1}
    assert report['audit_files_readable']


def test_clinical_file_fallback_and_conflicting_copy_are_audited(tmp_path):
    audit = clinical('p1')
    write(tmp_path / 'extraction/tasks/one.json', {'task': 'observations', 'status': 'partial',
                                                'coverage_audit': audit})
    conflicting = copy.deepcopy(audit); conflicting['attribution_quarantine'] = []
    write(tmp_path / 'extraction/coverage/p1-observations.json', conflicting)
    write(tmp_path / 'extraction/coverage/p2-observations.json', clinical('p2'))
    report = summarize([('a', 'train', tmp_path)])
    assert report['clinical']['assembled_sections'] == 2
    assert report['clinical']['section_statuses'] == {'partial': 1, 'unavailable': 1}
    assert report['clinical']['attribution_quarantined_facts'] == 4
    assert not report['audit_files_readable']
    assert len(report['read_errors']) == 1
    assert 'Conflicting' in report['read_errors'][0]['error']


def test_unavailable_audits_and_unreadable_files_are_visible(tmp_path):
    report = summarize([('missing', 'train', tmp_path / 'missing')])
    assert not report['audit_files_readable']
    assert report['timeline']['candidate_pairs'] is None
    assert report['clinical']['attribution_quarantined_facts'] is None
    broken = tmp_path / 'broken/extraction/tasks/broken.json'
    broken.parent.mkdir(parents=True); broken.write_text('{')
    report = summarize([('broken', 'validation', tmp_path / 'broken')])
    assert len(report['read_errors']) == 1
    assert report['read_errors'][0]['file'] == 'extraction/tasks/broken.json'
    assert report['per_article'][0]['read_error_count'] == 1


def test_empty_inputs_and_duplicate_articles(tmp_path):
    report = summarize(iter([]))
    assert report['completed_articles'] == 0
    assert report['timeline']['candidate_pairs'] is None
    assert report['clinical']['source_decision_count'] is None
    assert report['audit_files_readable']
    with pytest.raises(ValueError, match='Duplicate completed article'):
        summarize([('a', 'train', tmp_path), ('a', 'train', tmp_path)])
