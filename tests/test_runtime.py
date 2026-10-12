import copy
import json
from unittest.mock import AsyncMock

import httpx
import pytest

from openpatients2.client import APIClient
from openpatients2.config import APIConfig
from openpatients2.prompts import messages_for, task_signature
from openpatients2.schemas import TASK_MODELS
from openpatients2.data import write_json, normalize, prepare, records, input_fingerprint
from synthetic_fixtures import fixture_sections, transport


def test_identical_prefix_before_each_task(row):
    prefixes = [messages_for(row, t)[1]["content"].split("\nEXTRACTION_TASK:")[0] for t in TASK_MODELS]
    assert len(set(prefixes)) == 1
    assert row["record_id"] in prefixes[0]
    assert json.dumps(row["text"], ensure_ascii=False) in prefixes[0]


def test_signature_changes_with_checkpoint_and_generation():
    a = task_signature("oncology", {"revision": "A"}, {"temperature": 0})
    assert a != task_signature("oncology", {"revision": "B"}, {"temperature": 0})
    assert a != task_signature("oncology", {"revision": "A"}, {"temperature": 1})


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
