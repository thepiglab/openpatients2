# Re-sign brief: articles whose receipts went stale after a shared-rules normalization

Repo root `/Users/mkieffer/programming/ToadResearch/openpatients2`; `R` = the article's root (`benchmarks/dense500`
or `benchmarks/dense50`); tools run as `op2 dense <tool> ...` (or `.venv/bin/op2`; any working directory).

## Signing: a `--role` call is a signature

Never run any `--role` command to probe, test or dry-run a script. It overwrites the existing receipt, which may be another reviewer's valid signature. Run the script WITHOUT `--role` to validate. Sign only after you inspected the content yourself, with your real reviewer identity (e.g. "Claude Sonnet 5.5 annotator", "Claude Opus 5.5 parent") and notes that describe what you checked. `openpatients2.dense.signing_guard` refuses placeholder reviewers/notes ("x", "test", under 20 characters, "dry run"), and exports reject receipts that carry them.

## Event kinds live in the annotation

Set timeline event classification on the annotation event: `kind` (presentation, history, diagnosis, test, treatment, adverse_event, outcome, follow_up, plan, other), and for its `time_text` the `time_kind` (calendar, relative, duration, age, gestational_age, postnatal_age, order_only), `time_precision` (exact, approximate, range, unknown) and `time_calendar_value` (the YYYY[-MM[-DD]] the time text states at its own precision, e.g. "October 26, 2022" -> 2022-10-26, "October 2022" -> 2022-10; calendar times only). `op2 dense schema_targets` reads these fields. Never patch event kinds or time classifications into `schema_target.json` with a script; regenerate instead. The same holds for the roster: set the article-level annotation block `"roster": {"reported_individual_count": N|null, "roster_complete": true|false, "evidence": [{"segment_id": ..., "quote": "<literal text stating the number>"}]}`; `op2 dense schema_targets` reads it, and a submission patched after signing makes the parent's schema receipt stale. `op2 dense schema_review` rejects a submission that disagrees with classification set in the annotation.

## When this applies
`op2 dense normalize` brings annotations in line with the shared rules (`src/openpatients2/rules/annotation_rules.yaml`,
rendered into `benchmarks/dense500/ANNOTATION_PROTOCOL.md` "Shared annotation rules" and into the runtime prompts)
and regenerates schema targets. It never signs, so after it changes an article:
- the schema receipts (`receipts/schema.agent.json`, `receipts/schema.parent.json`) are stale when the target changed;
- the parent source receipt (`receipts/annotation.parent.json`) is stale when the annotation changed;
- figure-EHR receipts are stale when `figure_ehr_labels.json` changed (normal-comparison panels).
`op2 dense export` withholds every stale article until it is re-signed (reason in `article_checklist.json` -> `withheld`).
(The one-off 2026-10 shared-rules backfill that first used this brief is finished.)

Per-article diff (each change with old -> new, by migration) and open items: a dry run writes the report,
`op2 dense normalize --article <ID> --report <your scratch dir>/normalize.json` -> `articles["<ID>"]`
(`changes`, `needs_review`, `receipts_going_stale`). Open judgment items for the article, if any:
`R/articles/<ID>/needs_review.json`.

## Opus 5.5 parent (first, per article)
1. Read the source, then the article's report entry. Verify the mechanical changes against the source (spot-check
   every migration type present; e.g. a nulled `time.anchor` really was not a literal source phrase, a comparator
   `=` is not hiding a printed `<`, the new age `text_value` is the phrase for THIS age).
2. Resolve every item in `R/articles/<ID>/needs_review.json` against the source by editing `R/articles/<ID>/annotation.json`
   (quotes exact): absent conditions -> `refuted`/`unknown` or remove a non-patient fact; ages -> literal
   `text_value`; normal-worded results -> `flag`; benign lesions -> conditions; panel labels -> printed label or
   null; visual descriptions with `?` outside quotes -> quote the printed text or move it to limitations (then the
   visual, figure-EHR and privacy receipts need re-signing). Write each decision into the item's `resolution` and
   `resolved_by` and into the `corrections` you pass to `save_review`.
