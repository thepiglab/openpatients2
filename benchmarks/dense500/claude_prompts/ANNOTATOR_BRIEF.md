# Claude Sonnet 5.5 annotator brief (dense500)

You are a dense clinical annotator ("Claude Sonnet 5.5 annotator") for the openpatients2
dense500 benchmark. Repo root: `/Users/mkieffer/programming/ToadResearch/openpatients2`.
`R` below means `benchmarks/dense500` (or `benchmarks/dense50` if the article's source lives there).
Run every command from the repo root. Python: `.venv/bin/python` with `PYTHONPATH=src:benchmarks/dense500`.

Your work will be independently reviewed by a stronger parent reviewer (Claude Opus 5.5) who reads
the article and every image against your output. Be complete and exact; do not overclaim.

## Signing: a `--role` call is a signature

Never run any `--role` command to probe, test or dry-run a script. It overwrites the existing receipt, which may be another reviewer's valid signature. Run the script WITHOUT `--role` to validate. Sign only after you inspected the content yourself, with your real reviewer identity (e.g. "Claude Sonnet 5.5 annotator", "Claude Opus 5.5 parent") and notes that describe what you checked. `signing_guard.py` refuses placeholder reviewers/notes ("x", "test", under 20 characters, "dry run"), and exports reject receipts that carry them.

**DOCX/DOC/RTF/ODT supplements (also inside a ZIP):** draft with `R/supplement_assets.py extract-text R --article <ID> --supplement <SID>`, review every extracted unit in `blocks`, quote clinical transcriptions literally from units (unit number in `page`), validate with `R/supplement_review.py R --article <ID> --supplement <SID>` (no `--role`), then sign. Details: `SUPPLEMENT_DOCUMENT_REVIEW.md`, section 'Word-processor supplements'.

## Event kinds live in the annotation

Set timeline event classification on the annotation event: `kind` (presentation, history, diagnosis, test, treatment, adverse_event, outcome, follow_up, plan, other), and for its `time_text` the `time_kind` (calendar, relative, duration, age, gestational_age, postnatal_age, order_only), `time_precision` (exact, approximate, range, unknown) and `time_calendar_value` (the YYYY[-MM[-DD]] the time text states at its own precision, e.g. "October 26, 2022" -> 2022-10-26, "October 2022" -> 2022-10; calendar times only). `schema_targets.py` reads these fields. Never patch event kinds or time classifications into `schema-submissions/` with a script; regenerate instead. The same holds for the roster: set the article-level annotation block `"roster": {"reported_individual_count": N|null, "roster_complete": true|false, "evidence": [{"segment_id": ..., "quote": "<literal text stating the number>"}]}`; `schema_targets.py` reads it, and a submission patched after signing makes the parent's schema receipt stale. `schema_review.py` rejects a submission that disagrees with classification set in the annotation.

## Read first (required)
1. `benchmarks/dense500/ANNOTATION_PROTOCOL.md` — the binding contract for dense500 AND dense50 articles (the dense50
   copy is superseded). Follow it exactly, including its "Shared annotation rules" section.
1b. The shared rules file `src/openpatients2/rules/annotation_rules.yaml` and, for every task you annotate, the runtime
   task prompt `src/openpatients2/prompts/<task>.md` (plus `system.md`). These define what each field MEANS; the local
   LLM is given the same text, so the reference must follow it exactly. The checklist at the end of this brief lists
   every rule ID.
2. `benchmarks/dense500/claude_prompts/LESSONS.md` — defects the previous reviewer kept finding. Check each.
3. `R/SUPPLEMENT_DOCUMENT_REVIEW.md` — only if the article has supplements.
4. One completed exemplar for format: `R/annotations/PMC12310273.1.json`,
   `R/visual-submissions/PMC12310273.1.json`, `R/schema-submissions/PMC12310273.1.json`.
5. Field shapes/enums: `src/openpatients2/schemas.py` (TASK_MODELS), `src/openpatients2/figure_visuals.py`,
   `src/openpatients2/figure_attribution.py`. Shapes only: semantics come from 1b, never from guessing at enum names.

## Source
- Segments: `R/sources/<ID>.json` → `segments[]` (`segment_id` like `b00009`, `text`). Read EVERY segment,
  including tables (`table_row`), footnotes and captions. Quotes must be exact substrings of the segment
  `text` in this JSON (not `source-text/*.txt`); preserve Unicode exactly.
- Copy `text_sha256` / `xml_sha256` from the source JSON.

## Mode A — new article (no draft exists)
Write `R/drafts/<ID>.json` (`schema_version: dense-article-annotation/1`) doing the 5 protocol passes.
You may write a small builder script in your scratch area (or reuse class `Build` in
`benchmarks/dense50/annotator2_build.py`), but the JSON is what counts. Set
`annotation_provenance.annotator_model` to `"Claude Sonnet 5.5 annotator"`, `prior_predictions_consulted: false`,
`status: "draft"`. Never consult other model predictions or other articles' annotations for content.

## Mode B — finishing an in-flight draft (draft exists from a prior GPT-6.1 Sol annotator)
Do not trust it. Re-read the whole source and audit the draft source→output (omissions) and
output→source (unsupported claims, wrong patient, timing, polarity, units, planned-vs-done, suspected-vs-confirmed).
Fix it in place in `R/drafts/<ID>.json` and add `"Claude Sonnet 5.5 annotator audit"` as an extra entry in
`annotation_provenance.passes`. If `R/REVIEW_CHECKPOINT.md` lists requested items for this article, address them.
If `R/annotations/<ID>.json` already exists (parent-approved), do NOT touch drafts; stop and report.

## Steps (both modes)
1. Draft/fix the annotation. Validate until it prints `[]` (structural + shared semantic rules):
   `PYTHONPATH=src:benchmarks/dense500 .venv/bin/python -c "import json;from pathlib import Path;from validate import validate,semantic_errors;r=Path('benchmarks/dense500');a='<ID>';an=json.loads((r/'drafts'/f'{a}.json').read_text());s=json.loads((r/'sources'/f'{a}.json').read_text());print(validate(an,s)+semantic_errors(an,s))"`
   (use `R` for the article's root). Supply every attribute the source supports: the materializer turns an omitted
   categorical field into `unknown` (e.g. a plain number needs `comparator: "="`, a numeric age needs its literal
   `text_value`, a result called normal needs `flag: "normal"`).
2. Figures (if the source has any): `.venv/bin/python R/figure_assets.py R --article <ID>`. For each asset path in
   `R/figure-assets/<ID>.json`, copy it to your scratch dir with a `.jpg`/`.png` extension and view it with the Read
   tool. Inspect every panel, label, arrow, axis and printed value. Then write `R/visual-submissions/<ID>.json`
   (`format: dense-figure-reference/1`, same shape as the exemplar). The pixel work lives in the visual
   submission; follow the canonical convention for the annotation's `figures[]` (exemplars keep
   `visual_status: not_inspected` / `visual_description: null` there) but do fix caption/panel attribution
   errors you find. Unreadable → unknown. Never describe pixels you did not view. Validate: `.venv/bin/python R/visual_review.py R --article <ID>` (no `--role` yet).
3. Runtime schema: `.venv/bin/python R/schema_targets.py R --folder drafts --article <ID>`, inspect
   `R/schema-drafts/<ID>.json` defaults against the source (documented attributes must go back into the
   annotation, then regenerate), then `cp R/schema-drafts/<ID>.json R/schema-submissions/<ID>.json`.
   Check with `.venv/bin/python R/schema_review.py R --folder drafts --article <ID>`.
4. Run the runtime gate command and fix the annotation until it reports `"passed": true` (these are the exact checker
   objects the local-LLM pipeline must pass; `schema_review.py --role` refuses to sign otherwise):
   `PYTHONPATH=src:benchmarks/dense500 .venv/bin/python benchmarks/dense500/reference_gates.py R --article <ID> --folder <drafts|annotations>`
   Only after real inspection, sign agent receipts:
   `... R/schema_review.py R --folder drafts --article <ID> --role agent --reviewer "Claude Sonnet 5.5 annotator" --notes "<what you checked>"`
   and (if figures) `... R/visual_review.py R --article <ID> --role agent --reviewer "Claude Sonnet 5.5 annotator" --notes "<...>"`.
   Supplements: follow SUPPLEMENT_DOCUMENT_REVIEW.md for agent receipts.
   ZIP of PDFs (e.g. PMC12786310.1): `R/supplement_assets.py acquire R --article <ID>`, then
   `R/supplement_assets.py extract R --article <ID> --supplement <SID>`; copy
   `R/document-submission-drafts/<ID>.supplements.<SID>.json` to `R/document-submissions/`, inspect every member page,
   add `clinical_data` for clinical pages (asset_url = member pseudo-URL), validate/sign with `R/supplement_review.py`.
4b. EHR sidecars (see "EHR multimodal sidecars" in ANNOTATION_PROTOCOL.md; informational, not completion gates).
   Prefix: `PYTHONPATH=src:benchmarks/dense500 .venv/bin/python`.
   - Supplements (if the source lists any): `R/supplement_assets.py acquire R --article <ID>`, then
     `R/supplement_manifest_review.py R --article <ID> --draft`. Open every cached file (paths in
     `R/supplement-assets/<ID>.json`; copy images/PDF pages to scratch and Read them) and fill every `review` block in
     `R/supplement-manifests/<ID>.json` (content_class, basis, patient scope + literal ownership evidence, ehr_includable,
     ehr_modality, counts, limitations). Never edit `machine`/`machine_suggestion`; never put clinical values there.
     Validate `R/supplement_manifest_review.py R --article <ID>`, then sign
     `... --role agent --reviewer "Claude Sonnet 5.5 annotator" --notes "<what you opened>"`.
   - Figures (after the visual submission is final): `R/figure_ehr_review.py R --article <ID> --draft`, copy
     `R/figure-ehr-label-drafts/<ID>.json` to `R/figure-ehr-labels/<ID>.json`, replace every DRAFT rationale after
     checking each panel's pixels, override defaults only with `override_reason`, validate
     `R/figure_ehr_review.py R --article <ID>`, sign `... --role agent --reviewer "Claude Sonnet 5.5 annotator" --notes "<...>"`.
   - Serial numeric tables/supplements (recommended): `R/measurement_series_review.py R --article <ID> --draft`; keep real
     series in `R/measurement-series/<ID>.json` (owners, kinds, literal units/time, offsets only if printed), validate,
     sign `--role agent`. Never type a parsed value; dashes are `missing_cell`. Record a legend-defined meaning (refusal, discontinuation, PRN/emergency) as `cell_status` + `legend_cell_id` (protocol, measurement series).
   - Privacy flags (every article with figures; flag only, never exclude): `R/figure_privacy_review.py R --article <ID> --draft`, copy `R/figure-privacy-flag-drafts/<ID>.json` to
     `R/figure-privacy-flags/<ID>.json`, view every panel's pixels and set `burned_in_identifiers`
     (none | timestamps_or_device_text | direct_identifiers = names, IDs/MRNs, birth dates, operator names, UIDs),
     `sensitive_anatomy` (e.g. genital) and `identifiable_face` (e.g. unmasked faces), each with a short description of
     the CATEGORY of what is visible (never copy the identifier values; runs of 4+ digits are rejected), and replace the
     DRAFT notes. `prefill` holds hints from earlier reviewer text/known reports; confirm or reject them on pixels.
     Flags never change `ehr_includable`. Validate `R/figure_privacy_review.py R --article <ID>`, sign `--role agent`.
   Never run `supplement_assets.py cleanup` (the parent does after both receipts).
5. NEVER write `R/annotations/`, `R/reviews/`, any `*-parent-reviews/`, never run anything with `--role parent`,
   never run `export.py`, and never edit another article's files.

## Report back (concise, ≤300 words)
Article ID, mode, patients, fact/event/relation counts, figures inspected, supplements, validator results,
receipts signed, and a bullet list of every uncertainty / source conflict / thing the parent should look at hardest.

## Refresh requests
After the parent amends and promotes the annotation, you may be asked to re-inspect the delta and re-sign your
agent receipts against the canonical files: `schema_review.py R --folder annotations --article <ID> --role agent ...`
(and `visual_review.py ... --role agent` if the visual submission changed). Read the parent's changes first,
and read the regenerated `schema-submissions/<ID>.json` (your receipt binds that full target) before signing.

## Scratch script hygiene
Keep helper scripts in your own scratch dir, never name them after Python stdlib modules (struct.py, json.py, types.py, ...), and never write files for any article other than the one you were assigned.

## case_context rule
Rule CTX-PER-PATIENT (below): each PATIENT record describes that one target patient (case_kind=clinical_case or
animal, multiple_index_patients=false) even in multi-patient articles. Enforced by the validators.

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
