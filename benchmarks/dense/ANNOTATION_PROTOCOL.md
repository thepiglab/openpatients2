# Dense 500-article source annotation protocol

This dataset is being constructed for prompt optimization and independent
evaluation. It is AI-annotated and separately reviewed by the parent assistant;
it is not physician-adjudicated gold. Do not claim exhaustive clinical truth.
Annotators must read original retained source text, not previous model outputs,
historical reference answers, or each other's annotations.

**One rule set for both pipelines.** The semantic rules in "Shared annotation rules"
below are generated from `src/openpatients2/rules/annotation_rules.yaml`, the same file
that renders the rule blocks in the local-LLM runtime prompts
(`src/openpatients2/prompts/*.md`, roster/summary/timeline and figure prompts). The
reference must be exactly what the runtime pipeline is asked to emit. Field *shapes*
come from `src/openpatients2/schemas.py`; field *meaning* comes from the shared rules
and the task prompt `src/openpatients2/prompts/<task>.md`, which every annotator and
reviewer must read for each task they annotate. The mechanically checkable rules are
enforced by `openpatients2.semantic_rules` in both pipelines (rule IDs appear in error
messages as `[ID]`). This protocol governs every corpus article.

The final deliverable is a prototype of the full Glimmer patient bundle, not
only a list of positive facts. The fact annotation below is the evidence-linked
working representation. It must also be converted to, inspected in, and delivered
as all 14 actual clinical task contracts plus summary, patient timeline, roster
and figure attribution. A populated schema does not establish clinical truth.

## Deliverable for each article

Write `articles/<article_id>/annotation_draft.json`. Read its full `articles/<article_id>/source.json` and
`articles/<article_id>/source.txt`. Use `schema_version: dense-article-annotation/1`.

Top-level keys:
- `article_id`, `text_sha256`, `xml_sha256` (copy from source metadata).
- `disposition`: individual_cases, aggregate_only, no_patient_case, or unresolved.
- `patients`: list of the patient objects below.
- `figures`: figure objects below, covering every figure in the source.
- `source_coverage`: one row per source segment: `segment_id`, `disposition`
  (patient_facts/background/aggregate/figure_caption/unresolved), `patient_ids`,
  `note`. All source units must be reviewed; this is not proof of completeness.
- `article_limitations`: list of genuine unresolved issues, source conflicts,
  unreviewed pixels, unavailable supplements, or scope limits.
- `annotation_provenance`: `annotator_model`, `passes` (list of performed review
  passes), `prior_predictions_consulted` (must be false), `status` (draft).

Each patient:
- `patient_id` (p1, p2 in original case order), `label`, `species` (human,
  nonhuman, unknown), `species_as_documented`, `identity_evidence`.
- `facts`: source-grounded facts, as defined below.
- `events`: `event_id`, `description`, `occurrence`
  (occurred/planned/conditional/not_occurred/unknown; rule TIMELINE-ENUMS — the same
  vocabulary as the runtime `timeline_v2` contract, copied without remapping),
  `fact_ids`, `time_text` (literal or null), `evidence`.
- `temporal_relations`: `from_event_id`, `to_event_id`, `relation`
  (before/after/same_time/during/overlaps/unknown), `evidence`. Use only supported
  order, not an invented total timeline or assumed disease course.
- `summary`: list of `{text, fact_ids}`. Cover presentation/history, workup,
  diagnoses, actual/planned treatment and outcomes; no new facts.
- `domain_coverage`: mapping of all 14 domain names below to
  `{status: documented/not_documented/unresolved, note}`. Silence is never a
  patient-level negative.
- `ambiguities`: list of source conflicts/uncertainties with evidence and what
  must not be inferred.

Each fact:
- `fact_id`: unique short ID within article, e.g. p1-f001.
- `task`: one of case_context, demographics, conditions, symptoms_function,
  medications, allergies, procedures_devices, observations, oncology,
  family_genetics, reproductive_perinatal, social_exposures, outcomes, care_plans.
- `collection`: items, tumors, biomarkers, treatments, or null for case_context.
- `fields`: the source-supported attributes using the task schema's field
  names/enums (`src/openpatients2/schemas.py`) with the MEANING defined by the shared
  rules below and `src/openpatients2/prompts/<task>.md`. This is a partial positive
  target, not a fully populated runtime object. Omit unstated attributes instead of
  inventing a value or filling every null; the materializer fills omitted
  categorical fields with `unknown`, so supply every attribute the source supports
  (comparator `=` for a plain number, age `text_value`, `flag=normal` for a result
  called normal, ...). Include `subject`, `assertion`, time and action/status where
  they are supportable.
- `evidence`: list of `{segment_id, quote}`. Quotes must be exact contiguous
  text from that source segment, sufficient to ground qualifiers/negation.
- `clinical_statement`: a clear short statement of the fact for semantic review.
- `certainty`: supported, ambiguous, or source_conflict.
- `scoring`: include or review_only. Unresolved claims are review_only.
- `notes`: concise reason for unusual representation or ambiguity; empty allowed.

