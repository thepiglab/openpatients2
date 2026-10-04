"""Mock-only tests of executable pilot guards and source-backed workflows."""
import copy
import asyncio
import hashlib
import json

import httpx
import pytest

from openpatients2.articles import parse_article
from openpatients2.client import APIClient, Completion
from openpatients2.pilot_extract import (PilotConfig, PilotRunner, load_pilot_config,
                                         run_pilot as real_run_pilot, source_gate)
from openpatients2.schemas import TASK_MODELS


XML = '''<article xmlns:xlink="http://www.w3.org/1999/xlink" article-type="case-report">
<front><article-meta><article-id pub-id-type="pmc">1</article-id><title-group><article-title>Authored pilot fixture</article-title></title-group>
<permissions><license xlink:href="https://creativecommons.org/licenses/by/4.0/"/></permissions></article-meta></front>
<body><sec><title>Case</title><p>A 42-year-old woman received 5 mg.</p></sec>
<fig id="F1"><caption><p>The woman's radiograph.</p></caption><graphic xlink:href="fig1"/></fig></body></article>'''


async def run_pilot(config, *args, **kwargs):
    # Small mock output allowances; production defaults retain the full 16K.
    return await real_run_pilot({'max_output_tokens': 256, 'max_retry_tokens': 256, **config}, *args, **kwargs)


def article(xml=XML):
    value = parse_article(xml, {'pmcid': 'PMC1', 'version': 1, 'license_code': 'CC BY',
        'is_pmc_openaccess': 'yes', 'is_retracted': 'no',
        'media_urls': ['https://pmc-oa-opendata.s3.amazonaws.com/PMC1.1/fig1.jpg']})
    value['status'] = 'eligible'
    return value


def roster(value):
    sid = value['segments'][0]['segment_id']
    return {'disposition': 'individual_cases', 'reported_individual_count': 1, 'roster_complete': True,
        'patients': [{'patient_id': 'p1', 'label': 'woman', 'species': 'human',
            'species_as_documented': 'woman', 'identity_evidence': [{'segment_id': sid, 'quote': 'A 42-year-old woman'}],
            'source_segment_ids': [s['segment_id'] for s in value['segments']], 'attribution_limitations': []}],
        'background_segment_ids': [], 'unresolved_segment_ids': [], 'limitations': [],
        'figures': [{'figure_id': 'F1', 'panel': None, 'patient_ids': [], 'scope': 'unresolved', 'evidence': []}]}


def empty_section(task):
    value = {'coverage': 'limited', 'limitations': ['Authored test fixture; no completeness claim'],
             'documentation_status': 'not_documented', 'documentation_evidence': []}
    for key in ('items', 'tumors', 'biomarkers', 'treatments'):
        if key in TASK_MODELS[task].model_fields:
            value[key] = []
    if task == 'case_context':
        value.update(case_kind='unknown', index_subject_description=None, species='unknown',
            multiple_index_patients=False, subject_state='unknown', care_setting=None,
            chief_complaint=None, evidence=[])
    return value


def answer(task, value):
    if task == 'roster':
        return roster(value)
    if task in TASK_MODELS:
        return empty_section(task)
    if task in {'figure_attribution', 'pixel_attribution'}:
        return {'figure_id': 'F1', 'assignments': [{'panel': None, 'patient_ids': [],
            'scope': 'unresolved', 'subject': 'unresolved', 'evidence': [], 'rationale': 'Needs source review'}], 'limitations': []}
    if task == 'summary':
        return {'claims': [{'text': 'The woman received a documented dose.', 'evidence': [
            {'segment_id': value['segments'][0]['segment_id'], 'quote': 'A 42-year-old woman received 5 mg.'}]}], 'limitations': []}
    if task == 'timeline_v2':
        return {'schema_version': 'patient-timeline/2', 'record_id': 'PMC1.1:p1', 'events': [], 'edges': [], 'limitations': ['Timing not inferred']}
    if task == 'figure_visuals':
        return {'figure_id': 'F1', 'general_description': 'Authored image fixture', 'detailed_description': 'A test image.',
            'panels': [{'panel': None, 'general_description': 'Image', 'detailed_description': 'Image fixture',
                'image_kind': 'unknown', 'has_chart': False, 'charts': [], 'imaging_modalities': [],
                'imaging_submodalities': [], 'medical_domains': [], 'body_parts': [], 'mentioned_categories': [],
                'pixel_observations': ['Fixture pixels were presented'], 'caption_claims': [],
                'clinical_significance': [], 'limitations': ['Mock output, not medical evidence']}], 'limitations': []}
    raise AssertionError(task)


