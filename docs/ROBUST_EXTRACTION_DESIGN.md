# Evidence-preserving extraction, repair and patient timelines

Design and opt-in implementation, 2026-10-02. The current HiPerGator campaign,
its prompts, existing clinical schemas and reported scores are unchanged. Do not
sync source changes into its active checkout: campaign integrity checks pin files.

## Recommendation

Use a **source-first, patient-scoped fact store with a temporal evidence graph**.
Run the current direct extraction as the baseline. Escalate only where evidence,
patient identity, clinical semantics, chronology or source coverage has a specific
problem. A summary is a useful view, never the primary source for another pass.

Optimize **supported facts delivered per GPU-hour subject to error limits**.
A higher fraction of valid JSON objects or fewer failed sections is insufficient.
Source-supported numerical values, normal results, negatives, adverse events,
failed treatments and follow-up are useful even when other facts remain unresolved.

Published observations remain published observations. Later synthetic composition
must create a separate synthetic patient and retain mappings to each source case,
which fields were synthesized, and which temporal constraints were preserved.

## What is implemented now versus proposed

| Component | Implemented in this change | Remaining integration / evaluation |
| --- | --- | --- |
| Evidence | `extraction_contracts.py`: version/representation-specific source spans, hashes, code-point offsets; per-field evidence; domain/collection fact envelope | Obtain spans from model citations through an exact locator; validate the existing domain payload in its complete section; semantic support review |
| Coverage | An inventory-driven ledger; missing, unresolved, duplicate-cycle and wrong-patient fact checks | Build table-cell/panel inventories during ingestion and evaluate independent missed-fact reviews |
| Timeline | `longitudinal.py`: per-patient events, multiple time expressions, plan/occurrence separation, episode labels, fact references, relation edges, cycle/offset checks | Calibrated time normalizer, full interval algebra and integration into EHR seed export |
| Refinement | `refinement_plan.py`: typed issues and attempt history, dependency ordering, budgets and no-progress stops; proposes one next call per target | Model orchestrator, verified patch application, escalation policy, blinded quality comparison |
| Corpus | `corpus_policy.py`: rights-aware triage, case-priority and exploration queues, optional grounded-roster routing | Full inventory scheduler, yield estimates and evaluated threshold selection |

These components are opt-in and not imported by the existing benchmark runner.
Schema JSON files are provided for storage/application validation; they do **not**
require token-level schema-constrained generation. The model can emit prompt-only
JSON and the existing parser can recover complete, unambiguous objects.

Run the small, invented example without downloading data or making model calls:

```bash
uv run --locked --python 3.12 python scripts/audit_patient_timeline.py \
  --input examples/robust-extraction/timeline-request.json \
  --output /tmp/op2-timeline-audit.json
```

Passing this audit means the deterministic source/graph checks passed. The output
always keeps `clinical_timeline_verified=false`; semantic adjudication is separate.

## Source and patient contracts

1. Pin PMCID/version, PMID/DOI crosswalk, article license version, current metadata
   retrieval time, XML digest and each actual input representation. JATS/TXT/PDF
   spans have different namespaces. Never copy JATS offsets onto TXT or OCR.
2. Inventory every paragraph, abstract block, table row/cell, caption, figure panel
   and supplementary-file manifest before generation. Keep reference cross-links
   and correction/version metadata outside the ordinary clinical prompt.
3. Find original individual cases across the entire article. Assign stable keys
   such as `PMC123.1:p2`; `p2` alone is not globally unique. Record species and the
   documented species name. Family members, mother, fetus, newborn, donors,
   recipients and comparison subjects need explicit relationships and distinct
   identities when their individual courses are described.
4. Shared paragraphs can belong to several candidates. A patient-level packet does
   not establish ownership of every sentence in it. A subsequent attribution pass
   checks the particular fact and its distinguishing text/table column/panel.
5. Keep cited external cases, cohort aggregates and unidentified observations in
   separate lanes. A trial with 100 subjects does not yield 100 patient records
   unless the source supports distinct individuals and their measurements.

