# Beam repair-v2 diagnostic — run-20261029-174936

Analyzed 2026-10-30 from the local archive. SHA-256 verified:
`f7006fa6ca764bd61e55ad2a769659a8771249454e937271dd6a1682141b873c`.
The archive, original sources, call/task records, coverage receipts, scores,
worker telemetry and accounting were inspected. This is not a live-cluster check.

## Decision

Do not promote repair v2 to a long campaign yet. Keep the successful discovery,
measurement batching, scope-routing and reasoning capture work. Redesign timeline
generation and add clinical completeness/attribution checks before another large
run. This is an orchestration and evaluation problem as well as a model problem;
the evidence does not support abandoning Beam or training on all its traces.

All 12 training articles finished and produced prediction artifacts. Eleven have
patients; only one nonempty article passed every pipeline check. That article,
PMC13575834.1, still has only 24.24% clinical item F1 against the reference and
misses the reference's second, figure-caption-derived patient. Validity is not
completeness or clinical correctness.

## Comparable results

The previous column is recomputed on these same twelve IDs from
run-20261029-085947, not its complete 48-article cohort. Both campaigns use the
same reference snapshot SHA. Scores were recomputed with the diagnostic's frozen
scorer. The genomic article's previous missing prediction remains a full miss.
These are one-seed descriptive results, not a significance test.

| Metric | Previous on these 12 | Repair v2 on these 12 |
|---|---:|---:|
| Completed attempts | 12 | 12 |
| Prediction artifacts | 11 | 12 |
| Nonempty pipeline-valid articles | 0 | 1 |
| Clinical item precision | 85.73% | 82.41% |
| Clinical item recall | 76.49% | 75.36% |
| Clinical item F1 | 80.85% | 78.73% |
| Matched attribute accuracy | 83.92% | 84.05% |
| Timeline event F1 | 28.70% | 27.28% |
| Timeline relation F1 | 1.49% | 1.01% |
| Measurement-series point F1 | 0.00% | 82.32% |
| Articles with EHR-stage errors | 5 | 2 |
| Length-limited returned calls | 226 | 37 |
| Returned model calls | 4,653 | 9,752 |
| Input tokens | 75.21M | 113.10M |
| Output tokens, including reasoning | 49.25M | 88.10M |
| Reasoning tokens (subset of output) | 34.23M | 70.88M |
| Summed article service time | 3.76 h | 6.48 h |

Output grew 78.88%, calls 109.59%, and summed article time 72.53%; length-limited
calls fell 83.63%. Even excluding the newly recovered genomic article, the other
eleven took 69.47% longer. The diagnostic intentionally overrepresents hard cases;
its throughput is not a full-dataset estimate.

The references are AI annotations, not physician gold. No semantic judge was run.
Fuzzy/evidence matching does not make every unmatched event a hallucination:
event granularity, redundant/transitive edges and imperfect references affect the
scores. Review-only reference handling also makes clinical TP+FN slightly
alignment-dependent. Preserve original scores while adjudicating disagreements.

## What worked

- Both TP4 GPU workers completed normally. The observed worker span was 3.76 h;
  Slurm GPU allocations total 29.62 GPU-hours, including startup. CPU download
  took 56.4 minutes, and mean server startup was 26.7 minutes. No OOM event was
  reported. Cleanup records removal of weights, drafter, container and caches.
- Scope routing audited 269 out-of-batch candidates. The twelve articles'
  prior failure summary contained 217 relation-task records with candidate-batch
  errors; the new summary contains none. Relation leaf failures/partials fell
  from 244 to 37. Counts are task records, not independent clinical mistakes.
- Measurement batching removed the previous overflow/length failures in
  PMC12780777.1, PMC13476113.1 and PMC7770219.1. Their coverage receipts have no
  failed leaves. Series scoring now finds 284 matched points, 53 unmatched
  predictions and 69 missing reference points. This is a meaningful improvement,
  not complete semantic validation.
- The genomic article, PMC12779367.1, now passes five discovery windows and
  identity reconciliation. All source segments were attempted and a neonate was
  identified. Its clinical extraction still fails, as detailed below.
- The singleton perforation/endoclip patient in PMC13575834.1 is now discovered.
- All 9,752 returned calls have saved text reasoning; all 16,942 indexed objects
  have call/reasoning links. These objects are not necessarily unique correct
  facts and have not been filtered for training.

## Remaining failure mechanisms

### 1. Dense relation generation is the main cost and quality problem

Relation generation used 76.08M output tokens: 86.35% of the entire run. Events
plus relations used 93.30%. Of relation output, 73.13M tokens came from initial
calls and 2.95M from repair calls. Fixing retries alone cannot remove most cost.
2,004 relation responses had an empty parsed edge list yet consumed 11.69M output
tokens. The code still asks about every unordered event pair.

