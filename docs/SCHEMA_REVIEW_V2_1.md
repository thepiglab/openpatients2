# Clinical schema review: 2.1.0

This pass retains all 14 focused tasks and strengthens **documented fact versus absent documentation**, diagnostic certainty, test completion, allergy scope, source support and subject attribution. It is a software/semantic review, not independent clinical validation or a claim of FHIR conformance.

## Missing information is not a negative clinical fact

Every task now requires `documentation_status` and `documentation_evidence` alongside `coverage`, `limitations` and its typed fact collections.

| Source situation | Representation | Evidence requirement |
|---|---|---|
| No relevant information anywhere in the supplied source | `not_documented`, empty collections | No manufactured quote or placeholder fact. `documentation_evidence: []`. |
| Actual positive, negative, suspected or historical finding | `documented`, supported fact(s) | Literal source quotation for each clinical fact. |
| Source explicitly says the relevant information is unknown/unavailable | `explicitly_unknown`, empty collections | Exact quote in `documentation_evidence`. |
| Source explicitly establishes the task is not applicable | `not_applicable`, empty collections | Exact quote in `documentation_evidence`; do not infer it merely from sex or age. |
| The model cannot complete review of the supplied source | `coverage: limited` and nonempty limitations | Not a successful complete negative history; default cohort gates exclude it. |

For an ordinary items-based task, this is a **complete valid empty section**:

```json
{
  "coverage": "complete",
  "limitations": [],
  "documentation_status": "not_documented",
  "documentation_evidence": [],
  "items": []
}
```

Oncology uses empty `tumors`, `biomarkers`, and `treatments` collections rather than `items`. `case_context` has its own nullable contextual fields. Generate the complete schema files with `op2 schema`; do not force this small items example onto differently shaped tasks.

`coverage: complete` means the model reviewed this supplied text for the task, **not** that a complete medical history was available. `documented` requires an actual supported fact; explicit clinical negatives count as facts. A silent note cannot become "no cancer," "no medications," or "normal labs." An empty output is valid but may still be an extraction omission, which requires gold/review to detect.

## Explicit clinical negatives stay explicit and scoped

“No known drug allergies” becomes a documented absence assertion scoped to **drug allergies**. It does not deny food or environmental allergies. “No family history of breast cancer” is a relationship-scoped negative family finding, not absence of cancer in the index patient. “Denies current smoking” does not establish lifetime never-smoking. “No evidence of recurrence” is not a denial of the original cancer diagnosis.

`allergies.scope` distinguishes `specific_substance`, `drug_allergies`, `all_allergies`, and `unknown`. A drug-allergy-wide denial must use the drug category. The prompts discourage broad all-allergy denials when only drug allergies were assessed.

## Diagnostic certainty and chronology

`conditions.verification_status` now distinguishes `confirmed`, `provisional`, `differential`, `refuted`, `unconfirmed`, and `unknown`. It does not replace assertion or temporality. A refuted diagnosis must be an absence assertion; a prior differential can remain a separate possible fact at its documented time. Historical confirmed disease does not become currently active. Medication use, imaging appearance, or a risk factor alone must not be promoted into an unstated definitive diagnosis.

## Test orders and pending tests do not become negative results

`observations.result_absent_reason` is nullable, with `pending`, `not_performed`, `not_reported`, `not_applicable`, or `unknown` when no result is present. A real result cannot coexist with a reason saying its result is absent. Ordered/planned/pending/not-performed states cannot carry a fabricated completed test result.

A completed HIV test with a negative result remains a **present observation** with negative interpretation and no result-absence reason. A pending culture is neither negative nor normal. Preserve units, inequality signs, specimen and timing; do not impute zero values, standard reference ranges, calculated clinical scores or numerical midpoints from prose ranges.

## Prompt review across all tasks

Every task prompt has a decision-examples/negative-controls section covering a supported example, clinically scoped negation, valid empty output and relevant ambiguity. Examples are explicitly instructions, never source evidence. Important refinements include:

- Context/demographics: index case versus relative, exam vignette versus treatment delivered, age at a specified event, no attributes inferred from names.
- Conditions/symptoms: refuted versus differential, symptoms versus diagnosis, explicit functional scores only, negatives scoped to organ/time/subject.
- Medications/procedures: administered versus held/stopped/planned, device present versus insertion, no specimen reagents as medication.
- Allergies/observations: NKDA scope, missing versus negative history, pending versus completed negative tests, no fabricated normality.
- Oncology: grade versus stage, primary versus metastatic site, tumor-specific biomarkers/treatment, no stage/TNM/line-of-therapy/germline inference.
- Family/reproductive: relative versus index patient and maternal/fetal/newborn attribution, no pregnancy/parity inference.
- Social/outcomes/plans: current/former/never, follow-up missing versus favorable outcome, discharge versus cure, recommendation versus completed care.

Source quotes must support the populated clinical attributes, not merely mention a relevant noun. Deterministic checks verify literal text and structure; they do not prove semantic entailment, completeness or appropriateness of every qualification. Cross-section conflicts remain review findings rather than automatically reconciled histories.

The system prompt no longer asks to keep reasoning brief: that would confound a reasoning-effort experiment. It still requires final task JSON without extra clinical inventions. Returned reasoning is stored separately and never treated as documentary evidence.

Index facts come solely from extracted clinical sections. Returned reasoning stays in extraction/audit archives and is excluded from the clinical index payload. Missing clinical sections do not satisfy positive or negative cohort predicates.

## Primary semantic references

- FHIR data absent reasons (missingness has causes, not a single negative truth value): https://hl7.org/fhir/R4/codesystem-data-absent-reason.html
- FHIR diagnostic verification distinctions: https://hl7.org/fhir/R4/valueset-condition-ver-status.html

Field names and enumerations here are project-specific; similarity to a clinical standard is not interoperability certification. The existing Clinical Design document covers oncology linkage, source provenance and research-index limitations.
