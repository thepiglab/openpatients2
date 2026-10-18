"""Supplement paths beyond single-kind documents: images and videos embedded in DOCX/PPTX, image/video/XLSX members
of a ZIP, mixed archives (with an extensionless DOCX) and standalone XLSX. All fixtures are synthetic and built in tmp."""
from copy import deepcopy
import hashlib
import io
import json
from pathlib import Path
import shutil
import struct
import subprocess
import sys
import zipfile
import zlib

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tests'))
from openpatients2.dense_store import load as load_store
store = load_store()
AID = 'PMC-composite-test.1'
BASE = 'https://pmc-oa-opendata.s3.amazonaws.com/PMC-composite-test.1/'
SEGMENT = 'Case 1 is shown in the supplementary material.'

pytest.importorskip('anydoc')


# ------------------------------------------------------------------ synthetic files
def png_bytes(color=(255, 0, 0)):
    raw = b''.join(b'\x00' + bytes(color) * 2 for _ in range(2))
    chunk = lambda kind, data: struct.pack('>I', len(data)) + kind + data + struct.pack('>I', zlib.crc32(kind + data))
    return (b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', 2, 2, 8, 2, 0, 0, 0)) +
            chunk(b'IDAT', zlib.compress(raw)) + chunk(b'IEND', b''))


def jpeg_bytes(tag=b'1'):
    return b'\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00' + tag * 32 + b'\xff\xd9'


def _package(parts):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w') as z:
        for name, data in parts.items():
            z.writestr(name, data)
    return buffer.getvalue()


CT = ('<?xml version="1.0" encoding="UTF-8"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
      '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
      '<Default Extension="xml" ContentType="application/xml"/><Default Extension="png" ContentType="image/png"/>{}</Types>')
RELS = ('<?xml version="1.0" encoding="UTF-8"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '{}</Relationships>')


def docx_bytes(image=True):
    p = lambda text: f'<w:p><w:r><w:t xml:space="preserve">{text}</w:t></w:r></w:p>'
    body = p('Supplementary Figure S1. PET/CT of case 1') + p('Day 3 | CRP 12.5 mg/L')
    document = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?><w:document '
                'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body>' + body + '</w:body></w:document>')
    parts = {'[Content_Types].xml': CT.format('<Override PartName="/word/document.xml" ContentType="application/'
                                              'vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'),
             '_rels/.rels': RELS.format('<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/'
                                        'relationships/officeDocument" Target="word/document.xml"/>'),
             'word/document.xml': document}
    if image:
        parts['word/media/image1.png'] = png_bytes()
    return _package(parts)


def pptx_bytes(media=None):
    ns = ('xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" xmlns:r="http://schemas.openxmlformats.org/'
          'officeDocument/2006/relationships" xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"')
    paras = ''.join(f'<a:p><a:r><a:t>{t}</a:t></a:r></a:p>' for t in ('Teaching slide', 'Echo of case 1'))
    slide = (f'<?xml version="1.0" encoding="UTF-8"?><p:sld {ns}><p:cSld><p:spTree><p:nvGrpSpPr><p:cNvPr id="1" name=""/>'
             '<p:cNvGrpSpPr/><p:nvPr/></p:nvGrpSpPr><p:grpSpPr/><p:sp><p:nvSpPr><p:cNvPr id="2" name="T"/><p:cNvSpPr/>'
             f'<p:nvPr/></p:nvSpPr><p:spPr/><p:txBody><a:bodyPr/>{paras}</p:txBody></p:sp></p:spTree></p:cSld></p:sld>')
    return _package({
        '[Content_Types].xml': CT.format(
            '<Override PartName="/ppt/presentation.xml" ContentType="application/vnd.openxmlformats-officedocument.'
            'presentationml.presentation.main+xml"/><Override PartName="/ppt/slides/slide1.xml" ContentType="application/'
            'vnd.openxmlformats-officedocument.presentationml.slide+xml"/>'),
        '_rels/.rels': RELS.format('<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/'
                                   'relationships/officeDocument" Target="ppt/presentation.xml"/>'),
        'ppt/presentation.xml': f'<?xml version="1.0" encoding="UTF-8"?><p:presentation {ns}><p:sldIdLst>'
                                '<p:sldId id="256" r:id="rId2"/></p:sldIdLst></p:presentation>',
        'ppt/_rels/presentation.xml.rels': RELS.format('<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/'
                                                       'officeDocument/2006/relationships/slide" Target="slides/slide1.xml"/>'),
        'ppt/slides/slide1.xml': slide, 'ppt/media/image1.png': png_bytes((0, 0, 255)), **(media or {})})


