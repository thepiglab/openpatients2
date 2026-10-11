"""Per-article supplement content manifest (`dense-supplement-manifest/1`).

One row per declared supplement file. A machine block is copied verbatim from
the bounded acquisition record (hashes, sizes, detected type, literal peek) and
cannot be edited. The review block is a reviewer's content classification,
patient scope and EHR-usefulness assessment. The manifest never carries clinical
values: typed clinical facts belong in `dense-supplemental-clinical-reference/1`
and numeric series in `dense-measurement-series/1`.
"""
from __future__ import annotations

from copy import deepcopy
import re
from typing import Literal

from pydantic import Field, model_validator

from .provenance import json_digest
from .schemas import StrictModel
from .supplement_peek import peek_text

FORMAT = 'dense-supplement-manifest/1'

ContentClass = Literal[
    'reporting_checklist', 'literature_table', 'clinical_case_table', 'lab_results', 'vital_signs_series',
    'medication_record', 'imaging_dicom', 'imaging_still', 'video', 'waveform_signal', 'genomic_data',
    'questionnaire_instrument', 'protocol_methods', 'raw_dataset_tabular', 'code', 'other', 'unknown']
EhrModality = Literal['structured_table', 'document_text', 'dicom_imaging', 'still_image', 'video',
                      'waveform', 'genomic_sequence', 'archive_mixed', 'none', 'unknown']
# Content that is never part of a patient's record, whoever it mentions.
NON_EHR_CLASSES = {'reporting_checklist', 'literature_table', 'protocol_methods', 'code'}
CLASS_MODALITY = {'imaging_dicom': {'dicom_imaging', 'archive_mixed'}, 'imaging_still': {'still_image', 'archive_mixed'},
                  'video': {'video', 'archive_mixed'}, 'waveform_signal': {'waveform', 'still_image', 'structured_table', 'archive_mixed'},
                  'genomic_data': {'genomic_sequence', 'structured_table', 'archive_mixed'},
                  'lab_results': {'structured_table', 'document_text', 'still_image', 'archive_mixed'},
                  'vital_signs_series': {'structured_table', 'document_text', 'still_image', 'waveform', 'archive_mixed'}}
MACHINE_KEYS = ('filename', 'original_manifest_url', 'declared_md5', 'declared_kind', 'extension',
                'http_content_type', 'bytes', 'download_status', 'download_reason', 'sha256', 'md5_verified',
                'sample_sha256', 'sample_bytes', 'tail_sha256', 'detected_type', 'inspection_status', 'peek',
                'limitations', 'errors')


class OwnershipEvidence(StrictModel):
    basis: Literal['article', 'supplement_peek']
    segment_id: str | None
    quote: str = Field(min_length=1, max_length=1200)


class EstimatedCounts(StrictModel):
    rows: int | None = Field(ge=0)
    frames: int | None = Field(ge=0)
    pages: int | None = Field(ge=0)
    members: int | None = Field(ge=0)
    duration_seconds: float | None = Field(ge=0)
    basis: str | None


class FileReview(StrictModel):
    content_class: ContentClass
    content_class_rationale: str = Field(min_length=1)
    classification_basis: Literal['full_content', 'peek_content', 'caption_and_filename_only']
    patient_scope: Literal['patient_level', 'aggregate', 'background', 'administrative', 'unresolved']
    patient_ids: list[str]
    ownership_evidence: list[OwnershipEvidence]
    ehr_includable: bool
    ehr_modality: EhrModality
    estimated_counts: EstimatedCounts
    notes: str
    limitations: list[str]

    @model_validator(mode='after')
    def coherent(self):
        if len(self.patient_ids) != len(set(self.patient_ids)):
            raise ValueError('Duplicate supplement patient IDs')
        if self.patient_scope == 'patient_level':
            if not self.patient_ids or not self.ownership_evidence:
                raise ValueError('Patient-level supplement needs patient IDs and literal ownership evidence')
        elif self.patient_ids:
            raise ValueError('Only patient-level supplements may name patients')
        if self.content_class == 'unknown' and (self.ehr_includable or not self.limitations):
            raise ValueError('Unknown content cannot be EHR-includable and needs limitations')
        if self.content_class in NON_EHR_CLASSES and self.ehr_includable:
            raise ValueError(f'{self.content_class} is never EHR-includable')
        if self.ehr_includable and (self.patient_scope != 'patient_level' or
                                    self.classification_basis == 'caption_and_filename_only' or
                                    self.ehr_modality in ('none', 'unknown')):
            raise ValueError('EHR-includable content needs patient-level scope, inspected content and a modality')
        allowed = CLASS_MODALITY.get(self.content_class)
        if allowed and self.ehr_modality not in allowed | {'unknown'}:
            raise ValueError('Content class and EHR modality disagree')
        if self.classification_basis == 'caption_and_filename_only' and not self.limitations:
            raise ValueError('Caption/filename-only classification needs limitations')
        return self


