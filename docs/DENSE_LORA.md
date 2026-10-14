# Glimmer supervised LoRA pilot on HiPerGator

Separate campaign from GEPA and Fastino. Does not launch/cancel either campaign.
No annotation edits, Hub uploads, full-model checkpoints, constrained decoding,
QLoRA or RL training. This tests whether supervised adapters are worth a larger
extraction benchmark; it does not claim improved clinical performance.

## Launch and W&B

Commit/push the changes yourself, then pull into the existing HPG checkout.
Create the private configuration **on HiPerGator**, since Git ignores `.env`:

```bash
cp -n .env.example .env
chmod 600 .env
nano .env
bash scripts/run_dense_lora.sh
```

Set `WANDB_API_KEY`, optionally `WANDB_ENTITY`, and `WANDB_PROJECT`. Never paste the
key into a command, chat, or tracked file. `WANDB_MODE=auto` uses online tracking
when a key is supplied, offline otherwise; `offline` and `disabled` are supported.
This loader accepts simple KEY=value entries and quoted values, not shell code,
variable substitution or multiline secrets. Explicit exported W&B variables take
precedence. `LORA_ENV_FILE=/absolute/private/file` selects a different file.
Preparation copies only these four W&B values into private
`runtime/wandb.env` (0600). Neither `.env` nor runtime credentials enter exports.

Each smoke/training run logs completion loss, validation loss, gradient norm,
step time, peak GPU memory, generation totals and W&B system telemetry. The CPU
comparison logs F2, attribute-aware F2, precision, recall and attribute accuracy.
Runs share a campaign group. Sources, targets, prompts, generated clinical text,
model weights and API keys are not logged to W&B. Local raw results remain in the
private campaign/results archive. No W&B model artifact uploads are performed.

Offline W&B files live in `results/*/tracking/wandb/offline-run-*` and are included
in the result archive. After downloading, in an environment with the W&B CLI:
`wandb login`, then `wandb sync /path/to/offline-run-*` for each run. Failed CPU
comparison logging writes `summary/tracking-error.json`; scientific results still
export. Online authentication/network failure during training is recorded as a
failed trial, so use offline mode if compute-node internet is unavailable.
This logging integration applies to the new LoRA campaign, not an already
running GEPA campaign.

## Scheduling

1. CPU preparation: 8 CPUs / 32G / 2h. Freeze committed code, original reference,
   patient packets and training split; audit complete gold targets.
2. CPU setup: 4 CPUs / 32G / 4h. Download the pinned BF16 checkpoint and container,
   install pinned PEFT/Accelerate/W&B additions without replacing the container's
   torch/transformers stack; tokenize, audit lengths, preflight W&B, save resolved versions.
3. GPU smoke: **one B200 / 6 CPUs / 64G host RAM / 2h**. Largest eligible training
   example forward/backward/update; finite adapter gradients/weights, save/reload
   and generation. Also generate one matched BF16 baseline on eight diagnostic
   patient/task examples. No full-corpus Glimmer rerun.
4. Four independent GPU trials: rank 8 or 32 × learning rate 5e-5 or 1e-4,
   two epochs, seed 42. **Two concurrent by default**, each requesting one B200,
   6 CPUs and 64G host RAM, with an **8h cap per trial**. Early completion releases
   the GPU immediately. This is two waves, potentially 16h plus smoke/setup/queue;
   not a promise that the entire campaign finishes in eight hours.
5. CPU comparison/export: 4 CPUs / 32G / 4h, after every GPU job terminates,
   including failed jobs. Score generations, verify archive, clean up private base.

With GEPA's 8 CPUs / 60G running, two LoRA trials total 20 CPUs / 188G / three
GPUs. The reported 32CPU / 250G group limit, and other users' usage, still apply.
Seven free GPUs alone do not imply enough host memory. Tune deliberately:

```bash
LORA_HOURS=6 LORA_CONCURRENCY=2 bash scripts/run_dense_lora.sh
# Only with enough group CPU/RAM quota: four workers request 24 CPU / 256G.
# LORA_CONCURRENCY=4 bash scripts/run_dense_lora.sh
```

