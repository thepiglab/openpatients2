# Figure attribution and cross-article case linkage

Tested 29 September 2026. Recommendation: preserve article-local cases, add a focused figure/panel attribution stage, and use citations to retrieve possible prior reports. Keep published-case identity and synthetic composition as separate graph relations. Do not merge patients merely because their articles cite each other or their diagnoses resemble one another.

## What the papers actually support

[PMC-Patients](https://www.nature.com/articles/s41597-023-02814-8) uses its citation graph to label article relevance and patient similarity. Those are retrieval labels, not assertions that two records describe the same person.

[Synthetic Hospital](https://arxiv.org/html/2609.30027v1) deliberately constructs synthetic longitudinal patients by grouping compatible educational vignettes. Its graph fixes encounter membership before narrative generation. Its demographics/diagnosis compatibility rules are useful for future synthetic composition, not for identifying a real published patient across reports. I inspected [the patient-generation code](https://github.com/sparkcpark/synthetic_hospital/blob/b047385ae138566c891b7786c0fa9a78e6b5700a/etl/stages/s08_patients.py) at commit `b047385ae138566c891b7786c0fa9a78e6b5700a`; a small source copy is in the experiment directory.

## Recommended workflow

1. Acquire each licensed article independently. Keep XML/version hashes, article and asset rights, references, inline reference targets, tables, captions and explicit graphics. Bibliography remains outside the clinical extraction text.
2. Discover original patients and keep stable article-local IDs. A literature mention is a mention of another source's case, not a new original patient in the citing article.
3. Attribute each figure using its caption, explicit JATS links, nearby paragraphs and patient identity evidence. Split panels when ownership or subject differs. Keep shared multi-patient graphics shared; do not distribute all their clinical contents to every patient.
4. Preserve subject distinctions: patient anatomy, patient tissue, organisms from a patient, external cases, aggregate/background material and unresolved ownership. A parasite's uterus is not the host cat's uterus. External specimens may have a known subject while having no local patient ID.
5. Validate literal evidence and reference integrity. Repair failed formatting/evidence separately from semantic review. Literal quotes alone do not establish the right patient or entail the assignment. Escalate disagreement, contradictory ownership, missing panels and ambiguous context.
6. Use pixels where layout, labels, chart values or descriptions need inspection. Record the image URL/hash and keep model visual claims separate from article facts. Do not use visual resemblance to establish identity. Unknown image-only ownership stays unresolved.
7. Build bounded one-hop citation plans. Resolve the explicitly cited target, reapply its license/version checks, then assess its original cases. Distinguish `explicit_same_patient`, `cited_case_mention`, `similar_case`, `cohort_overlap`, `background_citation` and `uncertain`.
8. Same-case proposals need explicit continuity language, a particular source/target case, evidence from both sources and a separate recorded adjudication. Unresolved contradictions prevent acceptance. Only then build an additive linked view: preserve every source's facts, specimens, times and license; do not overwrite or blindly deduplicate them.
9. Future synthetic composite construction consumes compatible cases through a different relation. It must mark invented continuity and synthetic dates explicitly. It must not rewrite the research database's identity graph.

The identity helper requires direct accepted evidence for every pair in a proposed group. An A–B–C path alone remains pending; it cannot silently merge A with C. This favors avoiding false merges at the cost of missing some true transitive links. More permissive identity clustering needs a substantially larger reviewed benchmark.

## Figure experiment

**84 first-pass calls:** four working models × three strategies × seven figures. The figures contain **21 scored panels/shared-figure units across three articles**, with fixed source-checked patient rosters. Strategies: whole article text; focused caption/JATS-linked context; the same focused context plus actual image pixels. Output structure was requested in the prompt; API JSON-schema enforcement was not used.

Sources:

- [Two mesocolon tumor cases](https://pmc.ncbi.nlm.nih.gov/articles/PMC12285374/): F1/F2 belong to Case 1; F3/F4 to Case 2, including imaging and pathology panels.
- [Feline Dirofilaria report](https://pmc.ncbi.nlm.nih.gov/articles/PMC10998798/): Fig2 includes parasite microscopy; Fig3A is the index cat's organism and Fig3B is from an external canine case.
- [Three ethylene-glycol cases](https://pmc.ncbi.nlm.nih.gov/articles/PMC12802722/): one timeline figure explicitly covers all three cases.

Counts below are correctly delivered ownership decisions, out of 21. An invalid figure response contributes no delivered assignments; validation loss is therefore included. These are not population accuracy estimates.

| Model | Whole text | Focused text | Focused + pixels | Focused after validator correction + one repair |
|---|---:|---:|---:|---:|
| Glimmer | 9 | 8 | 4 | 15 |
| Inkling | 18 | 18 | 14 | 21 |
| Gemma | 21 | 21 | 19 | 21 |
| Spark | 21 | 21 | 21 | 21 |

No delivered assignment in this sample put a panel on the wrong local patient. Most misses were rejected outputs: nonliteral evidence, missing rationale fields, wrong figure identifiers, or a validator error discussed below. This is a coverage result on selected difficult examples, not proof of high precision in the wider corpus. The task did not test patient discovery, diagnosis from images, or all medical contents of a figure. Contributor and Cohere were not retried; their account/endpoint failures are already documented in the chunking study.

### What changed after seeing failures

The initial validator incorrectly treated subject type and local ownership as the same axis. It rejected external parasite/specimen images with no local patient ID even when the model correctly kept them separate. Correcting that rule recovered **four unchanged responses**, without asking a model to generate new answers. Original outputs, first-pass scores and source snapshots are retained.

Five focused outputs still failed. A single follow-up repaired three; two Glimmer responses still failed. Evidence/format repairs were required to preserve panel labels, patient IDs, scope and subject. A wrong-target response was handled as a separately labeled re-extraction, not passed off as a harmless formatting repair. There is no unbounded repair loop.

Subject labeling needs care in interpreting the scores. Our predeclared strict gold called the microfilariae in Fig2A/B `organism_from_patient`; several models called the blood-based microscopy `patient_specimen`. Both descriptions can be clinically compatible. The strict subtype score is preserved, and a separately labeled post-hoc audit accepts that overlap. All working models distinguished the parasite's uterus in Fig2C from host anatomy in valid outputs. Glimmer's repaired external-figure result still used a less appropriate subject label for the index panel, despite correct ownership.

### Cost and context

For Spark, the seven focused text calls used **27,625 input tokens**, versus **75,767** for whole text, a **63.5% reduction**. Ownership remained 21/21. Reported seven-figure cost was **$0.0978** focused versus **$0.1502** whole text; focused pixels cost **$0.0868**, with variable output length/caching, so this is not evidence that image input is inherently cheaper.

Gemma's focused calls cost **$0.00393** for the same seven figures and delivered 21/21 ownership decisions. This makes **Gemma for initial figure attribution and Spark for difficult adjudication** worth testing on a larger held-out sample. Spark remains the safer default when consistency across the earlier clinical extraction tasks also matters. API costs/latencies do not establish throughput on eight B200s.

## Cross-article linkage experiment

**36 first-pass calls:** four models × eight explicitly synthetic mechanism probes plus **one real published follow-up pair**. Controls cover ordinary similarity, external case mentions, same-cohort ambiguity, conflicting species, method citations, ambiguous prior case numbers and source/target case-number changes. This is a mechanism test, not a real-world entity-resolution benchmark.

- **No false same-patient claim** was accepted in these nine probes for any model. This tiny result is not a reliable false-merge rate estimate.
- All four correctly linked the controlled straightforward follow-up and correctly represented the external literature mention as `source_patient_id=null`, `target_patient_id=p2` in the dedicated mention test.
- Spark and Glimmer produced valid identity links for the real pair on the first pass. Inkling and Gemma initially supplied nonliteral source quotes; both succeeded with one repair.
- The initial multi-case renumbering probe had an overly broad roster label containing both cases' prose. Its first-pass outcome is retained but should not be treated as a clean model-quality comparison. A separately labeled correction gave Case 1 a distinguishing identity quote. Spark and Glimmer then linked source p2 to target p1; Inkling and Gemma abstained as uncertain. No false merge was introduced.
- Models sometimes called method citations or conflicting cases `cited_case_mention`. Typed edges prevented these relation-classification errors from becoming identity merges. Do not equate a valid JSON response with correct relation semantics.

### Real enrichment opportunity

The [2025 duodenal tumor follow-up](https://pmc.ncbi.nlm.nih.gov/articles/PMC12784145/) explicitly identifies itself as the continuation of [the 2018 report](https://doi.org/10.4166/kjg.2018.72.1.28). Reference `b7-kjco-25374` identifies the original publication. The earlier licensed English abstract reports treatment and 12-month follow-up; the newer report supplies an 88-month course and additional findings.

The original target was fetched from the publisher as JATS after checking its explicit CC BY-NC 3.0 notice; the experiment used its English abstract only, not an invented PMC identifier or an assumed translation of the Korean body. Its independent retraction status was not verified, so it remains **reference-only and barred from production clinical-seed export**. The current PMC source's CC BY-NC status and retraction flag were checked through current PMC metadata.

One particularly useful detail: the later report describes a kidney-region lesion whose relationship to the original tumor was uncertain. A linked view must retain that uncertainty and its separate timing, rather than silently recording definite recurrence or flattening the reports into a single clean narrative.

## Implementation and integration checks

- `jats_links.py` and `articles.py`: bibliography identifiers, exact inline target IDs, marker-enriched citation context outside the canonical clinical text, figure source links.
- `pmc_media.py`: explicit graphics outside normal `<fig>` tags, and group-level rights inherited by child figures.
- `figure_attribution.py`: separate panel pass, literal/reference checks, source/roster binding, unresolved coverage and whole-image URLs with selected-panel metadata.
- `experiment.py`: opt-in `figure_attribution: true` stage before patient-bundle construction. It uses the run's model. The tested repair/escalation routing remains experimental, not an automatic production policy.
- `article_tasks.py` / `ehr_seeds.py`: assignments and media carried into bundles/seeds; unrelated panels filtered from patient visual observations. Whole-figure caption claims remain context, not automatically patient facts.
- `case_links.py`: bounded citation plans, typed proposals, independent review provenance, conservative identity groups and additive linked views. Views verify article hashes and patient identity digests. Candidate retrieval and adjudication are explicit components; unrestricted citation crawling or automatic synthetic merging is not enabled.

**24 existing patient bundles and EHR seeds were rebuilt** with new media assignments. All existing clinical facts and timeline events were compared before/after and remained identical. The external canine panel is excluded from the index cat's assignments. This validates media integration, not the accuracy of those earlier clinical extractions.

Final verification: **396 tests passed**. Tests include the article runner calling the separate attribution stage, panel filtering in EHR seeds, literal evidence checks, source/roster binding, conflicting identifiers, contradictory identity claims, transitive-bridge rejection, group-level figure rights and unwrapped graphics. Frozen experimental inputs/source snapshots were hash-verified. Retained task artifacts occupy approximately **36.6 MB**.

The seven-figure benchmark did not cover cat Fig1. It therefore remains explicitly unreviewed in these exported demonstration bundles; it is not silently declared complete. Normal configured article processing visits every inventoried figure. Missing reviews and unassigned image URLs keep attribution completeness false.

An additional parser regression used [the hydrocephalus abstract](https://pmc.ncbi.nlm.nih.gov/articles/PMC12773240/). Its image is in a `<boxed-text><graphic>` block without a `<fig>` wrapper. The old parser reported zero figures. The new parser retains the image and its manifest URL. We inspected the small image but did not assert ownership from anatomical resemblance or article order; this asset remains unresolved. This parser check is separate from the 21-unit model benchmark.

## Artifacts and usage

All experiment files are under `runs/attribution-v1/`:

- `figure-protocol.json`, `figure-gold.json`, `figure-outcomes.json`, `figure-unit-audit.json`: frozen first-pass design, labels, responses and counts.
- `figure-v2-revalidated.json`, `figure-repair-outcomes.json`, `focused-after-repair-scores.json`: separate post-hoc corrections and repair results.
- `link-cases.json`, `link-protocol.json`, `link-outcomes.json`, `link-repair-outcomes.json`: clearly labeled real versus synthetic tests and follow-ups.
- `patient-bundles.jsonl`, `ehr-seeds.jsonl`, `bundle-integration-audit.json`: actual bundle outputs and unchanged-fact checks.
- `articles-v2.jsonl`, `citation-plans.jsonl`, `orphan-image-audit.json`: parsed articles, bounded retrieval plans and the recovered graphic.
- `v1-source-snapshots.json`, `frozen-v1-source/`, `pre-change-source/`: preserve experimental and earlier production code separately. Do not overwrite old protocol hashes when production code evolves. Old paid runners intentionally reject changed source versions; replay their saved outputs or restore the recorded versions in an isolated checkout.
- `budget-before.json`, `budget-after.json`, `budget.sqlite`: all **131 API calls** reconciled, **$0.74165164** spent this round, original-key usage **$25.54745536**, **$4.45254464 remaining**. No unknown charges.

Run the attribution-only example with:

```sh
uv run op2 article-experiment --config configs/experiments/figure-attribution-pilot.yaml
```

It uses the pinned small sample, fixed rosters, prompt-requested JSON and a fresh $1 local ledger. It is an example for a future authorized run, not an instruction to spend the remaining balance automatically. Add `figure_attribution: true` to a normal full clinical experiment to include the same stage there. Pixel description remains the separate `article-vision` workflow; it should not be confused with text-based ownership validation.

Build retrieval plans without fetching anything or merging identities:

```sh
uv run op2 citation-plan --input articles.jsonl --output citation-plans.jsonl --max-targets 20
uv run op2 case-link-graph --input adjudicated-edges.jsonl --output identity-graph.json
```

Inputs for citation planning need the new parser's reference fields. Plans preserve overflow as pending work. Each fetched target still needs independent source checks. Identity proposals stay candidates until an explicit second-pass review is recorded.

Temporary credentials, image pixels and skill-only dependencies were removed. No model weights, bulk corpus or ontology files were downloaded. Source/response artifacts remain for inspection and reproduction. The sample is small and selected; broader held-out source adjudication is required before adopting an automatic identity merge policy.
