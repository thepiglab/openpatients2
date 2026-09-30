import copy
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
import sys

import httpx
import pytest

from openpatients2.article_tasks import patient_packet
from openpatients2.data import normalize
from openpatients2.ehr_seeds import seed_patient
from openpatients2.figure_attribution import bind_figure_review
from openpatients2.literature_client import LiteratureClient, LiteratureConfig
from openpatients2.prompts import messages_for
from openpatients2.source_views import (official_text_view, validate_view, choose_view,
    firecrawl_pdf_view, download_asset)
from test_articles import article, roster
from test_attribution import fixture_review


def txt(body='Case 1\n\nA woman received 5 mg.\n\nCase 2\n\nA dog received 2 mg.'):
    return ('ARTICLE INFORMATION\nPMCID: PMC1\nArticle version: 1\n'
            'License URL: https://creativecommons.org/licenses/by-nc-sa/4.0/\n'
            '\x9f==============================\x9f\n'+body).encode()


def test_official_text_keeps_layout_and_late_material_and_removes_only_verified_references():
    a=article()
    citation='1 Author A. A very long prior case citation with a journal and publication year 2020.'
    a['references']=[{'citation_text':citation}]
    data=txt('Results\n\nPatient\tDay 1\tDay 2\nCase 1\t143\t122\n\nReferences\n\n'+citation+
             '\nA citation we cannot match\n\nFigure 2\n\nLate caption for Case 2.')
    view=official_text_view(a,data)
    combined='\n'.join(s['text'] for s in view['segments'])
    assert 'Patient\tDay 1\tDay 2\nCase 1\t143\t122' in combined
    assert citation not in combined and 'Late caption for Case 2.' in combined
    assert 'A citation we cannot match' in combined
    assert validate_view(a,view)==view
    for s in view['segments']:
        lo,hi=s['asset_span'];assert data.decode()[lo:hi]==s['text']


@pytest.mark.parametrize('change', ['id','version','license','boundary'])
def test_text_rejects_wrong_identity_rights_or_unknown_body_boundary(change):
    raw=txt()
    raw=raw.replace(*{'id':(b'PMC1',b'PMC2'),'version':(b'version: 1',b'version: 2'),
        'license':(b'by-nc-sa',b'by-nc-nd'),'boundary':(b'==============================',b'unknown')}[change])
    with pytest.raises(ValueError):official_text_view(article(),raw)


def test_view_tampering_and_cross_article_reuse_rejected():
    a=article();view=official_text_view(a,txt())
    view['segments'][0]['text']='forged'
    with pytest.raises(ValueError,match='integrity'):validate_view(a,view)
    view=official_text_view(a,txt());view['article_id']='PMC2.1'
    with pytest.raises(ValueError,match='another'):validate_view(a,view)


def test_txt_patient_bundle_keeps_jats_figures_and_roundtrips_original_hash():
    a=article();r=roster(a);a['source_views']={'pmc_text':official_text_view(a,txt())}
    reviews=[bind_figure_review(fixture_review(a),a,r,method='test')]
    record=patient_packet(a,r,r['patients'][0],clinical_source='pmc_text',figure_reviews=reviews)
    assert normalize(record)==record
    assert record['clinical_source_selection']['selected']=='pmc_text'
    assert record['packet_spans'][0]['segment_id'].startswith('txt-')
    assert record['figure_assignments'][0]['evidence'][0]['segment_id']=='b00006'
    assert record['multimedia']['figures'][0]['image_urls']==a['figures'][0]['image_urls']
    prompt=messages_for(record,'medications')[-1]['content']
    assert 'identity_evidence' not in prompt and 'txt-00001' in prompt
    with pytest.raises(ValueError,match='whole_article'):
        patient_packet(a,r,r['patients'][0],clinical_source='pmc_text',scope='localized')


def test_canonical_companion_survives_txt_ehr_export():
    a=article();r=roster(a);a['source_views']={'pmc_text':official_text_view(a,txt())}
    record=patient_packet(a,r,r['patients'][0],clinical_source='pmc_text',figure_reviews=[])
    patient={'source':record,'sections':{},'model':{'model_id':'test'},'complete_for_scope':False,
             'companions':{'summary':{'status':'valid','data':{'claims':[
                 {'text':'A woman received 5 mg.','evidence':[{'segment_id':'b00002','quote':'A woman received 5 mg.'}]}],
                 'limitations':[]}}}}
    seed=seed_patient(patient)
    assert seed['summary']['claims'][0]['evidence'][0]['segment_id']=='b00002'
    assert seed['clinical_source_provenance']['kind']=='pmc_text'
    assert seed['companion_source']=='canonical_jats'
    assert seed['quality']['source_coverage_complete'] is False
    record['canonical_evidence']['segments'][0]['text']='tampered canonical text'
    with pytest.raises(ValueError,match='digest mismatch'):seed_patient(patient)


