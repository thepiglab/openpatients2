"""One self-contained row per article: `openpatients2-unified-article/1`.

Shared by the dense500 reference export and the automated pipeline, so both
emit exactly the same record and a scorer can compare them field by field.
All joins are resolved here: patients, figure panels (pixel analysis, owner
attribution, EHR label, privacy flags), supplements (manifest class, reviewed
pages, typed facts, transcriptions) and measurement series.

The builder only re-arranges inputs that already passed their own contracts; it
never edits clinical content. Privacy flags carry categories and category
descriptions only, never identifier values. Nothing here is physician
adjudicated.
"""
from __future__ import annotations

from copy import deepcopy
import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .provenance import json_digest

FORMAT = 'openpatients2-unified-article/1'
TASKS = ('case_context', 'demographics', 'conditions', 'symptoms_function', 'medications', 'allergies',
         'procedures_devices', 'observations', 'oncology', 'family_genetics', 'reproductive_perinatal',
         'social_exposures', 'outcomes', 'care_plans')
IDENTIFIER_VALUE = re.compile(r'\d{4,}')


class _Model(BaseModel):
    model_config = ConfigDict(extra='forbid')


class Species(_Model):
    patient_species: dict[str, str]
    non_human_patient: bool | None
    article_has_non_human_patient: bool


class PanelEhr(_Model):
    status: str
    ehr_category: str | None
    ehr_includable: bool | None
    rationale: str | None
    basis: str | None
    override_reason: str | None
    default: dict | None


class PanelPrivacy(_Model):
    status: str
    burned_in_identifiers: Literal['none', 'timestamps_or_device_text', 'direct_identifiers'] | None
    burned_in_description: str | None
    sensitive_anatomy: bool | None
    sensitive_anatomy_description: str | None
    identifiable_face: bool | None
    face_description: str | None

    @model_validator(mode='after')
    def no_values(self):
        for text in (self.burned_in_description, self.sensitive_anatomy_description, self.face_description):
            if text and IDENTIFIER_VALUE.search(text):
                raise ValueError('Privacy descriptions name categories; identifier values are never copied')
        return self


class Panel(_Model):
    panel_key: str
    panel: str | None
    visual: dict
    attribution: dict
    patient_ids: list[str]
    ehr: PanelEhr
    privacy: PanelPrivacy
    species: Species


class Figure(_Model):
    figure_id: str
    label: str | None
    caption: str | None
    status: Literal['inspected', 'unavailable', 'not_reviewed']
    review_status: str
    assets: list[dict]
    general_description: str | None
    detailed_description: str | None
    figure_ehr_label: dict | None
    panels: list[Panel]
    limitations: list[str]
    # Explicit availability flags: an unobtainable figure (no declared asset) is listed, never claimed as reviewed.
    media_available: bool | None = None
    content_reviewed: bool = False


class Supplement(_Model):
    supplement_id: str
    description: str | None
    declared_assets: list[dict]
    content_inspected: bool
    review_status: str
    gate_satisfied_by: str | None
    manifest: dict
    document_review: dict | None
    image_review: dict | None
    clinical: dict | None
    patient_ids: list[str]
    # False when the source declares no fetchable asset (external link such as YouTube): listed, never reviewed.
    media_available: bool | None = None
    content_reviewed: bool = False


class Patient(_Model):
    patient_id: str
    record_id: str | None
    label: str | None
    species: str | None
    species_as_documented: str | None
    identity_evidence: list
    sections: dict
    summary: dict | None
    timeline: dict | None
    relative_timeline: dict | None
    ambiguities: list
    observation_measurements: list
    supplemental_clinical: list
    quality: dict | None
    figure_panel_refs: list[str]
    supplement_refs: list[str]
    measurement_series: list[dict]
    non_human_patient: bool | None

    @model_validator(mode='after')
    def all_sections(self):
        missing = [t for t in TASKS if t not in self.sections]
        if missing:
            raise ValueError(f'Patient {self.patient_id} lacks clinical sections {missing}')
        return self


