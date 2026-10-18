# Backfill brief: EHR sidecars for already-completed articles

You are a "Claude Sonnet 5.5 annotator" adding the new EHR multimodal sidecars to articles that are ALREADY
completed (parent-reviewed). Repo root `/Users/mkieffer/programming/ToadResearch/openpatients2`; `R` =
`benchmarks/dense` (the one corpus root); tools run as `op2 dense <tool> ...`
(or `.venv/bin/op2`; any working directory).

Read first: the figure rules at the end of this brief (generated from `src/openpatients2/rules/annotation_rules.yaml`,
also given to the local-LLM figure prompts), the "EHR multimodal sidecars" section of
`benchmarks/dense/ANNOTATION_PROTOCOL.md`, step 4b of
`benchmarks/dense/claude_prompts/ANNOTATOR_BRIEF.md`, and `src/openpatients2/figure_ehr.py` /
`src/openpatients2/supplement_manifest.py` for the enums.

## Signing: a `--role` call is a signature

Never run any `--role` command to probe, test or dry-run a script. It overwrites the existing receipt, which may be another reviewer's valid signature. Run the script WITHOUT `--role` to validate. Sign only after you inspected the content yourself, with your real reviewer identity (e.g. "Claude Sonnet 5.5 annotator", "Claude Opus 5.5 parent") and notes that describe what you checked. `openpatients2.dense.signing_guard` refuses placeholder reviewers/notes ("x", "test", under 20 characters, "dry run"), and exports reject receipts that carry them.

## Hard rules
- Do NOT modify `annotation.json`, `schema_target*.json`, `visual.json` (in `R/articles/<ID>/`) or any existing receipt (`receipts/`). These
  articles are complete; touching them would un-complete them. You only create the new sidecars and their
  agent receipts.
- Never run anything with `--role parent`, never run `op2 dense supplement_assets cleanup`, never run `op2 dense export`.
- Base every label on what you actually see (pixels / opened file) plus the reviewed visual submission and
  annotation; never invent clinical values.

## Figure EHR labels (per article)
1. `op2 dense figure_assets R --article <ID>` if assets are not cached (paths in `R/articles/<ID>/figure_assets.json`).
2. `op2 dense figure_ehr_review R --article <ID> --draft`, copy `R/articles/<ID>/figure_ehr_labels.draft.json` →
   `R/articles/<ID>/figure_ehr_labels.json`.
3. View every figure (copy each asset to your scratch dir with an image extension and Read it). For each figure
   and panel confirm or override the deterministic default `ehr_category` / `ehr_includable` (with
   `override_reason`), and replace every DRAFT rationale with a concrete one (what the image is, whose, why it
   would or would not belong in an EHR). Images of cited/external cases, diagrams, flowcharts, aggregate charts
   are not includable.
4. Validate `op2 dense figure_ehr_review R --article <ID>`, then sign
   `... --role agent --reviewer "Claude Sonnet 5.5 annotator" --notes "<what you checked>"`.

## Supplement manifests (per article with supplements)
`op2 dense supplement_assets acquire R --article <ID>` → `op2 dense supplement_manifest_review R --article <ID> --draft` →
open every cached file → fill each row's review block in `R/articles/<ID>/supplements/manifest.json` → validate → sign
`--role agent`. Do not edit `machine`/`machine_suggestion`.

## Figure privacy flags (per article with figures; separate sidecar, existing figure-ehr receipts stay valid)
`op2 dense figure_privacy_review R --article <ID> --draft`, copy `R/articles/<ID>/figure_privacy_flags.draft.json` to
`R/articles/<ID>/figure_privacy_flags.json`, view every panel's pixels and set `burned_in_identifiers`
(none | timestamps_or_device_text | direct_identifiers = names, IDs/MRNs, birth dates, operator names, UIDs),
`sensitive_anatomy` (e.g. genital) and `identifiable_face` (e.g. unmasked faces), each with a short description of
the CATEGORY of what is visible (never copy the identifier values; runs of 4+ digits are rejected), and replace the
DRAFT notes. `prefill` holds hints from earlier reviewer text/known reports; confirm or reject them on pixels.
Flags never change `ehr_includable`. Validate `op2 dense figure_privacy_review R --article <ID>`, sign `--role agent`.
Pre-filled drafts already suggest flags for 25 articles (e.g. PMC12689157.1 F1 A/C direct identifiers,
PMC12880940.1 f4 struck-through ECG ID, PMC12813956.1 printed timestamps, PMC12810418.1 recognizable face).
Every other panel still needs explicit confirmation of `none`/`false` on pixels.

## Species
Non-human patients are included; export adds `patient_species` / `non_human_patient` from the annotation. Nothing to do.

## Report (≤200 words)
Per article: figures/panels labelled, how many includable by category, overrides made and why, any article
where tooling failed (with the error). Flag anything the parent should look at.

## Shared figure rules

<!-- GENERATED rules:start — edit src/openpatients2/rules/annotation_rules.yaml, then run `PYTHONPATH=src .venv/bin/python -m openpatients2.annotation_rules --write` -->
Rules shared with the runtime prompts (`src/openpatients2/rules/annotation_rules.yaml`). Rule IDs appear in validator messages as `[ID]`.

- **FIG-LABELS** (No invented panel labels; question marks only inside quotes; validator): Use the panel letters or numbers printed in the caption or on the image; otherwise the panel label is null (whole figure). Caption position words ("left", "upper right", "the image on the left", "Main image") and patient names ("Case 1", "Patient 2") are not printed labels; do not use them as panel labels in the annotation, the visual description panels, or the figure-EHR label and privacy-flag rows that reuse those panel IDs. In visual descriptions a "?" appears only inside quotation marks that transcribe printed text (e.g. “Unstable?”); put uncertainty in limitations.
- **FIG-EHR-COMPARISON** (Normal-comparison images are non-clinical aids): Normal-comparison, control, atlas or reference images that are not this patient's data are non_clinical_diagnostic_aid and never EHR-includable, even when they are microscopy or radiology. Only panels attributed to a local patient can be includable.
- **FIG-PRIVACY** (Privacy flags name categories only): Privacy flags describe the category of visible identifiers (e.g. "patient name in scanner header"), never the identifier values themselves; flags never change EHR includability.
- **MEDIA-UNAVAILABLE** (Unobtainable media are listed, never described): A figure or supplement whose source declares no obtainable asset (no image/video URL, or only an external link such as YouTube) is recorded as unavailable, with a limitation saying it was not viewed. Never describe, classify from content, attribute or extract facts from media you did not view; caption text stays caption evidence. Such media do not block completion: the article is complete, flagged, and the media stay listed as unavailable and not reviewed (media_available=false, content_reviewed=false). A download or rendering failure of a declared asset is not unavailability: retry or report it.
<!-- GENERATED rules:end -->
