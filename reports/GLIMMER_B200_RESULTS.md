# Glimmer on eight B200s: October 2, 2026

**Recommendation.** Use RedHatAI FP8 with medium reasoning. Ordinary decoding delivered the most source-checked typed fields after reviewing equivalent labels (127/161). DFlash/medium delivered 125/161 and is the throughput choice: its full primary evaluation ran in 61.6 seconds versus 218.8 seconds, and its fastest warmed serving cell reached 10,998 output tokens/s across eight B200s. Keep ordinary FP8/medium as the quality control; the two-field difference is from one stochastic run per arm, not an established DFlash quality penalty.

**Campaign integrity.** All three verifier downloads completed, all three GPU stages completed, and all three cleanup stages deleted their owned checkpoints. The archive reports no failed models and no remaining weight storage. All 2,992 primary task records are present: 17 configurations × 176 tasks, with 11 patient bundles per configuration. Frozen first-prompt hashes and the fixture manifest match, and every saved automatic score reproduces exactly. Six optional serving-layout trials failed; those failures did not invalidate the completed quality runs.

**Models.** BF16 verifier weights were excluded as requested. All variants used the campaign’s pinned official Meta chat template, overriding quantizer templates. Four reasoning strengths were exercised. This gauntlet evaluated text, tables and captions; no image pixels or visual interpretation were evaluated.

