# Nemotron Super, Muse Spark Open and Reflection Beam clinical extraction benchmark

Separate campaign from the existing Glimmer, GEPA and LoRA runs.
It never submits or cancels their jobs. Uses the existing dense reference,
live patient discovery, source-aware extraction/repair, literal evidence checks,
caption attribution, measurement-series extraction and unified scorer.
The obsolete Fastino Lightning launcher and model support have been removed;
the `fastino` configuration here is the new **Super VL Healthcare** checkpoint.
The default campaign now focuses on four candidates: NVIDIA NVFP4 + MTP5,
NVIDIA BF16 + MTP5, Muse Spark Open + DSpark7, and Beam NVFP4 + DFlash2.
Fastino, LiLiCorr, RedHat DSpark and Beam FP8 remain explicit diagnostic options.
Beam BF16 is excluded.
Each configuration runs **once per article** using its supported modalities:
three default configurations receive text and images together; Beam NVFP4
receives text only. There are no duplicate text-versus-image runs.

## Run on HiPerGator

Commit/push this implementation and pull it into the HiPerGator checkout first.
The launcher freezes a **committed** Git snapshot, not the Mac's uncommitted files.
Start a new campaign after changing the selected models. Existing campaigns keep
their frozen model selection when resumed.

```bash
# From openpatients2 on HiPerGator, first run a small end-to-end hardware check:
DENSE_SUPER_LIMIT_ARTICLES=2 DENSE_HOURS=4 bash scripts/run_dense_nemotron_super.sh
bash scripts/run_dense_nemotron_super.sh status

# After the small campaign passes, run a representative development sample:
DENSE_SUPER_PROFILES='nvfp4 bf16 spark beam-nvfp4-dflash2' \
DENSE_SUPER_SPLITS=train DENSE_SUPER_ARTICLE_IDS='' \
DENSE_SUPER_LIMIT_ARTICLES=48 DENSE_HOURS=48 bash scripts/run_dense_nemotron_super.sh
bash scripts/run_dense_nemotron_super.sh status
bash scripts/run_dense_nemotron_super.sh verify
WORK="$(cat runs/dense-nemotron-super-last-work.txt)"
cat "$WORK/summary/comparison.md"
```

To stop the current campaign and start a fresh four-configuration smoke after updating:

```bash
bash scripts/run_dense_nemotron_super.sh cancel && \
  DENSE_SUPER_PROFILES='' \
  DENSE_SUPER_LIMIT_ARTICLES=2 DENSE_HOURS=4 bash scripts/run_dense_nemotron_super.sh
```

`cancel` uses this checkout's last-campaign pointer, or an explicit campaign path
as its second argument. It cancels the recorded bootstrap first, re-reads the
submission ledgers, and cancels only recorded jobs still in your queue. It waits
up to 60 seconds for them to disappear; if they are still stopping, rerun `cancel`.
Other campaigns are untouched. Downloads and partial results are retained.

The committed reference now contains **526 completed articles: 316 train, 105
validation, and 105 test**. The default split is **train only**: without a limit,
four configurations × 316 articles = 1,264 article runs. Use a small development
sample first; a full run can exceed one allocation's time limit and require resume.
Validation and test remain separate explicit selections. This is a development
comparison, not a blinded clinical validation.
Limited samples take round-robin turns across split/category buckets using the
existing reference primary_stratum labels. Within each bucket they favor
underrepresented figure counts, tables, source lengths, reference patient counts,
and species, with SHA256 seed-42 tie-breaking. Category and reference metadata
are used only for selection/reporting, never passed into inference prompts.
This intentionally balanced diagnostic distribution is not population prevalence.
The tracked development-sample-48.json documents the current 48-article selection
and reference checksum: all 36 training category labels, 20 single-patient, 26
multi-patient and two no-patient references; 38 articles with figures and 27 with
tables. Categories include overlapping specialties, age groups and modalities.
Some categories have only one or two sampled articles: use them to locate failure
cases, not to claim statistically reliable category rankings. The full eligibility
set remains train-only; no validation or test labels guide this selection.
Changing the reference snapshot changes the preview: regenerate it when refreshing
the data. summary/cohorts.json and cohorts.md report clinical category, length,
figures, tables, patient-count and species groups separately for each split and
model, preserving failed/missing predictions in planned denominators. They also
include completion, prediction, fully-valid and task-status counts.
A 48-article run means 192 article attempts across the four models. DENSE_HOURS=48
is the per-model allocation limit, not a promise of completion; longer cases may
need resume. The worker reserves a full article timeout before starting more work. A two-article sample is still only an
operational smoke. Each arm also runs synthetic startup
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
DENSE_SUPER_PROFILES=spark DENSE_SUPER_LIMIT_ARTICLES=2 DENSE_HOURS=4 \
  bash scripts/run_dense_nemotron_super.sh
