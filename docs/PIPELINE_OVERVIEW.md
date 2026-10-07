# Current pipeline overview

Last checked: **2026-10-07**, after implementation of the clinical architecture experiment.

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
[controlled campaign](CONTROLLED_COMPONENTS.md) for commands and the
[latest results](../reports/CONTROLLED_COMPONENTS_20261007.md) for measured gains,
failures, and the diagnostic table replay.

## New opt-in architecture experiment

The [clinical architecture campaign](CLINICAL_ARCHITECTURE_CAMPAIGN.md) is
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

The [alternative architecture proposal](EXTRACTION_ALTERNATIVES.md) develops
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
[component trials](../src/openpatients2/component_trial.py), and
[ledger stages](../src/openpatients2/ledger_pipeline.py).
