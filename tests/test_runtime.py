import copy
import json
from unittest.mock import AsyncMock

import httpx
import pytest

from openpatients2.client import APIClient
from openpatients2.config import APIConfig
from openpatients2.demo import transport, fixture_sections
from openpatients2.pipeline import run_pipeline, endpoint_for
from openpatients2.prompts import messages_for, task_signature
from openpatients2.schemas import TASK_MODELS
from openpatients2.profile import TokenGuard, profile_fingerprint, tokenize_record
from openpatients2.data import write_json, normalize, prepare, records, input_fingerprint
from openpatients2.metrics import summarize


def test_identical_prefix_before_each_task(row):
    prefixes = [messages_for(row, t)[1]["content"].split("\nEXTRACTION_TASK:")[0] for t in TASK_MODELS]
    assert len(set(prefixes)) == 1
    assert row["record_id"] in prefixes[0]
    assert json.dumps(row["text"], ensure_ascii=False) in prefixes[0]


def test_stable_endpoint_affinity():
    endpoints = [f"http://localhost:{8000+i}/v1" for i in range(8)]
    assert endpoint_for("abc", endpoints) == endpoint_for("abc", endpoints)
    assert len({endpoint_for(str(i), endpoints) for i in range(1000)}) == 8


def test_signature_changes_with_checkpoint_and_generation():
    a = task_signature("oncology", {"revision": "A"}, {"temperature": 0})
    assert a != task_signature("oncology", {"revision": "B"}, {"temperature": 0})
    assert a != task_signature("oncology", {"revision": "A"}, {"temperature": 1})


@pytest.mark.asyncio
async def test_pipeline_all_tasks_then_resume_no_http(config):
    calls = []
    async with httpx.AsyncClient(transport=transport(calls=calls)) as http:
        api = APIClient(config.api, http)
        first = await run_pipeline(config, client=api)
        assert first["new_complete_records"] == 1
        assert len(calls) == 14
        assert calls[0]["task"] == "case_context"
        assert first["cached_prompt_tokens"] is None
        second = await run_pipeline(config, client=api)
        assert second["attempts"] == 0
        assert second["fully_resumed_records"] == 1
        assert len(calls) == 14


@pytest.mark.asyncio
async def test_changed_revision_invalidates_resume(config):
    calls = []
    async with httpx.AsyncClient(transport=transport(calls=calls)) as http:
        await run_pipeline(config, client=APIClient(config.api, http))
        config.api.revision = "new-sha"
        result = await run_pipeline(config, client=APIClient(config.api, http))
        assert result["attempts"] == 14
        assert len(calls) == 28


@pytest.mark.asyncio
async def test_http_failure_not_silently_retried_as_prompt(config):
    async def handler(request):
        return httpx.Response(400, json={"error": "unsupported JSON schema"})
    config.tasks = ["case_context"]
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        result = await run_pipeline(config, client=APIClient(config.api, http))
    assert result["failed_records"] == 1
    assert result["attempts"] == 1


@pytest.mark.asyncio
async def test_length_retry_increases_budget(config, sections):
    config.tasks = ["case_context"]
    calls = []
    async def handler(request):
        body = json.loads(request.content)
        calls.append(body)
        finish = "length" if len(calls) == 1 else "stop"
        content = "{" if finish == "length" else json.dumps(sections["case_context"])
        return httpx.Response(200, text='data: '+json.dumps({"choices": [{"delta": {"content": content}, "finish_reason": finish}]})+'\n\ndata: [DONE]\n\n')
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        result = await run_pipeline(config, client=APIClient(config.api, http))
    assert result["new_complete_records"] == 1
    assert calls[1]["max_tokens"] == calls[0]["max_tokens"] * 2
    assert result["length_finish_rate"] == .5


@pytest.mark.asyncio
async def test_invalid_evidence_repair_suffix_after_note(config, sections):
    config.tasks = ["case_context"]
    calls = []
    async def handler(request):
        body = json.loads(request.content)
        calls.append(body)
        data = copy.deepcopy(sections["case_context"])
        if len(calls) == 1:
            data["evidence"][0]["quote"] = "fabricated"
        return httpx.Response(200, text='data: '+json.dumps({"choices": [{"delta": {"content": json.dumps(data)}, "finish_reason": "stop"}]})+'\n\ndata: [DONE]\n\n')
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        result = await run_pipeline(config, client=APIClient(config.api, http))
    assert result["new_complete_records"] == 1
    assert "RETRY:" in calls[1]["messages"][-1]["content"]
    assert calls[0]["messages"][-1]["content"].split("EXTRACTION_TASK:")[0] == calls[1]["messages"][-1]["content"].split("EXTRACTION_TASK:")[0]


