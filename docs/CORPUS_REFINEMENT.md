# Source-aware refinement campaign

This follows the source audit in `reports/CORPUS_CORRECTNESS_20261003.md`.
It tests corrections to validation losses, relative chronology, observation
notation, patient discovery and figure interpretation. The experiment does not
establish comprehensive medical accuracy or select a production default.

## Transfer and run

Changes are packaged locally; a Git pull alone will not obtain unpublished
edits. From your Mac:

```bash
scp /Users/mkieffer/programming/ToadResearch/openpatients2/dist/openpatients2-refinement-campaign.tar.gz \
  wkieffer@hpg.rc.ufl.edu:/blue/cai6734/ehr_agent/
```

On HiPerGator, extract into the repository before submitting. Do not replace
code while an older campaign using that checkout is active.

```bash
cd /blue/cai6734/ehr_agent/openpatients2
tar -xzf ../openpatients2-refinement-campaign.tar.gz

UV_LINK_MODE=copy uv run --locked --python 3.12 --no-dev op2 corpus-pilot submit \
  --config configs/pilot/refinement.yaml \
  --work-dir "/blue/cai6734/ehr_agent/op2-corpus-refinement/run-$(date +%Y%m%d-%H%M%S)"
```

This single submission schedules CPU preparation → container setup → checkpoint
download → GPU evaluation → checkpoint cleanup → report → article cleanup.
Only evaluation reserves GPUs: eight B200s on one node, eight FP8 TP1/DFlash
replicas, medium reasoning, T=1/top_p=.95/top_k=64, a 65,536-token context and
32,768-token prefill budget. Output allowances remain 16,384 tokens per call,
with up to two repairs. No BF16 verifier is downloaded. The batch job has a
12-hour time limit; a timeout is an incomplete trial, not successful evaluation.

## Comparisons

The same twenty canonical articles and seventeen reviewed patients are retained.
Seeds 42–45 each run these four arms, rotated so each occupies each execution
position once:

| Arm | Input | Repair | Patient roster |
| --- | --- | --- | --- |
| whole-legacy | Whole article | Legacy control | Reviewed, frozen |
| compact-legacy | Patient blocks + unresolved blocks, tables, captions | Legacy control | Reviewed, frozen |
| compact-refined | Same compact input | Source-aware corrections | Reviewed, frozen |
| compact-live | Same compact policy | Source-aware corrections | Generated live |

Independent refined discovery also runs once per seed. The live arm includes
discovery errors and has a different patient denominator; matching patient IDs
or counts alone does not prove matching identities. Legacy controls switch off
the new repair and prompt policy, but share current schema infrastructure and
figure-attribution instructions: they are not byte-identical historical replays.

Each extraction trial records a prefix-cache reset attempt and equal, bounded
warmup on every replica. If any reset fails, throughput is marked uncontrolled
in that receipt; execution-order balance alone cannot remove every speed
confound. These runs primarily compare extraction quality, not saturated serving
capacity. Generated token rates include reasoning and answer tokens.

A CPU-only prospective sample uses ten medical strata with case-focused and
broader lanes, up to 120 candidates from 2023. Acquisition is capped at 384 MB
network transfer and 512 MB stored source data; at most 24 eligible articles are
selected for inference. Existing license and asset-rights gates remain active.
Any PMCID already in the regression corpus is excluded, across article versions.
Legacy and source-aware live extraction each run with seeds 42 and 43, reversing
order. These sources have **no accuracy gold** until independent source review.
There is no locally downloaded model or prospective source corpus.

## Corrections and fields

Unbound fact time associations can become unknown while the original association
is retained in the audit. Values, units and specimens remain unchanged. Explicit
routes such as `IV` can populate route fields only from bound literal text. Item
repair rotates its bounded batches so later failed items get a turn. Accepted
neighbors remain frozen, and changed populated numbers, doses, units or specimens
require adjudication. Full-object repair protects individually gated graph/panel
atoms; metadata or an invalid edge need not be protected as a valid fact.

Failed timeline times, registry links, events and edges are quarantined separately.
A partial graph may retain source-gated events without claiming completeness.
Wrong-patient events never become accepted by deleting attribution evidence. If
the combined graph contradicts itself, its ordering edges are withheld for review.

