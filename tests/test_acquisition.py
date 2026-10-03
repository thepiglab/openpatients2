import gzip
import hashlib
import json
from pathlib import Path

import httpx
import pytest
import yaml

from openpatients2.acquisition import (AcquisitionConfig, AcquisitionClient, BudgetStop, Store,
    initialize, import_candidates, discover, run, status, export)
from openpatients2.cli import parser
from openpatients2.data import read_jsonl
from test_articles import XML


BASE = 'https://pmc-oa-opendata.s3.amazonaws.com'


def make_store(tmp_path, **overrides):
    config = AcquisitionConfig(**{'lanes':[], 'min_free_bytes':0, **overrides})
    return Store(tmp_path / 'work', config)


def seed(store, ids=('PMC1.1',)):
    path = store.work / 'seeds.txt'
    path.write_text('\n'.join(ids)+'\n')
    import_candidates(store, path)


def backend(request, *, license='CC BY-NC-SA', retracted=False, wrong_md5=False):
    if request.url.path.endswith('.json'):
        md5 = '0'*32 if wrong_md5 else hashlib.md5(XML.encode()).hexdigest()
        return httpx.Response(200, json={'pmcid':'PMC1','version':1,'is_pmc_openaccess':True,
            'is_retracted':retracted,'is_manuscript':False,'license_code':license,
            'xml_url':BASE+'/PMC1.1/PMC1.1.xml?md5='+md5,
            'media_urls':[BASE+'/PMC1.1/fig1.jpg', BASE+'/PMC1.1/values.csv']})
    if request.url.path.endswith('.xml'):
        return httpx.Response(200, text=XML, headers={'content-type':'application/xml'})
    raise AssertionError('Unexpected or forbidden download: '+str(request.url))


@pytest.mark.asyncio
async def test_fetch_resume_verifies_checksums_and_preserves_media_urls(tmp_path):
    store = make_store(tmp_path); seed(store); store.close()
    requests = []
    def handler(r):
        requests.append(r.url.path)
        return backend(r)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        first = await run(tmp_path/'work', 'fetch', http=http)
        second = await run(tmp_path/'work', 'fetch', http=http)
    assert first['stored_articles'] == second['stored_articles'] == 1
    assert len(requests) == 2 and all(not x.endswith(('.jpg','.csv','.pdf')) for x in requests)
    out = tmp_path/'articles.jsonl.gz'
    exported = export(tmp_path/'work', out)
    rows = list(read_jsonl(out))
    assert exported['articles'] == 1 and rows[0]['figures'][0]['image_urls']
    assert rows[0]['supplements'][0]['content_inspected'] is False
    assert rows[0]['retrieval']['xml']['manifest_md5_verified'] is True
    with pytest.raises(ValueError): export(tmp_path/'work', out)
    assert list(read_jsonl(out)) == rows


@pytest.mark.asyncio
@pytest.mark.parametrize('options', [{'license':'CC BY-ND'}, {'retracted':True}])
async def test_metadata_rejection_avoids_xml_download(tmp_path, options):
    store = make_store(tmp_path); seed(store); store.close()
    paths = []
    def handler(r):
        paths.append(r.url.path); return backend(r, **options)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        report = await run(tmp_path/'work', 'fetch', http=http)
    assert report['candidates'] == {'ineligible_or_unavailable':1}
    assert len(paths) == 1 and not report['stored_articles']


@pytest.mark.asyncio
async def test_unclassified_metadata_is_resolved_after_xml(tmp_path):
    store = make_store(tmp_path); seed(store); store.close()
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r: backend(r, license='other'))) as http:
        report = await run(tmp_path/'work', 'fetch', http=http)
    assert report['candidates'] == {'eligible':1}
    output = tmp_path/'accepted.jsonl'
    export(tmp_path/'work', output)
    rights = list(read_jsonl(output))[0]['license']
    assert rights['code'] == 'CC BY-NC-SA' and rights['version'] == '4.0'
    assert rights['basis'] == 'article_permissions'


