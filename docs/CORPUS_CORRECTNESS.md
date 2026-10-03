# Fixed-source correctness campaign

The correctness configuration uses the pinned twenty-article fixture at
`benchmarks/corpus-correctness/articles.jsonl.gz`, its hand-reviewed
`rosters.json`, and `reference.json`. Preparation hashes all three inputs into
the campaign runtime manifest. It refuses changed inputs before running.
This campaign makes no broad PMC searches and downloads no article bodies.

Run this command **on HiPerGator** from the separate packaged checkout:

```bash
uv run --locked --python 3.12 --no-dev op2 corpus-pilot submit \
  --config configs/pilot/correctness.yaml \
  --work-dir "/blue/cai6734/ehr_agent/op2-corpus-correctness/run-$(date +%Y%m%d-%H%M%S)"
```

An existing compatible SIF may be supplied with `--sif /absolute/container.sif`.
The command prepares and submits a held chain, then releases its successors
before its first job: CPU preparation, container setup, checkpoint download, GPU
evaluation, checkpoint cleanup, report, and owned article-source cleanup. Each
successor uses `afterany`, so an unsuccessful evaluation still reaches cleanup.
Only the GPU phase requests GPUs: one node with 32 CPUs, 250 GB RAM and eight
B200 GPUs. CPU preparation uses 32 CPUs and 64 GB RAM. No model checkpoint or
GPU inference runs on the submitting machine.

The CPU phase copies the fixed fixture into owned campaign storage, prepares the
two pinned tokenizer files, and produces scalar token statistics and histograms.
All twenty articles remain in the inference sample; there is no new random
article selection. It copies the reviewed rosters to `profile/rosters.json` and
pins their hash. At most twelve selected figures and 64 MB of pixels are prepared
through the rights and integrity gates; skipped figures remain visible.

The GPU phase uses one 65,536-token context with a 32,768-token prefill scheduling
budget. Seeds 42, 43 and 44 each have an independent live patient-discovery trial.
Their predictions are evaluated separately from the reviewed roster. Extraction
then runs two matched targeted variants for every seed:

- `whole-targeted`: whole-article patient input.
- `compact-targeted`: patient-section input.

Both use the same hand-reviewed frozen roster, so discovery failures do not
change their patient denominator or suppress figure evaluation. Variant order
alternates by seed: whole/compact, compact/whole, whole/compact. The runner stores
the seed, scope, execution order and frozen-roster hash with each result. Six
extraction trials and three discovery trials share the same article set and
server layout. Literal evidence checks and reference-assertion fidelity do not
establish correctness of facts outside the reviewed reference.

The source review identifies 17 patients across 14 articles, with six additional
articles testing exclusions and figure scope. Clinical extraction always has the
same 272 task slots per trial (14 clinical sections, summary and timeline per
patient); figure/pixel tasks are counted separately in detailed reports. Roster
conditioning is part of this experiment and is not automatic patient discovery.
One anonymous illustrative procedure case remains provisional and is excluded
from the definitive discovery-count denominator. The scorer checks 43 required
facts, eight forbidden facts and six figure-ownership assertions. These are
limited development checks, with manual review still needed for other claims.

Both extraction variants use medium reasoning, T=1, top_p=.95, top_k=64, a 16k
output allowance and at most two targeted repair calls. Eight FP8 TP1 replicas
use the existing DFlash head. Source text is never truncated to satisfy a context
limit. Compact input retains target blocks, unresolved blocks, tables and captions,
with their original segment IDs and text. The gold fact checklist is used only by
the CPU scorer, never in model prompts.

Timeline generation asks for exact quote/segment pairs. Code computes source IDs,
hashes and Unicode offsets only for unique literal matches, then applies the
ordinary patient, time-expression and graph gates. Ambiguity and conflicting
source identities remain failures. The audit preserves the raw model output and
every mechanical change. Targeted item repair freezes accepted neighbors and
retains unresolved originals; no documented value/time can be erased merely to
pass a gate. Normalized schema enums no longer need to appear verbatim in source
prose, while clinical values and patient attribution retain separate support and
review requirements. These reports do not automatically promote clinical facts.

Inspect progress and collect results:

```bash
uv run --locked --python 3.12 --no-dev op2 corpus-pilot status \
  --work-dir /blue/cai6734/ehr_agent/op2-corpus-correctness/run-YOUR_TIMESTAMP

uv run --locked --python 3.12 --no-dev op2 corpus-pilot export-results \
  --work-dir /blue/cai6734/ehr_agent/op2-corpus-correctness/run-YOUR_TIMESTAMP \
  --output /blue/cai6734/ehr_agent/correctness-run-01-results.tar.gz
```

The result archive has a 2 GB uncompressed cap and includes reports, extraction
audits, logs, scalar counts, histograms and bounded review pixels. It excludes
the SIF, model checkpoints, environments, tokenizer caches and complete source
article copies. It refuses existing archive paths and symlinks. Transfer this
small result archive instead of the entire campaign directory.

After the benchmark attempt ends and checkpoint deletion is verified, source
cleanup removes the owned acquisition database, article exports, selected source
sample and tokenizer directory. It preserves extraction results, profile reports,
scalar counts, histograms, reviewed rosters and review pixels. The original pinned
fixture in the package remains an input. Ownership, terminal GPU status and
nonblocking writer locks are required before any deletion. Failed cleanup stays
visible in the summary; it does not widen the deletion allowlist.

The original `configs/pilot/corpus.yaml` still supports bounded discovery and the
existing direct/targeted matrix. Its separate CPU/GPU submission commands remain
available. This correctness configuration fixes article and patient denominators
to support matched comparisons; it does not estimate performance across PMC.
