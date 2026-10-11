"""Additive EHR sidecars: bounded supplement cache, manifests, figure EHR labels, series.

Fixtures are synthetic; no actual clinical review is claimed.
"""
from copy import deepcopy
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import shutil
import struct
import sys
import zipfile

import httpx
import pytest

ROOT = Path(__file__).resolve().parents[1]
AID = 'PMC-test.1'
HOST = 'https://pmc-oa-opendata.s3.amazonaws.com/PMC-test.1/'


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    return module


@pytest.fixture
def mods(monkeypatch):
    monkeypatch.syspath_prepend(str(ROOT / 'benchmarks/dense500'))
    for name in ('sidecar_receipts', 'supplement_manifest_review', 'figure_ehr_review', 'measurement_series_review'):
        sys.modules.pop(name, None)
    assets = load('dense_supplement_assets', ROOT / 'benchmarks/dense500/supplement_assets.py')
    import supplement_manifest_review, figure_ehr_review, measurement_series_review
    return assets, supplement_manifest_review, figure_ehr_review, measurement_series_review


def docx_bytes(paragraphs):
    body = ''.join(f'<w:p><w:r><w:t>{p}</w:t></w:r></w:p>' for p in paragraphs)
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w') as z:
        z.writestr('[Content_Types].xml', '<Types/>')
        z.writestr('word/document.xml', f'<w:document xmlns:w="w"><w:body>{body}<w:tbl><w:tr></w:tr></w:tbl></w:body></w:document>')
    return buffer.getvalue()


FILES = {
    'checklist.docx': docx_bytes(['CARE Checklist', 'Item 1 Title page 1']),
    'labs.csv': b'Day,Glucose mmol/L\n1,9.48\n2,3.14\n',
    'big.zip': b'PK\x03\x04' + b'\x00' * 5000,
    'blob.bin': b'\x00\x01\x02\x03' * 50,
}


def transport(files, calls):
    def handler(request):
        name = request.url.path.rsplit('/', 1)[-1]
        data = files[name]
        calls.append((request.method, name, request.headers.get('range')))
        if request.method == 'HEAD':
            return httpx.Response(200, headers={'content-length': str(len(data)), 'content-type': 'application/octet-stream'})
        rng = request.headers.get('range')
        if rng and rng.startswith('bytes=-'):
            n = int(rng[7:]); return httpx.Response(206, content=data[-n:])
        if rng:
            a, b = rng[6:].split('-'); return httpx.Response(206, content=data[int(a):int(b) + 1])
        return httpx.Response(200, content=data)
    return httpx.MockTransport(handler)


@pytest.fixture
def article(tmp_path):
    root = tmp_path / 'root'; (root / 'sources').mkdir(parents=True); (root / 'annotations').mkdir()
    supplements = [dict(supplement_id=f'S{i}', description='', assets=[dict(url=HOST + name, filename=name,
                        original_manifest_url='s3://x/' + name, md5=hashlib.md5(data).hexdigest(), kind='supplementary_or_other')])
                   for i, (name, data) in enumerate(FILES.items(), 1)]
    supplements.append(dict(supplement_id='S9', description='Link only', assets=[]))
    source = dict(article_id=AID, text_sha256='source-hash', license={'code': 'CC BY'}, figures=[], supplements=supplements,
                  segments=[dict(segment_id='b1', kind='paragraph', text='Table S2 shows the first patient laboratory course.'),
                            dict(segment_id='b2', kind='table_row', table_id='T1',
                                 text='Table 1\nLabs.\nProject | First time | After 10 days | Reference value\nInsulin | > 600.00 | 521 | 2.6-24.9 uIU/ml'),
                            dict(segment_id='b3', kind='table_row', table_id='T1',
                                 text='Table 1\nLabs.\nProject | First time | After 10 days | Reference value\nC-peptide | 7.73 | - | 1.1-4.4 ng/ml')])
    annotation = dict(article_id=AID, text_sha256='source-hash', patients=[dict(patient_id='p1', label='Index patient',
                      species='human', identity_evidence=[])])
    (root / 'sources' / f'{AID}.json').write_text(json.dumps(source))
    (root / 'annotations' / f'{AID}.json').write_text(json.dumps(annotation))
    return root, source, annotation


def acquire(mods, root, tmp_path, caps=(10_000_000, 1000), files=FILES):
    assets = mods[0]; calls = []
    client = httpx.Client(transport=transport(files, calls))
    report = assets.acquire(root, AID, client=client, cache=tmp_path / 'cache', caps=caps)
    return report, calls


def test_cache_caps_large_files_sampled_and_types_recorded(mods, article, tmp_path, monkeypatch):
    assets = mods[0]
    monkeypatch.setattr(assets, 'HEAD_SAMPLE_BYTES', 64); monkeypatch.setattr(assets, 'TAIL_SAMPLE_BYTES', 64)
    root, source, _ = article
    report, calls = acquire(mods, root, tmp_path)
    rows = {r['supplement_id']: r for r in report['files']}
    assert rows['S1']['download_status'] == 'downloaded' and rows['S1']['md5_verified'] is True
    assert rows['S1']['peek']['first_lines'][0] == 'CARE Checklist'
    assert rows['S2']['peek']['rows'][1] == ['1', '9.48'] and rows['S2']['peek']['row_count'] == 3
    # Larger than the per-file cap: HEAD + bounded ranges only, never a full GET.
    assert rows['S3']['download_status'] == 'head_sample_only' and rows['S3']['download_reason'] == 'exceeds_per_file_cap'
    assert rows['S3']['sample_bytes'] == 64 and rows['S3']['sha256'] is None
    assert not any(c == ('GET', 'big.zip', None) for c in calls)
    # Unsupported content is recorded, never silently dropped.
    assert rows['S4']['inspection_status'] == 'unsupported_type' and rows['S4']['detected_type'] == 'binary_unknown'
    assert rows['S9']['download_status'] == 'no_declared_asset'
    assert len(report['files']) == 5 and report['cache_bytes_after'] <= report['cache_limit_bytes']


