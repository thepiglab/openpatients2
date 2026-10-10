# Clinical architecture run: useful candidates lost during verification

Archive: `run-20261007-174142-results-20261007-202341.tar.gz`.
Analysis date: 2026-10-07. All 32 planned trials completed. Model and working
source cleanup succeeded. Four independent one-B200 workers ran for 51.7–61.7
minutes each, totaling 3.70 GPU-hours; these durations exclude queue waiting.

**Keep the current extractor and compiled tables as the control. Do not promote
the new automatic verifier or encounter replacement. The useful improvement is
in candidate recovery; the next architectural change should compile patient
records from separately grounded source claims and relations.**

## Reproduced results

All eight saved bundle scores were reproduced from exported patients, predicted
rosters, figure tasks and visual annotations. Source hashes and reference quotes
also passed validation. No model calls or new article downloads were made.

The existing report's 259 required checks include four timeline-event checks.
The first column below isolates the remaining **255 clinical checks × two
seeds**, avoiding double interpretation of timeline performance.

| Arm | Clinical checks / 510 | Clinical fraction | Ordering checks / 82 | Valid / other tasks | GPU minutes for this arm |
| --- | ---: | ---: | ---: | ---: | ---: |
| Baseline + compiled tables | 467 | 91.6% | 25 | 2,175 / 118 | 66.3 |
| Independent source verification | 432 | 84.7% | 16 | 1,375 / 24 | +25.8 |
| Encounter extraction + verification | 422 | 82.7% | 14 | 2,821 / 113 | +44.7 |
| Additional coverage + verification | 459 | 90.0% | 16 | 3,945 / 136 | +50.9 |

For comparison with `SUMMARY.md`, the original mixed checklist scores are
475/518, 437/518, 430/518 and 464/518 respectively. The second seed repeats the
same reference facts; these are not 518 independent observations. Counts measure
delivery of a partial development checklist, not comprehensive medical accuracy.
“Other tasks” includes failed/partial tasks, not necessarily incorrect clinical
claims. Arms invoke different task families and have different denominators.

All arms had zero matches against 88 repeated forbidden probes, with 4–7
unavailable probes. This cannot establish precision or absence of hallucination.
The existing development “test” articles have been examined in prior campaigns.
No new blinded holdout was used. Figures and patient discovery were frozen for
the experimental arms, so this run does not test improvements to either.

## The useful signal concealed by the final scores

I replayed the saved `candidate_sections` before verification. For the comparison
including timeline-event checks, I restored the identical baseline companions;
otherwise a subsequently failed timeline would be incorrectly charged to
candidate extraction. The clinical-only comparison is:

| Path | Before the gate / 510 | Delivered after the gate / 510 | Change caused by filtering |
| --- | ---: | ---: | ---: |
| Baseline candidates → independent verifier | 467 | 432 | −35 |
| Encounter candidates → verifier | 450 | 422 | −28 |
| Baseline + coverage candidates → verifier | **485** | 459 | **−26** |

The additional coverage candidates gained **18 checks and lost zero baseline
clinical checks** before filtering. Eleven gains were normal laboratory results
from the same veterinary article, repeated across seeds. The gains therefore
justify testing complementary extraction, not claiming a broad 95.1% accuracy.
Extra candidates and duplicates have not been exhaustively checked for false
positives. Turning the gate off is not sufficient production validation.

The table compiler also delivered a real paired improvement: raw baseline
467/518 → corrected baseline 475/518, eight gains and no lost checks. This is a
within-run comparison. Comparing 475 with the previous campaign's 473 would mix
fresh stochastic baseline generations with the table change.

## What failed, with source checks

The [ten-candidate source audit](CLINICAL_ARCHITECTURE_20261007.source-audit.json)
contains the exact candidates, gate decisions and verified source citations.
This is a selected assistant review, not a blinded clinician precision study.

1. **Blind questions sometimes lose the identity of the claim.**
   `blind_questions()` uses `name`, then `attribute`, then the task name. Outcomes
   and care plans commonly have neither of the first two fields. Several distinct
   facts consequently become identical questions about “outcomes” or
   “care_plans,” distinguished only by arbitrary IDs. Reconciliation then rejects
   a true fact because its answer appeared under a different question ID.
   In PMC13314005.1, the explicitly described fibula reconstruction after six
   months was compared to an answer about absent adjuvant treatment. In
   PMC13542385.1, postoperative-day-one discharge was rejected because the answer
   about discharge was under q4 rather than q3. A diagnostic string screen found
   61 exclusion reasons referring to question/topic alignment; that is not an
   adjudicated count of 61 false rejections.

2. **The gate confuses clinical evidence, schema classification and missingness.**
   In PMC13618778.1 it rejects explicitly performed blood tests because the word
   “procedure” is not in the source. In PMC13587181.1 it withholds a supported
   SERPINC1 variant while objecting to unknown/null origin and timing fields.
   Missing information can merit enrichment; it is not a positive false claim.
   A normalized type label needs a defined mapping contract, not literal matching
   against prose.

