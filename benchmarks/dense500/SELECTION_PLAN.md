# What the 500 articles should test

The downstream unit is an identifiable patient with a supported clinical course.
Selection is purposive and stratified, not a random collection of interesting
papers. The sampling plan is fixed before acquiring the expanded source set.
Title metadata is a discovery aid; patient counts, course and suitability require
full-source confirmation. Selection must never depend on model performance.

## Primary specialty quotas (500 in total)

Each article has one primary quota stratum and can have several secondary tags.
Twenty articles each: cardiology, vascular medicine, respiratory medicine,
gastrointestinal medicine, hepatobiliary medicine, nephrology, endocrinology,
neurology, psychiatry, infectious disease, autoimmune/rheumatology, dermatology,
hematology, oncology, obstetrics, gynecology, surgery/transplantation,
ophthalmology, ENT, musculoskeletal/rehabilitation, toxicology/adverse drugs,
and veterinary medicine. These 22 strata contribute 440 articles.

Pediatrics and neonatal/infant medicine contribute 30 articles each. Age is also
tagged across other specialties; pediatric cases are not restricted to these
two strata. The 23 previously retained articles count toward these quotas.

## Cross-cutting selection targets

These overlap the specialty strata. They are acquisition targets to verify, not
labels to force onto a source or claims about population prevalence.

| Dimension | Target coverage |
| --- | --- |
| Identifiable patient count | About 320 single-patient, 100 two-patient, 50 three-patient, 30 with 4–10 individually described patients |
| Common and uncomplicated patterns | At least 150 articles on common diagnoses or ordinary clinical workflows; uncomplicated recovery as well as adverse courses |
| Normal/negative evidence | At least 250 articles with explicit patient-level normal examinations, normal test results, negative investigations, or absent symptoms |
| Rich trajectories | At least 300 with three or more supported clinical stages; include presentation → investigation → treatment → response |
| Treatment changes/failures | At least 100 with switches, discontinuations, recurrence, adverse reactions, contraindications, or failure to improve |
| Longer follow-up/repeat encounters | At least 100; relative ordering is sufficient, exact calendar dates are not required |
| Measurements | At least 150 with serial values, units, dose changes, ranges, comparators, or laboratory tables |
| Attribution stress cases | At least 100 multi-patient sources; include family members, mother/infant, comparison cases and repeated subjects |
| Media and structured source material | Include radiology, pathology, clinical photos, graphs, clinical charts and tables; collect captions and ownership without claiming unviewed pixel review |
| Species | 480 human-primary articles and 20 veterinary-primary articles, with explicit species on every patient |

Rare disorders remain useful but must not dominate merely because case-report
titles emphasize novelty. “Normal” means what the source explicitly documents;
it never means assuming all unreported tests were normal. A case-report corpus
cannot establish the distribution of routine clinical encounters or represent
a healthy-population EHR cohort.

## Eligibility and rejection rules

Prefer case reports, small case series and clinically detailed illustrative
vignettes within broader studies. Include enough history, patient-specific
workup, treatment or follow-up to support structured extraction. A sparse source
can be retained as a deliberately tagged challenge, but not counted as a richly
documented trajectory. Aggregate-only studies, reviews without an original
individual case, protocols and purely technical papers do not count toward 500.

Require accessible English full text and a verified compatible open license.
Use the existing article-level rights policy; do not equate OA with permission
to redistribute. Preserve DOI, PMCID/version, authors, URL, text/XML hashes,
license notices and third-party figure limitations. Reject retracted sources
and record unavailable tables or supplements. Do not infer absent patient data
from a parser that omitted a table.

Titles and full texts receive separate screening. Discovery candidates remain
`candidate_source_acquired` until content screening; annotation can identify a
needed replacement. The manifest records acceptance and exclusion reasons.

## Train, validation, and test separation

Target 300 training, 100 validation and 100 test articles, stratified by specialty
and patient count. All 23 previously benchmarked articles stay in training.
Group follow-up reports, shared patients and near-duplicate sources into a single
split. Check explicit cross-report links; inability to find a link does not prove
patient independence. Record unresolved family linkage and keep it out of clean
held-out comparisons until resolved.

Freeze splits before prompt optimization. Annotators see source material, never
Glimmer predictions or historical answer keys. GEPA may use training labels and
validation for selection; test annotations stay out of optimization and feedback.
No benchmark improvement is claimed merely from annotating the test set.

## Annotation and review

Every selected source is read in full by a GPT-6.1 Sol annotator, then reviewed
against its source by the parent assistant. Keep atomic clinical facts, exact
evidence, patient/encounter ownership, polarity, measurements, relative event
order and caption/panel attribution. Normal findings, uncertainty and genuine
source conflicts are first-class targets. Store drafted, structurally validated,
parent-reviewed and physician-adjudicated states separately; the last remains
false unless an actual clinician performs that review.

Local acquisition is limited to small XML/text payloads with a 150 MB network
budget per bounded acquisition run. No model weights, bulk PDFs or image archive
is downloaded. Retain only selected source evidence and annotations; remove
temporary caches and rejected payloads after review.
