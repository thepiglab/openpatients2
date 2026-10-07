# PMC patient-bundle review — 2026-10-07

The serving and completion infrastructure improved, but no new production prompt
winner is established. Clinical-only GEPA leads the pooled development checklist;
original whole-article extraction has the strongest held-out result among the
main live-discovery arms. Timeline completion and field-level entailment remain
the most useful targets for the next campaign.

## Provenance and completion

Reviewed `run-20261006-142003-results-20261007-083134.tar.gz`. Archive SHA-256:
`3b3d164d695fa9c80c6f5b50926336a6f803b339cab3403f81c0d1e8ce7ef9fc`.

The archive was streamed, not unpacked wholesale. Main extraction receipts cover
36,083 task files; selected source-bound patient exports were manually inspected.
Independent recomputation matched clinical, timeline and summary scores for all
seven retained seed-42 patient-export arms. Sources are the same 31 canonical
PMC fixtures used in development, with the revised 303-check reference: 259
required clinical checks and 44 known forbidden assertions. This rubric differs
from the preceding run; raw counts must not be treated as a controlled before/
after prompt comparison.

CPU and bootstrap completed. Main GPU status is **partial** because one ordinary-
decoding `gepa` seed-42 comparison was deferred for the GPU deadline reserve.
**43 comparisons completed**, with no fatal main-stage failures. Model and source
cleanup receipts say deleted. Independent GEPA is partial because 17 families
lack sufficient disjoint labelled examples. These differ from task-level failures.

Allocated-stage durations, excluding CPU and queue: bootstrap 40.4 minutes;
one-GPU optimization 2h 7m; main eight-GPU evaluation 5h 46m. Total **8h 33m**.

## Comparable main results

| Arm | Clinical checks / 777 | Held-out / 147 | Clinical valid / partial / failed | Ordering / 123 | Output tok/s, all 8 GPUs |
| --- | ---: | ---: | ---: | ---: | ---: |
| Baseline (frozen patients) | 686 (88.3%) | 138 | 1248 / 3 / 9 | 31 | 9,419 |
| Clinical audit (frozen) | 716 (92.1%) | 138 | 1249 / 5 / 6 | 46 | 9,734 |
| Complete (frozen) | 721 (92.8%) | 138 | 1251 / 7 / 2 | 42 | 8,812 |
| Joint pixels (frozen) | 699 (90.0%) | 139 | 1246 / 9 / 5 | 46 | 9,384 |
| Original, live complete | 707 (91.0%) | 138 | 1199 / 6 / 13 | 50 | 8,543 |
| Independent GEPA, all selected | 708 (91.1%) | 132 | 1179 / 6 / 5 | 53 | 8,352 |
| Joint fallback: original prompts | 703 (90.5%) | 141 | 1203 / 7 / 8 | 47 | 9,007 |
| GEPA clinical only | 728 (93.7%) | 135 | 1200 / 7 / 11 | 49 | 8,498 |
| GEPA auxiliary only | 724 (93.2%) | 126 | 1181 / 4 / 5 | 49 | 8,798 |
| Joint fallback + joint pixels | 715 (92.0%) | 135 | 1201 / 8 / 9 | 44 | 8,744 |
| Joint fallback + high reasoning | 703 (90.5%) | 137 | 1208 / 5 / 5 | 42 | 8,253 |
| Original, whole article | 724 (93.2%) | 140 | 1203 / 8 / 7 | 43 | 8,055 |

All rows use three seeds, 64K context, 32K prefill and DFlash. The 777 checks are
259 probes repeated three times, not 777 independently annotated clinical facts.
The held-out column is 49 checks on the same nine articles repeated three times;
these articles were withheld from this optimizer, but have previous pilot exposure.
Frozen-patient and live-discovery rows are separate experimental conditions.
"Valid" means accepted structure/evidence checks, not proven medical correctness.
Output token rates include reasoning/answer, repair and auxiliary calls; they
exclude startup/queue and do not measure complete valid patient records per second.

Clinical-only GEPA gained **54 checklist hits and lost 33**, net +21, versus
original live-complete. The net gain is concentrated in two patients: poisoning
`PMC12802722.1:p2` (+12) and `PMC10998798.1:p1` (+9); changes elsewhere cancel.
Its held-out result fell from 138 to 135. Original whole-article extraction scored
140/147 held-out, but also needs paired confirmation on new articles. No promotion
is justified by pooling train, validation and test cases.

## Source checks and persistent errors

- `PMC12285374.1:p1`: complete and joint-fallback seed 42 still assign **bilateral
  meningioma** using “He underwent bilateral craniotomy for meningioma in 2009
  and 2014.” The quote establishes procedure laterality, not tumor laterality.
  The new forbidden probe correctly catches it. Final SFT origin in the reviewed
  outputs is transverse-colon mesentery rather than the old pancreatic-origin
  error. Correcting one attribute does not validate all attributes in the item.
- `PMC13549756.1:p2`: the inspected outputs preserve cow's-milk allergy as
  possible, grounded in concern about CMPA. Sensitization/testing and confirmed
  clinical allergy must remain distinct.
- `PMC13612109.1:p1`: the reviewed outputs mostly retain separate RAI 100 mCi in
  2023 and 150 mCi in 2024, plus the later 6.6 GBq Lu-PSMA-617 treatment. Some
  outputs still combine EBRT with the first RAI event, attaching the RAI year
  to the combined encounter. Literal evidence must bind time to its event.

Zero known forbidden hits in an arm is not zero hallucinations: the checklist
covers a finite set, and some forbidden checks cannot be scored when the section
is unavailable. Pixel ownership is measured, but detailed radiological description,
clinical significance and chart reading were not newly adjudicated here.

## The main remaining bottleneck: chronological structure