def setup_mock(monkeypatch, value, *, count=10, server_max=8192, responses=None, usage=True, malformed=False, seed=42):
    events = []
    def tokenize(request):
        assert request.url.path == '/tokenize'
        body = json.loads(request.content)
        assert body['add_generation_prompt'] is True and body['add_special_tokens'] is False
        assert body['chat_template_kwargs'] == {'reasoning_strength': 'medium'}
        events.append(('tokenize', body))
        return httpx.Response(200, json={'count': count, 'tokens': [1] * (count - 1 if malformed else count), 'max_model_len': server_max})
    async def complete(client, endpoint, task, messages, cap):
        assert events[-1][0] == 'tokenize'
        body = client.body(task, messages, cap)
        assert 'response_format' not in body and 'structured_outputs' not in body
        assert (body['temperature'], body['top_p'], body['top_k'], body['seed']) == (1, .95, 64, seed)
        assert body['chat_template_kwargs'] == {'reasoning_strength': 'medium'}
        assert count + cap + 64 <= server_max
        events.append(('complete', task, messages, cap))
        reply = responses(task, messages) if responses else answer(task, value)
        if isinstance(reply, Completion):
            return reply
        return Completion(content=json.dumps(reply), finish_reason='stop',
            prompt_tokens=10 if usage else None, completion_tokens=20 if usage else None,
            reasoning_tokens=3 if usage else None, latency_seconds=.01)
    monkeypatch.setattr(APIClient, 'complete_once', complete)
    return httpx.AsyncClient(transport=httpx.MockTransport(tokenize)), events


def input_file(tmp_path, articles):
    path = tmp_path / 'sample.jsonl'
    path.write_text(''.join(json.dumps(a) + '\n' for a in articles))
    return path


async def test_full_pilot_is_executable_retains_sources_and_never_claims_accuracy(tmp_path, monkeypatch):
    value = article(); http, events = setup_mock(monkeypatch, value)
    async with http:
        report = await run_pilot({'max_articles': 1, 'max_output_tokens': 256, 'max_retry_tokens': 256},
            input_file(tmp_path, [value]), tmp_path / 'direct', ['http://127.0.0.1:8000/v1'], 4096, 'direct', http=http)
    requested = [e[1] for e in events if e[0] == 'complete']
    assert set(TASK_MODELS) | {'roster', 'figure_attribution', 'summary', 'timeline_v2'} == set(requested)
    assert report['patients_extracted'] == 1 and report['valid_tasks'] == len(requested)
    patient = json.loads((tmp_path / 'direct' / 'patients.jsonl').read_text())
    assert patient['source']['article_source']['xml_sha256'] == value['xml_sha256']
    assert patient['source']['article_source']['license'] == value['license']
    assert patient['source']['article_source']['figures'] == value['figures']
    assert patient['clinical_accuracy_verified'] is False
    assert patient['coverage_inventory']['semantic_coverage_verified'] is False
    assert patient['timeline_audit']['clinical_timeline_verified'] is False
    assert report['tokens']['total_tokens'] == len(requested) * 30
    assert report['tokens']['reasoning_tokens'] == len(requested) * 3
    assert report['tokens']['all_gpus_output_tokens_per_second'] > 0
    attempts = [json.loads(line) for line in (tmp_path / 'direct' / 'attempts.jsonl').read_text().splitlines()]
    assert all(a['response']['content'] and a['tokenized_prompt_tokens'] == 10 for a in attempts)
    review = json.loads(next((tmp_path / 'direct' / 'review').glob('*.json')).read_text())
    assert any(row['evidence_spans'] and row['source_xml_sha256'] == value['xml_sha256'] for row in review)


