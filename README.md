# OpenPatients 2 — v0.6.0

**Evidence-grounded clinical extraction, reproducible inference experiments, and an initial cohort-search database.**

A uv Python project for turning licensed PMC full articles and Open-Patients source cases into evidence-backed patient records. It supports patient discovery, species, 14 clinical sections, cited summaries, relative timelines, actual figure inspection, terminology grounding and cohort queries through OpenAI-compatible endpoints. Serving profiles include experimental Muse Glimmer and Gemma 4 31B layouts for eight B200 GPUs.

The [figure attribution and cross-article linkage experiment](reports/FIGURE_ATTRIBUTION_AND_LINKAGE.md) tests panel ownership, specimen context, citation-guided retrieval and explicit follow-up identity links. The article runner now enables a separate per-figure attribution stage by default, and citation plans preserve article-local cases. Synthetic patient composition remains a separate future step.

**A live, budgeted PMC/API pilot is now included**, alongside synthetic CPU/mock demonstrations. No B200 throughput or physician-adjudicated accuracy is claimed. No model weights or ontology releases were downloaded. A source case is not necessarily a unique real patient, and retrieved cohorts are not population-representative.

Read the [medical-fidelity comparison](reports/MEDICAL_FIDELITY.md), [earlier implementation pilot](reports/ARTICLE_PILOT.md), [article workflow and commands](docs/ARTICLE_PIPELINE.md), and [Synthetic Hospital implementation review](docs/SYNTHETIC_HOSPITAL_REVIEW.md). The new comparison tests regular `meta/muse-spark-1.2`, Glimmer, Inkling and Gemma against source-checked patient facts, separately measuring extraction and validation losses. Regular Spark works under the existing account settings; the earlier Contributor route restriction does not apply to these results. The earlier pilot also tested prose normalization, schema requests, real figure pixels and Cohere Command A+ (frequent endpoint generation/transport failures). Three conflicting article licenses were quarantined, and the release examples were rebuilt from audited sources.

The later [span, gating and targeted-repair experiments](reports/REFINEMENT_EXPERIMENTS.md) compare independently combined extraction and repair stages, including source audits of medical meaning and costs. Their original results remain frozen. Version 0.6 integrates bounded item repair, audited citation-format recovery, official PMC TXT reading surfaces, and an optional native Firecrawl PDF parser. See [the integration notes](docs/EXTRACTION_INTEGRATION.md) for defaults, commands and limitations. Chunking, span-first extraction and hierarchical reducers remain experimental. Passing field checks or validation is not proof of medical accuracy.

The [article length and token accounting report](reports/ARTICLE_TOKEN_ACCOUNTING.md) separates measured article-only lengths from repeated per-article processing costs, with mean, median and P95 for the small pilot samples.

## Start with the local, no-model demonstration

```bash
uv sync
uv run pytest -q
uv run op2 demo --output runs/demo
```

The demo exercises all 14 task schemas through a mock streaming HTTP endpoint, runs validation and evidence-offset resolution, saves resumable task state, builds a SQLite index, and executes a tumor-linked cohort query. It deliberately leaves token-throughput and clinical-accuracy claims unset. It is an executable integration example, **not a model evaluation**.

**Dependency lock:** `uv.lock` was resolved and the environment installed with `uv sync --python 3.14`. Use `uv sync --locked` for the recorded dependencies; the project supports Python 3.11–3.14.

## Source rows and article figures

Every row retains the complete original JSON, exact description, dataset revision and row index, source IDs, and PMID/PMCID/DOI links when available. **A bare `pmc-{number}-{case}` is not assumed to identify a PMID or PMCID.** Explicit fields/URLs or a reviewed source-row crosswalk establish the article. Ambiguous rows remain intact and unresolved instead of being linked to an unrelated paper.

A CPU-only stage discovers article figures using the PMC ID Converter, current PMC Cloud metadata and JATS XML. It saves image URLs, captions, labels, rights flags, version/hash provenance and explicit availability statuses. **It never downloads images, videos, PDFs or supplementary archives.** Figures remain article-level candidates with unverified patient attribution; no image-derived patient facts are invented. All original rows remain present even when an article has no mapping or discovery fails.

```bash
uv run op2 sources-demo --output runs/sources-demo  # local/mock, no network
```

Read [Source lineage and figures](docs/SOURCES_AND_FIGURES.md) for the implementation and CPU pilot. [PubMed indexing metadata](docs/PUBMED_METADATA_RESEARCH.md) is a separate investigation—not an implemented MeSH/bibliographic ingestion stage.

## Standard terminology grounding and knowledge graph

After evidence-backed clinical extraction, `code-link` retrieves candidates from
local version-pinned **ICD-10-CM, SNOMED CT and LOINC** files. Suitable unique exact
matches can be indexed; ambiguous mappings can use an OpenAI-compatible selector
and explicit human review. Returned selector reasoning is saved alongside proposals.
Codes never overwrite source facts, turn absence into presence, or merge people.

