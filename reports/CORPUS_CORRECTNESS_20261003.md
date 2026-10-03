# Glimmer corpus correctness campaign: 2026-10-03

The campaign completed successfully. Compact patient contexts are the better provisional choice for clinical extraction in this fixture: they delivered more checklist matches and more valid timelines. Overall task validity was essentially tied. The largest remaining problems are loss during repair, inconsistent placement of clinical facts, and unverified visual details. This run does not establish production medical accuracy.

Reviewed archive: `run-20261003-120419-results.tar.gz`. Aggregate calculations, per-trial failures, and audit notes are in `CORPUS_CORRECTNESS_20261003.json`. The original archive and its scores are unchanged. Temporary review copies were removed after analysis.

## What actually ran

- Red Hat `Muse-Glimmer-30B-FP8-block`, revision `1deb4641ff84f9a728dd11b27cac1f6a02a9ed14`, with DFlash and eight TP1 replicas on eight B200s.
- vLLM 0.30.0; 65,536 context, 32,768 prefill budget; medium reasoning; temperature 1, top-p .95, top-k 64; seeds 42, 43, 44; up to two targeted repair rounds; prompt-requested JSON without schema-constrained decoding.
- Twenty fixed PMC articles, 17 reviewed patient targets across 14 articles, and six negative articles. One illustrative target is provisional and excluded from definitive live-discovery scoring.
- Clinical whole/compact comparisons used the same **frozen reviewed rosters**. Live discovery was evaluated independently. Extraction scores therefore do not measure a fully automatic discovery-to-record pipeline.
- Forty-three required and eight forbidden clinical checks, six text-supported figure ownership checks, and 12 real image assets per trial. Checks repeat across seeds; 129 checks are 43 facts evaluated three times, not 129 unique medical facts.
- GPU stage: 2,013.6 seconds, approximately 33.6 minutes, including initialization and all experiments. No GPU experiment failures. CPU preparation was ready, model cleanup was deleted, and article cleanup was deleted. This correctness run reused bounded fixtures; it was not a new large corpus download.

The local source fixture, reviewed rosters, clinical reference, and extraction configuration match the archived runtime hashes. Source text for the manual audit came from those matching fixtures, and images came from the saved pixel assets.

## Aggregate comparison

| Measurement across three seeds | Whole article | Compact patient context |
| --- | ---: | ---: |
| Required checklist matches, first pass | 118/129 | 120/129 |
| Required checklist matches, final delivery | 111/129 (86.0%) | 119/129 (92.2%) |
| Required checks missing / unavailable | 12 / 6 | 7 / 3 |
| Forbidden hits / available checks | 0/24 | 0/22; 2 unavailable |
| Fully valid generated tasks | 972/1,041 (93.4%) | 974/1,041 (93.6%) |
| Partial / failed tasks | 23 / 46 | 20 / 47 |
| Valid clinical section tasks | 683/714 | 685/714 |
| Valid summaries | 49/51 | 48/51 |
| Valid timelines | 35/51 (68.6%) | 42/51 (82.4%) |
| Valid caption ownership tasks | 140/153 | 137/153 |
| Valid pixel description tasks | 32/36 | 29/36 |
| Valid pixel ownership tasks | 33/36 | 33/36 |
| Output tokens/s, all eight GPUs, weighted | 5,838 | 5,950 |
| Input tokens across three trials | 10,227,804 | 9,891,648 |
| Model calls across three trials | 1,453 | 1,454 |

**The 86.0% and 92.2% numbers are checklist delivery fractions, not clinical accuracy or comprehensive recall.** Schema validity checks representation and literal evidence, not whether every clinical claim follows from the article. Several checklist misses are alternate schema encodings, as documented below. Zero forbidden hits does not mean zero hallucinations outside the eight specific tests.

Each trial has 347 generated tasks: 238 clinical sections, 17 summaries, 17 timelines, 51 caption ownership tasks, 12 pixel descriptions, and 12 pixel ownership tasks. Frozen roster tasks are excluded from this generated-task denominator. A partial section can retain accepted facts while quarantining unresolved candidates; it is not an empty record.

