# Eight-hour paired pipeline campaign

Run `bash scripts/run_dense_overnight.sh` from the existing HiPerGator checkout
of main after committing/pushing these changes yourself and pulling on the
cluster. The launcher uses the committed revision, not uncommitted edits.
It submits CPU preparation, one CPU model setup, four independent TP1 B200
workers, and CPU comparison/export. No manual upload, clone or model serving is
needed. Account/QOS default to cai5724; shared storage remains under
`/blue/cai6734/ehr_agent/op2-dense-overnight`.

## Scientific plan

This is a paired pipeline experiment, **not GEPA**. The previous run exposed
output handling and discovery failures that should be measured before prompt
search. Both arms process all **158 training and 59 validation articles**, with
live patient discovery, original eligible pixels, and the same frozen reference
for scoring only. All **43 test articles are excluded before input preparation**.
No reference roster, clinical fact or image annotation enters inference. The
original annotated articles are never edited.

- **control:** the previous benchmark's extraction behavior, seed 42.
- **repairs:** the same configuration plus `overnight_repairs=true`: exact
  same-article figure-ID prefix normalization, bounded malformed-output excerpts
  in repair prompts, summary JSON-shape guidance, table/footnote discovery
  guidance, explicit exclusive segment-or-cell ownership citations, up to two
  EHR repair rounds, and no attribution generations without a valid inventory.
  Every normalized visual still passes full schema/evidence validation; raw
  candidates and normalization receipts remain available. Already-valid series
  are protected during repair. Source quotations and numeric validation remain
  mandatory.

The package is an intervention bundle, not a factorial ablation. A change in
score will not isolate the contribution of each fix. Oversized-source routing,
constrained decoding, larger context, clinical coverage expansion and GEPA are
not silently enabled. Unsupported supplements and explicit context failures
remain scored misses.

The historical recipe was checked against `docs/VLLM_B200_LESSONS_LEARNED.md`
and old launchers at git revision `c66587d`. Deployment stays identical to the
completed dense benchmark: pinned Glimmer FP8, vLLM 0.30.0, DFlash 15,
medium reasoning, 64K context, 32K prefill, 64 sequences, text concurrency 64,
vision/EHR concurrency 2, 16K output caps, four independent B200s. See
[DENSE260_HIPERGATOR_BENCHMARK.md](DENSE260_HIPERGATOR_BENCHMARK.md) for exact revisions.

Each GPU starts one server and takes whole article pairs from a shared queue,
with larger sources/images first. Atomic directory claims prevent duplicate concurrent work across nodes;
fast workers take more pairs rather than waiting for a fixed shard. Workers exit
when only pairs already owned by other workers remain. SHA256 parity of each article ID counterbalances arm order to
reduce cache-order bias. Requests use the same seed, but hardware scheduling
still need not give bitwise identical generations. There is no cross-article
batching or hidden warmup.

## Time limits and incomplete runs

Default Slurm allocation: **8 hours per GPU**, up to 32 allocated GPU-hours
across four independent jobs. Based on the previous 260-article run, the 434
article-arm evaluations should take approximately six to eight hours; this is
an estimate, not a completion guarantee. CPU preparation, model download/setup,
queue time and CPU scoring/export are additional. The four jobs need not start
simultaneously, so submission-to-download time can exceed eight hours.

The worker reserves ten minutes for shutdown and will not start an article with
less than its 15-minute per-article allowance remaining. The allowance includes
extraction and EHR. An article timeout remains an operational error, not a valid
empty prediction; completed invalid model outputs remain ordinary scored misses.
Completed article attempts are checkpointed. Each submission has a fresh queue
namespace, so claims left by a hard-killed job cannot block an explicit resume. Aggregation runs after any worker
outcome and exports partial results. `verify` fails on incomplete coverage and
the promotion gate becomes inconclusive. SIGTERM and hard kills can leave
unfinished attempts; scheduler accounting remains necessary for hard-kill time.

