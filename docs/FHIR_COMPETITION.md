# Beam competition: terminology and FHIR stage

The campaign reuses its running local Beam endpoint after each extraction. It
freezes a source-hashed article packet, proposes ICD-10-CM candidates, asks Beam
to select only candidate IDs or abstain, and deterministically compiles FHIR R4
resources. The model never writes arbitrary FHIR JSON. There are two exports:

- `uncoded/`: source text and typed fields, with no unreviewed terminology attached.
- `experimental-coded/`: equivalent model selections attached, with an explicit
  `model-coding-unreviewed` tag on the Bundle and every resource. Broader matches
  remain in the coding audit. This export is not clinically reviewed or ready for
  dataset release.

`run_article_stage` in `openpatients2.fhir_campaign` accepts the unified prediction
JSONL, source root, a new output directory, local endpoint, frozen arm manifest,
and catalog path. It returns and saves `receipt.json`. The worker controls a
300-second coding budget; the library default is 900 seconds. Candidate-bearing
requests are prioritized by task, capped at 16 batches of 8 mentions, with at
most 4 concurrent requests. Token/context preflight, split/repair limits, native
Beam template settings and reasoning capture use the extraction client. Empty
candidate sets become explicit abstentions and consume no model calls. Requests
beyond the bounded budget remain pending; they are not silently counted as coded.

The receipt includes selection/abstention/pending counts, export validation and
resource counts, catalog and compiler hashes, elapsed time, and standard
`PilotRunner` call-ledger paths. Those ledgers preserve messages, raw returned
reasoning, finish reasons, prompt/completion/reasoning token usage when reported,
and request timing. Unknown token counts stay unknown. `complete` denotes stage
coverage, not coding accuracy.

## Frozen terminology

`configs/terminology/icd10cm-2027.json.gz` contains **74,879 selectable leaf codes**
from CDC/NCHS's official FY2027 release. Its SHA-256 is
`5a58583057090f62d58895c68a100e0a363f6f9c8eac81cbe522cc7634731c0d`.

[CMS's release page](https://www.cms.gov/medicare/coding-billing/ICD-10-codes) and
[CDC's release page](https://www.cdc.gov/nchs/icd/icd-10-cm/files.html) specify
October 1, 2026 through September 30, 2027. The importer pins the official
[order/descriptions archive](https://ftp.cdc.gov/pub/Health_Statistics/NCHS/Publications/ICD10CM/2027/icd10cm-code-descriptions-2027.zip)
and [tabular/index archive](https://ftp.cdc.gov/pub/Health_Statistics/NCHS/Publications/ICD10CM/2027/icd10cm-table-and-index-2027.zip):

| Input | SHA-256 |
| --- | --- |
| Descriptions | `93e3ad6004badf470c55bfe679b748ae88fd9b2b421851e409eec382c7713b9a` |
| Tabular/index | `99b5c2be16ff53eab861cc0be4b1dacba27f687b8ff4bc700679944355037ee0` |

Rebuild with `.venv/bin/python -m openpatients2.icd10cm_catalog --help` for CLI
options; the builder refuses a changed source archive. The catalog retains
ancestor exclusions, code-first/additional-code instructions and seventh-character
rules. Category headers cannot be selected. Token-index retrieval is a bounded
lexical shortlist, not an ontology matcher or clinical confidence score. Abbreviations
and rare synonyms may miss candidates; measure those misses before introducing
embeddings or curated aliases.

This is a fixed FY2027 **research normalization** of historical articles, not
retrospective claims coding. Leaf membership alone does not establish correct
sequencing, admissibility for a particular encounter, or clinical equivalence.
The [official coding guidelines](https://www.cms.gov/files/document/fy-2027-icd-10-cm-coding-guidelines.pdf)
and source release notices remain applicable. No SNOMED CT, LOINC, RxNorm, or
ICD-10-PCS release is bundled; drugs and numeric observations therefore remain
text concepts unless a separate licensed, versioned catalog is supplied.

## Deterministic clinical mapping

Each resolved human patient gets a separate collection Bundle and synthetic
name/MRN, with UUIDs bound to article, source patient identity, resource type and
fact content. Patient links, source DocumentReferences and compiler-device
Provenance are deterministic. No clinician, institution, encounter, birth date or
calendar measurement date is invented. Negation, uncertainty, medication plans,
unknown statuses and relative times remain explicit.

Individually attributed, included measurement-series points now become
Observations. Their literal cells, patient ownership evidence and deterministic
numeric parses are revalidated against the source. Values preserve comparator
and stated unit without inferred UCUM coding or conversion; precision that cannot
round-trip through JSON numbers remains literal text. Relative time is retained
as an extension. Shared/aggregate/uncertain ownership, medication-dose series and
unvalidated supplemental cells are deferred with reasons. The complete source
extraction remains embedded even when a fact is deferred. The series export also
retains ownership-only cells that older unified exports omitted.

## Validation and remaining gates

Every Bundle receives the pinned HL7 R4 JSON Schema plus local patient-reference,
choice and status checks. Custom provenance extensions have packaged
StructureDefinitions under `rules/fhir-extensions`; the official validator is
given these definitions through `-ig`. Without a JAR, official validation is
reported as **not run**. With `validator_jar=...`, official failures quarantine the
Bundle and retain the OperationOutcome. The JAR hash, command and logs are saved.
The default `-tx n/a` disables remote terminology validation; this is explicitly
reported and is not a terminology-conformance pass.

For an existing frozen packet, a separate CPU validation export can be produced
with `op2 fhir export --work PACKET --output NEW_EXPORT --validator-jar PATH
--require-official`. Add `--allow-unreviewed-coding` only for the explicitly
experimental coded export. Java uses a 2 GiB heap; the validator may fetch
definition packages on first use. Use a CPU job rather than extending GPU residency
for this optional validation. Do not overwrite old exports.

The [HL7 validation guidance](https://www.hl7.org/fhir/R4/validation.html) distinguishes
schema checks from profiles, invariants, terminology and clinical review. These
are separate gates here. See `docs/reports/FHIR_VALIDATION_20261030.md` for the
actual local official-validator smoke results. Next review a stratified sample
of selected codes, abstentions, patient links and source-value matches, then
evaluate appropriate profiles and terminology validation before release.