`FactEnvelope` wraps a domain fact with an immutable fact ID, scoped record ID,
collection, source origin and field-level support. Every populated material leaf
needs evidence, including zero and false. The original domain schemas still check
value types, missingness, diagnostic certainty, treatment/test status and oncology
references. An exact quotation does not prove that it supports a field: an explicit
semantic review must assess subject, modifier, negation, time and value attachment.

A table value should cite its cell plus row label, patient column and time-group
header as separate spans. Do not manufacture one quote by joining distant cells.
For merged cells, footnotes, inequality signs, superscripts or uncertain OCR, retain
raw text and queue layout review. Never infer a unit from customary practice.

## Gate sequence and bounded repair

| Gate | Detect / review | Response |
| --- | --- | --- |
| Acquisition/rights | Missing or conflicting versions, rights, unavailable text or corrupt asset | Quarantine acquisition; an LLM cannot repair legal permission or source bytes |
| Transport/termination | Timeout, API failure, truncation, reasoning/final-channel error | Retry once or increase an output allowance within budget; split an oversized task; never treat incomplete JSON as a completed extraction |
| Structure | Invalid enums/types, missing required fields, contradictory result states | Repair the failed item/field, preserving accepted neighbors |
| Exact source | Wrong segment, modified quote, digest mismatch | Re-locate only within the correct source representation; ambiguous duplicates require review; never alter source to fit a claim |
| Attribution | Wrong patient, table column, maternal/fetal relationship, external case, shared panel | Review the identity evidence with relevant neighboring context and the registry |
| Clinical support | Unsupported dose, target mistaken for result, negation or certainty inversion | Propose a field-specific correction or abstain; compare with original source |
| Time | Wrong encounter, unsupported anchor, cycle, conflicting offsets | Recheck the event pair and time clause; preserve genuine source disagreement |
| Coverage | Unaccounted row, section, caption or follow-up event | Re-scan the original unit with enough surrounding context; add new candidates, not invented facts |
| Terminology | Ambiguous/obsolete code, over-specific LOINC/SNOMED mapping | Retrieve pinned catalog candidates, choose only a supported one or abstain |

The proposed controller is:

1. Parse and run deterministic gates; freeze structurally/source-valid neighbors.
2. Group actionable issues by immutable target ID, not list index. Schedule one
   next action per target, resolving upstream source/identity issues first.
3. Supply original candidate, exact gate reasons, target patient/episode, relevant
   source plus headers/neighbor context and allowed output shape. Keep defaults and
   nulls visible: hiding them misled an earlier semantic-review experiment.
4. Require proposed changes to identify corrected fields, new exact support,
   retained fields and unresolved issues. No changes to accepted neighbors.
5. Revalidate the full containing section, references and timeline graph. Additions
   receive their own source gates. Independently check high-risk semantic edits.
6. Commit only approved changes as new versions with before/after digests, model,
   prompt/settings, evidence, reasons, tokens, latency and reviewer provenance.
   Accepted-neighbor freezing is not permanent immunity: a later conflict can
   explicitly invalidate a fact, with a separate logged review operation.
7. Stop at two rounds per target, no progress, repeated candidates, regressions,
   or the patient request/token budget. Preserve accepted partial output and an
   unresolved queue; never rewrite failure as `not_documented` or `normal`.

The implemented planner defaults to 12 calls/patient, 2 rounds/target and 100,000
**input plus generated tokens**, with a reserved allowance of 8,192 per scheduled
call. These are experiment settings, not calibrated production settings. Tokenize
before dispatch; if a packet exceeds its allowance, resize/escalate the request
explicitly rather than dropping source context silently. The ledger must include
successful calls as well as failures; a new plan must account for pending calls.
The prototype assumes serialized plan execution and regeneration after each batch;
it is not a distributed reservation ledger.

