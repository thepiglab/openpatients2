# Fixed source checklist, 2026-10-03

This is a **20-article, 17-patient research fixture**, with 51 finite clinical checks (43 required, 8 forbidden) and six text-supported figure ownership checks. It is a hand source-reading checklist, not complete medical gold, physician adjudication, a population sample, or an accuracy estimate. Images are not bundled or newly inspected.

`articles.jsonl.gz` contains input sources only. `rosters.json` freezes source-checked identity targets for clinical extraction. `reference.json` is scoring-only: never include it in extraction prompts. Live discovery runs must not consume frozen rosters, and should be reported separately across seeds. Frozen clinical extraction is explicitly conditional on the hand roster.

| Articles | Identity target and challenge |
| --- | --- |
| PMC13612135.1 | One male, 76 years; synchronous malignancies, PSA/CEA, monthly therapy |
| PMC13542385.1 | One male, 12 years; VATER/cleft, completed reconstruction versus future advancement; literature comparison cases excluded |
| PMC13582412.1 | Two male patients, 65/55 years; left/right tumors, no initial metastasis for patient 2, BRCA1 VUS |
| PMC13549756.1 | Three infants; sodium 129/115/121, patient-specific medications and doses; mothers excluded |
| PMC13618765.1 | One female, 39 years; acemetacin dose/frequency, physical therapy, no thrombosis; case-specific Discussion details retained |
| PMC13624889.1 | One male, 72 years; negative vascular markers, NRAS alteration |
| PMC13624890.1 | One male, 68 years; PAD proposed, SCC suspicion not confirmation |
| PMC13587181.1 | One male, 10 years; drained-fluid creatinine is not serum, sclerotherapy and warfarin |
| PMC13624352.1 | One female, 48 years; pulmonary thromboembolism excluded, warfarin duration and follow-up |
| PMC13618778.1 | One male, 12 years; vitamin D 600 IU daily, 50-hour rehabilitation versus external 90-hour studies |
| PMC13524801.1 | One limited illustrative 4-week-old infant in Fig. 3; other displays and cohort data not that patient |
| PMC13624887.1 | One female, nine months; scalp lesion, no underlying bone involvement, conservative follow-up |
| PMC13575834.1 | **Provisional** anonymous Fig. 2 procedural case: unknown age/sex, cohort sedation doses not individually attributable |
| PMC13624771.1 | One female, 82 years; completed ESD, en bloc resection without adverse events |
| PMC13577788.1 | Zero patients: bacterial taxonomy; strains are not clinical cases |
| PMC13543720.1 | Zero individually described patients: experimental chicken groups; representative images are not patient identities |
| PMC13524818.1 | Zero original patients: systematic review of prior published cases; diagnostic diagram is background |
| PMC13624957.1 | Zero individual cases: linked claims/EHR cohort |
| PMC13575883.1 | Zero individual cases: gastric cancer/heart failure cohort |
| PMC13069285.1 | Zero individual cases: mutant mouse experimental groups and representative microscopy |

The 17 identity targets include two limited illustrative cases. The anonymous pyloric procedure's status as a patient record remains open for clinical adjudication; its discovery count is excluded from the definitive count denominator. No demographic values are invented for either illustrative case. All targets are human; experimental animals appear only in aggregate negative articles. The presence of nonhuman experiments does not justify naming individual animal patients.

Patient-specific main blocks and source-specific Discussion paragraphs were checked. Other blocks in case-bearing articles remain unresolved and retained rather than asserting exhaustive attribution. All figure entries in the frozen roster are deliberately unresolved; the six independent figure labels reside only in the scorer reference. Case count/species agreement does not establish correct identity mapping or exhaustive patient discovery. Negative labels follow this benchmark's original-case definition; previously reported comparison cases inside reviews are not new records.

## Source recovery and rights

