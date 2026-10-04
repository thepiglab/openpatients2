# PMC extraction refinement review: run-20261003-153746

Reviewed 2026-10-04. **Keep compact patient context with source-aware repair as the leading candidate.** This trial supports better delivery and less destructive repair. It does not establish exhaustive clinical accuracy or a production-ready longitudinal EHR corpus.

Source: `/Users/mkieffer/Downloads/run-20261003-153746-results.tar.gz`. Exact aggregates, source-audit excerpts and rescored live discovery are in `CORPUS_REFINEMENT_20261003.json`. No pipeline code or production default was changed during this review.

## Completion and comparison conditions

CPU preparation was ready; GPU benchmark completed with no experiment-level failures; model and acquired article cleanup both reported deleted. GPU stage duration was 4,192.8 seconds, about 70 minutes. Task-level failures remain despite successful campaign completion.

The regression comparison uses the same 20 articles, 17 frozen patient targets, and four seeds (42–45). Local article, roster and reference SHA-256 hashes match the campaign's CPU manifest. Model: RedHatAI/Muse-Glimmer-30B-FP8-block, revision `1deb4641ff84f9a728dd11b27cac1f6a02a9ed14`; eight TP1 replicas with DFlash; context 65,536 and prefill budget 32,768. The new-source holdout contains **15 articles**, not the configured maximum of 24, and two seeds. It includes case reports, a five-case series, reviews, aggregate cohorts and basic research controls.

These legacy controls use the current shared schema and experiment infrastructure. They are not byte-identical reproductions of older campaigns.

## Main results

Counts are summed across seeds; repeated checks on the same sources are not independent observations. Fully valid means schema and implemented evidence gates passed. Partial tasks retain accepted material and quarantine unresolved material; they are not counted as fully valid.

| Extraction arm | Strict required checks delivered | Reviewed equivalent representations included | Tasks valid / partial / failed | Clinical tasks fully valid | Timelines valid / partial / failed | Observed generated tokens/s, 8 GPUs |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| **Compact, source-aware repair; frozen roster** | **158/172 (91.9%)** | **167/172 (97.1%)** | **1,318 / 17 / 37** | **947/952 (99.5%)** | **54 / 14 / 0** | **5,846** |
| Compact, legacy repair; frozen roster | 155/172 (90.1%) | 161/172 (93.6%) | 1,308 / 16 / 48 | 930/952 (97.7%) | 53 / 0 / 15 | 5,962 |
| Whole article, legacy repair; frozen roster | 149/172 (86.6%) | 161/172 (93.6%) | 1,301 / 19 / 52 | 920/952 (96.6%) | 54 / 0 / 14 | 6,546 |
| Compact, source-aware repair; live roster | 150/172 (87.2%) | 155/172 (90.1%) | 1,297 / 11 / 43 | 853/868 (98.3%) | 55 / 7 / 0 | 5,046 |

The live arm has different patients and includes discovery tasks, so its validity denominator cannot be compared directly with the frozen arms. Some missing required checks are unavailable tasks rather than a demonstrably incorrect medical assertion. Conversely, passing checks does not validate every other generated claim. **97.1% is finite checklist delivery with reviewed representation alternatives, not clinical accuracy.**

The strict improvement over compact legacy is three check occurrences out of 172. Paired by seed, refined extraction gains ten check occurrences and loses seven versus legacy. This is a modest, variable improvement, not decisive evidence of a broad accuracy increase. No forbidden-check violations were found; some forbidden checks were unavailable, particularly with live discovery.

The repair result is more encouraging:

| Arm | Checked facts gained after first pass | Previously matching checks lost after repair | Model calls | Summed evaluation seconds |
| --- | ---: | ---: | ---: | ---: |
| Whole legacy | 7 | 5 | 1,886 | 598.5 |
| Compact legacy | 1 | 4 | 1,878 | 657.3 |
| **Compact refined** | **11** | **0** | **1,687** | **624.5** |
| Compact live | 6 | 2 | 1,623 | 680.0 |

Refined prompts/normalization/repair together needed about 10% fewer calls than compact legacy. The experiment does not isolate which component caused each gain. More generated tokens are not inherently more useful: the refined arm produced fewer output tokens and took less evaluation time than compact legacy despite its lower token rate.

