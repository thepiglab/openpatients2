# Nemotron 3.5 Super and Muse Spark Open clinical extraction benchmark

Separate campaign from the existing Glimmer, GEPA and LoRA runs.
It never submits or cancels their jobs. Uses the existing dense reference,
live patient discovery, source-aware extraction/repair, literal evidence checks,
caption attribution, measurement-series extraction and unified scorer.
The obsolete Fastino Lightning launcher and model support have been removed;
the `fastino` configuration here is the new **Super VL Healthcare** checkpoint.
Muse Spark Open is included in the default campaign alongside all five
Fastino/Nemotron configurations.

## Run on HiPerGator

Commit/push this implementation and pull it into the HiPerGator checkout first.
The launcher freezes a **committed** Git snapshot, not the Mac's uncommitted files.
Start a new campaign after changing the selected models. Existing campaigns keep
their frozen model selection when resumed.

```bash
# From openpatients2 on HiPerGator, first run a small end-to-end hardware check:
DENSE_SUPER_LIMIT_ARTICLES=2 DENSE_HOURS=2 bash scripts/run_dense_nemotron_super.sh
bash scripts/run_dense_nemotron_super.sh status

# After the small campaign terminates, run the full comparison:
bash scripts/run_dense_nemotron_super.sh
bash scripts/run_dense_nemotron_super.sh status
bash scripts/run_dense_nemotron_super.sh verify
WORK="$(cat runs/dense-nemotron-super-last-work.txt)"
cat "$WORK/summary/comparison.md"
```

To stop the current campaign and start a fresh six-model smoke after updating:

```bash
bash scripts/run_dense_nemotron_super.sh cancel && \
  DENSE_SUPER_PROFILES='fastino nvfp4 bf16 lilicorr spark redhat-fp8-dspark' \
  DENSE_SUPER_LIMIT_ARTICLES=2 DENSE_HOURS=2 bash scripts/run_dense_nemotron_super.sh
```

`cancel` uses this checkout's last-campaign pointer, or an explicit campaign path
as its second argument. It cancels the recorded bootstrap first, re-reads the
submission ledgers, and cancels only recorded jobs still in your queue. It waits
up to 60 seconds for them to disappear; if they are still stopping, rerun `cancel`.
Other campaigns are untouched. Downloads and partial results are retained.

Default: six configurations × **217 train/validation articles = 1,302 article
runs**. The 43 test articles are preserved. Train and validation scores are
separate. This is a development comparison, not a blinded clinical validation.
Small checks choose the first article IDs deterministically; they are operational
smokes, not representative quality samples. Each arm also runs synthetic startup
checks outside article timing. No annotated roster, facts, figure labels or
supplement transcription enters model inference.

Muse Spark Open requires approved access to Meta's gated Hugging Face checkpoint.
Use the approved account on HiPerGator and make its credential
available through `HF_TOKEN` or the existing Hugging Face credential cache.
Website access approval and cluster authentication are both required. If needed,
run `"$WORK/runtime/client-venv/bin/hf" auth login` in the cluster checkout using
a read token from the approved account.
CPU setup checks access to **every selected checkpoint before any weight downloads**;
missing access fails setup and prevents dependent GPU jobs from starting.
Credentials are never written into campaign receipts or exports.

```bash
# Spark-only hardware smoke after access is approved:
DENSE_SUPER_PROFILES=spark DENSE_SUPER_LIMIT_ARTICLES=2 DENSE_HOURS=2 \
  bash scripts/run_dense_nemotron_super.sh
```

Optional separate vision arms (all six default text arms still run):

```bash
DENSE_SUPER_WITH_IMAGES=1 bash scripts/run_dense_nemotron_super.sh
# A smaller campaign can select configurations:
DENSE_SUPER_PROFILES='nvfp4 lilicorr' DENSE_SUPER_LIMIT_ARTICLES=2 \
  DENSE_HOURS=2 bash scripts/run_dense_nemotron_super.sh
```

