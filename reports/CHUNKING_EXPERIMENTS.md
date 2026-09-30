# Chunking experiment — 29 September 2026

Chunking improved structured fact delivery in this pilot. The best measured Spark configuration was the 750-word overlapping window strategy. For Glimmer, 1,200-word sections followed by one local repair pass worked best. Reconstructing facts from summaries lost information, and the citation-only hierarchical reducer sometimes removed correct facts.

This is an isolated experiment; production extraction, schemas, and prompts were not changed. The study increased key usage by **$4.8580**, leaving **$5.1942** of the original $30 allowance. It stayed below its $9 cap. The temporary credential file was deleted after the final accounting request.

## Results

The following are **retained checklist facts out of 68 after validation**, using the same lossless envelope reader for all extraction arms. They measure structured delivery on this small challenge set, not overall medical accuracy. Empty or failed source chunks can coexist with useful facts from other chunks.

| Model | Whole article | 1,200-word sections | 750-word overlapping windows | Sections + local repair |
|---|---:|---:|---:|---:|
| Muse Spark 1.2 | 64 | 65 | **68** | **68** |
| Muse Glimmer 30B | 11 | 24 | 56 | **60** |
| Inkling | 30 | 28 | 37 | **48** |
| Gemma 4 31B | 1 | 17 | **46** | 33 |

Glimmer's whole-article score was heavily affected by malformed output and validation failures under this joint schema-and-summary prompt. Its pre-validation counts were 26 for whole articles, 60 for sections, and 61 for windows. Spark's corresponding counts were 66, 68, and 68. Gemma had substantial timeout losses, so its result must not be read as pure medical reasoning accuracy. These fresh controls are not the same prompt as the earlier refinement benchmark.

| Model | Section hierarchy | Repaired section hierarchy | Re-extract from final summary |
|---|---:|---:|---:|
| Muse Spark 1.2 | 65 | 67 | 39 |
| Muse Glimmer 30B | 24 | 59 | 10 |
| Inkling | 2 | 30 | 3 |
| Gemma 4 31B | 1 | 30 | 7 |

The hierarchy refuses to present an incomplete source pass as complete. Spark and Glimmer had all eight source units available for these comparisons; Inkling had four complete section passes, rising to six after repair, and Gemma five, rising to seven. Therefore the low Inkling/Gemma hierarchy totals include missing prerequisite chunks, not only losses caused by merging. On the fully available Spark and Glimmer runs, summary re-extraction still lost substantial detail. Spark retained 46 checks before validation and 39 afterward through the summary bottleneck, versus 68/68 with direct window extraction.

Higher retention did not mean fewer medical errors. Inkling and Gemma sometimes moved findings between patients. A posthoc heading/citation audit flagged 25 delivered items in Gemma's window arm and four in Inkling's window arm as using another original patient's prose. Concrete examples include assigning Case 3's sodium 122 and ethylene glycol 16.54 mg/dL to Case 2. These source-binding flags are additional diagnostics, not an exhaustive precision estimate.

Spark's window arm triggered none of the 11 frozen forbidden patterns or four known semantic probes. Its repaired-section arm reached the same 68 checks but retained an unsupported active status for a historical fissure and a parasite-uterus observation assigned to the cat. The hierarchy corrected those problems but introduced other losses. No arm is established as error-free by this pilot.

## Context and compute

Spark provides a clean comparison because its source-reading calls returned token usage throughout:

| Strategy | Calls | Mean input/request | Median input/request | P95 input/request | Total input | Total output | Reported cost |
|---|---:|---:|---:|---:|---:|---:|---:|
| Whole article | 8 | 8,226 | 7,552 | 12,528 | 65,806 | 40,228 | $0.252 |
| Sections | 35 | 4,117 | 3,994 | 5,456 | 144,089 | 101,309 | $0.600 |
| Overlapping windows | 45 | 3,895 | 3,963 | 4,832 | 175,275 | 112,296 | $0.672 |

Windows cut mean input per request by about 53%, but increased total input 2.66× and total output 2.79×. Smaller contexts improved delivery while using more total inference. The repeated schemas/instructions are a substantial part of the input.

For the eight tested units, Glimmer sections + repair used 55 calls, 238,050 input tokens, 104,893 output tokens, and $0.218 in reported charges. Its windows without repair used 45 calls and $0.172. Spark sections + repair cost $0.674; adding the hierarchical pass increased that to $1.206 while reducing retained checks from 68 to 67. Costs include each arm's shared prerequisites once; they are not additive across arms.

