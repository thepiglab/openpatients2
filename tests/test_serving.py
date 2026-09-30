import json
from pathlib import Path

import pytest

from openpatients2.serving import ServingConfig, render

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("path", sorted((ROOT / "configs/serving").glob("*.yaml")), ids=lambda x: x.name)
def test_all_profiles_fit_budget_and_render(path, monkeypatch):
    monkeypatch.delenv("CUDA_VISIBLE_DEVICES", raising=False)
    config = ServingConfig.load(str(path))
    commands = render(config, str(ROOT))
    assert config.gpus <= 8
    assert sum(len(x["devices"]) for x in commands) == config.gpus
    assert len({x["endpoint"] for x in commands}) == len(commands)
    assert all(x["argv"][0] == "apptainer" for x in commands)
    assert all("HF_HUB_OFFLINE=1" in x["argv"] for x in commands)
    assert all("--host" in x["argv"] and "127.0.0.1" in x["argv"] for x in commands)


def test_external_dp_has_disjoint_devices_and_explicit_rank(monkeypatch):
    monkeypatch.setenv("CUDA_VISIBLE_DEVICES", "GPU-a,GPU-b,GPU-c,GPU-d,GPU-e,GPU-f,GPU-g,GPU-h")
    config = ServingConfig.load(str(ROOT / "configs/serving/k2-vllm-tp2-dp4.yaml"))
    commands = render(config)
    assert len(commands) == 4
    assert commands[0]["devices"] == ["GPU-a", "GPU-b"]
    assert commands[3]["devices"] == ["GPU-g", "GPU-h"]
    assert all("--data-parallel-rank" in x["argv"] for x in commands)
    assert all(x["cache_affinity"] for x in commands)


def test_ep_not_additional_gpu_multiplier():
    config = ServingConfig.load(str(ROOT / "configs/serving/k2-vllm-tp1-dp8.yaml"))
    assert config.gpus == 8


def test_mtp_uses_local_checkpoint_not_hub_download(monkeypatch):
    monkeypatch.delenv("CUDA_VISIBLE_DEVICES", raising=False)
    config = ServingConfig.load(str(ROOT / "configs/serving/motif-4x2-mtp.yaml"))
    command = render(config, str(ROOT))[0]["argv"]
    spec = json.loads(command[command.index("--speculative-config") + 1])
    assert Path(spec["model"]).is_absolute()
    assert spec["num_speculative_tokens"] == 1


def test_vendor_internal_dp_does_not_pretend_rank_affinity(monkeypatch):
    monkeypatch.delenv("CUDA_VISIBLE_DEVICES", raising=False)
    config = ServingConfig.load(str(ROOT / "configs/serving/motif-vendor2.yaml"))
    assert render(config)[0]["cache_affinity"] is False


def test_over_budget_rejected():
    raw = ServingConfig.load(str(ROOT / "configs/serving/k2-vllm-tp8.yaml")).model_dump()
    raw["replicas"] = 2
    with pytest.raises(ValueError):
        ServingConfig.model_validate(raw)