class UnifiedArticle(_Model):
    format: Literal['openpatients2-unified-article/1']
    article_id: str
    pmcid: str | None
    pmid: str | None
    doi: str | None
    title: str
    authors: list
    article_url: str | None
    license: dict
    split: Literal['train', 'validation', 'test']
    primary_stratum: str | None
    text_sha256: str
    species: Species
    patients: list[Patient]
    figures: list[Figure]
    supplements: list[Supplement]
    measurement_series: dict
    sidecar_status: dict[str, str]
    provenance: dict
    completion: dict
    # Media that cannot be obtained ({kind: figure|supplement, id, reason}); they are not reviewed.
    unavailable_media: list[dict] = []
    physician_adjudicated: Literal[False]
    clinical_accuracy_verified: Literal[False]


def species_fields(patients, patient_ids):
    species = {p['patient_id']: p.get('species') or 'unknown' for p in patients}
    row = {pid: species.get(pid, 'unknown') for pid in patient_ids}
    values = set(row.values())
    return {'patient_species': row,
            'non_human_patient': True if 'nonhuman' in values else False if values == {'human'} else None,
            'article_has_non_human_patient': 'nonhuman' in species.values()}


def panel_key(figure_id, panel):
    return f'{figure_id}/{panel if panel is not None else "_whole"}'


def _assignments(analysis, panel):
    return [a for a in analysis['attribution']['assignments'] if a['panel'] is None or a['panel'] == panel]


def build_figures(source, visual_target, identities, *, labels=None, privacy=None, statuses=None, visual_status=None,
                  present_status='agent_and_parent_reviewed'):
    """Figures -> panels with visual analysis, attribution, EHR label and privacy flags pre-joined."""
    from .figure_ehr import derive_default
    statuses = statuses or {}
    label_rows = {(f['figure_id'], p['panel']): p for f in (labels or {}).get('figures', []) for p in f['panels']}
    figure_labels = {f['figure_id']: f.get('figure_label') for f in (labels or {}).get('figures', [])}
    flag_rows = {(f['figure_id'], p['panel']): p for f in (privacy or {}).get('figures', []) for p in f['panels']}
    rows = {f['figure_id']: f for f in (visual_target or {}).get('figures', [])}
    label_status = present_status if labels else statuses.get('figure_ehr_labels', 'not_started')
    flag_status = present_status if privacy else statuses.get('figure_privacy_flags', 'not_started')
    out = []
    for figure in source.get('figures', []):
        fid = figure.get('figure_key') or figure.get('figure_id')
        row = rows.get(fid)
        base = {'figure_id': fid, 'label': figure.get('label'), 'caption': figure.get('caption'),
                'review_status': visual_status or ('reviewed' if row else 'not_reviewed'),
                'figure_ehr_label': deepcopy(figure_labels.get(fid))}
        if not row or row.get('status') != 'inspected':
            out.append({**base, 'status': 'unavailable' if row else 'not_reviewed',
                        'media_available': False if row else None, 'content_reviewed': False,
                        'assets': [{'url': a['url'], 'sha256': a['sha256']} for a in (row or {}).get('assets', [])],
                        'general_description': None, 'detailed_description': None, 'panels': [],
                        'limitations': list((row or {}).get('limitations') or [])})
            continue
        visual = row['analysis']['visual']
        panels = []
        for p in visual['panels']:
            assignments = _assignments(row['analysis'], p['panel'])
            owners = sorted({pid for a in assignments for pid in a['patient_ids']})
            default = derive_default(p, assignments)
            label = label_rows.get((fid, p['panel']))
            flags = flag_rows.get((fid, p['panel']))
            panels.append({
                'panel_key': panel_key(fid, p['panel']), 'panel': p['panel'], 'visual': deepcopy(p),
                'attribution': {'scopes': sorted({a['scope'] for a in assignments}),
                                'subjects': sorted({a['subject'] for a in assignments}),
                                'assignments': deepcopy(assignments)},
                'patient_ids': owners,
                'ehr': {'status': label_status if label else ('panel_missing_from_labels' if labels else label_status),
                        'ehr_category': label['label']['ehr_category'] if label else None,
                        'ehr_includable': label['label']['ehr_includable'] if label else None,
                        'rationale': label['label']['rationale'] if label else None,
                        'basis': label['label']['basis'] if label else None,
                        'override_reason': label['label'].get('override_reason') if label else None,
                        'default': {k: default[k] for k in ('ehr_category', 'ehr_includable', 'rule')}},
                'privacy': {'status': flag_status if flags else ('panel_missing_from_flags' if privacy else flag_status),
                            **{k: (flags or {}).get(k) for k in ('burned_in_identifiers', 'burned_in_description',
                               'sensitive_anatomy', 'sensitive_anatomy_description', 'identifiable_face', 'face_description')}},
                'species': species_fields(identities, owners)})
        out.append({**base, 'status': 'inspected', 'media_available': True, 'content_reviewed': True, 'assets': [{'url': a['url'], 'sha256': a['sha256']} for a in row['assets']],
                    'general_description': visual.get('general_description'),
                    'detailed_description': visual.get('detailed_description'), 'panels': panels,
                    'limitations': list(visual.get('limitations') or []) + list(row.get('limitations') or [])})
    return out


