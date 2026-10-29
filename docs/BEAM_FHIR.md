# Beam optimization and literature-derived FHIR records

See [the October 29 repair assessment](reports/BEAM_REPAIR_20261029.md) before a full run.
The next command is `bash scripts/run_dense_beam.sh diagnostic`: twelve fixed training
articles, including all known long-document/series failures, singleton-cohort
discovery, representative multi-patient cases and empty controls. It runs two TP4
replicas, with a 12-hour worker limit and a four-hour article limit. No test data.

Repair policy `scope-routing-bounded-documents/2` is frozen in the arm manifest.
`diagnostic` and `repair-pilot` enable it; `long` now enables it by default.
For `long`, the original pipeline remains available with `DENSE_BEAM_REPAIR=0`;
`diagnostic` and `repair-pilot` always enable the repair policy.
This is a new experiment, not proof of better accuracy: first inspect the diagnostic,
then repeat `repair-pilot` on the same 48 training articles. Only after that should
we schedule `long` or compare speculative decoding settings on the revised pipeline.

Promotion checks: all twelve attempts finish; the genomic roster and measurement
coverage receipts show no failed leaves; singleton-case and empty-control decisions
match a source review; out-of-batch edges no longer consume targeted repair loops;
no reference patient is lost merely to raise validity; and manual event/relation
checks agree with the literal source. Compare matched clinical recall, timeline
quality, nonempty-valid documents/GPU-hour and per-task tokens on the 48-article
rerun. A cheaper empty output or higher schema-valid count is not an improvement.
The diagnostic deliberately overrepresents failures and is not a population metric.

## Run Beam first

The October 24 campaign used **Reflection Beam-1.0-501B-A23B-NVFP4** with
DFlash2. It was a different model from the NVIDIA Nemotron NVFP4 arm. NVFP4
describes the numeric format, not the model family.

Beam completed 421 attempts, with predictions for 415 articles, but only 43
fully valid extractions. Completed attempts therefore did not mean a clean
dataset. Long timeline prompts overflowed context, 3,378 calls hit their output
limit, and relation extraction was especially weak. Draft-token acceptance was
about 10.8%; that alone cannot tell us whether speculation sped up the pipeline.

