from copy import deepcopy
import hashlib
import json
from pathlib import Path

import httpx
import pytest

from openpatients2 import fhir_campaign as campaign
from openpatients2 import fhir_pipeline as pipeline
from openpatients2.data import read_jsonl
from openpatients2.fhir_export import compile_patient, fact_entries
from openpatients2.fhir_validation import validate_bundle
from openpatients2.measurement_series import Cell, expected_parse, series_rows
from openpatients2.terminology import candidates, load_catalog

ROOT = Path(__file__).resolve().parents[1]
CATALOG = ROOT / 'configs/terminology/icd10cm-2027.json.gz'


def test_official_catalog_is_pinned_leaf_only_and_retains_ancestor_rules():
    assert hashlib.sha256(CATALOG.read_bytes()).hexdigest() == '5a58583057090f62d58895c68a100e0a363f6f9c8eac81cbe522cc7634731c0d'
    catalog = load_catalog(CATALOG)
    assert len(catalog['concepts']) == 74879
    codes = {c['code']: c for c in catalog['concepts']}
    assert codes['I10']['version'] == '2027' and 'E11' not in codes
    assert codes['E08.3211']['context']['tabular_code'] == 'E08.321'
    assert any(n['kind'] == 'sevenChrDef' for n in codes['E08.3211']['context']['tabular_instructions'])
    hypertension = candidates(catalog, 'conditions', 'Essential hypertension')
    assert any(c['code'] == 'I10' for c in hypertension)
    assert candidates(catalog, 'medications', 'Essential hypertension') == []
    assert candidates(catalog, 'conditions', 'qwerty-nonexistent') == []


def test_modified_official_archive_cannot_build(tmp_path):
    from openpatients2.icd10cm_catalog import read_release
    p = tmp_path / 'changed.zip'; p.write_bytes(b'not the pinned release')
    with pytest.raises(ValueError, match='changed'):
        read_release('descriptions', p)


def test_batch_cannot_drop_duplicate_or_cross_assign_candidates():
    reqs = [{'id': 'one', 'candidates': [{'candidate_id': 'a'}]},
            {'id': 'two', 'candidates': [{'candidate_id': 'b'}]}]
    abstain = {'decision': 'abstain', 'candidate_id': None, 'relation': None, 'rationale': 'Unknown'}
    good = {'decisions': [{'request_id': r['id'], **abstain} for r in reqs]}
    assert len(campaign.check_batch(reqs, good)['decisions']) == 2
    for values in [good['decisions'][:1], [good['decisions'][0]] * 2]:
        with pytest.raises(ValueError, match='exactly one'):
            campaign.check_batch(reqs, {'decisions': values})
    bad = deepcopy(good)
    bad['decisions'][0].update(decision='select', candidate_id='b', relation='equivalent')
    with pytest.raises(ValueError, match='candidate set'):
        campaign.check_batch(reqs, bad)


def measured_article():
    a = json.loads((ROOT / 'tests/fixtures/unified_scoring_reference.jsonl').read_text().splitlines()[0])
    p = a['patients'][0]
    p['sections'] = {key: None for key in p['sections']}
    text = 'Patient 1. Sodium 120 mmol/L on day 2.'
    source = {'article_id': a['article_id'], 'text': text, 'text_sha256': hashlib.sha256(text.encode()).hexdigest(),
              'segments': [{'segment_id': 's1', 'kind': 'paragraph', 'text': text}]}
    a['text_sha256'] = source['text_sha256']
    def cell(cid, role, raw):
        value = {key: None for key in Cell.model_fields}
        value.update(cell_id=cid, origin='article_text', role=role, raw_text=raw, segment_id='s1')
        return value
    cells = [cell('owner', 'context', 'Patient 1'), cell('name', 'row_label', 'Sodium'),
             cell('value', 'value', '120 mmol/L'), cell('time', 'column_header', 'day 2')]
    parse_status, parsed = expected_parse('120 mmol/L')
    series = {'series_id': 's1', 'measurement_name': 'Sodium', 'name_cell_id': 'name',
        'measurement_kind': 'laboratory', 'ownership': 'individual', 'patient_ids': [p['patient_id']],
        'ownership_evidence': [{'segment_id': None, 'cell_id': 'owner', 'quote': 'Patient 1'}],
        'reference_range_cell_id': None, 'scoring': 'include', 'notes': 'Source-attributed extraction; unreviewed clinical meaning.',
        'points': [{'point_id': 'point1', 'value_cell_id': 'value', 'parse_status': parse_status, 'parsed': parsed,
            'unit_text': None, 'unit_cell_id': None,
            'time': {'time_cell_id': 'time', 'time_text': 'day 2', 'offset_value': 2,
                'offset_unit': 'days', 'offset_anchor_text': None, 'offset_basis': 'Printed day number'}}]}
    target = {'cells': cells, 'series': [series]}
    p['measurement_series'] = list(series_rows(a['article_id'], target))
    return a, p, source


def test_numeric_series_maps_literal_point_and_preserves_patient_ownership():
    a, p, s = measured_article()
    assert 'owner' in {c['cell_id'] for c in p['measurement_series'][0]['cells']}
    bundle, report = compile_patient(a, p, s, recorded='2026-10-30T00:00:00Z')
    assert validate_bundle(bundle)['passed']
    obs = next(e['resource'] for e in bundle['entry'] if e['resource']['resourceType'] == 'Observation')
    assert obs['valueQuantity'] == {'value': 120, 'unit': 'mmol/L'}
    assert 'effectiveDateTime' not in obs and 'encounter' not in obs and obs['status'] == 'unknown'
    assert report['facts'][0]['status'] == 'mapped'
    p['measurement_series'][0]['points'][0]['parsed']['magnitude'] = '130'
    _, report = compile_patient(a, p, s, recorded='2026-10-30T00:00:00Z')
    assert report['facts'][0]['reason'] == 'invalid_series_source_ownership_or_parse'