```bash
# Complete local example: no licensed vocabulary, model or GPU needed.
uv run op2 ontology-demo --output runs/ontology-demo

# After obtaining the releases and editing paths/license/version settings on CPU:
uv run op2 vocab-build --config configs/terminology/catalog.yaml --output data/terminology.sqlite
uv run op2 code-link --input runs/k2/patients.jsonl --output data/patients-coded.jsonl \
  --catalog data/terminology.sqlite --config configs/terminology/linking.yaml
uv run op2 index --input data/patients-coded.jsonl --catalog data/terminology.sqlite \
  --output data/clinical.sqlite
uv run op2 graph --input data/patients-coded.jsonl --catalog data/terminology.sqlite \
  --output data/clinical-graph
```

The graph retains source cases, facts, exact supporting text, code/version/mapping
provenance, imported hierarchies and explicit tumor links. Concept-descendant queries
use catalog relationships rather than text/code-prefix guesses. Default code cohorts
exclude unreviewed model proposals, broader approximations and unrelated/negative
subjects unless the query explicitly asks for those subjects/assertions.

The new article workflow implements case localization, patient summaries, temporal graphs and a separate pixel-inspection stage. EHR seeds preserve observed evidence and image descriptions; a patient simulator, patient merging and FHIR generation/server remain unimplemented. See the [current article workflow](docs/ARTICLE_PIPELINE.md) and [terminology guide](docs/TERMINOLOGY_AND_KNOWLEDGE_GRAPH.md).

The [HiPerGator K2 benchmark](docs/HIPERGATOR_K2.md) packages the exact previous medical-fidelity articles, prompts and factual checks for the four IFM checkpoints. Run `bash scripts/hpg_benchmark.sh --work-dir /blue/cai5724/wkieffer/op2-k2-runs/run-01` on the cluster: CPU jobs download one pinned model, four B200s evaluate 375B or two evaluate smaller models, and CPU cleanup deletes its weights before the next download. The defaults use `cai5724` account/QoS; text-only outputs explicitly mark vision unavailable.

The [Glimmer B200 gauntlet](docs/HIPERGATOR_GLIMMER.md) reuses those frozen inputs for FP8, NVIDIA NVFP4 and Unsloth NF4; the full BF16 verifier is excluded. It tests low/medium/high/xhigh reasoning, single-node DP/TP layouts, concurrency, experimental DCP, DFlash and DSpark. CPU jobs download and delete one verifier and its assistants at a time; GPU stages request eight B200s. Run `bash scripts/hpg_glimmer.sh --work-dir "$PWD/../op2-glimmer-runs/run-$(date +%Y%m%d-%H%M%S)"` from the updated cluster checkout. Software checks are local; B200 results are produced by that campaign.

## Within-model reasoning, trace capture and empty cases

The project includes **paired reasoning-level studies within a single pinned model**, saved returned reasoning alongside every task output, and review-gated distillation exports. Clinical schema **2.1.0** distinguishes absent documentation from explicitly negative or explicitly unknown findings. Every one of the 14 task prompts includes clinical decision examples and valid empty-output guidance.

```bash
# Fully local software example; no model, network endpoint, or GPU required.
uv run op2 reasoning-demo --output runs/reasoning-demo

# After the CPU model/dataset/pinned-config setup below:
uv run op2 reasoning-prepare --config configs/reasoning/k2.yaml \
  --tokenizer models/k2 --workers 16
# Inside a GPU allocation, using ONE fixed topology for all settings:
uv run --no-sync op2 reasoning-run --study runs/reasoning-k2 \
  --serving configs/serving/k2-vllm-tp8.yaml
# After releasing GPUs:
uv run op2 reasoning-analyze --study runs/reasoning-k2
```

The K2 study compares low/medium/high on the same sample with three matched seeds; Motif's supplied study measures default-mode repeatability only because no verified selectable-effort control is assumed. The analysis reports exact/partial fact agreement, evidence-span agreement, both-empty rates, source-cluster uncertainty and paired quality/cost tests. **Agreement and nonsignificant differences are not proof of clinical equivalence.** Independently reviewed gold can be supplied separately.

See [Reasoning studies and distillation](docs/REASONING_STUDY.md), [Clinical schema review](docs/SCHEMA_REVIEW_V2_1.md), [Changelog](CHANGELOG.md), and [local verification](reports/LOCAL_VERIFICATION.md).

## What is implemented

