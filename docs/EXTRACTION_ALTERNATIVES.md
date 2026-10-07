# Alternatives for improving clinical fidelity

Date: 2026-10-07. **Design proposal; no runtime defaults changed.**

A bounded subset is now implemented as the opt-in
[clinical architecture campaign](CLINICAL_ARCHITECTURE_CAMPAIGN.md). That document
distinguishes executable experiments from the remaining proposed work. Production
defaults are unchanged; local tests do not establish clinical gains.

The next substantial experiment should change the unit of extraction and the
verification contract. Repeatedly asking for full domain objects, then asking a
critic to repair them, has not reliably improved our delivered clinical facts.
This proposal extends the existing source-first design with independently tested
verification, source coverage accounting, and encounter-scoped state changes.

## What the latest evidence actually says

The [controlled campaign](../reports/CONTROLLED_COMPONENTS_20261007.md) found:

- Baseline: 473/518 repeated required checks; attribute audit: 471/518. The audit
  changed 204 fields/list entries, including 146 replacements with null/unknown.
  Those counts do not establish an improvement in precision.
- Every episode-rebuild call passed protocol validation, yet ordering checks fell
  from 36/82 to 15/82. Valid output is an inadequate optimization target.
- A CPU-only table citation repair recovered seven checks without changing model
  medical values. Some failures belong to our interface, not model reasoning.

These are development results, not comprehensive medical accuracy. Existing
reports already recommend smaller edit operations and encounter grouping. The
additional experiments below aim to test whether those mechanisms actually
preserve meaning, and whether we can catch information missing from all outputs.

## Proposed flow

```text
                     SOURCE + STABLE REFERENCES
                                 |
                 +---------------+----------------+
                 |                                |
          SOURCE-FIRST READING             PATIENT-FIRST READING
          clauses / table cells /           encounters, history,
          figure panels                     treatments, outcomes
          with explicit dispositions        and questions to resolve
                 |                                |
                 +------------+-------------------+
                              |
                  CANDIDATES + COVERAGE GAPS
                              |
                  CPU: exact-reference checks
                              |
              +---------------+----------------+
              |                                |
      directly supported                ambiguous / conflicting /
      unambiguous fields                context-dependent fields
              |                                |
              |                     INDEPENDENT SOURCE QUESTIONS
              |                     patient? encounter? assertion?
              |                     what actually happened?
              |                                |
              |                       reconcile evidence
              +---------------+----------------+
                              |
                 ACCEPTED FACTS + UNRESOLVED CANDIDATES
                              |
                  ENCOUNTERS / STATE TRANSITIONS
                              |
                 clinical sections + relative timeline
                 + summaries + patient/figure bundles
```

Direct support still needs semantic evaluation; a literal quote alone is not
proof. Keep source parsing deterministic where possible. The two readings are
separate requests with different tasks, not independent statistical witnesses.
Initially run the second reading only on the benchmark, coverage gaps and a
random sample; measure its extra recall before paying for it everywhere.

## 1. Account for the source, not just the output

An output audit cannot find a lab value neither extractor nor critic noticed.
Inventory source units before final patient filtering: clauses, table cells and
figure panels. Give every unit a disposition: represented by fact IDs, duplicate
mention, background/aggregate, other patient, unresolved, or not clinically
relevant. Retain the reason and evidence for exclusions.

The complementary patient-first pass asks clinical questions such as which
treatments were attempted, what changed afterward, and which findings were only
historical. It independently retrieves evidence; it does not see the first
reader's answers. Reconcile findings and identify unanswered questions.

This is not a claim to extract every fact merely because every sentence has a
label. A sentence can contain several facts, and an incorrect exclusion hides
recall loss. Review both represented and excluded units, with random sampling
alongside targeted difficult cases. Preserve ambiguous ownership so an incorrect
initial roster cannot permanently remove a patient from consideration.

## 2. Verify by answering independent questions

Replace a generic instruction to find mistakes with narrow evidence questions.
For a proposed observation, separately resolve patient, encounter, analyte,
magnitude/comparator, unit, and assertion. For procedures, distinguish planned,
started, completed, aborted and outcome; absence of documentation is separate
from a documented negative.

First obtain source-based answers without exposing the proposed field values or
the extractor's rationale. A later reconciliation step compares them with the
candidate. If local evidence is insufficient, expand to the containing section,
table headers, cross-references and then the bounded full article. Record which
context was examined. An unsupported result from a small excerpt is not grounds
for deleting the original candidate.

For hard disagreements, compare explicit alternatives, including unresolved:
patient 1 vs patient 2, admission vs 48-hour result, planned vs performed. These
alternatives are hypotheses, never additions to the source record. Do not accept
majority agreement as proof; the same model can repeat the same mistake.

Use support labels: explicit, contextually linked, contradicted, unresolved.
Contextual claims require the connecting source references. For the left renal
mass followed by nephrectomy example, retain the contextual link and its status
rather than pretending the operation sentence explicitly states laterality or
automatically erasing the side.

