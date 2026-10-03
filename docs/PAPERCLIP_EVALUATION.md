# Paperclip evaluation for license-grounded patient acquisition

Checked **2026-10-02** against public primary documentation and a bounded live,
unauthenticated probe. Recommendation: retain official **NCBI ESearch + PMC Cloud**
as the acquisition path. Consider Paperclip later as an optional literature
research and candidate-ID source. Do not make it the corpus enumerator, license
authority, canonical article parser, or patient/figure attribution authority.

## What was tested

[`scripts/probe_paperclip.py`](../scripts/probe_paperclip.py) reads public installer
text and OpenAPI, sends a one-result read-only PMC search, and attempts one article
metadata GET. It never reads credentials, uses environment API keys, signs in,
installs software, executes the installer, changes skills, or fetches images.
The diagnostic output is [`reports/PAPERCLIP_PROBE.json`](../reports/PAPERCLIP_PROBE.json).

| Request | Live result | Response-body bytes |
| --- | --- | ---: |
| GET `https://paperclip.gxl.ai/install.sh` | 200; inspected as text only | 7,296 |
| GET `https://paperclip.gxl.ai/api/v1/openapi.json` | 200; complete schema saved | 20,841 |
| POST `/api/v1/search`, `sources=["pmc"]`, `query="case report"`, one result | 401 `auth_error` | 135 |
| GET `/api/v1/documents/PMC8654144` | 401 `auth_error` | 135 |
| **Total per completed run** | No article or image retrieval | **28,407** |

The sandbox initially denied DNS; the authorized bounded network probe succeeded
after escalation. Two completed passes fetched **56,814 response-body bytes** in
total, well below 2 MB. The second pass expanded installer-text diagnostics. The
saved report contains the latest pass and response SHA-256 hashes. The script's
2,000,000-byte cap applies to response bodies read per invocation, not transport
headers or overhead. No paid calls or credential use were attempted.

Authentication prevents an empirical coverage, parsing, caption, image-quality,
license-normalization, or case-report yield assessment. A 401 is not a finding
that the example PMCID is absent. No authenticated functionality is represented
as successfully tested.

## Documented surfaces and actual public contract

The vendor's [current documentation](https://paperclip.gxl.ai/docs) describes:

| Surface | Vendor claim; not independently validated |
| --- | --- |
| Coverage | PMC OA ~7.5M full texts; bioRxiv ~400K and medRxiv ~100K full texts, last updated August 7, 2026; OpenAlex ~50M abstracts, July 21, 2026. |
| CLI search | `--article-type` for PMC; up to 1,000 results. |
| CLI license discovery | `lookup license VALUE`; not a documented search license flag. |
| SQL/export | SQL: 200 rows, 15 seconds; export: 1,000 rows. |
| Account limits | 120 requests/s; 10 short and 8 long concurrent commands; 100 map/verify daily; API-key MCP 600 calls/minute. Paid limits negotiable; public price not established. |
| Authentication | OAuth or API key; MCP available; SDK recommended for scripts. |

