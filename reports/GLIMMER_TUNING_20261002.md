# Glimmer FP8 / eight-B200 tuning review — 2026-10-02

The campaign `run-20261002-120538` completed its core work: GPU stage `completed`, checkpoint cleanup `deleted`, nine complete primary quality arms, 36 completed throughput cells, and two pixel evaluation stages. One experimental KV-cache layout failed before inference. All 28 saved GPU before/after snapshots report `released`. Completion does not establish clinical accuracy or image readiness.

The strongest measured **text extraction pilot** candidate is `RedHatAI/Muse-Glimmer-30B-FP8-block` with DFlash, eight independent one-GPU replicas, prefix caching, an 8,192-token scheduler batch budget, and up to 64 in-flight requests per replica. It reaches 18,715 generated tokens/s and 7.821 first-pass validator-valid tasks/s across eight B200s; its three-seed full-checklist confirmation passes the configured automatic guard. Image attribution remains unsuitable for automatic clinical export: only 13 of 93 pixel-attribution requests validate in either pixel configuration, and semantic review is still pending.

## Evidence and scope

Reviewed the archive `/Users/mkieffer/Downloads/run-20261002-120538-results.tar.gz` in streaming mode. Selected JSON members were capped at 1 MiB each; no weights, caches, environments, or whole archive were extracted. Seven small existing figure assets were copied for inspection (each under 283 KB); four were visually inspected and checked against their frozen SHA-256 provenance. Large aggregate `summary.json` and secondary reports were not loaded; per-arm reports and per-task records supplied the evidence.

The current frozen fixture manifest matches the campaign hash `4c32ad96674b353c69a62e22f720c789c49c51824836082df65a0f513a6d08a2`. All 1,584 primary task first-prompt hashes match current fixtures, and independently recomputing all nine arms reproduces every saved raw/delivered score. Each arm has 176 tasks and 11 patients. These are nine development articles, including seven case sources and two negative controls; the poisoning source supplies 91 of 161 required checks. The checklist is partial and cannot estimate full clinical recall, precision, or end-to-end patient discovery accuracy.

The companion JSON report saves compact metrics and verification. This review changed no runtime modules/configurations, existing reports, source fixtures, or archived predictions.

## Exact candidate recipe and token limits

| Setting | Evidence-backed value |
| --- | --- |
| Target checkpoint | `RedHatAI/Muse-Glimmer-30B-FP8-block` |
| Target revision | `1deb4641ff84f9a728dd11b27cac1f6a02a9ed14` |
| Checkpoint quantization | FP8 block / `compressed-tensors`; serving lets checkpoint metadata select quantization |
| Engine/container | vLLM `0.30.0`; `docker://vllm/vllm-openai:v0.30.0` |
| Drafter checkpoint | `meta-models/Muse-Glimmer-30B-assistant` |
| Drafter revision | `e8192f3a8f617f74be2ce220360c89ef4789f39f` |
| Speculation | `dflash`, 15 speculative tokens, `draft_sample_method=probabilistic` |
| Topology | Eight external replicas; TP1 per replica, no expert parallel |
| Scheduler | `max_num_seqs=64`, `max_num_batched_tokens=8192` |
| Context | `max_model_len=65536` |
| Cache/runtime | Prefix cache enabled, KV dtype `auto`, GPU memory utilization 0.9, BF16 runtime dtype, PIECEWISE CUDA graphs |
| Reasoning/sampling | `medium` → `reasoning_strength`; temperature 1, top-p .95, top-k 64 |
| Primary generation caps | 32,768 output tokens initially and on the one full-regeneration retry |
| Confirmation seeds | 42, 1729, 5724 |
| Text concurrency | Up to 64 per replica / 512 across node |
| Text mode | `--language-model-only`; vision was measured on separate multimodal servers |
| Template SHA-256 | `cfc67e5f349f37690dfd31ed1f18bc4442a9dd32fe39a648f993cb4eb3cae678` |

