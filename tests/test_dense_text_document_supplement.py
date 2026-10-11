"""DOCX (or DOCX-in-ZIP) supplements reviewed as literal extracted units: drafting, review, clinical payload,
receipts, supplement gate and runtime transcriptions (PMC12798534.1 shape)."""
from copy import deepcopy
import hashlib
import io
import json
from pathlib import Path
import sys
import zipfile

import pytest

ROOT = Path(__file__).resolve().parents[1]
from openpatients2.dense_store import load as load_store
store = load_store()
AID = 'PMC-text-doc-test.1'
ZIP_URL = 'https://pmc-oa-opendata.s3.amazonaws.com/PMC-text-doc-test.1/supp.zip?md5=x'
MEMBER = 'Supplementary Table S1.docx'

pytest.importorskip('anydoc')


def docx_bytes():
    def p(text):
        return f'<w:p><w:r><w:t xml:space="preserve">{text}</w:t></w:r></w:p>'
    def row(*cells):
        return '<w:tr>' + ''.join(f'<w:tc>{p(c)}</w:tc>' for c in cells) + '</w:tr>'
    body = (p('Supplementary Table S1. Infection incidence before and after dual biologic use')
            + '<w:tbl>' + row('Case', 'Infection after', 'Antibiotic use') + row('1', 'acute cystitis', 'yes')
            + row('2', 'pneumonia', 'yes (hospitalized)') + '</w:tbl>')
    document = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?><w:document '
                'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body>' + body + '</w:body></w:document>')
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w') as z:
        z.writestr('[Content_Types].xml', '<?xml version="1.0" encoding="UTF-8"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
                   '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
                   '<Default Extension="xml" ContentType="application/xml"/><Override PartName="/word/document.xml" '
                   'ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/></Types>')
        z.writestr('_rels/.rels', '<?xml version="1.0" encoding="UTF-8"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                   '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" '
                   'Target="word/document.xml"/></Relationships>')
        z.writestr('word/document.xml', document)
    return buffer.getvalue()


@pytest.fixture
def setup(tmp_path, monkeypatch):
    root = tmp_path / 'root'; root.mkdir()
    monkeypatch.syspath_prepend(str(ROOT / 'benchmarks/dense500'))
    for name in ('supplement_review', 'supplement_assets', 'sidecar_receipts', 'measurement_series_review'):
        sys.modules.pop(name, None)
    import supplement_assets, supplement_review as m
    member = docx_bytes()
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w') as z:
        z.writestr(MEMBER, member)
    data = buffer.getvalue()
    zip_path = tmp_path / 'cache' / 'supp.zip'; zip_path.parent.mkdir(); zip_path.write_bytes(data)
    original = dict(url=ZIP_URL, filename='supp.zip', original_manifest_url='s3://x/supp.zip',
                    md5=hashlib.md5(data).hexdigest(), kind='supplementary_or_other')
    source = dict(article_id=AID, text_sha256='source-sha', license={'code': 'CC BY'}, figures=[],
                  segments=[dict(segment_id='b1', kind='paragraph', text='Cases 1 and 2 are listed in Supplementary Table S1.')],
                  supplements=[dict(supplement_id='sup1', assets=[original])])
    annotation = dict(article_id=AID, text_sha256='source-sha', patients=[
        dict(patient_id=p, label=f'Case {p[1]}', species='human', identity_evidence=[], facts=[]) for p in ('p1', 'p2')])
    for folder, obj in (('sources', source), ('annotations', annotation)):
        store.path(root, folder, AID, mkdir=True).write_text(json.dumps(obj))
    store.path(root, 'supplement-assets', AID, mkdir=True).write_text(json.dumps({'files': [dict(
        supplement_id='sup1', url=ZIP_URL, filename='supp.zip', original_manifest_url='s3://x/supp.zip',
        declared_md5=original['md5'], bytes=len(data), sha256=hashlib.sha256(data).hexdigest(), path=str(zip_path),
        download_status='downloaded', detected_type='zip')]}))
    result = supplement_assets.extract_text_documents(root, AID, 'sup1', cache=tmp_path / 'cache2')
    draft = json.loads(Path(result['draft']).read_text())
    return m, root, source, annotation, draft