Requests without returned token usage are excluded from per-request token distributions. Their unknown token/cost totals are not treated as real zeros; Gemma's reported totals are consequently incomplete. The separate account-level before/after receipt captures actual overall key usage. The budget ledger additionally retains conservative reserves for unknown charges, including four requests interrupted during the scheduling restart.

## What the medical audit found

All 30 applied edits from the primary citation-only hierarchy were reviewed against source text. Useful corrections included removing a nematode's uterus from the index cat's anatomy and changing unsupported disease activity to unknown. Harmful changes included dropping Case 3's documented NIHSS 7 and next-day negative ethylene glycol because their short quotations did not repeat the patient's name. One merge also collapsed positive PCR results from two distinct specimens into a single mixed-specimen record.

The 339 targeted repairs and 99 edits in the exploratory repaired hierarchy are retained with before/after values. Valid neighboring items were programmatically checked for preservation. No accepted local replacement changed numeric value, unit, dose fields, patient subject, assertion, or entity name; changes mainly concerned citations and time fields, with explicit quarantines recorded separately. This is not a claim that every repaired fact was independently medically adjudicated: local validation also promoted some incorrectly attributed facts into delivered output, particularly for Gemma. Schema and quotation checks are necessary but do not establish clinical truth.

A deterministic sample of 56 summary claims, two per responding model/strategy, received assistant source review: **44 supported, eight uncertain, four unsupported**. This was neither blind nor physician review, and it is not a corpus precision estimate. Some supported claims had incomplete citations or were only valid within an excerpt. Uncertain cases often involved the paper's conflicting blood-gas timing: tables label admission, while narratives describe later tests. Unsupported claims included another patient's findings and turning a local excerpt's missing numbers into an article-wide claim that numbers were absent.

Five known-failure reducer batches were then replayed with the same candidates/instructions plus original headings and surrounding source text, bounded to 1,500 added words. Actual additions were 323–1,483 words. This prevented seven of eight prior quarantines, including the valid NIHSS measurement and normal ABG findings. One restored item still contains a hedged interpretation that deserves uncertainty; the remaining heparin quarantine removed a weakly cited duplicate while other records retained the documented infusion. This is a targeted mechanism test, not independent validation of a new full pipeline.

## Recommendation

Use **direct, patient-specific fact extraction from source chunks**, keeping original evidence and time expressions. The strongest measured variants here are Spark with 750-word overlapping windows and Glimmer with 1,200-word sections plus local repair. Preserve table headers and use a shared patient registry, but also validate patient-to-segment bindings: the registry alone did not prevent cross-patient errors.

Keep summaries alongside the structured facts. Do not make them the sole input to later schema extraction. Before merging, deduplicate only when patient, concept, specimen, time, and clinical event agree. When the reducer lacks enough context, retrieve the original heading and neighboring source text before correcting or discarding a fact. Preserve conflicting timelines as unresolved instead of selecting one silently.

I would keep this experimental for now and evaluate the preferred variants on a larger held-out set before changing the production defaults. This pilot supports chunking and targeted source-context recovery, but does not show that one fixed chunk size or a hierarchy improves every metric for every model.

## What was compared

The matched source test uses five existing licensed PMC articles, six patients, and eight patient/domain extraction units. The domains are observations, medications, procedures/devices, and conditions. It deliberately includes dense multi-patient tables, a veterinary case, treatment changes, and relative timing. It is a failure-enriched convenience sample, not a representative corpus sample or a physician-adjudicated benchmark.

| Strategy | What the model receives |
|---|---|
| Whole | All cleaned article segments, a fixed patient registry, one domain schema, and a request for a cited domain summary |
| Sections | Complete source segments packed to approximately 1,200 words, preferring section boundaries after 600 words |
| Windows | Approximately 750 source words, with one preceding complete segment repeated when it is at most 150 words |
| Hierarchical | Section outputs; reconcile batches of at most eight facts using their citations, and combine cited summaries through a binary tree |
| Summary re-extraction | Extract the schema again from the final hierarchical summary and its retained quotations |
| Sections + repair | One repair pass for invalid fields using only their original source chunk; regenerate an unparseable section once |
| Repaired hierarchy | Apply the same hierarchical reducer to the repaired section outputs |

Paragraphs and table rows are never cut. Rows retain their original column and time-group labels. All source segments are covered, including discussion and captions. References had already been removed by the article preparation pipeline. The patient registry is fixed across strategies; this experiment does not measure patient discovery. Images are not sent to the models.