def test_hard_cache_cap_never_exceeded(mods, article, tmp_path, monkeypatch):
    assets = mods[0]; root, *_ = article
    monkeypatch.setattr(assets, 'HEAD_SAMPLE_BYTES', 64); monkeypatch.setattr(assets, 'TAIL_SAMPLE_BYTES', 64)
    report, _ = acquire(mods, root, tmp_path, caps=(len(FILES['checklist.docx']) + 70, 10_000))
    statuses = [r['download_status'] for r in report['files']]
    assert statuses[0] == 'downloaded' and 'unavailable' in statuses
    assert any(r.get('download_reason') == 'cache_cap_reached' for r in report['files'])
    assert assets.cache_usage(tmp_path / 'cache') <= len(FILES['checklist.docx']) + 70


def manifest_from_draft(mods, root):
    review = mods[1]
    draft = review.write_draft(root, AID)
    for row in draft['files']:
        row['review'].update(content_class='unknown', content_class_rationale='Synthetic fixture', patient_scope='unresolved',
                             classification_basis='caption_and_filename_only', ehr_includable=False, ehr_modality='unknown',
                             limitations=['Synthetic fixture; not reviewed'])
    s1 = draft['files'][0]['review']
    s1.update(content_class='reporting_checklist', classification_basis='peek_content', patient_scope='administrative',
              ehr_modality='none', limitations=[])
    s2 = draft['files'][1]['review']
    s2.update(content_class='lab_results', classification_basis='full_content', patient_scope='patient_level',
              patient_ids=['p1'], ownership_evidence=[{'basis': 'article', 'segment_id': 'b1', 'quote': 'first patient'}],
              ehr_includable=True, ehr_modality='structured_table', limitations=[],
              estimated_counts={'rows': 3, 'frames': None, 'pages': None, 'members': None, 'duration_seconds': None, 'basis': None})
    (root / 'supplement-manifests').mkdir(exist_ok=True)
    (root / 'supplement-manifests' / f'{AID}.json').write_text(json.dumps(draft))
    return draft


def save(root, folder, value):
    (root / folder / f'{AID}.json').write_text(json.dumps(value))


def test_manifest_requires_review_and_rejects_invented_content(mods, article, tmp_path):
    root, source, annotation = article
    report, _ = acquire(mods, root, tmp_path)
    review = mods[1]
    raw = review.write_draft(root, AID)
    with pytest.raises(ValueError):
        review.validate_manifest(raw, source, annotation, report)
    manifest = manifest_from_draft(mods, root)
    result = review.validate_manifest(manifest, source, annotation, report)
    assert result['files'] == 5 and result['ehr_includable_files'] == 1
    for change in ('machine', 'peek', 'checklist_ehr', 'invented_count', 'peek_quote', 'unknown_patient', 'missing_row'):
        bad = deepcopy(manifest)
        if change == 'machine': bad['files'][1]['machine']['sha256'] = '0' * 64
        elif change == 'peek': bad['files'][1]['machine']['peek']['rows'][1] = ['1', '99.9']
        elif change == 'checklist_ehr': bad['files'][0]['review'].update(ehr_includable=True)
        elif change == 'invented_count': bad['files'][1]['review']['estimated_counts']['rows'] = 300
        elif change == 'peek_quote': bad['files'][1]['review']['ownership_evidence'] = [{'basis': 'supplement_peek', 'segment_id': None, 'quote': 'Patient 1'}]
        elif change == 'unknown_patient': bad['files'][1]['review']['patient_ids'] = ['p9']
        else: bad['files'].pop()
        with pytest.raises(ValueError):
            review.validate_manifest(bad, source, annotation, report)


def test_manifest_receipts_go_stale_and_cleanup_waits_for_signatures(mods, article, tmp_path):
    assets, review, *_ = mods
    root, source, annotation = article
    acquire(mods, root, tmp_path)
    manifest_from_draft(mods, root)
    cache = tmp_path / 'cache'
    with pytest.raises(ValueError, match='receipts'):
        assets.cleanup(root, AID, cache=cache, verify=review.reviewed_manifest)
    review.sign_review(root, AID, role='agent', reviewer='Claude synthetic test agent', notes='Synthetic fixture inspection notes for tests')
    assert review.reviewed_manifest(root, AID) is None
    with pytest.raises(ValueError, match='independent'):
        review.sign_review(root, AID, role='parent', reviewer='Claude synthetic test agent', notes='Synthetic fixture inspection notes for tests')
    review.sign_review(root, AID, role='parent', reviewer='Claude synthetic test parent', notes='Synthetic fixture inspection notes for tests')
    assert review.reviewed_manifest(root, AID)[1][1]['role'] == 'parent'
    with pytest.raises(ValueError, match='--force'):
        assets.acquire(root, AID, client=httpx.Client(transport=transport(FILES, [])), cache=cache)
    freed = assets.cleanup(root, AID, cache=cache, verify=review.reviewed_manifest)
    assert freed['freed_bytes'] > 0 and not (cache / AID).exists()
    assert review.reviewed_manifest(root, AID) is not None  # survives eviction
    with pytest.raises(FileNotFoundError):
        review.sign_review(root, AID, role='agent', reviewer='Claude synthetic test agent', notes='Synthetic fixture inspection notes for tests')
    for change in ('notes', 'annotation', 'source'):
        m = json.loads((root / 'supplement-manifests' / f'{AID}.json').read_text())
        a = deepcopy(annotation); s = deepcopy(source)
        if change == 'notes': m['files'][0]['review']['notes'] = 'changed'
        elif change == 'annotation': a['patients'][0]['label'] = 'changed'
        else: s['supplements'][0]['description'] = 'changed'
        save(root, 'supplement-manifests', m); save(root, 'annotations', a); save(root, 'sources', s)
        with pytest.raises(ValueError, match='stale|changed'):
            review.reviewed_manifest(root, AID)
        save(root, 'annotations', annotation); save(root, 'sources', source)


