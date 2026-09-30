"""Combine frozen checks, explicit semantic adjudications and run metrics.

Run score_medical_fidelity.py first. This script makes no network calls and
does not alter model predictions or the frozen source reference.
"""
from collections import Counter
from copy import deepcopy
import hashlib
import json
from pathlib import Path

from openpatients2.fidelity import last_parseable, summarize


ROOT = Path("runs/medical-fidelity-v1")


def read(name):
    return json.loads((ROOT / name).read_text())


reference = read("reference.json")
digest = hashlib.sha256((ROOT / "reference.json").read_bytes()).hexdigest()
automatic = read("automatic-scores.json")
adjudications = read("checklist-adjudications.json")
reviewed = read("claim-audit-reviewed.json")
assert digest == automatic["reference_sha256"] == adjudications["reference_sha256"] == reviewed["reference_sha256"]
audit = read("claim-audit-pending.json")
aliases = read("model-aliases.json")
expected_counts = Counter(c["record_id"].split(":")[0] for c in reference["cases"])
checks = {c["id"]: c for c in reference["checks"]}
result = {"reference_sha256": digest, "scoring_version": automatic.get("scoring_version"),
          "metric_scope": "Partial typed-field checklist, with explicit semantic corrections; not full medical recall or precision.",
          "models": {}, "review_complete": True}

for model, original_rows in automatic["details"].items():
    rows = deepcopy(original_rows)
    for row in rows:
        review = adjudications["reviews"].get(f"{model}:{row['check_id']}")
        if review:
            for mode in ("raw", "delivered"):
                credit = review.get(f"{mode}_credit")
                if credit is not None:
                    assert not credit or row[mode]["available"], "Cannot credit an unavailable section"
                    row[mode]["matched"] = credit
            row["semantic_review"] = review
    missing_reviews = [r["check_id"] for r in rows
                       if ((r["kind"] == "required" and r["raw"]["available"] and not r["raw"]["matched"])
                           or (r["kind"] == "forbidden" and r["raw"]["matched"]))
                       and f"{model}:{r['check_id']}" not in adjudications["reviews"]]
    claim_reviews = []
    pending = []
    for item in audit:
        if item["model_alias"] != aliases[model]:
            continue
        review = reviewed["reviews"].get(item["audit_id"])
        if review is None:
            pending.append(item["audit_id"])
            continue
        for key in ("claim", "cited_evidence", "delivered_section", "record_id"):
            assert item[key] == review[key], "A reviewed claim changed"
        claim_reviews.append(review)
    rosters = []
    folder = ROOT / "rosters" / model.replace("/", "--")
    for path in sorted(folder.glob("*-roster.json")):
        roster = json.loads(path.read_text())
        aid = roster["identity"]["article_id"]
        raw = roster["data"] if roster["status"] == "valid" else last_parseable(roster.get("attempt_responses", []))
        patients = raw.get("patients", []) if isinstance(raw, dict) else None
        rosters.append({"article_id": aid, "status": roster["status"],
                        "expected_count": expected_counts[aid],
                        "reported_individual_count": raw.get("reported_individual_count") if isinstance(raw, dict) else None,
                        "disposition": raw.get("disposition") if isinstance(raw, dict) else None,
                        "roster_complete": raw.get("roster_complete") if isinstance(raw, dict) else None,
                        "raw_count": len(patients) if isinstance(patients, list) else None,
                        "raw_count_correct": len(patients) == expected_counts[aid] if isinstance(patients, list) else False,
                        "raw_species": [p.get("species") for p in patients] if isinstance(patients, list) else None})
    metrics = []
    for path in (ROOT / "comparison" / "tasks").glob("*.json"):
        task = json.loads(path.read_text())
        if task.get("metrics", {}).get("model") == model:
            metrics.append(task["metrics"])
    model_result = {
        "patient_records": automatic["models"][model]["records"],
        "automatic": {mode: summarize(original_rows, mode) for mode in ("raw", "delivered")},
        "adjudicated": {mode: summarize(rows, mode) for mode in ("raw", "delivered")},
        "by_category": {category: {mode: summarize([r for r in rows if r["category"] == category], mode)
                                     for mode in ("raw", "delivered")}
                        for category in sorted({r["category"] for r in rows})},
        "by_article": {aid: {mode: summarize([r for r in rows if r["record_id"].split(":")[0] == aid], mode)
                              for mode in ("raw", "delivered")}
                       for aid in sorted(expected_counts)},
        "claim_audit": {"raw": dict(Counter(r["verdict"] for r in claim_reviews)),
                        "delivered": dict(Counter(r["verdict"] for r in claim_reviews if r["delivered_section"])),
                        "pending": pending, "sampled": len(claim_reviews) + len(pending),
                        "target": 3 * len(reference["cases"])},
        "unreviewed_checklist_misses": missing_reviews,
        "adjudication_categories": dict(Counter(v["verdict"] for k, v in adjudications["reviews"].items()
                                                if k.startswith(model + ":"))),
        "rosters": rosters,
        "tasks": {"total": len(metrics), "valid": sum(m["valid"] for m in metrics),
                  "attempts": sum(m["attempts"] for m in metrics),
                  "reported_cost_usd": sum(m.get("cost_usd", 0) or 0 for m in metrics)},
    }
    result["models"][model] = model_result
    if pending or missing_reviews or model_result["patient_records"] != 11 or len(rosters) != 9 or len(metrics) != 176:
        result["review_complete"] = False

Path("reports/medical-fidelity-results.json").write_text(json.dumps(result, indent=2) + "\n")
print(json.dumps({"review_complete": result["review_complete"], "models": {
    m: {"retained": {mode: r["adjudicated"][mode]["matched"] for mode in ("raw", "delivered")},
        "audit": r["claim_audit"], "tasks": r["tasks"], "unreviewed_checks": len(r["unreviewed_checklist_misses"])}
    for m, r in result["models"].items()}}, indent=2))