Sources were streamed from `run-20261003-000942-results.tar.gz`, primarily `extraction/context65536-prefill32768/targeted/attempts.jsonl` roster request `SOURCE_JSON`. No environments, SIFs, caches, image payloads or generated patient facts were extracted into this fixture. Canonical segment objects and captions are unchanged. Canonical text is rendered with the repository's segment-join rule.

The 14 case-bearing articles have the exact archived patient bundle's `source.article_source` metadata. Reconstructed text SHA256 matches that metadata for every one. The other six requests omitted authors, DOI/PMID, retrieval URLs, supplement inventory and full asset metadata. Their saved report's article license and XML SHA256 are retained; missing bibliographic fields remain unavailable (authors/supplements use empty lists only to satisfy the source packet shape, **not** to claim their original inventories were empty). Each source's `retrieval.recovered_fixture` explains these omissions. XML bytes are absent: their saved hashes identify the archived primary source but cannot be independently recomputed from this bundle.

Three negative-article figures have exact URLs and MD5/SHA256 provenance from the archive's small pixel manifest, bound by exact article/figure IDs. No URL was guessed. These projections lack the full asset-specific rights inventory and explicitly require asset rights review before reuse. A saved article license does not establish an asset exception is absent. Rights evidence is preserved as archived, subject to the current code's recheck; no fresh license approval is claimed. This fixture does not include image bytes and does not authorize fresh image acquisition.

`articles.jsonl.gz` uses deterministic gzip (mtime zero). The payload is under 0.5 MB across the three data files. Existing archives and unrelated workspace changes are untouched.

## Scoring API

```python
from openpatients2.corpus_fidelity import evaluate, validate_reference_sources
validate_reference_sources(reference, articles)  # run on CPU before GPU work
scores = evaluate(reference, patients,
                  task_rows=task_results,
                  discovery=predicted_rosters,
                  visual_rows=visual_annotations)
```

`patients` accepts exported bundle dictionaries, a JSONL path, or a folder containing `patients/*.json`. It uses `source.record_id`, `source.article_source`, `sections`, and plain `companions.summary`/`timeline_v2`; legacy companion wrappers are accepted. `task_rows` supplies first-attempt candidates. `discovery` accepts `predicted-rosters/1`, report article rows, or prediction rows. Optional missing arguments are unscorable, never fabricated successes.

Results include:

- `checks` and `summary.first_pass`, `summary.raw`, `summary.delivered`. **Raw aliases first_pass**: the first original full `raw_candidate` only. Later failed-item repairs can be partial patches and are excluded. Exported final data are delivered. Missing task/attempt logs make first-pass checks unscorable, not inferred from delivery.
- Each summary reports `required`, `matched`, `missing`, `unscorable`, `forbidden`, `forbidden_violations`, `forbidden_unscorable`, and `checklist_delivery_fraction`. The fraction counts unavailable delivery as not delivered; it is not accuracy. Unavailable forbidden sections are not proof that forbidden claims were safely absent.
- `discovery` reports source count/species agreement, extra/missing counts and unavailable articles. The provisional illustrative procedural article is excluded from definitive count totals. Wrong identities still require adjudication even when counts match.
- `figures.caption` and `figures.pixels` score independent attribution outputs. Pixel ownership uses `pixel_attribution` tasks or `visual_rows[].attribution.data`. Pixel descriptions, diagnoses and clinical significance remain unadjudicated.
- `schema.reported_task_statuses` is a separate structural status tally. `unreviewed_claims` counts generated items outside the finite checklist; `unsupported_claims` is null because those claims have not been reviewed.

A check must match a complete fact within **one item**, excluding evidence and documentation evidence. Values from different items cannot combine into a hit. Automatic regex/numeric patterns intentionally do not perform general medical entailment or unit conversion. A correct paraphrase or alternate schema encoding can miss a pattern and needs source review. A hit can still omit qualifiers outside that check. `validate_reference_sources` fails altered canonical text/hash, reference hash changes, or nonliteral gold quotes; it cannot independently verify absent XML bytes.
