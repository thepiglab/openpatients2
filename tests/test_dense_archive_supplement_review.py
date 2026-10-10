"""A declared ZIP supplement reviewed per member PDF: binding, staleness and typed payloads."""
from copy import deepcopy
import hashlib
import io
import json
from pathlib import Path
import shutil
import sys
import zipfile

import pytest

ROOT = Path(__file__).resolve().parents[1]
AID = 'PMC-archive-test.1'
ZIP_URL = 'https://pmc-oa-opendata.s3.amazonaws.com/PMC-archive-test.1/s001.zip?md5=x'
MEMBER = 'Supplementary/report.pdf'


def pdf(count):
    sys.path.insert(0, str(ROOT / 'tests'))
    from test_dense_document_supplement_review import pdf_bytes
    return pdf_bytes(count)


def make_zip(member_bytes):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w') as z:
        z.writestr(MEMBER, member_bytes)
        z.writestr('__MACOSX/Supplementary/._report.pdf', b'junk')
    return buffer.getvalue()


@pytest.fixture
def archive(tmp_path, monkeypatch):
    root = tmp_path / 'root'; root.mkdir()
    monkeypatch.syspath_prepend(str(ROOT / 'benchmarks/dense500'))
    for name in ('supplement_review', 'sidecar_receipts', 'measurement_series_review'):
        sys.modules.pop(name, None)
    import supplement_review as m
    monkeypatch.setattr(m, 'pdf_page_count', lambda path: 2)
    member = pdf(2); data = make_zip(member)
    zip_path = tmp_path / 'cache' / 's001.zip'; zip_path.parent.mkdir(); zip_path.write_bytes(data)
    extracted = tmp_path / 'cache' / 'member.pdf'; extracted.write_bytes(member)
    original = dict(url=ZIP_URL, filename='s001.zip', original_manifest_url='s3://x/s001.zip',
                    md5=hashlib.md5(data).hexdigest(), kind='supplementary_or_other')
    source = dict(article_id=AID, text_sha256='source-sha', license={'code': 'CC BY'}, figures=[],
                  segments=[dict(segment_id='b1', kind='paragraph', text='Serial labs are in the supplement.')],
                  supplements=[dict(supplement_id='S1', assets=[original])])
    annotation = dict(article_id=AID, text_sha256='source-sha', patients=[dict(patient_id='p1', label='Index patient',
                      species='human', identity_evidence=[], facts=[])])
    murl = m.member_url(ZIP_URL, MEMBER); msha = hashlib.sha256(member).hexdigest()
    page = lambda n, rel: dict(page=n, status='inspected', inspection_method='rendered_page', contents=f'Synthetic page {n}',
                               clinical_relevance=rel, clinical_relevance_notes='Synthetic', patient_ids=['p1'] if rel != 'administrative' else [],
                               rights_checked=True, rights_basis='source_license', rights_exceptions=[], limitations=[])
    def tx(tid, text, kind='clinical_data', col='Day1'):
        return dict(transcription_id=tid, asset_url=murl, asset_sha256=msha, page=2, panel=None, table_label='Table S1',
                    row_label='CRP', column_label=col, kind=kind, text=text, visual_cue=None)
    fields = dict(subject='index_patient', assertion='present', temporality='unknown',
                  time=dict(text='Day1', relation='during', anchor='hospitalization', date_iso=None), result_absent_reason=None,
                  name='CRP', kind='laboratory', status='resulted', text_value='12.5', numeric_value=12.5, comparator='=',
                  unit='mg/L', flag='unknown', reference_range_text=None, specimen=None, method=None, body_site=None)
    payload = dict(format=m.CLINICAL_FORMAT, coverage='complete', limitations=[],
                   transcriptions=[tx('S1-cell', '12.5'), tx('S1-day1', 'Day1', kind='context'),
                                   tx('S1-owner', 'Table S1 serial laboratory values of the patient', kind='context')],
                   facts=[dict(fact_id='S1-f001', task='observations', collection='items', fields=fields,
                               evidence=[dict(transcription_id='S1-cell', quote='12.5'), dict(transcription_id='S1-day1', quote='Day1')],
                               ownership='individual', patient_ids=['p1'],
                               ownership_evidence=[dict(basis='supplement', transcription_id='S1-owner', segment_id=None, quote='the patient')],
                               certainty='supported', scoring='include', notes='Synthetic only')])
    target = dict(format=m.ARCHIVE_FORMAT, article_id=AID, source_text_sha256='source-sha', supplement_id='S1', status='inspected',
                  document_classification='clinical_data', contents_summary='Synthetic report and lab table',
                  archive=dict(url=ZIP_URL, original_manifest_url=original['original_manifest_url'], filename='s001.zip',
                               md5=original['md5'], sha256=hashlib.sha256(data).hexdigest(), bytes=len(data), path=str(zip_path),
                               source_license=source['license'], media_type='application/zip', members=[MEMBER], other_members=[]),
                  assets=[dict(url=murl, member=MEMBER, sha256=msha, bytes=len(member), path=str(extracted),
                               media_type='application/pdf', page_count=2, pages=[page(1, 'administrative'), page(2, 'clinical_data')])],
                  limitations=[], clinical_data=payload)
    for folder in ('sources', 'annotations', 'document-submissions'):
        (root / folder).mkdir()
    def save():
        for folder, value, name in [('sources', source, f'{AID}.json'), ('annotations', annotation, f'{AID}.json'),
                                    ('document-submissions', target, f'{AID}.supplements.S1.json')]:
            (root / folder / name).write_text(json.dumps(value))
    save()
    return m, root, source, annotation, target, save