Accepted relation-batch payloads contained 44,846 edges. Global assembly
quarantined 34,641 (77.24%) for combined-graph conflicts, across 33 patients in
ten articles. Only 10,205 remained. These conflicts can arise when individually
valid batches imply a cycle or incompatible offsets when joined. The safeguard
is doing its job; weakening it would publish inconsistent timelines.

The final output has 2,440 events versus 397 reference events and 10,205 relations
versus 287 reference relations. Some excess is legitimate finer granularity or
redundancy; it still multiplies all-pairs requests and makes endpoint matching
fragile. Event recall is 97.48% but precision against the reference is 15.86%.

PMC7770219.1 and PMC12780777.1 alone consume 66.62% of all output tokens. Their
article times rise from 67.95 to 136.31 minutes and 65.19 to 102.36 minutes.

### 2. Clinical completeness can fail silently

PMC7770219.1's clinical F1 falls from 57.61% to 38.59%. Matched observation items
fall from 71 to 12, with 249 reference observations missed. The new observations
call returned only the first two analytes, hemoglobin and WBC (twelve values),
marked coverage complete, ended with finish_reason=stop, and passed its checker.
It used 27,808 output tokens, including 24,262 reasoning tokens. This was not a
length-limited or transport failure. A larger output limit does not enforce recall.

Clinical observations and EHR measurement-series points are separate outputs.
The latter's improvement does not automatically repair missing clinical facts.
Build a source-cell/section coverage inventory and reconcile these outputs with
patient and time provenance; do not blindly concatenate or deduplicate by name.

### 3. The genomic long-context fix stops after discovery

PMC12779367.1 has 753 source segments, including 716 table rows. The reconciled
patient owns 411 segments and 47 more remain unresolved. Patient routing still
produces oversized prompts: all fourteen clinical section tasks plus summary
fail with context_overflow_no_source_truncation. Its clinical F1 remains zero.
Its roster is structurally valid but roster_complete=false. A prediction file
now exists, but this is not a recovered clinical record.

Route genomic annotation tables separately from patient observations, retaining
their original source and explicit eligibility decisions. Bounded extraction
must also apply to clinical tasks, with coverage/merge checks across windows.
Never solve this by silently dropping appendix content or filling empty sections.

### 4. Two series batches still invent or miscopy time text

PMC12834360.1 still fails because time_text is not literal in its selected time
cell. PMC12373494.1 introduces the same error in row batch 2; three other batches
are retained. All focus cells were attempted, but the failed batches are not
validated output. Have the model select existing cell IDs; let code copy literal
time/value/unit text and validate the selected ownership and column associations.

### 5. Patient eligibility and cohort attribution need adjudication

PMC12373261.1 was an empty reference/control. Beam now extracts two individuals
distinguished in source b00020 by a 12-degree extension deficit and a 15-degree
flexion deficit. Those statements really exist: the discrepancy is not simply
invented patients. The outputs then expand into many shared cohort facts and
even attach the cohort mean follow-up of 8.8 months to individual findings.

PMC13575834.1 now extracts the perforation patient but misses the reference's
separate Figure 2 patient. It also imports procedure-protocol medications. Some
universal cohort statements may logically apply, but they need explicit derived
provenance; generic protocols and group statistics are not individual measurements.
Distinguish direct patient evidence, universal-cohort inference, aggregate values,
study protocol, background, and cited secondary cases. Review these boundary cases
before changing references or training on labels. Never change a reference solely
to improve this run's score.

### 6. Operational reporting has a self-verification defect

The archive contains aggregate-error.json reporting that packed jobs are active.
In the frozen code, aggregate() permits its own running aggregate job, but then
calls verify(), whose unfiltered active_jobs() includes that same job. Thus a
complete summary/export can coexist with this aggregation error. The later manual
verification and checksum passed. Fix internal verification to exclude only the
known current aggregate job, while still rejecting other active writers.

Do not reduce host RAM based on batch MaxRSS alone (~32.3 GiB). Cgroup peaks reach
~95.97 GiB of the requested 96 GiB per worker, including file cache, with no OOM.
Keep the two TP4 / 96-GiB-worker allocations while testing the pipeline redesign.

## Next experiment, in priority order

1. Replace exhaustive pair comparisons with a sparse, source-backed timeline:
   canonical event/episode anchors, directly supported temporal cues, and a small
   set of local links. Attach measurements to events instead of independently
   relating every value to every other event. Keep all original fact provenance.
   Label any code-derived transitive order separately from directly cited edges.
   Reconcile minimal conflicting sets; preserve unresolved candidates in audit.
   Measure missed-link recall against the old full-pair baseline before promotion.
