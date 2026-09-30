from __future__ import annotations

import json
import os
import platform
import shutil
import subprocess
import uuid
from pathlib import Path

from .config import PipelineConfig, load_config
from .data import records, write_json
from .pipeline import run_pipeline, smoke
from .serving import ServerGroup, ServingConfig


def environment_report() -> dict:
    report = {"platform": platform.platform(), "python": platform.python_version(),
              "slurm_job_id": os.environ.get("SLURM_JOB_ID"),
              "cuda_visible_devices": os.environ.get("CUDA_VISIBLE_DEVICES")}
    commands = [["nvidia-smi", "--query-gpu=name,uuid,memory.total,driver_version", "--format=csv"],
                ["nvidia-smi", "topo", "-m"]]
    for command in commands:
        key = " ".join(command)
        try:
            response = subprocess.run(command, capture_output=True, text=True, timeout=15)
            report[key] = response.stdout if response.returncode == 0 else None
        except (FileNotFoundError, subprocess.TimeoutExpired):
            report[key] = None
    return report


async def benchmark(config: PipelineConfig, limit: int = 128, warmup: bool = True) -> dict:
    run_id = uuid.uuid4().hex[:12]
    config = config.model_copy(deep=True)
    config.output = str(Path(config.output) / f"bench-{run_id}")
    config.namespace = "benchmark-" + run_id  # unique before source: prevents cross-run cache contamination
    Path(config.output).mkdir(parents=True, exist_ok=True)
    if warmup:
        # Schema compilation is outside measurement. Different namespace prevents
        # reusing the first record's note KV in the measured run.
        report = await smoke(config, next(records(config.input)), all_tasks=True)
        write_json(Path(config.output) / "smoke.json", report)
        if not report["passed"]:
            raise RuntimeError("Schema/parser smoke failed; refusing to benchmark an invalid configuration")
    write_json(Path(config.output) / "environment.json", environment_report())
    gpu_handle, gpu_process = None, None
    if shutil.which("nvidia-smi"):
        gpu_handle = open(Path(config.output) / "gpu.csv", "w")
        gpu_process = subprocess.Popen(["nvidia-smi", "--query-gpu=timestamp,index,utilization.gpu,utilization.memory,memory.used,power.draw",
                                        "--format=csv", "-l", "1"], stdout=gpu_handle, stderr=subprocess.DEVNULL)
    try:
        report = await run_pipeline(config, limit)
        report["benchmark_directory"] = config.output
        write_json(Path(config.output) / "report.json", report)
        return report
    finally:
        if gpu_process:
            gpu_process.terminate()
            try:
                gpu_process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                gpu_process.kill()
                gpu_process.wait()
        if gpu_handle:
            gpu_handle.close()


async def campaign(path: str) -> dict:
    import yaml
    specification = yaml.safe_load(Path(path).read_text())
    allowed = {"pipeline", "serving_profiles", "patient_concurrency", "task_fanout", "repeats", "limit", "output", "root"}
    if set(specification) - allowed:
        raise ValueError("Unknown campaign key")
    base = load_config(specification["pipeline"])
    root = specification.get("root", ".")
    output = Path(specification.get("output", "runs/campaign"))
    output.mkdir(parents=True, exist_ok=True)
    results = []
    for profile in specification["serving_profiles"]:
        serving = ServingConfig.load(profile)
        group = ServerGroup(serving, root, str(output / serving.name / "server"))
        try:
            commands = await group.start()
            snapshot = json.loads((Path(root) / serving.model_path / "op2_snapshot.json").read_text())
            if base.api.model_id != snapshot["model_id"] or base.api.revision != snapshot["revision"]:
                raise ValueError("Campaign pipeline identity/profile must match this model and its token profile; run separate campaigns per model")
            for concurrency in specification.get("patient_concurrency", [8, 16, 32]):
                for fanout in specification.get("task_fanout", [2]):
                    for repeat in range(specification.get("repeats", 3)):
                        cfg = base.model_copy(deep=True)
                        cfg.output = str(output / serving.name / f"p{concurrency}-f{fanout}-r{repeat}")
                        cfg.api.endpoints = [x["endpoint"] for x in commands]
                        cfg.api.metrics_urls = [x["endpoint"].removesuffix("/v1") + "/metrics" for x in commands]
                        cfg.patient_concurrency = concurrency
                        cfg.task_fanout = fanout
                        cfg.request_concurrency = max(32, concurrency * fanout)
                        if cfg.max_model_len != serving.max_model_len:
                            raise ValueError("Pipeline max_model_len must match serving profile")
                        report = await benchmark(cfg, specification.get("limit", 128), warmup=True)
                        results.append({"serving_profile": profile, "repeat": repeat, **report})
                        write_json(output / "campaign-results.json", results)
        finally:
            group.stop()
    return {"runs": len(results), "results_file": str(output / "campaign-results.json")}
