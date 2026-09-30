> Update: the article acquisition, roster, summary, timeline, vision and EHR-seed stages described below now have pilot implementations. See [ARTICLE_PIPELINE.md](ARTICLE_PIPELINE.md) and the [measured pilot](../reports/ARTICLE_PILOT.md) for current commands and remaining boundaries.

# End-to-end source-grounded pipeline

## Target architecture and actual implementation boundaries

```
Open-Patients original row + verified article/case identity
   ↓
Article XML/text, table/caption/figure manifest
   ↓
Patient-specific source evidence packet           [planned source expansion]
   ├── 14 focused clinical extractions             [implemented for input note]
   │        ↓
   │   validation + evidence + structured facts    [implemented]
   │        ↓
   │   standard terminology grounding              [implemented]
   │        ↓
   │   index + evidence-linked knowledge graph      [implemented]
   │
   └── companion source-backed narrative summary   [planned]

Reviewed figures + actual image interpretation     [planned visual stage]
   ↓
Reviewed case-linked visual findings, provenance    [planned]

Observed, grounded patient representation
   ↓
Separately authored synthetic EHR events / simulator [planned]
```

Current source enrichment discovers article metadata, captions and URLs only; it
is **not** a full-article text ingestion and case-localization system. Clinical
extraction currently consumes the preserved original note. Replacing it with an
article-derived packet requires its own source-text/evidence-offset representation;
the code explicitly prevents silently swapping text under an original-row hash.
This keeps terminology code usable now without misrepresenting the larger proposal
as already implemented.

## Source expansion design

Recover each article once and identify its cases before calling patient-specific
clinical extraction. A packet should reference original paragraph/table/caption IDs
rather than generate an untraceable replacement for them. Preserve full original
rows, article versions, reference hashes and both case/article identifiers.
Discussion paragraphs, literature-review cases, comparison patients, control images,
and hypothetical recommendations are not index-patient observations by default.

Generate clinical facts directly from the packet. A readable summary should be a
companion output supported by those facts and original passages, not a lossy sole
input to the extractor. The current 14 section-specific prompts and prefix-local
request scheduling can be reused once packet-source validation is implemented.

## Terminology placement

Run terminology mapping **after extraction and source checks**, not inside a large
all-in-one article-to-coded-EHR generation call. The model need not re-read the whole
article to decide among a small set of retrieved candidate concepts. The request
contains the fact, subject/assertion/time, supporting quote and relevant context.
An insufficient snippet should lead to abstention or evidence review, not invented
specificity. No diagnosis is derived merely because a vocabulary has a nearby code.

Deterministic extraction validation, catalog import/search, review merging, indexing
and graph creation are CPU stages. Clinical extraction and optional candidate
selection use the configured inference endpoint. Vocabulary licenses and model
environments remain separate. Do not reserve GPUs for terminology downloads/indexing.

## Relationships that are safe to store separately

1. Terminology hierarchy from the release: subtype or classification membership.
2. A particular fact's subject/assertion/time and quoted source evidence.
3. Explicit within-case links, such as a biomarker attached to a specified tumor.
4. Mapping decisions: raw string to a selected concept, with uncertainty and review.
5. Later authored simulation relationships: labeled SYNTHETIC, never retroactively
   promoted into the observed case record.

A code hierarchy does not prove that a finding caused a diagnosis. ICD/SNOMED codes
do not uniquely specify treatment, timing, patient identity or an EHR event sequence.
NLM's SNOMED→ICD rules are directional, conditional and sometimes one-to-many, not
an invertible dictionary. Detailed evaluation/execution is outside this release.

## Multimodal and simulator safeguards

An article image URL remains an article-level candidate until panel/case/time-point
association is reviewed. A vision model must actually retrieve pixels to inspect
them, even if persistent dataset storage remains URL-only. Author-caption findings,
model visual observations and human-reviewed interpretations should remain distinct.

For patient simulation, keep presentation-time evidence separate from future test
results and final outcomes. A figure caption can leak the answer. An eventual FHIR
resource/compiler should reflect explicit resource semantics and validate resources
against the chosen version/profile; attaching code URIs alone is not FHIR compliance.

No simulator, FHIR server, natural-language cohort interface, source article census,
real B200 throughput measurement or clinical gold dataset is created by this update.
