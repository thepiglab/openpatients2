# Current pipeline overview

Last checked: **2026-10-09**, after adding the unified article record and the optional EHR stages.

On 2026-10-10 the experimental campaign code, configs, reports and their docs (marked "archived" below) were
removed from the repository; every removed tracked file is still available at git commit `c66587d`. The campaign-orchestration CLI (`op2 corpus-pilot`,
HiPerGator/Slurm launchers) went with them; the baseline runner itself (`pilot_extract.run_pilot`) remains.

The completed experiment (`reports/CLINICAL_ARCHITECTURE_20261007.md`, archived) did not
justify promoting encounter replacement or automatic verification. Additional
coverage recovered useful candidate facts, but filtering lost more than it gained.
The diagrams below still describe the current baseline and experimental paths.
The proposed [source-claim compiler](SOURCE_CLAIM_COMPILER_DESIGN.md) is a new
design to test, not an implemented default.

This is the living overview of the full article extraction profile used as our
current benchmark baseline. Enabled stages depend on the configuration. The
diagram shows the flow of information, not an exact execution schedule: some
requests run concurrently, and initial summaries/timelines precede their review
and completion passes.

## Document to patient bundles

```text
                 PMC ARTICLE
          JATS XML + figures/captions
                       |
                       v
        +-------------------------------+
        | CPU: prepare the source       |
        |                               |
        | Check license                 |
        | Parse sections and tables     |
        | Preserve captions/image URLs  |
        | Assign stable source IDs      |
        | Remove bibliography           |
        +-------------------------------+
                       |
                       v
        +-------------------------------+
        | LLM: identify patients        |
        |                               |
        | How many individual cases?    |
        | Which passages belong to whom?|
        | Species, identity evidence    |
        +-------------------------------+
                       |
             One packet per patient
                       |
          +------------+-------------+
          |                          |
          v                          v
 +---------------------+   +-------------------------+
 | LLM: clinical tasks |   | LLM: figure processing  |
 |                     |   |                         |
 | Separate requests:  |   | Image + caption         |
 | - Conditions        |   | Visual descriptions     |
 | - Medications       |   | Modality/chart details  |
 | - Labs/observations |   | Patient/panel ownership |
 | - Procedures        |   +-------------------------+
 | - Symptoms          |               |
 | - History, etc.     |               |
 +---------------------+               |
          |                            |
          v                            |
 +--------------------------------+    |
 | CPU: parse and validate output  |    |
 |                                |    |
 | Schema/field checks            |    |
 | Literal source-evidence checks |    |
 | Patient/source references      |    |
 +--------------------------------+    |
          |                            |
     Problems found?                   |
       /         \                     |
     yes          no                   |
      |            |                   |
      v            |                   |
 +--------------+  |                   |
 | LLM: targeted|  |                   |
 | repair       |  |                   |
 |              |  |                   |
 | CPU recheck  |  |                   |
 | Bounded      |  |                   |
 | retries      |  |                   |
 +--------------+  |                   |
      |            |                   |
      +------------+-------------------+
                   |
                   v
 +---------------------------------------------+
 | LLM: clinical coverage and claim review      |
 |                                             |
 | Inventory clinically relevant information   |
 | Check coverage; attempt missing extraction  |
 | Audit extracted claims                      |
 +---------------------------------------------+
                   |
          +--------+--------+
          |                 |
          v                 v
 +----------------+  +-------------------------+
 | LLM: patient   |  | LLM: timeline           |
 | summary        |  |                         |
 |                |  | Events + linked facts   |
 | Completeness   |  | Relative ordering       |
 | review         |  | Completion/order review |
 +----------------+  +-------------------------+
          |                 |
          +--------+--------+
                   |
                   v
 +---------------------------------------------+
 | CPU: assemble patient JSON bundle           |
 |                                             |
 | Clinical sections + summary + timeline      |
 | Figures, descriptions, patient attribution  |
 | Source evidence + license + provenance      |
 | Validation status + unresolved problems     |
 +---------------------------------------------+
```

