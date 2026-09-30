# Original rows, article provenance, and URL-only figure discovery

Research checked: **September 27, 2026**. This is a first-time setup workflow. The clinical task schema remains **2.1.0**; source provenance has its own version.

## What is implemented

Every normalized source preserves `original_row`, an unchanged JSON object containing **all** input columns, and `original_row_hash`, a SHA-256 of canonical JSON. This preserves JSON values, not the original file's whitespace or key ordering. `text` preserves the exact original description, including whitespace. The original downloaded file and pinned acquisition manifest are retained separately.

`provenance` records the dataset ID/revision, source filename, zero-based row index, original source ID, upstream collection, declared component terms, article identifiers, identifier evidence/conflicts, and usable source links. Duplicate dataset IDs are retained with distinct deterministic processing keys; neither duplicate IDs nor duplicate text cause source rows to be dropped. An unavailable article does not cause its case to disappear.

The original row and lineage survive `patients.jsonl`, task-level `extractions.jsonl`, indexing, and returned cohort rows. `attempts.jsonl` remains an attempt audit log, not an additional full dataset copy. The clinical index also has a small `source_articles` table indexed by PMID/PMCID. General bibliographic/MeSH indexing is **not** implemented.

### Critical identifier distinction

Do not equate the aggregator's bare `pmc-{number}-{case}` with an upstream
`patient_uid` convention. The provided example `pmc-8654144-1` was identified with
PMCID `PMC8654144`, not PMID `8654144`. The code no longer infers either numeric
namespace from this string alone. Without explicit IDs the status is
`identifier_namespace_ambiguous`; source rows and case-group tokens remain intact.

Explicit PMID/PMCID/DOI fields and recognized URLs can supply the source identity.
Conflicting explicit identifiers block figure attachment. References inside the
patient narrative are not treated as source identifiers. Upstream row metadata or
a manually validated source crosswalk can attach identifiers without modifying the
original row:

```bash
uv run op2 attach-article-map --input data/prepared.jsonl \
  --mapping data/reviewed-article-map.jsonl --output data/source-identified.jsonl
uv run op2 enrich-sources --input data/source-identified.jsonl \
  --output data/enriched.jsonl --config configs/sources/pmc.yaml
```

Each crosswalk JSONL entry requires `record_id`, `source_hash`, one or more explicit
`pmcid`/`pmid`/`doi`, `reviewed: true`, `reviewer`, and `mapping_source`. This is a
caller-supplied review, not an automated proof of article identity. Metadata/NCBI
crosswalk checks run afterward. An unrelated paper with a coincidentally valid ID
is not sufficient evidence; verify its case content. There is no full-corpus source
crosswalk bundled. Source groups can conservatively retain common raw article-ID
components for leakage prevention without claiming a known numeric namespace.

## Discovery path

```text
Original case ID / declared PMID, PMCID or DOI
              ↓
PMC ID Converter: checked PMID ↔ PMCID ↔ DOI mapping
              ↓
PMC Cloud: list the available versions for that PMCID
              ↓
Per-version JSON metadata: actual distributed file URLs and rights flags
              ↓
JATS XML: figure labels, captions, graphic references and attribution
              ↓
Match graphic references against the explicit file manifest
              ↓
Save HTTPS image URLs and article-level candidates; fetch no image bodies
```

PubMed is a citation/indexing database. PMC is the full-text archive used here for machine-readable figure discovery. The ID Converter only crosswalks records that exist in PMC. An unmapped PMID is not evidence that the publisher has no figures.