@pytest.mark.asyncio
@pytest.mark.parametrize('license_url,accepted', [('https://opensource.org/licenses/MIT',True),
                                               ('https://publisher.example/custom-license',False)])
async def test_non_cc_resolution_and_review_export(tmp_path, license_url, accepted):
    store = make_store(tmp_path); seed(store); store.close()
    xml = XML.replace('https://creativecommons.org/licenses/by-nc-sa/4.0/', license_url)
    def handler(r):
        response = backend(r, license=None)
        if r.url.path.endswith('.json'):
            metadata = response.json()
            metadata['xml_url'] = BASE+'/PMC1.1/PMC1.1.xml?md5='+hashlib.md5(xml.encode()).hexdigest()
            return httpx.Response(200, json=metadata)
        return httpx.Response(200, text=xml)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        report = await run(tmp_path/'work', 'fetch', http=http)
    assert report['candidates'] == {('eligible' if accepted else 'license_review'):1}
    normal = tmp_path/'eligible.jsonl.gz'; review = tmp_path/'review.jsonl.gz'
    assert export(tmp_path/'work', normal)['articles'] == int(accepted)
    assert export(tmp_path/'work', review, license_review=True)['articles'] == int(not accepted)
    if accepted:
        assert list(read_jsonl(normal))[0]['license']['code'] == 'MIT'
    else:
        entry = list(read_jsonl(review))[0]
        assert entry['license']['statements'][0]['url'] == license_url
        assert entry['retrieval']['xml']['sha256'] == hashlib.sha256(xml.encode()).hexdigest()
        assert 'text' not in entry and not report['stored_articles']


@pytest.mark.asyncio
async def test_search_does_not_filter_out_non_cc_licenses(tmp_path):
    from openpatients2.corpus import search_candidates
    def handler(r):
        term = r.url.params['term']
        assert 'open access[filter]' in term and 'cc by' not in term and 'cc0 license' not in term
        return httpx.Response(200, json={'esearchresult':{'count':'0','idlist':[]}})
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        assert (await search_candidates('case report', http=http))['total'] == 0
        store = make_store(tmp_path, lanes=[{'name':'broad','query':'case report','start':'2024-01-01','end':'2024-01-01'}])
        client = AcquisitionClient(store, http)
        await discover(store, client)
        await client.close(); store.close()


@pytest.mark.asyncio
async def test_bad_md5_never_stored(tmp_path):
    store = make_store(tmp_path); seed(store); store.close()
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r: backend(r, wrong_md5=True))) as http:
        report = await run(tmp_path/'work', 'fetch', http=http)
    assert report['candidates'] == {'failed':1} and report['stored_articles'] == 0


@pytest.mark.asyncio
async def test_budget_stop_leaves_candidate_resumable(tmp_path):
    store = make_store(tmp_path, max_requests_per_invocation=1); seed(store); store.close()
    async with httpx.AsyncClient(transport=httpx.MockTransport(backend)) as http:
        report = await run(tmp_path/'work', 'fetch', http=http)
    assert report['candidates'] == {'pending':1}
    assert report['last_run']['status'] == 'bounded_stop'


@pytest.mark.asyncio
async def test_transient_failure_retried_without_silent_empty_article(tmp_path):
    store = make_store(tmp_path, retries=0); seed(store); store.close()
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(503))) as http:
        report = await run(tmp_path/'work', 'fetch', http=http)
    assert report['candidates'] == {'retryable':1}
    async with httpx.AsyncClient(transport=httpx.MockTransport(backend)) as http:
        report = await run(tmp_path/'work', 'fetch', http=http)
    assert report['candidates'] == {'eligible':1}


