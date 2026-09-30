import copy
import json

import jsonschema
import pytest

from openpatients2.schemas import TASK_MODELS, wire_schema, field_guide
from openpatients2.validation import validate, all_spans
from openpatients2.consistency import consistency_findings
from openpatients2.demo import SOURCE, common


@pytest.mark.parametrize("task", list(TASK_MODELS))
def test_each_fixture_structurally_valid(task, sections):
    checked = validate(task, sections[task], SOURCE)
    assert checked.valid, checked.errors
    jsonschema.validate(sections[task], wire_schema(task))


@pytest.mark.parametrize("task", list(TASK_MODELS))
def test_wire_schemas_strict_and_no_unsupported_unique_items(task):
    schema = wire_schema(task)
    jsonschema.Draft202012Validator.check_schema(schema)
    assert "uniqueItems" not in json.dumps(schema)
    assert schema["additionalProperties"] is False
    assert set(schema["required"]) == set(schema["properties"])
    assert "ROOT:" in field_guide(task)
    for item in schema.get("$defs", {}).values():
        if item.get("type") == "object":
            assert item["additionalProperties"] is False
            assert set(item["required"]) == set(item["properties"])


def test_fabricated_evidence_rejected(sections):
    sections["conditions"]["items"][0]["evidence"][0]["quote"] = "This sentence is not in the source."
    checked = validate("conditions", sections["conditions"], SOURCE)
    assert not checked.valid
    assert "exact contiguous" in str(checked.errors)


def test_unknown_not_false_and_missing_key_rejected(sections):
    del sections["conditions"]["items"][0]["severity"]
    assert not validate("conditions", sections["conditions"], SOURCE).valid


def test_no_codes_or_extra_fields(sections):
    sections["conditions"]["items"][0]["SNOMED"] = "123456789"
    assert not validate("conditions", sections["conditions"], SOURCE).valid


def test_repeated_quote_retains_all_offsets():
    assert all_spans("α abc abc", "abc") == [[2, 5], [6, 9]]


def test_tumor_reference_requires_same_section_target(sections):
    sections["oncology"]["biomarkers"][0]["tumor_ref"] = "t9"
    assert not validate("oncology", sections["oncology"], SOURCE).valid


def test_no_duplicate_tumor_ids(sections):
    sections["oncology"]["tumors"].append(copy.deepcopy(sections["oncology"]["tumors"][0]))
    assert not validate("oncology", sections["oncology"], SOURCE).valid


def test_invented_iso_date_rejected(sections):
    sections["conditions"]["items"][0]["time"]["date_iso"] = "2026-01-01"
    assert not validate("conditions", sections["conditions"], SOURCE).valid


def test_false_source_heading_rejected(sections):
    sections["conditions"]["items"][0]["evidence"][0]["source_section"] = "NONEXISTENT HEADING"
    assert not validate("conditions", sections["conditions"], SOURCE).valid


def test_nkda_is_explicit_absence_not_empty_history(sections):
    assert sections["allergies"]["items"][0]["assertion"] == "absent"
    assert validate("allergies", sections["allergies"], SOURCE).valid


def test_family_cancer_subject_is_not_patient(sections):
    assert sections["family_genetics"]["items"][0]["subject"] == "family_member"
    assert "breast" not in str(sections["conditions"])


def test_conflicting_conditions_preserved_for_review(sections):
    item = copy.deepcopy(sections["conditions"]["items"][0])
    item["assertion"] = "absent"
    sections["conditions"]["items"].append(item)
    issues = consistency_findings(sections)
    assert any(x["code"] == "conflicting_condition_assertions" for x in issues)
    assert len(sections["conditions"]["items"]) == 2


def test_numeric_normalization_only_warning(sections):
    sections["demographics"]["items"][0]["numeric_value"] = 88.0
    checked = validate("demographics", sections["demographics"], SOURCE)
    assert checked.valid
    assert any("numeric normalization" in x for x in checked.warnings)
    # Source matching is not a clinical correctness claim: gold evaluation still needed.
