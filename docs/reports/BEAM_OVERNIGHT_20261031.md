# Beam comparison diagnosis and dual overnight plan

Input: downloaded `run-20261030-162625/results.tar.gz`, checksum verified.
Neither worker crashed or ran out of GPU memory. DFlash finished 5/8 articles;
no-spec finished 3/8. Both exited with budget_exhausted, after respectively
7,118 and 7,622 worker seconds (about two hours), despite six-hour allocations.
The scheduler refused another article unless the entire four-hour article limit
remained. Startup itself took approximately 26.6 minutes in both workers.

The target-sharing fix worked: no-spec's CPU receipt records the reused Beam
target and staging took 354 seconds, versus 2,492 seconds for the initial download.

| Metric | DFlash2 | No speculation |
|---|---:|---:|
| Finished attempts | 5/8 | 3/8 |
| Matched articles | 3 | 3 |
| Matched item precision | 80.13% | 80.18% |
| Matched item recall | 89.30% | 90.32% |
| Matched item F1 | 84.47% | 84.95% |
| Mean input tokens/finished article | 4.434M | 5.673M |
| Mean output tokens/finished article | 4.261M | 4.856M |
| Output tokens/s, summed-article time | 3,893.5 | 2,437.2 |
| Fully valid extraction articles | 0 | 0 |

Matched pipeline service-time speedup is 2.083x for DFlash; acceptance is 21.42%.
Three matched articles and stochastic outputs are insufficient to establish a
general hardware speedup or accuracy equivalence. Unmatched means above have
different article coverage. These scores are annotation agreement, not clinical
validation. Full-cohort scores penalize the unattempted articles and are not a
fair standalone ranking. The pilot used the older repair pipeline rather than
the competition pipeline's bounded clinical extraction and sparse relations.

## Changes

- All Super workers now cap each article to the remaining time, retaining a
  shutdown margin, instead of reserving the full worst-case article timeout.
- Worker receipts record configured budgets and the reason/remaining seconds
  when stopping. FHIR-only retry remains restricted to competition manifests.
- `run_dense_beam.sh overnight` runs the curated 50 training articles through
  matched DFlash2/no-spec TP4 arms, bounded clinical extraction, sparse timelines,
  ICD-10-CM coding and deterministic FHIR export. It uses the existing audited
  competition configuration, including literal table time cells. Test stays held out.
- `run_dense_le_chonk.sh overnight` selects 24 diverse training articles, text
  plus images, TP8 native Mistral/DSpark, high reasoning, with 12-hour limit.
  It retains tokenization/image/streaming-reasoning startup gates. Mistral still
  has no successful hardware inference run; a longer allocation is not proof of fit.
- A dual launcher checks committed budget fixes in both checkouts and at least
  1,296 GiB free on the shared filesystem before submitting either campaign.
  This is Beam 418 + Mistral 878, including separate scratch/results reserves.
  It is a point-in-time check, not a reservation against other filesystem users.

Both use 12-hour GPU worker limits, not a guaranteed 12-hour end-to-end wall time.
CPU staging, GPU queue wait and aggregation are additional. Workers may finish
early if all work completes. The slower Beam arm and untested Mistral may leave
pending articles; retain their receipts/checkpoints rather than calling that a
successful full campaign. Do not run old frozen campaigns to pick up these fixes.

## Launch

Commit and push the changes, then update BOTH cluster checkouts. Source the main
checkout's .env in a subshell and call:

```bash
bash scripts/run_dense_dual_overnight.sh /blue/cai6734/ehr_agent/openpatients2-le-chonk
```

Beam uses cai5724: 2 x 4 B200, 32 CPUs, 192 GiB host RAM total. Mistral uses
cai6734: 8 B200, 16 CPUs, 200 GiB host RAM. These are separate allocations but
share disk. Existing active-campaign guards are retained independently in each
checkout. If the second submission fails, Beam may already be submitted; inspect
the printed IDs and queue before retrying. The dual launcher does not cancel jobs.

If the combined storage check fails, use sequential individual overnight launches
after cleanup, or inspect/delete only explicitly chosen old artifacts. Do not lower
the check merely to force both campaigns onto the same full filesystem.

Next review: matched quality/throughput, article/task failure inventories, remaining
budget receipts, real speculative acceptance, input/output/reasoning tokens, retained
reasoning, table coverage, timeline consistency, FHIR quarantine and coding abstention.
FHIR structural success and experimental ICD codes still require clinical review.
