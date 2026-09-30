"""Cross-section review flags. Preserve conflicts; never silently choose a diagnosis."""
from __future__ import annotations

from collections import defaultdict


def consistency_findings(sections: dict) -> list[dict]:
    findings = []
    context = sections.get("case_context") or {}
    demographics = (sections.get("demographics") or {}).get("items", [])
    for attribute in ["documented_sex", "sex_assigned_at_birth", "age_at_presentation"]:
        selected = [x for x in demographics if x["attribute"] == attribute and x["subject"] == "index_patient" and x["assertion"] == "present"]
        values = {(x["text_value"].casefold() if x["text_value"] else None, x["numeric_value"], x["unit"]) for x in selected}
        if len(values) > 1:
            findings.append({"code": "conflicting_demographics", "field": attribute,
                             "review_required": True, "explanation": "Multiple documented values; retain and adjudicate index time/subject."})
    statuses = defaultdict(set)
    for item in (sections.get("conditions") or {}).get("items", []):
        key = (item["name"].strip().casefold(), item["subject"], item["temporality"], item["time"]["text"])
        statuses[key].add(item["assertion"])
    for key, assertions in statuses.items():
        if {"present", "absent"}.issubset(assertions):
            findings.append({"code": "conflicting_condition_assertions", "name": key[0], "review_required": True,
                             "explanation": "May reflect genuine longitudinal change; resolve timing before cohort inclusion."})
    if context.get("case_kind") in {"cadaveric_anatomic", "in_vitro", "review_without_case"}:
        actual_medications = [x for x in (sections.get("medications") or {}).get("items", [])
                              if x["subject"] == "index_patient" and x["action"] in {"current", "started", "administered"}]
        if actual_medications:
            findings.append({"code": "nonclinical_context_with_patient_medication", "review_required": True,
                             "explanation": "Check specimen reagents, historical clinical events, and index subject."})
    return findings