2. Make extraction coverage explicit. Route clinical source windows/table rows
   by patient, task and evidence type; require every eligible unit to be extracted,
   excluded with a reason, or unresolved. Add source-cell IDs to time/value fields
   and deterministic literal projection. A model's coverage=complete is insufficient.
3. Audit patient attribution and benchmark semantics. Review the two ambiguous
   cohort/caption examples and sample accepted plus quarantined timeline edges.
   Calibrate a blinded, source-grounded judge against reviewed examples. Existing
   semantic_benchmark supports clinical fact agreement; it does not automatically
   evaluate timelines, rosters or EHR series and needs those extensions separately.
4. Cache immutable validated clinical outputs and rerun only revised stages with
   source/config hashes and dependency invalidation. Start on the two costly cases,
   genomic failure and two time-cell failures, then repeat these twelve training
   cases. Compare core recall, series coverage, graph conflicts, useful records
   and output/GPU cost together; do not promote cheap empty output.
5. Only after that compare DFlash2 seven-token, three-token and speculation-off
   profiles on identical revised inputs. This run's 15.05% acceptance is not a
   speedup measurement. Keep reasoning capture; separately test lower reasoning
   effort for mechanical ID selection after the workload is stable. Do not change
   pipeline, reasoning and speculation simultaneously.
6. Repeat the 48-article training pilot, then a frozen validation campaign. Keep
   test held out. Defer fine-tuning/distillation and FHIR release until accepted
   facts, patient ownership and reasoning targets have passed source review.

No GPU job was submitted and no extraction/serving defaults were changed for this
analysis. The next changes should be evaluated as a new pipeline experiment.

## Per-article comparison

Patient counts are reference / previous / new. F1 is clinical item agreement,
not series or timeline scoring; a dash means no scored items rather than 0%.

| Article | Title | Patients ref/old/new | Minutes old → new | Output M tokens old → new | Clinical F1 % old → new |
|---|---|---:|---:|---:|---:|
| PMC12283752.1 | Three case reports of pulmonary mucormycosis with a review of the literature | 3/3/3 | 12.0 → 24.2 | 1.95 → 3.89 | 83.4 → 80.3 |
| PMC12373261.1 | Surgical Management of Pediatric Capitellar Fractures: A Case Series of Five Patients | 0/0/2 | 0.2 → 11.5 | 0.01 → 2.02 | — → 0.0 |
| PMC12373494.1 | Bilateral Retinal Vasculitis in Systemic Lupus Erythematosus: A Case Series of Three Patients | 3/3/3 | 14.7 → 24.7 | 3.95 → 6.46 | 91.1 → 86.6 |
| PMC12470713.1 | Immune Checkpoint Inhibitor-Induced Diabetes Mellitus—A Brief Review and Three Case Reports | 3/3/3 | 9.9 → 7.0 | 0.91 → 1.02 | 86.6 → 86.4 |
| PMC12486612.1 | Successful treatment of five cases of catatonia treated with guanfacine without ECT: a case series from a psychiatric hospital in Japan | 6/6/6 | 34.8 → 48.4 | 4.78 → 10.03 | 84.8 → 84.0 |
| PMC12779367.1 | Congenital Anomalies in a Neonate With Partial Monosomy of Chromosome 21 q Arm: A Case Report | 1/0/1 | 0.0 → 6.9 | 0.00 → 1.08 | 0.0 → 0.0 |
| PMC12780777.1 | Biopsy-proven tyrosine kinase inhibitor–associated renal injury: a case series | 21/21/21 | 65.2 → 102.4 | 16.72 → 24.39 | 82.6 → 85.2 |
| PMC12834360.1 | Multisystem Complications in Postpartum-Onset Evans Syndrome: A Case Report | 1/1/1 | 11.4 → 14.9 | 1.43 → 2.44 | 82.7 → 85.0 |
| PMC12835657.1 | Violence Against Physicians by Patients With Schizophrenia: A Forensic Psychiatric Case Series | 0/0/0 | 0.1 → 0.0 | 0.00 → 0.00 | — → — |
| PMC13476113.1 | Radiological and Surgical Findings of Patients with Intradiploic Mass Lesion: Case Series and Systematic Review | 9/9/9 | 8.9 → 9.4 | 2.02 → 2.29 | 83.5 → 83.6 |
| PMC13575834.1 | Clinical Outcomes of Traction-Assisted Endoscopic Submucosal Dissection for Prepyloric and Pyloric Neoplasms: Propensity Score Matching Analysis | 2/0/1 | 0.2 → 3.1 | 0.01 → 0.16 | 0.0 → 24.2 |
| PMC7770219.1 | Iacta Alea Est: The Inexorable Advance of Tofacitinib in the Treatment of Dermatomyositis-Associated Rapidly Progressive Interstitial Lung Disease. A Case Report | 1/1/1 | 67.9 → 136.3 | 17.47 → 34.31 | 57.6 → 38.6 |
