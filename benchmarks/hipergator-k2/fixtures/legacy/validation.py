"""Structural and deterministic source checks. These do NOT establish clinical truth."""
from __future__ import annotations

import datetime as dt
import json
import re
from dataclasses import dataclass, field

from pydantic import ValidationError
from .schemas import TASK_MODELS
from .output_parser import parse_output, OutputParseError


@dataclass
class CheckResult:
    data: dict | None = None
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    evidence: list[dict] = field(default_factory=list)

    @property
    def valid(self) -> bool:
        return self.data is not None and not self.errors


def all_spans(text: str, quote: str) -> list[list[int]]:
    spans, start = [], 0
    while quote and (index := text.find(quote, start)) >= 0:
        spans.append([index, index + len(quote)])
        start = index + 1
    return spans


def iter_objects(value, path: str = ""):
    if isinstance(value, dict):
        yield path, value
        for key, child in value.items():
            yield from iter_objects(child, path + "/" + key)
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield from iter_objects(child, path + "/" + str(index))


def validate(task: str, raw: str | dict, source: str) -> CheckResult:
    result = CheckResult()
    try:
        value = parse_output(raw).value if isinstance(raw, str) else raw
        result.data = TASK_MODELS[task].model_validate(value).model_dump()
    except (json.JSONDecodeError, ValidationError, ValueError, TypeError) as exc:
        # Error locations suffice for repair; do not echo an entire clinical response into logs.
        if isinstance(exc, ValidationError):
            result.errors = [f"{'.'.join(map(str, e['loc']))}: {e['type']}: {e['msg']}" for e in exc.errors()][:30]
        else:
            result.errors = [f"Invalid JSON/object: {type(exc).__name__}"]
        return result
    if task == "case_context" and not result.data["evidence"] and result.data["case_kind"] != "unknown":
        result.errors.append("case_context requires source evidence when case_kind is not unknown")
    objects = list(iter_objects(result.data))
    if result.data.get("documentation_evidence"):
        objects.append(("/documentation", {"evidence": result.data["documentation_evidence"]}))
    for path, obj in objects:
        if "evidence" not in obj or not isinstance(obj["evidence"], list):
            continue
        quotes = [item["quote"] for item in obj["evidence"]]
        combined = "\n".join(quotes)
        for i, evidence in enumerate(obj["evidence"]):
            quote = evidence["quote"]
            spans = all_spans(source, quote)
            evidence_path = path + f"/evidence/{i}"
            if not spans:
                result.errors.append(f"{evidence_path}: quote is not an exact contiguous source substring")
            elif len(spans) > 1:
                result.warnings.append(f"{evidence_path}: quote matches multiple locations; all retained")
            heading = evidence["source_section"]
            if heading is not None and heading not in source:
                result.errors.append(f"{evidence_path}: source_section is not present in source")
            result.evidence.append({"path": evidence_path, "quote": quote, "spans": spans,
                                    "offset_unit": "Unicode code points; end exclusive"})
        time = obj.get("time")
        if time and time.get("text") and time["text"] not in combined:
            result.errors.append(f"{path}/time/text: temporal expression must occur literally in this fact's evidence; use null for an unstated time")
        if time and time.get("date_iso"):
            date = time["date_iso"]
            try:
                dt.date.fromisoformat(date)
            except ValueError:
                result.errors.append(f"{path}/time/date_iso: invalid date")
            if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", date) or date not in combined or date not in (time.get("text") or ""):
                result.errors.append(f"{path}/time/date_iso: retain only full ISO dates literally present in evidence/time text")
        # A heuristic warning, not an automatic semantic rejection: lexical checks cannot
        # distinguish e.g. 'no change in cancer' from 'no cancer'. Human adjudication is separate.
        if obj.get("assertion") == "present" and re.search(r"\b(no|denies|without|ruled out)\b", combined, re.I):
            result.warnings.append(f"{path}: affirmed fact has a negation cue in evidence; review semantics")
        for key in ["numeric_value", "dose_value", "pack_years", "gestational_age_weeks", "line_of_therapy"]:
            number = obj.get(key)
            if number is None:
                continue
            # Worded numbers may be faithfully normalized; absent digit is review, not failure.
            number_text = format(number, "g")
            if not re.search(r"(?<![\d.])" + re.escape(number_text) + r"(?![\d.])", combined.replace(",", "")):
                result.warnings.append(f"{path}/{key}: numeric normalization needs review against evidence")
        if obj.get("numeric_value") is not None and "unit" in obj and obj["unit"] is None:
            result.warnings.append(f"{path}: numeric value has no documented unit")
    return result
