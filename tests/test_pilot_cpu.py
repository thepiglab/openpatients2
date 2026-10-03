import gzip
import hashlib
import json
from pathlib import Path

import httpx
import pytest
import yaml

from openpatients2 import pilot_cpu, pilot_media
from openpatients2.data import read_jsonl, write_json
from test_articles import XML


BASE = 'https://pmc-oa-opendata.s3.amazonaws.com'


def config(tmp_path, **source_overrides):
    source = tmp_path/'source.yaml'
    source.write_text(yaml.safe_dump({'lanes':[dict(name='cases',query='case',stratum='heart',focus='case_focused',
        start='2024-01-01',end='2024-01-01',max_candidates=2)], 'max_candidates':2,
        'page_size':2, 'min_free_bytes':0, 'retries':0, **source_overrides}))
    metadata = tmp_path/'metadata.json'; metadata.write_text('{}')
    template = tmp_path/'template.jinja'; template.write_text('template')
    return {'source_config':str(source), 'tokenizer_metadata':str(metadata), 'chat_template':str(template),
            'fetch_batch':1, 'max_invocations':10}


def backend(request):
    if 'esearch' in request.url.path:
        return httpx.Response(200,json={'esearchresult':{'count':'20',
            'idlist':['1','2'] if request.url.params['retmax'] != '0' else [],'querytranslation':'case'}})
    if request.url.path == '/':
        pmcid = request.url.params['prefix'].split('/')[-1].rstrip('.')
        return httpx.Response(200,text=f'<ListBucketResult><IsTruncated>false</IsTruncated><Contents><Key>metadata/{pmcid}.1.json</Key></Contents></ListBucketResult>')
    pmcid = request.url.path.split('/')[-1].split('.')[0]
    xml = XML if pmcid == 'PMC1' else XML.replace('pub-id-type="pmc">1', 'pub-id-type="pmc">2').replace(
        'https://creativecommons.org/licenses/by-nc-sa/4.0/', 'https://publisher.example/custom-license')
    if request.url.path.endswith('.json'):
        return httpx.Response(200,json={'pmcid':pmcid,'version':1,'is_pmc_openaccess':True,
            'is_retracted':False,'is_manuscript':False,'license_code':'other',
            'xml_url':f'{BASE}/{pmcid}.1/{pmcid}.1.xml?md5='+hashlib.md5(xml.encode()).hexdigest()})
    assert request.url.path.endswith('.xml'), 'Unexpected pixel/model request'
    return httpx.Response(200,text=xml)


@pytest.fixture
def stages(monkeypatch):
    calls = []
    async def no_wait(self):
        pass
    monkeypatch.setattr('openpatients2.acquisition.RateLimiter.wait', no_wait)
    monkeypatch.setenv('CUDA_VISIBLE_DEVICES','')
    monkeypatch.delenv('SLURM_JOB_GPUS',raising=False)
    def tokenizer(destination, metadata):
        calls.append(('tokenizer', str(destination), str(metadata)))
        destination = Path(destination); destination.mkdir(exist_ok=True)
        (destination/'tokenizer.json').write_text('{}')
        (destination/'tokenizer_config.json').write_text('{}')
        write_json(destination/'tokenizer-manifest.json', {'weights_downloaded':False})
        return {'weights_downloaded':False}
    def profile(input_path, tokenizer_path, output, **kwargs):
        calls.append(('profile', kwargs))
        output = Path(output); output.mkdir()
        rows = list(read_jsonl(input_path))
        with gzip.open(output/'sample.jsonl.gz','wt') as handle:
            for row in rows: handle.write(json.dumps(row)+'\n')
        with gzip.open(output/'counts.jsonl.gz','wt') as handle:
            handle.write('{"article_id":"PMC1.1","words":12}\n')
        report = {'articles':len(rows), 'sample':{'size':len(rows)}}
        write_json(output/'profile.json', report)
        (output/'SUMMARY.md').write_text('Scalar counts only')
        return report
    async def media(input_path, output_dir, **kwargs):
        calls.append(('media',str(input_path),kwargs))
        output = Path(output_dir)/'vision-assets'; output.mkdir()
        manifest = {'complete':True,'figures':[],'bytes':0,'response_bytes':0}
        write_json(output/'manifest.json',manifest)
        return manifest
    monkeypatch.setattr(pilot_cpu,'tokenizer_snapshot',tokenizer)
    monkeypatch.setattr(pilot_cpu,'profile',profile)
    monkeypatch.setattr(pilot_media,'prepare_media',media)
    return calls