The new campaign uses the same pinned target/draft snapshots, container digest,
native Beam template, Harmony reasoning parser, stop tokens, temperature 0.7,
top-p 0.99 and maximum reasoning effort. The official recipe recommends TP4 for
NVFP4 on Blackwell and supports two replicas on eight GPUs. See the
[vLLM Beam recipe](https://recipes.vllm.ai/reflection/Beam-1.0-501B-A23B).

| Run mode | Four-GPU worker A | Four-GPU worker B | Articles | Worker limit |
|---|---|---|---|---|
| `diagnostic` | Beam + DFlash2, replica 0 | Same configuration, replica 1 | Twelve fixed training articles, distinct claims; repair v2 | 12 hours |
| `repair-pilot` | Beam + DFlash2, replica 0 | Same configuration, replica 1 | Same 48 diverse training articles, distinct claims; repair v2 | 24 hours |
| `pilot` | Beam + DFlash2, 7 draft tokens | Same Beam, speculation off | Same 48 diverse training articles per arm | 24 hours |
| `short-draft` | Beam + DFlash2, 3 draft tokens | Same Beam, speculation off | Same 48 diverse training articles per arm | 24 hours |
| `long` | Selected configuration, replica 0 | Same configuration, replica 1 | All train + validation, distinct article claims | 72 hours |

Each worker requests 16 CPUs and 96 GiB host RAM. Total: eight B200s, 32 CPUs,
192 GiB host RAM, below the group's 250 GiB limit. Two replicas of one configuration
share one downloaded target/drafter. Ablation configurations keep isolated runtimes but reuse completed sibling
checkpoints and the identical container through hard links. Only the speculative
arm loads the drafter. Mutable caches are never shared; cleanup unlinks each
runtime after the worker barrier, preserving other links. A resumed download
privatizes linked files before any update. Free-space preflight conservatively requires
418 GiB for `long`, or 708 GiB for the two-arm pilots, including 128 GiB reserve.
CPU downloads are serialized and overlap an eligible already-downloaded worker;
CPU cleanup follows the GPU wave. Initial staging and tail completion can leave
GPUs idle. Each TP4 allocation is single-node; Slurm may place the two workers
on different nodes. No cross-node tensor parallelism is requested.

From an updated, committed HiPerGator checkout:

```bash
(
  set -a
  source .env
  set +a
  DENSE_ACCOUNT=cai5724 DENSE_QOS=cai5724 bash scripts/run_dense_beam.sh diagnostic
)
```

Use `DENSE_BEAM_PILOT_ARTICLES=2 DENSE_HOURS=4 bash scripts/run_dense_beam.sh repair-pilot`
for a smaller hardware smoke; `diagnostic` always selects its twelve fixed cases.
The launcher must see committed code: untracked or uncommitted modules
will not be present in its frozen snapshot. It refuses a new launch while the
previous campaign is active. Monitor with:

```bash
squeue -u "$USER" -o "%.18i %.12P %.65j %.10T %.12M %R"
bash scripts/run_dense_nemotron_super.sh status
```

For a paired speculation comparison using the repaired pipeline, run
`DENSE_BEAM_REPAIR=1 bash scripts/run_dense_beam.sh pilot` or
`DENSE_BEAM_REPAIR=1 bash scripts/run_dense_beam.sh short-draft`. Without that
override, these two ablation modes retain the original pipeline as a control.

After examining matched quality, nonempty pipeline-valid document throughput, tokens per
article and `beam_speculation_comparison` in `summary/comparison.json`, select
the winning profile explicitly. For example, **if speculation off wins**:

```bash
DENSE_BEAM_PROFILE=beam-nvfp4-no-spec bash scripts/run_dense_beam.sh long
```

Otherwise select `beam-nvfp4-dflash2` or test `short-draft` first. The old
`run_dense_super_long.sh` now delegates to Beam-only `long`. A time limit is not
a completion guarantee. `resume` retains frozen code, settings and completed
article claims; use a fresh campaign to test code fixes. Do not choose settings
on test data or count incomplete/failed outputs as zero-cost successes.

## Context, accuracy and throughput changes

- Beam uses a 131,072-token context cap instead of 65,536. Server startup,
  exact `/tokenize` counts and reasoning-capture probes remain mandatory.
- Timeline event calls cover four source segments at a time. Relation calls
  cover two origin events and at most eight target events, including cross-batch
  candidates. Each event pair is considered once, allowing either edge direction,
  to avoid redundant reverse-pair calls. The repair pilot uses whole evidence/focus segments, identity evidence and
  adjacent context. No segment is shortened. Every segment remains an event
  focus; included segment IDs and scope are recorded per call.
- Context/length failures split non-singleton batches. The failed parent attempt
  and its returned reasoning remain in the ledger, while successful child calls
  replace its partial payload. An impossible singleton remains an explicit
  failure. Scoped context may miss a remote relation cue: candidate coverage is
  not proof of semantic coverage. Exhaustive target batching costs more calls,
  so measure the new workload before scheduling the full dataset.
- Repair v2 uses a relation-only prompt, without inherited instructions to return
  a complete timeline. Known pairs belonging to another batch are retained in
  `scope_routing` audits, not repeatedly repaired or accepted as clinical facts.
  Unknown endpoints, self-links, bad quotes and temporal contradictions still fail.
  Every unordered pair retains its own batch; emitted extra edges do not bypass
  the validation of that batch. Length failures split immediately instead of
  spending two large retries first.
- Oversized roster requests split into whole-source-segment windows. Every segment
  must be attempted and every window must pass before global discovery succeeds.
  Identity reconciliation covers every candidate exactly once, preserves literal
  identity evidence, and blocks on unresolved identities. The candidate mapping is
  model-generated, not a reviewed identity guarantee. A whole segment that cannot
  fit, or an oversized reconciliation request, remains an explicit failure.
  For these windowed articles, downstream patient requests retain assigned,
  unresolved, identity/adjacent and abstract blocks. Background stays in the full
  archived source and omission audit; semantic completeness is not asserted.
- Measurement extraction batches whole rows with original headers/legends, counts
  serving tokens, and splits further on length/context failures. Values, units and
  times are still checked against literal cells. Successful batches survive a failed
  neighbour, but the EHR stage stays failed/partial and its coverage receipt records
  the missing rows. No synthetic values or dates are introduced.
- Reports now separate empty prediction artifacts, nonempty patient articles,
  schema validity, nonempty pipeline validity (including EHR checks), and empty
  roster disagreements with references. Legacy JSON counters remain for compatibility;
  the displayed useful-valid rate uses nonempty records. Clinical accuracy remains
  unreviewed. Per-task input/output/reasoning totals and length/routing counts expose
  the cost of timeline repair instead of burying it in article averages.
- All requests retain endpoint-returned reasoning, raw response fields, messages,
  usage, task identity and model revision. Fact/citation/trace links remain in
  `summary/fact-reasoning.jsonl`. Reasoning is unverified model output, not source
  evidence or an automatically accepted distillation target.
- `summary/article-timing.csv` already contains each article's total input,
  output and reasoning tokens. Aggregate reports now include mean input, output,
  answer, reasoning and call counts per completed article. These include repeated
  prompts and repairs, not just unique source tokens. Reasoning is a subset of
  output. Missing provider usage remains unknown, never zero.
- The paired speculation report compares common completed article IDs, with
  baseline seconds / candidate seconds (>1 means faster). Check matched F1,
  failures, output lengths and hardware telemetry alongside it. This is pipeline
  service time, not an isolated decoding benchmark. Counters are sampled around
  real article work, not random prompts. No automatic winner is promoted.

The local tests exercise planning, requests, parsing and fixtures. They do not
establish B200 performance or validate a new GPU memory limit. The pilot is the
required empirical check. Keep TP4 fixed for the first comparison; attention-DP /
MegaMoE, reduced reasoning effort and concurrency sweeps would otherwise confound
the effect of speculation and the extraction fixes.

## Extraction → terminology → FHIR

The second-stage implementation targets the **HL7® FHIR® R4 standard (4.0.1)**.
The source extraction schema stays unchanged. An LLM can select terminology
candidates, but Python constructs the FHIR JSON and all identifiers/references.
This avoids repeated model-generated JSON repairs and model-invented foreign keys.

```text
Beam extraction + citations + returned reasoning
    → frozen unified article rows + matching primary source packets
    → versioned local terminology candidates → select or abstain → review
    → deterministic resource compiler
    → schema / reference checks → official HL7 validation → clinical review
```

Each source patient gets a separate `Bundle(type=collection)`; an article is not
assumed to describe one patient. UUIDs derive from article, local patient identity
evidence, resource kind and fact content. Fact ordering does not change IDs.
Changed identity evidence creates a new identity namespace. These IDs do not
assert that cases in different articles are the same real person.

The compiler adds a conspicuously synthetic name and MRN. It does not infer a
date of birth from publication date and age, convert documented sex to an
administrative gender, invent visits, or invent a physician who performed care.
A software `Device` identifies the compiler in `Provenance`; a `DocumentReference`
preserves the complete original patient extraction and another preserves the
primary source packet. Original license and provenance accompany the packet.
This is literature-derived data with synthetic identities, not a guarantee of
de-identification or a fully synthetic clinical history.

| Source content | Initial resource mapping | Conservative boundary |
|---|---|---|
| Diagnoses | `Condition` | Preserve certainty/refutation; do not infer active disease |
| Labs, vitals, symptoms, demographics and selected other findings | `Observation` | Preserve literal units/values; unknown result workflow stays unknown |
| Drug use | `MedicationStatement` | A mention of administration is not a complete administration record |
| Drug orders/plans | `MedicationRequest` | Preserve order vs plan intent |
| Procedures | `Procedure`; planned/ordered work → `ServiceRequest` | Never turn a plan into a completed procedure |
| Specific-substance allergies | `AllergyIntolerance` | Broad NKDA statements and non-allergic adverse effects are deferred |
| Explicit family conditions | `FamilyMemberHistory` | Keep the relative separate from the index patient |
| Care plans | `CarePlan` | Preserve stated action; no invented encounter or clinician |
| Other content | Original extraction `DocumentReference` + deferred mapping entry | Nothing is silently discarded |

This first compiler defers device lifecycles, tumor staging/treatment graphs,
supplemental clinical rows, measurement-series expansion, calendar/encounter
reconstruction and unsupported nonaffirmative statements. Nonhuman or unresolved
species patients are explicitly skipped. These are visible coverage gaps to
extend with adjudicated examples, not successful mappings. It does not claim
US Core/mCODE conformance or emit a transaction to a live EHR server.

## Use the second stage

No model/API is required for an uncoded structural export. For a downloaded or
cluster campaign, set `WORK` to the extracted campaign root and run:

```bash
uv run op2 fhir prepare \
  --input "$WORK/arms/beam-nvfp4-dflash2-text/summary/predictions.jsonl" \
  --sources "$WORK" --output runs/beam-fhir-work
uv run op2 fhir export --work runs/beam-fhir-work --output runs/beam-fhir-export
```

`prepare` checks source article identity/hash and snapshots the input, sources,
catalog and requests. `export` emits one patient bundle plus mapping/validation
audits, terminology decisions and a report. New directories are required so a
correction cannot silently overwrite an earlier dataset. No code is invented
when a terminology catalog has not been supplied: concepts retain source text.

Run model-assisted terminology selection in a separate serving session after
the timed extraction benchmark. Sharing its live endpoint during the benchmark
would contaminate throughput and speculative-decoding counters.

To enable coding, pass `--catalog catalog.json` to **prepare**. The format is:

```json
{
  "format": "openpatients2-terminology-catalog/1",
  "source": "authoritative release location and import provenance",
  "license": "applicable license",
  "concepts": [{
    "system": "http://hl7.org/fhir/sid/icd-10-cm",
    "version": "pinned release",
    "code": "code imported from that release",
    "display": "official preferred label",
    "aliases": [],
    "tasks": ["conditions"],
    "context": {"instructions": "retain any exclusions and coding rules"}
  }]
}
```

Supported canonical systems include ICD-10-CM, WHO ICD-10, SNOMED CT, LOINC and
RxNorm. ICD-10-CM and WHO ICD-10 are distinct; ICD-10-PCS is not accepted here.
Use disease classifications for diagnoses, LOINC with assay/specimen axes for
measurements, and a drug vocabulary for drugs. A lexical candidate retrieval
score is not a probability or proof of equivalence. This bounded prototype
retriever scans a local catalog; production-scale SNOMED/LOINC retrieval should
use an indexed terminology service with the same candidate/decision contract.

For an optional local model selection pass (a running Beam server or SSH-forwarded
port with its matching frozen arm manifest):

```bash
uv run op2 fhir select --work runs/beam-fhir-work \
  --endpoint http://127.0.0.1:8000/v1 \
  --manifest "$WORK/arms/beam-nvfp4-dflash2-text/manifest.json" \
  --max-requests 128 --concurrency 4
```

Each fact has separate requests for the applicable coding systems, so approved
SNOMED and ICD classifications can coexist without conflating the vocabularies.
The model may select a supplied candidate ID or abstain. It cannot supply a new
code. The request hash binds its decision to this fact and catalog. Returned
reasoning/messages/token usage are saved under `coding-calls/`. An empty
candidate list makes no model call. Model decisions stay unreviewed; the default
compiler only attaches approved equivalent codings. Broader matches stay in the
audit. Review a decision JSON and record approval explicitly:

```bash
uv run op2 fhir approve --work runs/beam-fhir-work --request-id REQUEST_ID \
  --decision reviewed-decision.json --reviewer REVIEWER_ID
uv run op2 fhir export --work runs/beam-fhir-work --output runs/beam-fhir-reviewed
```

`--allow-unreviewed-coding` on export is an explicit research experiment; it is
recorded in the report and does not make the data release-ready. No terminology
release or credentials are bundled. Licensing/access and imported catalog
correctness must be established before a coded release.

## Validation and repair

Offline validation uses the unmodified, checksum-pinned official R4 JSON schema
plus reference, status, choice-field and selected cross-field checks. The schema
license/source/hash are in `rules/fhir-r4-schema-provenance.yaml`. These checks
are necessary but insufficient for FHIR conformance. HL7 describes the different
[validation levels](https://hl7.org/fhir/R4/validation.html).

Supply a local official HL7 validator JAR to require its additional checks:

```bash
uv run op2 fhir export --work runs/beam-fhir-work --output runs/beam-fhir-validated \
  --validator-jar /absolute/path/validator_cli.jar --require-official
```

The command records the JAR hash, command and OperationOutcome. Terminology
network validation is disabled (`-tx n/a`) by default; offline structural checks
do not validate an imported catalog. Definition/package downloads may still be
needed by the JAR. Configure licensed terminology and application profiles in the
release validation environment. JSON-schema failures and official validator
errors quarantine the affected bundle; the command returns nonzero. A malformed
source fact is deferred before compilation. No automated repair changes a
clinical value to satisfy a validator.

The refinement loop is: inspect the mapping/OperationOutcome issue → correct
the deterministic mapping, or re-extract a specific unsupported field against
the source → retain the original and decision history → build a new export →
validate again. Before distillation, select clinically reviewed fact/trace pairs,
split by article, exclude unresolved attribution/negation/timing/coding, and
evaluate the student on untouched sources. More reasoning tokens are not a label
of correctness. Release readiness remains false until those review gates are met.

HL7 and FHIR are registered trademarks of Health Level Seven International.
Use of the specification does not constitute endorsement by HL7.
