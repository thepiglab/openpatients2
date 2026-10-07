# Controlled component results — 2026-10-07

Keep the existing full extraction baseline and the independent single-GPU
scheduler. Prioritize the table interface fix: an offline replay of saved
responses recovers seven reviewed checks without another model call. Keep the
working audit transport and negative-finding guards, but redesign contextual
verification and event assembly before promoting either component.

## Verification and resources

Archive: `run-20261007-144414-results-20261007-161541.tar.gz`.
SHA-256: `a4235463c12694f9e048712e057b638d27954011639f61f77d00fbc8167a68f0`.
All 32 shard/seed/arm trials completed; all four workers completed. Model, source
working copies and worker-cache cleanup succeeded. Recomputing all eight saved
bundle-quality evaluations from the patient, roster, figure-task and visual
exports reproduced their results exactly. Compact diagnostics are in
[the metrics file](CONTROLLED_COMPONENTS_20261007.metrics.json).

Workers ran for 27.4, 28.0, 35.2 and 30.6 minutes, totaling 2.02 GPU-hours. Their
telemetry starts were within four seconds; the GPU portion lasted about 35
minutes elapsed. These durations exclude the preceding CPU/queue stages.
Sampled GPU utilization averaged 68–73%, with 99% medians, including startup and
idle tails. The scheduler worked; there is no evidence here that we need an
all-eight-GPU allocation. Lower average utilization than the prior run partly
coincides with a shorter campaign and fixed startup overhead; kernel-level
causation has not been established.

## Submitted results, pooled across two seeds

| Arm | Required checks | Reviewed event nodes | Ordering checks | Valid / other calls | Output tok/GPU-s | Inference GPU-minutes |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Full baseline | 473/518 (91.3%) | 78/106 | 36/82 | 2013 / 116 | 2051 | 62.54 |
| Table addition | 473/518 (91.3%) | 78/106 | 36/82 | 13 / 5 | 1836 | 0.97 extra |
| Attribute audit | 471/518 (90.9%) | 78/106 | 36/82 | 670 / 34 | 1864 | 12.72 extra |
| Episode rebuild | 472/518 (91.1%) | 72/106 | 15/82 | 251 / 0 | 1643 | 11.17 extra |

The denominator repeats 259 reviewed development checks at seeds 42 and 43;
it is not 518 independent facts or comprehensive medical accuracy. A few checks
in this total inspect timeline exports. In particular, episode rebuilding leaves
all typed clinical sections unchanged; its one lost required check, `c039`, is
an outcome event-description check. The extra component rates cover different
work from the baseline and are not full-pipeline throughput comparisons.

Known forbidden checks have zero hits in every arm, but four of the 88 repeated
forbidden checks are unavailable. This finite list does not measure all false
medical assertions. Figure/pixel outputs are fixed across arms, so this run
does not establish better image description quality.

The composite bundle score is unsafe as the sole selection criterion: at seed
42 episode rebuilding scores 0.8529 versus baseline 0.8255 even though ordering
hits fall from 18 to 8. Avoiding one reversed relation removes a 0.05 penalty,
which outweighs multiple lost ordering hits under the current weights. Report
node coverage, order coverage, wrong order and unsupported links separately.

## Tables: a contract failure conceals useful outputs

The submitted table component changes **no clinical section** in either seed.
Across the three poisoning cases and two seeds, it identifies 234 target cells,
marks 104 covered, and requests the remaining 130. Five malformed-output batches
fail parsing. Thirteen calls are marked valid, but their 90 cell decisions are
all quarantined: 89 for citation-format mismatch and one for inconsistent
missing-result representation. Thus "13 valid calls" delivers zero new cells.

The specific contradiction is ours: `cell_messages` asks the model to put the
segment ID in `source_section`, while `schemas.Evidence` describes the same field
as the source heading. The model consistently returns a heading. Quotes in the
89 otherwise valid candidates are exact complete table rows; this is not a
failure to read the quantities. The supplied `cell_id` already identifies the
correct source row, patient column and encounter header.

### CPU-only diagnostic replay

