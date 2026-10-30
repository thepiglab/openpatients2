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
        reasoning_strength='max', max_figures=0, min_output_tokens=16384,
        max_output_tokens=32768, require_full_output_budget=True).model_dump()
    manifest = tmp_path / 'arm.json'; manifest.write_text(json.dumps({'extraction': cfg, 'context': 131072}))
    actual = httpx.AsyncClient
    generation_calls = []
    def response(request):
        body = json.loads(request.content)
        if request.url.path == '/tokenize':
            return httpx.Response(200, json={'count': 12, 'tokens': [1] * 12, 'max_model_len': 131072})
        generation_calls.append(request.url.path)
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
    attempt = tmp_path / 'article-attempt'
    output = attempt / 'fhir'
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
    coded = json.loads(next((output / 'experimental-coded/bundles').glob('*.json')).read_text())
    assert any(t['code'] == 'model-coding-unreviewed' for t in coded['meta']['tag'])
    assert len(generation_calls) == 1
    history = attempt / 'fhir-history'; history.mkdir()
    old_call_dir = None
    # Repeated stage-only retries move prior packets into deeper history paths.
    # Both direct and already-inherited trace links must remain resolvable.
    for index in range(2):
        previous = history / f'attempt-{index:04d}'
        output.rename(previous)
        if index == 0:
            old_call_dir = previous / 'packet' / decision['call_directory']
        repeated = await campaign.run_article_stage(inp, ROOT / 'benchmarks/dense', output,
            'http://localhost:8000/v1', manifest, cat, max_batches=1,
            previous_packet=previous / 'packet', previous_packet_original_location=output / 'packet')
        assert repeated['complete'] and repeated['coding']['reused_decisions'] == 1
        assert repeated['coding']['requests_selected'] == 0 and repeated['usage']['model_calls'] == 0
        assert len(generation_calls) == 1
        inherited = json.loads(next((output / 'packet/decisions').glob('*.json')).read_text())
        assert inherited['decision'] == decision['decision']
        assert (output / 'packet' / inherited['call_directory']).resolve() == old_call_dir.resolve()
        assert (output / 'packet' / inherited['call_directory'] / 'attempts.jsonl').exists()
        assert (output / 'packet' / inherited['call_directory'] / 'tasks' / (inherited['task_key'] + '.json')).exists()
        repeated_bundle = json.loads(next((output / 'experimental-coded/bundles').glob('*.json')).read_text())
        def codes(bundle):
            return [e['resource']['code']['coding'] for e in bundle['entry']
                    if e['resource'].get('code', {}).get('coding')]
        assert codes(repeated_bundle) == codes(coded)

    # The same fact identifier with different source text cannot inherit an old
    # decision. It is the full request hash, not just its filename, that matters.
    changed_article = deepcopy(article)
    source = json.loads((ROOT / 'benchmarks/dense/articles' / article['article_id'] / 'source.json').read_text())
    source['text'] += '\nAdditional source fixture text.'
    source['text_sha256'] = hashlib.sha256(source['text'].encode()).hexdigest()
    changed_article['text_sha256'] = source['text_sha256']
    changed_sources = tmp_path / 'changed-sources'; changed_sources.mkdir()
    (changed_sources / (article['article_id'] + '.json')).write_text(json.dumps(source))
    changed_input = tmp_path / 'changed-input.jsonl'; changed_input.write_text(json.dumps(changed_article) + '\n')
    with pytest.raises(ValueError, match='changed source facts'):
        await campaign.run_article_stage(changed_input, changed_sources, tmp_path / 'stale-source',
            'http://localhost:8000/v1', manifest, cat, previous_packet=output / 'packet')
    changed_catalog = deepcopy(catalog); changed_catalog['concepts'][0]['version'] = 'another-fixture-release'
    changed_cat = tmp_path / 'changed-catalog.json'; changed_cat.write_text(json.dumps(changed_catalog))
    with pytest.raises(ValueError, match='changed catalog'):
        await campaign.run_article_stage(inp, ROOT / 'benchmarks/dense', tmp_path / 'stale-catalog',
            'http://localhost:8000/v1', manifest, changed_cat, previous_packet=output / 'packet')
    assert len(generation_calls) == 1


async def test_skip_saved_decision_still_rejects_invalid_candidate(tmp_path, monkeypatch):
    # An existing file is not sufficient: the request hash and candidate choice
    # must validate before it can suppress a live coding call.
    article = json.loads((ROOT / 'tests/fixtures/unified_scoring_reference.jsonl').read_text().splitlines()[0])
    inp = tmp_path / 'input.jsonl'; inp.write_text(json.dumps(article) + '\n')
    packet = tmp_path / 'packet'
    pipeline.prepare(inp, ROOT / 'benchmarks/dense', packet)
    request = next(read_jsonl(packet / 'requests.jsonl'))
    from openpatients2.provenance import json_digest
    from openpatients2.pilot_extract import PilotConfig
    saved = packet / 'decisions' / (request['id'] + '.json'); saved.parent.mkdir()
    saved.write_text(json.dumps({'request_sha256': json_digest(request), 'review_status': 'model_unreviewed',
        'decision': {'decision': 'select', 'candidate_id': 'not-an-offered-candidate',
                     'relation': 'equivalent', 'rationale': 'Invalid saved fixture'}}))
    manifest = tmp_path / 'arm.json'
    manifest.write_text(json.dumps({'extraction': PilotConfig().model_dump(), 'context': 131072}))
    actual = httpx.AsyncClient
    def forbidden(request):
        raise AssertionError('Invalid saved choices must not trigger an endpoint request')
    monkeypatch.setattr(httpx, 'AsyncClient', lambda **kw: actual(transport=httpx.MockTransport(forbidden), **kw))
    with pytest.raises(ValueError, match='candidate set'):
        await campaign.select_batches(packet, 'http://localhost:8000/v1', manifest)