The complete dependency graph is held until submission succeeds. A submission
failure cancels only jobs just created by this launch. GPU jobs require successful setup/smoke with `afterok` and
`--kill-on-invalid-dep=yes`, so a failed smoke cancels its waiting trials without
allocating GPUs. Lane turnover and CPU export use `afterany` so independent trial
failures still allow the remaining experiments and diagnostics to finish. See
[Slurm dependency behavior](https://slurm.schedmd.com/sbatch.html).
Duplicate-launch protection applies to this campaign only. Never run models on
login nodes. CPU data/download/scoring/compression do not reserve GPUs. Validation
loss and short generation probes run on GPU because they need the model.

## Training contract and interpretation

BF16 base: `meta-models/Muse-Glimmer-30B` at
`a4e59da52a7bc87ae7251dd5545c0dd437c44b68`, native audited ATEM chat template,
fixed template date and medium reasoning. No invented reasoning traces: supervise
only the assistant routing header, answer and end-of-turn token. Token offsets
mask the entire input and any token straddling the completion boundary.

PEFT adapters target only text-transformer attention/MLP projections, never vision,
embeddings or output head. Microbatch one, accumulation eight, gradient
checkpointing, BF16 base with standard PEFT adapter precision, SDPA attention,
AdamW, constant LR, alpha twice rank, dropout .05. GPU smoke validates compatibility
on B200; local unit tests cannot prove this model/container actually trains.
The existing FP8/DFlash inference artifact is not the training base. No speculative
decoding in training/probes; DFlash compatibility and speed must be tested later.

Training uses 126 articles and inner validation uses 32 from the existing training
partition. The 59 confirmation articles and 43 test articles never train the model.
Gold examples with invalid evidence/schema or review-only facts are rejected as
whole examples, preserving annotations. Before length filtering, the audited source
has 374 train and 91 inner-validation patient/task examples; counts are recorded
again on every run. Only observations and medications are trained.

Default maximum is 16,384 tokens (prompt plus full answer). Oversized examples are
excluded with an audit entry, **never truncated**. `LORA_MAX_LENGTH` may be 2048–32768;
the smoke uses the longest retained training sequence. Reports must accompany any
claim with exclusion counts. Train/validation remain article-disjoint.

Every epoch saves an adapter; `best` is selected by inner-validation completion
loss. Eight fixed inner-validation patient/task examples (four per task) give an
exploratory generation diagnostic, with identical sources for base and all adapters.
No beam search, sampling, structured-output enforcement or clinical repair in this
probe. ATEM self-channel text is excluded; incomplete final answers, invalid schema
or unsupported evidence are failures and remain misses in metric denominators.
Reports include F2, attribute-aware F2, precision/recall, critical-attribute counts,
valid delivery and seconds per **patient/task**, not whole-article latency.
These eight cases are too few to pick a clinical winner reliably. Next, benchmark
promising adapters on the full confirmation set with the production extraction
pipeline, preserving the test set. Training loss improvement alone is insufficient.

## Results, retention and recovery

```bash
bash scripts/run_dense_lora.sh status
WORK="$(cat runs/dense-lora-last-work.txt)"
cat "$WORK/summary/report.md"
bash scripts/run_dense_lora.sh verify
ls -lh "$WORK/results.tar.gz"*
printf 'scp wkieffer@hpg.rc.ufl.edu:%s/results.tar.gz\\* ~/Downloads/\n' "$WORK"
```

Run the printed SCP command on your Mac; use a separate Downloads subfolder if
retaining several archives called `results.tar.gz`. The CPU job creates the archive
automatically. It contains code, split/reference data, tokenizer, adapter checkpoints,
metrics, generations, finished job logs and W&B offline files. It excludes the
container, runtime environment, credentials and base weights. Archives contain
source/reference data and should retain the same access controls as the campaign.

Adapters remain at `$WORK/adapters/<trial>/best/`, plus each epoch and smoke adapter.
Reload using the pinned BF16 base and `$WORK/tokenizer`; `adapter_config.json` and
`provenance.json` record the model revision. No merged model is saved. Keep both
adapter files and tokenizer/provenance for reproducibility.

Only `$WORK/runtime/base` is deleted, on CPU, after **all** trials complete,
on-disk adapters reload/generate, and the result archive's contents/hashes verify.
A runtime ownership marker, adapter hashes, symlink refusal and active-reader file
lock protect deletion. Shared caches, GEPA/Fastino runtimes and adapters are untouched.
A failed/incomplete campaign retains its base for investigation. The private SIF,
training environment and small download metadata remain; cleanup is not a global
cache purge. `summary/cleanup.json` and `archive-verified.json` are post-export
receipts on HPG and intentionally excluded from the archive's file inventory.
`verify` requires the complete campaign and intact results/archive.

Training timeouts/signals preserve completed epoch adapters and diagnostics, but do
not advertise a partial run as complete. There is no optimizer-state resume yet;
launch a new campaign after diagnosing a failed recipe. Do not rerun train into an
existing trial directory.

Sources: [Glimmer model-specific fine-tuning guidance](https://huggingface.co/blog/muse-glimmer#fine-tuning-with-trl),
[PEFT custom training loops](https://huggingface.co/docs/peft/quicktour),
[W&B settings implementation](https://github.com/wandb/wandb/blob/main/wandb/sdk/wandb_settings.py).