def visual_target(source):
    def panel(name, kind, modality=None):
        return {'panel': name, 'image_kind': kind, 'charts': [],
                'imaging_modalities': [{'value': modality}] if modality else []}
    def figure(fid, panels, assignments):
        return {'figure_id': fid, 'status': 'inspected', 'assets': [{'url': HOST + fid + '.jpg', 'sha256': 'a' * 64}],
                'analysis': {'visual': {'panels': panels}, 'attribution': {'assignments': assignments}}, 'limitations': []}
    own = lambda p: {'panel': p, 'patient_ids': ['p1'], 'scope': 'individual'}
    return {'format': 'dense-figure-reference/1', 'article_id': AID, 'source_text_sha256': source['text_sha256'], 'figures': [
        figure('F1', [panel('A', 'medical_imaging', 'ct'), panel('B', 'diagram')], [own('A'), {'panel': 'B', 'patient_ids': [], 'scope': 'background'}]),
        figure('F2', [panel(None, 'microscopy', 'light_microscopy')], [{'panel': None, 'patient_ids': [], 'scope': 'external'}]),
        {'figure_id': 'F3', 'status': 'unavailable', 'assets': [], 'analysis': None, 'limitations': ['Asset missing']}]}


def reviewed_labels(mods, root, source):
    review = mods[2]
    (root / 'visual-submissions').mkdir(exist_ok=True); save(root, 'visual-submissions', visual_target(source))
    labels = review.write_draft(root, AID)
    for figure in labels['figures']:
        for p in figure['panels']:
            p['label']['rationale'] = 'Synthetic fixture review'
        if figure['figure_label']:
            figure['figure_label']['rationale'] = 'Synthetic fixture review'
    (root / 'figure-ehr-labels').mkdir(exist_ok=True); save(root, 'figure-ehr-labels', labels)
    return labels


def test_figure_ehr_defaults_overrides_and_stale_receipts(mods, article):
    root, source, annotation = article
    review = mods[2]
    labels = reviewed_labels(mods, root, source)
    f1, f2, f3 = labels['figures']
    assert [(p['default']['ehr_category'], p['default']['ehr_includable']) for p in f1['panels']] == [
        ('clinical_imaging', True), ('diagram_or_schematic', False)]
    # An external case's slide is pathology, but not this patient's record.
    assert (f2['panels'][0]['default']['ehr_category'], f2['panels'][0]['default']['ehr_includable']) == ('pathology', False)
    assert f3['status'] == 'unavailable' and f3['panels'] == []
    vt = visual_target(source)
    assert review.validate_labels(labels, source, vt)['ehr_includable_panels'] == 1
    draft = review.draft_labels(source, vt)
    with pytest.raises(ValueError, match='Draft'):
        review.validate_labels(draft, source, vt)
    bad = deepcopy(labels); bad['figures'][0]['panels'][1]['label'].update(ehr_category='clinical_photo')
    with pytest.raises(ValueError, match='override_reason'):
        review.validate_labels(bad, source, vt)
    bad['figures'][0]['panels'][1]['label']['override_reason'] = 'Pixels show a photograph'
    review.validate_labels(bad, source, vt)
    bad = deepcopy(labels); bad['figures'][1]['panels'][0]['label'].update(ehr_includable=True, override_reason='x')
    with pytest.raises(ValueError, match='attribution'):
        review.validate_labels(bad, source, vt)
    bad = deepcopy(labels); bad['figures'][0]['panels'][1]['label'].update(ehr_includable=True, override_reason='x')
    with pytest.raises(ValueError, match='never'):
        review.validate_labels(bad, source, vt)
    bad = deepcopy(labels); bad['figures'][0]['panels'][0]['default']['rule'] = 'edited'
    with pytest.raises(ValueError, match='default'):
        review.validate_labels(bad, source, vt)
    review.sign_review(root, AID, role='agent', reviewer='Claude synthetic test agent', notes='Synthetic fixture inspection notes for tests')
    review.sign_review(root, AID, role='parent', reviewer='Claude synthetic test parent', notes='Synthetic fixture inspection notes for tests')
    assert review.reviewed_figure_ehr(root, AID) is not None
    vt['figures'][0]['analysis']['visual']['panels'][0]['image_kind'] = 'clinical_photograph'
    save(root, 'visual-submissions', vt)
    with pytest.raises(ValueError):
        review.reviewed_figure_ehr(root, AID)


def reviewed_series(mods, root, source):
    review = mods[3]
    draft = review.write_draft(root, AID)
    insulin = next(s for s in draft['series'] if s['measurement_name'] == 'Insulin')
    insulin.update(measurement_kind='laboratory', ownership='individual', patient_ids=['p1'], scoring='include',
                   ownership_evidence=[{'segment_id': 'b1', 'cell_id': None, 'quote': 'the first patient'}], notes='Reviewed fixture')
    insulin['points'][1]['time'].update(offset_value=10.0, offset_unit='days', offset_anchor_text='First time',
                                        offset_basis='Column header counts days after first measurement')
    insulin['points'][1].update(unit_text='uIU/ml', unit_cell_id='b2-c3')
    draft['series'] = [insulin]
    draft['excluded_cells'] = [{'cell_id': c['cell_id'], 'reason': 'Other analyte, not in fixture'} for c in draft['cells']
                               if c['role'] == 'value' and c['segment_id'] == 'b3']
    draft.update(coverage='complete', limitations=[])
    (root / 'measurement-series').mkdir(exist_ok=True); save(root, 'measurement-series', draft)
    return draft


