"""Per-panel privacy/sensitivity flags for reviewed figures (`dense-figure-privacy-flags/1`).

Policy: flag only. Burned-in identifiers, sensitive anatomy and identifiable faces
are recorded, but they never change `ehr_includable`. This is a separate sidecar
bound to the visual submission only, so existing figure-ehr-labels receipts stay
valid. Descriptions name the CATEGORY of visible text ("patient name, ID and scan
date in header"); they must never transcribe the identifier values themselves.
"""
from __future__ import annotations

import re
from typing import Literal

from pydantic import Field, model_validator

from .provenance import json_digest
from .schemas import StrictModel

FORMAT = 'dense-figure-privacy-flags/1'
DRAFT_MARK = 'DRAFT:'
# Findings reported by reviewers outside the sidecars; used only to pre-fill drafts.
KNOWN_REPORTS = {
    ('PMC12689157.1', 'F1', 'A'): ('direct_identifiers', 'Patient name, ID, date of birth and scan date/time burned into the image header'),
    ('PMC12689157.1', 'F1', 'C'): ('direct_identifiers', 'Patient name and ID, operator name and DICOM UID burned into the image header'),
    ('PMC12880940.1', 'f4-jchim-15-06-034', None): ('direct_identifiers', 'ECG header with a struck-through patient ID number'),
}
DIRECT = re.compile(r'\b(names?|MRN|DOB|birth|operator|UID|accession|hospital number|initials)\b|(?<!device )(?<!scanner )\bIDs?\b', re.I)
BURNED = re.compile(r'burned|identifier|time-?stamp|\bheader\b|printed on|\bdate\b|\d{1,2}:\d{2}:\d{2}', re.I)
TRIVIAL = re.compile(r'\bonly\b.{0,40}\b(panel letters?|annotations?|laterality|orientation|scale|arrows?|labels?)|author[- ]added|author annotation', re.I)
TIMEISH = re.compile(r'\b(dates?|times?|timestamps?|time-stamp|date/time|datetime|clock|header|vendor|device|hospital|scanner)\b|\d{1,2}:\d{2}', re.I)
NEGATED = re.compile(r'\b(no|without|not|none)\b', re.I)
FACE = re.compile(r'\b(face|facial)\b(?!-cropped)', re.I)
SENSITIVE = re.compile(r'\b(genital\w*|scrot\w*|penis|penile|vulv\w*|vagin\w*|labia\w*|perine\w*|anus|anal|perianal|buttocks?|gluteal|breasts?|nipples?)\b', re.I)
PHOTO_KINDS = {'clinical_photograph', 'operative_photograph', 'specimen_photograph', 'mixed', 'other'}


class PanelFlags(StrictModel):
    panel: str | None
    burned_in_identifiers: Literal['none', 'timestamps_or_device_text', 'direct_identifiers']
    burned_in_description: str | None
    sensitive_anatomy: bool
    sensitive_anatomy_description: str | None
    identifiable_face: bool
    face_description: str | None
    basis: Literal['pixels_reviewed']
    notes: str
    prefill: dict | None

    @model_validator(mode='after')
    def described(self):
        for flag, text, name in ((self.burned_in_identifiers != 'none', self.burned_in_description, 'burned_in'),
                                 (self.sensitive_anatomy, self.sensitive_anatomy_description, 'sensitive_anatomy'),
                                 (self.identifiable_face, self.face_description, 'face')):
            if flag and not (text or '').strip():
                raise ValueError(f'{name} flag needs a short description of what is visible')
            if not flag and text is not None:
                raise ValueError(f'{name} description given without the flag')
            if text and (text.startswith(DRAFT_MARK) or re.search(r'\d{4,}', text)):
                raise ValueError(f'{name} description must be reviewed and must not transcribe identifier values')
        if self.notes.startswith(DRAFT_MARK):
            raise ValueError('Replace DRAFT notes after viewing the pixels')
        return self


class FigureFlags(StrictModel):
    figure_id: str
    status: Literal['flagged', 'unavailable']
    panels: list[PanelFlags]
    limitations: list[str]


class FigurePrivacyFlags(StrictModel):
    format: Literal['dense-figure-privacy-flags/1']
    article_id: str
    source_text_sha256: str
    visual_target_sha256: str
    figures: list[FigureFlags]
    limitations: list[str]


def _sentences(*texts):
    for text in texts:
        for s in re.split(r'(?<=[.;])\s+', text or ''):
            if s.strip():
                yield s.strip()


