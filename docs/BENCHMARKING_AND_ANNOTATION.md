# Benchmarking, validation, and annotation protocol

## Measure the workload before allocating a long GPU job

Download the exact checkpoint and resolve its commit SHA on CPU. Pin the pipeline config, then profile with the checkpoint's local tokenizer and actual chat template. `op2 profile` writes one record-level token-count row per case and a summary. It distinguishes note tokens from all 14 task prompts. Source text is never silently truncated. Records whose prompt plus generation budget and safety allowance exceed the serving context limit fail with `context_overflow` and must be handled explicitly.

The tokenizer stage is CPU-only and supports a bounded thread pool. Runtime source/schema validation runs off the event loop. SQLite writes are batched up to 64 mutations or the next write after one second; normal shutdown flushes. A hard kill can require replaying a small uncommitted tail. This avoids millions of per-fact fsyncs while retaining resumability. No GPU is allocated for model/dataset downloads, offline tokenization, dependency resolution, or database indexing.

## First smoke, then timed runs

`op2 smoke` tests every task schema on every configured rank endpoint. This exercises JSON constraints, the model's reasoning parser, the final-answer extraction path, and the application validators. A schema/parser incompatibility is not silently downgraded to unconstrained text. `json_object` is an explicit compatibility option, not an automatic fallback.

`op2 benchmark` runs smoke outside the timed phase by default, gives the measurement run a fresh state directory and a new cache namespace, and never counts resumed records as newly generated throughput. Repeated benchmarks therefore cannot look faster just because the state DB already has the answers. A unique namespace before the note also avoids note-KV reuse from previous configurations. Within a run, ordinary per-patient prefix reuse is retained. `--no-warmup` explicitly includes cold schema-compilation costs and should be labeled accordingly.

For a controlled no-reuse comparison, set `cache_mode: isolated_requests`; each task receives a unique prefix namespace. This prevents note-prefix reuse without claiming a backend-wide cache flush. It still permits some identical very early system tokens to share cache blocks. To disable prefix caching entirely, use a serving profile with `prefix_cache: false` and restart the server. Record which comparison was performed.

## Metrics and their interpretations

| Metric | Definition / caution |
|---|---|
| Validated new cases/hour | Fully successful configured task sets divided by observed wall time. Structural/source validity is not clinician-adjudicated validity. Production resumes are not suitable benchmark samples. |
| Logical input tokens/s | API-reported prompt tokens divided by wall time; may include cached input. |
| Uncached input tokens per wall second | Prompt minus cache-hit tokens, only when the endpoint supplies complete counters. This is not isolated GPU prefill kernel throughput. |
| Output tokens/s | API completion tokens over wall time, including reasoning when the API includes it. Reasoning is never added twice. |
| TTFT | First streamed reasoning OR final token. Includes queueing, scheduler delay, prefill and first decode. |
| Time to final-answer start | First visible JSON token. May follow extensive hidden reasoning; not TTFT and not pure prefill. |
| Request latency | Entire streaming HTTP request, reported p50/p95/p99. |
| Error/truncation/retry rates | Includes failed attempts instead of only successful runs. |
| GPU telemetry | nvidia-smi utilization, HBM use, power and device topology, when available. |
| Raw server metrics | Before/after `/metrics` snapshots, including backend-specific KV/preemption/speculation counters if exposed. |

Unknown usage and missing cached-token counts remain `null`. Streaming chunks are not tokenizer tokens. Reports do not infer draft acceptance from HTTP events. Raw Prometheus snapshots deliberately avoid blindly summing duplicated per-rank/API-server counters; inspect label semantics for the engine/version before calculating global rates.

## Recommended experiment sequence

**Phase A — correctness and feasibility:** one locally prepared sample, every schema, pinned model/image, standard KV dtype, no speculation. Check longest prompts and final JSON truncation. Fix parser/kernel/schema failures before sweeping throughput.

**Phase B — same-model topology:** K2 TP8/EP8; TP2/DP4/EP8 with external endpoints; four independent TP2 groups; then optional TP1/DP8/EP8. Sweep active cases 8, 16, 32 (add 64 only if memory and failures permit), task fanout 1–3, and batch-token budgets 16k/32k. Do not change every axis at once. The supplied campaign varies active cases with fanout fixed at 2; serving YAMLs can vary the other axes.