The model sees source text repeatedly in separate clinical-domain requests.
It generates ordinary JSON from prompts containing the requested structure;
the current campaign does not enforce schema-constrained decoding. CPU parsing
and validation follow generation. Passing these checks does not establish
clinical entailment or complete extraction.

Figure work is organized per figure and linked into patient bundles; the drawing
does not imply that image interpretation is rerun for every patient. Pixel
inspection depends on available assets and the enabled vision configuration.
Unresolved attribution and failed or partial tasks remain visible in the output.

## Optional EHR stages and the unified article record

Both the automated pipeline and the dense500 reference release emit the same
row: `openpatients2-unified-article/1`, one row per article, built by the shared
[`unified_record`](../src/openpatients2/unified_record.py) builder/validator. The
four EHR stages are new runtime tasks
([`ehr_tasks`](../src/openpatients2/ehr_tasks.py)). They are off by default in
[`EhrStageConfig`](../src/openpatients2/ehr_pipeline.py) and run after a
baseline run, reusing its bounded call, repair and ledger machinery. Existing
task contracts and their fingerprints are unchanged.

```text
   PMC ARTICLES (JATS + figures + supplements)
                     |
                     v
   baseline extraction run (diagram above)
   patients.jsonl, rosters.json, visual-annotations.json
                     |
       +-------------+-------------+-------------------+------------------------+
       |             |             |                   |                        |
       v             v             v                   v                        v
  figure_ehr_   figure_privacy_ supplement_          measurement_series_     (CPU) supplement_assets
  label         flags           classification       extraction              acquire: capped cache,
  panel EHR     burned-in text, class / scope /      model picks cell IDs;   hashes, literal peeks
  category +    anatomy, face;  includable from      code parses values      (input to
  includable    categories only bounded peek         (no unit conversion)    classification)
       |             |             |                   |
       +-------------+------+------+-------------------+
                            |
             CPU: same validators as the reference sidecars
                            |
                            v
   unified_record.build_record  <-- the same builder the dense500 export uses
                            |
                            v
   one row per article: patients[] (14 sections, summary, timeline, refs),
   figures[].panels[] (visual, attribution, ehr, privacy), supplements[],
   measurement_series, provenance, completion
                            |
          +-----------------+------------------+
          |                                    |
          v                                    v
   pipeline: OUT/data/unified.jsonl     release: data/dense_clinical_cases_v1/
   (completed=false, unreviewed)        data/{train,validation,test}.jsonl
          |                                    |
          +---------> unified_scoring <--------+
                      clinical item names, panel EHR category/includable,
                      privacy-flag P/R, supplement class, series points
```

Run (all stages off unless enabled in the YAML):

```sh
python -m openpatients2.ehr_pipeline --config ehr.yaml --articles sample.jsonl --run-dir RUN \
    --output OUT --endpoints http://127.0.0.1:8000/v1 --context 131072 \
    [--image-manifest RUN_MEDIA/vision-assets/manifest.json] \
    [--supplement-assets benchmarks/dense500] [--supplement-transcriptions benchmarks/dense500]
python -m openpatients2.unified_scoring --reference data/dense_clinical_cases_v1/data \
    --predicted OUT/data/unified.jsonl --out score.json
```

`ehr.yaml` sets any of `figure_ehr_label`, `figure_privacy_flags` (needs pixels),
`supplement_classification` (needs acquisition records), and
`measurement_series_extraction` to true. It may also name `extraction_config`
for serving limits. Model labels are unreviewed and never mark a row completed.
`--supplement-assets` / `--supplement-transcriptions` take a dense benchmark root: acquisition records and
reviewed supplement targets are read from its per-article folders (`articles/<ID>/supplements/`). OUT uses the same
per-article layout (`OUT/articles/<ID>/figure_ehr_labels.json`, ...), resolved by `benchmarks/dense500/store.py`.

## Current experimental branches