Among main receipts, timeline completion accepted **487/872 (55.8%)** calls,
with 271 failed and 114 partial. Frequent terminal errors include 63 lost-existing-
fact-link errors, 49 unknown/wrong-patient fact IDs, 51 unparseable answers and
**34 pre-call context overflows**. Error messages can combine multiple defects;
these counts are not an exclusive partition. All 34 overflow errors are in
completion of timelines. Summary completion now accepted **856/872 (98.2%)**, with
no pre-call context overflow in these main receipts.

The best main arm recovers **53/123 (43.1%)** selected ordering relations.
An absent relation can result from a missed event, merged encounters or ambiguous
node mapping, as well as a missing/reversed edge. This is still weak for creating
longitudinal EHR records. Exact calendar dates are not required; supported relative
ordering, distinct encounters and links to clinical facts are the priority.

Coverage and media repair also reject erasure of existing data. Some coverage
backfills are blocked by changing a `limitations` string rather than a verified
clinical fact. Refine the protection boundary: preserve accepted patient facts
and evidence, while allowing correction of invalid fields and nonclinical metadata.
Do not weaken patient, specimen, uncertainty or encounter checks globally.

## What GEPA actually changed

Independent optimization completed 12/29 families. Only **four** selected
nonempty rewrites; eight completed searches retained the original instruction.
The remaining 17 need more disjoint annotations. The previous repair alias crash
is absent, and the repair search now completes.

| Rewritten component | Original validation | Selected validation | Validation articles |
| --- | ---: | ---: | ---: |
| Conditions | .833 | 1.000 | 3 |
| Observations | .825 | .925 | 3 |
| Targeted repair | .624 | .824 | 5 |
| Timeline completion | .572 | .770 | 3 |

The clinical-only arm uses the conditions and observations rewrites; it excludes
the repair/timeline rewrites. The complete independent program adds those two,
but delivers 708 clinical hits versus the original's 707, while held-out hits fall
132 versus 138. Local component gains do not guarantee a better whole bundle.

Joint GEPA starts from the original prompts now, and fresh confirmation completes.
It accepts **no mutation**: the only frontier candidate is the original. Five
group proposals complete, with equal or worse training-minibatch scores. The
summary/timeline proposal fails exact requested-group validation twice; the last
vision rollout reaches its budget and is unavailable. Only 49 metric calls run
against a nominal 512 cap. A fresh original control recovers 49 validation facts
versus the initial original's 52: this is repeat variability, not a learned
candidate losing three facts. The printed `gepa` arm is consequently an original-
prompt fallback, not evidence of a joint GEPA improvement.

Concrete issues in rejected proposals: a conditions rewrite invents incompatible
assertion enums; the shared coverage-repair rewrite carries medication-specific
instructions into every clinical domain; several minibatches are already near
perfect; and `joint_figure` is proposed but never invoked by the staged-pixel
joint program. Optimizing a prompt absent from the evaluated path has no reward
signal. None of these rejected instructions became production defaults.

## Serving findings

Active device-sample utilization averages are **99.4%** for one-GPU independent
search, **98.9%** for one-GPU joint search, and **81.6%** for main extraction
(median 99%). These are phase-tagged samples, not time-weighted kernel averages.
The prior eight-GPU joint search averaged only 42%; the resource-sizing fix worked.

The ordinary live-complete seed-42 control produces **2,550 tok/s**, versus
**8,858 tok/s** for its DFlash counterpart (3.47×). One seed is insufficient to
resolve small clinical differences. The companion ordinary `gepa` comparison is
still deferred. Main medium-reasoning arms run around 8.1–9.7K output tok/s across
eight GPUs. High reasoning takes about **1.64×** original live-complete arm time
and recovers fewer checked facts. Two-seed 128K original live-complete delivers
472/518 clinical hits and 24/82 ordering links, with no convincing global gain.
Keep the existing FP8, medium reasoning, TP1 replicas, DFlash, 64K/32K recipe;
use larger contexts or high reasoning selectively on residual hard tasks.

## Recommended next campaign

1. **Patch timelines rather than regenerate them.** Preserve accepted event/fact
   links in code; request additions and a separate edge-only pass with short,
   locally mapped IDs. Batch the unresolved facts/edges, retrieve their original
   evidence, and check unsupported ordering/cycles before merging. Keep unknown
   relative order unknown. Escalate only real overflow cases.
2. **Add attribute-level entailment repair.** Audit each laterality/site/specimen/
   time/diagnostic-certainty attribute against the appropriate quote and encounter.
   Repair or quarantine the specific unsupported field, preserving supported facts.
   Include multi-patient leakage, treatment-as-diagnosis and sensitization-as-allergy
   hard negatives in source-reviewed gold.
3. **Run focused, paired GEPA ablations.** Test original, conditions-only,
   observations-only and both on balanced new held-out articles, with multiple
   seeds. Optimize deployed repair paths separately, with a generic repair core
   plus task-specific guidance. Enforce schema-compatible reflection and exact
   requested component keys before spending rollout compute. Sample actual
   failures rather than saturated training batches; do not lower annotation minima
   or substitute the model's self-judgments for gold.
4. **Make extra passes conditional.** Trigger expensive inventory/backfill/
   timeline/high-reasoning calls for a documented omission, contradiction or
   validation failure. Measure supported clinical facts and usable ordered bundles
   per GPU-hour, not generated tokens alone. Joint pixel decoding and universal
   128K sweeps are lower priority than evidence and event-linking corrections.

Detailed counts, receipts and recomputed-score checks are in
[BUNDLE_CLINICAL_20261007.metrics.json](BUNDLE_CLINICAL_20261007.metrics.json).
No extraction or prompt default is changed by this review. The supplied archive
is retained; the small temporary review extraction is removed after analysis.
