# Clinical Article Library

Browse every acquired article with its source text on the left and extracted
patient records on the right. The viewer reads existing data; it never runs an
annotator, writes review receipts, or rebuilds the dataset export.

From the repository:

```bash
uv run --locked op2 viewer
```

Open **http://127.0.0.1:8765**. Leave the command running. No model, Node install,
extra dependencies, or article downloads are needed.

The default library combines `data/dense_clinical_cases_v1` with live sources and
reviewed targets in `benchmarks/dense50` and `benchmarks/dense500`. There are no
article IDs embedded in the page. New source files enter the catalog, and new
reviewed targets appear automatically. The page checks every 15 seconds and when
it becomes visible again; Refresh checks immediately. It preserves the selected
patient, section, and panel scroll positions on an automatic update.

## What you can inspect

- Search by title, article ID, or specialty; filter by specialty and review stage.
- Select each patient independently and view every available clinical schema.
- Read summaries, measurements and units, event records, and relative event edges.
- Click a source citation to scroll to and highlight its exact quote.
- View actual image links, reviewed visual descriptions, clinical classifications,
  panel/patient attribution, caption claims, and figure rights.
- Inspect the roster, supplements, provenance, license, and complete JSON payload.
- Copy a deep link to an article, patient, and section using the browser URL.

Timeline events are not silently converted into a total chronological order.
Their explicitly supported relations remain visible, including uncertainty and
partial order. An empty clinical field means missing documentation, not a
negative finding.

## Freshness and review boundaries

Live schema submissions take priority over exports. The browser checks source,
annotation, target, and both schema review hashes; visual descriptions also need
both pixel review receipts and matching patient identity. Changed submissions
with stale receipts are withheld. A stale exported record cannot override a
changed live annotation. Matching enriched exported bundles retain normalized
measurements and media columns. Work-in-progress drafts are never published.

“Schema reviewed” and “source reviewed” are distinct stages, not synonyms for
all article components being completed. Missing or unreviewed media are labeled
separately. These are AI reference annotations, not physician-adjudicated gold.

Published exports can be served without the annotation workspace. If the original
source packets are absent, the UI shows the original PMC link and explicitly
states that the local reading surface is unavailable.

## Another corpus or port

```bash
uv run --locked op2 viewer \
  --dataset /absolute/path/to/export \
  --source-root /absolute/path/to/annotation-workspace \
  --port 8770
```

Repeat `--source-root` for multiple annotation workspaces with the same layout.
The export uses the existing JSONL contracts: manifest, annotations, patient
bundles, article bundles, and reviewed figure/supplement outputs. Sources are
`SOURCE_ROOT/articles/ARTICLE_ID/source.json`. Source and review discovery happen on each
refresh, including files added after the server starts.

## Hosting a website later

Create a portable static site containing the UI, article catalog, reviewed
records, and installed source text:

```bash
uv run --locked op2 viewer --build site/clinical-library-$(date +%Y%m%d-%H%M%S)
```

Serve that generated directory from any static web host. It has no Python runtime
or machine-specific file dependencies. URLs work under a site subdirectory too.
Image URLs point to their original sources; the build does not duplicate image
files or retrieve missing images. Source authors, licenses, original links, and
review provenance remain in the payloads. Third-party figure rights remain
separate from article licensing.

The live server updates automatically from working files. A static deployment
is a snapshot: run the build and redeploy when the dataset changes, for example
as a later publication workflow. Builds require a new or empty output directory;
existing files are never overwritten. If inputs change during a build, it fails
rather than claim the output is a consistent snapshot.

The local server binds to loopback by default and serves only its known UI/API
routes, not the repository filesystem. For a public dynamic deployment, put it
behind a production web server with HTTPS and caching, or use the static build.
