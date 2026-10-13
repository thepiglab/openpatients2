# Clinical Article Library

Browse every acquired article with its source text on the left and extracted
patient records on the right. The viewer reads existing data; it never runs an
annotator, writes review receipts, or rebuilds the dataset export.

From the repository:

```bash
uv run --locked op2 viewer
```

Open **http://127.0.0.1:8765**. Leave the command running. The first page is an
overview dashboard. It shows headline counts, donuts for review status, patients
per article, and article type, bars for facts by record section, and coverage
meters for media and each specialty. Every segment, bar, and meter links into the
viewer (`/viewer.html`) with that filter applied, e.g.
`viewer.html#specialty=cardiac` or `viewer.html#status=schema_reviewed&patients=5+`.
Older links of the form `/#article=…` are forwarded to the viewer.

## Cohort builder

**Cohort builder** in the header opens `/cohort.html`. It filters reviewed
patients by specialty, age group, sex, course, condition, medication, and article
features such as figures or supplements. Choices within a group are combined with
OR, and groups are combined with AND. Each option shows how many patients it would
leave. The filters are kept in the URL, so a cohort can be bookmarked or shared.
Each row opens that patient in the viewer.

**Export JSONL** streams one line per patient: the full reviewed record, plus its
article ID, patient ID, PMCID, title, specialty, license, and source link. Records
are re-checked against live review receipts at export time. **Download CSV**
saves the table's summary columns. Ages come from the record's demographics, as
documented or parsed from phrasing such as "13 years 6 months" or "DOL 30". Sex
counts only the index patient, never relatives mentioned in the case. In a
static build, the export is assembled in the browser from the article files. No model, Node install,
extra dependencies, or article downloads are needed.

The default library combines `data/dense_clinical_cases_v1` with live sources and
reviewed targets in `benchmarks/dense50` and `benchmarks/dense500`. There are no
article IDs embedded in the page. New source files enter the catalog, and new
reviewed targets appear automatically. The page checks every 15 seconds and when
it becomes visible again. It preserves the selected
patient, section, and panel scroll positions on an automatic update.

## What you can inspect

- Search by title, article ID, or specialty; filter by specialty and review stage.
- Open **Filters** to narrow by patient count (including aggregate-only series),
  figures, figure review (AI described, EHR labeled, privacy flagged, or not yet
  reviewed), tables, supplements, article type, and license. The browser
  remembers these choices.
- Select each patient independently and view every available clinical schema.
- Read summaries, measurements and units, event records, and relative event edges.
  Expanding a timeline event lists the recorded values behind it, such as scores,
  lab results with units, or doses. Each links to its own source quote.
- Click a source citation to scroll to and highlight its exact quote.
- View actual image links, reviewed visual descriptions, clinical classifications,
  panel/patient attribution, caption claims, and figure rights.
- See each figure panel's reviewed EHR category and includability, plus privacy
  flags (burned-in text or identifiers, identifiable faces, sensitive anatomy).
  Hover a panel line for the reviewer's rationale.
- Open **Supplements** in the record to see every supplementary file the article
  lists. Each shows its type, which patient it belongs to, a link to the original
  file, and the reviewer's summary. Extracted facts are grouped by patient, and
  unassigned ones are kept separate. Transcribed tables are rebuilt as tables.
  Supplement facts cannot be highlighted in the article text; open the file to
  check them.
- Inspect the roster, provenance, license, and complete JSON payload.
- Copy a deep link to an article, patient, and section using the browser URL.

## On a phone

The same address works on an iPhone or Android phone. To reach a server running
on your computer, start it with `--host 0.0.0.0` and open
`http://<computer's LAN address>:8765` on the same network. On a phone:

- A bottom tab bar switches between **Library**, **Article**, and **Record**.
  You can also swipe sideways between Article and Record.
- Tap a fact in the record to see its source quote, in context, in a sheet
  that rises from the bottom. You stay in the record. Step through multiple
  quotes, drag the sheet down to dismiss it, or tap **Read it in the article**.
- The top bar tucks away while you scroll down and returns when you scroll up.
  A thin line under the article bar shows how far you have read.
- Figures open full screen. Pinch to zoom and swipe down to close.
- Use **Share → Add to Home Screen** to install it as an app with its own icon.

Timeline events are not silently converted into a total chronological order.
Their explicitly supported relations remain visible, including uncertainty and
partial order. An empty clinical field means missing documentation, not a
negative finding.

## Freshness and review boundaries

Live schema submissions take priority over exports. The browser checks source,
annotation, target, and both schema review hashes; visual descriptions also need
both pixel review receipts and matching patient identity. Changed submissions
with stale receipts are withheld. Supplement extractions and the supplement
catalogue are read live under the same rule. Workstation file paths, such as
cached figure copies, are removed from every payload. A stale exported record cannot override a
changed live annotation. Matching enriched exported bundles retain normalized
measurements and media columns. Work-in-progress drafts are never published.

“Schema reviewed” and “source reviewed” are distinct stages, not synonyms for
all article components being completed. Missing or unreviewed media are labeled
separately. Figure EHR labels and privacy flags appear only with current agent
and parent receipts bound to the reviewed visual record. Aggregate-only articles,
which report patients only as a series, are schema reviewed with no patient
records and say so. These are AI reference annotations, not physician-adjudicated gold.

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

## GitHub Pages

The `Publish article viewer` workflow builds and deploys the existing viewer on
relevant pushes to `main`, and can also be run from the Actions tab. Pull requests
build and validate the site without deploying it.

In the repository's **Settings → Pages → Build and deployment**, select
**GitHub Actions** as the source. Once the workflow is on `main`, run it or push
a viewer/data change. The deployment job reports the published URL; for this
repository the default is <https://thepiglab.github.io/openpatients2/>.
See [GitHub's custom workflow guide](https://docs.github.com/en/pages/getting-started-with-github-pages/using-custom-workflows-with-github-pages).

The build uses locked Python dependencies and the existing static exporter, then
Bun 1.2.13 minifies the generated JavaScript and CSS. JSON is compacted without
changing its data. Only the generated website is uploaded. The viewer source,
layout, interactions, review gates, and relative URLs are unchanged.

Pages includes the checked-in sources and current reviewed records from both
`benchmarks/dense50` and `benchmarks/dense500`. Ignored workstation exports under
`data/` are not part of this build; exported enrichments available only there
will not appear on Pages. Drafts and stale reviews remain withheld by the viewer.
Images and supplementary files still link to their original hosts.

To reproduce the Pages build locally, install uv and Bun 1.2.13, then run:

```bash
uv run --locked python scripts/build_viewer_site.py _site
uv run --locked python -m http.server 8000 --directory _site
```

Open `http://localhost:8000`. The output directory must be new or empty; use
another output path for subsequent builds. `_site/` is ignored by Git.

## Other static hosts

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