**Every prefix-cache reset returned HTTP 404.** Equal warmup completed, and arm order rotated, but cache carryover remained uncontrolled. Rates above are `sum completion tokens / sum arm wall seconds`, including repairs and companion calls, excluding startup, warmup and queue time. They include reasoning and answers, and must not be used as a clean throughput ranking. Reported reasoning-token counters are zero, which is not evidence that no reasoning tokens were generated.

## Measurements: useful separation, a comparator bug

I checked all 14 refined-arm measurement conflicts against the retained original source paragraphs. **All 14 are false conflicts.** Twelve concern the perirenal case, PMC13587181.1: drained-fluid urea nitrogen 13 mg/dL, creatinine 0.27 mg/dL, glucose 99 mg/dL, protein 0.1 g/dL, LDH 33 U/L, triglyceride <7 mg/dL, and kidney length 13.1 cm across seeds. Two concern patient 3 sodium 121 mmol/L and potassium 6.3 mmol/L in PMC13549756.1.

The model's numeric and unit fields agree with the source. However, its `text_value` sometimes contains only the number. The sidecar parser successfully parses that number with `unit=null`, then compares it with the separate, correctly populated model unit and reports a conflict. This is a software comparison error, not a clinical extraction error. Preserve independent provenance for magnitude, comparator, unit, analyte and specimen; an absent unit in one representation must not contradict a source-supported unit in another representation.

The holdout demonstrates successful new scientific-notation handling: the 74-year-old woman's BAL WBC `1.6 × 10^(4)/mL` is represented as mantissa 1.6, exponent 4, magnitude `16000.0`, unit `/mL`, while preserving the source expression. Both source-aware seeds produce this result. Frozen older fixtures still contain ambiguous flattened `×109/L`, appropriately quarantined from automatic exponent expansion. Do not silently interpret those as either 109 or 10^9.

The pediatric patient-2 sample preserves sodium 115 mmol/L, potassium 6.4 mmol/L, aldosterone >40,000 pmol/L, renin activity 22.4 nmol/L/h, discharge sodium 131 mmol/L, and later allergy findings without mixing patient-3 sodium/potassium into that record. Some timing was cleared when it lacked literal support within the fact's quotation. That should be recovered through encounter/event association, not by inventing a date or forcing the time into every quotation.

The many `unresolved` measurement rows include qualitative results, dimensions and composite values; that count is not a measurement failure rate. Agreement also does not prove correctness, since one fallback searches the source using the model's own number/unit pair. Ranges, dimension tuples, percentages of predicted values and compound measurements need explicit structured representations and independent source binding.

## Relative timelines: preservation improved, chronology still incomplete

Refined extraction retains a valid or partial timeline for **68/68 frozen patient runs**, versus 53/68 for compact legacy. It retains 743 events and 929 transitive before-pairs, versus 478 events and 415 pairs. These quantities measure retained structure, not semantic correctness; transitive pairs are especially not independent predictions.

Source inspection found useful longitudinal records:

- **Bullet embolism case, PMC13294519.1:** the refined timeline captures presentation and emergency surgery, postoperative hemiplegia and CTA, unsuccessful bullet aspiration followed by clot thrombectomy, heparin initiation, deterioration, heparin reversal and hemicraniectomy, rehabilitation and later cranioplasty. The medications preserve heparin started/stopped and protamine administered. This is the kind of relative clinical course needed for downstream EHR generation.
- **Scepter series, PMC13294577.1:** five distinct cases are discovered in both seeds. Case 3 correctly separates a decision to embolize from the finding that spinal artery contribution precluded embolic injection. Case 1 includes the day-2 hemorrhage and the three-month obliteration assessment; case 2 includes embolization followed by surgical removal. These are useful examples of plans, cancellations, complications and follow-up being kept separate.
- **Eczema patient 2, PMC13549756.1, seed 42:** fourteen useful events survive partial salvage, but seven are classified as order-unknown. Prior eczema treatment and recently introduced formula lack the expected before-presentation links. A malformed quotation loses the initial-labs-to-admission edge. The graph also asserts skin testing before repeat bloods where the paragraph does not establish a distinct temporal separation. The wording “had been managed” supports history, but simply describing two follow-up results in successive sentences does not establish ordering.

