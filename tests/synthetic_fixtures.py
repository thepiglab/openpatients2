"""Hand-authored SYNTHETIC test fixtures and mock HTTP transports (no real clinical records).

Moved here from the archived `openpatients2.demo` and `openpatients2.sources_demo` modules
(see the 2026-10-10 archive, git c66587d) so tests of active code keep their fixtures.
"""
from __future__ import annotations

import json
from pathlib import Path

import httpx

from openpatients2.data import normalize
from openpatients2.schemas import TASK_MODELS


SOURCE = (
    "A 67-year-old man presented with cough. Biopsy confirmed lung adenocarcinoma. "
    "The lung cancer was documented as stage IV with liver metastasis. "
    "Tumor testing was positive for EGFR exon 19 deletion. "
    "Osimertinib 80 mg orally daily was started for the lung cancer. "
    "He reported no known drug allergies. His mother had breast cancer. "
    "He previously smoked cigarettes and stopped 10 years ago. "
    "At three-month follow-up, the cough had improved."
)


def common(quote: str, subject: str = "index_patient", assertion: str = "present", temporality: str = "current") -> dict:
    return {"subject": subject, "assertion": assertion, "temporality": temporality,
            "time": {"text": None, "relation": "unknown", "anchor": None, "date_iso": None},
            "evidence": [{"quote": quote, "source_section": None}]}


def empty_section(task: str) -> dict:
    base = {"coverage": "complete", "limitations": [],
            "documentation_status": "not_documented", "documentation_evidence": []}
    if task == "oncology":
        return {**base, "tumors": [], "biomarkers": [], "treatments": []}
    if task == "case_context":
        return {**base, "case_kind": "unknown", "index_subject_description": None, "species": "unknown",
                "multiple_index_patients": None, "subject_state": "unknown", "care_setting": None,
                "chief_complaint": None, "evidence": []}
    return {**base, "items": []}


def example_record() -> dict:
    # Explicit provenance marks this as test data. Tests can opt into source gates,
    # but production default cohorts never silently include synthetic fixtures.
    return normalize({"_id": "synthetic-test-001", "description": SOURCE, "source_kind": "synthetic_fixture", "dataset_revision": "fixture-v1"})