Use failure codes, not free-form model confidence, to select a fallback. Keep
semantic critics advisory until a held-out test shows they reduce errors. Our
[prior refinement study](../reports/REFINEMENT_EXPERIMENTS.md) found useful local
repairs but also semantic-review regressions and missed normal table values.
An entity inventory is an optional coverage tool, not an exclusive list of what
downstream extractors are allowed to see.

## Patient chronology

A timeline is a graph of **events and source-supported temporal relations**, not
an ordered list of sentences. Each event links to original fact IDs and evidence.
Store multiple time expressions when the article gives onset, sampling time,
result-report time, treatment duration and follow-up time separately. The opt-in
schema retains these expressions, but role-specific onset/end/sample/result
normalization remains an integration step; do not collapse them to one date.

| Source statement | Representation and required restraint |
| --- | --- |
| “On admission” | Link to that patient's correct admission; do not assume every later admission is the same encounter |
| “Two days after surgery” | A relation between two explicit events; normalization requires reviewing which surgery |
| “Several weeks later” | Order-only relation and literal wording, without invented numeric bounds |
| “About two weeks later” | Approximate offset; do not treat it as an exact constraint or invent a tolerance interval |
| “Postoperative day 3” | Preserve source ordinal and convention; do not silently choose whether surgery is day 0 or day 1 |
| “In March 2020” | Month precision retained as text; a reviewed normalizer may supply a month value, never an invented day |
| “2020-03” | Literal calendar month, not `2020-03-01` |
| “At age 6” | Age-at-event, not an encounter date inferred from age/publication year |
| “At 30 weeks gestation” | Gestational age, distinct from postnatal age and elapsed follow-up |
| “Planned review in six weeks” | Planned event; absence of later text is not evidence that follow-up occurred |
| Different values in prose/table | Preserve both with source conflict; never average or silently choose one |

Time comparisons must retain their measurement semantics. A response after a drug
is a temporal relation; causation or an adverse drug reaction requires author
support and separate uncertainty. Repeated tests and interventions remain separate
events when time/status differs. Do not merge an order, specimen collection,
result and diagnosis simply because their names overlap.

The implemented graph gate catches strict-order cycles (including equality links)
and incompatible numeric offsets in compatible units. It does not convert months
or years to fixed days, treat approximate numbers as exact bounds, or order
unconnected events. `during` and `overlaps` remain reported interval relations with
review flags until interval endpoints are modeled. Numeric normalization and
relation entailment are always reviewed separately from mathematical consistency.

For synthesis later, select an artificial index date only in the synthetic layer.
Shift confirmed relative constraints together, preserve precision and intervals,
and label invented transitions/encounters. A source graph with contradictions,
unresolved identity or uncertain event attachment cannot become a certified
synthetic timeline just because a topological order can be computed.

## Figure and supplementary integration

Reuse the rich [figure schema](../schemas/figure-visuals.schema.json). Store raw
pixel description, caption claims and clinical significance separately. Compare
joint versus independent attribution using both correct and adversarial multi-case
figures. Test chart-versus-scan classification, modality/submodality, anatomy,
medical domain, panel identity, chart metric/unit/time, and readable versus inferred
values. Figure metadata or a plotted treatment target is not a measured vital sign.

A chart-derived reading must retain figure/panel, axis/series, patient, units,
approximation/readability, pixel hash and any table/text corroboration. It remains
an unreviewed visual annotation until the clinical support gate accepts it. A
figure may contain several modalities and several patients. A graph *about* image
modalities is not itself a CT/MRI image.

Inventory supplements by title, description, format, official URL, rights and
availability. Flag likely longitudinal CSV/XLSX tables for later bounded retrieval;
“supplement available” is not “supplement inspected.” Missing pixels or uninspected
supplements must survive coverage export and affect claimed completeness.

## Corpus selection: high yield without a case-report-only blind spot

