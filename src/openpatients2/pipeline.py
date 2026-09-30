from __future__ import annotations

import asyncio
import hashlib
import json
import random
import time
import uuid
from dataclasses import asdict
from itertools import islice
from pathlib import Path

from . import SCHEMA_VERSION, __version__
from .client import APIClient
from .config import PipelineConfig
from .data import records, write_json, input_fingerprint
from .metrics import summarize
from .profile import TokenGuard
from .prompts import messages_for, task_signature
from .store import Store
from .validation import validate
from .consistency import consistency_findings
from .evidence_recovery import packet_segments
from .targeted_repair import ItemRepair


def endpoint_for(record_id: str, endpoints: list[str]) -> str:
    # Endpoint must own a stable DP rank, or a whole independent model replica.
    # Hashing to a load balancer that internally chooses arbitrary ranks is NOT cache affinity.
    index = int.from_bytes(hashlib.sha256(record_id.encode()).digest()[:8], "big") % len(endpoints)
    return endpoints[index]


class Runner:
    def __init__(self, config: PipelineConfig, client: APIClient | None = None):
        self.config = config
        if config.api.revision in {"UNPINNED", "main", "unspecified"} and not config.allow_unpinned_revision:
            raise ValueError("Pin the checkpoint revision in api.revision; use allow_unpinned_revision only for explicit local tests")
        self.output = Path(config.output)
        self.output.mkdir(parents=True, exist_ok=True)
        self.guard = TokenGuard(config)
        self.store = Store(self.output / "state.sqlite")
        self.client = client or APIClient(config.api)
        self.owned_client = client is None
        self.request_sem = asyncio.Semaphore(config.request_concurrency)
        self.run_id = uuid.uuid4().hex
        self.signatures = {task: task_signature(task, config.api.identity(), {**config.api.generation(),
            'recovery_policy':{k:getattr(config,k) for k in ('recover_citation_formatting','recover_identical_duplicates','targeted_item_repair')},
            'validation_retries':config.validation_retries}) for task in config.tasks}
        self.completed = self.failed = self.resumed = 0
        self.seen_records: dict[str, str] = {}

    async def task(self, row: dict, task: str, endpoint: str) -> bool:
        signature = self.signatures[task]
        previous = self.store.get(row, task, signature)
        if previous and previous["status"] == "valid":
            return True
        namespace = self.config.namespace
        if self.config.cache_mode == "isolated_requests":
            namespace += ":" + uuid.uuid4().hex
        messages = messages_for(row, task, namespace)
        base_messages = messages_for(row, task, namespace)
        base_bytes = len(json.dumps(base_messages, ensure_ascii=False).encode())
        segments = packet_segments(row)
        plan = None
        budget = self.config.api.max_tokens
        last_checks: dict = {"errors": [], "warnings": [], "evidence": []}
        raw = None
        generation = None
        request_number = 0
        for repair in range(self.config.validation_retries + 1):
            try:
                overhead = max(0, len(json.dumps(messages, ensure_ascii=False).encode())-base_bytes)
                allowed_budget = self.guard.budget(row, task, budget, repair=bool(repair), extra_prompt_tokens=overhead)
            except ValueError as exc:
                partial = plan.output() if plan and plan.pending else None
                checks = {"errors": [str(exc)], "warnings": [], "evidence": [],
                          'item_repair':plan.audit() if plan else None}
                self.store.save(row, task, signature, "partial" if partial else "context_overflow", partial,
                                checks, raw, generation)
                return False
            result = None
            for transport_retry in range(self.config.api.http_retries + 1):
                async with self.request_sem:
                    result = await self.client.complete_once(endpoint, task, messages, allowed_budget)
                raw = result.content
                check = await asyncio.to_thread(validate, task, raw, row["text"], segments=segments,
                    recover_formatting=self.config.recover_citation_formatting,
                    recover_identical_duplicates=self.config.recover_identical_duplicates
                ) if not result.error and result.finish_reason in {"stop","eos"} else None
                if check and plan:
                    plan.apply(check.candidate)
                    parse_method, parse_audit, citation_audit = check.parse_method, check.parse_transformations, check.citation_recovery
                    check = validate(task, plan.output(), row['text'], segments=segments)
                    check.parse_transformations, check.citation_recovery = parse_audit, citation_audit
                    check.parse_method = parse_method
                    if plan.pending:check.errors.append('Targeted repair still has unresolved items')
                metric = result.metrics()
                metric.update({"is_retry": request_number > 0,
                               "validation_passed": check.valid if check else False,
                               "validation_warning_count": len(check.warnings) if check else 0})
                request = {"messages": messages if self.config.api.save_messages else None,
                           "parameters": {k: v for k, v in self.client.body(task, messages, allowed_budget).items()
                                          if k not in {"messages", "response_format", "structured_outputs"}},
                           "schema_signature": signature}
                generation = {**result.response(), "request": request, "metrics": metric,
                              'validation':asdict(check) if check else None,
                              'item_repair':plan.audit() if plan else None}
                attempt_id = self.store.attempt(self.run_id, row, task, endpoint, metric,
                                               generation, request, signature)
                generation["attempt_id"] = attempt_id
                generation["run_id"] = self.run_id
                request_number += 1
                if check and check.valid:
                    self.store.save(row, task, signature, "valid", check.data,
                                    {**asdict(check), 'item_repair':plan.audit() if plan else None}, raw, generation)
                    return True
                if check:
                    last_checks = {**asdict(check), 'item_repair':plan.audit() if plan else None}
                else:
                    last_checks = {"errors": [result.error or f"finish_reason={result.finish_reason}"],
                                   "warnings": [], "evidence": []}
                if self.client.transient(result) and transport_retry < self.config.api.http_retries:
                    await asyncio.sleep(min(30, 2 ** transport_retry) + random.random() * .25)
                    continue
                break
            assert result is not None
            if result.error:
                # A 400 schema/backend mismatch must be fixed, not hidden by prompt retries.
                break
            if result.finish_reason == "length":
                if budget >= self.config.api.max_retry_tokens:
                    break
                budget = min(budget * 2, self.config.api.max_retry_tokens)
                # No partial answer is fed back; re-extract with a larger total generation budget.
            else:
                if check and plan is None and self.config.targeted_item_repair:
                    plan = ItemRepair.create(task, check.candidate, row['text'], segments)
                suffix = json.dumps(last_checks["errors"], ensure_ascii=True)[:1800]
                messages = messages_for(row, task, namespace)
                messages[-1]["content"] += plan.instruction() if plan else (
                    "\nRETRY: Your previous result failed these deterministic checks: " + suffix
                    + "\nProduce the full corrected section from source only. Do not invent evidence or missing facts.")
        partial = plan.output() if plan and plan.pending else None
        if partial and not validate(task, partial, row['text'], segments=segments).valid:partial = None
        if plan:last_checks['item_repair'] = plan.audit()
        self.store.save(row, task, signature, "partial" if partial else "failed", partial, last_checks, raw, generation)
        return False

    async def patient(self, row: dict):
        self.store.put_record(row)
        self.seen_records[row["record_id"]] = input_fingerprint(row)
        missing = []
        ordered = ["case_context", *(t for t in self.config.tasks if t != "case_context")]
        for task in ordered:
            result = self.store.get(row, task, self.signatures[task])
            if not result or result["status"] != "valid":
                missing.append(task)
        if not missing:
            self.resumed += 1
            return
        endpoint = endpoint_for(row["record_id"], self.config.api.endpoints)
        # Warm ONE useful task, then branch. If context is resumed from disk, it does
        # not exist in GPU KV: warm the first missing task instead of stampeding.
        first_ok = await self.task(row, missing[0], endpoint)
        semaphore = asyncio.Semaphore(self.config.task_fanout)
        async def one(task):
            async with semaphore:
                return await self.task(row, task, endpoint)
        rest = await asyncio.gather(*(one(task) for task in missing[1:]))
        if first_ok and all(rest):
            self.completed += 1
        else:
            self.failed += 1

    async def run(self, limit: int | None = None) -> dict:
        manifest = {"run_id": self.run_id, "package_version": __version__, "schema_version": SCHEMA_VERSION,
                    "config": self.config.model_dump(), "config_hash": self.config.digest(),
                    "task_signatures": self.signatures, "hardware_claim": "No hardware assumptions; benchmark environment.json records devices when available"}
        write_json(self.output / f"manifest-{self.run_id}.json", manifest)
        before = await self.client.snapshot_metrics()
        write_json(self.output / f"server-metrics-before-{self.run_id}.json", before)
        queue: asyncio.Queue = asyncio.Queue(maxsize=self.config.patient_concurrency * 2)
        async def worker():
            while True:
                row = await queue.get()
                try:
                    if row is None:
                        return
                    await self.patient(row)
                finally:
                    queue.task_done()
        async def produce():
            iterator = records(self.config.input, limit)
            # Optional bounded-window shuffle. Benchmark samples should instead be
            # materialized once and shared across models for exact reproducibility.
            rng = random.Random(self.config.shuffle_seed)
            while batch := list(islice(iterator, 256)):
                if self.config.shuffle_seed is not None:
                    rng.shuffle(batch)
                for row in batch:
                    await queue.put(row)
            for _ in range(self.config.patient_concurrency):
                await queue.put(None)
        start = time.perf_counter()
        try:
            # TaskGroup propagates producer/worker errors and cancels peers; queue.join
            # on a crashed worker would otherwise hang indefinitely.
            async with asyncio.TaskGroup() as group:
                group.create_task(produce())
                for _ in range(self.config.patient_concurrency):
                    group.create_task(worker())
            self.store.flush()
            wall = time.perf_counter() - start
            report = summarize(self.store.metrics(self.run_id), wall, self.completed, self.failed, self.resumed)
            report.update({"run_id": self.run_id, "model": self.config.api.identity(),
                           "scope_tasks": self.config.tasks, "cache_mode": self.config.cache_mode,
                           "patient_concurrency": self.config.patient_concurrency,
                           "request_concurrency": self.config.request_concurrency,
                           "task_fanout": self.config.task_fanout,
                           "generation_parameters": self.config.api.generation(),
                           "clinical_accuracy": None, "device_benchmark_status": "user_endpoint_run"})
            write_json(self.output / f"scope-{self.run_id}.json", self.seen_records)
            report["patient_export_pending"] = not self.config.export_after_run
            write_json(self.output / "report.json", report)
            if self.config.export_after_run:
                from .exports import export_run
                export_run(str(self.output), self.run_id)
            write_json(self.output / f"server-metrics-after-{self.run_id}.json", await self.client.snapshot_metrics())
            return report
        finally:
            self.store.close()
            if self.owned_client:
                await self.client.close()


async def run_pipeline(config: PipelineConfig, limit: int | None = None, client: APIClient | None = None) -> dict:
    return await Runner(config, client).run(limit)


async def smoke(config: PipelineConfig, row: dict, all_tasks: bool = True) -> dict:
    """Compile/check every schema on every explicit cache-owning endpoint, outside timing."""
    results = []
    client = APIClient(config.api)
    try:
        for endpoint in config.api.endpoints:
            for task in config.tasks if all_tasks else ["case_context"]:
                response = await client.complete_once(endpoint, task, messages_for(row, task, "schema-smoke"), config.api.max_tokens)
                checked = validate(task, response.content, row["text"])
                results.append({"endpoint": endpoint, "task": task,
                                "valid": checked.valid and not response.error and response.finish_reason == "stop",
                                "errors": checked.errors, "http_error": response.error, "finish_reason": response.finish_reason})
    finally:
        await client.close()
    return {"passed": all(x["valid"] for x in results), "checks": results}