def complete(draft):
    target = deepcopy(draft)
    asset = target['assets'][0]
    units = {u['unit']: u for u in asset['units']}
    block = lambda a, b, rel, pids: dict(first_unit=a, last_unit=b, status='inspected', inspection_method='extracted_text',
                                         contents='Synthetic', clinical_relevance=rel, clinical_relevance_notes='Synthetic',
                                         patient_ids=pids, rights_checked=True, rights_basis='source_license',
                                         rights_exceptions=[], limitations=[])
    asset['blocks'] = [block(1, 2, 'background', []), block(3, 4, 'clinical_data', ['p1', 'p2'])]
    target.update(status='inspected', document_classification='clinical_data', contents_summary='Per-case infections',
                  limitations=[])
    from openpatients2.document_text import unit_transcription_id
    def tx(unit, text):
        assert text in units[unit]['text']
        return dict(transcription_id=unit_transcription_id(0, unit), asset_url=asset['url'], asset_sha256=asset['sha256'],
                    page=unit, panel=None, table_label='Table S1', row_label=units[unit]['cells'][0], column_label=None,
                    kind='clinical_data', text=units[unit]['text'], visual_cue=None)
    fields = lambda name: dict(subject='index_patient', assertion='present', temporality='unknown',
                               time=dict(text=None, relation='unknown', anchor=None, date_iso=None), name=name,
                               verification_status='confirmed', clinical_status='unknown', role='complication',
                               severity=None, diagnostic_basis=None, body_site=None, laterality='unknown')
    def fact(fid, unit, pid, name, owner_quote):
        return dict(fact_id=fid, task='conditions', collection='items', fields=fields(name),
                    evidence=[dict(transcription_id=unit_transcription_id(0, unit), quote=name)], ownership='individual',
                    patient_ids=[pid], ownership_evidence=[dict(basis='supplement', transcription_id=unit_transcription_id(0, unit),
                    segment_id=None, quote=owner_quote)], certainty='supported', scoring='include', notes='Synthetic')
    target['clinical_data'] = dict(format='dense-supplemental-clinical-reference/1', coverage='complete',
                                   transcriptions=[tx(3, 'acute cystitis'), tx(4, 'pneumonia')],
                                   facts=[fact('S1-f1', 3, 'p1', 'acute cystitis', '1 |'), fact('S1-f2', 4, 'p2', 'pneumonia', '2 |')],
                                   limitations=[])
    return target


def test_draft_extracts_literal_units_from_the_zip_member(setup):
    m, root, source, annotation, draft = setup
    assert draft['format'] == m.TEXT_FORMAT and draft['archive']['members'] == [MEMBER]
    asset = draft['assets'][0]
    assert asset['member'] == MEMBER and asset['extraction']['literal_verification'] == 'verified_against_docx_xml'
    assert [u['kind'] for u in asset['units']] == ['paragraph', 'table_row', 'table_row', 'table_row']
    assert asset['units'][2]['text'] == '1 | acute cystitis | yes'
    report = m.validate_submission(draft, source, annotation)            # skeleton validates as partial
    assert report['complete'] is False