class ManifestRow(StrictModel):
    supplement_id: str
    url: str | None
    machine: dict
    machine_suggestion: dict
    review: FileReview


class SupplementManifest(StrictModel):
    format: Literal['dense-supplement-manifest/1']
    article_id: str
    source_text_sha256: str
    source_supplements_sha256: str
    files: list[ManifestRow]
    limitations: list[str]


def machine_block(row):
    return {k: deepcopy(row.get(k)) for k in MACHINE_KEYS}


def suggest_class(machine, supplement):
    """Deterministic hint for reviewers; never a final label."""
    text = (peek_text(machine.get('peek')) + '\n' + (supplement.get('description') or '')).casefold()
    detected = machine.get('detected_type') or ''
    peek = machine.get('peek') or {}
    if machine.get('download_status') in ('no_declared_asset', 'rejected_host'):
        return {'content_class': 'unknown', 'rule': 'no_downloadable_asset'}
    if any(k in text for k in ('care checklist', 'care case report', 'strobe', 'prisma', 'consort', 'checklist item')):
        return {'content_class': 'reporting_checklist', 'rule': 'checklist_keyword'}
    if detected == 'dicom' or (peek.get('kind') == 'archive' and (peek.get('dicomdir_present') or '.dcm' in peek.get('member_extensions', {}))):
        return {'content_class': 'imaging_dicom', 'rule': 'dicom_magic_or_members'}
    if detected == 'video':
        return {'content_class': 'video', 'rule': 'video_container'}
    if detected.startswith('image_'):
        return {'content_class': 'imaging_still', 'rule': 'image_magic'}
    if detected == 'edf_signal':
        return {'content_class': 'waveform_signal', 'rule': 'edf_header'}
    if (machine.get('extension') or '') in ('.vcf', '.fasta', '.fa', '.fastq', '.bam'):
        return {'content_class': 'genomic_data', 'rule': 'genomic_extension'}
    if (machine.get('extension') or '') in ('.py', '.r', '.m', '.ipynb'):
        return {'content_class': 'code', 'rule': 'code_extension'}
    if detected in ('csv', 'tsv', 'xlsx', 'xls'):
        return {'content_class': 'raw_dataset_tabular', 'rule': 'tabular_container'}
    return {'content_class': 'unknown', 'rule': 'no_rule'}


def expected_rows(source, acquisition):
    """Map (supplement_id, url) -> acquisition row; one per declared file."""
    rows = {}
    for row in acquisition['files']:
        key = (row['supplement_id'], row.get('url'))
        if key in rows:
            raise ValueError('Duplicate acquisition row')
        rows[key] = row
    declared = set()
    for supplement in source.get('supplements', []):
        assets = supplement.get('assets') or []
        for asset in assets or [{'url': None}]:
            declared.add((supplement['supplement_id'], asset.get('url')))
    if set(rows) != declared:
        raise ValueError('Acquisition record does not cover exactly the declared supplement files')
    return rows


def draft_manifest(source, acquisition):
    rows = expected_rows(source, acquisition)
    supplements = {s['supplement_id']: s for s in source.get('supplements', [])}
    files = []
    for (sid, url), row in rows.items():
        machine = machine_block(row)
        files.append({'supplement_id': sid, 'url': url, 'machine': machine,
                      'machine_suggestion': suggest_class(machine, supplements[sid]),
                      'review': {'content_class': None, 'content_class_rationale': None,
                                 'classification_basis': None, 'patient_scope': None, 'patient_ids': [],
                                 'ownership_evidence': [], 'ehr_includable': None, 'ehr_modality': None,
                                 'estimated_counts': {'rows': None, 'frames': None, 'pages': None, 'members': None,
                                                      'duration_seconds': None, 'basis': None},
                                 'notes': '', 'limitations': []}})
    return {'format': FORMAT, 'article_id': source['article_id'], 'source_text_sha256': source['text_sha256'],
            'source_supplements_sha256': json_digest(source.get('supplements', [])), 'files': files,
            'limitations': ['Machine draft: every review block must be completed by a reviewer who inspected the content.']}


