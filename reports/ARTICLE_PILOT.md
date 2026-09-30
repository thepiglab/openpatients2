# PMC → patient records: implementation and live pilot

**Historical implementation results.** The later [medical-fidelity comparison](MEDICAL_FIDELITY.md) tests regular `meta/muse-spark-1.2` successfully and audits source facts across four models. Its findings qualify the provisional model choice below: structural success is not medical accuracy. Spark access limitations and account totals in this report describe the earlier run only.

Run date: September 28–29, 2026. Machine-readable measurements: [article-pilot-results.json](article-pilot-results.json). Recompute them with `uv run python scripts/summarize_article_pilot.py` without making model calls.

**Provisional choice: Muse Glimmer for text extraction, with Gemma as the lower-cost alternative and optional image worker. Keep output enforcement configurable.** This is a development pilot, not a physician-adjudicated benchmark. It establishes working acquisition, attribution, extraction, validation and export paths, and identifies concrete failure modes. It does not establish population accuracy or eight-B200 throughput.

## What was actually tested

- Twelve purposively selected PMC full articles: original single cases, multiple cases, literature-review contamination, a conference report, numerical tables, nonhuman patients, and two negative controls without original individual cases.
- Three original model candidates on patient discovery; four patients from three articles on all 14 clinical tasks plus summary and timeline; paired output-format controls; and actual figure pixels.
- Cohere Command A+ on the same roster and full clinical task sets, with a provider-specific schema projection, plus image and non-streaming controls.
- Spark access probes. The age gate cleared, but the Contributor endpoint then rejected requests under the account's paid-provider training privacy setting. **There is no Spark extraction score.** Saved zero-success access attempts must not be interpreted as poor model quality.
- A corrected, end-to-end run on three articles passing the final license audit, discovering four patients itself: one child, two adult men from one article, and one cat. Gemma inspected one figure from each article using Glimmer's roster.

Requests were pinned to provider routes with price ceilings; fallback routing was disabled. Glimmer used Together with low reasoning; Inkling used DeepInfra FP8 with reasoning disabled; Gemma used DeepInfra Turbo with reasoning disabled. Cohere used its own endpoint. These provider/precision combinations are not controlled comparisons of model architecture. Hosted revisions were not immutable weight snapshots.

Raw requests, returned reasoning, outputs, costs, failures and retries are retained under `runs/article-pilot/`. No secrets or image data URLs are stored in those requests. Application validation checks object structure, literal source quotes, temporal phrases, patient references and other invariants. A valid result can still omit facts, misattribute an exact quote, or misread an image.

### Corrected end-to-end result

The audited Glimmer run completed **65/67 tasks** on its initial pass including one bounded repair per task. The 67 tasks are three rosters plus 16 tasks for each of four discovered patients. One summary was incomplete JSON despite a `stop` finish reason; the cat's observations failed literal-source checks. One explicit retry sweep of just those two tasks brought the saved results to **67/67**, with every earlier failed attempt retained. That extra sweep is not counted as initial-pass success. Total reported request cost for both sweeps was **$0.2563**, excluding images and the broader comparison.

The resulting [hybrid EHR-seed examples](../runs/article-pilot/exports/muse-gemma-ehr-seeds.jsonl) contain four observed cases, **185 facts and 38 timeline events**: three humans and one cat. All have valid clinical tasks, summary and timeline. Only one has complete source coverage across every domain; the other three explicitly retain coverage limitations. None is clinically adjudicated. Gemma pixel annotations accompany three patients; the second patient in the two-case article retains its figure URLs/assignments without falsely claiming its unsampled figure was inspected.

The [SQLite research index](../runs/article-pilot/exports/muse-clinical.sqlite) contains all four cases and 185 facts. The [age ≥50 human cohort](../runs/article-pilot/exports/older-human-cohort.jsonl) returns both patients from the same article after deduplication. It explicitly permits incomplete/source-limited records. The same query with strict completeness returns zero, as expected; it does not mislabel the two records as fully complete.

## License audit changed the releasable corpus

The initial acquisition trusted PMC's license code and directly attached license URLs too much. Inspecting the actual JATS license prose exposed three contradictions:

