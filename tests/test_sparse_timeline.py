"""Bounded temporal retrieval, without treating retrieval as evidence."""
from copy import deepcopy
from types import SimpleNamespace
import json

import pytest

from openpatients2.sparse_timeline import canonical_events, plan
from openpatients2 import staged_timeline as timeline
from openpatients2.pilot_extract import sources_for
from openpatients2.evidence_recovery import resolve_timeline_spans


RID = 'PMC1.1:p1'


def event(i, *, text=None, segment=None):
    segment = segment or f's{i}'
    quote = text or f'Patient had occurrence {i}.'
    evidence = {'segment_id': segment, 'quote': quote}
    return {'event_id': f'e{i}', 'record_id': RID, 'episode_id': None, 'kind': 'test',
            'occurrence': 'occurred', 'description': quote, 'fact_ids': [],
            'evidence': [evidence], 'attribution_evidence': [deepcopy(evidence)], 'times': []}


def source(n):
    return [{'segment_id': f's{i}', 'text': f'Patient had occurrence {i}.'} for i in range(n)]


def test_sparse_candidate_complexity_and_unassessed_complement_are_explicit():
    events = [event(i) for i in range(1000)]
    pairs, audit = plan(events, source(1000), RID, neighbors=2, pairs_per_event_budget=8)
    assert len(pairs) == 1997
    assert len({frozenset(p) for p in pairs}) == len(pairs)
    assert audit['total_unordered_pairs'] == 499500
    assert audit['unassessed_pairs'] == 499500 - 1997
    assert len(pairs) <= audit['pair_budget'] == 8000
    assert not audit['candidate_order_is_chronology']
    assert not audit['semantic_coverage_verified'] and not audit['selection_is_relation_evidence']
    assert pairs == plan(deepcopy(events), source(1000), RID, neighbors=2, pairs_per_event_budget=8)[0]


def test_distant_calendar_and_literal_relative_anchors_get_bridge_candidates():
    events = [event(i) for i in range(30)]
    events[0]['description'] = 'Surgery performed.'
    events[-1]['times'] = [{'text': 'Two days after surgery', 'kind': 'relative',
        'calendar_value': None, 'evidence': [{'segment_id': 's29', 'quote': 'Two days after surgery she recovered.'}]}]
    events[3]['times'] = [{'text': '2020-01-03', 'kind': 'calendar', 'calendar_value': '2020-01-03'}]
    events[25]['times'] = [{'text': '2021-02-01', 'kind': 'calendar', 'calendar_value': '2021-02-01'}]
    pairs, audit = plan(events, source(30), RID)
    assert ['e0', 'e29'] in pairs and ['e3', 'e25'] in pairs
    bridge = audit['relative_bridges'][0]
    assert bridge['selected_matches'] == ['e0'] and not bridge['identity_verified']
    # Invented time phrases cannot create remote bridge candidates.
    events[-1]['times'][0]['evidence'][0]['quote'] = 'The source says something else.'
    assert ['e0', 'e29'] not in plan(events, source(30), RID)[0]


def test_repeated_ambiguous_treatments_remain_distinct_and_bridge_caps_audited():
    events = [event(i, text='Surgery performed.') for i in range(20)]
    e = event(20)
    e['times'] = [{'text': 'After surgery', 'kind': 'relative', 'calendar_value': None,
                   'evidence': [{'segment_id': 's20', 'quote': 'After surgery patient recovered.'}]}]
    pairs, audit = plan(events + [e], source(21), RID, bridge_matches=3)
    assert len(audit['event_ids']) == 21
    bridge = audit['relative_bridges'][0]
    assert len(bridge['selected_matches']) == 3 and len(bridge['unassessed_matches']) == 17
    assert not bridge['identity_verified']
    assert len(pairs) <= audit['pair_budget'] == 32 * 21


def test_candidates_reject_foreign_patient_and_duplicate_ids():
    events = [event(0), event(1)]
    events[1]['record_id'] = 'PMC1.1:p2'
    with pytest.raises(ValueError, match='one patient'):
        plan(events, source(2), RID)
    events[1] = deepcopy(events[0])
    with pytest.raises(ValueError, match='unique events'):
        plan(events, source(2), RID)


def test_canonicalization_keeps_repeated_visits_and_original_ids():
    a = event(0, text='Drug given.')
    b = deepcopy(a); b['event_id'] = 'other-local-id'
    c = event(2, text='Drug given.')  # Identical description, distinct source/visit.
    stages = [{'task_key': str(i), 'identity': {'batch': i}, 'data': {'events': [e]}}
              for i, e in enumerate([a, b, c])]
    before = deepcopy(stages)
    events, audit = canonical_events(stages)
    assert stages == before
    assert len(events) == 2 and audit['original_events'] == 3
    assert audit['mapping'][0]['event_id'] == audit['mapping'][1]['event_id']
    assert audit['mapping'][2]['event_id'] != audit['mapping'][0]['event_id']
    assert audit['mapping'][1]['original_event_id'] == 'other-local-id'
    assert audit['semantic_merges'] == 0