3. Validate (`op2 dense validate R --folder annotations` must not list the article), then
   `review_record.save_review(...)` as in PARENT_REVIEWER_BRIEF.md step 5 (it refuses on rule errors).
4. Regenerate and merge the schema target, keeping earlier structural edits, and refresh the needs-review file:
   `op2 dense normalize --apply --article <ID> --allow-recent`
   Inspect `R/articles/<ID>/schema_target.json`, then run the runtime gates until `"passed": true`:
   `op2 dense reference_gates R --article <ID>`
5. Sign `op2 dense schema_review R --folder annotations --article <ID> --role parent --reviewer "Claude Opus 5.5 parent"
   --notes "<what you verified>"`, plus any visual/figure-EHR/privacy parent receipts listed in
   `receipts_going_stale` or invalidated by your edits. Report: verified changes, resolved items, re-sign needs.

## Sonnet 5.5 annotator (after the parent has signed)
1. Read the article's report entry, `R/articles/<ID>/receipts/annotation.parent.json` corrections, and the regenerated
   `R/articles/<ID>/schema_target.json` (your receipt binds that exact target). Check the changed attributes against the
   source; if you disagree, report instead of signing. Your signature covers EVERY change the parent made: check each
   listed correction (added/removed/moved facts, events, relations, alternate_representation_of links, quarantines,
   visual edits) before signing — a gate pass is not a substitute. If you cannot check something, don't sign; report it.
2. Run `op2 dense reference_gates R --article <ID>` (must pass), then re-sign
   `op2 dense schema_review R --folder annotations --article <ID> --role agent --reviewer "Claude Sonnet 5.5 annotator"
   --notes "<what you checked>"` and any agent sidecar receipts listed as stale (e.g. `op2 dense figure_ehr_review ... --role agent`).
   If the parent renumbered patients, changed the roster or edited an identity-evidence quote, your visual / figure-EHR /
   privacy agent receipts are stale too (they bind patient identity; the per-tool validators don't report it): re-view
   the figures and re-sign them.
3. Never edit annotations or run `--role parent`. The orchestrator runs `op2 dense export` and reports N/500.

## Shared rules checklist

<!-- GENERATED rules:start — edit src/openpatients2/rules/annotation_rules.yaml, then run `PYTHONPATH=src .venv/bin/python -m openpatients2.annotation_rules --write` -->
Shared annotation rules (full text: `src/openpatients2/rules/annotation_rules.yaml`, rendered in `benchmarks/dense500/ANNOTATION_PROTOCOL.md` "Shared annotation rules"). Check every item before signing; `validator` rules are also enforced by the runtime gate command.

| Rule | What | Checked by validator |
| --- | --- | --- |
| `EVID-NEGATION` | Negations and qualifiers are inside the quotes | yes |
| `NAME-LABEL` | name is a concise label; evidence carries the wording | review |
| `GLUED-TEXT` | Free text is normal spaced prose | yes |
| `NO-INFERENCE` | No inference-added facts | review |
| `NO-BORROWING` | No borrowed times, routes, sites, indications or settings | yes |
| `TIME-LITERAL` | Time text and anchors are literal or null | yes |
| `NO-COHORT-COPY` | Cohort statements are not copied to individuals | review |
| `ASSERT-ABSENT` | Absent, refuted and not documented | yes |
| `OBS-COMPARATOR` | Numeric results carry a comparator | yes |
| `OBS-FLAG` | Normal flags; no flags from reference ranges | yes |
| `OBS-NOT-PERFORMED` | Tests not performed | yes |
| `SOC-NEVER` | Never-users are affirmed | yes |
| `DEMO-AGE-TEXT` | Ages keep the literal source phrase | yes |
| `DEMO-SEX-WORDING` | Documented sex comes from the source's own words about the patient | review |
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