For each final parsed response, require the supplied cell ID, an exact complete
row quote and the row's actual heading. Resolve that heading to the cell's known
canonical segment ID. Leave all medical values unchanged and rerun the existing
schema, source, patient-column, magnitude, comparator, unit and encounter checks.
This accepts 89 cells; the missing-result candidate remains rejected. The five
malformed batches remain unavailable. No model calls or newly invented values
are involved.

Append those accepted rows to copies of the same baseline and rescore:

| Seed | Original | Offline table replay | Gains / losses |
| --- | ---: | ---: | ---: |
| 42 | 240/259 | 245/259 | +5 / −0 |
| 43 | 233/259 | 235/259 | +2 / −0 |
| Pooled | 473/518 | 480/518 (92.7%) | +7 / −0 |

The gains are all in `PMC12802722.1`: missing 48-hour sodium and creatinine
measurements, and case-2 CRP, hemoglobin and ethylene-glycol results. The values
were checked against the retained source table and target case columns. These
are **diagnostic replay results**, not the delivered campaign score and not
proof of complete semantic accuracy for every one of the 89 additions.

Next: the model should emit cell IDs plus the few semantic decisions that remain
unknown. Code should attach immutable source spans, values and units. Missing
cells should receive explicit dispositions. This also avoids repeatedly copying
long table quotes and constructing full observation objects. Keep unresolved
column ownership visible; the current inventory only resolves explicit,
source-grounded Case/Patient N columns and cannot claim broad table coverage.

## Attribute audit: transport fixed, clinical policy still inconsistent

534/535 audit requests validate, versus the prior pointer-related failures.
There are 492 accepted challenges: 361 unsupported, 117 uncertain, eight wrong
patient and six wrong episode. Repairs run for 169 domain requests; 136 validate
and 33 fail. Twenty-eight repair failures are rejected for changing an
unchallenged value. That protection is useful, but asking for a whole domain
again makes accidental edits more likely.

109 patient-domain exports change, with 204 differing leaf/list fields; 146
changes replace a value with null or unknown. That is a strong deletion bias,
not an independently adjudicated precision improvement. Selected source reviews:

- **Context lost in both seeds:** `PMC13582412.1`, source `b00010`, documents a
  left renal mass followed by radical nephrectomy in that patient. The audit
  changes procedure laterality from left to unknown because the procedure
  sentence does not repeat the side. That loses reviewed check `c012` twice.
  The critic and benchmark apply different standards for contextual support.
  Resolve the policy explicitly; neither silently reward guessing nor require
  every fact to repeat every qualifier in one sentence.
- **Useful correction:** `PMC13542385.1`, `b00008`, gives instructions for a
  six-week soft diet. Seed-42 repair changes the diet plan from completed to
  planned. Giving instructions does not establish completing that diet.
- **Useful correction:** `PMC12802722.1`, `b00004`, says ventilation stopped and
  the patient was extubated. Seed-42 repair changes the contemporaneous
  "mechanical ventilation requirement" from present to absent.
- **Representation needs two states:** `PMC13549756.1`, `b00009`, describes six
  months of exclusive breastfeeding, then addition of formula. Repair changes
  a historical breastfeeding fact to absent while retaining the six-month
  duration. Record the earlier present interval and the later transition;
  negating the single historical fact obscures what actually happened.
- **Missing procedure status:** `PMC13294519.1`, `b00004–b00005`, describes
  attempted, unsuccessful endovascular retrieval followed by abortion of
  aspiration. Repair changes completed to cancelled. The procedure schema has
  no attempted/aborted distinction. Preserve initiation, completion and outcome
  separately rather than forcing an attempted procedure into either completed
  or cancelled-before-start.
- **Plan versus execution ambiguity:** `PMC13624887.1`, `b00003`, says a
  conservative approach with regular follow-up was adopted. Repair labels the
  combined plan completed. Adoption is documented; completion of all subsequent
  follow-up is not. Split plan issuance/adoption from execution.

These examples are targeted failure analysis, not a randomly sampled clinical
precision study. The tiny checklist delta (+0/−2) misses many of the 204 changes,
including both useful corrections and new ambiguities.

