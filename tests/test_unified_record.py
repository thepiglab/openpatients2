"""Unified one-row-per-article release, runtime EHR task contracts and the scorer."""
from copy import deepcopy
import json
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
RELEASE = ROOT / 'data/dense_clinical_cases_v1'
R = ROOT / 'benchmarks/dense500'
AID = 'PMC12786310.1'


def release_rows():
    return [json.loads(l) for s in ('train', 'validation', 'test')
            for l in (RELEASE / 'data' / f'{s}.jsonl').read_text().splitlines() if l.strip()]


needs_release = pytest.mark.skipif(not (RELEASE / 'data' / 'train.jsonl').exists(), reason='release not exported')


@needs_release
def test_every_released_row_validates_and_matches_checklist():
    from openpatients2.unified_record import UnifiedArticle, validate_record, IDENTIFIER_VALUE
    rows = release_rows()
    checklist = json.loads((RELEASE / 'article_checklist.json').read_text())
    completed = {a for a, v in checklist['articles'].items() if v['completed']}
    # Rows are written in the same export pass as the checklist; tolerate a concurrent re-export.
    if {r['article_id'] for r in rows} != completed:
        pytest.skip('release is being re-exported concurrently')
    for row in rows:
        UnifiedArticle.model_validate(row); validate_record(row)
        assert row['completion']['completed'] is True and row['physician_adjudicated'] is False
        for f in row['figures']:
            for p in f['panels']:
                for key in ('burned_in_description', 'sensitive_anatomy_description', 'face_description'):
                    assert not (p['privacy'][key] and IDENTIFIER_VALUE.search(p['privacy'][key]))
    card = (RELEASE / 'README.md').read_text()
    import yaml
    configs = yaml.safe_load(card.split('---', 2)[1])['configs']
    assert configs == [{'config_name': 'default', 'default': True,
                        'data_files': [{'split': s, 'path': f'data/{s}.jsonl'} for s in ('train', 'validation', 'test')]}]
    assert json.loads((RELEASE / 'schema.json').read_text())['$id'] == 'openpatients2-unified-article/1'


def test_privacy_task_rejects_copied_identifier_values():
    from openpatients2 import ehr_tasks
    from openpatients2.unified_record import PanelPrivacy
    row = {'figure_id': 'F1', 'analysis': {'visual': {'panels': [{'panel': None}]}}}
    good = {'figure_id': 'F1', 'panels': [{'panel': None, 'burned_in_identifiers': 'direct_identifiers',
            'burned_in_description': 'Patient name and medical record number in the header', 'sensitive_anatomy': False,
            'sensitive_anatomy_description': None, 'identifiable_face': False, 'face_description': None}]}
    assert ehr_tasks.check_privacy(good, row)['panels'][0]['burned_in_identifiers'] == 'direct_identifiers'
    bad = deepcopy(good); bad['panels'][0]['burned_in_description'] = 'MRN 00412345 and DOB 1957-03-02'
    with pytest.raises(ValueError, match='identifier values'):
        ehr_tasks.check_privacy(bad, row)
    with pytest.raises(ValueError):
        PanelPrivacy(status='x', burned_in_identifiers='direct_identifiers', burned_in_description='ID 123456',
                     sensitive_anatomy=False, sensitive_anatomy_description=None, identifiable_face=False, face_description=None)
    missing = deepcopy(good); missing['panels'][0]['burned_in_description'] = None
    with pytest.raises(ValueError):
        ehr_tasks.check_privacy(missing, row)


def test_figure_ehr_task_contract_requires_panels_and_override_reason():
    from openpatients2 import ehr_tasks
    panel = {'panel': 'A', 'image_kind': 'medical_imaging', 'charts': [], 'imaging_modalities': [{'value': 'ct'}]}
    row = {'figure_id': 'F1', 'assets': [{'url': 'u', 'sha256': 'a' * 64}],
           'analysis': {'visual': {'panels': [panel]},
                        'attribution': {'assignments': [{'panel': 'A', 'patient_ids': ['p1'], 'scope': 'individual', 'subject': 'patient'}]}}}
    value = {'figure_id': 'F1', 'figure_ehr_category': 'clinical_imaging', 'figure_ehr_includable': True, 'figure_rationale': 'CT',
             'panels': [{'panel': 'A', 'ehr_category': 'clinical_imaging', 'ehr_includable': True, 'rationale': 'Patient CT'}]}
    out = ehr_tasks.check_figure_ehr(value, row)
    assert out['panels'][0]['default']['ehr_category'] == 'clinical_imaging' and out['panels'][0]['label']['override_reason'] is None
    value['panels'][0].update(ehr_category='diagram_or_schematic', ehr_includable=False)
    with pytest.raises(ValueError, match='override_reason'):
        ehr_tasks.check_figure_ehr(value, row)
    value['panels'][0]['panel'] = 'B'
    with pytest.raises(ValueError, match='listed panels'):
        ehr_tasks.check_figure_ehr(value, row)
    assert set(ehr_tasks.schemas()) == set(ehr_tasks.TASKS)