def _machine_counts(machine):
    peek = machine.get('peek') or {}
    pages = peek.get('page_count')
    rows = peek.get('row_count')
    members = peek.get('member_count')
    duration = peek.get('duration_seconds')
    frames = peek.get('frame_count')
    if peek.get('kind') == 'dicom':
        value = (peek.get('tags') or {}).get('NumberOfFrames')
        frames = int(value) if isinstance(value, str) and value.isdigit() else frames
    return {'rows': rows, 'frames': frames, 'pages': pages, 'members': members, 'duration_seconds': duration}


def validate_manifest(target, source, annotation, acquisition):
    """Structural/provenance validation; it cannot attest that content was read."""
    manifest = SupplementManifest.model_validate(target)
    if manifest.article_id != source['article_id'] or manifest.source_text_sha256 != source['text_sha256']:
        raise ValueError('Supplement manifest has stale/wrong article source')
    if annotation.get('article_id') != source['article_id'] or annotation.get('text_sha256') != source['text_sha256']:
        raise ValueError('Wrong/stale patient annotation')
    if acquisition.get('article_id') != source['article_id'] or acquisition.get('source_text_sha256') != source['text_sha256']:
        raise ValueError('Stale supplement acquisition record')
    if manifest.source_supplements_sha256 != json_digest(source.get('supplements', [])):
        raise ValueError('Source supplement metadata changed after manifest')
    expected = expected_rows(source, acquisition)
    keys = [(r.supplement_id, r.url) for r in manifest.files]
    if len(keys) != len(set(keys)) or set(keys) != set(expected):
        raise ValueError('Manifest needs exactly one row per declared supplement file')
    supplements = {s['supplement_id']: s for s in source.get('supplements', [])}
    patients = {p['patient_id'] for p in annotation['patients']}
    segments = {s['segment_id']: s['text'] for s in source.get('segments', [])}
    counts = {}
    for row in manifest.files:
        acq = expected[(row.supplement_id, row.url)]
        if row.machine != machine_block(acq):
            raise ValueError(f'{row.supplement_id}: machine block differs from acquisition record (hand edits are not allowed)')
        if row.machine_suggestion != suggest_class(row.machine, supplements[row.supplement_id]):
            raise ValueError(f'{row.supplement_id}: machine suggestion was edited')
        review = row.review
        status = row.machine['inspection_status']
        if review.classification_basis == 'full_content' and (row.machine['download_status'] != 'downloaded' or status != 'peeked'):
            raise ValueError('Full-content classification requires a fully downloaded, peekable file')
        if review.classification_basis == 'peek_content' and status not in ('peeked', 'peeked_head_sample'):
            raise ValueError('Peek-based classification requires a machine peek')
        if set(review.patient_ids) - patients:
            raise ValueError('Unknown supplement patient identity')
        text = peek_text(row.machine.get('peek'))
        for evidence in review.ownership_evidence:
            if evidence.basis == 'article':
                if evidence.quote not in segments.get(evidence.segment_id, ''):
                    raise ValueError('Article ownership evidence must be literal')
            elif evidence.segment_id is not None or evidence.quote not in text:
                raise ValueError('Supplement ownership evidence must be literal in the machine peek')
        machine_counts = _machine_counts(row.machine)
        supplied = review.estimated_counts.model_dump()
        for key, known in machine_counts.items():
            if known is not None and supplied[key] is not None and supplied[key] != known:
                raise ValueError(f'Estimated {key} contradicts the machine-read value {known}')
        extra = [k for k in machine_counts if supplied[k] is not None and machine_counts[k] is None]
        if extra and (not review.estimated_counts.basis or review.classification_basis == 'caption_and_filename_only'):
            raise ValueError('Reviewer count estimates need inspected content and a stated basis')
        counts[review.content_class] = counts.get(review.content_class, 0) + 1
    return {'files': len(manifest.files), 'source_supplements': len(supplements),
            'ehr_includable_files': sum(r.review.ehr_includable for r in manifest.files),
            'peeked_files': sum(r.machine['inspection_status'] in ('peeked', 'peeked_head_sample') for r in manifest.files),
            'unsupported_or_unavailable_files': sum(r.machine['inspection_status'] in ('unsupported_type', 'not_inspectable', 'metadata_only') for r in manifest.files),
            'content_classes': dict(sorted(counts.items())), 'content_accuracy_measured': False}


def contract_sha256():
    return json_digest(SupplementManifest.model_json_schema())