def prefill(aid, figure_id, panel, visual_panel, label_texts=()):
    """Deterministic hints from reviewer-written text and known reports; never final."""
    texts = list(label_texts) + [visual_panel.get('detailed_description', '')] + \
        list(visual_panel.get('pixel_observations', [])) + list(visual_panel.get('limitations', []))
    hits = {'burned_in': [], 'face': [], 'sensitive': []}
    for s in _sentences(*texts):
        positive = not NEGATED.search(s)
        if BURNED.search(s) and positive and not TRIVIAL.search(s):
            hits['burned_in'].append(s[:300])
        if FACE.search(s) and positive:
            hits['face'].append(s[:300])
        if SENSITIVE.search(s) and positive:
            hits['sensitive'].append(s[:300])
    burned = 'none'
    if hits['burned_in']:
        burned = 'direct_identifiers' if any(DIRECT.search(s) for s in hits['burned_in']) else 'timestamps_or_device_text'
    source = 'reviewer_text_scan'
    known = KNOWN_REPORTS.get((aid, figure_id, panel))
    if known:
        burned, source = known[0], 'coordinator_report'
        hits['burned_in'].insert(0, known[1])
    photo = visual_panel.get('image_kind') in PHOTO_KINDS
    return {'suggested_burned_in_identifiers': burned,
            'suggested_identifiable_face': bool(hits['face']) and photo,
            'suggested_sensitive_anatomy': bool(hits['sensitive']) and photo,
            'evidence': hits, 'source': source}


def draft_flags(source, visual_target, labels=None):
    label_text = {}
    for f in (labels or {}).get('figures', []):
        for p in f.get('panels', []):
            label_text[(f['figure_id'], p['panel'])] = [p['label'].get('rationale') or '', p['label'].get('override_reason') or '']
    figures = []
    for row in visual_target['figures']:
        if row['status'] != 'inspected':
            figures.append({'figure_id': row['figure_id'], 'status': 'unavailable', 'panels': [],
                            'limitations': list(row.get('limitations') or ['Figure unavailable'])})
            continue
        panels = []
        for vp in row['analysis']['visual']['panels']:
            hint = prefill(source['article_id'], row['figure_id'], vp['panel'], vp, label_text.get((row['figure_id'], vp['panel']), ()))
            burned = hint['suggested_burned_in_identifiers']
            panels.append({'panel': vp['panel'], 'burned_in_identifiers': burned,
                           'burned_in_description': None if burned == 'none' else f'{DRAFT_MARK} describe the visible text category',
                           'sensitive_anatomy': hint['suggested_sensitive_anatomy'],
                           'sensitive_anatomy_description': f'{DRAFT_MARK} describe' if hint['suggested_sensitive_anatomy'] else None,
                           'identifiable_face': hint['suggested_identifiable_face'],
                           'face_description': f'{DRAFT_MARK} describe' if hint['suggested_identifiable_face'] else None,
                           'basis': 'pixels_reviewed', 'notes': f'{DRAFT_MARK} view the pixels and confirm all three flags',
                           'prefill': hint})
        figures.append({'figure_id': row['figure_id'], 'status': 'flagged', 'panels': panels, 'limitations': []})
    return {'format': FORMAT, 'article_id': source['article_id'], 'source_text_sha256': source['text_sha256'],
            'visual_target_sha256': json_digest(visual_target), 'figures': figures, 'limitations': []}


def validate_flags(target, source, visual_target):
    flags = FigurePrivacyFlags.model_validate(target)
    if flags.article_id != source['article_id'] or flags.source_text_sha256 != source['text_sha256']:
        raise ValueError('Privacy flags have stale/wrong article source')
    if flags.visual_target_sha256 != json_digest(visual_target):
        raise ValueError('Visual submission changed after privacy flagging')
    visual = {f['figure_id']: f for f in visual_target['figures']}
    ids = [f.figure_id for f in flags.figures]
    if len(ids) != len(set(ids)) or set(ids) != set(visual):
        raise ValueError('Every visual-submission figure must be flagged exactly once')
    counts = {'direct_identifiers': 0, 'timestamps_or_device_text': 0, 'sensitive_anatomy': 0, 'identifiable_face': 0}
    total = 0
    for figure in flags.figures:
        row = visual[figure.figure_id]
        if row['status'] != 'inspected':
            if figure.status != 'unavailable' or figure.panels or not figure.limitations:
                raise ValueError('Uninspected figures are unavailable, with limitations and no flags')
            continue
        if figure.status != 'flagged' or [p.panel for p in figure.panels] != [p['panel'] for p in row['analysis']['visual']['panels']]:
            raise ValueError('Flags must cover the reviewed visual panels in order')
        for p in figure.panels:
            total += 1
            counts[p.burned_in_identifiers] = counts.get(p.burned_in_identifiers, 0) + 1
            counts['sensitive_anatomy'] += p.sensitive_anatomy
            counts['identifiable_face'] += p.identifiable_face
    counts.pop('none', None)
    return {'figures': len(flags.figures), 'panels': total, **{f'{k}_panels': v for k, v in counts.items()},
            'includability_changed': False}


def panel_flags(target):
    return {(f['figure_id'], p['panel']): {k: p[k] for k in ('burned_in_identifiers', 'burned_in_description',
            'sensitive_anatomy', 'sensitive_anatomy_description', 'identifiable_face', 'face_description')}
            for f in target['figures'] for p in f['panels']}


def contract_sha256():
    return json_digest(FigurePrivacyFlags.model_json_schema())