def test_complete_review_signs_and_satisfies_the_supplement_gate(setup):
    m, root, source, annotation, draft = setup
    target = complete(draft)
    report = m.validate_submission(target, source, annotation)
    assert report['complete'] and report['inspected_units'] == 4
    store.path(root, 'document-submissions', AID, sid='sup1', mkdir=True).write_text(json.dumps(target))
    m.sign_review(root, AID, 'sup1', role='agent', reviewer='Claude synthetic test agent', notes='Read every extracted unit of the DOCX table')
    m.sign_review(root, AID, 'sup1', role='parent', reviewer='Claude synthetic test parent', notes='Independently read all units and facts')
    reviewed = m.reviewed_supplement_targets(root, AID)
    assert len(reviewed) == 1 and reviewed[0][1][0]['status'] == 'source_and_document_text_reviewed'
    sys.path.insert(0, str(ROOT / 'benchmarks/dense500'))
    import importlib.util
    spec = importlib.util.spec_from_file_location('text_doc_export', ROOT / 'benchmarks/dense500/export.py')
    exporter = importlib.util.module_from_spec(spec); spec.loader.exec_module(exporter)
    inventory = exporter.supplement_inventory(source, reviewed)
    assert exporter.supplement_satisfied(inventory[0])
    assert inventory[0]['review_status'] == 'agent_and_parent_source_and_document_text_reviewed'
    # The runtime pipeline reads the same extracted text (units + transcriptions) under the same ids.
    from openpatients2.ehr_tasks import load_transcriptions
    texts = load_transcriptions(root, AID)
    assert texts[('sup1', 'A1-U0003')] == '1 | acute cystitis | yes' and ('sup1', 'A1-U0001') in texts
    from openpatients2.unified_record import build_supplements
    built = build_supplements(source, reviewed=reviewed)[0]
    assert built['document_review']['assets'][0]['blocks'] and built['patient_ids'] == ['p1', 'p2']


def test_edited_units_nonliteral_quotes_and_missing_coverage_are_rejected(setup):
    m, root, source, annotation, draft = setup
    target = complete(draft)
    edited = deepcopy(target); edited['assets'][0]['units'][2]['text'] = '1 | acute pyelonephritis | yes'
    with pytest.raises(ValueError, match='units_sha256|Re-extraction'):
        m.validate_submission(edited, source, annotation)
    quote = deepcopy(target); quote['clinical_data']['transcriptions'][0]['text'] = 'acute pyelonephritis'
    with pytest.raises(ValueError, match='literal in its extracted unit'):
        m.validate_submission(quote, source, annotation)
    gap = deepcopy(target); gap['assets'][0]['blocks'] = gap['assets'][0]['blocks'][1:]
    with pytest.raises(ValueError, match='exactly one reviewed block'):
        m.validate_submission(gap, source, annotation)
    owner = deepcopy(target); owner['assets'][0]['blocks'][1]['patient_ids'] = ['p1']
    with pytest.raises(ValueError, match='text-block attribution'):
        m.validate_submission(owner, source, annotation)


def test_directly_declared_docx(tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(ROOT / 'benchmarks/dense500'))
    sys.modules.pop('supplement_review', None)
    import supplement_review as m
    data = docx_bytes(); path = tmp_path / 'table.docx'; path.write_bytes(data)
    url = 'https://pmc-oa-opendata.s3.amazonaws.com/X.1/table.docx?md5=y'
    asset = dict(url=url, filename='table.docx', original_manifest_url='s3://x/table.docx', md5=hashlib.md5(data).hexdigest())
    source = dict(article_id=AID, text_sha256='source-sha', license={'code': 'CC BY'}, figures=[], segments=[],
                  supplements=[dict(supplement_id='sup1', assets=[asset])])
    annotation = dict(article_id=AID, text_sha256='source-sha', patients=[dict(patient_id='p1', facts=[])])
    draft = m.draft_text_document(source, 'sup1', [(asset, path, None)])
    assert draft['assets'][0]['url'] == url and draft['assets'][0]['member'] is None
    target = deepcopy(draft)
    target['assets'][0]['blocks'] = [dict(first_unit=1, last_unit=4, status='inspected', inspection_method='extracted_text',
        contents='Synthetic', clinical_relevance='background', clinical_relevance_notes='Synthetic', patient_ids=[],
        rights_checked=True, rights_basis='source_license', rights_exceptions=[], limitations=[])]
    target.update(status='inspected', contents_summary='Synthetic table', limitations=[])
    assert m.validate_submission(target, source, annotation)['complete']