| Article | Conflict | Final treatment |
|---|---|---|
| PMC10828776 | CC BY versus CC BY-NC | Review required; excluded |
| PMC13290180 | CC BY-NC-SA URL/code versus CC BY-NC-ND prose | Review required; excluded |
| PMC13278253 | CC BY-NC-SA URL/code versus CC BY-NC-ND prose | Review required; excluded |

The corrected parser checks nested URLs, abbreviated license text and restrictive prose. Inference, pixel inspection, indexing and EHR-seed export recheck old acquisition approvals. Unknown/ND/conflicting licenses fail closed. CC BY-SA has a separate source-terms lane; Creative Commons licenses are not a single permissiveness ladder. This is an acquisition policy, not a legal determination that every resulting artifact can be uniformly relicensed. See the [CC license descriptions](https://creativecommons.org/share-your-work/cclicenses/).

Nine articles passed the final audit. The original acquisition snapshot and historical comparison outputs remain available for experiment auditing, **not as a release-ready licensed corpus**. Prior export examples were withdrawn to `runs/article-pilot/quarantine/pre-license-audit-exports/`. Corrected data are in `articles-audited.jsonl`; corrected export examples are in `runs/article-pilot/exports/`. The complete findings are in `research/license-audit.json` under the pilot run.

## Article lengths: measured, but not a population estimate

PMC is the full-text archive; PubMed is principally the bibliographic/abstract discovery layer. Full JATS gives the case narrative, tables, captions and follow-up needed here. Use the [current PMC Cloud interface](https://pmc.ncbi.nlm.nih.gov/tools/pmcaws/); the [old OA service was retired](https://pmc.ncbi.nlm.nih.gov/tools/oa-service/).

Lengths count whitespace-delimited words in abstract + body + tables + captions + appendices, excluding bibliography and author metadata. P95 uses linear interpolation at `(n−1)×0.95`.

| Sample | n | Mean words | Median | P95 |
|---|---:|---:|---:|---:|
| Original case-containing pilot articles, before license audit | 10 | 2,332 | 1,891 | 4,715 |
| Case-containing articles passing final license audit | 7 | 2,408 | 1,978 | 5,087 |
| All articles passing audit, including negative controls | 9 | 3,173 | 2,088 | 7,581 |

These are small, deliberately varied samples. They **do not determine the average/median/P95 of PMC-Patients source articles or the eligible PMC population**. The earlier estimate of roughly 2–2.5k words per case article remains a planning hypothesis. Estimate population percentiles from a probability sample of the eligible inventory, with discovery-stratum weights and uncertainty, before allocating the full campaign.

The frozen pilot text predates final parser improvements to block spacing and table group headers. It was not silently reparsed after model calls; source hashes and citations remain reproducible. The code used for future acquisition includes those fixes.

## Comparative extraction results

The historical clinical comparison uses fixed, source-checked patient rosters to isolate downstream extraction: a bullet embolism case, a canine case, and two transfusion cases. The latter two articles were subsequently excluded by the license audit. Prompts and validators were improved during development and some failures were retried; this table describes final stored task validity, not a blinded first-pass ranking.

| Model / route | Valid rosters, 12 articles | Valid clinical/summary/timeline tasks, 64 | Patients with all 14 + companions valid |
|---|---:|---:|---:|
| Muse Glimmer / Together | 12/12 | 62/64 | 3/4 |
| Inkling / DeepInfra FP8 | 11/12 | 46/64 | 0/4 |
| Gemma 4 31B / DeepInfra Turbo | 10/12 | 55/64 | 1/4 |
| Command A+ / Cohere | 1/12, unconstrained | 20/64, schema requested | 0/4 |
| Muse Spark Contributor | Access blocked | Not evaluated | Not evaluated |

All valid original-model rosters matched the manually checked individual counts. This is only a count spot-check, not validation of every attributed passage. Glimmer separated original cases from the six cited cases in one review and did not create individual records from the aggregate negative controls. Animal records retain explicit species and remain outside default human cohort queries.

For Cohere's full schema-requested run, 113 attempts yielded 44 responses without a transport error, 55 stream errors, 7 timeouts, 6 HTTP 422 errors and one HTTP 502 error. Of 64 final tasks, 29 ended in transport/provider errors and 15 failed output checks; 20 passed. Consequently, this run cannot support a clean claim about Cohere's intrinsic extraction ability. The unconstrained clinical run was stopped after 16 completed tasks because repeated failures were unproductive; its interruption record is preserved rather than presented as a completed comparison.

Two non-streaming Cohere controls also failed: one returned HTTP 422 with `INVALID_TOOL_GENERATION`; the other returned an empty observations object that contradicted its own documentation status and failed validation. Switching transport alone did not resolve the observed problem.

## Did enforcing JSON hurt generation?

The controlled roster subset contains two articles. Each arm used the same original evidence. “Schema requested” means the API accepted a structured-output request; backend grammar enforcement was not independently verified. Inkling did not advertise support for the required schema mode.

| Model | Prompt-only object, no API schema | Schema requested | Prose then explicit normalization |
|---|---:|---:|---:|
| Glimmer | 2/2 | 2/2 | 2/2 final rosters |
| Gemma | 2/2 | 2/2 | 2/2 final rosters |
| Inkling | 2/2 | Unsupported in this test | 1/2 final rosters |
| Cohere | 0/2 | 2/2 after schema compatibility fix | 1/2 final rosters |

On a second subset of eight clinical tasks, Glimmer passed 8/8 and Gemma 7/8 with schema requests; their corresponding unconstrained outputs also passed 8/8 and 7/8. **This small test found no evidence that schema requests lowered extraction quality. It also cannot establish equivalence.** Prose-first added another generation and normalization step without a demonstrated benefit here.

Cohere rejects parts of standard JSON Schema, including numeric/string/array bounds. Its explicit wire projection omits unsupported constraints while retaining the full prompt and application validation. See [Cohere's supported subset](https://docs.cohere.com/docs/structured-outputs). A schema that compiles is not sufficient to establish factual fidelity.

The default remains `prompt_json`, respecting the preference to avoid mandatory API enforcement. The parser accepts complete JSON, fenced/embedded objects, safe Python literals and complete YAML; it records recovery operations. It rejects conflicting objects, duplicate keys and truncated output. It does not guess medical meaning from arbitrary prose. The optional prose arm performs an explicit second model call against the original evidence.

## Inspecting fidelity beyond valid objects

- In a perfusion-map figure from **PMC13294519**, the printed mismatch ratio is **3.5**. Glimmer and Gemma read 3.5; Inkling reported 2.5. All three outputs passed structural validation. The pixels were independently inspected during this task; this is one observed numerical error, not an overall image accuracy rate.
- All three original models produced valid image objects on all three selected figures. Inkling left patient identity unresolved for all three; Glimmer and Gemma differed in how often source captions supported assignment. A valid image description and a valid patient association are different checks.
- In the historical two-patient transfusion example, Glimmer separated the patients' hemoglobin values. Maintenance chemotherapy appeared in oncology/summary but not medications: a domain-consistency issue that a global “fact present” score would miss.
- Strict source checks caught altered quotes and invented temporal wording. Another caught an age field populated with the dog's 24 kg weight. Empty output is valid only when the source truly lacks that domain and the metadata agrees; dropping evidence merely to satisfy a validator is not an acceptable repair.
- The corrected two-patient example retains distinct 50- and 55-year-old patients. Deduplication now uses within-article patient identity and roster evidence, preventing the shared full article from collapsing them into one row.
- Timeline outputs preserve literal follow-up intervals, including one year and 36 months. Some intervals remain order-only/unanchored when the model cannot ground an anchor. This is a reviewable limitation rather than a fabricated absolute date.

The corrected image stage inspected three additional, license-audited figures with Gemma; all three passed structural/source-attribution checks. Numeric readings and diagnostic interpretations remain unreviewed. Images are passed transiently and represented durably by actual source URLs, checksums, caption claims, pixel observations and patient assignments. Supplementary files are inventoried, not downloaded or assumed to contain measurements based on a filename alone.

## Cost and eight-B200 planning

The initial matched four-patient comparison, including its bounded repair calls, reported:

| Model | Request cost | Input tokens | Output tokens, including reported reasoning | Median task latency | P95 task latency |
|---|---:|---:|---:|---:|---:|
| Glimmer | $0.2466 | 416,181 | 121,860 | 11.69 s | 57.59 s |
| Inkling | $0.5903 | 525,728 | 89,633 | 4.59 s | 42.49 s |
| Gemma | $0.0631 | 485,015 | 90,695 | 13.61 s | 128.15 s |

These costs belong to the first-pass snapshot under earlier validators, not the final repaired score table. Failed/unknown charges, later development runs, figures and roster experiments are not included in this table. They are not per-article price quotes or predictions of self-hosted performance. Caching, output lengths, provider load and precision differ.

The important capacity observation is that a ~3k-word article does **not** imply only ~3k tokens of work per patient. Fourteen branches plus summary/timeline and retries produced roughly 104–131k logical input tokens and 22–30k output tokens per patient in that initial run. Shared-prefix caching reduces repeated prefill, but it does not remove decode work. Batch and route branches by patient/cache-owning worker.

Final OpenRouter account usage was **$3.23596264**, leaving **$26.76403736** of the supplied $30 limit at 02:57:33 UTC on September 29. Initial usage was zero. This account reading includes control probes and charges that could not be attributed from streaming responses. The local ledger reports $3.15596879 in explicit response costs and conservatively keeps unresolved timeout/error reservations; reserved dollars are not reported spending. An audited set of 78 pre-inference Spark routing rejections was released from reservation, with the evidence retained in `research/budget-reconciliation.json`. No unknown generation charge was silently treated as free.

Both Glimmer and Gemma now have command-checked vLLM 0.30.0 profiles for 8×TP1, 4×TP2, 2×TP4 and 1×TP8, using BF16 and no speculation. **No B200 measurements were made.** Start with a fixed workload and compare validated records/hour, supported facts/hour, latency, output length, retries, memory and per-domain recall. Do not select topology from hosted API latency or active-parameter count alone.

Glimmer's [versioned vLLM parser](https://docs.vllm.ai/en/v0.30.0/api/vllm/reasoning/muse_glimmer_reasoning_parser/) has a [reported structured-output decoding performance issue](https://github.com/vllm-project/vllm/issues/54453). That report is not a measurement made here; it is a reason to verify schema/reasoning behavior on the exact engine build. The [Gemma serving recipe](https://docs.vllm.ai/projects/recipes/en/stable/Google/Gemma4.html) supplies prerequisites, not throughput for this pipeline.

## Working artifacts and remaining boundaries

The [workflow guide](../docs/ARTICLE_PIPELINE.md) documents executable acquisition, experiments, image inspection, indexing, terminology and seed export. [Synthetic Hospital review](../docs/SYNTHETIC_HOSPITAL_REVIEW.md) records the paper/code lessons and why observed case facts must remain separate from future imputed events and patient merging.

The current code provides article/asset license lineage, patient/species attribution, 14 clinical domains, cited summaries, relative temporal graphs, figure URLs and pixel descriptions, supplementary manifests, vocabulary download/configuration, catalog-backed code matching, cohort search and observed EHR seeds. Ontology downloads were not performed. Existing ICD-10-CM/SNOMED/LOINC mapping remains candidate- and evidence-aware; RxNorm/UCUM mapping is not implemented.

The seed format is not a FHIR Bundle and published cases are not themselves synthetic patients. It deliberately does not invent missing labs, dates or outcomes, or merge patient identities. Full-corpus inventory scheduling, distributed storage/queues, global cross-article identity resolution and physician-adjudicated release evaluation remain deployment/research work. The article runner is a bounded pilot/shard harness, not a million-row in-memory job launcher.

Before a large release, freeze the extraction policy and held-out article clusters; independently annotate omissions, wrong-patient attribution, negation, units, temporal anchors and image readings. Include negative controls and low-priority discovery samples. A correct JSON object and an exact quote are necessary checks, not a clinical accuracy guarantee.

Software verification: **352 tests passed**, plus the real index/cohort/seed checks above and command generation for all eight Glimmer/Gemma serving profiles. Tests cover license contradictions, stale approvals, multi-patient deduplication, source offsets, malformed outputs, bounded media, budget accounting, temporal constraints and terminology archive handling. They do not substitute for clinical review. No weights or ontology releases were downloaded; the original article packets occupy under 1 MB, and the larger retained files are reproducibility/audit traces.