Use **PMC OA full text as the acquisition universe** and PubMed as a discovery,
publication-type, MeSH and citation crosswalk. PubMed search matches and abstracts
do not establish availability or reuse rights for the full article. PMC documents
approved automated acquisition mechanisms and warns that PMC availability alone
does not grant reuse rights. Use its current versioned Cloud inventory/metadata;
the legacy dataset distribution changed in August 2026. [PMC OA](https://pmc.ncbi.nlm.nih.gov/tools/openftlist/),
[PMC Cloud documentation](https://pmc.ncbi.nlm.nih.gov/tools/pmcaws/),
[PubMed help](https://pubmed.ncbi.nlm.nih.gov/help/).

| Priority lane | Candidate articles | Extraction policy |
| --- | --- | --- |
| First | Detailed case reports, small case series, clinicopathological/clinical reasoning reports, treatment complications/failures, long follow-up | Full roster, per-patient facts and event graph; highest expected yield is a hypothesis to measure |
| Second | Letters/brief reports, genetic families, diagnostic studies, surgical/therapeutic studies with identifiable individual vignettes | Same patient-level gate; subtype/title is a prioritization signal, not a hard inclusion rule |
| Targeted | Cohorts/trials with individual trajectories, patient-specific tables or accessible supplements | Extract only individually attributable observations; aggregate rows remain aggregates |
| Exploration | All other eligible article types, including atypically labeled cases | Audit a random stratified sample; expand the screen if useful cases are missed |
| Context | Reviews, guidelines, meta-analyses and aggregate-only studies without original individuals | Retain article/citation metadata; no invented individual patients |

PMC-Patients explicitly reports finding valuable cases outside articles labeled
case reports. Its citation-derived patient–article relevance and patient–patient
similarity labels are **not same-person identity labels**. Our article-local
records stay separate, with citation-guided retrieval proposing evidence to the
existing explicit identity-review workflow. [PMC-Patients paper](https://pmc.ncbi.nlm.nih.gov/articles/PMC10728216/).

[MultiCaRe](https://zenodo.org/records/10079370) provides useful existing case,
caption, image and metadata seeds. Recheck original article versions and rights;
its labels are useful candidates, not independent gold truth for our attribution.
CARE reporting guidance explicitly covers patient information, diagnostics,
interventions, chronology, adverse events and follow-up. Use those dimensions to
measure source richness, not as a certification that a paper is complete.
[CARE checklist](https://www.care-statement.org/checklist),
[CARE writing guidance](https://www.care-statement.org/writing-a-case-report).

Hard acquisition gates: explicit eligible license, unambiguous version/identity,
non-retracted status verified from metadata, usable full text and bounded verified
assets. Unknown or conflicting metadata is quarantined for review. Recheck current
retraction/correction status before release; offline `recheck_license` is not a
fresh metadata lookup. Corrected versions need explicit selection and retained
supersession links, not largest-version guessing.

Continue the requested noncommercial adaptation lane with CC0, CC BY, CC BY-NC
and CC BY-NC-SA, plus explicit worldwide public domain and supported permissive
article grants (MIT, MIT-0, Apache-2.0, BSD-2/3-Clause, ISC, 0BSD and Unlicense).
See [the implemented article license policy](ARTICLE_LICENSE_POLICY.md) for scope,
version and conflict gates. Retain exact source terms and figure exceptions. CC BY-SA is
kept in its own source-terms lane; “at least CC BY-NC-SA” is not a linear license
ordering. ND and unknown/restricted rights do not enter the adaptation pipeline.
Do not relabel every component with one blanket dataset license. [Creative Commons
FAQ](https://creativecommons.org/faq/index.html), [ShareAlike compatibility](https://wiki.creativecommons.org/wiki/ShareAlike_compatibility).

Do not hard-filter by successful outcome, rarity, journal impact, English language,
human species, age or article length. Record these attributes and estimate the
cost/yield of strata. Very long sources should route to section/table-preserving
chunks plus coverage reconciliation, not disappear. Animal patients remain a
separate filterable species lane. Autopsy/cadaver/ex-vivo material needs an explicit
subject state and must not acquire a living patient's course by inference.

A useful source-richness vector is: identifiable individual, presentation,
diagnostic evidence, intervention detail, outcome, follow-up, temporal anchors,
quantitative observations, patient-specific figures and supplement availability.
Keep each dimension separately. No current weighted score is calibrated.

Start with a proposed 300-article pilot: 100 case-focused, 75 adjacent types with
case signals, 75 randomly sampled eligible articles without those signals, and
50 difficult multi-patient/table/figure/longitudinal cases. Stratify within lanes
by specialty, year, language, species, source length and outcome. Record selection
probabilities; report challenge-set performance separately from estimated corpus
performance. Measure yield and false exclusions before choosing production cutoffs.
Do not interpret this selected published-case corpus as population incidence,
prevalence, or a representative clinical workload.

## Validation experiment before promotion

Freeze sources, licenses, patient identities, prompts, token limits and model
versions. Split by article, version family and known linked-case group, not by
paragraph or patient from the same article. Keep historical fixtures as regression
checks and add a separately adjudicated held-out set; never tune against its answers.

Ablate these mechanisms independently, then test promising combinations:

- Direct extraction with the current gates and output allowance.
- Direct plus item-local repair (including generalized oncology collection repair).
- Direct plus source-unit coverage rescan.
- Direct plus targeted patient/table attribution review.
- Direct plus event/anchor reconstruction and temporal graph gates.
- Optional span-first extraction with full-context fallback for missed units.
- A generic semantic critic as a comparison, not a presumed improvement.

Use the same seeds and an equal-call/token-budget comparison as well as an
unconstrained quality comparison. Evaluate model-assisted adjudication separately
from blinded clinician review; agreement between two LLMs is not clinical truth.

Primary measures: supported delivered facts, unsupported material fields, wrong-
patient assignments, preserved value/unit/time tuples, source-unit missed-fact
rate and supported facts per GPU-hour. Count unresolved facts and missing records
in coverage denominators; do not improve precision by quietly dropping hard cases.

Timeline measures: supported event recall, temporal relation precision/recall,
correct anchor attachment, exact/approximate/order-only calibration, plan/completed
confusions, contradictory graphs and unsupported absolute dates. Figure measures:
panel attribution, shared/external/unresolved handling, visual/caption separation,
chart quantity/unit/time accuracy and clinical-significance support.

Report per-article macro scores, fact-level micro scores, failures, abstentions,
retries, input/output/reasoning tokens and latency; bootstrap confidence intervals
by article/linked-case cluster. Count human edits and source-consultation time.
Proposed promotion rule: improve supported-fact yield without a meaningful increase
in critical attribution/value/negation/time errors; define margins before evaluation.
Critical error tolerance and review sampling rates are decisions to calibrate,
not numerical assurances supplied by a schema validator.

## Work sequence

1. Now: opt-in contracts, offline gates and adversarial software tests (implemented).
2. After the current campaign: audit actual failures and identify which gate types
   account for supported-fact loss; do not change inference settings mid-comparison.
3. Next: connect the coverage inventory and generalized item repair controller to a
   new experimental runner; add field-level review labels and source-conflict cases.
4. Then: run the stratified acquisition pilot and budget-matched model ablations;
   obtain independent clinical adjudication on the held-out subset.
5. Promote only measured improvements, then build source-backed EHR seeds and a
   separately versioned synthetic timeline compiler. No calendar ETA is claimed
   until cluster capacity and annotation effort are measured.

## Verification recorded for this change

- 90 tests passed across the new contracts and existing clinical schemas,
  refinement experiments, article handling, EHR seeds and recovery integration.
- All six exported JSON schemas match their Pydantic definitions.
- The offline invented timeline example passed deterministic checks while retaining
  unreviewed clinical status and the uncompleted planned follow-up.
- The triage heuristic was exercised on all nine existing frozen articles: six
  entered priority screening and three entered exploration screening. None were
  discarded by article type. This small selected set is not a corpus-yield study.
- No model inference, source downloads, ontology downloads or cluster changes were
  made for this work. The next model experiment remains necessary to measure gains.