def test_series_keep_raw_cells_and_never_invent_values(mods, article):
    root, source, annotation = article
    review = mods[3]
    series = reviewed_series(mods, root, source)
    points = series['series'][0]['points']
    assert points[0]['parsed']['comparator'] == '>' and points[0]['parsed']['magnitude'] == '600.00'
    assert points[1]['parsed']['unit'] is None  # unit stays a separate literal, never converted
    report = review.validate_series(series, source, annotation)
    assert report['parsed_points'] == 2 and report['coverage'] == 'complete'
    for change in ('value', 'cell_text', 'offset', 'missing_as_zero', 'coverage', 'owner_quote', 'unit'):
        bad = deepcopy(series)
        s = bad['series'][0]
        if change == 'value': s['points'][1]['parsed']['magnitude'] = '52.1'
        elif change == 'cell_text': next(c for c in bad['cells'] if c['cell_id'] == 'b2-c2')['raw_text'] = '52.1'
        elif change == 'offset': s['points'][1]['time']['offset_value'] = 14.0
        elif change == 'missing_as_zero':
            dash = next(c for c in bad['cells'] if c['raw_text'] == '-')
            bad['excluded_cells'] = [e for e in bad['excluded_cells'] if e['cell_id'] != dash['cell_id']]
            s['points'].append(dict(deepcopy(s['points'][1]), point_id='x', value_cell_id=dash['cell_id'],
                                    parse_status='parsed', parsed=deepcopy(s['points'][1]['parsed'])))
        elif change == 'coverage': bad['excluded_cells'] = []
        elif change == 'owner_quote': s['ownership_evidence'][0]['quote'] = 'patient 1'
        else: s['points'][1]['unit_text'] = 'mg/dL'
        with pytest.raises(ValueError):
            review.validate_series(bad, source, annotation)
    review.sign_review(root, AID, role='agent', reviewer='Claude synthetic test agent', notes='Synthetic fixture inspection notes for tests')
    review.sign_review(root, AID, role='parent', reviewer='Claude synthetic test parent', notes='Synthetic fixture inspection notes for tests')
    assert review.reviewed_series(root, AID) is not None
    series['series'][0]['notes'] = 'changed'; save(root, 'measurement-series', series)
    with pytest.raises(ValueError, match='stale'):
        review.reviewed_series(root, AID)


def test_peek_readers(tmp_path):
    from openpatients2 import supplement_peek as peek
    xlsx = tmp_path / 'data.xlsx'
    with zipfile.ZipFile(xlsx, 'w') as z:
        z.writestr('xl/workbook.xml', '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
                   'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets>'
                   '<sheet name="Vitals" r:id="rId1"/></sheets></workbook>')
        z.writestr('xl/_rels/workbook.xml.rels', '<Relationships><Relationship Id="rId1" Target="worksheets/sheet1.xml"/></Relationships>')
        z.writestr('xl/sharedStrings.xml', '<sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><si><t>HR</t></si></sst>')
        z.writestr('xl/worksheets/sheet1.xml', '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData>'
                   '<row r="1"><c r="A1" t="s"><v>0</v></c></row><row r="2"><c r="B2"><v>72</v></c></row></sheetData></worksheet>')
    detected, status, data, _ = peek.peek_file(xlsx, 'data.xlsx')
    assert (detected, status) == ('xlsx', 'peeked') and data['sheets'][0]['rows'] == [['HR'], ['', '72']]
    # ZIP central directory from a ranged tail sample, no extraction.
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w') as z:
        z.writestr('IMG0001.dcm', b'\x00' * 20000); z.writestr('DICOMDIR', b'x')
    whole = buffer.getvalue(); head = tmp_path / 'head.zip'; head.write_bytes(whole[:100])
    detected, status, data, _ = peek.peek_file(head, 'study.zip', complete=False, tail=whole[-300:], total_bytes=len(whole))
    assert status == 'peeked_head_sample' and data['member_count'] == 2 and data['dicomdir_present']
    # DICOM whitelist never copies identifying tags.
    def element(g, e, vr, value):
        return struct.pack('<HH', g, e) + vr + struct.pack('<H', len(value)) + value
    dicom = (b'\x00' * 128 + b'DICM' + element(2, 0x10, b'UI', b'1.2.840.10008.1.2.1\x00') +
             element(8, 0x60, b'CS', b'CT') + element(0x10, 0x10, b'PN', b'Doe^Jane') + element(0x28, 0x10, b'US', struct.pack('<H', 512)))
    path = tmp_path / 'x.dcm'; path.write_bytes(dicom)
    detected, status, data, _ = peek.peek_file(path, 'x.dcm')
    assert detected == 'dicom' and data['tags']['Modality'] == 'CT' and data['tags']['Rows'] == 512
    assert 'Doe' not in json.dumps(data)


def test_pdf_peek_uses_actual_tools(tmp_path):
    if not shutil.which('pdfinfo'):
        pytest.skip('pdfinfo unavailable')
    sys.path.insert(0, str(ROOT / 'tests'))
    from test_dense_document_supplement_review import pdf_bytes
    from openpatients2.supplement_peek import peek_file
    path = tmp_path / 'a.pdf'; path.write_bytes(pdf_bytes(3))
    detected, status, data, _ = peek_file(path, 'a.pdf')
    assert detected == 'pdf' and data['page_count'] == 3
    detected, status, data, _ = peek_file(path, 'a.pdf', complete=False)
    assert status == 'metadata_only' and data is None