def xlsx_bytes(rows=(('Patient', 'Gene', 'VAF'), ('Case 1', 'TP53', '0.42'), (), ('Case 1', 'KRAS', '0.11'))):
    shared = sorted({c for r in rows for c in r if not c.replace('.', '').isdigit()})
    def cell(ref, value):
        if value.replace('.', '').isdigit():
            return f'<c r="{ref}"><v>{value}</v></c>'
        return f'<c r="{ref}" t="s"><v>{shared.index(value)}</v></c>'
    data = ''.join(f'<row r="{i}">' + ''.join(cell(f'{"ABC"[j]}{i}', v) for j, v in enumerate(r)) + '</row>'
                   for i, r in enumerate(rows, 1) if r)
    main = 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'
    return _package({
        '[Content_Types].xml': CT.format(''),
        'xl/workbook.xml': f'<workbook xmlns="{main}" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/'
                           'relationships"><sheets><sheet name="Variants" sheetId="1" r:id="rId1"/>'
                           '<sheet name="Empty" sheetId="2" r:id="rId2"/></sheets></workbook>',
        'xl/_rels/workbook.xml.rels': RELS.format(
            '<Relationship Id="rId1" Type="worksheet" Target="worksheets/sheet1.xml"/>'
            '<Relationship Id="rId2" Type="worksheet" Target="worksheets/sheet2.xml"/>'),
        'xl/sharedStrings.xml': f'<sst xmlns="{main}">' + ''.join(f'<si><t>{s}</t></si>' for s in shared) + '</sst>',
        'xl/worksheets/sheet1.xml': f'<worksheet xmlns="{main}"><sheetData>{data}</sheetData></worksheet>',
        'xl/worksheets/sheet2.xml': f'<worksheet xmlns="{main}"><sheetData/></worksheet>'})


def mp4_bytes(tmp_path):
    if not (shutil.which('ffmpeg') and shutil.which('ffprobe')):
        pytest.skip('ffmpeg/ffprobe unavailable')
    out = tmp_path / 'synthetic.mp4'
    subprocess.run(['ffmpeg', '-nostdin', '-v', 'error', '-y', '-f', 'lavfi', '-i', 'testsrc=duration=1:size=32x32:rate=10',
                    '-pix_fmt', 'yuv420p', str(out)], check=True, capture_output=True, timeout=120)
    return out.read_bytes()


def pdf_bytes():
    from test_dense_document_supplement_review import pdf_bytes as make
    return make(1)


# ------------------------------------------------------------------ environment
@pytest.fixture
def env(tmp_path, monkeypatch):
    from openpatients2.dense import supplement_assets as assets, supplement_review as m
    monkeypatch.setattr(m, 'pdf_page_count', lambda path: 1)
    root = tmp_path / 'root'; root.mkdir(); downloads = tmp_path / 'downloads'; downloads.mkdir()
    cache = tmp_path / 'cache'

    def build(files, *, sid='S1'):
        """files: [(filename, bytes)] declared assets of supplement `sid`; returns (source, annotation, draft)."""
        declared, acquired = [], []
        for name, data in files:
            path = downloads / name; path.write_bytes(data)
            url = BASE + name + '?md5=x'
            declared.append(dict(url=url, filename=name, original_manifest_url='s3://x/' + name,
                                 md5=hashlib.md5(data).hexdigest(), kind='supplementary_or_other'))
            from openpatients2.supplement_peek import detect_type
            acquired.append(dict(supplement_id=sid, url=url, filename=name, original_manifest_url='s3://x/' + name,
                                 declared_md5=declared[-1]['md5'], bytes=len(data), sha256=hashlib.sha256(data).hexdigest(),
                                 path=str(path), download_status='downloaded', detected_type=detect_type(data[:4096], name)))
        source = dict(article_id=AID, text_sha256='source-sha', license={'code': 'CC BY'}, figures=[],
                      segments=[dict(segment_id='b1', kind='paragraph', text=SEGMENT)],
                      supplements=[dict(supplement_id=sid, assets=declared)])
        annotation = dict(article_id=AID, text_sha256='source-sha', patients=[
            dict(patient_id='p1', label='Case 1', species='human', identity_evidence=[], facts=[])])
        for kind, obj in (('sources', source), ('annotations', annotation)):
            store.path(root, kind, AID, mkdir=True).write_text(json.dumps(obj))
        store.path(root, 'supplement-assets', AID, mkdir=True).write_text(json.dumps({'files': acquired}))
        text = len(files) >= 1 and all(n.lower().endswith(('.docx', '.pptx')) for n, _ in files)
        result = (assets.extract_text_documents(root, AID, sid, cache=cache, every=0.5) if text else
                  assets.extract_members(root, AID, sid, cache=cache, every=0.5))
        return source, annotation, json.loads(Path(result['draft']).read_text())

    def save(target, sid='S1'):
        store.path(root, 'document-submissions', AID, sid=sid, mkdir=True).write_text(json.dumps(target))

    def sign(sid='S1'):
        m.sign_review(root, AID, sid, role='agent', reviewer='Claude synthetic test agent',
                      notes='Viewed every member, image and row of the synthetic fixture')
        m.sign_review(root, AID, sid, role='parent', reviewer='Claude synthetic test parent',
                      notes='Independently viewed every member, image and row of the fixture')
        return m.reviewed_supplement_targets(root, AID)
    return m, root, build, save, sign