The client uses the current [PMC ID Converter](https://pmc.ncbi.nlm.nih.gov/tools/id-converter-api/) and [PMC Article Datasets Cloud service](https://pmc.ncbi.nlm.nih.gov/tools/pmcaws/). The exact bucket schema is in its [official README](https://pmc-oa-opendata.s3.amazonaws.com/README.txt). Public-bucket access does not require an AWS key or account.

**Only four response categories may be fetched:** identifier JSON, S3 object-list XML, article metadata JSON, and article JATS XML. These requests run on CPU. There is no publisher-page scraper, PDF renderer, OCR, image HEAD/GET, archive extraction, vision inference, or image storage in this implementation. It does fetch article XML to read figure structure and captions; it does not persist full article XML in the dataset or cache. Metadata/XML bytes are reported separately from image bytes.

Requests use homogeneous identifier batches of at most 200, bounded concurrency, rate limits, timeout/retry/backoff, response-size caps, a host allowlist, safe XML parsing, and a SQLite cache. `NCBI_EMAIL` supplies the contact requested by NCBI. Run only one enrichment process per cache DB; the job itself parallelizes requests. `429`/temporary server failures are retried. Parsing, truncation, and network errors are explicit, not empty successful results.

## Saved representation

A merged extraction contains this layout (field names below are abbreviated for readability):

```text
source
  original_row                  # all original dataset columns and values
  original_row_hash
  record_id                     # unique processing key, possibly ::row-N
  source_id                     # original dataset ID
  text
  provenance
    dataset                     # ID, pinned revision, row index, source file, links
    upstream                    # component, source record ID, declared terms
    article
      pmid, pmcid, doi
      pubmed_url, pmc_url, doi_url
      identifier_evidence, identifier_conflicts
      resolution_status, resolved_at, resolver_url
    source_url
  multimedia
    status
    has_figures                 # true / false / null (unknown)
    has_image_urls              # manifest-listed image assets, not patient matches
    image_urls                  # may include article assets not matched to a figure
    versions
      version, metadata_url, source_xml_url
      metadata_retrieval, xml_retrieval
      license_code, is_manuscript, is_retracted
      article_rights_statements
      figures
        figure_id, label, caption, alt_text
        group_id, group_label, group_caption
        article_anchor_url
        graphic_references, unresolved_graphic_references
        image_urls, media, rights_statements
        association_scope: article
        patient_assignment: unverified
        clinical_modality: null
        training_approval: not_reviewed
      unassigned_media
sections                        # clinical extractions; never figure-derived guesses
generations                     # returned reasoning; never clinical source evidence
```

Each media entry records the exact manifest-derived URL, original S3 URL, filename, extension-based file category, supplied MD5, and `http_verified: false`. Image URLs come from actual entries in a successful metadata response; the code never invents a `.jpg` suffix for an XML reference. A reference without an extension can match an existing manifest entry with that stem. When matching fails, the reference is retained unresolved.

The article-level `image_urls` convenience list can include supplementary images, not only clinical figures. Prefer `versions[].figures[].image_urls` for figure-associated files. A suffix-based `kind=image` is a file classification, not a validated clinical modality. Multi-panel figures are not automatically segmented, and figure groups retain their shared caption where present.

All discovered article versions remain separate. The highest version number is not assumed to be the preferred publication version. Retrieval timestamps, hashes, ETags, and last-modified headers are retained where available because article files can change without a new version number. A URL listed at discovery time is not guaranteed to work forever; this stage deliberately does not request its image body to test it.

## Availability semantics

| Status | Meaning |
|---|---|
| `figures_with_image_urls` | JATS figures found and at least one matched image URL is present in PMC's manifest. |
| `figures_without_image_urls` | Figures exist in the available XML, but none has a matched distributed image URL. |
| `no_figures_found` | All discovered article-version XML parsed successfully and contained no figure markup. This is scoped to that XML. |
| `image_assets_without_figure_markup` | Manifest image assets exist but were not described by detected figure markup. |
| `no_pmc_mapping` | The converter did not provide a PMC article for the source identifier. Publisher figures remain unknown. |
| `no_distributed_article_files` | No article-version metadata objects found in the permitted PMC dataset. Not a finding that the article has no figures. |
| `no_article_identifier` | This row has no documented source article ID; keep its original collection provenance. |
| `partial`, `lookup_failed`, `identifier_lookup_failed` | Some lookup/parsing failed. Available information is retained; missing information is not made negative. |
| `not_checked_offline` | Required information is absent from the offline cache. |
| `identifier_conflict` | Conflicting source identifiers; do not attach article media to the case. |

Negative cache entries expire sooner than successful discoveries. Offline mode can use expired entries but marks them `cache_stale: true`. `--refresh` requests fresh network data; it does not authorize image downloads. No failure is retried forever.

## First CPU run

```bash
uv sync --extra data
export NCBI_EMAIL='your-real-maintainer-email@ufl.edu'
uv run op2 download-dataset --output data/prepared.jsonl

# A fixed random pilot is more informative than only the first N dataset rows.
uv run op2 sample --input data/prepared.jsonl \
  --output data/source-pilot.jsonl --size 64 --seed 42
uv run op2 enrich-sources --input data/source-pilot.jsonl \
  --output data/source-pilot-enriched.jsonl --config configs/sources/pmc.yaml

# Full run reuses the same cache, including the pilot's article discoveries.
uv run op2 enrich-sources --input data/prepared.jsonl \
  --output data/enriched.jsonl --config configs/sources/pmc.yaml
```

`configs/pipelines/k2.yaml` and `motif.yaml` consume `data/enriched.jsonl`; clinical prompt/token profiling happens after enrichment. Article captions/URLs are **not** appended to the clinical prompt; the source description remains the evidence for text extraction. The CPU Slurm preparation script includes this stage before model serving. A standalone CPU enrichment script is provided too. Do not allocate B200s for source lookup.

For output `data/enriched.jsonl`, the command also creates:

- `data/enriched.articles.jsonl`: one deduplicated article manifest per unique PMCID.
- `data/enriched.enrichment.json`: record/article counts, status distribution, failures, cache/network accounting, and output paths.

The enriched JSONL is self-contained; repeated case rows from one paper share a single network discovery but each carries its lineage and article candidates. File outputs are written via temporary files; failed rows are retained with error statuses. The CLI returns nonzero if actual lookup errors/conflicts need attention, even when a useful partial output was produced. Correct the issue and rerun; cached successful article results are reused.

An offline smoke test requires neither credentials nor external services:

```bash
uv run op2 sources-demo --output runs/sources-demo
```

The demo is authored mock data and explicitly labels its fake PMIDs/PMCIDs and URLs. Its image endpoints raise errors if requested. It exercises shared articles, no-figure XML, figures without distributed images, no PMC match, and repeated educational IDs. Its URL counts are **not a coverage estimate for Open-Patients**.

## Clinical and training boundaries

An image belonging to the article is not necessarily an image of this particular patient. A paper can contain multiple cases, control specimens, methodology diagrams, or figures spanning cases. The extractor therefore never turns an article candidate into a patient-associated CT, histology slide, operation photo, or outcome automatically. Keep a reviewed case/figure/panel association and review of reuse terms before using it as a patient observation.

Dataset/component/article/figure terms are separate: Open-Patients advertises CC-BY-SA-4.0, while the upstream PMC-Patients card advertises CC-BY-NC-SA-4.0. Individual articles and third-party figures may have other conditions. These declarations are preserved, not reconciled into invented blanket permission. Review the source terms before training, redistribution, or commercial use. See [PMC copyright guidance](https://pmc.ncbi.nlm.nih.gov/about/copyright/). Merely storing a URL is not a training-rights approval.

For the future patient simulator, keep observed case evidence, article-context candidates, and authored synthetic EHR events separate. A caption/title may reveal a diagnosis or future outcome and should not automatically be visible to a clinical agent at presentation. The current code does not build a simulator or produce clinical events from the figures. Baichuan-M2 describes a Patient Simulator plus Clinical Rubrics Generator; that is the likely reference in the project discussion ([technical report](https://arxiv.org/abs/2509.02208)).

Source-article IDs now join exact duplicate/source-ID/grouping constraints in reasoning-study clusters and distillation splits, preventing two cases from the same known paper from crossing train/test splits. Unknown cross-paper duplicates still require review.

## Verification limits

Local tests and the mock integration exercise the actual HTTP client/parser/export paths without requesting images. A public ID-converter response was inspected via the browser and confirmed its JSON crosswalk shape. Direct HTTP in this build container failed DNS; browser access to per-article cloud JSON/XML was also unavailable. Consequently **no real end-to-end figure discovery, real figure URL reachability, full-corpus yield, GPU throughput, or clinical accuracy was measured here**. Use the fixed random CPU pilot above to measure corpus coverage in the connected environment. Failures are not grounds for a zero-figure conclusion.
