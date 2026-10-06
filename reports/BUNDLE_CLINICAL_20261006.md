# Patient bundle review: run-20261005-171140

Reviewed October 6, 2026. **Keep Red Hat Glimmer FP8, medium reasoning and DFlash.
Fix the clinical rubric and completion/optimization plumbing before another broad
GEPA campaign.** There are useful improvements in individual timelines and
component validation, but no demonstrated whole-pipeline GEPA win.

Evidence: the supplied `run-20261005-171140-results.tar.gz`, original campaign
reports, saved candidates, accepted patient bundles, source segments and optimizer
receipts. [Machine-readable metrics](BUNDLE_CLINICAL_20261006.metrics.json) retain
the archive SHA-256, original aggregates, stage durations, telemetry and optimizer
decisions. The archive was streamed; checkpoints and the bulk archive contents
were not extracted. Clinical review below is a targeted source audit, not an
exhaustive adjudication of every generated field.

## Completion and comparison boundaries

- CPU preparation and both checkpoint/article cleanup stages succeeded.
- The main stage was **partial: 43 comparisons finished, one ordinary-decoding
  `gepa` confirmation was deferred at the deadline reserve**. No fatal main-stage
  experiment failures were recorded. The deferred cell is not a medical zero.
- Bootstrap took 40.1 minutes on eight GPUs; independent optimization took 47.0
  minutes on one GPU; the main GPU stage, including joint optimization and engine
  initialization, took 6.94 hours on eight GPUs. These sum to 8.40 hours of stage
  runtime, excluding queue waits and CPU stages.
- Main comparisons below use 64K context, 32K prefill budget, DFlash and three
  seeds. The same 31 articles provide 30 reference patient records. Three seeds
  repeat the same articles: they do not create three independent cohorts.
- There are 261 required clinical checks and 44 forbidden probes per seed. These
  are partial development checks, not exhaustive medical precision/recall.
  The optimizer-withheld test has 49 required checks per seed; those sources
  had already been used in earlier development and are not a new blinded test.
- Frozen rosters and live discovery are different experiments. Live discovery
  consistently excludes one disputed illustrative procedure in a cohort article,
  whereas the frozen roster includes it. This affects patient/task denominators.
- Scores in this report **preserve the original rubric**. The source audit found
  rubric defects; the following small ranking differences are provisional.

## Main results

Valid/partial/failed counts refer only to clinical section tasks. Auxiliary
discovery, image, audit, summary and timeline tasks are excluded from that column.
A valid field is not necessarily an entailed clinical assertion.

| Arm | Roster | Required checks /783 | Test checks /147 | Clinical valid / partial / failed | Ordered relations /123 | Generated tok/s, all eight GPUs |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| Baseline | Frozen | 700 (89.4%) | **141** | 1,249 / 3 / 8 | 39 | 9,302 |
| Complete | Frozen | 680 (86.8%) | 138 | 1,245 / 6 / 9 | 45 | 8,920 |
| Joint pixels | Frozen | **709 (90.5%)** | 136 | 1,242 / 8 / 10 | 47 | 9,592 |
| Clinical audit | Frozen | 686 (87.6%) | 138 | 1,243 / 7 / 10 | 47 | 9,460 |
| Live complete, original prompts | Live | **704 (89.9%)** | 129 | 1,199 / 2 / 17 | 46 | 8,955 |
| Independent GEPA | Live | 694 (88.6%) | 126 | 1,182 / 8 / 14 | 51 | 8,965 |
| GEPA clinical components only | Live | 696 (88.9%) | 125 | 1,185 / 16 / 17 | 52 | 9,226 |
| GEPA auxiliary components only | Live | 684 (87.4%) | 125 | 1,184 / 3 / 17 | **53** | 9,072 |
| Joint GEPA fallback: original prompts | Live | 673 (86.0%) | 132 | 1,188 / 15 / 15 | 40 | 9,424 |
| Joint GEPA fallback + joint pixels | Live | 693 (88.5%) | 129 | 1,191 / 9 / 18 | 45 | 9,118 |
| High reasoning, original prompts | Live | 611 (78.0%) | 124 | 1,154 / 4 / 18 | 33 | 7,837 |
| Whole article, complete | Live | 681 (87.0%) | 129 | 1,184 / 7 / 13 | 43 | 8,853 |

