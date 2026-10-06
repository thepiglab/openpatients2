# Corrections after the October 5 patient-bundle campaign

These changes implement the [source and runtime review](../reports/BUNDLE_CLINICAL_20261006.md).
They have local regression coverage; their clinical benefit and B200 performance
still require the next campaign. The historical archive and reference are unchanged.

| Failure found | Correction |
| --- | --- |
| Repair replay crashed on `coverage_repair_observations` | Resolve operational aliases to their clinical schema before item repair, normalization and accepted-value protection. Keep the original task name in receipts. |
| Completion prompts repeated evidence and overflowed before inference | Use compact fact IDs, values and source-segment references. Count the effective serving prompt and divide fact hints into batches. Every batch retains the original article; a source that still cannot fit fails explicitly. |
| Cross-domain inventory links failed a whole coverage batch | Keep those decisions uncertain, without coverage credit. Preserve other decisions and request source-grounded backfill for missing/uncertain hypotheses. |
| Completion erased an event with no fact IDs | Require existing unlinked events to survive with their description, kind, occurrence and time. Linked events may split into distinct encounters if their fact links survive. Failed completion retains the previous graph. |
| Clinical claims borrowed another field's meaning | Strengthen prompts, field guides and immutable optimization contracts for surgical versus tumor laterality, provisional versus final site, sensitization versus clinical allergy, recurrence versus remission, and treatment versus complication. |
| Selected patient sections lost explicit shared diagnoses | Retain supplied abstracts and introductory blocks as shared context. Source scope must explicitly support assigning a statement to the target patient. |
| Fresh GEPA validation timed out but printed a medical zero | Enforce deadlines inside rollouts/reflection, reserve fresh confirmation time, report unavailable counts as null and incomplete confirmation as partial, and retain originals. |
| Joint search began from a weaker independent candidate | Put original strategies on the actual frontier. Independent winners remain a separate comparison arm. Cache successful repeated evaluations; fresh confirmation bypasses that cache. |
| Minibatches contained no labelled patients for a prompt group | Choose train-only batches with relevant positive labels, plus negative probes when available. Keep bounded, component-specific reflection diagnostics and record the selected articles. |
| Eight GPUs were underfilled during joint search | Reuse the one-B200 optimization server for family and joint search. Start eight-GPU comparisons only after both write final receipts. This allocation change is not yet measured. |
| Bundles copied stale derived license metadata | Recompute the export decision from stored license evidence, preserving the acquisition decision separately. Source text and XML hashes remain unchanged. |

## Reviewed benchmark

`benchmarks/patient-bundle/reference-reviewed.json` is a new revision:
**259 required clinical assertions and 44 forbidden probes**. The original
`reference.json` retains its 261/44 probes and historical scores.

The reviewed revision accepts same-patient CD34/STAT6 positivity in either
`text_value` or `interpretation`; keeps suspected milk allergy possible rather
than confirmed; corrects the SFT word-boundary regex and adds its explicit shared
two-case diagnosis evidence; excludes the unadjudicated illustrative ESD record
from clinical/identity reward; and adds a forbidden tumor-laterality probe for
bilateral craniotomy. It retains that article for inspection rather than inventing
an adjudicated roster label. Other independently labelled articles are unchanged.

Reproduce and source-check this revision without downloads:

```bash
.venv/bin/python scripts/review_bundle_reference.py
```

The revision records its changes and the historical reference SHA-256. These are
previously exposed development sources, not a new blinded evaluation. Finite gold
probes, literal evidence and schema validity do not establish comprehensive medical
precision or recall. Clinical audit judgments remain suggestions, without authority
to delete accepted facts or silently repair a medical assertion.

## Next campaign

The existing launcher now selects the reviewed reference and corrected code:

```bash
cd /blue/cai6734/ehr_agent/openpatients2
bash scripts/run_bundle_overnight.sh
```

Use an updated checkout and a new campaign directory. Family and joint search
each have a 90-minute budget on one B200, inside a four-hour Slurm allocation.
The eight-B200 comparison budget is eight hours, including initialization and
confirmation layouts, inside its twelve-hour Slurm limit. CPU acquisition and
cleanup request no GPUs. Model weights and working article downloads are still
deleted after all GPU consumers finish; small source-review artifacts remain.

Compare the original versus evolved live-discovery arms under the **same revised
rubric**, including clinical matches, forbidden/unscorable probes, ordered course
relations, patient/panel ownership, completion failures and supported facts per
elapsed time. Original fallback must be distinguished from an accepted rewrite.
Keep medium reasoning, the pinned FP8 checkpoint and DFlash as the serving baseline.

If the primary source plus output reserve exceeds 64K even after hint batching,
the 128K confirmation remains necessary. No source truncation, pixel substitution,
automatic production promotion or model-judge clinical reward was introduced.
