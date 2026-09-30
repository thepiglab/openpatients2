"""Export review-gated distillation candidates, not clinically certified training data."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from .agreement import cluster_map
from .data import read_jsonl, write_json
from .exports import export_run


def review_template(run: str, output: str) -> dict:
    export_run(run)
    path = Path(output)
    if path.exists():
        raise ValueError("Review file already exists; refusing to overwrite adjudications")
    path.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with path.open("w",encoding="utf-8") as f:
        path.chmod(0o600)
        for row in read_jsonl(Path(run)/"extractions.jsonl"):
            if row["status"] != "valid":
                continue
            review={k:row[k] for k in ["record_id","task","task_signature","input_hash"]}
            review.update({"approved":False,"reasoning_approved":False,"reviewer":None,"notes":None})
            f.write(json.dumps(review)+"\n")
            count += 1
    return {"review_rows":count,"output":str(path),"default_approved":False}


def export_distillation(run: str, output: str, reviews: str | None = None,
                        include_reasoning: bool = False, allow_unreviewed: bool = False) -> dict:
    """Answer-only is the default target; optional reasoning is separate, never fabricated.

    Empty supported sections are retained. Related source IDs, declared cluster_id,
    and exact duplicate notes share a split. Near duplicates require upstream review.
    """
    if not reviews and not allow_unreviewed:
        raise ValueError("Supply --reviews or explicitly choose --allow-unreviewed-candidates")
    export_run(run)
    root=Path(run)
    review_map={}
    if reviews:
        for item in read_jsonl(reviews):
            fields=("record_id","task","task_signature","input_hash")
            if any(k not in item for k in fields):
                raise ValueError("Review must bind exact record, task, prompt/schema signature and input hash")
            key=tuple(item[k] for k in fields)
            if key in review_map:
                raise ValueError("Duplicate review key")
            review_map[key]=item
    # Source map is small compared with prompts; stream bulky task outputs below.
    sources={row["source"]["record_id"]:{k:v for k,v in row["source"].items() if k!="text"}
             for row in read_jsonl(root/"patients.jsonl")}
    clusters=cluster_map(sources)
    path=Path(output)
    if path.resolve() in {(root/name).resolve() for name in ["patients.jsonl","extractions.jsonl","attempts.jsonl"]}:
        raise ValueError("Distillation output may not overwrite an extraction export")
    path.parent.mkdir(parents=True,exist_ok=True)
    counts={"written":0,"failed_or_incomplete":0,"not_reviewed":0,"no_prompt":0,
            "reasoning_not_returned":0,"reasoning_not_reviewed":0,"empty_targets":0}
    split_counts={"train":0,"validation":0,"test":0}
    with path.open("w",encoding="utf-8") as out:
        path.chmod(0o600)
        for row in read_jsonl(root/"extractions.jsonl"):
            generation=row.get("generation") or {}
            if row["status"]!="valid" or generation.get("finish_reason")!="stop" or generation.get("error"):
                counts["failed_or_incomplete"]+=1
                continue
            key=tuple(row[k] for k in ("record_id","task","task_signature","input_hash"))
            review=review_map.get(key,{})
            approved=review.get("approved") is True and isinstance(review.get("reviewer"),str) and bool(review["reviewer"].strip())
            if not approved and not allow_unreviewed:
                counts["not_reviewed"]+=1
                continue
            request=generation.get("request") or {}
            if not request.get("messages"):
                counts["no_prompt"]+=1
                continue
            reasoning=row.get("reasoning_text")
            if include_reasoning and not reasoning:
                counts["reasoning_not_returned"]+=1
                continue
            if include_reasoning and approved and review.get("reasoning_approved") is not True and not allow_unreviewed:
                counts["reasoning_not_reviewed"]+=1
                continue
            cluster=clusters[row["record_id"]]
            bucket=int(hashlib.sha256(cluster.encode()).hexdigest()[:8],16)%100
            split="train" if bucket<80 else "validation" if bucket<90 else "test"
            section=row["extraction"]
            empty=not any(section.get(k) for k in ["items","tumors","biomarkers","treatments","evidence"])
            counts["empty_targets"]+=int(empty)
            assistant={"role":"assistant","content":generation["content"]}
            if include_reasoning:
                assistant["reasoning"]=reasoning
            data={"record_id":row["record_id"],"task":row["task"],"split":split,"source_cluster":cluster,
                  "source":row["source"],"input_hash":row["input_hash"],"task_signature":row["task_signature"],
                  "teacher":row["model"],"teacher_parameters":request.get("parameters"),
                  "messages":[*request["messages"],assistant],
                  "extraction":section,"reasoning_text":reasoning if include_reasoning else None,
                  "target_kind":"reasoning_and_answer" if include_reasoning else "answer_only",
                  "review_status":"reviewed" if approved else "UNREVIEWED_CANDIDATE",
                  "reasoning_reviewed":bool(approved and review.get("reasoning_approved") is True),
                  "review":review or None,"source_validation":row["validation"],
                  "training_note":"Map assistant.reasoning to the STUDENT chat template. Never train source/system tokens as targets."}
            out.write(json.dumps(data,ensure_ascii=False,allow_nan=False)+"\n")
            counts["written"]+=1
            split_counts[split]+=1
    report={**counts,"splits":split_counts,"output":str(path),"include_reasoning":include_reasoning,
            "review_required":not allow_unreviewed,
            "warning":"Teacher reasoning and exact quote matching do not establish clinical truth. Validate labels and licensing. Group near duplicates and related cases before using these splits."}
    write_json(str(path)+".manifest.json",report)
    return report
