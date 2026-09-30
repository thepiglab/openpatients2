"""Within-checkpoint extraction agreement; agreement is NOT clinical accuracy.

Fact comparison is conservative: whitespace/case normalization of text, exact
numeric values/units/qualifiers, order-independent collections, resolved local
tumor links. No LLM judge, invented synonyms, or absent=unknown substitution.
"""
from __future__ import annotations

import hashlib
import itertools
import json
import math
import random
from collections import defaultdict
from typing import Iterable

import numpy as np

from .evaluate import maximum_matching, matches


def canonical(value, key: str = ""):
    if isinstance(value, dict):
        return {k: canonical(v, k) for k, v in sorted(value.items())
                if k not in {"evidence", "tumor_ref", "coverage", "limitations", "documentation_evidence"}}
    if isinstance(value, list):
        return sorted((canonical(x, key) for x in value), key=lambda x: json.dumps(x, sort_keys=True))
    if isinstance(value, str):
        text = " ".join(value.split())
        # Unit case can be clinically meaningful. Do not equate m/M or mL/ML.
        return text if key in {"unit", "dose_unit"} else text.casefold()
    return value


def key_for(value) -> str:
    return json.dumps(canonical(value), sort_keys=True, ensure_ascii=False, allow_nan=False)


def atoms(section: dict, task: str) -> dict[str, dict]:
    linked = {x["tumor_ref"]: canonical(x) for x in section.get("tumors", [])}
    output: dict[str, dict] = {}
    collections = [k for k in ("items", "tumors", "biomarkers", "treatments") if k in section]
    values = [(collection, i, fact) for collection in collections for i, fact in enumerate(section[collection])]
    if task == "case_context" and section.get("evidence"):
        values = [("context", 0, section)]
    for collection, index, fact in values:
        payload = canonical(fact)
        if collection in {"biomarkers", "treatments"}:
            ref = fact.get("tumor_ref")
            payload["linked_tumor"] = linked.get(ref) if ref is not None else None
        signature = key_for({"collection": collection, "fact": payload})
        path = "" if collection == "context" else f"/{collection}/{index}"
        if signature not in output:
            output[signature] = {"payload": payload, "paths": [], "quotes": [], "count": 0}
        output[signature]["paths"].append(path)
        output[signature]["quotes"].extend(x["quote"] for x in fact.get("evidence", []))
        output[signature]["count"] += 1
    return output


def merge_intervals(intervals: Iterable[Iterable[int]]) -> list[tuple[int, int]]:
    result: list[tuple[int, int]] = []
    for left, right in sorted(set(tuple(x) for x in intervals)):
        if not (isinstance(left, int) and isinstance(right, int) and 0 <= left < right):
            raise ValueError("Invalid evidence offsets")
        if result and left <= result[-1][1]:
            result[-1] = (result[-1][0], max(right, result[-1][1]))
        else:
            result.append((left, right))
    return result


def interval_dice(a, b) -> float | None:
    a, b = merge_intervals(a), merge_intervals(b)
    denominator = sum(y-x for x, y in a) + sum(y-x for x, y in b)
    if not denominator:
        return None
    intersection = sum(max(0, min(r1, r2)-max(l1, l2)) for l1, r1 in a for l2, r2 in b)
    return 2 * intersection / denominator


def fact_spans(atom: dict, validation: dict) -> list[tuple[int, int]] | None:
    evidence = [e for e in validation.get("evidence", [])
                if any(e["path"].startswith(path + "/evidence/") for path in atom["paths"])]
    # No choosing an arbitrary occurrence of a repeated quote.
    if not evidence or any(len(e.get("spans", [])) != 1 for e in evidence):
        return None
    return merge_intervals(e["spans"][0] for e in evidence)