def sign_both(m, root):
    m.sign_review(root, AID, 'S1', role='agent', reviewer='Claude synthetic test agent', notes='Synthetic fixture inspection notes for tests')
    m.sign_review(root, AID, 'S1', role='parent', reviewer='Claude synthetic test parent', notes='Synthetic fixture inspection notes for tests')


def test_zip_members_bound_and_typed_payload_exported(archive):
    m, root, s, a, t, _ = archive
    report = m.validate_submission(t, s, a)
    assert report['complete'] and report['inspected_pages'] == 2 and report['source_assets'] == 1
    sign_both(m, root)
    (target, receipts), = m.reviewed_supplement_targets(root, AID)
    assert receipts[0]['status'] == 'source_and_document_pages_reviewed' and 'archive_sha256' in receipts[0]
    bundle = m.supplemental_clinical_bundle(target, a)
    asset = bundle['assets'][0]
    assert asset['member'] == MEMBER and asset['archive_url'] == ZIP_URL and asset['url'].endswith('#member=' + MEMBER)
    item = bundle['patients'][0]['sections']['observations']['items'][0]
    assert item['numeric_value'] == 12.5 and item['evidence'][0]['source_section'] == 'S1:transcription:S1-cell'
    # Measurement series can cite the reviewed member-page transcription.
    import measurement_series_review as series
    from openpatients2.measurement_series import validate_series, expected_parse
    transcriptions = series.reviewed_transcriptions(root, AID)
    assert transcriptions[('S1', 'S1-cell')] == '12.5'
    cell = lambda tid, role: dict(cell_id=tid, origin='supplement_transcription', role=role, segment_id=None, table_id=None,
                                  grid_row=None, column_index=None, supplement_id='S1', transcription_id=tid, asset_url=None,
                                  asset_sha256=None, sheet=None, row_index=None, page=2, raw_text=transcriptions[('S1', tid)])
    status, parsed = expected_parse('12.5')
    target_series = dict(format='dense-measurement-series/1', article_id=AID, source_text_sha256='source-sha', coverage='complete',
                         cells=[cell('S1-cell', 'value'), cell('S1-day1', 'column_header'), cell('S1-owner', 'context')],
                         series=[dict(series_id='crp', measurement_name='Table S1', name_cell_id='S1-owner', measurement_kind='laboratory',
                                      ownership='individual', patient_ids=['p1'],
                                      ownership_evidence=[dict(segment_id=None, cell_id='S1-owner', quote='the patient')],
                                      reference_range_cell_id=None, scoring='include', notes='Synthetic',
                                      points=[dict(point_id='crp-1', value_cell_id='S1-cell', parse_status=status, parsed=parsed,
                                                   unit_text=None, unit_cell_id=None,
                                                   time=dict(time_cell_id='S1-day1', time_text='Day1', offset_value=None, offset_unit=None,
                                                             offset_anchor_text=None, offset_basis=None))])],
                         excluded_cells=[], limitations=[])
    assert validate_series(target_series, s, a, transcriptions=transcriptions)['points'] == 1


