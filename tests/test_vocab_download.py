import io
import zipfile
import yaml
import httpx
import pytest
from openpatients2.vocab_download import download_vocabularies


def setup_config(tmp_path,members=None):
    p=tmp_path/'config.yaml'
    p.write_text(yaml.safe_dump({'output':str(tmp_path/'out'),'max_download_bytes':10000,'max_unpacked_bytes':10000,
        'downloads':[{'kind':'icd10cm','version':'fixture','url':'https://example.org/release.zip',
                      'members':members or {'*tabular*.xml':'tabular.xml'}}]}))
    return p


def test_dry_run_does_not_fetch(tmp_path):
    p=setup_config(tmp_path)
    assert download_vocabularies(str(p))['dry_run']
    assert not (tmp_path/'out').exists()


def test_bounded_download_and_selected_member(tmp_path):
    p=setup_config(tmp_path);buf=io.BytesIO()
    with zipfile.ZipFile(buf,'w') as z:
        z.writestr('release/tabular_fixture.xml','<fixture/>')
        z.writestr('unused.txt','do not retain')
    with httpx.Client(transport=httpx.MockTransport(lambda req:httpx.Response(200,content=buf.getvalue()))) as c:
        result=download_vocabularies(str(p),True,http=c)
    assert (tmp_path/'out/tabular.xml').read_text()=='<fixture/>'
    assert not (tmp_path/'out/unused.txt').exists()
    assert not result['archives_retained']


def test_output_cannot_escape(tmp_path):
    p=setup_config(tmp_path,{'tabular.xml':'../escaped.xml'})
    with pytest.raises(ValueError,match='escapes'):download_vocabularies(str(p))


def test_duplicate_archive_members_are_ambiguous(tmp_path):
    p=setup_config(tmp_path);buf=io.BytesIO()
    with zipfile.ZipFile(buf,'w') as z:
        z.writestr('a/tabular.xml','a');z.writestr('b/tabular.xml','b')
    with httpx.Client(transport=httpx.MockTransport(lambda req:httpx.Response(200,content=buf.getvalue()))) as c:
        with pytest.raises(ValueError,match='exactly one'):download_vocabularies(str(p),True,http=c)