**8,192 and 32,768 in the `b8192`/`b32768` layout names are `--max-num-batched-tokens`, not context lengths.** The default scheduler budget is 16,384, and all those text layouts retain the same 65,536-token context. Independently, the primary output cap is 32,768; the vision output cap is 16,384. These three limits serve different purposes. The selected archive deployment explicitly contains `--max-model-len 65536 --max-num-batched-tokens 8192`.

The serving snapshot also contains a legacy `speculation: off` field, but its actual `speculative_config` and launch argv enable DFlash. Aggregate server counters confirm drafts and accepted tokens: 641,906 drafts, 9,628,590 drafted tokens, and 2,279,384 accepted tokens in the selected cell (23.7% accepted-token ratio, 3.55 accepted tokens/draft). Do not infer speculation behavior from the legacy field alone.

## Primary quality and seed stability

Raw means the last parseable candidate before section rejection; delivered means a section accepted by structural/source validation. A valid section can still contain unsupported semantics or omissions. Forbidden hits are zero in all arms, but unavailable rejected sections limit delivered forbidden coverage.

| Arm / seed | Required raw / delivered of 161 | First valid of 176 | Final valid / rejected of 176 | Delivered forbidden probes unavailable of 36 | Roster valid of 9 | Figure attribution valid of 31 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| ordinary control, 42 | 141 / 115 | 134 | 165 / 11 | 6 | 7 | 15 |
| ordinary control, 1729 | 140 / 91 | 133 | 167 / 9 | 10 | 7 | 15 |
| ordinary control, 5724 | 145 / 96 | 138 | 168 / 8 | 9 | 8 | 18 |
| DFlash b8192 / c64, 42 | 139 / 120 | 139 | 172 / 4 | 4 | 7 | 13 |
| DFlash b8192 / c64, 1729 | 134 / 121 | 136 | 170 / 6 | 4 | 8 | 15 |
| DFlash b8192 / c64, 5724 | 131 / 95 | 141 | 169 / 7 | 8 | 9 | 19 |
| DFlash no-prefix / c64, 42 | 149 / 83 | 139 | 167 / 9 | 12 | 9 | 14 |
| DFlash no-prefix / c64, 1729 | 138 / 94 | 142 | 169 / 7 | 8 | 8 | 16 |
| DFlash no-prefix / c64, 5724 | 132 / 108 | 135 | 165 / 11 | 6 | 6 | 19 |

The selected DFlash candidate delivers 120/121/95 required checks across seeds, versus 115/91/96 for the ordinary control. Mean delivery is 112/161 (69.6%) versus 100.7/161 (62.5%). It has 511/528 final validator-valid tasks; 17 sections still fail. Raw retention falls from 426/483 control checks to 404/483 candidate checks, so the higher delivered total does not establish uniformly better extraction. The automatic guard allows up to five lost delivered checks and four lost tasks in each seed; it ignores broader omissions and raw retention. It is a stability screen, not medical equivalence.

The no-prefix candidate loses 32 delivered checks at seed 42 and fails the guard despite slightly more validator-valid tasks. Its 12,656 tok/s also trails the cached candidate. It should not be selected from this run.

Across the selected candidate's 17 rejected sections, temporal-evidence errors affect nine; six have literal quote/segment errors; two have no parseable final JSON. Counts here are categorized at section level. Full regeneration still trades retention for formatting; targeted repair and item quarantine should be evaluated separately with preservation checks.

## Throughput across layouts, concurrency, and repeats

Each cell uses 32 short/long prompts covering extraction domains, 16 copies, and three timed repeats: 512 requests/repeat, 1,536 requests/cell. Each has the same workload signature and ordinal-scoped seeds `5724 + ordinal`; the cell sampling metadata's seed 42 describes the base arm, not each measured request seed. Prefixes are explicitly warmed where enabled. The timings exclude startup and warmup. Output counts include reasoning and answers; recorded reasoning-token subtotals of zero do not mean reasoning was absent (sample response records retain reasoning text).