@pytest.mark.asyncio
async def test_cpu_pipeline_prepares_one_bounded_sample_and_pins_outputs(tmp_path, stages):
    cfg = config(tmp_path); work = tmp_path/'pilot'
    async with httpx.AsyncClient(transport=httpx.MockTransport(backend)) as http:
        report = await pilot_cpu.run_cpu(work,cfg,http=http)
    assert report['status'] == 'ready' and report['sample_size'] == 1
    assert report['source']['candidates'] == {'eligible':1,'license_review':1}
    assert not report['source']['discovery_complete_for_configured_queries']
    assert report['exports']['license-review.jsonl.gz']['articles'] == 1
    assert len(report['acquisition_invocations']) == 3  # discovery + two fetch batches
    assert [c[0] for c in stages] == ['tokenizer','profile','media']
    assert stages[1][1]['workers'] == 32 and stages[1][1]['sample_size'] == 48
    assert stages[2][2]['max_figures'] == 12 and stages[2][2]['max_total_bytes'] == 64_000_000
    assert {'profile/sample.jsonl.gz','profile/profile.json','profile/counts.jsonl.gz',
            'vision-assets/manifest.json','tokenizer/tokenizer.json','tokenizer/tokenizer_config.json'} <= report['hashes'].keys()
    assert all(pilot_cpu.sha256(work/name) == digest for name,digest in report['hashes'].items() if not name.startswith('/'))
    assert json.loads((work/'cpu.json').read_text()) == report
    before = (work/'cpu.json').read_bytes()
    with pytest.raises(ValueError,match='Completed CPU'):
        await pilot_cpu.run_cpu(work,cfg)
    assert (work/'cpu.json').read_bytes() == before and len(stages) == 3


@pytest.mark.asyncio
async def test_shared_invocation_bound_does_not_download_tokenizer_for_empty_queue(tmp_path, stages):
    cfg = {**config(tmp_path), 'max_invocations':1}
    async with httpx.AsyncClient(transport=httpx.MockTransport(backend)) as http:
        report = await pilot_cpu.run_cpu(tmp_path/'pilot',cfg,http=http)
    assert report['status'] == 'empty' and report['source']['candidates'] == {'pending':2}
    assert report['acquisition_stops']['fetch'] == 'invocation_limit'
    assert len(report['acquisition_invocations']) == 1 and not stages


@pytest.mark.asyncio
async def test_no_queue_progress_stops_fetch_loop(tmp_path, stages):
    cfg = config(tmp_path,max_requests_per_invocation=1)
    async with httpx.AsyncClient(transport=httpx.MockTransport(backend)) as http:
        report = await pilot_cpu.run_cpu(tmp_path/'pilot',cfg,http=http)
    assert report['status'] == 'empty' and report['source']['candidates'] == {'pending':2}
    assert report['acquisition_stops']['fetch'] == 'no_queue_progress'
    assert len(report['acquisition_invocations']) == 3 and not stages


@pytest.mark.asyncio
async def test_failed_profile_summary_and_partial_outputs_are_preserved(tmp_path, stages, monkeypatch):
    def broken(input_path, tokenizer_path, output, **kwargs):
        Path(output).mkdir(); (Path(output)/'FAILED.json').write_text('{"failed":true}')
        raise RuntimeError('profiling stopped')
    monkeypatch.setattr(pilot_cpu,'profile',broken)
    cfg = config(tmp_path); work = tmp_path/'pilot'
    async with httpx.AsyncClient(transport=httpx.MockTransport(backend)) as http:
        with pytest.raises(RuntimeError,match='profiling stopped'):
            await pilot_cpu.run_cpu(work,cfg,http=http)
    report = json.loads((work/'cpu.json').read_text())
    assert report['status'] == 'failed' and report['errors'][0]['stage'] == 'profile'
    assert (work/'articles.jsonl.gz').exists() and (work/'profile'/'FAILED.json').exists()
    before = (work/'cpu.json').read_bytes()
    with pytest.raises(ValueError,match='Existing profile'):
        await pilot_cpu.run_cpu(work,cfg)
    assert (work/'cpu.json').read_bytes() == before
    assert [c[0] for c in stages] == ['tokenizer']


