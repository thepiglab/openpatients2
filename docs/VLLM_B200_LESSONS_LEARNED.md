# vLLM on HiPerGator B200s: measured results and operating lessons

Last consolidated: **2026-10-06**. This is the reference for what we actually learned
from the K2, Glimmer precision, FP8 tuning, corpus, refinement and overnight GEPA
campaigns. It records successful settings, failed experiments and confounders.
The detailed reports linked below remain the evidence of record.

**Current serving baseline:** pinned Red Hat Glimmer FP8 + Meta DFlash, eight
independent TP1 servers on one eight-B200 node, medium reasoning, prefix caching
and PIECEWISE CUDA graphs. The fastest confirmed repeated-text cell reached
**18,715 generated tokens/s across eight GPUs**. Complete clinical extraction
arms generally ran around **5,000–6,500 tokens/s**, with repairs, attribution and
audits; the October 5 bundle suite ran around **8,900–9,600 tokens/s** for most
medium-reasoning main arms. These are different workloads; the difference does not demonstrate an
engine regression. Optimize supported, correctly attributed facts and usable
patient records per GPU-hour, with token throughput as a diagnostic.

No full BF16 verifier checkpoint was benchmarked or downloaded in the Glimmer
gauntlet. The FP8 checkpoint **does use `--dtype bfloat16` at runtime**. Quantized
weights, activation/runtime dtype and KV-cache dtype are separate choices; the
user's exclusion of a full BF16 checkpoint is not a claim that every tensor is FP8.

## 1. Evidence map and boundaries

| Campaign | What it established | Evidence |
| --- | --- | --- |
| Hosted experiments, September 29–30 | Prompting, chunking, span/repair and source-format lessons; no local GPU rates | [Medical fidelity](../reports/MEDICAL_FIDELITY.md), [refinement](../reports/REFINEMENT_EXPERIMENTS.md), [chunking](../reports/CHUNKING_EXPERIMENTS.md), [formats](../reports/SOURCE_FORMAT_EXPERIMENTS.md) |
| K2, `run-20261001-123645` | Four checkpoints, all supported effort levels, smaller allocations, frozen-source quality and local rates | [K2 comparison](../reports/K2_MODEL_COMPARISON.md) |
| Glimmer, `run-20261001-233830` | FP8/NVFP4/NF4, reasoning sweep, DP/TP, DFlash; DCP/DSpark failures | [Precision gauntlet](../reports/GLIMMER_B200_RESULTS.md) |
| FP8 tuning, `run-20261002-120538` | Concurrency, prefill budgets, caching, graphs, DFlash, quality confirmation and real pixels | [Tuning review](../reports/GLIMMER_TUNING_20261002.md) |
| Corpus, `run-20261003-000942` | Bounded acquisition, actual article/prompt lengths, direct versus repaired extraction | [Corpus pilot](../reports/CORPUS_PILOT_20261003.md) |
| Correctness, `run-20261003-120419` | Whole versus compact input on fixed patients; repair losses, image semantics | [Correctness review](../reports/CORPUS_CORRECTNESS_20261003.md) |
| Refinement, `run-20261003-153746` | Source-aware repair, quantities, relative chronology and panel scope | [Refinement review](../reports/CORPUS_REFINEMENT_20261003.md) |
| Overnight recovery, `run-20261004-111240` | Separate one-GPU GEPA, eight-GPU extraction, repaired delivery and explicit partial outcomes | [Overnight review](../reports/OVERNIGHT_CLINICAL_20261004.md) |
| Clinical GEPA, `run-20261004-204850` | Completed 27-family optimization and 66 trials; source-aware baseline wins strict delivery, focused timelines remain promising | [Clinical GEPA review](../reports/GEPA_CLINICAL_20261005.md) |
| Bundle GEPA, `run-20261005-171140` | 43 comparisons completed, one ordinary control deferred; four independent rewrites, no validated joint rewrite; compact completion and better gold are next priorities | [Bundle review](../reports/BUNDLE_CLINICAL_20261006.md) |

Historical B200 results use **vLLM 0.30.0**. Failures and recipes here apply to that
pinned engine, checkpoint, template and workload. They are not compatibility
claims for every later vLLM release. Hosted Muse Spark, Gemma and Inkling timings
are not B200 measurements. No comparable completed Nemotron result was found in
the saved model comparison. Failed access to an endpoint is not a quality score.

### October 5 bundle campaign: allocation and measurement lessons

The main stage took 6.94 hours on eight GPUs, including joint optimization and
server initialization. Bootstrap took 40.1 minutes on eight GPUs; independent
component optimization took 47.0 minutes on one GPU. Active-phase telemetry
sample averages were 83.0% utilization during main extraction, 98.8% during the
one-GPU searches, and only 42.0% during eight-GPU joint optimization. Small
sequential rollouts and patient-free minibatches still underfill a pod. Cache
unchanged parent evaluations and use fewer GPUs for sparse optimizer stages until
there is enough independent work. These utilization values are sampled phase
averages, not time-weighted kernel measurements.

The live original-prompt complete arm pooled 8,955 completion tokens/s across
three main seeds; the independent GEPA arm pooled 8,965. Calculate pooled rates
as total completion usage divided by total arm wall time, not an unweighted mean
of rates. A completed ordinary live control ran at 2,843 tok/s; its companion
ordinary GEPA trial was deferred, so the ordinary quality comparison is incomplete.

Late completion overflowed at 64K before inference because review payloads copied
facts and evidence repeatedly. Do not try to fix pre-call overflow with reasoning
strength: compact shared fact/evidence references and route genuine overflow
selectively. High reasoning under the deployed 16K caps took 1.71× the original
live arm's pooled time and lost more clinical checks. It is an escalation candidate,
not an established replacement for medium across every stage.

Reserve validation time **inside** the optimizer's expensive iterations. A stop
callback between steps does not prevent a last rollout from exhausting the fresh
confirmation budget. In this run the joint confirmation timed out, yet a
`completed` optimizer receipt recorded zero score/facts. Missing confirmation must
be reported as unavailable; it is neither clinical failure evidence nor a passed
promotion gate. Selected joint prompts were the originals, so arm names alone
cannot establish which prompt changes ran.

The [October 6 corrections](BUNDLE_CORRECTIONS_20261006.md) move joint search onto
the existing one-GPU optimization server, add successful-rollout caching and
component-relevant positive training batches, and enforce confirmation reserves
inside each rollout. Late completion now uses compact source references and
serving-token-counted hint batches. These are locally verified changes, not new
B200 throughput or clinical-quality measurements. Eight replicas still run the
program comparisons; no serving recipe was changed.

## 2. The exact successful Glimmer recipe