These are generated-token and **first-pass validator-valid task** rates, not supported clinical facts/s. All completed cells are eligible for the runner's selection screen, even though roughly one fifth of timed requests fail source/schema validation. The quality confirmations allow one retry and must not be confused with first-pass speed counts.

| Layout | In-flight / replica | Aggregate output tok/s | Valid tasks/s | Valid / rejected of 1,536 | Repeat output tok/s |
| --- | ---: | ---: | ---: | ---: | --- |
| dp4-tp2 | 8 | 2,078 | 0.880 | 1247 / 289 | 2,095, 2,085, 2,054 |
| dp4-tp2 | 16 | 3,693 | 1.541 | 1224 / 312 | 3,701, 3,694, 3,684 |
| dp4-tp2 | 32 | 5,839 | 2.431 | 1208 / 328 | 6,444, 5,349, 5,828 |
| dp4-tp2 | 64 | 8,159 | 3.420 | 1223 / 313 | 8,527, 7,464, 8,599 |
| dp4-tp2-dflash | 8 | 6,784 | 2.845 | 1217 / 319 | 6,643, 6,897, 6,816 |
| dp4-tp2-dflash | 16 | 10,692 | 4.479 | 1220 / 316 | 10,878, 10,388, 10,827 |
| dp4-tp2-dflash | 32 | 13,835 | 5.707 | 1199 / 337 | 13,562, 13,827, 14,131 |
| dp4-tp2-dflash | 64 | 15,528 | 6.463 | 1212 / 324 | 15,587, 15,272, 15,732 |
| dp4-tp2-dflash-b32768 | 8 | 7,009 | 2.885 | 1207 / 329 | 6,938, 7,178, 6,919 |
| dp4-tp2-dflash-b32768 | 16 | 11,273 | 4.721 | 1229 / 307 | 11,336, 11,321, 11,162 |
| dp4-tp2-dflash-b32768 | 32 | 13,948 | 5.711 | 1205 / 331 | 14,009, 13,838, 13,997 |
| dp4-tp2-dflash-b32768 | 64 | 15,974 | 6.600 | 1201 / 335 | 16,158, 15,844, 15,923 |
| dp8-tp1 | 8 | 2,713 | 1.167 | 1222 / 314 | 2,609, 2,734, 2,804 |
| dp8-tp1 | 16 | 4,453 | 1.821 | 1216 / 320 | 4,308, 4,516, 4,540 |
| dp8-tp1 | 32 | 6,640 | 2.745 | 1214 / 322 | 6,173, 7,084, 6,726 |
| dp8-tp1 | 64 | 8,783 | 3.662 | 1207 / 329 | 8,825, 8,852, 8,674 |
| dp8-tp1-dflash | 8 | 9,139 | 3.869 | 1243 / 293 | 9,273, 8,918, 9,233 |
| dp8-tp1-dflash | 16 | 14,059 | 5.854 | 1215 / 321 | 14,087, 13,951, 14,141 |
| dp8-tp1-dflash | 32 | 16,429 | 6.765 | 1218 / 318 | 16,581, 16,637, 16,079 |
| dp8-tp1-dflash | 64 | 18,278 | 7.724 | 1229 / 307 | 18,742, 17,965, 18,144 |
| dp8-tp1-dflash-b32768 | 8 | 9,302 | 3.956 | 1234 / 302 | 9,411, 9,196, 9,300 |
| dp8-tp1-dflash-b32768 | 16 | 14,081 | 5.975 | 1230 / 306 | 13,976, 14,432, 13,849 |
| dp8-tp1-dflash-b32768 | 32 | 16,899 | 7.081 | 1226 / 310 | 17,216, 16,865, 16,629 |
| dp8-tp1-dflash-b32768 | 64 | 18,439 | 7.770 | 1229 / 307 | 18,331, 18,681, 18,308 |
| dp8-tp1-dflash-b8192 | 8 | 9,368 | 3.873 | 1214 / 322 | 9,306, 9,562, 9,244 |
| dp8-tp1-dflash-b8192 | 16 | 13,837 | 5.793 | 1219 / 317 | 13,873, 13,547, 14,099 |
| dp8-tp1-dflash-b8192 | 32 | 16,663 | 7.009 | 1237 / 299 | 16,810, 16,761, 16,426 |
| dp8-tp1-dflash-b8192 | 64 | 18,715 | 7.821 | 1221 / 315 | 19,180, 18,162, 18,832 |
| dp8-tp1-dflash-eager | 8 | 2,379 | 0.986 | 1224 / 312 | 2,355, 2,402, 2,380 |
| dp8-tp1-dflash-eager | 16 | 4,143 | 1.729 | 1218 / 318 | 4,360, 3,986, 4,103 |
| dp8-tp1-dflash-eager | 32 | 6,386 | 2.711 | 1227 / 309 | 6,053, 6,473, 6,658 |
| dp8-tp1-dflash-eager | 64 | 9,555 | 4.013 | 1217 / 319 | 9,769, 9,250, 9,660 |
| dp8-tp1-dflash-no-prefix | 8 | 7,570 | 3.178 | 1230 / 306 | 7,507, 7,674, 7,531 |
| dp8-tp1-dflash-no-prefix | 16 | 10,431 | 4.429 | 1238 / 298 | 10,393, 10,340, 10,564 |
| dp8-tp1-dflash-no-prefix | 32 | 11,929 | 4.953 | 1212 / 324 | 12,019, 11,962, 11,808 |
| dp8-tp1-dflash-no-prefix | 64 | 12,656 | 5.306 | 1227 / 309 | 12,616, 12,678, 12,674 |

