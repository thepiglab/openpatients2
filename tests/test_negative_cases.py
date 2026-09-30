import copy

import pytest

from openpatients2.demo import SOURCE, empty_section
from openpatients2.schemas import TASK_MODELS
from openpatients2.validation import validate
from openpatients2.prompts import task_text


@pytest.mark.parametrize("task",list(TASK_MODELS))
def test_each_schema_accepts_empty_undocumented_output(task):
    assert validate(task,empty_section(task),"No clinical information is supplied.").valid
    assert "NEGATIVE CONTROLS" in task_text(task)
    assert "Empty output is a valid" in task_text(task)


@pytest.mark.parametrize("task",list(TASK_MODELS))
def test_unknown_domain_requires_and_checks_quoted_support(task):
    section=empty_section(task)
    section["documentation_status"]="explicitly_unknown"
    assert not validate(task,section,"History unavailable.").valid
    section["documentation_evidence"]=[{"quote":"History unavailable.","source_section":None}]
    assert validate(task,section,"History unavailable.").valid
    assert not validate(task,section,"Different source.").valid


def test_doc_status_cannot_hide_positive_or_explicit_negative_items(sections):
    sections["allergies"]["documentation_status"]="not_documented"
    assert not validate("allergies",sections["allergies"],SOURCE).valid
    sections["allergies"]["documentation_status"]="documented"
    assert validate("allergies",sections["allergies"],SOURCE).valid


def test_empty_documented_status_rejected():
    section=empty_section("conditions");section["documentation_status"]="documented"
    assert not validate("conditions",section,SOURCE).valid


def test_pending_test_cannot_be_negative_result(sections):
    section=copy.deepcopy(sections["observations"])
    obs=section["items"][0]
    obs.update(status="pending",text_value=None,numeric_value=None,flag="unknown",result_absent_reason="pending")
    assert validate("observations",section,SOURCE).valid # Structure only, not semantic entailment.
    obs["flag"]="negative"
    assert not validate("observations",section,SOURCE).valid


def test_reported_negative_test_is_not_missing(sections):
    section=copy.deepcopy(sections["observations"])
    item=section["items"][0]
    item.update(name="HIV test",status="resulted",text_value="negative",flag="negative",result_absent_reason=None)
    item["evidence"]=[{"quote":"HIV test negative.","source_section":None}]
    assert validate("observations",section,"HIV test negative.").valid
    item["result_absent_reason"]="not_reported"
    assert not validate("observations",section,"HIV test negative.").valid


def test_blank_limitations_cannot_claim_limited_coverage():
    section=empty_section("conditions");section["coverage"]="limited"
    assert not validate("conditions",section,SOURCE).valid
    section["limitations"]=["Ambiguous index subject"]
    assert validate("conditions",section,SOURCE).valid


def test_refuted_condition_cannot_be_affirmed(sections):
    item=sections["conditions"]["items"][0]
    item["verification_status"]="refuted"
    assert not validate("conditions",sections["conditions"],SOURCE).valid
    item["assertion"]="absent"
    assert validate("conditions",sections["conditions"],SOURCE).valid


def test_nkda_cannot_negate_food_allergies(sections):
    sections["allergies"]["items"][0]["category"]="food"
    assert not validate("allergies",sections["allergies"],SOURCE).valid