The latest controlled campaign runs the baseline once per shard/seed, then
starts each intervention independently from that exact saved baseline:

```text
                   SAME SAVED BASELINE
                   /        |        \
                  /         |         \
                 v          v          v
          TABLE ADDITION  ATTRIBUTE   TIMELINE
                         AUDIT       REBUILD
                 |          |          |
          Extract missing  Critique   Build events
          table cells      fields     from fixed facts
                 |          |          |
          Append accepted  Repair     Add validated
          observations     challenged events/links;
                           domains    quarantine errors
                 |          |          |
                 +----------+----------+
                            |
                   Compare each branch
                   against the baseline
```

These branches are not chained together. Patient discovery, figure descriptions
and figure attribution are frozen between comparisons. Changes to clinical facts
may require later summary/timeline reconciliation; component exports flag that
limitation. The baseline remains the retained extraction approach. See the
controlled campaign (`docs/CONTROLLED_COMPONENTS.md`, archived) for commands and the
latest results (`reports/CONTROLLED_COMPONENTS_20261007.md`, archived) for measured gains,
failures, and the diagnostic table replay.

## New opt-in architecture experiment

The clinical architecture campaign (`docs/CLINICAL_ARCHITECTURE_CAMPAIGN.md`, archived) is
implemented for the next comparison; no clinical gains are yet measured:

```text
full baseline -> compiled table additions -> frozen corrected comparator
                                            /          |          \
                               independent QA    encounter-first   second reading
                               of same facts     chunk extraction  additive facts
                                            \          |          /
                                           model support decisions
                                                      |
                       gate passes authored controls?--+--no--> shadow review
                                                      |
                                                     yes
                                                      |
                           accepted view + retained unresolved/original candidates
                                                      |
                      regenerate relative timeline when clinical records changed
                      withhold stale summary; freeze discovery and figure outputs
```

Encounter reading accounts for each source fragment and routes original chunks
to clinical domains, retaining failed/unresolved units. It separates prior/later
state and action initiation, completion and outcome. Verification first answers
source questions without candidate values, then compares with the proposed facts;
local ambiguity triggers bounded full-source expansion. Twenty authored controls
qualify the gate before filtering. They do not certify medical accuracy.

The experiment uses the existing 31 development articles and two seeds on four
independent B200 workers. The original baseline flow above remains available.
See the campaign document for incomplete capabilities and comparison limits.

## Remaining proposed direction

Let the model select source references and interpret clinical meaning and
relationships, while code carries forward exact IDs, quotes, measurements and
validated edits. The new experiment tests cell-ID-based table extraction and
encounter-aware source reading. Finer attribute edits, independently reviewed
state queries and more complete encounter coreference remain candidates. All
interventions must demonstrate fidelity gains before replacing baseline stages.

The alternative architecture proposal (`docs/EXTRACTION_ALTERNATIVES.md`, archived) develops
source coverage accounting, independent verification questions, encounter/state
extraction and corruption tests for qualifying gates before they edit records.
Its first bounded experiment is now implemented; the broader design is not a
change to the baseline above.

## Keeping this overview current

Update this file in the same change that adds, removes, reorders or promotes a
pipeline stage. Update the diagrams, the last-checked date, and the links to the
active campaign and latest evidence together. Keep baseline behavior,
experimental branches and proposed changes explicitly distinguished; a promising
offline replay is not a deployed improvement. Configuration-specific details and
exact scheduling remain in their implementation and campaign documentation.

Implementation references: [source parsing](../src/openpatients2/articles.py),
[article tasks](../src/openpatients2/article_tasks.py),
[baseline extraction](../src/openpatients2/pilot_extract.py),
[figure processing](../src/openpatients2/pilot_media.py),
component trials (`src/openpatients2/component_trial.py`, archived), and
[ledger stages](../src/openpatients2/ledger_pipeline.py),
[EHR stages](../src/openpatients2/ehr_pipeline.py), and
[unified record](../src/openpatients2/unified_record.py).
