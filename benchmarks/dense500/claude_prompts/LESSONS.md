# Lessons from the prior Codex (GPT-6 orchestrator + GPT-6.1 Sol annotators) dense500 run

Source: digests of ~/.codex/sessions/*01a0eac3-14bb-7161-82cb-90e1d8f10c7d*. Subagent prompt text was encrypted and is not recoverable.

## User requirements (dense500)
- Full Glimmer-shaped patient bundle per article: all 14 clinical schemas + timeline + summary + figure attribution ("It needs to be everything. Like everything.").
- Figures: pixels actually inspected before any visual claim; caption-only claims labelled separately.
- Stronger model personally verifies every output against the article. Validator success is never review.
- Progress tracked per article ID in the JSON checklist (data/dense_clinical_cases_v1/article_checklist.json); report counts periodically ("article N of 500").
- Don't stop early; keep going until done.

## Recurring annotator defects the reviewer caught (check these explicitly)
Attribution / ownership
- Cited/published cases (from reference list) assigned to the index patient. A literal quote containing "the patient" is not proof.
- Cohort/aggregate data copied into one patient. Comparison specimens/figures (other species, normal controls) assigned to the patient.
- Facts copied across patients in multi-patient articles; patients collapsed together.
Timeline
- Planned/proposed/recommended treatments or tests recorded as performed.
- Later remission grouped under earlier treatment; age attached to wrong event; "eight years earlier" anchored wrongly.
- Return visit treated as admission; follow-up after discharge missing; many events with few ordering links.
- A time must belong to its own fact (no borrowing "on admission" from a neighbouring quote).
Numbers / units
- Magnitude and unit in separate fields; keep comparator, verbatim text, reference interval; explicit high/low flags preserved.
- Scientific notation kept only as text; ordinal results ("1+") treated as numeric; chart readings mixed with exact table values.
- Unit inconsistencies flagged as source_conflict, never silently corrected.
- Table-only values omitted; normal labs and microbiology omitted.
Meaning / status
- Suspected vs confirmed diagnoses blurred; "not indicated" ≠ "declined" ≠ "cancelled"; a target level ≠ measured lab; a drug named as an example ≠ administered.
- Negative exam findings must be kept (assertion=present + negative result; explicitly absent symptom = absent).
- "History of" ≠ active.
Coverage / form
- Specialized schema doesn't replace general one (cancer dx also in conditions).
- Duplicate entries (e.g. 28 duplicate procedures); clinical_statement copying whole paragraphs — keep statements concise with focused quotes.
- Quotes too short to establish timing — include the context that establishes time.
- Source conflicts kept explicit (certainty=source_conflict, scoring=review_only), never reconciled.