```

Images are enabled by default for models that support them:

```bash
# Explicitly request the default supported-modality behavior:
DENSE_SUPER_WITH_IMAGES=1 bash scripts/run_dense_nemotron_super.sh
# An optional text-only comparison, still one run per configuration:
DENSE_SUPER_WITH_IMAGES=0 bash scripts/run_dense_nemotron_super.sh
# A smaller campaign can select configurations:
DENSE_SUPER_SCHEDULER=serial \
DENSE_SUPER_PROFILES='nvfp4 lilicorr' DENSE_SUPER_LIMIT_ARTICLES=2 \
  DENSE_HOURS=4 bash scripts/run_dense_nemotron_super.sh
```

The four default jobs are `nvfp4-vision`, `bf16-vision`, `spark-vision`, and
`beam-nvfp4-dflash2-text`. The other recipes require explicit DENSE_SUPER_PROFILES. The `-vision` suffix means text and image pixels are
processed together through the full pipeline. It is not an image-only job.
Fastino Super and Beam are text-only and never receive pixels. CPU preparation freezes
eligible source image bytes and verifies acquisition hashes; missing/rights-review
images remain explicit. Vision arms run original figure/EHR/privacy stages too.
Supplement extraction remains unsupported. Reports label modality groups because
their figure/EHR scopes and timings differ; these labels do not create extra jobs.
Direct CLI preparation also defaults to images where supported; `prepare --text-only`
explicitly disables pixels, and `prepare --with-images` retains the default behavior.

`DENSE_SUPER_SPLITS='validation'` selects validation; test requires BOTH
`DENSE_SUPER_SPLITS='test'` and `DENSE_INCLUDE_TEST=1`. Merely authorizing test does
not add it to training runs. Split scores remain separate when multiple splits
are explicitly selected. `DENSE_HOURS` defaults
to 12 **per server**, including startup; it is not an overall completion estimate.
Other launcher controls: `DENSE_ACCOUNT`, `DENSE_QOS`, `DENSE_REF`, or an absolute
output directory as the first positional argument.

The launcher uses `configs/hipergator/nemotron-super/reference.jsonl.gz`, with its
checksum and split counts in `reference-manifest.json`. It never silently falls
back to the older 260-article packet used by other campaigns. Refresh after a
reviewed release changes with `python3 scripts/freeze_super_reference.py`, then
commit both files. `DENSE_SUPER_EXPECTED` optionally asserts an exact completed
snapshot count; omitting it uses the manifest's count. Existing campaigns retain
their frozen references. Preparation checks source hashes against the reference.

`DENSE_SUPER_ARTICLE_IDS` selects exact IDs from the requested splits, mutually
exclusive with `DENSE_SUPER_LIMIT_ARTICLES`. To repeat the last regression cases:

```bash
DENSE_SUPER_LIMIT_ARTICLES='' DENSE_SUPER_SPLITS=train \
DENSE_SUPER_ARTICLE_IDS='PMC10998798.1 PMC12134816.1' DENSE_HOURS=4 \
  bash scripts/run_dense_nemotron_super.sh
```

Unset `DENSE_SUPER_ARTICLE_IDS` before a stratified sample. Override its seed with
`DENSE_SUPER_SELECTION_SEED`. New Super campaigns allow up to 48 frozen figures
per article (one new training article has 13); each request still follows the
publisher's per-prompt image limit. Missing or rights-restricted assets stay
explicit. Full source context remains intact; very long new articles can still
fail the exact-token context check instead of being silently shortened.

## Changes following the October 17 run

Timelines now use source-segment batches for event extraction, followed by batches
of focal events for relation linking. Each request sees the full source. Events
are assigned deterministic IDs, and cross-batch relations can reference the full
event registry. Only exact duplicate event content is collapsed; no semantic
merging is assumed. Combined contradictions quarantine relations and make the
assembled timeline partial rather than choosing a convenient chronology.

Repairs of these stages return only invalid stable-ID atoms. Accepted neighbors
stay local and cannot be rewritten by the repair response. Failed batches remain
visible in stage coverage and the final timeline status. Original and repair
reasoning stay in the ordinary ledgers and final fact lineage. These changes need
a fresh hardware smoke; earlier quality/throughput scores do not validate them.

Measurement-series instructions now explicitly require a unit and basis even
for baseline-zero offsets; absent timing remains null. Ownership citations use
exactly one segment or cell. Existing validators remain strict.

To diagnose RedHat's low draft acceptance, use the optional controlled baseline:

```bash
DENSE_SUPER_SCHEDULER=serial \
DENSE_SUPER_PROFILES='redhat-fp8-dspark redhat-fp8-no-spec' \
DENSE_SUPER_SPLITS=train DENSE_SUPER_LIMIT_ARTICLES=2 DENSE_HOURS=4 \
  bash scripts/run_dense_nemotron_super.sh