@pytest.mark.asyncio
async def test_request_limit_and_decoded_file_limit(tmp_path):
    store = make_store(tmp_path, max_metadata_bytes=1024)
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(200, content=b'x'*2000))) as http:
        client = AcquisitionClient(store, http)
        with pytest.raises(ValueError, match='per-file cap'):
            await client._text(BASE+'/metadata/PMC1.1.json','metadata')
        assert client.used_bytes == 0
        with pytest.raises(ValueError): await client._text('https://evil.example/a.json','metadata')
        with pytest.raises(ValueError): await client._text(BASE+'/a.jpg','xml')
        await client.close()
    store.close()


def test_inventory_stream_import_deduplicates_and_pins_versions(tmp_path):
    store = make_store(tmp_path)
    path = tmp_path/'inventory.csv.gz'
    with gzip.open(path,'wt') as f:
        f.write('"pmc-oa-opendata","metadata/PMC1.1.json","today","etag1"\n'*2)
        f.write('"pmc-oa-opendata","metadata/PMC1.2.json","today","etag2"\n')
    result = import_candidates(store,path,'inventory')
    assert result['next_row'] == 3 and store.db.execute('SELECT count(*) FROM candidates').fetchone()[0] == 2
    assert import_candidates(store,path,'inventory')['status'] == 'done'
    store.close()


def test_queue_limit_config_guard_and_single_owner(tmp_path):
    store = make_store(tmp_path,max_candidates=1)
    path = tmp_path/'ids.txt'; path.write_text('PMC1\nPMC2\n')
    with pytest.raises(BudgetStop): import_candidates(store,path)
    assert store.db.execute('SELECT count(*) FROM candidates').fetchone()[0] == 1
    with pytest.raises(ValueError, match='Another acquisition'): Store(store.work)
    store.close()
    with pytest.raises(ValueError, match='configuration changed'):
        Store(tmp_path/'work', AcquisitionConfig(max_candidates=2))


@pytest.mark.asyncio
async def test_discovery_splits_dates_and_marks_dense_day_incomplete(tmp_path):
    config = tmp_path/'config.yaml'
    config.write_text(yaml.safe_dump({'lanes':[dict(name='cases', query='case', start='2024-01-01',end='2024-01-02')],
        'page_size':1,'search_partition_limit':1, 'min_free_bytes':0}))
    initialize(tmp_path/'work', config)
    def handler(r):
        term = r.url.params['term']
        count = 2 if '"2024-01-02"[pdat]' in term else 1
        return httpx.Response(200,json={'esearchresult':{'count':str(count),'idlist':['1'] if int(r.url.params['retmax']) else [],'querytranslation':term}})
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        report = await run(tmp_path/'work','discover',http=http)
    assert report['search_partitions'] == {'split':1,'done':1,'needs_inventory':1}
    assert not report['discovery_complete_for_configured_queries']
    assert report['candidates'] == {'pending':1}


@pytest.mark.asyncio
async def test_discovery_paging_dedup_and_resume(tmp_path):
    config = tmp_path/'config.yaml'
    config.write_text(yaml.safe_dump({'lanes':[dict(name='cases',query='case',start='2024-01-01',end='2024-01-01')],
        'page_size':1,'min_free_bytes':0}))
    initialize(tmp_path/'work',config)
    requests=[]
    def handler(r):
        requests.append(str(r.url))
        ids = [str(int(r.url.params['retstart'])+1)] if int(r.url.params['retmax']) else []
        return httpx.Response(200,json={'esearchresult':{'count':'2','idlist':ids}})
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        first = await run(tmp_path/'work','discover',http=http)
        second = await run(tmp_path/'work','discover',http=http)
    assert first['discovery_complete_for_configured_queries'] and second['candidates'] == {'pending':2}
    assert len(requests)==3


@pytest.mark.asyncio
async def test_export_limit_cleans_only_own_partial_output(tmp_path):
    store = make_store(tmp_path); seed(store); store.close()
    async with httpx.AsyncClient(transport=httpx.MockTransport(backend)) as http:
        await run(tmp_path/'work','fetch',http=http)
    output = tmp_path/'short.gz'
    with pytest.raises(BudgetStop): export(tmp_path/'work',output,max_bytes=10)
    assert not output.exists()


