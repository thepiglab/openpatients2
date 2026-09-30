# Extraction integration — v0.6.0

The implementation promotes the conservative mechanisms supported by the local
studies. It does not treat their small, assistant-reviewed checklists as an
independent medical benchmark or change historical scores.

## Source routing

JATS and official PMC metadata remain canonical for article/version identity,
license decisions, the patient roster, citation links, figures, supplements,
summaries and timelines. Clinical extraction can read official PMC TXT instead.
Each alternate view has its own content hash, segment IDs, source offsets,
retrieval record and a digest bound to the canonical XML version. Clinical facts
cite the actual view shown to the model. Patient identity evidence and figure
assignments continue to cite JATS; they are never relabeled as TXT evidence.

Acquire both JATS and official TXT with bounded downloads:

```bash
uv run op2 article-fetch --input data/candidates.json --output data/articles.jsonl \
  --limit 100 --max-bytes 100000000 --source-view pmc_text --max-asset-bytes 12000000
```

URLs must come from the version-matched official manifest. Downloads reject
redirects, oversized responses, identity mismatches and supplied MD5 mismatches.
TXT header identity, version and license must agree with JATS. Tables retain
tabs, spaces, row order and headings. Front matter is removed only at the official
boundary. Only bibliography lines matching JATS citation text are removed;
unmatched material and later captions/tables remain available. This is deliberately
more conservative than the experimental blanket References cutoff.
If an acquired TXT representation contradicts the article license, the article
is quarantined; that conflict is not treated as an ordinary format fallback.

`clinical_source: pmc_text` selects TXT. Missing/failed optional acquisition
records a reason and uses JATS. A corrupted or mismatched stored view raises an
error. Alternate views require `packet_scope: whole_article`: patient-local JATS
spans cannot safely be guessed onto another format. The model gets the shared
patient registry and target label, but canonical identity quotes are kept out of
TXT/PDF prompts. This separation reduces evidence confusion; it does not prove
that the model assigned every fact to the correct patient.

The [Spark workflow](../configs/experiments/article-workflow-spark.yaml) uses TXT
when available, prompt-only JSON, a 16,384-token clinical allowance, one bounded
repair round and separate figure attribution. The [Glimmer workflow](../configs/experiments/article-workflow.yaml)
keeps JATS because TXT did not consistently win for Glimmer. These are sharded
article-runner examples, not a million-article scheduler. Check the configured
budget before running; the ledger ceiling is not the balance of an API account.

## Optional Firecrawl PDF path

```bash
uv sync --extra pdf
uv run op2 article-fetch --input data/candidates.json --output data/articles-with-pdf.jsonl \
  --limit 10 --source-view pdf_firecrawl --max-asset-bytes 12000000
```

The pinned dependency is `pdf-inspector==1.25.2`. Only its native
`detect_pdf`/`extract_pages_markdown` APIs are called. No OCR models, page images
or supplementary archives are fetched. The PDF lives in a temporary directory
that is removed on success or failure. The default limit is 12 MB and 100 pages.
See the [official parser API](https://github.com/firecrawl/pdf-inspector/blob/main/docs/python.md).

Page provenance and native OCR/table/column flags are stored. Every PDF page
also retains a layout review requirement: native OCR flags missed the pilot's
merged table/prose problem. `clinical_source: pdf_firecrawl` therefore falls back
to JATS by default. `allow_pdf_review: true` explicitly permits an unreviewed PDF
input while carrying its unresolved review queue into the bundle and EHR seed.
It does **not** mean layout was approved. Automatic region selection, OCR and
automatic vision escalation are not implemented; the queue identifies those
follow-up tasks. PDF bibliography is retained because a column-local heading is
not a reliable whole-document cutoff.

## Recovery and repair

Both the clinical runner and the article runner enable these independent options:

- `recover_identical_duplicates`: collapse only identical JSON duplicate values,
  with an audit entry. Conflicting duplicates and type differences such as `true`
  versus `1` fail. Complete fenced JSON is supported; unfinished output is not.
- `recover_citation_formatting`: normalize an exact displayed segment label and
  a uniquely matching whitespace-equivalent quotation back to the actual source.
  Save the original, replacement, segment and offsets. Never change a clinical
  value, unit, time, absent quote or glyph code. Never search another segment to
  repair an explicitly wrong citation. Application validation runs afterward.
- `targeted_item_repair`: freeze accepted neighbors and ask for replacements only
  for failed items, in order. Revalidate every replacement. Reject removal of
  documented measurements, units, doses, specimen or time fields to make a gate
  pass. Each request contains at most eight failed items and 16,000 serialized
  characters; larger/unsupported cases use the existing full-section retry.

`validation_retries` bounds refinement rounds (default one). Item repair applies
to the single-`items` domains. Case context and multi-collection oncology retain
full-section retry. Truncation uses a larger output allowance up to the configured
cap; partial JSON is never treated as evidence. Transport failures, parse failures
and clinical-field failures remain distinct in saved attempts.

If repairs remain unresolved, accepted items are exported as a `partial` section
with `coverage: limited`, and the rejected candidates/reasons remain in quality
checks. The patient is not marked complete. If no items survive, there is no
clinical section pretending that nothing was documented. All raw attempts and
recovery operations are retained. Source validity and successful repair still
do not establish medical entailment; numeric and temporal normalization can
require independent review.

The lower-level parser/validator stay strict by default for controlled studies.
The runners enable these options explicitly. Their resume signatures include
the recovery policy and retry bound, so changing them does not reuse another
policy's output as if it were a new result.

## Attribution and downstream exports

The article runner enables `figure_attribution: true` by default and makes one
focused call per figure. Shared panels, external comparators, unresolved ownership,
whole-image URLs, asset rights and pixel-inspection status survive patient-bundle
assembly. Missing reviews do not silently fall back to broad roster ownership.
The figure cap defaults to 6,144 tokens and a retry can increase it to 12,288.

Bundle integrity hashes now include the final reviewed assignments. Reloading a
normalized packet verifies successfully. EHR-seed export keeps TXT/PDF fact
provenance separate from canonical JATS companion evidence. It does not guess
cross-format fact-to-timeline links when segment IDs differ. Citation-guided
cross-article enrichment remains separate from explicit same-patient identity
and from future synthetic composition.

Chunking/window sizes, span-first tagging, semantic critics and hierarchical
reducers remain in the experimental harnesses. Their benefits were model/task
dependent; summary-only extraction lost facts. No new patient-merging rule,
ontology download, clinical diagnosis inference or automatic medical correction
was introduced by this integration.

## Verification

The software suite covers native parser routing, version/license/digest checks,
download caps, literal segment binding, duplicate-key conflicts, targeted repair,
partial exports, source namespace separation and figure-bundle round trips.
Three saved study articles were prepared through the new TXT path. The actual
native parser was smoke-tested on a generated text PDF. These are software checks,
not a new paid model comparison or clinical benchmark. No model requests were
made during integration.

The pre-integration runtime is preserved with hashes under
`runs/implementation-v1/pre-integration/`; old experiment protocols and outcomes
were not rewritten to match the new code. Their live-file hash guards are expected
to detect the production changes. Use that runtime snapshot to reproduce an old
study, and a new protocol/output directory for new experiments.