def build_supplements(source, *, manifest=None, reviewed=(), statuses=None, present_status='agent_and_parent_reviewed',
                      accept_unavailable_media=True):
    """Supplements with manifest classes, reviewed pages/pixels and typed clinical payloads joined."""
    from .supplement_manifest import EXTERNAL_UNAVAILABLE, manifest_gate_supplements
    statuses = statuses or {}
    reviewed = {t['supplement_id']: (t, r) for t, r in reviewed}
    files = {}
    for f in (manifest or {}).get('files', []):
        files.setdefault(f['supplement_id'], []).append(f)
    # Gate bases from the dual-signed manifest itself (non-clinical, or sampled-frame video review).
    gate_bases = manifest_gate_supplements(manifest, accept_external_unavailable=accept_unavailable_media) if manifest else {}
    out = []
    for s in source.get('supplements', []):
        sid = s['supplement_id']
        target, receipts = reviewed.get(sid, (None, None))
        document = image = clinical = None
        owners = set()
        if target is not None:
            if 'analysis' in target:
                image = {'format': target['format'], 'assets': deepcopy(target['assets']),
                         'visual': deepcopy(target['analysis']['visual']),
                         'attribution': deepcopy(target['analysis']['attribution'])}
                owners |= {pid for a in target['analysis']['attribution']['assignments'] for pid in a['patient_ids']}
            else:
                document = {'format': target['format'], 'status': target['status'],
                            'classification': target['document_classification'],
                            'contents_summary': target['contents_summary'],
                            'archive': {k: target['archive'][k] for k in ('url', 'sha256', 'md5', 'members')} if target.get('archive') else None,
                            'assets': [{k: deepcopy(a.get(k)) for k in ('url', 'member', 'sha256', 'page_count', 'pages',
                                                                        'media_type', 'extraction', 'units', 'blocks')
                                        if k in a or k in ('url', 'member', 'sha256', 'page_count', 'pages')}
                                       for a in target['assets']]}
                owners |= {pid for a in target['assets'] for p in a.get('pages', []) + a.get('blocks', [])
                           for pid in p['patient_ids']}
            if target.get('clinical_data'):
                clinical = deepcopy(target['clinical_data'])
                owners |= {pid for f in clinical['facts'] for pid in f['patient_ids']}
        mrows = files.get(sid, [])
        owners |= {pid for f in mrows for pid in f['review']['patient_ids']}
        basis = gate_bases.get(sid)
        satisfied = ('supplement_review' if target is not None else 'non_clinical_manifest' if basis == 'manifest_non_clinical'
                     else basis)
        out.append({
            'supplement_id': sid, 'description': s.get('description'),
            'declared_assets': [{k: a.get(k) for k in ('url', 'filename', 'md5', 'kind')} for a in s.get('assets', [])],
            'content_inspected': target is not None,
            'review_status': ('agent_and_parent_reviewed' if target is not None else
                              'agent_and_parent_manifest_video_frames_sampled' if basis == 'manifest_video_frames_sampled'
                              else 'external_media_unavailable' if basis == 'manifest_external_media_unavailable'
                              else 'linked_content_not_reviewed'),
            'gate_satisfied_by': satisfied,
            'manifest': {'status': present_status if manifest else statuses.get('supplement_manifest', 'not_started'),
                         'files': [{'url': f['url'], 'filename': f['machine'].get('filename'),
                                    'detected_type': f['machine'].get('detected_type'), 'bytes': f['machine'].get('bytes'),
                                    'sha256': f['machine'].get('sha256'), 'download_status': f['machine'].get('download_status'),
                                    'inspection_status': f['machine'].get('inspection_status'),
                                    **{k: deepcopy(f['review'][k]) for k in ('content_class', 'classification_basis', 'patient_scope',
                                       'patient_ids', 'ehr_includable', 'ehr_modality', 'estimated_counts', 'limitations')}}
                                   for f in mrows]},
            'document_review': document, 'image_review': image, 'clinical': clinical,
            'patient_ids': sorted(owners),
            'media_available': (None if not mrows else
                                not all(f['machine'].get('download_status') in EXTERNAL_UNAVAILABLE for f in mrows)),
            'content_reviewed': target is not None or basis in ('manifest_non_clinical', 'manifest_video_frames_sampled')})
    return out


