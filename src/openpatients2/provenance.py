"""Deterministic source lineage. Never identify an article from clinical free text.

A bare pmc-N-case identifier does not establish its numeric namespace.
Only explicit identifiers or a source-reviewed row crosswalk establish article links.
"""
from __future__ import annotations

import copy
import hashlib
import json
import re
from typing import Any
from urllib.parse import quote, unquote, urlparse

SOURCE_FORMAT = "openpatients2.source/1"
PROVENANCE_VERSION = "1.1.0"
OPEN_PATIENTS = "ncbi/Open-Patients"
DATASET_URL = "https://huggingface.co/datasets/ncbi/Open-Patients"
PMC_PATIENTS_URL = "https://huggingface.co/datasets/zhengyun21/PMC-Patients"
PMC_UID = re.compile(r"^pmc-(\d+)-(\d+)$", re.IGNORECASE)
PMC_ID = re.compile(r"^PMC([1-9]\d*)(?:\.(\d+))?$", re.IGNORECASE)
DOI = re.compile(r"^10\.\d{4,9}/\S+$", re.IGNORECASE)


def json_digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                    separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def canonical_pmid(value: Any) -> str | None:
    if isinstance(value, bool) or value is None:
        return None
    text = str(value).strip()
    return str(int(text)) if re.fullmatch(r"[0-9]+", text) and int(text) > 0 else None


def canonical_pmcid(value: Any) -> str | None:
    match = PMC_ID.fullmatch(str(value).strip()) if value is not None else None
    return f"PMC{int(match[1])}" if match else None


