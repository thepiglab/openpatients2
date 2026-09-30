import copy
import json
from pathlib import Path

import pytest

from openpatients2.data import digest_text
from openpatients2.demo import SOURCE, example_record, fixture_sections
from openpatients2.validation import validate
from openpatients2.warehouse import build_index, query_cohort, compile_cohort
from openpatients2.evaluate import maximum_matching, evaluate
from openpatients2.schemas import TASK_MODELS
from openpatients2 import SCHEMA_VERSION


def exported(sections=None):
    sections = sections or fixture_sections()
    return {"schema_version": SCHEMA_VERSION, "source": example_record(), "model": {"revision": "fixture"},
            "expected_tasks": list(TASK_MODELS), "complete_for_scope": True, "sections": sections,
            "quality": {task: {"status": "valid", "checks": {"errors": [], "warnings": [], "evidence": validate(task, data, SOURCE).evidence}} for task, data in sections.items()}}


def spec():
    return {"source_kinds": ["synthetic_fixture"], "criteria": [
        {"id": "cancer", "domain": "oncology_tumors", "where": [{"field": "primary_site", "value": "lung"}, {"field": "stage_group", "value": "IV"}]},
        {"id": "marker", "domain": "oncology_biomarkers", "linked_to": "cancer", "where": [{"field": "gene", "value": "EGFR"}, {"field": "interpretation", "value": "positive"}]}]}


def index(tmp_path, row):
    source = tmp_path / "patients.jsonl"
    source.write_text(json.dumps(row) + "\n")
    db = tmp_path / "index.sqlite"
    build_index(str(source), str(db))
    return str(db)


def test_positive_cohort_evidence(tmp_path):
    db = index(tmp_path, exported())
    output = str(tmp_path / "cohort.jsonl")
    report = query_cohort(db, spec(), output)
    assert report["returned_records"] == 1
    cohort = json.loads(Path(output).read_text())
    assert len(cohort["matches"]) == 2
    assert cohort["matches"][0]["evidence"][0]["spans"]


def test_default_source_gate_excludes_synthetic_fixture(tmp_path):
    db = index(tmp_path, exported())
    criteria = spec()
    del criteria["source_kinds"]
    assert query_cohort(db, criteria, str(tmp_path / "cohort.jsonl"))["returned_records"] == 0


def test_biomarker_wrong_tumor_does_not_match(tmp_path):
    row = exported()
    second = copy.deepcopy(row["sections"]["oncology"]["tumors"][0])
    second.update({"tumor_ref": "t2", "primary_site": "breast", "name": "breast cancer", "stage_group": None,
                   "histology": None, "disease_extent": "unknown", "metastatic_sites": []})
    row["sections"]["oncology"]["tumors"].append(second)
    row["sections"]["oncology"]["biomarkers"][0]["tumor_ref"] = "t2"
    db = index(tmp_path, row)
    assert query_cohort(db, spec(), str(tmp_path / "cohort.jsonl"))["returned_records"] == 0


def test_unknown_marker_not_positive(tmp_path):
    row = exported()
    row["sections"]["oncology"]["biomarkers"][0]["interpretation"] = "unknown"
    db = index(tmp_path, row)
    assert query_cohort(db, spec(), str(tmp_path / "cohort.jsonl"))["returned_records"] == 0


def test_possible_cancer_not_confirmed_cohort(tmp_path):
    row = exported()
    row["sections"]["oncology"]["tumors"][0]["assertion"] = "possible"
    db = index(tmp_path, row)
    assert query_cohort(db, spec(), str(tmp_path / "cohort.jsonl"))["returned_records"] == 0


def test_partial_scope_and_limited_coverage_excluded(tmp_path):
    row = exported()
    row["sections"]["conditions"]["coverage"] = "limited"
    row["sections"]["conditions"]["limitations"] = ["Test fixture: ambiguous case attribution"]
    db = index(tmp_path, row)
    assert query_cohort(db, spec(), str(tmp_path / "cohort.jsonl"))["returned_records"] == 0


def test_cadaver_excluded(tmp_path):
    row = exported()
    row["sections"]["case_context"]["case_kind"] = "cadaveric_anatomic"
    row["sections"]["case_context"]["subject_state"] = "cadaver"
    db = index(tmp_path, row)
    assert query_cohort(db, spec(), str(tmp_path / "cohort.jsonl"))["returned_records"] == 0


