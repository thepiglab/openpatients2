# Within-model reasoning study

**SYNTHETIC SOFTWARE FIXTURE — no model inference, clinical evaluation, or GPU throughput measurement.**

Checkpoint: `SYNTHETIC-FIXTURE` at `fixture-v1`.
6 source records; 3 source clusters; 6/6 runs.

Agreement is not clinical accuracy. The JSON report includes task-level intervals, denominators and paired tests.

| Comparison | Exact on all scheduled | Fact Dice (nonempty) | Both empty |
|---|---:|---:|---:|
| low vs medium | 0.982 [0.970, 1.000] | 0.979 [0.965, 1.000] | 0.179 [0.179, 0.179] |
| low vs high | 0.988 [0.976, 1.000] | 0.986 [0.972, 1.000] | 0.179 [0.179, 0.179] |
| medium vs high | 0.994 [0.988, 1.000] | 0.993 [0.986, 1.000] | 0.179 [0.179, 0.179] |
| low repeatability | 1.000 [1.000, 1.000] | 1.000 [1.000, 1.000] | 0.190 [0.179, 0.203] |
| medium repeatability | 0.988 [0.976, 1.000] | 0.986 [0.972, 1.000] | 0.179 [0.179, 0.179] |
| high repeatability | 1.000 [1.000, 1.000] | 1.000 [1.000, 1.000] | 0.179 [0.179, 0.179] |

## Interpretation

- Agreement is consistency, not truth; neither higher reasoning nor consensus is a gold label.
- Both-empty outputs are reported separately and excluded from fact Dice/Jaccard, not scored as perfect factual similarity.
- Failed/truncated outputs never count as agreement; both-valid agreement is accompanied by an all-scheduled metric.
- Fact matching is lexical/canonical, not comprehensive terminology or clinical equivalence.
- Span metrics are conditional on identical clinical facts; multiply-occurring evidence is not assigned an arbitrary offset.
- Bootstrap CIs are nominal pointwise 95% percentile intervals; Holm adjusts ALL prespecified primary difference p-values only.
- No significant difference does not prove equivalence/noninferiority; clinical margins and adjudicated labels need a separate prespecified decision.
- Degenerate bootstrap intervals (e.g. all observed agreements) do not prove zero population error.
- Distinct source IDs are not verified independent patients. Supply reviewed cluster_id for related reports.
- Seed support and effort controls may be ignored by an endpoint; saved requests document intent, not proof of internal implementation.
- Reused/resumed records invalidate naive full-run speed comparisons; inspect execution reports and rerun a fresh study for timing.
