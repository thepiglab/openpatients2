# Resumable PMC source acquisition

Implemented 2026-10-02, campaign format `pmc-acquisition/2`. This is a CPU-only acquisition queue using official NCBI
ESearch and PMC Cloud endpoints. It retrieves metadata and JATS XML, preserves
figure URLs/captions and supplement manifests, and exports the article packets
used by the extraction pipeline. It does not run a model, download weights,
scrape publisher pages, or fetch image/PDF/video/supplement payloads.

Use a separate checkout while the Glimmer benchmark runs: changing files in that
benchmark's active checkout would violate its pinned runtime manifest. The small
standalone archive `dist/openpatients2-pmc-acquisition.tar.gz` contains the source,
locked environment, acquisition configs and scripts needed to run independently.
It contains no article corpus, environment, credentials, model weights or images.

## Small pilot

Run from a checkout or the extracted package:

```bash
uv run --locked --python 3.12 --no-dev op2 corpus run \
  --config configs/sources/corpus-pilot.yaml \
  --work-dir /tmp/op2-pmc-pilot \
  --limit 3
```

The pilot config limits discovery to 200 candidates, network response bodies to
32 MB per invocation, individual XML files to 5 MB, and the work directory to a
256 MB allowance. `--limit` bounds **fetch attempts**, not discovery results or
successful patient cases. If you want a smaller discovery sample too, copy the
config and lower `max_candidates`. No large downloads are required to test this.

Resume with the same command/config, or process another bounded fetch batch:

```bash
uv run --locked --python 3.12 --no-dev op2 corpus fetch \
  --work-dir /tmp/op2-pmc-pilot --limit 20

uv run --locked --python 3.12 --no-dev op2 corpus status \
  --work-dir /tmp/op2-pmc-pilot
```

For automated local testing and cleanup:

```bash
uv run --locked --python 3.12 python scripts/smoke_pmc_acquisition.py \
  --output /tmp/op2-pmc-smoke-report.json
```

This smoke command discovers at most three candidates, limits XML to 500 KB each,
checks resume and compressed export, and deletes its temporary database, source
bodies, seeds/config and exported file on exit. Only its small diagnostic report
is retained. Discovery/fetch/resume each have a 4 MB per-invocation network cap.
For a local live smoke, also set `max_network_bytes_total: 4000000` in its temporary
config so the combined resumed campaign remains below 5 MB.

## Bounded stratified CPU pilot

`configs/sources/corpus-stratified-pilot.yaml` is the default cluster pilot:
**400 candidate lane memberships** across cardiovascular, neurology, oncology,
infectious disease, pulmonary, gastroenterology, renal/urology,
endocrine/metabolic, rheumatology/dermatology and hematology topics. Each topic
has a 30-candidate case-focused query and a 10-candidate broader query. Broader
queries retain other article types, which can still describe original patients.
Query membership is a search topic label, not a validated clinical specialty.

Discovery cycles through every lane using five-ID pages and durably resumes its
position. Each lane has its own enforceable quota. Overlapping IDs count toward
each matching lane's quota and are fetched only once, so the unique queue may be
smaller than 400. A sparse lane stays visibly sparse; its unused quota is not
silently transferred to another topic. Supplying any quota requires quotas for
every lane and a sum no greater than the global candidate cap.

This is a **purposive, latest-first query sample**, not an unbiased sample or
complete inventory. Within recursively split date windows, recent partitions
are visited first and each ESearch page uses publication-date order. The 2024–2025
window and specialty keywords deliberately limit coverage. Quota stops are
reported as `quota_reached`, and
`discovery_complete_for_configured_queries` remains false for those searches.

The pilot permits 128 MB decoded network bodies per invocation, **2 GB cumulative
network budget over all resumes**, 4 GB campaign storage, 5 MB/XML and 1,000
requests per invocation. These are independent ceilings, never download targets.
Fetch in batches of 25 attempts with `fetch_concurrency: 4` asynchronous download
workers sharing the campaign's request/byte budgets and rate limiters. The setting
accepts 1–8 workers; more concurrency does not raise the official request rates.
Each raw XML body stays task-local. A budget stop cancels and drains the whole
active wave before closing the HTTP client or SQLite store. The CPU script accepts an explicit smaller
limit. A larger pilot needs a copied config and a new independent campaign;
raise explicit quotas/caps only for cluster storage. Account for aggregate budgets
when running multiple campaigns: each directory enforces its own cumulative cap.

