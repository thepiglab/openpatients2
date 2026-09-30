# Experiments with entity spans, gates and targeted repair

September 29, 2026. **These are isolated experiments; production extraction, schemas, validators and defaults were not changed.** The goal was to distinguish improvements from entity discovery, contextual disambiguation, output recovery, and local repair, and then combine the mechanisms.

**The best practical candidate in this small test was Spark direct extraction with bounded output recovery followed by targeted local repair.** It delivered 67/68 strict checked fields for $0.542 across the eight task pairs, using 16 calls. Its sole strict miss was actually present: 8 mm midline shift was stored under “Head CT,” so the name matcher missed it. Span batches plus item gating delivered 68/68 for $3.622 and 188 calls. These results do not establish either pipeline's overall medical accuracy; the source audit found timeline and certainty problems in both. No combination improved every measured dimension, and none was promoted to production.

The development set contains eight task/patient pairs across five articles and six patients, with two models: `meta/muse-glimmer-30b` and regular `meta/muse-spark-1.2`. It covers laboratory tables and normal results, repeated measurements, the same table's different patient columns, medication/procedure overlap, historical disease status, and an animal case. The source packets and patient identities are the same license-audited packets used in the earlier fidelity study. Full original context remains available in every extraction/repair request. No model weights, ontologies or new article corpus were downloaded.

There are **68 required typed-field checks and 11 targeted forbidden patterns** in these selected tasks, drawn unchanged from the earlier frozen reference. Four additional source-frozen semantic probes check known problem types. This is a failure-enriched convenience set, not held-out or physician-reviewed evaluation. **44 of the 68 positive checks come from one poisoning article**, so the aggregate heavily weights laboratory-table extraction. Two task pairs have no positive checks in the frozen checklist; the semantic probes and source review address their particular risks. The numbers below are **field-check matches, not medical accuracy or exhaustive recall**. Strict matching can miss a correct paraphrase or a fact preserved in prose rather than the requested field.

The fresh direct control uses the same current prompts, schemas, providers and source as the span arms. API schema enforcement is disabled. Initial full-section outputs and the first whole-section retry each have an 8,192-token cap. This fixed-cap prototype comparison differs from the earlier pilot's expanded retry budget. Large outputs often truncate, especially with Spark; this must not be interpreted as poor medical understanding. An explicitly separate follow-up allows one 16,384-token recovery only when the whole retry still has no parseable result.

**The components were tested independently and in combination.**

| Component | What it does |
| --- | --- |
| Direct | Extract a complete task section from the full source. |
| Span hints | Mark source mentions separately by clinical domain; verify exact quotations and compute offsets locally. Give the verified mentions to the downstream extractor as advisory hints. |
| Spans plus context | Add a separate turn to resolve patient, table column, time, background mentions and overlapping domain roles. Decisions remain advisory, with unresolved spans recorded. |
| Span batches | Convert six marked entities at a time, after reviewing them and overlapping mentions in full context. Assemble the item lists; remove only exactly identical objects, including evidence and time. |
| Whole retry | On section failure, regenerate the entire section once. |
| Item gate | Keep individually valid facts and record rejected items. Mark the assembled section limited; do not claim source completeness. |
| Local repair | Freeze already accepted facts; send batches of at most eight failed facts with their errors and source for up to two repair turns. Keep unresolved facts and explicit quarantines in the audit trail. |
| Semantic review | Review the accepted facts against the source for patient, status, specimen, timing and result/target errors. Apply validated replacements or explicit quarantine; retain all edits for source audit. |
| Retry then local | Combine whole retry, conditional larger-output recovery, and local repair. Also test a final semantic review on this combination. |

The first three extraction modes were crossed with five postprocessors in the [initial frozen protocol](../runs/refinement-v1/protocol.json). The [prospective follow-up](../runs/refinement-v1/extension-protocol.json) added span batches and retry→local combinations. It also added a gate for failed/empty observation annotation: define laboratories, vitals, imaging, pathology, microbiology and physiological findings explicitly, and retry in chunks of 16 source segments. This followed two observed problems: an empty response because “observations” was considered underspecified, and a truncated annotation list. The unrun first follow-up proposal is preserved separately. These adaptive experiments are development evidence, not a preregistered held-out trial.