@pytest.mark.parametrize('mutation', ['license', 'status', 'hash', 'body', 'segments'])
async def test_rejected_or_incomplete_sources_do_not_reach_model(tmp_path, monkeypatch, mutation):
    value = article()
    if mutation == 'license':
        value['license']['statements'] = [{'type': 'license', 'text': 'No Derivatives'}]
    elif mutation == 'status':
        value['status'] = 'license_review'
    elif mutation == 'hash':
        value['text_sha256'] = '0' * 64
    elif mutation == 'body':
        value['has_body_text'] = False
    else:
        value['segments'][0]['text'] += ' forged'
    http, events = setup_mock(monkeypatch, value)
    async with http:
        report = await run_pilot({}, input_file(tmp_path, [value]), tmp_path / 'run',
            'http://localhost:8000/v1', 4096, 'targeted', http=http)
    assert events == [] and report['model_calls'] == 0 and report['patients_extracted'] == 0
    assert report['articles'][0]['status'] == 'source_rejected'


async def test_exact_context_guard_caps_output_and_records_overflow(tmp_path, monkeypatch):
    value = article(); http, events = setup_mock(monkeypatch, value, count=800, server_max=1000)
    async with http:
        runner = PilotRunner({'max_output_tokens': 256, 'require_full_output_budget': False}, tmp_path / 'fit', ['http://localhost:8000/v1'], 4096, 'direct', http=http)
        try:
            result = await runner.call('roster', task_messages_fixture(), lambda data: data,
                {'article_id': 'PMC1.1'}, 0, value['segments'])
            assert result['status'] == 'valid'
            assert result['attempts'][0]['max_tokens'] == 136
            assert result['attempts'][0]['output_cap_reduced'] is True
            assert result['attempts'][0]['effective_context'] == 1000
        finally:
            await runner.close()
    http, events = setup_mock(monkeypatch, value, count=900, server_max=1000)
    async with http:
        report = await run_pilot({}, input_file(tmp_path, [value]), tmp_path / 'overflow',
            ['http://localhost:8000/v1'], 4096, 'targeted', http=http)
    assert report['model_calls'] == 0
    assert 'context_overflow_no_source_truncation' in report['articles'][0]['roster']['errors'][0]
    assert len(events) == 1


def task_messages_fixture():
    return [{'role': 'user', 'content': 'Full immutable mock source'}]


async def test_bad_tokenizer_and_total_call_limits_fail_closed(tmp_path, monkeypatch):
    value = article(); http, events = setup_mock(monkeypatch, value, malformed=True)
    async with http:
        report = await run_pilot({}, input_file(tmp_path, [value]), tmp_path / 'bad',
            ['http://localhost:8000/v1'], 4096, 'direct', http=http)
    assert report['model_calls'] == 0
    assert 'exact_count' in report['articles'][0]['roster']['errors'][0]
    http, events = setup_mock(monkeypatch, value)
    async with http:
        report = await run_pilot({'max_calls': 1}, input_file(tmp_path, [value]), tmp_path / 'cap',
            ['http://localhost:8000/v1'], 4096, 'direct', http=http)
    assert report['model_calls'] == 1 and report['patients_extracted'] == 1
    patient = json.loads((tmp_path / 'cap' / 'patients.jsonl').read_text())
    assert not patient['complete_for_scope'] and all(v is None for v in patient['sections'].values())


async def test_incomplete_outputs_and_transport_retries_are_finite(tmp_path, monkeypatch):
    value = article()
    def reply(task, messages):
        return Completion(content='{"patients": []}', finish_reason='length', prompt_tokens=10, completion_tokens=20)
    http, events = setup_mock(monkeypatch, value, responses=reply)
    async with http:
        report = await run_pilot({'max_repair_rounds': 1}, input_file(tmp_path, [value]), tmp_path / 'incomplete',
            ['http://localhost:8000/v1'], 4096, 'targeted', http=http)
    assert report['model_calls'] == 2 and report['patients_extracted'] == 0
    assert report['articles'][0]['roster']['status'] == 'failed'
    def timeout(task, messages):
        return Completion(error='TimeoutError')
    http, events = setup_mock(monkeypatch, value, responses=timeout)
    async with http:
        report = await run_pilot({'transport_retries': 1}, input_file(tmp_path, [value]), tmp_path / 'timeouts',
            ['http://localhost:8000/v1'], 4096, 'targeted', http=http)
    assert report['model_calls'] == 2
    assert report['tokens']['total_tokens'] is None and report['tokens']['all_gpus_output_tokens_per_second'] is None


