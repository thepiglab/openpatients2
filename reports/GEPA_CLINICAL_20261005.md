# Clinical GEPA overnight results — 2026-10-05

Archive: `run-20261004-204850-results-20261005-145517.tar.gz`; campaign `run-20261004-204850`. **Completed successfully as a campaign; not every extraction task passed.** CPU ready, bootstrap completed, both GEPA profiles completed, GPU extraction completed with no deferred trials, and owned model/article downloads deleted. Retained review pixels/results are intentional. I reconciled **66 reports with 25,895 saved task records**, recomputed all required-match summaries from their check rows, and verified the canonical text hashes of all 18 new-source packets. This checks result integrity, not all clinical assertions.

The serving recipe remains pinned Red Hat Glimmer FP8, official template, DFlash and eight TP1 replicas. Active stage durations: bootstrap 33.9 minutes; GEPA 3.05 hours on one B200; extraction 5.37 hours on eight B200s. Total is about **8.98 hours**, excluding CPU preparation and queue waiting. GPU telemetry mean/median: GEPA **98.2%/100%**, extraction **59.3%/99%**, including startup/drain. Optimization was genuinely active and the previous closed-file logger/source-restoration failures did not recur.

## Which configuration to retain

**Keep the source-aware baseline for clinical extraction, and test focused timeline extraction as a separate component. Do not promote every GEPA supplement together.** The optimized prompts improved their selection rewards, but their combined clinical delivery did not beat the strongest simple control. Source-aware repairs remain useful; removing them sacrifices too many delivered facts.

All main rows use 64K context, 32K scheduler prefill budget, three seeds and the same fixed 20 articles / 17 patient targets, except live-roster arms. “Strict facts” is the frozen finite checklist in its expected schema fields. “Equivalent representations” additionally credits specifically reviewed alternative representations; it does not search arbitrary evidence for credit. Neither is comprehensive medical accuracy. The 129 denominator is **43 distinct required assertions repeated three times**; held-out 42 is **14 assertions in five held-out articles repeated three times**. Historical exposure to these development fixtures limits independence.

| Arm | Strict facts | Equivalent representations | Held-out facts | Clinical valid / partial / failed | Timeline valid / partial / failed | Generated tok/s | Minutes/trial |
| --- | ---: | ---: | ---: | --- | --- | ---: | ---: |
| baseline | 122/129 | 127/129 | 42/42 | 709 / 4 / 1 | 44 / 7 / 0 | 5,396 | 2.78 |
| clinical-audit | 114/129 | 122/129 | 40/42 | 707 / 2 / 5 | 43 / 7 / 1 | 5,013 | 5.10 |
| coverage-backfill | 116/129 | 125/129 | 42/42 | 706 / 2 / 6 | 47 / 4 / 0 | 5,285 | 5.07 |
| focused | 120/129 | 126/129 | 42/42 | 711 / 3 / 0 | 48 / 3 / 0 | 5,086 | 2.87 |
| gepa | 115/129 | 120/129 | 41/42 | 710 / 3 / 1 | 38 / 12 / 1 | 5,040 | 5.46 |
| gepa-aux-only | 122/129 | 127/129 | 42/42 | 708 / 2 / 4 | 40 / 11 / 0 | 4,545 | 6.04 |
| gepa-backfill | 118/129 | 123/129 | 42/42 | 711 / 3 / 0 | 43 / 8 / 0 | 4,705 | 6.33 |
| gepa-checklist | 121/129 | 122/129 | 42/42 | 711 / 2 / 1 | 44 / 7 / 0 | 5,003 | 4.87 |
| gepa-clinical-only | 120/129 | 123/129 | 41/42 | 709 / 3 / 2 | 48 / 3 / 0 | 5,025 | 5.04 |
| gepa-high | 118/129 | 119/129 | 41/42 | 714 / 0 / 0 | 41 / 7 / 3 | 4,396 | 9.05 |
| gepa-joint | 120/129 | 125/129 | 41/42 | 713 / 1 / 0 | 38 / 13 / 0 | 4,435 | 6.30 |
| gepa-live | 114/129 | 118/129 | 42/42 | 668 / 2 / 2 | 42 / 6 / 0 | 4,367 | 6.27 |
| joint-pixels | 120/129 | 127/129 | 42/42 | 711 / 1 / 2 | 46 / 5 / 0 | 5,636 | 4.41 |
| legacy-control | 116/129 | 122/129 | 40/42 | 696 / 12 / 6 | 36 / 0 / 15 | 5,680 | 2.92 |
| live-roster | 114/129 | 120/129 | 42/42 | 666 / 0 / 6 | 44 / 4 / 0 | 5,857 | 2.48 |
| no-repair | 93/129 | 100/129 | 35/42 | 632 / 0 / 82 | 29 / 12 / 10 | 7,314 | 1.59 |

Clinical counts cover 14 schema tasks per patient; partial means retained items with unresolved validation, rather than fully valid output. Most rows total 714 clinical tasks and 51 timelines. Live-roster rows instead extract 16 patients per seed (672 clinical tasks, 48 timelines), so their smaller error counts are not comparable as evidence of completeness. End-to-end patient discovery still needs an identity/recall audit.