def test_series_task_never_accepts_typed_values():
    from openpatients2 import ehr_tasks
    source = {'article_id': 'X', 'text_sha256': 'h', 'segments': [
        {'segment_id': 'b1', 'kind': 'paragraph', 'text': 'Labs of the first patient.'},
        {'segment_id': 'b2', 'kind': 'table_row', 'table_id': 'T1', 'text': 'T\nTest | Day 1 | Day 3\nCRP | 12.5 | 3.1'}]}
    annotation = {'article_id': 'X', 'text_sha256': 'h', 'patients': [{'patient_id': 'p1'}]}
    draft = ehr_tasks.series_draft(source)
    point = lambda c, t: {'value_cell_id': c, 'time_cell_id': t, 'time_text': None, 'unit_cell_id': None, 'unit_text': None,
                          'offset_value': None, 'offset_unit': None, 'offset_anchor_text': None, 'offset_basis': None}
    value = {'series': [{'name_cell_id': 'b2-c0', 'measurement_name': 'CRP', 'measurement_kind': 'laboratory', 'ownership': 'individual',
                         'patient_ids': ['p1'], 'ownership_evidence': [{'segment_id': 'b1', 'cell_id': None, 'quote': 'first patient'}],
                         'reference_range_cell_id': None, 'points': [point('b2-c1', 'b2-h1'), point('b2-c2', 'b2-h2')]}]}
    doc = ehr_tasks.check_series(value, source, annotation, draft)
    assert [p['parsed']['magnitude'] for p in doc['series'][0]['points']] == ['12.5', '3.1']
    value['series'][0]['points'][0]['value'] = '99'
    with pytest.raises(ValueError):
        ehr_tasks.check_series(value, source, annotation, draft)


@needs_release
def test_pipeline_builder_round_trip_scores_perfectly_on_reference_article():
    sys.path.insert(0, str(R))
    from openpatients2 import ehr_tasks
    from openpatients2.ehr_pipeline import unified_from_pipeline
    from openpatients2.unified_scoring import score
    reference = {r['article_id']: r for r in release_rows()}.get(AID)
    paths = [R / folder / f'{AID}.json' for folder in ('figure-ehr-labels', 'figure-privacy-flags', 'supplement-manifests',
                                                     'measurement-series', 'visual-submissions', 'supplement-assets')]
    if reference is None or not all(p.exists() for p in paths):
        pytest.skip('reference article not completed with all sidecars')
    labels, flags, manifest, series, visual, acquisition = (json.loads(p.read_text()) for p in paths)
    source = json.loads((R / 'sources' / f'{AID}.json').read_text())
    annotation = json.loads((R / 'annotations' / f'{AID}.json').read_text())
    from measurement_series_review import reviewed_transcriptions
    outputs = {}
    vrows = {f['figure_id']: f for f in visual['figures']}
    outputs['figure_ehr_label'] = ehr_tasks.labels_document(source, visual, [
        ehr_tasks.check_figure_ehr(ehr_tasks.output_from_sidecar('figure_ehr_label', f), vrows[f['figure_id']])
        for f in labels['figures'] if f['status'] == 'labeled'])
    outputs['figure_privacy_flags'] = ehr_tasks.flags_document(source, visual, [
        ehr_tasks.check_privacy(ehr_tasks.output_from_sidecar('figure_privacy_flags', f), vrows[f['figure_id']])
        for f in flags['figures'] if f['status'] == 'flagged'])
    outputs['supplement_classification'] = ehr_tasks.check_supplements(
        ehr_tasks.output_from_sidecar('supplement_classification', manifest), source, annotation, acquisition)
    outputs['measurement_series_extraction'] = ehr_tasks.check_series(
        ehr_tasks.output_from_sidecar('measurement_series_extraction', series), source, annotation, series,
        transcriptions=reviewed_transcriptions(R, AID))
    bundles = [json.loads(l) for l in (RELEASE / 'legacy' / 'patients.jsonl').read_text().splitlines() if f'"{AID}:' in l]
    predicted = unified_from_pipeline(source, bundles=bundles, roster={'patients': annotation['patients']}, visual_target=visual,
                                      outputs=outputs, split=reference['split'], statuses={})
    from openpatients2.unified_record import validate_record
    validate_record(predicted)
    result = score({AID: reference}, {AID: predicted})
    assert result['panel_ehr_category_accuracy'] == 1.0 and result['panels_scored'] > 0
    assert result['panel_ehr_includable']['fn'] == result['panel_ehr_includable']['fp'] == 0
    assert result['supplement_class_accuracy'] == 1.0
    assert result['series_points']['recall'] == 1.0 and result['series_points']['precision'] == 1.0
    assert all(v['fp'] == v['fn'] == 0 for v in result['privacy_flags'].values())
    assert result['clinical_item_names']['recall'] == 1.0