SQLite discovery receipts preserve selected ID pages, query parameters,
offsets, date boundaries, counts, translations, response hashes, retrieval times,
bytes and response latency. Exported eligible packets include `acquisition.sample`
with lane/topic/focus/query provenance, candidate origins, partition state, the
selecting receipts and a config hash. Aliases `acquisition.discovery_lanes` and
`acquisition.origins` aid downstream grouping; version and rights stay in the
article's existing fields. Retrieval receipts include `elapsed_seconds`;
`acquisition.elapsed_seconds` includes article retrieval and throttling.
Invocation status reports decoded bytes, elapsed time and bytes/second. These
measurements support CPU download throughput analysis at the existing rate limits.

## HiPerGator: use a separate directory while benchmarking

From the Mac, upload only the small acquisition package:

```bash
scp dist/openpatients2-pmc-acquisition.tar.gz \
  wkieffer@hpg.rc.ufl.edu:/blue/cai6734/ehr_agent/
```

On HiPerGator, extract into a new directory and submit the CPU job:

```bash
cd /blue/cai6734/ehr_agent
mkdir openpatients2-acquisition

tar -xzf openpatients2-pmc-acquisition.tar.gz -C openpatients2-acquisition
cd openpatients2-acquisition

sbatch scripts/pmc_acquire.sbatch \
  /blue/cai6734/ehr_agent/pmc-corpus/run-01 \
  configs/sources/corpus-stratified-pilot.yaml \
  25
```

The script requests **2 CPUs, 8 GB RAM, no GPUs**, account/QoS `cai5724`, and six
hours. It can wait for CPU/RAM allocation if the GPU benchmark already uses the
account's allocation. Do not request GPUs for acquisition. No cluster submission
was made during development.

For separate discovery and fetch stages:

```bash
uv run --locked --python 3.12 --no-dev op2 corpus init \
  --config configs/sources/corpus-stratified-pilot.yaml \
  --work-dir /blue/cai6734/ehr_agent/pmc-corpus/run-02

uv run --locked --python 3.12 --no-dev op2 corpus discover \
  --work-dir /blue/cai6734/ehr_agent/pmc-corpus/run-02

uv run --locked --python 3.12 --no-dev op2 corpus fetch \
  --work-dir /blue/cai6734/ehr_agent/pmc-corpus/run-02 --limit 25
```

`configs/sources/corpus-hpg.yaml` is a separate expansion configuration rather
than the pilot default. It permits 100,000 candidates, a 100 GB work-directory allowance,
2 GB of response bodies and 20,000 requests per invocation, 12 MB/XML, and 10 GB
minimum free disk space. Its omitted cumulative network setting takes the enforced
150 GB maximum default. These caps are settings, not forecasts of corpus size.
Resubmit the same command to continue another batch; an advisory file lock
prevents two owners from writing the same campaign simultaneously.

Inspect progress:

```bash
uv run --locked --python 3.12 --no-dev op2 corpus status \
  --work-dir /blue/cai6734/ehr_agent/pmc-corpus/run-01
```

Export for the extraction runner (plain JSONL or `.jsonl.gz` are supported):

```bash
uv run --locked --python 3.12 --no-dev op2 corpus export \
  --work-dir /blue/cai6734/ehr_agent/pmc-corpus/run-01 \
  --output /blue/cai6734/ehr_agent/pmc-corpus/articles-01.jsonl.gz \
  --max-bytes 2000000000
```

Export has a separate explicit **uncompressed** size cap and refuses to overwrite
existing files. Its output is outside the campaign storage allowance, so put large
exports on cluster storage. Byte-limit failures remove only the export's own
partial file. Normal article readers now stream gzip JSONL directly.

## Discovery and corpus coverage

The original small local and expansion configs have two lanes. All configured
lanes take turns during discovery:

- Case-focused phrases prioritize likely patient material.
- A broader OA lane keeps eligibility open to individually described patients in
  other article types. Article type is not a hard rejection rule.

Each lane has an explicit publication-date window and a broad OA query. Discovery
uses `open access[filter] NOT pmc embargo[filter]`; it does not restrict candidates
to named CC families, which would miss permissive non-CC papers.
The date window is recursively divided until each partition has at most 9,000
results, below this implementation's conservative 10,000-query ceiling. It pages
small ID batches and records counts, query translation, boundaries, offsets and
query membership. Overlap is deduplicated without losing discovery origins.

A single day that is still too dense is marked `needs_inventory`; it is never
silently truncated. Changed counts, unexpectedly empty pages or duplicate IDs
that prevent reaching the expected distinct count produce `unstable` partitions.
NCBI search is live, not a frozen snapshot. A completed set of configured queries
is not a claim to have enumerated all PMC articles. Missing publication dates,
indexing changes, date-window choices and keyword choices can all affect coverage.

