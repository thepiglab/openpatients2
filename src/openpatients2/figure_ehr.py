"""EHR-relevance overlay for reviewed figures (`dense-figure-ehr-labels/1`).

A separate sidecar over the visual submission (`articles/ID/visual.json`): the existing FigureVisuals
contract, its hashes and receipts are untouched. Each figure and panel gets a
deterministic default derived from the reviewed image_kind, imaging modality,
chart type/scope and patient attribution, plus a reviewer's final label. A label
says whether the image would plausibly belong in the patient's EHR; it is not a
diagnosis and does not establish what the pixels show.
"""
from __future__ import annotations

from collections import Counter
from typing import Literal

from pydantic import Field

from .provenance import json_digest
from .schemas import StrictModel

FORMAT = 'dense-figure-ehr-labels/1'
EhrCategory = Literal['clinical_imaging', 'waveform', 'pathology', 'clinical_photo',
                      'clinical_chart_of_patient_data', 'non_clinical_diagnostic_aid',
                      'diagram_or_schematic', 'literature_or_aggregate', 'other']
INCLUDABLE = {'clinical_imaging', 'waveform', 'pathology', 'clinical_photo', 'clinical_chart_of_patient_data'}
MICROSCOPY = {'light_microscopy', 'fluorescence_microscopy', 'electron_microscopy'}
DRAFT_MARK = 'DRAFT:'


class EhrLabel(StrictModel):
    ehr_category: EhrCategory
    ehr_includable: bool
    rationale: str = Field(min_length=1)
    basis: Literal['pixels_reviewed', 'visual_record_reviewed']
    override_reason: str | None


class PanelLabel(StrictModel):
    panel: str | None
    default: dict
    label: EhrLabel


class FigureLabels(StrictModel):
    figure_id: str
    status: Literal['labeled', 'unavailable']
    assets: list[dict]
    figure_label: EhrLabel | None
    panels: list[PanelLabel]
    limitations: list[str]


class FigureEhrLabels(StrictModel):
    format: Literal['dense-figure-ehr-labels/1']
    article_id: str
    source_text_sha256: str
    visual_target_sha256: str
    figures: list[FigureLabels]
    limitations: list[str]


def panel_assignments(analysis, panel):
    return [a for a in analysis['attribution']['assignments'] if a['panel'] is None or a['panel'] == panel]


def derive_default(panel, assignments):
    """Deterministic suggestion from the reviewed visual record; the reviewer decides."""
    kind = panel['image_kind']
    modalities = sorted({m['value'] for m in panel.get('imaging_modalities', [])})
    charts = panel.get('charts', [])
    chart_types = sorted({c['chart_type'] for c in charts})
    chart_scopes = sorted({c['scope'] for c in charts})
    metric_kinds = {r['metric_kind'] for c in charts for r in c.get('readings', [])}
    scopes = sorted({a['scope'] for a in assignments})
    patient_ids = sorted({p for a in assignments for p in a['patient_ids']})
    local = any(a['scope'] in ('individual', 'shared') for a in assignments) and bool(patient_ids)
    if kind == 'microscopy' or set(modalities) & MICROSCOPY:
        category, rule = 'pathology', 'microscopy'
    elif kind == 'specimen_photograph':
        category, rule = 'pathology', 'specimen_photograph'
    elif kind == 'medical_imaging':
        category, rule = 'clinical_imaging', 'medical_imaging'
    elif kind in ('clinical_photograph', 'operative_photograph'):
        category, rule = 'clinical_photo', kind
    elif kind in ('graph', 'table'):
        if 'waveform' in chart_types or metric_kinds & {'ecg', 'eeg'}:
            category, rule = 'waveform', 'waveform_chart'
        elif local and (kind == 'table' or any(c.get('is_clinical_chart') is True for c in charts)) and \
                set(chart_scopes) <= {'individual', 'multiple_individuals'}:
            category, rule = 'clinical_chart_of_patient_data', f'{kind}_of_attributed_patient_data'
        elif set(chart_scopes) & {'aggregate', 'background'} or not local:
            category, rule = 'literature_or_aggregate', f'{kind}_aggregate_or_unattributed'
        else:
            category, rule = 'other', f'{kind}_unclassified'
    elif kind in ('diagram', 'timeline'):
        category, rule = 'diagram_or_schematic', kind
    else:
        category, rule = 'other', f'image_kind_{kind}'
    includable = category in INCLUDABLE and local
    if category in INCLUDABLE and not local:
        rule += '+not_attributed_to_local_patient'
    return {'ehr_category': category, 'ehr_includable': includable, 'rule': rule, 'image_kind': kind,
            'imaging_modalities': modalities, 'chart_types': chart_types, 'chart_scopes': chart_scopes,
            'attribution_scopes': scopes, 'patient_ids': patient_ids}


def _visual_rows(visual_target):
    return {f['figure_id']: f for f in visual_target['figures']}


