import base64
from copy import deepcopy
import gzip
import hashlib
import json

import httpx
import pytest

from openpatients2.license_policy import license_decision
from openpatients2.pilot_media import load_asset, prepare_media, repair_visual_citations


PNG = b'\x89PNG\r\n\x1a\nsmall-image'


def article(number=1, figures=1):
    aid = f'PMC{number}.1'
    license = license_decision({'license_code': 'CC BY 4.0', 'is_retracted': False, 'is_pmc_openaccess': True}, [])
    result = {'article_id': aid, 'license': license, 'figures': []}
    for index in range(figures):
        md5 = hashlib.md5(PNG).hexdigest()
        url = f'https://pmc-oa-opendata.s3.amazonaws.com/{aid}/figure{index}.png?md5={md5}'
        result['figures'].append({'figure_key': f'F{index}', 'image_urls': [url], 'rights_statements': [],
            'media': [{'url': url, 'kind': 'image', 'availability': 'listed_in_pmc_metadata', 'md5': md5}]})
    return result


def source(tmp_path, rows, compressed=False):
    path = tmp_path / ('sample.jsonl.gz' if compressed else 'sample.jsonl')
    data = ''.join(json.dumps(row)+'\n' for row in rows).encode()
    path.write_bytes(gzip.compress(data) if compressed else data)
    return path


async def test_preparation_balances_articles_and_loads_frozen_gzip_sample(tmp_path):
    path = source(tmp_path, [article(1, 3), article(2, 2)], compressed=True)
    requests = []
    def handler(request):
        requests.append(request)
        return httpx.Response(200, content=PNG, headers={'content-type': 'image/png'})
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        report = await prepare_media(path, tmp_path/'out', max_figures=3, http=client)
        with pytest.raises(ValueError, match='Existing'):
            await prepare_media(path, tmp_path/'out', http=client)
    assert report['complete'] and report['status_counts'] == {'ready': 3}
    assert [r['article_id'] for r in report['figures']] == ['PMC1.1', 'PMC2.1', 'PMC1.1']
    assert len(requests) == 3 and all(r.headers['accept-encoding'] == 'identity' for r in requests)
    assert report['bytes'] == len(PNG)  # Identical pixel assets deduplicate durably.
    assert report['response_bytes'] == 3*len(PNG)
    assert base64.b64decode(load_asset(tmp_path/'out', report['figures'][0]).split(',')[1]) == PNG
    asset = tmp_path/'out'/report['figures'][0]['file']
    asset.write_bytes(PNG[:-1]+b'x')
    with pytest.raises(ValueError, match='digest'): load_asset(tmp_path/'out', report['figures'][0])


async def test_rights_conversion_missing_and_unlisted_never_download(tmp_path):
    rows = [article(i) for i in range(1, 7)]
    rows[0]['license']['allowed'] = False
    rows[1]['figures'][0]['rights_statements'] = ['Third-party copyright']
    rows[2]['figures'][0]['image_urls'] = ['https://pmc-oa-opendata.s3.amazonaws.com/PMC3.1/f.tif']
    rows[3]['figures'][0]['image_urls'] = []
    rows[4]['figures'][0]['media'] = []
    rows[5]['figures'][0]['image_urls'] = ['https://example.org/a.png']
    def handler(request): raise AssertionError('Skipped assets must never be requested')
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        report = await prepare_media(source(tmp_path, rows), tmp_path/'out', http=client)
    assert [r['status'] for r in report['figures']] == [
        'article_rights_review', 'asset_rights_review', 'conversion_required', 'missing_image', 'fetch_failed', 'fetch_failed']
    assert report['bytes'] == report['response_bytes'] == 0


@pytest.mark.parametrize('body,headers,expected', [
    (PNG[:-1]+b'x', {}, 'MD5'),
    (b'<html>not pixels</html>', {}, 'magic'),
    (PNG, {'content-type': 'image/jpeg'}, 'MIME'),
    (gzip.compress(PNG), {'content-encoding': 'gzip'}, 'Encoded'),
    (PNG, {'content-length': '999999'}, 'byte cap'),
])
async def test_integrity_and_response_caps_fail_without_persisted_payload(tmp_path, body, headers, expected):
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(200, content=body, headers=headers))) as client:
        result = await prepare_media(source(tmp_path, [article()]), tmp_path/'out', max_image_bytes=128, http=client)
    assert result['figures'][0]['status'] == 'fetch_failed'
    assert expected in result['figures'][0]['error']
    assert result['bytes'] == 0 and not list((tmp_path/'out'/'vision-assets').glob('*.image'))


