# Re-sign campaign brief: shared-rules migration (semantic-rules-2026-10)

Repo root `/Users/mkieffer/programming/ToadResearch/openpatients2`; `R` = the article's root (`benchmarks/dense500`
or `benchmarks/dense50`); run from the repo root with `PYTHONPATH=src:benchmarks/dense500 .venv/bin/python`.

## Signing: a `--role` call is a signature

Never run any `--role` command to probe, test or dry-run a script. It overwrites the existing receipt, which may be another reviewer's valid signature. Run the script WITHOUT `--role` to validate. Sign only after you inspected the content yourself, with your real reviewer identity (e.g. "Claude Sonnet 5.5 annotator", "Claude Opus 5.5 parent") and notes that describe what you checked. `signing_guard.py` refuses placeholder reviewers/notes ("x", "test", under 20 characters, "dry run"), and exports reject receipts that carry them.

## What happened
All 136 annotated articles were migrated to the shared rules (`src/openpatients2/rules/annotation_rules.yaml`, rendered
into `benchmarks/dense500/ANNOTATION_PROTOCOL.md` "Shared annotation rules" and into the runtime prompts) by
`benchmarks/dense500/migrations/semantic_rules_2026_10.py`. Nothing was signed, so:
- every schema receipt (`schema-agent-reviews`, `schema-parent-reviews`) is stale: the timeline vocabulary was unified
  (occurrence `uncertain` -> `unknown`, `not_occurred`/`conditional` and relation `unknown`/`during` now exist in both
  pipelines), so every schema target was regenerated;
- the parent source receipt (`reviews/`) is stale wherever the annotation changed (`annotation_changed` in the report);
- PMC12689157.1 `figure-ehr-labels` changed (normal-comparison panels -> `non_clinical_diagnostic_aid`): both
  figure-EHR receipts must be re-signed.
`export.py` withholds every stale article until it is re-signed (reason in `article_checklist.json` -> `withheld`).

Per-article diff (each change with old -> new, by migration) and open items:
`benchmarks/dense500/migrations/reports/semantic_rules_2026_10.json` -> `articles["<ID>"]`
(`changes`, `needs_review`, `receipts_going_stale`). Open judgment items for the article, if any:
`R/needs-review/<ID>.json`.

## Opus 5.5 parent (first, per article)
1. Read the source, then the article's report entry. Verify the mechanical changes against the source (spot-check
   every migration type present; e.g. a nulled `time.anchor` really was not a literal source phrase, a comparator
   `=` is not hiding a printed `<`, the new age `text_value` is the phrase for THIS age).
2. Resolve every item in `R/needs-review/<ID>.json` against the source by editing `R/annotations/<ID>.json`
   (quotes exact): absent conditions -> `refuted`/`unknown` or remove a non-patient fact; ages -> literal
   `text_value`; normal-worded results -> `flag`; benign lesions -> conditions; panel labels -> printed label or
   null; visual descriptions with `?` outside quotes -> quote the printed text or move it to limitations (then the
   visual, figure-EHR and privacy receipts need re-signing). Write each decision into the item's `resolution` and
   `resolved_by` and into the `corrections` you pass to `save_review`.
3. Validate (`validate.py R --folder annotations` must not list the article), then
   `review_record.save_review(...)` as in PARENT_REVIEWER_BRIEF.md step 5 (it refuses on rule errors).
4. Regenerate and merge the schema target, keeping earlier structural edits, and refresh the needs-review file:
   `benchmarks/dense500/migrations/semantic_rules_2026_10.py --apply --article <ID> --allow-recent`
   Inspect `R/schema-submissions/<ID>.json`, then run the runtime gates until `"passed": true`:
   `benchmarks/dense500/reference_gates.py R --article <ID>`
5. Sign `schema_review.py R --folder annotations --article <ID> --role parent --reviewer "Claude Opus 5.5 parent"
   --notes "<what you verified>"`, plus any visual/figure-EHR/privacy parent receipts listed in
   `receipts_going_stale` or invalidated by your edits. Report: verified changes, resolved items, re-sign needs.

## Sonnet 5.5 annotator (after the parent has signed)
1. Read the article's report entry, `R/reviews/<ID>.json` corrections, and the regenerated
   `R/schema-submissions/<ID>.json` (your receipt binds that exact target). Check the changed attributes against the
   source; if you disagree, report instead of signing.
2. Run `benchmarks/dense500/reference_gates.py R --article <ID>` (must pass), then re-sign
   `schema_review.py R --folder annotations --article <ID> --role agent --reviewer "Claude Sonnet 5.5 annotator"
   --notes "<what you checked>"` and any agent sidecar receipts listed as stale (e.g. `figure_ehr_review.py ... --role agent`).
3. Never edit annotations or run `--role parent`. The orchestrator runs `export.py` and reports N/500.

## Shared rules checklist

<!-- GENERATED rules:start — edit src/openpatients2/rules/annotation_rules.yaml, then run `PYTHONPATH=src .venv/bin/python -m openpatients2.annotation_rules --write` -->
Shared annotation rules (full text: `src/openpatients2/rules/annotation_rules.yaml`, rendered in `benchmarks/dense500/ANNOTATION_PROTOCOL.md` "Shared annotation rules"). Check every item before signing; `validator` rules are also enforced by the runtime gate command.

| Rule | What | Checked by validator |
| --- | --- | --- |
| `EVID-NEGATION` | Negations and qualifiers are inside the quotes | yes |
| `NAME-LABEL` | name is a concise label; evidence carries the wording | review |
| `NO-INFERENCE` | No inference-added facts | review |
| `NO-BORROWING` | No borrowed times, routes, sites, indications or settings | review |
| `TIME-LITERAL` | Time text and anchors are literal or null | yes |
| `NO-COHORT-COPY` | Cohort statements are not copied to individuals | review |
| `ASSERT-ABSENT` | Absent, refuted and not documented | yes |
| `OBS-COMPARATOR` | Numeric results carry a comparator | yes |
| `OBS-FLAG` | Normal flags; no flags from reference ranges | yes |
| `OBS-NOT-PERFORMED` | Tests not performed | yes |
| `SOC-NEVER` | Never-users are affirmed | yes |
| `DEMO-AGE-TEXT` | Ages keep the literal source phrase | yes |
| `SECTION-SIGNS` | Signs and clinical scales belong in symptoms_function | review |
| `SECTION-IMAGING` | Diagnostic imaging may also be a procedure | review |
| `SECTION-CAREPLANS` | care_plans scope | review |
| `ONC-BENIGN` | Benign lesions are not oncology tumors | yes |
| `CTX-VITAL-STATUS` | Vital status is consistent with outcomes | yes |
| `CTX-PER-PATIENT` | case_context describes the one target patient | yes |
| `ROSTER-ORDER` | Patients numbered by first mention | yes |
| `TIMELINE-ENUMS` | One timeline vocabulary | yes |
| `MED-ROUTE` | Routes keep the source spelling | yes |
| `FIG-LABELS` | No invented panel labels; question marks only inside quotes | yes |
| `FIG-EHR-COMPARISON` | Normal-comparison images are non-clinical aids | review |
| `FIG-PRIVACY` | Privacy flags name categories only | review |
| `MEDIA-UNAVAILABLE` | Unobtainable media are listed, never described | review |
| `REF-INTEGRITY` | References point to existing facts and events | yes |
<!-- GENERATED rules:end -->