Rates are **sum generated output tokens / sum arm wall seconds**, rounded, across all eight endpoints. Startup/warmup/queue are excluded; repairs and auxiliary calls are included. Do not add a separate reasoning count to output usage. The archive's comparison table averages trial rates instead, which produces slightly different values. These arms have different audit/call workloads, so tokens/s is not an isolated engine/layout benchmark.

Baseline retains **122/129 strict facts (94.6%)**, or 127/129 with reviewed equivalent representations. Focused retains 120/129 and gives **48/51 valid timelines**, versus baseline 44/51. This is a promising component choice, not proof that its order is medically complete. High reasoning makes all 714 clinical tasks valid but retains only 118/129 strict facts and takes 9.05 minutes/trial versus baseline 2.78. Valid fields alone are a poor promotion criterion. No-repair delivers just 93/129 and leaves 82 clinical failures despite its higher token rate.

No scored forbidden-check violations were reported. These are only eight distinct prohibited assertions, and some outputs are unavailable; this does not measure general false-positive precision.

The fairer audit-heavy GEPA comparison is **115/129 versus clinical-audit 114/129**, a one-match net gain: ten gains and nine losses on paired checks. Against the stronger simple baseline, GEPA has four gains and eleven losses, a seven-match net loss. Seed-level strict baseline results are 42/39/41 versus clinical GEPA 36/39/40. Three seeds on a small checklist cannot establish a stable general ranking.

## What GEPA achieved and why blanket promotion is premature

Both profiles optimized **27/27 prompt families**. Clinical feedback selected 24 nonempty supplements with 6,429 metric calls; checklist-only selected 14 with 3,850 calls. The previous logger failure is resolved and roster optimization runs. Searches stopped at their configured component budgets; “completed” is bounded search completion, not demonstrated convergence.

Several validation sets have only 1–10 examples; pixel ownership/joint tasks have one each. Validation score gains are proxy/model-judge gains, not independent clinician assessment. Some selected instructions are several thousand characters long and add restrictive unknown/null requirements. These are plausible contributors to missing typed values and poor transfer; this run does not isolate their causal effects. The next optimization should measure **patient-specific source recall, typed-field completeness, and clinically important ordering**, and compare each selected component on article-disjoint examples before combining them. No fine-tuning is indicated by these results.

Caption/panel checklist matches (18 repeated checks): baseline 12, clinical-audit 11, clinical GEPA 14, aux-only GEPA 14, checklist GEPA 6. Pixel-assisted ownership matches: baseline 9, clinical GEPA 12. At least six pixel checks across these three seeds are unavailable by design due to two excluded assets; additional task failures vary by arm. These are finite attribution checks, not accuracy of image interpretation. Selective auxiliary GEPA is worth investigating, but aux-only ties baseline fact delivery while worsening valid timelines (40/51) and doubling trial duration (6.04 minutes).

## Context confirmation

128K uses two seeds and the same reference; it has no simple-baseline control at that context. Compare paired seeds/configurations, not an 86-check denominator directly with 129.

| Arm | Strict facts | Equivalent representations | Held-out facts | Clinical valid / partial / failed | Timeline valid / partial / failed | Generated tok/s | Minutes/trial |
| --- | ---: | ---: | ---: | --- | --- | ---: | ---: |
| clinical-audit | 80/86 | 85/86 | 28/28 | 472 / 2 / 2 | 28 / 6 / 0 | 5,065 | 4.99 |
| gepa | 80/86 | 83/86 | 28/28 | 476 / 0 / 0 | 27 / 7 / 0 | 4,346 | 6.23 |
| gepa-backfill | 79/86 | 81/86 | 28/28 | 474 / 1 / 1 | 24 / 10 / 0 | 4,572 | 6.60 |
| gepa-checklist | 77/86 | 79/86 | 28/28 | 475 / 0 / 1 | 25 / 9 / 0 | 5,285 | 4.58 |

Clinical GEPA has 75/86 strict matches at 64K on seeds 42/43, versus 80/86 at 128K. Clinical-audit has 77/86 versus 80/86. This is limited evidence that extra room helps those two configurations, not a universal context recommendation. At 128K GEPA ties clinical-audit on strict delivery and is slower (6.23 versus 4.99 minutes). Keep 64K for fitting requests, and route actual overflow to a larger-context or grounded chunked strategy.

## New-source acquisition and extraction

The bounded acquisition stored 84 licensed articles and transferred about 10.9 MB decoded network content. Of 24 selected packets, six overlapped development sources and were removed, leaving **18 disjoint articles** with verified text hashes. The source restoration fix works. This is a small successful test, not proof of large-scale coverage or sustained download throughput.

