from __future__ import annotations

import asyncio
import hashlib
import json
import os
import shlex
import signal
import subprocess
import time
from pathlib import Path
from typing import Literal

import httpx
import yaml
from pydantic import Field, model_validator

from .config import ConfigModel
from .data import write_json


class ServingConfig(ConfigModel):
    name: str
    backend: Literal["vllm", "sglang"]
    version: str
    image_reference: str
    sif: str
    model_path: str
    model_id: str
    served_model_name: str = "clinical-extractor"
    container_python: str | None = None
    replicas: int = Field(default=1, ge=1)
    tensor_parallel: int = Field(default=8, ge=1)
    data_parallel: int = Field(default=1, ge=1)
    pipeline_parallel: int = Field(default=1, ge=1)
    expert_parallel: bool = True
    external_dp: bool = True
    reasoning_parser: str | None = None
    quantization: str | None = None
    speculation: Literal["off", "mtp", "ngram"] = "off"
    speculative_tokens: int = Field(default=1, ge=1)
    max_model_len: int = Field(default=65536, ge=1024)
    max_num_seqs: int = Field(default=128, ge=1)
    max_batched_tokens: int = Field(default=16384, ge=512)
    gpu_memory_utilization: float = Field(default=.85, gt=0, lt=1)
    kv_cache_dtype: str = "auto"
    prefix_cache: bool = True
    port: int = Field(default=8000, ge=1024, le=60000)
    rpc_port: int = Field(default=13345, ge=1024, le=60000)
    extra_args: list[str] = Field(default_factory=list)
    environment: dict[str, str] = Field(default_factory=dict)
    experimental: bool = False
    validation_note: str = "Not hardware-tested by this project"

    @model_validator(mode="after")
    def topology(self):
        if self.replicas * self.tensor_parallel * self.data_parallel * self.pipeline_parallel > 8:
            raise ValueError("This single-node profile exceeds the requested 8-GPU budget")
        if self.backend == "sglang" and (self.data_parallel != 1 or self.pipeline_parallel != 1 or self.speculation != "off"):
            raise ValueError("Only SGLang TP/EP non-speculative profiles are implemented; do not pretend an unverified feature works")
        if self.pipeline_parallel != 1:
            raise ValueError("PP>1 is not implemented in this one-node launcher")
        managed = {"--port", "--host", "--tensor-parallel-size", "--data-parallel-size", "--data-parallel-rank",
                   "--model", "--model-path", "--served-model-name", "--tp", "--tp-size", "--ep", "--ep-size",
                   "--speculative-config", "--gpu-memory-utilization", "--mem-fraction-static"}
        if any(arg.split("=")[0] in managed for arg in self.extra_args):
            raise ValueError("extra_args cannot override managed topology/model/memory flags")
        return self

    @classmethod
    def load(cls, path: str):
        return cls.model_validate(yaml.safe_load(Path(path).read_text()))

    @property
    def gpus(self):
        # EP partitions the MoE layers across TP*DP; it is not another multiplicative GPU axis.
        return self.replicas * self.tensor_parallel * self.data_parallel