def test_query_sql_injection_is_parameter_not_sql():
    query = spec()
    query["criteria"][0]["where"][0]["value"] = "lung' OR 1=1; --"
    sql, values = compile_cohort(query)
    assert "OR 1=1" not in sql
    assert "lung' OR 1=1; --" in values
    query["criteria"][0]["where"][0]["field"] = "payload'); DROP TABLE cases; --"
    with pytest.raises(ValueError):
        compile_cohort(query)


def test_one_to_one_matching_prevents_duplicate_true_positives():
    expected = [{"name": "cancer"}, {"name": "cancer"}]
    assert maximum_matching(expected, [{"name": "cancer"}]) == 1


def test_partial_gold_does_not_claim_precision(tmp_path):
    prediction = tmp_path / "patients.jsonl"
    prediction.write_text(json.dumps(exported()) + "\n")
    gold = tmp_path / "gold.jsonl"
    label = {"record_id": "synthetic-test-001", "task": "conditions", "closed_world": False,
             "expected": [{"name": "lung adenocarcinoma", "subject": "index_patient", "assertion": "present", "temporality": "current"}], "forbidden": []}
    gold.write_text(json.dumps(label) + "\n")
    result = evaluate(str(prediction), str(gold), bootstrap=10)
    assert result["overall"]["recall"] == 1
    assert result["overall"]["precision"] is None
    assert result["overall"]["f1"] is None


def test_annotation_requires_subject_assertion_time(tmp_path):
    prediction = tmp_path / "patients.jsonl"
    prediction.write_text(json.dumps(exported()) + "\n")
    gold = tmp_path / "gold.jsonl"
    gold.write_text(json.dumps({"record_id": "synthetic-test-001", "task": "conditions", "expected": [{"name": "lung adenocarcinoma"}]}) + "\n")
    with pytest.raises(ValueError, match="subject"):
        evaluate(str(prediction), str(gold))


def test_reasoning_is_not_copied_into_the_clinical_index(tmp_path):
    import sqlite3
    row = exported()
    row["generations"] = {"conditions": {"reasoning_text": "NOT A CLINICAL FACT"}}
    db = index(tmp_path, row)
    with sqlite3.connect(db) as conn:
        payload = json.loads(conn.execute("SELECT payload FROM cases").fetchone()[0])
    assert "generations" not in payload
    assert payload["sections"] == row["sections"]


def test_distinct_article_patients_survive_same_text_deduplication(tmp_path):
    from openpatients2.data import normalize,prepare
    rows=[]
    for pid,rid in [('p1','case-1'),('p2','case-2'),('p1','case-1-copy')]:
        p=exported()
        p['source']=normalize({'record_id':rid,'text':SOURCE,'source_kind':'synthetic_fixture',
            'article_source':{'article_id':'PMC1.1','license':{'allowed':True,'code':'CC BY'}},'patient_target':{'patient_id':pid},
            'article_roster_digest':'fixed-reference'})
        rows.append(p)
    source=tmp_path/'patients.jsonl';source.write_text(''.join(json.dumps(p)+'\n' for p in rows))
    db=tmp_path/'index.sqlite';build_index(str(source),str(db))
    result=query_cohort(str(db),spec(),str(tmp_path/'cohort.jsonl'))
    assert result['matched_source_records_before_exact_dedup']==3
    assert result['returned_records']==2
    packets=tmp_path/'packets.jsonl';packets.write_text(''.join(json.dumps(p['source'])+'\n' for p in rows))
    prepare(str(packets),str(tmp_path/'prepared.jsonl'))
    prepared=list(map(json.loads,(tmp_path/'prepared.jsonl').read_text().splitlines()))
    assert 'exact_duplicate_of' not in prepared[1]
    assert prepared[2]['exact_duplicate_of']=='case-1'


def test_index_rechecks_old_article_license_approval(tmp_path):
    p=exported()
    p['source']['article_source']={'article_id':'PMC1.1','license':{'allowed':True,'code':'CC BY',
        'statements':[{'type':'license','text':'Creative Commons No Derivatives'}]}}
    path=tmp_path/'patients.jsonl';path.write_text(json.dumps(p)+'\n')
    with pytest.raises(ValueError,match='license requires review'):
        build_index(str(path),str(tmp_path/'index.sqlite'))