def test_informational_checks_never_change_completion(monkeypatch, tmp_path):
    monkeypatch.syspath_prepend(str(ROOT / 'benchmarks/dense500'))
    exporter = load('dense_ehr_export', ROOT / 'benchmarks/dense500/export.py')
    monkeypatch.setattr(exporter, 'OUT', tmp_path)
    monkeypatch.setattr(exporter, 'EHR_SIDECAR_GATES', False)
    base = dict(title='t', article_url='https://x', text_sha256='h', annotation_status='annotated',
                review_status='parent_source_reviewed', full_schema_review_status='agent_and_parent_source_reviewed',
                pixel_review_status='agent_and_parent_reviewed', source_supplements=[], runtime_gates={'passed': True})
    rows = [dict(base, article_id='A', ehr_sidecar_status={'supplement_manifest': 'not_started', 'figure_ehr_labels': 'stale_or_invalid',
                                                         'measurement_series': 'unsigned'}),
            dict(base, article_id='B', ehr_sidecar_status={'supplement_manifest': 'no_source_supplements',
                 'figure_ehr_labels': 'agent_and_parent_reviewed', 'measurement_series': 'agent_and_parent_reviewed'})]
    stats = {k: 0 for k in ('acquired_candidate_articles', 'drafted_articles', 'parent_source_reviewed_articles',
                            'full_schema_reviewed_articles', 'pixel_reviewed_articles', 'reviewed_no_figure_inventories', 'reviewed_facts')}
    exporter.write_checklist({**stats, 'target_articles': 2}, rows)
    checklist = json.loads((tmp_path / 'article_checklist.json').read_text())
    assert checklist['completed_articles'] == 2
    assert checklist['articles']['A']['remaining'] == [] and 'supplement_manifest_reviewed' not in checklist['articles']['A']['checks']
    assert checklist['articles']['A']['informational_checks'] == {'supplement_manifest_reviewed': False,
        'figure_ehr_labels_reviewed': False, 'measurement_series_reviewed': False, 'figure_privacy_flags_reviewed': False}
    assert checklist['informational_counts']['figure_ehr_labels_reviewed'] == 1
    assert checklist['completed_with_all_informational_checks'] == 0
    assert checklist['ehr_sidecar_gates_enabled'] is False
    # The single switch turns the two sidecar checks into completion gates.
    monkeypatch.setattr(exporter, 'EHR_SIDECAR_GATES', True)
    exporter.write_checklist({**stats, 'target_articles': 2}, rows)
    gated = json.loads((tmp_path / 'article_checklist.json').read_text())
    assert gated['completed_articles'] == 1 and gated['articles']['B']['completed']
    assert gated['articles']['A']['remaining'] == ['supplement_manifest_reviewed', 'figure_ehr_labels_reviewed']
    assert 'measurement_series_reviewed' in gated['articles']['A']['informational_checks']
    assert gated['informational_counts']['figure_ehr_labels_reviewed'] == 1


def test_non_clinical_manifest_satisfies_legacy_supplement_gate(monkeypatch):
    monkeypatch.syspath_prepend(str(ROOT / 'benchmarks/dense500'))
    exporter = load('dense_ehr_export_gate', ROOT / 'benchmarks/dense500/export.py')
    def row(sid, cls, basis='full_content', scope='background', status='downloaded'):
        return {'supplement_id': sid, 'url': sid, 'machine': {'download_status': status},
                'review': {'content_class': cls, 'classification_basis': basis, 'patient_scope': scope,
                           'patient_ids': [], 'ehr_includable': False}}
    source = {'supplements': [{'supplement_id': s} for s in ('CHK', 'LIT', 'LAB', 'PEEK', 'MIX')]}
    manifest = {'files': [row('CHK', 'reporting_checklist'), row('LIT', 'literature_table'), row('LAB', 'lab_results'),
                          row('PEEK', 'reporting_checklist', basis='peek_content'),
                          row('MIX', 'code'), row('MIX', 'raw_dataset_tabular')]}
    inventory = {s['supplement_id']: s for s in exporter.supplement_inventory(source, manifest=manifest)}
    assert [sid for sid, s in inventory.items() if exporter.supplement_satisfied(s)] == ['CHK', 'LIT']
    assert all(s['content_inspected'] is False for s in inventory.values())
    unsigned = exporter.supplement_inventory(source)
    assert not any(exporter.supplement_satisfied(s) for s in unsigned)


def test_species_flags_on_ehr_rows(monkeypatch):
    monkeypatch.syspath_prepend(str(ROOT / 'benchmarks/dense500'))
    exporter = load('dense_ehr_export_species', ROOT / 'benchmarks/dense500/export.py')
    annotation = {'patients': [{'patient_id': 'p1', 'species': 'nonhuman'}, {'patient_id': 'p2', 'species': 'human'}]}
    assert exporter.species_fields(annotation, ['p1'])['non_human_patient'] is True
    assert exporter.species_fields(annotation, ['p2'])['non_human_patient'] is False
    unattributed = exporter.species_fields(annotation, [])
    assert unattributed['non_human_patient'] is None and unattributed['article_has_non_human_patient'] is True


def privacy_module(monkeypatch):
    monkeypatch.syspath_prepend(str(ROOT / 'benchmarks/dense500'))
    sys.modules.pop('figure_privacy_review', None)
    import figure_privacy_review
    return figure_privacy_review