# ------------------------------------------------------------------ reviewer-authored parts
OWNER = [dict(basis='article', transcription_id=None, segment_id='b1', quote='Case 1')]
RIGHTS = dict(rights_checked=True, rights_basis='source_license', rights_exceptions=[])


def scope(relevance='background', pids=(), **extra):
    return dict(status='inspected', contents='Synthetic contents', clinical_relevance=relevance,
                clinical_relevance_notes='Synthetic', patient_ids=list(pids), ownership_evidence=OWNER if pids else [],
                limitations=[], **RIGHTS, **extra)


def flags(panel=None):
    return dict(panel=panel, burned_in_identifiers='none', burned_in_description=None, sensitive_anatomy=False,
                sensitive_anatomy_description=None, identifiable_face=False, face_description=None,
                basis='pixels_reviewed', notes='Viewed the pixels: no identifiers, faces or sensitive anatomy', prefill=None)


def image_review(kind='medical_imaging', relevance='background', pids=('p1',)):
    panel = dict(panel=None, general_description='Synthetic image', detailed_description='A small red square.',
                 image_kind=kind, has_chart=False, charts=[], imaging_modalities=[] if kind == 'table' else [
                     dict(basis='pixels', visual_cue='square raster', evidence=[], certainty='uncertain', value='other',
                          as_documented=None)],
                 imaging_submodalities=[], medical_domains=[], body_parts=[], mentioned_categories=[],
                 pixel_observations=['Uniform red square'], caption_claims=[], clinical_significance=[], limitations=[])
    return scope(relevance, pids, inspection_method='viewed_pixels', privacy=[flags()],
                 visual=dict(figure_id='S1', general_description='Synthetic image', detailed_description='A small red square.',
                             panels=[panel], limitations=[]))


def text_blocks(asset, relevance='background', pids=()):
    block = scope(relevance, pids, first_unit=1, last_unit=len(asset['units']), inspection_method='extracted_text')
    block.pop('ownership_evidence')
    return [block]


def complete_parts(target):
    for a in target['assets']:
        kind = a.get('kind', 'text')
        if kind == 'pdf':
            a['pages'] = [dict(page=n, status='inspected', inspection_method='rendered_page', contents='Synthetic page',
                               clinical_relevance='administrative', clinical_relevance_notes='Synthetic', patient_ids=[],
                               limitations=[], **RIGHTS) for n in range(1, a['page_count'] + 1)]
        elif kind == 'text':
            a['blocks'] = text_blocks(a)
        elif kind == 'image':
            a['review'] = image_review()
        elif kind == 'video':
            a['frame_blocks'] = [scope(first_frame=1, last_frame=len(a['sampling']['frames']),
                                       inspection_method='viewed_sampled_frames', privacy=flags())]
        elif kind == 'spreadsheet':
            a['sheet_reviews'] = [dict(sheet=s['sheet'], status='inspected', contents='Synthetic sheet', limitations=[],
                                       blocks=[scope(first_row=r[0][0], last_row=r[-1][1], inspection_method='every_row_read')]
                                       if (r := s['row_ranges']) else []) for s in a['extraction']['sheets']]
        for image in a.get('embedded_images') or []:
            image['review'] = image_review()
        for video in a.get('embedded_videos') or []:
            video['frame_blocks'] = [scope(first_frame=1, last_frame=len(video['sampling']['frames']),
                                           inspection_method='viewed_sampled_frames', privacy=flags())]
    target.update(status='inspected', document_classification='other', contents_summary='Synthetic supplement',
                  limitations=[])
    return target