Vision adds three NVIDIA arms, one Red Hat FP8 arm and one Muse Spark Open arm,
for **2,387 article runs** across eleven arms.
Fastino Super has no vision encoder and never receives pixels. CPU preparation freezes
eligible source image bytes and verifies acquisition hashes; missing/rights-review
images remain explicit. Vision arms run original figure/EHR/privacy stages too.
Supplement extraction remains unsupported. Compare text arms with each other and
vision arms with each other; their scopes and timings differ.

`DENSE_INCLUDE_TEST=1` explicitly opts into test evaluation. `DENSE_HOURS` defaults
to 12 **per server**, including startup; it is not an overall completion estimate.
Other launcher controls: `DENSE_ACCOUNT`, `DENSE_QOS`, `DENSE_REF`, or an absolute
output directory as the first positional argument.

## Publisher recipes and sampling

Checked October 15, 2026. Model SHAs, card/config hashes, source URLs and deviations
are tracked in `configs/hipergator/nemotron-super/provenance.json`; executable
settings are in `nemotron_super_recipes.py`, `muse_spark_recipe.py` and
`redhat_nemotron_recipe.py`.
Inspect without downloading models:

```bash
python3 scripts/benchmark_dense_nemotron_super.py recipe
```

| Configuration | Serving target | GPUs | Speculation | Engine sequences | Client concurrency | Host RAM |
|---|---|---:|---|---:|---:|---:|
| fastino | Fastino Super Healthcare BF16 | 2 B200 | Off | 32 | 32 | 200G |
| nvfp4 | NVIDIA Super VL NVFP4 | 1 B200 | Native MTP, 5 tokens | 512 | 64 | 96G |
| bf16 | NVIDIA Super VL BF16 | 2 B200 | Native MTP, 5 tokens | 32 | 32 | 200G |
| lilicorr | **Same NVIDIA NVFP4 target**, plus LiLiCorr draft checkpoint | 1 B200 | DFlash/LiLiCorr, 7 tokens | 1 | 1 | 96G |
| spark | Muse Spark Open 816B-A42B NVFP4 | 8 B200 | DSpark, 7 tokens | 32 | 32 | 200G |
| redhat-fp8-dspark | Red Hat Nemotron Super VL FP8 | 2 B200 | Red Hat DSpark, 8 tokens, draft TP1 | 128 | 64 | 200G |

LiLiCorr is a drafter paired with the NVIDIA NVFP4 target. It declares block size 8,
so the published seven-token draft is used. It is never attached to Fastino.
Native NVIDIA MTP does not download another drafter.

The four original Nemotron configurations and Red Hat FP8 use the
**exact NVIDIA official vLLM nightly image**:
`vllm/vllm-openai:nightly-3f1bcfd82bb2e93fcd364fc7a5491718453a77e2`.
The cards also list v0.32.0; this campaign pins the named nightly and records the
installed version, SIF hash, and relevant upstream Python source hashes. It does
not reuse the older project's vLLM 0.30.0/Glimmer container or speculative head.

Muse Spark Open uses its own official `vllm/vllm-openai:muse-spark-open` launch
image, pinned to the Linux AMD64 manifest digest
`sha256:2790bff92e9f2a796dcb65682dc5c2e9559f967a4b773f6ae4ca09dbd31c1081`.
Its image source-revision label currently points to an unavailable Git commit;
the OCI digest pins the runtime, and CPU setup records installed source hashes.
Each selected image must pass the live tokenization checks below. Red Hat
reports evaluations on vLLM 0.31.0 but its main serving commands do not pin a
container; this campaign reuses the NVIDIA nightly after inspecting its
Speculators adapter, Qwen3 DSpark model and Nemotron auxiliary-state support.

- **NVIDIA NVFP4:** follows the detailed **1× GB200** recipe: MTP5, async scheduling,
  FP8 linear backend `torch`, FULL_AND_PIECEWISE graph/quantization settings,
  FlashInfer Mamba, FP16 SSM cache, stochastic rounding/5 Philox rounds,
  shared-prefix checkpoints and align cache mode, prefix-match unit 16.