Next: use context retrieval before a deletion decision, and classify support as
explicit, contextually linked, contradicted, or unresolved. Save the evidence
chain for contextual linkage. Have repair return small attribute-ID operations,
then programmatically apply them to an immutable fact. Review high-risk changes
in negation, owner, dose, laterality and event state with dedicated gold examples.
A disagreement should not automatically erase the original value.

## Timelines: more accepted nodes, worse encounter organization

The occurrence fix does work: inspected rebuilt graphs now link completed
examinations with absent spasticity in `PMC13618778.1`, and pathology with absent
melanin/negative vascular markers in `PMC13624889.1`. Preserve that distinction
between an event occurring and its findings being positive.

But 251/251 valid delta calls do not mean 251 complete or clinically correct
updates. Partial application quarantines additions in 88 calls, and 53/58 final
patient timelines are flagged partial. There are 320 unlinked facts among 3,039
accepted ledger facts. Leading quarantine reasons are event-evidence overlap
failure (101), unresolved event reference (93), wrong/other-subject fact (30),
unresolved exact quote (26), fact-evidence overlap failure (23), invalid addition
(21) and occurrence conflict (19). Some correctly exclude nonpatient or planned
facts; they should not all be counted as model errors.

Rebuilding produces 1,126 event nodes versus 838 in baseline, but reviewed node
matches decline from 78/106 to 72/106, and order matches from 36/82 to 15/82.
Ambiguous gold-node matches increase from 22 to 29. There are zero detected
reversed relations versus one in baseline, but that does not compensate for
losing more than half of the covered order relationships.

An inspected example, `PMC13542380.1`, represents the day-70 surgery from the
abstract as one event, then represents separate operative steps from the body
as other events. A body-sourced fact link to the abstract-sourced event fails
because the quotes do not overlap, even when both discuss the same operation.
Similar tracheostomy mentions recur in the abstract and case narrative. The
pipeline conflates a source mention, an individual action, and an encounter.
Sorting witnesses by article order does not resolve those identities or prove
chronological order.

Next: extract source mentions once, group them into evidence-backed encounters
or episodes, retain separate actions within those encounters, then ask for
relations among the resulting event IDs. Repeated abstract/body mentions need
an explicit coreference decision with both witnesses. Distinct doses/visits must
remain distinct. Fact-to-event association should use such a supported bridge
when literal span overlap cannot hold. Keep a partial order: unknown relations
remain unknown, and ties do not become fabricated dates. Pairwise temporal
checks, transitive consistency and cycle detection can run on CPU.

## Next path and decision gates

1. **Retain** the full baseline, fixed-input paired comparisons, independent
   TP1 GPU jobs, immutable source provenance, protected neighboring facts and
   correct negative-finding handling.
2. **Fix the table compiler first.** Replay all saved table outputs on CPU,
   including explicit missing-value handling, and add an integration regression
   using the actual generated heading citations. Software mocks that always
   returned the requested IDs failed to expose the real interface conflict.
3. **Redesign auditing around small operations and context support.** First
   settle labels for attempted/aborted care, adopted versus executed plans,
   negation and historical-to-current transitions. Validate those on a compact
   source-reviewed challenge set before another large run.
4. **Replace mention-by-mention timeline expansion with encounter grouping and
   relation decisions.** Test this on fixed baseline facts. Require better
   reviewed node/order coverage without more wrong links or collapsed distinct
   treatments. Evaluate encounter identity separately from event-description
   regex matching; duplicate mentions currently affect both delivery and scoring.
5. **Use GEPA after those interfaces and labels agree.** Optimize a compact
   cell/attribute/encounter task with feedback that measures the errors we care
   about. Keep a genuinely unseen article set for final selection. Raising a
   coarse bundle score or schema validity alone would optimize the wrong target.

The larger design shift is to let the model decide **meaning and relations**,
while code carries forward **source identity, exact measurements and validated
edits**. This run provides a concrete small demonstration through the table
replay. It does not yet establish that the full redesigned system will win.
