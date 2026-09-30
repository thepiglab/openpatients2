import copy

import pytest

from openpatients2.agreement import (compare_rows, cluster_map, cluster_summary, paired_signflip,
                                      holm, gold_metrics, interval_dice)
from openpatients2.demo import SOURCE, empty_section
from openpatients2.validation import validate


def row_for(task, section, source=SOURCE):
    checked=validate(task,section,source)
    assert checked.valid, checked.errors
    return {"record_id":"case","task":task,"input_hash":"immutable","status":"valid",
            "extraction":section,"validation":{"evidence":checked.evidence}}


def test_empty_outputs_not_perfect_factual_agreement():
    row=row_for("conditions",empty_section("conditions"))
    result=compare_rows(row,row)
    assert result["both_empty"]==1
    assert result["fact_set_dice"] is None
    assert result["nonempty_exact_agreement"] is None
    assert result["canonical_exact_agreement"]==1


def test_both_failures_never_count_as_agreement():
    row={"record_id":"x","task":"conditions","input_hash":"a","status":"failed","extraction":None}
    result=compare_rows(row,row)
    assert result["both_failed"]==1 and result["exact_agreement_all_scheduled"]==0
    assert result["canonical_exact_agreement"] is None


@pytest.mark.parametrize("field,value",[("assertion","absent"),("subject","family_member"),
    ("temporality","historical"),("laterality","left"),("verification_status","unconfirmed")])
def test_critical_qualifiers_cannot_be_normalized_away(sections,field,value):
    a=row_for("conditions",sections["conditions"])
    b=copy.deepcopy(a);b["extraction"]["items"][0][field]=value
    result=compare_rows(a,b)
    assert result["fact_set_dice"]==0
    assert result["canonical_exact_agreement"]==0


def test_local_tumor_ids_do_not_create_false_disagreement(sections):
    a=row_for("oncology",sections["oncology"])
    changed=copy.deepcopy(sections["oncology"])
    for collection in ["tumors","biomarkers","treatments"]:
        changed[collection][0]["tumor_ref"]="t7"
    b=row_for("oncology",changed)
    assert compare_rows(a,b)["fact_set_dice"]==1
    b["extraction"]["biomarkers"][0]["tumor_ref"]=None
    assert compare_rows(a,b)["fact_set_dice"]<1


def test_evidence_span_agreement_different_from_fact_agreement(sections):
    a=row_for("conditions",sections["conditions"])
    changed=copy.deepcopy(sections["conditions"])
    changed["items"][0]["evidence"][0]["quote"]="lung adenocarcinoma"
    b=row_for("conditions",changed)
    result=compare_rows(a,b)
    assert result["fact_set_dice"]==1
    assert result["exact_evidence_on_matched_facts"]==0
    assert 0<result["span_dice_on_matched_facts"]<1


def test_repeated_quotes_are_not_assigned_arbitrary_spans(sections):
    a=row_for("conditions",sections["conditions"],SOURCE+" "+SOURCE)
    result=compare_rows(a,a)
    assert result["span_dice_on_matched_facts"] is None
    assert result["ambiguous_or_missing_span_matches"]==1


def test_documentation_unknown_not_equated_to_silence():
    a=row_for("conditions",empty_section("conditions"))
    unknown=empty_section("conditions")
    unknown.update(documentation_status="explicitly_unknown",documentation_evidence=[{"quote":"History unknown.","source_section":None}])
    b=row_for("conditions",unknown,"History unknown.")
    assert compare_rows(a,b)["documentation_status_agreement"]==0


def test_source_revision_mismatch_rejected(sections):
    a=row_for("conditions",sections["conditions"])
    b=copy.deepcopy(a);b["input_hash"]="changed"
    with pytest.raises(ValueError,match="source revisions"):compare_rows(a,b)


def test_cluster_bootstrap_keeps_related_records_together():
    sources={"a":{"source_id":"article","source_hash":"h1"},
             "b":{"source_id":"article","source_hash":"h2"},
             "c":{"source_id":"other","source_hash":"h2"},
             "d":{"source_id":"different","source_hash":"h3"}}
    groups=cluster_map(sources)
    assert groups["a"]==groups["b"]==groups["c"]!=groups["d"]
    values={"a":1.,"b":1.,"c":1.,"d":1.}
    result=cluster_summary(values,groups,100)
    assert result["records"]==4 and result["source_clusters"]==2
    assert result["ci95_percentile"]==[1.,1.] and result["degenerate_bootstrap"]


def test_paired_exact_test_and_holm_reference():
    deltas={str(i):1 for i in range(5)}
    groups={str(i):str(i) for i in range(5)}
    result=paired_signflip(deltas,groups)
    assert result["p_value"]==2/32
    assert holm([.01,.04,.03,None])==[.03,.06,.06,None]
    assert paired_signflip(deltas,{x:"one_group" for x in deltas})["p_value"] is None


def test_gold_negative_cases_not_satisfied_by_failed_extraction():
    row=row_for("conditions",empty_section("conditions"))
    labels=[{"expected":[],"closed_world":True,"documentation_status":"not_documented"}]
    assert gold_metrics(row,labels)["negative_case_success"]==1
    row["status"]="failed";row["extraction"]=None
    assert gold_metrics(row,labels)["negative_case_success"]==0


def test_partial_gold_does_not_claim_f1(sections):
    row=row_for("conditions",sections["conditions"])
    labels=[{"expected":[{"name":"lung adenocarcinoma","subject":"index_patient","assertion":"present","temporality":"current"}],"closed_world":False}]
    result=gold_metrics(row,labels)
    assert result["gold_expected_recall"]==1 and result["clinical_f1"] is None


def test_interval_overlap_merges_overlapping_quotes():
    assert interval_dice([(0,10),(5,15)],[(0,15)])==1


def test_gold_unit_case_is_not_silently_normalized():
    from openpatients2.evaluate import matches
    assert not matches({"unit":"m"},{"unit":"M"})
    assert matches({"name":" Lung  Cancer "},{"name":"lung cancer"})