- **NVIDIA BF16:** follows the detailed **2× GB200** recipe with MTP5, async scheduling,
  32 sequences, shared-prefix checkpointing and native vision/tool parsers.
  The shorter quickstart instead shows MTP3; this campaign uses the detailed recipe.
- **LiLiCorr:** preserves the official **NVIDIA DGX Spark** c1/MTP-alternative recipe:
  DFlash7, 8,192 prefill tokens, 0.75 memory utilization, block/prefix units 64,
  and the published Mamba settings. This is explicitly a **B200 adaptation**;
  the card does not publish a B200-specific LiLiCorr recipe or guarantee its speed.
- **Red Hat FP8 + DSpark:** combines the explicitly paired compressed-tensors
  FP8 verifier and Red Hat five-layer DSpark drafter. Uses **8 speculative tokens,
  draft TP1 and greedy draft sampling**, standard rejection and adaptive
  verification **off**. The serving command uses 128 sequences, 16K prefill,
  0.89 memory utilization, prefix/block units 64, maximum CUDA graph capture 192,
  FlashInfer Mamba, FP16 cache, stochastic rounding/5 Philox rounds, align cache
  mode and TP/EP. The FP8 TP2 example documents two H100s; we allocate two B200s,
  cap client concurrency at 64 and context at 64K. Native pinned templates and
  `nemotron_v3`/`qwen3_coder` parsers apply. The drafter consumes auxiliary layers
  17/37/59/79/87; CPU setup verifies vLLM converts its generic `DSparkDraftModel`
  config to `Qwen3DSparkModel`. The target-only MTP example includes a fine-grained
  Mamba prefix flag absent from the DSpark command and pinned nightly; it is omitted.
  The card describes adaptive verification separately with experimental vLLM commit
  `92eb82dbb275d7eb214b65100faec54b14102e22`; this campaign follows its main
  serving recipe. Muse Spark's seven-token probabilistic/block/AV settings do not
  apply to this drafter. GPU fit and kernels still need a real B200 smoke.
- **Fastino Super:** follows its BF16/tensor-parallel quickstart, using TP2 as
  recommended for B200s. Speculation is off; this is the new Super checkpoint,
  not the retired Lightning model.
- **Muse Spark Open:** follows Meta's NVFP4 + DSpark7 command: expert parallelism,
  `muse` reasoning/tool parsers, automatic tool choice, Rust frontend, probabilistic
  draft sampling, block rejection sampling and adaptive verification. Meta's
  published deployment uses **four B300s**; this campaign uses **TP8 on eight B200s**
  for memory headroom, with 32 sequences, 32K prefill and two parallel loading
  workers. This adaptation still requires a real B200 smoke. The 816B-total /
  42B-active model requires at least 700 GB VRAM in NVFP4 according to Meta.
  Its approximately 1.63 TB BF16 weights exceed eight B200s' aggregate VRAM, so
  only NVFP4 is included.

All NVIDIA configurations use `nemotron_v3` reasoning and `qwen3_coder` tool
parsers, the native pinned chat template, and the publisher's media configuration
(one image/video per prompt, video pruning 0.5, 2 FPS/up to 256 frames). The clinical
benchmark does not submit video. Image bytes are passed as data URLs, so no broad
`--allowed-local-media-path=/` mount is needed. Models/remote processor Python are
downloaded at exact model SHAs; target and drafter stay offline on GPU jobs.

