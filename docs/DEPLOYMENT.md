# Deployment on one eight-B200 HiPerGator node

The UF inventory documents B200 nodes with **8 GPUs, 180 GB HBM each, 112 CPU cores and 2 TB host RAM**, with partition `hpg-b200` and GRES type `b200`. The example allocation uses 64 CPU cores, 512 GB host RAM and eight GPUs; adjust CPU/memory/time requests to your account entitlement and observed loading requirements. Four simultaneous model replicas can increase host staging and I/O pressure. This project does not claim the example memory request has been measured for every topology. [1]

## Separate CPU preparation from inference

On a CPU environment with permitted network access:

```bash
cd openpatients2
bash scripts/bootstrap.sh
mkdir -p logs data models containers
uv run op2 download-dataset --output data/prepared.jsonl
uv run op2 download-model \
  --repo IFM/K2-Horizon-375B-A23B-NVFP4 --output models/k2
uv run op2 pin-config --config configs/pipelines/k2.yaml \
  --snapshot models/k2/op2_snapshot.json --output configs/pipelines/k2-pinned.yaml
uv run op2 profile --config configs/pipelines/k2-pinned.yaml \
  --tokenizer models/k2 --output data/k2.tokens.jsonl --workers 16
bash scripts/pull_container_cpu.sh k2
```

For Motif, use `Motif-Technologies/Motif-3-NVFP4`, `models/motif`, the `motif.yaml` pipeline, `motif.tokens.jsonl`, and `pull_container_cpu.sh motif`.

The download helper resolves the model/dataset branch to a commit SHA before downloading. The model snapshot manifest is required by the GPU launcher. A revision label such as `main` is not acceptable for production inference state. The tokenizer is loaded **from local files**; custom tokenizer/model code must be trusted and audited at the pinned revision.

The container helper pulls versioned images and writes a local SIF SHA256. For a release campaign, replace mutable OCI tags with audited image digests. Load Apptainer through the cluster's current module system if it is not already available. The scripts deliberately do not invent a module version or your account/QOS.

Upstream vLLM 0.30.0's default published runtime uses CUDA 13.0, and a `v0.30.0-cu129` variant is documented. Check the actual node driver/runtime compatibility before choosing; edit the image/SIF consistently and record it. Do not assume all Blackwell driver installations support every newer CUDA container. Motif's own fork has its own runtime requirements. [2,3]

## Optional representative corpus sample for experiments

A seeded sample here means representative of this *input corpus sampling frame*, not representative of the clinical population:

```bash
uv run op2 sample --input data/prepared.jsonl --output data/pilot.jsonl --size 1000 --seed 42
```

Use the same `pilot.jsonl` for both models. Change the pipeline `input` to that path. A full-corpus token profile already covers a subset, provided source/provenance and task/model settings match. Alternatively profile each pilot config separately. A benchmark of the first 128 unshuffled raw rows is convenient for smoke but can be source-biased; materialize a shuffled/stratified clinical pilot before making model-selection claims.

## Start an endpoint or launch an entire run

Commands can be inspected on a CPU without starting a server:

```bash
uv run op2 serve --config configs/serving/k2-vllm-tp8.yaml
```

Inside an authorized eight-B200 allocation:

```bash
uv run --no-sync op2 launch-run \
  --config configs/pipelines/k2-pinned.yaml \
  --serving configs/serving/k2-vllm-tp8.yaml \
  --benchmark --limit 128
```

This starts all required ranks, waits for readiness, records deployment/startup metadata, checks all schemas, measures the requests, and shuts down its server process groups. It uses local model and SIF paths and sets Hugging Face offline flags. It does not install vLLM/SGLang into the uv client environment or download model weights after GPU allocation.

For a full extraction, omit `--benchmark --limit 128`. Re-running the same production config resumes successful matching task signatures from `output/state.sqlite`; changed source/provenance, checkpoint, prompt, schema version or generation settings invalidate relevant cached task outputs. Earlier input versions are preserved in the state DB. Keep the source dataset and checkpoint manifests as well.

Direct access to an existing server is also supported:

```bash
uv run op2 smoke --config configs/pipelines/k2-pinned.yaml
uv run benchmark --config configs/pipelines/k2-pinned.yaml --limit 128
uv run op2 run --config configs/pipelines/k2-pinned.yaml
```

Set `api.endpoints` to the server's OpenAI-compatible base URL(s), `api.model` to its served alias, and the true checkpoint ID/revision for provenance. Generic endpoints cannot independently prove their weights: the operator must supply and preserve accurate server metadata. The API key is read from `OPENAI_API_KEY` or another configured environment variable. Do not embed credentials in URLs or YAML.

## Slurm submission

```bash
export ACCOUNT=YOUR_ACCOUNT
export QOS=YOUR_QOS
export PIPELINE=configs/pipelines/k2-pinned.yaml
export SERVING=configs/serving/k2-vllm-tp8.yaml
export MODE=benchmark
export LIMIT=128
bash scripts/submit.sh
```

