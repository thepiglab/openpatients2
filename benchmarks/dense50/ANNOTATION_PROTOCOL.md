# Dense 50-article source annotation protocol

> **Superseded.** Annotate and review dense50 articles with the binding protocol
> `benchmarks/dense500/ANNOTATION_PROTOCOL.md` and its shared rules
> (`src/openpatients2/rules/annotation_rules.yaml`). This file is kept as history.

This dataset is being constructed for prompt optimization and independent
evaluation. It is AI-annotated and separately reviewed by the parent assistant;
it is not physician-adjudicated gold. Do not claim exhaustive clinical truth.
Annotators must read original retained source text, not previous model outputs,
historical reference answers, or each other's annotations.

## Deliverable for each article

Write `drafts/<article_id>.json`. Read its full `sources/<article_id>.json` and
`source-text/<article_id>.txt`. Use `schema_version: dense-article-annotation/1`.

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
  (occurred/planned/not_occurred/uncertain), `fact_ids`, `time_text` (literal or
  null), `evidence`.
- `temporal_relations`: `from_event_id`, `to_event_id`, `relation`
  (before/after/same_time/overlaps/unknown), `evidence`. Use only supported
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
- `fields`: the source-supported attributes using the existing task schema's
  field names/enums. Read `src/openpatients2/schemas.py` as needed. This is a
  partial positive target, not a fully populated runtime object. Omit unstated
  attributes instead of inventing a value or filling every null. Include
  `subject`, `assertion`, time and action/status where they are supportable.
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

For a performed negative examination, the examination occurred (`assertion=present`)
and the result is negative. For a symptom explicitly absent, use `assertion=absent`.
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

The parent assistant will then personally review every article, its facts,
timeline, omissions and attribution against the source, amend the final file,
and record its own article-level review receipt. Drafts must never mark parent
review complete. No GEPA optimization or GPU campaign is part of annotation.
