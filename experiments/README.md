# Extraction experiments

These scripts are isolated research harnesses. Production extraction, schemas,
validators, exports and defaults do not import them. They use the existing small
licensed PMC sample, save every model call, and download no model weights.

`refinement_v1.py` crosses direct extraction, domain-span hints and span/context
review with five postprocessors. All calls request text output without API JSON
schema enforcement. Application contracts and validation remain explicit.

`refinement_extensions_v1.py` adds an explicitly recorded follow-up: annotation
recovery for failed/empty observation tagging, batches of six entity mentions,
and whole-retry → local-repair combinations. It uses the same $10 experiment
ledger, not a second spending allowance. Run it after the primary harness.

The saved source reference is not available to either generation harness. Scoring
is offline and uses 68 positive typed-field checks in the selected eight tasks,
plus targeted forbidden-claim probes. This is a known-failure development set,
not a medical benchmark with physician gold labels. Failed/quarantined facts stay
in the audit trail; no experimental result is promoted to a release dataset.

Offline commands:

```sh
uv run python experiments/score_refinement_v1.py
uv run python experiments/prepare_refinement_audit.py
uv run python experiments/verify_refinement_v1.py
```

The [completed report](../reports/REFINEMENT_EXPERIMENTS.md) includes all 56
model/strategy comparisons. `verify_refinement_v1.py` requires the completed
study and reviewed audit; it checks frozen hashes, paired-cell coverage, sampled
claim integrity and preservation of initially accepted neighboring facts. These
are software/artifact checks, not independent clinical validation.

Live execution requires a private key file supplied with `--key-file`. Protocols
are frozen before their respective runs; do not overwrite them to present a new
experiment as the old one. Existing matching requests are resumed from disk;
failed requests are not silently rerun. The extension adds outcome arms while
retaining primary arms. Running the primary harness again rebuilds those files,
so run the extension again afterward to restore its cached arms.

The cost tables count dependencies for each pipeline as if that pipeline were
run alone. Shared annotation calls are counted once per source/model, not once
per patient. Adding all table rows would double-count shared work; actual total
account usage is reported separately.

## Chunking study

The [chunking report](../reports/CHUNKING_EXPERIMENTS.md) compares whole articles,
section chunks, overlapping windows, hierarchical reduction, summary-based
re-extraction, local repair and their combination. All six requested API routes
were attempted; four produced clinical comparisons. Original failed responses,
losslessly normalized outputs, source-linked audits and budget receipts are in
`runs/chunking-v1/`.

`chunking_v1.py` is the frozen primary definition. `chunking_parallel_v1.py`
resumes independent units concurrently; its reader amendment preserves the
original strict outputs. `chunking_repair_v1.py` and
`chunking_repaired_hierarchy_v1.py` add separately recorded exploratory arms.
`chunking_context_replay_v1.py` tests bounded source-context recovery on known
reducer failures. None is imported by production.

Offline scoring does not need an API key:

```sh
uv run python experiments/normalize_chunking_secondary.py
uv run python experiments/score_chunking_v1.py
uv run python experiments/score_chunking_v1.py --normalized
uv run python experiments/audit_chunking_v1.py
```

The audit preparation script regenerates samples and change listings, while the
separate `summary-adjudications.json` and `edit-adjudications.json` preserve manual
review. Regenerating audit candidates does not constitute a new medical review.

Unlike the older refinement runner, the chunking live runners expect a private
mode-600 key file at `/tmp/op2-chunking-key`. The study's temporary copy was deleted
on completion. Live runners share one $9 ledger and resume saved calls; do not
delete the ledger or frozen protocols to bypass the original spending bound.

## Download-format study

`source_formats_v1.py` compares JATS-rendered segments, official PMC TXT,
pypdf text, pdfplumber layout text and PDF text plus rendered pages.
`firecrawl_source_formats_v1.py` adds the user-requested native Rust
`pdf-inspector` arm. `firecrawl_regions_v1.py` is a separately labeled,
manually selected column-region diagnostic, not an automatic layout detector.
All paid arms share `runs/source-formats-v1/budget.sqlite` and its $3.95 cap.
No source-format change has been promoted to production.

Downloads are versioned, hashed and size-limited. PDF libraries use the bundled
runtime; Firecrawl extraction uses a temporary `pdf-inspector==1.25.2` binary
wheel without OCR models. Plain-text arms never receive JATS-derived captions
or clinical facts; all arms share only fixed patient and figure identifiers.

```sh
.venv/bin/python experiments/score_source_formats_v1.py
.venv/bin/python experiments/verify_source_formats_v1.py
.venv/bin/python experiments/report_source_formats_v1.py
```

Scores distinguish raw structured facts, exact-evidence delivery and secondary
citation-only formatting recovery. See [the format report](../reports/SOURCE_FORMAT_EXPERIMENTS.md)
for the source-cleaning differences, availability failures and limits of this
three-article development sample.