def compare_rows(left: dict, right: dict) -> dict:
    if left["record_id"] != right["record_id"] or left["task"] != right["task"]:
        raise ValueError("Comparisons must be paired by record and task")
    if left.get("input_hash") != right.get("input_hash"):
        raise ValueError("Cannot compare different source revisions")
    valid = left.get("status") == right.get("status") == "valid"
    result = {"both_valid": float(valid),
              "both_failed": float(left.get("status") != "valid" and right.get("status") != "valid"),
              "exact_agreement_all_scheduled": 0.0,
              "canonical_exact_agreement": None, "nonempty_exact_agreement": None,
              "fact_set_dice": None, "fact_set_jaccard": None,
              "both_empty": None, "documentation_status_agreement": None,
              "exact_evidence_on_matched_facts": None, "span_dice_on_matched_facts": None,
              "matched_facts": 0, "unambiguous_span_matches": 0, "ambiguous_or_missing_span_matches": 0}
    if not valid:
        return result
    lsec, rsec = left["extraction"], right["extraction"]
    a, b = atoms(lsec, left["task"]), atoms(rsec, right["task"])
    shared, union = a.keys() & b.keys(), a.keys() | b.keys()
    doc_equal = lsec.get("documentation_status") == rsec.get("documentation_status")
    exact = a.keys() == b.keys() and doc_equal and lsec.get("coverage") == rsec.get("coverage")
    result.update({"canonical_exact_agreement": float(exact), "exact_agreement_all_scheduled": float(exact),
                   "nonempty_exact_agreement": float(exact) if union else None,
                   "both_empty": float(not union), "documentation_status_agreement": float(doc_equal),
                   "fact_set_dice": 2*len(shared)/(len(a)+len(b)) if union else None,
                   "fact_set_jaccard": len(shared)/len(union) if union else None,
                   "matched_facts": len(shared),
                   "duplicate_fact_count_left": sum(x["count"]-1 for x in a.values()),
                   "duplicate_fact_count_right": sum(x["count"]-1 for x in b.values())})
    quote_scores, span_scores = [], []
    for key in sorted(shared):
        quote_scores.append(float(set(a[key]["quotes"]) == set(b[key]["quotes"])))
        aspans, bspans = fact_spans(a[key], left.get("validation", {})), fact_spans(b[key], right.get("validation", {}))
        if aspans is None or bspans is None:
            result["ambiguous_or_missing_span_matches"] += 1
        else:
            result["unambiguous_span_matches"] += 1
            span_scores.append(interval_dice(aspans, bspans))
    result["exact_evidence_on_matched_facts"] = mean(quote_scores)
    result["span_dice_on_matched_facts"] = mean(span_scores)
    return result


def mean(values) -> float | None:
    values = [float(x) for x in values if x is not None and math.isfinite(x)]
    return sum(values)/len(values) if values else None


def cluster_map(sources: dict[str, dict]) -> dict[str, str]:
    """Conservatively join declared source/patient groups AND exact duplicate notes.

    This is not entity resolution. Different reports about the same person still
    require a reviewed cluster_id before inference about an independent population.
    """
    parent = {rid: rid for rid in sources}
    def find(rid):
        while parent[rid] != rid:
            parent[rid] = parent[parent[rid]]
            rid = parent[rid]
        return rid
    owner = {}
    for rid, source in sorted(sources.items()):
        tokens = [f"{key}:{source[key]}" for key in ("cluster_id", "source_id", "source_hash") if source.get(key)]
        article = source.get("provenance", {}).get("article", {})
        article_group = source.get("provenance", {}).get("upstream", {}).get("article_group_token")
        if article_group:
            tokens.append("source_article_group:" + article_group)
        # A shared paper can share imagery, wording, and leakage even when patient
        # summaries differ. Keep every case from that paper in one source cluster.
        if not article.get("identifier_conflicts"):
            tokens.extend(f"article:{key}:{article[key].casefold()}" for key in ("pmid", "pmcid", "doi") if article.get(key))
        for token in tokens:
            if token in owner:
                a, b = find(rid), find(owner[token])
                parent[max(a,b)] = min(a,b)
            else:
                owner[token] = rid
    members = defaultdict(list)
    for rid in sources:
        members[find(rid)].append(rid)
    labels = {}
    for group in members.values():
        # Content-derived IDs keep identical texts across record aliases in one split.
        ids = sorted({sources[rid].get("source_hash", rid) for rid in group})
        label = hashlib.sha256(json.dumps(ids).encode()).hexdigest()[:24]
        for rid in group:
            labels[rid] = label
    return labels


def quantile(values: list[float], p: float) -> float:
    values = sorted(values)
    index = (len(values)-1)*p
    lower = int(index)
    upper = min(lower+1, len(values)-1)
    return values[lower]*(1-(index-lower)) + values[upper]*(index-lower)


