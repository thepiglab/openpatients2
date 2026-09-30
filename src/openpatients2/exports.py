from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from .consistency import consistency_findings


def export_run(directory: str, run_id: str | None = None, destination: str | None = None) -> dict:
    """CPU-only exports. Reasons stay beside outputs, never in the clinical facts table.

    patients.jsonl: merged source + sections + reasoning for each task.
    extractions.jsonl: one task row including exact request and selected response.
    attempts.jsonl: EVERY attempt, including failed/truncated/repaired responses.
    """
    root = Path(directory)
    if run_id is None:
        run_id = json.loads((root / "report.json").read_text())["run_id"]
    manifest = json.loads((root / f"manifest-{run_id}.json").read_text())
    scope = json.loads((root / f"scope-{run_id}.json").read_text())
    tasks, signatures = manifest["config"]["tasks"], manifest["task_signatures"]
    identity = {key: manifest["config"]["api"][key] for key in ["model", "model_id", "revision"]}
    path = Path(destination) if destination else root / "patients.jsonl"
    task_path, attempt_path = path.parent / "extractions.jsonl", path.parent / "attempts.jsonl"
    if path.name in {"extractions.jsonl", "attempts.jsonl"}:
        raise ValueError("Patient output may not overwrite a task/attempt export")
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(f"file:{(root / 'state.sqlite').resolve()}?mode=ro", uri=True)
    db.row_factory = sqlite3.Row
    complete = 0
    paths = [path, task_path, attempt_path]
    temps = [x.with_suffix(x.suffix + ".tmp") for x in paths]
    try:
        with temps[0].open("w", encoding="utf-8") as out, temps[1].open("w", encoding="utf-8") as task_out, temps[2].open("w", encoding="utf-8") as attempt_out:
            for temp in temps:
                temp.chmod(0o600)
            for rid, input_hash in sorted(scope.items()):
                row = db.execute("SELECT payload FROM record_versions WHERE record_id=? AND input_hash=?", (rid, input_hash)).fetchone()
                if row is None:
                    raise ValueError(f"Missing immutable source version for {rid}")
                record = json.loads(row[0])
                sections, quality, generations = {}, {}, {}
                for task in tasks:
                    result = db.execute("SELECT * FROM results WHERE record_id=? AND task=? AND signature=? AND input_hash=?",
                                        (rid, task, signatures[task], input_hash)).fetchone()
                    status = result["status"] if result else "missing"
                    data = json.loads(result["data"]) if result and status in {"valid","partial"} and result["data"] else None
                    checks = json.loads(result["checks"]) if result else {}
                    raw_generation = result["generation"] if result else None
                    generation = json.loads(raw_generation) if raw_generation else {
                        "reasoning_text": None, "reasoning_status": "no_response", "request": None}
                    sections[task] = data
                    quality[task] = {"status": status, "checks": checks}
                    # Avoid repeating each full source prompt in the merged patient row.
                    generations[task] = {k: v for k, v in generation.items() if k != "request"}
                    attempt_metrics = [json.loads(x[0]) for x in db.execute(
                        "SELECT metrics FROM attempts WHERE run_id=? AND record_id=? AND task=? ORDER BY attempt_id",
                        (generation.get("run_id", run_id), rid, task))]
                    def summed(name):
                        values = [x.get(name) for x in attempt_metrics]
                        return sum(values) if values and all(x is not None for x in values) else None
                    attempt_summary = {"attempt_count":len(attempt_metrics),
                        "latency_seconds":summed("latency_seconds"), "completion_tokens":summed("completion_tokens"),
                        "reasoning_tokens":summed("reasoning_tokens"), "reasoning_characters":summed("reasoning_characters"),
                        "first_attempt_source_valid":bool(attempt_metrics[0].get("validation_passed")) if attempt_metrics else None}
                    task_row = {"schema_version": manifest["schema_version"], "run_id": run_id,
                        "record_id": rid, "task": task, "input_hash": input_hash,
                        "source": {k: v for k, v in record.items() if k != "text"},
                        "model": identity, "generation_parameters": manifest["config"]["api"],
                        "task_signature": signatures[task], "status": status,
                        "extraction": data, "validation": checks, "attempt_summary": attempt_summary,
                        "reasoning_text": generation.get("reasoning_text"), "generation": generation}
                    # No credentials are stored (api_key_env is only an environment variable NAME).
                    task_out.write(json.dumps(task_row, ensure_ascii=False, allow_nan=False) + "\n")
                valid = all(x["status"] == "valid" for x in quality.values())
                complete += int(valid)
                out.write(json.dumps({"schema_version": manifest["schema_version"], "source": record, "model": identity,
                    "task_signatures": signatures, "expected_tasks": tasks, "complete_for_scope": valid,
                    "sections": sections, "quality": quality, "generations": generations,
                    "cross_section_findings": consistency_findings(sections)}, ensure_ascii=False, allow_nan=False) + "\n")
            for attempt in db.execute("SELECT * FROM attempts WHERE run_id=? ORDER BY attempt_id", (run_id,)):
                item = dict(attempt)
                for key in ["metrics", "generation", "request"]:
                    if item.get(key):
                        item[key] = json.loads(item[key])
                attempt_out.write(json.dumps(item, ensure_ascii=False, allow_nan=False) + "\n")
        for temp, final in zip(temps, paths):
            temp.replace(final)
        return {"records": len(scope), "complete_for_scope": complete, "output": str(path),
                "task_rows": str(task_path), "attempt_rows": str(attempt_path), "run_id": run_id}
    finally:
        db.close()
