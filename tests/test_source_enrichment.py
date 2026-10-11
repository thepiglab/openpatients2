import copy
import json
from pathlib import Path
from unittest.mock import AsyncMock

import httpx
import pytest

from openpatients2.data import prepare, read_jsonl
from openpatients2.literature_client import (LiteratureClient, LiteratureConfig, LiteratureError,
                                            MetadataCache, RateLimiter, ID_CONVERTER)
from openpatients2.pmc_media import CLOUD, cloud_url
from synthetic_fixtures import PMCID, PMID, fixture_metadata, fixture_transport, fixture_xml


@pytest.fixture(autouse=True)
def fast_and_identified(monkeypatch):
    monkeypatch.setattr(RateLimiter, "wait", AsyncMock())
    monkeypatch.setenv("NCBI_EMAIL", "test@example.invalid")
    monkeypatch.delenv("SLURM_JOB_GPUS", raising=False)
    monkeypatch.delenv("SLURM_STEP_GPUS", raising=False)


@pytest.fixture
def cfg(tmp_path):
    return LiteratureConfig(cache=str(tmp_path / "cache.sqlite"), retries=0)


def prepared(tmp_path, rows=None):
    rows = rows or [{"_id": f"pmc-{PMID}-1", "pmid": PMID, "description": "SYNTHETIC FIXTURE A."},
                    {"_id": f"pmc-{PMID}-2", "pmid": PMID, "description": "SYNTHETIC FIXTURE B."}]
    src, dest = tmp_path / "raw.jsonl", tmp_path / "prepared.jsonl"
    src.write_text("\n".join(json.dumps(r) for r in rows))
    prepare(str(src), str(dest), "fixture-revision", "ncbi/Open-Patients")
    return dest


@pytest.mark.asyncio
async def test_batch_identifier_results_matched_by_id_not_order(tmp_path, cfg):
    seen = []
    async def handler(request):
        seen.append(request)
        return httpx.Response(200, json={"status": "ok", "records": [
            {"requested-id": "2", "pmid": 2, "pmcid": "PMC222"},
            {"requested-id": "1", "pmid": 1, "pmcid": "PMC111"}]})
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client = LiteratureClient(cfg, http)
        try:
            values = await client.resolve_many({("pmid", "1"), ("pmid", "2")})
            assert values[("pmid", "1")]["pmcid"] == "PMC111"
            assert values[("pmid", "2")]["pmcid"] == "PMC222"
            assert len(seen) == 1
            assert seen[0].url.params["idtype"] == "pmid"
        finally:
            await client.close()


@pytest.mark.asyncio
async def test_resolver_missing_record_is_not_no_pmc_match(cfg):
    async def handler(request):
        return httpx.Response(200, json={"status": "ok", "records": []})
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client = LiteratureClient(cfg, http)
        try:
            result = await client.resolve_many({("pmid", "1")})
            assert result[("pmid", "1")]["status"] == "incomplete_identifier_response"
        finally:
            await client.close()


@pytest.mark.asyncio
async def test_homogeneous_batches_and_max_batch_size(cfg):
    cfg.identifier_batch_size = 2
    calls = []
    async def handler(request):
        calls.append(request)
        return httpx.Response(200, json={"status": "ok", "records": [
            {"requested-id": i, "errmsg": "not in PMC"} for i in request.url.params["ids"].split(",")]})
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client = LiteratureClient(cfg, http)
        try:
            await client.resolve_many({("pmid", "1"), ("pmid", "2"), ("pmid", "3"), ("doi", "10.9999/test")})
            assert len(calls) == 3
            assert all(len(r.url.params["ids"].split(",")) <= 2 for r in calls)
            assert {r.url.params["idtype"] for r in calls} == {"pmid", "doi"}
        finally:
            await client.close()


@pytest.mark.asyncio
async def test_empty_listing_means_unavailable_not_no_figures(cfg):
    async def handler(request):
        return httpx.Response(200, text='<ListBucketResult><IsTruncated>false</IsTruncated></ListBucketResult>')
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client = LiteratureClient(cfg, http)
        try:
            result = await client.discover_article(PMCID)
            assert result["status"] == "no_distributed_article_files"
            assert result["has_figures"] is None
        finally:
            await client.close()


