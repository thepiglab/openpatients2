# Research and deployment decisions

Research date: **27 September 2026**. These are source-supported deployment choices, not measurements of this project's models on B200s. Sources are at the end of this document. When upstream documentation and a particular checkpoint's instructions differ, the checkpoint-specific recipe takes priority until an actual smoke test shows otherwise.

## Decision in one paragraph

Use a small bespoke, OpenAI-compatible extraction client. Start K2 with upstream **vLLM 0.30.0**, TP8/EP8, no speculation, default/BF16 KV. Compare throughput topologies rather than assuming TP1/DP8 wins. Test Motif with **its own vLLM 0.26.0-motif3 fork**, not a blanket upgrade to upstream vLLM. Compare Motif's MTP on and off, including four two-GPU groups and an eight-GPU EP group. Treat **SGLang 0.5.20** as a serious but unvalidated K2 NVFP4/B200 challenger. Select the final configuration using adjudicated extraction quality plus completed cases/hour, not public chat/decode benchmarks alone. [1–8]

## Corrections to the earlier throughput discussion

The previously discussed 90k–140k input tokens/s, 30k–60k output tokens/s, and 8–12 hours for a complete corpus pass were **not measured on this model/workload**. They should not be used as an allocation guarantee. A benchmark from a different model, attention architecture, input distribution, or measurement convention is not transferable by parameter-count scaling.

The old notes also recorded approximately 65,902 characters and 14,441 tokens per record. The published Open-Patients JSONL is around 482 MB for 180,142 records, so those measurements need reconciliation against the actual input transformation. They may have included repeated prompt material or a different representation. This project does not guess which. `op2 profile` measures raw note characters, raw note tokens, and **each actual rendered chat prompt** separately, using the checkpoint's local tokenizer and template. This build environment did not download/tokenize the real corpus. [9]

Prefix caching does not magically give every DP rank the same cache. vLLM documents independent KV caches per DP engine. The new launcher exposes explicit rank endpoints for external DP; the client pins each case to one. A single endpoint backed by internal DP load balancing does **not** supply that guarantee. TP1/DP8 also introduces cross-rank expert synchronization and replicated dense/attention weights; it is an experiment, not a known throughput winner. [6]

## K2 versus Motif: intelligence and efficiency

| Property | K2-Horizon-375B-A23B-NVFP4 | Motif-3-NVFP4 |
|---|---|---|
| Advertised total / active parameters | 375B / 23B | 314B / 13.2B |
| Advertised context | 524,288 | 262,144 |
| Attention consideration | Conventional full-attention K2 architecture | GDLA compressed latent attention |
| Speculation found in checkpoint-specific instructions | No verified dedicated MTP/EAGLE checkpoint identified in this review | Built-in one-layer MTP; vendor recommends one speculative token |
| Checkpoint-specific Blackwell deployment | NVFP4 requires Blackwell; smoke exact stack | Vendor validates a two-B200 deployment |
| Serving default for this project | Upstream vLLM 0.30.0 | Motif fork, v0.26.0-motif3 |
| Comparable measured B200 clinical-extraction TPS | Not found | Not found |

Sources: K2/Motif model cards and engine release/recipe pages. [1–5,7]

Artificial Analysis displayed **34 for Motif-3 and 31 for K2 Horizon** on Intelligence Index **v4.3.2** when inspected. Crucially, the Motif page included **“Estimate (independent evaluation forthcoming)”** and the speed entries were **N/A**. Treat this as a reason to evaluate Motif, not as established superiority or a clinical accuracy result. [10,11]

The quantized model cards report GPQA scores of about 84.34 (Motif) and 85.45 (K2), and other benchmark figures differ across base, quantized and provider evaluations. These are **not a harmonized head-to-head clinical extraction experiment**. Do not use small score differences or a different model-card headline to settle the switch. [1,2]

Motif's smaller active parameter count and compressed attention make it a credible efficiency candidate. The active-count ratio, roughly 23/13.2 = 1.74, is **not** a predicted speedup: expert routing, communication, prefill attention, kernel maturity, batch size, speculation, and KV residency all matter. Its two-GPU vendor configuration gives an especially useful baseline for replicated groups. Four copies of that *hardware shape* are not proof that the project's external-rank launch settings are already validated.

**Switch criterion:** use the same source cases and clinical annotation rubric, retain uncertainty and source grounding, and require an acceptable paired quality difference before rewarding higher completed cases/hour. Example pilot decision policy: pre-specify critical cohort false-positive tolerances, then pick the fastest model meeting them. No universal numerical tolerance is asserted here; a research team must set it for its use case.

