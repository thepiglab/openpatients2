# K2 medical extraction benchmark on HiPerGator

The transfer archive contains code, a uv lockfile, frozen evaluation inputs and Slurm workers. It contains no model weights, images, API keys, ontologies or large datasets. Downloading and testing the four models happens on HiPerGator. No OpenRouter credits are used.

The defaults follow your ms2smiles scripts: account **cai5724**, QoS **cai5724**, GPU partition **hpg-b200**, one node per model with **four B200s for 375B** or **two B200s for smaller models**. The largest model requests 16 CPUs/250G host RAM; 32B and 7B request 8 CPUs/64G, and MoVA requests 8 CPUs/128G. These fit the cai5724 envelope of 32 CPUs/250G/eight GPUs. CPU jobs use the site's default CPU partition and request no GPUs. The workers load `apptainer` if it is not already on PATH; change that module line if your environment uses a different module name. `uv`, `sbatch`, `scontrol`, and network access in the CPU setup/download allocation must be available.

## Run

Transfer [the archive](../dist/openpatients2-k2-benchmark.tar.gz) to your HiPerGator shared storage. For example, after placing it in `/blue/cai5724/wkieffer/`:

```bash
tar -xzf openpatients2-k2-benchmark.tar.gz
cd openpatients2-k2-benchmark
bash scripts/hpg_benchmark.sh \
  --work-dir /blue/cai5724/wkieffer/op2-k2-runs/run-01
```

That single launcher command installs the small, locked Python 3.12 client environment and submits the complete job chain. Heavy dependencies, the container and all checkpoints are acquired in CPU jobs. GPU jobs use `uv run --no-sync --offline` and Hugging Face offline mode; they cannot fetch missing checkpoint files. Results and scratch must be on shared storage visible from all nodes. Use a **new work directory** for every new campaign.