@pytest.mark.asyncio
async def test_transport_retry_recorded(config, monkeypatch):
    config.tasks = ["case_context"]
    monkeypatch.setattr("openpatients2.pipeline.asyncio.sleep", AsyncMock())
    good = transport()
    seen = 0
    async def handler(request):
        nonlocal seen
        seen += 1
        if seen == 1:
            return httpx.Response(503)
        return await good.handle_async_request(request)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        result = await run_pipeline(config, client=APIClient(config.api, http))
    assert result["new_complete_records"] == 1
    assert result["retry_attempts"] == 1


def test_required_profile_and_overflow(config, tmp_path, row):
    config.require_token_profile = True
    with pytest.raises(ValueError, match="profile"):
        TokenGuard(config)
    profile = tmp_path / "tokens.jsonl"
    config.token_profile = str(profile)
    item = {"record_id": row["record_id"], "source_hash": row["source_hash"], "input_fingerprint": input_fingerprint(row), "prompt_tokens": {"case_context": 65000}}
    profile.write_text(json.dumps(item) + "\n")
    write_json(str(profile)+".manifest.json", {"fingerprint": profile_fingerprint(config)})
    guard = TokenGuard(config)
    with pytest.raises(ValueError, match="context_overflow"):
        guard.budget(row, "case_context", 4096)
    config.api.revision = "changed"
    with pytest.raises(ValueError, match="Stale"):
        TokenGuard(config)


def test_tokenizer_uses_rendered_chat_template(config, row):
    class FakeTokenizer:
        def apply_chat_template(self, messages, tokenize, add_generation_prompt, **kwargs):
            assert tokenize and add_generation_prompt
            return list(range(sum(len(x["content"]) for x in messages)))
        def encode(self, text, add_special_tokens=False):
            assert not add_special_tokens
            return list(range(len(text)))
    result = tokenize_record(row, config, FakeTokenizer())
    assert all(x > result["note_tokens"] for x in result["prompt_tokens"].values())


def test_usage_unknown_not_zero():
    report = summarize([{"prompt_tokens": 100, "completion_tokens": 20}], 2, 1, 0)
    assert report["logical_input_tokens_per_second"] == 50
    assert report["uncached_input_tokens_per_wall_second"] is None
    assert report["cache_hit_fraction"] is None


def test_reasoning_not_double_counted():
    report = summarize([{"prompt_tokens": 100, "completion_tokens": 30, "reasoning_tokens": 20, "cached_tokens": 80}], 2, 1, 0)
    assert report["output_tokens_per_second"] == 15
    assert report["uncached_input_tokens_per_wall_second"] == 10


def test_reserved_api_overrides_rejected():
    with pytest.raises(ValueError):
        APIConfig(extra_body={"messages": []})


def test_prepare_preserves_source_and_keeps_duplicate_ids(tmp_path, row):
    source = tmp_path / "source.jsonl"
    source.write_text(json.dumps(row) + "\n" + json.dumps(row) + "\n")
    destination = tmp_path / "prepared.jsonl"
    summary = prepare(str(source), str(destination))
    rows = [json.loads(x) for x in destination.read_text().splitlines()]
    assert summary["duplicate_id_rows_preserved"] == 2
    assert len({x["record_id"] for x in rows}) == 2
    assert rows[0]["original_row"] == rows[1]["original_row"] == row["original_row"]
    text = "  α\n literal spacing  "
    assert normalize({"_id": "id", "description": text})["text"] == text


@pytest.mark.asyncio
async def test_deferred_cpu_export(config):
    from pathlib import Path
    from openpatients2.exports import export_run
    config.export_after_run = False
    async with httpx.AsyncClient(transport=transport()) as http:
        report = await run_pipeline(config, client=APIClient(config.api, http))
    assert report["patient_export_pending"]
    assert not (Path(config.output) / "patients.jsonl").exists()
    exported = export_run(config.output)
    assert exported["records"] == 1
    assert (Path(config.output) / "patients.jsonl").exists()


@pytest.mark.asyncio
async def test_source_provenance_invalidates_result_cache(config, row):
    from pathlib import Path
    calls = []
    async with httpx.AsyncClient(transport=transport(calls=calls)) as http:
        await run_pipeline(config, client=APIClient(config.api, http))
        row["dataset_revision"] = "different-input-version"
        Path(config.input).write_text(json.dumps(row) + "\n")
        report = await run_pipeline(config, client=APIClient(config.api, http))
    assert report["attempts"] == 14
    assert len(calls) == 28


def test_cli_failure_exit_status(monkeypatch):
    from openpatients2 import cli
    async def failed_demo(output):
        return {"failed_records": 1}
    monkeypatch.setattr("openpatients2.demo.run_demo", failed_demo)
    assert cli.main(["demo"]) == 2


def test_cli_restores_termination_handler(monkeypatch):
    import signal
    from openpatients2 import cli
    original = signal.getsignal(signal.SIGTERM)
    def interrupt(argv):
        assert signal.getsignal(signal.SIGTERM) is cli._handle_termination
        return 130
    monkeypatch.setattr(cli, "_main", interrupt)
    assert cli.main(["demo"]) == 130
    assert signal.getsignal(signal.SIGTERM) == original