The candidate cap stops discovery with an explicit incomplete status. Filling a
case/exploration queue is not a random sample: a separate seeded sampling stage
is needed for unbiased yield estimates. Use the downstream roster gate before
claiming that an eligible article contains original individual patients.

An `eligible` source packet carries a conservative priority route, not a model
assessment. Abstract/body/table/caption text and references are preserved in their
appropriate roles; bibliography is omitted from ordinary clinical prose while
citation links remain available for later source tracing.

## Existing dataset seeds and large inventory runs

PMC-Patients, MultiCaRe and reviewed citation plans can supply seed IDs. Create a
UTF-8 file with one `PMC123` or explicitly pinned `PMC123.2` per line. There is no
need to download the original dataset's images. Initialize with a config having
`lanes: []` for a seed-only campaign, then:

```bash
uv run --locked --python 3.12 --no-dev op2 corpus init \
  --config configs/sources/corpus-pilot.yaml --work-dir /tmp/op2-seeded

uv run --locked --python 3.12 --no-dev op2 corpus import \
  --work-dir /tmp/op2-seeded --input pmcids.txt

uv run --locked --python 3.12 --no-dev op2 corpus fetch \
  --work-dir /tmp/op2-seeded --limit 20
```

The example initializes the supplied pilot config, but `fetch` does not execute
its discovery lanes. To avoid pending unused search partitions in status, use the
recommended copied config with `lanes: []`. Imported IDs are still independently
checked for metadata rights, retraction and version ambiguity.