| Checkpoint | Hugging Face | Pinned revision |
| --- | --- | --- |
| FP8 block | [RedHatAI](https://huggingface.co/RedHatAI/Muse-Glimmer-30B-FP8-block) | 1deb4641ff84f9a728dd11b27cac1f6a02a9ed14 |
| Mixed NVFP4 | [NVIDIA](https://huggingface.co/nvidia/Muse-Glimmer-30B-NVFP4) | 47818374517751c48c55cde2621594926b1888b6 |
| BNB NF4 | [Unsloth](https://huggingface.co/unsloth/Muse-Glimmer-30B-unsloth-bnb-4bit) | ecedda395d4d099b477d135a9a573eedaa5a7aad |

The successful DFlash assistant was [Meta’s Muse-Glimmer-30B-assistant](https://huggingface.co/meta-models/Muse-Glimmer-30B-assistant), pinned to e8192f3a8f617f74be2ce220360c89ef4789f39f. DFlash used its 15 predicted tokens plus the trained anchor position.

**Best configurations, with source-reviewed equivalences.** Raw counts describe the last parseable extraction before validation; delivered counts additionally require its whole section to pass the frozen validator. Required checks total 161. Valid/invalid counts total 176 primary tasks, including summaries, timelines and valid empty clinical sections. These counts are not comprehensive medical accuracy or a count of all generated facts.

| Configuration | Raw auto / reviewed | Delivered auto / reviewed | Valid / invalid | Primary batch seconds |
| --- | --- | --- | --- | --- |
| glimmer-fp8/meta_medium | 143 / 152 | 119 / 127 | 170 / 6 | 218.8 |
| glimmer-fp8/meta_medium_dflash | 134 / 140 | 120 / 125 | 173 / 3 | 61.6 |
| glimmer-nf4/meta_xhigh | 149 / 151 | 120 / 121 | 161 / 15 | 850.9 |
| glimmer-nvfp4/meta_xhigh | 134 / 141 | 112 / 119 | 172 / 4 | 229.8 |

The 24 explicit equivalence credits follow the earlier review policy: CD34/STAT6/SATB2 positivity in `interpretation`, arterial/ABG pH, a reordered carbon-dioxide assay name, an imaging result with a generic CT name, a CRP name suffix, and the source-supported `<=8 mm` upper bound. They do not alter predictions or the reference. Missing typed time fields, values retained only in prose, unspecified comparators and unnormalized case-sensitive units receive no credit. Only the four selected configurations received this additional checklist adjudication; the complete arm table below remains automatic.

**Comparison with prior runs.** The historical and K2 rows below use their previously saved source-reviewed equivalences. All rows use the same 161-check reference and frozen first prompts. Provider routing, sampling, reasoning and token caps differ, so this compares configured pipelines rather than isolating quantization quality.

| Configuration | Raw reviewed / 161 | Delivered reviewed / 161 | Valid | Invalid |
| --- | --- | --- | --- | --- |
| glimmer-fp8/meta_medium | 152 | 127 | 170 | 6 |
| glimmer-fp8/meta_medium_dflash | 140 | 125 | 173 | 3 |
| glimmer-nf4/meta_xhigh | 151 | 121 | 161 | 15 |
| glimmer-nvfp4/meta_xhigh | 141 | 119 | 172 | 4 |
| Muse Glimmer 30B / historical | 125 | 100 | 166 | 10 |
| K2-375B NVFP4 / ifm_medium | 131 | 78 | 148 | 28 |
| Muse Spark 1.2 / historical | 143 | 70 | 163 | 13 |
| Gemma 4 31B / historical | 91 | 61 | 149 | 27 |
| Inkling / historical | 139 | 48 | 114 | 62 |
| K2-32B NVFP4 / ifm_high | 143 | 39 | 123 | 53 |
| K2-7B FP8 / ifm_high | 82 | 34 | 108 | 68 |
| K2-MoVA 36B FP8 / ifm_low | 113 | 30 | 96 | 80 |

Ordinary local FP8/medium delivers 27 more checked fields than historical hosted Glimmer (127 versus 100); DFlash delivers 25 more. That is better measured delivery under these settings, not proof that local quantization improved the base model. No comparable Nemotron result was found in the existing comparison; endpoint failures for other models are not clinical-quality scores.

**Manual medical source review.** All 131 fixed-seed sampled claims were inspected against the actual frozen article text, patient sections, table columns and chronology. The plan was 132 claims; one NF4 summary sample was unavailable. Material unsupported modifiers and wrong structured semantics count as unsupported. Uncertain preserves ambiguity. This was unblinded agent review, not physician adjudication or a population precision estimate.

| Configuration | Supported | Unsupported | Uncertain | Unavailable planned |
| --- | --- | --- | --- | --- |
| glimmer-fp8/meta_medium | 31 | 0 | 2 | 0 |
| glimmer-fp8/meta_medium_dflash | 32 | 0 | 1 | 0 |
| glimmer-nf4/meta_xhigh | 30 | 2 | 0 | 1 |
| glimmer-nvfp4/meta_xhigh | 30 | 3 | 0 | 0 |

- The reviewed FP8/DFlash claims correctly distinguished the three poisoning patients, the two mesocolon tumors, the two VBDE patients, and the infected cat. Additional medication inspection confirmed 73 mg alteplase and 10 g of 10% ethanol in poisoning Case 1, 0.9 mg/kg rt-PA in Case 3, and heparin cessation/protamine reversal in the bullet case.
- NVFP4/xhigh put a negative immunohistochemistry result sentence in `time.text`, classified transfer to a psychiatric ward as discharge, and removed “likely” from a proposed reinfarction cause. These semantic defects can pass literal evidence/schema checks.
- NF4/xhigh added an unstated serum specimen to a creatinine measurement and removed the same causal qualifier. Correct numbers do not make every modifier supported.
- Ordinary FP8/medium anchored a one-month repeat MRI to clinic presentation, although the source only establishes the preceding MRI as the contextual antecedent. FP8 configurations also classify an inpatient rehabilitation transfer as discharge where facility/hospital scope is ambiguous.
- The source itself contains conflicts: poisoning ABG table headers say on admission while prose places some ABGs later; mesocolon Case 1 prose calls Ki67 negative while its caption describes occasional labeled nuclei. Extraction should preserve conflicting source statements, not invent a reconciled clinical truth.
- An additional completeness probe found FP8/DFlash’s bullet `care_plans` section empty despite the explicit Anti-Xa treatment target 0.3–0.5. The target should be captured as a goal, not fabricated as a measured laboratory result. Its procedure section also lacks a typed failed-removal finding despite that failure being present in the timeline.

All 36 forbidden patterns had zero hits in available final sections. Those probes do not cover the unsupported modifiers above and cannot establish zero hallucinations. The audit includes accepted and rejected sections; delivered-only verdict counts are saved separately.

**Figure attribution and patient discovery.** Secondary tasks use the reference roster independently of discovery. Therefore primary fact counts do not establish end-to-end success when a discovery roster fails. FP8/DFlash discovered and correctly distinguished all humans and the two negative controls, but its cat roster failed to parse: 8/9 roster tasks succeeded. NVFP4/xhigh had 9/9 source-consistent roster identities, including the nonhuman cat.

| Configuration | Roster valid / 9 | Figure valid / 31 | Whole-figure source-supported raw / 31 | Supported and delivered / 31 |
| --- | --- | --- | --- | --- |
| FP8 / medium + DFlash | 8 | 19 | 27 | 17 |
| NVFP4 / xhigh | 9 | 26 | 26 | 22 |

All 62 figure decisions in these two configurations received a text-source review. A single wrong panel subject or missed patient link makes the whole figure not wholly supported. Supported includes correct abstention for cohort examples without identifiable local patients and nonpatient plant diagrams; it does not measure image diagnosis.

- Both models correctly proposed shared ownership of the three-patient poisoning timeline, but newline changes in its caption quote caused rejection. FP8/DFlash correctly separated the cat’s microfilariae panel from the external canine comparison; that section also failed citation formatting.
- Both left mesocolon Figure 4 unresolved even though Case 2 explicitly cites panels 4A–E. Reproducing the actual focused prompt shows it omitted b00006, the paragraph containing that link. Frozen parser 1.1 lacks JATS cross-reference metadata; the current parser is 1.3 and retains it. This is a benchmark/context-selection defect, not evidence that the model ignored supplied case prose.
- Both called osteosarcoma Figure 2B a patient specimen. The caption describes a negative-image version of CBCT, so it is patient imaging. NVFP4 also missed three histology/IHC patient links; FP8/DFlash had two unparseable figure outputs.

**Throughput.** Each cell runs the same 32 short/long task prompts, four copies per prompt, two repeats: 256 timed requests. All use medium reasoning, temperature 1, top-p .95, top-k 64, natural completion and equal 32,768-token caps. Prefixes are explicitly warmed. Rates pool all eight B200s and exclude startup, warmup and queue time; output includes reasoning plus answers, not just useful clinical facts. Recorded reasoning subtotals of zero do not establish zero reasoning.

| Checkpoint | Layout | In-flight / replica | Output tokens/s, all 8 GPUs | First-pass valid tasks/s | Valid / invalid of 256 |
| --- | --- | --- | --- | --- | --- |
| FP8 | dp2-tp4 | 16 | 2,402 | 0.96 | 197 / 59 |
| FP8 | dp2-tp4 | 8 | 1,349 | 0.56 | 197 / 59 |
| FP8 | dp4-tp2-dflash | 16 | 8,532 | 3.63 | 204 / 52 |
| FP8 | dp4-tp2-dflash | 8 | 6,264 | 2.70 | 208 / 48 |
| FP8 | dp4-tp2 | 16 | 2,704 | 1.19 | 213 / 43 |
| FP8 | dp4-tp2 | 8 | 1,672 | 0.72 | 208 / 48 |
| FP8 | dp8-tp1-dflash | 16 | 10,998 | 4.65 | 203 / 53 |
| FP8 | dp8-tp1-dflash | 8 | 7,999 | 3.57 | 209 / 47 |
| FP8 | dp8-tp1 | 16 | 2,445 | 0.96 | 194 / 62 |
| FP8 | dp8-tp1 | 8 | 2,440 | 1.02 | 201 / 55 |
| NF4 | dp8-tp1 | 16 | 697 | 0.25 | 175 / 81 |
| NF4 | dp8-tp1 | 8 | 603 | 0.22 | 179 / 77 |
| NVFP4 | dp1-tp8 | 16 | 1,611 | 0.64 | 197 / 59 |
| NVFP4 | dp1-tp8 | 8 | 894 | 0.36 | 199 / 57 |
| NVFP4 | dp2-tp4 | 16 | 1,661 | 0.65 | 194 / 62 |
| NVFP4 | dp2-tp4 | 8 | 1,271 | 0.52 | 199 / 57 |
| NVFP4 | dp4-tp2-dflash | 16 | 7,479 | 2.93 | 198 / 58 |
| NVFP4 | dp4-tp2-dflash | 8 | 5,355 | 2.21 | 200 / 56 |
| NVFP4 | dp4-tp2 | 16 | 2,473 | 0.99 | 196 / 60 |
| NVFP4 | dp4-tp2 | 8 | 1,891 | 0.73 | 189 / 67 |
| NVFP4 | dp8-tp1-dflash | 16 | 9,250 | 3.75 | 194 / 62 |
| NVFP4 | dp8-tp1-dflash | 8 | 8,430 | 3.31 | 197 / 59 |
| NVFP4 | dp8-tp1 | 16 | 2,489 | 1.00 | 199 / 57 |
| NVFP4 | dp8-tp1 | 8 | 2,496 | 1.02 | 197 / 59 |

The fastest cell is FP8 + DFlash, eight independent one-GPU replicas, 16 in-flight requests per replica (128 across the node): 10,998 tokens/s and 4.65 first-pass valid tasks/s. This is 4.50× its same-topology non-speculative token rate of 2,445, or 4.07× the best non-speculative FP8 topology (four TP2 replicas: 2,704). Repeat rates were 12,053 and 10,144 tokens/s; this is a short warmed benchmark, not a guaranteed corpus-ingestion rate.

Full 176-task DFlash quality was evaluated at eight in-flight requests per replica, not 16. Before adopting 16, repeat the full factuality/figure benchmark there. NVFP4’s fastest DFlash cell reached 9,250 tokens/s at 16 per replica, but its DFlash/medium arm delivered only 69 automatic checked fields. Its best quality arm is ordinary xhigh; do not attribute medium-speed numbers to xhigh. NF4’s medium speed cells reached only 697 tokens/s; its best quality arm used xhigh and took 850.9 seconds for the primary batch.

**What failed in the optional layouts.**

DCP2 failed for FP8 and NVFP4 with `DCP not support sliding window.` The corresponding assertion is explicit in [vLLM 0.30’s sliding-window KV implementation](https://github.com/vllm-project/vllm/blob/v0.30.0/vllm/v1/kv_cache_interface.py). This combination should be skipped for this pinned engine rather than consuming another initialization trial.

DSpark failed in both TP1 and TP2 for both quantizations because free GPU memory at startup was below the configured 90% budget: examples are 156.38 GiB free versus 160.52 GiB requested for FP8, and 149.63 versus 160.52 for NVFP4. This is the runtime’s startup memory gate, consistent with its [GPU worker memory sizing](https://github.com/vllm-project/vllm/blob/v0.30.0/vllm/v1/worker/gpu_worker.py). It does not establish an inference-quality or intrinsic DSpark-compatibility failure.

The telemetry shows persistent allocations on devices before their DSpark replicas launched. During the FP8 first-wave replicas’ readiness wait, GPUs 2/3 held 21,872 MiB and GPUs 4–7 held 9,016 MiB despite those replicas not yet starting. NVFP4 likewise had 17,020 MiB on GPUs 2/3. Residual processes or CUDA allocations from earlier layouts are the leading explanation; the archive lacks per-process GPU inventories to establish the exact owner. `ServerGroup.stop()` waits for launchers and signals their groups but does not verify descendant termination and released GPU memory.

**Most useful next changes, in order.**

1. Benchmark the production citation recovery and targeted item repair as a separate arm, keeping the frozen regeneration control. The current HPG replay performs one complete-regeneration retry and does not use those production recovery paths. Preserve valid fields, attach exact source quotes/header evidence to failed items, and quarantine unresolved items with coverage marked limited.
2. Make targeted repair work with validated nonempty documentation evidence and bounded batches of failed items. The current `ItemRepair.create` guard requires empty documentation evidence and at most eight pending items, so many of these valid envelopes would not activate it. Validate/freeze the envelope separately; never erase supported lab values or timing merely to pass a gate.
3. Expand figure context through JATS links and case-level passages; use a full-text fallback when cross-reference metadata is absent or a locally known figure remains unresolved. Version this as a new figure arm instead of changing frozen comparison prompts. Reuse deterministic caption quote recovery while retaining original/corrected strings and source offsets.
4. Add semantic gates for temporal expressions, patient/column attribution, stated versus inferred specimen/route, transfer versus discharge, probable causal statements, treatment goals versus actual observations, and imaging versus tissue specimens. A schema-valid record still needs these checks.
5. Capture owned process trees and GPU process/memory snapshots per layout. After shutdown, terminate only owned remaining descendants and wait for the allocated devices to return to their pre-layout baseline before starting another layout. Fail clearly if they do not. Retest DSpark first in a fresh allocation with one replica before testing startup waves; a lower memory fraction can be a secondary controlled trial, not a substitute for cleanup.
6. Repeat FP8 medium with and without DFlash across several seeds, including a full run at concurrency 16, then evaluate more held-out multi-patient, table-heavy and figure-heavy articles. Select supported fields and usable timelines per GPU-hour rather than generated-token rate alone.

**Why preservation matters.** In FP8/DFlash’s rejected Case 1 poisoning observations, 30/33 final items pass the same validator individually. Three bad time fields reject the entire section. Its initial attempt had 52 observation items and its regeneration retained only 33. NF4/high lost 47 initially matched checks while gaining seven during retries (141 raw initially → 101 finally), even as task validity improved. A format-repair instruction alone does not enforce preservation. Individual validity is only a structural/source diagnostic and does not independently establish medical correctness.

**All 17 arms, original automatic scores.** `matched` uses temperature 0, top-p 1, low reasoning and 8,192 initial / 16,384 retry output caps. Meta low/medium/high/xhigh use the publisher sampling above with equal 32,768 caps. Differences between matched and Meta arms cannot be assigned solely to reasoning.

| Checkpoint | Arm | Raw / 161 | Delivered / 161 | First valid / 176 | Final valid / invalid | Primary output tokens/s |
| --- | --- | --- | --- | --- | --- | --- |
| FP8 | matched | 134 | 107 | 139 | 168 / 8 | 2,260 |
| FP8 | meta_high | 124 | 105 | 134 | 170 / 6 | 2,989 |
| FP8 | meta_low | 129 | 97 | 133 | 168 / 8 | 2,644 |
| FP8 | meta_medium | 143 | 119 | 139 | 170 / 6 | 2,388 |
| FP8 | meta_medium_dflash | 134 | 120 | 132 | 173 / 3 | 8,428 |
| FP8 | meta_xhigh | 131 | 87 | 138 | 171 / 5 | 2,970 |
| NF4 | matched | 124 | 81 | 105 | 150 / 26 | 950 |
| NF4 | meta_high | 101 | 80 | 121 | 160 / 16 | 901 |
| NF4 | meta_low | 113 | 70 | 117 | 152 / 24 | 960 |
| NF4 | meta_medium | 122 | 71 | 123 | 156 / 20 | 999 |
| NF4 | meta_xhigh | 149 | 120 | 123 | 161 / 15 | 895 |
| NVFP4 | matched | 119 | 80 | 132 | 165 / 11 | 2,767 |
| NVFP4 | meta_high | 128 | 92 | 134 | 168 / 8 | 3,046 |
| NVFP4 | meta_low | 126 | 91 | 136 | 163 / 13 | 2,855 |
| NVFP4 | meta_medium | 112 | 73 | 134 | 166 / 10 | 3,125 |
| NVFP4 | meta_medium_dflash | 145 | 69 | 137 | 166 / 10 | 8,643 |
| NVFP4 | meta_xhigh | 134 | 112 | 138 | 172 / 4 | 3,362 |

**Limits.** These are nine development sources: seven case articles, 11 patients and two negative controls. The poisoning article contributes 91/161 required checks, and 76 checks concern numbers/units. Best arms were selected post hoc. Higher reasoning did not consistently improve delivery. No BF16 reference was measured, so quantization degradation cannot be isolated. No image pixels were inspected. Quality, preservation, figure linkage and throughput must be assessed separately.

**Artifacts and reproduction.** The archive was streamed; weights/caches were not extracted or downloaded. The original archive and predictions remain unchanged. Source reviews and the report are in `reports/glimmer-b200-*.json` and this file; metrics and selected task payloads are under `runs/glimmer-comparison-20261002/`.

```sh
uv run --locked --python 3.12 --no-dev python scripts/analyze_glimmer_results.py /Users/mkieffer/Downloads/run-20261001-233830-results.tar.gz
uv run --locked --python 3.12 --no-dev python scripts/render_glimmer_comparison.py
```

Rendering applies saved explicit adjudications; it does not call a model or create new clinical labels.
