# Glimmer on eight HiPerGator B200s

Run from the updated `openpatients2` checkout on HiPerGator. All outputs,
campaign environments and containers live under `ehr_agent`; no tar extraction
is required if the updated repository is already there.

If the cluster checkout has not been updated, use the packaged file allowlist
from a **Mac terminal**, inside this local repository:

```bash
rsync -av --files-from=dist/openpatients2-glimmer-benchmark.tar.gz.files.txt \
  ./ wkieffer@hpg.rc.ufl.edu:/blue/cai6734/ehr_agent/openpatients2/
```

This copies the current benchmark code, configuration and frozen fixtures, and
omits local run histories, credentials, model weights and environments. It does
not delete files on the cluster. The portable tar is also available in `dist`;
`op2 hpg-benchmark package --output dist/openpatients2-glimmer-benchmark.tar.gz`
refreshes both the archive and this transfer list after edits.

```bash
cd /blue/cai6734/ehr_agent/openpatients2
uv run --locked --python 3.12 --no-dev op2 hpg-benchmark submit \
  --config configs/hipergator/glimmer.yaml \
  --work-dir "$PWD/../op2-glimmer-runs/run-$(date +%Y%m%d-%H%M%S)"
```

Alternatively, `bash scripts/hpg_glimmer.sh --work-dir "$PWD/../op2-glimmer-runs/run-$(date +%Y%m%d-%H%M%S)"`
installs the small CPU client before submitting. `hpg-benchmark plan` with the
same arguments prepares an offline submission plan without downloads or jobs.
Use a fresh directory each time. Keep the checkout unchanged while its campaign
is active; every stage checks its package and fixture fingerprints.

The defaults request account/QoS `cai5724`, one node, eight B200s, 32 CPUs and
250 GB host memory for GPU stages. Scheduler resource preflight precedes held
submission of the entire chain. Jobs are released only after submission succeeds.
Independent replicas load in waves of two to bound transient host memory; all
eight GPUs participate in the measured workload after startup completes.
These are queued batch jobs, so logging out does not interrupt the campaign.

## Checkpoints

