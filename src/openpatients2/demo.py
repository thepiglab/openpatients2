"""Hand-authored SYNTHETIC fixtures and mock HTTP transport, not a model benchmark.

No real clinical records and no real GPU performance measurements are included.
"""
from __future__ import annotations

import copy
import json
from pathlib import Path

import httpx

from .client import APIClient
from .config import APIConfig, PipelineConfig
from .data import normalize, write_json
from .pipeline import run_pipeline
from .schemas import TASK_MODELS


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


async def run_demo(destination: str) -> dict:
    out = Path(destination)
    out.mkdir(parents=True, exist_ok=True)
    source = out / "synthetic-input.jsonl"
    source.write_text(json.dumps(example_record()) + "\n", encoding="utf-8")
    config = PipelineConfig(input=str(source), output=str(out / "extraction"),
        api=APIConfig(endpoints=["http://mock.invalid/v1"], model="hand-authored-fixture", model_id="NOT_A_MODEL", revision="fixture-v1"),
        require_token_profile=False, patient_concurrency=1)
    async with httpx.AsyncClient(transport=transport()) as http:
        report = await run_pipeline(config, client=APIClient(config.api, http))
    report["device_benchmark_status"] = "MOCK_ONLY_NO_GPU_NO_CLINICAL_MODEL"
    report["validated_new_records_per_hour"] = None
    report["wall_seconds"] = None
    write_json(out / "extraction" / "report.json", report)
    from .warehouse import build_index, query_cohort
    database = out / "cohorts.sqlite"
    if not database.exists():
        build_index(str(out / "extraction" / "patients.jsonl"), str(database))
    spec = {"name": "synthetic demonstrator only", "source_kinds": ["synthetic_fixture"], "criteria": [
        {"id": "cancer", "domain": "oncology_tumors", "where": [{"field": "primary_site", "value": "lung"}, {"field": "stage_group", "value": "IV"}]},
        {"id": "marker", "domain": "oncology_biomarkers", "linked_to": "cancer", "where": [{"field": "gene", "value": "EGFR"}, {"field": "interpretation", "value": "positive"}]}]}
    cohort = query_cohort(str(database), spec, str(out / "cohort.jsonl"))
    return {"mode": "hand-authored synthetic fixtures + mock HTTP; NOT inference", "extraction": report, "cohort": cohort}
