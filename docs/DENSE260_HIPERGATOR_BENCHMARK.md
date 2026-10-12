# Benchmark the completed dense articles on HiPerGator

This is a baseline benchmark of the frozen completed release, using the hardened
pipeline on `dense500-shared-rules` (including `677dcd5` and subsequent rules).
It does not run GEPA or edit annotations. The initial package contains **260
articles: 158 train, 59 validation, 43 test**, with **758 ready figure images**,
five missing images and one figure requiring rights review.

The launcher is `scripts/benchmark_dense.py`; implementation is
`src/openpatients2/dense_benchmark.py`. Preparation copies current working-tree
code, sources, image bytes and references into a separate campaign and records
their SHA-256 hashes. The release/checklists/annotation files remain unchanged.
Prepare defaults to exactly 260 completed rows and refuses a changed count until
you deliberately change `--expected`. Never edit a prepared campaign's code or
inputs. To change the implementation, prepare a new campaign.

## Configuration and provenance

The recipe comes from the archived `docs/VLLM_B200_LESSONS_LEARNED.md`,
`scripts/frontier_pilot.sbatch`, and serving/frontier modules at commit `c66587d`.
Four independent TP1 workers were measured in the October 7 campaigns. The
serving and allocation-telemetry helpers here were recovered from that revision.
**DFlash is the established speculative decoder. DSpark had no successful trial
establishing a replacement.** The new campaign orchestration is locally tested;
its first full hardware execution is still pending.

| Setting | Value |
| --- | --- |
| Target | `RedHatAI/Muse-Glimmer-30B-FP8-block` |
| Target revision | `1deb4641ff84f9a728dd11b27cac1f6a02a9ed14` |
| Assistant | `meta-models/Muse-Glimmer-30B-assistant` |
| Assistant revision | `e8192f3a8f617f74be2ce220360c89ef4789f39f` |
| Engine | vLLM 0.30.0; `docker://vllm/vllm-openai:v0.30.0` |
| Speculation | `dflash`, 15 speculative tokens, probabilistic draft sampling |
| GPUs | Four independent jobs, each 1 B200, TP1, 8 CPUs, 60 GB host RAM |
| Context / prefill / sequences | 65,536 / 32,768 / 64 |
| Memory / cache / graphs | 0.90 GPU memory, automatic KV dtype, prefix caching, PIECEWISE graphs |
| Generation | Medium reasoning, temperature 1.0, top-p 0.95, top-k 64, seed 42 |
| Output / retry | 16,384 tokens, up to two source-aware extraction repair rounds |
| Concurrency | Up to 64 text calls; up to two figure analyses; EHR stages bounded at two |
| Article scheduling | One active article per GPU; four workers run independently |
| GPU time limit | 12 hours per worker, configurable with `--hours` |

The official Meta chat template is restored verbatim under
`configs/hipergator/dense-benchmark/`, with its Apache license and expected SHA:
`cfc67e5f349f37690dfd31ed1f18bc4442a9dd32fe39a648f993cb4eb3cae678`.
The container digest and checkpoint file hashes are captured during CPU setup;
workers check artifact size/mtime and mount weights read-only. Startup probes
require vLLM 0.30.0, the Glimmer architecture/parser, and one B200-class GPU.
Per-worker caches are isolated. Original model replies, attempts, task outputs,
deployment commands, GPU telemetry and before/after vLLM counters are retained.

The current hardened original extraction path uses live patient discovery,
whole-article context, source-aware targeted repair, secondary-case isolation,
focused figure prompts and staged panel attribution. Optional inventories,
claim audits, coverage expansion, timeline/summary completion and evidence-ledger
experiments are off. This is one baseline arm, one seed, no optimized prompt.
The standard serving recipe does not imply that every historical experimental
extraction flag was a validated quality improvement.

