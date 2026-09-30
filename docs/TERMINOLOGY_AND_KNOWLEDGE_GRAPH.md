# Terminology grounding and evidence-linked patient graphs

OpenPatients 2 v0.5.0 · clinical schema 2.1.0 · terminology/coding/graph formats v1.
This is first-time project setup, not a production coding or billing application.

## Goal and the three different objects

The **clinical schema** organizes facts about a case. **Terminologies/classifications**
provide existing concept identifiers and definitions. The **case knowledge graph**
connects source cases, individual fact occurrences, supporting text, and selected
concepts. We import standard vocabularies; we do not ask a language model to invent
ICD/SNOMED/LOINC or manufacture new patient observations to fill their categories.

Raw clinical facts are retained unchanged. Every mapping is an annotation with its
own version, source/fact/candidate hashes, candidates, status, and review history.
An ontology match can improve retrieval; it is not proof that the fact is true.

## What the implementation provides

* CPU-only local vocabulary import, exact synonym retrieval and SQLite FTS5
  candidates; pinned edition/version, input-file SHA256s, optional expected hashes.
* CDC **ICD-10-CM tabular XML** (not WHO ICD-10 or ICD-10-PCS), including diagnosis
  parents, inclusion terms and inherited instructions. Header/leaf distinction is
  NOT certification of billability. Chapters/ranges are not selectable codes.
* **SNOMED CT RF2 Snapshot** concepts/descriptions, optional explicit dialect
  reference set, and optional inferred `is-a` relationships from a complete edition.
  Active/inactive concepts, FSNs, preferred terms, acceptable synonyms and semantic
  tags remain distinguishable. This is not a SNOMED description-logic reasoner.
* **LOINC CSV** with all six principal axes, active/deprecated status, names and
  retrieval-only keywords. Related-name keywords are deliberately not exact aliases.
* Per-occurrence code linking, optional bounded async model selection with saved
  returned reasoning, review files, cohort indexing and hierarchical queries.
* An optional official SNOMED→ICD map importer retains map rules, groups, priorities
  and advice. Rules are **not evaluated** and never treated as cross-system synonymy.
* JSONL graph nodes/edges preserving original rows, evidence and tumor associations.

No vocabulary content/weights are included. Format tests use synthetic fixtures;
full licensed release imports and clinical mapping accuracy still need evaluation.
No SapBERT/embedding retrieval, medication-to-RxNorm normalization, post-coordinated
SNOMED expressions, billing sequence engine, general causal graph generation, or
natural-language-to-SQL interface is implemented here.

## Routing and clinical scope

| Fact field | Default targets |
|---|---|
| Conditions / oncology tumor `name` | SNOMED CT and ICD-10-CM, independent annotations |
| Symptoms and functional findings | SNOMED CT |
| Procedures, nonpharmacologic therapies, devices | SNOMED CT; semantic domain depends on kind |
| Observations and tumor biomarker test `name` | LOINC candidates |
| Family-history condition `name` | SNOMED CT with the family subject retained |

Medication strings, detailed genomic variants, exposures and demographics remain in
the clinical record even where no system is configured. Skipped domains are reported.
A biomarker mutation label alone often cannot identify a LOINC assay: abstention is
appropriate. The LOINC code describes the observation/test, not its patient value,
positive/negative interpretation, pathogenicity or result status.

### Critical semantics

`No pulmonary embolism` may refer to the embolism concept, while the occurrence
remains `assertion=absent`. A mother's cancer remains `subject=family_member`.
Historical disease is not current disease. Pending tests do not acquire results.
The model may not derive a diagnosis from an exam answer or turn a distractor into
a patient finding. Missing documentation yields no mapping rows, not a fake negative.

`exact_unique` means one active exact catalog name/accepted synonym, no ambiguity
or applicable automatic guard, and not a short ambiguous abbreviation. It does NOT
mean a clinician validated the source extraction. Disable `auto_exact` to require
human adjudication of every mapping. Source/quote checks are structural checks only.

LOINC always requires adjudication in this implementation: specimen, property,
collection interval, scale, analyte and assay specificity cannot be recovered from
an analyte name or a numeric value alone. Laterality mismatch or an unrecorded side,
inactive concepts, and semantic-domain mismatch block selection. ICD instructions
and category/header matches require review; choosing one remains research coding,
not claim eligibility or completeness of a code set.

## State machine and review