For broad census work, use the current official **PMC metadata inventory**, not
one giant ESearch query or the retired legacy archives. Download a selected
inventory manifest and its CSV shards **on cluster storage**, using the official
[PMC Cloud README](https://pmc-oa-opendata.s3.amazonaws.com/README.txt). Preserve the
manifest, its listed checksums and retrieval date. Do not download the whole
inventory locally just to try the pipeline.

The importer reads an existing CSV or gzip CSV as a stream:

```bash
uv run --locked --python 3.12 --no-dev op2 corpus import \
  --work-dir /blue/cai6734/ehr_agent/pmc-corpus/run-01 \
  --format inventory --input /blue/cai6734/ehr_agent/pmc-inventory/shard.csv.gz \
  --file-schema 'Bucket, Key, LastModifiedDate, ETag'
```

Use the **actual `fileSchema` from that manifest**, not an assumed column order.
Only keys matching `metadata/PMC<id>.<version>.json` in `pmc-oa-opendata` enter the
queue. Exact versions can skip a per-article version-listing request. Inventory
rows do not themselves establish license eligibility or current-version status;
metadata and XML are checked when fetched. Multiple explicit versions stay
separate until an explicit version-selection policy decides their use.

Import progress is durable per input path, size, mtime and schema. A resumed gzip
import scans past its committed prefix without storing another decompressed copy.
The importer does not download or cryptographically authenticate an external
inventory file; verify the downloaded shard against its manifest separately.
Treat changed inventory snapshots as new campaigns when refreshing the corpus.
For millions of records, use disjoint manifest shards/work directories on cluster
storage. This version is a single-owner queue, not a distributed crawler; throttle
aggregate NCBI request rates across workers. One client uses 2 ESearch and 4 Cloud
requests/second by default, with bounded retries for 429/transient failures.

## What is stored, rejected and deferred

The SQLite store holds the queue, partition/import progress, discovery origins,
compressed immutable raw XML, compressed parsed article packets, content hashes,
retrieval timestamps and rejection/failure reasons. Body insertion and completion
are one transaction; interruption cannot mark a missing article as successfully
stored. Completed records are skipped on resume. Retryable failures have an
attempt ceiling and never turn into empty successful articles.

Raw XML and metadata provenance let us audit parsing later. XML URL MD5 checks
are verified whenever supplied by the official manifest, and SHA256 hashes are
retained for all downloaded bodies. Arbitrary hosts, redirects and media responses
are rejected. Explicit IDs/version prefixes and XML/metadata identities must agree.

Metadata rights are checked **before XML**. Known ND grants and failed
OA/retraction checks stop there. Unknown OA license codes proceed to bounded XML
rights inspection, so an explicit permissive article grant is not missed.
JATS rights are checked again afterward. CC0, CC BY, CC BY-NC and CC BY-NC-SA
are accepted with version-specific obligations; CC BY-SA has a separate source-terms
lane. Named permissive licenses including MIT, Apache 2.0, BSD and ISC are accepted
when they apply to the article. Explicit worldwide public-domain declarations and
Public Domain Mark are recognized. Figure exceptions remain attached to assets.
See the [full license policy](ARTICLE_LICENSE_POLICY.md) for supported terms,
version handling, release obligations and exclusions.

Unknown or conflicting grants produce `license_review` with retained terms and
source provenance. They are excluded from normal article exports, but can be
exported for review without downloading or retaining a clinical body:

```bash
uv run --locked --python 3.12 --no-dev op2 corpus export \
  --work-dir /tmp/op2-pmc-pilot --license-review \
  --output /tmp/op2-pmc-license-review.jsonl
```

This exports the stored review decisions; it makes no new network requests and
does not approve those sources. Presence in PMC or an existing dataset is not
itself a reuse permission. [PMC OA guidance](https://pmc.ncbi.nlm.nih.gov/tools/openftlist/).

Version selection prefers one unambiguous eligible published version over author
manuscripts. Multiple eligible published versions are deferred for explicit
selection; a larger numeric version is not assumed to be the right version.
No-body records are also deferred, not passed to an LLM as full clinical sources.

We retain figure links/captions/panel hints and supplementary descriptions,
formats, likely-tabular flags and URLs. Those assets remain **uninspected**.
Download selected pixels later on the CPU stage of the extraction workflow,
subject to their own rights/hash/size checks. Clinical image interpretation is
not inferred during acquisition.

Metadata status is as of retrieval. Before public release, refresh correction,
retraction and reuse metadata with a new dated acquisition/review; resuming an
old completed queue does not secretly recheck or overwrite its original records.
No same-person merge is performed from citation links or similar case text.

## Limits, completion and failure handling

- Config and acquisition/parser implementation hashes are pinned to the campaign.
  A changed config/code needs a new campaign; old results remain readable/exportable.
- SQLite reserves roughly half the directory budget for its main database and the
  rest for rollback journals and small side files. It enforces a page limit and
  checks available filesystem space before requests. Do not place unrelated large
  files in the campaign directory.
- Per-file and per-invocation limits apply to decoded response bodies, including
  received bodies later rejected. `max_network_bytes_total` additionally bounds
  all resumed invocations of a campaign and cannot exceed 150,000,000,000 bytes.
  Its default is this maximum; choose a smaller explicit pilot cap. Network
  allowances are distinct from compressed storage and export allowances.
- Before every decoded application read, SQLite commits a reservation of up to
  64 KiB. A successful read atomically replaces it with the received byte count.
  Cancellation, interruption or a transport failure leaves an unconfirmed
  reservation charged permanently, so restarting cannot bypass the cap. Status
  shows decoded bytes, unconfirmed reserved bytes, their charged sum and the
  remaining allowance. Reservations are never automatically reset. A cap stop
  can leave less than one chunk unused or an exactly full final chunk unaccepted
  when EOF cannot safely be probed; this is deliberate fail-closed accounting.
  Decoded chunks delivered to the acquisition loop never exceed the network
  allowance. HTTP/transport/decoder buffers and headers are outside this
  application-level counter.
- Parsed JSON has its own size cap. Figures/supplements do not bypass the network
  allowance because their payloads are never requested.
- Budget stops keep work pending; they are not evidence of source ineligibility.
  A cap too small for even one article's metadata/XML sequence must be raised in a
  new configuration/campaign. A full candidate/storage allowance likewise needs
  deliberate sharding or a larger new campaign, not an automatic unlimited retry.
- `completed_invocation` means the command finished its bounded work.
  `fetch_queue_complete` means no pending/retryable IDs remain, even if some were
  rejected, failed or need version review. Inspect those counts.
  `discovery_complete_for_configured_queries` requires all search leaves completed;
  imported inventory coverage is reported separately under `imports`.
- Query errors and transient search failures preserve the current partition cursor.
  There is no whole-PMC success flag that can hide an inaccessible search tail.

## Verification

The original pre-v3 live smoke test examined three candidates: two passed both rights checks and
one was rejected at the JATS license check. The retained packets contained nine
figure references in total. Both accepted XML objects matched their manifest MD5.
Resume issued **zero** extra requests; compressed export round-tripped successfully.
Total decoded network data was **107,275 bytes** and temporary files occupied
**108,896 bytes** before automatic cleanup. Every source body, export and temporary
database was deleted. Only the small [diagnostic report](../reports/PMC_ACQUISITION_SMOKE.json)
remains; no clinical correctness or patient-case yield is claimed from this test.

Software tests exercise rejection-before-download, checksums, canonical URL
restrictions, resume, retries, version ambiguity, search splitting and changing
counts, quota fairness and overlap, interrupted reservations, cumulative cap
enforcement, source/query provenance, inventory deduplication, locks, actual SQLite size enforcement, gzip reads
and export limits. No model, ontology, image or large inventory downloads were
performed during development.
