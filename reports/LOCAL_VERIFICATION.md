# Local verification — OpenPatients 2 v0.5.0

**294 automated tests passed; zero failures.** Clinical schema **2.1.0** is unchanged.
No real model, B200 inference, clinical adjudication, or full licensed terminology
release was run in this environment. New importer tests are miniature, clearly
labeled synthetic file-format fixtures, not a validation against production releases.

## Executed checks

- Editable package installation and real `uv run --no-sync op2 --help` entry point.
- `uv run --no-sync pytest -q --junitxml=reports/junit.xml`: 294 passed.
- Python byte compilation; `bash -n` for every shell/Slurm script.
- Existing clinical mock: all 14 extraction tasks, source validation and cohort query.
- New ontology CLI demo: four unique-exact LOCAL mappings, one ambiguous mapping,
  one mock selector completion with saved reasoning, explicit test-only review,
  one hierarchy-based cohort match, and a graph with 14 fact/15 evidence/5 concept
  nodes. Test reviews are labeled SYNTHETIC_TEST_NOT_CLINICIAN. No official codes
  or real clinical labels are represented by these LOCAL identifiers.
- Reasoning demo: 6 synthetic records × 3 mock modes × 2 repeats × 14 tasks =
  **504 mock requests**, 7 intentional disagreement rows, no clinical-equivalence
  or throughput claim.
- Source demo: 7 preserved rows, 4 explicit synthetic article identifiers, 3 mock
  PMC articles, and **zero image requests or bytes**. Bare numeric ID namespaces
  are not inferred; tests cover source-reviewed crosswalk attachment separately.

New tests cover catalog membership/version/hashes, exact-match truncation/ambiguity,
ICD hierarchy/instruction preservation, SNOMED Snapshot/dialect/semantic-tag handling,
LOINC axes and retrieval-only keywords, inactive concepts, crosswalk rules retained
unevaluated, cycle/dangling-parent rejection, negative/family context, source-integrity
checks, fixed candidate membership, selector failures/truncation/trace capture/resume,
queue failure cancellation, review provenance/gating, code-descendant queries and
source/evidence/graph export. Tests are not estimates of mapping precision or recall.

## Boundaries

No credentials or licensed vocabularies are bundled. No clean PyPI resolution was
performed: installation used matching dependencies already available in the container.
Run normal network-enabled `uv sync --extra data` on CPU and retain its generated
lockfile before reproducible deployment. The test virtual environment is not included.

Full-article patient localization, new summaries, image interpretation, FHIR
resource/server creation, patient simulation, and source-corpus throughput remain
separate planned components. Clinical extraction currently uses the original note.
See `docs/END_TO_END_PIPELINE.md` for these boundaries.

## Archive verification

The published source is copied without virtual environments, credentials, model
weights, caches or licensed terminology data. The separate extracted-source run passed **294 tests, zero failures**;
`archive-junit.xml` records that run. Only verification reports were added after
testing the extracted source; all source/config/test files remain byte-identical.