@pytest.mark.parametrize('change', ['member_sha', 'member_page', 'archive_md5', 'members', 'payload'])
def test_changed_archive_or_member_makes_receipts_stale(archive, change):
    m, root, s, a, t, save = archive
    sign_both(m, root)
    if change == 'member_sha':
        t['assets'][0]['sha256'] = '0' * 64
        t['clinical_data']['transcriptions'][0]['asset_sha256'] = '0' * 64
    elif change == 'member_page': t['assets'][0]['pages'][0]['contents'] = 'changed'
    elif change == 'archive_md5': t['archive']['sha256'] = 'f' * 64
    elif change == 'members': t['archive']['other_members'] = ['notes.txt']
    else: t['clinical_data']['facts'][0]['notes'] = 'changed'
    save()
    with pytest.raises(ValueError):
        m.reviewed_supplement_targets(root, AID)


def test_tampered_member_bytes_cannot_be_signed(archive, tmp_path):
    m, root, s, a, t, _ = archive
    zip_path = Path(t['archive']['path'])
    zip_path.write_bytes(make_zip(pdf(2) + b'% tampered\n'))
    with pytest.raises(ValueError, match='Archive bytes changed'):
        m.validate_submission(t, s, a)
    # Same archive hash recorded for the tampered ZIP still disagrees on the member.
    data = zip_path.read_bytes()
    t['archive'].update(sha256=hashlib.sha256(data).hexdigest(), bytes=len(data), md5=None)
    s['supplements'][0]['assets'][0]['md5'] = None
    with pytest.raises(ValueError, match='member bytes changed'):
        m.validate_submission(t, s, a)


def test_member_page_count_mismatch_uses_actual_pdfinfo(archive, monkeypatch):
    if not shutil.which('pdfinfo'):
        pytest.skip('pdfinfo unavailable')
    m, root, s, a, t, _ = archive
    monkeypatch.undo()
    monkeypatch.syspath_prepend(str(ROOT / 'benchmarks/dense500'))
    assert m.pdf_page_count(t['assets'][0]['path']) == 2
    t['assets'][0]['page_count'] = 3
    t['assets'][0]['pages'].append(dict(deepcopy(t['assets'][0]['pages'][0]), page=3))
    with pytest.raises(ValueError, match='page count'):
        m.validate_submission(t, s, a)


@pytest.mark.parametrize('change', ['missing_member', 'non_pdf_member', 'wrong_url', 'zip_md5', 'missing_page'])
def test_incomplete_or_mismatched_archive_rejected(archive, change):
    m, root, s, a, t, _ = archive
    if change == 'missing_member': t['assets'] = []
    elif change == 'non_pdf_member': t['archive']['other_members'] = ['data.xlsx']
    elif change == 'wrong_url': t['assets'][0]['url'] = ZIP_URL
    elif change == 'zip_md5': t['archive']['md5'] = '0' * 32
    else: t['assets'][0]['pages'].pop()
    with pytest.raises(ValueError):
        m.validate_submission(t, s, a, verify_files=False)


def test_extract_writes_bounded_members_and_skeleton(archive, tmp_path, monkeypatch):
    m, root, s, a, t, _ = archive
    spec = __import__('importlib.util').util.spec_from_file_location('dense_assets_zip', ROOT / 'benchmarks/dense500/supplement_assets.py')
    assets = __import__('importlib.util').util.module_from_spec(spec); spec.loader.exec_module(assets)
    data = Path(t['archive']['path']).read_bytes()
    (root / 'supplement-assets').mkdir()
    (root / 'supplement-assets' / f'{AID}.json').write_text(json.dumps({'files': [dict(
        supplement_id='S1', url=ZIP_URL, filename='s001.zip', original_manifest_url='s3://x/s001.zip', declared_md5=t['archive']['md5'],
        download_status='downloaded', detected_type='zip', sha256=hashlib.sha256(data).hexdigest(), bytes=len(data),
        path=t['archive']['path'])]}))
    cache = tmp_path / 'supp-cache'
    record = assets.extract_archive(root, AID, 'S1', cache=cache)
    assert [x['member'] for x in record['members']] == [MEMBER] and record['archive']['members'] == [MEMBER]
    assert Path(record['members'][0]['path']).parent == cache / AID / 'members'
    skeleton = json.loads((root / 'document-submission-drafts' / f'{AID}.supplements.S1.json').read_text())
    assert skeleton['format'] == m.ARCHIVE_FORMAT and skeleton['status'] == 'partial'
    with pytest.raises(assets.CacheFull):
        assets.extract_archive(root, AID, 'S1', cache=tmp_path / 'tiny', max_cache=10)


def test_existing_pdf_path_unchanged(archive):
    m, *_ = archive
    assert m.DOCUMENT_FORMAT == 'dense-supplemental-document-reference/1'
    assert 'archive' not in m.DocumentSupplementTarget.model_fields
