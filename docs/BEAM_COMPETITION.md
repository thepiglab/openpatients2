# Beam competition campaign

This campaign compares two Beam NVFP4 configurations on the same fifty training
articles, through clinical extraction, measurement series, ICD-10-CM selection
and deterministic FHIR R4 export. It freezes code, source documents, selection,
reference annotations, model/template revisions and the terminology catalog.
References are used only for selection and scoring, never as model input.

| Configuration | GPUs | CPUs | Host RAM | Worker limit |
|---|---:|---:|---:|---:|
| Beam NVFP4 + DFlash2, 7 draft tokens | 4 B200 | 16 | 96 GiB | 12 hours |
| Same Beam NVFP4, speculation off | 4 B200 | 16 | 96 GiB | 12 hours |
| Concurrent total | 8 B200 | 32 | 192 GiB | One wave |

Both arms retain the pinned native chat template, reasoning parser, maximum
reasoning effort, temperature 0.7, top-p 0.99, 131,072-token context and identical
client concurrency. This is a matched speculation comparison with the revised
pipeline in both arms, not a clean old-pipeline versus new-pipeline experiment.
Compare accuracy, measured latency and output lengths together; draft acceptance
alone is not speedup.

The fifty articles contain 67 reference patients across 26 broad specialty
groups; 48 contain tables and 24 have annotated measurement series. Sources are
at most 40,000 characters and sixty table rows, with one to three referenced human
patients. All have article-level CC BY permission metadata. The selection is an
enriched development/demo sample, not an unbiased clinical benchmark. See the
[selection report and all fifty titles](reports/COMPETITION_50_SELECTION.md).
The actual extracted patient count may differ and is scored rather than forced.

## Changes being tested

- Whole-source windows and per-value-cell decisions prevent a model's global
  `complete` label from hiding skipped measurement columns. Unresolved cells,
  failed windows and original responses are retained. Numeric values are never
  converted to fit a validator. Equal values in separate source cells remain
  distinct occurrences.
- Each clinical fact receives an attribution scope. Only direct patient facts
  enter these sections; cohort aggregates, protocols, secondary cases and
  uncertain assignments remain in quarantine for review. These are model
  judgments, not clinically verified decisions.
- Sparse timeline requests prioritize local events, explicit time/episode
  anchors and clinical event anchors. Candidate selection never establishes
  chronology. Unassessed pairs and conflicting edges are reported explicitly.
  Prior-output replay reduced candidate pairs 59.06%, retaining 80.94% of
  aligned reference edges; this is a retrieval tradeoff, not new accuracy or
  throughput evidence. See the [replay report](reports/SPARSE_TIMELINE_REPLAY_20261030.md).
- A selected table-time cell is copied verbatim into time text, with the original
  model text retained in the audit. This does not infer a calendar date.
- Beam selects a pinned ICD-10-CM FY2027 candidate ID or abstains. The coding
  stage permits at most 128 candidate-bearing mentions in sixteen batches,
  four concurrent requests and a 300-second coding deadline per article.
  Remaining requests stay pending. Codes are not applied to drug/lab concepts
  simply because ICD-10-CM is available.
- Typed source facts compile into linked FHIR resources with deterministic IDs
  and provenance. Measurement-series points can become Observations. Default
  bundles remain uncoded; the experimental coded export is explicitly tagged
  as unreviewed. No clinician, institution, visit or calendar date is invented.

## Launch on HiPerGator

The launcher archives a **commit**, not uncommitted working files. Commit and push
the complete implementation, then update the cluster checkout first. If the
cluster branches diverge, reconcile them before launching; do not discard local
work merely to start this campaign.

```bash
git pull --ff-only &&
(
  set -a
  source .env
  set +a
  DENSE_ACCOUNT=cai5724 DENSE_QOS=cai5724 \
    bash scripts/run_dense_beam.sh competition
)
```

`DENSE_HOURS` can explicitly override the twelve-hour default. The launch checks
for an active previous Super campaign. It does not cancel jobs or remove old
campaigns automatically. Source data remains under `/blue/cai6734` by default;
the scheduler account above is `cai5724`.

There are **100 planned article runs**, fifty per configuration. CPU staging and
queueing are additional to each twelve-hour GPU allocation. Downloads are serial;
the first ready worker can start during later staging within the group budget.
The second profile reuses verified hardlinks to the same target/container; only
the speculative profile needs DFlash2. Storage preflight budgets about **418 GiB**
for the paired Beam wave, including a 128 GiB scratch/results reserve. Separate
GPU workers use separate devices. Cleanup waits for both workers and retains
results, logs and reasoning while removing campaign-owned weights and caches.