`DENSE_HOURS=8`, `DENSE_ACCOUNT`, `DENSE_QOS`, and `DENSE_REF` can be specified at
launch. Hours are frozen in the manifest. A resume receives another full
allocation budget, so it is an explicit additional run, never an automatic
extension of the eight-hour campaign.

## Run and inspect

On HiPerGator, from the already-cloned repository (after pulling your commit):

```bash
bash scripts/run_dense_overnight.sh
bash scripts/run_dense_overnight.sh status
squeue -u "$USER"
```

After CPU aggregation finishes:

```bash
bash scripts/run_dense_overnight.sh verify
WORK=$(cat runs/dense-overnight-last-work.txt)
cat "$WORK/summary/comparison.md"
```

`status` reports execution coverage; scoring/export may still be running after
all articles complete. `verify` requires scored, complete, hash-verified results.
Inspect `WORK.bootstrap/prepare.log`, `WORK/logs/`, and
`WORK/results/workers/*/job-*/receipt.json` for infrastructure/budget failures.
An invalid model result is not a failed Slurm job by itself.

Optional read-only submission preview after preparation:

```bash
python3 "$WORK/code/scripts/benchmark_dense_overnight.py" submit --work "$WORK" --dry-run --resume
```

Only after previous jobs are terminal, an explicit resume can finish interrupted
articles (it does not reroll completed-but-invalid model outputs):

```bash
python3 "$WORK/code/scripts/benchmark_dense_overnight.py" submit --work "$WORK" --resume
```

## Interpretation and saved results

`summary/comparison.md` and `.json` show train/validation metrics, timing and a
predeclared **validation-only** recommendation. Recommend the repairs arm only
when both arms completed every planned article, validation clinical F1 improves
by at least 0.5 percentage points, precision and matched-attribute accuracy drop
by no more than one point each, and patient recall does not decline. Otherwise
retain the control for review; partial runs are inconclusive. This is an
engineering gate, not a statistical significance claim or automatic deployment.
No test scores are used to select an arm, and no test evaluation follows it.

Full-arm scores include missing/unfinished articles in the reference denominator.
`scores-completed-pairs.json` is a separate diagnostic on the paired completed
subset; it can be biased and cannot authorize promotion. A completed empty roster
stays in that paired subset as a miss.

- `arms/control/summary/` and `arms/repairs/summary/`: full per-article and split
  scores, article-timing CSV, errors and task validity.
- `summary/paired-article-timing.jsonl`: both latencies and patient counts for each
  planned article, including missing entries explicitly.
- `results/workers/`: shared startup, GPU telemetry, DFlash counters and receipts.
- `arms/*/results/articles/`: original replies, repair attempts and prediction
  artifacts; never replaced by the comparator.

Article latency excludes shared startup and queue time. Shared allocation totals
appear only in the root comparison. Do not interpret zero arm-level worker
receipts as free inference or add rates as concurrent throughput. Existing
reasoning-token reporting may be zero despite saved reasoning text; total output
usage is retained, but separate reasoning-token cost is not reliable.

## Download to your Mac

Set REMOTE to the campaign path printed at launch (or the contents of the pointer
file on HiPerGator). Run locally:

```bash
HPG=wkieffer@hpg.rc.ufl.edu
REMOTE=/blue/cai6734/ehr_agent/op2-dense-overnight/run-TIMESTAMP
DEST="$HOME/Downloads/$(basename "$REMOTE")"
mkdir -p "$DEST"
scp "$HPG:$REMOTE/results.tar.gz" "$HPG:$REMOTE/results.tar.gz.sha256" "$DEST/"
cd "$DEST"
shasum -a 256 -c results.tar.gz.sha256
tar -xzf results.tar.gz
python3 "$(basename "$REMOTE")/code/scripts/benchmark_dense_overnight.py" verify --work "$DEST/$(basename "$REMOTE")"
```

The archive includes frozen code, sources, references, both result arms and
comparison evidence. It excludes model weights, containers and environments.
No commits, pushes, cluster submissions or external inference are performed by
local code validation.
