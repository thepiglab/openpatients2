# Short distributed evidence-ledger frontier pilot

The new campaign compares an architectural change on the fixed 31-article bundle
fixture. It runs the existing live-complete program, evidence-ledger source routing
with additive episode assembly, and the same ledger program with independent
attribute challenges. Each article stays in one deterministic shard and runs all
three arms at seeds 42 and 43. Live patient discovery, medium reasoning, 16K
initial/retry caps, the pinned FP8/DFlash model, staged figure analysis, and the
same bounded prepared pixels remain matched across arms.

The ledger arms replace the legacy inventory, coverage, claim, ordering and late
completion passes. Their source maps are routing hypotheses. An attribute verdict
can trigger source-aware re-extraction of a domain; it cannot silently commit a
model's suggested clinical assertion. Episode assembly uses bounded additive
deltas over stable fact aliases. These changes require measured comparisons;
neither a valid ledger nor literal quotations prove clinical support.

## Launch on HiPerGator

Submit from the complete repository checkout, with a **new absolute campaign
directory on shared storage visible to all nodes**:

```bash
bash scripts/run_frontier_pilot.sh /absolute/shared/op2-frontier/run-NEW \
  --sif /absolute/shared/vllm-v0.30.0.sif
```

An existing SIF is optional; without it the CPU setup job acquires the pinned
container. The CPU download job acquires and verifies one FP8 checkpoint plus its
small assistant snapshot exactly once. The default is four independent one-B200
jobs, each with 8 CPUs, 60 GB host memory and a 90-minute limit including probe,
startup, warmup and inference. They may run on different nodes and may start at
different times. Nothing requests a complete eight-GPU node.

For eight workers, use `--gpu-workers 8`; each then requests one B200, 4 CPUs and
30 GB host memory. `--gpu-minutes 60` through `--gpu-minutes 120` selects the short
GPU limit. Both modes remain within account/QoS `cai5724`'s global **32 CPU / 250 GB
/ 8 GPU** allocation: four workers sum to 32 CPU / 240 GB / 4 GPU; eight sum to
32 CPU / 240 GB / 8 GPU. The smaller host-memory allocations have local scheduler
coverage but require an actual cluster startup measurement. They are not new
B200 performance results.

To validate inputs and inspect requests locally without Slurm submission, model
downloads, or inference:

```bash
uv run --locked --python 3.12 --no-dev python -m openpatients2.frontier_campaign plan \
  --work-dir /absolute/new/plan-directory
```

The main review plan is `frontier-plan.json`. The nested `engine` directory also
contains the historical engine helper's unused planning receipts; only the new
frontier scheduler submits jobs.

## CPU/GPU stages and ownership

The job graph is CPU prepare → CPU setup → CPU download → independent GPU workers
→ CPU aggregation → CPU cleanup. Preparation profiles every fixed article,
prepares at most twelve figures/64 MB of pixels, writes immutable shard inputs,
and retains a bounded source/reference review snapshot. Every CPU job requests
`--gres=none`; runtime checks also refuse CPU work inside GPU allocations. GPU
workers only perform container/GPU probing, local model serving and inference.
GPU-stage dependency synchronization and model/network downloads are disabled.

The shared `engine` mount is read-only in every worker's container. Workers use
different ports, shard directories, traces, telemetry and HF/compilation caches.
They hold concurrent shared reader locks; model cleanup uses the existing owned
exclusive lock. No worker creates a checkpoint copy or rewrites another shard.
The SIF digest check is part of initialization inside each worker's GPU time
budget, matching the existing campaign's verification contract.

Submission preflights and holds the entire graph before releasing successors and
then preparation. Fan-in dependencies use `afterany` and explicitly include all
workers, so failed setup/downloads reach short readiness failures and subsequent
cleanup rather than leaving `DependencyNeverSatisfied` GPU jobs. A partial
submission is cancelled while held. Failed releases rehold and cancel the graph.

Cleanup verifies that every submitted worker is terminal in Slurm accounting
before deleting the owned checkpoint. The exclusive checkpoint lock remains an
additional guard. It deletes model storage even if aggregation crashed, rebuilds
an explicit partial summary if necessary, then removes owned acquisition,
temporary shard inputs and per-worker runtime/HF/compilation caches. Small fixed review sources, selected pixels, extraction
audits, patient exports and the container remain available for review.
If accounting is unavailable or a worker is still queued/running, cleanup fails
closed and preserves the checkpoint for a subsequent CPU cleanup attempt:

```bash
uv run --python 3.12 --no-sync --offline python -m openpatients2.frontier_campaign stage \
  --work-dir /absolute/shared/op2-frontier/run-NEW --phase cleanup
```

Run this retry only in a CPU allocation; the scheduler's original cleanup is
automatic. Inspect `frontier-jobs.json`, `cleanup.json` and the stage logs when a
job fails.

## Interpreting and exporting results

`SUMMARY.md` reports delivered clinical probes, ordering probes, valid task counts,
missing shards, bundle scores and output tokens per GPU-second. CPU aggregation
merges patients and discovery by arm/seed and scores the **complete reference once
per pair**, retaining missing shards in the denominator. It does not sum shard
scores. `paired_comparison_available` is false for any missing, failed or deferred
trial. Partial runs remain useful diagnostics, and cannot establish an improvement.

Each ledger arm/seed also has a `paired-comparison.json` with separate train,
validation and test metrics, gained/lost probes and article-cluster bootstrap
intervals. Discovery alignment is source-bound and supplied for both arms. The
existing test split has historical development exposure; it is not a newly
blinded clinical test. Schema acceptance, literal evidence and finite source-gold
checks do not establish exhaustive medical accuracy.

Token efficiency pools measured completion usage over summed one-GPU arm wall
time. Unknown usage stays unavailable; completed-trial rates exclude startup,
warmup, failed and deferred trials. Staggered worker token rates are never added
and described as concurrent throughput. Worker telemetry and startup logs provide
the separate serving/allocation overhead.

`objective-adequacy.json` inventories source-map, attribute and episode labels for
future focused prompt work. This first campaign runs the architectural ablation;
it has no GEPA job. Sparse or indirect labels should be improved and actual paired
failures examined before spending a long optimization allocation.

Export the complete bounded review bundle with one command after collection:

```bash
uv run --python 3.12 --no-sync --offline python -m openpatients2.frontier_campaign export-results \
  --work-dir /absolute/shared/op2-frontier/run-NEW \
  --output /absolute/shared/op2-frontier/results-NEW.tar.gz
```

The new archive must be outside the campaign. Its allowlist includes merged
results, source/episode ledgers, raw task attempts, patient outputs, provenance,
the bounded fixed review fixture and selected pixels. It refuses symlinks and
caps uncompressed contents at 8 GB. It omits checkpoints, the SIF, environments,
GPU compilation/HF caches and temporary shard source copies.

Local regressions cover independent requests, CPU/GPU allocation limits,
failure-tolerant dependencies, submission rollback, reader/cleanup locking,
full-reference aggregation, paired splits and safe result export. No cluster job
has been submitted or new model downloaded by these local checks.
