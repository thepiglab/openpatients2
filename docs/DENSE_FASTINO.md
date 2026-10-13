# Fastino reasoning sweep on HiPerGator

Run from the existing checkout after committing/pushing these changes yourself
and pulling them on HiPerGator. The launcher freezes **committed** code. It does
not modify annotations, train weights, optimize prompts, or submit local jobs.

```bash
bash scripts/run_dense_fastino.sh
bash scripts/run_dense_fastino.sh status
squeue -u "$USER"
# Once the CPU aggregate job finishes:
bash scripts/run_dense_fastino.sh verify
WORK="$(cat runs/dense-fastino-last-work.txt)"
cat "$WORK/summary/comparison.md"
cat "$WORK/cleanup.json"
echo "$WORK"
```

The default runs **three Fastino modes × 217 train/validation articles = 651
article runs**, preserving the 43 test articles. Only Fastino is downloaded and
served; no new Glimmer job is submitted. The modes are off, efficient thinking,
and ordinary thinking on. To intentionally include all 260 completed articles, set
`DENSE_INCLUDE_TEST=1 bash scripts/run_dense_fastino.sh` **instead** of the default
launch. Existing annotations remain frozen. A second invocation is refused while
the previous campaign has active jobs. Override `DENSE_HOURS` (default 8 per GPU),
`DENSE_ACCOUNT`/`DENSE_QOS` (both default cai5724), or give an absolute campaign
output path as the first argument. Normal `sbatch` validates account/QOS policy
and queues accepted jobs; submission is not gated by a `--test-only` start-time
estimate, which can fail with “Resource temporarily unavailable” on a busy cluster. Eight hours is an allocation cap, not a guaranteed completion time.

CPU preparation freezes source packets and reference membership. CPU setup
installs the existing locked client dependencies, downloads the pinned model and
vLLM container, tests native tokenizer rendering/architecture/parser support,
and hashes the artifacts. Four independent B200 jobs each request **1 GPU,
8 CPUs, 128 GB host RAM**; TP1, a shared dynamic article-triplet queue, one
active article/mode per worker with concurrent extraction tasks. Each worker
loads Fastino once and tests all three modes per claimed article, with cyclic
rotation and reversal of mode order based on article-ID hash. Fast workers
claim more articles; workers exit when their queue is exhausted. Four GPUs total,
not four per mode. A complete run may need more than the eight-hour cap;
partial results are labeled inconclusive and can be resumed. CPU aggregation runs after all
workers terminate, including failures. It scores and writes a checksummed
`results.tar.gz` automatically. **Do not zip it manually.** Model weights are
never part of that archive. CPU jobs request no GPUs. GPU jobs are offline and
never download/install/score/export; their receipts include startup/shutdown
and sampled GPU telemetry. Pipeline dependencies and short/late requests can
still leave GPU gaps; sustained saturation is not guaranteed.

After complete coverage, successful verification and archive checksum validation,
CPU cleanup deletes only this campaign's `runtime/weights`, private
`runtime/download-cache`, and `runtime/vllm-0.30.0.sif`. Shared HF caches and other
campaigns are untouched. Runtime hashes remain in `runtime.json` in the export.
The post-export cleanup receipt stays at `cleanup.json` beside the archive.
Incomplete or failed campaigns retain their model for diagnosis/resume.

On your **Mac**, replace RUN with the exact folder printed above:

```bash
RUN=run-YYYYMMDD-HHMMSS
mkdir -p "$HOME/Downloads/fastino-$RUN"
scp "wkieffer@hpg.rc.ufl.edu:/blue/cai6734/ehr_agent/op2-dense-fastino/$RUN/results.tar.gz*" "$HOME/Downloads/fastino-$RUN/"
(cd "$HOME/Downloads/fastino-$RUN" && shasum -a 256 -c results.tar.gz.sha256)
```

Use your usual working SSH hostname if it differs. The archive has a RUN/
root containing `summary/comparison.md` and `comparison.json`, matched-article
timing, plus `arms/{off,efficient,on}/summary/` with `scores*.json`,
`article-timing.csv`, raw predictions, request ledgers, worker telemetry, frozen code/source/reference,
model revision/hash metadata, and submission receipts. It excludes weights,
container and virtual environments. For scheduler allocation time:

```bash
sacct -u "$USER" --starttime today --format=JobID,JobName%24,State,Elapsed,AllocTRES%45
```

If partial, wait until all jobs are terminal, then on HiPerGator:

```bash
WORK="$(cat runs/dense-fastino-last-work.txt)"
python3 "$WORK/code/scripts/benchmark_dense_fastino.py" submit --work "$WORK" --resume
```

Resume uses existing weights and retries only unfinished/operationally failed
articles. A completed article with invalid model output is scored as a miss,
not retried until it happens to succeed. Successful campaigns clean their model;
start a new campaign to rerun completed inference. If setup failed before
`runtime.json` was written, resume schedules CPU setup again.

## Model and comparison scope

Primary sources checked October 12, 2026:

- [Model card](https://huggingface.co/fastino/Fastino-Nemotron-3.5-Lightning-Healthcare):
  text-only, merged 30B total/3B active MoE, BF16 (~66 GB weights), Apache 2.0.
- [Pinned files](https://huggingface.co/fastino/Fastino-Nemotron-3.5-Lightning-Healthcare/tree/c602dcde62676c42bb9a31647d4fd8697e7c9e64):
  `NemotronHForCausalLM`, 262144 architectural context, native chat template
  supports `enable_thinking=False` and supplies the closed think prefix.

This first profile uses pinned revision `c602dcde62676c42bb9a31647d4fd8697e7c9e64`,
vLLM **0.30.0** (the repository's tokenizer/API contract), BF16, 64K context,
16K generation/repair caps and greedy temperature 0 in every mode. The pinned
[chat template](https://huggingface.co/fastino/Fastino-Nemotron-3.5-Lightning-Healthcare/blob/c602dcde62676c42bb9a31647d4fd8697e7c9e64/chat_template.jinja)
exposes two controls, rather than Glimmer's low/medium/high/xhigh enum:

| Mode | enable_thinking | medium_effort | Native behavior |
|---|---|---|---|
| off | false | default false | Closed think prefix |
| efficient | true | true | Open think prefix plus an efficient-effort user hint |
| on | true | false | Open think prefix, ordinary reasoning |

We test all three meaningful reasoning modes. The efficient flag while thinking
is off only adds a hint to a non-thinking prompt; it is not a fourth reasoning
level. The same per-mode kwargs go to token counting and generation. CPU setup
checks all three distinct native renderings before inference. The sweep compares
quality and speed at fixed decoding/resource limits; it does not tune GPU engine
parameters or establish an optimal temperature. Shared prefix caching can affect
later arms; rotating order mitigates, but does not eliminate, this effect. The
model card demonstrates vLLM 0.23.0; compatibility with our newer pinned runtime
is checked on CPU and then on the allocated B200, but has not been hardware-tested
locally. There is no Glimmer template, DFlash drafter, tool parser, quantization
conversion, or constrained JSON decoding. Schemas remain prompt instructions
and local validators. CPU setup fails before inference if template or registry
checks fail; GPU startup errors remain visible in logs and partial exports.

The prompts, live patient discovery, literal evidence checks, bounded repairs,
caption attribution and text measurement-series stage reuse the existing
benchmark. No reference rosters/facts enter inference. No images are copied,
downloaded, or passed to this text model. Figure EHR/privacy pixel stages and
supplement extraction are unsupported. Full reference denominators are retained;
primary comparison is clinical precision, recall, F1 and recall-weighted **F2**
(`5 TP / (5 TP + 4 FN + FP)`), on the same IDs/splits. Existing scores also report
attributes and patient matching. These measure annotation agreement, not clinical
validation. Inspect validation separately from training. This campaign does not
automatically compare unrelated old run directories or claim a model winner.

Timing includes per-article mean/median/p95, each article's CSV row, GPU worker
span and summed worker seconds. Queue/setup/scoring are excluded from article
latency. Glimmer's image-inclusive latency is not a controlled text-only speed
comparison. Existing Glimmer results remain the historical baseline; this script
runs Fastino only. Compare quality on matching article IDs/splits when analyzing
the returned results. No unrelated old run directory is silently loaded.

The archived `docs/VLLM_B200_LESSONS_LEARNED.md` at
`7b757c81c78abd5c5e2855e96feb2a53bcef5d8b^` was checked: retain CPU staging,
independent TP1 workers, exact tokenizer checks, frozen identities and measured
GPU telemetry. Its Glimmer-specific speculative settings are not transferable
to this different architecture.

## Timing, partial results, and current runs

Every mode retains all the original clinical, attribute, patient, summary,
timeline, figure-caption and series scores, plus clinical F2 by split. The
comparison reports mean/median/p95 article latency, generated tokens per article
second, and speedup versus thinking-off on the **same completed article triplets**.
Complete-attempt model failures still count as misses. Partial-run full scores
include unfinished articles as misses; matched-triplet scores are supplemental
and may be biased. No automatic winner is declared on partial coverage.

Setup seconds, scoring seconds, server startup, worker span and summed worker
seconds are separate. The external `cleanup.json` records total CPU
scoring/export/cleanup duration and elapsed time since campaign preparation
(including subsequent queue waits, excluding initial bootstrap wait). Use sacct
for allocation and initial queue times. An eight-hour cap across four workers
can consume up to 32 GPU-hours. Timing is measured, not predicted from older runs.

Launching uses a new dated directory, dedicated runtime/cache/ports and a
Fastino-specific last-run pointer. It neither cancels nor modifies your other
campaigns. Submission-error cancellation is restricted to job IDs created by
this submission; cleanup is restricted to the campaign-owned model artifacts.
The launcher rejects another active Fastino campaign from this checkout. Other
runs are allowed to continue. **Account GPU limits, fair-share and queue capacity
are shared**: additional jobs can wait or affect scheduling, so this is not a
guarantee of zero scheduling impact. Existing queued/running jobs retain their
frozen code and allocated resources. No benchmark jobs are submitted by local
code preparation or tests.

The posted cai5724 investment allowance is 32 CPUs, 250 GB host memory and
8 GPUs. Five GPUs total does not imply that both campaigns can run every worker
concurrently: four Fastino workers alone request 512 GB host memory. With the
current conservative 128 GB-per-worker profile, Slurm must stagger Fastino
workers under that memory allowance. Pending jobs consume no GPU allocation;
their eight-hour time limit starts when they run. Account policy, burst QOS and
other group users can change the effective availability.
