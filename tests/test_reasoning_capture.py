import copy
import json
import sqlite3
from pathlib import Path

import httpx
import pytest

from openpatients2.client import APIClient, Completion
from openpatients2.pipeline import run_pipeline
from openpatients2.demo import transport
from openpatients2.data import read_jsonl
from openpatients2.distill import export_distillation, review_template
from openpatients2.profile import profile_fingerprint


def test_reasoning_aliases_count_once_and_preserve_raw():
    c=Completion()
    c.capture_reasoning({"reasoning":"alpha ","reasoning_content":"alpha "})
    c.capture_reasoning({"reasoning":"beta"})
    assert c.reasoning_text=="alpha beta"
    assert c.reasoning_characters==10
    assert c.reasoning_fields["reasoning_content"]=="alpha "
    assert c.response()["reasoning_status"]=="returned_text"
    assert "reasoning_text" not in c.metrics()
    assert "reasoning_fields" not in c.metrics()


def test_missing_empty_opaque_and_disabled_reasoning_are_distinct():
    c=Completion()
    assert c.response()["reasoning_status"]=="not_returned"
    c.capture_reasoning({"reasoning":None})
    assert c.response()["reasoning_status"]=="empty_field"
    c.capture_reasoning({"reasoning_details":[{"type":"encrypted","data":"opaque"}]})
    assert c.response()["reasoning_status"]=="returned_nontext"
    assert c.reasoning_text is None
    disabled=Completion(reasoning_saved=False)
    disabled.capture_reasoning({"reasoning_content":"returned but opted out"})
    assert disabled.reasoning_text is None
    assert disabled.reasoning_fields=={}
    assert disabled.reasoning_characters>0
    assert disabled.response()["reasoning_status"]=="disabled_by_client"


@pytest.mark.asyncio
async def test_reasoning_saved_next_to_extraction_and_patient_row(config):
    async with httpx.AsyncClient(transport=transport()) as http:
        await run_pipeline(config,client=APIClient(config.api,http))
    rows=list(read_jsonl(Path(config.output)/"extractions.jsonl"))
    assert len(rows)==14
    assert all(x["reasoning_text"]=="mock" for x in rows)
    assert all(x["generation"]["request"]["messages"] for x in rows)
    assert all(x["attempt_summary"]["attempt_count"]==1 for x in rows)
    patient=next(read_jsonl(Path(config.output)/"patients.jsonl"))
    assert patient["generations"]["conditions"]["reasoning_text"]=="mock"
    assert "reasoning_text" not in patient["sections"]["conditions"]
    assert "request" not in patient["generations"]["conditions"]


@pytest.mark.asyncio
async def test_failed_retry_reasoning_is_preserved_without_mixing(config,sections):
    config.tasks=["case_context"]
    calls=[]
    async def handler(request):
        calls.append(json.loads(request.content))
        n=len(calls)
        events=[{"choices":[{"delta":{"reasoning":"attempt-"+str(n)},"finish_reason":None}]},
                {"choices":[{"delta":{"content":"{" if n==1 else json.dumps(sections["case_context"])},
                             "finish_reason":"length" if n==1 else "stop"}]}]
        return httpx.Response(200,text="".join("data: "+json.dumps(x)+"\n\n" for x in events)+"data: [DONE]\n\n")
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        await run_pipeline(config,client=APIClient(config.api,http))
    row=next(read_jsonl(Path(config.output)/"extractions.jsonl"))
    assert row["reasoning_text"]=="attempt-2"
    attempts=list(read_jsonl(Path(config.output)/"attempts.jsonl"))
    assert [x["generation"]["reasoning_text"] for x in attempts]==["attempt-1","attempt-2"]
    assert row["attempt_summary"]["attempt_count"]==2
    assert row["attempt_summary"]["first_attempt_source_valid"] is False


@pytest.mark.asyncio
async def test_distillation_review_required_and_reasoning_opt_in(config,tmp_path):
    async with httpx.AsyncClient(transport=transport()) as http:
        await run_pipeline(config,client=APIClient(config.api,http))
    out=str(tmp_path/"distill.jsonl")
    with pytest.raises(ValueError,match="reviews"):
        export_distillation(config.output,out)
    result=export_distillation(config.output,out,allow_unreviewed=True)
    assert result["written"]==14 and result["empty_targets"]>0
    rows=list(read_jsonl(out))
    assert all(x["reasoning_text"] is None for x in rows)
    assert len({x["split"] for x in rows})==1
    reviews=str(tmp_path/"review.jsonl")
    review_template(config.output,reviews)
    labels=list(read_jsonl(reviews))
    assert not any(x["approved"] for x in labels)
    result=export_distillation(config.output,out,reviews=reviews)
    assert result["written"]==0
    for item in labels:
        item.update(approved=True,reviewer="synthetic-test-reviewer",reasoning_approved=False)
    Path(reviews).write_text("".join(json.dumps(x)+"\n" for x in labels))
    result=export_distillation(config.output,out,reviews=reviews,include_reasoning=True)
    assert result["written"]==0 and result["reasoning_not_reviewed"]==14
    for item in labels: item["reasoning_approved"]=True
    Path(reviews).write_text("".join(json.dumps(x)+"\n" for x in labels))
    result=export_distillation(config.output,out,reviews=reviews,include_reasoning=True)
    assert result["written"]==14
    assert all(x["messages"][-1]["reasoning"]=="mock" for x in read_jsonl(out))


@pytest.mark.asyncio
async def test_stale_review_cannot_approve_changed_source(config,tmp_path):
    async with httpx.AsyncClient(transport=transport()) as http:
        await run_pipeline(config,client=APIClient(config.api,http))
    reviews=tmp_path/"reviews.jsonl"
    review_template(config.output,str(reviews))
    values=list(read_jsonl(reviews))
    for item in values: item.update(approved=True,reviewer="test",input_hash="STALE")
    reviews.write_text("".join(json.dumps(x)+"\n" for x in values))
    result=export_distillation(config.output,str(tmp_path/"out.jsonl"),reviews=str(reviews))
    assert result["written"]==0


def test_token_profile_independent_of_seed_but_not_effort(config):
    before=profile_fingerprint(config)
    config.api.seed=99
    assert profile_fingerprint(config)==before
    config.api.extra_body={"chat_template_kwargs":{"reasoning_effort":"high"}}
    assert profile_fingerprint(config)!=before
