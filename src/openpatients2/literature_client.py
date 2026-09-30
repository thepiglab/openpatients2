"""Bounded, cached, CPU-only PMC identifier and URL discovery.

Only ID-converter JSON, S3 listings, metadata JSON and article XML can be fetched.
Media/PDF/video payloads are never requested. Publisher HTML is never scraped.
"""
from __future__ import annotations

import asyncio
import email.utils
import hashlib
import json
import os
import re
import sqlite3
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

import httpx
import yaml
from pydantic import BaseModel, ConfigDict, Field

from .pmc_media import (CLOUD, CLOUD_HOSTS, PARSER_VERSION, base_discovery, cloud_url,
                        local_name, media_manifest, metadata_flag, metadata_identifiers, parse_figures, parsed_xml)
from .provenance import canonical_pmcid, canonical_pmid, canonical_doi

ID_CONVERTER = "https://pmc.ncbi.nlm.nih.gov/tools/idconv/api/v1/articles/"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class LiteratureConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    cache: str = "data/cache/literature.sqlite"
    concurrency: int = Field(default=8, ge=1, le=32)
    cloud_requests_per_second: float = Field(default=8, gt=0, le=50)
    identifier_requests_per_second: float = Field(default=2, gt=0, le=3)
    identifier_batch_size: int = Field(default=200, ge=1, le=200)
    contact_email_env: str = "NCBI_EMAIL"
    timeout_seconds: float = Field(default=30, gt=0, le=180)
    retries: int = Field(default=3, ge=0, le=8)
    cache_ttl_days: float = Field(default=30, gt=0)
    negative_cache_ttl_days: float = Field(default=1, gt=0)
    max_metadata_bytes: int = Field(default=4_000_000, ge=1024)
    max_xml_bytes: int = Field(default=20_000_000, ge=1024)
    max_versions: int = Field(default=32, ge=1, le=256)
    max_listing_pages: int = Field(default=20, ge=1, le=200)
    offline: bool = False
    refresh: bool = False

    @classmethod
    def load(cls, path: str | None) -> "LiteratureConfig":
        return cls.model_validate(yaml.safe_load(Path(path).read_text()) or {}) if path else cls()


class LiteratureError(RuntimeError):
    pass


class RateLimiter:
    def __init__(self, rps: float):
        self.interval = 1.0 / rps
        self.lock = asyncio.Lock()
        self.next_time = 0.0

    async def wait(self):
        async with self.lock:
            await asyncio.sleep(max(0.0, self.next_time - time.monotonic()))
            self.next_time = time.monotonic() + self.interval


class MetadataCache:
    """One event-loop owns writes. Store derived manifests, not article/image bodies."""
    def __init__(self, path: str):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path, timeout=30)
        self.db.execute("CREATE TABLE IF NOT EXISTS cache(key TEXT PRIMARY KEY, expires REAL NOT NULL, payload TEXT NOT NULL)")
        self.db.commit()
        Path(path).chmod(0o600)

    def get(self, key: str, allow_stale: bool = False) -> dict | None:
        row = self.db.execute("SELECT expires,payload FROM cache WHERE key=?", (f"{PARSER_VERSION}:{key}",)).fetchone()
        if not row or (row[0] < time.time() and not allow_stale):
            return None
        value = json.loads(row[1])
        if row[0] < time.time():
            value["cache_stale"] = True
        return value

    def put(self, key: str, value: dict, days: float):
        self.db.execute("INSERT OR REPLACE INTO cache VALUES(?,?,?)",
                        (f"{PARSER_VERSION}:{key}", time.time() + days * 86400,
                         json.dumps(value, ensure_ascii=False, allow_nan=False)))
        self.db.commit()

    def close(self):
        self.db.close()


def retry_delay(value: str | None, fallback: float) -> float:
    if value:
        try:
            return min(120.0, max(0.0, float(value)))
        except ValueError:
            try:
                return min(120.0, max(0.0, email.utils.parsedate_to_datetime(value).timestamp() - time.time()))
            except (ValueError, TypeError, OverflowError):
                pass
    return fallback