| Knob | Confirmed peak-text setting | Notes |
| --- | --- | --- |
| Target | [RedHatAI/Muse-Glimmer-30B-FP8-block](https://huggingface.co/RedHatAI/Muse-Glimmer-30B-FP8-block) | FP8 block / compressed-tensors; let checkpoint metadata select its quantization |
| Target revision | `1deb4641ff84f9a728dd11b27cac1f6a02a9ed14` | Pin the commit, not just the repository name |
| Drafter | [meta-models/Muse-Glimmer-30B-assistant](https://huggingface.co/meta-models/Muse-Glimmer-30B-assistant) | DFlash assistant, not another full verifier |
| Drafter revision | `e8192f3a8f617f74be2ce220360c89ef4789f39f` | 15 predicted tokens plus its trained anchor position |
| Speculative configuration | `method=dflash`, `num_speculative_tokens=15`, `draft_sample_method=probabilistic` | Verify actual argv and draft counters |
| Container | `docker://vllm/vllm-openai:v0.30.0` | Record the actual SIF SHA-256; a tag alone is insufficient |
| Interpreter | `/usr/bin/python3` | Under Apptainer `--cleanenv`; do not assume a `python` alias |
| Observed runtime | vLLM 0.30.0, Torch 2.13.0+cu130 | Historical container checks, not a required future Torch upgrade |
| Layout | Eight external replicas × TP1 | Eight separate servers/endpoints; no expert parallelism |
| Context | `max_model_len=65536` | Prompt + multimodal expansion + generation must fit |
| Scheduler prefill budget | `max_num_batched_tokens=8192` | Fastest confirmed text cell; close competitors at 16K/32K |
| Sequence limit | `max_num_seqs=64` | Server capacity, distinct from client concurrency |
| Client concurrency | 64 in-flight requests per replica | 512 requests across eight servers, for queued text work |
| Memory | `gpu_memory_utilization=0.9` | Requires enough genuinely free memory before starting |
| Dtypes | Runtime BF16; KV `auto` | No measured successful FP8-KV result |
| Execution | PIECEWISE CUDA graphs; chunked prefill | Eager was substantially slower in the matched sweep |
| Prefix cache | Enabled | Measured workload intentionally reused warmed prefixes |
| Reasoning/sampling | Medium; temperature 1; top-p .95; top-k 64 | Passed through the official template's `reasoning_strength` |
| Output cap | 32,768 initial/retry tokens in the tuning confirmation | Later corpus suites also test 16K caps; do not silently mix protocols |
| Text-only switch | `--language-model-only` | Remove for actual image inference |
| Chat template | Pinned Meta template, overriding quantizer templates | SHA-256 `cfc67e5f349f37690dfd31ed1f18bc4442a9dd32fe39a648f993cb4eb3cae678` |

The [tuning configuration](../configs/hipergator/glimmer-fp8-tuning.yaml) describes
the search, so its top-level 16K batch budget / concurrency 8 are **not the selected
cell's effective overrides**. Consult the winning cell's `servers/deployment.json`
and launch argv. A legacy `speculation: off` field coexisted with an active
`speculative_config`; actual commands and counters resolve that ambiguity.

The current clinical campaign retains TP1/DFlash and client concurrency 64 but
uses a **32K prefill budget**, 64K main context, two source-aware repair rounds and
16K default output/retry budgets, with separate 128K and reasoning/output tests.
That is a correctness-suite baseline, not a claim that 32K prefill beat 8K.
See [clinical model settings](../configs/pilot/glimmer-gepa-clinical.yaml) and
[experiment matrix](../configs/pilot/overnight-gepa-clinical.yaml).

## 3. What the throughput sweeps actually showed

### Focused FP8 sweep: all eight GPUs

Each cell: 32 real short/long task prompts × 16 copies × three timed repeats =
1,536 requests. Identical prompt signatures, ordinal-scoped seeds, medium
reasoning and natural completion with 32K caps. Startup, warmup and queue wait
are excluded. Output usage includes reasoning and final answers.

| Layout / change | Concurrency 8 / replica | 16 / replica | 32 / replica | 64 / replica |
| --- | ---: | ---: | ---: | ---: |
| DP4 × TP2, ordinary | 2,078 | 3,693 | 5,839 | 8,159 |
| DP4 × TP2, DFlash, 16K prefill | 6,784 | 10,692 | 13,835 | 15,528 |
| DP4 × TP2, DFlash, 32K prefill | 7,009 | 11,273 | 13,948 | 15,974 |
| DP8 × TP1, ordinary | 2,713 | 4,453 | 6,640 | 8,783 |
| DP8 × TP1, DFlash, 16K prefill | 9,139 | 14,059 | 16,429 | 18,278 |
| DP8 × TP1, DFlash, 32K prefill | 9,302 | 14,081 | 16,899 | 18,439 |
| **DP8 × TP1, DFlash, 8K prefill** | **9,368** | **13,837** | **16,663** | **18,715** |
| DP8 × TP1, DFlash, eager | 2,379 | 4,143 | 6,386 | 9,555 |
| DP8 × TP1, DFlash, prefix disabled | 7,570 | 10,431 | 11,929 | 12,656 |

All table entries are **aggregate generated tokens/s**, not per-GPU rates.
The selected cell yielded 7.821 first-pass validator-valid tasks/s, versus 3.662
for ordinary TP1/c64. About one fifth of timed requests failed source/schema
validation even in fast cells. Token speed does not establish useful clinical
output speed.

Measured implications:

- **DFlash:** 18,715 versus 8,783 tok/s at TP1/c64, about 2.13×. In later
  matched clinical trials, focused extraction was 5,744 versus 1,605 tok/s
  (3.58×), and GEPA extraction 5,269 versus 1,723 (3.06×). The benefit varies
  with workload. Keep ordinary decoding as a paired quality control.
- **Concurrency:** substantial gains from 8→16→32→64. Both the client and
  server must permit enough requests. The first sweep used only 128 requests
  per repeat; it could not test filling 512 client slots. The later sweep used
  512 per repeat. Increasing a concurrency number without supplying work does
  not measure that setting.
- **TP1 versus TP2:** eight TP1 replicas beat four TP2 replicas in the matched
  DFlash text sweep. Glimmer FP8 fits on one B200; spreading its weights across
  GPUs sacrificed replica count without a compensating aggregate gain here.
  Larger checkpoints and long multimodal requests can have different needs.
- **CUDA graphs:** graph-enabled DFlash/c64 was about 1.96× eager/c64. Keep the
  tested PIECEWISE setting. Eager remains a diagnostic option, not the speed
  baseline.
- **Prefix caching:** 18,715 versus 12,656 tok/s on this repeated workload,
  about 1.48×. Preserve stable shared instructions/source prefixes and patient
  affinity where practical, but measure unique-article and cold-cache behavior
  separately. These data do not prove the same gain on unseen articles.
- **Prefill budget:** the c64 8K/16K/32K results differ by only 1.5–2.4%, with
  overlapping repeats. We have a selected cell, not a universal 8K optimum.
- **Latency:** selected c64 median/P95 request latency was 11.08/41.75 s;
  TTFT .687/1.233 s; first-answer time 10.28/34.32 s. Ordinary DFlash c32 reached
  16,429 tok/s with P95 latency 27.99 s. c64 suits queued batch work; c32 is a
  latency/throughput tradeoff, without the same full quality confirmation.

Selected DFlash counters: 641,906 drafts, 9,628,590 drafted tokens and 2,279,384
accepted tokens: 23.7% accepted-token ratio and 3.55 accepted tokens/draft.
Those counters establish that speculation ran. Acceptance ratio alone does not
predict wall-clock speed; drafting overhead, verification, graphs and concurrency
also matter. No speculative-depth sweep established an optimum beyond the
publisher-aligned 15-token setting.

### Earlier quantization/topology screen

This was a smaller warm-prefix workload: 256 requests/cell, concurrency 16 per
replica, medium reasoning. Do not compare its absolute rates with the larger
focused sweep as if only the engine changed.

| Checkpoint | Ordinary layout → tok/s | DFlash layout → tok/s | Lesson |
| --- | --- | --- | --- |
| Red Hat FP8 | DP2×TP4: 2,402; DP4×TP2: 2,704; DP8×TP1: 2,445 | DP4×TP2: 8,532; DP8×TP1: **10,998** | DFlash made TP1 the clear aggregate winner in this screen |
| NVIDIA NVFP4 | DP1×TP8: 1,611; DP2×TP4: 1,661; DP4×TP2: 2,473; DP8×TP1: 2,489 | DP4×TP2: 7,479; DP8×TP1: 9,250 | Smaller weights did not beat FP8 with this runtime |
| Unsloth BNB NF4 | DP8×TP1: 697 | Not established | Smallest checkpoint was much slower |

**Fewer bits do not guarantee faster inference.** Kernel support and actual
serving behavior matter. NF4 was not the throughput choice. NVFP4 remained
functional, but its best quality arm used ordinary xhigh; attaching its
DFlash/medium speed to xhigh quality would combine different experiments.

Shisa's inspected FP8 weight shards had the same published SHA-256s as Red Hat's;
that would not be an independent weight-quality comparison. Other dynamic/channel
FP8 and mixed INT8 alternatives were not clinically benchmarked here. No full
BF16 control means we cannot isolate quantization degradation.

### K2 rates and hardware sizing

These rates pool all replicas and all four arms, including secondary tasks.
Startup/warmup/queue time are excluded from tok/s; GPU-stage hours include stage
work. They are not eight-GPU saturation measurements.

| Model | Successful allocation | Output tok/s | Output tok/GPU-second | GPU-stage hours |
| --- | --- | ---: | ---: | ---: |
| [K2-375B NVFP4](https://huggingface.co/IFM/K2-Horizon-375B-A23B-NVFP4) | Four B200s, one TP4 server | 948.2 | 237.0 | 1.99 |
| [K2-32B NVFP4](https://huggingface.co/IFM/K2-Horizon-32B-NVFP4) | Two B200s, two TP1 servers | 1,042.1 | 521.1 | 1.28 |
| [K2-MoVA-36B FP8](https://huggingface.co/IFM/K2-Horizon-MoVA-36B-A4B-FP8) | Two B200s, two TP1 servers | 555.3 | 277.7 | 3.57 |
| [K2-7B FP8](https://huggingface.co/IFM/K2-Horizon-7B-FP8) | Two B200s, two TP1 servers | 2,124.4 | 1,062.2 | .84 |

The smaller allocation reduced the need to wait for an entire eight-GPU node.
It was a practical scheduling choice, not a full search for each model's optimal
layout. Active-parameter count alone did not predict throughput: MoVA was slower
than dense 32B in this setup. Do not extrapolate these rates linearly to eight GPUs.

## 4. Quality, reasoning and sampling are part of the performance contract

The frozen historical fixture has nine articles, 11 patients, **161 required
typed-field checks and 36 forbidden probes**. There are 176 primary tasks
(14 clinical sections + summary + timeline per patient). K2 IFM arms add nine
rosters and 31 caption-attribution tasks, explaining terminal totals of 216.
Do not compare a 216-task terminal count with a 176-task primary denominator.

Raw coverage is the last parseable candidate before rejection. Delivered coverage
requires acceptance by the relevant gates. A wrong/unavailable/omitted check is
not always a medically false statement, and a valid empty section can omit care.
Equivalence-reviewed scores and original strict scores must remain separate.

| Best reviewed delivery arm from the historical comparisons | Raw /161 | Delivered /161 | Valid / invalid primary tasks |
| --- | ---: | ---: | ---: |
| Local Glimmer FP8, ordinary medium | 152 | 127 | 170 / 6 |
| Local Glimmer FP8, DFlash medium | 140 | 125 | 173 / 3 |
| Local Glimmer NF4, xhigh | 151 | 121 | 161 / 15 |
| Local Glimmer NVFP4, ordinary xhigh | 141 | 119 | 172 / 4 |
| Hosted Glimmer | 125 | 100 | 166 / 10 |
| K2-375B, medium | 131 | 78 | 148 / 28 |
| Hosted Spark 1.2 | 143 | 70 | 163 / 13 |
| Hosted Gemma 4 31B | 91 | 61 | 149 / 27 |
| Hosted Inkling | 139 | 48 | 114 / 62 |
| K2-32B, high | 143 | 39 | 123 / 53 |
| K2-7B, high | 82 | 34 | 108 / 68 |
| K2-MoVA, low | 113 | 30 | 96 / 80 |

These are configured-pipeline comparisons with post hoc best-arm selection,
not pure quantization/model-intelligence comparisons. The poisoning article
contributes 91/161 checks. Agent source audits found supported and unsupported
claims beyond the finite checklist; this is not physician-adjudicated accuracy.
The ordinary/DFlash Glimmer two-check difference was one stochastic run, not
proof of a DFlash quality penalty. Later three-seed delivery was 115/91/96 for
ordinary versus 120/121/95 for the selected DFlash cell.

The tuning guard allowed at most five lost delivered checks and four lost valid
tasks per seed, with no additional forbidden hits. It passed for the selected
cell but did not protect every raw fact or prove clinical equivalence. Disabling
prefix caching lost 32 delivered checks at seed 42 and failed that guard; we do
not infer that prefix caching intrinsically makes a model more medically capable.

### Reasoning controls tested

- **Glimmer:** low, medium, high and xhigh through official
  `chat_template_kwargs.reasoning_strength`. On the first FP8 ordinary sweep,
  automatic delivered checks were low 97, medium 119, high 105, xhigh 87 of 161.
  NF4 and NVFP4 selected xhigh instead. More effort was not monotonically better.
- **K2:** low, medium and high. 375B/low had the greatest raw coverage, 156/161,
  but medium delivered 78 versus low's 54. 32B favored high; 7B high had the
  best delivery while medium had the best raw IFM coverage; MoVA low had its
  best delivery. Prefer measured task outcomes over effort labels.
- **`matched`:** historical low reasoning, temperature 0, top-p 1, 8K initial
  and 16K retry caps. Publisher-style K2 arms used temperature 1, top-p .95,
  equal 32K caps; Glimmer added top-k 64. Thus matched-versus-publisher is
  **not a reasoning-only ablation**.
- **Templates/parsers:** quantizer templates were overridden with the pinned
  publisher template. K2 low/medium initially returned empty answer content
  while final JSON appeared in `reasoning_text` after `</ifm|think>`. Correct
  final-channel splitting and the official template were essential. HTTP 200
  and `finish_reason=stop` did not establish a usable answer. Never treat all
  free-form reasoning as final clinical extraction.
- **Output structure:** current baseline requests JSON in the prompt and uses
  robust application parsing/validation; it does not force schema-constrained
  decoding. The record does not establish a universal quality advantage from
  avoiding constrained decoding. Test it as a separate matched ablation.

## 5. Context, prefill budget and output cap are three different knobs

`max_model_len` bounds an individual request's prompt plus output. A scheduler's
`max_num_batched_tokens` controls batched work; 8K prefill does not mean an 8K
article/context limit. Request `max_tokens` bounds generation, including the
reasoning/output behavior of the endpoint. Increasing one does not automatically
increase the others.

Use the exact rendered request, template and image expansion for admission:

```text
rendered prompt tokens + image tokens + reserved output + safety margin
    <= deployed max_model_len
```

Runtime `/tokenize` guards are the authority for actual requests. Plain prose
statistics alone omit schemas, structural serialization, roster, tables and image
tokens. If the runtime count already includes image expansion, do not add image
tokens a second time; the formula above separates components conceptually.
No silent truncation: route oversized inputs to a documented compact or
chunking fallback with coverage retained, or report the explicit failure.

### Actual bounded article profile

The first CPU pilot profiled 62 accepted articles: 35 research articles, 15
reviews, nine formal case reports, two letters and one commentary. This is a
purposive mixed sample, not PMC population percentiles.

| Measurement, n=62 | Mean | Median | P75 | P95 | Max |
| --- | ---: | ---: | ---: | ---: | ---: |
| Retained words | 6,037.8 | 4,317.5 | 7,631.0 | 12,232.7 | 39,343 |
| Characters | 40,911.8 | 30,383.5 | 53,634.0 | 90,533.2 | 230,437 |
| Glimmer prose tokens | 9,208.9 | 6,885.5 | 11,265.2 | 19,999.0 | 60,417 |
| Structural article packet tokens | 10,009.1 | 7,492.5 | 12,526.0 | 22,859.3 | 63,549 |
| Full roster prompt tokens | 22,432.8 | 18,870.5 | 27,714.5 | 44,804.3 | 112,088 |

The nine formal case reports were much shorter: words mean/median/P95
**1,776/1,520/2,850**, prose tokens **2,720/1,975/5,273**. The 14 articles
yielding patients in the strongest pilot arm had words **1,817/1,500/3,466**
and tokens **2,572/1,919/4,942**, but model/validator survival selects that group.

With 16K output reserve and a 512-token margin, only 22/62 roster prompts fit
32K, while 60/62 fit 64K. Structural/schema overhead more than doubled mean
article-packet size in the roster prompt. A later 15-source holdout had a
62,956-token maximum roster prompt before generation: nominal 64K still leaves
little generation room. Eight holdout image calls failed context admission in
the refinement run; these were scheduling failures, not vision reasoning errors.

64K is the tested general baseline; 128K is a supported tested confirmation
candidate, not a blanket quality improvement. The latest overnight GEPA128K
arm matched 85/86 development checks and 28/28 held-out occurrences over only two
seeds. It must be compared with equal seeds, output reserves and source coverage.

### Smaller packets: useful, but not free

Hosted chunking tests found Glimmer section extraction plus local repair retained
60/68 checked facts versus 11/68 for that experiment's whole joint prompt. Spark
windows retained 68/68 versus 64/68 whole. These are different prompts from the
later clinical-domain benchmark. Smaller inputs increased total calls and token
work: Spark windows used 2.66× total input and 2.79× output versus whole.
Re-extraction from final summaries lost substantial detail. Keep source-grounded
atomic facts through merging; a summary must not be the clinical source of truth.

On B200s with the fixed 20-article/17-patient fixture, compact clinical packets
delivered 119/129 checks versus 111/129 whole across three seeds, and valid
timelines 42/51 versus 35/51. The weighted token rates, 5,950 versus 5,838,
differed by only 1.9% and were order/cache-confounded. Compact input is a
provisional clinical-delivery choice, not a demonstrated speed breakthrough.
Retain unresolved blocks, relevant tables, captions, cross-references and a
whole-source fallback; an omitted case paragraph caused a real figure-link miss
in the earlier fixture.

## 6. Repairs, timelines and GEPA affect useful throughput

The first corpus pilot's 64K/32K-prefill direct arm produced 197 valid tasks of
334; targeted repair produced 419/479 and 17 bundles versus 14. Different roster
survival changed downstream denominators. Only one targeted bundle passed every
scope-completeness gate: more valid tasks did not imply complete patients.

Whole-object regeneration frequently erased correct material while making JSON
valid. In the first Glimmer run, a rejected poisoning observations section had
30/33 items individually passing gates, while regeneration had already shrunk
its initial 52 items to 33. NF4/high lost 47 initially matching checks and gained
seven during retries. Preserve accepted facts; quarantine and repair invalid
items locally, with fair scheduling instead of repeatedly retrying the first
few failures.

Later source-aware repair was more useful:

| Four-seed frozen-roster refinement | Strict delivery /172 | Clinical fully valid /952 | Checked facts gained / lost during repair | Generated tok/s |
| --- | ---: | ---: | ---: | ---: |
| Whole, legacy repair | 149 | 920 | 7 / 5 | 6,546 |
| Compact, legacy repair | 155 | 930 | 1 / 4 | 5,962 |
| Compact, source-aware repair | **158** | **947** | **11 / 0** | **5,846** |

Reviewed equivalent encodings raised the last row to 167/172, which remains
finite checklist delivery. Refined prompts/normalization/repair together needed
about 10% fewer calls than compact legacy. This was not an isolated repair-policy
ablation. All prefix-reset calls returned HTTP 404, so these rates are explicitly
cache-uncontrolled; do not rank engine layouts from them.

Protect accepted source-grounded clinical atoms, not every populated candidate
field. Unsupported time offsets, wrong auxiliary ownership, invented audit IDs
and limitations metadata must be correctable. Earlier blanket erasure guards
blocked legitimate fixes. Derive unique exact-quote offsets/hashes in code rather
than asking the model to count characters; record ambiguity and recovered spans.
An absent unit in one representation is not a contradiction of a separately
supported unit. Preserve magnitude, comparator, unit, exponent, analyte and
specimen independently. Flattened `×109/L` is ambiguous; do not silently expand it.

Timeline output needs source-supported **partial order**, not invented dates,
paragraph order or forced total order. Historical care, presentation, plans,
attempts, cancellations, treatment changes and follow-up need separate events.
Graph validity/event counts do not establish correct chronology. Topological
layers are not simultaneous encounters. Keep uncertainty and conflicting source
claims, and evaluate edge precision/recall and fact-to-event linkage separately.

### The latest completed overnight comparison

| Arm | Seeds | Required occurrences | Held-out occurrences | Clinical valid / partial / failed | Eight-GPU tok/s |
| --- | ---: | ---: | ---: | --- | ---: |
| Baseline64K | 4 | 153/172 | 53/56 | 927 / 18 / 7 | 6,175 |
| Focused64K | 4 | 150/172 | 52/56 | 918 / 25 / 9 | 5,989 |
| GEPA64K | 4 | 160/172 | 53/56 | 940 / 6 / 6 | 5,509 |
| Xhigh, 32K output, 64K context | 4 | 159/172 | 54/56 | 932 / 12 / 8 | 5,658 |
| GEPA128K | 2 | 85/86 | 28/28 | 471 / 3 / 2 | 5,490 |
| No repair64K | 4 | 96/172 | 27/56 | 726 / 0 / 226 | 7,667 |

Removing repairs inflated generated-token speed but sharply reduced useful
delivery. GEPA gained seven development matches at 64K but tied baseline on
held-out matches. The held-out denominator represents 14 unique assertions in
five articles repeated across seeds, not 56 independent facts. GEPA trials took
4.40 minutes on average versus baseline's 2.71 because audits added work and
generation. Token rate alone hides this cost. GEPA timeline validity, 52/68,
was slightly below baseline's 53/68.

The extraction stage ended **partial** after 5.247 hours; GEPA took 21.72 minutes
on one B200. All 34 restored new-source packets failed readiness because their
legacy snapshots lacked canonical text/body/supplement metadata, resulting in
zero cached rosters and 12 downstream failures. Two ordinary-decoding trials
were deferred by the deadline reserve. The finished report and cleanup did not
make these experiments successful.

Implemented for the following campaign: hash-verified source
reconstruction and CPU source gates; complete roster-cache checks; per-family
GEPA loggers without global stdout redirection; replay of actual failed repair
candidates; uniform held-out scoring; separate checklist and frozen-source model
feedback across 27 prompt families. The October 5 review confirms successful
logger/source recovery and completed optimization, but not a blanket clinical
quality gain. A model grader is an unadjudicated proxy, never
medical ground truth. Freeze the evaluator; do not optimize its prompt to reward
the same evolving outputs. Split examples at article level to prevent patient,
panel and seed leakage.

Latest completed evidence, `run-20261004-204850`: source-aware baseline delivered
122/129 strict required checks versus clinical GEPA 115/129; focused extraction
delivered 120/129 with 48/51 valid timelines versus baseline 44/51. Weighted output
rates were 5,396/5,040/5,086 tok/s respectively across eight GPUs, including repair
and auxiliary calls but excluding startup. High reasoning passed every clinical
schema task yet retained fewer required facts and took 3.25× baseline trial time.
GEPA ran 3.05 hours on one GPU with 98.2% mean sampled utilization. Keep the
serving recipe, retain source-aware repairs, and optimize chronology/typed-field
completeness before adding another engine sweep. The new-source check contains
only four patients; full medical accuracy is still unestablished. See the
[dated analysis](../reports/GEPA_CLINICAL_20261005.md).

### 6.1 October 5 follow-up: whole patient programs

The last completed run consumed 33.9 minutes for bootstrap, 3.05 hours for
one-GPU family search and 5.37 hours for eight-GPU extraction: about 8.98 active
hours, excluding queue waits. Sampled utilization during family search averaged
98.2% (median 100%); extraction averaged 59.3% (median 99%), including startup
and drain. These are sampled utilization statistics, not throughput ratios or
proof that every phase saturates the node.

Keep the one-GPU allocation for independent family searches. The new
[patient-bundle campaign](PATIENT_BUNDLE_OVERNIGHT.md) separately runs joint
full-program evolution on eight GPUs: every candidate regenerates live rosters,
clinical facts, repairs, backfill, timelines and media. Small article minibatches
would otherwise reach only a few patient-pinned replicas, so this experiment
round-robins independent calls across all endpoints. Coverage/claim audit batches
also run concurrently. Dependencies still await their inputs. **No speed or
quality improvement from these new changes has been measured yet.** Compare
telemetry and useful delivered facts per wall time after the campaign.

GEPA cannot repair a mismatched experiment objective. Previously, many searches
used zero repairs while deployment used two; frozen family intermediates and
independently selected supplements did not capture downstream interactions. The
new family rollouts use deployed repairs and source labels, and joint search
scores completed bundles. Test articles/labels never enter feedback. Fresh
validation must improve the bundle objective while preserving clinical check
matches, known forbidden-fact counts and their scorability. Reflection receives
the actual invoked strategies; seven related prompt groups rotate globally so
Pareto parent selection cannot repeatedly reset to the first prompt. Per-component
proposals, mutations and model invocations are separate receipts. More valid fields or higher generated
tok/s alone do not establish clinical improvement.

The completion passes are a workload choice: rebuild timelines/summaries after
backfill so added treatments acquire source-supported course events. This adds
calls and can reduce articles/hour even if generation tok/s is unchanged. Protect
existing fact links and retain failed completion receipts. Gold relative-order
probes require before/after reachability; source paragraph order is not a clinical
timeline. The benchmark also distinguishes native pixel inventories from
unadjudicated free visual descriptions and clinical interpretations.

For comparisons pool completion-token totals over summed trial wall time. Do not
average per-trial token rates or treat missing usage as zero. Include auxiliary
and repair calls in evaluation rates; report startup, optimizer time and entire
GPU-stage duration separately. The new suite keeps the FP8/DFlash serving recipe
and tests context/prefill/draft confirmations without another unbounded engine
sweep. The corrected suite's eight-hour main-stage work budget includes initialization;
joint search has a separate 90-minute budget on the one-GPU optimization server;
deferred experiments remain explicit. An overnight time estimate excludes queues
and is not a completion guarantee.

## 7. Vision requires a separate workload and quality gate

The first precision gauntlet was text/tables/captions only. It did **not** test
pixels. K2 checkpoints were treated as text-only: mark visual interpretation
unavailable and omit generated image-description fields, while retaining captions,
URLs and text-supported ownership. Missing vision is not an empty normal image.

The focused Glimmer sweep did inspect all 31 fixture assets, three seeds per
method, with separate caption attribution, pixel attribution, description and
joint calls. Vision used two concurrent requests per replica, sequence limit
eight and 16K output cap. Text's 18.7k rate cannot be transferred to this workload.

| Pixel method | Caption valid /93 | Pixel ownership valid /93 | Description valid /93 | Joint valid /93 | Aggregate tok/s |
| --- | ---: | ---: | ---: | ---: | ---: |
| Ordinary FP8 | 9 | 13 | 28 | 6 | 1,035 |
| FP8 + DFlash | 9 | 13 | 27 | 6 | 4,400 |

Most failures concerned nonliteral caption citations, segment IDs, malformed JSON
or disconnected evidence. Some malformed outputs stopped normally at only 4,479
or 5,786 tokens: raising the cap would not repair their syntax. The stage's
`completed` flag required some usable separate outputs, not 93 successful rows.
Agreement among only surviving pairs was not an ownership-accuracy estimate.

Later schema/source improvements raised description validity, but semantic
audits still found invented diagram arrows, PET-only panels tagged as CT, and
whole composite dashboards assigned to a patient despite other patients' beds.
In refinement, descriptions were 39 valid/one partial of 40 while pixel ownership
matched only 11/24 repeated checks with eight unavailable. High structural
validity therefore does not establish vision readiness.

Operational/representation lessons:

- Turn off `--language-model-only` for real multimodal work; include pixels, not
  only image URLs/captions. Count actual image-expanded contexts.
- Separate panel inventory/labels, detailed pixel description/classification,
  caption/article claims and independent patient/panel attribution. Test joint
  versus staged extraction explicitly. Use figure/caption/relevant case mentions
  rather than full unrelated article context for every visual call.
- Store figure/graph type, modality/submodality, domain/body part, visible labels
  and chart readings at panel scope. A graph about MRI is not an MRI acquisition.
  A CoMET instability score is not blood pressure or heart rate.
- Distinguish visibly printed values from estimated plotted points and caption
  measurements. A caption's 200 μm scale does not prove the number is printed on
  the pixels. Species and patient identity require source evidence.
- Reconcile A/B versus top/bottom using a canonical panel registry. Shared captions
  do not prove every specimen belongs to every mentioned patient. Preserve asset
  hashes, URLs and licensing; asset-rights exclusions remain unavailable in scores.
- Caption whitespace recovery must resolve to exact original spans with provenance.
  Noncontiguous quotes need multiple evidence spans. Do not loosen citation gates
  globally or treat recovered text as proof of medical entailment.

## 8. GPU lifecycle and allocation design

### Correct allocation shape

The observed account/QoS **cai5724** allowance is eight GPUs, 32 CPUs and 250 GB
host RAM. Use `slurminfo -g cai5724`; the default output previously showed the
user's other investment allocation. Original 64-CPU/512-GB submission requests
did not fit this envelope; the original traceback alone did not prove the exact
Slurm rejection reason, so preserve scheduler stderr/preflight receipts.

For controlled eight-GPU Glimmer sweeps: one node, `hpg-b200`,
`--gres=gpu:b200:8 --cpus-per-task=32 --mem=250G`, account/QoS cai5724.
TP ranks need colocated communicating GPUs. Eight independent replicas also use
one node here for consistent comparison and simple orchestration; independent
DP is not intrinsically restricted to one node.

Free GPUs across the cluster may be scattered across nodes or unavailable to
this job because of priority, reservations, CPU/RAM needs or account limits.
The earlier scheduled node had `AllocTRES ... gres/gpu:b200=8` despite low CPU
load and ample RAM: all GPUs were allocated. GPU utilization is not availability.
`NODES=1` means one requested/allocated node, not one GPU.

### Reserve GPUs only for the work that needs them

```text
CPU source preparation / token-length profile / dependency and container setup
  → CPU download and checkpoint integrity audit
  → eight-GPU bootstrap / failure examples
  → one-GPU GEPA with concurrent families
  → eight-GPU extraction / layout comparisons
  → CPU checkpoint cleanup → CPU report → CPU article cleanup
```

An earlier eight-GPU process was administratively canceled after 1:53:20, with
a low-utilization email. Small sequential optimization batches while holding
eight GPUs were the problem addressed by splitting the allocation. `CANCELLED
by 0` alone identifies administrative cancellation, not its cause; correlate
the email, logs and telemetry. Most prompt families are independent and can
run concurrently, but evaluate→feedback→proposal steps inside each search have
dependencies. Parallelize independent families/calls, not dependent turns.

The recovery used one GPU for GEPA, eight for extraction. Telemetry median GPU
utilization was 99%, mean 62.3% for GEPA and 58.2% for extraction, including
startup/drain/orchestration. This establishes activity, not continuous saturation
of every device. Downloads, CPU scoring/plots, token-length profiling and cleanup
must not occupy a GPU allocation merely because they belong to an ML campaign.

The latest planned campaign has up to three hours of one-GPU search, a four-hour
GEPA Slurm limit and an eight-hour extraction work budget inside a twelve-hour
allocation. Its estimated 9–12 active hours are a projection, plus CPU/queue
time; no artificial delay pads the runtime. Deadlines may legitimately produce
explicit partial/deferred outcomes.

### Start, stop and drain correctly

Launch servers in waves of two to bound transient host loading memory. Map only
Slurm-assigned devices into each clean container; inspect physical GPU UUIDs
alongside logical `CUDA_VISIBLE_DEVICES` indices. Do not assume device 0 inside
one replica is physical device 0 on the node.

Stop process groups/descendants and verify release before the next layout.
Current drain checks compare assigned physical GPUs with baseline memory,
allow a 512 MiB tolerance, check compute-process inventory and wait up to 120 s.
Abort a contaminated next layout rather than quietly benchmarking less memory.
Never kill unrelated jobs to make the benchmark fit. Readiness/cleanup receipts
and memory/PID telemetry make later failures interpretable.

DSpark initially failed the 90% free-memory startup gate: 156.38 GiB free versus
160.52 GiB requested for FP8, and 149.63 versus 160.52 for NVFP4. Earlier layouts
left apparent persistent allocations on devices that had not yet started their
next-wave replicas. Residual allocations were the leading explanation; old
archives lacked process inventories to establish ownership. Later drains fixed
isolation and all 28 tuning snapshots reported released, but **DSpark was not
retested**. It has no successful inference/quality result here, not a proven
intrinsic incompatibility.

## 9. CPU acquisition, disk ownership and environments

Use CPU jobs to pin/download models, assistants, tokenizer files, containers and
dependencies. GPU jobs use local audited checkpoints and offline HF/Transformers
mode. Own HF/Xet/module/Triton/vLLM/Inductor caches under the campaign's marked
`active-model` tree. Download one verifier and its assistants at a time; cleanup
must succeed before the next download. Do not purge a user's general HF cache.

The K2-375B checkpoint was 229.64 GB; its guard required about 278 GB free and
the guide recommended at least 300 GB shared scratch plus outputs/container.
Container construction separately needed temporary OCI/SIF space. Filesystem
free space is not account quota. On this laptop, stream archive reviews and
extract only bounded artifacts; never download model weights for report review.

PMC/JATS is the full-text/assets source; PubMed is primarily an index/abstract
source. The CPU pilot used official PMC metadata/XML, retained body/tables/captions
and supplements metadata, and excluded bibliography. JATS preserves links/table
structure that flat text loses. Hosted PDF comparisons found Firecrawl native
parsing fast locally (roughly 26–52 ms for the three tested documents), but it
could merge adjacent tables/prose. Local parser speed is neither B200 throughput
nor evidence of more faithful clinical extraction. Keep provenance and a tested
format fallback; see the source-format report rather than assuming PDF is better.

Acquisition uses four concurrent article fetches with shared source-specific rate
and byte reservations. 32 allocated CPUs do not authorize 32 simultaneous calls
to a rate-limited provider. Local parse/tokenizer work can be parallelized with
nested tokenizer/BLAS threads disabled. The measured tokenizer worker trial was
nearly flat: about 13.14 articles/s at one worker and 13.33 at 32. This did not
prove useful scaling; profile work and overhead before reserving more CPUs.

Tokenizer arrays are transient RAM only; retain scalar length statistics and
histograms, not a second tokenized corpus. Offline CPU length profiling does not
bypass vLLM chat rendering or image tokenization at inference. Pretokenized IDs
were not established as a throughput optimization for this workflow.

The pilot fetched only 9.38 MB of cumulative article/metadata response bytes,
despite a policy ceiling of 150 GB. Query lane quotas made discovery incomplete.
The conservative defaults remain bounded; a ceiling is not a target download.
After benchmarking, delete owned acquisition data and tokenizer/model caches,
while retaining bounded source/pixel review packets and results. The first
transfer accidentally included container/environment/cache data and grew to
7.6 GiB; use the result allowlist instead of archiving all scratch.

Use a locked Python3.12 client environment for HPG and the container's interpreter
for vLLM. GEPA is installed by CPU setup with the optimize extra, pinned in the
lockfile (`gepa==0.1.4` for the current campaign). Avoid `--no-sync` against a
Python3.13 host venv while requesting 3.12. `UV_LINK_MODE=copy` handles
cross-filesystem install behavior without changing correctness.

## 10. Failure catalog: symptom, cause and lesson

| Observed symptom | What we learned / current handling |
| --- | --- |
| `CalledProcessError` from `sbatch` without useful explanation | Capture scheduler stdout/stderr. Preflight all resource profiles, submit held jobs, release the complete chain only after acceptance; rollback carefully. Original exception alone did not establish why Slurm rejected it. |
| GPU pending `Dependency`, `StartTime=Unknown` | CPU prerequisite has not finished. A GPU start estimate generally becomes available only once eligible. `Reason=Resources` with dependency cleared means resource scheduling, not stalled download. |
| `FATAL: "python": executable file not found in $PATH` | Clean container lacked alias. Use probed `/usr/bin/python3`; save CPU interpreter/version receipt. |
| `unrecognized arguments: --disable-log-requests` | Launcher flag incompatible with pinned vLLM CLI. Remove/verify against the actual container's serve help. |
| `unrecognized arguments: --calculate-kv-scales` | Experimental FP8-KV layout failed before inference. No FP8-KV speed or quality result exists from it. |
| `DCP not support sliding window.` | DCP2 failed for both Glimmer FP8/NVFP4 in vLLM0.30. Skip that pinned combination; not a universal prohibition on context parallelism. |
| DSpark startup reports insufficient free GPU memory | Check prior process release and startup budget. Failed TP1/TP2 trials did not reach inference; improved drain logic is not a successful DSpark retest. |
| `object NoneType can't be used in 'await' expression` | `ServerGroup.stop()` was synchronous but awaited. This masked the original server startup exception. Call appropriately and preserve original failure plus per-rank logs. |
| HTTP200, `stop`, empty answer but JSON in reasoning | K2 channel/parser mismatch; use publisher delimiters and final-channel extraction. More output budget does not repair that parser. |
| `finish_reason=length` | Investigate reasoning/output cap and input reserve. Preserve partial/raw response; larger caps need context and latency revalidation. |
| Invalid JSON with `finish_reason=stop` well below cap | Genuine syntax/structure failure. Parse safely or request scoped repair; not automatic proof of truncation. |
| Nonliteral caption/time/evidence quote | Recover a uniquely supported exact span in code where justified; otherwise repair/quarantine. Preserve original text and evidence. Do not rewrite clinical facts merely to pass formatting. |
| Whole section fails because a few items fail | Keep accepted items, quarantine unresolved ones, report partial coverage. Validity, completeness and entailment are different metrics. |
| Repair blocked by `field-erasure` for invalid offsets/metadata | Protect verified atoms, allow source-checked correction of unsupported fields; blanket protection made bad proposals unrepairable. |
| GEPA concurrent logger error / closed file | Framework logger redirected process-global streams across threads. Use explicit per-family LoggerProtocol; keep independent search concurrency. |
| All restored sources rejected; zero cached rosters | Review snapshots lacked canonical packet fields. Reconstruct/hash-verify and gate CPU inputs before GPUs; refuse an incomplete roster registry. |
| Prefix-reset HTTP404 | Cache reset did not happen. Record that outcome; restart when a cold regime is required or label performance uncontrolled. |
| Report exists, cleanup deleted, GPU result missing | Report/cleanup can run after failures/cancellation. Inspect Slurm accounting, GPU receipt, server logs and expected trial/task counts. |
| No jobs in `squeue` | Could mean success, failure, timeout or cancellation. Use `sacct` and receipts. |
| `Running package changed` | Submitted jobs pin code/config/lock fingerprints. Keep active checkout immutable; use a new campaign for changed inputs. Do not bypass ownership/integrity checks casually. |
| Existing work-directory refusal | Results are intentionally not overwritten. Pick a new timestamped directory; resume only with the supported compatible recovery route. |
| `invalid choice: corpus-pilot` | Cluster checkout/package was stale. Update through Git or transfer, then verify CLI help. Git updates do not require tar extraction. |
| `--project ./openpatients2` cannot find project | Already inside repository. Run there without a nested project argument, or use an absolute project path. |
| uv failed to hardlink, falls back to copy | Cross-filesystem behavior, not a failed install. `export UV_LINK_MODE=copy` silences the warning. |
| Apptainer underlay >50 bind mounts | Seen in successful container checks too; not the root failure by itself. Read the subsequent fatal/error line. |
| Leaked semaphore/shared-memory warning at shutdown | Does not alone prove inference failed. Check process release, receipts and stage exit; retained worker memory still needs investigation. |

A container preflight that imports Torch/vLLM, finds the architecture/parser and
sees B200 compute capability `(10,0)` proves those checks only. It does not prove
checkpoint loading, quantization kernels, multimodal encoding or successful
generation. Smoke-test actual serving, one text request and one pixel request
before committing a long sweep.

## 11. Measuring and comparing future runs

For every changed serving setting, keep checkpoint/draft commits, container digest,
template, task inputs/roster, sampling/seeds, output caps, validation and repair
policy fixed. Separate experiments for layouts, reasoning, prompts and repair.
Use multiple repeats/seeds and balanced order. Version source snapshots and gold
checks; do not reuse a benchmark label after silently changing its denominator.

Record the following separately:

| Metric | Meaning / accounting |
| --- | --- |
| Aggregate output tok/s | Sum endpoint completion tokens / timed wall interval; never sum rates from overlapping intervals with incompatible denominators |
| Output tok/GPU-second | Tokens / (allocated GPU count × measured interval); account for the whole node allocation |
| Input/prompt tok/s | Prefill-side work; prefix reuse means workload input and newly processed tokens can differ |
| First-pass valid tasks/s | Structurally/source-valid first attempts / timed interval; not all final repaired tasks |
| Supported facts or complete reviewed patients/GPU-hour | Preferred utility metric; needs source adjudication and complete coverage definition |
| Article/hour | State whether discovery, all patient tasks, pictures, repair, startup and audits are included; many tasks belong to one article |
| Per-request latency, TTFT and TTFA | Report median/P75/P95; first reasoning token can precede the first answer by many seconds |
| Per-article input/output distributions | Aggregate all applicable calls, including repairs, discovery and figures; missing usage is unavailable, not zero |
| Full GPU-stage time | Includes startup, warmup, layout switching/drain and inference; queue/CPU time reported separately |
| Quality | Raw/delivered strict checks, reviewed equivalences, valid/partial/failed, omissions, unsupported and wrong-patient claims |
| Speculation/cache/telemetry | Draft/accepted counters, cache hits, assigned GPU utilization, memory/PIDs, startup/drain receipts |

Server logs such as `GPU KV cache usage: 7.6%, Prefix cache hit rate: 81.1%`
refer to occupied KV-cache capacity and measured prefix reuse, respectively.
They are not GPU compute utilization or a direct speedup multiplier. A reasoner
can be compute-busy with little KV memory occupied. Use GPU telemetry and request
rates alongside those logs. Usage fields reporting zero reasoning subtotals did
not prove absence of reasoning: saved responses contained reasoning text.

Cold, warm-prefix, prefix-disabled and resumed runs are distinct regimes. A
disabled-prefix test with repeated inputs is not a unique-article corpus test.
Resumed results may reuse completed tasks; suppress fresh-throughput claims
when their elapsed denominator cannot account for the original work.

CPU/report phases should verify expected trials and required articles, not merely
find a summary. The scheduler uses `afterany` so failed GPU stages can reach
cleanup/report, and `afterok` cleanup gates later downloads. That is intentional
storage hygiene, not success propagation. `partial` and explicit deferred trials
must remain visible even if the Slurm job exits normally.

## 12. HiPerGator runbook: access, accounts, submissions and GPU provenance

### 12.1 Log in and establish which resources you can use

From the Mac:

```bash
ssh wkieffer@hpg.rc.ufl.edu
```

On a HiPerGator login node:

```bash
hostname
id
groups
showAssoc "$USER"
slurminfo -g cai5724 -u
```

`id`/`groups` show Unix group membership; `showAssoc` shows the Slurm account,
default account and available QoS associations. Set **both** account and QoS
when using a secondary allocation; omitted flags can select the primary group's
resources. These concepts are documented in UF's
[secondary-resource guide](https://docs.rc.ufl.edu/scheduler/secondary_resources/).

| Name in our runs | What it controls / proves |
| --- | --- |
| Login user `wkieffer` | Owns submissions and authenticates SSH/SCP |
| Unix primary group `xuefeng.liu` in the pasted job records | Filesystem identity; does not mean every job uses that compute account |
| Slurm account `cai5724` | Compute allocation charged by these benchmark submissions |
| QoS `cai5724` | Scheduling/limit policy selected for that allocation |
| `/blue/cai6734/ehr_agent` | Shared project/results storage used in our runs; distinct from the selected compute account |
| Partition `hpg-b200` | Eligible B200 compute nodes |
| GRES `gpu:b200:8` | Request eight B200 GPUs per node; our jobs request one node |

A storage directory's group and the compute billing account need not have the
same name. Filesystem permissions must allow the job's user to read/write the
chosen directory. A visible directory does not by itself prove a Slurm association.
Our historical jobs ran from `/blue/cai6734/ehr_agent` with `Account=cai5724` and
`QOS=cai5724`; keep both explicitly set rather than inferring them from `pwd`.

The recorded allowance was **eight GPUs / 32 CPUs / 250 GB RAM** across the
cai5724 allocation. Recheck its current limits and running usage before selecting
resources: simultaneous CPU jobs also consume that allocation's CPU/RAM budget.
These are group limits, not an entitlement to every physically free node. See
[UF account/QoS limits](https://docs.rc.ufl.edu/scheduler/qos_limits/).

### 12.2 Use the right directory and prepare the client

```bash
cd /blue/cai6734/ehr_agent/openpatients2
pwd -P
git status --short
git log -1 --format='%H %s'
export UV_LINK_MODE=copy
uv run --locked --python 3.12 --no-dev op2 corpus-pilot --help
```

Confirm the checkout contains the desired command before submitting. If it is
stale, update it through the repository's usual Git workflow **before** starting
jobs. An already updated Git checkout does not need tar extraction. The launchers
create/synchronize campaign-specific environments during CPU stages; activating
the repository's `.venv` is not necessary to run the shown `uv` commands.

Keep outputs beside the checkout, not in a nested nonexistent `openpatients2`
directory. `~/blue_cai6734` was a convenience path in earlier sessions; `pwd -P`
reveals the resolved working directory. Use absolute shared paths in submission
and transfer commands.

```bash
ls -ld /blue/cai6734/ehr_agent
df -h /blue/cai6734/ehr_agent
blue_quota
```

Blue storage is the working-data filesystem; `blue_quota` reports user/group
quota, whereas `df` reports filesystem capacity. Node-local `$SLURM_TMPDIR` is
temporary and removed after the job; anything needed later must be staged back
to shared storage. Our multi-stage campaigns use shared work directories so
successor jobs can run on different nodes. See
[UF practical storage](https://docs.rc.ufl.edu/quickstart/practical_storage/).

### 12.3 Understand the resource flags before changing them

| Flag | Our eight-B200 example | Meaning |
| --- | --- | --- |
| `--account` | `cai5724` | Select compute billing association |
| `--qos` | `cai5724` | Select its permitted scheduling policy |
| `--partition` | `hpg-b200` | Select the eligible hardware queue |
| `--nodes` | `1` | Put this allocation on one node; does not by itself reserve the entire node exclusively |
| `--ntasks` | `1` | One batch orchestrator; it launches replica/TP worker processes itself |
| `--cpus-per-task` | `32` | CPUs allocated to that orchestrator and its children |
| `--mem` | `250G` | Host RAM per node, **not GPU VRAM** |
| `--gres` | `gpu:b200:8` | Eight GPUs per node; TP/DP layout is configured separately in vLLM |
| `--time` | `12:00:00` | Allocation's maximum wall time, not expected completion or queue delay |
| `--chdir` | Absolute repository path | Batch working directory; important for relative configuration/fixture paths |
| `--output` | `/absolute/logs/gpu-%j.log` | Slurm stdout/stderr log; `%j` expands to job ID; parent directory must exist |

We use typed `--gres` consistently. UF requires `hpg-b200` for B200 requests,
at least one CPU per requested GPU, and documents that `--gpus` requests can be
missing from `slurmInfo` GPU accounting. There is no GPU burst QoS; do not switch
these GPU jobs to a `-b` QoS in an attempt to obtain idle GPUs. See
[UF GPU access](https://docs.rc.ufl.edu/scheduler/gpu_access/).

The following are our tested/configured **allocation shapes**, not interchangeable
vLLM launch settings:

| Work | Nodes / B200s | CPUs / host RAM | Serving shape |
| --- | --- | --- | --- |
| Container smoke probe | 1 / 1 | 4 / 16G | Check image/CUDA; no checkpoint inference |
| Current GEPA search | 1 / 1 | 16 / 96G | One TP1 Glimmer server; concurrent optimization families |
| Glimmer extraction/tuning | 1 / 8 | 32 / 250G | Eight TP1 or four TP2 servers, according to cell |
| K2-375B | 1 / 4 | 16 / 250G | One TP4 server |
| K2-32B or 7B | 1 / 2 | 8 / 64G | Two TP1 servers |
| K2-MoVA | 1 / 2 | 8 / 128G | Two TP1 servers |
| Article preparation | CPU only / 0 | Up to 32 / 64G in the corpus pilot | Acquisition, parsing and token-length profiling |

`--ntasks=8` is not required merely because there are eight replicas: our Python
orchestrator launches them within one task. Likewise `--exclusive` is not part
of the proven recipe. Requesting eight GPUs on one node reserves those resources,
not necessarily all CPUs/RAM on the node. Avoid hardcoding historical node names
such as `c1004a-s15`; let Slurm choose an eligible node.

### 12.4 Submit complete campaigns with one command

From inside `/blue/cai6734/ehr_agent/openpatients2`, choose **one** of these
examples. Each submits a full dependency chain and returns; the login shell can
close. They are separate campaigns, not steps to execute together.

```bash
# Current whole-program experiment: CPU → bootstrap8 → GEPA1 → joint/inference8 → cleanup.
bash scripts/run_bundle_overnight.sh

# Previous clinical GEPA experiment.
bash scripts/run_gepa_clinical_overnight.sh

# Same launcher with an explicit new results directory under ehr_agent.
bash scripts/run_gepa_clinical_overnight.sh \
  "/blue/cai6734/ehr_agent/op2-gepa-clinical/run-$(date +%Y%m%d-%H%M%S)"

# Focused Red Hat FP8 serving/layout benchmark.
bash scripts/hpg_glimmer_tune.sh --work-dir \
  "/blue/cai6734/ehr_agent/op2-glimmer-tuning/run-$(date +%Y%m%d-%H%M%S)"

# Historical precision gauntlet: FP8, NVFP4 and NF4, one verifier at a time.
bash scripts/hpg_glimmer.sh --work-dir \
  "/blue/cai6734/ehr_agent/op2-glimmer-runs/run-$(date +%Y%m%d-%H%M%S)"

# Historical four-model K2 benchmark with per-model GPU counts.
bash scripts/hpg_benchmark.sh --work-dir \
  "/blue/cai6734/ehr_agent/op2-k2-runs/run-$(date +%Y%m%d-%H%M%S)"
```

Equivalent direct `uv` submission for the focused FP8 search:

```bash
uv run --locked --python 3.12 --no-dev op2 hpg-benchmark submit \
  --config configs/hipergator/glimmer-fp8-tuning.yaml \
  --work-dir "/blue/cai6734/ehr_agent/op2-glimmer-tuning/run-$(date +%Y%m%d-%H%M%S)"
```

To inspect this search's intended commands without submitting or downloading:

```bash
uv run --locked --python 3.12 --no-dev op2 hpg-benchmark plan \
  --config configs/hipergator/glimmer-fp8-tuning.yaml \
  --work-dir "/blue/cai6734/ehr_agent/op2-glimmer-tuning/plan-$(date +%Y%m%d-%H%M%S)"
```

The plan's placeholder dependency IDs are for review, not a submission script to
execute. Use `submit` with a separate new directory when ready. Campaign launchers
preflight resource requests and submit held jobs before releasing the chain;
save the printed work directory and IDs. Keep the checkout unchanged while those
jobs are active. Do not wrap the campaign submit command in another eight-GPU
`sbatch`: it would unnecessarily reserve GPUs just to submit other jobs.

### 12.5 Direct batch and interactive examples for diagnosis

To submit the repository's existing **one-GPU container probe**, using a SIF
already downloaded by CPU setup:

```bash
cd /blue/cai6734/ehr_agent/openpatients2
mkdir -p /blue/cai6734/ehr_agent/op2-diagnostics

# Replace this with an actual retained image path.
sif=/absolute/path/to/vllm.sif

# Test scheduling validity without creating a job.
sbatch --test-only --account=cai5724 --qos=cai5724 \
  --partition=hpg-b200 --gres=gpu:b200:1 \
  --nodes=1 --ntasks=1 --cpus-per-task=4 --mem=16G --time=00:10:00 \
  scripts/hpg_container_check.sbatch "$sif"

# Submit the real probe; the printed number is its Slurm job ID.
sbatch --account=cai5724 --qos=cai5724 \
  --partition=hpg-b200 --gres=gpu:b200:1 \
  --nodes=1 --ntasks=1 --cpus-per-task=4 --mem=16G --time=00:10:00 \
  --chdir=/blue/cai6734/ehr_agent/openpatients2 \
  --output=/blue/cai6734/ehr_agent/op2-diagnostics/container-%j.log \
  scripts/hpg_container_check.sbatch "$sif"
```

That script imports Torch/vLLM and checks GPUs, architecture and parser. It does
not download a model or validate checkpoint inference. The explicit flags above
mirror its `#SBATCH` defaults. `sbatch` queues work and returns; commands inside
the script run later on the allocated compute node, not on the login node.

For a short interactive GPU diagnosis, request a compute shell:

```bash
srun --account=cai5724 --qos=cai5724 \
  --partition=hpg-b200 --gres=gpu:b200:1 \
  --nodes=1 --ntasks=1 --cpus-per-task=4 --mem=16G --time=00:15:00 \
  --pty bash -l

# Run these after the allocated shell starts.
hostname
echo "$SLURM_JOB_ID"
echo "$CUDA_VISIBLE_DEVICES"
nvidia-smi

# Finish the allocated shell when the diagnosis is complete.
exit
```

Do not launch inference on a login node. If an interactive allocation is still
pending, cancel/exit that request instead of submitting duplicates. For unattended
overnight work use the batch campaign launchers, not a long interactive shell.

### 12.6 Verify allocation and retain GPU provenance

From the login node, replace `JOB_ID` with the actual submitted ID:

```bash
scontrol show job JOB_ID
sacct -j JOB_ID \
  --format=JobID,JobName%32,Account,QOS,Partition,State%24,ExitCode,AllocTRES%80,NodeList%24,Elapsed
squeue --start -j JOB_ID
```

Read `Account`, `QOS`, `Partition`, `ReqTRES`, `AllocTRES`, `NodeList`, `WorkDir`,
`StdOut` and `Dependency` in the job record. Requested resources are not proof of
allocated resources; pending jobs have no completed allocation yet. After a job
ends, `sacct` is the accounting record. If checking a scheduled node, use its
actual reported name with `scontrol show node NODE_NAME` and inspect allocated
GPU TRES, not only CPU load or memory.

Inside a GPU job/allocated shell, this prints a useful allocation identity record:

```bash
date -u
hostname
id
env | sort | rg '^(SLURM_(JOB_ID|JOB_ACCOUNT|JOB_QOS|JOB_PARTITION|JOB_NODELIST|JOB_GPUS|STEP_GPUS|CPUS_PER_TASK)|CUDA_VISIBLE_DEVICES)='
nvidia-smi --query-gpu=index,uuid,name,pci.bus_id,driver_version,memory.total \
  --format=csv
```

If `rg` is unavailable on the cluster, use `grep -E` with the same expression.
The NVIDIA inventory may expose more hardware than the application's allocation;
do not infer ownership from a GPU appearing in `nvidia-smi`. Record Slurm's
assigned devices and physical UUIDs alongside each server's container
`CUDA_VISIBLE_DEVICES` and Torch-visible logical devices. Logical index zero can
map to a different physical GPU in every replica.

For each benchmark keep this chain of provenance:

```text
user + Unix groups
  → Slurm job ID + account/QoS + partition + requested/allocated TRES + node
  → assigned physical GPU UUIDs + driver/capabilities
  → per-server CUDA mapping + actual launch argv + runtime versions
  → container/checkpoint/drafter/template/config/source hashes
  → raw task outputs + utilization/counters + timing + drain/cleanup receipts
```

Our launchers already retain much of this in `jobs.json`, `submission.json`,
`slurm-commands.json`, `campaign.json`, container probes, server deployment files
and GPU telemetry. Keep those with transferred results. For deeper diagnoses,
include the `scontrol`/`sacct` output so a throughput number can be tied to the
actual account, node and GPU allocation rather than a planned YAML layout.

### 12.7 Progress, completion, cancellation and result transfer

Run launchers **inside the updated repository**. The current broader campaign
is an experimental successor, not a measured improvement:

```bash
cd /blue/cai6734/ehr_agent/openpatients2
export UV_LINK_MODE=copy
bash scripts/run_gepa_clinical_overnight.sh
```

The smaller focused serving search remains available with:

```bash
bash scripts/hpg_glimmer_tune.sh --work-dir \
  "$PWD/../op2-glimmer-tuning/run-$(date +%Y%m%d-%H%M%S)"
```

Inspect queue/allocation without guessing from cluster-wide utilization:

```bash
watch -n 15 'echo "=== YOUR JOBS ==="; squeue -u "$USER" -o "%.12i %.12P %.32j %.12T %.12M %R"; echo; slurminfo -g cai5724 -u'

# Use the actual job ID; the estimate may be unknown while dependencies remain.
squeue --start -j JOB_ID
scontrol show job JOB_ID
sacct -j JOB_ID --format=JobID,JobName%32,State%24,Reason%40,ExitCode,Elapsed,Start,End
```

Change the directory pattern to the campaign family you actually launched.
Choosing the newest directory is a convenience, not proof it is the intended run:

```bash
ls -dt /blue/cai6734/ehr_agent/op2-gepa-clinical/run-*/
run_dir=$(ls -dt /blue/cai6734/ehr_agent/op2-gepa-clinical/run-*/ | head -n 1)
run_dir=${run_dir%/}
echo "$run_dir"
cat "$run_dir/SUMMARY.md" "$run_dir/COMPARISON.md"
cat "$run_dir/cpu.json" "$run_dir/gepa.json" "$run_dir/gpu.json"
cat "$run_dir/engine/results/glimmer-fp8/cleanup.json" "$run_dir/source-cleanup.json"
ls -lh "$run_dir/logs/"
```

If a file is absent, investigate rather than assuming success. Find the actual
GPU/per-replica log paths under that work directory and read their tails. Inspect
`gpu.json` failures, `trials.json`, `schedule-outcome.json` and GEPA components
for deferred/unavailable work. For old `hpg-benchmark` runs the GPU receipt is
`results/MODEL/gpu.json`, rather than the corpus pilot's top-level `gpu.json`.

Verify CLI support against the actual image before adding flags; this CPU help
probe needs no checkpoint/GPU (load the site's Apptainer module if needed):

```bash
apptainer exec --cleanenv /absolute/path/to/vllm.sif \
  /usr/bin/python3 -m vllm.entrypoints.cli.main serve --help
```

Ctrl-C on the submitting shell does not cancel Slurm jobs. To cancel, use explicit
campaign IDs from `jobs.json`, check `squeue`, and wait for inference to stop
before owned cleanup. Canceling every stage also cancels its scheduled cleanup;
use the documented cleanup/recovery route afterwards. Avoid account-wide kills.

Create the result allowlist archive **on HiPerGator**, after reviewing completion:

```bash
cd /blue/cai6734/ehr_agent/openpatients2
uv run --locked --python 3.12 --no-dev op2 corpus-pilot export-results \
  --work-dir "$run_dir" --output "${run_dir}-results.tar.gz"
```

From the **Mac terminal**, substitute the printed exact remote path:

```bash
scp wkieffer@hpg.rc.ufl.edu:/blue/cai6734/ehr_agent/op2-gepa-clinical/run-TIMESTAMP-results.tar.gz \
  ~/Downloads/
```

A single compressed tar avoids per-file SCP latency from thousands of JSONs and
usually reduces transfer bytes. Do not include checkpoint trees, SIFs, virtual
environments or caches. Retain raw attempts, selected full source packets,
bounded pixel assets, hashes and receipts so clinical review remains possible
after acquisition cleanup.

## 13. What remains unproven, and what to test next

| Question | Evidence status / next useful test |
| --- | --- |
| Is FP8-KV faster and clinically safe? | No inference result; fix/verify version-specific configuration, then matched text and vision quality/speed trials |
| Does DSpark beat DFlash? | No successful DSpark trial; retest only after clean-memory checks, recording assistant/runtime compatibility and counters |
| Does DCP help long Glimmer context? | Pinned vLLM0.30 sliding-window/DCP2 combination failed; revisit only with verified upstream support |
| Is a different FP8/INT8 quantizer stronger? | Not established; identical weight hashes are not a new comparison |
| Is 8K prefill universally best? | No; 8K/16K/32K rates were close, and real pipeline context/repair loads differ |
| Is c64 optimal for images or huge contexts? | No; image tests used c2 and require separate memory/latency/quality tuning |
| Does 128K improve clinical completeness? | Early two-seed GEPA confirmation was promising, but the 2026-10-07 review finds no convincing global gain. Test matched seeds on adjudicated overflow sources; avoid blanket context escalation. |
| Does GEPA generalize? | In the 2026-10-07 review, conditions/observations rewrites raise live development checklist delivery from 707/777 to 728/777, but optimizer-held-out delivery falls from 138/147 to 135/147. Joint GEPA retains originals. Test these components separately on new source-reviewed holdouts. |
| Do visual descriptions and patient attribution work reliably? | Structural gains coexist with incorrect arrows/modalities/composite ownership; adjudicate panels and chart readings |
| Can we scale ingestion/throughput to the entire corpus? | CPU worker scaling was flat; query discovery was capped; unique-article serving and complete-patient rates remain unmeasured |
| Are long relative patient histories ready for synthetic EHRs? | Useful events survive, but temporal edges/state links still fail; score chronology and omissions against source-derived gold |

Prefer bounded, stratified, source-adjudicated confirmations of the current
FP8/DFlash deployment before another broad quantization sweep. Include single
and multiple patients, long/table-heavy articles, nonhuman cases, cited follow-up
versus cited-only mentions, mixed-owner figures and difficult timelines. Track
unsupported modifiers and ownership leakage outside the checklist. A citation
link is evidence for investigation, not permission to merge two patients.

Implementation references: [server lifecycle](../src/openpatients2/serving.py),
[GPU telemetry](../src/openpatients2/gpu_telemetry.py),
[HiPerGator stages](../src/openpatients2/hipergator.py),
[Glimmer layouts](../src/openpatients2/glimmer_benchmark.py),
[tuning selection](../src/openpatients2/glimmer_tuning.py),
[corpus orchestration](../src/openpatients2/corpus_pilot.py),
[extraction](../src/openpatients2/pilot_extract.py),
[GEPA](../src/openpatients2/prompt_optimization.py),
[frozen feedback](../src/openpatients2/gepa_feedback.py).
For full run procedures, see [K2](HIPERGATOR_K2.md), [Glimmer](HIPERGATOR_GLIMMER.md),
[FP8 tuning](HIPERGATOR_GLIMMER_TUNING.md), [corpus](CORPUS_PILOT.md) and
[current clinical GEPA](GEPA_CLINICAL_OVERNIGHT.md). Future results should update
this reference with dated evidence; keep unsuccessful experiments and historical
limitations visible.

## 14. Measured update — run 20261006-142003, reviewed 2026-10-07

Full evidence and limitations:
[patient-bundle review](../reports/BUNDLE_CLINICAL_20261007.md) and
[counts/receipts](../reports/BUNDLE_CLINICAL_20261007.metrics.json).

- **Right-sizing joint GEPA worked.** One-GPU independent search averaged 99.4%
  active utilization; one-GPU joint search averaged 98.9%, versus the previous
  eight-GPU joint search's 42%. Main eight-GPU extraction averaged 81.6%, median
  99%. These are phase-tagged device-sample averages, not time-weighted kernel
  measurements. High utilization does not establish clinical improvement.
- **Retain DFlash for the current FP8 recipe.** The matched seed-42 original
  live-complete cells produced 8,858 output tok/s with DFlash and 2,550 with
  ordinary decoding, a 3.47× rate ratio. Rates aggregate eight GPUs and include
  auxiliary/repair calls while excluding startup. One seed does not establish
  clinical equivalence; the companion ordinary `gepa` cell was deferred.
- **High reasoning and blanket 128K remain unjustified.** High reasoning took
  about 1.64× the original live-complete arm time, delivering fewer checked
  clinical facts. Two-seed 128K original trials delivered 472/518 clinical probes
  and 24/82 ordering probes. Use 64K context/32K prefill, medium reasoning and TP1
  replicas as the current experimental recipe, escalating documented hard cases.
- **Completion prompt construction still limits useful work.** All 34 main
  pre-call context overflows occurred in timeline completion; summary completion
  had none. Timeline completion was valid in only 487/872 calls. Compact evidence,
  short locally mapped IDs and additions/edge-only patches are more useful next
  experiments than universally allocating larger KV caches. Preserve verified
  event links in code instead of repeatedly regenerating the complete graph.
- **Use patient outcomes alongside token rates.** Clinical-only GEPA delivered
  728/777 development probes versus original live-complete's 707/777 at similar
  token rates (8,498 versus 8,543 tok/s), but scored worse on the optimizer's
  held-out probes. Extra generated tokens are not extra supported patient facts.
  The revised reference and repeated seeds are not independent population samples.
- **Budget partial work explicitly.** Forty-three comparisons completed; one
  ordinary-decoding comparison was deferred at the main deadline reserve.
  Allocated GPU-stage wall time summed to 8h 33m across bootstrap, one-GPU
  optimization and main evaluation, excluding CPU acquisition and queue waiting.
  Partial completion is different from server failure or failed extraction tasks.
- **A finished optimizer may select no change.** Joint search completed fresh
  original-control confirmation, but accepted no mutation. Five group proposals
  tied or lost on training batches, a timeline/summary reflection failed its
  requested-key contract, and the last vision rollout exhausted its budget.
  Reject malformed or schema-conflicting reflection before costly rollouts;
  optimize only components actually invoked by the evaluated deployment path.

## 15. Proposed independent-GPU pilot — not yet hardware-benchmarked

The [frontier pilot](FRONTIER_PILOT.md) runs four independent one-B200 Slurm jobs,
each with a TP1 FP8/DFlash server and disjoint article subset. They may run on
different nodes or start at different times. No cross-node model communication
is needed because each GPU holds its own model replica. This does not apply to
a future model that requires tensor parallelism across GPUs.

The default allocation is four times 8 CPUs / 60 GB / one GPU: 32 CPUs, 240 GB,
four GPUs in total, within cai5724's 32 CPU / 250 GB / eight GPU limit. The optional
eight-worker plan uses 4 CPUs / 30 GB per worker; it has less per-worker host
memory and CPU headroom and has not been validated on this cluster. Start with
four. Independent jobs remove the requirement to find eight free GPUs on one
node; they do not guarantee shorter queues.

CPU jobs prepare fixtures and dependencies, download one shared checkpoint,
aggregate results and clean downloads. GPU jobs perform server initialization,
warmup and inference with local request validation; they do not download models
or run corpus preparation. Per-worker writable caches are isolated from the
read-only shared checkpoint. Cleanup waits for all workers and checks ownership.

Each worker has a 90-minute allocation by default, including initialization;
incomplete cells are explicitly deferred. This is a budget, not a measured
completion estimate. Never add rates from workers that ran at different times
and label that number simultaneous throughput. The report uses total generated
tokens divided by summed measured GPU arm-seconds, plus coverage and clinical
scores. Startup, queue waiting and missing usage are reported separately.

The [source-ledger experiment](EVIDENCE_LEDGER_DESIGN.md) may reduce repeated
prefill and oversized timeline prompts, but it adds map/audit calls and may
reduce prefix reuse. Use supported clinical facts and ordered events per GPU
time to decide whether it helps; output token rate alone cannot settle this.