def test_privacy_flags_are_separate_and_never_stale_figure_ehr(mods, article, monkeypatch):
    root, source, annotation = article
    ehr = mods[2]
    labels = reviewed_labels(mods, root, source)
    labels['figures'][0]['panels'][0]['label']['rationale'] = 'CT with patient name and DOB burned into the header.'
    save(root, 'figure-ehr-labels', labels)
    ehr.sign_review(root, AID, role='agent', reviewer='Claude synthetic test agent', notes='Synthetic fixture inspection notes for tests')
    ehr.sign_review(root, AID, role='parent', reviewer='Claude synthetic test parent', notes='Synthetic fixture inspection notes for tests')
    privacy = privacy_module(monkeypatch)
    draft = privacy.write_draft(root, AID)
    panel = draft['figures'][0]['panels'][0]
    assert panel['burned_in_identifiers'] == 'direct_identifiers' and panel['prefill']['source'] == 'reviewer_text_scan'
    assert draft['figures'][0]['panels'][1]['burned_in_identifiers'] == 'none'
    (root / 'figure-privacy-flags').mkdir()
    vt = visual_target(source)
    with pytest.raises(ValueError, match='DRAFT|reviewed'):
        privacy.validate_flags(draft, source, vt)
    for f in draft['figures']:
        for p in f['panels']:
            p['notes'] = 'Viewed pixels (synthetic fixture)'
    panel['burned_in_description'] = 'Name 1234567 in header'
    with pytest.raises(ValueError, match='identifier values'):
        privacy.validate_flags(draft, source, vt)
    panel['burned_in_description'] = 'Patient name and date of birth in the scanner header'
    panel.update(identifiable_face=True, face_description=None)
    with pytest.raises(ValueError, match='description'):
        privacy.validate_flags(draft, source, vt)
    panel.update(identifiable_face=False)
    report = privacy.validate_flags(draft, source, vt)
    assert report['direct_identifiers_panels'] == 1 and report['includability_changed'] is False
    save(root, 'figure-privacy-flags', draft)
    privacy.sign_review(root, AID, role='agent', reviewer='Claude synthetic test agent', notes='Synthetic fixture inspection notes for tests')
    privacy.sign_review(root, AID, role='parent', reviewer='Claude synthetic test parent', notes='Synthetic fixture inspection notes for tests')
    assert privacy.reviewed_privacy_flags(root, AID) is not None
    # Adding/signing privacy flags leaves the figure EHR receipts valid.
    assert ehr.reviewed_figure_ehr(root, AID) is not None
    draft['figures'][0]['panels'][0]['notes'] = 'changed'; save(root, 'figure-privacy-flags', draft)
    with pytest.raises(ValueError, match='stale'):
        privacy.reviewed_privacy_flags(root, AID)
    assert ehr.reviewed_figure_ehr(root, AID) is not None


def test_known_reports_prefill_drafts():
    from openpatients2.figure_privacy import prefill
    hint = prefill('PMC12689157.1', 'F1', 'A', {'image_kind': 'medical_imaging'})
    assert hint['suggested_burned_in_identifiers'] == 'direct_identifiers' and hint['source'] == 'coordinator_report'
    hint = prefill('X', 'F', None, {'image_kind': 'clinical_photograph',
                                    'pixel_observations': ['Unmasked infant face visible.', 'Only panel letters are burned in.']})
    assert hint['suggested_identifiable_face'] and hint['suggested_burned_in_identifiers'] == 'none'