def render(config: ServingConfig, root: str = ".") -> list[dict]:
    root_path = Path(root).resolve()
    model = str((root_path / config.model_path).resolve())
    sif = str((root_path / config.sif).resolve())
    visible = os.environ.get("CUDA_VISIBLE_DEVICES", "").split(",")
    if not visible or visible == [""]:
        visible = [str(i) for i in range(config.gpus)]
    if len(visible) < config.gpus:
        raise ValueError("Not enough GPUs in CUDA_VISIBLE_DEVICES for this profile")
    commands = []
    for replica in range(config.replicas):
        ranks = range(config.data_parallel) if config.external_dp else range(1)
        for rank in ranks:
            local_rank_count = 1 if config.external_dp else config.data_parallel
            start = replica * config.tensor_parallel * config.data_parallel + rank * config.tensor_parallel
            devices = visible[start:start + config.tensor_parallel * local_rank_count]
            port = config.port + len(commands)
            env = {"CUDA_VISIBLE_DEVICES": ",".join(devices), "HF_HUB_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1",
                   "HF_DATASETS_OFFLINE": "1", "VLLM_NO_USAGE_STATS": "1", "DO_NOT_TRACK": "1",
                   **config.environment}
            cmd = ["apptainer", "exec", "--nv", "--cleanenv", "--bind", f"{root_path}:{root_path}"]
            # Local model paths outside the project root must also be explicitly mounted.
            if not Path(model).is_relative_to(root_path):
                cmd += ["--bind", f"{model}:{model}"]
            for key, value in env.items():
                cmd += ["--env", f"{key}={value}"]
            cmd += [sif]
            if config.backend == "vllm":
                cmd += [config.container_python, "-m", "vllm.entrypoints.cli.main"] if config.container_python else ["vllm"]
                cmd += ["serve", model, "--served-model-name", config.served_model_name,
                        "--trust-remote-code", "--dtype", "bfloat16", "--host", "127.0.0.1", "--port", str(port),
                        "--tensor-parallel-size", str(config.tensor_parallel),
                        "--max-model-len", str(config.max_model_len), "--max-num-seqs", str(config.max_num_seqs),
                        "--max-num-batched-tokens", str(config.max_batched_tokens),
                        "--gpu-memory-utilization", str(config.gpu_memory_utilization),
                        "--kv-cache-dtype", config.kv_cache_dtype, "--enable-chunked-prefill", "--disable-log-requests"]
                if config.prefix_cache:
                    cmd += ["--enable-prefix-caching"]
                else:
                    cmd += ["--no-enable-prefix-caching"]
                if config.data_parallel > 1:
                    cmd += ["--data-parallel-size", str(config.data_parallel),
                            "--data-parallel-address", "127.0.0.1", "--data-parallel-rpc-port", str(config.rpc_port + replica * 1000)]
                    if config.external_dp:
                        cmd += ["--data-parallel-rank", str(rank)]
                    else:
                        cmd += ["--data-parallel-size-local", str(config.data_parallel)]
                if config.expert_parallel:
                    cmd += ["--enable-expert-parallel"]
                if config.speculation == "mtp":
                    spec = {"model": model, "num_speculative_tokens": config.speculative_tokens}
                    cmd += ["--speculative-config", json.dumps(spec)]
                elif config.speculation == "ngram":
                    spec = {"method": "ngram", "num_speculative_tokens": config.speculative_tokens,
                            "prompt_lookup_max": 5, "prompt_lookup_min": 3}
                    cmd += ["--speculative-config", json.dumps(spec)]
            else:
                cmd += ["python", "-m", "sglang.launch_server", "--model-path", model,
                        "--served-model-name", config.served_model_name, "--trust-remote-code",
                        "--dtype", "bfloat16", "--host", "127.0.0.1", "--port", str(port),
                        "--tp-size", str(config.tensor_parallel), "--context-length", str(config.max_model_len),
                        "--max-running-requests", str(config.max_num_seqs),
                        "--chunked-prefill-size", str(config.max_batched_tokens),
                        "--mem-fraction-static", str(config.gpu_memory_utilization), "--kv-cache-dtype", config.kv_cache_dtype]
                if config.expert_parallel:
                    cmd += ["--ep-size", str(config.tensor_parallel)]
                if not config.prefix_cache:
                    cmd += ["--disable-radix-cache"]
            if config.reasoning_parser:
                cmd += ["--reasoning-parser", config.reasoning_parser]
            if config.quantization:
                cmd += ["--quantization", config.quantization]
            cmd += config.extra_args
            commands.append({"replica": replica, "rank": rank, "devices": devices, "argv": cmd,
                             "endpoint": f"http://127.0.0.1:{port}/v1",
                             "cache_affinity": config.data_parallel == 1 or config.external_dp})
    return commands