One fact can have several attributes, but do not conflate repeated measurements,
different doses/encounters, planned versus completed actions, multiple patients,
or clinical positives with negatives. Record measurements separately by test and
occurrence. Preserve documented normal and negative findings as carefully as
abnormal findings. No diagnosis from a reference-range comparison unless the
source states it. Keep patient, family-member and organism-from-patient subjects
distinct. Do not convert published percentages/cohort means into individual cases.

Magnitude and unit use separate fields when unambiguous. Preserve comparator,
verbatim expression and reference interval. Scientific notation must not silently
change units; uncertain number parses stay textual. Document source inconsistencies.

Examination signs and clinical scales belong in `symptoms_function` (rule
SECTION-SIGNS): an explicitly absent sign ("no murmur") is `kind=sign`,
`assertion=absent`; a scoped normal examination ("neurologic examination normal") is a
sign with `text_value` "normal" and `assertion=present`. A negative laboratory or
imaging test that was performed is an observation with `assertion=present` and
`flag=negative`/`normal`. For a symptom explicitly absent, use `assertion=absent`.
Historical afebrile presentation and later fever can both be true. Do not use
`current` to mean every fact in a retrospective article is simultaneously active.

Each figure:
- `figure_id`, `caption_evidence`, `patient_ids`, `scope`
  (individual/shared/aggregate/background/external/unresolved).
- `panels`: list of `{panel, patient_ids, scope, description_from_caption,
  modality, body_part, evidence}`. Unknown panel labels or modality remain null.
- `caption_summary`, `visual_status` (not_inspected unless pixels actually viewed),
  `visual_description` (null unless viewed), `attribution_notes`.
Do not invent pixel details from a caption. Available downloaded assets and their
hashes will be listed separately. If instructed to inspect assets, use the image
viewer and record actual evidence separately from caption claims.

## Required annotation passes

1. Read the complete source; enumerate original patients and segment ownership.
2. Extract all clinically relevant documented information for those patients,
   including history, tests, treatments, dose/route, failures, complications,
   negatives, outcomes, follow-up and normal table rows. Background assertions
   and cohort statistics are not patient facts.
3. Re-read patient-bearing text, tables and captions looking for omissions,
   wrong patient, timing and polarity errors. There is no fact-count quota.
4. Check chronological events, exact quantities/units and caption/panel ownership.
5. Validate every quote locally and correct failures. Leave ambiguities explicit.

Check inline bibliographic references and the referenced citation metadata when
assigning discussion sentences to patients. A literal quote containing “the
patient” is insufficient: the sentence can describe a patient in a cited article.
For example, PMC13624890.1 discusses diclofenac and remission with reference 15,
a published vulvar case; those claims must not become treatment and recovery of
its index male patient. Keep such claims outside that patient's accepted facts,
summary and timeline. Record unresolved ownership explicitly. Citation linkage
is evidence for investigation, not permission to merge patients across articles.

The parent assistant will then personally review every article, its facts,
timeline, omissions and attribution against the source, amend the final file,
and record its own article-level review receipt. Drafts must never mark parent
review complete. No GEPA optimization or GPU campaign is part of annotation.

## Full patient-bundle review

Run `op2 dense schema_targets ROOT --folder drafts --article ARTICLE_ID` to build the
actual Pydantic contracts in `articles/ID/schema_target.draft.json`. For articles with a parent-approved
`articles/ID/annotation.json`, use that canonical file with `--folder annotations`; do not
replace parent corrections with an older draft. Generation fills missing nullable
attributes with null and unknown categorical attributes with unknown. It never
supplies a missing substantive clinical value. Inspect those defaults against the
source: an attribute that was documented must be added to the source annotation.

Copy a reviewed target to `articles/ID/schema_target.json`. Review the actual full clinical
sections, citation identifiers, summary, event kinds and time types, partial event
order, roster completeness and figure/panel subjects. Clinical values, ownership
and chronology changes belong in the fact annotation first, followed by regeneration.
Direct submission edits may refine the supported structural classifications only.
Do not mark pixels inspected or invent detailed image descriptions from captions.

Before signing, inspect every attribute in each actual item model, not only the
fourteen section names. Read source-to-output to find omitted information as well
as output-to-source to detect unsupported claims. Documented sites/laterality,
specimens, methods, qualifiers, numeric units, drug route/duration/indication,
diagnostic certainty, recommendations and follow-up must not be lost behind nulls.
Specialized schemas do not replace general ones: a documented cancer diagnosis
also belongs in Conditions, named test results in Observations, and delivered
cancer drugs/procedures in the applicable general and Oncology contracts. Mark
these as alternate representations with shared evidence; row totals do not count
unique biological facts. Never infer unreported attributes merely to fill fields.

Before signing, run the runtime gate command on the article; it applies the same
checker objects the local-LLM pipeline must pass (shared semantic rules, runtime task
validators on the patient packet, roster order, summary, timeline, figure and EHR
sidecar round trips) and must report `"passed": true`:
`op2 dense reference_gates ROOT --article ARTICLE_ID --folder FOLDER`.
`op2 dense schema_review --role` and `review_record.save_review` refuse to sign on clinical
gate/semantic errors; `op2 dense export` withholds any article whose gates fail and records
the reasons in `article_checklist.json` (`withheld`, `runtime_gates`).

