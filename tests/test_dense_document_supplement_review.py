"""PDF supplements require actual page coverage and independent bound receipts."""
from copy import deepcopy
import hashlib
import importlib.util
import json
from pathlib import Path
import shutil

import pytest

ROOT = Path(__file__).resolve().parents[1]
AID = 'PMC-test.1'


def pdf_bytes(count=2):
    """Small valid PDF fixture, with real page objects and a cross-reference table."""
    objects = [b'<< /Type /Catalog /Pages 2 0 R >>',
               ('<< /Type /Pages /Count %d /Kids [%s] >>' %
                (count, ' '.join(f'{n+3} 0 R' for n in range(count)))).encode()]
    objects += [b'<< /Type /Page /Parent 2 0 R /MediaBox [0 0 100 100] /Resources << >> >>'] * count
    data = bytearray(b'%PDF-1.4\n'); offsets = []
    for n, obj in enumerate(objects, 1):
        offsets.append(len(data)); data.extend(f'{n} 0 obj\n'.encode() + obj + b'\nendobj\n')
    start = len(data)
    data.extend(f'xref\n0 {len(objects)+1}\n0000000000 65535 f \n'.encode())
    for offset in offsets: data.extend(f'{offset:010} 00000 n \n'.encode())
    data.extend(f'trailer\n<< /Size {len(objects)+1} /Root 1 0 R >>\nstartxref\n{start}\n%%EOF\n'.encode())
    return bytes(data)


@pytest.fixture
def document(tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(ROOT / 'benchmarks/dense500'))
    spec = importlib.util.spec_from_file_location('dense_document_review', ROOT / 'benchmarks/dense500/supplement_review.py')
    m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
    # Every provenance test still checks bytes; actual pdfinfo is exercised below.
    monkeypatch.setattr(m, 'pdf_page_count', lambda path: 2)
    data = pdf_bytes(); path = tmp_path / 'document.pdf'; path.write_bytes(data)
    original = dict(url='https://example.invalid/document.pdf?original=1', filename='document.pdf',
                    original_manifest_url='s3://original/document.pdf', md5=hashlib.md5(data).hexdigest(), kind='supplementary_or_other')
    source = dict(article_id=AID, text_sha256='source-hash', figures=[], segments=[], license={'code':'CC BY'},
                  supplements=[dict(supplement_id='SM1', assets=[original], content_inspected=False)])
    annotation = dict(article_id=AID, text_sha256='source-hash', patients=[dict(patient_id='p1', label='Index patient',
                      species='human', identity_evidence=[])])
    page = dict(page=1, status='inspected', inspection_method='rendered_page', contents='Synthetic administrative page.',
                clinical_relevance='administrative', clinical_relevance_notes='No clinical measurements.', patient_ids=[],
                rights_checked=True, rights_basis='page_exception', rights_exceptions=[dict(notice='CC BY-NC-ND',
                interpretation='Separate printed page terms; source license is not blanket asset licensing.')], limitations=[])
    target = dict(format=m.DOCUMENT_FORMAT, article_id=AID, source_text_sha256='source-hash', supplement_id='SM1',
                  status='inspected', document_classification='administrative_checklist', contents_summary='Two synthetic checklist pages.',
                  assets=[{k:original[k] for k in ('url','filename','original_manifest_url','md5')} | dict(
                      sha256=hashlib.sha256(data).hexdigest(), bytes=len(data), path=str(path), source_license=source['license'],
                      media_type='application/pdf', page_count=2, pages=[page, dict(deepcopy(page), page=2)])], limitations=[])
    for folder in ('sources','annotations','document-submissions'): (tmp_path / folder).mkdir()
    def save():
        for folder,value,filename in [('sources',source,f'{AID}.json'),('annotations',annotation,f'{AID}.json'),
                                       ('document-submissions',target,f'{AID}.supplements.SM1.json')]:
            (tmp_path / folder / filename).write_text(json.dumps(value))
    save()
    return m,tmp_path,source,annotation,target,save


def sign_both(document):
    m,root,*_ = document
    for role in ('agent','parent'):
        m.sign_review(root,AID,'SM1',role=role,reviewer='Claude synthetic '+role,notes='Synthetic test; not an actual clinical review.')


