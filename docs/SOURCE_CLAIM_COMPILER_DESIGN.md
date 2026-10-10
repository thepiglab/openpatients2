# Compile patient records from source claims

Proposal, 2026-10-07. **Not implemented or benchmarked.** No production default
changes. Motivated by the [architecture results](../reports/CLINICAL_ARCHITECTURE_20261007.md).

The experimental coverage pass contains more required clinical information than
the baseline before verification, but its gate removes enough to reverse the
gain. The next prototype changes the unit of generation and acceptance. Keep
existing public schemas; generate them with code from a smaller intermediate
representation instead of asking an LLM to repeatedly write complete sections.

## What changes relative to earlier experiments

| Current architecture experiment | Proposed prototype |
| --- | --- |
| Rich encounter inventories followed by full domain JSON | Small source-span selections and relation decisions |
| Claim identity inferred from name or generic domain | Stable claim and occurrence IDs supplied with explicit targets |
| One doubtful field can exclude an entire object | Decisions attached to individual fields/relations; critical identity conflicts block only the dependent claim |
| Free generation of IDs, quotes, quantities and timeline objects | CPU assigns IDs and copies values/evidence; model handles semantic relationships |
| Regenerate a timeline after facts change | Update affected event links; compile a partial order |
| Per-shard synthetic controls authorize deletion | Fixed experimental policy calibrated on reviewed natural cases; controls remain regressions |
| Review count, schema validity and repeated checklist | Unique supported claims, false claims, state queries, event identity and relation quality |

Earlier span experiments are relevant evidence, not an untested idea to rename:
they still projected spans into large rich task objects and sometimes lost
unmarked source facts. This design must demonstrate that removing those
generation contracts actually reduces losses. The old pipeline remains a
measured comparator until that is established.

## Intermediate objects and dependencies

Keep separately addressable tables/collections:

1. `source_units`: article/hash, clause or cell/panel ID, literal text, source
   offsets, neighboring units, table headers, caption/pixel provenance and rights.
2. `mentions`: selected source spans, raw concept/value/time expressions and
   tentative domain. No fabricated normalized fields are required for admission
   to the candidate store. A proposed span must resolve exactly to source.
3. `claims`: refer to mentions and distinguish the reported test/action/finding.
   Each field stores its own support and dependency references. Source assertions
   remain distinct from a reconciled patient-level interpretation.
4. `relations`: owner, specimen, value-of, result-of, time-of, same-occurrence,
   different-occurrence, before/after/overlap, and explicit source conflict.
   Store the original candidate, evidence, decision and version for each link.
5. `patient_views`: deterministic projection into current clinical schemas,
   timeline, measurements and media bundles; unresolved links remain inspectable.

Separate a lab test occurring from its result being negative. Separate an action
being recommended, initiated, completed, aborted or not performed. Retain earlier
states after later changes. Do not force a single scalar clinical truth when the
publication contradicts itself.

Examples from this run:

- The 2024 150 mCi RAI administration and later rejection of empirical RAI are
  different occurrences. A verifier must not receive only the topic “RAI.”
- A documented negative pulmonary embolism result does not imply the CT exam was
  absent. The occurrence and result polarity belong to different fields.
- Both negative Ki67 prose and occasional labeling in a caption remain source
  claims; resolved interpretation stays uncertain until adjudicated.

## Inference and gating contract

Use ordinary compact responses with the existing tolerant parser; this proposal
does not require JSON-schema-constrained decoding. Failures are local and bounded.

**Enumeration.** CPU makes clause/cell IDs without judging clinical relevance.
Use patient-focused and source-order readings as complementary candidate
generators. Every unit has a disposition, but a missing disposition flags that
unit for retry rather than invalidating all other selections. A disposition is
coverage bookkeeping, not proof that all clinical facts were extracted.

**Discovery.** Enumerate candidate patient identity mentions independently from
source classification, figure assignment and comprehensive roster metadata.
Resolve coreference with nearby and necessary full-article context. No missing
schema field is allowed to turn a clearly described patient into “no patient.”
Ambiguous ownership remains unattached rather than assigned by position.