def demographics(value):
    sid = value['segments'][0]['segment_id']
    age = {'subject': 'index_patient', 'assertion': 'present', 'temporality': 'current',
        'time': {'text': None, 'relation': 'unknown', 'anchor': None, 'date_iso': None},
        'evidence': [{'quote': 'A 42-year-old woman', 'source_section': sid}],
        'attribute': 'age_at_presentation', 'text_value': '42-year-old', 'numeric_value': 42.0, 'unit': 'years'}
    sex = {**copy.deepcopy(age), 'attribute': 'documented_sex', 'text_value': 'woman',
           'numeric_value': None, 'unit': None,
           'evidence': [{'quote': 'fabricated wording', 'source_section': sid}]}
    return {'coverage': 'limited', 'limitations': ['Authored fixture'], 'documentation_status': 'documented',
            'documentation_evidence': [], 'items': [age, sex]}


@pytest.mark.parametrize('fix', [True, False])
async def test_item_repair_freezes_supported_neighbors_and_partial_is_not_success(tmp_path, monkeypatch, fix):
    value = article(); initial = demographics(value); seen = 0
    def reply(task, messages):
        nonlocal seen
        if task != 'demographics':
            return answer(task, value)
        seen += 1
        if seen == 1:
            return initial
        assert 'TARGETED ITEM REPAIR' in messages[-1]['content']
        assert 'Previously accepted facts are frozen' in messages[-1]['content']
        repaired = copy.deepcopy(initial)
        repaired['items'] = [repaired['items'][1]]
        if fix:
            repaired['items'][0]['evidence'][0]['quote'] = 'woman'
        return repaired
    http, events = setup_mock(monkeypatch, value, responses=reply)
    async with http:
        report = await run_pilot({'max_repair_rounds': 1}, input_file(tmp_path, [value]), tmp_path / 'targeted',
            ['http://localhost:8000/v1'], 8192, 'targeted', http=http)
    patient = json.loads((tmp_path / 'targeted' / 'patients.jsonl').read_text())
    assert seen == 2
    assert patient['sections']['demographics']['items'][0] == initial['items'][0]
    assert patient['quality']['demographics']['status'] == ('valid' if fix else 'partial')
    assert patient['complete_for_scope'] is fix
    if not fix:
        assert len(patient['sections']['demographics']['items']) == 1
        assert report['task_statuses']['partial'] == 1
    attempts = [json.loads(line) for line in (tmp_path / 'targeted' / 'attempts.jsonl').read_text().splitlines()]
    assert any(a.get('item_repair', {}).get('initially_accepted_items') == 1 for a in attempts)
    assert any('fabricated wording' in a['response']['content'] for a in attempts)
    envelopes = patient['fact_review']['envelopes']
    age = next(row for row in envelopes if row['fact']['value'].get('attribute') == 'age_at_presentation')
    pointers = {support['pointer'] for support in age['fact']['field_support']}
    assert {'/numeric_value', '/text_value'} <= pointers
    assert '/assertion' not in pointers and '/subject' not in pointers
    assert age['gate_report']['clinical_entailment_verified'] is False
    assert any(issue['pointer'] == '/subject' for issue in age['gate_report']['review_issues'])
    assert age['gate_report']['field_provenance_gates_passed'] is False


async def test_direct_arm_does_not_repair_and_first_prompts_match_targeted(tmp_path, monkeypatch):
    value = article()
    def reply(task, messages):
        return demographics(value) if task == 'demographics' else answer(task, value)
    http, events = setup_mock(monkeypatch, value, responses=reply)
    path = input_file(tmp_path, [value])
    async with http:
        direct = await run_pilot({}, path, tmp_path / 'direct', ['http://localhost:8000/v1'], 8192, 'direct', http=http)
        direct_prompts = {e[1]: e[2] for e in events if e[0] == 'complete'}
        events.clear()
        targeted = await run_pilot({}, path, tmp_path / 'targeted', ['http://localhost:8000/v1'], 8192, 'targeted', http=http)
    assert targeted['model_calls'] == direct['model_calls'] + 1
    first = {}
    for event in events:
        if event[0] == 'complete':
            first.setdefault(event[1], event[2])
    # Clinical/summary/discovery first prompts are identical. The graph receives
    # this arm's accepted fact IDs, so its derived registry intentionally differs.
    assert {k: v for k, v in first.items() if k != 'timeline_v2'} == {k: v for k, v in direct_prompts.items() if k != 'timeline_v2'}


