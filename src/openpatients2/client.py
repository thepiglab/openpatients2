from __future__ import annotations

import asyncio
import json
import os
import time
from dataclasses import asdict, dataclass, field
from typing import AsyncIterator

import httpx

from .config import APIConfig
from .schemas import wire_schema, provider_schema


@dataclass
class Completion:
    content: str = ""
    finish_reason: str | None = None
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    cached_tokens: int | None = None
    reasoning_tokens: int | None = None
    latency_seconds: float = 0.0
    ttft_seconds: float | None = None
    ttfa_seconds: float | None = None
    reasoning_characters: int = 0
    reasoning_text: str | None = None
    reasoning_fields: dict = field(default_factory=dict)
    reasoning_field_seen: bool = False
    reasoning_saved: bool = True
    response_id: str | None = None
    response_model: str | None = None
    provider: str | None = None
    system_fingerprint: str | None = None
    http_status: int | None = None
    error: str | None = None
    usage: dict = field(default_factory=dict)

    def metrics(self) -> dict:
        return {k: v for k, v in asdict(self).items() if k not in {"content", "usage", "reasoning_text", "reasoning_fields"}}


    def response(self) -> dict:
        """Only endpoint-returned reasoning; never reconstruct missing/internal thoughts."""
        state = ("disabled_by_client" if not self.reasoning_saved else
                 "returned_text" if self.reasoning_text else
                 "returned_nontext" if any(self.reasoning_fields.values()) else
                 "empty_field" if self.reasoning_field_seen else "not_returned")
        return {"content": self.content, "reasoning_text": self.reasoning_text,
                "reasoning_fields": self.reasoning_fields, "reasoning_status": state,
                "response_id": self.response_id, "response_model": self.response_model,
                "provider": self.provider,
                "system_fingerprint": self.system_fingerprint, "finish_reason": self.finish_reason,
                "usage": self.usage, "error": self.error,
                "reasoning_provenance": "endpoint-returned output, not verified clinical evidence"}

    def capture_reasoning(self, delta: dict) -> bool:
        keys = [k for k in ("reasoning", "reasoning_content", "reasoning_details") if k in delta]
        if not keys:
            return False
        self.reasoning_field_seen = True
        # Preserve aliases/opaque blocks separately; do not stringify them into a rationale.
        if self.reasoning_saved:
            for key in keys:
                value = delta[key]
                if isinstance(value, str):
                    existing = self.reasoning_fields.get(key, "")
                    self.reasoning_fields[key] = existing + value if isinstance(existing, str) else [existing, value]
                elif value is not None:
                    existing = self.reasoning_fields.setdefault(key, [])
                    if not isinstance(existing, list):
                        existing = [existing]
                        self.reasoning_fields[key] = existing
                    existing.extend(value if isinstance(value, list) else [value])
        # The two supported vLLM field names may both be present as aliases. Count once.
        text = next((delta[k] for k in ("reasoning", "reasoning_content")
                     if isinstance(delta.get(k), str) and delta[k]), "")
        self.reasoning_characters += len(text)
        if self.reasoning_saved and text:
            self.reasoning_text = (self.reasoning_text or "") + text
        return bool(text or delta.get("reasoning_details"))


async def sse_events(response: httpx.Response) -> AsyncIterator[str]:
    parts = []
    async for line in response.aiter_lines():
        if not line:
            if parts:
                yield "\n".join(parts)
                parts.clear()
        elif line.startswith("data:"):
            parts.append(line[5:].lstrip())
    if parts:
        yield "\n".join(parts)


