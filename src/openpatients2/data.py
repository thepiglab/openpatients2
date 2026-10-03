from __future__ import annotations

import copy
import gzip
import hashlib
import json
import os
import random
from collections import Counter
from pathlib import Path
from typing import Iterable, Iterator

from .provenance import SOURCE_FORMAT, OPEN_PATIENTS, build_provenance, json_digest


def digest_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def case_duplicate_key(record: dict) -> str:
    """The same full article can be evidence for several distinct patients."""
    original=record.get('original_row') or record
    target=record.get('patient_target') or original.get('patient_target')
    article=record.get('article_source') or original.get('article_source')
    if target and article:
        return json_digest({'source_hash':record['source_hash'],'article_id':article['article_id'],
            'patient_id':target['patient_id'],'identity_evidence':target.get('identity_evidence'),
            'roster_digest':original.get('article_roster_digest')})
    return record['source_hash']


def write_json(path: str | Path, value: object) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False) + "\n", encoding="utf-8")
    os.replace(temp, path)


def read_jsonl(path: str | Path) -> Iterator[dict]:
    opener = gzip.open if str(path).endswith('.gz') else open
    with opener(path, 'rt', encoding="utf-8") as f:
        for line_number, line in enumerate(f, 1):
            if not line.strip():
                continue
            try:
                value = json.loads(line)
                if not isinstance(value, dict):
                    raise ValueError("each JSONL line must be an object")
                yield value
            except (ValueError, TypeError) as exc:
                raise ValueError(f"Invalid JSONL at {path}:{line_number}: {exc}") from exc


def source_kind(record_id: str) -> str:
    name = record_id.lower()
    if name.startswith("pmc-"):
        return "published_case_summary"
    if name.startswith("usmle-"):
        return "educational_vignette"
    if name.startswith("trec-cds-2016-"):
        return "ehr_summary"
    if name.startswith(("trec-cds-2014-", "trec-cds-2015-", "trec-ct-")):
        return "synthetic_vignette"
    return "unknown"


def normalize(raw: dict, revision: str = "local-unversioned", *, dataset_id: str = "local",
              row_index: int | None = None, source_file: str | None = None) -> dict:
    rid = raw.get("record_id", raw.get("_id"))
    text = raw.get("text", raw.get("description"))
    if not isinstance(rid, str) or not rid or not isinstance(text, str) or not text.strip():
        raise ValueError("Each record needs nonempty string _id/record_id and description/text")
    # Idempotence is explicit; arbitrary original fields called 'provenance' are not
    # interpreted as application-owned metadata unless the source format is declared.
    if raw.get("source_format") == SOURCE_FORMAT:
        result = copy.deepcopy(raw)
        original = result.get("original_row")
        if not isinstance(original, dict) or result.get("original_row_hash") != json_digest(original):
            raise ValueError(f"Original-row integrity failure for {rid}")
        if text != original.get("text", original.get("description")):
            raise ValueError(f"Normalized text differs from preserved original for {rid}")
    else:
        original = copy.deepcopy(raw)
        result = {"record_id": rid, "text": text, "source_format": SOURCE_FORMAT,
                  "source_hash": digest_text(text),
                  "source_kind": raw.get("source_kind", source_kind(rid)),
                  "dataset_revision": raw.get("dataset_revision", revision),
                  "source_id": raw.get("source_id", raw.get("_id", rid)),
                  "original_row": original, "original_row_hash": json_digest(original)}
        result["provenance"] = build_provenance(original, dataset_id, result["dataset_revision"],
                                                  row_index, source_file)
        for key in ["cluster_id", "exact_duplicate_of"]:
            if key in raw:
                result[key] = raw[key]
    for key in ["cluster_id", "exact_duplicate_of"]:
        if key in result and (not isinstance(result[key], str) or not result[key]):
            raise ValueError(f"{key} must be a nonempty string")
    if "source_hash" in raw and raw["source_hash"] != digest_text(text):
        raise ValueError(f"Source hash mismatch for {rid}")
    return result


def records(path: str | Path, limit: int | None = None) -> Iterator[dict]:
    seen = set()
    for i, row in enumerate(read_jsonl(path)):
        if limit is not None and i >= limit:
            break
        row = normalize(row)
        if row["record_id"] in seen:
            raise ValueError(f"Duplicate record_id: {row['record_id']}")
        seen.add(row["record_id"])
        yield row