```
validated extracted occurrence → local candidate retrieval
    ├─ unique suitable exact alias → exact_unique (or needs_review by policy)
    ├─ competing candidates → ambiguous / needs_review
    ├─ no candidate → unresolved
    └─ missing release → catalog_unavailable

ambiguous/needs_review → optional model → model_proposed / model_abstained
                                      → human review → reviewed / reviewed_unresolved
```

The model returns only `{decision,candidate_id,relation,rationale}`. It cannot insert
a code outside the supplied set. Candidate IDs refer to system/version/code tuples.
`equivalent` and `broader` are separate; narrower unsupported codes are prohibited.
Only **exact_unique or reviewed equivalent** mappings enter default concept queries.
Broader mappings and unreviewed model proposals remain audit data. A proposal never
overrides an accepted exact match or an existing human decision.

Reviews require mapping ID, immutable request digest, reviewer, time and rationale.
They are revalidated on import and tied to the exact output/candidate set. This is
an auditable local workflow, not cryptographic authentication of reviewer identity.
Human review can still be wrong. Gold evaluation must be independent of these
production decisions. No self-reported model confidence is called a probability.

## Acquire and pin vocabularies on CPU

Download through the official providers and unpack to local paths. Read the license
before changing `license_acknowledged` to true. Edit `configs/terminology/catalog.yaml`
to match actual filenames and versions; no login credentials belong in the config.

* ICD-10-CM: CDC/CMS public distribution. The example selects the April 1, 2026
  FY2026 update. FY2027 files are published but effective October 1, 2026. A research
  normalization snapshot need not be the historical encounter coding edition; record
  that distinction. No wall-clock auto-switch or implicit cross-version equivalence.
* SNOMED CT: obtain a complete US Edition through NLM/UMLS licensing, including
  concepts, English descriptions, US dialect and inferred relationship Snapshots.
  The example version is US-20260901; use the matching files. Do not mix releases or
  give the importer a Full history file. US use is no-fee under the applicable license,
  not public-domain redistribution of the entire terminology.
* LOINC: free registered download under LOINC terms; the checked example release is
  2.83. `LoincTable/Loinc.csv` is the main import. Account/license obligations remain.

A successful import verifies file format/consistency, not that the user honestly
labeled the release. Verify official checksums where supplied and set expected hashes.
Derived catalogs, candidate dumps and graph labels can contain licensed vocabulary
content: evaluate redistribution permissions before publishing them or distilling it.

```bash
uv run op2 vocab-build --config configs/terminology/catalog.yaml \
  --output data/terminology.sqlite
uv run op2 code-link --input runs/k2/patients.jsonl \
  --output data/patients-coded.jsonl --catalog data/terminology.sqlite \
  --config configs/terminology/linking.yaml
```

The CPU linker is bounded by configurable workers/batches and does not load model
weights or contact a model API. No optional selector is required to index suitable
exact matches. Choosing a lower candidate limit trades retrieval recall for space;
a top-1 window does not falsely prove an exact match is unique.

## Optional model selection

```bash
# CPU: prepare short fact/evidence/candidate requests, not repeated full articles.
uv run op2 code-prepare --input data/patients-coded.jsonl \
  --output data/coding-requests.jsonl
# Edit selector api.revision to the resolved model SHA; match served model name.
uv run op2 code-profile --config configs/terminology/k2-selector.yaml \
  --tokenizer models/k2 --output data/coding-k2.tokens.jsonl --workers 16

# Model endpoint / GPU stage: after CPU work, with your existing server running.
uv run --no-sync op2 code-infer --config configs/terminology/k2-selector.yaml

# CPU: attach proposals, export review worksheet, then apply completed reviews.
uv run op2 code-apply --input data/patients-coded.jsonl \
  --output data/patients-proposed.jsonl --catalog data/terminology.sqlite \
  --proposals runs/coding-k2-low/proposals.jsonl
uv run op2 code-review-template --input data/patients-proposed.jsonl \
  --output data/coding-review.jsonl
# Edit reviewed rows: reviewed=true, reviewer, reviewed_at, rationale,
# decision=select/abstain, offered candidate_id, equivalent/broader or null relation.
uv run op2 code-apply --input data/patients-proposed.jsonl \
  --output data/patients-reviewed.jsonl --catalog data/terminology.sqlite \
  --reviews data/coding-review.jsonl
```

Selector API config is independent of model/engine; K2 low is an example, not a
requirement. All context budgets are checked before network calls. No source
truncation or model-repair prompt is hidden. Network retries are bounded and audited.
SQLite checkpoints are keyed by input/prompt/model/generation identity. Incomplete
or invalid responses retain raw text/reasoning and are not valid mappings. Failed
runs are resumable; CPU data prep is never automatically run on the GPU node.