def canonical_doi(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    text = value.strip()
    if text.lower().startswith(("https://doi.org/", "http://doi.org/", "https://dx.doi.org/")):
        text = unquote(urlparse(text).path.lstrip("/"))
    if text.lower().startswith("doi:"):
        text = text[4:].strip()
    return text if DOI.fullmatch(text) else None


def identifier_urls(article: dict) -> dict:
    return {
        "pubmed_url": f"https://pubmed.ncbi.nlm.nih.gov/{article['pmid']}/" if article.get("pmid") else None,
        "pmc_url": f"https://pmc.ncbi.nlm.nih.gov/articles/{article['pmcid']}/" if article.get("pmcid") else None,
        "doi_url": f"https://doi.org/{quote(article['doi'], safe='/():;') }" if article.get("doi") else None,
    }


def from_url(value: Any) -> dict[str, str]:
    if not isinstance(value, str):
        return {}
    parsed = urlparse(value.strip())
    if parsed.scheme not in {"http", "https"} or parsed.username or parsed.password:
        return {}
    host = (parsed.hostname or "").lower()
    path = unquote(parsed.path)
    if host == "pubmed.ncbi.nlm.nih.gov":
        pmid = canonical_pmid(path.strip("/"))
        return {"pmid": pmid} if pmid else {}
    if host in {"pmc.ncbi.nlm.nih.gov", "www.ncbi.nlm.nih.gov"}:
        match = re.fullmatch(r"/(?:pmc/)?articles/(PMC\d+(?:\.\d+)?)/?", path, re.I)
        if match:
            return {"pmcid": canonical_pmcid(match[1])}
        match = re.fullmatch(r"/pubmed/(\d+)/?", path)
        if match:
            return {"pmid": canonical_pmid(match[1])}
    if host in {"doi.org", "dx.doi.org"}:
        doi = canonical_doi(path.lstrip("/"))
        return {"doi": doi} if doi else {}
    return {}


def build_provenance(original: dict, dataset_id: str, revision: str,
                     row_index: int | None = None, source_file: str | None = None) -> dict:
    """Keep declared IDs and documented UID mappings; conflicts block enrichment."""
    rid = original.get("_id", original.get("record_id", ""))
    evidence: list[dict] = []
    candidates: dict[str, set[str]] = {k: set() for k in ("pmid", "pmcid", "doi")}

    def add(kind, value, method):
        if value:
            candidates[kind].add(value)
            evidence.append({"identifier_type": kind, "value": value, "method": method})

    uid = PMC_UID.fullmatch(rid)
    upstream = {"collection": "unknown", "record_id": rid, "url": None, "declared_license": None}
    if uid:
        # The aggregator IDs and upstream patient_uid conventions are not interchangeable.
        upstream.update(collection="PMC-Patients", record_id=f"{uid[1]}-{uid[2]}",
                        url=PMC_PATIENTS_URL, declared_license="CC-BY-NC-SA-4.0",
                        patient_index_in_article=int(uid[2]),
                        article_group_token=f"{dataset_id}:pmc-{uid[1]}")
    elif match := re.fullmatch(r"trec-(cds|ct)-(\d{4})-(\d+)", rid):
        upstream.update(collection=f"TREC-{match[1].upper()}",
                        url=f"https://www.trec-cds.org/{match[2]}.html")
    elif re.fullmatch(r"usmle-\d+", rid):
        upstream.update(collection="MedQA-USMLE", url="https://github.com/jind11/MedQA")
        upstream["locator_note"] = "ID is the US_qbank question index; no unverified row URL is inferred."

    for key, canonical in (("pmid", canonical_pmid), ("pmcid", canonical_pmcid), ("doi", canonical_doi)):
        for name in (key, key.upper()):
            if name in original:
                add(key, canonical(original[name]), f"explicit original-row field {name}")
    external_urls = []
    for key in ("url", "article_url", "source_url", "source"):
        value = original.get(key)
        if isinstance(value, str):
            parsed = urlparse(value.strip())
            if parsed.scheme in {"https", "http"} and parsed.hostname and not parsed.username and not parsed.password:
                external_urls.append(value.strip())
                for kind, ident in from_url(value).items():
                    add(kind, ident, f"explicit original-row URL field {key}")
    conflicts = [{"identifier_type": k, "values": sorted(v)} for k, v in candidates.items() if len(v) > 1]
    article = {k: next(iter(v)) if len(v) == 1 else None for k, v in candidates.items()}
    article.update(identifier_urls(article))
    article.update(identifier_evidence=evidence, identifier_conflicts=conflicts,
                   resolution_status="identifier_conflict" if conflicts else "not_resolved",
                   external_urls=list(dict.fromkeys(external_urls)))
    if not conflicts and not any(article[k] for k in candidates):
        article["resolution_status"] = "identifier_namespace_ambiguous" if uid else "no_article_identifier"
        if uid:
            article["unresolved_numeric_id"] = uid[1]
            article["identifier_note"] = "Bare source ID may use a different namespace from upstream patient_uid; supply explicit PMID/PMCID/DOI or a reviewed source-row crosswalk. No article is inferred from numbers alone."
    dataset = {"id": dataset_id, "revision": revision, "source_file": source_file,
               "row_index": row_index, "row_index_base": 0,
               "url": DATASET_URL if dataset_id == OPEN_PATIENTS else None,
               "revision_url": f"{DATASET_URL}/tree/{quote(revision, safe='')}" if dataset_id == OPEN_PATIENTS else None,
               "viewer_url": f"{DATASET_URL}/viewer/default/train?row={row_index}" if dataset_id == OPEN_PATIENTS and row_index is not None else None,
               "viewer_note": "Convenience link follows the current viewer; use revision, row ID and source hash for reproducibility.",
               "declared_license": "CC-BY-SA-4.0" if dataset_id == OPEN_PATIENTS else None}
    return {"version": PROVENANCE_VERSION, "dataset": dataset, "upstream": upstream, "article": article,
            "source_url": article["pubmed_url"] or article["pmc_url"] or article["doi_url"] or
                          (external_urls[0] if external_urls else None) or upstream["url"] or dataset["viewer_url"] or dataset["url"],
            "rights_note": "Dataset, upstream component, article, and individual figure terms are separate. No training or commercial reuse permission is inferred."}


def source_reference(article: dict) -> tuple[str, str] | None:
    if article.get("identifier_conflicts"):
        return None
    for kind in ("pmcid", "pmid", "doi"):
        if article.get(kind):
            return kind, article[kind]
    return None


def attach_resolved(provenance: dict, mapping: dict, discovered: dict) -> dict:
    """Add checked identifier crosswalks without overwriting conflicting source IDs."""
    result = copy.deepcopy(provenance)
    article = result["article"]
    for claims in (mapping, discovered.get("identifiers", {})):
        for key in ("pmid", "pmcid", "doi"):
            candidate = claims.get(key)
            if candidate and article.get(key) and candidate.casefold() != article[key].casefold():
                conflict = {"identifier_type": key, "values": [article[key], candidate]}
                if conflict not in article["identifier_conflicts"]:
                    article["identifier_conflicts"].append(conflict)
            elif candidate:
                article[key] = candidate
    article.update(identifier_urls(article))
    article["resolution_status"] = "identifier_conflict" if article["identifier_conflicts"] else mapping.get("status", article["resolution_status"])
    article["resolved_at"] = mapping.get("retrieved_at")
    article["resolver_url"] = mapping.get("resolver_url")
    result["source_url"] = article["pubmed_url"] or article["pmc_url"] or article["doi_url"] or result["source_url"]
    return result