Use `op2 dense schema_review ROOT --folder FOLDER --article ARTICLE_ID --role agent
--reviewer NAME --notes NOTES` only after inspection. The parent separately reads
and reviews that bundle and records a parent receipt. Receipts bind source,
annotation and full-target hashes. The release exporter requires both full-schema
receipts as well as the parent source-annotation review. A stale receipt blocks
promotion; validator success is never substituted for review.

Facts with `scoring: review_only` (source conflicts, ambiguities) are exported in the
unified record inside their section collection with `scoring: "review_only"`, `fact_id`
and `certainty`, so scorers can ignore predictions that match them; they are never
dropped and never counted as accepted facts.

The release is one unified row per completed article
(`openpatients2-unified-article/1`, `data/{train,validation,test}.jsonl` plus
`schema.json`), built by `src/openpatients2/unified_record.py` with every join
already resolved. The former per-type files (`patients.jsonl`,
`article_bundles.jsonl`, ...) remain only under `legacy/` for the viewer. `schemas.json` freezes the actual
runtime schema contracts. Full articles and fabricated inference metrics are not
included. Measurement parsing sidecars use the same deterministic runtime parser
and keep their own unresolved/unreviewed state without changing extracted values.

## Actual figure inspection and independent parent review

The deliverable includes actual pixel annotation, not only captions. Fetch one
article with `op2 dense figure_assets ROOT --article ID`; the cache is bounded to 1 GB,
8 MB per asset, and not included in the release. Read each path in its manifest
with `view_image`. Inspect all original panels, labels, chart axes/series and
printed values, using bounded magnified inspection where necessary. Unreadable
values remain unknown; author clinical interpretation needs exact text evidence.

Write `articles/ID/visual.json` with `format: dense-figure-reference/1`, article
ID, source text hash, and one row for every figure. Inspected rows contain
`status: inspected`, the inspected assets' URLs/SHA256s, `analysis` with the
actual `JointFigureAnalysis` contract (`visual: FigureVisuals`, `attribution:
FigureReview`), and limitations. Use the existing `figure_visuals.py` model:
whole-figure and per-panel general/detailed descriptions, image kind, graph/type,
readings/units/axes, modality/submodality, medical domain, body parts, pixel facts,
caption claims and author-supported clinical significance. Every panel needs
patient/shared/aggregate/unresolved attribution. Do not infer identity from looks.
Unavailable rows have null analysis and the actual failure; they are incomplete,
not successful image reviews. No figures means no visual task is applicable.

After actual inspection, `op2 dense visual_review ROOT --article ID --role agent
--reviewer NAME --notes NOTES` validates and records the annotator's review.
The parent must independently view every asset and read every visual output
against the article before a separate parent receipt. Neither downloading nor
schema validation establishes inspection. Receipts bind source, patient identity,
output and image hashes. Only dual-reviewed visuals reach `figure_visuals.jsonl`,
`figure_panels.jsonl` and patient `vision.panels`. Keep the earlier caption-only
companion identifiable as text-only. No physician adjudication is claimed.

## EHR multimodal sidecars (additive; informational, not completion gates)

These sidecars prepare the downstream multimodal synthetic EHR. They never edit
the annotation, runtime schemas, visual submissions or their receipts. Each has
its own contract, independent agent and parent receipts (binding source, sidecar,
asset and patient-identity hashes plus the sidecar contract fingerprint), and
goes stale on any change. The checklist reports them as `informational_checks`
outside `completed`. Commands are `op2 dense <tool> ...` (from any directory);
`R` = `benchmarks/dense`.

**Supplement manifest** (`dense-supplement-manifest/1`, `R/articles/ID/supplements/manifest.json`),
required when the source lists supplements:
1. `op2 dense supplement_assets acquire R --article ID` downloads declared files into
   `data/.dense-supplement-cache` (hard 30 GB shared cap under a lock, 250 MB per
   file; larger files keep a HEAD size/type plus a 2 MB head sample and, for ZIP,
   a central-directory tail). It writes `R/articles/ID/supplements/assets.json` with hashes,
   MD5 checks, detected type and a literal bounded peek (CSV/TSV/XLSX rows,
   PDF page count + first-page text, DOC/DOCX text, ZIP member list, image size,
   whitelisted non-identifying DICOM tags, ffprobe video metadata). Unsupported or
   unavailable files are recorded with their status, never dropped.
2. `op2 dense supplement_manifest_review R --article ID --draft`, then open each cached
   file (or the peek when only a sample exists) and complete every `review`
   block in a copy at `R/articles/ID/supplements/manifest.json`: `content_class`
   (reporting_checklist, literature_table, clinical_case_table, lab_results,
   vital_signs_series, medication_record, imaging_dicom, imaging_still, video,
   waveform_signal, genomic_data, questionnaire_instrument, protocol_methods,
   raw_dataset_tabular, code, other, unknown), rationale, `classification_basis`
   (full_content / peek_content / caption_and_filename_only), `patient_scope`
   (patient_level / aggregate / background / administrative / unresolved),
   `patient_ids` with literal `ownership_evidence` (article segment quote or
   machine-peek quote), `ehr_includable`, `ehr_modality`, `estimated_counts`
   (machine-read counts must match; your own estimates need a `basis`), notes and
   limitations. `machine` and `machine_suggestion` blocks are copied from the
   acquisition record and cannot be edited. Checklists, literature tables,
   methods and code are never EHR-includable; unresolved ownership is never
   includable. Do not put clinical values here: typed facts stay in
   `dense-supplemental-clinical-reference/1`, numeric series in the series sidecar.