The [official client repository](https://github.com/GXL-ai/paperclip) describes
`content.lines`, named section files, PMC figure/supplement directories, binary
figure downloads through redirected `cat`, and vision `ask-image`. Its SDK wraps
remote commands; client source does not establish server parser fidelity. The
client's Apache-2.0 license does not license the articles or their assets.

The **live [OpenAPI schema](https://paperclip.gxl.ai/api/v1/openapi.json)** gives a
more specific HTTP API contract (full snapshot and hash in the probe report):

| Question | Observed schema contract and implication |
| --- | --- |
| PubMed versus PMC | `SearchRequest.sources` says default `abstracts` is a PubMed/Crossref title-and-abstract index, explicitly not OpenAlex or PMC. `pmc`/`papers` is a separate full-text collection. The website's OpenAlex description must not be assumed to describe this API's default. Neither source proves a complete PubMed mirror. Specify the corpus explicitly. |
| Abstract versus full text | `SearchContents` has `abstract`, `highlights`, and `text` toggles; text defaults false. A hit/PMID/abstract is insufficient evidence that a full article is present. Returned content coverage remains untested. |
| Identifiers | `Identifiers` distinguishes `pmc`, `pmid`, DOI, NCT and arXiv IDs. `SearchHit.id` is an internal/source ID; `pmid` is nullable. Preserve namespaces and independently resolve candidates. |
| Search completeness | `num_results` is 1–200 over a deeper ranked candidate pool. No offset, cursor, exhaustive count, stable snapshot token, or continuation field appears in `SearchRequest`/`SearchResponse`. Fan-out supports at most 20 queries and deduplicates candidate pools by PMID/DOI/title. This is not an enumeration guarantee. |
| Date filters | `published_after`/`before` are described as year-granular aliases. Unknown years remain included. Do not emulate our day-partitioned discovery by assuming exact daily bounds. |
| Publication types and licenses | `SearchFilters` declares year/date aliases, author and journal only. No `article_type`, PubMed `PublicationTypeList`, MeSH or license filter is declared. `lookup` permits generic field/value, but this does not prove a licensed case-report census. CLI flags cannot be assumed to work as API JSON filters. |
| Document retrieval | GET `/documents/{doc_id}` is described as metadata retrieval. `DocumentResponse` declares bibliographic fields, identifiers, abstract and an unstructured `meta` object. It does not declare full JATS, figure records, captions, panels, asset URL lists, license evidence, version IDs, or object digests. `meta` could contain more; no actual response was accessible. |

These are absences from the inspected public contract, not assertions that
Paperclip's internal system never stores those fields. CLI/SDK/MCP and `/api/v1`
are distinct interfaces with different defaults and limits. Corpus totals and
refresh dates are vendor descriptions, not measured coverage or completeness.

## Patient evidence, figures and rights

For text, `content.lines` could help interactive reading, regex triage, and
reviewer navigation. We could not compare a parsed line against official XML,
check table order, identify omitted footnotes, separate bibliography from patient
narrative, or establish whether line numbers survive reindexing. Paperclip line
citations alone should not replace our evidence coordinates, article version,
retrieval hash and saved canonical source.

For media, documented binary figure access establishes a potential route to
pixels. It does not establish a durable **upstream** image URL, exact manifest
membership, original JATS `<fig>`/`<fig-group>` IDs, captions, credit lines,
graphic links, panel segmentation, patient association, or asset-specific rights.
`ask-image` produces model interpretation; it cannot prove which patient a panel
depicts. No pixels or model calls were requested in this evaluation.

License lookup can be useful triage, but it is insufficient for our acceptance
policy. We need the actual version's metadata plus scoped article permissions,
exact license version/jurisdiction, source statements and links, conflicts,
third-party exceptions, and review decisions. A search hit or an OA tag does not
supply that evidence. Rejecting unknown/ND terms or accepting a custom permissive
grant still belongs to the existing rights gate. The [PMC copyright notice](https://pmc.ncbi.nlm.nih.gov/about/copyright/)
also warns that third-party assets can have separate conditions.

## Why the official acquisition path remains appropriate

The [PMC Cloud documentation](https://pmc.ncbi.nlm.nih.gov/tools/pmcaws/)
provides anonymous, free retrieval, per-version metadata and a daily inventory;
it recommends subdividing ESearch queries beyond 10,000 results or using the
inventory. Version numbers can persist while files change, so retain hashes and
timestamps. The [bucket README](https://pmc-oa-opendata.s3.amazonaws.com/README.txt)
defines JATS XML, per-version license/retraction/manuscript flags, manifest-listed
media URLs, and MD5 digests. These directly support our auditable source packets.

PubMed's [publication-type field](https://pubmed.ncbi.nlm.nih.gov/help/#publication-type-pt)
supports a case-report discovery lane such as `Case Reports[pt]`, with the
documented indexing-lag caveat. Its [PMC subset](https://pubmed.ncbi.nlm.nih.gov/help/#pubmed-central-subset)
can constrain linked full texts. Crosswalk returned PMIDs before retrieving PMC
files. Our broader PMC lane remains necessary for individual patients in other
article types, and official inventory is preferable for a census. Source-file
availability and article rights must still be checked independently of discovery.

| Pipeline need | Current official workflow | Paperclip decision |
| --- | --- | --- |
| Resumable broad acquisition | Partitioned queries/inventory, durable queue, explicit caps and incompleteness | Keep official workflow; no documented complete enumeration contract |
| Clinical source parsing | Saved JATS with stable local hashes, source block/figure relationships | Optional reviewer convenience only until measured against JATS |
| Rights decision | Version metadata plus JATS permissions and asset exceptions | Never substitute a license lookup or model answer |
| Figure URLs without pixels | Manifest-to-JATS matching with attribution and unresolved links | Keep official manifests; binary access would add unnecessary downloads |
| Literature exploration | Query lanes and seed IDs | Paperclip may add candidate IDs and hypothesis searches after auth |
| Large extraction campaigns | Local source packets and our own audited models | No basis for outsourcing to map/vision under unverified quotas, pricing, fidelity |

## Small authenticated follow-up, only if access is later provided

Compare a fixed 10–20-PMCID sample against the saved official packets: a case
report, multi-patient series, individual cases in a research article, tables,
multi-panel figures, supplements, alternative versions, third-party credits,
ND/unknown/custom permissive licenses, and recently added articles. Measure ID
resolution, text completeness, exact figure/caption/credit retention, upstream
URL availability, line stability, missingness, latency and billed cost. Do not
estimate coverage from only successful search hits. Reconcile explicit misses.

Before bulk use, establish an exhaustive enumeration mechanism, frozen-source
provenance, removal/retraction handling, file-version evidence, license/asset
terms, and a written cost/quota model. Until then, import any Paperclip-discovered
PMCIDs as seeds into the **existing official acquisition queue**, where normal
rights and roster checks still apply. No runtime integration or acquisition
policy change was made as part of this evaluation.
