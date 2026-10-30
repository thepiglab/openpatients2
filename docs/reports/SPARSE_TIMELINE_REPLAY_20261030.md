# Sparse timeline retrieval replay — 2026-10-30

The new `timeline_sparse_relations` mode replaces the full unordered-pair grid
with deterministic candidate retrieval. This replay uses the 12-article
run-20261029-174936 archive, its source packets, extracted event registries and
frozen reference annotations. It makes no inference calls and does not alter
archived outputs or reference labels.

The archived prediction has 2,440 events across 51 patient timelines and 95,980
possible unordered within-patient pairs. Existing event/identity matching aligns
both endpoints of 278 reference temporal edges. The denominator excludes reference
edges without aligned extracted endpoints. References and alignments are imperfect;
this is candidate-retrieval coverage, not clinical recall or semantic correctness.

| Source-neighbor width | Selected pairs | Aligned reference edges selected | Candidate coverage |
|---:|---:|---:|---:|
| 2 | 18,018 | 181 / 278 | 65.11% |
| 4 | 20,989 | 196 / 278 | 70.50% |
| 8 | 27,295 | 214 / 278 | 76.98% |
| 12 | 33,512 | 222 / 278 | 79.86% |
| 16 (default) | 39,294 | 225 / 278 | 80.94% |
| 24 | 49,273 | 238 / 278 | 85.61% |

All rows include clinical-event anchors, exact source/calendar/episode anchor
chains and literal relative-time phrase bridges, with a hard budget of 32*N
unique pairs per patient. The default removes 59.06% of the old all-pairs grid,
while retaining 80.94% of the aligned reference candidates. This is a known
coverage tradeoff, not proof that the new pipeline preserves or improves recall.
An earlier source-neighbor-only prototype selected just 105/278 edges and was
rejected as the default. Broader clinical bridges protect links obscured by many
intervening laboratory events.

The opt-in configuration is:

```json
{
  "timeline_bounded_context": true,
  "timeline_sparse_relations": true,
  "timeline_sparse_neighbors": 16,
  "timeline_sparse_pairs_per_event": 32
}
```

The event prompt additionally asks for distinct clinical occurrences/timepoints,
grouping co-reported measurements only when the source establishes the same
occurrence. This can reduce the event count further, but that effect is untested
until a new Beam run. Source-backed clinical facts and measurement cells remain
separate outputs. Code never semantically merges two visits based on similar
names, values or inferred dates.

## Audit and validity behavior

- Exact content deduplication preserves every original task/batch/event ID in
  `event_canonicalization`; different evidence, times, occurrence or fact links
  keep events distinct. Original task responses remain unchanged.
- `relation_candidate_audit` records event/source hashes, parameters, each
  considered pair and retrieval reasons, ambiguity-limited bridge matches,
  budget exclusions, total and unassessed pair counts, and leaf request coverage.
  Unassessed pairs are not negative relation labels.
- Shared anchors and source neighbors select requests only. They never establish
  chronology, equality, patient attribution or a relation. Either edge direction
  remains legal. The model must cite primary source evidence for each relation.
- Sparse batches contain at most 16 candidate pairs and split further on context
  or output-length failures. Each planned pair belongs to exactly one leaf request.
- Existing evidence, patient, temporal-cycle and offset validators stay enforced.
  Global contradictions remain quarantined with their original candidates; no
  model-generated or deterministic winning chronology is silently substituted.
- `global_graph_audit` reports locally accepted, retained and quarantined edge
  counts. The existing `partial` status remains when graph assembly or a leaf
  fails. Semantic coverage is always unverified.

## Validation and promotion

Unit/integration tests cover 1,000-event bounded complexity, deterministic replay,
remote calendar/relative anchors, ambiguous repeated treatments, cross-patient
rejection, conservative event identity, context splitting, reverse-direction
relations, saved reasoning and cyclic-graph quarantine. Legacy timeline tests
exercise unchanged all-pairs behavior when the new flag is absent.

For the new 50-article campaign, report candidate coverage and semantic reference
agreement alongside task validity, tokens and runtime. Sample unassessed pairs
and discarded edges for source review. Use a larger neighborhood or targeted
second pass when required recall is not met; do not relabel missed edges as
unsupported merely because sparse retrieval did not select them.