class APIClient:
    def __init__(self, config: APIConfig, http: httpx.AsyncClient | None = None, schema_overrides: dict[str, dict] | None = None):
        self.config = config
        self.schema_overrides = schema_overrides or {}
        self.owned = http is None
        self.http = http or httpx.AsyncClient(timeout=config.timeout_seconds,
                                             limits=httpx.Limits(max_connections=4096, max_keepalive_connections=256))

    async def close(self):
        if self.owned:
            await self.http.aclose()

    def body(self, task: str, messages: list[dict], max_tokens: int) -> dict:
        body = {"model": self.config.model, "messages": messages, "stream": True,
                "stream_options": {"include_usage": True}, "temperature": self.config.temperature,
                "top_p": self.config.top_p, "max_tokens": max_tokens, **self.config.extra_body}
        if self.config.seed is not None:
            body["seed"] = self.config.seed
        schema = provider_schema(self.schema_overrides.get(task) or wire_schema(task),self.config.schema_profile)
        if self.config.response_format == "json_schema":
            body["response_format"] = {"type": "json_schema", "json_schema": {
                "name": "op2_" + task, "strict": True, "schema": schema}}
        elif self.config.response_format == "vllm_structured_outputs":
            body["structured_outputs"] = {"json": schema}
        elif self.config.response_format == "json_object":
            body["response_format"] = {"type": "json_object"}
            # Explicitly selected compatibility mode: syntax protection is weaker,
            # application validation remains mandatory. Never silently fall back.
        return body

    async def complete_once(self, endpoint: str, task: str, messages: list[dict], max_tokens: int) -> Completion:
        result = Completion(reasoning_saved=self.config.save_reasoning)
        start = time.perf_counter()
        key = os.environ.get(self.config.api_key_env)
        headers = {"Authorization": f"Bearer {key}"} if key else {}
        try:
            async with asyncio.timeout(self.config.timeout_seconds), self.http.stream("POST", endpoint.rstrip("/") + "/chat/completions",
                                        json=self.body(task, messages, max_tokens), headers=headers) as response:
                result.http_status = response.status_code
                if response.status_code != 200:
                    # No raw response logging: API errors can contain patient text.
                    await response.aread()
                    result.error = f"http_{response.status_code}"
                    return result
                async for raw in sse_events(response):
                    if raw == "[DONE]":
                        break
                    event = json.loads(raw)
                    if event.get("error"):
                        result.error = "server_stream_error"
                        break
                    result.response_id = event.get("id") or result.response_id
                    result.response_model = event.get("model") or result.response_model
                    result.provider = event.get("provider") or result.provider
                    result.system_fingerprint = event.get("system_fingerprint") or result.system_fingerprint
                    usage = event.get("usage")
                    if usage:
                        result.usage = usage
                        result.prompt_tokens = usage.get("prompt_tokens")
                        result.completion_tokens = usage.get("completion_tokens")
                        result.cached_tokens = (usage.get("prompt_tokens_details") or {}).get("cached_tokens")
                        result.reasoning_tokens = (usage.get("completion_tokens_details") or {}).get("reasoning_tokens")
                    for choice in event.get("choices", []):
                        if choice.get("index", 0) != 0:
                            continue
                        delta = choice.get("delta") or {}
                        content = delta.get("content") or ""
                        reasoning = result.capture_reasoning(delta)
                        now = time.perf_counter() - start
                        if (content or reasoning) and result.ttft_seconds is None:
                            result.ttft_seconds = now
                        if content:
                            if not isinstance(content, str):
                                result.error = "unsupported_nontext_content"
                                break
                            if result.ttfa_seconds is None:
                                result.ttfa_seconds = now
                            result.content += content
                        if choice.get("finish_reason") is not None:
                            result.finish_reason = choice["finish_reason"]
            if not result.error and result.finish_reason is None:
                result.error = "stream_ended_without_finish_reason"
        except (httpx.HTTPError, ValueError, TimeoutError) as exc:
            result.error = type(exc).__name__
        finally:
            result.latency_seconds = time.perf_counter() - start
        return result

    @staticmethod
    def transient(result: Completion) -> bool:
        return result.http_status in {408, 429, 500, 502, 503, 504} or result.error in {
            "ReadTimeout", "ConnectTimeout", "ConnectError", "ReadError", "RemoteProtocolError",
            "TimeoutError", "total_request_deadline",
            "stream_ended_without_finish_reason", "server_stream_error"}

    async def snapshot_metrics(self) -> dict[str, str | None]:
        async def fetch(url: str):
            try:
                response = await self.http.get(url, timeout=10)
                response.raise_for_status()
                return url, response.text
            except httpx.HTTPError:
                return url, None
        return dict(await asyncio.gather(*(fetch(url) for url in self.config.metrics_urls)))