3. Validate with `op2 dense supplement_manifest_review R --article ID`; sign with
   `--role agent|parent --reviewer NAME --notes NOTES` only after inspection
   (signing re-hashes the cached files). After both receipts,
   `op2 dense supplement_assets cleanup R --article ID` frees the cache.

**Figure EHR labels** (`dense-figure-ehr-labels/1`, `R/articles/ID/figure_ehr_labels.json`),
required when the source has figures, after the visual submission is final:
`op2 dense figure_ehr_review R --article ID --draft` derives a deterministic default
per panel from the reviewed `image_kind`, modality, chart type/scope and panel
attribution. Copy to `R/articles/ID/figure_ehr_labels.json` and, for every panel and the
whole figure, set the final `ehr_category` (clinical_imaging, waveform,
pathology, clinical_photo, clinical_chart_of_patient_data,
non_clinical_diagnostic_aid, diagram_or_schematic, literature_or_aggregate,
other), `ehr_includable`, a real `rationale` replacing the DRAFT text, and
`basis` (pixels_reviewed or visual_record_reviewed). A label that differs from
the default needs `override_reason`. Only the five patient-data categories can be
includable, and only for panels attributed to a local patient (external or
cited-case images are not). Validate/sign with `op2 dense figure_ehr_review R
--article ID [--role ...]`. Editing the visual submission makes labels stale.

**Measurement series** (`dense-measurement-series/1`, `R/articles/ID/measurement_series.json`),
recommended for serial tables and numeric supplements:
`op2 dense measurement_series_review R --article ID --draft` lists every literal
main-article table cell (and CSV/TSV/XLSX supplement cells) with grid
provenance plus DRAFT candidate row series. Keep real series only; assign
patients with literal evidence, `measurement_kind`, literal `unit_text` from a
cited cell, and `time` (literal `time_text`; an `offset_value` must be printed in
that text, except 0 with a stated basis). Cells from reviewed supplement
transcriptions use `origin: supplement_transcription`. Raw cells and parsed
points stay separate: `parsed` must equal the deterministic `parse_measurement`
of the raw cell, a dash/blank cell is `missing_cell` (never a value), and units
are never converted. `coverage: complete` requires every value cell to be used
once or listed in `excluded_cells` with a reason.
Footnoted and dose cells (`measurement_series.expected_parse`, still
deterministic): trailing footnote markers (`^(a)`, `*`, `†`) are stripped for
parsing and kept in `parsed.footnote_markers` (so `-^(b)` is `missing_cell`);
dose cells parse as `kind: dose_components` with literal routes, `× n`
multiplicity and `+` combinations (`5 IM × 3^(a)`, `5 IM + 5 PO`). A cell meaning
printed in a table legend or footnote is recorded on the point as optional
`cell_status` (refused, discontinued, held, not_given, prn_or_emergency) with
`legend_cell_id`, a `context` cell quoting that legend. The validator requires the
legend to name the cell token (`-`, `d/c`) or its marker and to state the status.
A dash stays `missing_cell` and is never zero. Without a printed legend there is no
`cell_status`. These fields are additive: documents without them are unchanged,
and the contract fingerprint excludes them, so existing receipts stay current.

**Figure privacy flags** (`dense-figure-privacy-flags/1`, `R/articles/ID/figure_privacy_flags.json`),
required for every article with figures. Policy is flag only: panels stay
includable. A separate sidecar bound to the visual submission (so signed
figure-ehr receipts are unaffected), with its own agent/parent receipts.
`op2 dense figure_privacy_review R --article ID --draft` pre-fills hints from earlier
reviewer text and known reports. On pixels, set per panel `burned_in_identifiers`
(none | timestamps_or_device_text | direct_identifiers: names, IDs/MRNs, birth
dates, operator names, UIDs), `sensitive_anatomy` and `identifiable_face`, each
flag with a short category description (never the identifier values), and real
notes. Validate/sign with `op2 dense figure_privacy_review R --article ID [--role ...]`.