@pytest.mark.asyncio
async def test_tokenizer_failure_can_resume_verified_exports_without_refetch(tmp_path, stages, monkeypatch):
    good = pilot_cpu.tokenizer_snapshot
    def broken(*args, **kwargs):
        raise RuntimeError('tokenizer unavailable')
    monkeypatch.setattr(pilot_cpu,'tokenizer_snapshot',broken)
    cfg = config(tmp_path); work = tmp_path/'pilot'
    async with httpx.AsyncClient(transport=httpx.MockTransport(backend)) as http:
        with pytest.raises(RuntimeError,match='tokenizer unavailable'):
            await pilot_cpu.run_cpu(work,cfg,http=http)
    monkeypatch.setattr(pilot_cpu,'tokenizer_snapshot',good)
    def forbidden(request):
        raise AssertionError('Finished acquisition must not fetch again on resume')
    async with httpx.AsyncClient(transport=httpx.MockTransport(forbidden)) as http:
        report = await pilot_cpu.run_cpu(work,cfg,http=http)
    assert report['status'] == 'ready' and len(report['errors']) == 1
    assert len(report['acquisition_invocations']) == 3


@pytest.mark.asyncio
async def test_changed_checkpoint_export_fails_without_overwriting(tmp_path, stages, monkeypatch):
    monkeypatch.setattr(pilot_cpu,'tokenizer_snapshot',lambda *a: (_ for _ in ()).throw(RuntimeError('stop')))
    cfg = config(tmp_path); work = tmp_path/'pilot'
    async with httpx.AsyncClient(transport=httpx.MockTransport(backend)) as http:
        with pytest.raises(RuntimeError):
            await pilot_cpu.run_cpu(work,cfg,http=http)
    (work/'articles.jsonl.gz').write_bytes(b'changed')
    with pytest.raises(ValueError,match='artifact changed'):
        await pilot_cpu.run_cpu(work,cfg)
    assert (work/'articles.jsonl.gz').read_bytes() == b'changed'


@pytest.mark.asyncio
async def test_existing_profile_is_refused_before_any_acquisition(tmp_path, stages):
    work = tmp_path/'pilot'; (work/'profile').mkdir(parents=True)
    (work/'profile'/'keep').write_text('keep')
    with pytest.raises(ValueError,match='Existing profile'):
        await pilot_cpu.run_cpu(work,config(tmp_path))
    assert not (work/'acquisition').exists() and (work/'profile'/'keep').read_text() == 'keep'


@pytest.mark.asyncio
async def test_interrupted_invocation_is_durably_charged_on_resume(tmp_path, stages, monkeypatch):
    import asyncio
    actual = pilot_cpu.acquisition.run
    async def interrupted(*args, **kwargs):
        raise asyncio.CancelledError()
    monkeypatch.setattr(pilot_cpu.acquisition,'run',interrupted)
    cfg = {**config(tmp_path), 'max_invocations':1}; work = tmp_path/'pilot'
    with pytest.raises(asyncio.CancelledError):
        await pilot_cpu.run_cpu(work,cfg)
    prior = json.loads((work/'cpu.json').read_text())
    assert prior['status'] == 'interrupted' and len(prior['acquisition_invocations']) == 1
    monkeypatch.setattr(pilot_cpu.acquisition,'run',actual)
    def forbidden(request):
        raise AssertionError('Interrupted invocation already consumed the only allowed invocation')
    async with httpx.AsyncClient(transport=httpx.MockTransport(forbidden)) as http:
        resumed = await pilot_cpu.run_cpu(work,cfg,http=http)
    assert resumed['status'] == 'empty' and not stages
    assert resumed['acquisition_stops']['discover'] == 'invocation_limit'
    assert len(resumed['acquisition_invocations']) == 1 and len(resumed['errors']) == 1


@pytest.mark.asyncio
async def test_empty_profiler_sample_never_becomes_ready(tmp_path, stages, monkeypatch):
    good = pilot_cpu.profile
    def empty(*args, **kwargs):
        report = good(*args, **kwargs)
        with gzip.open(Path(args[2])/'sample.jsonl.gz','wt'):
            pass
        return report
    monkeypatch.setattr(pilot_cpu,'profile',empty)
    cfg = config(tmp_path); work = tmp_path/'pilot'
    async with httpx.AsyncClient(transport=httpx.MockTransport(backend)) as http:
        with pytest.raises(ValueError,match='nonempty eligible sample'):
            await pilot_cpu.run_cpu(work,cfg,http=http)
    assert json.loads((work/'cpu.json').read_text())['status'] == 'failed'
    assert [c[0] for c in stages] == ['tokenizer','profile']
