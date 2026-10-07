# Clinical architecture experiment

Implemented 2026-10-07; opt-in, not a production promotion. This implements a
bounded first test of the [alternative design](EXTRACTION_ALTERNATIVES.md).

## Launch on HiPerGator

After syncing the updated checkout, run:

```bash
cd /blue/cai6734/ehr_agent/openpatients2
bash scripts/run_clinical_architecture.sh
```

The optional first argument is a new work directory. By default results go under
`/blue/cai6734/ehr_agent/op2-architecture/run-TIMESTAMP` when launched from that
checkout. The launcher uses the locked uv environment; no new dependencies.
Existing runs are not overwritten. Do not edit the runtime while jobs are queued
or running: the campaign checks its source fingerprints.

The one command submits CPU preparation, container setup and one shared FP8
checkpoint download; **four independent one-B200 jobs**, CPU aggregation and
owned model/source/cache cleanup. No shared eight-GPU node is required. Account
and QoS are `cai5724`. Each GPU worker reserves 8 CPUs, 60 GB RAM and **two hours**,
including startup; these are limits, not promised runtimes. Four concurrent
workers fit the 32 CPU / 250 GB allocation. The existing failure-tolerant cleanup,
read-only checkpoint and held-job preflight remain in use. Review fixtures and
bounded figure assets remain in the export; working downloads are cleaned up.

## What runs

All 31 existing development articles, two seeds (42/43), four article shards and
four arms produce 32 planned trials. Within each shard/seed:

```text
Original full baseline: live roster + extraction + actual figure processing
                              |
             CPU-owned table results + gate qualification
                              |
                Frozen corrected comparator (live-complete)
                       /             |             \
                      v              v              v
              source-verification encounter-state coverage-pass
              same candidates     new extraction   additive extraction
              independent QA      chunk reading    independent reading
                      |              |              |
                 qualified gate / accepted view + retained candidates
                      |              |              |
               source-checked relative timelines where records changed
```

- **Comparator:** fixes the table output contract. Models select cell IDs and
  clinical names/categories; code carries the exact row, patient column,
  magnitude, comparator, units and encounter header. Missing cells and ambiguous
  columns remain unresolved. The old heading-style response is also recoverable
  only when both heading and complete row match the identified cell exactly.
- **Source verification:** source-only questions receive topic/domain labels, but
  no proposed results, times or extractor rationale. A separate comparison checks
  the candidate against those answers and original passages. Unresolved or
  contradictory local reviews trigger one full-source expansion. Failure or
  context overflow stays unresolved; source text is never truncated to fit.
- **Encounter state:** a new source reader processes all retained text in roughly
  10,000-character chunks with 200-character overlap. Every fragment needs a
  disposition. It records encounters, source evidence, earlier/later states, and
  action initiation/completion/outcome separately. Clinical domains are projected
  from the original routed chunk plus identity evidence, not from its summary or
  another whole-article pass. Failed/unresolved reading routes all domains;
  tables always route observations. Per-domain chunk outputs are unioned,
  retaining exact citations and separate measurements. This arm replaces the
  comparator's clinical sections before verification.
- **Coverage pass:** independently runs that source reader and projection, then
  appends candidates to the comparator before verification. It preserves original
  clinical values. Singular case-context and relational oncology additions are
  withheld from additive merging until identity reconciliation is designed;
  their candidate outputs remain available. Exact duplicate objects are deduped;
  semantically equivalent mentions may remain and need review.

A source-reading exclusion can still be wrong. Source dispositions, failed units,
chunk/domain calls and literal citation links to delivered candidate facts are
exported. Citation bookkeeping is explicitly not a claim of full clinical recall.
Case-context merging selects the first documented chunk result; alternatives are
retained in calls. Oncology IDs are namespaced during chunk union, not assumed
identical across mentions. These are measurable limitations of this pilot.

## Gates and truth

Before any filtering, the same verifier runs 20 authored controls (10 correct,
10 corrupted) covering patient, encounter, negation, plan/execution, unit,
exponent, comparator, contextual laterality, history and aborted procedures.
Expected answers never enter inference prompts. The seed/shard's gate must
accept every true control and reject or abstain on every corruption.

If qualification fails, **filtering stays in shadow mode** for that shard/seed.
The new extraction arms still run, but receipts identify that the gate was not
applied. Do not pool filtered and shadow outputs and call them one qualified
verifier result without reporting qualification coverage.

Explicit/contextual model support enters the experimental accepted view when
qualified. Other candidates are retained with reasons and full originals. Passing
20 controls is neither clinical certification nor a calibrated accuracy guarantee.
The same model can make correlated errors even across blind requests.

Patient rosters and figure predictions are frozen across arms. This tests text
and caption extraction, not new pixel reasoning or discovery. New visual gates,
fresh blinded article-family evaluation, GEPA optimization, source-annotated
patient-state QA, and automatic production promotion are **not** part of this
campaign. GEPA should follow stable, independently evaluated gate objectives.

Modified records regenerate source-checked timelines with encounter hypotheses
and current fact IDs. Earlier states are retained; no synthetic dates or automatic
state persistence are generated. Modified summaries are withheld pending a
fact-linked summary evaluation, so composite whole-bundle scores are not the
selection criterion. Inspect clinical and ordering metrics separately.

## Outputs and cost accounting

- `SUMMARY.md`, `summary.json`: existing clinical/temporal checklist and paired
  comparisons. Failed shards remain in full denominators.
- `ARCHITECTURE.md`, `architecture-summary.json`: qualification errors, filtering
  counts, decision categories, expanded context, failed projection domains,
  source dispositions and unreviewed units.
- Per trial `architecture/`: qualification controls/results, complete source
  readings, original candidate sections, accepted sections and withheld facts.
- `live-complete/raw-baseline/`: original pre-table baseline, including attempts.
  Its historical absolute paths are accompanied by an explicit relocation map in
  the corrected report.
- Per-call attempts retain prompts, answers/reasoning, validation and token usage.
  Figure predictions copied between arms do not count as new inference.

Baseline rates include original extraction, tables and gate qualification. Phase
reports retain their individual usage; synthetic gate probes are not clinical
article-length samples. Intervention rates measure incremental work. For an
end-to-end comparison add the shared baseline cost, rather than claiming a
speedup from ratios of different task families. Worker telemetry includes startup
and tails. The existing reference has been examined repeatedly; it is development
material, not a new blinded medical accuracy test.

## Inspect and export

On HiPerGator, set the exact directory printed at submission:

```bash
run_dir=/blue/cai6734/ehr_agent/op2-architecture/run-TIMESTAMP
squeue -u "$USER"
cat "$run_dir/SUMMARY.md"
cat "$run_dir/ARCHITECTURE.md"
cat "$run_dir/cleanup.json"
```

Successful orchestration is separate from improved model quality. Check for all
32 completed trials, qualification failures and unresolved/invalid outputs before
interpreting scores. After jobs and cleanup finish, from the checkout:

```bash
archive="${run_dir}-results-$(date +%Y%m%d-%H%M%S).tar.gz"
uv run --locked --python 3.12 --no-dev python -m openpatients2.frontier_campaign export-results \
  --work-dir "$run_dir" --output "$archive"
printf 'Run on your Mac: scp wkieffer@hpg.rc.ufl.edu:%s ~/Downloads/\n' "$archive"
```

The archive includes retained review sources and all experimental receipts, but
excludes weights, containers and caches. Existing 8 GB uncompressed export limits
still apply. No Slurm jobs or model downloads are launched by local unit tests.