def draft_labels(source, visual_target):
    figures = []
    for row in visual_target['figures']:
        assets = [{'url': a['url'], 'sha256': a['sha256']} for a in row.get('assets', [])]
        if row['status'] != 'inspected':
            figures.append({'figure_id': row['figure_id'], 'status': 'unavailable', 'assets': assets,
                            'figure_label': None, 'panels': [], 'limitations': list(row.get('limitations') or ['Figure unavailable'])})
            continue
        panels = []
        for panel in row['analysis']['visual']['panels']:
            default = derive_default(panel, panel_assignments(row['analysis'], panel['panel']))
            panels.append({'panel': panel['panel'], 'default': default,
                           'label': {'ehr_category': default['ehr_category'], 'ehr_includable': default['ehr_includable'],
                                     'rationale': f'{DRAFT_MARK} confirm default rule {default["rule"]} against pixels',
                                     'basis': 'visual_record_reviewed', 'override_reason': None}})
        top = Counter(p['label']['ehr_category'] for p in panels).most_common(1)[0][0]
        figures.append({'figure_id': row['figure_id'], 'status': 'labeled', 'assets': assets,
                        'figure_label': {'ehr_category': top, 'ehr_includable': any(p['label']['ehr_includable'] for p in panels),
                                         'rationale': f'{DRAFT_MARK} confirm whole-figure label', 'basis': 'visual_record_reviewed',
                                         'override_reason': None},
                        'panels': panels, 'limitations': []})
    return {'format': FORMAT, 'article_id': source['article_id'], 'source_text_sha256': source['text_sha256'],
            'visual_target_sha256': json_digest(visual_target), 'figures': figures,
            'limitations': []}


def _check_label(label, default=None):
    if label.rationale.startswith(DRAFT_MARK):
        raise ValueError('Draft rationale must be replaced by the reviewer')
    if label.ehr_includable and label.ehr_category not in INCLUDABLE:
        raise ValueError(f'{label.ehr_category} is never EHR-includable')
    if default is not None:
        if label.ehr_includable and not default['patient_ids']:
            raise ValueError('EHR-includable panel requires local patient attribution in the reviewed visual record')
        changed = (label.ehr_category, label.ehr_includable) != (default['ehr_category'], default['ehr_includable'])
        if changed and not (label.override_reason or '').strip():
            raise ValueError('Overriding the deterministic default needs override_reason')
        if not changed and label.override_reason:
            raise ValueError('override_reason is only for labels that differ from the default')


def validate_labels(target, source, visual_target):
    labels = FigureEhrLabels.model_validate(target)
    if labels.article_id != source['article_id'] or labels.source_text_sha256 != source['text_sha256']:
        raise ValueError('Figure EHR labels have stale/wrong article source')
    if visual_target.get('article_id') != source['article_id'] or visual_target.get('source_text_sha256') != source['text_sha256']:
        raise ValueError('Visual submission has stale/wrong article source')
    if labels.visual_target_sha256 != json_digest(visual_target):
        raise ValueError('Visual submission changed after figure EHR labelling')
    visual = _visual_rows(visual_target)
    ids = [f.figure_id for f in labels.figures]
    if len(ids) != len(set(ids)) or set(ids) != set(visual):
        raise ValueError('Every visual-submission figure must be labelled exactly once')
    categories = Counter(); includable = overridden = panels_total = 0
    for figure in labels.figures:
        row = visual[figure.figure_id]
        if figure.assets != [{'url': a['url'], 'sha256': a['sha256']} for a in row.get('assets', [])]:
            raise ValueError('Figure EHR labels must bind the reviewed visual assets')
        if row['status'] != 'inspected':
            if figure.status != 'unavailable' or figure.panels or figure.figure_label or not figure.limitations:
                raise ValueError('Uninspected figures are unavailable, with limitations and no labels')
            continue
        if figure.status != 'labeled' or figure.figure_label is None:
            raise ValueError('Inspected figures need a figure label')
        visual_panels = row['analysis']['visual']['panels']
        if [p.panel for p in figure.panels] != [p['panel'] for p in visual_panels]:
            raise ValueError('Panel labels must match the reviewed visual panels in order')
        for label, panel in zip(figure.panels, visual_panels):
            default = derive_default(panel, panel_assignments(row['analysis'], panel['panel']))
            if label.default != default:
                raise ValueError(f'{figure.figure_id}/{panel["panel"]}: deterministic default was edited or is stale')
            _check_label(label.label, default)
            categories[label.label.ehr_category] += 1; panels_total += 1
            includable += label.label.ehr_includable
            overridden += label.label.override_reason is not None
        _check_label(figure.figure_label)
        if figure.figure_label.ehr_category not in {p.label.ehr_category for p in figure.panels}:
            raise ValueError('Whole-figure category must be one of its panel categories')
        if figure.figure_label.ehr_includable != any(p.label.ehr_includable for p in figure.panels):
            raise ValueError('Whole-figure includability must agree with its panels')
    return {'figures': len(labels.figures), 'labeled_figures': sum(f.status == 'labeled' for f in labels.figures),
            'panels': panels_total, 'ehr_includable_panels': includable, 'overridden_defaults': overridden,
            'ehr_categories': dict(sorted(categories.items())), 'pixel_accuracy_measured': False}


def label_rows(article_id, target, visual_target, split=None):
    """One release row per labelled panel, joined to its reviewed assets and owners."""
    visual = _visual_rows(visual_target)
    for figure in target['figures']:
        for panel in figure['panels']:
            yield {'format': FORMAT, 'article_id': article_id, 'figure_id': figure['figure_id'],
                   'panel': panel['panel'], **panel['label'], 'default': panel['default'],
                   'patient_ids': panel['default']['patient_ids'], 'figure_label': figure['figure_label'],
                   'assets': visual[figure['figure_id']]['assets'], 'split': split}


def contract_sha256():
    return json_digest(FigureEhrLabels.model_json_schema())
