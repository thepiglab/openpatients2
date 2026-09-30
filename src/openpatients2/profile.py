from __future__ import annotations

import hashlib
import json
from pathlib import Path

from .config import PipelineConfig
from .data import records, write_json, input_fingerprint
from .metrics import percentiles
from .prompts import messages_for, task_signature


def profile_fingerprint(config: PipelineConfig) -> str:
    payload = {"identity": config.api.identity(), "tasks": {
        task: task_signature(task, config.api.identity(), {"scope": "token_profile"}) for task in config.tasks},
        "chat_template_kwargs": config.api.extra_body.get("chat_template_kwargs", {}),
        "top_level_reasoning_effort": config.api.extra_body.get("reasoning_effort")}
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


def tokenize_record(row: dict, config: PipelineConfig, tokenizer) -> dict:
    chat_kwargs = dict(config.api.extra_body.get("chat_template_kwargs", {}))
    if "reasoning_effort" in config.api.extra_body:
        chat_kwargs.setdefault("reasoning_effort", config.api.extra_body["reasoning_effort"])
    counts = {}
    for task in config.tasks:
        ids = tokenizer.apply_chat_template(messages_for(row, task, config.namespace), tokenize=True,
                                             add_generation_prompt=True, **chat_kwargs)
        counts[task] = len(ids)
    return {"record_id": row["record_id"], "source_hash": row["source_hash"], "input_fingerprint": input_fingerprint(row),
            "note_tokens": len(tokenizer.encode(row["text"], add_special_tokens=False)),
            "note_characters": len(row["text"]), "prompt_tokens": counts}


def create_profile(config: PipelineConfig, tokenizer_path: str, destination: str, workers: int = 1) -> dict:
    # Optional heavy imports are confined to CPU preparation. Tokenizer files must be local.
    from transformers import AutoTokenizer
    tokenizer = AutoTokenizer.from_pretrained(tokenizer_path, local_files_only=True, trust_remote_code=True)
    # Fast tokenizers parallelize internally. A thread pool preserves one tokenizer object;
    # no GPU context or tensor library is intentionally initialized by this command.
    from concurrent.futures import ThreadPoolExecutor
    path = Path(destination)
    path.parent.mkdir(parents=True, exist_ok=True)
    notes, prompts, characters = [], [], []
    with ThreadPoolExecutor(max_workers=workers) as executor, path.open("w", encoding="utf-8") as out:
        # Bounded batches avoid Executor.map consuming the whole input on Python <3.14.
        from itertools import islice
        iterator = iter(records(config.input))
        while batch := list(islice(iterator, 256)):
            for item in executor.map(lambda r: tokenize_record(r, config, tokenizer), batch):
                out.write(json.dumps(item) + "\n")
                notes.append(item["note_tokens"])
                characters.append(item["note_characters"])
                prompts.extend(item["prompt_tokens"].values())
    result = {"fingerprint": profile_fingerprint(config), "tokenizer_path": str(Path(tokenizer_path).resolve()),
              "records": len(notes), "note_tokens": percentiles(notes), "note_characters": percentiles(characters),
              "full_prompt_tokens": percentiles(prompts), "note_tokens_total": sum(notes),
              "logical_prompt_tokens_all_tasks_total": sum(prompts),
              "model_identity": config.api.identity(), "note": "Counts rendered chat template, not character/4 estimates."}
    write_json(path.with_suffix(path.suffix + ".manifest.json"), result)
    return result


class TokenGuard:
    def __init__(self, config: PipelineConfig):
        self.config = config
        self.rows = {}
        if config.token_profile:
            from .data import read_jsonl
            path = Path(config.token_profile)
            meta = json.loads(path.with_suffix(path.suffix + ".manifest.json").read_text())
            if meta["fingerprint"] != profile_fingerprint(config):
                raise ValueError("Stale token profile: model revision, templates, tasks, or generation config changed")
            self.rows = {x["record_id"]: x for x in read_jsonl(path)}
        elif config.require_token_profile:
            raise ValueError("Run CPU `op2 profile` first; production never silently truncates a record")

    def budget(self, row: dict, task: str, requested: int, repair: bool = False, extra_prompt_tokens: int | None = None) -> int:
        if not self.rows:
            return min(requested, self.config.max_model_len - self.config.context_safety_tokens)
        item = self.rows.get(row["record_id"])
        if not item or item["source_hash"] != row["source_hash"] or item.get("input_fingerprint") != input_fingerprint(row):
            raise ValueError("Record absent from token profile or source changed")
        prompt = item["prompt_tokens"].get(task)
        if prompt is None:
            raise ValueError("Task missing from token profile")
        # Repair suffix is bounded by characters; conservative extra allowance. Never truncate source.
        overhead = extra_prompt_tokens if extra_prompt_tokens is not None else (1024 if repair else 0)
        available = self.config.max_model_len - prompt - self.config.context_safety_tokens - overhead
        if available < requested:
            raise ValueError(f"context_overflow: need {prompt}+{requested}+safety, limit {self.config.max_model_len}")
        return requested
