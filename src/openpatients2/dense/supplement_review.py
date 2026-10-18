"""Bind supplemental image, PDF-page, text-unit, spreadsheet-row or sampled-video-frame review separately from main figures.

Signing is permitted only after every source-listed asset has actually been viewed.
Validation checks provenance and schema structure; it cannot attest pixel accuracy.
The public reader returns [(sidecar, [agent_receipt, parent_receipt]), ...]. Cache
files are checked at signing, but may subsequently be evicted from the bounded cache.
"""
import argparse
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import io
import json
from pathlib import Path
import re
import subprocess
from typing import Annotated, Literal, Union
from typing import get_args
import zipfile

from pydantic import BaseModel, ConfigDict, Field

from openpatients2.document_text import TEXT_DOCUMENT_TYPES, embedded_images
from openpatients2.figure_privacy import PanelFlags
from openpatients2.figure_visuals import FigureVisuals, validate_joint, validate_visuals
from openpatients2.supplement_peek import VIDEO_EXTENSIONS, XLSX_MAX_PART_BYTES, XlsxReader, detect_type, extension
from openpatients2.schemas import TASK_MODELS
from openpatients2.dense.validate import digest
from openpatients2 import dense_store as store
from openpatients2.dense.visual_review import identity_digest

FORMAT = 'dense-supplemental-figure-reference/1'
DOCUMENT_FORMAT = 'dense-supplemental-document-reference/1'
CLINICAL_FORMAT = 'dense-supplemental-clinical-reference/1'
# Additive: a declared ZIP reviewed per member PDF. Member pages behave exactly
# like PDF pages; each member is a pseudo-asset URL `ZIP_URL#member=NAME`.
ARCHIVE_FORMAT = 'dense-supplemental-archive-document-reference/1'
# Additive: word-processor documents (DOCX/DOC/RTF/ODT, directly declared or inside one declared ZIP)
# reviewed as literal text units extracted offline by openpatients2.document_text (anydoc Inline.text).
TEXT_FORMAT = 'dense-supplemental-text-document-reference/1'
# Additive: one declared ZIP of heterogeneous members (PDF pages, text units, images, sampled video frames,
# spreadsheet rows), or directly declared spreadsheets. Each member kind keeps its own exact-coverage rule.
COMPOSITE_FORMAT = 'dense-supplemental-composite-reference/1'
DOCUMENT_FORMATS = (DOCUMENT_FORMAT, ARCHIVE_FORMAT, TEXT_FORMAT, COMPOSITE_FORMAT)


class SupplementTranscription(BaseModel):
    """Reviewer-authored literal transcription, never an immutable JATS segment."""
    model_config = ConfigDict(extra='forbid', strict=True)
    transcription_id: str = Field(min_length=1)
    asset_url: str = Field(min_length=1)
    asset_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    page: int | None = Field(ge=1)
    panel: str | None
    table_label: str | None
    row_label: str | None
    column_label: str | None
    kind: Literal['clinical_data', 'context']
    text: str = Field(min_length=1)
    visual_cue: str | None
    # Spreadsheet cells only: A1 reference within sheet `page` (1-based sheet index); null elsewhere.
    cell: str | None = Field(default=None, pattern=r'^[A-Z]{1,3}[1-9][0-9]*$')


