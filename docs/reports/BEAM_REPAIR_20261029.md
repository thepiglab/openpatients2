# Beam repair pilot — run-20261029-085947

Analyzed 2026-10-29 from the downloaded results.tar.gz. SHA-256 verified:
`44b27ea6108f861247949da8714b70ec0e57c665b4e4e489f8816a34bbf8e69e`.
Evidence: comparison.json, per-arm report/scores/timing/failure summaries, original
call ledgers, sampled relation-task records, worker receipts, Slurm accounting and
cleanup receipt. This analysis does not inspect the live cluster.

## Outcome

48/48 attempts finished, 47 prediction artifacts, one roster context failure.
Two TP4 Beam NVFP4 + DFlash2 replicas ran on eight B200 GPUs. Both GPU jobs and
cleanup completed. Observed worker span was 5.81 hours (excluding initial download
and queue), 44.18 observed GPU-hours, approximately 8.27 finished attempts/hour.
CPU download took 41.2 minutes; worker startup averaged 25.4 minutes. Batch MaxRSS
was 32.66/32.63 GiB against 96 GiB requested per worker; this is not a total-cgroup
RAM measurement. No OOM or worker interruption is reported.

Cleanup receipt records removal of target/draft weights, container, download cache
and runtime caches. Accounting captured aggregation while it was RUNNING, so that
snapshot is not its final state. An aggregate-error.json says packed jobs were
still active; a later complete summary and readable checksummed export exist.
Treat that as an earlier aggregation refusal, not evidence that inference failed;
aggregation status/error timestamps should be made clearer.

## Comparison with October 28 DFlash2 arm

| Metric | Previous pilot | Repair pilot |
|---|---:|---:|
| Finished attempts | 47/48 | 48/48 |
| Prediction artifacts | 46 | 47 |
| Reported fully valid articles | 3 | 3 |
| Full-cohort clinical precision | 80.57% | 80.20% |
| Full-cohort clinical recall | 75.50% | 78.51% |
| Full-cohort clinical F1 | 77.95% | 79.34% |
| Attribute accuracy | 84.66% | 83.85% |
| Timeline event F1 | 43.90% | 41.47% |
| Timeline relation F1 | 1.63% | 2.32% |
| Mean input tokens/finished attempt | 8.205M | 3.120M |
| Mean output tokens/finished attempt | 2.703M | 2.228M |
| Mean model calls/finished attempt | 382.0 | 228.3 |
| Length-limited calls | 34 | 340 |
| Draft-token acceptance | 17.23% | 16.41% |

Full-cohort comparisons include different completion coverage. Across the 47
shared completed IDs, input tokens fell 64.67%, output tokens 29.57%, calls 43.17%,
and summed article service time 7.94%. Reported clinical F1 on those IDs was
79.81% before and 80.12% now. This is a descriptive comparison of changed pipeline
configurations, not a controlled significance test. The reference snapshot hash
matches across runs, but matching-derived counts should be audited before treating
small score changes as meaningful. These are fuzzy/evidence annotation agreement
scores, not semantic-judge or physician-adjudicated accuracy. Test was held out.
The much shorter wall time also reflects two replicas versus one previously.

## Important validity caveat

All three fully valid articles have zero extracted patients. Two have no reference
patients; PMC13575834.1 is a cohort paper where Beam classified a described singleton
complication as aggregate-only. The reference treats that complication and its
endoclip treatment as an individual patient. Source b00021 explicitly describes
one small perforation immediately managed with endoclips. Resolve and document the
individual-case eligibility policy; do not conflate an empty schema-valid output
with successful patient extraction. No nonempty patient extraction passed every
pipeline task in this run. Prediction artifact count also includes empty rosters.

## Failure mechanisms

- 61 failed and 857 partial task records, plus five article-level EHR errors.
  Task counts include parent/assembled reports and are not independent failures.
- Timeline relations dominate: 708 relation task failures/partials; 641 have an
  out-of-candidate-batch error somewhere in repair history across 44 articles
  (576 have it as their initial error). 684 non-parent records end with
  targeted_items_unresolved. Sampled repairs omit out-of-batch edges but the
  TimelineAtomRepair pending set retains them; retries cannot resolve that by
  omission. Preserve the original candidate and evidence, route/quarantine it
  explicitly, and validate in-scope edges independently. Do not change endpoints
  to manufacture a relation or silently declare lost evidence successful.
- Relation generation consumed 78.394M output tokens (73.31%); event generation
  consumed 13.164M (12.31%). Together timelines consume 85.62% of output.
- 340 length-limited responses versus 34 previously. Larger relation batches can
  produce enormous edge enumerations even though the prompt requests sparse edges.
  Use compact pair-ID output contracts and bounded retries/splitting; do not just
  raise every output limit or request exhaustive unknown edges.