def test_cli_and_pilot_config():
    args = parser().parse_args(['corpus','run','--work-dir','/tmp/test','--limit','3'])
    assert args.command == 'corpus' and args.limit == 3
    config = AcquisitionConfig.model_validate(yaml.safe_load(Path('configs/sources/corpus-pilot.yaml').read_text()))
    assert len(config.lanes)==2 and config.max_network_bytes_per_invocation==32_000_000


@pytest.mark.asyncio
async def test_ambiguous_versions_need_selection_before_xml(tmp_path):
    store = make_store(tmp_path); seed(store, ('PMC1',)); store.close()
    paths=[]
    def handler(r):
        paths.append(r.url.path)
        if r.url.path == '/':
            return httpx.Response(200,text='<ListBucketResult><IsTruncated>false</IsTruncated>'
                '<Contents><Key>metadata/PMC1.1.json</Key></Contents><Contents><Key>metadata/PMC1.2.json</Key></Contents></ListBucketResult>')
        version = int(r.url.path.split('.')[-2])
        return httpx.Response(200,json={'pmcid':'PMC1','version':version,'is_pmc_openaccess':True,
            'is_retracted':False,'is_manuscript':False,'license_code':'CC BY',
            'xml_url':f'{BASE}/PMC1.{version}/PMC1.{version}.xml'})
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        report = await run(tmp_path/'work','fetch',http=http)
    assert report['candidates'] == {'version_selection_required':1}
    assert not any(p.endswith('.xml') for p in paths)


@pytest.mark.asyncio
async def test_changed_search_count_is_incomplete_not_a_census(tmp_path):
    config = tmp_path/'config.yaml'
    config.write_text(yaml.safe_dump({'lanes':[dict(name='cases',query='case',start='2024-01-01',end='2024-01-01')],
        'page_size':1,'min_free_bytes':0}))
    initialize(tmp_path/'work',config)
    def handler(r):
        count = '2' if r.url.params['retmax']=='0' else '3'
        return httpx.Response(200,json={'esearchresult':{'count':count,'idlist':[]}})
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        result = await run(tmp_path/'work','discover',http=http)
    assert result['search_partitions'] == {'unstable':1}
    assert not result['discovery_complete_for_configured_queries']


def test_storage_page_bound_is_a_real_sqlite_limit(tmp_path):
    import sqlite3
    store = make_store(tmp_path,max_storage_bytes=8_000_000)
    pages = store.db.execute('PRAGMA max_page_count').fetchone()[0]
    assert pages*4096 <= (8_000_000-1_000_000)//2
    with pytest.raises(sqlite3.OperationalError, match='full'):
        with store.db:
            store.db.execute("INSERT INTO articles VALUES('large',zeroblob(5000000),x'00','hash','hash','test','now')")
    assert store.db.execute('SELECT count(*) FROM articles').fetchone()[0] == 0
    assert sum(p.stat().st_size for p in store.work.rglob('*') if p.is_file()) < 8_000_000
    store.close()


def test_cumulative_network_cap_and_quota_config_fail_closed():
    with pytest.raises(ValueError):
        AcquisitionConfig(max_network_bytes_total=150_000_000_001)
    lane = dict(name='first', query='first', start='2024-01-01', end='2024-01-01', max_candidates=2)
    with pytest.raises(ValueError, match='every lane a quota'):
        AcquisitionConfig(lanes=[lane, {**lane, 'name':'second', 'max_candidates':None}])
    with pytest.raises(ValueError, match='sum'):
        AcquisitionConfig(lanes=[lane], max_candidates=1)
    config = AcquisitionConfig.model_validate(yaml.safe_load(Path('configs/sources/corpus-stratified-pilot.yaml').read_text()))
    assert config.max_candidates == sum(x.max_candidates for x in config.lanes) == 400
    assert len({x.stratum for x in config.lanes}) == 10
    assert sum(x.max_candidates for x in config.lanes if x.focus == 'case_focused') == 300
    assert config.max_network_bytes_total == 2_000_000_000