OBSERVATION = dict(subject='index_patient', assertion='present', temporality='unknown',
                   time=dict(text=None, relation='unknown', anchor=None, date_iso=None), result_absent_reason=None,
                   name='TP53 variant allele fraction', kind='laboratory', status='resulted', text_value='0.42',
                   numeric_value=0.42, comparator='=', unit=None, flag='unknown', reference_range_text=None, specimen=None,
                   method=None, body_site=None)


def clinical(tx, quote):
    return dict(format='dense-supplemental-clinical-reference/1', coverage='complete', limitations=[], transcriptions=[tx],
                facts=[dict(fact_id='S1-f1', task='observations', collection='items', fields=OBSERVATION,
                            evidence=[dict(transcription_id=tx['transcription_id'], quote=quote)], ownership='individual',
                            patient_ids=['p1'], ownership_evidence=OWNER, certainty='supported', scoring='include',
                            notes='Synthetic')])


# ------------------------------------------------------------------ 1. images embedded in DOCX
def test_docx_embedded_images_require_pixel_review(env):
    m, root, build, save, sign = env
    source, annotation, draft = build([('table.docx', docx_bytes())])
    asset = draft['assets'][0]
    image, = asset['embedded_images']
    assert image['part'] == 'word/media/image1.png' and image['url'] == asset['url'] + '#part=word/media/image1.png'
    assert image['media_type'] == 'image/png' and Path(image['path']).read_bytes() == png_bytes()
    target = complete_parts(deepcopy(draft))
    missing = deepcopy(target); missing['assets'][0]['embedded_images'][0]['review'] = None
    with pytest.raises(ValueError, match='embedded image requires its own pixel inspection'):
        m.validate_submission(missing, source, annotation)
    legacy = deepcopy(target); legacy['assets'][0].pop('embedded_images')
    with pytest.raises(ValueError, match='embeds images that this sidecar does not inventory'):
        m.validate_submission(legacy, source, annotation)
    # A data-bearing (table) image needs a literal, pixel-cued transcription of an inspected panel.
    target['assets'][0]['embedded_images'][0]['review'] = image_review('table', 'clinical_data')
    tx = dict(transcription_id='S1-img1', asset_url=image['url'], asset_sha256=image['sha256'], page=None, panel=None,
              table_label='Figure S1', row_label='TP53', column_label='VAF', kind='clinical_data', text='0.42',
              visual_cue='Second row of the pictured table')
    target['clinical_data'] = clinical(tx, '0.42')
    uncued = deepcopy(target); uncued['clinical_data']['transcriptions'][0]['visual_cue'] = None
    with pytest.raises(ValueError, match='inspected panel and visual cue'):
        m.validate_submission(uncued, source, annotation)
    report = m.validate_submission(target, source, annotation)
    assert report['complete'] and report['inspected_images'] == 1
    save(target)
    (reviewed, receipts), = sign()
    assert receipts[0]['status'] == 'source_and_document_text_and_images_reviewed'
    from openpatients2.dense import export as exporter
    row, = exporter.supplement_inventory(source, [(reviewed, receipts)])
    assert exporter.supplement_satisfied(row) and row['review_status'] == 'agent_and_parent_source_and_document_text_and_images_reviewed'
    bundle = m.supplemental_clinical_bundle(reviewed, annotation)
    assert any(a.get('part') == 'word/media/image1.png' for a in bundle['assets'])
    # Stale: the extracted image no longer matches its document part.
    Path(image['path']).write_bytes(png_bytes((0, 255, 0)))
    with pytest.raises(ValueError, match='differs from its document part'):
        m.validate_submission(target, source, annotation)


def test_docx_without_images_keeps_the_text_status(env):
    m, root, build, save, sign = env
    source, annotation, draft = build([('plain.docx', docx_bytes(image=False))])
    assert draft['assets'][0]['embedded_images'] == []
    save(complete_parts(deepcopy(draft)))
    (_, receipts), = sign()
    assert receipts[0]['status'] == 'source_and_document_text_reviewed'


