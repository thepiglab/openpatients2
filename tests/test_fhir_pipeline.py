import base64
from copy import deepcopy
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from openpatients2 import fhir_pipeline as pipeline
from openpatients2.fhir_export import compile_patient, fact_entries, map_fact
from openpatients2.fhir_validation import validate_bundle, official_validate
from openpatients2.provenance import json_digest
from openpatients2.terminology import load_catalog, candidates, selected_coding, validate_decision
from openpatients2.data import read_jsonl

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def specimen():
    article = json.loads((ROOT / 'tests/fixtures/unified_scoring_reference.jsonl').read_text().splitlines()[0])
    source = json.loads((ROOT / 'benchmarks/dense/articles' / article['article_id'] / 'source.json').read_text())
    return article, article['patients'][0], source


def test_real_reference_compiles_r4_and_preserves_all_deferred_facts(specimen):
    a, p, s = specimen
    bundle, mapping = compile_patient(a, p, s, recorded='2026-10-28T12:00:00Z')
    assert validate_bundle(bundle)['passed']
    assert len(mapping['facts']) == len(list(fact_entries(a, p)))
    resources = [e['resource'] for e in bundle['entry']]
    patient = next(r for r in resources if r['resourceType'] == 'Patient')
    assert 'birthDate' not in patient and 'gender' not in patient
    assert patient['name'][0]['text'].startswith('Synthetic patient')
    assert not any(r['resourceType'] in {'Practitioner', 'Encounter', 'MedicationAdministration'} for r in resources)
    docs = [r for r in resources if r['resourceType'] == 'DocumentReference']
    raw = json.loads(base64.b64decode(docs[0]['content'][0]['attachment']['data']))
    assert raw['patient'] == p and raw['license'] == a['license']
    assert mapping['clinical_accuracy_verified'] is False
    assert any(r['status'] == 'deferred' for r in mapping['facts'])


def test_patient_ids_stable_on_fact_reorder_and_separate_between_patients(specimen):
    a, p, s = specimen
    one, _ = compile_patient(a, p, s, recorded='2026-10-28T12:00:00Z')
    reordered = deepcopy(p)
    for section in reordered['sections'].values():
        if section and section.get('items'):
            section['items'].reverse()
    two, _ = compile_patient(a, reordered, s, recorded='2026-10-28T12:00:00Z')
    assert {e['fullUrl'] for e in one['entry']} == {e['fullUrl'] for e in two['entry']}
    other = deepcopy(p); other['patient_id'] = 'another'
    three, _ = compile_patient(a, other, s, recorded='2026-10-28T12:00:00Z')
    assert not {e['fullUrl'] for e in one['entry']} & {e['fullUrl'] for e in three['entry']}
    assert validate_bundle(three)['passed']


def test_negation_plan_uncertainty_and_family_are_not_affirmed_events(specimen):
    a, p, s = specimen
    entries = list(fact_entries(a, p))
    condition = deepcopy(next(e for e in entries if e['task'] == 'conditions'))
    condition['fact'].update(assertion='absent', verification_status='refuted')
    resource, _ = map_fact(condition, 'urn:uuid:patient')
    assert resource['verificationStatus']['coding'][0]['code'] == 'refuted' and 'clinicalStatus' not in resource
    condition['fact'].update(assertion='possible', verification_status='unknown')
    assert map_fact(condition, 'patient')[0]['verificationStatus']['coding'][0]['code'] == 'unconfirmed'
    condition['fact']['subject'] = 'family_member'
    assert map_fact(condition, 'patient')[0] is None
    medication = deepcopy(next(e for e in entries if e['task'] == 'medications'))
    medication['fact']['action'] = 'planned'
    resource, _ = map_fact(medication, 'patient')
    assert resource['resourceType'] == 'MedicationRequest' and resource['intent'] == 'plan'
    medication['fact']['assertion'] = 'conditional'
    assert map_fact(medication, 'patient')[0] is None
    condition['fact']['scoring'] = 'review_only'
    assert map_fact(condition, 'patient')[1] == 'review_only_or_quarantined'


def test_unresolved_reference_and_missing_required_primitive_fail(specimen):
    a, p, s = specimen
    bundle, _ = compile_patient(a, p, s, recorded='2026-10-28T12:00:00Z')
    observation = next(e['resource'] for e in bundle['entry'] if e['resource']['resourceType'] == 'Observation')
    observation['subject']['reference'] = 'urn:uuid:missing'
    observation.pop('status')
    report = validate_bundle(bundle)
    assert not report['passed']
    assert any('unresolved reference' in e for e in report['errors'])
    assert any('missing status' in e for e in report['errors'])


