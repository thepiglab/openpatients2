# Recall-weighted GEPA on Glimmer

This campaign learns four task-specific prompt supplements: observations,
procedures/devices, conditions, medications. It retains local Glimmer FP8 +
DFlash, medium reasoning, the existing immutable task prompts, literal-evidence
validators and bounded repairs. **No constrained decoding**, including reflection.
No annotation edits, model finetuning, external API calls or automatic deployment.

## Research and decisions

Primary references consulted October 12, 2026:

- [GEPA paper](https://arxiv.org/abs/2507.19457): reflective text evolution uses
  execution feedback and Pareto selection to search reusable instructions.
- [Official GEPA FAQ](https://gepa-ai.github.io/gepa/guides/faq/): use small
  reflection batches (typically 2–5), a representative selection set and a budget
  large enough for multiple candidates. Optimize with the deployment model;
  stronger reflection models are recommended. Component-level optimization can
  save expensive whole-program rollouts. Here we use batch 3, 1,500 article-task
  evaluations per optimizer, and the same local Glimmer for reflection. This
  keeps the run self-contained but may limit proposal quality versus a frontier
  reflector. Metric-call limits are not exact model-call limits: one article can
  contain many patients and each extraction can invoke bounded repairs.
- [Adapter guide](https://gepa-ai.github.io/gepa/guides/adapters/): useful feedback
  should explain failures, not just report a scalar. Our feedback gives missed
  reference items, unmatched predictions attribute mismatches and validator errors. Diagnostic examples are bounded;
  extraction source and metric denominators are not truncated. Clinical scores
  come from deterministic annotation matching, not the model judging itself.
- [F-beta definition](https://scikit-learn.org/stable/modules/generated/sklearn.metrics.fbeta_score.html):
  `Fβ = (1+β²)TP / ((1+β²)TP + β² FN + FP)`.
  β=2 gives FN a coefficient of four versus FP. β=1 recovers F1. This does not
  imply that any loss in precision is acceptable.

The implementation uses the repository's pinned **gepa==0.1.4**, verified against
its installed adapter and optimize API. No dependency upgrade is required.

## Objective and evaluation

The defaults below are engineering choices for this dataset, not universal GEPA
rules. Search uses article-macro, task-specific F2. Per-article reward is F2 times
`min(1, precision/0.80)` times `(0.8 + 0.2 * matched_attribute_accuracy)` times
the fraction of patient outputs that pass validation. Empty reference plus empty
valid prediction scores 1; missed reference facts score 0 even with no predictions.
Review-only facts retain the existing scorer's exclusions. Precision penalties
are smooth during search so weak candidates still supply a gradient-free signal.
The report also includes pooled micro F2, precision, recall, attributes and latency.
These are agreement with the frozen annotations, not physician-verified accuracy.

All patients from one article stay in the same partition:

- Original 158 training articles: deterministic SHA256 split into 126 reflection
  articles and 32 inner selection articles. GEPA never reflects on selection
  labels. The selection set chooses prompts and therefore is not a test set.
- Original 59 validation articles: only evaluated after each task's candidate is
  frozen. Two fresh paired runs use seeds 42 and 43, reversing arm order. Both
  seeds must pass the gates. GPU batching can remain nondeterministic at a fixed
  seed, so seeds alone do not prove reproducibility.
- Original 43 test articles: excluded before packet preparation and inference.
  Existing validation has been inspected in earlier development work; it is not
  a pristine final test.

Each task must improve article-macro F2 by 0.005 on both confirmation seeds, avoid
micro F2 regression, retain precision >= max(0.80, baseline precision - 0.01), and
lose no more than 0.01 matched-attribute accuracy or per-field assertion, subject,
value and unit agreement. Reports include an article-cluster bootstrap interval
for the F2 delta, averaging the two seeds within each article; this is descriptive,
not a substitute for the untouched final test. Passing creates **proposed**
prompt overrides only. Full-pipeline evaluation with discovered patients is still
required before production adoption or final held-out testing.

Patient identities are fixed from reference identity fields. Target sections,
reference facts, summaries and timelines are NOT supplied to extraction. This is
an explicitly conditional task experiment; it does not measure roster discovery,
figure interpretation, series extraction or end-to-end article performance.
Reflection gets training reference diagnostics, as supervised optimization should.
Instructions prohibit copying case-specific answers; obvious article/segment IDs
and oversized proposals are rejected. This is not a proof against memorization.

## CPU/GPU allocation and failure handling

CPU preparation freezes source packets, code and references; CPU setup installs
client dependencies and downloads the pinned container/model/drafter. One TP1 B200 job runs four concurrent GEPA engines (one per task), sharing the
model server and a 64-request cap. This overlaps small reflection/evaluation
batches instead of reserving four GPUs for mostly sequential search loops. Each
engine has its own logger, candidate state and scoring cache. The worker exits
when all engines finish; no GPU barrier or second model startup. Sequential
reflection still limits batching, so zero idle time is not guaranteed. Telemetry
and model counters measure actual utilization. Failures in one optimizer are
recorded while the others finish; incomplete campaigns cannot pass verification.

Eight hours is the single-GPU cap, not a promise to consume eight hours. Search stops
on the metric budget or time stopper, reserving time for two-seed confirmation.
Overflows are explicit; no source truncation. Transport failures abort the task
rather than become low-score training examples. Saved calls, proposals and GEPA
state survive failures. The CPU afterany job reports incomplete tasks and exports
partial results; verification then fails. No automatic resume or repeated
confirmation selection is implemented for this first campaign.

CPU aggregation, integrity hashing and archive compression run after workers
release GPUs. Inline validation and scoring still run during search because GEPA
needs feedback to choose its next proposal. Article-task timing includes repairs,
not startup/queue; it is not equivalent to full-article pipeline latency.

## Launch and retrieve

Commit/push the changes yourself, then pull into the existing HiPerGator checkout.

```bash
bash scripts/run_dense_gepa.sh
bash scripts/run_dense_gepa.sh status
squeue -u "$USER"
```

Optional settings must be declared before launching a new campaign:

```bash
DENSE_BETA=2 DENSE_METRIC_CALLS=1500 DENSE_HOURS=8 bash scripts/run_dense_gepa.sh
```

The launcher refuses a second campaign while the previous recorded preparation
or worker/aggregation jobs are still queued or running. It uses committed code.

After CPU aggregation finishes, on HiPerGator:

```bash
bash scripts/run_dense_gepa.sh verify
WORK=$(cat runs/dense-gepa-last-work.txt)
cat "$WORK/summary/comparison.md"
```

On your Mac, substitute the run timestamp printed at launch:

```bash
REMOTE=/blue/cai6734/ehr_agent/op2-dense-gepa/run-TIMESTAMP
DEST="$HOME/Downloads/$(basename "$REMOTE")"
mkdir -p "$DEST"
scp "wkieffer@hpg.rc.ufl.edu:$REMOTE/results.tar.gz" \
    "wkieffer@hpg.rc.ufl.edu:$REMOTE/results.tar.gz.sha256" "$DEST/"
cd "$DEST"
shasum -a 256 -c results.tar.gz.sha256 && tar -xzf results.tar.gz
```

Inspect `summary/comparison.json`, `summary/proposed-overrides.json`,
`results/gepa/TASK/selection.json`, `confirmation.json`, `optimizer.log`, saved
calls/proposals, and `results/workers/*/job-*/gpu-telemetry.jsonl`.