Each article runs separately, so the pilot's 48-article and 12-image total limits
cannot silently reduce coverage. Preparation refuses any article with more than
12 figures. Patient discovery has a uniform ceiling of 48 patients per article,
independent of annotated patient counts; discovered patients beyond that ceiling
are an explicit operational error. Context overflows are logged and scored as
failures; there is no source truncation or automatic switch to 128K.

## Scope and interpretation

Inference sees the canonical article, captions and eligible original pixels. It
does **not** receive annotated patients, clinical facts, figure labels or reviewed
supplement transcriptions. EHR figure labels, privacy flags and article-table
measurement series run after extraction. **Supplement classification and clinical
extraction are not implemented in this campaign.** Unsupported targets remain in
the full-reference score and are identified in the scope report.

All 260 references remain in the denominator, including failed patient discovery
and missing predictions. Overall, split and per-article results report clinical
item precision/recall/F1, separate attribute correctness, roster, timelines,
summary, figures and measurement series. These are annotation-agreement scores.
Using all splits, including articles previously used for debugging, makes this a
development benchmark; it is not a blinded held-out evaluation.

## Prepare and upload from your Mac

Run in this repository. A ready package may already exist from implementation;
skip preparation if using it. Preparation refuses to overwrite an existing run.

```bash
cd /Users/mkieffer/programming/ToadResearch/openpatients2
RUN=dense260-glimmer-20261011
.venv/bin/python scripts/benchmark_dense.py prepare --work "$PWD/runs/$RUN" --expected 260
```

Replace `YOUR_GATORLINK` once. The storage root follows the previous campaigns;
it is independent of the Slurm account/QOS.

```bash
HPG=YOUR_GATORLINK@hpg.rc.ufl.edu
BASE=/blue/cai6734/ehr_agent/op2-dense-benchmark
ssh "$HPG" "mkdir -p '$BASE'"
scp "runs/$RUN.inputs.tar.gz" "runs/$RUN.inputs.tar.gz.sha256" "$HPG:$BASE/"
ssh "$HPG"
```

## Launch on HiPerGator

These commands use Python 3.11+ for the lightweight launcher, `uv` on PATH, and
the cluster's Apptainer module. CPU setup installs a locked Python 3.12 client
environment, downloads the pinned models, and pulls the container. No inference,
model downloads or dependency installation runs on the login node. Existing
Hugging Face credentials/access must permit the assistant checkpoint; an access
failure appears in the CPU setup log. Allow adequate scratch space for both model
snapshots, the container, caches and raw results (at least 150 GB free recommended).

```bash
BASE=/blue/cai6734/ehr_agent/op2-dense-benchmark
RUN=dense260-glimmer-20261011
cd "$BASE"
sha256sum -c "$RUN.inputs.tar.gz.sha256"
tar -xzf "$RUN.inputs.tar.gz"
WORK="$BASE/$RUN"
python3 "$WORK/code/scripts/benchmark_dense.py" submit --work "$WORK" --dry-run
python3 "$WORK/code/scripts/benchmark_dense.py" submit --work "$WORK" --account cai5724 --qos cai5724 --hours 12
```

The account/QOS values are the historical recipe. Submission first runs Slurm
`--test-only` for **every** job, so current limits are checked before submission.
Jobs are submitted held, then released downstream-first with dependencies:
CPU setup → four GPU workers → CPU aggregate/export. Aggregation runs after any
worker outcome and exports partial failures too. If submission fails, only jobs
created by that submission are cancelled. Job IDs and exact commands are saved
under `submissions/`. Twelve hours is an allocation budget, not a completion-time
estimate; use the new measured timings to choose future budgets.

## Check progress and completion

```bash
python3 "$WORK/code/scripts/benchmark_dense.py" status --work "$WORK"
squeue -u "$USER" -o '%.18i %.30j %.10T %.12M %.12l %R'
tail -n 30 "$WORK"/logs/worker-*.log
```

Get this campaign's exact IDs, then scheduler outcomes, elapsed allocation time
and exit codes (including queue timestamps):