# ------------------------------------------------------------------ 2. PPTX
def test_pptx_text_units_and_slide_images(env):
    m, root, build, save, sign = env
    source, annotation, draft = build([('slides.pptx', pptx_bytes())])
    asset = draft['assets'][0]
    assert asset['media_type'].endswith('presentationml.presentation')
    assert [u['text'] for u in asset['units']] == ['Teaching slide', 'Echo of case 1']
    assert asset['extraction']['literal_verification'] == 'verified_against_pptx_xml'
    assert [i['part'] for i in asset['embedded_images']] == ['ppt/media/image1.png']
    target = complete_parts(deepcopy(draft))
    nonliteral = deepcopy(target)
    nonliteral['clinical_data'] = clinical(dict(transcription_id='A1-U0002', asset_url=asset['url'], asset_sha256=asset['sha256'],
        page=2, panel=None, table_label=None, row_label=None, column_label=None, kind='clinical_data',
        text='Echo of case 2', visual_cue=None), 'Echo')
    with pytest.raises(ValueError, match='literal in its extracted unit'):
        m.validate_submission(nonliteral, source, annotation)
    assert m.validate_submission(target, source, annotation)['complete']


def test_pptx_embedded_video_parts_are_frame_sampled_and_reviewed(env):
    """A PPTX with an embedded MP4 clip and its poster image (as PMC7453273.1 supp-2): the clip is inventoried with
    exact bytes, sampled like a composite video member, and a full review needs frame blocks over every frame."""
    m, root, build, save, sign = env
    video = mp4_bytes(root.parent)
    deck = pptx_bytes({'ppt/media/image2.png': png_bytes((0, 255, 0)), 'ppt/media/media1.mp4': video})
    source, annotation, draft = build([('clips.pptx', deck)])
    asset = draft['assets'][0]
    assert [i['part'] for i in asset['embedded_images']] == ['ppt/media/image1.png', 'ppt/media/image2.png']
    clip, = asset['embedded_videos']
    assert (clip['video'], clip['part'], clip['url']) == (1, 'ppt/media/media1.mp4', asset['url'] + '#part=ppt/media/media1.mp4')
    assert clip['sha256'] == hashlib.sha256(video).hexdigest() and Path(clip['path']).read_bytes() == video
    assert clip['media_type'] == 'video/mp4' and clip['continuous_motion_inspected'] is False
    frames = clip['sampling']['frames']
    assert len(frames) >= 2 and frames[0]['timestamp_seconds'] == 0.0 and all(Path(f['path']).exists() for f in frames)
    assert [f['timestamp_seconds'] for f in frames] == m.video_timestamps(clip['probe'], 0.5, clip['sampling']['max_frames'])
    target = complete_parts(deepcopy(draft))
    unreviewed = deepcopy(target); unreviewed['assets'][0]['embedded_videos'][0]['frame_blocks'] = []
    with pytest.raises(ValueError, match='sampled frame requires exactly one reviewed block'):
        m.validate_submission(unreviewed, source, annotation)
    omitted = deepcopy(target); omitted['assets'][0]['embedded_videos'] = []
    with pytest.raises(ValueError, match='Embedded video inventory differs'):
        m.validate_submission(omitted, source, annotation)
    unlisted = deepcopy(target); unlisted['assets'][0].pop('embedded_videos')
    with pytest.raises(ValueError, match='Embedded image inventory differs'):
        m.validate_submission(unlisted, source, annotation)
    thinned = deepcopy(target); thinned['assets'][0]['embedded_videos'][0]['sampling']['frames'].pop()
    thinned['assets'][0]['embedded_videos'][0]['frame_blocks'][0]['last_frame'] -= 1
    with pytest.raises(ValueError, match='fixed-interval timestamps across the whole video'):
        m.validate_submission(thinned, source, annotation)
    # The legacy inventory (video part as an unviewable image row) stays partial-only and names the fix.
    legacy = deepcopy(target); legacy['assets'][0].pop('embedded_videos')
    legacy['assets'][0]['embedded_images'].append(dict(image=3, part=clip['part'], url=clip['url'], sha256=clip['sha256'],
        bytes=clip['bytes'], path=clip['path'], media_type='application/octet-stream', preview_path=None,
        preview_sha256=None, review=None))
    with pytest.raises(ValueError, match='inventories embedded video parts as images'):
        m.validate_submission(legacy, source, annotation)
    legacy.update(status='partial', limitations=['Embedded MP4 not reviewed'])
    assert m.validate_submission(legacy, source, annotation)['complete'] is False
    # A clinical transcription read from a sampled frame cites the clip's pseudo-URL and frame index.
    block = target['assets'][0]['embedded_videos'][0]['frame_blocks'][0]
    block.update(clinical_relevance='clinical_data', patient_ids=['p1'], ownership_evidence=OWNER)
    tx = dict(transcription_id='S1-v1', asset_url=clip['url'], asset_sha256=clip['sha256'], page=1, panel=None,
              table_label=None, row_label='TP53', column_label='VAF', kind='clinical_data', text='0.42',
              visual_cue='Overlay text in the first sampled frame')
    target['clinical_data'] = clinical(tx, '0.42')
    report = m.validate_submission(target, source, annotation)
    assert report['complete'] and report['inspected_images'] == 2 and report['sampled_video_frames'] == len(frames)
    save(target)
    (reviewed, receipts), = sign()
    assert receipts[0]['status'] == 'source_and_document_text_and_images_and_sampled_video_frames_reviewed'
    from openpatients2.dense import export as exporter
    row, = exporter.supplement_inventory(source, [(reviewed, receipts)])
    assert row['video_frames_sampled'] is True and exporter.supplement_satisfied(row)
    assert m.target_patient_ids(reviewed) == {'p1'}
    bundle = m.supplemental_clinical_bundle(reviewed, annotation)
    assert any(a.get('part') == clip['part'] for a in bundle['assets'])
    Path(clip['path']).write_bytes(video + b'tail')
    with pytest.raises(ValueError, match='differs from its document part'):
        m.validate_submission(target, source, annotation)