Independent question answering is inspired by
[Chain-of-Verification](https://aclanthology.org/2024.findings-acl.212/), which
reported benefits on its QA and generation tasks. Our source-grounded clinical
adaptation is a hypothesis requiring its own evaluation. Research on
[intrinsic self-correction](https://arxiv.org/abs/2310.01798) also cautions against
assuming another self-review pass will improve an answer without useful feedback.

## 3. Build encounters and state changes before final schemas

Try extracting small records of who, what, when relative to an encounter, and
what source supports that association. Construct clinical domains from these
records after resolving repeated mentions. Abstract and body descriptions of the
same surgery should share an encounter, while distinct actions within that
operation remain separate.

Use separate levels: source mention -> clinical fact/action -> encounter ->
relative-order relation. Shared encounter membership must not merge different
tests or assign every action the exact same time. Keep uncertain coreference
explicit; ordering should be a partial graph, not a forced total sequence.

Store changes rather than overwrite history. For example, six months of exclusive
breastfeeding followed by addition of formula supports an earlier exclusive
feeding state and a later change. It does not establish that all breastfeeding
stopped. Likewise, treatment recommendation, treatment initiation, treatment
failure and treatment cessation are different events.

Derived patient-state views must preserve unknown intervals. Do not assume a
diagnosis or medication remains active indefinitely, or infer causal treatment
effects merely because improvement follows administration. Keep author-stated
causation distinct from temporal association.

## 4. Test the gates with known corruptions before trusting them

Create a reviewed fact set and mutation tests with explicit expected outcomes:

| Test | Expected behavior |
| --- | --- |
| Change patient 1's fact to patient 2 | Reject wrong attribution or retain unresolved if genuinely ambiguous |
| Replace admission sodium with the 48-hour value | Detect encounter mismatch |
| Change recommended treatment to completed treatment | Detect unsupported execution |
| Flip a documented negative to positive | Detect polarity change |
| Change magnitude, exponent, comparator or unit | Detect the numerical/semantic mismatch |
| Replace a figure's modality with an unrelated one | Detect conflict where pixels/caption establish modality |
| Shuffle table columns together with their headers | Preserve patient-value associations |
| Replace a source heading while keeping its stable ID | Preserve the same grounded facts |
| Remove decisive evidence | Abstain unless another retained source independently supports the fact |

Use both corrupted and unmodified correct records. A gate that rejects everything
scores well on corruption detection alone and is useless for extraction.
Measure false acceptance, false rejection, abstention and retrieval rescue by
error class. Some mutations can accidentally remain true; validate fixtures
before scoring. Synthetic tests supplement held-out natural cases, not replace
them. This adapts the behavioral testing approach of
[CheckList](https://aclanthology.org/2020.acl-main.442/).

## 5. Give code responsibility for exact fields

For unambiguous cells, the model chooses cell IDs and semantic interpretations;
code attaches source IDs, quotes and parsed numbers/units. Never ask the model to
retype a long source row simply to pass a validator. Uncertain parses retain the
original string and do not acquire invented numeric values.

Apply repairs as operations on identified facts/attributes with preconditions.
Keep the original candidate and all decisions. Separate accepted export from
unresolved/quarantined candidates; an accepted view must not silently re-import
rejected claims through summaries. Summaries link to accepted fact IDs; uncertainty
can be described explicitly with its own provenance. Produce updates only for
affected summaries/encounters instead of regenerating the entire patient.

For figures, keep pixel observations, caption claims and article interpretations
separate. Ownership is another question: a clinical-looking image is not itself
evidence that it belongs to a particular patient. Preserve panel IDs, shared
ownership, aggregate figures and unresolved assignments.

## A bounded experiment that can change our decision

1. **CPU replay first.** Fix the known table citation contract, regression-test
   the saved responses, and establish that baseline-plus-fix as the shared
   comparator. Do not credit this known repair to the new architecture.
2. **Gate qualification.** Before new extraction campaigns, test independent
   verification on reviewed true facts and corruptions. Include contextual truths
   that the old critic erased. Reject a gate that gains error detection primarily
   by rejecting correct facts. Select thresholds on development data only.
3. **Small paired pilot.** Run the comparator, comparator plus qualified verifier,
   and encounter-first extraction plus verifier on the same sources/seeds. Add
   the second reading as a separate ablation to measure marginal missing-fact
   recovery. Include multi-patient tables, historical/negative findings, failed
   procedures and multi-panel images; balance easier natural cases too.
4. **Blind source review.** Annotate source facts before viewing arm outputs.
   Review both accepted and excluded/unresolved samples. Preserve an untouched
   article-family holdout; the repeatedly examined 31 articles are development
   material. Human-reviewed reference data are needed before claiming clinical
   precision, especially for image interpretation and contextual inference.
5. **Promotion by separate metrics.** Supported fact precision/recall, wrong
   patient/encounter rate, state-query correctness, event and edge recall,
   incorrect edges, visual grounding and cost per supported delivered fact.
   Do not use the current composite score as the sole winner selector. Report
   paired article-level uncertainty and adjudication coverage.

Patient-state questions provide a downstream check: what was known on admission;
what intervention was actually attempted; which findings belong to follow-up;
what remains unknown? Evaluate the answers against separately reviewed source
answers, not an LLM reconstruction of its own bundle.

Use GEPA for narrowly scored extraction and verification prompts once these
references and interfaces are stable. Freeze train/development/test article-family
splits, prohibit held-out evidence in optimization feedback, and track every
prompt version. Optimize supported recovery subject to false-acceptance and
correct-fact-retention constraints. Do not reward verbosity, raw item counts or
schema validity as substitutes for fidelity; do not let the same unreviewed
model judgments define both training reward and claimed test accuracy.

CPU workers handle inventory, retrieval indexes, deterministic validation and
scoring. Independent TP1 GPU workers process ready inference requests across
articles; dependencies exist within an article, not between all articles.
Batch short verification requests from multiple articles to avoid serial idle
time. Use bounded retrieval/repair rounds and stop on repeated decisions; no
open-ended agent loop or automatic promotion. We should measure this pilot's
cost before budgeting another overnight sweep.
