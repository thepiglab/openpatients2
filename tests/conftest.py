import json
import pytest

from openpatients2.config import APIConfig, PipelineConfig
from openpatients2.demo import example_record, fixture_sections


@pytest.fixture
def sections():
    return fixture_sections()


@pytest.fixture
def row():
    return example_record()


@pytest.fixture
def config(tmp_path, row):
    path = tmp_path / "input.jsonl"
    path.write_text(json.dumps(row) + "\n")
    return PipelineConfig(input=str(path), output=str(tmp_path / "run"), require_token_profile=False,
                          api=APIConfig(endpoints=["http://mock.test/v1"], model_id="fixture", revision="test-sha"),
                          patient_concurrency=2, task_fanout=2)
