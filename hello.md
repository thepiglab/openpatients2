# Schema overview

The `schemas/` folder holds **schema version 2.1.0**: fourteen clinical extraction tasks, plus a separate terminology-selection schema. They are generated from the Pydantic models in `src/openpatients2/schemas.py`. Each task is a focused section of one source case, not a full patient chart and not a FHIR or OMOP resource.

There are two copies of each clinical task:

- `*.schema.json` is the full schema, with titles and descriptions, used for validation.
- `wire/*.json` is the same constraints with titles and descriptions stripped, sent to the model as structured output. The prompt carries the clinical meaning; the wire schema only constrains the JSON shape.

## Shared shape

Every clinical section has the same envelope:

- **coverage** — `complete`, `limited`, or `not_applicable`. `complete` means the model finished reviewing this source for this task. It does not mean the medical history is complete.
- **limitations** — free-text reasons when coverage is limited.
- **documentation_status** — `documented`, `not_documented`, `explicitly_unknown`, or `not_applicable`. Silence is `not_documented`. An empty list is not “the patient has none of these.”
- **documentation_evidence** — quotes only when the source explicitly says the information is unknown or the task does not apply.

Most tasks then have an `items` list. Oncology uses `tumors`, `biomarkers`, and `treatments` instead. `case_context` uses its own fields rather than items.

Every fact (except the case-context header) carries the same four semantics:

- **subject** — index patient, family member, mother, fetus, newborn, other, or unknown. A relative’s disease is not the patient’s disease.
- **assertion** — present, absent, possible, conditional, or unknown. An explicit negative is a real fact, not a missing field.
- **temporality** plus a **time** mention — current, historical, future, or unknown, with the verbatim phrase. Dates are only filled when the source states a full unshifted date.
- **evidence** — one or more exact source quotes.

No schema invents ICD, SNOMED, LOINC, or RxNorm codes. Those come later, in a separate linking stage.

## The fourteen tasks

| Schema | What it holds | The distinction that matters |
|---|---|---|
| [`case_context`](schemas/case_context.schema.json) | What kind of source this is: clinical case, EHR-style record, exam vignette, synthetic vignette, cadaver, animal, in vitro, review with no case, multi-patient, or unknown. Also species, whether there are multiple index patients, living/deceased/cadaver, care setting, and chief complaint. | A death during care is not a cadaver report. An exam question is not an EHR. This gates whether a row can enter a default cohort. |
| [`demographics`](schemas/demographics.schema.json) | Age at presentation, diagnosis, or another stated event; documented sex; sex assigned at birth; gender identity; race, ethnicity, and language as written. | Do not compute age from the publication year or infer attributes from a name. A range stays text. |
| [`conditions`](schemas/conditions.schema.json) | Diagnoses and history: name, verification (confirmed, provisional, differential, refuted, unconfirmed), clinical status (active, resolved, remission, recurrence, inactive), role (primary, comorbidity, complication, history, differential), site, laterality, severity, diagnostic basis. | Present vs possible and current vs historical are separate. A drug is not proof of its usual indication. A refuted diagnosis must be an absence assertion. |
| [`symptoms_function`](schemas/symptoms_function.schema.json) | Symptoms, signs, functional status, performance scores (ECOG, Karnofsky, GCS), mental status, quality of life. | Copy an explicit score. Do not compute one from prose. A symptom is not a diagnosis. |
| [`medications`](schemas/medications.schema.json) | Drug or regimen name, what happened to it (current, started, administered, ordered, planned, held, stopped, declined, historical), dose, unit, route, frequency, duration, indication, regimen. | Ordered or planned is not given. Formalin on a specimen is not a treatment. |
| [`allergies`](schemas/allergies.schema.json) | Substance, scope (one substance, all drug allergies, all allergies, or unknown), category, reaction, type (allergy, intolerance, adverse effect), severity. | “NKDA” denies known **drug** allergies only. Missing allergy documentation is not NKDA. |
| [`procedures_devices`](schemas/procedures_devices.schema.json) | Procedures, devices, and non-drug therapies, plus site, laterality, indication, finding, and complication. Actions include completed, ordered, planned, declined, cancelled, device present, and removed. | Done is not planned. A device being present is not the same event as insertion or removal. |
| [`observations`](schemas/observations.schema.json) | Labs, vitals, imaging, pathology, microbiology, and physiologic results: value, comparator (`<`, `>`, and so on), unit, flag, reference range, specimen, method, site. | An order is not a result. A negative test is a present observation with a negative flag, not a negated diagnosis. Pending or not-performed tests use `result_absent_reason` and carry no invented value. |
| [`oncology`](schemas/oncology.schema.json) | Three linked collections. **Tumors** have a local id (`t1`, `t2`, …), primary site, histology, grade, laterality, extent, metastatic sites, stage system/edition/context, T/N/M, and disease status. **Biomarkers** have gene, alteration, somatic vs germline, interpretation (including VUS). **Treatments** have modality, given vs planned, line, intent, setting, and response. Markers and treatments point at a tumor only when the source links them. | Metastatic site is not the primary. Grade is not stage. Somatic, germline, and VUS are not interchangeable. The model does not derive TNM from imaging. |
| [`family_genetics`](schemas/family_genetics.schema.json) | Family conditions, genetic tests, inheritance, and consanguinity, with relative, age at diagnosis as text, gene, variant, and origin. | A relative’s cancer is not the index patient’s cancer. A VUS is not a pathogenic result. |
| [`reproductive_perinatal`](schemas/reproductive_perinatal.schema.json) | Pregnancy, gravidity/parity, delivery, gestational age, newborn and congenital events, fertility, lactation. | Mother, fetus, and newborn are different subjects. Do not infer pregnancy from a specimen or calculate a parity history. |
| [`social_exposures`](schemas/social_exposures.schema.json) | Tobacco, alcohol, substances, occupation, environment, housing, food security, social support, exercise, diet, travel, sexual history, access to care. Status is current, former, never, passive, or unknown. | “Denies current smoking” is not lifetime never-smoking. Pack-years are stored only when the source states them. |
| [`outcomes`](schemas/outcomes.schema.json) | Death, discharge, recovery, improvement, deterioration, recurrence, readmission, complication, follow-up, lost to follow-up, censored, functional outcome, and perinatal outcomes (live birth, stillbirth, termination). | Discharged is not cured. No follow-up does not mean death. Cause is stored as written, without inferred causality. |
| [`care_plans`](schemas/care_plans.schema.json) | Future follow-up, referrals, monitoring, counseling, and goals of care, with status planned, recommended, scheduled, declined, or completed. | A recommendation is not care that already happened. Do not answer the “next best step” in a USMLE question. |

## The extra wire schema

[`wire/terminology_selection.json`](schemas/wire/terminology_selection.json) is not a clinical section. After extraction, a later stage may ask a model to pick among candidate ICD-10-CM, SNOMED CT, or LOINC codes. That schema only allows `select` or `abstain`, an optional candidate id, a relation of `equivalent` or `broader`, and a short rationale. The selector cannot invent a new code, and a selected code never overwrites the original fact.