async def test_pixels_use_prepared_hash_checked_files_and_expanded_tokenize(tmp_path, monkeypatch):
    value = article(); body = b'\xff\xd8\xffauthored mock JPEG bytes'
    digest = hashlib.sha256(body).hexdigest()
    (tmp_path / 'vision-assets').mkdir()
    pixel_file = tmp_path / 'vision-assets' / (digest + '.image'); pixel_file.write_bytes(body)
    manifest = {'output_dir': str(tmp_path), 'rows': [{'article_id': 'PMC1.1', 'figure_id': 'F1',
        'status': 'ready', 'file': 'vision-assets/' + digest + '.image', 'source_license': value['license'],
        'pixel_provenance': {'bytes': len(body), 'sha256': digest,
            'mime_type': 'image/jpeg', 'url': value['figures'][0]['image_urls'][0]}}]}
    def reply(task, messages):
        if task in {'figure_attribution', 'pixel_attribution'}:
            caption = value['segments'][-1]
            return {'figure_id': 'F1', 'assignments': [{'panel': None, 'patient_ids': ['p1'],
                'scope': 'individual', 'subject': 'patient', 'evidence': [{'segment_id': caption['segment_id'],
                    'quote': caption['text']}], 'rationale': 'Source case/caption candidate'}], 'limitations': []}
        return answer(task, value)
    http, events = setup_mock(monkeypatch, value, responses=reply)
    async with http:
        report = await run_pilot({}, input_file(tmp_path, [value]), tmp_path / 'vision',
            ['http://localhost:8000/v1'], 8192, 'direct', image_manifest=manifest, http=http)
    assert report['pixel_figures_inspected'] == 1
    assert {'figure_visuals', 'pixel_attribution'} <= {e[1] for e in events if e[0] == 'complete'}
    pixel_tokenize = [e[1] for e in events if e[0] == 'tokenize' and isinstance(e[1]['messages'][1]['content'], list)]
    assert len(pixel_tokenize) == 2 and pixel_tokenize[0]['messages'][1]['content'][1]['image_url']['url'].startswith('data:image/jpeg;base64,')
    visual = json.loads((tmp_path / 'vision' / 'visual-annotations.json').read_text())[0]
    assert visual['pixel_accuracy_verified'] is False and visual['panel_columns'][0]['clinical_fact_status'] == 'unreviewed_visual_annotation'
    patient = json.loads((tmp_path / 'vision' / 'patients.jsonl').read_text())
    figure = patient['vision']['media']['figures'][0]
    assert figure['figure_key'] == 'F1' and figure['visual_description']['general_description']
    assert figure['panel_columns'] and figure['pixel_provenance']['sha256'] == digest
    assert figure['attribution_comparison']['status'] == 'assignments_agree_unreviewed'
    assert figure['usable_as_clinical_fact'] is False
    assert patient['vision']['caption_only_media']['pixels_inspected'] is False
    assert patient['vision']['media']['pixels_inspected'] is True
    assert 'data:image' not in (tmp_path / 'vision' / 'attempts.jsonl').read_text()
    pixel_file.write_bytes(b'changed')
    http, events = setup_mock(monkeypatch, value)
    async with http:
        report = await run_pilot({}, input_file(tmp_path, [value]), tmp_path / 'changed',
            ['http://localhost:8000/v1'], 8192, 'direct', image_manifest=manifest, http=http)
    assert report['pixel_figures_inspected'] == 0
    assert 'figure_visuals' not in {e[1] for e in events if e[0] == 'complete'}