@pytest.mark.parametrize('cycle', [False, True])
async def test_sparse_extract_keeps_either_direction_and_global_conflict_gate(tmp_path, monkeypatch, cycle):
    article = {'article_id': 'PMC1.1', 'segments': source(3)}
    _, sources = sources_for(article)
    calls = []
    async def fits(*args): return True
    monkeypatch.setattr(timeline, 'fits', fits)
    class Runner:
        config = SimpleNamespace(timeline_segment_batch_size=1, timeline_relation_batch_size=1,
            timeline_target_batch_size=1, timeline_bounded_context=True, timeline_prefer_windows=True,
            timeline_scope_repair=True, timeline_sparse_relations=True, refinement_policy='source_aware')
        output = tmp_path; arm = 'targeted'; results = []
        async def call(self, task, messages, checker, identity, replica, segments, **kwargs):
            calls.append(identity)
            if task == 'timeline_events':
                n = int(identity['focus_segments'][0][1:])
                assert 'EPISODE GRANULARITY' in messages[-1]['content']
                value = {'schema_version': 'patient-timeline/2', 'record_id': RID,
                         'events': [event(n)], 'edges': [], 'limitations': []}
            else:
                pair = identity['candidate_event_pairs'][0]
                assert len(identity['candidate_event_pairs']) == 1
                assert 'establish NO order' in messages[-1]['content']
                if cycle:
                    a, b = pair if pair != ['e1', 'e3'] else ['e3', 'e1']
                else:
                    # Direction can oppose source/ID order; neither is chronology.
                    a, b = pair[::-1]
                value = {'edges': [{'edge_id': 'local-edge', 'from_event_id': a, 'to_event_id': b,
                    'relation': 'before', 'offset': None,
                    'evidence': [{'segment_id': 's1', 'quote': article['segments'][1]['text']}]}], 'limitations': []}
            value, _ = resolve_timeline_spans(value, 'PMC1.1:jats', segments)
            return {'task': task, 'identity': identity, 'status': 'valid', 'data': checker(value),
                    'attempts': [{'response': {'reasoning_text': 'preserved source reasoning'}}]}
    (tmp_path / 'tasks').mkdir()
    result = await timeline.extract(Runner(), article, {'patient_id': 'p1'}, {}, [],
        {'article_id': 'PMC1.1', 'record_id': RID}, 0, sources)
    assert len(result['data']['events']) == 3
    assert result['relation_candidate_audit']['candidate_pairs'] == 3
    assert result['relation_candidate_audit']['all_candidates_assigned_to_leaf_requests']
    assert all(a['response']['reasoning_text'] for a in result['attempts'])
    if cycle:
        assert result['status'] == 'partial' and result['data']['edges'] == []
        assert result['global_graph_audit']['quarantined_edges'] == 3
        assert len(result['quarantine']) == 3
    else:
        assert result['status'] == 'valid' and len(result['data']['edges']) == 3
        assert all(e['from_event_id'] > e['to_event_id'] for e in result['data']['edges'])


async def test_sparse_context_splitting_preserves_pair_coverage(tmp_path, monkeypatch):
    article = {'article_id': 'PMC1.1', 'segments': source(8)}
    _, sources = sources_for(article)
    calls = []
    async def fits(runner, task, messages, replica):
        if task == 'timeline_events': return True
        content = messages[-1]['content']
        pairs = json.loads(content.split('CANDIDATE_PAIRS:\n')[1].split('\nSCHEMA:')[0])
        return len(pairs) <= 2
    monkeypatch.setattr(timeline, 'fits', fits)
    class Runner:
        config = SimpleNamespace(timeline_segment_batch_size=1, timeline_relation_batch_size=2,
            timeline_target_batch_size=8, timeline_bounded_context=True, timeline_prefer_windows=True,
            timeline_scope_repair=True, timeline_sparse_relations=True, refinement_policy='source_aware')
        output = tmp_path; arm = 'targeted'; results = []
        async def call(self, task, messages, checker, identity, replica, segments, **kwargs):
            if task == 'timeline_events':
                n = int(identity['focus_segments'][0][1:])
                value = {'schema_version': 'patient-timeline/2', 'record_id': RID,
                         'events': [event(n)], 'edges': [], 'limitations': []}
            else:
                calls.extend(identity['candidate_event_pairs'])
                assert len(identity['candidate_event_pairs']) <= 2
                value = {'edges': [], 'limitations': []}
            value, _ = resolve_timeline_spans(value, 'PMC1.1:jats', segments)
            return {'task': task, 'identity': identity, 'status': 'valid', 'data': checker(value), 'attempts': []}
    (tmp_path / 'tasks').mkdir()
    result = await timeline.extract(Runner(), article, {'patient_id': 'p1'}, {}, [],
        {'article_id': 'PMC1.1', 'record_id': RID}, 0, sources)
    expected = [r['pair'] for r in result['relation_candidate_audit']['candidates']]
    assert sorted(calls) == sorted(expected) and len(calls) == len({tuple(p) for p in calls})
    assert result['status'] == 'valid'