def test_ehr_stage_defaults_off_and_runner_emits_unified_rows(tmp_path, monkeypatch):
    import asyncio
    from openpatients2 import ehr_pipeline, ehr_tasks
    from openpatients2.pilot_extract import PilotRunner
    assert not any(getattr(ehr_pipeline.EhrStageConfig(), s) for s in ehr_pipeline.STAGES)
    labels_path = R / 'figure-ehr-labels' / f'{AID}.json'
    if not (labels_path.exists() and (RELEASE / 'legacy' / 'patients.jsonl').exists()):
        pytest.skip('reference artifacts unavailable')
    source = json.loads((R / 'sources' / f'{AID}.json').read_text())
    annotation = json.loads((R / 'annotations' / f'{AID}.json').read_text())
    visual = json.loads((R / 'visual-submissions' / f'{AID}.json').read_text())
    labels = json.loads(labels_path.read_text())
    run = tmp_path / 'run'; run.mkdir()
    (run / 'patients.jsonl').write_text(''.join(l + '\n' for l in (RELEASE / 'legacy' / 'patients.jsonl').read_text().splitlines()
                                                if f'"{AID}:' in l))
    (run / 'rosters.json').write_text(json.dumps({'articles': [{'article_id': AID, 'roster': {'patients': annotation['patients']}}]}))
    (run / 'visual-annotations.json').write_text(json.dumps([
        {'identity': {'article_id': AID, 'figure_id': f['figure_id']}, 'status': 'valid' if f['status'] == 'inspected' else 'incomplete',
         'visual': {'data': (f.get('analysis') or {}).get('visual')}, 'attribution': {'data': (f.get('analysis') or {}).get('attribution')},
         'pixel_provenance': (f['assets'] or [{}])[0]} for f in visual['figures']]))
    articles = tmp_path / 'articles.jsonl'; articles.write_text(json.dumps(source) + '\n')
    answers = {f['figure_id']: ehr_tasks.output_from_sidecar('figure_ehr_label', f) for f in labels['figures'] if f['status'] == 'labeled'}
    async def fake_call(self, task, messages, checker, identity, replica, segments, **kw):
        assert task == 'figure_ehr_label' and task in self.schemas
        return {'status': 'valid', 'data': checker(answers[identity['figure_id']]), 'errors': []}
    monkeypatch.setattr(PilotRunner, 'call', fake_call)
    report = asyncio.run(ehr_pipeline.run_ehr_stages({'figure_ehr_label': True}, articles, run, tmp_path / 'out',
                                                     'http://127.0.0.1:9/v1', 4096))
    assert report['articles'][0]['status']['figure_ehr_labels'] == 'model_output_unreviewed', report
    row = json.loads((tmp_path / 'out' / 'data' / 'unified.jsonl').read_text())
    assert row['format'] == 'openpatients2-unified-article/1' and row['completion']['completed'] is False
    assert all(p['ehr']['status'] == 'model_output_unreviewed' for f in row['figures'] for p in f['panels'])
    assert row['sidecar_status']['figure_privacy_flags'] == 'stage_disabled'