```bash
JOBS=$(python3 -c 'import json,pathlib,sys; print(",".join(j["id"] for p in pathlib.Path(sys.argv[1]).glob("*.json") for j in json.loads(p.read_text())["jobs"]))' "$WORK/submissions")
sacct -j "$JOBS" --format=JobID,JobName%30,State,ExitCode,Submit,Start,End,Elapsed,AllocTRES%45
```

After the aggregate job ends:

```bash
python3 "$WORK/code/scripts/benchmark_dense.py" verify --work "$WORK"
cat "$WORK/summary/report.md"
head -n 6 "$WORK/summary/article-timing.csv"
cd "$WORK"
sha256sum -c results.tar.gz.sha256
```

`verify` exits nonzero for missing completed attempts, stale summaries or changed
inputs/predictions/scored artifacts. A verified benchmark can contain invalid
model outputs: those are measured outcomes, not hidden operational successes.
Read `completed_with_prediction`, `all_extraction_tasks_valid`, task statuses and
EHR errors alongside the score. An article whose discovery fails remains a full
scored miss even though its inference attempt ran to completion.

If any article was interrupted or had an operational exception, wait until **all
prior campaign jobs are terminal**, inspect/fix infrastructure, and resume:

```bash
python3 "$WORK/code/scripts/benchmark_dense.py" submit --work "$WORK" --account cai5724 --qos cai5724 --hours 12 --resume
```

Completed attempts are immutable and skipped. Incomplete articles get fresh
attempt directories; old attempts remain exported and their elapsed costs are
reported separately. Invalid-but-completed model outputs are not selectively
rerolled. If you change code or configuration, prepare a new campaign instead.

## Timing reports

- `summary/article-timing.csv`: every selected article, status, wall seconds,
  extraction seconds, EHR-stage seconds, model calls, input/output/reasoning
  tokens, failed attempts and number of extracted patients. Pending rows are
  explicit. Unknown token usage is blank/null, never silently zero.
- `summary/report.json` and `report.md`: article mean, median, p95 and maximum;
  startup timings, slowest articles, summed worker seconds and observed worker
  span. The JSON includes retry costs and scope/failure summaries.
- `summary/timing-all-attempts.jsonl`: includes operationally failed attempts,
  in addition to the first completed result for each article.
- `results/workers/SHARD/job-JOBID/`: startup receipts, probe, deployment,
  per-allocation GPU telemetry and engine counters. Use `sacct` to complete
  accounting for hard-killed jobs lacking final timing receipts.

Per-article time includes live discovery, images, generation, validation/repair
and EHR stages. It excludes queue and model startup. The first article is cold;
there is no hidden warmup. One active article per GPU makes latency attributable,
but does not measure the maximum throughput possible with cross-article batching.
Worker rates are not added and called simultaneous throughput. CPU preparation
and queue time remain separate from inference latency.

## Copy results back to your Mac

Run locally after aggregation finishes. The archive contains the frozen code,
references, source images, predictions, raw attempts, scores, timing, runtime
identity receipts and logs. It excludes weights, the container, venv and caches.
Partial failures also produce an archive; use `verify` to distinguish them.

```bash
cd /Users/mkieffer/programming/ToadResearch/openpatients2
HPG=YOUR_GATORLINK@hpg.rc.ufl.edu
RUN=dense260-glimmer-20261011
REMOTE=/blue/cai6734/ehr_agent/op2-dense-benchmark/$RUN
DEST="$PWD/runs/returned-$RUN"
mkdir -p "$DEST"
scp "$HPG:$REMOTE/results.tar.gz" "$HPG:$REMOTE/results.tar.gz.sha256" "$DEST/"
cd "$DEST"
shasum -a 256 -c results.tar.gz.sha256
tar -xzf results.tar.gz
python3 "$RUN/code/scripts/benchmark_dense.py" verify --work "$PWD/$RUN"
cat "$RUN/summary/report.md"
```

Keep the cluster runtime until results have been copied and verified. This
launcher does not delete shared model files or scratch automatically.
