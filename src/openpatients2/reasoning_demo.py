"""Synthetic fixtures exercise the reasoning study plumbing, NOT a real LLM/GPU."""
from __future__ import annotations

import copy
import json
from pathlib import Path

import httpx
import yaml

from .client import APIClient
from .config import APIConfig, PipelineConfig
from .data import normalize, write_json
from .demo import SOURCE, empty_section, fixture_sections
from .reasoning_study import prepare_study, run_study, analyze_study


def demo_cases(count: int = 6) -> tuple[list[dict],dict[str,dict]]:
    rows,answers=[],{}
    for i in range(count):
        rid=f"synthetic-reasoning-{i:03d}"
        source=SOURCE
        sections=fixture_sections()
        if i%2:
            source=source.replace("He reported no known drug allergies. ","")
            sections["allergies"]=empty_section("allergies")
        source+=f" Synthetic fixture label {i}; this is not a real clinical record."
        rows.append(normalize({"record_id":rid,"text":source,"source_kind":"synthetic_fixture",
                               "dataset_revision":"reasoning-fixture-v1","cluster_id":f"fixture-group-{i//2}"}))
        answers[rid]=sections
    return rows,answers


async def run_reasoning_demo(output: str) -> dict:
    root=Path(output)
    if (root/"study"/"study.json").exists():
        raise ValueError("Use a fresh reasoning-demo output directory")
    root.mkdir(parents=True,exist_ok=True)
    inputs,answers=demo_cases()
    source=root/"input.jsonl"
    source.write_text("".join(json.dumps(x)+"\n" for x in inputs))
    gold=root/"gold.jsonl"
    labels=[]
    for record in inputs:
        for task in ["conditions","allergies"]:
            section=answers[record["record_id"]][task]
            labels.append({"record_id":record["record_id"],"task":task,"collection":"items",
                           "expected":section["items"],"closed_world":True,
                           "documentation_status":section["documentation_status"],
                           "annotation_provenance":"hand-authored synthetic software fixture, NOT clinician adjudication"})
    gold.write_text("".join(json.dumps(x)+"\n" for x in labels))
    config=PipelineConfig(input=str(source),output=str(root/"unused"),require_token_profile=False,
        api=APIConfig(endpoints=["http://synthetic.invalid/v1"],model="MOCK-NOT-A-MODEL",
                      model_id="SYNTHETIC-FIXTURE",revision="fixture-v1"),
        patient_concurrency=2,task_fanout=2)
    config_path=root/"pipeline.yaml"
    config_path.write_text(yaml.safe_dump(config.model_dump()))
    spec={"pipeline":str(config_path),"output":str(root/"study"),"sample_size":len(inputs),
          "levels":[{"name":name,"value":name} for name in ["low","medium","high"]],
          "seeds":[17,43],"bootstrap_resamples":200,"permutation_resamples":199,
          "primary_difference_metrics":["source_valid","clinical_f1","negative_case_success"],
          "gold":str(gold),"notes":"MOCK ONLY; no model, clinical inference, or GPU performance measured."}
    spec_path=root/"study-config.yaml";spec_path.write_text(yaml.safe_dump(spec))
    prepare_study(str(spec_path))
    async def handler(request):
        body=json.loads(request.content)
        user=body["messages"][-1]["content"]
        rid=user.split("RECORD_ID: ")[1].split("\n")[0]
        task=user.split("EXTRACTION_TASK: ")[1].split("\n")[0]
        effort=body["chat_template_kwargs"]["reasoning_effort"]
        data=copy.deepcopy(answers[rid][task])
        # Deliberately induce ONE omission and ONE seed-varying omission to test reports.
        if effort=="low" and task=="conditions" and rid.endswith("000"):
            data=empty_section(task)
        if effort=="medium" and body.get("seed")==43 and task=="allergies" and rid.endswith("002"):
            data=empty_section(task)
        trace=f"SYNTHETIC MOCK TRACE: requested mode={effort}; no real reasoning/inference performed."
        events=[{"id":"synthetic-id","model":"MOCK-NOT-A-MODEL","choices":[{"delta":{"reasoning":trace}}]},
                {"choices":[{"delta":{"content":json.dumps(data)},"finish_reason":"stop"}]}]
        return httpx.Response(200,text="".join("data: "+json.dumps(x)+"\n\n" for x in events)+"data: [DONE]\n\n")
    # One reusable mock HTTP client, with no actual outbound network calls.
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        await run_study(str(root/"study"),client_factory=lambda cfg:APIClient(cfg.api,http),warmup=False)
    design=json.loads((root/"study"/"study.json").read_text())
    design["demonstration_only"]=True
    for cell in design["runs"]:
        report=cell["report"]
        report["device_benchmark_status"]="SYNTHETIC_MOCK_NO_GPU_NO_CLINICAL_MODEL"
        for key in ["validated_new_records_per_hour","wall_seconds"]:
            report[key]=None
        write_json(root/"study"/cell["directory"]/"report.json",report)
    write_json(root/"study"/"study.json",design)
    result=analyze_study(str(root/"study"))
    return {"mode":"SYNTHETIC SOFTWARE DEMO ONLY, not a model/clinical/throughput evaluation",**result}
