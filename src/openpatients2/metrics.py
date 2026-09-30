from __future__ import annotations

import math
from collections import Counter


def percentiles(values: list[float | int | None]) -> dict:
    seq = sorted(x for x in values if x is not None)
    def p(q):
        if not seq:
            return None
        position = (len(seq) - 1) * q
        low = math.floor(position)
        high = math.ceil(position)
        return seq[low] + (seq[high] - seq[low]) * (position - low)
    return {"n": len(seq), "min": p(0), "p50": p(.5), "p95": p(.95), "p99": p(.99), "max": p(1)}


def summarize(attempts: list[dict], wall_seconds: float, completed: int, failed: int, resumed: int = 0) -> dict:
    n = len(attempts)
    def known_total(key):
        vals = [x.get(key) for x in attempts]
        return sum(vals) if vals and all(v is not None for v in vals) else None
    prompt, output, cached = (known_total(k) for k in ["prompt_tokens", "completion_tokens", "cached_tokens"])
    cache_valid = cached is not None and prompt is not None and 0 <= cached <= prompt
    uncached = prompt - cached if cache_valid else None
    return {
        "wall_seconds": wall_seconds, "new_complete_records": completed, "failed_records": failed,
        "fully_resumed_records": resumed, "attempts": n,
        "validated_new_records_per_hour": completed * 3600 / wall_seconds if wall_seconds else None,
        "logical_prompt_tokens": prompt, "output_tokens_including_reasoning": output,
        "cached_prompt_tokens": cached, "uncached_prompt_tokens": uncached,
        "logical_input_tokens_per_second": prompt / wall_seconds if prompt is not None and wall_seconds else None,
        "uncached_input_tokens_per_wall_second": uncached / wall_seconds if uncached is not None and wall_seconds else None,
        "output_tokens_per_second": output / wall_seconds if output is not None and wall_seconds else None,
        "cache_hit_fraction": cached / prompt if cache_valid and prompt else None,
        "http_error_rate": sum(bool(x.get("error")) for x in attempts) / n if n else None,
        "length_finish_rate": sum(x.get("finish_reason") == "length" for x in attempts) / n if n else None,
        "attempt_validation_failure_rate": sum(x.get("validation_passed") is False for x in attempts) / n if n else None,
        "retry_attempts": sum(bool(x.get("is_retry")) for x in attempts),
        "ttft_seconds": percentiles([x.get("ttft_seconds") for x in attempts]),
        "time_to_final_answer_start_seconds": percentiles([x.get("ttfa_seconds") for x in attempts]),
        "request_latency_seconds": percentiles([x.get("latency_seconds") for x in attempts]),
        "finish_reasons": dict(Counter(str(x.get("finish_reason")) for x in attempts)),
        "notes": ["TTFT includes queueing, scheduling, prefill, and first decode; it is NOT isolated prefill time.",
                  "Logical input TPS can include cache hits; do not call it physical prefill throughput.",
                  "Completion tokens already include reasoning when reported that way by the API; never add reasoning twice.",
                  "Missing usage/cached-token metrics remain null, not zero.",
                  "Source/structural validation is not clinical adjudication; quality-adjusted clinical throughput requires gold labels."]}