**Export and gates.** EHR-facing rows (`figure_ehr_labels.jsonl`,
`supplement_manifests.jsonl`, `measurement_series.jsonl`, article bundles) carry
`patient_species`, `non_human_patient` and `article_has_non_human_patient`;
non-human patients are included, flagged. Figure EHR rows carry `privacy_flags`
from a dual-signed privacy sidecar. `EHR_SIDECAR_GATES` in `src/openpatients2/dense/export.py` (default
False) makes `supplement_manifest_reviewed` and `figure_ehr_labels_reviewed`
completion gates; measurement series and privacy flags stay informational. A
supplement whose dual-signed manifest classifies every file as
reporting_checklist, literature_table, protocol_methods or code, with
`classification_basis: full_content`, no patient-level scope and not includable,
satisfies `supplement_review_or_no_supplements`. A video supplement (MP4 etc.)
has no page/pixel sidecar: its dual-signed manifest row is the content review and
satisfies the gate when the file is downloaded and machine-probed as video,
`content_class: video`, `classification_basis: full_content` (sampled frames were
actually viewed), a duration or frame count is recorded, and a limitation states
the sampling scope (which frames/interval were viewed; audio and continuous
motion not reviewed). `article_checklist.json` lists such supplements under
`manifest_reviewed_supplements` (`manifest_video_frames_sampled`); they stay
`content_inspected: false`. External-link media with no declared PMC asset
(e.g. YouTube; `download_status: no_declared_asset`/`rejected_host`) are recorded
honestly as `caption_and_filename_only` with limitations and are never a content
review. **Unobtainable media: complete, flagged** (`UNAVAILABLE_MEDIA_POLICY=
'complete_flagged'` in `src/openpatients2/dense/export.py`, the default): when an article's only gap is
media that cannot be obtained (such an external-link supplement, or a figure the
dual-signed visual review records as `status: unavailable` because the source
declares no asset, e.g. a video figure), the article completes. The media stay
listed in `article_checklist.json` (`unavailable_media`,
`completed_with_unavailable_media`) and in the unified record (top-level
`unavailable_media`; per figure/supplement `media_available: false`,
`content_reviewed: false`), never as reviewed. A failed download of a declared
asset is not unavailability. `'block'` restores the strict rule. Other clinical-data supplements still need the `op2 dense supplement_review`
path (details: `SUPPLEMENT_DOCUMENT_REVIEW.md`). Word-processor supplements (DOCX/DOC/RTF/ODT, also inside a ZIP) use `dense-supplemental-text-document-reference/1` via `op2 dense supplement_assets extract-text` (see that file).

**Document peeks (peek engine `supplement-peek/2`).** With the optional
`anydoc` extra (`firecrawl-anydoc==0.2.4`, offline only; hosted OCR is banned),
DOC/DOCX/PPT/PPTX/RTF/ODT/ODP peeks come from anydoc's structured text model and
include `tables` (`rows` with null for merged-over slots, `spans`, `header_rows`).
DOCX lines/cells are cross-checked against the raw XML. Any that are not literal
are listed in `nonliteral` and cannot be quoted. PDF stays on pdftotext and XLSX
on the built-in reader. Peeks already acquired are never recomputed:
`op2 dense supplement_assets peek` is refused once any manifest receipt exists.

## Shared annotation rules

<!-- GENERATED rules:start — edit src/openpatients2/rules/annotation_rules.yaml, then run `PYTHONPATH=src .venv/bin/python -m openpatients2.annotation_rules --write` -->
Rules shared with the runtime prompts (`src/openpatients2/rules/annotation_rules.yaml`). Rule IDs appear in validator messages as `[ID]`.

