"""Tests of experimental conservation/gating, not clinical accuracy."""
from copy import deepcopy
import importlib.util
from pathlib import Path

spec = importlib.util.spec_from_file_location('refinement_v1', Path(__file__).parents[1]/'experiments/refinement_v1.py')
study = importlib.util.module_from_spec(spec)
spec.loader.exec_module(study)


def test_literal_spans_keep_every_occurrence_and_reject_fabrication():
    article = {'segments': [{'segment_id': 'b1', 'text': 'ethanol then ethanol'}]}
    valid, rejected = study.verified_spans({'spans': [
        {'segment_id': 'b1', 'quote': 'ethanol'}, {'segment_id': 'b1', 'quote': 'fomepizole'},
        {'segment_id': 'b1', 'quote': 'ethanol'}, 'bad']}, article, 'medications')
    assert len(valid) == 1 and len(rejected) == 2
    assert valid[0]['segment_offsets'] == [[0, 7], [13, 20]]


def test_cross_domain_overlaps_are_not_deduplicated_away():
    a = {'span_id': 'a', 'domain': 'medications', 'segment_id': 'b1', 'segment_offsets': [[0, 7]]}
    b = {**a, 'span_id': 'b', 'domain': 'procedures_devices', 'segment_offsets': [[4, 12]]}
    assert study.overlaps([a, b]) == [['a', 'b']]


def test_validation_repair_cannot_win_by_erasing_values_or_time():
    before = {'numeric_value': 152, 'unit': 'mmol/L', 'time': {'text': 'At 48 h'}}
    after = {'numeric_value': None, 'unit': None, 'time': {'text': None}}
    assert study.erased_fields(before, after) == ['numeric_value', 'unit', 'time.text']
    changed = deepcopy(before); changed['evidence'] = [{'quote': 'At 48 h'}]
    assert study.erased_fields(before, changed) == []


def test_gate_keeps_valid_item_when_neighbor_fails_and_binds_segment():
    import json
    root = Path(__file__).parents[1]/'runs/medical-fidelity-v1'
    patient = json.loads((root/'comparison/meta--muse-glimmer-30b/PMC10998798.1-p1.json').read_text())
    article = next(json.loads(l) for l in (root/'articles.jsonl').read_text().splitlines()
                   if json.loads(l)['pmcid'] == 'PMC10998798')
    first = deepcopy(patient['sections']['observations']['items'][0])
    wrong = deepcopy(first); wrong['evidence'][0]['source_section'] = 'b00005'
    before = deepcopy(first)
    kept, failed = study.split_items('observations', {'items': [first, wrong]}, article)
    assert kept == {0: before} and failed[0]['index'] == 1
    assert any('segment ID' in e for e in failed[0]['errors'])
    assert first == before


async def test_local_repair_freezes_good_neighbor_and_keeps_explicit_failed_items():
    import json
    root = Path(__file__).parents[1]/'runs/medical-fidelity-v1'
    patient = json.loads((root/'comparison/meta--muse-glimmer-30b/PMC10998798.1-p1.json').read_text())
    article = next(json.loads(l) for l in (root/'articles.jsonl').read_text().splitlines()
                   if json.loads(l)['pmcid'] == 'PMC10998798')
    original = deepcopy(patient['sections']['observations']['items'][1])
    bad = deepcopy(original); bad['evidence'][0]['source_section'] = 'b00005'
    experiment = study.Study.__new__(study.Study)
    experiment.articles = {'cat': article}
    experiment.source_packets = {('cat', 'p1'): {'patient_target': {'patient_id': 'p1'}}}
    calls = []

    async def erase(*args, **kwargs):
        calls.append(args)
        replacement = deepcopy(original)
        replacement['numeric_value'] = None
        return {'data': {'repairs': [{'index': 1, 'action': 'replace', 'reason': 'make valid', 'item': replacement}]}}

    experiment.call = erase
    candidate, audit = await experiment.local_repair({}, 'cat', 'p1', 'observations', {'items': [original, bad]}, 'test')
    assert len(calls) == 2 and candidate['items'] == [original]
    assert len(audit['pending']) == 1
    assert all(row['decision'] == 'rejected_repair' for row in audit['trace'])
    assert audit['initially_valid_facts_preserved'] == 1

    async def correct(*args, **kwargs):
        return {'data': {'repairs': [{'index': 1, 'action': 'replace', 'reason': 'correct segment binding', 'item': original}]}}

    experiment.call = correct
    fixed, audit = await experiment.local_repair({}, 'cat', 'p1', 'observations', {'items': [original, bad]}, 'test')
    assert fixed['items'] == [original, original]  # No unsafe value-based deduplication.
    assert not audit['pending'] and audit['trace'][0]['decision'] == 'accepted'
