# Beam pilot: run-20261028-122419

Archive checksum verified locally on 2026-10-29. Evidence: archived summary/comparison.json,
extraction-failures.json, article timing and slurm-accounting.json. No live cluster inspection.
The archive is readable and intact; the campaign is **incomplete**.

| Metric | DFlash2 (7 draft tokens) | No speculation |
|---|---:|---:|
| Completed attempts / planned | 47/48 | 45/48 |
| Predictions | 46 | 44 |
| All extraction tasks valid | 3 | 3 |
| Worker hours (includes startup/failed attempts) | 11.27 | 16.43 |
| Matched clinical precision (45 shared articles) | .7900 | .7861 |
| Matched clinical recall | .7800 | .8202 |
| Matched clinical F1 | .7850 | .8028 |
| Full-cohort clinical F1 (unfinished counted as missing) | .7795 | .6784 |
| Timeline event F1, full cohort | .4390 | .4717 |
| Timeline relation F1, full cohort | .01635 | .01547 |
| Output tokens/s (article-time basis) | 3596 | 2320 |
| Mean input tokens/completed article | 8.205M | 5.795M |
| Mean output tokens/completed article | 2.703M | 2.396M |
| Mean calls/completed article | 382.0 | 325.5 |
| Length-limited calls | 34 | 14 |
| Calls with returned reasoning saved | 19,361 | 18,615 |

DFlash2 matched-article service-time speedup is 1.509x, not an isolated decoding
speedup. Draft acceptance is 17.23%. Shared articles are a selected subset; the
no-spec arm's substantially lower full-cohort F1 is largely confounded by missing
articles. Its matched F1 is higher. Neither establishes a statistically significant
quality winner. Semantic judge scores were not applied; these are annotation
agreement metrics, not physician-adjudicated accuracy.

## What worked

Both serving configurations started, passed context/reasoning probes and generated
outputs. No OOM is reported in accounting. Batch MaxRSS was 32.46/31.64 GiB versus
96 GiB requested. First staging took 56.1 minutes, second 12.1 minutes, consistent
with reuse plus validation/probes. Both cleanup jobs completed. The accounting
snapshot lists aggregation RUNNING because it was captured during aggregation;
it does not describe current scheduler state. Remote disk cleanup needs a live
check; an archive alone cannot prove present-day filesystem state.

## Failures

- Both workers returned FAILED due to article errors, not the 24-hour job limit.
- DFlash2 timed out on PMC7770219.1. No-spec additionally timed out on
  PMC12486612.1 and PMC12780777.1. The old article limit was 3,600 seconds.
- These are respectively a long pulmonary case, a five-case psychiatric report,
  and a long renal case series. Timeouts remove entire predictions from aggregate
  scoring even when intermediate calls exist.
- DFlash2 recorded 1,198 `total_token_budget_exhausted` leaf failures, concentrated
  in the renal series. Exhaustive small relation batches repeatedly send the
  article and consume the shared token budget. Increasing concurrency alone
  cannot solve this.
- 339/331 leaf failures are `targeted_items_unresolved`. Nonliteral timeline
  citations, unsupported offsets and contradictory temporal orders remain.
  Do not relax validation or reinterpret these as successes.
- Both arms failed roster discovery for PMC12779367.1 with context overflow.
  The primary source has 471,375 characters / 753 segments; 720 are genomic
  appendix segments. This is a structured-data routing problem, not an excuse
  to truncate source or drop a difficult article from the denominator.
- Summary repair sometimes refuses field erasure. Preserve this protection;
  unsupported claims need explicit quarantine/adjudication, not silent deletion.

## Implemented next experiment

`run_dense_beam.sh repair-pilot` uses DFlash2 alone, two independent TP4 replicas
on eight GPUs, the same deterministic 48-article training selection, 24 hours per
worker. It introduces whole evidence/identity/adjacent-segment timeline windows
as the first choice, event batches of four segments and relation batches of four
origins by sixteen targets. Every unordered event pair remains eligible in
exactly one top-level batch. Context/length splitting remains active. Windows
can miss distant relation cues: coverage metadata is retained and semantic
coverage is not asserted. Prompts explicitly require sparse supported edges and
null offsets when no literal duration is quoted. Beam's per-article timeout is
four hours, frozen in the arm manifest; historical manifests retain one hour.

This is a diagnostic candidate, not a validated final configuration. Compare the
same article IDs against this archive: token/call counts, timeout rate, matched
clinical F1, event/relation F1, citation failures, and valid documents/GPU-hour.
The genomic-appendix roster case remains unresolved and must remain visible.

Before a final full run:
1. Resolve genomic appendix routing with explicit retained source/sidecar coverage
   and patient identity reconciliation. Test the neonatal case independently.
2. Adjudicate a small training sample of timeline edges, negations, family history
   and complex multi-patient attribution. Separate extraction defects from fuzzy
   scoring mismatches. Evaluate semantic judging against that sample.
3. Complete all planned articles or clearly report unresolved failures. Check
   token usage from interrupted attempts separately before any API cost claim.
4. Then run the selected configuration on train+validation using `long`; keep
   test held out until configuration/prompt choices are frozen. Do not promote
   source-window changes based on speed alone.

At the previously discussed assumed rates ($0.40/M input, $1.20/M output), completed
attempts correspond to $306.69 DFlash2 and $233.71 no-spec, $540.40 total. This is
not an actual bill, excludes interrupted-attempt usage and new terminology calls,
and assumes no cache discount. DFlash2's larger completed workload matters.
FHIR export was not exercised by this campaign and has no new validation result.