At concurrency 64, b8192 gives 18,715 tok/s versus b32768 18,439 and default b16384 18,278. Differences are only 1.5–2.4%, with overlapping repeat ranges; the data do not establish a decisive batch-budget optimum. b8192 is the defensible choice because it is the fastest selected cell and the only cached speed candidate with a three-seed full-checklist confirmation here. It gives 2.13× the ordinary TP1/c64 rate (8,783 tok/s), and 2.14× its valid-task rate (3.662/s).

The selected cell's request latency median/p95 is 11.08/41.75 seconds, TTFT median/p95 .687/1.233 seconds, and time-to-first-answer median/p95 10.28/34.32 seconds. At ordinary DFlash c32, p95 request latency is 27.99 seconds and throughput 16,429 tok/s; c32 can be considered for an interactive latency budget, but its full quality was not confirmed here. The c64 winner is most suitable for queued batch work, with concurrency bounded by real latency and memory limits.

## Real pixels, attribution, and failures

All 31 fixture figure assets were acquired, with three seeds per method: 93 figure rows each. For each row the runner separately requests caption attribution, pixel attribution, visual description, and joint description/attribution. Those stages use full article text and the frozen reference roster. They do not test whether an upstream discovered roster is correct.

| Pixel method | Caption attribution valid / 93 | Pixel attribution valid / 93 | Description valid / 93 | Joint valid / 93 | Separate/joint ownership agreement | Aggregate tok/s |
| --- | ---: | ---: | ---: | ---: | --- | ---: |
| Ordinary | 9 | 13 | 28 | 6 | 4/4 comparable | 1,035 |
| DFlash | 9 | 13 | 27 | 6 | 2/2 comparable | 4,400 |

The pixel-stage `completed` condition requires **any** row to have valid separate attribution and description. It does not require all figures or patients to succeed. Comparable agreement is calculated on only four/two surviving pairs and cannot estimate ownership accuracy. Vision uses TP1, two concurrent requests per replica, max_num_seqs eight, and a 16,384 output cap; the 18,715 text tok/s rate must not be applied to multimodal extraction.

Precise dominant failures:

