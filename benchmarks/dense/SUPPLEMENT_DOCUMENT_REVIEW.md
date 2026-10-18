# PDF supplement review

PDF supplements use `dense-supplemental-document-reference/1`, separately from the unchanged supplemental image contract. Save one sidecar per source supplement in `articles/ARTICLE/supplements/SUPPLEMENT.document.json`. The concrete HMPV example is `articles/PMC12756086.1/supplements/SM1.document.json`; the Pydantic contract is `DocumentSupplementTarget` in `src/openpatients2/dense/supplement_review.py`.

Preserve each original source-listed asset URL, MD5 (or null if absent), original manifest URL, filename, SHA256, byte count and exact source license. `media_type` is `application/pdf`. Obtain the actual `page_count` and personally inspect every rendered page. Each page entry requires its one-based page number, `status: inspected`, `inspection_method: rendered_page`, contents, clinical relevance (`administrative`, `clinical_data`, `background`, or `mixed`), relevance notes, supported patient IDs (otherwise empty), rights assessment and limitations. `rights_checked: true` records the actual page check; retain differing printed notices as `rights_basis: page_exception` with `rights_exceptions` notice/interpretation entries. No printed exception does not establish blanket asset licensing; use `unclear` where appropriate. Distinguish an administrative checklist from clinical data. Do not turn blank checklist cells into clinical negatives or create figure/vision output for document pages.

Validate and sign only your own inspection:

```sh
op2 dense supplement_review benchmarks/dense --article ARTICLE --supplement SUPPLEMENT
op2 dense supplement_review benchmarks/dense --article ARTICLE --supplement SUPPLEMENT --role agent --reviewer 'YOUR REVIEWER IDENTITY' --notes 'Actual full-page inspection notes'
```

Signing checks actual PDF bytes, original MD5, SHA256, byte count and `pdfinfo` page count. Poppler's `pdfinfo` must be available; its absence cannot be treated as completion. A complete sidecar requires exact source asset coverage and exactly one inspected entry for every PDF page. Partial/unavailable sidecars need limitations and cannot receive completed receipts. Downloading, parsing or rendering alone never establishes human/agent inspection.

The parent independently inspects all pages and signs with `--role parent` and a distinct reviewer identity. Both receipts bind source metadata, asset hashes, patient identity and the whole target (including page contents and rights). Changed/stale targets or receipts fail validation. Signing requires cache files; validated hashes can subsequently survive cache eviction, as for the existing image contract. The immutable article source is never mutated.

Export includes only dual-reviewed document sidecars in `supplement_documents.jsonl`, with status `agent_and_parent_source_and_document_pages_reviewed` in the supplement inventory. They complete the applicable supplementary gate without affecting pixel-review counts or creating image panels. Missing either receipt leaves the supplement pending. These are AI reference reviews, not physician gold or measured clinical accuracy.

## Clinical tables and images

An inspected clinical-data PDF page, supplemental table image, or supplemental clinical chart with readings also requires `clinical_data` before the supplement can receive a completed receipt. Administrative CARE checklists keep the existing contract and need no clinical payload. For images, `clinical_relevance: clinical_data` or `mixed` may explicitly identify other data-bearing scopes. Partial/limited extraction remains incomplete. Presence of transcriptions is not a measured claim of semantic exhaustiveness.

The optional payload uses `format: dense-supplemental-clinical-reference/1`, `coverage: complete | limited`, `transcriptions`, `facts`, and `limitations`. Each transcription contains all of:

```json
{"transcription_id":"SM1-cell-001","asset_url":"EXACT_SOURCE_URL","asset_sha256":"64_LOWERCASE_HEX","page":1,"panel":null,"table_label":"Supplementary Table 1","row_label":"6:00","column_label":"Day2","kind":"clinical_data","text":"3.6","visual_cue":null}
```

`kind: context` supports literal table headings, patient ownership and footnotes without manufacturing a clinical fact. Transcriptions are personally checked literal text from the asset; they are not JATS segments and must never be inserted into the article source. PDF references must name an actually inspected one-based page; image references use `page: null`, an actually inspected panel (or null for the whole image), and a nonempty pixel cue. Table/row/column labels preserve visible coordinates; missing coordinates are null.