- **EVID-NEGATION** (Negations and qualifiers are inside the quotes; validator): Evidence quotes are exact contiguous source text and must contain the words that establish negation, uncertainty, subject and timing. An item with assertion=absent needs its negation cue ("no", "denied", "without", "ruled out", "negative") inside its own quotes, not in a neighbouring sentence. A word that negates a specific finding is that finding with assertion=absent, and the quote keeps the word: "afebrile"/"apyrexial" -> fever, assertion=absent; "asymptomatic"/"symptom-free" -> the named symptoms (or symptoms), assertion=absent; "pain-free" -> pain, assertion=absent; "uneventful recovery" -> the complications it excludes, assertion=absent (or an outcome stated as such). Never record the negation word as a present item's text_value, and never invert polarity to silence a warning.
- **NAME-LABEL** (name is a concise label; evidence carries the wording): `name` (and other label fields such as substance or action_text) is a concise clinical label for the item: the source term or abbreviation trimmed to the concept (e.g. "serum creatinine", "left nephrectomy"), with no codes, values, dates or invented synonyms. It need not be a literal substring; the exact source wording is carried by the evidence quotes.
- **GLUED-TEXT** (Free text is normal spaced prose; validator): Every free-text field you write (name, text_value, clinical_statement, event descriptions, summaries, limitations, ambiguities, captions, notes) is normal prose with a space between words: "after seven months", "could not be performed", "by month 9", never "aftersevenmonths", "couldnotbeperformed" or "bymonth9". Never drop spaces to shorten text; shorten by choosing fewer words. Abbreviations and terms keep their source spelling ("HbA1c", "IL-6", "Roux-en-Y").
- **NO-INFERENCE** (No inference-added facts): Add no fact or attribute that the source does not state: no device "in place" that is only implied, no diagnosis implied by a drug or test, no normal result implied by silence, no ordering implied by paragraph order. Record such implications as an ambiguity or limitation, never as an item.
- **NO-BORROWING** (No borrowed times, routes, sites, indications or settings; validator): Time, route, dose, body site, laterality, indication and care setting must come from the item's own evidence. Never borrow them from a neighbouring sentence about another fact, drug, encounter, table row or patient (e.g. "on admission" from a different finding, "intravenous" from a different drug). A fact's time comes from its own evidence sentence or from a table row, column, heading or footnote, or a figure caption's lead sentence, that structurally governs it. Adding an extra evidence quote whose only role is to supply a time (a neighbouring sentence, or a bare "By Day 3" / "On day 7" fragment) is borrowing: leave time.text null (relation unknown) when the fact's own sentence states no time. A quote that spans several sentences does not make one sentence's time the time of a fact stated in another, unless the time sentence scopes what follows ("On admission:", "On admission, ..." heading the findings, a table or figure caption).
- **TIME-LITERAL** (Time text and anchors are literal or null; validator): time.text must occur literally in the item's own evidence. time.anchor uses the literal source phrase when one exists (case-insensitive substring of the source, e.g. "admission", "surgery"; prefer restoring that phrase over deleting the anchor), otherwise null; never a paraphrase such as "index hospital admission" or "table-relative treatment course". date_iso (YYYY-MM-DD) only for a full calendar date written in time.text: the ISO literal or an unambiguous month-name form ("May 20, 2025", "20 May 2025" -> 2025-05-20); all-numeric non-ISO dates ("05/06/2025", "20.05.2025") and partial dates ("May 2025") leave date_iso null. relation (at, before, after, ...) needs a time text, anchor or date to relate to: when all three are null, relation is unknown. "By <time>" is an upper bound with relation before ("by May 20, 2025", "by day 3", "by the next morning"; anchor null unless the source names one), not at, after, until or unknown. "Within <duration>" with no named reference point ("within 24 h", "within two weeks") is likewise an upper bound: relation before, anchor null. "Within <duration> of/after <event>" names its reference point: anchor is that literal event phrase and relation is after ("within two hours of presentation": anchor "presentation", relation after) — never before the anchor.
- **TEMPORALITY** (temporality is unknown unless the evidence labels it): temporality is not the item's time (that is `time`) and not inferred from the narrative. Default temporality=unknown, including for ordinary findings, test results, treatments and outcomes of the described course, even when the case is told in the past tense. Use historical only when the item's own evidence places it in the patient's history before the presented episode ("history of", "previously diagnosed", "prior", "known", "X years ago", "in childhood"); future only for a planned, scheduled or recommended item that has not yet happened; current only when the evidence says it is ongoing at the time described ("currently", "ongoing", "still", "at present"). Never set temporality from paragraph order, the presentation date or the publication date.
- **ROSTER-RELATIVES** (Newborn, fetus and relatives stay in the index patient's record): In a case about one index patient, a fetus, newborn, mother or other relative mentioned in that case (the delivery, the baby's sex, weight, Apgar score or NICU stay, a family history) is NOT a separate roster patient: their facts stay in the index patient's record with subject fetus, newborn, mother or family_member. A relative is a separate patient only when the article presents them as an additional case with their own individually described clinical course (e.g. "Case 2", a twin with their own workup and treatment, a donor with their own evaluation).
- **ROSTER-LABEL** (Roster labels describe the patient from the case text; no names; validator): A patient label is a short description written from the case text's own identity evidence (age, sex, presentation or diagnosis as the case states them, e.g. "Case 2: 17-day-old infant with cystic biliary atresia"), not the article title copied or trimmed. Labels never contain personal names, initials, record numbers or other identifiers, even when the source prints them.
- **NO-COHORT-COPY** (Cohort statements are not copied to individuals): Cohort, series-level, literature-review and general statements ("In our study, there was no mortality", "these tumors usually present with ...", published cases cited by reference) are not facts about an individual patient. Do not copy them to each patient; shared statements apply to several patients only when the source explicitly says so for those patients.
- **ASSERT-ABSENT** (Absent, refuted and not documented; validator): assertion=absent is an explicit source negation for this patient; silence is not absent (no item). For conditions, a diagnosis excluded by workup ("ruled out", "excluded", "CT showed no ...") is assertion=absent with verification_status=refuted; a denied or absent history ("no history of diabetes", "denied hypertension") is assertion=absent with verification_status=unknown. assertion=absent is never combined with verification_status=confirmed, provisional or differential. Example: "He was afebrile" is symptoms_function fever (kind=sign) with assertion=absent, not a present sign named "afebrile".
- **OBS-COMPARATOR** (Numeric results carry a comparator; validator): An observation with numeric_value uses comparator "=" unless the source prints <, <=, >, >= (or "less than", "greater than", "up to") for that value. comparator=unknown is only for items without a numeric value. Upper-bound wording ("up to N", "maximum of N", "at most N", "not exceeding N", "no more than N") is "<=": "fever up to 38°C" is numeric_value 38 with comparator "<=" (">=" for "at least N", "N or more"). A measured peak or maximum stated as a value ("peaked at 6.2 mg/dL", "a peak of 6.2", "Tmax 38.9°C", "SUVmax 8.5") is "=", with the peak/maximum kept in name or text_value; "approximately"/"about" is not a comparator (stays "=").
- **OBS-FLAG** (Normal flags; no flags from reference ranges; validator): flag=normal when the source calls that result normal, unremarkable or within normal limits. high/low only when the source says so (or prints an H/L marker or the interval and the classification); never classify a value against outside reference ranges. positive/negative are for qualitative tests (antibody, culture, PCR, stain). A result described only as "normal" is flag=normal, not negative or unknown.
- **OBS-TABLE-ROWS** (Every patient table cell is an observation): Each result a table reports for this patient is its own observation: one item per analyte per result cell (per time column, visit or specimen), with the value, unit and comparator from that cell, the time from its column/row heading or caption, the reference interval in reference_range_text, and the row as evidence (plus the caption/heading quote when it supplies the time). Normal and unflagged values are items too (flag=normal only when the table or text calls them normal). Never summarize a table as one item, keep only abnormal rows, or skip a row because the text repeats it; a value stated in both table and text is one item citing both. Rows about other patients or literature cases are not this patient's.
- **OBS-NOT-PERFORMED** (Tests not performed; validator): A test the source says was not performed, not done or not available is an observation with status=not_performed, result_absent_reason=not_performed and assertion=present (the non-performance is affirmed). It is not assertion=absent and never a negative result.
- **SOC-NEVER** (Never-users are affirmed; validator): status=never only for explicit never-use wording ("never smoked", "has never used drugs", "lifelong non-smoker"), always with assertion=present; status=never with assertion=absent is a double negative and invalid. "No known contact with tuberculosis", "no exposure to ...", "denies alcohol" or "denies current tobacco use" are NOT never-use: record them as assertion=absent with status unknown (or current for a scoped current denial).
- **DEMO-AGE-TEXT** (Ages keep the literal source phrase; validator): Every age with numeric_value keeps text_value = the literal source phrase that states it ("67-year-old", "aged 3 months", "8 years old"), copied verbatim from the item's evidence quote as an exact substring: same case, spacing and characters, including Unicode hyphens (U+2010 "67‐year‐old" stays U+2010, never retyped as "-"); unit is the written unit. Spelled-out or approximate ages keep text_value and leave numeric_value null.
- **DEMO-SEX-WORDING** (Documented sex comes from the source's own words about the patient): documented_sex is supported by the source's own wording about this patient: a sex/gender noun ("woman", "boy", "male neonate") or a pronoun that unambiguously refers to the patient ("She was admitted…", "his mother") — quote that word in the evidence. Never infer it from names, photographs, diagnoses or organs. It does not establish sex_assigned_at_birth or gender_identity.
- **ONE-STATEMENT** (One statement, one item, in the section that owns it): Record each source statement once per section and in the section that owns it. Never repeat an identical item; serial results are separate items only when value, time or specimen differ. Two sections hold the same statement only where the rules intend both views: a diagnostic procedure and its findings (procedures_devices + observations, SECTION-IMAGING), a cancer treatment in oncology.treatments and in medications or procedures_devices, a malignant diagnosis in conditions and oncology.tumors, a delivery in reproductive_perinatal and a live birth in outcomes. Otherwise choose one: a diagnosis or infection is a condition (an observation only when a test and its result are stated); a drug taken as treatment is medications, not social_exposures (social_exposures keeps tobacco, alcohol, recreational drugs, occupational and environmental exposures and social circumstances); induction of labor or a cesarean is one procedures_devices item, with the delivery itself in reproductive_perinatal.
- **SECTION-SIGNS** (Signs and clinical scales belong in symptoms_function): Physical-examination signs, including normal or negative examination findings ("no murmur", "abdomen soft", "neurologic examination normal"), and clinical scales or scores belong in symptoms_function (kind=sign or performance_score). An explicitly absent sign is kind=sign with assertion=absent; a scoped normal examination is a sign with text_value "normal" and assertion=present. observations holds laboratory, vital-sign, imaging, pathology, microbiology and physiologic test results. Criterion for a scored instrument: if it rates THIS patient's symptoms, symptom severity, functional status or disability, physical performance, quality of life or acute illness severity (clinician-rated or patient-reported questionnaire, bedside performance test) it goes in symptoms_function as kind=performance_score (functional_status, mental_status or quality_of_life when that is the construct), e.g. GCS, NIHSS, ECOG, mRS, Barthel, pain NRS/VAS, PHQ-9, HADS, IBS-SSS, mMRC dyspnea grade, NEWS, Woods-Teuber mirror-movement scale, MMSE/MoCA screens. If it is a standardized cognitive, intellectual, neuropsychological, developmental or behavioral test battery reported as norm-referenced scores (IQ, index scores, T-scores, percentiles: WISC-R, BRIEF-P, Bayley) or an instrument/lab-like measurement (spirometry, audiometry, polysomnography indices, breath tests), it goes in observations (kind=other or physiologic). Objective physical performance tests measured in physical units or counts stay in observations as measurements (kind=physiologic): Timed Up and Go (s), gait speed (m/s), 6-minute walk distance (m), grip or knee-extension force (kg, N), 30-second chair-stand repetitions (CS-30), and the Life-Space Assessment (LSA) and J-CHS frailty criteria scores as reported; questionnaires and rating scales about the same domain go to symptoms_function. Never record the same score in both sections.
- **SECTION-IMAGING** (Diagnostic imaging may also be a procedure): A performed diagnostic imaging study, endoscopy or other diagnostic procedure may also be listed in procedures_devices (kind=procedure, action=completed) when the source describes the procedure itself; its findings and results always go in observations. Listing the procedure never replaces the observation.
- **SECTION-CAREPLANS** (care_plans scope): care_plans holds follow-up, referral, monitoring, counseling, rehabilitation and goals-of-care actions, whether still planned or already carried out; action_status records which (planned, recommended, scheduled, declined, completed, unknown). Drug administration belongs in medications and operations/diagnostic procedures in procedures_devices; what a follow-up visit found belongs in outcomes/observations.
- **ONC-BENIGN** (Benign lesions are not oncology tumors; validator): oncology.tumors holds malignant or explicitly suspected-malignant neoplasms. Benign tumors and lesions (adenoma, hemangioma, lipoma, benign or inflammatory polyp, cyst) are conditions, not oncology tumors, unless the source explicitly evaluates them as a possible malignancy (then assertion=possible).
- **CTX-VITAL-STATUS** (Vital status is consistent with outcomes; validator): case_context.subject_state=deceased_during_course when this patient died during the described course, including follow-up; living_at_presentation only when no death of this patient is reported; cadaver for a pre-existing anatomic specimen. A patient with an outcomes death item (assertion=present) is never living_at_presentation.
- **CTX-PER-PATIENT** (case_context describes the one target patient; validator): Each per-patient record describes that one target patient: case_kind=clinical_case (animal for a nonhuman patient, or another single-subject kind) with multiple_index_patients=false, even when the article has several patients. multi_patient/true is only for a whole-article record with no target patient.
- **ROSTER-ORDER** (Patients numbered by first mention; validator): Number patients p1..pn in order of their first mention in the source (the earliest segment and position of each patient's identity evidence), not by importance, age or table position. Patient identifiers are joined on this order downstream. reported_individual_count is the number of individual patients the source states (literal, e.g. "two patients" -> 2; a singular indefinite case statement such as "We present a 67-year-old woman", "a case of ...", "a patient", "one patient" -> 1), else null; roster_complete is true only when every stated individual is in the roster. In the dense annotation these are the article-level roster block (reported_individual_count, roster_complete, evidence quote stating the number), which the materializer reads; never patch them into schema submissions.
- **SUMMARY-CLAIMS** (Summary claims are phase paragraphs): A patient summary is 1-6 claims (usually 2-5), each a 25-85-word paragraph covering one phase of the course in chronological order: presentation and relevant history; workup and diagnosis; treatment and course (including complications); outcome and follow-up. Merge a thin phase into its neighbour; never split one phase into a claim per sentence or per finding. Each claim cites quotes for every statement it makes and adds no fact the record does not state.
- **TIMELINE-ENUMS** (One timeline vocabulary; validator): Event occurrence is one of occurred, planned, conditional, not_occurred, unknown: not_occurred for an explicitly cancelled, declined or not-done event; conditional for an if-then plan; unknown when the source does not establish whether it happened. Pairwise relations are one of before, after, same_time, during, overlaps, unknown; unknown records a pair whose order the source leaves open. Never invent order from paragraph order. Event kind and the time kind/precision (and a literal calendar value) are classified on the event itself; in the dense annotation they are the event's kind, time_kind, time_precision and time_calendar_value fields. The materializer reads them; never patch event kinds or time classifications into schema submissions by script.
- **MED-ROUTE** (Routes keep the source spelling; validator): medications.route copies the route as written in the item's own evidence ("intravenous", "IV", "orally", "p.o."); do not expand, abbreviate or normalize it, and never borrow a route from another drug. Automated normalizers preserve the literal spelling.
- **FIG-LABELS** (No invented panel labels; question marks only inside quotes; validator): Use the panel letters or numbers printed in the caption or on the image; otherwise the panel label is null (whole figure). Caption position words ("left", "upper right", "the image on the left", "Main image") and patient names ("Case 1", "Patient 2") are not printed labels; do not use them as panel labels in the annotation, the visual description panels, or the figure-EHR label and privacy-flag rows that reuse those panel IDs. In visual descriptions a "?" appears only inside quotation marks that transcribe printed text (e.g. “Unstable?”); put uncertainty in limitations.
- **FIG-EHR-COMPARISON** (Normal-comparison images are non-clinical aids): Normal-comparison, control, atlas or reference images that are not this patient's data are non_clinical_diagnostic_aid and never EHR-includable, even when they are microscopy or radiology. Only panels attributed to a local patient can be includable.
- **FIG-PRIVACY** (Privacy flags name categories only): Privacy flags describe the category of visible identifiers (e.g. "patient name in scanner header"), never the identifier values themselves; flags never change EHR includability.
- **MEDIA-UNAVAILABLE** (Unobtainable media are listed, never described): A figure or supplement whose source declares no obtainable asset (no image/video URL, or only an external link such as YouTube) is recorded as unavailable, with a limitation saying it was not viewed. Never describe, classify from content, attribute or extract facts from media you did not view; caption text stays caption evidence. Such media do not block completion: the article is complete, flagged, and the media stay listed as unavailable and not reviewed (media_available=false, content_reviewed=false). A download or rendering failure of a declared asset is not unavailability: retry or report it.
- **REF-INTEGRITY** (References point to existing facts and events; validator): Every reference inside a patient record points to an existing id of that same patient: event fact links, temporal relation endpoints, summary claim sources, ambiguity fact links and alternate_representation_of. When a fact or event is removed (e.g. as a duplicate), remove or repoint every reference to it in the same edit.
<!-- GENERATED rules:end -->