3. **Occurrence identity is missing from the verification target.**
   PMC13612109.1 explicitly reports 150 mCi RAI in 2024 and later rules out
   empirical RAI. The verifier compares the earlier administration to that later
   decision and rejects it. In PMC12802722.1, afebrile admission and subsequent
   fever are treated as a contradiction. The candidate's unanchored `current`
   field needs correction, but the original negative finding must survive as an
   admission observation. These are exactly the longitudinal distinctions the
   downstream EHR needs.

4. **Real source discrepancies are deleted instead of represented.**
   In PMC12285374.1, the text calls Ki67 negative while the figure caption
   describes occasional Ki-67 labeling. The gate rejects the positive candidate
   using the text, and rejects the negative candidate using the caption. Both
   source statements should remain traceable, with normalized interpretation
   unresolved. Do not decide medical truth from an apparent publication conflict.

5. **One error still has too large a failure radius.**
   The source-verification arm withheld 192 candidate facts in groups where
   filtering was enabled. It erased ten case-context sections and three oncology
   sections across the two seeds; a removed tumor can leave dangling references
   that invalidate an entire oncology section. Another 24 exclusion reasons say
   verification was unavailable. A failed question batch must not become evidence
   against all six facts in that batch.

6. **The encounter reader is another difficult generation task.**
   It failed 52/120 calls in encounter-state and 57/120 in coverage-pass. Common
   causes include unparseable output, missing source-unit dispositions, nonliteral
   time expressions and invalid enum combinations. Across both arms, 48 terminal
   reading failures were unparseable objects. Requiring an entire elaborate
   encounter inventory to pass atomically recreates the failure mode we wanted
   chunking to solve. Failed readings do trigger broad task fallback; failed
   reading counts are not equivalent to wholly lost articles.

7. **Timelines are still generated as large, fragile objects.**
   Reconciled timeline tasks failed 17/46 in source-verification, 10/54 in
   encounter-state, and 22/54 in coverage-pass. Saved errors include source-span
   errors, unknown fact IDs, calendar-precision validation and context overflow.
   More candidates enlarge the timeline request. There is also event-identity
   ambiguity in the scorer, so a missing ordering match is not automatically a
   medically wrong order. The delivered graph is nonetheless not dependable.

8. **A failed roster still suppresses two entire patients.**
   PMC13582412.1 produced no roster in either seed because no complete object
   could be parsed. The retained article explicitly presents two renal-tumor
   cases. Seven required checks per seed are unreachable downstream. The
   experimental arms freeze this failure, so they cannot repair it.

The reviewer sometimes makes useful observations: it flags an “asymptomatic”
item marked absent, and a performed CT examination marked absent because its
embolism result was negative. That argues for localized attribute correction,
not wholesale acceptance of the baseline or wholesale rejection by this gate.

## Why the qualification test did not protect us

Seven of eight shard/seed groups passed all 20 authored controls and applied
filtering; one group failed one true control and remained in shadow mode. Thus
46/54 patient replicas were filtered. Passing these small, mostly isolated
controls did not validate natural multi-occurrence questions, schema labels,
source discrepancies or dependent references. Qualification also made the
export policy vary by stochastic shard/seed result. Keep these controls as
regressions, not as permission to delete natural-case facts.

## The larger next direction: source claims compiled into records

Change the internal unit from a generated clinical object to a **source claim
plus separately grounded relationships**. Keep the final public schemas as
database views. This differs from our existing evidence-ledger/encounter trials:
those still ask the LLM to regenerate full clinical sections and whole timelines,
then ask another LLM pass whether to discard them.

```text
JATS clauses / table cells / figure panels + stable source IDs
                            |
              +-------------+-------------+
              |                           |
       patient-focused reader      source-order reader
              |                           |
              +---- candidate claims -----+
                            |
       CPU: copy exact source values, units and provenance
                            |
       parallel, small decisions on ambiguous relationships
       patient? same occurrence? polarity? planned/executed?
       before/after/overlap/unknown? caption or pixel claim?
                            |
          source claims + accepted links + unresolved links
                            |
       CPU: compile clinical sections and partial timeline
                            |
       patient-state queries + fact-linked summary + media
```

Concrete requirements for a vertical prototype:

- **Source selection instead of repeated transcription.** Give clauses/cells
  stable IDs; models select spans and propose relationships. Code copies literal
  quantities, source strings and offsets and builds the schema. An ambiguous
  number/unit parse remains raw. A matching number alone does not prove that it
  belongs to the patient, test or occurrence.
- **Decouple patient enumeration from the giant roster.** Enumerate identity
  mentions first, resolve duplicates separately, then attach facts and figures.
  An invalid figure assignment must not prevent discovery of two otherwise
  explicit patients. Keep aggregate/background and unresolved owners available.
- **Accept or challenge fields and links independently.** A blood-test result
  can remain supported while its date is unresolved. Uncertain owner or result
  identity blocks patient-level delivery of that claim, not unrelated neighbors.
  Types, unknowns, omitted evidence and contradictions require different rules.
  Model disagreement routes work; it is not proof of falsity.