@pytest.mark.asyncio
async def test_only_version_two_exists_never_assume_version_one(cfg):
    calls = []
    async def handler(request):
        calls.append(str(request.url))
        if request.url.path == "/":
            return httpx.Response(200, text=f'<ListBucketResult><IsTruncated>false</IsTruncated><Contents><Key>metadata/{PMCID}.2.json</Key></Contents></ListBucketResult>')
        if request.url.path.endswith(".json"):
            return httpx.Response(200, json=fixture_metadata(version=2))
        return httpx.Response(200, text=fixture_xml())
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client = LiteratureClient(cfg, http)
        try:
            result = await client.discover_article(PMCID)
            assert [x["version"] for x in result["versions"]] == [2]
            assert result["status"] == "figures_with_image_urls"
            assert not any(f"{PMCID}.1/" in u for u in calls)
        finally:
            await client.close()


@pytest.mark.asyncio
async def test_listing_pagination(cfg):
    async def handler(request):
        token = request.url.params.get("continuation-token")
        if not token:
            return httpx.Response(200, text=f'<ListBucketResult><IsTruncated>true</IsTruncated><NextContinuationToken>next</NextContinuationToken><Contents><Key>metadata/{PMCID}.1.json</Key></Contents></ListBucketResult>')
        assert token == "next"
        return httpx.Response(200, text=f'<ListBucketResult><IsTruncated>false</IsTruncated><Contents><Key>metadata/{PMCID}.2.json</Key></Contents></ListBucketResult>')
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client = LiteratureClient(cfg, http)
        try:
            assert [v for v, _ in await client._version_objects(PMCID)] == [1, 2]
        finally:
            await client.close()


@pytest.mark.asyncio
async def test_broken_xml_is_partial_not_successful_empty(cfg):
    base = fixture_transport()
    async def handler(request):
        if request.url.path.endswith(".xml"):
            return httpx.Response(200, text="<broken")
        return await base.handle_async_request(request)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client = LiteratureClient(cfg, http)
        try:
            result = await client.discover_article(PMCID)
            assert result["status"] == "partial"
            assert result["has_figures"] is None
            # Metadata already proves image assets are listed, even without captions.
            assert result["has_image_urls"] is True
        finally:
            await client.close()


@pytest.mark.asyncio
async def test_response_size_cap_and_mime_block(cfg):
    cfg.max_metadata_bytes = 1024
    async def handler(request):
        return httpx.Response(200, content=b"x" * 2048, headers={"content-type": "application/json"})
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client = LiteratureClient(cfg, http)
        try:
            with pytest.raises(LiteratureError, match="size cap"):
                await client._text(CLOUD + "/metadata/a.json", "metadata")
            with pytest.raises((LiteratureError, ValueError)):
                await client._text(CLOUD + "/a.jpg", "metadata")
            with pytest.raises((LiteratureError, ValueError)):
                await client._text("https://example.invalid/x.xml", "xml")
        finally:
            await client.close()


@pytest.mark.asyncio
async def test_image_mime_even_on_json_path_is_rejected(cfg):
    async def handler(request):
        return httpx.Response(200, content=b"not downloaded", headers={"content-type": "image/png"})
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client = LiteratureClient(cfg, http)
        try:
            with pytest.raises(LiteratureError, match="body not downloaded"):
                await client._text(CLOUD + "/metadata/a.json", "metadata")
            assert client.stats["metadata_xml_bytes_received"] == 0
        finally:
            await client.close()


@pytest.mark.asyncio
async def test_retry_after_429(cfg, monkeypatch):
    cfg.retries = 1
    sleeps = AsyncMock()
    monkeypatch.setattr("openpatients2.literature_client.asyncio.sleep", sleeps)
    attempts = []
    async def handler(request):
        attempts.append(request)
        if len(attempts) == 1:
            return httpx.Response(429, headers={"retry-after": "2"})
        return httpx.Response(200, json={"status": "ok", "records": []})
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client = LiteratureClient(cfg, http)
        try:
            await client.resolve_many({("pmid", "1")})
            assert len(attempts) == 2
            sleeps.assert_awaited_with(2.0)
            assert client.stats["retries"] == 1
        finally:
            await client.close()


def test_cache_ttl_and_explicit_stale_offline_marker(tmp_path):
    cache = MetadataCache(str(tmp_path / "cache.sqlite"))
    try:
        cache.put("x", {"status": "ok"}, 1)
        cache.db.execute("UPDATE cache SET expires=0")
        cache.db.commit()
        assert cache.get("x") is None
        assert cache.get("x", allow_stale=True)["cache_stale"] is True
    finally:
        cache.close()