- Fourteen clinically focused Pydantic/JSON Schema tasks with substantive prompts, explicit subject/assertion/time semantics, literal evidence and a first-class oncology model.
- Streaming async extraction with stable case-to-endpoint affinity, useful first-task warming, bounded fanout, HTTP retry, bounded JSON/evidence repair, truncation detection and resumable per-task state.
- CPU dataset/model download and pinned manifests; exact-tokenizer prompt profiling; fail-closed context guards; no source truncation.
- Benchmark/campaign commands with fresh run state, schema warmup, logical versus uncached input rates, output/reasoning accounting, latency percentiles, errors, retries, raw engine metrics and GPU telemetry when available.
- Source/structural revalidation, evidence spans, contradiction flags, catalog-backed terminology mapping/review, normalized SQLite domain tables, hierarchy queries and a safe parameterized cohort DSL with tumor-specific joins.
- Local vocabulary import, optional candidate-only model proposals with returned reasoning, and provenance-linked JSONL graph export.
- Fact-level gold evaluation with one-to-one matching, correct handling of partial labels, forbidden-pattern checks and case-level bootstrap intervals.
- Version-specific serving command generation, process lifecycle management, CPU/GPU Slurm templates, model-specific experiment profiles and CPU CI tests.

## Clinical sections

`case_context`, `demographics`, `conditions`, `symptoms_function`, `medications`, `allergies`, `procedures_devices`, `observations`, `oncology`, `family_genetics`, `reproductive_perinatal`, `social_exposures`, `outcomes`, `care_plans`.

All task-specific instructions follow the same complete source note, maximizing shared token-prefix reuse. Machine schemas are supplied separately from the compact semantic field guide. Unknown is not false; family disease is not patient disease; ordered is not administered; grade is not stage; a negative test is not automatically a negated diagnosis. Read [Clinical design](docs/CLINICAL_DESIGN.md) for the actual distinctions and limitations.

## Actual model setup

Run dependency resolution, model/dataset acquisition, SIF acquisition and tokenization on CPU **before** reserving GPUs:

```bash
bash scripts/bootstrap.sh
uv run op2 download-dataset --output data/prepared.jsonl
export NCBI_EMAIL='your-real-maintainer-email@ufl.edu'
uv run op2 enrich-sources --input data/prepared.jsonl \
  --output data/enriched.jsonl --config configs/sources/pmc.yaml
uv run op2 download-model \
  --repo IFM/K2-Horizon-375B-A23B-NVFP4 --output models/k2
uv run op2 pin-config --config configs/pipelines/k2.yaml \
  --snapshot models/k2/op2_snapshot.json --output configs/pipelines/k2-pinned.yaml
uv run op2 profile --config configs/pipelines/k2-pinned.yaml \
  --tokenizer models/k2 --output data/k2.tokens.jsonl --workers 16
bash scripts/pull_container_cpu.sh k2
```

Inside a valid GPU allocation:

```bash
uv run --no-sync op2 launch-run \
  --config configs/pipelines/k2-pinned.yaml \
  --serving configs/serving/k2-vllm-tp8.yaml \
  --benchmark --limit 128
```

For an already running endpoint:

```bash
uv run benchmark --config configs/pipelines/k2-pinned.yaml --limit 128
uv run op2 run --config configs/pipelines/k2-pinned.yaml
```

For an automated topology/concurrency experiment:

```bash
uv run --no-sync op2 campaign --config configs/campaign-k2.yaml
```

Do not run a whole campaign before smoke-testing its constituent serving profiles. The supplied campaign grids are examples, not a claim that every candidate is supported or that an entire grid will fit one QOS time limit. Use a fixed, appropriate source sample, not merely the first few corpus rows, for a comparative experiment. See [Deployment](docs/DEPLOYMENT.md) for Motif, Slurm, driver compatibility, GPU accounting, cleanup and local cache/storage considerations.

## Model-agnostic configuration

Copy `configs/pipelines/generic.yaml` and supply the endpoint(s), served alias, actual model ID/revision, context cap and token profile. API secrets come from an environment variable. The article experiment configs use `prompt_json` (no grammar enforcement), while older pipeline configs default to `json_schema`. `vllm_structured_outputs` and `json_object` are also available. All modes undergo application-level parsing and evidence validation. Model-specific request fields are isolated in `api.extra_body`.

A K2 example uses `chat_template_kwargs.reasoning_effort: low`, with quality comparison against higher budgets required. Motif's config does not invent a low-reasoning switch. The inference client never installs a serving engine or assumes every OpenAI-compatible endpoint has the same parser/grammar/tokenizer behavior.

A single URL in front of internal DP load balancing is **not** a cache-affinity guarantee. The external-rank serving profiles return explicit endpoints and the launcher fills them into the client. Each patient's first missing task finishes before its other tasks fan out. A resumed result on disk does not imply a warm GPU cache; the first remaining task becomes the useful warming request.

## Build and query the research index

