import copy
import json
import sqlite3
from pathlib import Path

import httpx
import pytest

from openpatients2.agreement import cluster_map
from openpatients2.client import APIClient
from openpatients2.data import normalize, prepare, read_jsonl, input_fingerprint
from openpatients2.demo import transport
from openpatients2.pipeline import run_pipeline
from openpatients2.provenance import OPEN_PATIENTS, build_provenance, from_url
from openpatients2.warehouse import build_index, query_cohort


def test_preserve_all_original_fields_and_types_without_mutation():
    raw = {"_id": "pmc-23193287-1", "description": "  α\n literal ",
           "extra": {"number": 3, "flag": False, "missing": None}, "list": [1, "1"]}
    original = copy.deepcopy(raw)
    row = normalize(raw, "commit-sha", dataset_id=OPEN_PATIENTS, row_index=44, source_file="Open-Patients.jsonl")
    assert row["original_row"] == original == raw
    raw["extra"]["number"] = 4
    assert row["original_row"]["extra"]["number"] == 3
    assert row["provenance"]["dataset"]["row_index"] == 44
    assert row["provenance"]["dataset"]["revision"] == "commit-sha"
    assert "row=44" in row["provenance"]["dataset"]["viewer_url"]
    assert normalize(row) == row


def test_numeric_pmc_namespace_stays_unresolved_without_crosswalk():
    row = normalize({"_id": "pmc-8654144-1", "description": "Case."})
    p = row['provenance']
    assert p['article']['pmid'] is None and p['article']['pmcid'] is None
    assert p['article']['resolution_status']=='identifier_namespace_ambiguous'
    assert p['upstream']['patient_index_in_article']==1
    explicit=normalize({"_id": "pmc-8654144-1", "description": "Case.","PMCID":"PMC8654144","PMID":"34925991"})
    assert explicit['provenance']['article']['pmcid']=='PMC8654144'
    assert explicit['provenance']['article']['pmid']=='34925991'


@pytest.mark.parametrize("url,expected", [
    ("https://pubmed.ncbi.nlm.nih.gov/23193287/", {"pmid": "23193287"}),
    ("https://pmc.ncbi.nlm.nih.gov/articles/PMC3531190.2/", {"pmcid": "PMC3531190"}),
    ("https://www.ncbi.nlm.nih.gov/pmc/articles/PMC3531190/", {"pmcid": "PMC3531190"}),
    ("https://doi.org/10.1093/nar/gks1195", {"doi": "10.1093/nar/gks1195"}),
    ("https://evil.example/23193287/", {}),
    ("https://pubmed.ncbi.nlm.nih.gov.evil.example/23193287/", {}),
    ("http://user:password@pubmed.ncbi.nlm.nih.gov/23193287/", {}),
    ("file:///etc/passwd", {}),
])
def test_explicit_identifier_urls(url, expected):
    assert from_url(url) == expected


def test_identifier_conflict_not_overwritten():
    row = normalize({"_id": "pmc-23193287-1", "description": "Case.", "PMID": 9, "url": "https://pubmed.ncbi.nlm.nih.gov/23193287/"})
    assert row["provenance"]["article"]["resolution_status"] == "identifier_conflict"
    assert row["provenance"]["article"]["pmid"] is None


def test_references_inside_case_text_are_not_source_identifiers():
    row = normalize({"_id": "usmle-17", "description": "Read PMID 23193287 or PMC3531190. This is a question."})
    assert row["provenance"]["article"]["pmid"] is None
    assert row["provenance"]["article"]["pmcid"] is None
    assert row["provenance"]["upstream"]["collection"] == "MedQA-USMLE"


def test_original_row_integrity_detects_extra_column_change():
    row = normalize({"_id": "x", "description": "Case.", "extra": 1})
    row["original_row"]["extra"] = 2
    with pytest.raises(ValueError, match="integrity"):
        normalize(row)


