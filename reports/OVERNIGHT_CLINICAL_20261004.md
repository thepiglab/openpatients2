# Overnight clinical / GEPA recovery analysis — 2026-10-04

Archive: `run-20261004-111240-results.tar.gz`. GPU extraction stage ended partial after 5.247 hours; one-B200 GEPA stage took 21.72 minutes. Model and article cleanup receipts are deleted. The saved results show ordinary completion with explicit failures/deferred trials, rather than a second administrative cancellation.

| Configuration | Seeds | Required checklist matches | Held-out matches | Clinical valid / partial / failed | Output tok/s, eight GPUs |
| --- | ---: | ---: | ---: | --- | ---: |
| baseline, 64K | 4 | 153/172 | 53/56 | 927 / 18 / 7 | 6175 |
| focused, 64K | 4 | 150/172 | 52/56 | 918 / 25 / 9 | 5989 |
| gepa, 64K | 4 | 160/172 | 53/56 | 940 / 6 / 6 | 5509 |
| xhigh-32k-output, 64K | 4 | 159/172 | 54/56 | 932 / 12 / 8 | 5658 |
| gepa, 128K | 2 | 85/86 | 28/28 | 471 / 3 / 2 | 5490 |
| no-repair, 64K | 4 | 96/172 | 27/56 | 726 / 0 / 226 | 7667 |

These are finite development assertions, not overall medical accuracy. The held-out denominator is recomputed uniformly from the saved check rows, including reused bootstrap reports. It represents only 14 unique assertions across five held-out articles, repeated across seeds. GEPA improves overall development delivery by seven matches over baseline but ties it on held-out 64K checks. The 128K result has only two seeds; it is a confirmation candidate rather than proof that larger contexts improve extraction.

GEPA 64K clinically valid tasks: 940/952 (98.74%) vs baseline 927/952 (97.37%). Medium GEPA and xhigh/32K output are close on overall fact delivery (160 vs159), but the latter has fewer valid clinical tasks and worse timeline completion. Removing repairs drops delivery to96/172 and leaves226 failed clinical tasks. Two repairs should remain the control.

DFlash is useful: matched seed42 focused extraction is5744 vs1605 tok/s without drafting (3.58x), and GEPA is5269 vs1723 (3.06x). These are generated reasoning-plus-answer token rates, not useful-fact rates. GEPA 64K trials average4.40 minutes vsbaseline2.71 because GEPA arms perform more audit work and generate more text. TP2 and the8K prefill budget do not establish a decisive quality win in this small confirmation sample.

Allocation telemetry: GEPA median99%, mean62.3%; extraction median99%, mean58.2%. Startup/teardown and idle orchestration periods are included. This is useful evidence that GPU inference was active; it does not prove every device stayed saturated throughout.

## Problems to fix before scaling

- All 34 restored new-source articles were rejected: recovery snapshot omitted has_body_text, canonical text and supplements; discovery exported zero rosters, then all 12 cached-roster arms failed before clinical extraction.
- GEPA repair failure is consistent with framework Logger context redirecting process-global stdout/stderr across concurrent threads, leading to a closed file; use explicit per-component thread-safe LoggerProtocol implementation.
- Bootstrap-reused report rows lack GEPA test subsets. Recalculation gives baseline 53/56 and GEPA 53/56 at matched 64K, rather than mixing 42- and 56-check denominators.
- Timeline/figure/audit repairs sometimes cannot remove unsupported auxiliary fields because generic field-erasure protection also protects invalid initial fields; distinguish source-accepted clinical facts from invalid optional offsets/assignments.
- Two ordinary-decoding seed43 trials were deferred by the per-cell deadline reserve; this is separate from twelve new-source failures.

## Source spot checks

In PMC13612135.1:p1, GEPA correctly separated PSA137ng/mL from CEA102.6ng/mL and their reference intervals, preserved the12- and16-month narrative markers, and retained multiple PET-tracer findings with literal source quotations. The patient’s timeline still failed in seed42 despite these correct facts. This demonstrates why valid clinical fields do not establish a usable longitudinal patient state.

In the renal-tumor article PMC13582412.1:p1, the8.5cm tumor dimension, pathology stage and shared immunostaining statements are supported by the retained source text. Negated metastasis/thrombosis assertions in other cases can be delivered in observations rather than conditions; strict location-specific checklist misses are not automatically clinical omissions. Representation-aware scores remain separate.

No full pixel-description adjudication was performed in this review. At64K GEPA matched14/24 finite pixel-attribution checks, with10 unavailable; baseline matched9/24 with11 unavailable. GEPA timeline validity52/68 is slightly below baseline53/68. Remaining figure/graph failures need targeted source/panel checks; do not label these pipelines production-complete.

## Recommended next campaign

Fix source-snapshot reconstruction and add CPU preflight requiring source-gate success and exact discovery/cache article-set agreement before allocating extraction GPUs. Fix GEPA logging without removing concurrency. Make repairs schema-specific: protect verified clinical facts, while allowing justified correction/retraction of invalid auxiliary offsets, figure ownership and audit registry entries. Capture live roster examples for GEPA on train/validation only.

Run a smaller, adjudicated new-source confirmation rather than another fifteen-arm sweep: baseline64K, GEPA64K, GEPA128K, and a GEPA ablation limited to improved clinical-task prompts (without expensive audits). Keep FP8/DFlash and two repairs. Match seeds; measure supported fact delivery and article/hour rather than token rate alone. Review timelines, patient/panel ownership, numeric/specimen/negation details and omissions on stratified sources. GEPA128K is the best quality candidate from this development run, but promote it only after this independent confirmation.