Each fact contains `fact_id`, `task`, `collection`, `fields`, `evidence`, `ownership`, `patient_ids`, `ownership_evidence`, `certainty`, `scoring`, and `notes`. Its `fields` must explicitly supply every required clinical attribute of the production item model, including required nullable values, but omit `evidence` and section metadata. No clinical null filling is performed. `task` is one of the production TASK_MODELS. `collection` is `items`, an oncology collection, or null for a complete case-context row. Evidence uses `{"transcription_id":"SM1-cell-001","quote":"3.6"}`; the quote must occur literally in that transcription. Evidence added to runtime models has source_section `SM1:transcription:SM1-cell-001`, distinctly supplemental.

`ownership` is `individual` (exactly one known patient), `shared` (two or more), or `unresolved` (empty patient IDs). Attributed facts need exact ownership evidence, using either `{"basis":"supplement","transcription_id":"SM1-header-p1","segment_id":null,"quote":"first patient"}` or an immutable article segment with basis `article`, transcription_id null, segment_id and exact quote. PDF-page and image-panel ownership must agree with the inspected sidecar. Unresolved facts remain `scoring: review_only`, with non-index/unknown subject and no invented assignment. Numerical similarity cannot identify a patient. `certainty: supported | uncertain` preserves transcription or clinical ambiguity separately from clinical assertion.

Every clinical transcription needs explicit typed fact coverage, including cells whose printed value is a dash. Such a cell is missing monitoring, with unknown assertion/flag and appropriate unknown/not-performed status and absent-result reason; it is never a performed negative test. Preserve a printed explanation as a separate context transcription rather than inventing which absent-monitoring explanation applies to each cell. Quotes, clocks, hospital-day labels, magnitudes and units stay separate; do not anchor readings to an unreported treatment change. Complete coverage is an annotator assertion about the reviewed supplement scope, not machine verification of every clinically meaningful detail.

Both existing receipts bind the entire new payload. Changing it requires each reviewer to personally inspect and sign again; old page/pixel receipts do not approve added clinical extraction. Export retains the main article sections unchanged and writes `supplement_clinical.jsonl`, article `supplemental_clinical` records, and separate patient `supplemental_clinical` sections/fact pointers. Only accepted attributed rows enter patient sections. Unresolved and review-only rows remain in the article supplemental bundle, with asset/page/cell provenance and limitations. These records use the same production schemas but never pretend their quotes came from original article segments.

Production `time.text` and `date_iso`, when supplied, must also occur literally in a referenced transcription. Preserve separate day/clock coordinates instead of concatenating a new purported quote. Section coverage and limitations explicitly refer to the supplement scope. Clinical receipts bind the production schema fingerprint as well as the whole payload. Patient supplemental exports contain only accepted rows for that patient and their referenced transcriptions; whole payloads and unresolved rows stay in article-level supplemental output.

## ZIP supplements containing PDFs

A source-listed ZIP is reviewed per member PDF with `dense-supplemental-archive-document-reference/1` (Pydantic `ArchiveDocumentTarget` in `src/openpatients2/dense/supplement_review.py`). The sidecar goes in the same place: `articles/ARTICLE/supplements/SUPPLEMENT.document.json`.

```sh
op2 dense supplement_assets acquire benchmarks/dense --article ARTICLE
op2 dense supplement_assets extract benchmarks/dense --article ARTICLE --supplement SUPPLEMENT
```

`extract` checks the cached ZIP hash. It reads the central directory, ignoring `__MACOSX`/`.DS_Store` junk, and writes only the PDF members, under hashed names, into this article's folder of the capped supplement cache (250 MB per member, 30 GB shared cap). It records `articles/ARTICLE/supplements/SUPPLEMENT.archive_members.json` and a skeleton at `articles/ARTICLE/supplements/SUPPLEMENT.document.draft.json`. Copy the skeleton to `articles/ARTICLE/supplements/SUPPLEMENT.document.json`, then inspect every rendered page of every member PDF. Fill `pages` exactly as for PDFs, set `status: inspected`, the classification and a summary, and remove the DRAFT text.

