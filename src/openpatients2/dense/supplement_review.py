"""Bind supplemental image or PDF-page review separately from main figures.

Signing is permitted only after every source-listed asset has actually been viewed.
Validation checks provenance and schema structure; it cannot attest pixel accuracy.
The public reader returns [(sidecar, [agent_receipt, parent_receipt]), ...]. Cache
files are checked at signing, but may subsequently be evicted from the bounded cache.
"""
import argparse
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import subprocess
from typing import Literal
from typing import get_args

from pydantic import BaseModel, ConfigDict, Field

from openpatients2.document_text import TEXT_DOCUMENT_TYPES
from openpatients2.figure_visuals import validate_joint
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
DOCUMENT_FORMATS = (DOCUMENT_FORMAT, ARCHIVE_FORMAT, TEXT_FORMAT)


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


def _clinical_requirements(target):
    """Recognize clinical documents/tables without changing administrative behavior."""
    if target.get('format') == TEXT_FORMAT:
        return {(a['url'], (b['first_unit'], b['last_unit']), None) for a in target['assets'] for b in a['blocks']
                if b['clinical_relevance'] in ('clinical_data', 'mixed')}
    if target.get('format') in DOCUMENT_FORMATS:
        return {(a['url'], p['page'], None) for a in target['assets'] for p in a['pages']
                if p['clinical_relevance'] in ('clinical_data', 'mixed')}
    panels = target.get('analysis', {}).get('visual', {}).get('panels', [])
    # The joint image analysis may combine panels from different source assets;
    # require each data-bearing panel, not every panel in every asset.
    required = {(None, None, p['panel']) for p in panels
                if p.get('image_kind') == 'table' or
                any(c.get('is_clinical_chart') and c.get('readings') for c in p.get('charts', []))}
    if target.get('clinical_relevance') in ('clinical_data', 'mixed') and not required:
        required = {(None, None, p['panel']) for p in panels}
    return required


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


def validate_clinical_data(target, source, annotation):
    """Validate typed rows against reviewed transcriptions and production schemas.

    Coverage means explicit authored transcription-to-fact coverage, never measured
    semantic exhaustiveness or automatic proof that pixels were read correctly.
    """
    required = _clinical_requirements(target)
    payload = target.get('clinical_data')
    if payload is None:
        return not required
    data = SupplementClinicalData.model_validate(payload)
    if data.coverage == 'limited' and not data.limitations:
        raise ValueError('Limited supplemental clinical extraction needs limitations')
    assets = {a['url']: a for a in target['assets']}
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
        if target['format'] == TEXT_FORMAT:
            asset = assets[t.asset_url]
            units = {u['unit']: u for u in asset['units']}
            if t.page not in units or t.panel is not None:
                raise ValueError('Text-document transcription cites an extracted unit number in `page`, never a panel')
            if t.page in asset['extraction']['nonliteral_units'] or t.text not in units[t.page]['text']:
                raise ValueError('Text-document transcription must be literal in its extracted unit')
        elif target['format'] in DOCUMENT_FORMATS:
            pages = {p['page']: p for p in assets[t.asset_url]['pages']}
            if t.page not in pages or t.panel is not None:
                raise ValueError('PDF transcription requires an inspected page, not an image panel')
        else:
            panels = {p['panel'] for p in target['analysis']['visual']['panels']}
            if t.page is not None or t.panel not in panels or not t.visual_cue:
                raise ValueError('Image transcription requires an inspected panel and visual cue')
        if t.kind == 'clinical_data':
            clinical_locations.add((t.asset_url, t.page, t.panel))
    def located(url, page, panel):
        if isinstance(page, tuple):          # text-document block (first_unit, last_unit)
            return any(url == u and page[0] <= p <= page[1] for u, p, _ in clinical_locations)
        return any((url is None or url == actual_url) and page == actual_page and panel == actual_panel
                   for actual_url,actual_page,actual_panel in clinical_locations)
    if not all(located(url, page, panel) for url,page,panel in required):
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
            if target['format'] == TEXT_FORMAT:
                block = next(b for b in assets[t.asset_url]['blocks'] if b['first_unit'] <= t.page <= b['last_unit'])
                if set(fact.patient_ids) - set(block['patient_ids']):
                    raise ValueError('Typed fact ownership exceeds inspected text-block attribution')
            elif target['format'] in DOCUMENT_FORMATS:
                page = next(p for p in assets[t.asset_url]['pages'] if p['page'] == t.page)
                if set(fact.patient_ids) - set(page['patient_ids']):
                    raise ValueError('Typed fact ownership exceeds inspected PDF-page attribution')
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
                if target.get('format') == ARCHIVE_FORMAT else ())} for a in _bundle_assets(target)],
        schema_fingerprints={k:digest(v.model_json_schema()) for k,v in TASK_MODELS.items()},
        patients=patients, unassigned_or_review_only_facts=[{**f.model_dump(),
            'value_as_extracted':_clinical_item(f,sid)} for f in data.facts
            if f.ownership == 'unresolved' or f.scoring == 'review_only'],
        completeness_is_measured=False, main_article_sections_unchanged=True)