def test_directly_declared_pptx_with_embedded_video_in_the_composite_path(env):
    from openpatients2.dense import supplement_assets as assets
    m, root, build, save, sign = env
    video = mp4_bytes(root.parent)
    source, annotation, _ = build([('clips.pptx', pptx_bytes({'ppt/media/media1.mp4': video}))])
    result = assets.extract_members(root, AID, 'S1', cache=root.parent / 'cache', every=0.5)
    draft = json.loads(Path(result['draft']).read_text())
    part, = draft['assets']
    assert part['kind'] == 'text' and [v['part'] for v in part['embedded_videos']] == ['ppt/media/media1.mp4']
    assert result['assets'][0]['embedded_videos'][0]['frames'] == [f['path'] for f in part['embedded_videos'][0]['sampling']['frames']]
    target = complete_parts(deepcopy(draft))
    omitted = deepcopy(target); omitted['assets'][0]['embedded_videos'] = []
    with pytest.raises(ValueError, match='Embedded video inventory differs'):
        m.validate_submission(omitted, source, annotation)
    report = m.validate_submission(target, source, annotation)
    assert report['complete'] and report['sampled_video_frames'] == len(part['embedded_videos'][0]['sampling']['frames'])
    save(target)
    (reviewed, receipts), = sign()
    assert receipts[0]['status'] == 'source_and_document_text_and_images_and_sampled_video_frames_reviewed'


# ------------------------------------------------------------------ 3-5. ZIP members (images, video, mixed)
def test_zip_image_members_each_need_a_pixel_review(env):
    m, root, build, save, sign = env
    source, annotation, draft = build([('figs.zip', _package({'a.jpg': jpeg_bytes(b'1'), 'b.jpg': jpeg_bytes(b'2'),
                                                              '__MACOSX/._a.jpg': b'junk'}))])
    assert draft['format'] == m.COMPOSITE_FORMAT
    assert [(x['member'], x['kind']) for x in draft['archive']['members']] == [('a.jpg', 'image'), ('b.jpg', 'image')]
    target = complete_parts(deepcopy(draft))
    gap = deepcopy(target); gap['assets'][1]['review'] = None
    with pytest.raises(ValueError, match='image member requires its own pixel inspection'):
        m.validate_submission(gap, source, annotation)
    dropped = deepcopy(target); dropped['assets'].pop()
    with pytest.raises(ValueError, match='every member'):
        m.validate_submission(dropped, source, annotation)
    save(target)
    (_, receipts), = sign()
    assert receipts[0]['status'] == 'source_and_images_reviewed' and 'archive_sha256' in receipts[0]
    # Stale hash: a changed member SHA256 no longer matches the signed receipts.
    target['assets'][0]['sha256'] = '0' * 64; save(target)
    with pytest.raises(ValueError, match='stale'):
        m.reviewed_supplement_targets(root, AID)


