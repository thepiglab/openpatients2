# Dense reference set, batch 2 (dense500b)

Batch 2 of the dense clinical reference set: permissive-licensed PMC case reports (CC0, CC BY,
CC BY-SA or public domain; NC/ND terms rejected) chosen for gap-targeted lanes, i.e. strata that
dense500 under-covers. Articles come from a supplied selection list, not the dense500 discovery lanes.

The annotation protocol, agent briefs, review receipts and per-article layout are shared with dense500;
see [../dense500/README.md](../dense500/README.md), [ANNOTATION_PROTOCOL.md](../dense500/ANNOTATION_PROTOCOL.md),
[SUPPLEMENT_DOCUMENT_REVIEW.md](../dense500/SUPPLEMENT_DOCUMENT_REVIEW.md) and
[claude_prompts/](../dense500/claude_prompts/). Every `op2 dense` tool takes `benchmarks/dense500b` as `R`.

## What differs from dense500

- `selection.json`: one row per acquired article with `lane`/`stratum`, `group`, `selection_role`
  (primary or alternate), `selection_slot` (the primary it fills; `replaces` when an alternate was used),
  `license_policy_version` and the selection file's name and SHA-256.
- `acquisition/decisions.json` records every attempt (license, fetch, title screen, duplicate); `acquisition/network.json` records the byte budget.
- No PMCID may also appear in dense50 or dense500. Acquisition checks all three roots.
- Release: `op2 dense export --set dense500b` writes its own dataset, `data/dense_clinical_cases_dense500b_v1/`
  (rows tagged `reference_set: dense500b`). It is never merged into `dense_clinical_cases_v1`.

## Acquisition

```
op2 dense acquire acquire --root benchmarks/dense500b --selection-file SELECTION.json --permissive-only [--limit N]
```

Primaries are tried in file order. If a primary fails the license policy or acquisition, the next unused
alternate from the same lane takes its place. The run is resumable.

`--recheck-held` re-attempts `license_review` holds logged under an older policy version (filled slots
are left alone). Policy `article-adaptations-permissive-only/2` accepts an article-level CC BY license
whose only other grant is the BMC/Springer sentence placing *the data* under a CC0 waiver
(reason `article_cc_by_with_cc0_data_waiver`). `version_selection_required` holds (several published
PMC versions) are not re-run; they need an explicit version choice.