- PMC12779367.1 still has roster context_overflow_no_source_truncation and no model
  calls. It is the known genomic-appendix case. Route tables/appendices separately
  with explicit coverage and patient identity reconciliation; retain all source.
- EHR measurement-series errors: context overflow for PMC12780777.1 and
  PMC13476113.1, nonliteral time cell for PMC12834360.1, length limit for
  PMC7770219.1, and missing roster for PMC12779367.1.
- Previously timed-out PMC7770219.1 now finishes in 67.95 minutes, but generates
  17.47M output tokens and still has an EHR failure. PMC12780777.1 takes 65.19
  minutes / 16.72M output tokens; PMC12486612.1 takes 34.76 minutes.
  No total_token_budget_exhausted failures remain in the failure summary.
- Citation literalness, ambiguous quotations, contradictory temporal orders and
  summary repair field-erasure guards remain legitimate unresolved gates.

## Reasoning, throughput and scope

All 10,960 returned calls have saved text reasoning. All 55,377 indexed extracted
objects have task-level call/reasoning links; these are not necessarily unique
correct clinical facts. Total usage: 149,766,359 input; 106,935,318 output;
80,780,257 reasoning tokens (already included in output). Mean output is 2.228M
per finished attempt, of which 1.683M is reasoning. About 2,922 output tokens/s is
summed-article throughput for a four-GPU server with concurrent task requests,
not single-stream decoding speed or aggregate two-replica throughput. Acceptance
alone does not establish speculation speedup; this run has no speculation-off arm.

At the previously discussed illustrative $0.40/M input and $1.20/M output rates,
this usage would be $188.23 without cache discounts, approximately $3.92/planned
article. These are hypothetical rates, not verified current pricing or a bill.

Beam is text-only here. Captions are used; figure pixels are not. The EHR stage in
this run is measurement-series extraction, not a validated end-to-end FHIR export.
Do not use all saved reasoning directly as distillation targets; filter/review
source support, patient attribution and extraction validity first.

## Recommended next experiment

1. Fix relation batch routing and repair termination using offline replay of the
   archived failures. Keep rejects auditable and distinguish invalid evidence from
   a valid relation emitted in the wrong batch. Add regression cases for empty
   repairs, repeated rejects, contradictory edges and endpoint identity changes.
2. Split reporting into finished attempts, prediction artifacts, nonempty patient
   records, schema/evidence-valid records and semantic/reference agreement. Report
   empty-roster eligibility separately. Preserve original benchmark denominators.
3. Add lossless document/table routing for the genomic roster and long measurement
   tasks. Fix singleton-case discovery policy using PMC13575834.1 and empty controls.
4. Run a small diagnostic training slice with these difficult IDs plus representative
   successes and empty controls. Adjudicate source-supported events/relations before
   judging quality changes; do not choose a next configuration on throughput alone.
5. Once correctness improves, compare seven-token DFlash2, a shorter draft and no
   speculation on the identical revised workload and article set, matching quality
   and output lengths. Keep two TP4 replicas for throughput, not TP8 by default.
6. Re-run the fixed 48-article pilot, then the longer train/validation campaign.
   Keep test held out. Only reviewed facts should enter terminology/FHIR export or
   reasoning distillation. No new cluster job or extraction-code change was made
   during this analysis.

## Subsequent implementation: repair v2

The follow-up code change implements scope-routing audits before timeline atom
repair, a relation-only prompt, smaller relation batches, immediate splitting on
length failures, bounded whole-segment roster discovery with explicit identity
reconciliation, and bounded table-row measurement extraction. Failed leaves and
unknown identities stay visible; no evidence/attribution validator was relaxed.
Reports separate nonempty pipeline-valid documents from empty/schema-valid results
and add task-level usage counters. See `docs/BEAM_FHIR.md` for the diagnostic and
promotion checks. Historical archives remain unchanged.

Offline replay of four saved relation task records (ten parsed candidate responses)
kept 99 candidate edges byte-for-byte and separately audited 137 out-of-batch edges.
This checks routing/preservation, not semantic validity or live-model speedup.
Mock regression tests cover invalid citations, endpoint identity, unresolved roster
merges, row/header coverage, length splitting, original trace retention, empty-output
reporting and unknown token usage.

A real preparation/dry submission selected all twelve diagnostic articles from the
training snapshot, froze both new modules, and requested two TP4 workers: eight
B200s, 32 CPUs and 192 GiB host RAM, twelve hours per worker. No GPU job was
submitted locally. The code must be committed/pushed and pulled on HiPerGator;
the Slurm bootstrap archives the commit, not uncommitted working-tree files.
