"""Measure section-level validation loss without salvaging/releasing facts.

Individual validity still cannot prove the patient attribution or medical
meaning is correct. Preserve original section metadata during these checks.
"""
from copy import deepcopy
import json
from pathlib import Path

from openpatients2.fidelity import load_predictions
from openpatients2.validation import validate

root = Path("runs/medical-fidelity-v1")
results = []
for folder in (root / "comparison").iterdir():
    if not folder.is_dir() or "--" not in folder.name:
        continue
    for rid, record in load_predictions(folder).items():
        section = record["raw"].get("observations")
        if not isinstance(section, dict) or record["delivered"].get("observations") is not None:
            continue
        items = section.get("items", [])
        if not isinstance(items, list):
            continue
        valid_indices = []
        failures = []
        for index, item in enumerate(items):
            single = deepcopy(section)
            single["items"] = [item]
            checked = validate("observations", single, record["patient"]["source"]["text"])
            if checked.valid:
                valid_indices.append(index)
            else:
                failures.append({"index": index, "errors": checked.errors})
        results.append({"model": folder.name.replace("--", "/"), "record_id": rid,
                        "items_in_rejected_section": len(items),
                        "individually_source_schema_valid": len(valid_indices),
                        "valid_indices": valid_indices, "failures": failures})

(root / "observation-validation-diagnostics.json").write_text(json.dumps({
    "method": "Revalidate each observation alone with original section metadata and original source; do not change stored results.",
    "warning": "Source/schema-valid does not establish medical fidelity or patient attribution. No automatic release or repair.",
    "results": results,
}, indent=2) + "\n")
for row in results:
    print(row["model"], row["record_id"], row["individually_source_schema_valid"], "/", row["items_in_rejected_section"])