@pytest.mark.parametrize('change,reason', [
    ('owner', 'series_patient_ownership_unresolved'),
    ('source', 'invalid_series_source_ownership_or_parse'),
    ('dose', 'dose_series_requires_medication_mapping')])
def test_series_unsafe_attribution_raw_values_and_doses_defer(change, reason):
    a, p, s = measured_article()
    series = p['measurement_series'][0]
    if change == 'owner': series['patient_ids'] = ['someone-else']
    if change == 'source': series['cells'][0]['raw_text'] = 'forged text'
    if change == 'dose': series['measurement_kind'] = 'medication_dose'
    bundle, report = compile_patient(a, p, s, recorded='2026-10-30T00:00:00Z')
    assert report['facts'][0]['reason'] == reason
    assert not any(e['resource']['resourceType'] == 'Observation' for e in bundle['entry'])


async def test_live_batch_stage_has_raw_reasoning_tokens_and_separate_unreviewed_export(tmp_path, monkeypatch):
    from openpatients2 import beam_recipe as beam
    from openpatients2.pilot_extract import PilotConfig
    article = json.loads((ROOT / 'tests/fixtures/unified_scoring_reference.jsonl').read_text().splitlines()[0])
    patient = article['patients'][0]
    fact = next(e['fact'] for e in fact_entries(article, patient) if e['task'] == 'conditions')
    patient['sections'] = {key: None for key in patient['sections']}
    patient['sections']['conditions'] = {'items': [fact]}
    patient['measurement_series'] = []
    article['patients'] = [patient]
    catalog = {'format': 'openpatients2-terminology-catalog/1', 'source': 'test-only', 'license': 'test-only',
        'concepts': [{'system': 'http://hl7.org/fhir/sid/icd-10-cm', 'version': 'fixture', 'code': 'TEST',
                      'display': fact['name'], 'tasks': ['conditions']}]}
    cat = tmp_path / 'catalog.json'; cat.write_text(json.dumps(catalog))
    inp = tmp_path / 'input.jsonl'; inp.write_text(json.dumps(article) + '\n')
    cfg = PilotConfig(model_id=beam.NVFP4, revision=beam.PINS[beam.NVFP4]['revision'],
        served_model=beam.NVFP4, tokenizer_contract=beam.TOKENIZER_CONTRACT,
        temperature=.7, top_p=.99, top_k=None, stop_token_ids=beam.STOP_TOKEN_IDS,
        reasoning_strength='max', max_figures=0).model_dump()
    manifest = tmp_path / 'arm.json'; manifest.write_text(json.dumps({'extraction': cfg, 'context': 131072}))
    actual = httpx.AsyncClient
    def response(request):
        body = json.loads(request.content)
        if request.url.path == '/tokenize':
            return httpx.Response(200, json={'count': 12, 'tokens': [1] * 12, 'max_model_len': 131072})
        assert body['chat_template_kwargs']['reasoning_effort'] == 'max'
        payload = body['messages'][-1]['content'].split('REQUESTS:\n', 1)[1].split('\nSCHEMA:', 1)[0]
        requests = [p['request'] for p in json.loads(payload)]
        result = {'decisions': [{'request_id': r['id'], 'decision': 'select',
            'candidate_id': r['candidates'][0]['candidate_id'], 'relation': 'equivalent',
            'rationale': 'Fixture choice; not clinical review.'} for r in requests]}
        events = [{'choices': [{'index': 0, 'delta': {'reasoning_content': 'Returned mapping trace'}}]},
            {'choices': [{'index': 0, 'delta': {'content': json.dumps(result)}, 'finish_reason': 'stop'}],
             'usage': {'prompt_tokens': 12, 'completion_tokens': 40}}]
        return httpx.Response(200, text=''.join('data: ' + json.dumps(e) + '\n\n' for e in events) + 'data: [DONE]\n\n')
    monkeypatch.setattr(httpx, 'AsyncClient', lambda **kw: actual(transport=httpx.MockTransport(response), **kw))
    output = tmp_path / 'output'
    report = await campaign.run_article_stage(inp, ROOT / 'benchmarks/dense', output,
        'http://localhost:8000/v1', manifest, cat, max_batches=1)
    assert report['complete'] and not report['clinical_accuracy_verified']
    assert report['usage']['prompt_tokens'] == 12 and report['usage']['completion_tokens'] == 40
    assert report['exports']['uncoded']['codings_attached'] == 0
    assert report['exports']['experimental-coded']['codings_attached'] == 1
    attempts = list(read_jsonl(output / report['call_ledgers'][0]))
    assert attempts[0]['response']['reasoning_text'] == 'Returned mapping trace'
    decision = json.loads(next((output / 'packet/decisions').glob('*.json')).read_text())
    assert decision['review_status'] == 'model_unreviewed'
    coded = json.loads(next((output / 'experimental-coded').rglob('*.bundle.json')).read_text())
    assert any(t['code'] == 'model-coding-unreviewed' for t in coded['meta']['tag'])
