# Article profiling with decision models

Research snapshot: **2026-10-23**. This is an experiment design and source/evidence
contract, not a deployed classifier. No decision-model weights were downloaded,
no inference was run, and no training or PubMed-wide filtering was started.

## Recommendation

Use a small decision model to screen and characterize documents, select evidence
candidates, and route ambiguous extraction work. Keep a stronger model for
cross-patient attribution and difficult temporal/clinical interpretation. Test
zero-shot performance first; fine-tune a small model if the domain-specific error
analysis supports it. Do not turn a general leaderboard score into a clinical
quality claim or silently discard uncertain articles.

The first experiment should compare a strong Gemma decision checkpoint, a small
Liquid checkpoint, a deterministic NLM-metadata baseline, and a compact encoder
baseline if its lineage is permitted. Their document-level accuracy, abstention
and complete-profile throughput will tell us whether the larger model is worth
using only as a teacher/escalation path.

## Models and the backbone restriction

The [Jev Decision Index](https://huggingface.co/spaces/multimodalart/jev-decision-index)
payload generated at `2026-10-22T20:10:35Z` contains the following **Full** scores.
Do not mix them with older public-suite numbers from model cards. Rankings are
screening evidence, not clinical validation; close numerical values are not
proof of a meaningful difference. Pins, source URLs and the downloaded index
hash are in
[`decision-model-research-20261023.json`](../configs/article-profile/decision-model-research-20261023.json).

| Candidate | Declared backbone | Full score | Proposed role |
|---|---|---:|---|
| Jeffan-31b-1710 | Google Gemma 4 31B | 69.75 | Quality anchor / candidate teacher |
| KnowLine-26B-A4B-Gen2.5 | Google Gemma 4 26B, 4B active | 64.57 | Lower active-compute quality candidate |
| GLiClass v4 edge | Microsoft DeBERTa-v3-large, about 0.44B total | 31.21 | Optional compact classifier, subject to backbone policy |
| Liquid d1-3B | Liquid LFM2.5-VL-3B | 31.12 | Small open-weight decision candidate |
| Liquid d1-omni-600M | Liquid LFM2.5 encoder | 11.75 | Optional cost-floor experiment |
| Laya | ModernBERT-based encoder, about 0.42B | 9.21 | Permissive task-specific fine-tuning baseline |

**Jeffan-31B is the highest-scoring released candidate whose acceptable backbone
and permissive weight license I verified in this snapshot.** Its pinned metadata
and LICENSE say Apache-2.0. Its native option-token prompt matters, and the
submitted engine conditionally rereads uncertain requests; a generic chat prompt
does not reproduce that system. The card lists an 8K input context while its
vLLM example configures a larger limit, so evaluate long inputs explicitly rather
than assuming the server limit is a validated operating range. It is a quality
reference, not a tiny first-pass model.
([Pinned card](https://huggingface.co/tattrongvu/Jeffan-31b-1710/blob/cebfa86c2bed2b5c83b12e44de4374b20c87fd79/README.md),
[license](https://huggingface.co/tattrongvu/Jeffan-31b-1710/blob/cebfa86c2bed2b5c83b12e44de4374b20c87fd79/LICENSE))

**KnowLine Gen2.5** is Apache-2.0 and uses a merged LoRA on Gemma. It serves the
same weights as Gen2 but repeats short text inputs, at most 3,000 tokens, before
answering. Its calibration temperature is already folded into the weights;
applying it again changes the result. The publisher supplies an SGLang BF16
recipe. About 4B active parameters does not mean only 4B parameters of weight
memory: it still stores the full MoE. Treat the custom request front end as part
of the evaluated configuration.
([Pinned card](https://huggingface.co/PelaAI/KnowLine-26B-A4B-Gen2.5/blob/7486767f8546beb510e61aef07d0e74219ceb4e3/README.md))

**Liquid d1-3B** offers a native decision head over an LFM backbone and generates
probabilities, not explanations. It is open-weight under the custom LFM license,
not an unrestricted Apache/MIT release. Its performance must be tested on our
five-question article screen; the general score is substantially below the
larger Gemma candidates. The smaller omni model is useful only as a cost floor
initially. Its image path truncates state/question text to 896 tokens, which is
unsuitable for silently passing a full article. Hosted **d1-ultra** is a different
product; I found no verified open checkpoint to equate with d1-3B.
([d1-3B](https://huggingface.co/LiquidAI/d1-3B),
[omni](https://huggingface.co/LiquidAI/d1-omni-600M),
[d1-ultra announcement](https://www.liquid.ai/blog/d1-ultra))

**Laya** has permissive metadata and a ModernBERT lineage, but its broad score is
low. It is a possible training baseline, not the preferred zero-shot classifier.
The English root checkpoint has a short default window; long inputs need an
explicit, tested windowing configuration.
([Model card](https://huggingface.co/convaiinnovations/laya))

The exclusion must apply to the actual checkpoint, not its publisher's name:

* Baseten Elephant/Mammoth use Qwen3.5; Perplexity Decider v1.2 uses Qwen3.8-27B;
  Microsoft-Decision-1 uses Qwen3.5-9B. Their methods are informative, but their
  weights do not meet this request's backbone constraint.
  ([Baseten](https://labs.baseten.co/articles/mammoth-1-and-elephant-1-frontier-decision-models),
  [Perplexity](https://huggingface.co/perplexity-ai/pplx-decider-v1.2),
  [Microsoft](https://commandline.microsoft.com/microsoft-decision-1-model-foundry/))
* Jeffan **4B** and KnowLine **4B Gen5.5** are Qwen-based even though the specific
  larger checkpoints above are Gemma-based. Do not substitute family siblings.
  ([Jeffan 4B](https://huggingface.co/tattrongvu/Jeffan-4B-2010),
  [KnowLine 4B](https://huggingface.co/PelaAI/KnowLine-4B-Gen5.5))
* H2O Lightning 31B uses Gemma, but its current license restricts commercial and
  production use, redistribution and certain competing-model training. It is
  not the default teacher for a released student. Amber and Crivo-1B also carry
  noncommercial restrictions.
  ([H2O license](https://huggingface.co/h2oai/h2o-lightning-31b/blob/b6c969e76e37e221aa975c2dae5512e5d34b5735/LICENSE),
  [Amber](https://huggingface.co/MLukauskas/amber-0.4b),
  [Crivo](https://huggingface.co/tardellirs/crivo-1b))

This verifies declared model lineage and published licenses, not institutional
approval of every third-party fine-tuner, dependency or training-data source.
If the local rule also excludes particular publishers or research origins,
apply that narrower allowlist before deployment. No unverified checkpoint is
automatically downloaded by the code added here.

Artificial Analysis measures a different composite: knowledge/hallucination,
multiple-choice reasoning, tool calling and safety classification. None is a
patient-attribution or clinical-table benchmark. Its hosted products and the
open Jev reproduction board should not be merged into one numerical ranking.
([Official methodology](https://artificialanalysis.ai/evaluations/artificial-analysis-decision-index))

## What decision models can extract

A fixed-choice model cannot emit an arbitrary new drug name, number, quote or
free-form rationale through a decision-only interface. It can select among
supplied candidates, including a `none/insufficient_evidence` alternative. One
API request with many questions may still involve a forward pass per question;
that is adapter-dependent, not automatically constant cost.
([vLLM structured decisions](https://docs.vllm.ai/en/latest/models/structured_decisions_models/))

Three useful approaches are distinct:

1. **Choose evidence IDs.** Supply candidate sentences, paragraphs or table cells
   with stable IDs. Ask which candidates support the metadata label or proposed
   fact. Code copies the chosen source text and offsets. For multiple supporting
   spans, classify each candidate or use bounded iterative selection; do not
   force all evidence into a single exclusive answer. A literal quote proves
   provenance, not entailment or correct patient attribution.
2. **Use a span extractor.** GLiNER2.5's boundary head already returns start/end
   character offsets and supports entities, records, relations and classification.
   The 194M base model is Apache-2.0 and DeBERTa-based, not Qwen-based. It is an
   attractive candidate for names, doses and numeric mentions if that backbone
   is permitted. Long-document helpers remap overlapping chunks to document
   offsets, but relations require both endpoints in the same chunk. Missing
   cross-chunk relations must not be mistaken for absent relationships.
   ([Official card and span API](https://huggingface.co/fastino/gliner2.5-base-v1))
3. **Train a dedicated encoder head.** An allowed ModernBERT/LFM encoder can be
   trained for BIO labels or start/end spans plus patient/time/negation attributes.
   This requires labeled spans and an extraction objective; it is not a feature
   obtained merely by changing a Jev JSON schema.

GLiClass v4 edge is a separate small instruction-conditioned classifier. It can
score labels in one pass, but its raw multilabel scores are not interchangeable
with a normalized exclusive-choice distribution. Use the appropriate native
readout and calibrate it on our corpus.
([Official card](https://huggingface.co/knowledgator/gliclass-v4-edge))

The useful hybrid is: **parser/span model proposes literal candidates → decision
model classifies and assigns them → larger model resolves ambiguity → validators
check evidence, identity and chronology**. A second use is cheap task routing:
shared patient tables trigger column-aware extraction; long timelines trigger
smaller event batches; unsupported figure ownership triggers review. These are
proposed experiments, not automatic policies enabled in this patch.

A decision model could also judge candidate/reference pairs using fixed labels
such as equivalent, contradictory, related-but-distinct, unsupported and unknown.
Give it the source and patient context, hide extractor identity/reasoning, and
calibrate against human judgments. Keep this separate from the current item
score: uncertain answers are not matches, and a source-supported fact missing
from the reference may be an annotation gap rather than a false positive.
See [semantic benchmark design](SEMANTIC_BENCHMARK.md).

## Closed-vocabulary article schema

The executable vocabulary is
[`article_profile_v1.yaml`](../src/openpatients2/rules/article_profile_v1.yaml).
It distinguishes publication form from study design, clinical subjects from
specimens, and individual cases from aggregate enrollment. Every question has
`insufficient_evidence` and `not_applicable`; applicable vocabularies have `other`.
Missing information is never converted into `no`.

| Group | Contents / examples |
|---|---|
| Publication form | Original research, clinical case publication, review, protocol, guideline, correspondence, resource, correction |
| Study design | Case report/series, prospective/retrospective cohort, case-control, cross-sectional, randomized/nonrandomized intervention, diagnostic accuracy, review types, preclinical, qualitative, mixed |
| Original patient cases | Whether original individually described cases are present, separately from cited literature cases |
| Individual case count | None, one, 2–4, 5–9, 10–24, 25–99, 100+; not the aggregate cohort total |
| Study population size | Separate enrollment/population bands; unresolved denominators remain unknown |
| Data granularity | Individual longitudinal, individual single-time, aggregate, mixed, no clinical data |
| Subject types | Human, veterinary, experimental animal, human/nonhuman specimens, in-vitro, synthetic/computational |
| Specialties | 32 multi-select categories including hematology, oncology, pediatrics, radiology, pathology and veterinary medicine |
| Subspecialties | 20 multi-select categories including pediatric hematology/oncology, thrombosis, stroke, transplantation and maternal-fetal medicine |
| Age groups | Prenatal, neonate, infant, child, adolescent, adult, older adult; human subjects only |
| Care settings | Inpatient, outpatient, emergency, ICU, operating room, community/home, long-term, veterinary clinic |
| Clinical content | Demographics, symptoms, diagnoses, laboratory values, doses, procedures, adverse events, outcomes, genetics, serial measures, figure ownership |
| Temporal detail | Calendar dates, numeric relative intervals, qualitative ordering, no temporal detail |
| Extraction risks | Multiple subjects, cited-case mixing, shared tables, composite figures, uncertain ownership, ambiguous time anchors, history/current, plans/performed, negation, contradictions, supplements, damaged layout |

Specialties are not exclusive. The pediatric thrombosis paper can be pediatric,
hematologic, oncologic and vascular at once. A veterinary case is not a pediatric
human because its animal subject is young. A review containing one original case
must not be excluded solely because its primary publication form is a review.

The full catalog expands to **105 decisions**. Start with only five questions:
publication form, study design, original cases, case-count band and data
granularity. Expand specialties/content/risk fields only where useful. Exact
patient counts are a later extraction field: propose literal count candidates
with their scope and evidence, then adjudicate whether they count enrolled,
analyzed, individually described or cited subjects. Do not label a chosen band
as an exact count.

Keep deterministic metadata outside model inference: PMID/PMCID/DOI, publication
date, language, journal, NLM publication types, MeSH, rights/full-text availability,
parsed figure/table counts and article-version hashes. Retain NLM and predicted
labels separately with provenance; the proposed specialty vocabulary supplements
MeSH rather than replacing it.
([NLM MeSH downloads](https://www.nlm.nih.gov/databases/download/mesh.html))

Each model decision stores the full option distribution, selected label, exact
source spans, model/serving revision, template and source hashes, input scope,
elapsed time, tokens and calibration ID. Span offsets are half-open Unicode
character positions within the cited source segment. The validator rejects
invented labels, missing decisions, stale hashes, malformed distributions and
nonliteral spans. It intentionally never claims semantic support was verified.

### Implemented preparation interface

```bash
.venv/bin/python scripts/prepare_article_profile.py \
  --source benchmarks/dense/articles/PMC12134816.1/source.json \
  --scope title_abstract \
  --fields publication_form study_design original_patient_cases individual_case_count data_granularity \
  --out runs/article-profile-example
```

This writes a strict result JSON Schema, source-only questions and stable
evidence candidates. It makes **zero model calls**. It also supports normalized
JSONL/JSONL.gz with `--article-id`. The source allowlist excludes reference
rosters, gold specialty labels and extracted clinical answers. Multilabel fields
become independent four-way decisions. All native inference adapters, windowing,
calibration and policy thresholds still need implementation and evaluation.

## Benchmark before fine-tuning

Use the frozen 316 train / 105 validation / 105 test article split, with the test
set kept sealed. The existing corpus is enriched for cases, so add a separately
sampled PubMed negative/control set: trials, reviews, protocols, aggregate-only
cohorts, basic science, animal experiments, corrections, missing abstracts and
non-English records. Otherwise a filter can look excellent while failing on the
actual PubMed distribution. Group duplicate DOI/PMC versions and related case
publications before splitting; prevent source, patient and teacher-label leakage.

Start with a few hundred diverse, manually reviewed development documents and a
small field set. Label supporting spans as well as categories. Review all model
disagreements plus random confident positives and negatives. Report per-field
macro/micro F1, original-case recall, confusion matrices, calibration/Brier score,
abstention/coverage, evidence entailment, patient attribution, and performance
by source length, language, subject type and study design. Report false-negative
filtering separately: a missed article disappears from every downstream task.

Measure **complete documents/second at the chosen schema**, decisions/second,
input tokens/second, GPU-hours, host RAM, p50/p95 latency and failed/truncated
requests. Measure warm and cold runs and preserve the exact question order and
native adapter. Prefix caching helps repeated states but does not make 105
questions free. Truncation must produce a coverage warning and escalation, not
confidently claim the full article was inspected.

## What to borrow from Baseten and Perplexity

Baseten describes successive rounds of failure-targeted data improvement,
decontamination, abstention training and a cross-entropy plus Brier objective.
Its final runs use supervised full fine-tuning; the lesson for this task is to
improve representative labels and calibrated uncertainty before adding an RL
pipeline. We do not need to reproduce its multi-million-example scale to test
a narrow article-profile task.
([Technical report](https://labs.baseten.co/articles/mammoth-1-and-elephant-1-frontier-decision-models))

Perplexity's newer Decider combines broader held-out/domain evaluation with
training-data cleaning and checkpoint weight averaging. Its joint-question
architecture can make one question affect another, so question grouping is part
of the experimental configuration. Noncausal attention/readout changes also
require compatible serving code. Borrow the evaluation discipline and targeted
data design; do not assume an arbitrary Gemma/LFM adapter inherits those
architectural benefits.
([v1.2 model report](https://huggingface.co/perplexity-ai/pplx-decider-v1.2),
[v1.1 report](https://huggingface.co/perplexity-ai/pplx-decider-v1.1-27b))

Our first training experiment should be native supervised head tuning or LoRA
on an allowed backbone, followed by held-out calibration. Include hard negatives
(cited cases, aggregate enrollment versus individual cases, planned procedures,
missing cells and cross-patient tables), unknown examples, and randomized label
order. Split model-selection and calibration data or use nested validation.
Fine-tune evidence selection/span heads separately from document metadata, so
good labels cannot conceal poor citations.

Teacher reasoning from Spark/Beam can help reviewers identify errors, but it is
not an automatic gold label. Keep source evidence and reviewed outcomes as the
training target. Distill soft decision distributions or selected spans into the
small classifier; a non-generative classifier need not emit chain-of-thought.
Check the selected teacher/student licenses before releasing derived weights.

## PubMed-wide release path

Begin with bulk PubMed XML ingestion and deterministic metadata, then the
five-question title/abstract screen, then targeted full-text profiling where
licensed PMC text is available, and finally expensive extraction on selected or
uncertain articles. Missing abstracts and uncertain classifications should remain
discoverable. PubMed is a citation/abstract database; it does not provide every
article's full text. Use its baseline and ordered update files, including revised
and deleted records, rather than millions of individual requests.
([Official download guidance](https://pubmed.ncbi.nlm.nih.gov/download/))

Do not promise a quick global pass from a leaderboard's millisecond latency.
For an illustrative **40 million** records, 10 completed documents/s is 46.3 days
on one worker; 100/s is 4.63 days; 1,000/s is 11.1 hours. Those are arithmetic
scenarios, not measured capacity. The actual number of decisions, source lengths,
tokenization, I/O and repeated reads determine throughput.

A useful first release is a versioned metadata index: source IDs/hashes,
provenance, closed labels, calibrated uncertainty, supporting spans and explicit
coverage. Release only source text permitted by the relevant rights; otherwise
publish source identifiers and offsets with derived metadata. Keep this index
independent from later clinical extraction so users can apply their own filters
and update classifications without redownloading model-generated patient records.