# Classes whose dual-signed, fully inspected manifest rows satisfy the legacy
# supplement gate. Clinical/patient-data classes still need openpatients2.dense.supplement_review.
NON_CLINICAL_GATE_CLASSES = frozenset({'reporting_checklist', 'literature_table', 'protocol_methods', 'code'})


def non_clinical_supplements(manifest):
    """Supplement IDs whose every file is a fully inspected, non-patient, non-clinical class."""
    rows = {}
    for row in manifest.get('files', []):
        rows.setdefault(row['supplement_id'], []).append(row)
    return {sid for sid, files in rows.items() if all(
        f['review']['content_class'] in NON_CLINICAL_GATE_CLASSES and f['review']['classification_basis'] == 'full_content'
        and f['machine'].get('download_status') == 'downloaded' and f['review']['patient_scope'] != 'patient_level'
        and not f['review']['patient_ids'] and f['review']['ehr_includable'] is False for f in files)}


# Video supplements (MP4 etc.) have no page/pixel sidecar in openpatients2.dense.supplement_review: the dual-signed
# manifest row IS their content review. A row satisfies the supplement gate when the file was
# downloaded and machine-probed as a video (ffprobe duration/frame count), the reviewers classified
# it from the content itself (classification_basis=full_content: frames were sampled and viewed),
# a duration or frame count is recorded, and a limitation states the sampling scope (which frames
# or interval were viewed; audio/continuous motion not reviewed). See SUPPLEMENT_DOCUMENT_REVIEW.md.
VIDEO_SAMPLING_CUE = re.compile(r'\b(sampl\w*|frames?|contact[- ]sheets?|every \d+(?:\.\d+)?\s*s(?:ec\w*)?)\b', re.I)
# Machine download states meaning the source declares no fetchable PMC asset (external link such as
# YouTube, or a non-PMC host refused by policy). Nothing was viewed; never a content review.
EXTERNAL_UNAVAILABLE = frozenset({'no_declared_asset', 'rejected_host'})
GATE_BASES = ('manifest_non_clinical', 'manifest_video_frames_sampled', 'manifest_external_media_unavailable')


def _row_basis(f, *, accept_external_unavailable=False):
    review, machine = f['review'], f['machine']
    status = machine.get('download_status')
    if (review['content_class'] in NON_CLINICAL_GATE_CLASSES and review['classification_basis'] == 'full_content'
            and status == 'downloaded' and review['patient_scope'] != 'patient_level'
            and not review['patient_ids'] and review['ehr_includable'] is False):
        return 'manifest_non_clinical'
    counts = review.get('estimated_counts') or {}
    if (review['content_class'] == 'video' and review['classification_basis'] == 'full_content'
            and status == 'downloaded' and machine.get('detected_type') == 'video'
            and machine.get('inspection_status') == 'peeked'
            and (counts.get('duration_seconds') is not None or counts.get('frames') is not None)
            and any(VIDEO_SAMPLING_CUE.search(x) for x in review.get('limitations') or [])):
        return 'manifest_video_frames_sampled'
    if (accept_external_unavailable and status in EXTERNAL_UNAVAILABLE
            and review['classification_basis'] == 'caption_and_filename_only' and review['ehr_includable'] is False
            and review.get('limitations')):
        return 'manifest_external_media_unavailable'
    return None


def manifest_gate_supplements(manifest, *, accept_external_unavailable=False):
    """{supplement_id: basis} for supplements whose dual-signed manifest rows satisfy the supplement gate.

    basis is 'manifest_non_clinical' (fully inspected non-patient checklist/literature/protocol/code),
    'manifest_video_frames_sampled' (downloaded, probed video whose sampled frames were viewed), or, only
    when the caller's policy accepts it, 'manifest_external_media_unavailable' (no declared PMC asset; nothing
    viewed, limitations recorded). A supplement with several files needs every file to qualify; mixed bases
    are joined with '+'. Patient-data documents/images/tables still need openpatients2.dense.supplement_review.
    """
    rows = {}
    for row in (manifest or {}).get('files', []):
        rows.setdefault(row['supplement_id'], []).append(row)
    out = {}
    for sid, files in rows.items():
        bases = [_row_basis(f, accept_external_unavailable=accept_external_unavailable) for f in files]
        if all(bases):
            out[sid] = '+'.join(b for b in GATE_BASES if b in bases)
    return out


def external_unavailable_supplements(manifest):
    """Supplement IDs with at least one file that has no declared/fetchable PMC asset (never viewed)."""
    return {f['supplement_id'] for f in (manifest or {}).get('files', [])
            if f['machine'].get('download_status') in EXTERNAL_UNAVAILABLE}
