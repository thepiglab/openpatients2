# K2 versus previous extraction models: October 1, 2026

**Recommendation.** Glimmer delivers the most checked structured facts through the current validator. K2-375B/low is the strongest candidate for improving source coverage: 156/161 checked fields before validation, versus Spark 143, Glimmer 125 and Inkling 139. K2-375B/medium delivers more facts than low (78 versus 54), but retains fewer before validation (131 versus 156). Choose low for the next extraction/repair experiment and keep medium and Glimmer as delivery controls; this run does not establish that low is ready for a production dataset.

**Campaign integrity.** All four GPU stages completed and all four owned model checkpoints were deleted. All 16 configurations have 176 primary task results and 11 patient bundles. First-attempt prompt hashes match the frozen requests, and all automatic scores reproduce exactly. The four historical scores also reproduce against the same reference. This confirms completed inference, not universally valid or clinically correct outputs.

**Comparison population.** Nine frozen, previously license-audited PMC sources: seven case articles, 11 patients (10 humans and one cat), and two negative controls. Each patient has 14 clinical-domain tasks, one summary and one timeline. The primary comparison uses the source-checked roster, so these results isolate downstream extraction rather than end-to-end patient discovery. Each IFM arm separately adds nine roster and 31 text-based figure-attribution tasks. The terminal totals of 216 combine these secondary tasks with the 176 primary tasks; the tables below consistently use 176.

**Meaning of the scores.** The source checklist has 161 required typed-field checks plus 36 forbidden patterns. Retained means the expected fields agree with the source labels, with explicit semantic equivalence credits; delivered additionally requires the whole section to pass validation. A missing or unmatched check can be an omission, an unavailable response, an incorrect value, or a correct fact left in prose/an unsuitable typed field. It is not necessarily a medical error. Correct checked values can coexist with additional unsupported modifiers or wrong claims elsewhere. Valid tasks include empty but valid sections and do not measure medical accuracy.

The tables use the existing historical semantic corrections and 48 new explicit K2 equivalences: broader examination/assay names, CRP/WBC suffixes, IHC positivity stored in `interpretation`, age-unit wording, negative serology wording, and the source-supported `<= 8 mm` bound. Numeric values left only in prose, missing actions, and unnormalized unit/comparator values receive no typed-field credit. The original reference and all predictions remain unchanged. Automatic counts are retained alongside the reviewed counts in the full arm table and CSV.

**Ranking by facts delivered through the current pipeline.** Each model uses its best observed delivery arm; ties use raw coverage. Arm selection is post hoc, and this ranking measures the configured model plus the current parser/validator.

| Rank | Model / arm | Raw retained / missing of 161 | Delivered / 161 | Valid tasks | Invalid tasks |
| --- | --- | --- | --- | --- | --- |
| 1 | Muse Glimmer 30B / historical | 125 / 36 | 100 (62.1%) | 166 | 10 |
| 2 | K2-375B NVFP4 / ifm_medium | 131 / 30 | 78 (48.4%) | 148 | 28 |
| 3 | Muse Spark 1.2 / historical | 143 / 18 | 70 (43.5%) | 163 | 13 |
| 4 | Gemma 4 31B / historical | 91 / 70 | 61 (37.9%) | 149 | 27 |
| 5 | Inkling / historical | 139 / 22 | 48 (29.8%) | 114 | 62 |
| 6 | K2-32B NVFP4 / ifm_high | 143 / 18 | 39 (24.2%) | 123 | 53 |
| 7 | K2-7B FP8 / ifm_high | 82 / 79 | 34 (21.1%) | 108 | 68 |
| 8 | K2-MoVA 36B FP8 / ifm_low | 113 / 48 | 30 (18.6%) | 96 | 80 |

**Ranking by checked source coverage before validation.** This is the more useful selection table for a model whose local repair and validation still need improvement. Spark and K2-32B/high tie on pooled retention; no clinical superiority is established by the tie ordering.

| Model / best raw arm | Retained | Available but unmatched | Unavailable | Mean per-article retention |
| --- | --- | --- | --- | --- |
| K2-375B NVFP4 / ifm_low | 156/161 | 4 | 1 | 98.4% |
| Muse Spark 1.2 / historical | 143/161 | 2 | 16 | 87.0% |
| K2-32B NVFP4 / ifm_high | 143/161 | 8 | 10 | 88.9% |
| Inkling / historical | 139/161 | 4 | 18 | 85.1% |
| Muse Glimmer 30B / historical | 125/161 | 14 | 22 | 89.7% |
| K2-MoVA 36B FP8 / ifm_low | 113/161 | 37 | 11 | 75.2% |
| K2-7B FP8 / matched | 102/161 | 41 | 18 | 74.5% |
| Gemma 4 31B / historical | 91/161 | 32 | 38 | 78.0% |