- The `archive` block keeps the declared ZIP URL, MD5, original manifest URL, filename, license, SHA256, bytes, the PDF member list and `other_members`. Any non-PDF member makes the review `partial`.
- Each `assets` entry is one member, with pseudo-URL `ZIP_URL#member=NAME`, `member`, SHA256, bytes, extracted path, `application/pdf`, pdfinfo `page_count` and per-page reviews.
- Typed clinical transcriptions use that member pseudo-URL as `asset_url`, the member SHA256 as `asset_sha256`, and the one-based member page. Exported supplemental assets carry `member`, `archive_url` and `archive_sha256`.
- Measurement series cite these rows with `origin: supplement_transcription` once dual-signed.
- Validation and signing use the same `op2 dense supplement_review ... --supplement SUPPLEMENT [--role agent|parent]` commands. Signing re-hashes the ZIP and every member, re-reads each member from the ZIP and re-checks pdfinfo page counts.
- Receipts also bind `archive_sha256`. Any change to the ZIP, member list, member bytes, pages or payload makes them stale.

## Video supplements

`op2 dense supplement_review` has no contract for a standalone video (video members of a ZIP are reviewed as sampled frames by the composite path below). For a source-listed video file (MP4, MOV, ...) the dual-signed supplement manifest (`op2 dense supplement_manifest_review`, `dense-supplement-manifest/1`) is the content review; no extra sidecar and no schema change is needed. The manifest row satisfies `supplement_review_or_no_supplements` (`openpatients2.supplement_manifest.manifest_gate_supplements`, basis `manifest_video_frames_sampled`) only when all of the following hold:

- machine block: `download_status: downloaded`, `detected_type: video`, `inspection_status: peeked` (ffprobe duration/frame count read from the cached file);
- review: `content_class: video`, `classification_basis: full_content`, meaning both reviewers rendered and personally viewed sampled frames (e.g. contact sheets at a fixed interval across the whole duration, plus full-resolution frames where detail matters);
- `estimated_counts` records `duration_seconds` or `frames` (machine-read values must match);
- a `limitations` entry states the sampling scope, naming the frames/interval viewed (wording such as "frames sampled every 10 s", "contact sheets"), and what was not reviewed (frames between samples, continuous motion, audio, burned-in text that is too small).

Patient scope, ownership evidence and `ehr_includable`/`ehr_modality: video` follow the ordinary manifest rules. On-screen captions or measurements seen in sampled frames are not transcribed as clinical facts by this path; a reviewer who needs them uses a still-image supplement review of the extracted frames. In `article_checklist.json` the supplement appears under `manifest_reviewed_supplements` with basis `manifest_video_frames_sampled`, the inventory `review_status` is `agent_and_parent_manifest_video_frames_sampled`, and `content_inspected` stays false (no page/pixel sidecar was signed).

**External-link media.** A supplement declared only as an external link (YouTube etc.) has no fetchable PMC asset: acquisition records `download_status: no_declared_asset` (or `rejected_host`). Record it honestly in the manifest: `classification_basis: caption_and_filename_only`, `ehr_includable: false`, and a limitation saying the media was not viewed. It is never a content review. Under the default `UNAVAILABLE_MEDIA_POLICY='complete_flagged'` in `src/openpatients2/dense/export.py` (the same policy applies to figures recorded `status: unavailable`), the dual-signed row satisfies the gate with basis `manifest_external_media_unavailable`. The article completes, flagged: the checklist lists the supplement in `unavailable_media` (and `unavailable_external_supplement_ids`), the inventory `review_status` is `external_media_unavailable`, `content_inspected` stays false, and the unified record sets `media_available: false` and `content_reviewed: false`. `UNAVAILABLE_MEDIA_POLICY='block'` keeps such articles incomplete.

## Word-processor supplements (DOCX/DOC/RTF/ODT/PPTX/ODP, or such members of a ZIP)

Format `dense-supplemental-text-document-reference/1` (Pydantic `TextDocumentTarget` in `src/openpatients2/dense/supplement_review.py`). It is reviewed like a PDF, but the unit of inspection is a literal **text unit** instead of a rendered page. Text is extracted offline by `openpatients2.document_text`, using the shipped firecrawl-anydoc structured model (`to_document`, Inline.text runs only). Hosted OCR and Markdown output are disabled in the worker, and nothing is uploaded. There is no lossy fallback: a file anydoc cannot read cannot be completed by this path.