def cluster_summary(values: dict[str, float | None], clusters: dict[str, str], n_resamples: int = 2000,
                    seed: int = 42) -> dict:
    """Equal-record mean; resample whole source clusters, preserving paired records."""
    values = {rid: v for rid, v in values.items() if v is not None and math.isfinite(v)}
    grouped = defaultdict(list)
    for rid, value in values.items():
        grouped[clusters[rid]].append(value)
    groups = [grouped[k] for k in sorted(grouped)]
    result = {"mean": mean(values.values()), "records": len(values), "source_clusters": len(groups),
              "ci95_percentile": None, "degenerate_bootstrap": None}
    if len(groups) < 2 or n_resamples < 1:
        return result
    rng = np.random.default_rng(seed)
    sums, counts = np.array([sum(g) for g in groups]), np.array([len(g) for g in groups])
    resampled = []
    # Bounded vectorized batches; no B x records x tasks tensor is held in RAM.
    for start in range(0, n_resamples, 128):
        indices = rng.integers(len(groups), size=(min(128,n_resamples-start),len(groups)))
        resampled.extend((sums[indices].sum(axis=1)/counts[indices].sum(axis=1)).tolist())
    result["ci95_percentile"] = [quantile(resampled,.025), quantile(resampled,.975)]
    result["degenerate_bootstrap"] = min(resampled) == max(resampled)
    return result


def paired_signflip(deltas: dict[str, float | None], clusters: dict[str, str], n_resamples: int = 9999,
                    seed: int = 42) -> dict:
    """Two-sided paired label-swap test: one shared sign per source cluster.

    Exact for <=16 clusters. Monte Carlo uses (b+1)/(B+1). Exchangeability of
    treatment labels under the sharp null is an assumption, not a model guarantee.
    """
    sums = defaultdict(float)
    for rid, value in deltas.items():
        if value is not None and math.isfinite(value):
            sums[clusters[rid]] += value
    values = [sums[k] for k in sorted(sums)]
    if len(values) < 2:
        return {"p_value": None, "method": "insufficient_source_clusters"}
    observed = abs(sum(values))
    if len(values) <= 16:
        samples = itertools.product((-1,1), repeat=len(values))
        count = 2**len(values)
        more = sum(abs(sum(s*x for s,x in zip(signs,values))) >= observed-1e-12 for signs in samples)
        return {"p_value": more/count, "method": "exact_cluster_label_swap", "permutations": count}
    rng = np.random.default_rng(seed)
    more = 0
    array = np.array(values)
    for start in range(0,n_resamples,128):
        signs = rng.integers(0,2,size=(min(128,n_resamples-start),len(values)))*2-1
        more += int(np.count_nonzero(np.abs((signs*array).sum(axis=1)) >= observed-1e-12))
    return {"p_value": (more+1)/(n_resamples+1), "method": "monte_carlo_cluster_label_swap", "permutations": n_resamples}


def holm(pvalues: list[float | None]) -> list[float | None]:
    ordered = sorted((p, i) for i,p in enumerate(pvalues) if p is not None)
    output: list[float | None] = [None]*len(pvalues)
    previous = 0.0
    for rank, (p, index) in enumerate(ordered):
        previous = max(previous, min(1., (len(ordered)-rank)*p))
        output[index] = previous
    return output


def gold_metrics(row: dict, labels: list[dict]) -> dict:
    """Optional adjudicated-pattern evaluation. Failures never count as correct empties."""
    if not labels:
        return {}
    task = row["task"]
    section = row.get("extraction") or {}
    is_valid = row.get("status") == "valid"
    expected_total = tp = predicted_total = forbidden = 0
    closed = all(x.get("closed_world",False) for x in labels)
    annotated = {label.get("collection","items") for label in labels}
    required = {"tumors","biomarkers","treatments"} if task == "oncology" else {"items"}
    closed = closed and annotated == required and task != "case_context"
    has_forbidden = any(x.get("forbidden") for x in labels)
    for label in labels:
        expected = label["expected"]
        for fact in expected:
            if not {"subject","assertion","temporality"}.issubset(fact):
                raise ValueError("Gold facts must specify subject, assertion, temporality")
        actual = section.get(label.get("collection","items"),[]) if is_valid else []
        tp += maximum_matching(expected, actual)
        expected_total += len(expected)
        predicted_total += len(actual)
        forbidden += sum(any(matches(pattern,fact) for fact in actual) for pattern in label.get("forbidden",[]))
    denominator = expected_total + predicted_total
    doc_ok = all(label.get("documentation_status", section.get("documentation_status")) == section.get("documentation_status") for label in labels)
    return {"gold_expected_recall": tp/expected_total if expected_total else None,
            "clinical_f1": (2*tp/denominator if is_valid else 0.) if closed and denominator else None,
            "gold_task_exact": float(is_valid and tp == expected_total == predicted_total and doc_ok and forbidden == 0) if closed else None,
            "negative_case_success": float(is_valid and predicted_total == 0 and doc_ok) if closed and expected_total == 0 else None,
            "gold_forbidden_violations": float(forbidden) if has_forbidden and is_valid else None}