**All reasoning and sampling arms.** `matched` uses the historical prompt/gates with low reasoning, temperature 0, top-p 1 and an 8,192-token initial cap / 16,384 retry cap. IFM low/medium/high use temperature 1, top-p .95 and the same 32,768-token cap. High is the publisher recommendation saved in the pinned campaign metadata; low and medium are controlled effort alternatives. All three supported levels were exercised for each checkpoint. The improved IFM-versus-matched scores cannot be attributed solely to reasoning because sampling and output budgets also change. There is one stochastic run per IFM arm, with bounded repair; no replicated significance estimate.

| Model | Arm | Raw auto / reviewed | Delivered auto / reviewed | Valid / invalid primary tasks | Primary output tokens/s |
| --- | --- | --- | --- | --- | --- |
| K2-32B NVFP4 | ifm_high | 139 / 143 | 39 / 39 | 123 / 53 | 1317.0 |
| K2-32B NVFP4 | ifm_low | 111 / 111 | 28 / 28 | 120 / 56 | 928.7 |
| K2-32B NVFP4 | ifm_medium | 127 / 135 | 31 / 32 | 125 / 51 | 1086.7 |
| K2-32B NVFP4 | matched | 91 / 92 | 39 / 39 | 129 / 47 | 1208.4 |
| K2-375B NVFP4 | ifm_high | 147 / 148 | 45 / 45 | 136 / 40 | 946.7 |
| K2-375B NVFP4 | ifm_low | 153 / 156 | 53 / 54 | 141 / 35 | 925.2 |
| K2-375B NVFP4 | ifm_medium | 128 / 131 | 77 / 78 | 148 / 28 | 925.8 |
| K2-375B NVFP4 | matched | 97 / 99 | 36 / 36 | 123 / 53 | 1007.7 |
| K2-7B FP8 | ifm_high | 82 / 82 | 34 / 34 | 108 / 68 | 2288.3 |
| K2-7B FP8 | ifm_low | 49 / 49 | 15 / 15 | 82 / 94 | 1872.6 |
| K2-7B FP8 | ifm_medium | 90 / 98 | 33 / 33 | 106 / 70 | 2269.6 |
| K2-7B FP8 | matched | 95 / 102 | 27 / 29 | 93 / 83 | 2375.0 |
| K2-MoVA 36B FP8 | ifm_high | 97 / 98 | 15 / 15 | 88 / 88 | 539.5 |
| K2-MoVA 36B FP8 | ifm_low | 109 / 113 | 27 / 30 | 96 / 80 | 511.1 |
| K2-MoVA 36B FP8 | ifm_medium | 86 / 87 | 28 / 28 | 108 / 68 | 579.4 |
| K2-MoVA 36B FP8 | matched | 31 / 36 | 20 / 22 | 94 / 82 | 614.5 |

More reasoning did not monotonically improve retention or validation. Within the comparable IFM arms, K2-375B and MoVA retain most checked raw fields at low; K2-32B at high; K2-7B at medium. K2-7B matched retains more checked raw fields than its IFM variants, but with substantially more medical/representation errors in its sampled claims. High gives the best K2-7B delivery score. These are observations for this run, not universal optimal settings.

**Manual source review.** I reviewed 213 new, seeded claims against the full frozen article text, patient sections, table headers and chronology. The 231-claim plan left 18 samples unavailable. The earlier study contributes 130 reviewed claims, making 343 reviewed claims across both studies. The sample chooses two clinical claims from preferably distinct domains and one summary claim per patient/configuration when available. It is an unblinded agent review, not physician adjudication; model-dependent availability changes the sampled domains. Counts are diagnostic, not global clinical precision.

