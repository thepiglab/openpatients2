import json
from pathlib import Path

import httpx
import pytest

from openpatients2 import dense_ablation as a, dense_overnight as o, dense_benchmark as b
from openpatients2.pilot_extract import PilotConfig, PilotRunner
from test_dense_overnight import prepared
from test_dense_benchmark import finish
from test_pilot_extract import article, setup_mock, input_file


def test_selection_is_stable_includes_regressions_and_excludes_test():
    metadata = [{'article_id': f'PMC{i}.1', 'split': 'train'} for i in range(158)]
    metadata += [{'article_id': aid, 'split': 'train'} for aid in a.DIAGNOSTICS]
    metadata += [{'article_id': f'PMC{1000+i}.1', 'split': 'validation'} for i in range(59)]
    metadata += [{'article_id': 'PMC9999.1', 'split': 'test'}]
    selected = a.select_articles(metadata)
    assert selected == a.select_articles(list(reversed(metadata)))
    assert len(selected) == len(set(selected)) == 83
    assert set(a.DIAGNOSTICS) <= set(selected)
    assert 'PMC9999.1' not in selected
    assert sum(row['split'] == 'validation' for row in metadata if row['article_id'] in selected) == 59


def test_component_configs_keep_old_bundle_reproducible():
    legacy = PilotConfig(overnight_repairs=True)
    assert all(legacy.repair_enabled(c) for c in ('figures', 'series', 'roster', 'summary_text', 'malformed_json'))
    assert not legacy.summary_structured_output
    for name, config in a.ARM_CONFIGS.items():
        parsed = PilotConfig(**config)
        assert not parsed.overnight_repairs
        assert not parsed.repair_enabled('summary_text') and not parsed.repair_enabled('malformed_json')
        assert sum(parsed.repair_enabled(c) for c in ('figures', 'series', 'roster')) == (name in {'figures', 'series', 'roster'})
        assert parsed.summary_structured_output == (name == 'summary_json')


def test_ablation_prepare_score_partial_verify_and_multiarm_queue(tmp_path, monkeypatch):
    prepared(tmp_path, monkeypatch)
    root = tmp_path / 'repo'
    (root / 'docs/DENSE_ABLATION.md').write_text('fixture')
    work = tmp_path / 'ablation'
    o.prepare(root, work, expected=3, plan='ablation')
    assert o.arm_names(work) == tuple(a.ARM_CONFIGS)
    m = b.check_snapshot(work)
    assert m['splits'] == {'train': 1, 'validation': 1}
    assert m['overnight']['promotion_gate'] == a.GATES
    jobs = list(b.paired_jobs(work, [work / 'arms' / n for n in a.ARM_CONFIGS], 0))
    assert len(jobs) == 10
    assert len({arm.name for arm, _ in jobs[:5]}) == 5
    assert len({item['article_id'] for _, item in jobs[:5]}) == 1
    refs = b.rows(work / 'reference/unified.jsonl')
    for name in a.ARM_CONFIGS:
        cfg = b.read(work / 'arms' / name / 'manifest.json')['extraction']
        for k, v in a.ARM_CONFIGS[name].items():
            assert cfg[k] == v
        for ref in refs.values():
            if name == 'summary_json' and ref['split'] == 'validation': continue
            folder = finish(work / 'arms' / name, ref)
            receipt = b.read(folder / 'receipt.json'); receipt['patients_extracted'] = len(ref['patients'])
            b.write(folder / 'receipt.json', receipt)
    result = o.aggregate(work)
    assert not result['complete']
    assert all(d['recommendation'] == 'inconclusive' for d in result['component_decisions'].values())
    assert result['completed_control_pairs']['summary_json'] == 1
    with pytest.raises(ValueError, match='lack a completed attempt'): o.verify(work)
    ref = next(r for r in refs.values() if r['split'] == 'validation')
    folder = finish(work / 'arms/summary_json', ref)
    receipt = b.read(folder / 'receipt.json'); receipt['patients_extracted'] = len(ref['patients'])
    b.write(folder / 'receipt.json', receipt)
    result = o.aggregate(work)
    assert result['complete'] and result['completed_pairs'] == 2
    assert all(d['recommendation'] != 'promising_component' for d in result['component_decisions'].values())
    assert o.verify(work)['verified']
    assert o.status(work)['arms']['summary_json']['pending'] == 0
    assert (work / 'summary/per-article-series.json').exists()
    # Re-aggregation must not hash the previous root integrity file into itself.
    o.aggregate(work)
    assert o.verify(work)['verified']