class LiteratureClient:
    def __init__(self, config: LiteratureConfig, http: httpx.AsyncClient | None = None):
        self.config = config
        self.cache = MetadataCache(config.cache)
        self.http = http or httpx.AsyncClient(timeout=config.timeout_seconds, follow_redirects=False,
            headers={"User-Agent": "OpenPatients2/0.4.0 (article-URL-discovery)", "Accept-Encoding": "gzip, deflate"},
            limits=httpx.Limits(max_connections=config.concurrency, max_keepalive_connections=config.concurrency))
        self.owns_http = http is None
        self.cloud_rate = RateLimiter(config.cloud_requests_per_second)
        self.id_rate = RateLimiter(config.identifier_requests_per_second)
        self.slots = asyncio.Semaphore(config.concurrency)
        self.stats: Counter = Counter()
        self.mapping_results: dict[tuple[str, str], dict] = {}

    async def close(self):
        self.cache.close()
        if self.owns_http:
            await self.http.aclose()

    async def _text(self, url: str, kind: str, params: dict | None = None) -> tuple[str, dict]:
        if self.config.offline:
            raise LiteratureError("Network is disabled in offline mode")
        parsed = urlparse(url)
        if kind == "identifier":
            if url != ID_CONVERTER:
                raise LiteratureError("Only the documented PMC ID converter is allowed")
            limiter, cap = self.id_rate, self.config.max_metadata_bytes
        else:
            cloud_url(url)  # Reject arbitrary hosts, credentials, and path traversal.
            if kind == "listing" and not (parsed.path in {"", "/"} and params and params.get("list-type") == "2"):
                raise LiteratureError("Only ListObjectsV2 listings are allowed")
            if kind == "metadata" and not parsed.path.endswith(".json"):
                raise LiteratureError("Metadata requests must target JSON, never media")
            if kind == "xml" and not parsed.path.endswith(".xml"):
                raise LiteratureError("Article requests must target XML, never media")
            if kind not in {"listing", "metadata", "xml"}:
                raise LiteratureError("Unsupported download kind; media downloads are forbidden")
            limiter = self.cloud_rate
            cap = self.config.max_xml_bytes if kind == "xml" else self.config.max_metadata_bytes
        last_error = None
        for attempt in range(self.config.retries + 1):
            try:
                async with self.slots:
                    await limiter.wait()
                    self.stats[f"{kind}_requests"] += 1
                    async with self.http.stream("GET", url, params=params, timeout=self.config.timeout_seconds,
                                                follow_redirects=False) as response:
                        if response.status_code == 429 or response.status_code >= 500:
                            delay = retry_delay(response.headers.get("Retry-After"), 2.0 ** attempt)
                            error = LiteratureError(f"Temporary HTTP {response.status_code} from {parsed.hostname}")
                        else:
                            if response.status_code != 200:
                                raise LiteratureError(f"HTTP {response.status_code} from {parsed.hostname}; not evidence of no figures")
                            content_type = response.headers.get("content-type", "").lower()
                            if any(x in content_type for x in ("image/", "video/", "audio/", "application/pdf", "application/zip", "text/html")):
                                raise LiteratureError(f"Unexpected response type {content_type}; body not downloaded")
                            length = response.headers.get("content-length")
                            if length and int(length) > cap:
                                raise LiteratureError("Metadata/XML response exceeds configured size cap")
                            chunks, size = [], 0
                            async for chunk in response.aiter_bytes():
                                size += len(chunk)
                                self.stats["metadata_xml_bytes_received"] += len(chunk)
                                if size > cap:
                                    raise LiteratureError("Metadata/XML response exceeds configured size cap")
                                chunks.append(chunk)
                            data = b"".join(chunks)
                            return data.decode("utf-8-sig"), {"url": url, "retrieved_at": utc_now(),
                                "etag": response.headers.get("etag"), "last_modified": response.headers.get("last-modified"),
                                "sha256": hashlib.sha256(data).hexdigest()}
                last_error = error
                if attempt < self.config.retries:
                    self.stats["retries"] += 1
                    await asyncio.sleep(delay)
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                last_error = LiteratureError(f"{type(exc).__name__} contacting {parsed.hostname}")
                if attempt < self.config.retries:
                    self.stats["retries"] += 1
                    await asyncio.sleep(2.0 ** attempt)
        raise last_error or LiteratureError("Metadata request failed")

    async def resolve_many(self, refs: set[tuple[str, str]]) -> dict[tuple[str, str], dict]:
        pending: dict[str, list[str]] = {"pmid": [], "doi": []}
        for kind, value in sorted(refs):
            if kind == "pmcid":
                self.mapping_results[(kind, value)] = {"pmcid": value, "status": "explicit_pmcid", "retrieved_at": None}
                continue
            cached = None if self.config.refresh and not self.config.offline else self.cache.get(
                f"mapping:{kind}:{value}", self.config.offline)
            if cached:
                self.mapping_results[(kind, value)] = cached
                self.stats["identifier_cache_hits"] += 1
            elif self.config.offline:
                self.mapping_results[(kind, value)] = {kind: value, "status": "not_checked_offline"}
            else:
                pending[kind].append(value)
        if any(pending.values()):
            contact = os.getenv(self.config.contact_email_env, "").strip()
            if not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", contact):
                raise ValueError(f"Set {self.config.contact_email_env} to a valid maintainer email for PMC API requests")
        for kind, values in pending.items():
            for start in range(0, len(values), self.config.identifier_batch_size):
                ids = values[start:start + self.config.identifier_batch_size]
                try:
                    text, info = await self._text(ID_CONVERTER, "identifier", {
                        "ids": ",".join(ids), "idtype": kind, "format": "json", "versions": "yes",
                        "tool": "openpatients2", "email": os.getenv(self.config.contact_email_env)})
                    payload = json.loads(text)
                    if payload.get("status") != "ok" or not isinstance(payload.get("records"), list):
                        raise LiteratureError("Unexpected ID-converter response")
                    found = {}
                    for record in payload["records"]:
                        requested = str(record.get("requested-id", ""))
                        if requested not in ids:
                            # Fail closed; never align by response order.
                            continue
                        pmcid = canonical_pmcid(record.get("pmcid"))
                        value = {"pmcid": pmcid, "pmid": canonical_pmid(record.get("pmid")),
                                 "doi": canonical_doi(record.get("doi")), "status": "resolved" if pmcid else "no_pmc_mapping",
                                 "live": record.get("live"), "release_date": record.get("release-date"),
                                 "versions": record.get("versions", []), "retrieved_at": info["retrieved_at"],
                                 "resolver_url": ID_CONVERTER, "response_sha256": info["sha256"],
                                 "message": record.get("errmsg") or record.get("error")}
                        if kind == "pmid" and value["pmid"] and value["pmid"] != requested:
                            value.update(status="identifier_conflict", pmcid=None)
                        value[kind] = value.get(kind) or requested
                        found[requested] = value
                    for ident in ids:
                        value = found.get(ident, {kind: ident, "status": "incomplete_identifier_response", "retrieved_at": info["retrieved_at"]})
                        self.mapping_results[(kind, ident)] = value
                        if value["status"] in {"resolved", "no_pmc_mapping"}:
                            self.cache.put(f"mapping:{kind}:{ident}", value,
                                self.config.cache_ttl_days if value["pmcid"] else self.config.negative_cache_ttl_days)
                except (LiteratureError, ValueError, UnicodeError) as exc:
                    for ident in ids:
                        self.mapping_results[(kind, ident)] = {kind: ident, "status": "identifier_lookup_failed",
                                                               "error": str(exc), "retrieved_at": utc_now()}
        return self.mapping_results

    async def _version_objects(self, pmcid: str) -> list[tuple[int, str]]:
        prefix = f"metadata/{pmcid}."
        params = {"list-type": "2", "prefix": prefix, "max-keys": "1000"}
        found, tokens = {}, set()
        for _ in range(self.config.max_listing_pages):
            text, _ = await self._text(CLOUD + "/", "listing", params)
            root = parsed_xml(text)
            if local_name(root.tag) != "ListBucketResult":
                raise LiteratureError("Unexpected S3 listing response")
            for item in root:
                if local_name(item.tag) == "Contents":
                    key = next((x.text for x in item if local_name(x.tag) == "Key"), "")
                    match = re.fullmatch(re.escape(prefix) + r"([1-9]\d*)\.json", key)
                    if match:
                        found[int(match[1])] = f"{CLOUD}/{key}"
            if len(found) > self.config.max_versions:
                raise LiteratureError("Article exceeds configured max_versions; refusing silent truncation")
            truncated = next((x.text for x in root if local_name(x.tag) == "IsTruncated"), "false")
            if truncated.lower() != "true":
                return sorted(found.items())
            token = next((x.text for x in root if local_name(x.tag) == "NextContinuationToken"), None)
            if not token or token in tokens:
                raise LiteratureError("Missing/repeated S3 continuation token")
            tokens.add(token)
            params["continuation-token"] = token
        raise LiteratureError("Exceeded S3 listing page cap; refusing silent truncation")

    def cached_article(self, pmcid: str) -> dict | None:
        return self.cache.get(f"article:{pmcid}", allow_stale=self.config.offline)

    async def discover_article(self, pmcid: str) -> dict:
        if canonical_pmcid(pmcid) != pmcid:
            raise ValueError("Use an unversioned canonical PMCID")
        cached = None if self.config.refresh and not self.config.offline else self.cached_article(pmcid)
        if cached:
            self.stats["article_cache_hits"] += 1
            return cached
        if self.config.offline:
            return base_discovery("not_checked_offline", pmcid=pmcid)
        result = base_discovery("lookup_failed", pmcid=pmcid, retrieved_at=utc_now(), errors=[])
        try:
            objects = await self._version_objects(pmcid)
            if not objects:
                result["status"] = "no_distributed_article_files"
                self.cache.put(f"article:{pmcid}", result, self.config.negative_cache_ttl_days)
                return result
            identifiers = {"pmcid": pmcid, "pmid": None, "doi": None}
            for version, url in objects:
                item = {"version": version, "metadata_url": url, "status": "metadata_failed", "figures": [], "media": []}
                result["versions"].append(item)
                try:
                    text, info = await self._text(url, "metadata")
                    metadata = json.loads(text)
                    ids = metadata_identifiers(metadata, pmcid, version)
                    for kind, ident in ids.items():
                        if ident and identifiers[kind] and ident.casefold() != identifiers[kind].casefold():
                            raise LiteratureError("Conflicting identifiers across article versions")
                        identifiers[kind] = ident or identifiers[kind]
                    media = media_manifest(metadata)
                    item.update(status="metadata_only", metadata_retrieval=info, identifiers=ids, media=media,
                                license_code=metadata.get("license_code"), is_pmc_openaccess=metadata_flag(metadata.get("is_pmc_openaccess")),
                                is_manuscript=metadata_flag(metadata.get("is_manuscript")), is_retracted=metadata_flag(metadata.get("is_retracted")),
                                raw_metadata_flags={k: metadata.get(k) for k in ("is_pmc_openaccess", "is_manuscript", "is_retracted")},
                                training_approval="not_reviewed")
                    raw_xml_url = metadata.get("xml_url")
                    if not raw_xml_url:
                        item["status"] = "xml_not_distributed"
                        continue
                    xml_url = cloud_url(raw_xml_url)
                    # IDs must match the explicitly listed version. Even an allowlisted
                    # bucket must not be used to substitute another article's XML.
                    if urlparse(xml_url).path != f"/{pmcid}.{version}/{pmcid}.{version}.xml":
                        raise LiteratureError("XML path does not match the requested article version")
                    item["source_xml_url"] = xml_url
                    xml, xml_info = await self._text(xml_url, "xml")
                    extracted = await asyncio.to_thread(parse_figures, xml, pmcid, version, media, xml_url, ids["pmid"])
                    item.update(extracted, xml_retrieval=xml_info, status="ok")
                except Exception as exc:
                    # Parsing/transport failures belong to the manifest, not an empty
                    # successful figure list. Cancellation is a BaseException and propagates.
                    item["status"] = "xml_or_parse_failed" if item["status"] != "metadata_failed" else "metadata_failed"
                    item["error"] = f"{type(exc).__name__}: {exc}"
                    result["errors"].append({"version": version, "error": item["error"]})
            result["identifiers"] = identifiers
            versions = result["versions"]
            figures = [f for v in versions for f in v["figures"]]
            image_urls = sorted({a["url"] for v in versions for a in v["media"] if a["kind"] == "image"})
            all_xml = all(v["status"] == "ok" for v in versions)
            all_metadata = all(v.get("metadata_retrieval") for v in versions)
            result.update(image_urls=image_urls, figure_version_records=len(figures),
                          has_figures=True if figures else False if all_xml else None,
                          has_image_urls=True if image_urls else False if all_metadata else None)
            if result["errors"]:
                result["status"] = "partial"
            elif figures:
                result["status"] = "figures_with_image_urls" if any(f["image_urls"] for f in figures) else "figures_without_image_urls"
            elif image_urls:
                result["status"] = "image_assets_without_figure_markup"
            elif all_xml:
                result["status"] = "no_figures_found"
            else:
                result["status"] = "figure_availability_unknown"
            self.cache.put(f"article:{pmcid}", result, self.config.cache_ttl_days if not result["errors"] else self.config.negative_cache_ttl_days)
            self.stats["articles_discovered"] += 1
        except Exception as exc:
            result["errors"].append({"error": f"{type(exc).__name__}: {exc}"})
            # Network failures are not negative cache entries.
        return result