Those 18 comprise ten research articles, five reviews, two case reports and one editorial. Discovery is valid on 15 and fails on three per seed because **context_overflow_no_source_truncation**; these are not confirmed negative patient cases. The same three papers yield just **four patient targets**, so this new-source sample has little clinical diversity. A research-article-tagged technical report does contain two usable cases: retain an exploratory lane rather than requiring a formal case-report tag.

| New-source arm, two seeds | Clinical valid / partial / failed (112) | Timeline valid / partial (8) | Generated tok/s |
| --- | --- | --- | ---: |
| clinical-audit | 104 / 3 / 5 | 5 / 3 | 1,856 |
| clinical GEPA | 110 / 2 / 0 | 5 / 3 | 2,094 |
| GEPA + backfill | 111 / 1 / 0 | 7 / 1 | 2,113 |
| checklist GEPA | 109 / 2 / 1 | 6 / 2 | 2,188 |

GEPA/backfill improve **validation** here; comprehensive clinical recall is unadjudicated. With only four patients, lower aggregate throughput can reflect insufficient parallel work rather than a serving regression. Obtain a larger stratified patient-bearing holdout before using these scores to choose production prompts.

## Manual source and pixel inspection

I inspected selected typed clinical fields and timelines in four new-source patients across three complete articles, one regression medication-route example, and two native figure assets. The audit is deliberately targeted and small, not a precision/recall study. Evidence and hashes are saved in [the analysis JSON](GEPA_CLINICAL_20261005.json).

- **Infant craniofacial case, PMC13542380:** gestation, birth weight/length and day-14/day-55/day-70 interventions are supported. Its GEPA timeline has 19 events but only three ordering links. In particular, day-55 tracheostomy → day-70 surgery is absent. More events do not automatically give a usable longitudinal course.
- **Two aneurysm cases, PMC13570994:** the 70-year-old's 6.1 cm TAAA and the 78-year-old's separate aortic/iliac dimensions remain correctly attributed in the reviewed observations. Their timeline graphs still leave major presentation/imaging/procedure transitions unordered. Generic device-technique facts elsewhere in the paper were not exhaustively audited for patient leakage.
- **Thyroid case, PMC13612109:** reviewed Tg/antibody trajectories, RAI doses and 6.6 GBq Lu-PSMA treatment are supported; declined surgery remains unperformed. The 12-event timeline omits EBRT and the 2023/2024 RAI treatment courses even though clinical sections retain them. Body and caption also disagree on Tg units and show different numeric sequences; preserve source-specific observations and flag the discrepancy rather than silently reconciling them.
- **Regression medication route, PMC13549756:p2:** IV flucloxacillin is present in source and in the model's evidence, but the delivered route field is null. This is an omitted typed field, not an invented treatment or missing medication.
- **Pixels:** the thyroid figure's eight panel letters, years and Tg labels are correctly read; the infant's two photographs are described and linked to its case. However, the thyroid annotation marks “thyroid carcinoma” as caption-documented although its cited quote does not establish that diagnosis, and the infant annotation places the visible ear on the wrong side of the panel in a pixel cue. Keep diagnoses tied to article/caption evidence, distinguish viewer from anatomical laterality, and audit evidence entailment beyond literal quote matching.

## Next changes and trial

1. **Make chronology the priority:** build an inventory from all retained clinical facts, ensure important occurred interventions enter the timeline, infer only explicit same-anchor order (e.g. day 55 before day 70), and repair missing edges with source evidence. Preserve uncertainty, simultaneous events and planned/declined treatment status; do not force a total order or invent dates. Track event recall and important-edge recall, not only schema validity.
2. **Repair typed-field and auxiliary losses:** preserve already supported facts while allowing correction/removal of invalid optional assignments. Target documented route/dose/result/unit/negation omissions. Existing final errors include parse failures, nonliteral evidence, incompatible coverage links and assignment-erasure refusals; they are separate from medical falsehoods.
3. **Use GEPA per component with stronger independent targets:** retain the simple clinical baseline; evaluate focused timeline and auxiliary figure supplements separately, then their combination. Increase article-disjoint examples and reward complete, correctly attributed facts, not merely valid or conservative empty outputs. Keep high reasoning as a bounded rescue experiment.
4. **Improve source selection and overflow routing:** enrich case reports/series and technical reports with actual individual cases, while retaining an exploration lane. Retry three overlength discovery articles with explicit length-aware routing. Do not treat failure as no patient.
5. **Finish measurement/media provenance:** flag body/caption conflicts and distinguish pixel observations from caption-supported clinical interpretations. The license review also contains an accepted CC BY 4.0 URL with concatenated suffix `/4.0/This`, producing jurisdiction `This`; clean XML-adjacent URL extraction and recheck provenance before release. This is a metadata parsing issue, not evidence that the underlying clearly stated CC BY article should be rejected.

Keep the current FP8/TP1/DFlash engine settings while these clinical changes are isolated. Another large parallelism or high-reasoning sweep is lower priority than demonstrating a complete patient course on independently reviewed cases.

No production defaults were changed in this analysis. Only selected small reports, source snapshots and two bounded pixels were extracted locally; the temporary review files were removed after saving this report. The user's original archive is untouched.