async def test_summary_schema_request_is_task_local_and_never_falls_back(tmp_path):
    requests = []
    def handler(request):
        body = json.loads(request.content)
        if request.url.path == '/tokenize':
            return httpx.Response(200, json={'count': 10, 'tokens': [1]*10, 'max_model_len': 65536})
        requests.append(body)
        return httpx.Response(400, json={'error': 'unsupported structured output'})
    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    runner = PilotRunner({'summary_structured_output': True}, tmp_path / 'runner',
                         ['http://localhost:8000/v1'], 65536, 'targeted', http=http)
    try:
        client = runner.clients[0]
        messages = [{'role': 'user', 'content': 'A source-grounded summary.'}]
        assert 'structured_outputs' not in client.body('observations', messages, 1000)
        body = client.body('summary', messages, 1000)
        assert body['structured_outputs']['json'] == runner.schemas['summary']
        result = await runner.call('summary', messages, lambda v: v, {'article_id': 'PMC1.1'}, 0, [])
        assert result['status'] == 'failed' and result['errors'] == ['http_400']
        assert len(requests) == 1 and 'structured_outputs' in requests[0]
        assert result['attempts'][0]['response_format'] == 'vllm_structured_outputs'
    finally:
        await runner.close(); await http.aclose()


async def test_series_retry_allowlist_does_not_repair_unrelated_ehr_tasks(tmp_path, monkeypatch):
    value = article()
    http, events = setup_mock(monkeypatch, value, responses=lambda *args: {})
    runner = PilotRunner({'repair_components': ['series'], 'task_repair_allowlist': ['measurement_series_extraction'],
        'max_repair_rounds': 2, 'max_output_tokens': 256, 'max_retry_tokens': 256},
        tmp_path / 'runner', ['http://localhost:8000/v1'], 8192, 'targeted', http=http)
    from openpatients2 import ehr_tasks
    runner.schemas.update(ehr_tasks.schemas())
    def invalid(value): raise ValueError('invalid ownership')
    try:
        for task, count in [('figure_ehr_label', 1), ('measurement_series_extraction', 3)]:
            result = await runner.call(task, [{'role': 'user', 'content': 'source'}], invalid,
                                       {'article_id': value['article_id']}, 0, value['segments'])
            assert len(result['attempts']) == count
    finally:
        await runner.close(); await http.aclose()


async def test_roster_audit_has_one_task_file_with_actual_prepared_prompt(tmp_path, monkeypatch):
    value = article(); http, _ = setup_mock(monkeypatch, value)
    runner = PilotRunner({'repair_components': ['roster'], 'isolate_secondary_cases': True,
                         'max_output_tokens': 256, 'max_retry_tokens': 256},
                        tmp_path / 'runner', ['http://localhost:8000/v1'], 8192, 'targeted', http=http)
    try:
        await runner.run(input_file(tmp_path, [value]), discovery_only=True)
        files = list((runner.output / 'tasks').glob('*.json'))
        assert len(files) == 1
        result = json.loads(files[0].read_text())
        assert result['task'] == 'roster' and 'secondary_case_quarantine' in result
        assert 'table footnotes' in str(result['attempts'][0]['request'])
    finally:
        await runner.close(); await http.aclose()


def test_component_gate_rewards_own_target_but_enforces_clinical_guards():
    control = {'series_points_f1': .4, 'clinical_f1': .73, 'clinical_precision': .85,
               'attribute_accuracy': .83, 'roster_recall': .95}
    improved = {**control, 'series_points_f1': .6}
    assert a.decision(control, improved, 'series_points_f1', True)['recommendation'] == 'promising_component'
    assert a.decision(control, {**improved, 'clinical_f1': .7}, 'series_points_f1', True)['recommendation'] != 'promising_component'
    assert a.decision(control, improved, 'series_points_f1', False)['recommendation'] == 'inconclusive'


async def test_structured_summary_still_rejects_nonliteral_evidence(tmp_path):
    from openpatients2.article_tasks import check_article_task
    from test_pilot_extract import roster
    source = article()
    bad = {'claims': [{'text': 'Invented finding', 'evidence': [
        {'segment_id': source['segments'][0]['segment_id'], 'quote': 'Invented finding'}]}], 'limitations': []}
    def handler(request):
        if request.url.path == '/tokenize':
            return httpx.Response(200, json={'count': 10, 'tokens': [1]*10, 'max_model_len': 65536})
        assert 'structured_outputs' in json.loads(request.content)
        event = {'choices': [{'index': 0, 'delta': {'content': json.dumps(bad)}, 'finish_reason': 'stop'}]}
        return httpx.Response(200, text='data: ' + json.dumps(event) + '\n\ndata: [DONE]\n\n',
                              headers={'content-type': 'text/event-stream'})
    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    runner = PilotRunner({'summary_structured_output': True, 'max_repair_rounds': 0}, tmp_path / 'runner',
                         ['http://localhost:8000/v1'], 65536, 'targeted', http=http)
    try:
        result = await runner.call('summary', [{'role': 'user', 'content': 'Source.'}],
            lambda value: check_article_task('summary', value, source, roster(source)['patients'][0]),
            {'article_id': source['article_id']}, 0, source['segments'])
        assert result['status'] == 'failed'
        assert any('nonliteral' in e for e in result['errors'])
        assert result['attempts'][0]['raw_candidate'] == bad
    finally:
        await runner.close(); await http.aclose()
