import asyncio
import copy
import json
from pathlib import Path

import httpx
import pytest
import yaml

from openpatients2.client import APIClient
from openpatients2.config import PipelineConfig
from openpatients2.data import read_jsonl
from openpatients2.demo import transport
from openpatients2.reasoning_study import StudyConfig, prepare_study, run_study, analyze_study


def make_study(tmp_path, config):
    config.tasks=["case_context","conditions","allergies"]
    pipeline=tmp_path/"base.yaml";pipeline.write_text(yaml.safe_dump(config.model_dump()))
    output=tmp_path/"study"
    spec={"pipeline":str(pipeline),"output":str(output),"sample_size":1,
          "levels":[{"name":"low","value":"low"},{"name":"high","value":"high"}],
          "seeds":[1,2],"bootstrap_resamples":100,"permutation_resamples":100}
    path=tmp_path/"study.yaml";path.write_text(yaml.safe_dump(spec))
    prepare_study(str(path))
    return output,path


def test_study_design_equal_budgets_and_only_effort_and_seed_change(tmp_path,config):
    root,path=make_study(tmp_path,config)
    design=json.loads((root/"study.json").read_text())
    assert len(design["schedule"])==4
    assert [x["repeat"] for x in design["schedule"]]==[0,0,1,1]
    cfgs=[yaml.safe_load((root/x["config"]).read_text()) for x in design["schedule"]]
    assert all(x["api"]["max_tokens"]==32768==x["api"]["max_retry_tokens"] for x in cfgs)
    assert all(x["validation_retries"]==0 for x in cfgs)
    assert len({x["namespace"] for x in cfgs})==4
    assert len({x["input"] for x in cfgs})==1
    with pytest.raises(ValueError,match="already exists"):prepare_study(str(path))


def test_study_refuses_arbitrary_model_control():
    with pytest.raises(ValueError,match="reasoning controls"):
        StudyConfig(pipeline="unused",control_path="model",levels=[{"name":"a","value":"b"}])
    with pytest.raises(ValueError,match="exactly one level"):
        StudyConfig(pipeline="unused",control_path=None,levels=[{"name":"low","value":"low"},{"name":"high","value":"high"}])


@pytest.mark.asyncio
async def test_study_e2e_strictly_within_one_model_and_resume(tmp_path,config):
    root,path=make_study(tmp_path,config)
    calls=[]
    async with httpx.AsyncClient(transport=transport(calls=calls)) as http:
        factory=lambda cfg:APIClient(cfg.api,http)
        result=await run_study(str(root),client_factory=factory,warmup=False)
        assert result["completed_runs"]==4
        assert len(calls)==12
        await run_study(str(root),client_factory=factory,warmup=False)
        assert len(calls)==12
    result=analyze_study(str(root))
    report=json.loads(Path(result["report"]).read_text())
    assert report["within_model_only"]
    assert not report["clinical_equivalence_established"]
    assert report["pairwise_agreement"][0]["overall"]["fact_set_dice"]["mean"]==1
    assert len(report["same_level_repeatability"])==2
    assert all(x["p_value"] is None for x in report["paired_differences"]) # One source cluster.


@pytest.mark.asyncio
async def test_frozen_sample_edit_rejected(tmp_path,config):
    root,_=make_study(tmp_path,config)
    with (root/"sample.jsonl").open("a") as f:f.write("\n")
    with pytest.raises(ValueError,match="sample was modified"):
        await run_study(str(root),warmup=False)


@pytest.mark.asyncio
async def test_edited_effort_config_rejected_before_http(tmp_path,config):
    root,_=make_study(tmp_path,config)
    design=json.loads((root/"study.json").read_text())
    path=root/design["schedule"][0]["config"]
    cfg=yaml.safe_load(path.read_text());cfg["api"]["temperature"]=1.0
    path.write_text(yaml.safe_dump(cfg))
    with pytest.raises(ValueError,match="config changed"):
        await run_study(str(root),warmup=False)


def test_incomplete_study_does_not_silently_report_success(tmp_path,config):
    root,_=make_study(tmp_path,config)
    with pytest.raises(ValueError,match="incomplete"):
        analyze_study(str(root))
    result=analyze_study(str(root),allow_partial=True)
    report=json.loads(Path(result["report"]).read_text())
    assert report["is_partial"] and report["completed_runs"]==0


@pytest.mark.asyncio
async def test_analysis_refuses_different_model_manifest(tmp_path,config):
    root,_=make_study(tmp_path,config)
    async with httpx.AsyncClient(transport=transport()) as http:
        await run_study(str(root),client_factory=lambda cfg:APIClient(cfg.api,http),warmup=False)
    design=json.loads((root/"study.json").read_text())
    cell=design["runs"][0]
    path=root/cell["directory"]/f"manifest-{cell['run_id']}.json"
    manifest=json.loads(path.read_text());manifest["config"]["api"]["model_id"]="OTHER-MODEL"
    path.write_text(json.dumps(manifest))
    with pytest.raises(ValueError,match="different models"):
        analyze_study(str(root))


@pytest.mark.asyncio
async def test_gold_freeze_and_external_adjudication_provenance(tmp_path,config):
    root,path=make_study(tmp_path,config)
    spec=yaml.safe_load(path.read_text())
    spec["output"]=str(tmp_path/"study-with-gold")
    gold=tmp_path/"labels.jsonl"
    gold.write_text(json.dumps({"record_id":"outside-the-sample","task":"conditions","collection":"items","expected":[],"closed_world":True})+"\n")
    spec["gold"]=str(gold)
    path.write_text(yaml.safe_dump(spec))
    prepare_study(str(path))
    root=Path(spec["output"])
    design=json.loads((root/"study.json").read_text())
    assert design["gold_sha256"]
    assert (root/"frozen-gold.jsonl").read_bytes()==gold.read_bytes()
    async with httpx.AsyncClient(transport=transport()) as http:
        await run_study(str(root),client_factory=lambda cfg:APIClient(cfg.api,http),warmup=False)
    analyzed=analyze_study(str(root))
    report=json.loads(Path(analyzed["report"]).read_text())
    assert report["gold_supplied"] and not report["gold_provenance"]["external_post_preparation_override"]
    (root/"frozen-gold.jsonl").write_text("\n")
    with pytest.raises(ValueError,match="Frozen gold"):
        analyze_study(str(root))
    analyzed=analyze_study(str(root),gold_override=str(gold))
    report=json.loads(Path(analyzed["report"]).read_text())
    assert report["gold_provenance"]["external_post_preparation_override"]


def test_grouping_metadata_changes_input_provenance_hash(row):
    from openpatients2.data import input_fingerprint
    changed={**row,"cluster_id":"reviewed-related-source"}
    assert input_fingerprint(row)!=input_fingerprint(changed)


def test_study_checks_context_capacity_during_cpu_preparation(tmp_path,config,monkeypatch):
    from openpatients2 import profile
    _,path=make_study(tmp_path,config)
    spec=yaml.safe_load(path.read_text());spec["output"]=str(tmp_path/"overflow-study")
    path.write_text(yaml.safe_dump(spec))
    monkeypatch.setattr(profile,"create_profile",lambda *a,**kw:{})
    class Guard:
        def __init__(self,cfg):pass
        def budget(self,*a,**kw):raise ValueError("context_overflow: before GPU allocation")
    monkeypatch.setattr(profile,"TokenGuard",Guard)
    with pytest.raises(ValueError,match="before GPU allocation"):
        prepare_study(str(path),tokenizer="fake-local-tokenizer")
    assert not (Path(spec["output"])/"study.json").exists()