- **Resolve occurrence identity before ordering.** Distinguish the 2024 RAI
  administration from the later decision against another course. Resolve repeated
  abstract/body mentions without merging separate administrations. Classify small
  event pairs with original supporting context, then assemble a partial graph on
  CPU. Unknown remains unknown; source order alone does not establish chronology.
  Cycles trigger review rather than arbitrary deletion or invented ordering.
- **Preserve publication conflicts.** Store both Ki-67 source assertions and
  their scope. Expose unresolved normalized interpretation. A solver can enforce
  consistency of accepted links; it cannot determine medical truth.
- **Preserve coverage without inflating duplicates.** The complementary reader
  supplies candidates. Match them by patient, concept, occurrence and evidence,
  not exact JSON equality. Only audit disagreements/uncovered source regions.
  Extend the successful table compiler to single-patient tables; the present
  Case/Patient-N restriction misses useful tables such as the veterinary one.
- **Generate summaries last.** Compute patient-state views from the accepted
  occurrence graph, retaining uncertain intervals and conflicting claims.
  Historical treatment must not silently become indefinitely active medication.
  Captions and image observations remain distinct; figure/panel ownership is a
  separately scored relation.

This is a testable architecture proposal, not evidence that it already beats
Glimmer's baseline. The first implementation should cover observations,
medications, procedures and their relative order on a small complete slice;
rewriting every domain before validating the representation would repeat our
current mistake.

## A next experiment that distinguishes causes

1. **CPU replay and annotation first.** Preserve the 18 recovered checks and
   examine added claims as well as rejected ones. Turn the ten audited examples
   into failure regressions. Use source-annotated claim/field labels to test the
   compiler independently of extraction. Gold-input results are diagnostic only.
2. **Create fresh evaluation material.** Use a bounded set of roughly 12 new
   articles spanning dense tables, multi-patient cases, treatment changes,
   negative tests, conflicts and figures. Annotate the chosen clinical slice
   before viewing outputs, including normal/negative/history facts. Hold back
   entire article families. Clinician adjudication remains needed for a release
   claim; this small sample does not certify broad safety or accuracy.
3. **Compare three meaningful arms.** Current baseline; complementary candidates
   with advisory-only criticism; source-selection/relationship compiler. Use the
   same sources and budgets, two seeds, and four independent TP1 B200 workers.
   Stop unsuccessful prototypes after the diagnostic subset. Do not pay for an
   all-domain overnight campaign just to learn that a contract is broken.
4. **Add a small capability ceiling comparison.** Run identical bounded
   source/relationship questions through a stronger reference model when access
   and budget are arranged. Source-review its output too. If both models fail at
   the same boundary, fix the interface or reference; if only Glimmer fails,
   measure selective escalation rather than adding more Glimmer critique loops.
5. **Measure the downstream task.** Supported unique claims, false claims,
   patient/occurrence errors, state-query correctness, event identity and order,
   unresolved coverage and GPU-seconds per supported fact. Audit accepted and
   withheld outputs. Freeze primary metrics and do not optimize against the
   repeatedly exposed 31-article checklist alone.

GEPA can optimize the new compact decision prompts once their targets are
annotated: ownership, occurrence linking, assertion and temporal relations.
Use independent train/validation/test article families and precision/recall
constraints. It should not optimize a self-review verdict that is itself wrong,
nor use schema-valid task count as the reward for clinical completeness.

## Research informing this choice

[ClinicalNLP 2022](https://aclanthology.org/2022.clinicalnlp-1.12/) found output
representation mattered for clinical temporal extraction, with one-event-at-a-time
generation outperforming the natural whole-relation formulation in that study.
[CTRL-PG](https://arxiv.org/abs/2012.08790) studies document-level temporal
dependencies and global inference. These support testing local relation decisions
plus global constraints, but their trained systems do not establish gains for our
untuned Glimmer pipeline.

[VERGE](https://arxiv.org/html/2609.04366v1) reports precision improving from .764
to .849 on a narrow clinician-labeled symptom/family-history task, with recall
.705 → .702. Its targeted false-negative recovery also introduced new false
positives. The relevant lesson is to measure the tradeoff on labeled natural
claims; verification is not a guaranteed recall improvement for comprehensive
longitudinal extraction.

## Artifacts and limits

- [Reproduced metrics and replays](CLINICAL_ARCHITECTURE_20261007.metrics.json)
- [Selected source audit](CLINICAL_ARCHITECTURE_20261007.source-audit.json)
- [Proposed implementation contract](../docs/SOURCE_CLAIM_COMPILER_DESIGN.md)

The archive contains 3.31 GB expanded. Analysis retained a bounded approximately
313 MB temporary selection and compacted receipts rather than unpacking it all.
Temporary analysis files were removed after saving these compact artifacts; the
user's original archive remains intact. No runtime defaults changed.