| Stage | Ordinary failures | DFlash failures |
| --- | --- | --- |
| Pixel attribution | 69 nonliteral/missing citations; 6 wrong figure; 3 malformed JSON; 2 evidence disconnected from figure | 67 nonliteral/missing citations; 7 wrong figure; 3 malformed JSON; 2 disconnected evidence; 1 missing schema fields |
| Description | 33 nonliteral citations; 27 malformed JSON; 4 conflicting objects; 1 graph incorrectly tagged as an imaging acquisition | 35 nonliteral citations; 19 malformed JSON; 5 conflicting objects; 2 wrong figure; 4 missing schema fields; 1 graph incorrectly tagged as an imaging acquisition |
| Joint analysis | 33 nonliteral citations; 19 malformed JSON; 11 disconnected evidence; 7 conflicting objects; 17 schema/semantic failures | 39 nonliteral citations; 14 malformed JSON; 8 disconnected evidence; 7 conflicting objects; 19 schema/semantic failures |

The nonliteral category is the validator's specific `Nonliteral/missing source evidence` / `Visual schema contains nonliteral source evidence`; it can include wrong segment IDs. Sample inspection confirms a frequent concrete cause: the model rewrites `Fig. 3\nStained blood films...` as `Fig. 3 Stained blood films...`. Other outputs join noncontiguous caption sentences while omitting the intervening panel A text. These are not exact contiguous evidence. Some malformed outputs finish normally well below the token cap: DFlash cat Fig3 description uses 4,479 tokens, poisoning F1 uses 5,786, both `finish_reason=stop`, with invalid JSON delimiters. A larger cap would not fix these sampled failures.

## Limited manual source/pixel inspection

This is an unblinded agent diagnostic review of five primary task sections and four figures' ordinary/DFlash seed42 outputs, against frozen full source text and actual archived pixels. It is neither physician adjudication nor a random precision estimate, and cannot substitute for the pending comprehensive review. Raw rejected proposals are distinguished from delivered annotations.

- **Poisoning Case 1 medications (`PMC12802722.1:p1`, selected text candidate).** Delivered 73 mg alteplase at 1:40 p.m., intravenous sodium bicarbonate, 10 g of 10% ethanol infusion, and antibiotics for pneumonia agree with Case 1 b00004. The indication should retain that stroke was suspected before ethylene glycol poisoning was recognized. This sample supports these particular doses/assignments, not all medications.
- **Bullet case (`PMC13294519.1:p1`).** Heparin start, cessation, and protamine reversal agree with b00006. The timeline retains unsuccessful bullet-fragment removal and separates later clot aspiration/revascularization. However `care_plans` is accepted as `complete/not_documented` with zero items despite b00006's explicit Anti-Xa target 0.3–0.5. Capture this as a treatment goal, never a measured lab result. A valid empty section is not proof of completeness.
- **Mesocolon Case 2 (`PMC12285374.1:p2`).** Conditions preserve GIST/soft-tissue-sarcoma as differential diagnoses and the rectosigmoid mesentery tumor. The model flags that its selected primary packet lacks an explicit solitary-fibrous-tumor diagnosis name. Whole-source/context coverage remains important: the article title and integrated case discussion provide context beyond a narrow packet.
- **Mesocolon F4.** Pixels visibly contain panels A/B above C/D/E. DFlash seed42 separate attribution and description validate, assign A–E to p2, and broadly agree with microscopy layout and b00006/b00015. The output correctly flags the caption's inconsistent panel-B stain wording and treats body site/magnification as source context. Ordinary raw ownership likewise assigns p2 but is rejected for rewritten/noncontiguous caption evidence. Full-text context now includes b00006, removing the prior focused-prompt omission documented in the earlier run.
- **Cat Fig3 (`PMC10998798.1`).** Both raw attribution passes correctly assign panel a to the cat and panel b to an external canine comparator. Caption newline rewriting rejects these outputs. Ordinary description says the black scale bars are *labeled* 200 μm; the inspected pixels have black bars with no numeric labels. The 200 μm value comes from the caption. Species identity and nuclear details likewise require source attribution and should not be presented as independently proven from these pixels.
- **Osteosarcoma Fig2 (`PMC13314005.1`).** The raw proposals correctly recognize two CBCT renderings of p1 and avoid the older patient-specimen misclassification. Pixels show an upper warm rendering/lower inverted rendering, not tissue histology. The model calls the lower red marks rectangular, while the inspected marks are two red slanted bars. Ordinary uses A/B panel IDs and DFlash descriptions use top/bottom; attribution uses A/B, so explicit panel-alias reconciliation is necessary. Nerve involvement is a caption/case claim; DFlash appropriately says the nerve course is not directly visible. Separate outputs are rejected for rewritten caption evidence.
- **Poisoning F1.** Pixels contain three labeled case timelines, agreeing with raw shared ownership p1/p2/p3. Ordinary description correctly treats it as a diagram without depicted CT/MRI acquisitions; DFlash joint output triggers the graph-as-imaging gate. Raw attribution is rejected for caption newline rewriting, and DFlash separate description is malformed JSON.