Completed **56 model/strategy combinations**, each covering the same eight task pairs. Costs below are for those eight task pairs, not eight complete articles or a full EHR extraction.

| Combination | Glimmer checks / 68 | Glimmer API cost | Spark checks / 68 | Spark API cost |
| --- | ---: | ---: | ---: | ---: |
| Direct + whole retry | 18 | $0.118 | 7 | $0.418 |
| Direct + local repair | 17 | $0.093 | 7 | $0.257 |
| Direct + retry/recovery + local | 40 | $0.154 | 67 | $0.542 |
| Spans + local repair | 47 | $0.166 | 8 | $0.534 |
| Spans + retry/recovery + local | 48 | $0.177 | 68 | $0.789 |
| Spans + context + retry/recovery + local | 54 | $0.204 | 68 | $1.149 |
| Span batches + item gate | 57 | $0.453 | 68 | $3.622 |
| Span batches + local repair | 59 | $0.486 | 68 | $3.764 |
| Span batches + local + semantic review | 59 | $0.509 | 68 | $3.921 |
| Span batches + whole retry/recovery + local | 49 | $0.580 | 68 | $3.986 |

The un-repaired full-section controls delivered only 4/68 for either model. This primarily exposes all-or-nothing section validation and output truncation, not a 6% medical accuracy rate. Spark whole retry already contained 51 checked fields in parseable raw candidates but delivered only seven; adding local repair and conditional larger-output recovery brought delivery to 67. All 13 larger-output recovery calls produced parseable objects. All 165 small batch-projection calls were parseable as well.

For Glimmer, spans plus local repair improved delivery from 12 to 47 relative to gating the same span-assisted seed. Adding context was not consistently helpful: context plus local repair delivered 46. With whole retry/recovery before local repair, context reached 54 for $0.204 and 41 calls. Its highest strict count was 59 with span batches plus local repair, at $0.486 and 186 calls.

For Spark, item gating alone already retained all 68 checked fields in the batch arm; local repair added 37 other items but no additional checklist matches. Those additional items are not automatically correct or useful. The batch/local outputs also contained seven Glimmer and one Spark exact clinical duplicates after ignoring evidence; synonym and differently qualified duplicates are not captured by this conservative count.

Whole-section regeneration can undo useful work: Glimmer batch/local retained 59 checks, while putting whole retry/recovery before local repair retained 49. The original batch facts were replaced by a new full-section candidate. Preserving a good partial result before recovery is therefore a separate requirement from validating the replacement.

The matcher found no violations of the 11 frozen forbidden patterns; unavailable or empty sections still do not establish clinical negatives. The broader semantic probes caught an unsupported active-status assignment for historical anal fissures in Spark span and span/context outputs. It survived the final semantic review, even when the positive score reached 68/68. See the [source probe review](../runs/refinement-v1/probe-source-audit.json). Zero hits on these narrow probes is not proof that an output has no other errors.

The [complete machine-readable scores](../runs/refinement-v1/scores.json) and [all combinations](../runs/refinement-v1/combinations.csv) include raw and delivered check counts, unavailable checks, section/item counts, probes, API calls and costs. Costs include each pipeline's prerequisites: shared annotation calls count once per source/model, and semantic-after-local includes local repair. Rows share calls in this experiment; summing their costs would double-count work. Cached hosted timings are not 8×B200 throughput measurements.

For context, re-scoring the **earlier saved outputs on these same 68 strict checks** gives Glimmer 34 delivered and Spark 7 delivered. Those historical outputs used different prompts/retry settings and are not a controlled counterfactual. The fresh matched controls are the relevant comparison for the new components.

**A valid section can still lose facts.** Item gating makes useful facts available without discarding the whole section, but an empty or reduced accepted list can also pass validation. That is why the main comparison counts required fields and keeps unavailable/quarantined facts visible. Coverage remains `limited`. Nothing from these experiments was promoted to a release dataset.

The conservation guard also has a limitation: it rejects a proposed repair that deletes a numeric value, unit, dose or time. This prevented silent timestamp loss, but it also blocked legitimate removal of examination names incorrectly placed in `time.text`. In one Glimmer bullet-case response, the local repair proposed nulling both erroneous event labels and genuine follow-up times; the guard rejected them. A production version needs support-aware preservation, not “never clear any nonempty field.” This experiment did not implement such a production policy.