```

Both arms use the same target checkpoint, quantization override, GPU topology,
sampling, client concurrency and server settings. The second omits the drafter
and speculative config. It is an explicit diagnostic experiment, excluded from
the default four configurations. The existing serial CPU download/cleanup
lifecycle is retained. New article receipts capture speculative counter snapshots
and acceptance deltas; missing, reset or unsupported counters are unavailable,
not zero. The counters follow [vLLM's speculative-decoding metric definitions](https://docs.vllm.ai/en/v0.20.2/api/vllm/v1/spec_decode/metrics/).
The two arms' output lengths may differ, so also compare document and
answer-token throughput rather than only total generated tokens.

Offline semantic judging is documented in [SEMANTIC_BENCHMARK.md](SEMANTIC_BENCHMARK.md).
It operates on saved outputs, preserves the original scores and archive, and
requires an explicitly selected judge endpoint/model/revision and calibration.

## Publisher recipes and sampling

Checked October 15–16, 2026. Model SHAs, card/config hashes, source URLs and deviations
are tracked in `configs/hipergator/nemotron-super/provenance.json`; executable
settings are in `nemotron_super_recipes.py`, `muse_spark_recipe.py` and
`redhat_nemotron_recipe.py` and `beam_recipe.py`.
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
| beam-fp8-dflash2 | Reflection Beam 501B-A23B FP8 | 8 B200 | DFlash2, 7 tokens, adaptive verification | 32 | 32 | 200G |
| beam-nvfp4-dflash2 | Reflection Beam 501B-A23B NVFP4 | 4 B200 | DFlash2, 7 tokens, adaptive verification | 32 | 32 | 200G |

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
- **Reflection Beam:** follows the [official vLLM recipe](https://recipes.vllm.ai/reflection/Beam-1.0-501B-A23B).
  FP8 uses TP8 on eight B200s and the Triton MoE backend; NVFP4 uses TP4 on four
  B200s with the image's default Blackwell backend. Both use FP8 KV cache, pure
  tensor parallelism (no EP), `beam` reasoning/tool parsers and native output
  grammar. The common DFlash2 drafter proposes seven tokens with `FLASH_ATTN`
  and adaptive verification. Its trained block size is 16; seven proposals are
  the publisher recommendation. The model image `vllm/vllm-openai:reflection-beam`
  is pinned to Linux AMD64 digest
  `sha256:25635d363e0a86a099e16690f642e85301c4940ee11acf9335c4570657db9d9d`.
  Its source-revision label is unavailable publicly; runtime source hashes are
  recorded and live tokenization/streaming capture must pass. Project overrides
  are 64K context, 32K prefill, c32, 0.90 GPU memory and two loading workers.
  Beam BF16 is not selected. No B200 benchmark result is claimed before a run.

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
Beam uses its generation-config/evaluation defaults **temperature 0.7 / top-p 0.99**,
native `reasoning_effort='max'` (effort value 512, not a token limit),
`allow_missing_thinking=False`, and its pinned Harmony template. It stops on
`<|return|>` (151674) and `<|call|>` (151675), allowing analysis to finish at
`<|end|>` without ending generation. The same kwargs are sent to tokenization
and completion. Beam's native output grammar stays enabled; JSON schemas remain
prompt instructions plus local validation.
All use seed 42 and **no inherited Glimmer top-k 64**.
`--generation-config vllm` prevents checkpoint defaults from overriding the
explicit per-request sampling. Tokenization uses the identical native template
kwargs. Schemas are prompt instructions plus local validators, not constrained
generation.

Project-specific overrides are explicit: **65,536 context**, 32,768 output/repair
tokens, 32,768 prefill tokens on BF16/Fastino/Muse Spark Open, 0.90 GPU utilization
on NVIDIA MTP/Muse Spark Open profiles, 0.89 on Red Hat FP8 and 0.85 on Fastino, loopback ports and
offline local snapshots.
The official models support larger contexts; this comparison keeps the established
source context budget. Length-limited retries request up to 49,152 output tokens.
Every request is tokenized first: output is capped at remaining context, with a
minimum 16,384-token allowance; smaller allowances fail visibly without source
truncation. Reasoning remains enabled and saved. B200
replaces GB200; no B200 performance is claimed before a real run.

The October 16 smoke exposed three initialization timeouts while weights were
still loading or kernels were warming up. New Super runs allow **5,400 seconds**
for server readiness and engine initialization. Spark uses a small executable
wrapper to forward this timeout through the Rust frontend's `--args-json`
bootstrap, which otherwise defaults to 600 seconds in the pinned launch image.
The wrapper execs the original image binary and preserves its inherited socket
descriptors; native templates/parsers and sampling are unchanged. CPU setup checks
that the binary supports the timeout option before GPU inference.

Red Hat's pinned FP8 config excludes `backbone.embeddings`, but vLLM preserves
regex exclusions verbatim while renaming that layer to
`language_model.model.embed_tokens`. The serving command adds only the renamed
embedding exclusion via `--hf-overrides`, carrying the entire original
quantization config because vLLM replaces dict-valued overrides. The downloaded
config and weights remain byte-identical. A CPU loader-method probe checks that
the embedding remains unquantized; the GPU smoke remains required.

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

The shell launcher defaults to `DENSE_SUPER_SCHEDULER=packed` for the four focused
profiles. Direct Python/CLI preparation retains serial mode unless passed
`--scheduler packed`. Other profiles require explicit serial mode. A frozen
campaign never changes its scheduling mode on resume.

| Wave | Model / replicas | GPUs | CPUs | Host RAM requested |
|---|---|---:|---:|---:|
| 1 | Beam NVFP4, one TP4 server | 4 | 16 | 96G |
| 1 | NVIDIA BF16 MTP, one TP2 server | 2 | 8 | 64G |
| 1 | NVIDIA NVFP4 MTP, two TP1 servers | 1 + 1 | 4 + 4 | 32G + 32G |
| 1 total | Three models / four servers | 8 | 32 | 224G |
| 2 | Muse Spark, one TP8 server | 8 | 16 | 160G |

These fit the supplied cai5724 investment cap of 8 GPUs, 32 CPUs and 250G RAM.
Host RAM requests are distinct from GPU VRAM. They include headroom over the
user-supplied sacct batch MaxRSS values from the preceding two-article run:
NVFP4 8.87G, BF16 19.93G, Beam NVFP4 31.07G, Spark 51.98G. MaxRSS does not prove
aggregate job memory safety; longer articles and concurrent loading still need
a hardware smoke. The serial recipe table above retains the previous host RAM
requests; packed job overrides and their evidence are frozen under scheduler
in manifest.json. GPU tensor parallelism, sampling and reasoning settings stay
unchanged. The extra NVFP4 GPU is a second independent replica, not TP2.

Each model has an isolated `profiles/NAME` lifecycle workspace. The two NVFP4
workers share read-only weights but use separate compilation caches and worker
locks; atomic article claims prevent duplicate processing. They process the same
combined 48-article set as every other model, not 48 articles per replica.
Slurm assigns disjoint GPUs to the independent jobs, which may land on different
nodes. The scheduler fits the supplied resource cap but cannot guarantee immediate
cluster availability or globally optimal elapsed time.

New packed plans (version 2) pipeline serial CPU staging with inference. Stage
NVFP4 first and start replica 0; then stage BF16 and start it; then stage Beam.
While Beam downloads, NVFP4 replica 0 (32G) + BF16 (64G) + CPU download (128G)
use at most 224G and 20 CPUs. Once staging finishes, Beam and the second NVFP4
replica can start, reaching the full 224G/32-CPU/8-GPU wave. Extra replicas are
deferred to preserve staging headroom. Admission checks reserve 128G and eight
CPUs for remaining staging, so subsets also fit. Readiness removes a dependency;
actual start still depends on Slurm resources and queueing. Cleanup waits for
every worker in the wave, then runs serially; the next wave starts only after
cleanup succeeds. Version-1 frozen campaigns retain their all-downloads barrier. Model/startup failures still allow
cleanup and diagnostic aggregation; failure of cleanup blocks subsequent staging.
Jobs are held until the entire dependency graph is accepted. Submission failure
cancels only the newly created IDs. The ordinary cancel/status/verify/resume/export
commands cover all child jobs. Export collects canonical article results and
profile lifecycle receipts, excluding all runtime weights and caches.

Packed mode trades disk space for elapsed time. The prior artifact inventories
were approximately 78.1 GiB (NVFP4), 240.0 GiB (BF16), 285.1 GiB (Beam NVFP4), and
514.3 GiB (Spark). Staging estimates reserve 80+245+290 GiB plus 128 GiB for scratch
and results: wave 1 requires approximately **743 GiB free** on a fresh campaign;
wave 2 requires 648 GiB after wave 1 is removed. CPU preflight checks actual free
space before each wave and subtracts only existing artifacts in that same wave
on resume. It fails explicitly when space is insufficient; it never deletes old
campaigns. Disk estimates are headroom, not a storage quota reservation.

Use `DENSE_SUPER_SCHEDULER=serial` if disk cannot hold the concurrent wave. Serial
mode retains the one-target-at-a-time lifecycle and the earlier resource requests.
Downloads use eight CPUs, 128G RAM, up to eight hours and no GPUs. Setup, cleanup
and aggregation use eight CPUs and 32G RAM, without overlapping GPU waves. The CPU
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
Beam's token-count smoke uses `low` effort; extraction/repair use `max`.
Every configuration also runs a separate **streaming reasoning smoke**, through
the actual extraction client with the production reasoning/template/sampling/stop
settings. It must return nonempty, separately parsed reasoning text. Failures
stop the worker before article inference, with diagnostics in
`results/workers/*/job-*/reasoning-smoke.json`. The bounded synthetic trace can end
at its token cap; it is excluded from article metrics and training artifacts.
These checks can reject an incompatible runtime; local tests do not establish
GPU kernel compatibility or memory fit.

Aggregation always exports diagnostic results, including incomplete runs, but
the aggregate CLI/Slurm job exits unsuccessfully when articles remain pending.
An archive's existence or checksum only establishes that results were packaged,
not that inference succeeded. The comparison table shows completed/planned
articles and reports quality as **not measured** when no attempts completed.
The JSON retains full-reference missing-prediction penalties for accounting,
with `quality_measured: false` distinguishing those from measured quality.
The pipeline-validity table separately shows fully valid extractions, failed and
partial tasks, EHR errors, and length-limited calls. `complete` means every attempt
finished; `all_extractions_valid` tracks whether every extraction passed its
schema/evidence checks. Neither field establishes semantic accuracy. Legacy
receipts without finish-reason counts report truncations as unavailable.

Each article has a 60-minute operational timeout; a worker stops taking articles
near its allocation deadline. Operational failures/pending articles are retained
for explicit resume; completed invalid model outputs are scored and never rerolled.

```bash
# Wait for every previous campaign job to terminate; investigate logs first.
bash scripts/run_dense_nemotron_super.sh resume
# Inspect a concrete prepared submission without queueing it:
python3 "$WORK/code/scripts/benchmark_dense_nemotron_super.py" submit --work "$WORK" --dry-run
```

Resume skips completed articles/configurations and schedules CPU downloads only
for profiles with pending articles. Packed mode collects completed child results
first, rebuilds a fresh claim namespace and re-stages only pending profiles;
cleanup-only stages recover retained completed runtimes. Retained partial downloads can resume when
available; cleaned weights are downloaded again. A canceled cleanup is recovered
before a different profile downloads. Runtime lifecycle locks prevent download or cleanup while workers hold shared
read locks; separate per-replica locks and article claims protect concurrent inference. A failed access setup
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
DENSE_SUPER_LIMIT_ARTICLES=2 DENSE_HOURS=4 bash scripts/run_dense_nemotron_super.sh
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
`reasoning`, `reasoning_content` and `thinking` aliases are retained verbatim;
`reasoning_details` blocks remain in their original structure. Alias text is
counted once. The comparison report includes returned-text/missing-text call
counts and the number of facts linked to returned reasoning for every model.

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

## Resource evidence for packed runs

Worker gpu-telemetry.jsonl samples now include host_memory from the current
process cgroup (current/peak/limit bytes when supported), including descendants.
No whole-node memory usage is substituted. summary/slurm-accounting.json captures
job and step memory, CPU and allocated-resource accounting when sacct is available.
Missing accounting remains unavailable rather than zero. Accounting may lag.

The article throughput table remains normalized by summed article wall seconds.
It now distinguishes GPUs/server from scheduled replica count. It must not be
read as combined throughput from both NVFP4 replicas. comparison.json additionally
reports observed_worker_span_goodput, using first worker start to last worker
finish per model, including startup, overlap and any intervening gaps. Missing
worker receipts make its document rate unavailable. Keep actual sacct allocation
costs separate from model-generation throughput.