**Sampling:** NVIDIA text requests use **temperature 1.0 / top-p 0.95**;
requests actually containing pixels, including repairs and EHR calls, use
**0.6 / 0.95**. This follows the Model Summary's explicit recommendation, although
the cards' API image examples show 1.0. Fastino Super uses **0.0 / 0.95**, following
its quickstart's greedy example; its saved generation-config default is 1.0.
The five Nemotron profiles use native `enable_thinking=True`. Red Hat FP8 uses
its generation-config text defaults **1.0 / 0.95** and inherits the NVIDIA
parent's image recommendation **0.6 / 0.95**. Muse Spark Open
uses **temperature 1.0 / top-p 1.0** for text and images, native
`reasoning_effort='xhigh'`, and a frozen template date of `2026-10-15`. It stops
on `<|end_of_text|>` (200001) and `<|eot|>` (200008), never `<|eom|>`.
All use seed 42 and **no inherited Glimmer top-k 64**.
`--generation-config vllm` prevents checkpoint defaults from overriding the
explicit per-request sampling. Tokenization uses the identical native template
kwargs. Schemas are prompt instructions plus local validators, not constrained
generation.

Project-specific overrides are explicit: **65,536 context**, 16,384 output/repair
tokens, 32,768 prefill tokens on BF16/Fastino/Muse Spark Open, 0.90 GPU utilization
on NVIDIA MTP/Muse Spark Open profiles, 0.89 on Red Hat FP8 and 0.85 on Fastino, loopback ports and
offline local snapshots.
The official models support larger contexts; this comparison keeps the established
source/output budget. Overflows fail visibly without source truncation. B200
replaces GB200; no B200 performance is claimed before a real run.