Keep a partial order. Topological layers are not simultaneous visits, event IDs are not chronology, and a display ordering across incomparable events is not evidence. Add a second pass focused on missing history/presentation/treatment/follow-up links, using source-supported encounter anchors and joint evidence spans. Evaluate edge correctness and recall directly, not just graph validity or event count.

## Discovery and cited patients

Independent discovery matches counts on 19/19 definitive articles for three seeds and 18/19 for the fourth, with one unavailable article. These are count checks; identities are not fully adjudicated. One additional article is explicitly provisional and excluded from these denominators.

The live extraction report's embedded discovery scorer incorrectly labels every roster unavailable because that call does not receive discovery predictions. I rescored the saved roster task outputs directly: seeds 42/44 match 18/19 definitive counts with one unavailable roster each; seeds 43/45 match 19/19. The rescore is in the companion JSON. The seed-42 unavailable roster is the two-patient renal-tumor paper: **a nonliteral quote in optional `cited_cases` invalidates the entire roster and blocks both primary patients**. This is an avoidable coupling. Seed 44 fails an aggregate control's roster consistency check. The provisional article is classified aggregate-only in all live seeds; whether that is a genuine miss requires adjudication and must not be counted as four definitive losses.

On the holdout, seed 42 promotes a briefly cited gout patient in **PMC13450100.1, a narrative review**, into a primary extraction target; seed 43 excludes it. The source says the authors recently reported that patient elsewhere and does not present a new longitudinal course here. The resulting facts are largely supported by that mention, but provenance is wrong for a primary case. Keep the mention as a secondary cited-case record/link candidate. Preserve and expose inline reference markers—the parsed source already contains `text_with_reference_markers` and cross-reference IDs, even where plain text shows empty brackets. Require original case detail or a newly reported follow-up before promotion into primary extraction. Do not discard legitimate follow-up papers automatically.

Other holdout decisions are sensible in inspected examples: the Envisia study reports aggregate cohorts rather than individually identifiable cases, while the yeast and hemolysis papers do not create clinical patient records. Distinguishing `aggregate_only` from `no_patient_data` is less consequential than ensuring neither yields an invented individual patient.

## Images and attribution

I inspected the persisted mechanism diagram, both CoMET dashboard figures and the composite PET image directly. This is a purposive audit, not a visual-accuracy estimate or radiologist review.

| Example | What improved | What remains wrong or unstable |
| --- | --- | --- |
| Eczema mechanism diagram | Seed 42 correctly describes the arrow from hyponatraemia to increased proximal sodium reabsorption, and retains the potassium-excretion pathway. | Structured relations omit some links and do not consistently encode both directions of a bidirectional link. Mechanism claims must remain background annotations, not patient findings. |
| CoMET overview, Fig. 1 | Printed INST values 6.00, 4.67, 2.52, 2.48, etc. are correctly captured as structured readings in the inspected refined output. | These are risk scores, not blood pressure/heart-rate values. Beds cannot be linked to the case infant solely by number or appearance. |
| CoMET case example, Fig. 3 | Seeds 42/44 separate the patient's scatter/time-series panels from the aggregate bed list. | Seeds 43/45 assign the whole composite to p1 despite acknowledging other patients' beds. Whole-image attachment must not turn all panel readings into p1 measurements. The 4.60 printed score and estimated line-chart values also need separate provenance. |
| PET composite | Most refined seeds identify panel A as nuclear medicine and panels B/F as fused imaging. | Seed 45 still tags the PET-only MIP panel A with CT; several fused panels omit CT. Taxonomy and panel scope are not yet stable. |

Refined pixel-description tasks are 39 valid and one partial out of 40, versus compact legacy's 35 valid/five failed. However, pixel ownership matches only **11/24 repeated gold checks**, with eight unavailable. Caption ownership matches 17/24, with two unavailable. Two of six gold assets are intentionally skipped for asset-rights review in every seed; additional tasks can fail or have the wrong scope. No checked wrong-patient ID assignments were reported, but these coarse checks miss whole-composite contamination. Schema improvement has not established accurate visual interpretation.