**Semantic review was tested on known errors, not judged only by schema success.** Six controlled replays paired earlier incorrect items with source-checked correct neighbors. Error labels were not shown to the models. All six repaired outputs were reviewed against the source:

| Known error | Glimmer reviewer | Spark reviewer |
| --- | --- | --- |
| Case 2 transfusion assigned to case 1 | Correctly quarantined it. | Correctly quarantined it. |
| Dialysis “not indicated” encoded as “declined” | Removed refusal; retained absence and the documented renal-function reason. | Recognized refusal was wrong but changed it to “cancelled,” which is also unsupported. |
| Anti-Xa treatment target encoded as a resulted lab | Left the erroneous resulted observation unchanged. | Recognized the target and removed `resulted`, but retained it as a laboratory observation rather than a treatment/monitoring target. |

Correct neighboring facts were retained in all six controls. The [source audit](../runs/refinement-v1/error-replay/source-audit.json) records the distinction between corrected, partially corrected, unchanged and newly unsupported claims. A narrow “no declined action” probe would score Spark's cancellation replacement as an improvement even though the medical meaning remained wrong. A generic reviewer is therefore not a reliable automatic truth gate.

The review also made some unnecessary schema complaints because its compact input omitted null/unknown fields. Those fields existed in the actual candidate. This is a presentation defect in this experimental reviewer, and a reason not to promote its changes unquestioningly. Any subsequent test should supply the full candidate or explicitly explain suppressed defaults.

The [audit of applied edits in the span-batch semantic arm](../runs/refinement-v1/selected-semantic-source-audit.json) found useful enrichment, such as replacing a generic negative CT summary with the actual chronic infarct and negative acute findings. It also found a representation regression: Spark changed an affirmed negative imaging observation to `assertion=absent`, contrary to the project's convention for performed negative tests. Several edits merely changed an allowed unknown field or an empty anchor. Improved prose, unchanged checklist scores and medically safe downstream semantics are different outcomes.

The span stage needs a **coverage gate**, too. Glimmer's veterinary annotation was valid and nonempty but omitted six checked normal laboratory rows and two microbiology results. Its batch extraction therefore missed all eight. The failed/empty-only annotation recovery gate could not detect this. Of Glimmer's nine strict misses after batch extraction plus local repair, the remaining miss was a matching artifact: the correct 8 mm midline shift appeared under `name="Head CT"`, with “midline shift” in `text_value`. The [selected-miss source audit](../runs/refinement-v1/selected-miss-source-audit.json) records both kinds; the frozen scores were not adjusted.

**Direct review against the source: 57 sampled claims.** The sample was frozen before extension outcomes and selected up to two facts per task for two candidate pipelines. I reviewed every available sampled fact; this was an assistant review, not a blinded physician review. Empty or one-item tasks account for the seven unused sample slots. Different strategies emitted different facts, so this table is diagnostic and must not be used as a precision leaderboard.

| Model and pipeline | Supported | Unsupported material field | Uncertain interpretation | Sampled |
| --- | ---: | ---: | ---: | ---: |
| Glimmer — span batches + local | 12 | 0 | 2 | 14 |
| Spark — span batches + local | 11 | 2 | 1 | 14 |
| Glimmer — direct + retry/recovery + local | 11 | 1 | 3 | 15 |
| Spark — direct + retry/recovery + local | 10 | 1 | 3 | 14 |

The four unsupported sampled fields were timeline representations: an ethanol result borrowed admission timing from a sodium sentence; the other three placed procedure/treatment/indication text in `time.text` without an actual temporal expression. Literal quotations and valid schemas did not catch them. The nine uncertain claims involved unstated diagnostic-verification qualifiers, capture month used as measurement time, or the poisoning article's conflicting table-versus-narrative blood-gas timing. Coarse historical/current labels in retrospective completed-event narration were not treated as proof of an extra clinical episode.

The [reviewed claims and rationales](../runs/refinement-v1/claim-audit-reviewed.json), [sampling and availability summary](../runs/refinement-v1/claim-audit-summary.json), six known-error replays, and applied-edit audits remain separate evidence streams. They do not form a single unbiased error-rate estimate.

