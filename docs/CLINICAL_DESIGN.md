# Clinical extraction and cohort design

Schema version **2.1.0**. See [the new missingness/negative-case review](SCHEMA_REVIEW_V2_1.md) and [within-model reasoning study](REASONING_STUDY.md). This is a deliberately conservative, source-grounded research representation, not a diagnostic assistant, clinical decision support certification, or a conformant FHIR/OMOP export.

The old project descriptions and output-review notes were available, but the complete original schema file was not recovered. These schemas are a versioned redesign, not a claimed in-place patch of that unavailable file.

## The correct unit is a source case, not necessarily a person

Open-Patients combines published case summaries, educational USMLE vignettes, synthetic TREC cases and real-EHR-derived summaries. Its card lists 180,142 records with `_id` and `description` fields. PMCPatients contributes 167,034 and USMLE 12,893; TREC-CDS and TREC-CT supply smaller components. The dataset card describes TREC-CDS 2016 as real-EHR-derived and the other named TREC sets as synthetic. These distinctions remain explicit in `source_kind`. [1]

A source row is not proof of a unique longitudinal human patient. A case report can contain several subjects, family members, an index mother and fetus, a cadaver, animal material or background discussion. Exact text duplicates are detected; patient identity resolution and publication-level near-duplicate linkage are **not** claimed. A researcher can retrieve an evidence-backed candidate cohort, but the result is not a representative clinical population. Do not estimate population prevalence or infer treatment effects from case-report sampling.

Default cohort gates require a fully extracted, single-human clinical case/record with suitable source provenance. They exclude unknown/multi-patient/cadaveric/anatomical/nonclinical contexts and educational/synthetic inputs. Explicit overrides are possible and are recorded in the cohort manifest. Exact-text deduplication defaults on. Uniform sampling applies to matching corpus records only.

## Shared semantics on every clinical fact

Each fact carries:

- **Subject:** index patient, family member, mother, fetus, newborn, other, or unknown. A relative's disease never automatically becomes the patient's disease. Index-subject identity is independent of biological role.
- **Assertion:** present, absent, possible, conditional, unknown. An explicitly negative test is a *present observation* with a negative interpretation; it is not a nonexistent observation. A diagnosis discussed as a differential remains possible.
- **Temporality:** current, historical, future, unknown, with the literal time expression, relation and anchor. Do not derive actual dates from publication time or a deidentified placeholder.
- **Evidence:** one or more exact contiguous source quotes plus a source heading only when it exists. Deterministic validation resolves every quote to all matching Unicode code-point spans. Repeated matches are retained, not arbitrarily assigned.

All keys are required, and unknown nullable values must be `null`. An empty list means no eligible facts were extracted from this source; it does **not** mean the patient has no diagnoses, takes no medications or has no family history. Clinically important negations are explicit facts. `coverage=complete` means this task reviewed the supplied source, not that the source documents a complete history. `limited` output and cross-section contradictions exclude a case from default indexing eligibility.

The schemas intentionally omit model-invented ICD, SNOMED, RxNorm, LOINC and oncology terminology identifiers. Raw documented terminology comes first; reviewed, versioned mappings are a separate deterministic stage.

## Fourteen focused tasks

| Task | What it captures | Important distinction |
|---|---|---|
| `case_context` | Case type, species, index subject, number of patients, care setting, chief complaint, subject state | A death during clinical care differs from a cadaveric anatomy report; an exam question is not an EHR. |
| `demographics` | Age at presentation/diagnosis/other event, documented sex, sex assigned at birth, gender identity, race/ethnicity/language when stated | Do not calculate age from publication year or infer demographic attributes from names. A range remains text rather than a fabricated midpoint. |
| `conditions` | Primary diagnoses, comorbidities, complications, history and differential; activity/remission/recurrence; site/laterality | Present versus possible and current versus historical are separate dimensions. A medication is not proof of its usual indication. |
| `symptoms_function` | Symptoms, signs, mental status, function, performance scores, quality of life | Extract an explicit ECOG/Karnofsky/GCS value; do not compute one from prose. |
| `medications` | Drug name, actual action, dose, units, route, frequency, duration, indication and regimen | Ordered/planned/held/stopped differs from administered. Do not treat formalin used on a specimen as treatment. |
| `allergies` | Substance, reaction, category, allergy/intolerance/adverse-effect distinction, severity | NKDA is an explicit absence assertion; missing allergy documentation is not NKDA. |
| `procedures_devices` | Procedures, devices and nonpharmacological therapies, site, laterality, findings and complications | Completed versus planned/declined/cancelled; device presence versus insertion/removal. |
| `observations` | Relevant laboratory, vital, imaging, pathology, microbiology and physiological results | A test order is not a result; negative test polarity is not diagnosis negation; preserve inequality signs, exact units and ranges. |
| `oncology` | Separate tumors, primary site, histology, grade, stage system/edition/context/TNM, extent, metastases, molecular markers, tumor-linked treatments | Metastatic site is not necessarily the primary; grade is not stage; somatic/germline/VUS are not interchangeable. |
| `family_genetics` | Relationship-specific history, genetic tests, inheritance, consanguinity | Relative's cancer differs from index-patient cancer; a VUS is not a pathogenic result. |
| `reproductive_perinatal` | Pregnancy, gestational age, gravidity/parity, delivery, fetal/newborn events, fertility/lactation | Separate maternal/fetal/newborn subjects; do not infer pregnancy from a specimen or calculate a parity history. |
| `social_exposures` | Tobacco, alcohol, substances, occupation, environment and documented social determinants | Current/former/never/passive/unknown are distinct; do not compute pack-years or infer housing/income from stereotypes. |
| `outcomes` | Recovery, deterioration, death, recurrence, discharge, follow-up, readmission, complications and perinatal outcomes | Discharged is not cured; no follow-up does not establish censoring or death; preserve attribution without inferring causality. |
| `care_plans` | Future follow-up, referrals, monitoring, counseling and goals of care | Recommendations are not completed care; do not solve the “next best step” in a USMLE question. |