Sources:
[Fastino Super](https://huggingface.co/fastino/Fastino-Nemotron-3.5-Super-VL-Healthcare),
[NVFP4 detailed vLLM](https://huggingface.co/nvidia/NVIDIA-Nemotron-3.5-Super-VL-120B-A12B-NVFP4#vllm),
[BF16 detailed vLLM](https://huggingface.co/nvidia/NVIDIA-Nemotron-3.5-Super-VL-120B-A12B-BF16#vllm),
[LiLiCorr](https://huggingface.co/nvidia/NVIDIA-Nemotron-3.5-Super-VL-120B-A12B-NVFP4-LiLiCorr),
[Meta's official vLLM deployment](https://dev.meta.ai/docs/muse-spark-open/deploy#vllm),
[Meta prompting and sampling](https://dev.meta.ai/docs/muse-spark-open/prompting),
[Muse Spark Open NVFP4](https://huggingface.co/meta-models/Muse-Spark-Open-816B-A42B-NVFP4),
[Muse Spark DSpark drafter](https://huggingface.co/Inferact/Muse-Spark-Open-DSpark),
[Red Hat FP8 verifier](https://huggingface.co/RedHatAI/NVIDIA-Nemotron-3.5-Super-VL-120B-A12B-FP8),
[Red Hat Nemotron DSpark serving recipe](https://huggingface.co/RedHatAI/NVIDIA-Nemotron-3.5-Super-VL-120B-A12B.dspark#vllm-serving),
[pinned vLLM tokenizer implementation](https://github.com/vllm-project/vllm/blob/3f1bcfd82bb2e93fcd364fc7a5491718453a77e2/vllm/entrypoints/serve/tokenize/serving.py).

## Resources, validation and recovery

CPU preparation/access checks → **for each selected profile: CPU download → GPU
benchmark → CPU cleanup** → CPU aggregation/export. Downloads use `hpg-default`,
eight CPUs, **128G RAM**, up to eight hours, and **no GPUs**. Setup, cleanup and
aggregation request eight CPUs and 32G RAM.
All six default profiles remain selected. Text and optional vision arms for a
profile share its download; cleanup waits for both arms to terminate.
At most **eight B200s, 16 CPUs and 200G host RAM** for this campaign at once.
Only Muse Spark Open requests eight GPUs; the Nemotron profiles request at most
two GPUs and eight CPUs. Other users'
jobs and account CPU/RAM/GPU limits still affect scheduling. GPU stages require
successful CPU access checks and their own CPU download/probe job. Cleanup runs
after the profile's download and GPU jobs terminate, including failures. The next
profile downloads only after cleanup succeeds; a cleanup failure blocks further
large downloads. Independent model failures allow later profiles and diagnostics
to continue once cleanup succeeds. Jobs remain held until the whole dependency graph is accepted.
Submission failure cancels only IDs created by that submission.

Storage now holds **one target plus its drafter and serving image at a time**,
rather than every model. Budget roughly **650–750 GB free** for the largest profile
(Muse Spark Open), its drafter/container, and working space, plus inputs/results.
Actual free space and image sizes still determine whether it fits. The CPU
download job builds the container first and removes its OCI cache/conversion
scratch before downloading model weights, reducing peak space. Image builds use
`apptainer build --mksquashfs-args '-processors 8 -mem 8G'`: compression must not
size its RAM buffers from the full node's memory instead of the Slurm allocation.
Apptainer 1.4+ with this build option is required; unsupported versions fail
before fetching the large image or weights. A temporary image is checked with
`apptainer sif list` and published only after successful conversion; failed or
interrupted temporary images are removed by cleanup. Cleanup removes
the completed profile's target/drafter, image, download caches and arm compilation
caches; outputs, citations, returned reasoning, pinned revisions, weight hashes
and CPU probe receipts remain. NVFP4 is downloaded again for LiLiCorr rather than
kept resident through another profile. Downloads, full hashing, cleanup, scoring
and compression run on CPU allocations. No inference or installation runs on a
login node. The launcher needs existing `uv`, Apptainer 1.4+ and required Hugging Face
access. No packages are installed globally.

CPU preflight verifies pinned configurations/templates and native reasoning modes
in each model's own image. Nemotron additionally
checks the exact upstream tokenization/registry source hashes. Muse Spark Open's
gated config, template, generation config and tokenizer file are pinned to public
Git blob identities and checked after authorized download; each download job confirms the
target architecture is registered in its official image.
On each GPU allocation, a probe requires the expected count of B200-class GPUs.
It also checks published serving flags with `vllm serve --help=all`, using the
same container, `--nv`, and GPU visibility as the server. The pinned CUDA builds
construct a device configuration even for CLI help, so this check cannot run on
a CPU node. Its stdout/stderr are retained in the worker's `probe.json`.
The server must then pass synthetic text (and optional image) requests whose
`/tokenize` count exactly equals generation's `usage.prompt_tokens`. This checks
the actual multimodal expansion path before article inference. Smoke time/output
is separate; article latency therefore follows a small synthetic warmup.
The Muse Spark Open synthetic smoke uses `low` reasoning effort for speed;
article extraction and repair use the manifest's `xhigh` setting.
These checks can reject an incompatible runtime; local tests do not establish
GPU kernel compatibility or memory fit.

Aggregation always exports diagnostic results, including incomplete runs, but
the aggregate CLI/Slurm job exits unsuccessfully when articles remain pending.
An archive's existence or checksum only establishes that results were packaged,
not that inference succeeded. The comparison table shows completed/planned
articles and reports quality as **not measured** when no attempts completed.
The JSON retains full-reference missing-prediction penalties for accounting,
with `quality_measured: false` distinguishing those from measured quality.

Each article has a 30-minute operational timeout; a worker stops taking articles
near its allocation deadline. Operational failures/pending articles are retained
for explicit resume; completed invalid model outputs are scored and never rerolled.

```bash
# Wait for every previous campaign job to terminate; investigate logs first.
bash scripts/run_dense_nemotron_super.sh resume
# Inspect a concrete prepared submission without queueing it:
python3 "$WORK/code/scripts/benchmark_dense_nemotron_super.py" submit --work "$WORK" --dry-run
```

Resume skips completed articles/configurations and schedules CPU downloads only
for profiles with pending articles. Retained partial downloads can resume when
available; cleaned weights are downloaded again. A canceled cleanup is recovered
before a different profile downloads. A campaign runtime lock prevents download,
cleanup and inference from using its weights concurrently. A failed access setup
is rerun if no ready root runtime receipt exists. Change code, recipe, sampling,
storage policy or input selection by preparing a **new** campaign. Old campaigns
continue to use their frozen all-model download code when resumed.

To reclaim the previous campaign's downloaded weights before starting a new one,
first cancel it, then run the current checkout's cleanup command on a **CPU** job.
This keeps its frozen inputs/code, results and reasoning. It also supports older
campaigns; their deleted weights cannot be reused by resume.

```bash
bash scripts/run_dense_nemotron_super.sh cancel &&
sbatch --wait --account="${DENSE_ACCOUNT:-cai5724}" --qos="${DENSE_QOS:-cai5724}" \
  --partition=hpg-default --nodes=1 --ntasks=1 --cpus-per-task=1 --mem=2G \
  --time=01:00:00 --job-name=op2-super-clean-old \
  --wrap='python3 scripts/benchmark_dense_nemotron_super.py cleanup --work "$(cat runs/dense-nemotron-super-last-work.txt)"' &&
DENSE_SUPER_LIMIT_ARTICLES=2 DENSE_HOURS=2 bash scripts/run_dense_nemotron_super.sh
```

Export `HF_TOKEN` (for example `set -a; source .env; set +a`) before launching the
new campaign. Commit/push/pull the updated implementation first. Do not resume an
old campaign to get the new download policy.

## Scores and export

`summary/comparison.md` and `.json` separate text/vision and train/validation/test.
They report clinical precision/recall/F1/**F2**, attribute accuracy, roster recall,
per-article latency and completed article GPU seconds (wall seconds × GPU count).
The comparison also displays **output tok/s, answer tok/s, doc/s, doc/hour and
output tok/GPU-second** for each split and scope. `comparison.json` includes the
same rates on the identical completed-article intersection across models.
Every arm also keeps all existing unified scores, raw attempts, task validity,
reasoning/token usage, per-article CSV, before/after engine counters, startup and
GPU telemetry. Measured worker GPU seconds include probes/startup/retries; use
`sacct` for exact queue/allocation costs, especially after hard timeouts.

Throughput is **sum of completion tokens / sum of completed-article wall time**,
including discovery, concurrent task calls, repairs, validation and EHR stages.
It is never an average of individual request rates. Completion tokens already
include reasoning tokens; they are not added a second time. Answer-only tok/s is
available only when every call reports a valid reasoning-token breakdown. Missing
usage stays unavailable rather than zero. Doc/s counts completed article attempts,
including completed invalid outputs; prediction-document and valid-extraction
document rates are reported separately in `report.json`. Schema/literal validity
does not establish semantic correctness.

Each arm's `summary/report.json` also reports worker goodput: completed documents
per observed server second, including startup, probes, operational failures,
retries and cleanup. Missing worker receipts make these rates unavailable;
`sacct` is required for complete allocation costs. With one article active per
server, these are throughput measurements of this clinical pipeline. A separate
cross-document concurrency sweep would be needed to estimate saturated serving
capacity. Judge/scoring time is outside extraction throughput.

Full scores include all planned references, treating missing predictions as
misses. Matched completed article scores and timing use identical IDs within each
scope; partial-run matched subsets may still be biased. Different GPU counts,
sampling and concurrency make this a **publisher-configuration comparison**, not
a controlled quantization/speculation ablation or a maximum throughput test.

Export includes frozen code/inputs/references, manifests, diagnostics, predictions,
metrics and integrity receipts. It excludes all model weights, caches, SIFs,
environments and credentials. Verification requires full execution coverage and
intact scores; it does not mean every model output passed clinical validation.

### Reasoning and fact lineage

Endpoint-returned reasoning is saved by default in every initial/repair call's
`response.reasoning_text` and `response.reasoning_fields` in
`results/articles/<article>/attempt-*/extraction/attempts.jsonl`; EHR calls use
`ehr/calls/attempts.jsonl`. Requests, generation settings, pinned model identity,
response IDs, token usage, raw answer, parsed candidates, and validation errors
are retained. A reasoning field that is missing, empty, opaque, or disabled has
an explicit status. Raw answer content remains saved even when the endpoint
does not provide a separate reasoning field.

Fact review rows and clinical fact envelopes now link to their same-patient,
same-task initial and repair calls through ledger sequence IDs. CPU aggregation
exports `summary/fact-reasoning.jsonl`: extracted element, source citation/spans,
split, model revision, and ledger links together. These are **task-level traces**:
one response can produce many facts, and a repair can retain earlier facts.
No fact-specific rationale or causal attribution is invented. The index covers
clinical facts and summary/timeline review objects; vision/EHR traces remain in
their original ledgers. Reasoning-capture counts appear in `report.json`.

This index is a research artifact, not an automatically accepted training set.
For a distillation experiment, use only training articles, retain source-level
split boundaries, and filter for complete outputs, source grounding, semantic
correctness and correct patient attribution. Keep validation/test traces out of
training. Compare answer-only supervision, full returned reasoning plus answer,
and separately generated concise evidence explanations under the same student
inference budget. Measure quality and document throughput after training: longer
student reasoning can erase serving gains, and the student's speculative drafter
must be rechecked after the student changes. Reformat into each student's own
native template rather than copying teacher-specific control tokens.

### Semantic judging proposal (not enabled)

The current scorer uses one-to-one evidence-overlap/name matching with separate
attribute checks; it is not exact-string matching. It can miss synonyms, and
shared source spans can align different claims. Item F1 measures item detection:
wrong assertion/value/time can still be an item TP and an attribute error.
Both measures remain visible in the comparison. Current references are AI
annotations, so agreement is not physician-verified accuracy.

Add an independently calibrated small judge as a **separate semantic score**:

1. Freeze a judge checkpoint, prompt, sampling and rubric across every model.
   First audit paraphrases and hard negatives (negation, certainty, value/unit,
   patient, time, specimen and laterality) on a manually adjudicated calibration
   set drawn from training articles. Judge confidence alone is not validation.
2. Give it source passages and patient context, extracted structured claims and
   reference claims. Hide generating-model identities and all extractor reasoning.
   Treat article text and predictions as data, never judge instructions.
3. Judge both source entailment and reference equivalence. Preserve distinct
   equivalent, conflicting, related-but-different, unsupported and uncertain
   outcomes, with cited evidence and a short decision explanation. Correct
   source-supported facts absent from the reference are annotation-gap candidates.
4. Use high-recall candidate retrieval and one-to-one matching on a common atomic
   claim representation. Audit accepted deterministic matches as well as apparent
   misses; only reviewing misses leaves overly generous matches uncorrected.
   Count duplicate predictions once, preserve strict attribute errors, and report
   abstentions and unjudged coverage. An uncertain judge result is not a free TP.
5. Report deterministic and semantic precision/recall/F1/F2 side by side, with
   per-field errors and judge/human agreement. Cache decisions by source, claims,
   judge revision and prompt hash; retain raw decisions and judge cost separately.
   Escalate ambiguous/high-impact cases to a stronger judge or human review.

LLM judges have documented position, verbosity and self-preference biases; a
small judge should earn its role through calibration rather than automatically
replace the existing score. See [Zheng et al., 2023](https://arxiv.org/abs/2306.05685).

On your Mac, substitute the exact remote WORK printed by the launcher:

```bash
scp wkieffer@hpg.rc.ufl.edu:REMOTE_WORK/results.tar.gz ~/Downloads/nemotron-super-results.tar.gz
scp wkieffer@hpg.rc.ufl.edu:REMOTE_WORK/results.tar.gz.sha256 ~/Downloads/nemotron-super-results.tar.gz.sha256
# The checksum names results.tar.gz; verify the renamed local file explicitly:
cd ~/Downloads
expected="$(awk '{print $1}' nemotron-super-results.tar.gz.sha256)"
actual="$(shasum -a 256 nemotron-super-results.tar.gz | awk '{print $1}')"
test "$expected" = "$actual"
```
