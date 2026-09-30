"""Stream preserved source rows through deduplicated literature discovery."""
from __future__ import annotations

import asyncio
import copy
import json
import os
import time
from collections import Counter
from pathlib import Path

import httpx

from .data import records, write_json
from .literature_client import LiteratureClient, LiteratureConfig, utc_now
from .pmc_media import base_discovery
from .provenance import attach_resolved, source_reference


async def enrich_sources(source: str, destination: str, config: LiteratureConfig,
                         limit: int | None = None, http: httpx.AsyncClient | None = None) -> dict:
    """CPU stage. Preserve input order, all original fields, and all unresolved rows.

    Only the small unique identifier set is held in memory. Article manifests are
    cached in SQLite and case rows are streamed on a second pass. CPU XML parsing
    uses worker threads; API requests are asynchronous and globally rate-limited.
    """
    if limit is not None and limit < 1:
        raise ValueError("limit must be positive")
    if os.getenv("SLURM_JOB_GPUS") or os.getenv("SLURM_STEP_GPUS"):
        raise ValueError("Source discovery is CPU-only; run it outside the GPU allocation")
    src, dst = Path(source), Path(destination)
    article_path = dst.with_suffix(".articles.jsonl")
    if src.resolve() in {dst.resolve(), article_path.resolve()}:
        raise ValueError("Enrichment must not overwrite its source input")
    dst.parent.mkdir(parents=True, exist_ok=True)
    refs: set[tuple[str, str]] = set()
    n = 0
    for row in records(src, limit):
        n += 1
        ref = source_reference(row["provenance"]["article"])
        if ref:
            refs.add(ref)
    start = time.monotonic()
    client = LiteratureClient(config, http)
    transient: dict[str, dict] = {}
    temps = [p.with_suffix(p.suffix + ".tmp") for p in (dst, article_path)]
    try:
        mappings = await client.resolve_many(refs)
        pmcids = sorted({x["pmcid"] for x in mappings.values() if x.get("pmcid") and x.get("status") != "identifier_conflict"})
        queue: asyncio.Queue = asyncio.Queue(maxsize=config.concurrency * 2)

        async def produce():
            for pmcid in pmcids:
                await queue.put(pmcid)
            for _ in range(config.concurrency):
                await queue.put(None)

        async def worker():
            while True:
                pmcid = await queue.get()
                try:
                    if pmcid is None:
                        return
                    result = await client.discover_article(pmcid)
                    # Failed lookups and offline misses have no durable negative
                    # meaning. Keep the result for this run only, not as 'no images'.
                    if result["status"] in {"lookup_failed", "not_checked_offline"}:
                        transient[pmcid] = result
                finally:
                    queue.task_done()

        async with asyncio.TaskGroup() as tasks:
            tasks.create_task(produce())
            for _ in range(config.concurrency):
                tasks.create_task(worker())

        def article_result(pmcid):
            value = transient.get(pmcid) or client.cached_article(pmcid)
            if value is None:
                raise RuntimeError(f"Missing article discovery result for {pmcid}")
            return value

        status_counts: Counter = Counter()
        image_rows = article_figures = failed = 0
        with temps[0].open("w", encoding="utf-8") as out, temps[1].open("w", encoding="utf-8") as art_out:
            for p in temps:
                p.chmod(0o600)
            for pmcid in pmcids:
                item = article_result(pmcid)
                article_figures += item["figure_version_records"]
                art_out.write(json.dumps(item, ensure_ascii=False, allow_nan=False) + "\n")
            for row in records(src, limit):
                article = row["provenance"]["article"]
                ref = source_reference(article)
                if article.get("identifier_conflicts"):
                    discovery = base_discovery("identifier_conflict")
                elif ref is None:
                    discovery = base_discovery(article.get("resolution_status", "no_article_identifier"),
                        reason="No verified PubMed/PMC/DOI identifier; source collection and original row are retained.")
                else:
                    mapping = mappings[ref]
                    pmcid = mapping.get("pmcid")
                    discovery = copy.deepcopy(article_result(pmcid)) if pmcid else base_discovery(mapping["status"])
                    row["provenance"] = attach_resolved(row["provenance"], mapping, discovery)
                    if row["provenance"]["article"].get("identifier_conflicts"):
                        discovery = base_discovery("identifier_conflict",
                            reason="Conflicting source/resolver/metadata identifiers; images are not attached.")
                    discovery["identifier_resolution"] = mapping
                row["multimedia"] = discovery
                status_counts[discovery["status"]] += 1
                image_rows += int(discovery["has_image_urls"] is True)
                failed += int(discovery["status"] in {"lookup_failed", "identifier_lookup_failed", "partial",
                    "incomplete_identifier_response", "identifier_conflict"})
                out.write(json.dumps(row, ensure_ascii=False, allow_nan=False) + "\n")
        for temp, final in zip(temps, (dst, article_path)):
            temp.replace(final)
        report = {"records": n, "unique_source_identifiers": len(refs), "unique_pmc_articles": len(pmcids),
                  "records_with_article_image_urls": image_rows, "figure_version_records_unique_articles": article_figures,
                  "status_counts": dict(status_counts), "failed_records": failed,
                  "output": str(dst), "articles_output": str(article_path), "cache": config.cache,
                  "offline": config.offline, "retrieved_at": utc_now(), "elapsed_seconds": time.monotonic() - start,
                  "network": {**client.stats, "image_requests": 0, "image_bytes_downloaded": 0},
                  "scope": "Article-level URL discovery only; patient/figure assignment and reuse permissions are unreviewed.",
                  "coverage_note": "No PMC mapping or no distributed files does not imply no publisher figures."}
        write_json(dst.with_suffix(".enrichment.json"), report)
        return report
    finally:
        await client.close()
        for path in temps:
            path.unlink(missing_ok=True)