| Seed | Scope | Final checklist / 43 | Valid / partial / failed | Valid timelines / 17 | Output tokens/s |
| --- | --- | ---: | ---: | ---: | ---: |
| 42 | Whole | 38 | 324 / 8 / 15 | 11 | 5,165 |
| 42 | Compact | 40 | 321 / 8 / 18 | 12 | 5,810 |
| 43 | Whole | 34 | 324 / 9 / 14 | 12 | 6,511 |
| 43 | Compact | 40 | 328 / 6 / 13 | 16 | 5,379 |
| 44 | Whole | 39 | 324 / 6 / 17 | 12 | 6,029 |
| 44 | Compact | 39 | 325 / 6 / 16 | 14 | 6,858 |

Compact matched more required checks in two seeds and tied in one. It produced more valid timelines in every seed. Pixel description validity was lower, so this result does not justify choosing compact contexts for every visual task.

Throughput is total endpoint completion usage divided by elapsed extraction-arm wall time, including repair requests and orchestration, excluding initialization and queue waiting. It includes all completion tokens counted by the server, not only delivered factual content. Rates cannot be read as supported facts/s or production articles/hour. Separate reasoning-token usage was reported as zero, which does not prove the absence of reasoning in generation.

The approximately 1.9% weighted speed difference is not a convincing optimization result. The second trial ran faster in **every seed**, including seed 43 when whole ran second. Order was whole→compact for seeds 42/44 and compact→whole for seed 43; warm caches/order effects remain. Compact reduced total input usage only 3.3%, partly because unresolved blocks, tables, captions, and schema overhead remain. Use equal warmup and balanced randomized repetitions for a throughput claim.

## What worked, checked against source text

These are purposive developer audits of difficult facts and scoring disagreements, not a random precision study or physician adjudication.

| Article / fact | Manual finding | Implication |
| --- | --- | --- |
| `PMC13549756.1`, three infants | All six trials retained the correct initial Na/K pairs for p1, p2, p3: **129/7.4**, **115/6.4**, **121/6.3 mmol/L**. | Encouraging attribution of numerical facts in a multi-patient article. This verifies these pairs, not every subsequent encounter. |
| Same article, flucloxacillin | All six trials mention flucloxacillin. Three leave the dedicated route field null while writing **IV flucloxacillin** in the name. | A structured-field completeness failure, not complete omission of the antibiotic. Normalize explicit routes into the route column while preserving source wording. |
| `PMC13582412.1`, RCC patient 2 | All six retain **initial imaging showed no evidence of metastasis** in observations. The conditions-only checklist misses it. Later metastatic findings are a separate encounter. | The evaluator must recognize equivalent clinical propositions across schema sections while preserving time. Otherwise it undercounts supported facts. |
| `PMC13624889.1`, NRAS | **NRAS Q61R and VAF 30%** survive in observations in all six trials even when the specialized oncology section fails. | Missing from oncology does not mean missing from the complete patient record. Atomic biomarker normalization and partial oncology repair are still needed for filtering. |
| `PMC13624887.1`, infant scalp imaging | The source-negative underlying bone involvement finding can be represented with `assertion=absent`; the gold expects a present observation with negative text. | Define the assertion's target: a performed test and an absent finding are different propositions. The current pattern can reject a clinically equivalent negative result. |
| `PMC13618765.1`, axillary web syndrome | Inspected outputs preserve the negative Doppler result and acemetacin 60 mg twice daily. The source also confirms the planned physiotherapy was later delivered in 15 sessions. | Keep planned/performed status and encounter time distinct; retain both source statements rather than overwriting the planned stage. |

The exact-quote span recovery remains useful: 4,694 span resolutions across the six trials' attempts, with `clinical_content_changed=false`. This is repeated mechanical recovery work, not 4,694 independently verified clinical facts. Compact trials reported no unresolved recoverable spans; whole trials reported 12. These mechanical successes do not establish entailment or ownership.