def test_missing_txt_falls_back_explicitly():
    a=article();r=roster(a)
    record=patient_packet(a,r,r['patients'][0],clinical_source='pmc_text')
    assert record['clinical_source_selection']['reason']=='requested_view_unavailable'
    assert record['clinical_source_selection']['selected']=='jats'


def test_native_pdf_temp_cleanup_no_ocr_and_no_automatic_layout_acceptance(monkeypatch):
    paths=[]
    def detect(path):return SimpleNamespace(page_count=2)
    def extract(path):
        paths.append(Path(path))
        assert Path(path).exists()
        return SimpleNamespace(pages=[SimpleNamespace(page=0,markdown='| Patient | Result |',needs_ocr=False,ocr_reason=None),
            SimpleNamespace(page=1,markdown='A woman received 5 mg.',needs_ocr=False,ocr_reason=None)],
            pages_with_tables=[1],pages_with_columns=[],is_complex=True)
    monkeypatch.setitem(sys.modules,'pdf_inspector',SimpleNamespace(detect_pdf=detect,extract_pages_markdown=extract))
    monkeypatch.setattr('openpatients2.source_views.importlib.metadata.version',lambda _: '1.25.2')
    a=article();view=firecrawl_pdf_view(a,b'%PDF-1.7\nfixture')
    assert not any(p.exists() for p in paths)
    assert view['segments'][0]['page']==1 and view['parser']['ocr_used'] is False
    assert len(view['review_queue'])==2
    a['source_views']={'pdf_firecrawl':view}
    assert choose_view(a,'pdf_firecrawl')[1]['selected']=='jats'
    assert choose_view(a,'pdf_firecrawl',allow_pdf_review=True)[0]==view
    with pytest.raises(ValueError,match='page cap'):
        firecrawl_pdf_view(a,b'%PDF-1.7\nfixture',max_pages=1)


async def test_asset_downloader_checks_version_digest_and_stream_caps(tmp_path):
    a=article();data=txt();md5=hashlib.md5(data).hexdigest()
    a['metadata']['text_url']=f'https://pmc-oa-opendata.s3.amazonaws.com/PMC1.1/PMC1.1.txt?md5={md5}'
    requested=[]
    async def handle(request):
        requested.append(str(request.url));return httpx.Response(200,content=data)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as http:
        client=LiteratureClient(LiteratureConfig(cache=str(tmp_path/'cache'),cloud_requests_per_second=50),http)
        try:
            downloaded,info=await download_asset(client,a,'pmc_text',10000)
            assert downloaded==data and info['manifest_md5_verified']
            with pytest.raises(ValueError,match='byte cap'):await download_asset(client,a,'pmc_text',10)
            a['metadata']['text_url']=a['metadata']['text_url'].replace(md5,'0'*32)
            with pytest.raises(ValueError,match='MD5'):await download_asset(client,a,'pmc_text',10000)
            a['metadata']['text_url']=a['metadata']['text_url'].replace('PMC1.1','PMC2.1')
            before=len(requested)
            with pytest.raises(ValueError,match='version'):await download_asset(client,a,'pmc_text',10000)
            assert len(requested)==before
        finally:await client.close()


@pytest.mark.parametrize('mode',['available','unavailable','conflicting_license'])
async def test_acquisition_attaches_optional_views_but_quarantines_license_conflicts(tmp_path,mode):
    from openpatients2.corpus import fetch_article
    from test_articles import XML
    from unittest.mock import AsyncMock
    a=article()
    data=txt()
    if mode=='conflicting_license':data=data.replace(b'by-nc-sa',b'by-nc-nd')
    a['metadata']['text_url']='https://pmc-oa-opendata.s3.amazonaws.com/PMC1.1/PMC1.1.txt'
    async def handle(request):return httpx.Response(404) if mode=='unavailable' else httpx.Response(200,content=data)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as http:
        client=LiteratureClient(LiteratureConfig(cache=str(tmp_path/'cache')),http)
        client._version_objects=AsyncMock(return_value=[(1,'https://pmc-oa-opendata.s3.amazonaws.com/PMC1.1/metadata.json')])
        async def text(url,kind):return (json.dumps(a['metadata']) if kind=='metadata' else XML),{'url':url}
        client._text=text
        try:
            result=await fetch_article(client,'PMC1',source_views=('pmc_text',))
            if mode=='conflicting_license':
                assert result['status']=='license_rejected' and not result['license']['allowed']
            else:
                assert result['status']=='eligible'
                if mode=='available':assert result['source_views']['pmc_text']['segments']
                else:assert result['source_view_failures'][0]['kind']=='pmc_text'
        finally:await client.close()