The vLLM 0.30.0 runtime image installs Python 3 as `/usr/bin/python3`; the launcher uses that absolute interpreter for both probes and `-m vllm.entrypoints.cli.main serve`. It does not assume a `python` alias exists. CPU setup checks the interpreter and installed vLLM version before any checkpoint download, saves `WORK/container-python.json`, and blocks downloads if setup failed. This CPU metadata check does not test CUDA or checkpoint loading. See the [pinned image Dockerfile](https://github.com/vllm-project/vllm/blob/v0.30.0/docker/Dockerfile).

To test an existing SIF on one B200 without downloading weights, submit `scripts/hpg_container_check.sbatch` with the SIF path as its argument. For example, from the checkout:

```bash
sbatch --output=/path/to/run/container-check-%j.log \
  scripts/hpg_container_check.sbatch /path/to/run/vllm.sif
```

The diagnostic prints Python/Torch/CUDA/vLLM failures directly, checks native K2 and its reasoning parser, and requests one GPU for at most ten minutes. Passing it does not establish successful quantized checkpoint loading or inference.

Before submitting any jobs, the launcher checks each CPU/GPU resource profile with `sbatch --test-only`. It then submits every job held, and releases successors first and CPU setup last. Downloads cannot begin until the full chain, including every cleanup job, has been accepted. Preflight checks do not reserve resources; a later rejection still triggers rollback. Scheduler stdout/stderr and exact commands are saved in `WORK/slurm-commands.json`. `WORK/submission.json` records preflight, release and rollback status. A rejection prints Slurm's error directly instead of an uninformative Python traceback. See [Slurm's preflight and hold options](https://slurm.schedmd.com/sbatch.html) and [release command](https://slurm.schedmd.com/scontrol.html).

If the client environment is already prepared, the equivalent command is:

```bash
uv run --no-sync op2 hpg-benchmark submit \
  --work-dir /blue/cai5724/wkieffer/op2-k2-runs/run-02
```

For a direct uv command that also prepares the small client environment, use:

```bash
uv run --locked --python 3.12 --no-dev op2 hpg-benchmark submit \
  --work-dir /blue/cai5724/wkieffer/op2-k2-runs/run-03
```

To inspect the job commands before submitting, use `plan` with another new work directory. This creates a reviewable `submit-plan.sh` and `jobs.json` without downloading models or contacting Slurm. The plan script uses placeholder job IDs and is for inspection, not execution. Run `submit` with a new directory when ready.

```bash
uv run --no-sync op2 hpg-benchmark plan --work-dir /tmp/op2-k2-plan
```

Pass `--sif /path/to/vllm-030.sif` to reuse a pre-existing vLLM 0.30.0 Apptainer image. Otherwise the CPU setup job pulls `docker://vllm/vllm-openai:v0.30.0` once. Its SHA256 and actual runtime versions are recorded, and each GPU job checks the image SHA, native K2 architecture, reasoning parser, the profile's exact number of visible Blackwell GPUs and exact vLLM version. The image tag is versioned but mutable; the recorded SIF digest identifies the actual image used.

## Recover from a rejected submission

The older launcher hid `sbatch` stderr and requested 64 CPUs/512G host RAM. The traceback alone cannot establish why Slurm rejected that request. The current configuration uses one TP4 replica for 375B and two TP1 replicas for smaller models, with per-model CPU/RAM requests. An earlier fix used eight GPUs/32 CPUs/250G; update the launcher source as well as the YAML to enable smaller allocations. The uv hardlink-to-copy warning is unrelated to Slurm submission; `export UV_LINK_MODE=copy` can silence it.

The older launcher attempted to cancel jobs already submitted. Check the job IDs in `run-01/jobs.json` against `squeue -u "$USER"` before replacing code. Cancel any remaining jobs from that campaign with `scancel JOB_ID ...`; this does not delete downloaded files. If `run-01/active-model` exists, wait until its GPU job has stopped and use the owned cleanup command below with the original package before updating it. Every worker verifies its package fingerprint, so replacing code while old jobs remain active will stop those workers.

Copy `openpatients2-k2-smaller-gpus.tar.gz` to HiPerGator. From your existing checkout, apply the small patch and use a fresh campaign directory:

```bash
tar -xzf /path/to/openpatients2-k2-smaller-gpus.tar.gz
uv run --locked --python 3.12 --no-dev op2 hpg-benchmark submit \
  --work-dir /blue/cai5724/wkieffer/op2-k2-runs/run-02
```

The patch contains the launcher, evaluator, serving launcher, GPU worker, container diagnostic, resource YAML, README and this guide. Retain the old work directory for inspection. If the new resource preflight fails, no jobs have been submitted; the displayed scheduler error and saved diagnostics identify the actual rejection. A submission or release failure requests cancellation of known job IDs and reports any rollback failure. On a release failure the launcher first reholds successors, so canceling an `afterany` parent cannot start a download. If reholding fails, it leaves the complete chain in place for inspection instead of canceling its cleanup jobs. A timeout or invalid job-ID response has an ambiguous submission outcome: inspect `squeue` for the recorded job name before retrying. Cancellation requests are recorded as requests, rather than proof that jobs have stopped.

## Storage and failures

The chain is:

```text
CPU setup → CPU download 375B → GPU evaluation → CPU deletion
          → CPU download 32B  → GPU evaluation → CPU deletion
          → CPU download MoVA → GPU evaluation → CPU deletion
          → CPU download 7B   → GPU evaluation → CPU deletion → CPU report
```

The largest pinned checkpoint occupies **229.64 GB** before scratch. The download guard requires approximately **278 GB free** (checkpoint × 1.1 + 25 GB headroom). Allow **at least 300 GB free shared scratch**, plus room for the retained container and outputs. Filesystem free space does not measure your account quota; check both. The CPU container pull separately requires 70 GB free for temporary OCI layers and its SIF; its build cache is removed before checkpoint downloading begins. Existing SIFs bypass that build requirement.

Every checkpoint is downloaded to `WORK/active-model/weights`; Hugging Face, Xet, custom-code and inference caches also live inside `WORK/active-model`. The CPU stage pins the exact HF commit, verifies every expected file's size, hashes every file, and checks LFS SHA256s. GPU preflight requires that successful audit and unchanged files. Cleanup deletes only this campaign's marked storage, preserving outputs, fixtures, container and uv environment. A filesystem lock also prevents inference and deletion from overlapping.

The GPU stage and cleanup use `afterany`, so failed or timed-out models still reach deletion. A next download uses `afterok` on deletion, so a failed deletion stops the chain before a second model can occupy the disk. A failed setup/download does not leave dependent GPU/cleanup jobs stuck on `DependencyNeverSatisfied`. A handled download failure cancels its still-dependent GPU job, allowing CPU cleanup to proceed without acquiring GPUs; abrupt scheduler termination instead reaches GPU preflight and then cleanup. The other models continue after successful cleanup; the final report marks incomplete models and exits nonzero. Structural/medical extraction failures are measured outcomes, not reasons to abandon all remaining tasks.

The GPU container preflight records its exact command, exit code, stdout and stderr in `WORK/results/MODEL/container-probe.json`, including failed probes. Its failure message includes the subprocess error and diagnostic path; a successful image build does not establish successful GPU initialization or native model/parser availability.

Inspect `WORK/logs/`, `WORK/jobs.json`, and `WORK/results/MODEL/{download,gpu,cleanup}.json`. Slurm cancellation of the **entire** chain also cancels scheduled cleanup: after all GPU jobs have stopped, remove leftover owned storage with:

```bash
uv run --no-sync op2 hpg-benchmark stage cleanup \
  --work-dir /blue/cai5724/wkieffer/op2-k2-runs/run-01 --model k2-375b-nvfp4
```

Use the actual active model name; the ownership guard rejects a wrong name and never purges your general Hugging Face cache. Do not edit a submitted package: every stage checks its recorded code/lockfile/metadata fingerprint. Jobs do not requeue automatically. To replay an interrupted GPU stage before deleting its weights, its already committed task outputs are reused; resumed runs suppress fresh-throughput claims.

## Starting GPU parallelism

| Checkpoint | Exact weight-repository size | TP per replica | Replicas | Total B200s |
| --- | ---: | ---: | ---: | ---: |
| K2-Horizon-375B-A23B-NVFP4 | 229.64 GB | 4 | 1 | 4 |
| K2-Horizon-32B-NVFP4 | 23.28 GB | 1 | 2 | 2 |
| K2-Horizon-MoVA-36B-A4B-FP8 | 48.39 GB | 1 | 2 | 2 |
| K2-Horizon-7B-FP8 | 11.08 GB | 1 | 2 | 2 |

These are **educated guesses requiring hardware measurement**. One TP4 copy of the largest checkpoint preserves the same per-replica layout while requesting four GPUs rather than eight. Its 250G host-memory request retains loading headroom for the large checkpoint. Each smaller checkpoint fits one B200; replication avoids unnecessary TP collectives. MoVA's FP8 card specifically recommends TP1 or TP2 because of its 128-wide quantization blocks. Expert parallelism partitions experts within each TP group; it is not an additional GPU multiplier.

All profiles use native vLLM K2 execution, the checkpoint's own quantization configuration, BF16 for the unquantized tensors, 64k served context, prefix caching, 16 scheduler sequences per replica, and eight in-flight requests per replica. Patient tasks stay on the same replica for prefix reuse. Edit `configs/hipergator/k2.yaml` **before submission** to try, for example, TP8 × 1 for 375B or TP2 × 1 for MoVA. GPU requests and preflight derive their counts from TP × replicas, which must be between one and eight; adjust model `gpu_resources` when changing topology. No speculative decoding or automatic backend/precision fallback is enabled. Exact quantized checkpoint execution has not been tested by this project on B200s yet; preflight and server logs expose any unsupported kernel/load path.

Sources: [HiPerGator GPU access](https://docs.rc.ufl.edu/scheduler/gpu_access/), [vLLM 0.30.0 K2 native implementation](https://github.com/vllm-project/vllm/blob/v0.30.0/vllm/model_executor/models/k2_horizon.py), [IFM 375B NVFP4](https://huggingface.co/IFM/K2-Horizon-375B-A23B-NVFP4), [IFM 32B NVFP4](https://huggingface.co/IFM/K2-Horizon-32B-NVFP4), [IFM MoVA FP8](https://huggingface.co/IFM/K2-Horizon-MoVA-36B-A4B-FP8), [IFM 7B FP8](https://huggingface.co/IFM/K2-Horizon-7B-FP8).

## What is compared

This uses the **exact nine PMC full-text article records** from `medical-fidelity-v1`, including seven articles with **11 individual patient packets**, one no-patient article and one aggregate-only article. These are PMC full texts, rather than PubMed abstracts. Article versions, text, XML hashes, source licenses, rosters, patient IDs, packet text, original messages and all 197 gold checks are frozen and verified. The reference contains **161 required-fact checks and 36 forbidden-fact checks**. The old scores for Glimmer, Inkling, Gemma and Spark are included. The gold checklist is never supplied to the models.

Each checkpoint runs two distinct arms:

* **matched:** 176 exact previous initial prompt messages: 14 clinical domains plus summary and timeline for each of 11 patients. Prompt-only JSON, T=0/top_p=1, K2 low reasoning, 8,192 output tokens initially and 16,384 on one complete-regeneration retry. It uses the preserved historical clinical schemas, parser and validator. No new citation recovery, targeted repair or changed chunking strategy is mixed into this arm. Summary/timeline source checks use the packaged deterministic validator and frozen schemas. Provider reasoning settings are model specific, so this is a matched extraction protocol, not identical reasoning computation across vendors.
* **ifm_high / ifm_medium / ifm_low:** the same 176 messages at each supported reasoning level, all with T=1/top_p=.95 and 32,768 output tokens on both attempts. High is the publisher recommendation; medium and low measure the speed/quality tradeoff. Equal sampling and caps isolate the reasoning-level comparison. Each also runs the same secondary roster-discovery and text-based figure-attribution experiment after clinical scoring. Compare these arms separately from the historical matched arm, which uses different sampling and caps.

All four pinned chat templates explicitly support only `high`, `medium`, and `low`; unsupported levels raise an error. Pinned template hashes, supported levels and sampling-source URLs are recorded in each checkpoint metadata file and campaign model profile. CPU download verifies the template hash. Sources: [375B](https://huggingface.co/IFM/K2-Horizon-375B-A23B-NVFP4#api-usage), [32B](https://huggingface.co/IFM/K2-Horizon-32B-NVFP4#api-usage), [MoVA](https://huggingface.co/IFM/K2-Horizon-MoVA-36B-A4B-FP8#api-usage), [7B](https://huggingface.co/IFM/K2-Horizon-7B-FP8#api-usage).

The benchmark's schema is included in the prompts; decoding is **not** forced by JSON grammar. Invalid/truncated results, raw parseable facts, first-attempt validity, surviving delivery, forbidden facts and repair attempts are all retained. Exact tokenized prompt size is checked before each request; a context failure is recorded without truncating the article. Reasoning returned by vLLM is stored separately and is never used as clinical evidence.

For text-only models, patient bundles contain `model.capabilities.vision=false`, `vision.status="unsupported_by_model"`, and `pixels_inspected=false`. Pixel interpretation/observation fields are **absent**, rather than empty or fabricated. Authored image captions, figure URLs, supplements and attribution evidence are preserved. Secondary figure ownership supports individual, shared and panel assignments; valid assignments are written into each IFM arm patient bundles. Failed figure tasks remain unreviewed/missing instead of falling back to the reference assignments. Text/caption facts can still be extracted; their presence never implies the model saw image pixels. Figure identity and clinical truth still need semantic review.

## Results

`WORK/SUMMARY.md` and `WORK/summary.json` compare all four checkpoints and include historical checklist scores. Each model also writes `WORK/results/MODEL/throughput.json` immediately on completion, and its GPU log prints those numbers. The throughput table shows aggregate output tokens/s across all replicas, output tokens/GPU-second, GPU-stage elapsed seconds and complete-benchmark articles/hour. Per-arm token reports include total input/output tokens, aggregate input tokens/s and measured GPU count. Combined model rates divide summed token counts by summed evaluation time, including secondary calls when present; rates are never an average of per-replica or per-arm rates. Allocated-time output rate and articles/hour include startup for the full four-arm benchmark; queue waiting is excluded. Resumed runs and missing usage keep the affected rates unavailable. Each `WORK/results/MODEL/ARM/` contains patient JSON/JSONL bundles, every attempt (including errors/reasoning), raw versus delivered check results, domain-level scores, and pending masked factual-review forms. each IFM arm also has `secondary/` roster/figure outputs; structural attribution validity is reported separately from pending semantic accuracy.

The arm report provides per-article **mean, median and P95 input/output/reasoning tokens**, including retries and repeated article inputs across tasks/patients. It reports both all nine articles and the seven with patients: negative controls consume zero primary-extraction tokens, while their discovery calls are secondary. each IFM arm also reports secondary token totals and combined workflow statistics including discovery and figure ownership. Missing server usage is counted and stays unknown; complete-sample averages are unavailable if any article has missing usage, with reported-only statistics separated. Output tokens include reasoning when the server includes it; an absent reasoning-token breakdown is not recorded as zero. Latency, TTFT, aggregate output tokens/s and tokens/GPU-second are also included. This is a small quality pilot, not a saturating throughput benchmark; the largest model has only 11 patient streams feeding its replica.

`article-lengths.json` reports word mean/median/P95 and exact model-token mean/median/P95 for the **nine frozen articles**. This is a convenience sample, not a representative length survey of all eligible PMC articles. GPU utilization CSV, Prometheus snapshots, container SHA, engine/device versions and serving commands support later topology experiments. Throughput excludes startup, warmup and secondary tasks; separate GPU-stage duration includes most startup/evaluation work. Old hosted API timings cannot establish relative B200 throughput.

The gold checks cover selected facts, not all medically relevant claims. Good checklist scores do not establish comprehensive medical precision, recall, correct patient identity or vision accuracy. Review forms are intentionally pending rather than automatically marked clinician approved.

Regenerate the final report on a CPU node after weight deletion:

```bash
uv run --no-sync op2 hpg-benchmark report --work-dir /blue/cai5724/wkieffer/op2-k2-runs/run-01
```

Create a fresh portable archive from this repository with `uv run op2 hpg-benchmark package`. It uses an explicit file allowlist; `.env`, private keys, local caches and model weights are excluded.

Startup failures now retain the original exception, replica exit codes and log tails in `results/MODEL/servers/startup.json` and `gpu.json`. Full logs are `results/MODEL/servers/server-*.log`. A successful container probe checks dependencies and GPUs; checkpoint loading and inference still require successful server startup.