**Phase C — Motif:** use the Motif fork. Compare four two-GPU groups to one eight-GPU EP group. Compare MTP off/on with the *same* client and input distribution. The explicit external-rank versions preserve case affinity but need runtime validation; the vendor two-GPU internal-LB profile is provided as a feasibility reference, not a promise of rank-local cache reuse.

**Phase D — SGLang:** only after the exact NVFP4 checkpoint, B200 attention/MoE kernels and structured-output path pass smoke. Its supplied profile is experimental. Do not compare a working vLLM quantized run to a different SGLang precision and call that an engine comparison.

**Phase E — quality-gated full run:** preserve a held-out sample, deployment manifest, tokenizer revision, config hash, source hashes and prompt/schema signatures. Then run the complete corpus with bounded retries. Resume uses task signatures and source digests; changed weights or prompts invalidate old task outputs automatically.

Run multiple repetitions on the same prepared case sample. Use separate but semantically comparable model tokenizers, and compare cases/hour and clinical output quality as primary model-selection metrics. Raw token TPS across tokenizers and reasoning styles is not an apples-to-apples productivity metric. Equal output budgets can still penalize a model with a different reasoning style: log truncation and run a quality-budget sensitivity check.

Large merged exports are deferred in the K2/Motif configs. Run `op2 export --run <benchmark-directory>` on CPU before evaluating that run; its record scope and input versions are preserved separately.

## Clinical validation set

Construct a clinician-reviewed pilot of around 500–1,000 cases as an initial practical design choice, not a guaranteed statistically adequate sample. Include oncology and non-oncology; confirmed, suspected and negated disease; both sexes and explicitly unknown demographics; age units/ranges; family-only disease; multi-tumor cases; maternal/fetal/newborn cases; sparse notes; long/contradictory notes; cadaveric/anatomy examples; and each dataset source category. Enrich rare but damaging failure modes, and also retain a separate random corpus sample to estimate ordinary operational failure rates.

Have annotators work from source text, not the model's proposed answer alone. Blind the model identity during comparative review. Annotate fields that matter to your actual cohort questions, with exact evidence, subject, assertion and time. Double-annotate a subset and resolve disagreements before freezing the held-out set. Group related source publications/near-duplicates in the same split; this package detects exact text duplication but does not automate publication-level linkage.

The included gold format is JSONL:

```json
{"record_id":"synthetic-test-001","task":"conditions","collection":"items","closed_world":true,"expected":[{"name":"lung adenocarcinoma","subject":"index_patient","assertion":"present","temporality":"current"}],"forbidden":[{"name":"breast cancer","subject":"index_patient"}]}
```

`closed_world=true` asserts that the **whole evaluated collection** is exhaustively annotated. Partial labels must set it false. The evaluator uses one-to-one maximum matching of expected patterns to predicted facts; duplicate predictions cannot be counted as multiple true positives. It reports precision/F1 only when the annotations support those quantities. It bootstraps cases, not correlated facts, for confidence intervals when at least two annotated cases are available. Matching is literal/normalized-case field matching, not a hidden LLM judge or an ontology-equivalence oracle.

Measure downstream **cohort membership** on hand-labeled eligibility queries as well as field-level scores. A structurally valid erroneous sex, age unit, stage, negation or tumor link can change eligibility. Report false inclusions, false exclusions and unknown cases separately. The current evaluator directly supports fact-collection matching; a dedicated fully annotated cohort-level statistical evaluator is a future addition. Exported cohort membership and supporting fact IDs make manual/independent evaluation possible now.

## Scaling and limitations

The async pipeline streams input in bounded batches, but exact profile rows and token percentile lists are held in memory. These are reasonable for roughly 180k cases; they are not designed as a distributed billion-row data engine. The SQLite index and joins are a practical research starting point. Large cross-domain joins can expand many fact combinations; move the same domain/evidence model to a proper analytical database when necessary.

Raw source and model output can be sensitive even when derived from public reports. Use restrictive filesystem permissions, keep the API loopback-bound by default, and do not forward private records to an endpoint without appropriate authorization. Do not expose the demonstration server or cohort DB publicly as a clinical system.