async def test_competing_pixel_and_caption_patients_remain_explicit_candidates(tmp_path, monkeypatch):
    value = article(XML.replace('<fig id=', '<sec><title>Other case</title><p>A 50-year-old man received 2 mg.</p></sec><fig id='))
    body = b'\xff\xd8\xffmock image'; digest = hashlib.sha256(body).hexdigest()
    folder = tmp_path / 'vision-assets'; folder.mkdir(); (folder / (digest + '.image')).write_bytes(body)
    manifest = {'output_dir': str(tmp_path), 'rows': [{'article_id': 'PMC1.1', 'figure_id': 'F1',
        'status': 'ready', 'file': 'vision-assets/' + digest + '.image', 'source_license': value['license'],
        'pixel_provenance': {'bytes': len(body), 'sha256': digest, 'mime_type': 'image/jpeg',
                             'url': value['figures'][0]['image_urls'][0]}}]}
    def reply(task, messages):
        if task == 'roster':
            data = roster(value); data['reported_individual_count'] = 2
            data['patients'][0]['source_segment_ids'] = [value['segments'][0]['segment_id'], value['segments'][-1]['segment_id']]
            data['patients'].append({'patient_id': 'p2', 'label': 'man', 'species': 'human',
                'species_as_documented': 'man', 'identity_evidence': [{'segment_id': value['segments'][1]['segment_id'], 'quote': 'A 50-year-old man'}],
                'source_segment_ids': [value['segments'][1]['segment_id']], 'attribution_limitations': []})
            return data
        if task in {'figure_attribution', 'pixel_attribution'}:
            return {'figure_id': 'F1', 'assignments': [{'panel': None,
                'patient_ids': ['p1' if task == 'figure_attribution' else 'p2'], 'scope': 'individual', 'subject': 'patient',
                'evidence': [{'segment_id': value['segments'][-1]['segment_id'], 'quote': value['segments'][-1]['text']}],
                'rationale': 'Unreviewed model ownership claim'}], 'limitations': []}
        return answer(task, value)
    http, _ = setup_mock(monkeypatch, value, responses=reply)
    async with http:
        await run_pilot({'max_patients': 1}, input_file(tmp_path, [value]), tmp_path / 'conflict',
            ['http://localhost:8000/v1'], 8192, 'direct', image_manifest=manifest, http=http)
    patient = json.loads((tmp_path / 'conflict' / 'patients.jsonl').read_text())
    comparison = patient['vision']['media']['caption_pixel_comparisons'][0]
    assert comparison['status'] == 'competing_patient_assignments'
    assert comparison['caption_patient_assignments'][0]['patient_ids'] == ['p1']
    assert comparison['pixel_patient_assignments'][0]['patient_ids'] == ['p2']
    assert not patient['vision']['media']['figures']
    assert patient['vision']['caption_only_media']['figures']
    assert patient['vision']['media']['article_figure_annotations'][0]['visual_description']
    assert patient['clinical_accuracy_verified'] is False


def test_local_endpoints_pin_settings_and_existing_arm_is_immutable(tmp_path):
    assert load_pilot_config('configs/pilot/glimmer-extraction.yaml').max_articles == 48
    config = load_pilot_config('configs/pilot/glimmer-extraction.yaml')
    assert config.max_output_tokens == config.max_retry_tokens == 16384
    assert config.max_total_tokens == 32_000_000 and config.require_full_output_budget is True
    with pytest.raises(ValueError):
        PilotRunner({}, tmp_path / 'remote', ['https://example.com/v1'], 4096, 'direct')
    assert PilotConfig(reasoning_strength='high').reasoning_strength == 'high'
    with pytest.raises(ValueError):
        PilotConfig(reasoning_strength='unsupported')
    output = tmp_path / 'exists'; output.mkdir(); (output / 'report.json').write_text('{}')
    with pytest.raises(ValueError, match='independent'):
        PilotRunner({}, output, ['http://localhost:8000/v1'], 4096, 'direct')