Each task has a substantial prompt in `src/openpatients2/prompts/`. A common source-handling instruction is followed by the complete note, then the focused task instructions and a compact field/type/enum guide. The complete machine schema is supplied separately through the endpoint's structured-output interface. **A grammar constrains syntax; it does not teach the model the clinical meanings of fields.** Therefore the semantic prompts and field guides remain essential.

## Oncology design and tumor-specific cohorts

`oncology` contains three collections: `tumors`, `biomarkers`, `treatments`. Every tumor has a local `tumor_ref`; markers and treatments reference it only when the association is supported. A model validator rejects dangling references and duplicate IDs. The relational index scopes references to the source record. The cohort DSL's `linked_to` ensures that a positive EGFR finding cannot qualify an unrelated tumor in the same case.

A tumor stores primary site, histology, grade, disease extent, metastatic sites, stage group, stage system, stage edition, clinical/pathological/post-neoadjuvant context, individual T/N/M categories and disease status. The extractor does not derive TNM from imaging or convert “metastatic” into an unstated stage. Biomarkers retain gene, alteration, somatic/germline/unknown origin, interpretation, assay, specimen and reported value/unit. Treatment includes modality, delivered/planned state, stated line, intent, setting and response.

This is informed by mCODE's cancer-specific data organization. General status/evidence separation is informed by FHIR Condition, and domain separation by OMOP CDM. It is **not a claim of conformance**. [2–4]

A later tumor-identity/event-resolution layer will be needed for long longitudinal records with repeated staging assessments and multiple ambiguously linked primaries. This release prefers unresolved/nullable links to an invented association. That improves conservatism but can lower cohort recall; the evaluation must measure both.

## Validation is layered, not magical

1. **Schema checks:** strict required fields, enums, nullable values, no extra keys; clinically relevant collections have typed fields. Unsupported `uniqueItems` is not used. Application-level checks additionally validate tumor references.
2. **Source checks:** exact quotes, all matching offsets, source-section existence, literal ISO-date restrictions. Numeric normalization and negation cues are review warnings, not reliable clinical inference rules.
3. **Consistency checks:** conflicting demographics, contradictory same-time condition assertions and nonclinical-context/medication conflicts are surfaced without discarding one side.
4. **Clinical adjudication:** compare subject, assertion, temporality, value/unit, completeness, linkage and evidence sufficiency to human labels. A quote can exist in the source while failing to support the claimed fact. Neither grammar validity nor exact quote matching establishes truth.

Indexing recomputes schema/source validation and offsets instead of trusting an editable exported file. It refuses source-digest mismatches. Partial/failed tasks stay explicit and are never converted into empty successful arrays. Cross-task overlaps such as a pathology finding also supporting a cancer diagnosis are kept in separate domains; blindly merging those into one undifferentiated list would lose useful semantics.

## Terminology and missingness

The separate terminology stage imports pinned local ICD-10-CM/SNOMED/LOINC
releases, retrieves candidates, optionally requests model selection, and attaches
reviewed or eligible unique-exact mappings without modifying raw fact values.
`code-link` and `index --catalog` enforce catalog identity, active codes, source/fact
hashes and mapping status. The selector returns candidate IDs or abstains, not newly
invented codes. Missing mappings remain unresolved, never negative findings.

The `index --terms` dictionary option is only for LOCAL/project terms; official codes
require the catalog workflow. Local demo IDs are explicitly not SNOMED/ICD/LOINC.
Never assume an abbreviation such as MS is unambiguous. Unit conversion, eGFR/age/
stage calculation, ingredient normalization and interval reasoning remain separate,
versioned derivations, not code-lookup side effects. See
[Terminology and knowledge graph](TERMINOLOGY_AND_KNOWLEDGE_GRAPH.md).

The cohort DSL supports explicit equality, membership, numeric ranges, literal substring checks, null checks and reviewed concepts. It does not interpret arbitrary natural-language queries or execute model-generated SQL. It does not implement full temporal interval algebra, clinical trial logic, patient identity resolution, or population representativeness. Those are future research-layer features, not hidden promises of this initial index.

## Sources

[1] Open-Patients card: https://huggingface.co/datasets/ncbi/Open-Patients

[2] mCODE implementation guide: https://build.fhir.org/ig/HL7/fhir-mCODE-ig/

[3] FHIR R4 Condition: https://hl7.org/fhir/R4/condition.html

[4] OMOP CDM 5.4: https://ohdsi.github.io/CommonDataModel/cdm54.html