## Where correct facts were lost

Of the first-pass checklist matches, whole retained 110, lost eight, and gained one during subsequent processing. Compact retained 116, lost four, and gained three. Net delivery improved little relative to already strong first-pass matches; repair must prevent loss before increasing generation volume.

The clearest example is **perirenal drainage-fluid creatinine 0.27 mg/dL** in `PMC13587181.1`. It is a fluid measurement, not serum creatinine. The value appears in all six first-pass candidates but only three final observations sections. In whole seeds 42/43, the repair attempted to clear an unsupported time string, but the time-erasure guard rejected it despite the value, unit, and specimen being retained. Compact seed 44 continued to fail the time/evidence association. Accepted neighboring observations remained in partial output, but this correct numerical fact stayed quarantined.

The final nonvalid task errors include 41 full-object field-erasure refusals, 46 unresolved item-repair results, and 11 no-parseable-object failures across the six trials. These categories describe final error messages and can overlap; they are not an exhaustive count of every intermediate cause. Some graph repairs also fail on incorrect fact IDs, literal time expressions, duplicate event IDs, or conflicting JSON objects.

Two different protection policies need refinement:

1. Item repair freezes accepted neighbors, which is good, but protects every populated candidate `time` even if the model supplied an unsupported association. Its first-eight pending batch can also be repeated without progress, leaving later candidates without a repair opportunity.
2. Full-object repair's general erasure guard protects all original nonempty fields, including invalid edges, incorrect ownership, and even limitations metadata. In one failed CoMET timeline, `/limitations/1` alone prevented repair. An untrusted candidate is not automatically a source-supported fact.

Allow an audited retraction or correction of an unsupported field after checking its source. Continue protecting accepted, source-grounded values, units, specimen, patient identity, and genuine time expressions. Keep unresolved candidates with their raw source and rejection reason. Do not weaken clinical evidence gates simply to raise validity.

For timelines, use code-generated event IDs and the accepted fact registry, then repair individual events/edges. A bad chronology link should remain pending without suppressing the entire valid event set. Store literal temporal wording separately from normalized relation/offset and its supporting context.

## Live patient discovery

| Seed | Correct definitive article counts | Unavailable outputs | Extra targets relative to primary-case gold |
| --- | ---: | ---: | ---: |
| 42 | 19/19 | 0 | 0 |
| 43 | 17/19 | 1 | 2 |
| 44 | 18/19 | 1 | 0 |

The provisional anonymous procedural illustration was excluded from these denominators. Count and species agreement does not prove identity attribution.

Seed 43 adds two **explicitly cited prior-publication patients** in the axillary web article, with limitations naming Demir et al. and Dündar Ahi et al. These are not fabricated primary cases. This exposes a policy/schema mismatch: primary cases, individually described cited cases, and aggregate/background patients need separate roles. Keep those two as cited-case/link candidates, with their originating citation, rather than mixing them into the primary roster or discarding them without trace. This is compatible with later evidence-based cross-article linking; a citation alone still does not establish patient identity equivalence.

The clinical comparison bypassed these discovery errors using frozen rosters. The next evaluation must also run a live-discovery end-to-end arm to measure missing cases and cross-patient leakage.

## Images and figures: useful fields, incomplete verification

All six trials actually inspected 12 images. Pixel availability was not the main problem in this run. Pixel descriptions were fully valid in 32/36 whole tasks and 29/36 compact tasks; ownership was valid in 33/36 for both. Valid here means schema/evidence validation, not correct visual interpretation.

I inspected saved pixels for the infant mechanism diagram, the multi-tracer PET/CT figure, and the CoMET dashboard against generated annotations:

- **Mechanism diagram, `PMC13549756.1`, seed 43 compact:** a valid description invents a directed skin-barrier→GI-barrier connection and a GI→atopy loop and misplaces a dashed hyponatraemia connection. The broad topic and background ownership are reasonable, but diagram arrow connectivity is wrong. Preserve visible labels and relations independently, with uncertain relations withheld from clinical facts.
- **PET/CT, `PMC13612135.1`, seed 43 compact:** the broad A–F layout is mostly recognizable. Panel C's valid description nevertheless says “FAPI and FDG?” and leaves another label uncertain, although the visible arrangement is PSMA/FDG above DOTATATE/FAPI. A blanket all-panels-PET/CT statement also overlooks grayscale PET MIPs in A and standalone grayscale CT views in F. Keep modality tags and visual claims at panel/subimage level; use label-reading/crops and caption cross-checks.
- **CoMET, `PMC13524801.1`:** only whole seeds 43/44 deliver valid pixel descriptions, **2/6 trials**. Failed trials encounter field-erasure refusal, nonliteral source evidence, or parsing failure. Seed 43 whole correctly reads the visible bed/INST values in prose but leaves chart readings empty and marks the clinical-chart flag false. The risk dashboard needs a clear chart-role definition and structured printed readings, not just a correct paragraph. Do not reinterpret these instability scores as blood pressure or heart rate. This dashboard contains multiple beds and should not be attached solely to the illustrative infant.

The figure gold also has a coverage problem: only **three of its six figures** were among the prepared pixel assets. The other three are unavailable by design. Consequently the raw pixel checklist matches, 7/18 whole and 6/18 compact, are not vision accuracy rates. There were seven scorable pixel ownership instances per scope: whole matched 7/7 and compact 6/7, but that small subset is not a representative attribution benchmark. The compact mismatch conservatively marked the CoMET scope unresolved with no individual owner; it did not assign the dashboard to a wrong patient.

Caption ownership matched 14/18 whole and 13/18 compact. Several differences concern background/aggregate/unresolved categories, including a representative animal experimental image. Clarify those categories before interpreting scope disagreements as clinical patient errors. The small gold set found no wrong patient ID assignments, but it does not establish safe ownership for all other figures or panels.

For joint histology captions from multi-patient reports, distinguish “figure discusses both cases” from “this physical panel/specimen belongs to both patients.” A shared caption is insufficient to establish each panel's specimen origin.

## Next changes and next evaluation

Use compact contexts provisionally for the clinical branch, retaining uncertain source blocks and a whole-article fallback. Keep visual interpretation and patient/panel ownership separate, with access to caption and cross-reference context. Keep the current DFlash/eight-TP1 deployment as the tested baseline; this run did not compare serving layouts, reasoning levels, or quantizations.

Priorities, in order:

1. **Repair without losing supported content.** Differentiate accepted facts from untrusted candidate fields; allow source-checked correction/retraction of unsupported times/edges/metadata; use fair repair scheduling and partial event/oncology/visual delivery.
2. **Normalize atomic clinical facts and fix evaluation.** Explicit route, individual panel biomarkers, specimen, result negation, planned/performed stage, and time belong in filterable fields. Score clinical meaning separately from expected schema placement and field completeness. Keep the original development scores for comparison.
3. **Strengthen visual extraction.** Panel labels and crops, figure/graph type, modality hierarchy, printed chart readings, uncertainty, and diagram-edge verification. Test pixel attribution on all six gold figures plus multi-patient panel examples. Retain raw descriptions as unreviewed until checked.
4. **Test the actual pipeline.** Add primary/cited-case roles, then live discovery → attribution → extraction → repair → timelines. Include long articles, tables, referenced patients, ambiguous figures, and individual animal cases. The current positive cases are all human.
5. **Use held-out source adjudication.** Keep these 20 articles as regressions and add 30–50 independently reviewed articles. Measure supported-fact precision, recall against source-derived gold, value/unit/specimen correctness, patient leakage, time ordering, and retained supported facts after repair. Report those separately from schema validity.

For the next small campaign, compare the current repair policy with the revised policy under identical seeds and source fixtures, add the held-out/live-roster arm, and use balanced warmup/order for throughput. Fix the identified correctness losses before scaling corpus inference. No production code or default was changed by this analysis.