def unavailable_media(figures, supplements):
    """Media that cannot be obtained, listed explicitly; they are never reported as reviewed."""
    out = [{'kind': 'figure', 'id': f['figure_id'], 'reason': 'no_declared_asset', 'content_reviewed': False}
           for f in figures if f.get('media_available') is False]
    out += [{'kind': 'supplement', 'id': s['supplement_id'], 'reason': 'external_link_no_declared_asset',
             'content_reviewed': False} for s in supplements if s.get('media_available') is False]
    return out


def build_record(*, article, split, identities, patient_bundles, source, visual_target=None, visual_status=None,
                 labels=None, privacy=None, manifest=None, supplement_reviews=(), series=None,
                 statuses=None, provenance=None, completion=None, primary_stratum=None,
                 present_status='agent_and_parent_reviewed'):
    """Assemble one unified row. `identities` are roster patients (id, label, species, evidence, ambiguities)."""
    from .measurements import observation_measurements
    from .measurement_series import series_rows
    statuses = dict(statuses or {})
    figures = build_figures(source, visual_target, identities, labels=labels, privacy=privacy,
                            statuses=statuses, visual_status=visual_status, present_status=present_status)
    supplements = build_supplements(source, manifest=manifest, reviewed=supplement_reviews, statuses=statuses,
                                    present_status=present_status)
    series_list = list(series_rows(source['article_id'], series)) if series else []
    for row in series_list:
        row.pop('split', None); row.pop('format', None); row.pop('article_id', None)
    by_id = {p['patient_id']: p for p in identities}
    patients = []
    for bundle in patient_bundles:
        target = bundle['source']['patient_target']; pid = target['patient_id']
        identity = by_id.get(pid, {})
        refs = [p['panel_key'] for f in figures for p in f['panels'] if pid in p['patient_ids']]
        patients.append({
            'patient_id': pid, 'record_id': bundle['source'].get('record_id'),
            'label': target.get('label'), 'species': target.get('species'),
            'species_as_documented': target.get('species_as_documented'),
            'identity_evidence': deepcopy(target.get('identity_evidence') or []),
            'sections': deepcopy(bundle['sections']),
            'summary': deepcopy((bundle.get('companions') or {}).get('summary')),
            'timeline': deepcopy((bundle.get('companions') or {}).get('timeline_v2')),
            'relative_timeline': deepcopy(bundle.get('relative_timeline')),
            'ambiguities': deepcopy(identity.get('ambiguities') or []),
            'observation_measurements': observation_measurements(bundle['sections'].get('observations'), source.get('segments', [])),
            'supplemental_clinical': deepcopy(bundle.get('supplemental_clinical') or []),
            'quality': deepcopy(bundle.get('quality')),
            'figure_panel_refs': refs,
            'supplement_refs': [s['supplement_id'] for s in supplements if pid in s['patient_ids']],
            'measurement_series': [deepcopy(s) for s in series_list if pid in s['patient_ids']],
            'non_human_patient': species_fields(identities, [pid])['non_human_patient']})
    license_ = article.get('license') or source.get('license') or {}
    record = {
        'format': FORMAT, 'article_id': source['article_id'], 'pmcid': source.get('pmcid'), 'pmid': source.get('pmid'),
        'doi': source.get('doi'), 'title': article.get('title') or source.get('title'),
        'authors': list(article.get('authors') or source.get('authors') or []),
        'article_url': article.get('article_url') or source.get('source_url'), 'license': deepcopy(license_),
        'split': split, 'primary_stratum': primary_stratum, 'text_sha256': source['text_sha256'],
        'species': species_fields(identities, [p['patient_id'] for p in identities]),
        'patients': patients, 'figures': figures, 'supplements': supplements,
        'measurement_series': {'status': present_status if series else statuses.get('measurement_series', 'not_started'),
                               'coverage': series.get('coverage') if series else None,
                               'series': series_list,
                               'limitations': list(series.get('limitations') or []) if series else []},
        'sidecar_status': {k: str(v) for k, v in statuses.items()},
        'provenance': deepcopy(provenance or {}), 'completion': deepcopy(completion or {}),
        'unavailable_media': unavailable_media(figures, supplements),
        'physician_adjudicated': False, 'clinical_accuracy_verified': False}
    return validate_record(record)


