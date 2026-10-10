# PDF supplement review

PDF supplements use `dense-supplemental-document-reference/1`, separately from the unchanged supplemental image contract. Save one sidecar per source supplement in `document-submissions/ARTICLE.supplements.SUPPLEMENT.json`. The concrete HMPV example is `document-submissions/PMC12756086.1.supplements.SM1.json`; the Pydantic contract is `DocumentSupplementTarget` in `supplement_review.py`.

Preserve each original source-listed asset URL, MD5 (or null if absent), original manifest URL, filename, SHA256, byte count and exact source license. `media_type` is `application/pdf`. Obtain the actual `page_count` and personally inspect every rendered page. Each page entry requires its one-based page number, `status: inspected`, `inspection_method: rendered_page`, contents, clinical relevance (`administrative`, `clinical_data`, `background`, or `mixed`), relevance notes, supported patient IDs (otherwise empty), rights assessment and limitations. `rights_checked: true` records the actual page check; retain differing printed notices as `rights_basis: page_exception` with `rights_exceptions` notice/interpretation entries. No printed exception does not establish blanket asset licensing; use `unclear` where appropriate. Distinguish an administrative checklist from clinical data. Do not turn blank checklist cells into clinical negatives or create figure/vision output for document pages.

Validate and sign only your own inspection:

```sh
.venv/bin/python benchmarks/dense500/supplement_review.py benchmarks/dense500 --article ARTICLE --supplement SUPPLEMENT
.venv/bin/python benchmarks/dense500/supplement_review.py benchmarks/dense500 --article ARTICLE --supplement SUPPLEMENT --role agent --reviewer 'YOUR REVIEWER IDENTITY' --notes 'Actual full-page inspection notes'
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

A source-listed ZIP is reviewed per member PDF with `dense-supplemental-archive-document-reference/1` (Pydantic `ArchiveDocumentTarget` in `supplement_review.py`). The sidecar goes in the same place: `document-submissions/ARTICLE.supplements.SUPPLEMENT.json`.

```sh
PYTHONPATH=src:benchmarks/dense500 .venv/bin/python benchmarks/dense500/supplement_assets.py acquire benchmarks/dense500 --article ARTICLE
PYTHONPATH=src:benchmarks/dense500 .venv/bin/python benchmarks/dense500/supplement_assets.py extract benchmarks/dense500 --article ARTICLE --supplement SUPPLEMENT
```

`extract` checks the cached ZIP hash. It reads the central directory, ignoring `__MACOSX`/`.DS_Store` junk, and writes only the PDF members, under hashed names, into this article's folder of the capped supplement cache (250 MB per member, 30 GB shared cap). It records `supplement-archive-members/ARTICLE.SUPPLEMENT.json` and a skeleton at `document-submission-drafts/ARTICLE.supplements.SUPPLEMENT.json`. Copy the skeleton to `document-submissions/`, then inspect every rendered page of every member PDF. Fill `pages` exactly as for PDFs, set `status: inspected`, the classification and a summary, and remove the DRAFT text.

- The `archive` block keeps the declared ZIP URL, MD5, original manifest URL, filename, license, SHA256, bytes, the PDF member list and `other_members`. Any non-PDF member makes the review `partial`.
- Each `assets` entry is one member, with pseudo-URL `ZIP_URL#member=NAME`, `member`, SHA256, bytes, extracted path, `application/pdf`, pdfinfo `page_count` and per-page reviews.
- Typed clinical transcriptions use that member pseudo-URL as `asset_url`, the member SHA256 as `asset_sha256`, and the one-based member page. Exported supplemental assets carry `member`, `archive_url` and `archive_sha256`.
- Measurement series cite these rows with `origin: supplement_transcription` once dual-signed.
- Validation and signing use the same `supplement_review.py ... --supplement SUPPLEMENT [--role agent|parent]` commands. Signing re-hashes the ZIP and every member, re-reads each member from the ZIP and re-checks pdfinfo page counts.
- Receipts also bind `archive_sha256`. Any change to the ZIP, member list, member bytes, pages or payload makes them stale.

## Video supplements

`supplement_review.py` has no video contract (it reviews PDF pages and image pixels). For a source-listed video file (MP4, MOV, ...) the dual-signed supplement manifest (`supplement_manifest_review.py`, `dense-supplement-manifest/1`) is the content review; no extra sidecar and no schema change is needed. The manifest row satisfies `supplement_review_or_no_supplements` (`openpatients2.supplement_manifest.manifest_gate_supplements`, basis `manifest_video_frames_sampled`) only when all of the following hold:

- machine block: `download_status: downloaded`, `detected_type: video`, `inspection_status: peeked` (ffprobe duration/frame count read from the cached file);
- review: `content_class: video`, `classification_basis: full_content`, meaning both reviewers rendered and personally viewed sampled frames (e.g. contact sheets at a fixed interval across the whole duration, plus full-resolution frames where detail matters);
- `estimated_counts` records `duration_seconds` or `frames` (machine-read values must match);
- a `limitations` entry states the sampling scope, naming the frames/interval viewed (wording such as "frames sampled every 10 s", "contact sheets"), and what was not reviewed (frames between samples, continuous motion, audio, burned-in text that is too small).

Patient scope, ownership evidence and `ehr_includable`/`ehr_modality: video` follow the ordinary manifest rules. On-screen captions or measurements seen in sampled frames are not transcribed as clinical facts by this path; a reviewer who needs them uses a still-image supplement review of the extracted frames. In `article_checklist.json` the supplement appears under `manifest_reviewed_supplements` with basis `manifest_video_frames_sampled`, the inventory `review_status` is `agent_and_parent_manifest_video_frames_sampled`, and `content_inspected` stays false (no page/pixel sidecar was signed).

**External-link media.** A supplement declared only as an external link (YouTube etc.) has no fetchable PMC asset: acquisition records `download_status: no_declared_asset` (or `rejected_host`). Record it honestly in the manifest: `classification_basis: caption_and_filename_only`, `ehr_includable: false`, and a limitation saying the media was not viewed. It is never a content review. Under the default `UNAVAILABLE_MEDIA_POLICY='complete_flagged'` in `export.py` (the same policy applies to figures recorded `status: unavailable`), the dual-signed row satisfies the gate with basis `manifest_external_media_unavailable`. The article completes, flagged: the checklist lists the supplement in `unavailable_media` (and `unavailable_external_supplement_ids`), the inventory `review_status` is `external_media_unavailable`, `content_inspected` stays false, and the unified record sets `media_available: false` and `content_reviewed: false`. `UNAVAILABLE_MEDIA_POLICY='block'` keeps such articles incomplete.