`proposals.jsonl` has reasoning alongside final selection, exact prompt, candidate
set, endpoint/model identity, usage and response metadata. `attempts.jsonl` includes
failures/retries. Reasoning is only what the endpoint returns; absent usage is null.
Reasoning-token usage is not added to completion-token usage twice. The resulting
coded patient row retains its model proposal and reasoning. The clinical index and
graph exclude model reasoning. Reviewed proposals can serve as separate terminology
selection distillation candidates; no automatic student training/export integration
is claimed. Existing clinical-task distillation commands remain available.

## Index and graph

```bash
uv run op2 index --input data/patients-reviewed.jsonl \
  --catalog data/terminology.sqlite --output data/clinical.sqlite
uv run op2 graph --input data/patients-reviewed.jsonl \
  --catalog data/terminology.sqlite --output data/clinical-graph
```

Indexing revalidates facts, candidate definitions, exact uniqueness, reviews and
hashes. A coded export cannot be indexed without its catalog. The lightweight
`--terms` CSV option remains for LOCAL vocabularies only, not unchecked official IDs.
Every returned cohort match includes original fact, evidence, concepts and compact
coding audit. Code equality supports explicit system/version; `concept_descendant`
requires version and includes the requested root plus imported descendants, never
string-prefix matching. Default subject/assertion filters are index_patient/present;
add explicit current/active/confirmed criteria when required. Specify family/absent
criteria to search those documented statements instead. `not_documented` does not
match an explicit negative finding.

The graph writes `nodes.jsonl`, `edges.jsonl`, `manifest.json`:

* SourceCase → has_documented_fact → Fact → supported_by → Evidence.
* Fact → denotes → Concept, with mapping ID/status and release identity.
* Concept → is_a → Concept for imported SNOMED/local hierarchies;
  ICD nodes use `classification_parent` for imported tabular parents.
* Biomarker/treatment Fact → linked_to_tumor → Tumor Fact when the extraction contains
  that explicit reference; no inferred causal link.
* SourceCase → source_article → Article where an explicit source identifier exists.

Clinical facts remain distinct occurrences even when they denote the same code.
The graph does not merge people, derive causal relationships, treat external
article figures as validated patient imagery, or manufacture longitudinal events.
No RDF/OWL/FHIR conformance is asserted. This graph can be an input to a separately
implemented simulator/EHR compiler, not proof that such a simulator already exists.

## Evaluation and useful pilot measurements

Keep gold mapping review independent of candidate generation. Measure retrieval
recall@K before measuring selection; the selector cannot choose a missing candidate.
Report correct mappings, incorrect specificity, abstention coverage, unsupported
laterality/specimen/assay selections, and patient/family/negative-state preservation.
Stratify by domain, terminology, abbreviation ambiguity, case source and prompt size.
Review a sample of automatic exact matches as well as the harder model-proposed cases.

Use source-article-clustered held-out cases. Compare each model's reasoning settings
on identical candidate sets and fixed snapshot versions. Candidate Jaccard/overlap
is consistency, not correctness. Existing reasoning-study statistics cover clinical
extraction; automated paired inference/statistics for this new selector is not yet
wired into that runner. Separate selector configs/reports support an initial pilot.
The source/structural tests are not a medical quality benchmark.

## Inspected reference and external sources

Synthetic Hospital stages s05/s06 inspired the modular separation. Its s05 accepts
model-suggested diagnosis codes and contains an unvalidated fallback; its exam
extraction also uses correct answers and distractors. We do not adopt either as a
way to establish facts in published patient cases. We also do not combine cases
sharing codes into a purported real longitudinal patient.

- https://github.com/sparkcpark/synthetic_hospital/blob/main/etl/stages/s05_ontology.py
- https://github.com/sparkcpark/synthetic_hospital/blob/main/etl/stages/s06_relationships.py
- https://www.cms.gov/medicare/coding-billing/icd-10-codes
- https://www.nlm.nih.gov/healthit/snomedct/us_edition.html
- https://www.nlm.nih.gov/research/umls/mapping_projects/snomedct_to_icd10cm.html
- https://loinc.org/downloads/
- https://loinc.org/kb/license
- https://loinc.org/kb/users-guide/major-parts-of-a-loinc-term

External source review date: September 27, 2026. Examples are research choices, not
assertions that these will always be the latest releases.