def table_docx():
    """DOCX with a repeated header row, a horizontal merge and a vertical merge."""
    W = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'
    def cell(text, props=''):
        return f'<w:tc><w:tcPr>{props}</w:tcPr><w:p><w:r><w:t xml:space="preserve">{text}</w:t></w:r></w:p></w:tc>'
    rows = [('<w:trPr><w:tblHeader/></w:trPr>', cell('Analyte') + cell('Day 1') + cell('Day 2')),
            ('', cell('Glucose', '<w:vMerge w:val="restart"/>') + cell('9.48') + cell('3.14')),
            ('', cell('', '<w:vMerge/>') + cell('7.0 mmol/L') + cell('6.94')),
            ('', cell('Note: fasting samples', '<w:gridSpan w:val="3"/>'))]
    body = ('<w:p><w:r><w:t>Table S1 Laboratory course</w:t></w:r></w:p><w:tbl><w:tblGrid><w:gridCol/><w:gridCol/><w:gridCol/></w:tblGrid>'
            + ''.join(f'<w:tr>{pr}{cells}</w:tr>' for pr, cells in rows) + '</w:tbl>')
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w') as z:
        z.writestr('[Content_Types].xml', '<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
                   '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
                   '<Default Extension="xml" ContentType="application/xml"/>'
                   '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/></Types>')
        z.writestr('_rels/.rels', '<?xml version="1.0"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                   '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/></Relationships>')
        z.writestr('word/document.xml', f'<?xml version="1.0"?><w:document {W}><w:body>{body}</w:body></w:document>')
    return buffer.getvalue()


anydoc_required = pytest.mark.skipif(importlib.util.find_spec('anydoc') is None, reason='firecrawl-anydoc not installed')


@anydoc_required
def test_anydoc_tables_are_literal_with_spans_and_headers(tmp_path):
    from openpatients2 import supplement_peek as peek
    path = tmp_path / 't.docx'; path.write_bytes(table_docx())
    detected, status, data, limits = peek.peek_file(path, 't.docx')
    assert (detected, status, data['tool']) == ('docx', 'peeked', 'anydoc@0.2.4')
    table = data['tables'][0]
    assert table['header_rows'] == 1 and table['rows'][0] == ['Analyte', 'Day 1', 'Day 2']
    assert table['rows'][2][0] is None  # covered slot of the vertical merge
    assert [1, 0, 2, 1] in table['spans'] and [3, 0, 1, 3] in table['spans']
    with zipfile.ZipFile(path) as archive:
        raw = peek._docx_text(archive)[0]
    # Golden: every emitted cell/line is a substring of the raw w:t text.
    for row in table['rows']:
        for cell in row:
            assert cell is None or all(part in raw for part in cell.split('\n'))
    assert all(line in raw for line in data['first_lines'])
    assert data['literal_verification'] == 'verified_against_docx_xml'
    assert '7.0 mmol/L' in peek.peek_text(data)


def test_document_falls_back_without_anydoc(tmp_path, monkeypatch):
    from openpatients2 import supplement_peek as peek
    monkeypatch.setattr(peek, 'anydoc_available', lambda: False)
    path = tmp_path / 't.docx'; path.write_bytes(table_docx())
    detected, status, data, limits = peek.peek_file(path, 't.docx')
    assert status == 'peeked' and data['tool'] == 'docx_xml' and 'tables' not in data
    assert any('anydoc not installed' in l for l in limits)
    pptx = tmp_path / 's.pptx'; pptx.write_bytes(b'PK\x03\x04' + b'\x00' * 100)
    detected, status, data, _ = peek.peek_file(pptx, 's.pptx')
    assert (detected, status, data) == ('pptx', 'metadata_only', None)


def test_pdf_route_never_uses_anydoc(tmp_path, monkeypatch):
    if not shutil.which('pdfinfo'):
        pytest.skip('pdfinfo unavailable')
    from openpatients2 import supplement_peek as peek
    sys.path.insert(0, str(ROOT / 'tests'))
    from test_dense_document_supplement_review import pdf_bytes
    monkeypatch.setattr(peek, '_anydoc_document', lambda *a: pytest.fail('PDF must use pdftotext'))
    path = tmp_path / 'a.pdf'; path.write_bytes(pdf_bytes(2))
    assert peek.peek_file(path, 'a.pdf')[2]['tool'] == 'pdfinfo+pdftotext'


@anydoc_required
def test_hosted_ocr_is_banned(monkeypatch):
    import anydoc
    from openpatients2 import supplement_peek as peek
    for name in ('_parse_hosted', 'to_markdown', 'to_markdown_bytes'):
        monkeypatch.setattr(anydoc, name, getattr(anydoc, name))
    prelude = peek.ANYDOC_WORKER.split('path, fmt')[0]
    exec(prelude, {})
    with pytest.raises(RuntimeError, match='banned'):
        anydoc.to_markdown('x.pdf', ocr='hosted')
    with pytest.raises(RuntimeError, match='banned'):
        anydoc._parse_hosted(b'', 'x', None, None)
    # The worker converts with the offline structured model only and gets no credentials.
    body = peek.ANYDOC_WORKER.split('path, fmt')[1]
    assert 'to_document' in body and 'to_markdown' not in body and 'hosted' not in body
    import inspect
    assert "env={'PATH': '/usr/bin:/bin'}" in inspect.getsource(peek._anydoc_document)


def test_repeek_refused_once_manifest_signed(mods, article, tmp_path):
    assets, review, *_ = mods
    root, *_ = article
    acquire(mods, root, tmp_path)
    manifest_from_draft(mods, root)
    review.sign_review(root, AID, role='agent', reviewer='Claude synthetic test agent', notes='Synthetic fixture inspection notes for tests')
    before = (root / 'supplement-assets' / f'{AID}.json').read_text()
    with pytest.raises(ValueError, match='--force'):
        assets.repeek(root, AID)
    assert (root / 'supplement-assets' / f'{AID}.json').read_text() == before
    review.check(root, AID)  # still valid


def test_video_manifest_review_and_unavailable_media_complete_flagged(monkeypatch, tmp_path):
    monkeypatch.syspath_prepend(str(ROOT / 'benchmarks/dense500'))
    exporter = load('dense_ehr_export_video_gate', ROOT / 'benchmarks/dense500/export.py')
    sampled = ['Viewed 17 frames sampled every 10 s, not every frame; audio was not inspected.']
    def row(sid, *, status='downloaded', detected='video', basis='full_content', limitations=sampled,
            counts={'frames': 5076, 'duration_seconds': 169.4}):
        return {'supplement_id': sid, 'url': sid,
                'machine': {'download_status': status, 'detected_type': detected,
                            'inspection_status': 'peeked' if status == 'downloaded' else 'not_inspectable'},
                'review': {'content_class': 'video', 'classification_basis': basis, 'patient_scope': 'patient_level',
                           'patient_ids': ['p1'], 'ehr_includable': basis == 'full_content', 'ehr_modality': 'video',
                           'estimated_counts': {'rows': None, 'pages': None, 'members': None, 'basis': 'ffprobe', **counts},
                           'limitations': limitations}}
    source = {'supplements': [{'supplement_id': s} for s in ('VID', 'NOSAMPLE', 'PEEK', 'YT')]}
    manifest = {'files': [row('VID'), row('NOSAMPLE', limitations=['Patient video.']),
                          row('PEEK', basis='peek_content'),
                          row('YT', status='no_declared_asset', detected=None, basis='caption_and_filename_only',
                              counts={'frames': None, 'duration_seconds': None},
                              limitations=['External YouTube link; not viewed.'])]}
    # Strict policy: only the sampled-frame video satisfies the gate; the external link is listed, pending.
    monkeypatch.setattr(exporter, 'UNAVAILABLE_MEDIA_POLICY', 'block')
    strict = {s['supplement_id']: s for s in exporter.supplement_inventory(source, manifest=manifest)}
    assert [sid for sid, s in strict.items() if exporter.supplement_satisfied(s)] == ['VID']
    assert strict['VID']['manifest_review_basis'] == 'manifest_video_frames_sampled'
    assert strict['VID']['review_status'] == 'agent_and_parent_manifest_video_frames_sampled'
    assert strict['VID']['content_inspected'] is False   # no page/pixel sidecar claimed
    assert strict['YT']['external_media_unavailable'] and strict['YT']['review_status'] == 'external_media_unavailable'
    # Default ('complete_flagged', user decision): unobtainable external media do not block, never content-inspected.
    monkeypatch.setattr(exporter, 'UNAVAILABLE_MEDIA_POLICY', 'complete_flagged')
    inventory = {s['supplement_id']: s for s in exporter.supplement_inventory(source, manifest=manifest)}
    assert inventory['YT']['manifest_review_basis'] == 'manifest_external_media_unavailable'
    assert exporter.supplement_satisfied(inventory['YT']) and not inventory['YT']['content_inspected']
    assert not exporter.supplement_satisfied(inventory['NOSAMPLE']) and not exporter.supplement_satisfied(inventory['PEEK'])
    # article_checklist.json: video reviewed via manifest; external link and unavailable figure complete, flagged.
    monkeypatch.setattr(exporter, 'OUT', tmp_path); monkeypatch.setattr(exporter, 'EHR_SIDECAR_GATES', False)
    base = dict(title='t', article_url='https://x', text_sha256='h', annotation_status='annotated',
                review_status='parent_source_reviewed', full_schema_review_status='agent_and_parent_source_reviewed',
                pixel_review_status='no_source_figures', runtime_gates={'passed': True}, ehr_sidecar_status={})
    rows = [dict(base, article_id='V', source_supplements=[inventory['VID']]),
            dict(base, article_id='Y', source_supplements=[inventory['VID'], inventory['YT']]),
            dict(base, article_id='F', source_supplements=[], pixel_review_status='reviewed_with_unavailable_assets',
                 unavailable_figure_ids=['VID1'])]
    stats = {k: 0 for k in ('acquired_candidate_articles', 'drafted_articles', 'parent_source_reviewed_articles',
                            'full_schema_reviewed_articles', 'pixel_reviewed_articles', 'reviewed_no_figure_inventories', 'reviewed_facts')}
    exporter.write_checklist({**stats, 'target_articles': 3}, rows)
    checklist = json.loads((tmp_path / 'article_checklist.json').read_text()); articles = checklist['articles']
    assert articles['V']['completed'] and articles['V']['manifest_reviewed_supplements'] == {'VID': 'manifest_video_frames_sampled'}
    assert not articles['V']['completed_with_unavailable_media'] and articles['V']['unavailable_media'] == []
    assert articles['Y']['completed'] and articles['Y']['completed_with_unavailable_media']
    assert articles['Y']['unavailable_media'] == [{'kind': 'supplement', 'id': 'YT', 'reason': 'external_link_no_declared_asset'}]
    assert articles['F']['completed'] and articles['F']['figure_review_status'] == 'reviewed_with_unavailable_assets'
    assert articles['F']['unavailable_media'] == [{'kind': 'figure', 'id': 'VID1', 'reason': 'no_declared_asset'}]
    assert checklist['completed_with_unavailable_media'] == 2 and checklist['unavailable_media_policy'] == 'complete_flagged'
    monkeypatch.setattr(exporter, 'UNAVAILABLE_MEDIA_POLICY', 'block')
    exporter.write_checklist({**stats, 'target_articles': 3}, [dict(r, source_supplements=[strict[s['supplement_id']]
                                                                    for s in r['source_supplements']]) for r in rows])
    blocked = json.loads((tmp_path / 'article_checklist.json').read_text())['articles']
    assert blocked['Y']['remaining'] == ['supplement_review_or_no_supplements']
    assert blocked['F']['remaining'] == ['figure_dual_review_or_no_figures']
    # Unified record: explicit per-item flags, never claimed as reviewed.
    from openpatients2.unified_record import build_figures, build_supplements, unavailable_media
    built = {s['supplement_id']: s for s in build_supplements(source, manifest=manifest)}
    assert built['VID']['gate_satisfied_by'] == 'manifest_video_frames_sampled' and built['VID']['content_reviewed']
    assert built['YT']['media_available'] is False and built['YT']['content_reviewed'] is False
    assert built['YT']['review_status'] == 'external_media_unavailable'
    figures = build_figures({'figures': [{'figure_key': 'VID1', 'label': 'Video 1', 'caption': 'TTE'}]},
                            {'figures': [{'figure_id': 'VID1', 'status': 'unavailable', 'assets': [],
                                          'limitations': ['No resolved asset URL.']}]}, [])
    assert figures[0]['media_available'] is False and figures[0]['content_reviewed'] is False
    assert unavailable_media(figures, list(built.values())) == [
        {'kind': 'figure', 'id': 'VID1', 'reason': 'no_declared_asset', 'content_reviewed': False},
        {'kind': 'supplement', 'id': 'YT', 'reason': 'external_link_no_declared_asset', 'content_reviewed': False}]
    # The runtime pipeline emits the same flags for a figure whose source declares no image asset.
    from openpatients2.unified_record_flags import media_flags
    assert media_flags({'status': 'missing_image'}) == {'media_available': False, 'content_reviewed': False,
                                                         'unavailable_reason': 'no_declared_asset'}
    assert media_flags({'status': 'fetch_failed'})['media_available'] is None


def test_stale_visual_or_figure_sidecar_receipt_withholds_the_article(monkeypatch, tmp_path):
    monkeypatch.syspath_prepend(str(ROOT / 'benchmarks/dense500'))
    exporter = load('dense_ehr_export_withhold', ROOT / 'benchmarks/dense500/export.py')
    monkeypatch.setattr(exporter, 'OUT', tmp_path)
    base = dict(title='t', article_url='https://x', text_sha256='h', annotation_status='annotated', source_supplements=[],
                review_status='parent_source_reviewed', full_schema_review_status='agent_and_parent_source_reviewed',
                pixel_review_status='agent_and_parent_reviewed', runtime_gates={'passed': True},
                ehr_sidecar_status={'supplement_manifest': 'no_source_supplements', 'figure_ehr_labels': 'agent_and_parent_reviewed'})
    rows = [dict(base, article_id='OK'),
            dict(base, article_id='S', pixel_review_status='stale_visual_receipt',
                 withheld_reason='S: stale visual agent receipt patient_identity_sha256',
                 ehr_sidecar_status={**base['ehr_sidecar_status'], 'figure_ehr_labels': 'stale_or_invalid'})]
    stats = {k: 0 for k in ('acquired_candidate_articles', 'drafted_articles', 'parent_source_reviewed_articles',
                            'full_schema_reviewed_articles', 'pixel_reviewed_articles', 'reviewed_no_figure_inventories', 'reviewed_facts')}
    exporter.write_checklist({**stats, 'target_articles': 2}, rows)
    checklist = json.loads((tmp_path / 'article_checklist.json').read_text())
    assert checklist['articles']['OK']['completed'] and not checklist['articles']['S']['completed']
    assert checklist['articles']['S']['withheld']['statuses'] == ['stale_visual_receipt', 'stale_figure_sidecar_receipt']
    assert 'patient_identity_sha256' in checklist['articles']['S']['withheld']['reason']
    assert checklist['withheld_articles'] == 1
