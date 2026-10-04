# Changelog

## Unreleased — overnight clinical fidelity / GEPA

- Adds a single-launch, bounded overnight campaign: 15 extraction ablations, four seeds, five B200 serving layouts, and up to 48 disjoint new sources across 14 specialties.
- Integrates pinned GEPA prompt searches with article-level train/validation/test separation, finite clinical checklist versus structural-only rewards, candidate traces and no automatic production promotion.
- Adds independent full-article clinical inventories/coverage/claim audits, protected omission backfill, immutable-event order review and explicit unknown-time patient state indexes.
- Tests staged versus joint pixel attribution with exact panel inventory coverage and patient-specific panel columns.
- Fixes numeric-only measurement/unit false conflicts, isolates optional citation-link failures, exports live discovery for scoring and verifies cache reset success on loopback servers.
- Downloads and cleanup remain CPU-only; GPU work stops within a 10-hour budget, with failed/deferred trials and GEPA coverage in the comparison report.

## Unreleased — corpus refinement trial

- Adds a one-command CPU→GPU→cleanup campaign with four balanced regression arms and a small disjoint live-discovery sample.
- Quarantines unsupported time associations, rotates item-repair batches, protects accepted atoms and populated measurements, and retains partial timelines/panels with failed candidates in audit.
- Exports relative clinical order without synthetic dates and compares source-parsed decimal magnitudes/units/scientific scales against unchanged model fields.
- Preserves JATS superscript/subscript notation for new sources; frozen older flattened exponents remain explicitly ambiguous.
- Separates cited case link candidates, strengthens visual consistency prompts/gates, prioritizes reference figures and keeps strict versus representation-aware scores separate.
- Cleans both owned article download trees and archives results with verified duplicate elimination.

## Unreleased — Glimmer HiPerGator gauntlet

- Pinned FP8, ModelOpt mixed NVFP4 and Unsloth NF4 checkpoints, official corrected ATEM template and four publisher reasoning strengths; full BF16 verifier excluded.
- Replays the frozen PMC clinical evaluation, with source-check and validation counts, bounded retries, captions/figure attribution, and explicit text-only vision status.
- Measures all-eight-GPU DP/TP/concurrency layouts, experimental DCP, official DFlash and a Glimmer-derived DSpark head; speculative TP1 runs also repeat the full medium quality arm.
- CPU-only acquisition and deletion, campaign-local client/plugin environments, audited read-only DSpark compatibility overlay, warm-cache workload controls, speculative counters and failure diagnostics.
- No local weight downloads or B200 performance/quality claims before the cluster campaign runs.

## 0.5.0 — terminology grounding

- Local pinned ICD-10-CM/SNOMED CT/LOINC catalog imports and hierarchy lookup.
- Auditable candidate retrieval, optional model selection with saved reasoning, human review and fail-closed indexing.
- Evidence-linked graph export and explicit versioned concept-descendant cohort filters.
- Source identifier namespaces stay unresolved without explicit identifiers or reviewed source crosswalk.
- First-time setup documentation separates implemented components from planned source expansion/simulation.


## 0.4.0 — 2026-09-27

- Preserves every original dataset row/column, exact description, canonical row hash, pinned dataset lineage and duplicate IDs with separate processing keys.
- Adds correct PMC-Patients PMID-case mapping, checked source identifier crosswalks, DOI/PubMed/PMC URLs and explicit conflicting-ID handling.
- Adds CPU-only, batched/cached PMC Cloud figure discovery storing manifest-backed URLs, JATS captions/labels, article versions and rights/retraction provenance; no image/PDF/video/archive downloads.
- Adds explicit absent/unknown/failed discovery states, offline cache behavior, rate limits/retries/caps and safe XML parsing. Cases from one paper share a discovery lookup.
- Carries original rows and figure candidates through extraction exports and cohort results. Groups known shared source articles in reasoning statistics and distillation splits.
- Adds a no-network source demo, a CPU preparation script, source tests and documentation. General PubMed/MeSH metadata remains investigation-only.
- Keeps article media as unverified patient candidates; clinical extraction still uses only the original note. This release does not implement a patient simulator.

## 0.3.0 — 2026-09-27

- Added paired **within-checkpoint** reasoning studies, randomized blocked execution, per-level CPU token profiles, immutable sample/config/gold provenance, and same-level repeatability analysis.
- Added conservative fact/evidence agreement, independent empty-output reporting, source-cluster bootstrap intervals, paired cluster randomization tests, and Holm-adjusted primary comparisons. Agreement does not establish clinical accuracy or equivalence.
- Added streaming reasoning-field capture, preserved raw structured reasoning, selected-response trace metadata, exact requests, and separate failed/retry attempt exports. No unavailable reasoning is reconstructed.
- Added review-gated, leakage-aware answer-only or optional reasoning-bearing distillation JSONL exports. A student trainer is not included.
- Clinical schema **2.1.0**: required documentation status/evidence; valid empty outputs; explicit unknown/nonapplicability; condition verification, allergy-denial scope, and observation result-absence reasons. All 14 prompts now include clinical decision and negative examples.
- Added K2 low/medium/high study and Motif default-mode repeatability configs, CPU/GPU study scripts, synthetic end-to-end demo, expanded tests and usage documentation.
- Clinical indexes do not copy reasoning into their payloads. Source/structural validation remains distinct from clinical adjudication.
- No GPU/real-model performance or clinically adjudicated accuracy is asserted by this release. Direct dependencies are pinned; the build environment could not resolve PyPI, so generate the uv lock on a connected CPU machine.

## 0.2.0

Initial extraction, validation, serving-profile, benchmark, cohort-index and CPU/mock-tested release.