def test_zip_video_member_frame_sampled_review(env):
    m, root, build, save, sign = env
    video = mp4_bytes(root.parent)
    source, annotation, draft = build([('video.zip', _package({'Video 1.mp4': video}))])
    part, = draft['assets']
    assert part['kind'] == 'video' and part['continuous_motion_inspected'] is False
    frames = part['sampling']['frames']
    assert len(frames) >= 2 and frames[0]['timestamp_seconds'] == 0.0 and all(Path(f['path']).exists() for f in frames)
    assert any('Sampled frames only' in text for text in part['limitations'])
    target = complete_parts(deepcopy(draft))
    short = deepcopy(target); short['assets'][0]['frame_blocks'][0]['last_frame'] = len(frames) - 1
    with pytest.raises(ValueError, match='sampled frame requires exactly one reviewed block'):
        m.validate_submission(short, source, annotation)
    thinned = deepcopy(target); thinned['assets'][0]['sampling']['frames'].pop(); thinned['assets'][0]['frame_blocks'][0]['last_frame'] -= 1
    with pytest.raises(ValueError, match='fixed-interval timestamps across the whole video'):
        m.validate_submission(thinned, source, annotation)
    vague = deepcopy(target); vague['assets'][0]['limitations'] = ['Audio not inspected.']
    with pytest.raises(ValueError, match='frame-sampling scope'):
        m.validate_submission(vague, source, annotation)
    save(target)
    reviewed = sign()
    from openpatients2.dense import export as exporter
    row, = exporter.supplement_inventory(source, reviewed)
    assert row['review_status'] == 'agent_and_parent_source_and_sampled_video_frames_reviewed'
    assert row['video_frames_sampled'] is True and exporter.supplement_satisfied(row)
    Path(frames[0]['path']).write_bytes(b'changed frame')
    with pytest.raises(ValueError, match='Sampled frame bytes changed'):
        m.validate_submission(target, source, annotation)


def test_mixed_archive_combines_member_rules_and_detects_extensionless_docx(env):
    m, root, build, save, sign = env
    members = {'notes.docx': docx_bytes(image=False), 'Disclosure_DCLee': docx_bytes(), 'form.pdf': pdf_bytes(),
               'photo.jpg': jpeg_bytes(), 'table.xlsx': xlsx_bytes()}
    source, annotation, draft = build([('mixed.zip', _package(members))])
    kinds = {x['member']: (x['kind'], x['detected_type']) for x in draft['archive']['members']}
    assert kinds == {'Disclosure_DCLee': ('text', 'docx'), 'form.pdf': ('pdf', 'pdf'), 'notes.docx': ('text', 'docx'),
                     'photo.jpg': ('image', 'image_jpeg'), 'table.xlsx': ('spreadsheet', 'xlsx')}
    target = complete_parts(deepcopy(draft))
    report = m.validate_submission(target, source, annotation)
    assert report['complete'] and report['inspected_pages'] == 1 and report['inspected_images'] == 2
    assert report['inspected_sheet_rows'] == 3
    # Each member keeps its own coverage rule.
    by_member = {a['member']: i for i, a in enumerate(target['assets'])}
    for member, change, message in [
            ('form.pdf', lambda a: a['pages'].clear(), 'rendered-page inspection'),
            ('notes.docx', lambda a: a['blocks'][0].update(last_unit=1), 'exactly one reviewed block'),
            ('Disclosure_DCLee', lambda a: a['embedded_images'][0].update(review=None), 'embedded image'),
            ('table.xlsx', lambda a: a['sheet_reviews'].pop(), 'Every sheet requires exactly one sheet review')]:
        broken = deepcopy(target); change(broken['assets'][by_member[member]])
        with pytest.raises(ValueError, match=message):
            m.validate_submission(broken, source, annotation)
    save(target)
    (_, receipts), = sign()
    assert receipts[0]['status'] == 'source_and_document_pages_and_document_text_and_images_and_spreadsheet_cells_reviewed'


def test_unsupported_member_keeps_the_archive_partial(env):
    m, root, build, save, sign = env
    source, annotation, draft = build([('odd.zip', _package({'a.jpg': jpeg_bytes(), 'trace.bin': b'\x00\x01binary trace'}))])
    assert [x['kind'] for x in draft['archive']['members']] == ['image', 'unsupported']
    assert any('trace.bin' in text for text in draft['limitations'])
    target = complete_parts(deepcopy(draft))
    with pytest.raises(ValueError, match='Unsupported archive members'):
        m.validate_submission(target, source, annotation)
    target.update(status='partial', limitations=['trace.bin (binary_unknown) cannot be reviewed'])
    assert m.validate_submission(target, source, annotation)['complete'] is False
    save(target)
    with pytest.raises(ValueError, match='cannot receive completed'):
        sign()