Units come in document order:
- `paragraph`: one non-blank line.
- `table_row`: one table row, cells joined by ` | `, with `table`/`row` coordinates and the raw `cells`.

DOCX units are cross-checked against the raw `w:t` text of `word/document.xml`. Units that do not match are listed in `extraction.nonliteral_units` and may not be quoted.

```sh
# 1. acquire (as for every supplement), then draft the review skeleton (extracts ZIP members into the cache)
op2 dense supplement_assets extract-text benchmarks/dense --article ARTICLE --supplement SID
# 2. copy articles/ARTICLE/supplements/SID.document.draft.json to articles/ARTICLE/supplements/SID.document.json, read EVERY unit
#    (open the original file too when layout matters), fill `blocks`, status, classification, summary, clinical_data
# 3. validate (no --role: this is the dry run)
op2 dense supplement_review benchmarks/dense --article ARTICLE --supplement SID
# 4. sign only after reading every unit (a --role call is a signature)
op2 dense supplement_review benchmarks/dense --article ARTICLE --supplement SID --role agent --reviewer "Claude Sonnet 5.5 annotator" --notes "<what you read>"
```

- **Units and extraction are machine fields; never edit them.** `extraction.units_sha256` binds the units. With the cached file present, validation re-extracts and requires identical units, tool version and nonliteral list.
- **Blocks.** `blocks` play the role of PDF page reviews. Each block covers a consecutive run `first_unit..last_unit`, and together the blocks cover every unit exactly once, in order. Each block records:
  - `inspection_method`: `extracted_text`, or `extracted_text_and_rendered_document`;
  - `contents`;
  - `clinical_relevance` (administrative / clinical_data / background / mixed) and notes;
  - `patient_ids`;
  - rights fields;
  - limitations.
- **Clinical transcriptions:**
  - cite the asset URL (a member pseudo-URL `ZIP_URL#member=NAME` inside a ZIP) and its SHA256;
  - put the **unit number in `page`**, with `panel: null`;
  - their text must be literal in that unit (a whole table row, e.g. `1 | acute cystitis | yes`, or part of it). Use the conventional id `A<asset>-U<unit>` (e.g. `A1-U0003`).
- **Facts and coverage.** Facts, ownership evidence, coverage and dash-cell rules are exactly as for PDFs. A fact's owners must be listed on the block containing its units, and every clinical block needs at least one clinical transcription.
- **ZIP archives.** The `archive` block keeps the declared ZIP. All word-processor members must be reviewed; any other member makes the review `partial`. Receipts bind `archive_sha256`.
- **Receipts and export.** Receipts have status `source_and_document_text_reviewed`. Export reports `agent_and_parent_source_and_document_text_reviewed`, and the supplement satisfies `supplement_review_or_no_supplements`.
- **Runtime pipeline.** It reads the same text: `ehr_tasks.load_transcriptions` and `measurement_series_review.reviewed_transcriptions` offer every literal unit under the same `A<asset>-U<unit>` ids (`openpatients2.document_text.reviewed_text`). Local models and measurement series can therefore cite the reviewed units.
- **PPTX/ODP** are text documents too (`extract-text`). PPTX units are cross-checked against the slides' `a:t` text (`verified_against_pptx_xml`). Slide boundaries are not units: open the deck when slide context matters.

## Images embedded in DOCX/PPTX (and XLSX/ODF)

Text units never cover pixels. `extract-text` (and `extract-members`) also inventory every embedded media part (`word/media/*`, `ppt/media/*`, `xl/media/*`, ODF `Pictures/*`). The exact part bytes are copied into the cache, and each part becomes one `embedded_images` entry on its asset: `image` (1..n), `part`, pseudo-URL `ASSET_URL#part=PART`, SHA256, bytes, path, `media_type`, `preview_path`/`preview_sha256` (an offline `sips` PNG for TIFF/BMP), and `review`.

- **Complete coverage.** Every embedded image needs a `review`, otherwise the sidecar stays `partial`. EMF/WMF/SVG or unknown parts have no raster view, so they keep the review partial.
- **Review shape** (`ImageReview`). It uses the supplemental image contract's semantics:
  - `inspection_method: viewed_pixels`;
  - `visual`, a `FigureVisuals` object with `figure_id` set to the supplement id. Panels carry pixel cues, and citations must be literal article quotes;
  - `privacy`, exactly one `PanelFlags` per panel;
  - the page-review fields (contents, clinical relevance, patient IDs, rights, limitations);
  - `ownership_evidence`, required whenever `patient_ids` is non-empty: a literal article segment quote or a supplement transcription.