@pytest.mark.asyncio
async def test_cumulative_cap_is_durable_across_clients_and_resumes(tmp_path):
    store = make_store(tmp_path, max_network_bytes_total=1024)
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(200, content=b'x'*600))) as http:
        client = AcquisitionClient(store, http)
        body, receipt = await client._text(BASE+'/metadata/PMC1.1.json', 'metadata')
        assert len(body) == receipt['bytes'] == 600 and receipt['elapsed_seconds'] >= 0
        await client.close()
    store.close()
    store = Store(tmp_path/'work')
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(200, content=b'x'*600))) as http:
        client = AcquisitionClient(store, http)
        with pytest.raises(BudgetStop, match='cumulative'):
            await client._text(BASE+'/metadata/PMC1.1.json', 'metadata')
        assert client.used_bytes == 0
        await client.close()
    store.close()
    report = status(tmp_path/'work')
    assert report['cumulative_decoded_network_bytes'] == report['cumulative_network_budget_charged_bytes'] == 600
    assert report['remaining_network_bytes'] == 424


@pytest.mark.asyncio
async def test_unknown_length_stream_never_overshoots_cumulative_cap(tmp_path):
    class Stream(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield b'x'*2048
    store = make_store(tmp_path, max_network_bytes_total=1024)
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(200, stream=Stream()))) as http:
        client = AcquisitionClient(store, http)
        with pytest.raises(BudgetStop, match='Cumulative'):
            await client._text(BASE+'/metadata/PMC1.1.json', 'metadata')
        assert store.get('network_bytes') == 1024 and store.network_remaining() == 0
        await client.close()
    store.close()


@pytest.mark.asyncio
async def test_interrupted_read_reservation_cannot_be_reset_by_resume(tmp_path):
    import asyncio
    class Interrupted(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield b'x'*100
            raise asyncio.CancelledError()
    store = make_store(tmp_path, max_network_bytes_total=1024)
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(200, stream=Interrupted()))) as http:
        client = AcquisitionClient(store, http)
        with pytest.raises(asyncio.CancelledError):
            await client._text(BASE+'/metadata/PMC1.1.json', 'metadata')
        await client.close()
    store.close()
    store = Store(tmp_path/'work')
    assert store.get('network_bytes') == 0 and store.get('network_reserved_bytes') == 1024
    assert store.network_remaining() == 0
    def forbidden(r):
        raise AssertionError('Exhausted campaign must make no new request')
    async with httpx.AsyncClient(transport=httpx.MockTransport(forbidden)) as http:
        client = AcquisitionClient(store, http)
        with pytest.raises(BudgetStop, match='Cumulative'):
            await client._text(BASE+'/metadata/PMC1.1.json', 'metadata')
        await client.close()
    store.close()


@pytest.mark.asyncio
async def test_lane_quotas_are_fair_deduplicate_and_resume(tmp_path):
    lanes = [dict(name=name, query=name, stratum=name, focus='case_focused',
                  start='2024-01-01', end='2024-01-01', max_candidates=2) for name in ('first','second','third')]
    config = tmp_path/'config.yaml'
    config.write_text(yaml.safe_dump(dict(lanes=lanes, max_candidates=6, page_size=5,
                                        max_requests_per_invocation=4, min_free_bytes=0)))
    initialize(tmp_path/'work', config)
    pages = []
    def handler(r):
        lane = next(x for x in ('first','second','third') if r.url.params['term'].startswith('('+x+')'))
        pages.append((lane, int(r.url.params['retmax'])))
        ids = {'first':['1','2'], 'second':['2','3'], 'third':['3','4']}[lane]
        return httpx.Response(200, json={'esearchresult':{'count':'100', 'idlist':ids if r.url.params['retmax'] != '0' else [], 'querytranslation':r.url.params['term']}})
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        first = await run(tmp_path/'work', 'discover', http=http)
        second = await run(tmp_path/'work', 'discover', http=http)
        third = await run(tmp_path/'work', 'discover', http=http)
    assert pages == [('first',0), ('second',0), ('third',0), ('first',2), ('second',2), ('third',2)]
    assert first['last_run']['status'] == 'bounded_stop'
    assert second['candidates'] == third['candidates'] == {'pending':4}
    assert second['search_partitions'] == {'quota_reached':3}
    assert not second['discovery_complete_for_configured_queries']
    assert [x['selected_candidates'] for x in second['discovery_lanes']] == [2,2,2]
    assert third['last_run']['requests'] == third['last_run']['decoded_network_bytes'] == 0