All rates pool completion-token usage over elapsed arm time; they exclude startup
and queue waits and include reasoning, repairs and secondary calls. They do not
measure supported facts/s. An arm that produces more reasoning can have a good
token rate and still take longer to deliver usable records.

Frozen joint-pixels leads the full checklist by only nine hits over baseline;
baseline leads the withheld test. Live-complete leads the main live-discovery
checklist. Neither result establishes a general winner. The joint GEPA fallback
and live-complete use the original prompt strategy: their differences cannot be
credited to an optimized prompt. Sampling and execution variability remain.

## What worked

Clinical validity was generally high after source-aware repair. Independent GEPA
found four nonempty rewrites, with gains on its small component validation sets:

| Component | Original validation score | Selected score | Validation articles |
| --- | ---: | ---: | ---: |
| Procedures/devices | .900 | 1.000 | 3 |
| Observations | .863 | 1.000 | 3 |
| Relative timeline | .412 | .652 | 3 |
| Coverage repair | .885 | .969 | 5 |

There were 11 completed component searches, 17 with insufficient disjoint
examples, and one failed repair-family search. Seven completed searches retained
the original instruction. Thus **four rewritten prompts**, not 29 successfully
optimized prompts. The independent bundle did not improve the withheld clinical
checklist (126 versus live-complete's 129), so these local validation gains are
not a deployment criterion.

Completion can improve clinically useful chronology. For `PMC13612109.1:p1`,
seed 42 baseline merged RAI 100 mCi in 2023 and 150 mCi in 2024 into one event.
Live-complete split those treatments and recovered all five selected course
nodes and four order relations, versus baseline's three nodes and one relation.
The source's later one-cycle 6.6 GBq Lu-PSMA-617 treatment and subsequent response
were retained. This is a useful gain for constructing longitudinal records.

For `PMC13549756.1:p2`, the baseline had the admission, treatment, discharge and
follow-up facts but almost none of their ordering links. Live completion added
presentation → initial labs → treatment → discharge → review. However, for
`PMC13542380.1:p1` the same live-complete seed lost an unambiguous reintubation
node that baseline had captured. Completion needs a retention gate for supported
events and links, not just additional narrative detail.

## Clinical failures and benchmark defects

The audit separates these two categories; changing prompts to satisfy an incorrect
gold label would make extraction worse.

| Source/example | Finding | Required action |
| --- | --- | --- |
| `PMC12285374.1`, both patients, CD34/STAT6 | Several correct positive markers were stored in `interpretation`, while four checks require positivity specifically in `text_value`. | Version the evaluator to accept equivalent typed representations of the same marker and patient. Preserve the original scores alongside any reviewed rescore. |
| `PMC13549756.1:p2`, milk | Source reports concern about cow's milk allergy, then skin-prick sensitization. The gold requires a confirmed present allergy; an output correctly used `possible` and separately recorded positive testing. | Distinguish suspected allergy, sensitization and confirmed clinical allergy in the gold and feedback. |
| `PMC13575834.1`, cohort ESD study | Frozen gold includes one anonymous illustrative procedural figure; its own notes say a clinical reviewer must adjudicate whether it warrants a patient record. All live arms classify the article as aggregate-only. | Adjudicate this record type. Keep anonymous procedural/figure context without treating the disputed count as a confirmed missed patient. |
| `PMC12285374.1:p2`, SFT | Case-specific paragraphs describe a tumor and pathology, while the explicit shared two-case SFT diagnosis appears in the abstract/introduction. Compact packets can omit that shared statement. The SFT abbreviation regex also contains escaped word-boundary defects. | Preserve source-bound shared case-series assertions with explicit scope; fix the regex and gold evidence. Do not ban all abstracts/captions/discussion. |
| `PMC12285374.1:p1`, meningioma | Output sets tumor laterality to bilateral using only a sentence about **bilateral craniotomy**. The procedure's laterality does not establish the tumor's laterality. | Check field-level entailment, including anatomy, laterality and episode; preserve null when unstated. |
| Same SFT case | Live oncology records pancreas as primary site despite the final specimen statement describing growth from transverse-colon mesentery; other outputs infer remission from absence of recurrence. | Preserve provisional imaging localization versus final pathology and documented versus derived disease status. |
| `PMC12285374.1:p2`, transfusion | A packed-red-cell transfusion appears as a confirmed condition/complication in one baseline bundle. | Separate procedure, indication and complication; a source mention alone does not validate every typed assertion. |

No main arm hit the finite set of known forbidden assertions. **This is not a
zero hallucination rate**: the laterality and category errors above were outside
those probes. Literal evidence and a valid schema cannot substitute for entailment.

## Why GEPA did not establish a bundle improvement

The joint search proposed groups covering all 29 components, but accepted no new
candidate. Its only initial frontier candidate was the independent warm start,
which scored **.772 versus .879 for the original bundle** on initial validation.
The original was evaluated outside the GEPA frontier instead of being used as
the stronger initial candidate. All proposed minibatch changes scored no better
than their parent, so none reached full candidate validation.

There were additional experiment defects:

1. **The fresh confirmation timed out.** The receipt says
   `confirmation_complete=false` and `wall_budget_exhausted=true`. The printed
   zero score/zero facts means unavailable confirmation, not demonstrated zero
   medical accuracy. Report status `completed` obscures this distinction.
2. **The time reserve was not enforced inside a rollout.** Search stopping was
   checked at iteration boundaries, while rollouts used the final campaign
   deadline. A last expensive iteration could consume the confirmation reserve.
   Original validation alone took about 360 seconds; an old/new minibatch pair
   can exceed the nominal reserve before reflection overhead.
3. **Minibatches were not component-balanced.** One oncology/procedures/outcomes/
   social group compared three negative articles containing no patients. Such
   minibatches provide no signal for improving those patient facts. Many families
   had too few distinctly labelled sources, particularly medications, outcomes
   and vision.
4. **Two reflection calls exceeded context.** Large feedback and candidate
   dictionaries were supplied to group reflection. Some reflected instructions
   also proposed excluding whole source classes, including captions/abstracts,
   which can contain the very facts this dataset needs.
5. **The repair-family search crashed:** `KeyError:
   'coverage_repair_observations'`. `PilotRunner.call()` forwards the operational
   alias to `ItemRepair.create()`, which indexes canonical `TASK_MODELS` names.
   Alias normalization must occur before constructing the repair plan.

The saved selected joint strategy contains 29 empty supplements: it deliberately
fell back to original prompts. This campaign therefore does not demonstrate that
GEPA cannot improve the pipeline; it demonstrates that this experiment produced
no validated joint improvement.

## Completion bottlenecks and image evaluation

At 64K context, live-complete had 24 pre-call context failures across three seeds,
including 16 summary-completion and seven timeline-completion tasks. Independent
GEPA had 27 such failures, including 18 summary and nine timeline completions.
These fail **before inference**. Summary completion appends full review rows,
including repeated candidates and evidence metadata, to the source prompt.
Increasing reasoning or changing prose instructions cannot repair this.

Coverage review also fails repeatedly because a represented feature must link
only to facts with exactly the inventory's chosen task label. Clinically related
facts can appear in another domain. This needs audited cross-domain equivalence
and stable proposition IDs, not an unconditional relaxation of patient, episode,
specimen or polarity checks. Repair erasure guards can also reject removal of
invalid fields: protect verified facts while allowing targeted correction of the
specific invalid value.

Ordering remains the weakest measured bundle dimension: the best arm recovers
only **53/123 selected relations (43.1%)**. Some misses are event merging or
ambiguous duplicate nodes rather than reversal; both matter for EHR usability.
Build encounter-specific events and explicit partial ordering. Do not impose a
total clinical order merely from the narrative's sentence order.

Live-complete matched **25/30 figure-ownership probes**, versus independent GEPA's
17/30. The high-reasoning arm matched 6/6 limited pixel inventories, but those
inventories cover only two figures repeated across seeds and coarse image/panel/
chart properties. They do not validate detailed visual descriptions, anatomy,
clinical significance or numerical graph readings. No broad vision winner is
established. Next vision gold should adjudicate per-panel ownership and separate
visible findings, caption statements and clinical interpretation.

## Serving and resource lessons from this run

- Active main extraction telemetry averaged **83.0% GPU utilization**, with
  median 99%; one-GPU component optimization averaged **98.8%**. These are sample
  averages tagged by campaign phase, not time-weighted kernel utilization.
- Eight-GPU joint optimization averaged only **42.0%**, median 0 across GPU
  samples. Small sequential rollouts and sparse patient batches left replicas
  idle. Cache unchanged parent evaluations and right-size optimization stages
  or supply enough independent work before reserving eight GPUs.
- 128K confirmations did not establish a blanket quality improvement. Their
  sources/seeds must be compared with the matching 64K trials, not a differently
  pooled three-seed row. Use larger contexts selectively for verified overflow.
- The completed ordinary live-complete control yielded 2,843 tok/s, versus roughly
  9K in DFlash main trials. This supports retaining the established DFlash recipe,
  but the companion ordinary `gepa` cell was deferred, and one seed cannot resolve
  stochastic clinical quality differences.
- High reasoning generated more tokens and took about **1.71×** live-complete's
  pooled arm time, while delivering fewer required facts. It had 14 terminal
  length failures in primary main task receipts; seed 42 lost the three-patient
  poisoning roster. This compares the deployed 16K-cap protocol, not an unlimited
  high-reasoning model. Try escalation for a specific truncated/complex task, not
  high reasoning across the whole pipeline.

## Next campaign, in order

1. **Repair and freeze the measurement system.** Adjudicate the disputed cases,
   semantic field equivalences and shared statements; preserve the original
   benchmark version. Add source-reviewed positives and hard negatives for
   laterality, normal labs, changing diagnoses, repeated treatments, medication
   details and multi-patient/table/panel attribution. Newly reviewed development
   errors belong in training; acquire a separate untouched test set.
2. **Fix the deterministic failures first.** Normalize repair aliases; test
   actual saved failure replay. Give every fact a stable ID and compact payload,
   reference source evidence by ID instead of copying it repeatedly, and count
   tokens before a call. Batch late summaries/graph edits by clinical episode;
   route genuine overflow to 128K. Retain accepted facts and use explicit graph
   edits for missing events/edges with cycle and link-retention checks.
3. **Run focused, component-aware GEPA.** Start from the strongest original
   validated bundle; retain originals in the candidate frontier. Select training
   minibatches containing relevant positive and negative cases. Cache parent
   evaluations, compact reflection feedback and enforce a separate search
   deadline. Reserve measured time for full repeated validation, report missing
   validation as unavailable, and promote only with clinical nonregression.
4. **Test selective completion/escalation against the plain baseline.** Compare
   existing direct/source-aware extraction, compact targeted repair, episode
   timeline edits and panel ownership on identical sources/seeds/output budgets.
   Use medium by default, larger context/caps for measured overflow and high
   reasoning only for designated vision or difficult repair calls. Measure
   supported facts, verified ordered courses and complete usable bundles per
   GPU-hour alongside tok/s and per-component latency.

This review adds analysis and operating documentation. It does not change
production defaults or implement the proposed next campaign.
