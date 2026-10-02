# Focused Red Hat Glimmer FP8 / eight-B200 tuning

This campaign follows the October 2 result analysis. It uses only
[RedHatAI/Muse-Glimmer-30B-FP8-block](https://huggingface.co/RedHatAI/Muse-Glimmer-30B-FP8-block)
at `1deb4641ff84f9a728dd11b27cac1f6a02a9ed14`, plus the pinned
[Meta DFlash assistant](https://huggingface.co/meta-models/Muse-Glimmer-30B-assistant).
The [official vLLM recipe](https://recipes.vllm.ai/meta-models/Muse-Glimmer-30B)
lists these artifacts. No full BF16 verifier, alternative quantizer, BNB package
or DSpark checkpoint is downloaded. Existing three-precision gauntlet configuration
and frozen evaluation inputs remain unchanged.

The search found other quantizations, but they are outside this user-selected
campaign. Shisa's two FP8 weight shards have exactly the same published SHA256s
as Red Hat's, so that checkpoint would not provide an independent weight-quality
comparison. Dynamic/channel FP8 and calibrated mixed INT8 checkpoints also exist;
none has been established as stronger on our clinical workload.

## Run

Update the cluster checkout before submitting. From this Mac repository:

```bash
rsync -av --files-from=dist/openpatients2-glimmer-fp8-tuning.tar.gz.files.txt \
  ./ wkieffer@hpg.rc.ufl.edu:/blue/cai6734/ehr_agent/openpatients2/
```

Then on HiPerGator, from inside `openpatients2`:

```bash
cd /blue/cai6734/ehr_agent/openpatients2
bash scripts/hpg_glimmer_tune.sh \
  --work-dir "$PWD/../op2-glimmer-tuning/run-$(date +%Y%m%d-%H%M%S)" \
  --sif /blue/cai6734/ehr_agent/op2-k2-runs/run-20260930-174918/vllm.sif
```

Omit `--sif` if that retained image no longer exists; CPU setup acquires the
pinned stock vLLM 0.30 image. The Python interpreter stays `/usr/bin/python3`.
The equivalent uv command is:

```bash
uv run --locked --python 3.12 --no-dev op2 hpg-benchmark submit \
  --config configs/hipergator/glimmer-fp8-tuning.yaml \
  --work-dir "$PWD/../op2-glimmer-tuning/run-$(date +%Y%m%d-%H%M%S)"
```

Use `plan` instead of `submit` for offline validation. Always use a fresh work
directory. Keep the submitted checkout unchanged while its jobs are active.
The account/QoS is `cai5724`; GPU resources are one node, eight B200s, 32 CPUs,
250 GB RAM and a 12-hour limit. Completion time depends on initialization,
generated lengths and scheduler availability; the time limit is not a runtime
guarantee. CPU-only setup/download and cleanup do not allocate GPUs. Cleanup
deletes owned weights, assistants and model-local caches, preserving results
and the image. The final report runs on CPU.

## Measurements

- Ten server layouts, four concurrency levels (8, 16, 32, 64 per replica):
  forty throughput cells. Eight TP1 replicas versus four TP2 replicas, with
  and without DFlash; DFlash prefill budgets of 8k/16k/32k; prefix caching
  enabled/disabled; experimental FP8 KV with calculated scales; and eager
  execution versus the existing PIECEWISE CUDA graph setting. Every layout
  occupies the same eight-GPU node. TP4/TP8 are omitted after the previous
  throughput results; DCP is omitted because this engine rejects sliding-window DCP.
- Each cell uses the same 32 short/long prompts covering all extraction tasks,
  sixteen copies, three repeats: 1,536 timed requests. Sixteen copies ensure
  the 64-per-replica TP1 test can actually fill all 512 request slots.
  Warmup/tokenization are outside timing; outputs complete naturally.
  Seeds, output budgets and prompt hashes match across all cells. Repeated
  prompts intentionally measure prefix reuse; the disabled-prefix control
  measures the same workload without prefix caching, not a unique-article corpus.
- Three full ordinary-decoding controls use seeds 42/1729/5724, medium reasoning,
  temperature 1, top-p .95, top-k 64, equal 32k output/retry caps and the pinned
  Meta template. No schema-constrained decoding. All use the exact historical
  176-task/11-patient fixtures; full regeneration remains the unchanged repair
  control, so tuning does not confound a new extraction/repair algorithm.
- After the speed sweep, select the highest first-pass valid tasks/s candidate
  separately for ordinary KV/warm-prefix, ordinary KV/no-prefix, and FP8 KV/warm-prefix.
  Restart each candidate at its winning concurrency and repeat all three full
  quality seeds, including discovery and figure attribution. A per-seed guard
  allows at most five lost required checks and four lost valid tasks versus
  ordinary decoding, with no extra forbidden hits. This is an explicit
  development tolerance, not a statistical significance test or full medical
  equivalence. Only confirmed candidates can appear as the best measured layout;
  source review is still pending. No production default changes automatically.
- Save total input/output tok/s, output tokens/GPU-second, valid tasks/s,
  latency/TTFT/TTFA mean/median/P95, speculative counter deltas, request payloads,
  repeat rates, stage duration, GPU utilization, per-process GPU memory, and
  pre/post-layout drain checks. Output tokens include reasoning and answers.
  Startup, warmup and queue time are excluded from cell tok/s; stage time includes
  all GPU initialization and experiments. These are not article/hour predictions.

## Actual image evaluation

Before the long text-only speed sweep, two multimodal layouts inspect the actual
31 figure images: ordinary FP8 and FP8+DFlash, all three seeds. These layouts
remove `--language-model-only` and enable the vision encoder. Each figure receives
full case text and captions for both caption-only and pixel-assisted panel
attribution, plus a separate pixel description. This avoids the old focused
prompt's missing-case-paragraph problem. The pixel description separates visible
content, modality, legible labels/values and uncertainty from author caption claims.
Patient links require literal source evidence; appearance cannot establish identity.
Shared, external, background and unresolved ownership remain explicit.

An additional joint-call arm emits visual description and ownership in one
response, so joint versus independent description/attribution can be compared
for validity, panel/patient agreement and later source/pixel accuracy. Independent
description receives no generated patient-attribution hypotheses. Neither arm
uses schema-enforced decoding. The schema in `figure_visuals.py` produces
filterable per-panel columns: general/detailed descriptions, image kind,
has_chart, chart type, clinical-chart flag, measurements/readings/units/series/time,
imaging modality/submodality, medical domain, body part, mentioned categories,
pixel observations, caption claims and author-supported clinical significance.
Modality and medical-domain labels use controlled vocabularies; other raw labels
are preserved. Graphs about MRI/CT belong in mentioned categories, not acquisition
modality. Printed values and estimates/unreadable points remain distinct.

`figure-panels.jsonl` and `figure-panels.csv` expose these columns for both analysis
methods, including patient IDs/scopes, source license and image provenance.
Structured JSON schemas are also saved in `schemas/figure-visuals.schema.json`
and `schemas/joint-figure-analysis.schema.json` for downstream consumers.
CSV list/object cells contain JSON, keeping one row per figure panel.

Images are acquired in the CPU download stage, only from the allowlisted PMC
URLs already in the frozen fixtures, with license/asset-exception checks, magic-byte
verification, the metadata MD5 when available, a SHA256, and 8 MB/image / 64 MB total
limits. The small image inputs are retained under `vision-assets` so the results
can be independently reviewed after transfer. Model weights are still deleted.
Missing assets and unsupported encodings are recorded rather than substituted.

Results are under `results/glimmer-fp8/vision/{ordinary,dflash}`. Each seed has
per-figure outputs and patient media bundles with panel assignments, source image
URLs, hashes, licenses and descriptions. `integrated-patients/{arm}` combines
these visual sidecars with clinical bundles while preserving the original scored
outputs. Visual interpretations remain unreviewed annotations, not clinical facts.
Both vision layouts report their own tok/s and valid/failed task counts; they
are not mixed into the historical text-only throughput numbers. Full semantic
image/caption/patient attribution accuracy requires the saved source/pixel review
forms. Frozen roster labels are candidate identities for controlled comparison;
the separately tested roster discovery remains an independent measurement.

Launcher process groups are terminated even if their parent already exited.
Before starting any layout, the allocated GPU UUIDs must have returned to their
original memory baseline (512 MiB tolerance) with no new compute PIDs. Cleanup
waits at most 120 seconds, records remaining PIDs, and aborts the GPU stage if
workers/memory persist. It never kills unrelated processes to clear memory.
Experimental-layout startup failures are recorded; cleanup failures stop the sweep.

## Inspect and transfer

```bash
run_dir=$(ls -dt /blue/cai6734/ehr_agent/op2-glimmer-tuning/run-* | head -n 1)
cat "$run_dir/results/glimmer-fp8/progress.json"
cat "$run_dir/SUMMARY.md"
cat "$run_dir/results/glimmer-fp8/gpu.json"
```

An empty queue alone does not establish success. Check GPU `status: completed`,
cleanup `status: deleted`, confirmation outcomes and optional failures. Once
finished, on HiPerGator:

```bash
tar -czf "${run_dir}-results.tar.gz" -C "$run_dir" .
```

Copy the printed archive path to a Mac terminal:

```bash
scp wkieffer@hpg.rc.ufl.edu:/blue/cai6734/ehr_agent/op2-glimmer-tuning/run-TIMESTAMP-results.tar.gz ~/Downloads/
```

The benchmark has not been executed on B200s from this Mac. Local checks validate
launch planning, flags, scoring, cleanup gates and reporting; on-cluster generation
and model/kernel compatibility remain runtime checks.