Eight of 48 source-aware holdout pixel tasks fail from context overflow: description and ownership for two long non-case papers, in both seeds. Legacy suffers the same eight failures. These are scheduling/context failures, not vision misunderstandings. Budget image tokens, output reserve and prompt structure; use the figure, caption, relevant mentions and roster evidence rather than the entire article for every visual call. Do not silently truncate source text.

## Holdout and article lengths

On new sources, source-aware extraction has 396 valid/3 partial/33 failed tasks versus legacy's 388/10/34. Clinical tasks improve from 199/210 fully valid to 208/210; timelines improve from 10 valid/five failed to 14 valid/one partial. Clinical accuracy remains unscored: the holdout has no comprehensive gold fact, timeline or panel reference yet, and its live targets differ by seed because of the cited gout mention.

| Holdout length, n=15 | Mean | Median | P95 |
| --- | ---: | ---: | ---: |
| Retained words | 5,129 | 3,927 | 12,669 |
| Glimmer prose tokens | 7,322 | 6,354 | 16,491 |
| Article packet tokens | 8,092 | 6,734 | 18,741 |
| Roster prompt tokens | 22,386 | 16,867 | 54,413 |

This small, mixed holdout is not representative of all licensed case reports. The largest roster prompt reaches 62,956 tokens before generation; a 65,536 context still requires an output-budget decision. Prose length alone is insufficient for context sizing. The source sample and selected pixels remain in the results archive for review; model and acquired raw-article cleanup succeeded.

## Recommended next trial

1. **Fix deterministic comparison and reporting first.** Separate missing-unit evidence from conflicting units; bind analyte/value/unit/specimen independently to literal source spans. Add the 14 reviewed false-conflict examples and the explicit BAL exponent example as regression cases. Wire actual live roster predictions into discovery scoring. Repair and verify cache reset receipts; if the endpoint is unavailable, restart serving or mark performance cells uncontrolled. Clean malformed license URL suffixes such as `/4.0/This` from normalized rights metadata while retaining raw evidence.
2. **Make primary patient discovery resilient.** Independently validate/repair primary identities, source blocks, optional cited links and figure assignments. A bad secondary citation must not delete good primary patients. Cache the accepted roster once per article and share it across downstream arms. Evaluate missing patients against gold identities and separately report primary, follow-up and cited-only targets; add the gout review and renal-tumor failure as tests.
3. **Improve relative chronology with a focused pass.** Recover explicit historical-before-presentation and treatment-before-follow-up relations; keep planned, attempted, cancelled and performed actions distinct. Repair invalid quotations locally without rewriting accepted clinical facts. Require source support for each edge and allow uncertainty instead of ordering follow-up results by paragraph position. Score event recall and supported edge precision against reviewed clinical courses.
4. **Isolate visual panels and context budgets.** Benchmark caption/text attribution followed by cropped pixel description against joint attribution, with stable panel IDs and scope. Store printed readings, estimated plotted points and inferred clinical significance separately. Include the CoMET mixed-patient dashboard, PET-only versus fused panels and bidirectional mechanism arrows. Optional citation/ownership repair should not be blocked merely because a structurally valid earlier assignment was semantically wrong.
5. **Run a bounded correctness confirmation.** Reuse these 20 regression and 15 holdout sources; no large acquisition or new model sweep is needed. First adjudicate the holdout's primary/cited identities, selected quantitative facts, key timeline edges and panel ownership. Then compare the current refined arm with the corrected arm on identical accepted rosters across several seeds, plus a separately scored end-to-end discovery run. Report strict/representation-aware delivery, unsupported and wrong-patient facts, supported timeline edges, magnitude/unit accuracy and per-panel ownership. Add a reproducible random sample of claims outside the checklist for precision review.

Only after those checks improve should we expand the corpus or resume throughput tuning. The most useful performance objective is supported, correctly attributed facts/patients per GPU-hour with complete source provenance; generated tokens/s is a secondary diagnostic.

## Review limits

Manual inspection targeted the failures above and selected positive clinical courses; it was not exhaustive, blinded, random or physician validated. The finite regression checklist does not establish full precision/recall, and four seeds on the same articles do not create four independent corpora. The original archive is preserved; only selected temporary analysis files were extracted locally.