Each article has at most a one-hour deadline. Near allocation expiry, the
remaining time can be used with a shorter article deadline instead of reserving
an unused full hour. Partial outputs remain visible. Completion within twelve
hours is not guaranteed: this is the first full run of the revised pipeline.

## Inspect, resume and collect

```bash
watch -n 10 'squeue -u "$USER" -o "%.18i %.12P %.60j %.10T %.12M %.5D %R"'
```

After leaving `watch` with Ctrl-C:

```bash
bash scripts/run_dense_nemotron_super.sh status
```

Competition status distinguishes finished extraction attempts from unfinished
coding/FHIR stages. Verification rejects unfinished required stages. If only a
downstream stage is incomplete, an explicit resume reuses the extraction and
validated coding decisions, preserves old exports/receipts under `fhir-history`,
and processes the remaining coding work. It does not automatically reroll a
completed extraction or an accepted code to obtain a better score. Original and
retry token costs are counted once. Changed code requires a new campaign.

```bash
bash scripts/run_dense_nemotron_super.sh resume
```

An invalid underlying extraction can continue to fail FHIR validation on resume;
inspect the audit instead of repeatedly retrying an unchanged input. A new
campaign is appropriate when fixes change frozen code or data.

The CPU aggregator creates an archive even for incomplete campaigns. This lets
us diagnose failures. An archive/checksum alone does not prove completion:

```bash
set +e
WORK="$(cat runs/dense-nemotron-super-last-work.txt)"
bash scripts/run_dense_nemotron_super.sh verify "$WORK"
(cd "$WORK" && sha256sum -c results.tar.gz.sha256)
printf 'REMOTE="%s"\n' "$WORK"
```

Use the printed `REMOTE` assignment on your Mac, then:

```bash
DEST="$HOME/Downloads/$(basename "$REMOTE")"
mkdir -p "$DEST" &&
scp -o PreferredAuthentications=keyboard-interactive \
  -o ServerAliveInterval=30 -o ServerAliveCountMax=6 \
  "wkieffer@hpg.rc.ufl.edu:$REMOTE/results.tar.gz" \
  "wkieffer@hpg.rc.ufl.edu:$REMOTE/results.tar.gz.sha256" \
  "$DEST/" &&
(cd "$DEST" && shasum -a 256 -c results.tar.gz.sha256)
```

## Outputs and release gates

- `summary/comparison.md`: quality, coverage, matched speculation speedup,
  per-task tokens, mean input/output/reasoning tokens per article and throughput.
- `summary/competition-fhir.json`: coding selections/abstentions/pending requests,
  stage timings, resource counts and local bundle validation outcomes.
- `summary/fhir-dataset/<arm>/*.bundles.jsonl`: consolidated uncoded and
  experimental-coded Bundle NDJSON. Its manifest retains missing/quarantined
  patients and links each included bundle to the original audit.
- `arms/<arm>/summary/extraction-audits.json`: source-cell decisions, attribution
  quarantine, candidate/unassessed timeline pairs and retained/quarantined edges.
- `arms/<arm>/results/articles/<id>/attempt-*/`: original clinical, measurement
  and coding prompts, endpoint-returned reasoning, token usage and citations;
  the FHIR packet, per-patient bundles, source/terminology hashes and mapping audits.

Coding tokens are included in article totals and reported separately. Reasoning
is a subset of completion tokens, never added a second time. Interrupted
requests retain partial returned reasoning and mark unreported usage unknown.
End-to-end timing and individual extraction/EHR/FHIR timings are separate.

The campaign checks pinned R4 JSON schema, links and source provenance. **The
official HL7 validator is not run inside GPU workers.** The local implementation
was also smoke-tested with that validator, but every new output still needs the
separate CPU validation/review gate described in [FHIR_COMPETITION.md](FHIR_COMPETITION.md).
No bundles are marked release-ready. A schema pass, model code selection, or
reference-annotation agreement does not establish clinical correctness.

After this run, compare paired speculation results on shared completed articles;
review patient attribution and table coverage, sample codes and abstentions,
run the intended FHIR profile/terminology validation, then freeze a reviewed
fifty-article competition release. Hold out validation/test data for later
generalization checks rather than using this selected demo set as an accuracy claim.