`relative_timeline` in each patient bundle is a partial ordered clinical course:
history, presentation, investigations, treatments, adverse events, changes and
follow-up can have before/after relations with no duration or dates. Relations
must be supported by the case; event kinds and paragraph order do not determine
chronology. `before_pairs` includes transitive ordering, `same_time_groups` uses
explicit equality only, and `incomparable_pairs` preserves unknown order.
`topological_layers` is a rendering aid, **not simultaneous visits**. Events retain
occurred/planned/conditional status and links to the clinical fact registry.

Observations already have separate LLM `numeric_value`, `unit`, `comparator`,
`text_value` and `reference_range_text` fields. The refined prompt emphasizes
those fields. Every bundle now adds an `observation_measurements` sidecar:

```json
{
  "raw_text": "17.9×10⁹/L",
  "comparator": "=",
  "mantissa": "17.9",
  "power_of_ten": 9,
  "magnitude": "17900000000.0",
  "unit": "/L",
  "notation": "scientific"
}
```

Magnitude is an exact decimal string, avoiding float precision loss. Original
notation and model fields remain available. The parser accepts explicit
superscripts, `10^(9)`, `10^9`, `e9`, comparators and common biomedical units;
normalizes only spelling such as `/hr` to `/h`; performs no unit-scale conversion.
Only a uniquely located source result is compared with the LLM. Ranges, dimensions,
composite blood pressure readings, duplicate possible results and unsupported
formats remain unresolved. Reference intervals are excluded from the search.
Agreement, conflict, source-only results and unresolved cases are counted
separately; lexical agreement is not proof of attribution or entailment.

New JATS parsing preserves `<sup>` and `<sub>` as `^(...)` and `_(...)`.
The frozen benchmark remains unchanged, including older flattened `×109/L`.
That spelling is flagged as ambiguous; it is not silently converted to `×10^9/L`.
The prospective sample may not contain every measurement notation; the local
parser tests cover explicit science notation, thresholds, ranges and composites.

Cited prior patients have a separate `cited_cases` collection with literal
evidence and known bibliography identifiers. They remain link candidates, not
additional primary patients or automatically merged records.

Visual prompts distinguish PET projection/fusion images, diagram arrow endpoints,
and clinical risk dashboard readings. Structured diagram relations and
`clinical_risk_score` chart readings are supported. Ambiguous labels and described
but unstructured INST values trigger repair; individually accepted panels can
survive partial output. These are consistency gates, not pixel accuracy checks.
All six reference figure-ownership cases receive pixel-selection priority,
but missing assets or unresolved rights can still prevent inspection. Text and
pixel ownership are evaluated separately, preserving disagreements.

## Review and return results

`SUMMARY.md` includes strict first-pass/delivered checklist scores, separate
representation-aware scores, forbidden hits, valid/partial timelines, caption
and pixel ownership, measurement comparison counts and relative-order counts.
The original 43 required and eight forbidden assertions are unchanged. Five
explicit representation alternatives in `reference-v2.json` acknowledge equivalent
facts in other fields/tasks; they never use quoted evidence as a generated claim
or relax forbidden checks. Raw model responses, repair audits and review forms
are retained. Both scores are limited development checks.

```bash
run_dir=$(ls -dt /blue/cai6734/ehr_agent/op2-corpus-refinement/run-*/ | head -n 1)
run_dir=${run_dir%/}
uv run --locked --python 3.12 --no-dev op2 corpus-pilot status --work-dir "$run_dir"
cat "$run_dir/SUMMARY.md"

uv run --locked --python 3.12 --no-dev op2 corpus-pilot export-results \
  --work-dir "$run_dir" --output "${run_dir}-results.tar.gz"
```

On your Mac, replace the timestamp with the one printed above:

```bash
scp wkieffer@hpg.rc.ufl.edu:/blue/cai6734/ehr_agent/op2-corpus-refinement/run-YOUR_TIMESTAMP-results.tar.gz ~/Downloads/
```

Cleanup deletes both owned acquisition trees, source exports/samples and
tokenizers after inference/reporting; weights have a separate preceding cleanup.
Results retain a bounded parsed review snapshot of the selected prospective
sources (at most 24 articles/32 MB, including cases with no discovered patients),
their exact evidence quotes, selected review pixels, hashes,
licenses, scalar lengths and histograms. The archive excludes corpus databases,
weights, environments and containers. Identical duplicate ledgers/bundles are
omitted only after equality checks; task files retain all raw responses. Arbitrary
shell tar of the whole run can include a large retained container.