```bash
uv run op2 export --run runs/k2
uv run op2 validate --input runs/k2/patients.jsonl --output runs/k2/validation.json
uv run op2 index --input runs/k2/patients.jsonl --output data/clinical-cases.sqlite
uv run op2 cohort --database data/clinical-cases.sqlite \
  --config configs/cohorts/egfr_lung_cancer.yaml --output runs/egfr-cohort.jsonl
```

The example cohort asks for documented male cases, age 40–75 **years at presentation**, active stage-IV lung cancer, and a positive EGFR result linked to that same tumor. Every returned membership includes the matching facts and source spans, plus the full original row, article links and discovered figure candidates. Default filters exclude incomplete/limited/conflicted cases, nonclinical contexts and synthetic/educational source types. Unknown fields do not satisfy positive criteria or pretend to be clinical negatives.

Raw strings are preserved. Real controlled-vocabulary mappings must be reviewed and versioned. `examples/terms.local-demo.csv` illustrates the mapping format using explicitly local demonstration IDs; it is not a medical vocabulary. The current SQL DSL is an initial deterministic research index, not an unrestricted natural-language clinical query engine or a complete temporal eligibility system.

## Output layout

```text
runs/<run>/
  state.sqlite                    # resumable tasks, attempts, input versions
  patients.jsonl                  # original row + source/figure lineage + sections + reasoning
  extractions.jsonl               # each task: extraction, reasoning_text, exact request/response
  attempts.jsonl                  # all attempts, including invalid/truncated/repaired responses
  manifest-<run-id>.json           # exact config/prompt/task signatures
  report.json                     # throughput/error/latency definitions and counts
  server-metrics-*.json            # raw optional engine metrics
  server-logs/                     # deployment commands and real startup timing
```

Returned reasoning is saved by default, including both `reasoning` and `reasoning_content` fields. Missing reasoning stays `null`; opaque reasoning is preserved without inventing readable text. Audit traces are excluded from clinical index payloads. For distillation, `op2 review-template` and `op2 distill` require explicit review by default; answer-only is the default target and `--include-reasoning` requires separate trace approval.

The K2/Motif pipeline templates set `export_after_run: false`: after the GPU job exits, run `op2 export --run <directory>` on CPU. The generic config/demo can export immediately for convenience.

Benchmark runs use fresh `bench-<id>` subdirectories; ordinary runs reuse the configured state DB. One writer owns each DB. Source hashes and model/prompt/schema/generation signatures prevent reuse of stale extraction results. Failed tasks remain failed, not empty successes.

## Evaluate quality separately from JSON validity

```bash
uv run op2 evaluate --predictions runs/k2/patients.jsonl \
  --gold your_adjudicated_gold.jsonl --output runs/k2/gold-evaluation.json
```

Exact evidence matching catches fabricated quotes, but does not prove that the quote supports the diagnosis or that the model extracted everything. Use blinded clinical review and downstream cohort-eligibility checks before relying on the database. Missing gold labels are not automatically false positives: partial annotations report recall but not unsupported precision/F1 claims.

The [benchmarking and annotation protocol](docs/BENCHMARKING_AND_ANNOTATION.md) explains the metrics, proposed experiment order, gold format, critical error classes and statistical limitations.

## Research conclusions

Use upstream vLLM **0.30.0** for the K2 baseline and **Motif's 0.26.0-motif3 fork** for Motif. Motif is worth a controlled trial because of its smaller active parameter count, compressed attention, built-in MTP and documented two-B200 deployment—not because there is a verified B200 clinical TPS comparison. SGLang's published K2 cookbook validates BF16/H200, so its B200/NVFP4 profile remains a challenger requiring real validation. A bespoke extraction runtime was chosen for explicit cache affinity and provenance; NVIDIA Data Designer remains a useful option for separate synthetic/adversarial test generation.

**The earlier six-figure TPS forecasts were not measured and are not guaranteed here.** Profile the actual corpus and benchmark the actual task first. Read [Research and decisions](docs/RESEARCH_AND_DECISIONS.md) for the dated sources, model intelligence comparison, speculation caveats and reasons for these choices.

## Safety, licenses and scope

Code is MIT. Dataset/model/terminology/engine licenses remain separate. The Open-Patients card specifies CC-BY-SA-4.0, while its upstream PMC-Patients component advertises CC-BY-NC-SA-4.0. Article and figure terms may differ again. All declarations are preserved separately; no blanket training, redistribution or commercial permission is inferred. Do not upload private clinical data to unauthorized endpoints. Public case text can still contain sensitive information, and this project is not a de-identification or HIPAA-compliance certification.

This release is not a trained clinical model, a finished clinical product, a benchmark result on your GPUs, or an implementation of every possible cohort query. It is a tested starting codebase with explicit deployment experiments and clinical data semantics, designed so those validations can be performed rather than assumed.