def fixture_sections() -> dict:
    sections = {task: empty_section(task) for task in TASK_MODELS}
    sections["case_context"].update({"case_kind": "clinical_case", "index_subject_description": "67-year-old man",
        "species": "human", "multiple_index_patients": False, "subject_state": "living_at_presentation",
        "chief_complaint": "cough", "evidence": [{"quote": "A 67-year-old man presented with cough.", "source_section": None}]})
    sections["demographics"]["items"] = [
        {**common("A 67-year-old man presented with cough."), "attribute": "age_at_presentation", "text_value": "67-year-old", "numeric_value": 67.0, "unit": "years"},
        {**common("A 67-year-old man presented with cough."), "attribute": "documented_sex", "text_value": "male", "numeric_value": None, "unit": None}]
    sections["conditions"]["items"] = [{**common("Biopsy confirmed lung adenocarcinoma."),
        "name": "lung adenocarcinoma", "clinical_status": "active", "role": "primary_diagnosis", "body_site": "lung",
        "laterality": "unknown", "severity": None, "diagnostic_basis": "biopsy"}]
    sections["symptoms_function"]["items"] = [{**common("A 67-year-old man presented with cough."),
        "name": "cough", "kind": "symptom", "text_value": "cough", "numeric_value": None, "unit": None,
        "body_site": None, "severity": None, "activity_context": None}]
    sections["procedures_devices"]["items"] = [{**common("Biopsy confirmed lung adenocarcinoma."),
        "name": "biopsy", "kind": "procedure", "action": "completed", "body_site": "lung", "laterality": "unknown",
        "indication": None, "finding": "adenocarcinoma", "complication": None}]
    sections["observations"]["items"] = [{**common("Biopsy confirmed lung adenocarcinoma."),
        "name": "biopsy pathology", "kind": "pathology", "status": "resulted", "text_value": "lung adenocarcinoma",
        "numeric_value": None, "comparator": "unknown", "unit": None, "flag": "unknown",
        "reference_range_text": None, "specimen": None, "method": None, "body_site": "lung"}]
    sections["oncology"]["tumors"] = [{**common("The lung cancer was documented as stage IV with liver metastasis."),
        "tumor_ref": "t1", "name": "lung adenocarcinoma", "primary_site": "lung", "histology": "adenocarcinoma",
        "grade": None, "laterality": "unknown", "disease_extent": "distant_metastatic", "metastatic_sites": ["liver"],
        "stage_group": "IV", "stage_system": None, "stage_edition": None, "stage_context": "unknown",
        "t_category": None, "n_category": None, "m_category": None, "disease_status": "active"}]
    sections["oncology"]["tumors"][0]["evidence"].append({"quote": "Biopsy confirmed lung adenocarcinoma.", "source_section": None})
    sections["oncology"]["biomarkers"] = [{**common("Tumor testing was positive for EGFR exon 19 deletion."),
        "tumor_ref": "t1", "name": "EGFR exon 19 deletion", "gene": "EGFR", "alteration": "exon 19 deletion",
        "origin": "unknown", "interpretation": "positive", "text_value": "positive", "numeric_value": None,
        "unit": None, "assay": None, "specimen": "tumor"}]
    sections["oncology"]["treatments"] = [{**common("Osimertinib 80 mg orally daily was started for the lung cancer."),
        "tumor_ref": "t1", "name": "Osimertinib", "modality": "systemic", "action": "given", "line_of_therapy": None,
        "intent": "unknown", "setting": "unknown", "response": None}]
    sections["medications"]["items"] = [{**common("Osimertinib 80 mg orally daily was started for the lung cancer."),
        "name": "Osimertinib", "action": "started", "ingredient_as_documented": None, "dose_text": "80 mg",
        "dose_value": 80.0, "dose_unit": "mg", "route": "oral", "frequency": "daily", "duration_text": None,
        "indication": "lung cancer", "regimen": None}]
    sections["allergies"]["items"] = [{**common("He reported no known drug allergies.", assertion="absent"),
        "substance": "known drug allergies", "category": "drug", "reaction": None, "reaction_type": "unknown", "severity": None}]
    sections["family_genetics"]["items"] = [{**common("His mother had breast cancer.", subject="family_member", temporality="historical"),
        "name": "breast cancer", "kind": "family_condition", "relative": "mother", "age_at_diagnosis_text": None,
        "gene": None, "variant": None, "origin": "unknown", "interpretation": None, "inheritance": None}]
    sections["social_exposures"]["items"] = [{**common("He previously smoked cigarettes and stopped 10 years ago.", temporality="historical"),
        "domain": "tobacco", "name": "cigarettes", "status": "former", "amount_text": None,
        "frequency": None, "duration_text": None, "pack_years": None}]
    sections["outcomes"]["items"] = [{**common("At three-month follow-up, the cough had improved."),
        "event": "improvement", "text_value": "cough improved", "related_condition": "cough", "disposition": None,
        "follow_up_duration_text": "three-month", "cause_as_documented": None}]
    for task, section in sections.items():
        if task == "case_context" or any(section.get(key) for key in ["items", "tumors", "biomarkers", "treatments"]):
            section["documentation_status"] = "documented"
    sections["conditions"]["items"][0]["verification_status"] = "confirmed"
    sections["allergies"]["items"][0]["scope"] = "drug_allergies"
    sections["observations"]["items"][0]["result_absent_reason"] = None
    return sections


def transport(sections: dict | None = None, calls: list | None = None) -> httpx.MockTransport:
    sections = sections or fixture_sections()
    calls = calls if calls is not None else []
    async def handle(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        task = body["messages"][-1]["content"].split("EXTRACTION_TASK: ")[1].split("\n")[0]
        calls.append({"task": task, "endpoint": str(request.url), "body": body})
        content = json.dumps(sections[task])
        # Deliberately absent cache/usage counts. Fake traffic must not produce
        # plausible-looking hardware tokens/s or invented cache hit percentages.
        chunks = [
            {"choices": [{"index": 0, "delta": {"reasoning_content": "mock"}, "finish_reason": None}]},
            {"choices": [{"index": 0, "delta": {"content": content}, "finish_reason": None}]},
            {"choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]}]
        return httpx.Response(200, text="".join("data: " + json.dumps(x) + "\n\n" for x in chunks) + "data: [DONE]\n\n",
                              headers={"content-type": "text/event-stream"})
    return httpx.MockTransport(handle)


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