def case_context_warnings(record):
    """Per-patient records must be clinical_case/animal with multiple_index_patients=false.

    Reported (not raised) so the release still builds while older articles are migrated.
    """
    out = []
    for p in record['patients']:
        section = p['sections'].get('case_context') or {}
        if section.get('case_kind') == 'multi_patient' or section.get('multiple_index_patients') is not False:
            out.append(f'{p["patient_id"]}: case_context_packet_rule ({section.get("case_kind")!r}, '
                       f'{section.get("multiple_index_patients")!r})')
    return out


def validate_record(record):
    """Contract plus cross-reference checks; returns the record unchanged."""
    UnifiedArticle.model_validate(record)
    warnings = case_context_warnings(record)
    if warnings:
        record['provenance'].setdefault('warnings', []).extend(w for w in warnings if w not in record['provenance'].get('warnings', []))
    keys = {p['panel_key'] for f in record['figures'] for p in f['panels']}
    if len(keys) != sum(len(f['panels']) for f in record['figures']):
        raise ValueError('Duplicate panel key')
    pids = {p['patient_id'] for p in record['patients']}
    sids = {s['supplement_id'] for s in record['supplements']}
    for patient in record['patients']:
        if set(patient['figure_panel_refs']) - keys or set(patient['supplement_refs']) - sids:
            raise ValueError('Patient references a missing panel or supplement')
    for figure in record['figures']:
        for panel in figure['panels']:
            if panel['ehr']['ehr_includable'] and panel['ehr']['ehr_category'] not in (
                    'clinical_imaging', 'waveform', 'pathology', 'clinical_photo', 'clinical_chart_of_patient_data'):
                raise ValueError('Includable panel with a non-EHR category')
            if set(panel['patient_ids']) - pids and pids:
                raise ValueError('Panel attributed to an unknown patient')
    return record


def record_digest(record):
    return json_digest(record)


def json_schema():
    schema = UnifiedArticle.model_json_schema()
    schema['$id'] = FORMAT
    schema['description'] = ('One row per article. Joins are pre-resolved; privacy descriptions never contain '
                             'identifier values; no field is physician adjudicated.')
    return schema