| Verifier | Precision | Pinned revision | Selected checkpoint files |
| --- | --- | --- | ---: |
| [RedHatAI FP8 block](https://huggingface.co/RedHatAI/Muse-Glimmer-30B-FP8-block) | FP8 W8A8; vision/embeddings/head retained | `1deb4641ff84f9a728dd11b27cac1f6a02a9ed14` | 34.42 GB |
| [NVIDIA NVFP4](https://huggingface.co/nvidia/Muse-Glimmer-30B-NVFP4) | ModelOpt mixed NVFP4 W4A16 / FP8 / BF16 | `47818374517751c48c55cde2621594926b1888b6` | 24.70 GB |
| [Unsloth NF4](https://huggingface.co/unsloth/Muse-Glimmer-30B-unsloth-bnb-4bit) | BitsAndBytes NF4, double quantization | `ecedda395d4d099b477d135a9a573eedaa5a7aad` | 22.24 GB |

The FP8 checkpoint is published by Red Hat, not Meta. NVIDIA's checkpoint is
mixed precision rather than uniform four-bit W4A4. The inventories, quantization
configs, hashes and exact selected byte totals are in `configs/hipergator/glimmer`.
The full BF16 verifier is excluded from this campaign.
We use Unsloth's safetensors NF4 export so all three verifiers share vLLM, tokenizer
controls and evaluation code. Its GGUF exports require another loader/backend and
are not included in this comparison.

Speculative companions:

| Companion | Method | Revision | Size |
| --- | --- | --- | ---: |
| [Meta assistant](https://huggingface.co/meta-models/Muse-Glimmer-30B-assistant) | DFlash, 16-position blocks (15 predictions + anchor) | `e8192f3a8f617f74be2ce220360c89ef4789f39f` | 5.11 GB |
| [Abstract & Extraordinary DSpark head](https://huggingface.co/abstract-extraordinary/Muse-Glimmer-30B-DSpark) | DSpark, 16-token blocks, `sample_from_anchor=false` | `4699c0896e9337a2709149bfb0f64da1d91d839e` | 5.32 GB |

These are assistants derived from Glimmer, not replacement verifiers. Their
published consumer-GPU speedups do not establish speedups for eight B200s or for
our medical workload. The DSpark checkpoint has no confidence head; this campaign
tests its fixed block length, not confidence-based adaptive verification.
The [vLLM Glimmer recipe](https://recipes.vllm.ai/meta-models/Muse-Glimmer-30B#speculative-decoding)
sets DFlash's `num_speculative_tokens=15`: its first position holds the last
accepted token. DSpark's author requires `num_speculative_tokens=16` with the
non-anchor layout. The two launch configurations preserve this distinction.

## Lifecycle and storage

Each checkpoint follows `CPU download → GPU quality/layout sweeps → CPU cleanup`.
The next download depends on successful cleanup. The GPU job owns one complete
node allocation only while loading, warming, generating and measuring GPU work;
weight downloads, hashing, dependency installation, container acquisition and
deletion all happen in CPU jobs. There is one verifier on disk at a time, with
its two assistants in the same owned `active-model` directory. Both assistants
are omitted for NF4. Model-local Hugging Face, Xet, Triton and compilation caches
are deleted with the checkpoint. Results and the SIF remain.

The largest checkpoint plus assistants is about 45 GB. Download preflight
requires approximately 79.4 GB free after container acquisition, including a
10% margin and 30 GB scratch headroom; filesystem quotas still apply. Acquiring
the serving SIF has its own 70 GB scratch check. No weights are downloaded to
this local Mac. Only small metadata/configuration/template files were fetched
while implementing the gauntlet.

To reuse the existing stock vLLM 0.30 SIF, append:

```bash
--sif /blue/cai6734/ehr_agent/op2-k2-runs/run-20260930-174918/vllm.sif
```

Otherwise the CPU setup job pulls `docker://vllm/vllm-openai:v0.30.0`, records its
SHA256, and checks `/usr/bin/python3`. It installs `vllm-bnb-plugin==0.0.3` and
`bitsandbytes==0.50.2` into the campaign's `container-plugins` directory without
replacing Torch or vLLM. The official plugin is required because vLLM moved BNB
out of tree. Installed files are fingerprinted and verified before inference.

The [DSpark author](https://huggingface.co/abstract-extraordinary/Muse-Glimmer-30B-DSpark)
documents a target-wrapper bug still present in vLLM 0.30. CPU setup reads that
one source file from the SIF, replaces exactly one occurrence of
`target_inner = target_language_model.model` with a `getattr` fallback, and
records the original/patched hashes. DSpark servers bind only this patched file
read-only; the SIF is unchanged. An ambiguous patch fails CPU setup. No arbitrary
development branch or CUDA rebuild is installed during a GPU allocation.

## Template, sampling and extraction quality

All variants explicitly use the corrected [official Meta chat template](https://huggingface.co/meta-models/Muse-Glimmer-30B/blob/a4e59da52a7bc87ae7251dd5545c0dd437c44b68/chat_template.jinja),
SHA256 `cfc67e5f349f37690dfd31ed1f18bc4442a9dd32fe39a648f993cb4eb3cae678`.
The quantized repositories contain an older template. CPU download renders all
176 frozen prompts at every level with the real tokenizer, checks exactly one
correct reasoning directive, and verifies the context budget before releasing
the GPU job. Both `muse_glimmer` reasoning and tool parsers are enabled; ATEM
special tokens are preserved. Reasoning text is never treated as clinical evidence.

| Arm | Temperature / top-p / top-k | Reasoning | First / retry output cap |
| --- | --- | --- | --- |
| `matched` | 0 / 1 / unset | low | 8,192 / 16,384 |
| `meta_low` | 1 / .95 / 64 | low | 32,768 / 32,768 |
| `meta_medium` | 1 / .95 / 64 | medium | 32,768 / 32,768 |
| `meta_high` | 1 / .95 / 64 | high | 32,768 / 32,768 |
| `meta_xhigh` | 1 / .95 / 64 | xhigh | 32,768 / 32,768 |

The four levels and sampling settings come from [Meta's model card](https://huggingface.co/meta-models/Muse-Glimmer-30B).
Control is passed as `chat_template_kwargs.reasoning_strength`, not a generic
provider `reasoning_effort` alias. Seed 42 is recorded; GPU inference and
different precisions can still produce different outputs with identical seeds.
All arms use 65,536 context, BF16 KV cache, language-model-only loading and
prompt-requested JSON with application parsing/validation. There is **no
schema-constrained decoding**.

Quality always uses the same non-speculative TP1/DP8 layout. It replays the exact
nine PMC articles, eleven cases, patient-specific attribution packets, first
prompts and frozen clinical validators used for K2 and the earlier hosted models.
Each arm has 176 primary extraction/summary/timeline tasks, with one complete
regeneration retry after validation failure. Every Meta arm additionally runs
nine discovery and 31 text/figure-attribution tasks. The primary checklist has
161 required facts and 36 forbidden facts, with raw and delivered scores kept
separate. The report also gives first-pass validity and final valid/invalid
counts; both attempts and endpoint-returned reasoning are retained.

DFlash and DSpark TP1/DP8 additionally repeat the full `meta_medium` quality arm
and the 40 discovery/attribution tasks. Compare these paired arms to ordinary
`meta_medium` before adopting a speculative configuration. The quantized target,
shared output head and speculative implementation can affect behavior; a speed
result is not a quality-parity guarantee.

These are partial development checks, not comprehensive medical precision/recall
or a physician validation. Source quotes, raw candidate JSON, failed fields,
per-fact check details and pending claim-review forms are saved for adjudication.
The historical comparison has already shown that structurally valid fields can
contain unsupported clinical interpretations.

No pixels are supplied, to preserve the earlier text comparison. Glimmer is
vision-capable, so exported records say `not_evaluated_text_only`, retain caption
and figure ownership data, and omit pixel interpretation fields.

## Serving sweep

| Layout | Replicas × TP | DCP | Speculation |
| --- | ---: | ---: | --- |
| `dp8-tp1` | 8 × 1 | 1 | off |
| `dp4-tp2` | 4 × 2 | 1 | off |
| `dp2-tp4` | 2 × 4 | 1 | off |
| `dp1-tp8` | 1 × 8 | 1 | off |
| `dp2-tp4-dcp2` | 2 × 4 | 2 | off; experimental hybrid-attention test |
| `dp8-tp1-dflash` | 8 × 1 | 1 | DFlash |
| `dp4-tp2-dflash` | 4 × 2 | 1 | DFlash |
| `dp8-tp1-dspark` | 8 × 1 | 1 | DSpark |
| `dp4-tp2-dspark` | 4 × 2 | 1 | DSpark |

DP here means independent vLLM servers with disjoint device lists. Dense Glimmer
does not need MoE expert parallelism or vLLM's MoE DP flags. TP communication
stays on the same eight-GPU node. DCP reuses TP ranks and does not multiply the
GPU count. Unsloth prequantized BNB supports TP1 only; other layouts are explicitly
skipped. FP8 TP8 is skipped because splitting the 19,968-column FFN yields 2,496
columns per rank, incompatible with its 128-element quantization blocks.
Prefill context parallelism and pipeline parallelism are not added without a
verified Glimmer recipe. DCP and speculation must pass a real load/generation
smoke before any measurements; failures are retained while subsequent layouts
continue after server shutdown. A control-layout failure stops that model's GPU
stage and still reaches CPU cleanup.

The [vLLM Glimmer recipe](https://recipes.vllm.ai/meta-models/Muse-Glimmer-30B)
supports the native parsers, chunked prefill, prefix caching and DFlash.
[NVIDIA's checkpoint](https://huggingface.co/nvidia/Muse-Glimmer-30B-NVFP4)
explicitly reports B200 compatibility. Our profiles use PIECEWISE CUDA graphs,
16,384 batched tokens, 64 maximum sequences/replica, `.90` GPU memory use and
BF16 KV. Those are starting settings, not a measured B200 optimum.

Every layout measures concurrency 8 and 16 **per replica**, with two repetitions
of the same 128-request workload. The 32 actual frozen prompts include a short
and long source prompt for each of the 16 task types. Prompts, ordinal seeds,
medium reasoning, output budgets and copies are identical across configurations.
Assignment is round-robin across replicas. Exact tokenizer checks and an explicit
prefix warmup precede timing on every cell, so the layout that performed quality
testing does not get a unique warm-cache advantage. Each request generates to
its natural end with the same 32k budget; no retries are performed in the speed
cells. Truncations, parse failures and clinical validation failures are reported.

Cell reports include aggregate input/output tokens/s across all eight GPUs,
output tokens/GPU-second, valid tasks/s, completion counts, latency/TTFT/time-to-
final-answer mean/median/P95 and repeat-level rates. Missing usage stays unknown.
Warm prefix reuse reflects repeated work; it does not estimate uncached ingestion
of entirely new articles. Startup and queue waiting are excluded from cell rates;
model GPU-stage duration includes model loads and all experiments. Raw Prometheus
snapshots before/after each cell preserve speculative acceptance counters, and
GPU utilization/memory/power are sampled every second. The best measured cell
maximizes application-valid tasks/s among cells with at least 95% complete,
nonempty final responses. This is a serving candidate to review alongside full
quality scores, not an automatic production change.

An eight-GPU node may wait longer in the queue than the previous smaller K2
allocations. Each verifier's GPU job has a 12-hour limit; completion by morning
depends on queueing, initialization and measured generation speed.

## Progress, success and transfer

```bash
run_dir=$(ls -dt /blue/cai6734/ehr_agent/op2-glimmer-runs/run-* | head -n 1)
squeue -u "$USER"
cat "$run_dir"/results/*/progress.json
tail -n 20 "$run_dir"/logs/gpu-*.log
```

After all jobs finish:

```bash
cat "$run_dir/SUMMARY.md"
python - "$run_dir" <<'PY'
import json, sys
from pathlib import Path
root = Path(sys.argv[1]); summary = json.loads((root / 'summary.json').read_text())
assert not summary['failed_models'], summary['failed_models']
assert not summary['weight_storage_exists'], 'Owned weights still exist'
for model in summary['models']:
    for name, layout in model['layouts'].items():
        status = layout['status']
        if status['status'] != 'completed':
            print(model['model']['name'], name, status['status'], status.get('reason', status.get('error')))
print('Core campaigns and CPU cleanup completed. Review skipped/failed experimental layouts above.')
PY
```

`SUMMARY.md` and `summary.json` contain quality tables, all layout results,
historical scores and explicit missing/failed/skipped stages. A failed speculative
experiment can coexist with completed core quality tests; inspect the deployment
outcomes, not just whether the Slurm queue is empty.

Create a **results-only** tar on HiPerGator to avoid slow transfers of thousands
of small JSON files. Keep the SIF, plugin environment and caches on the cluster:

```bash
tar -czf "${run_dir}-results.tar.gz" -C "$run_dir" \
  campaign.json jobs.json submission.json slurm-commands.json runtime-manifest.json \
  setup.json container-python.json container-plugins.json dspark-patch.json \
  summary.json SUMMARY.md logs results
```

Then on the Mac, replace the run name with the completed directory printed above:

```bash
scp wkieffer@hpg.rc.ufl.edu:/blue/cai6734/ehr_agent/op2-glimmer-runs/run-YYYYMMDD-HHMMSS-results.tar.gz ~/Downloads/
```

Implementation checks are CPU/mock tests. Loading these exact checkpoints,
quantization kernels, DCP and speculative layouts on B200 remains to be measured
by this campaign.

The bundled `meta-models-chat_template.jinja` is from Meta's Apache-2.0 Glimmer
release; its source revision and SHA256 are listed above. See the accompanying
Apache license and model card for attribution and model terms.