## Safe integration and remaining adjudication

Use the pinned text candidate for a bounded, reviewable queued pilot. Preserve canonical source IDs, patient scope, exact numeric/negation/uncertainty wording, table column headers, and timeline anchors. Store valid facts individually, quarantine unresolved items, and expose source coverage rather than interpreting valid-empty sections as complete clinical extraction. Measure supported facts/patient records per GPU-hour and completeness alongside validator-valid tasks and token speed.

For figures, shorten each request into panel discovery, panelwise description/tags, and independent ownership with selected case paragraphs plus exact caption segments. Supply clear one-object schemas and a canonical panel registry; reconcile visible top/bottom with caption A/B without silently changing identities. Distinguish pixel observations, caption claims, article claims, and inferred context. Keep whole figures with selected-panel scope; unselected panels must not become that patient's facts.

Deterministic source-span recovery can address unique whitespace-only quote rewrites if it stores original/generated text, exact recovered quote, source offsets, recovery method, and ambiguity status, then reruns all gates. Noncontiguous composite quotes require separate supported spans; wrong source IDs/ownership require scoped repair or quarantine. Do not loosen all literal-citation gates or declare recovered quotes medically correct. Evaluate repair in a separate versioned arm, retain prior supported fields, and count omissions after repair. No diagnostic salvage in this review changed the exported archive.

Before automatic export, adjudicate patient/panel ownership and visual accuracy for the surviving figure outputs, the unsupported/inferred modifiers in rejected proposals, every patient's required source coverage, and the seed-sensitive omissions. Re-run quality on held-out multi-patient/table-heavy/figure-heavy articles using the production extraction/repair path. Broader license and sampling scope are separate pilot concerns handled outside this report.

**DFlash/DSpark:** DFlash is exercised in both text and multimodal stages and its counters show actual speculation. DSpark is absent from this tuning campaign, so it has no new success/failure result. The previous `reports/GLIMMER_B200_RESULTS.md` records DSpark startup memory-budget failures with possible residual allocations; those do not establish inference-quality failure. This tuning run's explicit GPU drains improve isolation but do not constitute a DSpark retest. The new `dp8-tp1-dflash-kvfp8` failure is instead an unsupported CLI argument (`--calculate-kv-scales`) before inference; no FP8-KV quality or speed was measured. Eager DFlash runs but reaches only 9,555 tok/s at c64, supporting the selected CUDA-graph configuration for this recipe.

Source-backed implementation references: `src/openpatients2/glimmer_tuning.py` defines the per-seed guard and permissive completion condition; `src/openpatients2/glimmer_benchmark.py` defines workload/warmup/ordinal seeds and measured counters; `src/openpatients2/glimmer_vision.py` defines four image calls, stage usability, annotation integration, and frozen-roster scope; `src/openpatients2/figure_attribution.py` / `figure_visuals.py` define literal evidence and reference/schema gates. Exact campaign launch settings are in the archived selected layout's `servers/deployment.json`.