| Model / arm | Supported | Unsupported material field | Uncertain | Unavailable planned samples |
| --- | --- | --- | --- | --- |
| Muse Spark 1.2 / historical | 31 | 2 | 0 | 0 |
| Gemma 4 31B / historical | 30 | 2 | 1 | 0 |
| Inkling / historical | 22 | 5 | 4 | 2 |
| Muse Glimmer 30B / historical | 31 | 2 | 0 | 0 |
| K2-32B NVFP4 / ifm_high | 28 | 1 | 2 | 2 |
| K2-375B NVFP4 / ifm_low | 30 | 3 | 0 | 0 |
| K2-375B NVFP4 / ifm_medium | 30 | 3 | 0 | 0 |
| K2-7B FP8 / ifm_high | 27 | 1 | 1 | 4 |
| K2-7B FP8 / matched | 14 | 8 | 2 | 9 |
| K2-MoVA 36B FP8 / ifm_low | 28 | 1 | 1 | 3 |
| K2-MoVA 36B FP8 / ifm_medium | 33 | 0 | 0 | 0 |

Unsupported includes clinically incorrect structured meanings as well as patient/timing errors and invented status/specimen details. A correct number with an unsupported modifier is not counted as wholly supported. Uncertain preserves ambiguous timing or scope instead of forcing a wrong/correct classification. General article background can be source-supported but remains unsuitable as a patient fact; those scope notes are retained in the audit.

**Concrete source findings.**