async def test_parallel_tokenization_cannot_overrun_shared_call_budget(tmp_path, monkeypatch):
    value = article(); sent = []
    async def tokenize(request):
        await asyncio.sleep(0)
        return httpx.Response(200, json={'count': 10, 'tokens': [1] * 10, 'max_model_len': 4096})
    async def complete(client, endpoint, task, messages, cap):
        sent.append(task)
        await asyncio.sleep(0)
        return Completion(content='{}', finish_reason='stop', prompt_tokens=10, completion_tokens=1)
    monkeypatch.setattr(APIClient, 'complete_once', complete)
    async with httpx.AsyncClient(transport=httpx.MockTransport(tokenize)) as http:
        runner = PilotRunner({'max_calls': 1, 'max_output_tokens': 256}, tmp_path / 'parallel', ['http://localhost:8000/v1'], 4096, 'direct', http=http)
        try:
            result = await asyncio.gather(*(runner.call('roster', task_messages_fixture(), lambda data: data,
                {'article_id': 'PMC1.1', 'test_target': str(i)}, 0, value['segments']) for i in range(8)))
            assert len(sent) == runner.calls == 1
            assert sum(row['status'] == 'valid' for row in result) == 1
        finally:
            await runner.close()


@pytest.mark.parametrize('policy', ['legacy', 'source_aware'])
async def test_temporal_source_and_graph_gates_block_delivery(tmp_path, monkeypatch, policy):
    value = article(); segment = value['segments'][0]
    source_span = {'source_id': 'PMC1.1:jats', 'segment_id': segment['segment_id'],
        'segment_sha256': hashlib.sha256(segment['text'].encode()).hexdigest(),
        'start': 0, 'end': len(segment['text']), 'quote': segment['text']}
    def reply(task, messages):
        if task != 'timeline_v2':
            return answer(task, value)
        event = {'record_id': 'PMC1.1:p1', 'episode_id': None, 'kind': 'treatment', 'occurrence': 'occurred',
            'description': 'Documented treatment', 'fact_ids': [], 'evidence': [source_span],
            'attribution_evidence': [source_span], 'times': []}
        return {'schema_version': 'patient-timeline/2', 'record_id': 'PMC1.1:p1',
            'events': [{**event, 'event_id': 'e1'}, {**event, 'event_id': 'e2'}],
            'edges': [{'edge_id': 't1', 'from_event_id': 'e1', 'to_event_id': 'e2', 'relation': 'before',
                       'offset': None, 'evidence': [source_span]},
                      {'edge_id': 't2', 'from_event_id': 'e2', 'to_event_id': 'e1', 'relation': 'before',
                       'offset': None, 'evidence': [source_span]}], 'limitations': []}
    http, _ = setup_mock(monkeypatch, value, responses=reply)
    async with http:
        report = await run_pilot({'refinement_policy': policy}, input_file(tmp_path, [value]), tmp_path / 'cyclic',
            ['http://localhost:8000/v1'], 8192, 'direct', http=http)
    patient = json.loads((tmp_path / 'cyclic' / 'patients.jsonl').read_text())
    if policy == 'legacy':
        assert patient['companions']['timeline_v2'] is None
    else:
        assert len(patient['companions']['timeline_v2']['events']) == 2
        assert patient['companions']['timeline_v2']['edges'] == []
        assert patient['quality']['timeline_v2']['status'] == 'partial'
        assert len(patient['quality']['timeline_v2']['quarantine']) == 2
        assert not patient['complete_for_scope']
    assert 'contradictory_temporal_order' in patient['quality']['timeline_v2']['errors'][0]
    assert not patient['complete_for_scope'] and report['task_statuses']['failed' if policy == 'legacy' else 'partial'] == 1


async def test_full_output_budget_excludes_nonfitting_source_even_above_minimum(tmp_path, monkeypatch):
    value = article(); http, events = setup_mock(monkeypatch, value, count=800, server_max=1000)
    async with http:
        report = await run_pilot({}, input_file(tmp_path, [value]), tmp_path / 'full-budget',
            ['http://localhost:8000/v1'], 4096, 'direct', http=http)
    attempt = report['articles'][0]['roster']['attempts'][0]
    assert report['model_calls'] == 0 and len(events) == 1
    assert attempt['available_output_tokens'] == 136 > 128
    assert attempt['desired_output_tokens'] == 256 and attempt['require_full_output_budget'] is True
    assert attempt['errors'] == ['context_overflow_no_source_truncation']