## Why vLLM first, not a blanket claim that it is faster

Upstream vLLM 0.30.0 was released on 22 September and includes K2 support. Motif explicitly recommends a custom 0.26.0-based fork/image. The project keeps these environments separate from the uv client. [2,3]

The SGLang K2 cookbook says its validated matrix is **H200 plus BF16**. It labels EP, NGRAM, PD disaggregation and HiCache playground overrides as unverified, and does not expose DP-attention or alternative MoE backends as validated options. Therefore `k2-sglang-tp8.yaml` is clearly experimental for B200 NVFP4; it does not blindly carry over H200 FlashAttention-3 settings to Blackwell. There was no checkpoint-specific Motif SGLang recipe verified in this review. Automated Hugging Face “Use with SGLang” menus do not establish runtime support. [4,5]

vLLM is the more directly documented starting point for this pair of checkpoints. The project does not claim it beats SGLang's throughput: the same client, prompts, source cases, validation, and measurements can test either once its model/kernels/grammar path passes smoke.

## Speculation: benchmark rather than assume

Motif includes a one-layer MTP head and an explicit one-token self-speculation recipe. Both on/off profiles are supplied. No separate draft model download is required for this Motif path. K2's optional NGRAM profile is an experiment, not an identified trained K2 draft checkpoint. [1,2]

Speculation accelerates some **decode** work, not the initial prompt prefill. vLLM's documentation specifically frames it around medium/low-QPS, memory-bound workloads; saturated batched throughput can decline when verification overhead outweighs accepted drafts. Structured-output compatibility, parser separation and acceptance must also be tested. [8]

For a hypothetical workload spending 90% of its time in prefill, doubling decode speed gives only:

`overall speedup = 1 / (0.90 + 0.10 / 2) = 1.053`.

That is an illustrative Amdahl-law calculation, not a measured prefill fraction. Prefix reuse may shift the workload toward decoding and change the result. Record engine draft/acceptance counters when available; the client never fabricates them from streaming chunk counts.

## Why bespoke orchestration instead of Data Designer in the hot path

NVIDIA NeMo Data Designer remains relevant and maintained: it supports seed data, model-driven columns, dependency graphs, validation and extensibility. It is not being rejected as incapable. [12]

This workload's central requirements are faithful source extraction, useful initial prefill, explicit cache-rank affinity, bounded per-case fanout, immutable evidence offsets, per-task retry/resume, and a measurement definition that distinguishes logical from uncached inputs. A small direct client keeps those decisions visible and avoids introducing a second orchestration abstraction into the performance-sensitive path.

Data Designer would be useful for a **separate** synthetic/adversarial evaluation set: contradictory notes, mother/baby cases, negated diagnoses, specimen reagents, and intentionally missing units. Such examples must be labeled synthetic and must never become invented facts in the source-derived clinical database. No Data Designer integration is claimed or included as a core dependency in this release.

## Sources

1. K2 NVFP4 model card: https://huggingface.co/IFM/K2-Horizon-375B-A23B-NVFP4
2. Motif NVFP4 model card: https://huggingface.co/Motif-Technologies/Motif-3-NVFP4
3. vLLM releases: https://github.com/vllm-project/vllm/releases ; K2 recipe: https://recipes.vllm.ai/IFM/K2-Horizon-375B-A23B
4. SGLang releases: https://github.com/sgl-project/sglang/releases
5. SGLang K2 cookbook: https://docs.sglang.io/cookbook/autoregressive/IFM/K2-Horizon
6. vLLM DP deployment and per-rank KV: https://docs.vllm.ai/en/latest/serving/data_parallel_deployment/
7. Motif base architecture: https://huggingface.co/Motif-Technologies/Motif-3
8. vLLM speculative decoding: https://docs.vllm.ai/en/latest/features/speculative_decoding/
9. Dataset card and files: https://huggingface.co/datasets/ncbi/Open-Patients
10. AA Motif page: https://artificialanalysis.ai/models/motif-3
11. AA K2 page: https://artificialanalysis.ai/models/k2-horizon-375b-a23b
12. NeMo Data Designer: https://github.com/NVIDIA-NeMo/DataDesigner
13. Prefix caching: https://docs.vllm.ai/en/latest/features/automatic_prefix_caching/
14. Structured outputs: https://docs.vllm.ai/en/latest/features/structured_outputs/
15. HiPerGator GPU inventory: https://docs.rc.ufl.edu/resources/gpus/

Web pages are mutable. Pin checkpoint commit SHAs and serving image/SIF digests for an actual campaign, and preserve the generated deployment manifests.