- MoVA/low assigned mesocolon Case 2’s 18×15×12 cm CT dimensions and sigmoid/bladder displacement to Case 1’s summary. Case 1 instead has a 16.7×12.3×13.4 cm mass involving the pancreatic body/tail. The summary was rejected, but this is a genuine attribution error beyond the 161-field checklist. [PMC source](https://pmc.ncbi.nlm.nih.gov/articles/PMC12285374/).
- K2-375B/medium encoded packed-red-cell transfusions as a surgical complication. The article says transfusions were required and the postoperative course was uneventful. This structured error passed validation. [PMC source](https://pmc.ncbi.nlm.nih.gov/articles/PMC12285374/).
- K2-7B/matched placed dense left hemiplegia at initial presentation. The patient initially moved all extremities; the deficit appeared after laparotomy. It also put catecholamine infusion and IV ethanol in nonpharmacologic therapy, and CRRT in medication ingredients. [Bullet source](https://pmc.ncbi.nlm.nih.gov/articles/PMC13294519/), [poisoning source](https://pmc.ncbi.nlm.nih.gov/articles/PMC12802722/).
- K2-375B/low labeled historical anal fissures inactive, although the source did not establish current status. K2-375B/medium strengthened negative D. immitis tests into a definitively refuted infection. Both illustrate source certainty/status preservation rather than numerical extraction errors.
- Additional targeted probes found K2-32B/high and low placing the Anti-Xa treatment target into resulted observations; K2-32B/medium encoded non-indicated dialysis as declined. MoVA/low called serum amyloid A 37.3 within its 5–10 reference interval, despite also marking the result high. These probes are outside the seeded sample and are saved separately.
- Correct results also occur in rejected sections: admission glucose 5.8, calcium 1.27, 48-hour sodium 135 and AST 53 are source-supported in the reviewed claims. The three poisoning patients are distinguished, but correct results can still carry bad timing, unsupported specimen details or malformed citation fields.

All 36 frozen forbidden patterns have zero hits in available final sections. Some sections are unavailable, and these patterns do not cover the errors above. Zero hits therefore do not mean zero hallucinations. Full claim payloads, source hashes, verdicts and rationales are retained in `claim-audit-reviewed.json`; the additional probes are in `additional-source-checks.json`.

**Why useful facts disappear.** Whole-section validation dominates delivery loss. K2-375B/medium retains 131 checked fields but delivers 78; low retains 156 and delivers 54. In the rejected medium cat observations, 38/50 items pass the same existing source/schema validator when tested individually with the original section metadata. In low’s bullet case, 18/28 rejected-section items pass individually. This does not prove clinical correctness, but it supports testing local quarantine/repair while preserving unaffected items. No facts were salvaged into exports during this analysis.

Other failures include nonliteral temporal evidence, noncontiguous quotes, invalid enums and section-level spelling mistakes. K2-375B/low’s poisoning Case 3 observations use `limations` instead of `limitations`, causing rejection of the entire 53-item section. Merely loosening validation would also let through real patient/status/action mistakes, so source-aware local repair and attribution checks must accompany any salvage strategy.

**Observed GPU throughput.** Aggregate rates below sum all replicas and all four arms, including secondary calls, and divide total tokens by measured evaluation time. They exclude queue wait and startup/warmup. Output includes generated reasoning and final answers, not just useful facts. Separate reasoning-token usage is not reliable for interpreting the recorded zero subtotals; do not read them as no reasoning.

| Model | GPUs / topology | Output tokens/s | Tokens/GPU-second | GPU-stage hours |
| --- | --- | --- | --- | --- |
| K2-375B NVFP4 | 4 / 1×TP4 | 948.2 | 237.0 | 1.99 |
| K2-32B NVFP4 | 2 / 2×TP1 | 1042.1 | 521.1 | 1.28 |
| K2-MoVA 36B FP8 | 2 / 2×TP1 | 555.3 | 277.7 | 3.57 |
| K2-7B FP8 | 2 / 2×TP1 | 2124.4 | 1062.2 | 0.84 |

K2-7B generates fastest, but that does not make it the best extractor. MoVA is slower than 32B in this measured setup and delivers fewer checked fields. Hosted-model timings cannot be compared directly to these B200 rates. These are 4-GPU or 2-GPU measurements, not measured saturation throughput on eight B200s. The saved campaign articles/hour measure completion of all four arms plus secondary tasks and are not single-pass corpus processing rates.

**Evaluation limits and next experiment.** 91/161 positive checks come from one three-patient poisoning article, and 76 checks concern numeric values/units. Mean article retention is included to expose this imbalance, but seven articles remain too few for a definitive architecture ranking. The sources overlap prior development work. Provider routes, quantization, output budgets, reasoning settings and repair behavior differ. The 68-check chunking/refinement experiments are separate development subsets and are not pooled into this 161-check ranking.

For the next controlled benchmark I would use K2-375B/low as the coverage candidate, Glimmer as the current delivery baseline, and K2-32B/high as the smaller local candidate. Test per-item validation/quarantine plus targeted repairs that preserve already supported fields, bind patient/column/time explicitly, retain normal results, and separate treatment goals from measured observations. Measure supported facts and usable patient timelines per GPU-hour on a larger clinician-reviewed set. Keep a separate caption/image attribution stage; no K2 pixel interpretation was evaluated here.

Secondary rosters and figure-attribution outputs exist, but semantic figure attribution and patient-identity mapping remain pending; task validity is not attribution accuracy. The primary scores cannot establish end-to-end patient discovery or figure fidelity. No comparable Nemotron run was found locally. Cohere and Spark Contributor had prior endpoint/access failures, which cannot be ranked as medical-quality failures.

**Saved artifacts and reproduction.** The results archive was streamed without fully extracting it or downloading weights. Original results were not changed. The detailed CSV, scores and source reviews are under `runs/hpg-comparison-20261001/`.

```sh
.venv/bin/python scripts/compare_hpg_results.py /Users/mkieffer/Downloads/run-20261001-123645-results.tar.gz
.venv/bin/python scripts/render_k2_comparison.py
```

Rendering requires the saved manual adjudications. Those are human-readable review records, not newly generated labels or API calls.

**Source-stratified raw coverage, each model’s best raw arm.**

| Article | K2-375B NVFP4 | Muse Spark 1.2 | K2-32B NVFP4 | Inkling | Muse Glimmer 30B | K2-MoVA 36B FP8 | K2-7B FP8 | Gemma 4 31B |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Fibro-dentinoma child | 6/6 | 6/6 | 4/6 | 6/6 | 6/6 | 5/6 | 6/6 | 6/6 |
| Mandibular osteosarcoma | 8/8 | 8/8 | 8/8 | 8/8 | 8/8 | 7/8 | 5/8 | 8/8 |
| Bullet embolism | 11/11 | 11/11 | 10/11 | 11/11 | 11/11 | 10/11 | 8/11 | 11/11 |
| Dolichoectasia (2 patients) | 13/13 | 13/13 | 12/13 | 13/13 | 13/13 | 11/13 | 8/13 | 13/13 |
| Ethylene glycol (3 patients) | 87/91 | 89/91 | 79/91 | 87/91 | 62/91 | 64/91 | 47/91 | 38/91 |
| Mesocolon tumors (2 patients) | 13/14 | 14/14 | 12/14 | 14/14 | 13/14 | 13/14 | 11/14 | 13/14 |
| D. repens cat | 18/18 | 2/18 | 18/18 | 0/18 | 12/18 | 3/18 | 17/18 | 2/18 |