@pytest.mark.asyncio
async def test_exported_packet_has_durable_lane_and_response_provenance(tmp_path):
    config = tmp_path/'config.yaml'
    config.write_text(yaml.safe_dump(dict(lanes=[dict(name='heart_cases', query='heart case',
        stratum='cardiovascular', focus='case_focused', max_candidates=1,
        start='2024-01-01',end='2024-01-01')], max_candidates=1, min_free_bytes=0)))
    initialize(tmp_path/'work', config)
    def handler(r):
        if 'esearch' in r.url.path:
            return httpx.Response(200, json={'esearchresult':{'count':'2','idlist':['1'] if r.url.params['retmax'] != '0' else [], 'querytranslation':'heart case'}})
        if r.url.path == '/':
            return httpx.Response(200, text='<ListBucketResult><IsTruncated>false</IsTruncated><Contents><Key>metadata/PMC1.1.json</Key></Contents></ListBucketResult>')
        return backend(r)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        result = await run(tmp_path/'work', 'run', fetch_limit=1, http=http)
    assert result['stored_articles'] == 1 and not result['discovery_complete_for_configured_queries']
    output = tmp_path/'sample.jsonl.gz'; export(tmp_path/'work', output)
    row = list(read_jsonl(output))[0]
    sample = row['acquisition']['sample']
    assert sample['lanes'][0]['stratum'] == 'cardiovascular'
    assert row['acquisition']['discovery_lanes'] == ['heart_cases']
    assert sample['search_partitions'][0]['status'] == 'quota_reached'
    receipt = sample['discovery_receipts'][0]
    assert receipt['params']['sort'] == 'pub date' and receipt['params']['retstart'] == 0
    assert receipt['retrieval']['bytes'] > 0 and receipt['retrieval']['sha256']
    assert row['retrieval']['xml']['elapsed_seconds'] >= 0


@pytest.mark.asyncio
async def test_concurrent_fetch_is_bounded_deduplicated_and_keeps_each_raw_xml(tmp_path, monkeypatch):
    import asyncio
    async def no_wait(self):
        await asyncio.sleep(0)
    monkeypatch.setattr('openpatients2.acquisition.RateLimiter.wait', no_wait)
    store = make_store(tmp_path, fetch_concurrency=4)
    seed(store, tuple(f'PMC{i}.1' for i in range(1,8))); store.close()
    active = peak = 0; requests = []
    def xml_for(number):
        return XML.replace('pub-id-type="pmc">1', f'pub-id-type="pmc">{number}').replace(
            'A woman received 5 mg.', f'Article {number} woman received 5 mg.')
    async def handler(request):
        nonlocal active, peak
        active += 1; peak = max(peak,active); requests.append(request.url.path)
        try:
            await asyncio.sleep(0.02)
            number = int(request.url.path.split('/')[-1].split('.')[0].removeprefix('PMC'))
            xml = xml_for(number)
            if request.url.path.endswith('.json'):
                return httpx.Response(200,json={'pmcid':f'PMC{number}','version':1,'is_pmc_openaccess':True,
                    'is_retracted':False,'is_manuscript':False,'license_code':'CC BY-NC-SA',
                    'xml_url':f'{BASE}/PMC{number}.1/PMC{number}.1.xml?md5='+hashlib.md5(xml.encode()).hexdigest()})
            return httpx.Response(200,text=xml)
        finally:
            active -= 1
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        report = await run(tmp_path/'work','fetch',fetch_limit=6,http=http)
    assert peak == 4 and active == 0
    assert len(requests) == len(set(requests)) == 12
    assert report['candidates'] == {'eligible':6,'pending':1}
    store = Store(tmp_path/'work')
    for row in store.db.execute('SELECT article_id,xml_gz,xml_sha256 FROM articles'):
        number = int(row['article_id'].split('.')[0].removeprefix('PMC'))
        assert gzip.decompress(row['xml_gz']) == xml_for(number).encode()
        assert row['xml_sha256'] == hashlib.sha256(xml_for(number).encode()).hexdigest()
    assert store.get('network_reserved_bytes') == 0
    store.close()