**What the evidence does and does not support.** Source-located spans make overlap, patient/column assignment and missing coverage inspectable. Small batches address output-length failures. Local repair can preserve correct neighboring facts and avoid paying to regenerate them. These are separable benefits; a more elaborate pipeline is not automatically better. The follow-up's span-batch arm changes batching, annotation recovery and context review together, so its gains cannot all be attributed to entity tagging itself. A deterministic source-chunk baseline and equal-compute replicated runs would be needed to isolate that effect.

**Recommendation for the next development iteration, without changing production now:**

1. Keep direct extraction with a sufficient output allowance as the inexpensive baseline. Detect truncated/unparseable responses separately from medical-field validation failures. The measured Spark direct/recovery/local arm is the strongest cost-versus-checklist candidate here.
2. When items are parseable, preserve the accepted subset and repair only failed items, with bounded rounds and an explicit unresolved queue. Across the 64 seeds, local repair preserved all 745 initially accepted item occurrences after normalization. “Accepted” still only means the existing gates passed, not that those facts are medically true.
3. Use source-located spans and context review selectively for dense tables, multi-patient attribution and unresolved overlap. Require every relevant source row/span to have an explicit disposition—extracted, another patient, background, duplicated or unresolved. A valid nonempty span list is insufficient proof of completeness. Routing to this fallback based on such signals is a recommendation, not a policy evaluated in this study.
4. Make repairs evidence-specific: verify the patient/column, value-unit pair, assertion, treatment goal versus measured result, and the relationship between a time expression and its event. A proposed deletion should distinguish an unsupported field from a supported field being lost. Preserve source conflicts instead of forcing a single timeline.
5. Keep a generic semantic critic advisory until separately validated. Here it improved some details, failed to fix others, and introduced unsupported or inconsistent representations. Its extra calls did not improve the positive checklist counts for any corresponding final arm.

Glimmer remains a lower-cost candidate for separate self-hosted testing, but these results do **not** establish it as more medically faithful than Spark. Hosted API cost, latency and call counts cannot determine whether replicas of a smaller model or tensor parallelism on eight B200s will deliver more validated facts per GPU-hour. That requires the actual serving benchmark, with the same source and quality gates. A held-out, broader, independently adjudicated set and equal-compute repeated runs are also needed before selecting a production policy.

**Cost, storage and software verification.** This study spent **$8.7663**, within its $10 cap. The account moved from $11.1815 to $19.9478 cumulative usage, leaving **$10.0522** of the original $30. The [before](../runs/refinement-v1/budget-before.json) and [after](../runs/refinement-v1/budget-after.json) account snapshots agree with response-reported costs. The shared ledger records 709 requests and retains $0.1809 of conservative reservations for three transport failures; no calls remain running. Six additional cached records are local context-guard failures that did not send a request. No call was blocked by the monetary cap.

The full software suite passed **364 tests**. The [artifact verification](../runs/refinement-v1/verification.json) confirms all 56 scored combinations have eight task pairs, all 57 sampled claims were reviewed without changing the sampled payloads, initially accepted neighbors survived local repair, and all frozen source/reference/harness hashes remain unchanged. The [operational summary](../runs/refinement-v1/operational-summary.json) separates parse failures, truncation, context limits and transport failures from clinical-field errors. Model-call log `valid=true` in this experimental raw-text harness means a parseable object; clinical section validity is assessed separately in the outcome artifacts.

Study artifacts occupy approximately **222 MiB**. No model weights, ontology releases or large source downloads were added. The temporary API-key file and temporary audit helper were deleted. Production code and defaults remain unchanged.

The experiment implementation is confined to [experiments/](../experiments/README.md), its tests and study artifacts. To recompute scores and prepare the fixed-seed source-audit sample without API calls:

```sh
uv run python experiments/score_refinement_v1.py
uv run python experiments/prepare_refinement_audit.py
uv run python experiments/verify_refinement_v1.py
```

Raw calls, candidate spans, overlap/context decisions, field errors, individual repair attempts, rejected modifications, quarantines, provider settings and budget records are retained under `runs/refinement-v1/`. The model-generated semantic critiques are hypotheses to inspect, not gold labels.