# ------------------------------------------------------------------ 6. XLSX
def test_standalone_xlsx_rows_cells_and_staleness(env, tmp_path):
    m, root, build, save, sign = env
    source, annotation, draft = build([('Table_1.xlsx', xlsx_bytes())])
    part, = draft['assets']
    variants, empty = part['extraction']['sheets']
    assert (variants['name'], variants['nonempty_rows'], variants['row_ranges'], variants['max_column']) == ('Variants', 3, [[1, 2], [4, 4]], 'C')
    assert empty['nonempty_rows'] == 0 and part['extraction']['formulas_evaluated'] is False
    dump = [json.loads(line) for line in Path(variants['row_dump']).read_text().splitlines()]
    assert dump[1] == {'row': 2, 'cells': {'A': 'Case 1', 'B': 'TP53', 'C': '0.42'}}
    target = complete_parts(deepcopy(draft))
    block = target['assets'][0]['sheet_reviews'][0]['blocks'][0]
    block.update(clinical_relevance='clinical_data', patient_ids=['p1'], ownership_evidence=OWNER)
    tx = dict(transcription_id='S1-C2', asset_url=part['url'], asset_sha256=part['sha256'], page=1, panel=None,
              table_label='Variants', row_label='Case 1', column_label='VAF', kind='clinical_data', text='0.42',
              visual_cue=None, cell='C2')
    target['clinical_data'] = clinical(tx, '0.42')
    assert m.validate_submission(target, source, annotation)['complete']
    wrong = deepcopy(target); wrong['clinical_data']['transcriptions'][0]['text'] = '0.43'
    wrong['clinical_data']['facts'][0]['evidence'][0]['quote'] = '0.43'
    with pytest.raises(ValueError, match='literal in its parsed cell'):
        m.validate_submission(wrong, source, annotation)
    gap = deepcopy(target); gap['assets'][0]['sheet_reviews'][0]['blocks'][0]['last_row'] = 2
    with pytest.raises(ValueError, match='row 4 is not covered'):
        m.validate_submission(gap, source, annotation)
    summary = deepcopy(target)
    summary['assets'][0]['sheet_reviews'][0]['blocks'][0]['inspection_method'] = 'homogeneous_block_summary'
    with pytest.raises(ValueError, match='summary must state'):
        m.validate_submission(summary, source, annotation)
    unowned = deepcopy(target); unowned['assets'][0]['sheet_reviews'][0]['blocks'][0]['ownership_evidence'] = []
    with pytest.raises(ValueError, match='ownership evidence'):
        m.validate_submission(unowned, source, annotation)
    edited = deepcopy(target); edited['assets'][0]['extraction']['sheets'][0]['nonempty_rows'] = 2
    with pytest.raises(ValueError, match='sheets_sha256'):
        m.validate_submission(edited, source, annotation)
    save(target)
    (reviewed, receipts), = sign()
    assert receipts[0]['status'] == 'source_and_spreadsheet_cells_reviewed'
    from openpatients2.dense import export as exporter
    row, = exporter.supplement_inventory(source, [(reviewed, receipts)])
    assert row['review_status'] == 'agent_and_parent_source_and_spreadsheet_cells_reviewed' and exporter.supplement_satisfied(row)
    bundle = m.supplemental_clinical_bundle(reviewed, annotation)
    assert bundle['patients'][0]['sections']['observations']['items'][0]['numeric_value'] == 0.42
    # Stale hash: different bytes at the cached path cannot be re-signed.
    Path(part['path']).write_bytes(xlsx_bytes((('Patient', 'Gene', 'VAF'), ('Case 1', 'TP53', '0.43'))))
    with pytest.raises(ValueError, match='bytes changed'):
        m.validate_submission(target, source, annotation)


def test_sheet_over_the_hard_cap_is_never_silently_truncated(env, monkeypatch):
    m, root, build, save, sign = env
    monkeypatch.setattr(m, 'SHEET_MAX_ROWS', 2)
    source, annotation, draft = build([('big.xlsx', xlsx_bytes())])
    assert draft['assets'][0]['extraction']['sheets'][0]['truncated'] is True
    target = complete_parts(deepcopy(draft))
    with pytest.raises(ValueError, match='exceeds the parse caps'):
        m.validate_submission(target, source, annotation)
    target.update(status='partial', limitations=['Sheet Variants exceeds the row cap'])
    assert m.validate_submission(target, source, annotation)['complete'] is False


def test_member_kind_sniffs_content_not_names():
    from openpatients2.dense.supplement_review import member_kind
    assert member_kind(docx_bytes(), 'no_extension') == ('text', 'docx')
    assert member_kind(xlsx_bytes(), 'misnamed.docx') == ('spreadsheet', 'xlsx')
    assert member_kind(pptx_bytes(), None) == ('text', 'pptx')
    assert member_kind(png_bytes(), 'x.dat') == ('image', 'image_png')
    assert member_kind(b'plain text', 'notes.txt')[0] == 'unsupported'