@pytest.mark.asyncio
async def test_concurrent_request_claim_is_rechecked_after_rate_wait(tmp_path, monkeypatch):
    import asyncio
    async def no_wait(self):
        await asyncio.sleep(0)
    monkeypatch.setattr('openpatients2.acquisition.RateLimiter.wait', no_wait)
    store = make_store(tmp_path, fetch_concurrency=4, max_requests_per_invocation=1)
    seed(store, ('PMC1.1','PMC2.1','PMC3.1','PMC4.1')); store.close()
    requests = []
    async def handler(request):
        requests.append(str(request.url))
        await asyncio.sleep(0.02)
        return backend(request)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        report = await run(tmp_path/'work','fetch',http=http)
        await asyncio.sleep(0)  # No surviving task should access the closed store.
    assert len(requests) == report['last_run']['requests'] == 1
    assert report['last_run']['status'] == 'bounded_stop'
    assert report['candidates'] == {'pending':4}
    assert not [t for t in asyncio.all_tasks() if t.get_name().startswith('pmc-fetch:')]


@pytest.mark.asyncio
@pytest.mark.parametrize('invocation_cap,campaign_cap',[(1024,4096),(4096,1024)])
async def test_concurrent_byte_reservations_stop_and_drain_every_task(tmp_path, monkeypatch, invocation_cap,campaign_cap):
    import asyncio
    async def no_wait(self):
        await asyncio.sleep(0)
    monkeypatch.setattr('openpatients2.acquisition.RateLimiter.wait', no_wait)
    cancelled = closed = 0
    class Waiting(httpx.AsyncByteStream):
        async def __aiter__(self):
            nonlocal cancelled
            try:
                await asyncio.sleep(60)
                yield b'x'*1024
            except asyncio.CancelledError:
                cancelled += 1
                raise
        async def aclose(self):
            nonlocal closed
            closed += 1
    store = make_store(tmp_path, fetch_concurrency=4, max_network_bytes_per_invocation=invocation_cap,
                       max_network_bytes_total=campaign_cap)
    seed(store, ('PMC1.1','PMC2.1','PMC3.1','PMC4.1')); store.close()
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(200,stream=Waiting()))) as http:
        report = await asyncio.wait_for(run(tmp_path/'work','fetch',http=http),timeout=2)
    assert cancelled == 1 and closed >= 1
    assert report['last_run']['status'] == 'bounded_stop'
    assert report['candidates'] == {'pending':4}
    assert report['cumulative_decoded_network_bytes'] == 0
    assert report['unconfirmed_reserved_network_bytes'] == report['cumulative_network_budget_charged_bytes'] == 1024
    assert not [t for t in asyncio.all_tasks() if t.get_name().startswith('pmc-fetch:')]
    # Permanent reservations remain charged after all tasks/client/store close.
    store = Store(tmp_path/'work')
    assert store.get('network_reserved_bytes') == 1024 and store.network_remaining() == campaign_cap-1024
    store.close()