FIELD_DICTIONARY = '''
## Unified record fields (`openpatients2-unified-article/1`)

One JSONL row = one completed article (`data/{train,validation,test}.jsonl`, JSON Schema in `schema.json`).
All joins are pre-resolved; no other file is needed to use a row.

| Field | Meaning |
| --- | --- |
| `article_id`, `pmcid`, `pmid`, `doi`, `title`, `authors`, `article_url`, `license`, `text_sha256` | Source identity, attribution and per-article license evidence |
| `split`, `primary_stratum` | Predetermined clinical split and sampling stratum |
| `species` | `patient_species` map, `non_human_patient`, `article_has_non_human_patient` (non-human patients are included, flagged) |
| `patients[]` | `patient_id`, identity evidence, all 14 clinical `sections`, `summary`, `timeline`, `relative_timeline`, `ambiguities`, `observation_measurements`, `supplemental_clinical`, `quality` |
| `patients[].sections.*.items[]` (and oncology collections) with `scoring: "review_only"` | Reference facts quarantined by review (source conflict/ambiguity), exported with `fact_id` and `certainty`; not accepted facts, never required for recall. Items without `scoring` are accepted facts. A review_only case_context fact is in `sections.case_context.review_only_facts`. |
| `patients[].figure_panel_refs` | `panel_key`s (`figure_id/panel`, `_whole` for an unlettered figure) of panels attributed to this patient |
| `patients[].supplement_refs`, `patients[].measurement_series` | Supplements linked to this patient; this patient's dual-reviewed numeric series inline with cell provenance |
| `figures[]` | `figure_id`, label, caption, `status`, asset URL/SHA256, whole-figure descriptions, `figure_ehr_label`; `media_available` (false: the source declares no asset) and `content_reviewed` (true only for dual-reviewed pixels) |
| `figures[].panels[].visual` | Pixel visual analysis (descriptions, image kind, modalities, charts/readings, body parts, caption claims) |
| `figures[].panels[].attribution`, `.patient_ids` | Panel ownership scopes/subjects/assignments with literal evidence |
| `figures[].panels[].ehr` | `ehr_category` (clinical_imaging, waveform, pathology, clinical_photo, clinical_chart_of_patient_data, non_clinical_diagnostic_aid, diagram_or_schematic, literature_or_aggregate, other), `ehr_includable`, `rationale`, `basis`, `override_reason`, deterministic `default`, `status` |
| `figures[].panels[].privacy` | `burned_in_identifiers` (none / timestamps_or_device_text / direct_identifiers), `sensitive_anatomy`, `identifiable_face`, each with a category description, and `status`. Flags never change includability. |
| `supplements[]` | Declared assets; `manifest` (per-file content_class, patient scope, ehr_includable, modality, counts); `document_review` pages (PDF or ZIP member PDFs); `image_review`; `clinical` typed facts + literal transcriptions; `gate_satisfied_by`; `media_available` (false: external link with no declared PMC asset) and `content_reviewed` |
| `measurement_series` | All series (`cells` with segment/table/supplement provenance, `points` with deterministic parses, no unit conversion) and status |
| `sidecar_status` | Review state of each EHR sidecar (`agent_and_parent_reviewed`, `unsigned`, `not_started`, `no_source_*`, ...) |
| `provenance` | Annotation review, annotator model, every agent/parent receipt (role, reviewer, time, status) |
| `completion` | Completion checks behind this row (all true for released rows) |
| `unavailable_media` | Media that cannot be obtained (`kind` figure/supplement, `id`, `reason`, `content_reviewed: false`): the article is complete, flagged; these media were never reviewed |
| `physician_adjudicated`, `clinical_accuracy_verified` | Always `false`: AI reference annotations |

When a sidecar is not dual-signed, its fields are present with null values and an explicit `status`.
**Identifier values are never copied**: privacy descriptions name the category of visible text
(for example "patient name and date of birth in the scanner header"), and descriptions containing
runs of four or more digits are rejected.
'''