class ServerGroup:
    def __init__(self, config: ServingConfig, root: str, logs: str):
        self.config, self.root, self.logs = config, Path(root).resolve(), Path(logs)
        self.commands = render(config, root)
        self.processes: list[subprocess.Popen] = []
        self.files = []

    def preflight(self):
        model = (self.root / self.config.model_path).resolve()
        sif = (self.root / self.config.sif).resolve()
        if not model.is_dir() or not (model / "config.json").exists() or not sif.is_file():
            raise ValueError("Missing local model/config.json or serving SIF. Download/build on CPU before allocating GPUs.")
        if not (model / "op2_snapshot.json").is_file():
            raise ValueError("Missing pinned op2_snapshot.json; use `op2 download-model` on CPU first")
        snapshot = json.loads((model / "op2_snapshot.json").read_text())
        if snapshot.get("model_id") != self.config.model_id:
            raise ValueError("Serving profile model_id differs from downloaded snapshot")
        import shutil
        if not shutil.which("apptainer"):
            raise ValueError("Apptainer is not on PATH; load the cluster module first")
        return snapshot

    async def start(self, timeout: float = 1800):
        snapshot = self.preflight()
        self.logs.mkdir(parents=True, exist_ok=True)
        write_json(self.logs / "deployment.json", {"serving": self.config.model_dump(), "snapshot": snapshot,
                                                  "commands": self.commands, "kernel_validation": "pending real smoke/benchmark"})
        try:
            for i, item in enumerate(self.commands):
                handle = open(self.logs / f"server-{i}.log", "w", encoding="utf-8")
                self.files.append(handle)
                self.processes.append(subprocess.Popen(item["argv"], cwd=self.root, stdout=handle, stderr=subprocess.STDOUT, start_new_session=True))
            # All EP-connected ranks must launch before waiting for readiness.
            start = time.monotonic()
            async with httpx.AsyncClient(timeout=5) as client:
                while time.monotonic() - start < timeout:
                    failed = [(i, p.returncode) for i, p in enumerate(self.processes) if p.poll() is not None]
                    if failed:
                        details = []
                        for i, code in failed:
                            path = (self.logs / f"server-{i}.log").resolve()
                            with path.open('rb') as source:
                                source.seek(max(0, path.stat().st_size - 12000))
                                tail = source.read().decode('utf-8', errors='replace')
                            details.append({'replica': i, 'returncode': code, 'log': str(path), 'tail': tail})
                        write_json(self.logs / 'startup.json', {'status': 'failed', 'failed_servers': details})
                        detail = '\n'.join(f"Replica {d['replica']} exited {d['returncode']}; {d['log']}\n{d['tail']}" for d in details)
                        raise RuntimeError("Serving process exited during startup:\n" + detail)
                    ready = []
                    for item in self.commands:
                        try:
                            response = await client.get(item["endpoint"] + "/models")
                            ready.append(response.status_code == 200)
                        except httpx.HTTPError:
                            ready.append(False)
                    if all(ready):
                        write_json(self.logs / "startup.json", {"startup_seconds": time.monotonic() - start,
                                                               "ready_endpoints": [x["endpoint"] for x in self.commands]})
                        return self.commands
                    await asyncio.sleep(2)
            raise TimeoutError("Serving readiness timeout")
        except BaseException:
            try:
                self.stop()
            except Exception as cleanup_error:
                write_json(self.logs / 'cleanup-error.json', {'error': repr(cleanup_error)})
            raise

    def stop(self):
        for process in self.processes:
            if process.poll() is None:
                try:
                    os.killpg(process.pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass
        for process in self.processes:
            try:
                process.wait(timeout=30)
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                process.wait(timeout=10)
        for handle in self.files:
            handle.close()
        self.processes.clear()
        self.files.clear()
