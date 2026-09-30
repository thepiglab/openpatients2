"""Guard against lost source, table corruption, and unsafe merge operations."""
import importlib.util
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path('experiments').resolve()))
import chunking_v1 as c
from chunking_adapter_v1 import envelope
import pytest


def test_real_chunks_preserve_source_atoms_and_headers():
    articles = [json.loads(l) for l in (c.OLD/'articles.jsonl').read_text().splitlines()]
    for a in articles:
        originals = {s['segment_id']: s for s in a['segments']}
        for mode, bound in [('sections', 1200), ('windows', 750)]:
            chunks = c.chunks(a['segments'], mode)
            assert {s['segment_id'] for ch in chunks for s in ch} == set(originals)
            for ch in chunks:
                assert all(s == originals[s['segment_id']] for s in ch)
                assert sum(len(s['text'].split()) for s in ch) <= bound or len(ch) == 1
            if mode == 'windows':
                for left, right in zip(chunks, chunks[1:]):
                    overlap = {s['segment_id'] for s in left} & {s['segment_id'] for s in right}
                    assert len(overlap) <= 1
                    assert sum(len(originals[s]['text'].split()) for s in overlap) <= 150


def test_merge_keeps_untouched_items_and_rejects_bad_ids_and_replacements():
    items = [{'name': 'a'}, {'name': 'b'}]
    value = {'operations': [
        {'ids': ['0', '0'], 'action': 'quarantine', 'reason': 'duplicate id'},
        {'ids': ['1'], 'action': 'replace', 'reason': 'missing fields', 'replacement': {'name': 'c'}},
        {'ids': ['99'], 'action': 'quarantine', 'reason': 'not present'}]}
    result, trace = c.apply_operations(items, value, 'conditions', [])
    assert result == items
    assert not any(t['applied'] for t in trace)
    result, trace = c.apply_operations(items, {'operations': [{'ids': ['0'], 'action': 'quarantine', 'reason': 'explicit reason'}]}, 'conditions', [])
    assert result == [items[1]]
    assert trace[0]['before'] == [items[0]]


def test_partial_map_is_not_marked_complete():
    valid = {'candidate': c.section([]), 'delivered': c.section([]), 'summary': {'claims': []}, 'dependencies': ['a']}
    invalid = {'candidate': None, 'delivered': None, 'summary': {'claims': []}, 'dependencies': ['b']}
    result = c.union([valid, invalid])
    assert not result['complete_source_pass']
    assert result['chunks_delivered'] == 1
    assert result['dependencies'] == ['a', 'b']


def test_quotes_must_be_seen_in_the_stated_segment():
    # Reuse a real valid clinical item, then hide or mislabel its evidence.
    for path in Path('runs/refinement-v1/outcomes').glob('*.json'):
        row = json.loads(path.read_text())
        items = (row['arms']['item_gate']['delivered'] or {}).get('items', [])
        if items and items[0].get('evidence'):
            item = items[0]
            evidence = item['evidence'][0]
            candidate = c.section([item])
            delivered, rejected = c.gate(row['task'], candidate, [{'segment_id': 'unseen', 'heading': '', 'text': evidence['quote']}])
            assert not delivered['items'] and rejected
            return
    raise AssertionError('No real fixture found')


def test_envelope_adapter_is_lossless_and_rejects_ambiguous_or_truncated_objects():
    facts = c.section([{'name': 'sodium', 'numeric_value': 143}])
    summary = {'claims': [], 'limitations': []}
    assert envelope(json.dumps({'section': facts, 'summary': summary})) == {'section': facts, 'summary': summary}
    assert envelope(json.dumps({'section': 'observations', **summary})+json.dumps(facts)) == {'section': facts, 'summary': summary}
    assert envelope(json.dumps({'section': 'observations', **facts, 'claims': []}))['section'] == facts
    with pytest.raises(ValueError): envelope(json.dumps(facts)+json.dumps(c.section([{'name':'sodium','numeric_value':152}])))
    with pytest.raises(ValueError): envelope(json.dumps(facts)[:-1])