`MODE=production` runs the full configured input. `MODE=campaign` also requires `CAMPAIGN=configs/campaign-k2.yaml` (or the Motif campaign). `PIPELINE`/`SERVING` still provide explicit defaults in the job environment. A supplied 12-hour job limit is a request, **not a predicted completion time**; alter it to fit your QOS and empirical campaign results.

The GPU job's pre-timeout signal triggers cleanup and allows committed task state to resume. Processes are contained in the Slurm allocation; normal completion releases GPUs. For sustained production, use an appropriate persistent scratch directory, verify its SQLite locking behavior, and avoid simultaneous writers to one state DB. Node-local storage can be faster, but requires deliberate checkpoint copying before job expiration; the code does not secretly promise durable node-local state.

CPU preparation and postprocessing sbatch templates are provided. Supply a valid CPU partition/account/QOS and permitted network environment for preparation. The production templates defer the large merged JSONL export until CPU postprocessing (`export_after_run: false`). Run `op2 export --run runs/k2` (or a specific benchmark subdirectory) after the GPU job exits. The CPU postprocessing script can take `RUN` and perform that export first. For postprocessing, set `PATIENTS` to `patients.jsonl` and `INDEX` to a new index filename. Do not run independent model download jobs into the same target directory concurrently.

## Serving profile map

| Profile | Devices | Purpose |
|---|---:|---|
| `k2-vllm-tp8.yaml` | 8 | Straightforward upstream K2 baseline, no speculation. |
| `k2-vllm-tp2-dp4.yaml` | 8 | One EP8 group, four external DP endpoints. |
| `k2-vllm-tp1-dp8.yaml` | 8 | More attention replication/DP; experimental, not presumed fastest. |
| `k2-vllm-4x2.yaml` | 8 | Four independent two-GPU copies; no shared EP collectives between copies. |
| `k2-vllm-ngram.yaml` | 8 | Experimental n-gram prompt lookup; no trained draft checkpoint assumed. |
| `motif-vendor2.yaml` | 2 | Vendor two-GPU hardware shape, internal DP routing; feasibility reference. |
| `motif-4x2-no-spec.yaml`, `motif-4x2-mtp.yaml` | 8 | Four two-GPU EP groups, explicit per-rank endpoints, MTP off/on. |
| `motif-dp8-no-spec.yaml`, `motif-dp8-mtp.yaml` | 8 | Single EP8 group, explicit per-rank endpoints, MTP off/on. |
| `k2-sglang-tp8.yaml` | 8 | Experimental B200 NVFP4 challenger; official cookbook validates BF16 H200 only. |

**External DP mechanics:** each rank gets a disjoint `CUDA_VISIBLE_DEVICES` subset, a unique HTTP port and a rank within its MoE group. Each independent group receives a separate RPC port. All ranks launch together before readiness checks because their expert layers synchronize. EP is not an additional multiplicative GPU factor: for these PP1 configurations, GPUs = replicas × TP × DP. [4]

The Python renderer checks the eight-GPU budget and prevents generic extra arguments from overriding managed topology flags. It does not claim all model/engine combinations support every topology just because the command renders. The provided tests verify command composition and accounting; only a real server smoke can validate kernels and parsers.

The SGLang SIF is intentionally not pulled from an unverified container tag. Build/materialize an audited **v0.5.20** SGLang runtime and record its digest, then pass the actual local SIF path in its profile. Do not include that experimental profile in an expensive campaign until it passes the exact checkpoint's loading, quantization and schema tests. A Motif SGLang launch is not included because no verified checkpoint-specific implementation was found in this review.

## Controls intentionally left off by default

FP8 KV, speculative K2 drafts, KV offloading, prefill/decode disaggregation, context parallelism, and hand-picked alternative MoE kernels are not silently enabled. Each can affect feasibility, quality or speed and needs a separate experiment. Default KV dtype is `auto` with BF16 model dtype; inspect the engine's actual cache dtype in logs. The model-card maximum context is not automatically allocated: 65,536 is the initial cap, and the CPU profiler decides whether it is appropriate.

Independent rank affinity plus bounded active cases provides an ordinary in-memory caching baseline before adding a distributed KV service. If prefix eviction remains high, compare fewer active cases, different batching, replicas, and explicit cache metrics before introducing more moving parts.

## Sources

[1] https://docs.rc.ufl.edu/resources/gpus/

[2] https://github.com/vllm-project/vllm/releases

[3] https://huggingface.co/Motif-Technologies/Motif-3-NVFP4

[4] https://docs.vllm.ai/en/latest/serving/data_parallel_deployment/

[5] https://docs.sglang.io/cookbook/autoregressive/IFM/K2-Horizon

## CPU source enrichment

The model pipeline templates read `data/enriched.jsonl`. Before token profiling, run the source-provenance and URL-only discovery stage described in [Sources and figures](SOURCES_AND_FIGURES.md). Set `NCBI_EMAIL` for NCBI contact identification. `scripts/prepare_cpu.sbatch` includes the stage, and `scripts/enrich_sources_cpu.sbatch` runs it on its own. Do not run two writers against the same literature cache at once. No image downloads or GPU allocations are part of enrichment; the code rejects Slurm jobs reporting allocated GPUs.
