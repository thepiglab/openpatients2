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
    assert all("--disable-log-requests" not in x["argv"] for x in commands)


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


@pytest.mark.asyncio
async def test_real_server_exit_retains_original_error_and_log(tmp_path, monkeypatch):
    import sys
    from openpatients2.serving import ServerGroup
    monkeypatch.delenv('CUDA_VISIBLE_DEVICES', raising=False)
    config = ServingConfig.load(str(ROOT / 'configs/serving/k2-vllm-tp8.yaml'))
    group = ServerGroup(config, str(tmp_path), str(tmp_path / 'logs'))
    monkeypatch.setattr(group, 'preflight', lambda: {'synthetic': True})
    group.commands = [{'argv': [sys.executable, '-c',
                               "import sys; print('SYNTHETIC kernel failure', flush=True); sys.exit(7)"],
                       'endpoint': 'http://127.0.0.1:1/v1'}]
    with pytest.raises(RuntimeError, match='exited 7') as failure:
        await group.start(timeout=10)
    assert 'SYNTHETIC kernel failure' in str(failure.value)
    record = json.loads((tmp_path / 'logs/startup.json').read_text())
    assert record['failed_servers'][0]['returncode'] == 7
    assert Path(record['failed_servers'][0]['log']).is_absolute()
    assert not group.processes and not group.files
    assert group.stop() is None  # Real synchronous interface; repeated cleanup is safe.


@pytest.mark.asyncio
async def test_independent_replicas_wait_for_readiness_before_loading_next_wave(tmp_path, monkeypatch):
    import httpx
    from openpatients2 import serving
    monkeypatch.delenv('CUDA_VISIBLE_DEVICES', raising=False)
    raw = ServingConfig.load(str(ROOT / 'configs/serving/k2-vllm-tp8.yaml')).model_dump()
    raw.update(replicas=8, tensor_parallel=1, startup_wave_size=2)
    group = serving.ServerGroup(ServingConfig.model_validate(raw), str(tmp_path), str(tmp_path / 'logs'))
    monkeypatch.setattr(group, 'preflight', lambda: {'synthetic': True})
    ready = set(); launched = []; probes = []
    class Process:
        returncode = None
        def poll(self): return None
    def launch(*args, **kwargs):
        # A new wave may start only after all previous replicas were probed.
        assert ready == set(range(len(launched)))
        launched.append(Process())
        return launched[-1]
    # Check at wave boundaries; both members launch together.
    def launch_at_boundary(*args, **kwargs):
        if len(launched) % 2 == 0:
            return launch(*args, **kwargs)
        launched.append(Process()); return launched[-1]
    monkeypatch.setattr(serving.subprocess, 'Popen', launch_at_boundary)
    def handler(request):
        replica = request.url.port - 8000
        assert replica < len(launched)
        probes.append(replica)
        if replica == 1 and probes.count(1) == 1:
            return httpx.Response(503)
        ready.add(replica)
        return httpx.Response(200, json={'data': []})
    original = httpx.AsyncClient
    monkeypatch.setattr(serving.httpx, 'AsyncClient', lambda **kw: original(transport=httpx.MockTransport(handler), **kw))
    await group.start(timeout=10)
    assert len(launched) == 8 and ready == set(range(8))
    assert probes[:4] == [0, 1, 0, 1]  # Waited for the second replica's readiness.
    assert len(json.loads((tmp_path / 'logs/startup.json').read_text())['ready_endpoints']) == 8
    for handle in group.files: handle.close()


def test_connected_external_dp_cannot_load_in_independent_waves():
    raw = ServingConfig.load(str(ROOT / 'configs/serving/k2-vllm-tp2-dp4.yaml')).model_dump()
    raw['startup_wave_size'] = 2
    with pytest.raises(ValueError, match='Connected external DP'):
        ServingConfig.model_validate(raw)