def test_documents_have_page_review_not_image_analysis(document):
    m,root,s,a,t,_ = document
    report = m.validate_submission(t,s,a)
    assert report['complete'] and report['inspected_pages'] == 2
    assert 'pixel_accuracy_measured' not in report and 'analysis' not in t
    assert m.reviewed_supplement_targets(root,AID) == []
    m.sign_review(root,AID,'SM1',role='agent',reviewer='Claude synthetic test agent',notes='Synthetic fixture inspection notes for tests')
    assert m.reviewed_supplement_targets(root,AID) == []
    m.sign_review(root,AID,'SM1',role='parent',reviewer='Claude synthetic test parent',notes='Synthetic fixture inspection notes for tests')
    reviewed = m.reviewed_supplement_targets(root,AID)
    assert reviewed[0][0] == t
    assert all(r['status']=='source_and_document_pages_reviewed' for r in reviewed[0][1])
    assert s['supplements'][0]['content_inspected'] is False


@pytest.mark.parametrize('change',['missing_page','duplicate_page','wrong_page_count','unknown_patient','rights','license','manifest','md5','bytes','sha256','image_analysis','administrative_clinical'])
def test_document_rejects_missing_pages_stale_provenance_and_misclassification(document,change):
    m,_,s,a,t,_ = document; asset=t['assets'][0]
    if change=='missing_page': asset['pages'].pop()
    elif change=='duplicate_page': asset['pages'][1]['page']=1
    elif change=='wrong_page_count':
        asset['page_count']=1; asset['pages'].pop()
    elif change=='unknown_patient': asset['pages'][0]['patient_ids']=['p99']
    elif change=='rights': asset['pages'][0]['rights_exceptions']=[]
    elif change=='license': asset['source_license']={}
    elif change=='manifest': asset['original_manifest_url']='changed'
    elif change=='md5': asset['md5']='0'*32
    elif change=='bytes': asset['bytes']+=1
    elif change=='sha256': asset['sha256']='0'*64
    elif change=='image_analysis': t['analysis']={}
    else: asset['pages'][0]['clinical_relevance']='clinical_data'
    with pytest.raises(ValueError): m.validate_submission(t,s,a)


@pytest.mark.parametrize('change',['source','source_supplement','target_page','patient_identity'])
def test_document_receipts_bind_every_content_and_identity_change(document,change):
    m,r,s,a,t,save = document; sign_both(document)
    if change=='source': s['text_sha256']='changed'
    elif change=='source_supplement': s['supplements'][0]['description']='changed'
    elif change=='target_page': t['assets'][0]['pages'][0]['contents']='changed'
    else: a['patients'][0]['label']='changed'
    save()
    with pytest.raises(ValueError,match='stale|source'): m.reviewed_supplement_targets(r,AID)


@pytest.mark.parametrize('status',['partial','unavailable'])
def test_document_partial_and_unavailable_never_complete(document,status):
    m,r,s,a,t,save = document; t.update(status=status,assets=[],limitations=['Page inspection incomplete']); save()
    assert m.validate_submission(t,s,a)['complete'] is False
    with pytest.raises(ValueError,match='cannot receive completed'):
        m.sign_review(r,AID,'SM1',role='agent',reviewer='Claude fixture reviewer',notes='Synthetic fixture inspection notes for tests')


def test_document_same_reviewer_cannot_supply_independent_receipts(document):
    m,r,*_ = document
    m.sign_review(r,AID,'SM1',role='agent',reviewer='Claude same reviewer',notes='Synthetic fixture inspection notes for tests')
    with pytest.raises(ValueError,match='independent'):
        m.sign_review(r,AID,'SM1',role='parent',reviewer='Claude same reviewer',notes='Synthetic fixture inspection notes for tests')


def test_reviewed_document_survives_cache_eviction_but_cannot_resign(document):
    m,r,_,_,t,_ = document; sign_both(document)
    Path(t['assets'][0]['path']).unlink()
    assert len(m.reviewed_supplement_targets(r,AID))==1
    with pytest.raises(FileNotFoundError): m.sign_review(r,AID,'SM1',role='agent',reviewer='Claude fixture reviewer',notes='Synthetic fixture inspection notes for tests')


def test_pdf_page_count_uses_actual_document(tmp_path,monkeypatch):
    if not shutil.which('pdfinfo'): pytest.skip('Actual PDF validation requires pdfinfo')
    monkeypatch.syspath_prepend(str(ROOT / 'benchmarks/dense500'))
    import supplement_review
    path=tmp_path/'actual.pdf'; path.write_bytes(pdf_bytes(3))
    assert supplement_review.pdf_page_count(path)==3
    path.write_bytes(b'not a PDF')
    with pytest.raises(ValueError,match='not a PDF'): supplement_review.pdf_page_count(path)
