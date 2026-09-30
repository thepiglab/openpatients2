"""Synthetic HTTP fixtures: executable discovery demo, never a live PMC benchmark."""
from __future__ import annotations

import json
import os
from pathlib import Path
from unittest.mock import patch

import httpx

from .data import prepare, write_json
from .literature_client import LiteratureConfig
from .pmc_media import CLOUD
from .source_enrichment import enrich_sources

PMID = "90000001"
PMCID = "PMC99990001"


def fixture_metadata(pmcid: str = PMCID, pmid: str = PMID, version: int = 1, images: bool = True) -> dict:
    root = f"s3://pmc-oa-opendata/{pmcid}.{version}"
    return {"pmcid": pmcid, "pmid": pmid, "version": version, "doi": "10.9999/synthetic-demo",
            "license_code": "CC BY", "is_pmc_openaccess": "yes", "is_manuscript": "no", "is_retracted": "no",
            "xml_url": f"{root}/{pmcid}.{version}.xml?md5=synthetic-fixture-not-a-real-digest",
            "media_urls": [f"{root}/case-f1.jpg?md5=synthetic-fixture-not-a-real-digest",
                           f"{root}/case-supplement.xlsx?md5=synthetic-fixture-not-a-real-digest"] if images else []}


def fixture_xml(pmcid: str = PMCID, pmid: str = PMID, figures: bool = True) -> str:
    body = '''<fig id="F1"><label>Figure 1</label><caption><p>SYNTHETIC EXAMPLE: case A (panel a), case B (panel b). Not real patient imagery.</p></caption><graphic xlink:href="case-f1"/></fig>''' if figures else "<p>SYNTHETIC EXAMPLE without figure markup.</p>"
    return f'''<?xml version="1.0" encoding="UTF-8"?><article xmlns:xlink="http://www.w3.org/1999/xlink"><front><article-meta><article-id pub-id-type="pmc">{pmcid}</article-id><article-id pub-id-type="pmid">{pmid}</article-id><permissions><license>Fixture license text; not permission for any real image.</license></permissions></article-meta></front><body>{body}</body></article>'''


def fixture_transport(calls: list | None = None) -> httpx.MockTransport:
    calls = calls if calls is not None else []
    async def handler(request: httpx.Request):
        calls.append(str(request.url))
        if request.url.host == "pmc.ncbi.nlm.nih.gov":
            values = []
            for ident in request.url.params["ids"].split(","):
                if ident in {"90000001", "90000002", "90000003"}:
                    values.append({"requested-id": ident, "pmid": ident,
                                   "pmcid": f"PMC9999{int(ident) - 90000000:04d}"})
                else:
                    values.append({"requested-id": ident, "errmsg": "fixture: no PMC mapping"})
            return httpx.Response(200, json={"status": "ok", "records": values})
        if request.url.path == "/":
            prefix = request.url.params["prefix"]
            key = prefix + "1.json"
            return httpx.Response(200, text=f'<ListBucketResult xmlns="http://s3.amazonaws.com/doc/2006-03-01/"><IsTruncated>false</IsTruncated><Contents><Key>{key}</Key></Contents></ListBucketResult>', headers={"content-type": "application/xml"})
        if request.url.path.endswith(".json"):
            pmcid = Path(request.url.path).name.split(".")[0]
            pmid = str(90000000 + int(pmcid[-4:]))
            return httpx.Response(200, json=fixture_metadata(pmcid, pmid, images=pmid == "90000001"))
        if request.url.path.endswith(".xml"):
            pmcid = Path(request.url.path).name.split(".")[0]
            pmid = str(90000000 + int(pmcid[-4:]))
            return httpx.Response(200, text=fixture_xml(pmcid, pmid, figures=pmid != "90000002"),
                                  headers={"content-type": "application/xml"})
        raise AssertionError(f"No image/media body may be requested: {request.url}")
    return httpx.MockTransport(handler)


async def run_sources_demo(directory: str) -> dict:
    root = Path(directory)
    root.mkdir(parents=True, exist_ok=True)
    rows = [
        {"_id": "pmc-90000001-1", "description": "SYNTHETIC FIXTURE: case A.", "extra_column": {"preserve": True}},
        {"_id": "pmc-90000001-2", "description": "SYNTHETIC FIXTURE: case B, from the same mock article."},
        {"_id": "pmc-90000002-1", "description": "SYNTHETIC FIXTURE: mock article has no figures."},
        {"_id": "pmc-90000003-1", "description": "SYNTHETIC FIXTURE: figure without a distributed image."},
        {"_id": "pmc-90000004-1", "description": "SYNTHETIC FIXTURE: no PMC identifier conversion."},
        {"_id": "usmle-1", "description": "SYNTHETIC FIXTURE: educational example."},
        {"_id": "usmle-1", "description": "SYNTHETIC FIXTURE: duplicate identifier, different original row."},
    ]
    for row in rows:
        if row['_id'].startswith('pmc-'):
            row['pmid'] = row['_id'].split('-')[1]  # explicit mock fixture identifier, not an inferred production convention
    raw, prepared, enriched = root / "original.jsonl", root / "prepared.jsonl", root / "enriched.jsonl"
    raw.write_text("".join(json.dumps(x) + "\n" for x in rows))
    preparation = prepare(str(raw), str(prepared), "synthetic-fixture-v1", "local-synthetic-fixtures")
    cfg = LiteratureConfig(cache=str(root / "cache.sqlite"), refresh=True, cloud_requests_per_second=50)
    calls = []
    with patch.dict(os.environ, {"NCBI_EMAIL": "synthetic-fixture@example.invalid"}):
        async with httpx.AsyncClient(transport=fixture_transport(calls)) as http:
            result = await enrich_sources(str(prepared), str(enriched), cfg, http=http)
    result.update(validation_environment="mock HTTP only; not a live PMC test", preparation=preparation,
                  mock_request_count=len(calls))
    write_json(root / "demo-report.json", result)
    return result