def prepare(source: str, destination: str, revision: str = "local-unversioned",
            dataset_id: str = "local") -> dict:
    path = Path(destination)
    if path.resolve() == Path(source).resolve():
        raise ValueError("Preparation must not overwrite the original source file")
    path.parent.mkdir(parents=True, exist_ok=True)
    # Raw Open-Patients contains repeated USMLE IDs. Preserve every occurrence,
    # assign stable row-qualified processing keys, and retain the original ID.
    id_counts = Counter(row.get("record_id", row.get("_id")) for row in read_jsonl(source))
    seen: set[str] = set()
    seen_text: dict[str, str] = {}
    kinds: Counter = Counter()
    chars, duplicates, duplicate_id_rows = 0, 0, 0
    temp = path.with_suffix(path.suffix + ".tmp")
    try:
        with temp.open("w", encoding="utf-8") as out:
            temp.chmod(0o600)
            for index, row in enumerate(read_jsonl(source)):
                item = normalize(row, revision, dataset_id=dataset_id,
                                 row_index=index, source_file=Path(source).name)
                if id_counts[item["record_id"]] > 1:
                    duplicate_id_rows += 1
                    original_id = item["record_id"]
                    candidate = f"{original_id}::row-{index}"
                    while candidate in id_counts or candidate in seen:
                        candidate += ":duplicate"
                    item["record_id"] = candidate
                    item["duplicate_source_id"] = original_id
                if item["record_id"] in seen:
                    raise ValueError(f"Conflicting processing key {item['record_id']}")
                seen.add(item["record_id"])
                duplicate_key=case_duplicate_key(item)
                if duplicate_key in seen_text:
                    duplicates += 1
                    item["exact_duplicate_of"] = seen_text[duplicate_key]
                else:
                    seen_text[duplicate_key] = item["record_id"]
                chars += len(item["text"])
                kinds[item["source_kind"]] += 1
                out.write(json.dumps(item, ensure_ascii=False, allow_nan=False) + "\n")
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)
    summary = {"records": len(seen), "total_characters": chars,
               "mean_characters": chars / len(seen) if seen else None,
               "source_kinds": dict(kinds), "exact_duplicate_texts": duplicates,
               "duplicate_id_rows_preserved": duplicate_id_rows,
               "original_rows_preserved": True, "dataset": dataset_id,
               "dataset_revision": revision, "unit": "source case, not verified unique person"}
    write_json(path.with_suffix(".manifest.json"), summary)
    return summary


def sample_records(items: Iterable[dict], size: int, seed: int) -> list[dict]:
    """Uniform reservoir sample of source records, NOT a representative clinical population."""
    rng = random.Random(seed)
    sample = []
    for i, row in enumerate(items):
        if i < size:
            sample.append(row)
        else:
            j = rng.randrange(i + 1)
            if j < size:
                sample[j] = row
    return sample


def download_dataset(destination: str, revision: str = "main") -> dict:
    from huggingface_hub import HfApi, hf_hub_download
    info = HfApi().dataset_info("ncbi/Open-Patients", revision=revision)
    sha = info.sha
    local = hf_hub_download("ncbi/Open-Patients", "Open-Patients.jsonl", repo_type="dataset", revision=sha)
    summary = prepare(local, destination, sha, OPEN_PATIENTS)
    summary["dataset"] = "ncbi/Open-Patients"
    summary["license_notice"] = "Open-Patients: CC-BY-SA-4.0; PMC-Patients component: CC-BY-NC-SA-4.0. Article/figure terms are separate; no training/commercial permission is inferred."
    write_json(Path(destination).with_suffix(".manifest.json"), summary)
    return summary


def download_model(repo_id: str, destination: str, revision: str = "main") -> dict:
    from huggingface_hub import HfApi, snapshot_download
    sha = HfApi().model_info(repo_id, revision=revision).sha
    snapshot_download(repo_id, revision=sha, local_dir=destination)
    result = {"model_id": repo_id, "revision": sha, "local_path": str(Path(destination).resolve())}
    write_json(Path(destination) / "op2_snapshot.json", result)
    return result


def input_fingerprint(record: dict) -> str:
    """Cache identity includes source provenance and grouping metadata for safe exports."""
    material = {key: record.get(key) for key in ["record_id", "source_hash", "source_kind", "dataset_revision", "source_id", "cluster_id", "exact_duplicate_of", "original_row_hash", "provenance", "multimedia"]}
    return hashlib.sha256(json.dumps(material, sort_keys=True).encode()).hexdigest()