- **Clinical data.** Table panels, clinical charts with readings, and any `clinical_data`/`mixed` image need `clinical_data` transcriptions with `asset_url` = the part pseudo-URL, the image SHA256, `page: null`, an inspected `panel` and a non-empty `visual_cue`.
- **Legacy sidecars.** A sidecar drafted before images were inventoried has no `embedded_images` key. Signing re-reads the package and refuses while it embeds any image: re-run `extract-text`, then inspect every image.
- **Status.** Receipts become `source_and_document_text_and_images_reviewed` (export: `agent_and_parent_...`). A document without images keeps `source_and_document_text_reviewed`.

```sh
op2 dense supplement_assets extract-text benchmarks/dense --article ARTICLE --supplement SID   # units + embedded_images
# view each embedded_images[].path (or preview_path), fill blocks and every embedded_images[].review, then validate/sign as above
```

### Videos embedded in DOCX/PPTX/ODF (`embedded_videos`)

A media part whose bytes are a video (MP4/MOV/M4V/AVI/MKV/WebM/MPEG by magic bytes, e.g. `ppt/media/media1.mp4`) is not an image row. It goes into a separate `embedded_videos` list on the same asset. Each entry has `video` (1..n), `part`, pseudo-URL `ASSET_URL#part=PART`, SHA256, bytes and the path of the exact part bytes. It also carries the machine fields of a composite `video` member: ffprobe `probe`, fixed-interval `sampling.frames` (PNG paths, SHA256), `content_inspected: sampled_frames`, `continuous_motion_inspected: false`, `audio_inspected: false` and pre-filled `limitations`. A poster image of the clip stays an ordinary `embedded_images` row.

- **Sampling.** `extract-text` and `extract-members` take `--every S` / `--max-frames N` (default every 2 s, 60 frames). Pass a small `--every` for short clips.
- **Review.** `frame_blocks` must cover every sampled frame, in order, exactly as for a composite video member: whole-frame privacy flags (`panel: null`), ownership evidence for attributed blocks. Transcriptions cite the part pseudo-URL and the frame index in `page`, with `panel: null` and a visual cue.
- **Coverage.** Validation re-reads the package. The image and video inventories must equal its non-video and video parts. It re-probes each clip and re-hashes every frame. Dropping a clip, thinning its frames or leaving its `frame_blocks` short fails.
- **Legacy sidecars.** A sidecar without the `embedded_videos` key lists video parts as unviewable `embedded_images` rows. It still validates as `partial` but can never complete: re-run the extraction.
- **Status.** The receipt status adds `_and_sampled_video_frames`, e.g. `source_and_document_text_and_images_and_sampled_video_frames_reviewed`, and the export sets `video_frames_sampled: true`. Continuous motion and audio are never claimed as reviewed.

## Mixed ZIPs, image/video/XLSX members, standalone XLSX (composite)

Format `dense-supplemental-composite-reference/1` (Pydantic `CompositeSupplementTarget`) covers one source-listed ZIP whose members may be of different kinds. It also covers directly declared XLSX (or other non-video) assets. A ZIP of only PDFs or only word-processor files may still use the older contracts.

```sh
op2 dense supplement_assets acquire benchmarks/dense --article ARTICLE
op2 dense supplement_assets extract-members benchmarks/dense --article ARTICLE --supplement SID [--every 0.1] [--max-frames 60]
# copy SID.document.draft.json to SID.document.json, review every member, then:
op2 dense supplement_review benchmarks/dense --article ARTICLE --supplement SID                      # validate (dry run)
op2 dense supplement_review benchmarks/dense --article ARTICLE --supplement SID --role agent --reviewer "Claude Sonnet 5.5 annotator" --notes "<what you inspected>"
```

**Member classification.** `extract-members` classifies every non-junk member by its bytes, using magic bytes and then the OOXML parts. An extensionless DOCX is therefore `text`/`docx`. It writes supported members under hashed names into the capped cache. `archive.members` binds `[member, kind, detected_type]`. Signing re-hashes the ZIP and every member and re-classifies them. Any `unsupported` member (for example CSV, plain text, DICOM, nested ZIP, or above the 250 MB member cap) keeps the review `partial`. Each `assets` entry has a `kind`, with its own exact-coverage rule:

| kind | machine fields | coverage that must be complete |
|---|---|---|
| `pdf` | pdfinfo `page_count` | one `pages` review per page (PDF rules) |
| `text` | units, extraction, `embedded_images`, `embedded_videos` | `blocks` cover every unit, every embedded image has a review, and every embedded video's `frame_blocks` cover its sampled frames |
| `image` | `media_type`, preview | one `review` (ImageReview, as above) |
| `video` | ffprobe `probe`, `sampling.frames` (paths, SHA256) | `frame_blocks` cover every sampled frame, in order |
| `spreadsheet` | `extraction.sheets` inventory, `embedded_images` | one `sheet_reviews` entry per sheet, and its row blocks cover every non-empty row |

**Video members (sampled frames only).** Frames are sampled with the `op2 dense video_frames` machinery (`probe`, `sample`). They are taken at fixed `--every` intervals from 0 to two frame intervals before the end, spread evenly when `--max-frames` would truncate. The default is every 2 s: pass a small `--every` (e.g. `0.1`) for sub-second clips. Validation recomputes the timestamps from the probe, so a thinned or cherry-picked frame list fails. The part records:

- `content_inspected: sampled_frames`;
- `continuous_motion_inspected: false` and `audio_inspected: false`;
- non-empty `limitations` that state the sampling scope (the draft pre-fills this).

Each frame block is a review of the viewed frames, with a whole-frame privacy flag set (`panel: null`) and ownership evidence. Transcriptions cite the frame index in `page`, with `panel: null` and a visual cue. The status names the sampling: e.g. `source_and_sampled_video_frames_reviewed`. The export inventory sets `video_frames_sampled: true`. Frames between samples, continuous motion and audio are never claimed as reviewed.

**Spreadsheets** (`XlsxReader` in `openpatients2.supplement_peek`, streaming, no formula evaluation). Cell text is the stored literal value: the cached `<v>` of a formula, a shared or inline string, or an unformatted number (dates stay serial numbers).

- **Inventory.** The draft binds a per-sheet inventory: name, state, non-empty row count, `row_ranges` of non-empty rows, `max_column`, `cell_count` and `cells_sha256`. `extraction.sheets_sha256` binds the whole inventory. Each sheet's rows are dumped as JSONL (`row_dump`, `{"row": n, "cells": {"A": ...}}`) for reading.
- **Hard cap.** A sheet above 250,000 non-empty rows or 5,000,000 cells (or with a missing or oversized part) is `truncated: true`. It can only be reviewed as `partial`, never silently cut.
- **Row blocks.** Blocks are ordered, non-overlapping `first_row..last_row` runs. Each contains at least one non-empty row, and together they cover every non-empty row of the sheet. Use `inspection_method: every_row_read`. `homogeneous_block_summary` is allowed for large uniform runs (for example a variant list), but its `limitations` must state what was summarized. Attributed blocks need `ownership_evidence`.
- **Transcriptions** cite `page` = 1-based sheet index, `table_label` = sheet name, `cell` = A1 reference (e.g. `C2`) and `panel: null`. With the file present, the text must occur literally in that parsed cell, and validation and signing re-parse the workbook.
- **Clinical coverage.** Every clinical/mixed block needs a clinical transcription within its rows. Fact owners must be listed on the containing block.
- **Status.** `source_and_spreadsheet_cells_reviewed`.

**Composite status.** The receipt status names every reviewed kind, e.g. `source_and_document_pages_and_document_text_and_images_and_spreadsheet_cells_reviewed`. Export prefixes `agent_and_parent_`, and the supplement satisfies `supplement_review_or_no_supplements`. Receipts bind the target, the asset hashes and `archive_sha256`. Any changed member, inventory, frame or review makes them stale.

**Not covered by this path.** The following stay partial, or use the manifest:

- standalone videos, which keep the manifest sampled-frames basis;
- embedded images inside DOC/RTF, which cannot be enumerated;
- vector images (EMF/WMF/SVG);
- CSV/TSV/text/DICOM members;
- truncated sheets.