async def test_total_byte_cap_includes_failed_integrity_and_blocks_next_request(tmp_path):
    calls = []
    def handler(request):
        calls.append(request)
        return httpx.Response(200, content=PNG[:-1]+b'x')
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        r = await prepare_media(source(tmp_path, [article(1), article(2)]), tmp_path/'out', max_total_bytes=len(PNG), http=client)
    assert len(calls) == 1 and r['response_bytes'] == len(PNG)
    assert [a['status'] for a in r['figures']] == ['fetch_failed', 'total_byte_cap_exhausted']


async def test_image_byte_cap_and_redirect_are_refused(tmp_path):
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(200, content=PNG))) as client:
        result = await prepare_media(source(tmp_path, [article()]), tmp_path/'cap', max_image_bytes=3, http=client)
    assert result['figures'][0]['status'] == 'fetch_failed' and result['bytes'] == 0
    calls = []
    def redirect(request):
        calls.append(request)
        return httpx.Response(302, headers={'location': 'https://example.org/secret'})
    async with httpx.AsyncClient(transport=httpx.MockTransport(redirect), follow_redirects=True) as client:
        result = await prepare_media(tmp_path/'sample.jsonl', tmp_path/'redirect', http=client)
    assert len(calls) == 1 and result['figures'][0]['status'] == 'fetch_failed'


def test_caption_newline_recovery_preserves_raw_semantics_and_unicode_offsets():
    segments = [{'segment_id': 's1', 'text': 'α preface: Fig. 3\nStained blood films. a). cat; b). canine.'}]
    candidate = {'patient_ids': ['p1'], 'evidence': [{'segment_id': 's1', 'quote': 'Fig. 3 Stained blood films.'}]}
    original = deepcopy(candidate)
    value, audit = repair_visual_citations(candidate, segments)
    assert candidate == original == audit['raw'] and value['patient_ids'] == ['p1']
    entry = audit['recoveries'][0]
    assert value['evidence'][0]['quote'] == segments[0]['text'][entry['start']:entry['end']]
    assert entry['replacement'] == 'Fig. 3\nStained blood films.'
    assert entry['source_sha256'] == hashlib.sha256(segments[0]['text'].encode()).hexdigest()


@pytest.mark.parametrize('text,quote,reason', [
    ('caption. (A) first panel. (B) second panel.', 'caption. (B) second panel.', 'no_contiguous'),
    ('Fig. 3\ncaption; Fig. 3\ncaption', 'Fig. 3 caption', 'ambiguous'),
    ('a\na\na', 'a a', 'ambiguous'),
    ('dose 73 mg', 'dose 37 mg', 'no_contiguous'),
])
def test_discontiguous_ambiguous_and_semantic_changes_stay_unresolved(text, quote, reason):
    candidate = {'quote': quote, 'segment_id': 's1'}
    value, audit = repair_visual_citations(candidate, [{'segment_id': 's1', 'text': text}])
    assert value == candidate and not audit['recoveries']
    assert audit['unresolved'][0]['reason'].startswith(reason)


def test_asset_path_and_duplicate_source_ids_refused(tmp_path):
    with pytest.raises(ValueError, match='Duplicate'):
        repair_visual_citations({}, [{'segment_id': 's1', 'text': 'x'}]*2)
    row = {'status': 'ready', 'file': '../outside', 'pixel_provenance': {
        'sha256': hashlib.sha256(PNG).hexdigest(), 'bytes': len(PNG), 'mime_type': 'image/png'}}
    with pytest.raises(ValueError, match='escaped'): load_asset(tmp_path, row)


async def test_stream_without_content_length_cannot_persist_over_cap(tmp_path):
    class Chunks(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield PNG[:8]
            yield PNG[8:]
    async with httpx.AsyncClient(transport=httpx.MockTransport(
            lambda r: httpx.Response(200, stream=Chunks()))) as client:
        result = await prepare_media(source(tmp_path, [article()]), tmp_path/'out', max_image_bytes=10, http=client)
    assert result['figures'][0]['status'] == 'fetch_failed'
    assert 'byte cap' in result['figures'][0]['error'] and result['bytes'] == 0


async def test_cross_article_and_conflicting_md5_never_requested(tmp_path):
    first, second = article(1), article(2)
    first['figures'][0]['image_urls'] = second['figures'][0]['image_urls']
    first['figures'][0]['media'] = second['figures'][0]['media']
    second['figures'][0]['media'][0]['md5'] = '0'*32
    def handler(request): raise AssertionError('Invalid manifest must not be requested')
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await prepare_media(source(tmp_path, [first, second]), tmp_path/'out', http=client)
    assert all(row['status'] == 'fetch_failed' for row in result['figures'])
    assert result['response_bytes'] == 0
