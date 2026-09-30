from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

from .schemas import TASK_MODELS


class ConfigModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class APIConfig(ConfigModel):
    endpoints: list[str] = Field(default_factory=lambda: ["http://127.0.0.1:8000/v1"], min_length=1)
    model: str = "clinical-extractor"
    model_id: str = "unspecified"
    revision: str = "UNPINNED"
    api_key_env: str = "OPENAI_API_KEY"
    response_format: Literal["json_schema", "json_object", "vllm_structured_outputs", "prompt_json"] = "json_schema"
    schema_profile: Literal["standard", "cohere"] = "standard"
    timeout_seconds: float = Field(default=600, gt=0)
    http_retries: int = Field(default=3, ge=0, le=10)
    temperature: float = Field(default=0.2, ge=0, le=2)
    top_p: float = Field(default=0.95, gt=0, le=1)
    max_tokens: int = Field(default=4096, ge=128)
    max_retry_tokens: int = Field(default=16384, ge=128)
    seed: int | None = None
    save_reasoning: bool = True
    save_messages: bool = True
    extra_body: dict = Field(default_factory=dict)
    metrics_urls: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def reserved_fields(self):
        reserved = {"model", "messages", "stream", "stream_options", "response_format", "structured_outputs",
                    "max_tokens", "max_completion_tokens", "temperature", "top_p", "n", "seed"}
        if reserved & self.extra_body.keys():
            raise ValueError(f"extra_body may not override {sorted(reserved & self.extra_body.keys())}")
        if any(not x.startswith(("http://", "https://")) for x in self.endpoints):
            raise ValueError("Use HTTP(S) OpenAI-compatible base URLs ending in /v1")
        if self.max_retry_tokens < self.max_tokens:
            raise ValueError("max_retry_tokens must be >= max_tokens")
        return self

    def identity(self) -> dict:
        # Alias and endpoint alone are insufficient: supply actual checkpoint revision.
        return {"model": self.model, "model_id": self.model_id, "revision": self.revision}

    def generation(self) -> dict:
        return {k: getattr(self, k) for k in ["temperature", "top_p", "max_tokens", "max_retry_tokens",
                                              "extra_body", "response_format", "schema_profile", "seed", "save_reasoning", "save_messages"]}


class PipelineConfig(ConfigModel):
    api: APIConfig = Field(default_factory=APIConfig)
    input: str = "data/prepared.jsonl"
    output: str = "runs/production"
    tasks: list[str] = Field(default_factory=lambda: list(TASK_MODELS))
    patient_concurrency: int = Field(default=8, ge=1, le=1024)
    task_fanout: int = Field(default=2, ge=1, le=32)
    request_concurrency: int = Field(default=32, ge=1, le=4096)
    export_after_run: bool = True
    validation_retries: int = Field(default=1, ge=0, le=5)
    recover_citation_formatting: bool = True
    recover_identical_duplicates: bool = True
    targeted_item_repair: bool = True
    max_model_len: int = Field(default=65536, ge=1024)
    context_safety_tokens: int = Field(default=512, ge=0)
    token_profile: str | None = None
    require_token_profile: bool = True
    allow_unpinned_revision: bool = False
    namespace: str = "production-v2"
    cache_mode: Literal["locality", "isolated_requests"] = "locality"
    shuffle_seed: int | None = None

    @model_validator(mode="after")
    def task_names(self):
        if not self.tasks or len(self.tasks) != len(set(self.tasks)):
            raise ValueError("tasks must be nonempty and unique")
        if set(self.tasks) - TASK_MODELS.keys():
            raise ValueError(f"Unknown tasks: {set(self.tasks) - TASK_MODELS.keys()}")
        if "case_context" not in self.tasks:
            raise ValueError("case_context is required for subject/provenance gating")
        return self

    def digest(self) -> str:
        # Execution limits do not invalidate individual tasks, but belong in run provenance.
        return hashlib.sha256(json.dumps(self.model_dump(), sort_keys=True).encode()).hexdigest()


def load_config(path: str | Path) -> PipelineConfig:
    with open(path, encoding="utf-8") as f:
        raw = yaml.safe_load(f)
    return PipelineConfig.model_validate(raw or {})