def test_source_text_cannot_be_changed_separately():
    row = normalize({"_id": "x", "description": "Case."})
    row["text"] = "Changed."
    with pytest.raises(ValueError, match="differs"):
        normalize(row)


def test_idempotent_multimedia_and_original_preservation():
    row = normalize({"_id": "x", "description": "Case.", "unknown": [None, False]})
    row["multimedia"] = {"status": "not_checked_offline", "image_urls": []}
    assert normalize(normalize(row)) == row


def test_duplicate_source_ids_preserve_every_row_and_order(tmp_path):
    raw = [{"_id": "usmle-1409", "description": "A"},
           {"_id": "usmle-1409", "description": "B"},
           {"_id": "unique", "description": "C"}]
    src = tmp_path / "raw.jsonl"
    src.write_text("\n".join(json.dumps(x) for x in raw))
    output = tmp_path / "prepared.jsonl"
    report = prepare(str(src), str(output), "sha", OPEN_PATIENTS)
    rows = list(read_jsonl(output))
    assert report["records"] == 3
    assert report["duplicate_id_rows_preserved"] == 2
    assert [x["original_row"] for x in rows] == raw
    assert [x["provenance"]["dataset"]["row_index"] for x in rows] == [0, 1, 2]
    assert len({x["record_id"] for x in rows}) == 3
    first = output.read_text()
    prepare(str(src), str(output), "sha", OPEN_PATIENTS)
    assert output.read_text() == first


def test_same_article_cases_share_statistical_and_distillation_cluster():
    a = normalize({"_id": "pmc-23193287-1", "description": "Case A."})
    b = normalize({"_id": "pmc-23193287-2", "description": "Case B."})
    c = normalize({"_id": "pmc-99999999-1", "description": "Different case."})
    groups = cluster_map({x["record_id"]: x for x in [a, b, c]})
    assert groups[a["record_id"]] == groups[b["record_id"]]
    assert groups[c["record_id"]] != groups[b["record_id"]]


def test_original_and_media_provenance_are_in_input_identity():
    row = normalize({"_id": "x", "description": "Case."})
    old = input_fingerprint(row)
    row["multimedia"] = {"image_urls": ["https://example.invalid/a.jpg"]}
    assert input_fingerprint(row) != old


@pytest.mark.asyncio
async def test_full_source_reaches_extraction_index_and_cohort(config, tmp_path):
    # Preserve user-added fields, original note and provenance across every stage.
    raw = json.loads(Path(config.input).read_text())["original_row"]
    raw["external_column"] = {"keep": [1, None, False]}
    row = normalize(raw)
    row["multimedia"] = {"status": "not_checked_offline", "image_urls": [], "has_image_urls": None}
    Path(config.input).write_text(json.dumps(row) + "\n")
    async with httpx.AsyncClient(transport=transport()) as http:
        await run_pipeline(config, client=APIClient(config.api, http))
    patient = next(read_jsonl(Path(config.output) / "patients.jsonl"))
    task = next(read_jsonl(Path(config.output) / "extractions.jsonl"))
    assert patient["source"]["original_row"] == raw
    assert task["source"]["original_row"] == raw
    index = tmp_path / "db.sqlite"
    build_index(str(Path(config.output) / "patients.jsonl"), str(index))
    with sqlite3.connect(index) as db:
        assert db.execute("SELECT count(*) FROM source_articles").fetchone()[0] == 1
    spec = {"source_kinds": [row["source_kind"]], "include_nonclinical_context": True,
            "criteria": [{"id": "cancer", "domain": "conditions", "where": [{"field": "name", "op": "contains", "value": "lung"}]}]}
    output = tmp_path / "cohort.jsonl"
    query_cohort(str(index), spec, str(output))
    cohort = next(read_jsonl(output))
    assert cohort["source"]["original_row"] == raw
    assert cohort["source"]["multimedia"] == row["multimedia"]