**Compilation.** Code attaches canonical source IDs, literal values and units.
Use exact decimal arithmetic and preserve comparators/scientific notation and
original strings. Unit conversion, inferred normality, or clinical interpretation
is a separate operation requiring an explicit contract. A literal number match
does not by itself ground the patient/test/time relation.

**Relationship decisions.** Give each decision an explicit target and alternatives,
such as which of two documented RAI occurrences a sentence refers to. Show the
original supporting context. Allow another occurrence, unresolved, and expansion
to additional source context. Use short batches with per-item result salvage;
never equate an unavailable batch with six rejected facts. Independent questions
must retain enough identity to be about the same claim, even when hiding the
candidate answer value.

**Acceptance.** Separate deterministic contract failures, unsupported clinical
attributes, missing information, source discrepancies and normalized type labels.
An unresolved optional date does not remove a supported magnitude or treatment.
An unresolved owner blocks attaching that claim to a patient. A contradictory
magnitude cannot be exported as a supported numeric result. Record unresolved
candidates separately; summaries must not reintroduce them as certain facts.
Model confidence/agreement only prioritizes review until calibrated on labels.

**Temporal assembly.** First resolve repeated mentions of the same occurrence.
Then classify a bounded set of plausible event pairs/anchors using source evidence.
Use a CPU graph pass for identity constraints, reference integrity, cycle detection
and transitive order. It must report inconsistency rather than invent a tie-break.
Dates are optional. A non-total order is an acceptable output, and source sentence
order alone cannot be its evidence. Keep planned and executed actions distinct.

**Incremental updates.** A local correction invalidates only dependent links and
views. Source claim IDs remain stable. Do not erase an oncology domain because a
tumor edge is unresolved; retain unattached biomarker/treatment source claims and
materialize only validated links. Summaries are generated after the accepted
views stabilize and cite the facts they summarize.

**Figures.** Preserve caption assertions, pixel observations and article
interpretations separately. Patient and panel ownership is another relation.
The prototype must distinguish individual, shared, aggregate, external and
unresolved subjects. Keep modality/chart labels and descriptions, but do not
interpret a pixel-derived number as a clinically measured value without an
appropriate grounding check. The current run supplies no new visual accuracy
evidence because experimental arms reused baseline media outputs.

## Experiment before a broader rewrite

1. Implement the intermediate compiler and run it on reviewed source claims.
   Replay saved table and gate failures on CPU. This diagnoses representation
   loss independently of the model; gold-fed results never count as deployed
   extraction performance.
2. Implement a complete small slice: observations, medications, procedures,
   patient identity and relative ordering. Score all relevant claims in that
   slice; report other domains as out of scope, not “fully extracted.”
3. Add approximately 12 new bounded articles. Annotate source claims, critical
   relationships and patient-state questions before examining predictions.
   Split by article family, with an untouched holdout. Include normal/negative
   findings, history, failed treatments, multi-patient tables and source conflicts.
4. Compare frozen baseline, advisory-only complementary extraction, and compiled
   relationships under comparable source and inference budgets. Use two seeds;
   report paired article-level outcomes and both false acceptance and omission.
5. Include a small identical-prompt stronger-model reference arm only when access
   and budget are arranged. Its outputs also need source review. This separates
   model capability from representation failures and tests whether selective
   escalation is worth the cost.
6. Optimize compact decision prompts with GEPA only after scoring labels exist.
   Train-only reflection, validation selection, frozen holdout. Optimize supported
   recovery with constraints on false claims and wrong patient/occurrence links.
   A self-judge verdict is not the reference label.

Use four independent TP1 B200 workers with CPU preprocessing/scoring/cleanup and
inference-only GPU queues. Run the diagnostic subset first and stop clearly failed
arms. The latest run needed 52–62 minutes per worker; that is a planning reference,
not a promise for a new workload. Compare GPU-seconds per supported unique claim
and state-query accuracy, not tokens/second or the number of emitted facts alone.

Promotion requires new-source gains with reviewed precision, retained supported
facts, improved occurrence/order accuracy and an acceptable cost. More candidates
or valid JSON alone is insufficient. This protocol is intentionally capable of
rejecting the proposed architecture.
