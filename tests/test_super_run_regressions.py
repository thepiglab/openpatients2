"""Regressions from the archived 2026-10-16 Super hardware smoke."""
import asyncio
import copy
import importlib.util
import json
from pathlib import Path
import re
from types import SimpleNamespace

import httpx
import pytest

from openpatients2 import dense_benchmark as b, dense_nemotron_super as s
from openpatients2 import dense_benchmark_serving as serving, redhat_nemotron_recipe as redhat
from test_dense_nemotron_super import campaign
from test_dense_benchmark import finish
from test_pilot_extract import article, input_file, run_pilot, setup_mock
from openpatients2.client import Completion


def wrapper_module():
    spec = importlib.util.spec_from_file_location('rust_wrapper',
        Path(__file__).resolve().parents[1] / 'scripts/vllm_rust_frontend.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_rust_timeout_preserves_bootstrap_and_execs_original_binary(monkeypatch):
    import sys
    module = wrapper_module()
    payload = {'model_tag': '/model', 'reasoning_parser': 'muse', 'engine_ready_timeout_secs': 600}
    argv = ['frontend', '--listen-fd', '19', '--args-json', json.dumps(payload)]
    monkeypatch.setattr(sys, 'argv', ['wrapper', *argv])
    monkeypatch.setenv('VLLM_RUST_FRONTEND_PATH', '/wrapper')
    monkeypatch.setenv('VLLM_ENGINE_READY_TIMEOUT_S', '5400')
    class Env:
        @property
        def VLLM_RUST_FRONTEND_PATH(self):
            assert 'VLLM_RUST_FRONTEND_PATH' not in module.os.environ
            return '/image/vllm-rs'
    monkeypatch.setitem(sys.modules, 'vllm', SimpleNamespace(envs=Env()))
    calls = []
    monkeypatch.setattr(module.os, 'execv', lambda binary, args: calls.append((binary, args)))
    module.main()
    binary, args = calls[0]
    assert binary == args[0] == '/image/vllm-rs'
    assert args[1:4] == ['frontend', '--listen-fd', '19']
    assert json.loads(args[-1]) == {**payload, 'engine_ready_timeout_secs': 5400}
    assert payload['engine_ready_timeout_secs'] == 600
    with pytest.raises(ValueError):
        module.bootstrap_args(['frontend'], 5400)


def test_redhat_alias_preserves_every_original_quantization_setting():
    fixture = Path(__file__).parent / 'fixtures/redhat_nemotron_fp8_config.json'
    assert b.sha(fixture) == redhat.PINS[redhat.FP8]['config_sha256']
    source = b.read(fixture); original = copy.deepcopy(source)
    quant = redhat.quantization_overrides(source)['quantization_config']
    assert source == original
    original_quant = source['quantization_config']
    assert {**quant, 'ignore': quant['ignore'][:-1]} == original_quant
    old_patterns = [p.removeprefix('re:') for p in original_quant['ignore']]
    prefix = 'language_model.model.embed_tokens'
    assert not any(re.search(p, prefix) for p in old_patterns)
    assert re.search(quant['ignore'][-1].removeprefix('re:'), prefix)
    assert not re.search(quant['ignore'][-1].removeprefix('re:'), 'language_model.model.layers.0.mixer.q_proj')
    with pytest.raises(ValueError, match='exclusion'):
        redhat.quantization_overrides({'quantization_config': {'quant_method': 'other'}})


@pytest.mark.parametrize('ready_after,timeout', [(1900, 5400), (6000, 5400)])
def test_server_waits_past_old_limit_and_records_timeout(campaign, tmp_path, monkeypatch, ready_after, timeout):
    work, _, _ = campaign
    config = s.serving(work, 'fastino-text')
    assert config.startup_timeout_seconds == timeout
    assert config.environment['VLLM_ENGINE_READY_TIMEOUT_S'] == str(timeout)
    group = serving.ServerGroup(config, str(work), str(tmp_path / 'logs'))
    monkeypatch.setattr(group, 'preflight', lambda: {})
    clock = [0.0]
    monkeypatch.setattr(serving.time, 'monotonic', lambda: clock[0])
    async def advance(_): clock[0] += 100
    monkeypatch.setattr(serving.asyncio, 'sleep', advance)
    monkeypatch.setattr(serving.subprocess, 'Popen', lambda *a, **kw: SimpleNamespace(poll=lambda: None))
    stopped = []
    def stop():
        stopped.append(True)
        for f in group.files: f.close()
    monkeypatch.setattr(group, 'stop', stop)
    real = httpx.AsyncClient
    monkeypatch.setattr(serving.httpx, 'AsyncClient', lambda **kw: real(
        transport=httpx.MockTransport(lambda req: httpx.Response(200 if clock[0] >= ready_after else 503)), **kw))
    if ready_after < timeout:
        assert asyncio.run(group.start())
        assert b.read(group.logs / 'startup.json')['startup_seconds'] >= ready_after
        stop()
    else:
        with pytest.raises(TimeoutError, match='5400s'):
            asyncio.run(group.start())
        assert stopped
        assert b.read(group.logs / 'startup.json')['status'] == 'timeout'


async def test_length_retry_expands_budget_without_truncating_source(tmp_path, monkeypatch):
    value = article()
    def reply(task, messages):
        return Completion(content='', finish_reason='length', prompt_tokens=10,
                          completion_tokens=256, reasoning_tokens=256)
    http, events = setup_mock(monkeypatch, value, server_max=8192, responses=reply)
    async with http:
        await run_pilot({'max_repair_rounds': 1, 'max_length_retry_tokens': 16384,
                         'require_full_output_budget': False}, input_file(tmp_path, [value]),
            tmp_path / 'run', ['http://localhost:8000/v1'], 8192, 'targeted', http=http)
    calls = [e for e in events if e[0] == 'complete']
    assert [e[3] for e in calls] == [256, 8192 - 10 - 64]
    assert calls[0][2] == calls[1][2][:len(calls[0][2])]
    entries = [json.loads(line) for line in (tmp_path / 'run/attempts.jsonl').read_text().splitlines()]
    assert entries[1]['desired_output_tokens'] == 16384 and entries[1]['output_cap_reduced']
    assert b.ledger_metrics([tmp_path / 'run/attempts.jsonl'])['finish_reasons'] == {'length': 2}


def test_report_separates_finished_attempts_from_valid_extractions(campaign):
    work, refs, _ = campaign
    for name in s.check(work)['nemotron_super']['arms']:
        for ref in refs:
            path = finish(work / 'arms' / name, ref)
            receipt = b.read(path / 'receipt.json')
            receipt.update(all_extraction_tasks_valid=False, task_statuses={'failed': 1},
                           finish_reasons={'length': 3})
            b.write(path / 'receipt.json', receipt)
    result = s.aggregate(work)
    assert result['complete'] and not result['all_extractions_valid']
    assert result['arms']['nvfp4-text']['report']['length_limited_calls'] == 6
    report = (work / 'summary/comparison.md').read_text()
    assert 'Pipeline validity' in report and '| nvfp4-text | 0 / 2 | 2 | 0 | 0 | 6 |' in report
