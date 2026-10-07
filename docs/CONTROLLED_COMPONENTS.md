# Controlled component campaign

On HiPerGator, from this checkout, launch the complete campaign with:

```bash
bash scripts/run_controlled_components.sh
```

An optional first argument selects a fresh campaign directory. The command
submits CPU preparation, container setup and one shared checkpoint download;
four independent one-B200 GPU jobs; CPU scoring; then owned checkpoint, source
and worker-cache cleanup. Each worker requests 8 CPUs, 60 GB host memory and
90 minutes. Concurrent workers total 32 CPUs, 240 GB and four GPUs. CPU preparation
uses 32 CPUs and 64 GB; aggregate uses 2 CPUs and 8 GB. No local GPU or checkpoint
download is needed. Existing held-DAG preflight, failure-tolerant dependencies,
read-only checkpoint mounts and cleanup ownership checks remain in use.

All 31 retained development articles run at seeds 42 and 43, partitioned across
workers. For each shard and seed, the original live-complete extraction first
creates a fresh baseline. The following interventions each start independently
from its actual exported patients, predicted roster, figure assignments and
pixel descriptions:

- `table-observations` extracts missing measurements from a lossless table-cell
  inventory and augments observations with source-checked additions, preserving
  baseline facts. Its receipt records cell coverage and changed fact links.
  Patient ownership is currently resolved only for source-grounded `Case N` or
  `Patient N` columns. Other layouts and ambiguous ownership remain explicitly
  unresolved in the retained inventory; this is not complete table semantics.
- `attribute-audit` critiques and repairs scalar clinical attributes using
  code-generated IDs and source witnesses.
- `episode-rebuild` rebuilds the encounter graph from original passages with
  fixed baseline clinical facts and independent fact assertion/event occurrence.

Changed facts refresh the derived fact registries. Removed attribute facts have
their old timeline links removed; frozen summaries and timelines are marked for
reconciliation. These arms measure component effects and are not promoted as
complete production bundles. Inspect `component_consistency` and the intervention
receipts before interpreting clinical or temporal probe changes.

Source sample, media manifest, pixel bytes, baseline clinical exports and figure
task outputs are hashed in `frozen-inputs.json`. Components neither rerun
discovery/pixels nor consume another component's output. Derived trials copy the
baseline's scorer inputs; copied inference work contributes no new component
token usage. Components rotate execution order across seeds and shards after the
required baseline. Missing, failed or deadline-deferred baselines make dependent
arms explicitly unavailable. Incomplete pairs never become a valid paired result.

`SUMMARY.md` and `summary.json` score the full reviewed reference once per arm
and seed, keeping failed shards in the denominator. Paired improvements are
available only when both arms deliver every shard with matching baseline
provenance. Token rates use newly executed tasks over summed one-GPU trial wall
time and exclude server startup and warmup. Baseline and component rates represent
different task families; their rate ratio does not measure whole-pipeline speedup.

The existing test split has already been exposed during development. This is a
component experiment on development checks, without GEPA search or a fresh
blinded clinical evaluation. The 90-minute deadline is enforced; partial results
remain reviewable and do not shrink the gold denominator.

After cleanup, export retained sources, pixels, trials and intervention receipts:

```bash
uv run --locked --python 3.12 --no-dev python -m openpatients2.frontier_campaign export-results \
  --work-dir /absolute/campaign/path --output /absolute/new-results.tar.gz
```

Exports exclude model weights, containers, environments and runtime caches and
retain the existing 8 GB uncompressed safety cap. The previous frontier launcher
and three-arm architecture configuration retain their behavior.
