# October 30 Beam competition and Mistral smoke diagnostics

Analyzed the user-downloaded archives for `run-20261030-015355` (Beam) and
`run-20261030-131831` (Mistral). Both archive SHA-256 checksums passed.
Evidence is archived status, accounting, worker/download logs, extraction failure
reports and saved FHIR packets. No live cluster inspection or submission occurred.

## Results

| Configuration | Execution | Interpretation |
|---|---|---|
| Beam NVFP4 + DFlash2 | 50/50 extraction attempts; 50 nonempty patient articles; 49/50 coding/FHIR stages complete | Useful outputs, but zero articles pass every extraction/EHR validation gate |
| Beam NVFP4 without speculation | 0/50; CPU download failed; GPU job cancelled | Disk quota exhausted while downloading a redundant target copy; not a model failure |
| Mistral Large 4 NVFP4 + DSpark | 0/2; storage preflight failed | Needed 878 GiB free, found 721 GiB; neither download nor GPU worker started |

Beam DFlash GPU job 48273893 completed in 38,906 seconds (10h48m26s), including
about 27m47s server startup, on four B200s. Its CPU download took 44m11s.
The no-spec download job 48273892 failed after 379 seconds with disk quota
exceeded, and dependent GPU job 48273894 was cancelled. Both Beam cleanup jobs
completed and their receipts list removal of model weights, images and caches.

Mistral's cleanup then incorrectly reported runtime ownership mismatch: the
storage check had prevented setup from creating any runtime. This is distinct
from the earlier Mistral config.json/params.json startup failure, and provides
no new evidence of model quality or GPU fit. Aggregation accounting snapshots
may show their own jobs RUNNING; those snapshots are not live queue state.

## Beam quality and cost

| Metric | DFlash2 arm |
|---|---:|
| Clinical item precision | 72.05% |
| Clinical item recall | 82.50% |
| Clinical item F1 | 76.92% |
| Attribute accuracy | 88.00% |
| Mean input tokens/article | 1,623,762 |
| Mean output tokens/article | 1,601,833 |
| Mean reasoning tokens/article, included in output | 1,296,465 |
| Mean model calls/article | 140.8 |
| Mean article service time | 12.38 minutes |
| Output tokens/s, summed-article denominator | 2,155.6 |
| Attempts/hour, summed-article denominator | 4.84 |
| Draft token acceptance | 12.62% |
| Calls with saved text reasoning | 7,039 / 7,039 |

These are training-cohort reference-agreement scores, not independent clinical
validation. Semantic judging was not applied. The no-spec arm has no results,
so there is no measured speculation speedup. Article throughput includes concurrent
task requests and excludes server startup; it is not single-stream decoding speed.

109 failed and 131 partial task records include dependent/assembled tasks, so
they are not independent failures. Unresolved cell coverage appears in 48 records
across 39 articles. Assembled timeline validation failures appear in 48 records
across 41 articles, commonly contradictory order and inconsistent offsets. Timeline
relations consumed 49.24M of roughly 80.09M output tokens (about 61%).

Six EHR series extraction reports have incomplete row coverage: PMC10751923.1,
PMC12689009.1, PMC12834080.1, PMC12846762.1, PMC12875553.1 and PMC6029867.1.
These checks remain active; marking outputs valid without resolving evidence
would disguise the remaining quality problems.

## Corrections implemented

1. **Share weights after metadata setup.** Setup creates a metadata-only model
   directory. The old reuse function skipped any existing target directory, so
   the second Beam profile downloaded its target again despite a valid donor.
   Reuse now stages verified donor hard links, replaces the owned metadata-only
   directory, excludes mutable caches and reports reused model IDs in the download
   receipt. Existing weight-bearing directories retain the original resume path.
   Tests exercise both absent and metadata-populated targets, and confirm donor
   cleanup does not remove the recipient's weights.
2. **Handle pre-setup cleanup.** If no runtime exists, cleanup records an empty
   deletion receipt. Existing runtimes without valid ownership still fail closed;
   unknown profiles are rejected. The storage threshold remains unchanged.
3. **Do not emit empty FHIR reference-range strings.** Two lactic-acid observations
   in PMC7769828.1 had genuine empty source range cells. Emitting
   `referenceRange: [{"text": ""}]` quarantined the entire patient bundle. Empty
   optional ranges are now omitted while their original cells remain in source
   provenance. Nonempty ranges are retained literally.

Local validation: 73 serving/lifecycle/packing tests and 28 FHIR/competition tests
passed. Two affected lifecycle tests were rerun successfully after adding profile
validation and reuse receipt metadata. `git diff --check` passed.

## Offline FHIR replay

Recompiled the archived PMC7769828.1 packet with the corrected compiler and its
saved decisions, without model calls. Both uncoded and experimental-coded bundles
now pass packaged R4 JSON Schema and local structural/reference checks. All 429
Observations and all 18 attached experimental codes remain. The original archive
and historical receipts were not rewritten.

Derived outputs are in `runs/fhir-replay-20261030-PMC7769828/`, with separate
`uncoded` and `coded` reports and bundles. This is an offline compiler repair, not
clinical approval. The official HL7 validator has not been run. Original exports
contained 349 attached experimental codes overall; the much larger abstention
count includes tasks outside the condition-focused ICD-10-CM vocabulary, not just
failed condition coding.

## Next steps

- Commit/sync the fixes before launching a fresh campaign; old campaigns retain
  frozen code. Do not repeat all 50 DFlash articles solely to repair FHIR.
- Check current free space after Beam cleanup. Mistral still requires 878 GiB
  available. It was short by about 157 GiB at its check. Separate accounts and
  checkouts do not reserve separate filesystem capacity. Wait for cleanup or
  remove only reviewed obsolete artifacts before another two-article smoke.
- First verify a small matched Beam speculation-on/off run with shared target
  weights, including the CPU download receipt's reused_models field. Then recover
  the missing baseline; do not claim DFlash is faster from acceptance alone.
- Prioritize offline table-cell and temporal-constraint diagnostics on the saved
  failures, followed by a small diverse regression cohort. Separate contradictory
  evidence, model errors and source-coverage gaps before changing prompts or
  validators. Keep the test split held out and review source attribution/coding
  before releasing the competition dataset.