def test_forged_or_unreviewed_terminology_never_silently_codes(tmp_path):
    catalog = {'format': 'openpatients2-terminology-catalog/1', 'source': 'test fixture, not an imported clinical release',
        'license': 'test-only', 'concepts': [{'system': 'http://hl7.org/fhir/sid/icd-10-cm',
            'version': 'fixture', 'code': 'TEST', 'display': 'Fixture diagnosis', 'tasks': ['conditions']}]}
    path = tmp_path / 'catalog.json'; path.write_text(json.dumps(catalog))
    catalog = load_catalog(path)
    request = {'id': 'fact', 'fact': {'name': 'Fixture diagnosis'}, 'candidates': candidates(catalog, 'conditions', 'Fixture diagnosis')}
    candidate = request['candidates'][0]
    decision = {'decision': 'select', 'candidate_id': candidate['candidate_id'], 'relation': 'equivalent', 'rationale': 'Fixture match'}
    receipt = {'request_sha256': json_digest(request), 'decision': decision, 'review_status': 'model_unreviewed'}
    assert selected_coding(request, receipt) is None
    assert selected_coding(request, receipt, allow_unreviewed=True)['version'] == 'fixture'
    receipt.update(review_status='approved', reviewer='test-reviewer')
    assert selected_coding(request, receipt)['code'] == 'TEST'
    with pytest.raises(ValueError, match='candidate set'):
        validate_decision(request, {**decision, 'candidate_id': 'invented-code'})
    receipt['decision']['relation'] = 'broader'
    assert selected_coding(request, receipt) is None
    with pytest.raises(ValueError, match='different fact'):
        selected_coding({**request, 'id': 'changed'}, receipt)


def test_end_to_end_offline_export_and_snapshot_guard(specimen, tmp_path):
    a, p, s = specimen
    input_path = tmp_path / 'input.jsonl'; input_path.write_text(json.dumps(a) + '\n')
    work = tmp_path / 'work'
    manifest = pipeline.prepare(input_path, ROOT / 'benchmarks/dense', work)
    assert manifest['patients'] == len(a['patients'])
    report = pipeline.export(work, tmp_path / 'export')
    assert report['complete'] and report['statuses'].get('quarantined', 0) == 0
    assert report['statuses']['passed'] >= 1 and not report['release_ready']
    assert report['codings_attached'] == 0
    with pytest.raises(ValueError, match='require-official'):
        pipeline.export(work, tmp_path / 'strict', require_official=True)
    with (work / 'articles.jsonl').open('a') as f:
        f.write('{}\n')
    with pytest.raises(ValueError, match='modified'):
        pipeline.export(work, tmp_path / 'tampered')


def test_official_validator_requires_outcome_not_only_exit_code(tmp_path, monkeypatch):
    import openpatients2.fhir_validation as fv
    jar = tmp_path / 'validator.jar'; jar.write_bytes(b'fixture')
    bundle = tmp_path / 'bundle.json'; bundle.write_text('{}')
    commands = []
    def run(cmd, **kwargs):
        commands.append(cmd)
        Path(cmd[-1]).write_text(json.dumps({'resourceType': 'OperationOutcome',
            'issue': [{'severity': 'error', 'code': 'invalid', 'diagnostics': 'fixture failure'}]}))
        return SimpleNamespace(returncode=0, stdout='', stderr='')
    monkeypatch.setattr(fv.subprocess, 'run', run)
    report = official_validate(bundle, jar, output=tmp_path / 'outcome.json')
    assert not report['passed'] and report['official_validator_run']
    assert commands[0][commands[0].index('-tx')+1] == 'n/a'