class SupplementEvidence(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    transcription_id: str = Field(min_length=1)
    quote: str = Field(min_length=1, max_length=1200)


class SupplementOwnershipEvidence(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    basis: Literal['supplement', 'article']
    transcription_id: str | None
    segment_id: str | None
    quote: str = Field(min_length=1, max_length=1200)


class SupplementClinicalFact(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    fact_id: str = Field(min_length=1)
    task: str
    collection: str | None
    fields: dict
    evidence: list[SupplementEvidence] = Field(min_length=1)
    ownership: Literal['individual', 'shared', 'unresolved']
    patient_ids: list[str]
    ownership_evidence: list[SupplementOwnershipEvidence]
    certainty: Literal['supported', 'uncertain']
    scoring: Literal['include', 'review_only']
    notes: str


class SupplementClinicalData(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    format: Literal['dense-supplemental-clinical-reference/1']
    coverage: Literal['complete', 'limited']
    transcriptions: list[SupplementTranscription] = Field(min_length=1)
    facts: list[SupplementClinicalFact] = Field(min_length=1)
    limitations: list[str]


def _locations(target):
    """{url: (kind, entry)} of every addressable reviewed asset of a document-like sidecar; None for image sidecars.

    kind is pdf | text | image | video | spreadsheet; embedded images (in text or spreadsheet parts) are image,
    embedded videos are video (transcriptions cite a sampled frame index)."""
    fmt = target.get('format')
    if fmt not in DOCUMENT_FORMATS:
        return None
    out = {}
    for a in target['assets']:
        out[a['url']] = (a.get('kind') or ('text' if fmt == TEXT_FORMAT else 'pdf'), a)
        for image in a.get('embedded_images') or []:
            out[image['url']] = ('image', image)
        for video in a.get('embedded_videos') or []:
            out[video['url']] = ('video', video)
    return out


def _clinical_requirements(target):
    """Recognize clinical documents/tables without changing administrative behavior.

    Tagged requirements: ('at', url, page, panel), ('range', url, first, last) over unit/frame numbers,
    ('rows', url, sheet, first_row, last_row)."""
    clinical = ('clinical_data', 'mixed')
    locations = _locations(target)
    if locations is not None:
        required = set()
        for url, (kind, a) in locations.items():
            if kind == 'pdf':
                required |= {('at', url, p['page'], None) for p in a['pages'] if p['clinical_relevance'] in clinical}
            elif kind == 'text':
                required |= {('range', url, b['first_unit'], b['last_unit']) for b in a['blocks']
                             if b['clinical_relevance'] in clinical}
            elif kind == 'video':
                required |= {('range', url, b['first_frame'], b['last_frame']) for b in a['frame_blocks']
                             if b['clinical_relevance'] in clinical}
            elif kind == 'spreadsheet':
                required |= {('rows', url, sheet['sheet'], b['first_row'], b['last_row']) for sheet in a['sheet_reviews']
                             for b in sheet['blocks'] if b['clinical_relevance'] in clinical}
            elif a.get('review'):
                required |= {('at', url, None, panel) for panel in _data_panels(a['review']['visual']['panels'],
                                                                                   a['review']['clinical_relevance'])}
        return required
    return {('at', None, None, panel) for panel in _data_panels(target.get('analysis', {}).get('visual', {}).get('panels', []),
                                                                 target.get('clinical_relevance'))}


def _data_panels(panels, relevance):
    # The joint image analysis may combine panels from different source assets;
    # require each data-bearing panel, not every panel in every asset.
    required = {p['panel'] for p in panels if p.get('image_kind') == 'table' or
                any(c.get('is_clinical_chart') and c.get('readings') for c in p.get('charts', []))}
    if relevance in ('clinical_data', 'mixed') and not required:
        required = {p['panel'] for p in panels}
    return required


def _cell_row(cell):
    return int(re.search(r'[0-9]+$', cell).group(0))


def _containing(blocks, first, last, number):
    return next((b for b in blocks if b[first] <= number <= b[last]), None)


def _clinical_model(fact):
    if fact.task not in TASK_MODELS:
        raise ValueError('Unknown production clinical task')
    model = TASK_MODELS[fact.task]
    if fact.task == 'case_context':
        if fact.collection is not None:
            raise ValueError('Case context has no item collection')
        return model
    allowed = ('tumors', 'biomarkers', 'treatments') if fact.task == 'oncology' else ('items',)
    if fact.collection not in allowed:
        raise ValueError('Wrong production clinical collection')
    return get_args(model.model_fields[fact.collection].annotation)[0]


def _clinical_item(fact, sid):
    """Use explicit authored attributes; no automatic clinical null/default fill."""
    if 'evidence' in fact.fields or any(k in fact.fields for k in
            ('coverage', 'limitations', 'documentation_status', 'documentation_evidence')):
        raise ValueError('Clinical fields cannot override supplemental evidence or section metadata')
    fields = deepcopy(fact.fields)
    fields['evidence'] = [{'quote': e.quote,
                          'source_section': f'{sid}:transcription:{e.transcription_id}'} for e in fact.evidence]
    if fact.task == 'case_context':
        fields.update(coverage='complete', limitations=[], documentation_status='documented',
                      documentation_evidence=[])
    return _clinical_model(fact).model_validate(fields).model_dump()


def validate_clinical_data(target, source, annotation, *, cells=None):
    """Validate typed rows against reviewed transcriptions and production schemas.

    Coverage means explicit authored transcription-to-fact coverage, never measured
    semantic exhaustiveness or automatic proof that pixels were read correctly. `cells`
    ({(asset_url, sheet, A1): text}, parsed from the actual file) checks spreadsheet literals.
    """
    required = _clinical_requirements(target)
    payload = target.get('clinical_data')
    if payload is None:
        return not required
    data = SupplementClinicalData.model_validate(payload)
    if data.coverage == 'limited' and not data.limitations:
        raise ValueError('Limited supplemental clinical extraction needs limitations')
    locations = _locations(target)
    assets = {a['url']: a for a in target['assets']} if locations is None else {u: e for u, (_, e) in locations.items()}
    patients = {p['patient_id'] for p in annotation['patients']}
    segments = {s['segment_id']: s['text'] for s in source.get('segments', [])}
    ids = [t.transcription_id for t in data.transcriptions]
    if len(ids) != len(set(ids)):
        raise ValueError('Duplicate supplemental transcription identity')
    transcriptions = {t.transcription_id: t for t in data.transcriptions}
    clinical_locations = set()
    for t in data.transcriptions:
        if t.asset_url not in assets or t.asset_sha256 != assets[t.asset_url]['sha256']:
            raise ValueError('Transcription references wrong asset URL/SHA256')
        kind = 'legacy_image' if locations is None else locations[t.asset_url][0]
        asset = assets[t.asset_url]
        if (t.cell is not None) != (kind == 'spreadsheet'):
            raise ValueError('Only spreadsheet transcriptions (and all of them) cite a `cell`')
        if kind == 'text':
            units = {u['unit']: u for u in asset['units']}
            if t.page not in units or t.panel is not None:
                raise ValueError('Text-document transcription cites an extracted unit number in `page`, never a panel')
            if t.page in asset['extraction']['nonliteral_units'] or t.text not in units[t.page]['text']:
                raise ValueError('Text-document transcription must be literal in its extracted unit')
        elif kind == 'pdf':
            pages = {p['page']: p for p in asset['pages']}
            if t.page not in pages or t.panel is not None:
                raise ValueError('PDF transcription requires an inspected page, not an image panel')
        elif kind == 'video':
            if t.page not in {f['index'] for f in asset['sampling']['frames']} or t.panel is not None or not t.visual_cue:
                raise ValueError('Video transcription cites a sampled frame index in `page` with a visual cue')
        elif kind == 'spreadsheet':
            sheets = {x['sheet']: x for x in asset['extraction']['sheets']}
            if t.page not in sheets or t.panel is not None or t.table_label != sheets[t.page]['name']:
                raise ValueError('Spreadsheet transcription cites its sheet index in `page` and the sheet name as table_label')
            row = _cell_row(t.cell)
            if not any(a <= row <= b for a, b in sheets[t.page]['row_ranges']):
                raise ValueError('Spreadsheet transcription cites an empty or unparsed row')
            if cells is not None and t.text not in cells.get((t.asset_url, t.page, t.cell), ''):
                raise ValueError('Spreadsheet transcription must be literal in its parsed cell')
        else:
            visual = (target['analysis']['visual'] if kind == 'legacy_image' else (asset.get('review') or {}).get('visual'))
            panels = {p['panel'] for p in (visual or {}).get('panels', [])}
            if t.page is not None or t.panel not in panels or not t.visual_cue:
                raise ValueError('Image transcription requires an inspected panel and visual cue')
        if t.kind == 'clinical_data':
            clinical_locations.add((t.asset_url, t.page, t.panel, _cell_row(t.cell) if t.cell else None))
    def located(requirement):
        tag, url, *where = requirement
        if tag == 'range':
            return any(url == u and where[0] <= p <= where[1] for u, p, _, _ in clinical_locations)
        if tag == 'rows':
            return any(url == u and p == where[0] and where[1] <= r <= where[2] for u, p, _, r in clinical_locations)
        return any((url is None or url == u) and where == [p, panel] for u, p, panel, _ in clinical_locations)
    if not all(located(r) for r in required):
        raise ValueError('Every clinical-data page/panel requires reviewed clinical transcriptions')
    ids = [f.fact_id for f in data.facts]
    main_ids = {f['fact_id'] for p in annotation['patients'] for f in p.get('facts', [])}
    if len(ids) != len(set(ids)) or set(ids) & main_ids:
        raise ValueError('Duplicate or main-article-colliding supplemental fact identity')
    covered = set()
    def literal(tid, quote):
        if tid not in transcriptions or quote not in transcriptions[tid].text:
            raise ValueError('Supplement evidence must be literal in its reviewed transcription')
        return transcriptions[tid]
    for fact in data.facts:
        item = _clinical_item(fact, target['supplement_id'])
        if len(fact.patient_ids) != len(set(fact.patient_ids)) or set(fact.patient_ids) - patients:
            raise ValueError('Unknown or duplicate supplemental patient identity')
        if ((fact.ownership == 'individual' and len(fact.patient_ids) != 1) or
                (fact.ownership == 'shared' and len(fact.patient_ids) < 2) or
                (fact.ownership == 'unresolved' and fact.patient_ids)):
            raise ValueError('Supplement ownership and patient IDs disagree')
        if fact.ownership == 'unresolved' and (fact.scoring != 'review_only' or
                fact.fields.get('subject') == 'index_patient'):
            raise ValueError('Unresolved supplemental findings cannot become index-patient facts')
        if fact.patient_ids and not fact.ownership_evidence:
            raise ValueError('Patient ownership requires literal source evidence')
        for e in fact.ownership_evidence:
            if e.basis == 'supplement':
                if e.segment_id is not None:
                    raise ValueError('Supplement ownership must not pretend to be an article segment')
                literal(e.transcription_id, e.quote)
            elif e.transcription_id is not None or e.quote not in segments.get(e.segment_id, ''):
                raise ValueError('Article ownership requires literal immutable article evidence')
        fact_clinical = set()
        for e in fact.evidence:
            t = literal(e.transcription_id, e.quote)
            if t.kind == 'clinical_data':
                fact_clinical.add(t.transcription_id)
            kind = None if locations is None else locations[t.asset_url][0]
            asset = assets[t.asset_url]
            if kind is not None:
                if kind == 'text':
                    scope, what = _containing(asset['blocks'], 'first_unit', 'last_unit', t.page), 'text-block'
                elif kind == 'pdf':
                    scope, what = next(p for p in asset['pages'] if p['page'] == t.page), 'PDF-page'
                elif kind == 'video':
                    scope, what = _containing(asset['frame_blocks'], 'first_frame', 'last_frame', t.page), 'video-frame'
                elif kind == 'spreadsheet':
                    sheet = next((x for x in asset['sheet_reviews'] if x['sheet'] == t.page), {'blocks': []})
                    scope, what = _containing(sheet['blocks'], 'first_row', 'last_row', _cell_row(t.cell)), 'spreadsheet-row'
                else:
                    scope, what = asset.get('review'), 'image'
                if set(fact.patient_ids) - set((scope or {}).get('patient_ids', [])):
                    raise ValueError(f'Typed fact ownership exceeds inspected {what} attribution')
            else:
                assignments = [x for x in target['analysis']['attribution']['assignments']
                               if x['panel'] is None or x['panel'] == t.panel]
                owners = {pid for x in assignments for pid in x['patient_ids']}
                if set(fact.patient_ids) - owners:
                    raise ValueError('Typed fact ownership exceeds inspected image attribution')
        time = item.get('time') or {}
        referenced = [transcriptions[e.transcription_id] for e in fact.evidence]
        for key in ('text', 'date_iso'):
            value = time.get(key)
            if value and not any(value in t.text for t in referenced):
                raise ValueError('Supplement clinical time must be literal in its referenced transcriptions; retain separate grid coordinates')
        if not fact_clinical:
            raise ValueError('Clinical fact requires a clinical-data transcription, not context alone')
        covered.update(fact_clinical)
        # A printed dash/missing cell is not a performed test with a negative result.
        if fact.task == 'observations' and any(e.quote.strip() in ('-', '—', '–') and
                transcriptions[e.transcription_id].kind == 'clinical_data' for e in fact.evidence):
            if (item['assertion'] != 'unknown' or item['status'] not in ('unknown','not_performed') or
                    item['numeric_value'] is not None or item['text_value'] is not None or item['flag'] != 'unknown'):
                raise ValueError('Missing supplemental cell cannot be a performed/negative test result')
    clinical_ids = {t.transcription_id for t in data.transcriptions if t.kind == 'clinical_data'}
    if not clinical_ids.issubset(covered):
        raise ValueError('Reviewed clinical transcriptions lack explicit typed fact coverage')
    # Exercise the complete section models too, including case-context cardinality.
    supplemental_clinical_bundle(target, annotation)
    return data.coverage == 'complete'


def supplemental_clinical_bundle(target, annotation):
    """Export separate production-shaped sections with supplemental cell provenance."""
    if not target.get('clinical_data'):
        return None
    data = SupplementClinicalData.model_validate(target['clinical_data'])
    sid = target['supplement_id']
    scope_limitation = f'Supplement {sid} scope only; coverage does not describe the main article or patient history completeness.'
    patients = []
    for patient in annotation['patients']:
        pid = patient['patient_id']
        facts = [f for f in data.facts if pid in f.patient_ids and f.scoring == 'include']
        if not facts:
            continue
        sections = {}; links = []
        for fact in facts:
            item = _clinical_item(fact, sid)
            if fact.task == 'case_context':
                if fact.task in sections:
                    raise ValueError('Use one complete supplemental case-context row per patient')
                item['coverage'] = data.coverage
                item['limitations'] = list(data.limitations) + [scope_limitation]
                sections[fact.task] = item; pointer = '/'
            else:
                section = sections.setdefault(fact.task, dict(coverage=data.coverage,
                    limitations=list(data.limitations) + [scope_limitation],
                    documentation_status='documented', documentation_evidence=[]))
                for key in ('tumors', 'biomarkers', 'treatments') if fact.task == 'oncology' else ('items',):
                    section.setdefault(key, [])
                pointer = f'/{fact.collection}/{len(section[fact.collection])}'
                section[fact.collection].append(item)
            links.append(dict(fact_id=fact.fact_id, task=fact.task, pointer=pointer,
                              evidence=[e.model_dump() for e in fact.evidence],
                              ownership=fact.ownership, patient_ids=fact.patient_ids,
                              ownership_evidence=[e.model_dump() for e in fact.ownership_evidence],
                              certainty=fact.certainty, notes=fact.notes))
        for fact in data.facts:
            if pid in fact.patient_ids and fact.scoring == 'review_only' and fact.task in sections:
                sections[fact.task]['coverage'] = 'limited'
                sections[fact.task]['limitations'].append('Attributed review-only candidates remain separate; not accepted as patient section facts.')
        sections = {task: TASK_MODELS[task].model_validate(value).model_dump()
                    for task, value in sections.items()}
        patients.append(dict(patient_id=pid, sections=sections, fact_pointers=links))
    return dict(format=CLINICAL_FORMAT, article_id=target['article_id'], supplement_id=sid,
        source_text_sha256=target['source_text_sha256'], clinical_data=deepcopy(target['clinical_data']),
        assets=[{k:deepcopy(a.get(k)) for k in ('url', 'sha256', 'md5', 'bytes', 'filename',
                'original_manifest_url', 'source_license') + (('member', 'archive_url', 'archive_sha256')
                if target.get('format') in (ARCHIVE_FORMAT, COMPOSITE_FORMAT) else ()) +
                (('kind',) if target.get('format') == COMPOSITE_FORMAT else ()) +
                (('parent_url', 'part') if 'part' in a else ())} for a in _bundle_assets(target)],
        schema_fingerprints={k:digest(v.model_json_schema()) for k,v in TASK_MODELS.items()},
        patients=patients, unassigned_or_review_only_facts=[{**f.model_dump(),
            'value_as_extracted':_clinical_item(f,sid)} for f in data.facts
            if f.ownership == 'unresolved' or f.scoring == 'review_only'],
        completeness_is_measured=False, main_article_sections_unchanged=True)


class DocumentRightsException(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    notice: str = Field(min_length=1)
    interpretation: str = Field(min_length=1)


Relevance = Literal['administrative', 'clinical_data', 'background', 'mixed']
RightsBasis = Literal['source_license', 'page_exception', 'unclear']


class ScopeReview(BaseModel):
    """Common fields of one reviewed image, frame run or row run (as a PDF page review, plus ownership evidence)."""
    model_config = ConfigDict(extra='forbid', strict=True)
    status: Literal['inspected']
    contents: str = Field(min_length=1)
    clinical_relevance: Relevance
    clinical_relevance_notes: str = Field(min_length=1)
    patient_ids: list[str]
    ownership_evidence: list[SupplementOwnershipEvidence]
    rights_checked: Literal[True]
    rights_basis: RightsBasis
    rights_exceptions: list[DocumentRightsException]
    limitations: list[str]


class ImageReview(ScopeReview):
    """Pixel inspection of one image, with the supplemental image contract's panel model and privacy flags."""
    inspection_method: Literal['viewed_pixels']
    visual: FigureVisuals
    privacy: list[PanelFlags]


class VideoProbe(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    duration_seconds: float | None
    width: int | None
    height: int | None
    video_codec: str | None
    average_frame_rate: str | None
    audio_codec: str | None


class VideoFrame(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    index: int = Field(ge=1)
    timestamp_seconds: float = Field(ge=0)
    sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    bytes: int = Field(gt=0)
    path: str = Field(min_length=1)


class VideoSampling(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    method: Literal['ffmpeg_fixed_interval_full_resolution']
    every_seconds: float = Field(gt=0)
    max_frames: int = Field(ge=1)
    frames: list[VideoFrame] = Field(min_length=1)


class FrameBlockReview(ScopeReview):
    """A consecutive run of sampled frames, all personally viewed (frames between samples were not)."""
    first_frame: int = Field(ge=1)
    last_frame: int = Field(ge=1)
    inspection_method: Literal['viewed_sampled_frames']
    privacy: PanelFlags


class EmbeddedImage(BaseModel):
    """One media part embedded in a DOCX/PPTX/XLSX/ODF package (machine fields + reviewer `review`)."""
    model_config = ConfigDict(extra='forbid', strict=True)
    image: int = Field(ge=1)
    part: str = Field(min_length=1)
    url: str = Field(min_length=1)
    sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    bytes: int = Field(gt=0)
    path: str = Field(min_length=1)
    media_type: str
    preview_path: str | None
    preview_sha256: str | None
    review: ImageReview | None


class SampledVideo(BaseModel):
    """Machine probe and fixed-interval frames of one video, plus the reviewer's `frame_blocks` over those frames."""
    model_config = ConfigDict(extra='forbid', strict=True)
    media_type: str = Field(pattern=r'^video/')
    probe: VideoProbe
    sampling: VideoSampling
    frame_blocks: list[FrameBlockReview]
    content_inspected: Literal['sampled_frames']
    continuous_motion_inspected: Literal[False]
    audio_inspected: Literal[False]
    limitations: list[str] = Field(min_length=1)


class EmbeddedVideo(SampledVideo):
    """One video part embedded in a DOCX/PPTX/XLSX/ODF package (e.g. ppt/media/media1.mp4), reviewed as sampled
    frames exactly like a composite video member."""
    video: int = Field(ge=1)
    part: str = Field(min_length=1)
    url: str = Field(min_length=1)
    sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    bytes: int = Field(gt=0)
    path: str = Field(min_length=1)


class DocumentPageReview(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    page: int = Field(ge=1)
    status: Literal['inspected']
    inspection_method: Literal['rendered_page']
    contents: str = Field(min_length=1)
    clinical_relevance: Literal['administrative', 'clinical_data', 'background', 'mixed']
    clinical_relevance_notes: str = Field(min_length=1)
    patient_ids: list[str]
    rights_checked: Literal[True]
    rights_basis: Literal['source_license', 'page_exception', 'unclear']
    rights_exceptions: list[DocumentRightsException]
    limitations: list[str]


class DocumentAssetReview(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    url: str = Field(min_length=1)
    original_manifest_url: str | None
    filename: str | None
    md5: str | None
    sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    bytes: int = Field(gt=0)
    path: str = Field(min_length=1)
    source_license: dict
    media_type: Literal['application/pdf']
    page_count: int = Field(ge=1)
    pages: list[DocumentPageReview]


class DocumentSupplementTarget(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    format: Literal['dense-supplemental-document-reference/1']
    article_id: str
    source_text_sha256: str
    supplement_id: str
    status: Literal['inspected', 'partial', 'unavailable']
    document_classification: Literal['administrative_checklist', 'clinical_data', 'mixed', 'other']
    contents_summary: str = Field(min_length=1)
    assets: list[DocumentAssetReview]
    limitations: list[str]
    clinical_data: SupplementClinicalData | None = None


def pdf_page_count(path):
    """Check the actual PDF, never infer its page count from declared inspection."""
    path = Path(path)
    with path.open('rb') as stream:
        if b'%PDF-' not in stream.read(1024):
            raise ValueError('Supplement document is not a PDF')
    try:
        result = subprocess.run(['pdfinfo', str(path.resolve())], check=True,
                                capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError) as exc:
        raise ValueError('Actual PDF page-count verification requires working pdfinfo') from exc
    match = re.search(r'^Pages:\s+(\d+)\s*$', result.stdout, re.MULTILINE)
    if not match or int(match.group(1)) < 1:
        raise ValueError('Actual PDF page count unavailable')
    return int(match.group(1))


def validate_document_submission(target, source, annotation, *, verify_files=True):
    document = DocumentSupplementTarget.model_validate(target)
    if document.article_id != source['article_id'] or document.source_text_sha256 != source['text_sha256']:
        raise ValueError('Supplement target has stale/wrong article source')
    if annotation.get('article_id') != source['article_id'] or annotation.get('text_sha256') != source['text_sha256']:
        raise ValueError('Wrong/stale patient annotation')
    supplement = _supplement(source, document.supplement_id)
    declared = supplement.get('assets', [])
    urls = [a.get('url') for a in declared]
    if not urls or any(not u for u in urls) or len(urls) != len(set(urls)):
        raise ValueError('Supplement requires unique source-listed asset URLs')
    if any(not str(a.get('filename') or a['url'].split('?')[0]).lower().endswith('.pdf') for a in declared):
        raise ValueError('Document review supports only source-listed PDF assets')
    actual = [a.url for a in document.assets]
    if len(actual) != len(set(actual)) or set(actual) - set(urls):
        raise ValueError('Duplicate or undeclared supplemental asset URL')
    report = dict(supplement_id=document.supplement_id, complete=False,
                  inspected_assets=0, source_assets=len(urls), inspected_pages=0,
                  document_accuracy_measured=False)
    if document.status in ('partial', 'unavailable'):
        if not document.limitations:
            raise ValueError('Incomplete supplement requires limitations')
        return report
    if set(actual) != set(urls):
        raise ValueError('Complete inspection requires exact source-listed asset coverage')
    patients = {p['patient_id'] for p in annotation['patients']}
    relevance = []
    for asset in document.assets:
        original = next(a for a in declared if a['url'] == asset.url)
        for key in ('md5', 'filename', 'original_manifest_url'):
            if getattr(asset, key) != original.get(key):
                raise ValueError(f'Asset changed original source {key}')
        if asset.source_license != source.get('license'):
            raise ValueError('Asset source license must be preserved')
        if asset.md5 is not None and not re.fullmatch(r'[0-9a-f]{32}', asset.md5):
            raise ValueError('Invalid source MD5')
        pages = [p.page for p in asset.pages]
        if sorted(pages) != list(range(1, asset.page_count + 1)):
            raise ValueError('Every PDF page requires exactly one actual rendered-page inspection')
        for page in asset.pages:
            if len(page.patient_ids) != len(set(page.patient_ids)) or set(page.patient_ids) - patients:
                raise ValueError('Unknown or duplicate document patient identity')
            if (page.rights_basis == 'page_exception') != bool(page.rights_exceptions):
                raise ValueError('Page rights exceptions and rights basis disagree')
            relevance.append(page.clinical_relevance)
        if verify_files:
            data = Path(asset.path).read_bytes()
            if hashlib.sha256(data).hexdigest() != asset.sha256:
                raise ValueError('Supplement document bytes changed after inspection')
            if asset.md5 and hashlib.md5(data).hexdigest() != asset.md5:
                raise ValueError('Supplement document source MD5 mismatch')
            if asset.bytes != len(data):
                raise ValueError('Supplement document byte count mismatch')
            if pdf_page_count(asset.path) != asset.page_count:
                raise ValueError('Declared page count differs from actual PDF')
    if document.document_classification == 'administrative_checklist' and set(relevance) != {'administrative'}:
        raise ValueError('Administrative checklist cannot conceal clinical-data pages')
    if document.document_classification == 'clinical_data' and not set(relevance) & {'clinical_data', 'mixed'}:
        raise ValueError('Clinical-data classification requires clinical-data pages')
    report.update(complete=validate_clinical_data(target, source, annotation), inspected_assets=len(document.assets),
                  inspected_pages=sum(a.page_count for a in document.assets))
    return report


def _bundle_assets(target):
    """Exported asset rows: ZIP members carry their archive; embedded images/videos are rows of their own (parent_url)."""
    fmt = target.get('format')
    archive = target.get('archive') if fmt in (ARCHIVE_FORMAT, TEXT_FORMAT, COMPOSITE_FORMAT) else None
    rows = []
    for a in target['assets']:
        if archive:
            a = {**a, 'archive_url': archive['url'], 'archive_sha256': archive['sha256'], 'md5': None, 'filename': a['member'],
                 'original_manifest_url': archive.get('original_manifest_url'), 'source_license': archive['source_license']}
        rows.append(a)
        rows += [{'url': i['url'], 'sha256': i['sha256'], 'md5': None, 'bytes': i['bytes'], 'filename': i['part'],
                  'original_manifest_url': None, 'source_license': a.get('source_license'), 'kind': 'image',
                  'member': a.get('member'), 'archive_url': a.get('archive_url'), 'archive_sha256': a.get('archive_sha256'),
                  'parent_url': a['url'], 'part': i['part']} for i in a.get('embedded_images') or []]
        rows += [{'url': v['url'], 'sha256': v['sha256'], 'md5': None, 'bytes': v['bytes'], 'filename': v['part'],
                  'original_manifest_url': None, 'source_license': a.get('source_license'), 'kind': 'video',
                  'member': a.get('member'), 'archive_url': a.get('archive_url'), 'archive_sha256': a.get('archive_sha256'),
                  'parent_url': a['url'], 'part': v['part']} for v in a.get('embedded_videos') or []]
    return rows


ARCHIVE_JUNK = re.compile(r'(^|/)(__MACOSX/|\.DS_Store$|Thumbs\.db$|desktop\.ini$)')
MAX_MEMBER_BYTES = 250_000_000


def member_url(archive_url, member):
    return f'{archive_url}#member={member}'


def zip_members(path):
    """(PDF member names, other non-junk member names) from the central directory only."""
    import zipfile
    with zipfile.ZipFile(path) as archive:
        names = [i.filename for i in archive.infolist() if not i.is_dir() and not ARCHIVE_JUNK.search(i.filename)]
    pdfs = sorted(n for n in names if n.lower().endswith('.pdf'))
    return pdfs, sorted(set(names) - set(pdfs))


def zip_member_bytes(path, member, cap=MAX_MEMBER_BYTES):
    """Bounded in-memory read of one member; never writes outside the cache."""
    import zipfile
    with zipfile.ZipFile(path) as archive:
        info = archive.getinfo(member)
        if info.file_size > cap:
            raise ValueError('ZIP member exceeds the per-member byte cap')
        with archive.open(info) as stream:
            data = stream.read(cap + 1)
    if len(data) > cap:
        raise ValueError('ZIP member exceeds the per-member byte cap')
    return data


class ArchiveAsset(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    url: str = Field(min_length=1)
    original_manifest_url: str | None
    filename: str | None
    md5: str | None
    sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    bytes: int = Field(gt=0)
    path: str = Field(min_length=1)
    source_license: dict
    media_type: Literal['application/zip']
    members: list[str]
    other_members: list[str]


class ArchiveMemberReview(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    url: str = Field(min_length=1)
    member: str = Field(min_length=1)
    sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    bytes: int = Field(gt=0)
    path: str = Field(min_length=1)
    media_type: Literal['application/pdf']
    page_count: int = Field(ge=1)
    pages: list[DocumentPageReview]


class ArchiveDocumentTarget(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    format: Literal['dense-supplemental-archive-document-reference/1']
    article_id: str
    source_text_sha256: str
    supplement_id: str
    status: Literal['inspected', 'partial', 'unavailable']
    document_classification: Literal['administrative_checklist', 'clinical_data', 'mixed', 'other']
    contents_summary: str = Field(min_length=1)
    archive: ArchiveAsset
    assets: list[ArchiveMemberReview]
    limitations: list[str]
    clinical_data: SupplementClinicalData | None = None


def validate_archive_submission(target, source, annotation, *, verify_files=True):
    """A declared ZIP of PDFs: bind ZIP MD5/SHA256, member list, member SHA256 and pdfinfo page counts."""
    document = ArchiveDocumentTarget.model_validate(target)
    if document.article_id != source['article_id'] or document.source_text_sha256 != source['text_sha256']:
        raise ValueError('Supplement target has stale/wrong article source')
    if annotation.get('article_id') != source['article_id'] or annotation.get('text_sha256') != source['text_sha256']:
        raise ValueError('Wrong/stale patient annotation')
    supplement = _supplement(source, document.supplement_id)
    declared = supplement.get('assets', [])
    if len(declared) != 1 or not str(declared[0].get('filename') or declared[0].get('url', '').split('?')[0]).lower().endswith('.zip'):
        raise ValueError('Archive review supports one source-listed ZIP asset')
    original, archive = declared[0], document.archive
    if archive.url != original.get('url'):
        raise ValueError('Archive URL must be the source-listed ZIP')
    for key in ('md5', 'filename', 'original_manifest_url'):
        if getattr(archive, key) != original.get(key):
            raise ValueError(f'Archive changed original source {key}')
    if archive.source_license != source.get('license'):
        raise ValueError('Archive source license must be preserved')
    urls = [a.url for a in document.assets]
    if len(urls) != len(set(urls)):
        raise ValueError('Duplicate archive member')
    for asset in document.assets:
        if asset.member not in archive.members or asset.url != member_url(archive.url, asset.member):
            raise ValueError('Member review must cite a listed PDF member by its pseudo-URL')
    if verify_files:
        data = Path(archive.path).read_bytes()
        if hashlib.sha256(data).hexdigest() != archive.sha256 or len(data) != archive.bytes:
            raise ValueError('Archive bytes changed after inspection')
        if archive.md5 and hashlib.md5(data).hexdigest() != archive.md5:
            raise ValueError('Archive source MD5 mismatch')
        if zip_members(archive.path) != (archive.members, archive.other_members):
            raise ValueError('Archive member listing changed')
        for asset in document.assets:
            member = zip_member_bytes(archive.path, asset.member)
            extracted = Path(asset.path).read_bytes()
            if hashlib.sha256(member).hexdigest() != asset.sha256 or extracted != member or len(member) != asset.bytes:
                raise ValueError('Archive member bytes changed after inspection')
            if pdf_page_count(asset.path) != asset.page_count:
                raise ValueError('Declared page count differs from actual member PDF')
    report = dict(supplement_id=document.supplement_id, complete=False, inspected_assets=0,
                  source_assets=len(archive.members), inspected_pages=0, document_accuracy_measured=False)
    if document.status in ('partial', 'unavailable'):
        if not document.limitations:
            raise ValueError('Incomplete supplement requires limitations')
        return report
    if archive.other_members:
        raise ValueError('Complete archive review requires PDF-only members; record others as partial')
    if set(a.member for a in document.assets) != set(archive.members) or not archive.members:
        raise ValueError('Complete inspection requires every PDF member')
    patients = {p['patient_id'] for p in annotation['patients']}
    relevance = []
    for asset in document.assets:
        if sorted(p.page for p in asset.pages) != list(range(1, asset.page_count + 1)):
            raise ValueError('Every member PDF page requires exactly one actual rendered-page inspection')
        for page in asset.pages:
            if len(page.patient_ids) != len(set(page.patient_ids)) or set(page.patient_ids) - patients:
                raise ValueError('Unknown or duplicate document patient identity')
            if (page.rights_basis == 'page_exception') != bool(page.rights_exceptions):
                raise ValueError('Page rights exceptions and rights basis disagree')
            relevance.append(page.clinical_relevance)
    if document.document_classification == 'administrative_checklist' and set(relevance) != {'administrative'}:
        raise ValueError('Administrative checklist cannot conceal clinical-data pages')
    if document.document_classification == 'clinical_data' and not set(relevance) & {'clinical_data', 'mixed'}:
        raise ValueError('Clinical-data classification requires clinical-data pages')
    report.update(complete=validate_clinical_data(target, source, annotation), inspected_assets=len(document.assets),
                  inspected_pages=sum(a.page_count for a in document.assets))
    return report


TEXT_EXTENSIONS = tuple('.' + f for f in TEXT_DOCUMENT_TYPES)


def zip_text_members(path):
    """(word-processor member names, other non-junk member names) from the central directory only."""
    import zipfile
    with zipfile.ZipFile(path) as archive:
        names = [i.filename for i in archive.infolist() if not i.is_dir() and not ARCHIVE_JUNK.search(i.filename)]
    docs = sorted(n for n in names if n.lower().endswith(TEXT_EXTENSIONS))
    return docs, sorted(set(names) - set(docs))


class TextUnit(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    unit: int = Field(ge=1)
    kind: Literal['paragraph', 'table_row']
    table: int | None
    row: int | None
    text: str
    cells: list[str] | None


class TextExtraction(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    tool: str = Field(min_length=1)
    format: Literal[tuple(TEXT_DOCUMENT_TYPES)]
    units_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    unit_count: int = Field(ge=1)
    table_count: int = Field(ge=0)
    nonliteral_units: list[int]
    literal_verification: str


class TextBlockReview(BaseModel):
    """One reviewed run of consecutive units (like a PDF page review)."""
    model_config = ConfigDict(extra='forbid', strict=True)
    first_unit: int = Field(ge=1)
    last_unit: int = Field(ge=1)
    status: Literal['inspected']
    inspection_method: Literal['extracted_text', 'extracted_text_and_rendered_document']
    contents: str = Field(min_length=1)
    clinical_relevance: Literal['administrative', 'clinical_data', 'background', 'mixed']
    clinical_relevance_notes: str = Field(min_length=1)
    patient_ids: list[str]
    rights_checked: Literal[True]
    rights_basis: Literal['source_license', 'page_exception', 'unclear']
    rights_exceptions: list[DocumentRightsException]
    limitations: list[str]


class TextDocumentAsset(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    url: str = Field(min_length=1)
    member: str | None
    original_manifest_url: str | None
    filename: str | None
    md5: str | None
    sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    bytes: int = Field(gt=0)
    path: str = Field(min_length=1)
    source_license: dict
    media_type: Literal[tuple(TEXT_DOCUMENT_TYPES.values())]
    extraction: TextExtraction
    units: list[TextUnit] = Field(min_length=1)
    blocks: list[TextBlockReview]
    # Embedded pixels (word/media, ppt/media, Pictures/). Absent only in sidecars drafted before images were
    # inventoried; signing then re-reads the package and refuses if it embeds any image.
    embedded_images: list[EmbeddedImage] | None = None
    # Embedded video parts, split out of `embedded_images` (drafted only for a package that embeds video). Absent:
    # the legacy inventory, where a video part is an unviewable `embedded_images` row and keeps the review partial.
    embedded_videos: list[EmbeddedVideo] | None = None


class TextArchive(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    url: str = Field(min_length=1)
    original_manifest_url: str | None
    filename: str | None
    md5: str | None
    sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    bytes: int = Field(gt=0)
    path: str = Field(min_length=1)
    source_license: dict
    media_type: Literal['application/zip']
    members: list[str]
    other_members: list[str]


class TextDocumentTarget(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    format: Literal['dense-supplemental-text-document-reference/1']
    article_id: str
    source_text_sha256: str
    supplement_id: str
    status: Literal['inspected', 'partial', 'unavailable']
    document_classification: Literal['administrative_checklist', 'clinical_data', 'mixed', 'other']
    contents_summary: str = Field(min_length=1)
    archive: TextArchive | None
    assets: list[TextDocumentAsset]
    limitations: list[str]
    clinical_data: SupplementClinicalData | None = None


def _text_format(name):
    ext = Path(str(name or '').split('?')[0]).suffix.lower()
    return ext[1:] if ext in TEXT_EXTENSIONS else None


def validate_text_document_submission(target, source, annotation, *, verify_files=True):
    """DOCX/DOC/RTF/ODT/PPTX/ODP supplements (or such members of one declared ZIP) reviewed as extracted literal
    units, plus a pixel inspection of every embedded image."""
    document = TextDocumentTarget.model_validate(target)
    if document.article_id != source['article_id'] or document.source_text_sha256 != source['text_sha256']:
        raise ValueError('Supplement target has stale/wrong article source')
    if annotation.get('article_id') != source['article_id'] or annotation.get('text_sha256') != source['text_sha256']:
        raise ValueError('Wrong/stale patient annotation')
    supplement = _supplement(source, document.supplement_id)
    declared = supplement.get('assets', [])
    archive = document.archive
    if archive is not None:
        _check_declared_zip(archive, declared, source)
        expected = {member_url(archive.url, m): m for m in archive.members}
        if verify_files:
            _verify_zip_bytes(archive)
            if zip_text_members(archive.path) != (archive.members, archive.other_members):
                raise ValueError('Archive member listing changed')
    else:
        urls = [a.get('url') for a in declared]
        if not urls or any(not u for u in urls) or len(urls) != len(set(urls)):
            raise ValueError('Supplement requires unique source-listed asset URLs')
        if any(_text_format(a.get('filename') or a['url']) is None for a in declared):
            raise ValueError('Text-document review supports only source-listed DOCX/DOC/RTF/ODT/PPTX/ODP assets (or one ZIP of them)')
        expected = {u: None for u in urls}
    actual = [a.url for a in document.assets]
    if len(actual) != len(set(actual)) or set(actual) - set(expected):
        raise ValueError('Duplicate or undeclared text-document asset')
    for asset in document.assets:
        if asset.member != expected[asset.url]:
            raise ValueError('Text-document member must be cited by its archive pseudo-URL')
        fmt = _text_format(asset.member if archive is not None else (asset.filename or asset.url))
        if archive is None:
            _check_direct_asset(asset, declared, source)
        if verify_files:
            _verify_part_bytes(asset, archive)
        _verify_text_asset(asset, fmt, verify_files=verify_files)
    report = dict(supplement_id=document.supplement_id, complete=False, inspected_assets=0,
                  source_assets=len(expected), inspected_units=0, document_accuracy_measured=False)
    images = sum(len(a.embedded_images or []) for a in document.assets)
    frames = sum(len(v.sampling.frames) for a in document.assets for v in a.embedded_videos or [])
    if images:
        report['inspected_images'] = 0
    if frames:
        report['sampled_video_frames'] = 0
    if document.status in ('partial', 'unavailable'):
        if not document.limitations:
            raise ValueError('Incomplete supplement requires limitations')
        return report
    if set(actual) != set(expected) or not expected:
        raise ValueError('Complete inspection requires every declared document (every document member of the ZIP)')
    if archive is not None and archive.other_members:
        raise ValueError('Complete archive review requires word-processor-only members; record others as partial')
    context = _ReviewContext(source, annotation, target)
    relevance = []
    for asset in document.assets:
        relevance += _complete_text_asset(asset, context)
    _check_classification(document.document_classification, relevance)
    report.update(complete=validate_clinical_data(target, source, annotation), inspected_assets=len(document.assets),
                  inspected_units=sum(len(a.units) for a in document.assets))
    if images:
        report['inspected_images'] = images
    if frames:
        report['sampled_video_frames'] = frames
    return report


def _check_declared_zip(archive, declared, source):
    if len(declared) != 1 or not str(declared[0].get('filename') or declared[0].get('url', '').split('?')[0]).lower().endswith('.zip'):
        raise ValueError('An archive review needs one source-listed ZIP asset')
    original = declared[0]
    if archive.url != original.get('url'):
        raise ValueError('Archive URL must be the source-listed ZIP')
    for key in ('md5', 'filename', 'original_manifest_url'):
        if getattr(archive, key) != original.get(key):
            raise ValueError(f'Archive changed original source {key}')
    if archive.source_license != source.get('license'):
        raise ValueError('Archive source license must be preserved')


def _verify_zip_bytes(archive):
    data = Path(archive.path).read_bytes()
    if hashlib.sha256(data).hexdigest() != archive.sha256 or len(data) != archive.bytes:
        raise ValueError('Archive bytes changed after inspection')
    if archive.md5 and hashlib.md5(data).hexdigest() != archive.md5:
        raise ValueError('Archive source MD5 mismatch')


def _check_direct_asset(asset, declared, source):
    original = next(a for a in declared if a['url'] == asset.url)
    for key in ('md5', 'filename', 'original_manifest_url'):
        if getattr(asset, key) != original.get(key):
            raise ValueError(f'Asset changed original source {key}')
    if asset.source_license != source.get('license'):
        raise ValueError('Asset source license must be preserved')
    if asset.md5 is not None and not re.fullmatch(r'[0-9a-f]{32}', asset.md5):
        raise ValueError('Invalid source MD5')


def _verify_part_bytes(asset, archive):
    """Actual bytes of a declared asset (MD5) or of a ZIP member (re-read from the verified archive)."""
    data = Path(asset.path).read_bytes()
    if hashlib.sha256(data).hexdigest() != asset.sha256 or len(data) != asset.bytes:
        raise ValueError('Supplement bytes changed after inspection')
    if archive is not None:
        if zip_member_bytes(archive.path, asset.member) != data:
            raise ValueError('Archive member bytes changed after inspection')
    elif asset.md5 and hashlib.md5(data).hexdigest() != asset.md5:
        raise ValueError('Supplement source MD5 mismatch')
    return data


def _verify_text_asset(asset, fmt, *, verify_files):
    from openpatients2.document_text import TEXT_DOCUMENT_TYPES as types, extract_units, units_digest
    if asset.extraction.format != fmt or asset.media_type != types[fmt]:
        raise ValueError('Text-document format/media type disagree with the source filename')
    units = [u.model_dump() for u in asset.units]
    if [u['unit'] for u in units] != list(range(1, len(units) + 1)) or asset.extraction.unit_count != len(units):
        raise ValueError('Extracted units must be numbered 1..n in document order')
    if asset.extraction.units_sha256 != units_digest(units):
        raise ValueError('Extracted units were edited after extraction (units_sha256 mismatch)')
    _verify_embedded_images(asset, verify_files=verify_files)
    if verify_files:
        fresh = extract_units(asset.path, fmt)
        if fresh['units'] != units or fresh['tool'] != asset.extraction.tool or \
                fresh['nonliteral_units'] != asset.extraction.nonliteral_units:
            raise ValueError('Re-extraction differs from the reviewed units (different file or extractor version)')


def _complete_text_asset(asset, context):
    relevance, covered = [], []
    for block in asset.blocks:
        if block.last_unit < block.first_unit:
            raise ValueError('Text block must run from first_unit to last_unit')
        covered += list(range(block.first_unit, block.last_unit + 1))
        context.patients_of(block)
        relevance.append(block.clinical_relevance)
    if covered != list(range(1, len(asset.units) + 1)):
        raise ValueError('Every extracted unit requires exactly one reviewed block, in order')
    return relevance + _complete_embedded_images(asset, context)


def _check_classification(classification, relevance):
    if classification == 'administrative_checklist' and set(relevance) != {'administrative'}:
        raise ValueError('Administrative checklist cannot conceal clinical-data units')
    if classification == 'clinical_data' and not set(relevance) & {'clinical_data', 'mixed'}:
        raise ValueError('Clinical-data classification requires clinical-data units')


# ---------------------------------------------------------------- images (embedded or ZIP members)
IMAGE_TYPES = {'image_png': 'image/png', 'image_jpeg': 'image/jpeg', 'image_gif': 'image/gif',
               'image_tiff': 'image/tiff', 'image_webp': 'image/webp'}
# Viewable as-is by a reviewer; other raster formats (TIFF/BMP) need the PNG preview the extractor renders.
VIEWABLE_IMAGE_TYPES = {'image/png', 'image/jpeg', 'image/gif', 'image/webp'}


def image_media_type(data, name=None):
    """Media type by magic bytes; vector/unknown parts (EMF/WMF/SVG) are not pixel-reviewable by this path."""
    detected = detect_type(data[:4096], name)
    if detected in IMAGE_TYPES:
        return IMAGE_TYPES[detected]
    if data[:2] == b'BM':
        return 'image/bmp'
    if data[:4] == b'\x01\x00\x00\x00' and data[40:44] == b' EMF':
        return 'image/x-emf'
    if data[:4] == b'\xd7\xcd\xc6\x9a':
        return 'image/x-wmf'
    return 'application/octet-stream'


class _ReviewContext:
    """Patients, article segments and supplement transcriptions for checking review-level ownership evidence."""
    def __init__(self, source, annotation, target):
        self.source = source
        self.patients = {p['patient_id'] for p in annotation['patients']}
        self.segments = {s['segment_id']: s['text'] for s in source.get('segments', [])}
        self.transcriptions = {t['transcription_id']: t['text'] for t in (target.get('clinical_data') or {}).get('transcriptions', [])}
        self.sid = target['supplement_id']

    def patients_of(self, review, *, evidence=False):
        if len(review.patient_ids) != len(set(review.patient_ids)) or set(review.patient_ids) - self.patients:
            raise ValueError('Unknown or duplicate document patient identity')
        if (review.rights_basis == 'page_exception') != bool(review.rights_exceptions):
            raise ValueError('Rights exceptions and rights basis disagree')
        if not evidence:
            return
        if review.patient_ids and not review.ownership_evidence:
            raise ValueError('Patient attribution of an image/frame/row run requires literal ownership evidence')
        for e in review.ownership_evidence:
            if e.basis == 'supplement':
                if e.segment_id is not None or e.quote not in self.transcriptions.get(e.transcription_id, ''):
                    raise ValueError('Supplement ownership evidence must be literal in a reviewed transcription')
            elif e.transcription_id is not None or e.quote not in self.segments.get(e.segment_id, ''):
                raise ValueError('Article ownership requires literal immutable article evidence')

    def image(self, review):
        """The image contract's panel semantics: pixel cues, literal citations, one privacy flag set per panel."""
        self.patients_of(review, evidence=True)
        visual = validate_visuals(review.visual.model_dump(), self.source, self.sid)
        panels = [p['panel'] for p in visual['panels']]
        if sorted(map(str, (f.panel for f in review.privacy))) != sorted(map(str, panels)) or \
                len(review.privacy) != len(panels):
            raise ValueError('Every inspected image panel requires exactly one privacy flag set')
        return review.clinical_relevance


def embedded_media(path):
    """([(part, bytes)] embedded images, [(part, bytes)] embedded videos) of a package, split by magic bytes."""
    images, videos = [], []
    for part, data in embedded_images(path) or []:
        (videos if detect_type(data[:4096], part) == 'video' else images).append((part, data))
    return images, videos


def _verify_embedded_images(asset, *, verify_files):
    """Embedded image (and, when inventoried, embedded video) parts are exactly the package's media parts."""
    images, videos = asset.embedded_images, getattr(asset, 'embedded_videos', None)
    for n, image in enumerate(images or [], 1):
        if image.image != n or image.url != f'{asset.url}#part={image.part}':
            raise ValueError('Embedded images are numbered 1..n and cited by the pseudo-URL PARENT_URL#part=PART')
    for n, video in enumerate(videos or [], 1):
        if video.video != n or video.url != f'{asset.url}#part={video.part}':
            raise ValueError('Embedded videos are numbered 1..n and cited by the pseudo-URL PARENT_URL#part=PART')
        _verify_video(video, verify_files=False)
    if videos and images is None:
        raise ValueError('An embedded video inventory requires the embedded image inventory')
    if not verify_files:
        return
    if images is None or videos is None:   # legacy inventory: every media part (video included) is an image row
        actual, actual_videos = embedded_images(asset.path) or [], []
    else:
        actual, actual_videos = embedded_media(asset.path)
    expected = [(part, hashlib.sha256(data).hexdigest(), len(data)) for part, data in actual]
    if images is None:
        if expected:
            raise ValueError('The document embeds images that this sidecar does not inventory; re-run the extraction '
                             'command and inspect every embedded image')
        return
    if [(i.part, i.sha256, i.bytes) for i in images] != expected:
        raise ValueError('Embedded image inventory differs from the actual document parts')
    if [(v.part, v.sha256, v.bytes) for v in videos or []] != \
            [(part, hashlib.sha256(data).hexdigest(), len(data)) for part, data in actual_videos]:
        raise ValueError('Embedded video inventory differs from the actual document video parts; re-run the '
                         'extraction command and review the sampled frames of every embedded video')
    for image, (_, data) in zip(images, actual):
        if image.media_type != image_media_type(data, image.part) or Path(image.path).read_bytes() != data:
            raise ValueError('Extracted embedded image differs from its document part')
        _verify_preview(image)
    for video, (_, data) in zip(videos or [], actual_videos):
        if Path(video.path).read_bytes() != data:
            raise ValueError('Extracted embedded video differs from its document part')
        _verify_video(video, verify_files=True)   # ffprobe metadata and sampled frame bytes


def _verify_preview(image):
    if (image.preview_path is None) != (image.preview_sha256 is None):
        raise ValueError('Image preview path and SHA256 go together')
    if image.preview_path and hashlib.sha256(Path(image.preview_path).read_bytes()).hexdigest() != image.preview_sha256:
        raise ValueError('Image preview changed after extraction')


def _complete_image(image, context, what):
    if image.review is None:
        raise ValueError(f'Every {what} requires its own pixel inspection (otherwise record the review as partial)')
    if image.media_type not in VIEWABLE_IMAGE_TYPES and not image.preview_path:
        raise ValueError(f'{what} format {image.media_type} cannot be viewed (no raster preview); record the review as partial')
    return context.image(image.review)


def _complete_video(video, context):
    """Frame blocks cover every sampled frame, each with ownership evidence and one whole-frame privacy flag set."""
    _blocks_cover(video.frame_blocks, 'first_frame', 'last_frame', len(video.sampling.frames), 'sampled frame')
    relevance = []
    for block in video.frame_blocks:
        context.patients_of(block, evidence=True)
        if block.privacy.panel is not None:
            raise ValueError('Frame privacy flags describe the whole frame (panel null)')
        relevance.append(block.clinical_relevance)
    return relevance


def _complete_embedded_images(asset, context):
    """Every embedded image needs a pixel review; every embedded video, frame blocks over all its sampled frames."""
    videos = getattr(asset, 'embedded_videos', None)
    if videos is None and any(extension(i.part) in VIDEO_EXTENSIONS for i in asset.embedded_images or []):
        raise ValueError('This sidecar inventories embedded video parts as images; re-run the extraction command '
                         '(embedded_videos) and review the sampled frames of every embedded video')
    relevance = [_complete_image(i, context, 'embedded image') for i in asset.embedded_images or []]
    for video in videos or []:
        relevance += _complete_video(video, context)
    return relevance


def draft_text_document(source, supplement_id, files):
    """Skeleton text-document sidecar: `files` = [(asset_dict, path, member|None)], archive = asset_dict of the ZIP."""
    from openpatients2.document_text import TEXT_DOCUMENT_TYPES as types, extract_units
    assets = []
    for asset, path, member in files:
        fmt = _text_format(member or asset.get('filename') or asset['url'])
        data = Path(path).read_bytes()
        extraction = extract_units(path, fmt)
        units = extraction.pop('units')
        assets.append({'url': member_url(asset['url'], member) if member else asset['url'], 'member': member,
                       'original_manifest_url': None if member else asset.get('original_manifest_url'),
                       'filename': member if member else asset.get('filename'), 'md5': None if member else asset.get('md5'),
                       'sha256': hashlib.sha256(data).hexdigest(), 'bytes': len(data), 'path': str(Path(path).resolve()),
                       'source_license': source['license'], 'media_type': types[fmt], 'extraction': extraction,
                       'units': units, 'blocks': []})
    return {'format': TEXT_FORMAT, 'article_id': source['article_id'], 'source_text_sha256': source['text_sha256'],
            'supplement_id': supplement_id, 'status': 'partial', 'document_classification': 'other',
            'contents_summary': 'DRAFT: describe after reading every extracted unit', 'archive': None, 'assets': assets,
            'limitations': ['DRAFT skeleton: units not yet reviewed']}


# ---------------------------------------------------------------- composite: mixed ZIPs and spreadsheets
MEMBER_KINDS = ('pdf', 'text', 'image', 'video', 'spreadsheet')
XLSX_TYPE = 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
SHEET_MAX_ROWS = 250_000      # non-empty rows per sheet; a larger sheet is truncated and its review can only be partial
SHEET_MAX_CELLS = 5_000_000   # non-empty cells per sheet, likewise
SPREADSHEET_TOOL = 'xlsx_stream/1'


def member_kind(data, name=None):
    """(kind, detected_type) by content: magic bytes, then the package's own parts (an extensionless or misnamed
    OOXML package is still a DOCX/PPTX/XLSX). kind is one of MEMBER_KINDS or 'unsupported'."""
    detected = detect_type(data[:1_000_000], name)
    if data[:4] == b'PK\x03\x04' and detected not in ('odt', 'odp', 'ods'):
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as package:
                names = set(package.namelist())
        except zipfile.BadZipFile:
            names = set()
        detected = ('docx' if 'word/document.xml' in names else 'pptx' if 'ppt/presentation.xml' in names
                    else 'xlsx' if 'xl/workbook.xml' in names else 'zip')
    if detected == 'pdf':
        return 'pdf', detected
    if detected in TEXT_DOCUMENT_TYPES:
        return 'text', detected
    if detected == 'xlsx':
        return 'spreadsheet', detected
    if detected in IMAGE_TYPES:
        return 'image', detected
    if detected == 'video':
        return 'video', detected
    return 'unsupported', detected


def composite_members(path):
    """[{member, kind, detected_type}] for every non-junk ZIP member, classified by its actual bytes (bounded reads)."""
    out = []
    with zipfile.ZipFile(path) as archive:
        infos = sorted((i for i in archive.infolist() if not i.is_dir() and not ARCHIVE_JUNK.search(i.filename)),
                       key=lambda i: i.filename)
        for info in infos:
            if info.file_size > MAX_MEMBER_BYTES:
                out.append({'member': info.filename, 'kind': 'unsupported', 'detected_type': 'exceeds_member_cap'})
                continue
            kind, detected = member_kind(archive.read(info), info.filename)
            out.append({'member': info.filename, 'kind': kind, 'detected_type': detected})
    if len({m['member'] for m in out}) != len(out):
        raise ValueError('ZIP lists a member name more than once')
    return out


def column_letter(index):
    letters = ''
    index += 1
    while index:
        index, rest = divmod(index - 1, 26)
        letters = chr(65 + rest) + letters
    return letters


def _runs(numbers):
    runs = []
    for n in numbers:
        if runs and n == runs[-1][1] + 1:
            runs[-1][1] = n
        else:
            runs.append([n, n])
    return runs


def spreadsheet_inventory(path, *, dump_dir=None, wanted=(), max_rows=None, max_cells=None):
    """Stream every sheet once: ([sheet inventory], {(sheet, A1): text} for `wanted` cells).

    Cell text is the stored literal value (<v>, shared or inline string); formulas are never evaluated and
    number formats are not applied. A sheet above a cap, or whose part is missing/too large, is `truncated`.
    With dump_dir, each sheet's non-empty rows are written as JSONL ({"row": n, "cells": {"A": ...}})."""
    wanted = set(wanted); cells = {}; sheets = []
    max_rows = SHEET_MAX_ROWS if max_rows is None else max_rows
    max_cells = SHEET_MAX_CELLS if max_cells is None else max_cells
    with XlsxReader(path) as reader:
        for index, sheet in enumerate(reader.sheets, 1):
            entry = {'sheet': index, 'name': sheet['name'], 'state': sheet['state'], 'nonempty_rows': 0, 'row_ranges': [],
                     'max_column': None, 'cell_count': 0, 'cells_sha256': None, 'truncated': False, 'row_dump': None}
            sheets.append(entry)
            if not sheet['available'] or reader.part_bytes(sheet) > XLSX_MAX_PART_BYTES:
                entry['truncated'] = True
                continue
            digest_ = hashlib.sha256(); rows = []; width = -1
            dump = None
            if dump_dir is not None:
                entry['row_dump'] = str((Path(dump_dir) / f'sheet{index:02d}.jsonl').resolve())
                dump = open(entry['row_dump'], 'w', encoding='utf-8')
            try:
                for number, values in reader.rows(sheet):
                    present = {i: t for i, t in sorted(values.items()) if t != ''}
                    if not any(t.strip() for t in present.values()):
                        continue
                    if len(rows) >= max_rows or entry['cell_count'] + len(present) > max_cells:
                        entry['truncated'] = True
                        break
                    rows.append(number); entry['cell_count'] += len(present); width = max(width, max(present))
                    row = {column_letter(i): t for i, t in present.items()}
                    for letter, text in row.items():
                        if (index, f'{letter}{number}') in wanted:
                            cells[(index, f'{letter}{number}')] = text
                    line = json.dumps({'row': number, 'cells': row}, ensure_ascii=False, sort_keys=True)
                    digest_.update(line.encode() + b'\n')
                    if dump:
                        dump.write(line + '\n')
            finally:
                if dump:
                    dump.close()
            entry.update(nonempty_rows=len(rows), row_ranges=_runs(sorted(set(rows))), cells_sha256=digest_.hexdigest(),
                         max_column=column_letter(width) if width >= 0 else None)
    return sheets, cells


def _inventory_digest(sheets):
    return digest([{k: v for k, v in s.items() if k != 'row_dump'} for s in sheets])


class _Part(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    url: str = Field(min_length=1)
    member: str | None
    original_manifest_url: str | None
    filename: str | None
    md5: str | None
    sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    bytes: int = Field(gt=0)
    path: str = Field(min_length=1)
    source_license: dict


class CompositePdf(_Part):
    kind: Literal['pdf']
    media_type: Literal['application/pdf']
    page_count: int = Field(ge=1)
    pages: list[DocumentPageReview]


class CompositeText(TextDocumentAsset):
    kind: Literal['text']


class CompositeImage(_Part):
    kind: Literal['image']
    media_type: str
    preview_path: str | None
    preview_sha256: str | None
    review: ImageReview | None


class CompositeVideo(_Part, SampledVideo):
    kind: Literal['video']


class SheetInventory(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    sheet: int = Field(ge=1)
    name: str | None
    state: str
    nonempty_rows: int = Field(ge=0)
    row_ranges: list[list[int]]
    max_column: str | None
    cell_count: int = Field(ge=0)
    cells_sha256: str | None
    truncated: bool
    row_dump: str | None


class SpreadsheetExtraction(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    tool: Literal['xlsx_stream/1']
    formulas_evaluated: Literal[False]
    sheet_count: int = Field(ge=1)
    sheets: list[SheetInventory]
    sheets_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')


class RowBlockReview(ScopeReview):
    """A run of rows; `homogeneous_block_summary` (large uniform blocks) must state what was summarized."""
    first_row: int = Field(ge=1)
    last_row: int = Field(ge=1)
    inspection_method: Literal['every_row_read', 'homogeneous_block_summary']


class SheetReview(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    sheet: int = Field(ge=1)
    status: Literal['inspected']
    contents: str = Field(min_length=1)
    blocks: list[RowBlockReview]
    limitations: list[str]


class CompositeSpreadsheet(_Part):
    kind: Literal['spreadsheet']
    media_type: Literal['application/vnd.openxmlformats-officedocument.spreadsheetml.sheet']
    extraction: SpreadsheetExtraction
    sheet_reviews: list[SheetReview]
    embedded_images: list[EmbeddedImage]
    embedded_videos: list[EmbeddedVideo] | None = None


CompositePart = Annotated[Union[CompositePdf, CompositeText, CompositeImage, CompositeVideo, CompositeSpreadsheet],
                          Field(discriminator='kind')]


class CompositeMember(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    member: str = Field(min_length=1)
    kind: Literal['pdf', 'text', 'image', 'video', 'spreadsheet', 'unsupported']
    detected_type: str


class CompositeArchive(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    url: str = Field(min_length=1)
    original_manifest_url: str | None
    filename: str | None
    md5: str | None
    sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    bytes: int = Field(gt=0)
    path: str = Field(min_length=1)
    source_license: dict
    media_type: Literal['application/zip']
    members: list[CompositeMember]


class CompositeSupplementTarget(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    format: Literal['dense-supplemental-composite-reference/1']
    article_id: str
    source_text_sha256: str
    supplement_id: str
    status: Literal['inspected', 'partial', 'unavailable']
    document_classification: Literal['administrative_checklist', 'clinical_data', 'mixed', 'other']
    contents_summary: str = Field(min_length=1)
    archive: CompositeArchive | None
    assets: list[CompositePart]
    limitations: list[str]
    clinical_data: SupplementClinicalData | None = None


def _blocks_cover(blocks, first, last, count, what):
    covered = []
    for block in blocks:
        if getattr(block, last) < getattr(block, first):
            raise ValueError(f'{what} block must run from {first} to {last}')
        covered += list(range(getattr(block, first), getattr(block, last) + 1))
    if covered != list(range(1, count + 1)):
        raise ValueError(f'Every {what} requires exactly one reviewed block, in order')


def video_timestamps(probe, every, max_frames):
    """op2 dense video_frames fixed-interval timestamps over the whole duration, ending two frame intervals before
    the container end (the final instants often have no decodable frame, e.g. when audio outlasts video)."""
    from openpatients2.dense.video_frames import timestamps
    duration = probe['duration_seconds']
    try:
        num, den = (float(x) for x in str(probe.get('average_frame_rate') or '').split('/'))
        margin = 2 * den / num if num > 0 and den > 0 else 0.1
    except ValueError:
        margin = 0.1
    return timestamps(max(0.0, duration - margin) if duration else duration, every, max_frames)


def _verify_video(asset, *, verify_files):
    from openpatients2.dense.video_frames import probe
    frames = asset.sampling.frames
    if [f.index for f in frames] != list(range(1, len(frames) + 1)):
        raise ValueError('Sampled frames are numbered 1..n')
    if [f.timestamp_seconds for f in frames] != video_timestamps(asset.probe.model_dump(), asset.sampling.every_seconds,
                                                                 asset.sampling.max_frames):
        raise ValueError('Sampled frames must be the fixed-interval timestamps across the whole video duration')
    if not any(re.search(r'sampl|frame', text, re.I) for text in asset.limitations):
        raise ValueError('Video limitations must state the frame-sampling scope and what was not reviewed')
    if verify_files:
        if VideoProbe.model_validate(probe(asset.path)) != asset.probe:
            raise ValueError('ffprobe metadata differs from the reviewed video')
        for frame in frames:
            data = Path(frame.path).read_bytes()
            if hashlib.sha256(data).hexdigest() != frame.sha256 or len(data) != frame.bytes:
                raise ValueError('Sampled frame bytes changed after inspection')


def _verify_spreadsheet(asset, *, verify_files, wanted):
    sheets = [s.model_dump() for s in asset.extraction.sheets]
    if [s['sheet'] for s in sheets] != list(range(1, len(sheets) + 1)) or asset.extraction.sheet_count != len(sheets):
        raise ValueError('Sheet inventory must list sheets 1..n in workbook order')
    if asset.extraction.sheets_sha256 != _inventory_digest(sheets):
        raise ValueError('Sheet inventory was edited after extraction (sheets_sha256 mismatch)')
    _verify_embedded_images(asset, verify_files=verify_files)
    if not verify_files:
        return None
    fresh, cells = spreadsheet_inventory(asset.path, wanted=wanted)
    if _inventory_digest(fresh) != asset.extraction.sheets_sha256:
        raise ValueError('Re-parsed sheets differ from the reviewed inventory (different file or parser)')
    return {(asset.url, sheet, ref): text for (sheet, ref), text in cells.items()}


def _complete_spreadsheet(asset, context):
    relevance = []
    if [r.sheet for r in asset.sheet_reviews] != [s.sheet for s in asset.extraction.sheets]:
        raise ValueError('Every sheet requires exactly one sheet review, in workbook order')
    for inventory, review in zip(asset.extraction.sheets, asset.sheet_reviews):
        if inventory.truncated:
            raise ValueError(f'Sheet {inventory.sheet} ({inventory.name}) exceeds the parse caps or is unreadable; '
                             'a complete review is impossible, record the review as partial')
        previous = 0
        for block in review.blocks:
            if block.first_row <= previous or block.last_row < block.first_row:
                raise ValueError('Row blocks must be ordered, non-overlapping runs first_row..last_row')
            previous = block.last_row
            if not any(a <= block.last_row and block.first_row <= b for a, b in inventory.row_ranges):
                raise ValueError('A row block must contain at least one non-empty parsed row')
            if block.inspection_method == 'homogeneous_block_summary' and not block.limitations:
                raise ValueError('A homogeneous block summary must state in limitations what was summarized')
            context.patients_of(block, evidence=True)
            relevance.append(block.clinical_relevance)
        blocks = [x.model_dump() for x in review.blocks]
        for a, b in inventory.row_ranges:
            row = a
            while row <= b:
                block = _containing(blocks, 'first_row', 'last_row', row)
                if block is None:
                    raise ValueError(f'Sheet {inventory.sheet} row {row} is not covered by any row block')
                row = block['last_row'] + 1
    return relevance + _complete_embedded_images(asset, context)


def validate_composite_submission(target, source, annotation, *, verify_files=True):
    """One declared ZIP of heterogeneous members, or directly declared spreadsheets, each member under its own rule."""
    document = CompositeSupplementTarget.model_validate(target)
    if document.article_id != source['article_id'] or document.source_text_sha256 != source['text_sha256']:
        raise ValueError('Supplement target has stale/wrong article source')
    if annotation.get('article_id') != source['article_id'] or annotation.get('text_sha256') != source['text_sha256']:
        raise ValueError('Wrong/stale patient annotation')
    supplement = _supplement(source, document.supplement_id)
    declared = supplement.get('assets', [])
    archive = document.archive
    if archive is not None:
        _check_declared_zip(archive, declared, source)
        if verify_files:
            _verify_zip_bytes(archive)
            if composite_members(archive.path) != [m.model_dump() for m in archive.members]:
                raise ValueError('Archive member listing or member types changed')
        members = {m.member: m for m in archive.members}
        if len(members) != len(archive.members):
            raise ValueError('Duplicate archive member')
        expected = {member_url(archive.url, m.member): m.kind for m in archive.members if m.kind != 'unsupported'}
    else:
        urls = [a.get('url') for a in declared]
        if not urls or any(not u for u in urls) or len(urls) != len(set(urls)):
            raise ValueError('Supplement requires unique source-listed asset URLs')
        expected = {a.url: a.kind for a in document.assets if a.url in urls}
        expected.update({u: None for u in urls if u not in expected})
    actual = [a.url for a in document.assets]
    if len(actual) != len(set(actual)) or set(actual) - set(expected):
        raise ValueError('Duplicate or undeclared composite asset')
    wanted = {}
    for t in (document.clinical_data.transcriptions if document.clinical_data else []):
        if t.cell:
            wanted.setdefault(t.asset_url, set()).add((t.page, t.cell))
    cells = {} if verify_files else None
    for asset in document.assets:
        if archive is not None:
            if asset.member not in members or members[asset.member].kind != asset.kind or \
                    asset.url != member_url(archive.url, asset.member):
                raise ValueError('Member review must cite a listed member of its detected kind by its pseudo-URL')
            if asset.source_license != archive.source_license:
                raise ValueError('Member source license must be the archive license')
        else:
            if asset.member is not None:
                raise ValueError('A directly declared asset has no archive member')
            _check_direct_asset(asset, declared, source)
        if verify_files:
            data = _verify_part_bytes(asset, archive)
            if member_kind(data, asset.member or asset.filename)[0] != asset.kind:
                raise ValueError('Asset bytes are not of the reviewed kind')
        if asset.kind == 'pdf':
            if verify_files and pdf_page_count(asset.path) != asset.page_count:
                raise ValueError('Declared page count differs from actual PDF')
        elif asset.kind == 'text':
            fmt = asset.extraction.format
            if fmt != (members[asset.member].detected_type if archive is not None else
                       member_kind(data, asset.filename)[1] if verify_files else fmt):
                raise ValueError('Text-document format disagrees with the detected member type')
            _verify_text_asset(asset, fmt, verify_files=verify_files)
        elif asset.kind == 'image':
            if verify_files:
                if asset.media_type != image_media_type(data, asset.member or asset.filename):
                    raise ValueError('Image media type disagrees with its bytes')
                _verify_preview(asset)
        elif asset.kind == 'video':
            _verify_video(asset, verify_files=verify_files)
        else:
            found = _verify_spreadsheet(asset, verify_files=verify_files, wanted=wanted.get(asset.url, ()))
            if found is not None:
                cells.update(found)
    counts = dict(inspected_pages=sum(a.page_count for a in document.assets if a.kind == 'pdf'),
                  inspected_units=sum(len(a.units) for a in document.assets if a.kind == 'text'),
                  inspected_images=sum(a.kind == 'image' for a in document.assets) +
                      sum(len(a.embedded_images or []) for a in document.assets if a.kind in ('text', 'spreadsheet')),
                  sampled_video_frames=sum(len(a.sampling.frames) for a in document.assets if a.kind == 'video') +
                      sum(len(v.sampling.frames) for a in document.assets if a.kind in ('text', 'spreadsheet')
                          for v in a.embedded_videos or []),
                  inspected_sheet_rows=sum(s.nonempty_rows for a in document.assets if a.kind == 'spreadsheet'
                                           for s in a.extraction.sheets))
    report = dict(supplement_id=document.supplement_id, complete=False, inspected_assets=0, source_assets=len(expected),
                  **{k: 0 for k in counts}, document_accuracy_measured=False)
    if document.status in ('partial', 'unavailable'):
        if not document.limitations:
            raise ValueError('Incomplete supplement requires limitations')
        return report
    if archive is not None and any(m.kind == 'unsupported' for m in archive.members):
        raise ValueError('Unsupported archive members cannot be reviewed by this path; record the review as partial')
    if set(actual) != set(expected) or not expected:
        raise ValueError('Complete inspection requires every member (every declared asset), each of its detected kind')
    context = _ReviewContext(source, annotation, target)
    relevance = []
    for asset in document.assets:
        if asset.kind == 'pdf':
            if sorted(p.page for p in asset.pages) != list(range(1, asset.page_count + 1)):
                raise ValueError('Every member PDF page requires exactly one actual rendered-page inspection')
            for page in asset.pages:
                context.patients_of(page)
                relevance.append(page.clinical_relevance)
        elif asset.kind == 'text':
            relevance += _complete_text_asset(asset, context)
        elif asset.kind == 'image':
            relevance.append(_complete_image(asset, context, 'image member'))
        elif asset.kind == 'video':
            relevance += _complete_video(asset, context)
        else:
            relevance += _complete_spreadsheet(asset, context)
    _check_classification(document.document_classification, relevance)
    report.update(complete=validate_clinical_data(target, source, annotation, cells=cells),
                  inspected_assets=len(document.assets), **counts)
    return report


def review_status(target):
    """Receipt status naming exactly what was reviewed (export prefixes `agent_and_parent_`)."""
    fmt = target.get('format')
    images = any(a.get('embedded_images') for a in target.get('assets', []))
    videos = any(a.get('embedded_videos') for a in target.get('assets', []))
    if fmt == TEXT_FORMAT:
        return ('source_and_document_text' + ('_and_images' if images else '') +
                ('_and_sampled_video_frames' if videos else '') + '_reviewed')
    if fmt == COMPOSITE_FORMAT:
        kinds = {a['kind'] for a in target['assets']} | ({'image'} if images else set()) | ({'video'} if videos else set())
        labels = [label for kind, label in (('pdf', 'document_pages'), ('text', 'document_text'), ('image', 'images'),
                                            ('spreadsheet', 'spreadsheet_cells'), ('video', 'sampled_video_frames'))
                  if kind in kinds]
        return 'source_and_' + '_and_'.join(labels or ['members']) + '_reviewed'
    if fmt in DOCUMENT_FORMATS:
        return 'source_and_document_pages_reviewed'
    return 'source_and_pixels_reviewed'


def sampled_video(target):
    """True when the review covers video only through sampled frames (never continuous motion or audio)."""
    assets = target.get('assets', []) if target.get('format') in (TEXT_FORMAT, COMPOSITE_FORMAT) else []
    return any(a.get('kind') == 'video' or a.get('embedded_videos') for a in assets)


def target_patient_ids(target):
    """Patients attributed anywhere in a reviewed document-like sidecar (pages, blocks, images, frames, rows)."""
    scopes = []
    for a in target.get('assets', []):
        scopes += a.get('pages', []) + a.get('blocks', []) + a.get('frame_blocks', [])
        scopes += [b for s in a.get('sheet_reviews', []) for b in s['blocks']]
        scopes += [i['review'] for i in a.get('embedded_images') or [] if i.get('review')]
        scopes += [b for v in a.get('embedded_videos') or [] for b in v['frame_blocks']]
        if a.get('review'):
            scopes.append(a['review'])
    return {pid for scope in scopes for pid in scope['patient_ids']}


def _supplement(source, sid):
    rows = [s for s in source.get('supplements', []) if s.get('supplement_id') == sid]
    if len(rows) != 1:
        raise ValueError('Unknown or duplicate source supplement identity')
    if sid in {f['figure_key'] for f in source.get('figures', [])}:
        raise ValueError('Supplement identity must remain separate from main figures')
    if not re.fullmatch(r'[A-Za-z0-9_.-]+', sid) or sid in ('.', '..'):
        raise ValueError('Unsafe supplement identity')
    return rows[0]


def _temporary_article(source, supplement, match=None):
    """Supply only original supplemental reference/caption links to joint validation."""
    sid = supplement['supplement_id']
    captions = list(supplement.get('caption_segment_ids', []))
    references = list(supplement.get('source_segment_ids', []))
    for segment in source.get('segments', []):
        for ref in segment.get('cross_references', []):
            if ref.get('ref_type') in ('supplementary-material', 'supplement', 'supp') and sid in ref.get('target_ids', []):
                references.append(segment['segment_id'])
    if match:
        original = next(f for f in source['figures'] if f['figure_key'] == match['figure_id'])
        references += original['caption_segment_ids'] + original.get('source_segment_ids', [])
    article = deepcopy(source)
    article['figures'] = [dict(figure_key=sid, figure_id=sid, group_id=None,
        caption_segment_ids=captions, source_segment_ids=list(dict.fromkeys(references)),
        image_urls=[a['url'] for a in supplement.get('assets', [])])]
    return article


def validate_submission(target, source, annotation, *, verify_files=True):
    if target.get('format') == COMPOSITE_FORMAT:
        return validate_composite_submission(target, source, annotation, verify_files=verify_files)
    if target.get('format') == TEXT_FORMAT:
        return validate_text_document_submission(target, source, annotation, verify_files=verify_files)
    if target.get('format') == ARCHIVE_FORMAT:
        return validate_archive_submission(target, source, annotation, verify_files=verify_files)
    if target.get('format') == DOCUMENT_FORMAT:
        return validate_document_submission(target, source, annotation, verify_files=verify_files)
    if target.get('format') != FORMAT:
        raise ValueError('Unknown supplemental visual format')
    if target.get('article_id') != source['article_id'] or target.get('source_text_sha256') != source['text_sha256']:
        raise ValueError('Supplement target has stale/wrong article source')
    if annotation.get('article_id') != source['article_id'] or annotation.get('text_sha256') != source['text_sha256']:
        raise ValueError('Wrong/stale patient annotation')
    supplement = _supplement(source, target.get('supplement_id'))
    declared = supplement.get('assets', [])
    urls = [a.get('url') for a in declared]
    if not urls or any(not u for u in urls) or len(set(urls)) != len(urls):
        raise ValueError('Supplement requires unique source-listed asset URLs')
    if any(a.get('kind') != 'image' for a in declared):
        raise ValueError('Only image supplements can complete this image review gate')
    rows = target.get('assets', [])
    actual = [a.get('url') for a in rows]
    if len(actual) != len(set(actual)) or set(actual) - set(urls):
        raise ValueError('Duplicate or undeclared supplemental asset URL')
    status = target.get('status')
    if status in ('partial', 'unavailable'):
        if not target.get('limitations'):
            raise ValueError('Incomplete supplement requires limitations')
        return dict(supplement_id=supplement['supplement_id'], complete=False,
                    inspected_assets=0, source_assets=len(urls), pixel_accuracy_measured=False)
    if status != 'inspected' or len(actual) != len(urls) or set(actual) != set(urls):
        raise ValueError('Complete inspection requires exact source-listed asset coverage')
    declared_by_url = {a['url']: a for a in declared}
    for asset in rows:
        if not re.fullmatch(r'[0-9a-f]{64}', asset.get('sha256', '')):
            raise ValueError('Asset requires its SHA256')
        original = declared_by_url[asset['url']]
        for key in ('md5', 'filename', 'original_manifest_url'):
            if original.get(key) is not None and asset.get(key) != original[key]:
                raise ValueError(f'Asset changed original source {key}')
        if asset.get('source_license') != source.get('license'):
            raise ValueError('Asset source license must be preserved')
        if verify_files:
            data = Path(asset['path']).read_bytes()
            if hashlib.sha256(data).hexdigest() != asset['sha256']:
                raise ValueError('Supplement image bytes changed after inspection')
            if original.get('md5') and hashlib.md5(data).hexdigest() != original['md5']:
                raise ValueError('Supplement image source MD5 mismatch')
            if asset.get('bytes') != len(data):
                raise ValueError('Supplement image byte count mismatch')
    match = target.get('pixel_match_to_main_figure')
    if match:
        figures = [f for f in source.get('figures', []) if f['figure_key'] == match.get('figure_id')]
        if len(figures) != 1 or match.get('kind') != 'exact_photographic_correspondence' or not match.get('visual_cue'):
            raise ValueError('Main-image bridge requires documented exact photographic correspondence')
        original = figures[0]; media = match.get('asset', {})
        if media.get('url') not in original.get('image_urls', []) or not re.fullmatch(r'[0-9a-f]{64}',media.get('sha256','')):
            raise ValueError('Main-image correspondence must bind its declared source asset')
        if verify_files:
            data = Path(media['path']).read_bytes()
            if hashlib.sha256(data).hexdigest() != media['sha256']:
                raise ValueError('Matched main image bytes changed')
            declared_md5 = next((a.get('md5') for a in original.get('media', []) if a.get('url') == media['url']),None)
            if declared_md5 and hashlib.md5(data).hexdigest() != declared_md5:
                raise ValueError('Matched main image source MD5 mismatch')
    roster = {'patients': [{'patient_id': p['patient_id']} for p in annotation['patients']]}
    joint = validate_joint(target['analysis'], _temporary_article(source, supplement, match), roster, supplement['supplement_id'])
    assignments = joint['attribution']['assignments']
    if not any(a['panel'] is None for a in assignments):
        if {a['panel'] for a in assignments} != {p['panel'] for p in joint['visual']['panels']}:
            raise ValueError('Every supplemental panel requires attribution or unresolved ownership')
    return dict(supplement_id=supplement['supplement_id'], complete=validate_clinical_data(target,source,annotation),
                inspected_assets=len(rows), source_assets=len(urls), pixel_accuracy_measured=False)


def _targets(root, aid):
    paths = store.supplement_targets(root, aid)
    targets = [json.loads(p.read_text()) for p in paths]
    ids = [t.get('supplement_id') for t in targets]
    if len(ids) != len(set(ids)):
        raise ValueError('Duplicate supplemental review sidecars')
    return targets


def _inputs(root, aid):
    source = json.loads(store.path(root, 'sources', aid).read_text())
    folder = 'annotations' if store.path(root, 'annotations', aid).exists() else 'drafts'
    annotation = json.loads(store.path(root, folder, aid).read_text())
    return source, annotation


def _binding(target, source, annotation):
    supplement = _supplement(source, target['supplement_id'])
    binding = dict(article_id=source['article_id'], supplement_id=target['supplement_id'],
        status=review_status(target), source_text_sha256=source['text_sha256'],
        source_supplement_sha256=digest(supplement), target_sha256=digest(target),
        asset_sha256=digest([{'url':a['url'],'sha256':a['sha256']} for a in sorted(target['assets'],key=lambda a:a['url'])]),
        patient_identity_sha256=identity_digest(annotation),
        patient_ids=sorted(p['patient_id'] for p in annotation['patients']))
    if target.get('format') in (ARCHIVE_FORMAT, TEXT_FORMAT, COMPOSITE_FORMAT) and target.get('archive'):
        archive = target['archive']
        binding['archive_sha256'] = digest({k: archive[k] for k in ('url', 'md5', 'sha256', 'bytes', 'members')})
    if target.get('clinical_data'):
        binding['clinical_schema_sha256'] = digest({k:v.model_json_schema() for k,v in TASK_MODELS.items()})
    return binding


def sign_review(root, aid, sid, *, role, reviewer, notes):
    root = Path(root)
    if role not in ('agent', 'parent') or not reviewer or not notes:
        raise ValueError('Review role, reviewer and actual inspection notes required')
    from openpatients2.dense.signing_guard import check_signature
    check_signature(reviewer, notes)
    targets = [t for t in _targets(root, aid) if t.get('supplement_id') == sid]
    if len(targets) != 1:
        raise ValueError('Missing supplemental review sidecar')
    target = targets[0]
    source, annotation = _inputs(root, aid)
    report = validate_submission(target, source, annotation)
    if not report['complete']:
        raise ValueError('Unavailable, partial or missing typed clinical-data supplement cannot receive completed review receipt')
    if target.get('format') in DOCUMENT_FORMATS or target.get('clinical_data'):
        other = 'parent' if role == 'agent' else 'agent'
        other_path = store.receipt(root, 'supplement', other, aid, sid)
        if other_path.exists() and json.loads(other_path.read_text()).get('reviewer') == reviewer:
            raise ValueError('Supplement review requires independent agent and parent reviewers')
    receipt = {'role':role, 'reviewer':reviewer, **_binding(target,source,annotation),
        'reviewed_at':datetime.now(timezone.utc).isoformat(), 'inspection_notes':notes,
        'physician_adjudicated':False, **report}
    path = store.receipt(root, 'supplement', role, aid, sid); path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix('.json.tmp')
    temporary.write_text(json.dumps(receipt,ensure_ascii=False,indent=2)+'\n')
    temporary.replace(path)
    return receipt


def reviewed_supplement_targets(root, aid):
    """Return only complete sidecars with valid independent agent and parent receipts.

    Missing sidecars/receipts produce no completed entry. Present stale or invalid
    receipts raise rather than silently losing an already-published reviewed asset.
    """
    root = Path(root); targets = _targets(root, aid)
    if not targets:
        return []
    source, annotation = _inputs(root, aid); out = []
    for target in targets:
        sid = target.get('supplement_id'); _supplement(source,sid)
        paths = [store.receipt(root, 'supplement', role, aid, sid) for role in ('agent','parent')]
        if not all(p.exists() for p in paths):
            continue
        report = validate_submission(target,source,annotation,verify_files=False)
        if not report['complete']:
            raise ValueError('Incomplete supplement carries completed receipts')
        binding = _binding(target,source,annotation); receipts = []
        for role,path in zip(('agent','parent'),paths):
            receipt = json.loads(path.read_text())
            for key,value in {'role':role,**binding,**report}.items():
                if receipt.get(key) != value:
                    raise ValueError(f'{aid}/{sid}: stale supplemental {role} receipt {key}')
            if not receipt.get('reviewer') or not receipt.get('inspection_notes') or receipt.get('physician_adjudicated') is not False:
                raise ValueError('Supplement receipt lacks actual inspection metadata')
            from openpatients2.dense.signing_guard import receipt_identity_errors
            if receipt_identity_errors(receipt):
                raise ValueError(f'{aid}/{sid}: supplemental {role} receipt has a placeholder reviewer/notes')
            receipts.append(receipt)
        if (target.get('format') in DOCUMENT_FORMATS or target.get('clinical_data')) and receipts[0]['reviewer'] == receipts[1]['reviewer']:
            raise ValueError('Supplement review requires independent agent and parent reviewers')
        out.append((target,receipts))
    return out


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('root',type=Path);parser.add_argument('--article',required=True)
    parser.add_argument('--supplement',required=True);parser.add_argument('--role',choices=['agent','parent'])
    parser.add_argument('--reviewer');parser.add_argument('--notes');args=parser.parse_args()
    if args.role:
        receipt=sign_review(args.root,args.article,args.supplement,role=args.role,reviewer=args.reviewer,notes=args.notes)
        print(json.dumps({k:receipt[k] for k in ('supplement_id','complete','inspected_assets','source_assets')}))
    else:
        source,annotation=_inputs(args.root,args.article)
        candidates=[t for t in _targets(args.root,args.article) if t.get('supplement_id')==args.supplement]
        if len(candidates)!=1:raise ValueError('Missing supplemental review sidecar')
        print(json.dumps(validate_submission(candidates[0],source,annotation)))