The source-reading strategies use the same prompt and generation settings, including a 16,384-token output cap. JSON is requested in the prompt, without API schema enforcement. An identical item-level schema and literal-citation gate is applied to each output. Summaries also need literal citations, but citation validity does not establish medical entailment.

The shortest article fits in one chunk under all three source strategies. Its prompts are identical, making differences there generation variability rather than a chunking effect. Smaller source chunks also mean more separate generations and shorter outputs; this study does not isolate an attention mechanism as the cause of any improvement.

## Evaluation and reproducibility

The frozen checklist contains 68 required typed-field matches and 11 forbidden patterns for the selected units. Four additional probes check patient-misattributed transfusion, dialysis incorrectly labeled as declined, an anti-Xa goal mislabeled as a result, and unsupported current activity of a historical anal fissure. Forty-four of the 68 positive checks come from two patient observation tasks in one poisoning article. Counts must not be interpreted as overall medical precision or recall.

Scores are retained both before and after item validation. Exact wording and field choice can cause a checklist miss even when the fact appears elsewhere in a response. A model's failure to produce a usable response is reported as unavailable delivery, not a medically false answer.

The source, primary runner, production code, prompts, and reference hashes were frozen before model calls. After observing Inkling's output, a separate deterministic adapter was added for unwrapped or split complete JSON objects. It does not repair medical values or salvage truncated responses. The same reader was applied to summary re-extraction, which also sometimes returned an unwrapped section. Original responses and strict-envelope results are preserved; normalized results feed the comparisons. The local repair arms are explicitly exploratory additions prompted by validation losses.

All successful neighboring facts are frozen during local repair. Failed facts, replacements, quarantines, and reducer operations are saved for review. Reducers keep unmentioned facts by default. Their input contains citations, not the full article: a mistaken patient assignment cannot necessarily be corrected if identifying context was omitted from those citations.

Token and cost totals count each prerequisite request once within a strategy. Strategies share map/tree calls, so summing strategy costs would overstate actual experiment spending. Input-token statistics include instructions, schemas, patient labels, and source text. Article-level token aggregates cover only the tested patient/domain units, not a complete fourteen-domain EHR pipeline.

Hosted API latency is not an eight-B200 throughput benchmark. Requests initially ran with five shared slots, then resumed with eight and cached completed calls. Interrupted/unknown charges retain conservative budget reservations. A $9 ceiling applies to this study, within the user's original $30 key allowance.

## Model availability

All six requested API routes were attempted. The contributor route returned HTTP 404 with an explicit account privacy restriction against paid model training; age confirmation does not resolve that separate setting. No privacy settings were changed. Cohere returned HTTP 422 (`INVALID_TOOL_GENERATION`) and streaming failures/timeouts, including a tiny nonstreaming diagnostic. Those routes cannot be ranked for medical quality from these responses.

The four routes producing clinical outputs are Muse Glimmer 30B, Muse Spark 1.2, Inkling, and Gemma 4 31B. Any per-request format failures, truncation, or timeouts remain visible in their availability and score tables.

## Artifacts

- Primary protocol: `runs/chunking-v1/protocol.json`
- Adapter amendment: `runs/chunking-v1/adapter-amendment.json`
- Secondary reader amendment: `runs/chunking-v1/secondary-adapter-amendment.json`
- Repair protocols: `runs/chunking-v1/repair-protocol.json` and `repaired-hierarchy-protocol.json`
- Original outcomes: `runs/chunking-v1/outcomes/`
- Losslessly normalized maps: `runs/chunking-v1/outcomes-normalized/`
- Requests, responses, token usage, and errors: `runs/chunking-v1/calls/`
- Comparisons: `runs/chunking-v1/comparison.csv` and `normalized-comparison.csv`
- Detailed scores: `runs/chunking-v1/scores.json` and `normalized-scores.json`
- Source-linked summary sample and complete applied-edit audit: `runs/chunking-v1/summary-audit-with-source.json` and `edit-audit-compact.json`
- Manual adjudications: `runs/chunking-v1/summary-adjudications.json` and `edit-adjudications.json`
- Additional attribution diagnostics: `runs/chunking-v1/posthoc-attribution-flags.json`
- Context recovery replays: `runs/chunking-v1/context-replays/`

Validation completed with 369 tests passing and 64 frozen file hashes verified. The matrix contains 42 model/strategy cells (including unavailable-route placeholders) and 336 patient/domain outcomes. Experiment artifacts occupy approximately 107 MB. No model weights, ontologies, or new article corpus were downloaded.
