"""Gold-label evaluation. Exact, one-to-one fact matching with patient-level bootstrap.

Gold patterns must include critical subject/assertion/time qualifiers. This evaluator
cannot infer semantic equivalence of terms; use a reviewed normalizer or adjudicate.
"""
from __future__ import annotations

import random
from collections import defaultdict

from .data import read_jsonl
from .metrics import percentiles


def matches(pattern, value, field: str = "") -> bool:
    if isinstance(pattern, dict):
        return isinstance(value, dict) and all(k in value and matches(v, value[k], k) for k, v in pattern.items())
    if isinstance(pattern, list):
        return pattern == value
    if isinstance(pattern, str) and isinstance(value, str):
        if field in {"unit", "dose_unit"}:
            return pattern.strip() == value.strip()
        return " ".join(pattern.split()).casefold() == " ".join(value.split()).casefold()
    return pattern == value


def maximum_matching(expected: list[dict], predicted: list[dict]) -> int:
    assigned = {}
    def augment(i, seen):
        for j, fact in enumerate(predicted):
            if j in seen or not matches(expected[i], fact):
                continue
            seen.add(j)
            if j not in assigned or augment(assigned[j], seen):
                assigned[j] = i
                return True
        return False
    return sum(augment(i, set()) for i in range(len(expected)))


def scores(tp: int, fp: int | None, fn: int) -> dict:
    precision = tp / (tp + fp) if fp is not None and tp + fp else None
    recall = tp / (tp + fn) if tp + fn else None
    f1 = 2 * tp / (2 * tp + fp + fn) if fp is not None and 2 * tp + fp + fn else None
    return {"tp": tp, "fp": fp, "fn": fn, "precision": precision, "recall": recall, "f1": f1}


def evaluate(predictions_path: str, gold_path: str, bootstrap: int = 1000, seed: int = 42) -> dict:
    gold_rows = list(read_jsonl(gold_path))
    needed = {row["record_id"] for row in gold_rows}
    predictions = {row["source"]["record_id"]: row for row in read_jsonl(predictions_path) if row["source"]["record_id"] in needed}
    results = []
    seen = set()
    for label in gold_rows:
        key = (label["record_id"], label["task"], label.get("collection", "items"))
        if key in seen:
            raise ValueError("Gold has duplicate record/task/collection annotations")
        seen.add(key)
        record_id, task, collection = key
        expected = label["expected"]
        for fact in expected:
            if not {"subject", "assertion", "temporality"}.issubset(fact):
                raise ValueError("Gold facts must specify subject, assertion, and temporality")
        section = predictions.get(record_id, {}).get("sections", {}).get(task) or {}
        predicted = section.get(collection, [])
        tp = maximum_matching(expected, predicted)
        fp = len(predicted) - tp if label.get("closed_world", False) else None
        fn = len(expected) - tp
        forbidden = sum(any(matches(pattern, value) for value in predicted) for pattern in label.get("forbidden", []))
        results.append({"record_id": record_id, "task": task, "collection": collection,
                        "forbidden_pattern_violations": forbidden, **scores(tp, fp, fn)})
    def aggregate(rows):
        tp, fn = sum(x["tp"] for x in rows), sum(x["fn"] for x in rows)
        fp = sum(x["fp"] for x in rows) if rows and all(x["fp"] is not None for x in rows) else None
        return scores(tp, fp, fn)
    by_patient = defaultdict(list)
    for row in results:
        by_patient[row["record_id"]].append(row)
    patient_ids = sorted(by_patient)
    rng = random.Random(seed)
    intervals = {key: [] for key in ["precision", "recall", "f1"]}
    if len(patient_ids) >= 2:
        for _ in range(bootstrap):
            sampled = [item for rid in rng.choices(patient_ids, k=len(patient_ids)) for item in by_patient[rid]]
            measured = aggregate(sampled)
            for key in intervals:
                if measured[key] is not None:
                    intervals[key].append(measured[key])
    def ci(values):
        if not values:
            return None
        seq = sorted(values)
        return [seq[int(.025 * (len(seq)-1))], seq[int(.975 * (len(seq)-1))]]
    return {"overall": aggregate(results), "by_task": {task: aggregate([x for x in results if x["task"] == task]) for task in sorted({x["task"] for x in results})},
            "patient_bootstrap_95_percentile_ci": {key: ci(values) for key, values in intervals.items()},
            "forbidden_pattern_violations": sum(x["forbidden_pattern_violations"] for x in results),
            "annotated_records": len(patient_ids), "details": results,
            "warning": "Precision/F1 require closed-world annotation of the entire evaluated collection; partial labels report recall only. No clinical equivalence inferred."}