def test_two_approved_systems_attach_to_one_fact_without_cross_patient_links(specimen, tmp_path):
    a, p, s = specimen
    fact = next(e for e in fact_entries(a, p) if e['task'] == 'conditions')
    catalog = {'format': 'openpatients2-terminology-catalog/1', 'source': 'test fixture', 'license': 'test-only',
        'concepts': [{'system': system, 'version': 'fixture', 'code': 'TEST', 'display': fact['fact']['name'],
                      'tasks': ['conditions']} for system in ('http://hl7.org/fhir/sid/icd-10-cm', 'http://snomed.info/sct')]}
    catalog_path = tmp_path / 'catalog.json'; catalog_path.write_text(json.dumps(catalog))
    input_path = tmp_path / 'input.jsonl'; input_path.write_text(json.dumps(a) + '\n')
    work = tmp_path / 'work'
    pipeline.prepare(input_path, ROOT / 'benchmarks/dense', work, catalog_path)
    reqs = [r for r in read_jsonl(work / 'requests.jsonl') if r['fact_key'] == fact['fact_key']]
    assert len(reqs) == 2 and reqs[0]['id'] != reqs[1]['id']
    for r in reqs:
        pipeline.approve(work, r['id'], {'decision': 'select', 'candidate_id': r['candidates'][0]['candidate_id'],
            'relation': 'equivalent', 'rationale': 'Test fixture only'}, 'test-reviewer')
    report = pipeline.export(work, tmp_path / 'export')
    assert report['codings_attached'] == 2
    bundle_path = next(row['bundle'] for row in report['patients'] if row['patient_id'] == p['patient_id'])
    bundle = json.loads((tmp_path / 'export' / bundle_path).read_text())
    coded = [e['resource'] for e in bundle['entry'] if e['resource'].get('code', {}).get('coding')]
    assert len(coded) == 1 and len(coded[0]['code']['coding']) == 2


async def test_local_coding_preserves_returned_reasoning_without_self_approval(specimen, tmp_path, monkeypatch):
    import httpx
    from openpatients2 import beam_recipe as beam
    from openpatients2.pilot_extract import PilotConfig
    a, p, s = specimen
    fact = next(e for e in fact_entries(a, p) if e['task'] == 'conditions')
    catalog = {'format': 'openpatients2-terminology-catalog/1', 'source': 'test fixture', 'license': 'test-only',
        'concepts': [{'system': 'http://hl7.org/fhir/sid/icd-10-cm', 'version': 'fixture',
                     'code': 'TEST', 'display': fact['fact']['name'], 'tasks': ['conditions']}]}
    catalog_path = tmp_path / 'catalog.json'; catalog_path.write_text(json.dumps(catalog))
    input_path = tmp_path / 'input.jsonl'; input_path.write_text(json.dumps(a) + '\n')
    work = tmp_path / 'work'
    pipeline.prepare(input_path, ROOT / 'benchmarks/dense', work, catalog_path)
    cfg = PilotConfig(model_id=beam.NVFP4, revision=beam.PINS[beam.NVFP4]['revision'],
        served_model=beam.NVFP4, tokenizer_contract=beam.TOKENIZER_CONTRACT, temperature=.7,
        top_p=.99, top_k=None, stop_token_ids=beam.STOP_TOKEN_IDS, reasoning_strength='max',
        max_figures=0, max_output_tokens=512).model_dump()
    manifest = tmp_path / 'arm.json'; manifest.write_text(json.dumps({'extraction': cfg, 'context': 131072}))
    actual = httpx.AsyncClient
    def response(request):
        body = json.loads(request.content)
        if request.url.path == '/tokenize':
            return httpx.Response(200, json={'count': 12, 'tokens': [1]*12, 'max_model_len': 131072})
        assert body['chat_template_kwargs']['reasoning_effort'] == 'max'
        assert body['stop_token_ids'] == beam.STOP_TOKEN_IDS
        result = {'decision': 'abstain', 'candidate_id': None, 'relation': None, 'rationale': 'Insufficient detail'}
        events = [{'choices': [{'index': 0, 'delta': {'reasoning_content': 'Endpoint-returned trace'}}]},
                  {'choices': [{'index': 0, 'delta': {'content': json.dumps(result)}, 'finish_reason': 'stop'}],
                   'usage': {'prompt_tokens': 12, 'completion_tokens': 40}}]
        return httpx.Response(200, text=''.join('data: '+json.dumps(e)+'\n\n' for e in events)+'data: [DONE]\n\n')
    monkeypatch.setattr(httpx, 'AsyncClient', lambda **kw: actual(transport=httpx.MockTransport(response), **kw))
    report = await pipeline.select(work, 'http://localhost:8000/v1', manifest, max_requests=1)
    assert report['statuses'] == {'valid': 1}
    decision = json.loads(next((work / 'decisions').glob('*.json')).read_text())
    assert decision['review_status'] == 'model_unreviewed'
    attempts = list(read_jsonl(Path(report['call_directory']) / 'attempts.jsonl'))
    assert attempts[0]['response']['reasoning_text'] == 'Endpoint-returned trace'