class DocumentRightsException(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    notice: str = Field(min_length=1)
    interpretation: str = Field(min_length=1)


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
    if target.get('format') == TEXT_FORMAT and not target.get('archive'):
        return target['assets']
    if target.get('format') not in (ARCHIVE_FORMAT, TEXT_FORMAT):
        return target['assets']
    archive = target['archive']
    return [{**a, 'archive_url': archive['url'], 'archive_sha256': archive['sha256'], 'md5': None,
             'filename': a['member'], 'original_manifest_url': archive.get('original_manifest_url'),
             'source_license': archive['source_license']} for a in target['assets']]


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


TEXT_EXTENSIONS = ('.docx', '.doc', '.rtf', '.odt')


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
    format: Literal['docx', 'doc', 'rtf', 'odt']
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
    """DOCX/DOC/RTF/ODT supplements (or such members of one declared ZIP) reviewed as extracted literal units."""
    document = TextDocumentTarget.model_validate(target)
    if document.article_id != source['article_id'] or document.source_text_sha256 != source['text_sha256']:
        raise ValueError('Supplement target has stale/wrong article source')
    if annotation.get('article_id') != source['article_id'] or annotation.get('text_sha256') != source['text_sha256']:
        raise ValueError('Wrong/stale patient annotation')
    supplement = _supplement(source, document.supplement_id)
    declared = supplement.get('assets', [])
    archive = document.archive
    if archive is not None:
        if len(declared) != 1 or not str(declared[0].get('filename') or declared[0].get('url', '').split('?')[0]).lower().endswith('.zip'):
            raise ValueError('A text-document archive review needs one source-listed ZIP asset')
        original = declared[0]
        if archive.url != original.get('url'):
            raise ValueError('Archive URL must be the source-listed ZIP')
        for key in ('md5', 'filename', 'original_manifest_url'):
            if getattr(archive, key) != original.get(key):
                raise ValueError(f'Archive changed original source {key}')
        if archive.source_license != source.get('license'):
            raise ValueError('Archive source license must be preserved')
        expected = {member_url(archive.url, m): m for m in archive.members}
        if verify_files:
            data = Path(archive.path).read_bytes()
            if hashlib.sha256(data).hexdigest() != archive.sha256 or len(data) != archive.bytes:
                raise ValueError('Archive bytes changed after inspection')
            if archive.md5 and hashlib.md5(data).hexdigest() != archive.md5:
                raise ValueError('Archive source MD5 mismatch')
            if zip_text_members(archive.path) != (archive.members, archive.other_members):
                raise ValueError('Archive member listing changed')
    else:
        urls = [a.get('url') for a in declared]
        if not urls or any(not u for u in urls) or len(urls) != len(set(urls)):
            raise ValueError('Supplement requires unique source-listed asset URLs')
        if any(_text_format(a.get('filename') or a['url']) is None for a in declared):
            raise ValueError('Text-document review supports only source-listed DOCX/DOC/RTF/ODT assets (or one ZIP of them)')
        expected = {u: None for u in urls}
    actual = [a.url for a in document.assets]
    if len(actual) != len(set(actual)) or set(actual) - set(expected):
        raise ValueError('Duplicate or undeclared text-document asset')
    from openpatients2.document_text import TEXT_DOCUMENT_TYPES as types, extract_units, units_digest
    for asset in document.assets:
        if asset.member != expected[asset.url]:
            raise ValueError('Text-document member must be cited by its archive pseudo-URL')
        fmt = _text_format(asset.member if archive is not None else (asset.filename or asset.url))
        if asset.extraction.format != fmt or asset.media_type != types[fmt]:
            raise ValueError('Text-document format/media type disagree with the source filename')
        units = [u.model_dump() for u in asset.units]
        if [u['unit'] for u in units] != list(range(1, len(units) + 1)) or asset.extraction.unit_count != len(units):
            raise ValueError('Extracted units must be numbered 1..n in document order')
        if asset.extraction.units_sha256 != units_digest(units):
            raise ValueError('Extracted units were edited after extraction (units_sha256 mismatch)')
        if archive is None:
            original = next(a for a in declared if a['url'] == asset.url)
            for key in ('md5', 'filename', 'original_manifest_url'):
                if getattr(asset, key) != original.get(key):
                    raise ValueError(f'Asset changed original source {key}')
            if asset.source_license != source.get('license'):
                raise ValueError('Asset source license must be preserved')
        if verify_files:
            data = Path(asset.path).read_bytes()
            if hashlib.sha256(data).hexdigest() != asset.sha256 or len(data) != asset.bytes:
                raise ValueError('Text-document bytes changed after inspection')
            if archive is not None:
                if zip_member_bytes(archive.path, asset.member) != data:
                    raise ValueError('Archive member bytes changed after inspection')
            elif asset.md5 and hashlib.md5(data).hexdigest() != asset.md5:
                raise ValueError('Text-document source MD5 mismatch')
            fresh = extract_units(asset.path, fmt)
            if fresh['units'] != units or fresh['tool'] != asset.extraction.tool or \
                    fresh['nonliteral_units'] != asset.extraction.nonliteral_units:
                raise ValueError('Re-extraction differs from the reviewed units (different file or extractor version)')
    report = dict(supplement_id=document.supplement_id, complete=False, inspected_assets=0,
                  source_assets=len(expected), inspected_units=0, document_accuracy_measured=False)
    if document.status in ('partial', 'unavailable'):
        if not document.limitations:
            raise ValueError('Incomplete supplement requires limitations')
        return report
    if set(actual) != set(expected) or not expected:
        raise ValueError('Complete inspection requires every declared document (every document member of the ZIP)')
    if archive is not None and archive.other_members:
        raise ValueError('Complete archive review requires word-processor-only members; record others as partial')
    patients = {p['patient_id'] for p in annotation['patients']}
    relevance = []
    for asset in document.assets:
        covered = []
        for block in asset.blocks:
            if block.last_unit < block.first_unit:
                raise ValueError('Text block must run from first_unit to last_unit')
            covered += list(range(block.first_unit, block.last_unit + 1))
            if len(block.patient_ids) != len(set(block.patient_ids)) or set(block.patient_ids) - patients:
                raise ValueError('Unknown or duplicate document patient identity')
            if (block.rights_basis == 'page_exception') != bool(block.rights_exceptions):
                raise ValueError('Rights exceptions and rights basis disagree')
            relevance.append(block.clinical_relevance)
        if covered != list(range(1, len(asset.units) + 1)):
            raise ValueError('Every extracted unit requires exactly one reviewed block, in order')
    if document.document_classification == 'administrative_checklist' and set(relevance) != {'administrative'}:
        raise ValueError('Administrative checklist cannot conceal clinical-data units')
    if document.document_classification == 'clinical_data' and not set(relevance) & {'clinical_data', 'mixed'}:
        raise ValueError('Clinical-data classification requires clinical-data units')
    report.update(complete=validate_clinical_data(target, source, annotation), inspected_assets=len(document.assets),
                  inspected_units=sum(len(a.units) for a in document.assets))
    return report


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
        status=('source_and_document_text_reviewed' if target.get('format') == TEXT_FORMAT else
                'source_and_document_pages_reviewed' if target.get('format') in DOCUMENT_FORMATS
                else 'source_and_pixels_reviewed'), source_text_sha256=source['text_sha256'],
        source_supplement_sha256=digest(supplement), target_sha256=digest(target),
        asset_sha256=digest([{'url':a['url'],'sha256':a['sha256']} for a in sorted(target['assets'],key=lambda a:a['url'])]),
        patient_identity_sha256=identity_digest(annotation),
        patient_ids=sorted(p['patient_id'] for p in annotation['patients']))
    if target.get('format') in (ARCHIVE_FORMAT, TEXT_FORMAT) and target.get('archive'):
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
