# Synthetic Hospital: decisions for OpenPatients 2

Reviewed the [paper](https://arxiv.org/html/2609.30027v1) and the [authors' repository](https://github.com/sparkcpark/synthetic_hospital), with code inspection pinned to commit `b047385ae138566c891b7786c0fa9a78e6b5700a`. Small source snapshots and the Git tree are in `runs/article-pilot/research/`; no model weights, EHR corpus or ontology releases were downloaded.

The paper constructs longitudinal records from medical education cases, separates ontology-backed patient state from chart narrative, and uses constrained clustering plus timeline planning before note assembly. Its record-level realism study does not establish population representativeness. These are useful architectural ideas, not evidence that an extraction system can accurately recover every published patient's history.

The code's ICD stage tries a proposed valid/billable code, exact description matching and fuzzy resolution, retaining a flagged unresolved suggestion when appropriate. SNOMED processing includes description matching, reverse ICD-map candidates and optional semantic retrieval. Inspect the pinned [repository tree](https://github.com/sparkcpark/synthetic_hospital/tree/b047385ae138566c891b7786c0fa9a78e6b5700a) rather than treating one fallback's score as universal medical confidence.

Our implementation follows the separation of extraction, terminology, temporal representation and later synthesis. Important choices for published cases:

- Preserve what the article actually documents. Educational answer choices, distractors, general medical knowledge and cited literature cases cannot establish diagnoses for the source patient.
- Resolve terminology against a pinned catalog. A real ICD code may still be the wrong code for the source fact. Fuzzy/embedding similarity proposes candidates; laterality, disease specificity, inactive concepts and coding instructions need their own checks.
- A reversed SNOMED-to-ICD mapping is not necessarily a valid automatic ICD-to-SNOMED equivalence. Keep candidate relationships and mapping rules separate from accepted concept identity.
- LOINC is a multi-axis vocabulary: analyte alone is insufficient where specimen, method, property or collection interval differs. Preserve unknowns instead of selecting a conveniently specific code.
- Attach codes to the original fact with its subject, negation and time. A code on a family-history fact does not become an affirmative diagnosis of the index patient.
- Use article PMCID as the minimum split/deduplication cluster. Several patient records, an abstract, a full-text version and figure variants from the same article must not leak across train/test splits. Cross-article reuse of the same real patient remains a separate unresolved deduplication problem.
- Store relative events now. A future synthetic compiler can choose an artificial starting date and render encounters while preserving documented intervals and distinguishing imputed transitions. It must not fill missing laboratory values or treatment outcomes in the research extraction.
- Before later profile merging, check demographics/species, incompatible temporal histories, causal ordering and source reuse. Never describe merged published identities as a verified real patient. This project intentionally does not perform merging yet.

Implemented now: bounded source acquisition, patient rosters, full-evidence clinical branches, cited summaries, relative timelines, figure inspection, exact-evidence validation, terminology acquisition configuration/catalog matching, cohort indexing and an EHR-seed intermediate export. A physician review protocol, distributed ingestion service, chart simulator and FHIR transformation remain separate work. The cohort index and seed format are custom research representations; FHIR interoperability cannot be established by renaming these JSON fields.
