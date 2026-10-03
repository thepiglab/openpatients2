# Bounded PMC acquisition and Glimmer extraction pilot

Run CPU preparation first. This workflow does not submit GPU work automatically
and never runs inference over the entire acquired corpus. A download budget is a
ceiling, not an instruction to fill storage.

## Evidence behind the configuration

[Tuning review](../reports/GLIMMER_TUNING_20261002.md) audited the supplied
`run-20261002-120538` archive. The leading confirmed **text** configuration is
[Red Hat FP8 Glimmer](https://huggingface.co/RedHatAI/Muse-Glimmer-30B-FP8-block)
at `1deb4641ff84f9a728dd11b27cac1f6a02a9ed14`, eight independent TP1 replicas,
Meta DFlash, medium reasoning, T=1, top_p=.95, top_k=64, prefix caching and
64 in-flight requests/replica. It measured 18,715 generated tokens/s across eight
B200s, including reasoning. This is a warm repeated-prompt rate, not a promise
for newly downloaded diverse articles. The best measured prefill budget was
8192; its advantage over 32768 was small and repeats overlapped.

Those scheduler budgets are **not context lengths**. The archived run used a
65536-token context. The new matrix compares actual contexts 32768/65536 and
prefill budgets 8192/32768 on the same selected sample. There is no 128k GPU
claim or production default change. A 128k context-fit statistic is descriptive
only. FP8 checkpoint weights are used; no BF16 checkpoint baseline is downloaded.

The old image stage is not ready for production: most attribution/description
tasks failed, and spot review found unsupported pixel claims even in plausible
outputs. The new pilot separately runs raw pixel description and ownership,
with caption evidence, immutable source/pixel hashes, panel metadata and an
independent direct/targeted arm. Unique contiguous whitespace citation recovery
is recorded and reversible. It never joins source chunks or repairs medical
meaning silently. Medical validity and recall need source adjudication.

## 1. Transfer the code

The small allowlisted `dist/openpatients2-corpus-pilot.tar.gz` contains code,
configs, schemas and frozen regression fixtures. It contains no weights,
downloaded corpus, environments or credentials. Extract it into the cluster
checkout (not into an active running campaign). No original result archives need
to be transferred back to the cluster.

From your Mac, using your usual HiPerGator SSH hostname:

```bash
scp dist/openpatients2-corpus-pilot.tar.gz \
  wkieffer@hpg.rc.ufl.edu:/blue/cai6734/ehr_agent/
```

On HiPerGator:

```bash
cd /blue/cai6734/ehr_agent/openpatients2
tar -xzf ../openpatients2-corpus-pilot.tar.gz
export UV_LINK_MODE=copy
```

## 2. CPU only: sources, token statistics, bounded assets

```bash
cd /blue/cai6734/ehr_agent/openpatients2
work_dir="/blue/cai6734/ehr_agent/op2-corpus-pilot/run-$(date +%Y%m%d-%H%M%S)"
uv run --locked --python 3.12 --no-dev op2 corpus-pilot submit-cpu \
  --work-dir "$work_dir"
```

Save the printed work directory. The CPU job requests 32 CPUs/64 GB and no GPUs,
within your cai5724 allocation of 32 CPUs/250 GB/eight GPUs. Four concurrent
article fetches hide network latency while shared request/byte reservations and
source-specific rate limiters enforce the acquisition limits. Extra CPUs do not
raise the PMC request rate. Token-length counting uses 32 external workers with
nested tokenizer/BLAS threading disabled to avoid oversubscription.
It discovers at most 400 candidate lane memberships across ten topics: 30
case-focused plus 10 broader candidates/topic, deduplicating overlap and keeping
all selecting query receipts. Recent date-ordered query prefixes are purposive,
not random PMC sampling. Coverage is explicitly incomplete. Individual patients
must still be discovered by the model; a case-report tag is not mandatory and
human species is not a hard gate.

Official PMC OA Cloud metadata and JATS XML are primary. PubMed provides article
indexing/abstracts; PMC supplies full text/assets. Bibliography is excluded while
abstract/body/captions/table rows and supplementary manifests are retained.
Supplementary payloads are not downloaded. Rights/retraction/version gates and
bounded/resumable checksums run before inference.

Default budgets: 2 GB cumulative decoded article/metadata network bytes,
4 GB acquisition storage, 128 MB/invocation, 5 MB/XML, 100 acquisition invocations.
The policy's absolute article/metadata network ceiling is **150,000,000,000
bytes**; do not raise the default until this pilot is reviewed. Storage/export
budgets and filesystem quotas are separate. Tokenizer files (~28 MB), selected
pixels (at most 64 MB), and later model/container setup have separate caps; they
are not charged as article downloads. Network figures count decoded response
bytes, not protocol overhead.

The CPU stage downloads only the two pinned tokenizer files, no model weights.
It counts characters, words, prose tokens, structural article tokens and exact
rendered roster prompt tokens; reports mean/median/P75/P90/P95/P99 by topic and
overall. Worker trials 1/2/4/8/16/32 use a fixed workload; they are small warm CPU
measurements. Token arrays exist transiently in RAM and are **never saved**.
The raw eligible export stays compressed; the 48-article sample is a small
duplicate needed for repeatable extraction, not a tokenized corpus copy.
`profile/token-histograms.png` and `.svg` show cleaned prose and full-prompt
lengths, each with linear/log axes and median/P75/P95 markers. The JSON alongside
them stores reproducible bin edges/counts; no token arrays are saved.

```bash
uv run --locked --python 3.12 --no-dev op2 corpus-pilot status --work-dir "$work_dir"
cat "$work_dir/profile/SUMMARY.md"
cat "$work_dir/cpu.json"
```

Inspect eligibility/review counts, topic balance and length tails. `cpu.json`
must say `ready` before the next command. This means the bounded inputs were
prepared, not that every query or the entire PMC archive was downloaded.

[Live concurrent smoke receipt](../reports/PMC_ACQUISITION_PARALLEL_SMOKE.json): three
candidates, two eligible articles, one rights-review row, 107015 received bytes,
MD5 checks passed, resumed fetch made zero requests, all temporary payloads
deleted. [Tokenizer smoke](../reports/GLIMMER_TOKENIZER_SMOKE.json): pinned
files verified, nine frozen articles counted, temporary 28 MB snapshot deleted.
Neither smoke supplies population length estimates or medical accuracy.

## 3. Explicit GPU sample experiment

After reviewing CPU output:

```bash
uv run --locked --python 3.12 --no-dev op2 corpus-pilot submit-gpu \
  --work-dir "$work_dir"
```

Optionally add `--sif /absolute/path/to/verified/vllm.sif` to reuse a previous
vLLM 0.30 container. Setup verifies it. The chain preflights resources, submits
held jobs, then releases successors before their parent: CPU container setup,
CPU pinned FP8/DFlash download, one-node/eight-B200 GPU experiment, CPU owned
checkpoint deletion, CPU report, then CPU article-cache deletion. GPUs are not reserved by CPU jobs. Scheduler
queue time is excluded from inference rates. Failed setup/download stages still
reach readiness failure and cleanup rather than leaving a dependency stranded.
Only 48 selected articles, at most 96 patients and 12 pixel figures per arm are
processed. Per-arm calls/tokens/output caps are explicit in
`configs/pilot/glimmer-extraction.yaml`; limits and omissions are reported.

The matrix runs independent direct and targeted arms at the same temperature,
seed, source sample and initial source-based prompts. Patient discovery and
figure/panel attribution precede patient-scoped extraction across all clinical
domains, summaries and timeline-v2 audits. Targeted repair preserves supported
neighbors, bounded attempts and raw responses; unsupported facts are not made
valid by erasing them. Dependent timeline prompts can differ when the accepted
fact registry differs. The fixed historical article/checklist regression is
also replayed at context65536/prefill8192 for continuity with hosted results.

Context guards use the same complete request at the deployed vLLM `/tokenize`
endpoint. This includes processed image expansion, verified against v0.30
[tokenize source](https://github.com/vllm-project/vllm/blob/v0.30.0/vllm/entrypoints/serve/tokenize/serving.py)
and [renderer](https://github.com/vllm-project/vllm/blob/v0.30.0/vllm/renderers/online_renderer.py).
API failures, non-fitting prompts and unknown images remain explicit failures;
source text is never silently truncated. This pilot does not yet implement
long-document chunk/merge inference; those sources remain identifiable for a
separate coverage-preserving experiment.

Facts carry literal evidence locations and experimental coverage/timeline
contracts. Coverage units remain `unresolved` until reviewed. Dates/relative
time anchors preserve stated precision rather than inventing encounters.
Missing pixels do not produce fabricated visual columns. Numerical chart data,
modality/submodality, medical domain/body parts, general detailed description,
clinical interpretation, and patient/panel ownership remain distinct claims.
Figure asset exceptions can block an image even when the article license passes.

## Outputs and interpretation

- `cpu.json`, `profile/SUMMARY.md`, `profile/profile.json`: source, lengths, context
  fit, worker trials and immutable input receipts.
- `extraction/context*-prefill*/{direct,targeted}/report.json`: valid/failed counts,
  per-article input/output/reasoning token distributions, calls and throughput.
- Per-arm append-only attempts, task responses, patient bundles, source/evidence
  contracts, figure columns and source adjudication forms.
- `gpu.json`, `summary.json`, `SUMMARY.md`: stage completion/failures, checkpoint
  cleanup. `progress.json` and `logs/` allow monitoring.
- `source-cleanup.json`: verified deletion of the owned acquisition database,
  downloaded article exports/input sample and tokenizer after the report. It
  preserves output bundles (including their cited source evidence), scalar counts,
  histograms, logs and at most 64 MB of figure pixels for adjudication. CPU inputs
  no longer pass replay verification after cleanup; a fresh campaign is required.
  If a GPU job is canceled before it records a terminal attempt, cleanup defers
  rather than assuming all benchmarking is finished; its error receipt explains why.

Validator-valid tasks, literal support, medical entailment, patient attribution
and complete recall are separate metrics. Fresh-source accuracy is **unreviewed**
until reference checks/independent source review exist. Compare like-for-like
arms, including repair cost and final delivered facts. An experiment completing
is not equivalent to every task passing.

[Paperclip evaluation](PAPERCLIP_EVALUATION.md): documented search is ranked and
bounded; the public REST API does not expose exhaustive pagination or license
filters, and data endpoints required authentication in our small probes. CLI
figure/fulltext capabilities are promising but authenticated content fidelity,
licenses, upstream URLs and rate limits remain unverified. Official PMC remains
the acquisition path; Paperclip can be tested later as an authenticated discovery
aid. [Implemented license policy](ARTICLE_LICENSE_POLICY.md) supports appropriate
CC, public-domain and permissive article grants without blanket relicensing.
