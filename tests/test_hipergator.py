import asyncio
import copy
import hashlib
import json
import shutil
import subprocess
import tarfile
from pathlib import Path

import httpx
import pytest

from openpatients2 import hipergator as hpg
from openpatients2.hpg_eval import Replay, fixtures
from openpatients2.provenance import json_digest
from openpatients2.serving import render

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / 'benchmarks/hipergator-k2/fixtures'


def test_fixture_matches_the_previous_article_patient_and_request_sample():
    manifest, articles, packets, requests, reference = fixtures(FIXTURES)
    assert (len(articles), len(packets), len(requests), len(reference['checks'])) == (9, 11, 176, 197)
    assert (FIXTURES / 'articles.jsonl').read_bytes() == (ROOT / 'runs/medical-fidelity-v1/articles.jsonl').read_bytes()
    assert (FIXTURES / 'reference.json').read_bytes() == (ROOT / 'runs/medical-fidelity-v1/reference.json').read_bytes()
    for r in requests:
        previous = next((ROOT / 'runs/medical-fidelity-v1/comparison/attempts').glob(r['origin_signature'] + '-0-*.json'))
        assert r['messages'] == json.loads(previous.read_text())['request']
    assert manifest['negative_control_articles'] == 2


def test_modified_fixture_is_rejected(tmp_path):
    shutil.copytree(FIXTURES, tmp_path / 'fixtures')
    p = tmp_path / 'fixtures/articles.jsonl'
    p.write_bytes(p.read_bytes() + b'\n')
    with pytest.raises(ValueError, match='integrity'): fixtures(tmp_path / 'fixtures')


def test_chain_does_not_hold_gpus_for_download_or_overlap_checkpoints(tmp_path):
    result = hpg.prepare(ROOT / hpg.DEFAULT_CONFIG, ROOT, tmp_path / 'campaign')
    jobs = result['jobs']
    assert len(jobs) == 14
    assert [j['stage'] for j in jobs] == ['setup'] + ['download', 'gpu', 'cleanup'] * 4 + ['report']
    for i, job in enumerate(jobs):
        command = job['command']
        assert '--account=cai5724' in command and '--qos=cai5724' in command
        if job['stage'] == 'gpu':
            expected = 4 if job['model'] == 'k2-375b-nvfp4' else 2
            assert '--partition=hpg-b200' in command and f'--gres=gpu:b200:{expected}' in command
        else:
            assert '--gres=none' in command and '--partition=hpg-b200' not in command
        if i:
            kind = 'afterok' if jobs[i-1]['stage'] == 'cleanup' else 'afterany'
            assert '--dependency=' + kind + ':' + jobs[i-1]['id'] in command
    assert not (tmp_path / 'campaign/active-model').exists()
    with pytest.raises(ValueError, match='new work'): hpg.prepare(ROOT / hpg.DEFAULT_CONFIG, ROOT, tmp_path / 'campaign')


def test_submit_checks_resources_then_releases_setup_only_after_all_successors(tmp_path, monkeypatch):
    calls = []; accepted = []; released = []
    monkeypatch.setenv('SBATCH_PARTITION', 'unexpected-partition')
    monkeypatch.setenv('SBATCH_GRES', 'gpu:1')
    monkeypatch.setenv('HF_TOKEN', 'SYNTHETIC_NOT_A_SECRET')
    def scheduler(args, **kwargs):
        calls.append(args)
        assert not any(k.startswith('SBATCH_') for k in kwargs['env'])
        if args[0] == 'sbatch':
            if '--test-only' in args:
                assert not accepted
                if '--partition=hpg-b200' in args:
                    if '--gres=gpu:b200:4' in args:
                        assert '--cpus-per-task=16' in args and '--mem=250G' in args
                    else:
                        assert '--gres=gpu:b200:2' in args and '--cpus-per-task=8' in args
                        assert '--mem=64G' in args or '--mem=128G' in args
                return subprocess.CompletedProcess(args, 0, '', 'sbatch: Job would be scheduled')
            assert '--hold' in args
            job = str(4100 + len(accepted)); accepted.append(job)
            return subprocess.CompletedProcess(args, 0, job + ';hipergator\n', '')
        assert args[:2] == ['scontrol', 'release'] and len(accepted) == 14
        if args[2] == accepted[0]:
            assert released == list(reversed(accepted[1:]))
        released.append(args[2])
        return subprocess.CompletedProcess(args, 0, '', '')
    monkeypatch.setattr(hpg.subprocess, 'run', scheduler)
    work = tmp_path / 'campaign'
    result = hpg.prepare(ROOT / hpg.DEFAULT_CONFIG, ROOT, work, submit=True)
    assert len([c for c in calls if '--test-only' in c]) == 8
    assert released == list(reversed(accepted))
    assert all(j['release_confirmed'] for j in result['jobs'])
    assert json.loads((work / 'submission.json').read_text()) == {'status': 'released', 'preflight_passed': True}
    audit = (work / 'slurm-commands.json').read_text()
    assert 'Job would be scheduled' in audit and 'SYNTHETIC_NOT_A_SECRET' not in audit
    assert not (work / 'active-model').exists()


def test_resource_rejection_surfaces_slurm_stderr_without_submitting_any_jobs(tmp_path, monkeypatch, capsys):
    from openpatients2.cli import main
    calls = []
    error = 'sbatch: error: QOSMaxCpuPerJobLimit\nsbatch: error: Batch job submission failed: Job violates accounting/QOS policy\n'
    def scheduler(args, **kwargs):
        calls.append(args)
        assert args[0] == 'sbatch' and '--test-only' in args
        return subprocess.CompletedProcess(args, 1 if '--partition=hpg-b200' in args else 0, '', error if '--partition=hpg-b200' in args else '')
    monkeypatch.setattr(hpg.subprocess, 'run', scheduler)
    monkeypatch.chdir(ROOT)
    work = tmp_path / 'campaign'
    assert main(['hpg-benchmark', 'submit', '--work-dir', str(work)]) == 2
    message = capsys.readouterr().err
    assert error.strip() in message and 'gpu resource preflight' in message
    assert 'Traceback' not in message and str(work / 'slurm-commands.json') in message
    assert len(calls) == 3 and not (work / 'active-model').exists()
    assert json.loads((work / 'jobs.json').read_text())['jobs'] == []
    state = json.loads((work / 'submission.json').read_text())
    assert state['phase'] == 'preflight' and state['rollback']['status'] == 'not_needed'
    assert not state['preflight_passed']
    assert json.loads((work / 'slurm-commands.json').read_text())['commands'][-1]['stderr'] == error


@pytest.mark.parametrize('cancel_code', [0, 1])
def test_rejection_after_preflight_keeps_jobs_held_and_records_rollback(tmp_path, monkeypatch, cancel_code):
    calls = []; accepted = []
    error = 'sbatch: error: Requested node configuration is not available'
    def scheduler(args, **kwargs):
        calls.append(args)
        if '--test-only' in args:
            return subprocess.CompletedProcess(args, 0, '', '')
        if args[0] == 'sbatch':
            assert '--hold' in args
            if '--partition=hpg-b200' in args:
                return subprocess.CompletedProcess(args, 1, '', error)
            job = str(44131000 + len(accepted)); accepted.append(job)
            return subprocess.CompletedProcess(args, 0, job, '')
        assert args == ['scancel', *accepted]
        return subprocess.CompletedProcess(args, cancel_code, '', 'scancel: error: Controller unavailable' if cancel_code else '')
    monkeypatch.setattr(hpg.subprocess, 'run', scheduler)
    work = tmp_path / 'campaign'
    with pytest.raises(RuntimeError, match=error) as exc:
        hpg.prepare(ROOT / hpg.DEFAULT_CONFIG, ROOT, work, submit=True)
    assert len(accepted) == 2 and not any(c[0] == 'scontrol' for c in calls)
    assert 'gpu/k2-375b-nvfp4' in str(exc.value)
    state = json.loads((work / 'submission.json').read_text())
    assert state['rollback']['job_ids'] == accepted
    assert state['rollback']['status'] == ('failed' if cancel_code else 'cancellation_requested')
    if cancel_code:
        assert 'Controller unavailable' in str(exc.value) and 'Inspect these jobs' in str(exc.value)
    assert not (work / 'active-model').exists()


@pytest.mark.parametrize('hold_code', [0, 1])
def test_partial_release_reholds_successors_before_canceling_their_parents(tmp_path, monkeypatch, hold_code):
    calls = []; held = {}; released = []
    def scheduler(args, **kwargs):
        calls.append(args)
        if '--test-only' in args:
            return subprocess.CompletedProcess(args, 0, '', '')
        if args[0] == 'sbatch':
            job = str(5100 + len(held)); held[job] = True
            return subprocess.CompletedProcess(args, 0, job, '')
        if args[:2] == ['scontrol', 'release']:
            assert held['5100']  # Setup has not been released; no downloads can start.
            if len(released) == 2:
                return subprocess.CompletedProcess(args, 1, '', 'scontrol: error: Synthetic release rejection')
            held[args[2]] = False; released.append(args[2])
        elif args[:2] == ['scontrol', 'hold']:
            if hold_code:
                return subprocess.CompletedProcess(args, 1, '', 'scontrol: error: Controller unavailable during hold')
            for job in args[2:]: held[job] = True
        else:
            assert args == ['scancel', *held] and all(held.values())
        return subprocess.CompletedProcess(args, 0, '', '')
    monkeypatch.setattr(hpg.subprocess, 'run', scheduler)
    work = tmp_path / 'campaign'
    with pytest.raises(RuntimeError, match='Synthetic release rejection'):
        hpg.prepare(ROOT / hpg.DEFAULT_CONFIG, ROOT, work, submit=True)
    assert len(held) == 14 and released == ['5113', '5112']
    state = json.loads((work / 'submission.json').read_text())
    assert state['phase'] == 'release'
    if hold_code:
        assert calls[-1] == ['scontrol', 'hold', *held]
        assert not any(c[0] == 'scancel' for c in calls)
        assert state['rollback']['status'] == 'failed' and 'during hold' in state['rollback']['error']
    else:
        assert calls[-2] == ['scontrol', 'hold', *held] and calls[-1] == ['scancel', *held]


@pytest.mark.parametrize('response', ['timeout', 'invalid_id'])
def test_ambiguous_submission_is_logged_and_never_automatically_retried(tmp_path, monkeypatch, response):
    submissions = []
    def scheduler(args, **kwargs):
        if '--test-only' in args:
            return subprocess.CompletedProcess(args, 0, '', '')
        if args[0] == 'sbatch':
            submissions.append(args)
            if len(submissions) == 3:
                if response == 'timeout':
                    raise subprocess.TimeoutExpired(args, 60, stderr=b'sbatch: Controller response timed out')
                return subprocess.CompletedProcess(args, 0, 'unexpected response', '')
            return subprocess.CompletedProcess(args, 0, str(6100 + len(submissions)), '')
        assert args == ['scancel', '6101', '6102']
        return subprocess.CompletedProcess(args, 0, '', '')
    monkeypatch.setattr(hpg.subprocess, 'run', scheduler)
    work = tmp_path / 'campaign'
    with pytest.raises(RuntimeError, match='inspect squeue'):
        hpg.prepare(ROOT / hpg.DEFAULT_CONFIG, ROOT, work, submit=True)
    assert len(submissions) == 3
    audit = json.loads((work / 'slurm-commands.json').read_text())['commands']
    if response == 'timeout':
        assert audit[-2]['stderr'] == 'sbatch: Controller response timed out'
    else:
        assert audit[-2]['stdout'] == 'unexpected response'


def test_topologies_use_exactly_the_requested_gpus_and_offline_native_quantization(tmp_path, monkeypatch):
    hpg.prepare(ROOT / hpg.DEFAULT_CONFIG, ROOT, tmp_path / 'campaign')
    campaign = hpg.read_campaign(tmp_path / 'campaign')
    hpg.write_json(tmp_path / 'campaign/setup.json', {'sif': '/shared/vllm.sif'})
    for key in ('HF_HOME', 'HF_HUB_CACHE', 'HF_XET_CACHE', 'HF_MODULES_CACHE', 'HF_XET_CHUNK_CACHE_SIZE_BYTES', 'XDG_CACHE_HOME'):
        monkeypatch.setenv(key, '')
    for model in campaign['config']['models']:
        count = 4 if model['name'] == 'k2-375b-nvfp4' else 2
        assert hpg.gpu_count(model) == count
        monkeypatch.setenv('CUDA_VISIBLE_DEVICES', ','.join('GPU-' + str(i) for i in range(count)))
        cfg = hpg.serving_config(campaign, model)
        commands = render(cfg, campaign['work'])
        assert len(commands) == model['replicas']
        assert sorted(gpu for c in commands for gpu in c['devices']) == ['GPU-' + str(i) for i in range(count)]
        for c in commands:
            args = c['argv']
            assert 'HF_HUB_OFFLINE=1' in args and 'TRANSFORMERS_OFFLINE=1' in args
            assert args[args.index('--model-impl')+1] == 'vllm'
            assert args[args.index('--quantization')+1] == model['quantization']
            assert len(c['devices']) == model['tensor_parallel']
            assert 'IFM/' not in args[args.index('serve')+1]  # Local checkpoint, never a Hub repo download.


def test_all_gpu_profiles_are_preflighted_including_the_mova_ram_override(tmp_path, monkeypatch):
    calls = []
    def scheduler(args, **kwargs):
        calls.append(args)
        assert '--test-only' in args
        return subprocess.CompletedProcess(args, 1 if '--mem=128G' in args else 0, '',
                                           'Synthetic MoVA memory rejection' if '--mem=128G' in args else '')
    monkeypatch.setattr(hpg.subprocess, 'run', scheduler)
    work = tmp_path / 'campaign'
    with pytest.raises(RuntimeError, match='Synthetic MoVA memory rejection'):
        hpg.prepare(ROOT / hpg.DEFAULT_CONFIG, ROOT, work, submit=True)
    assert len(calls) == 5 and json.loads((work / 'jobs.json').read_text())['jobs'] == []


def synthetic_throughput_arms():
    def tokens(rows, seconds):
        return {'per_article': rows, 'fresh_measurement': True,
                'aggregate_output_tokens_per_second': sum(r['output_tokens'] for r in rows) / seconds}
    a = {'article_id': 'a', 'requests_sent': 1, 'input_tokens': 2000, 'output_tokens': 1000}
    b = {'article_id': 'a', 'requests_sent': 1, 'input_tokens': 500, 'output_tokens': 500}
    combined = [{'article_id': 'a', 'requests_sent': 2, 'input_tokens': 600, 'output_tokens': 1000},
                {'article_id': 'b', 'requests_sent': 1, 'input_tokens': 500, 'output_tokens': 0}]
    return {'matched': {'wall_seconds': 10, 'tokens': tokens([a], 10)},
            'ifm_high': {'wall_seconds': 20, 'tokens': tokens([b], 20),
                         'secondary_summary': {'wall_seconds': 10},
                         'workflow_tokens_including_secondary': tokens(combined, 30)}}


def test_model_throughput_weights_elapsed_time_and_includes_secondary_without_double_counting():
    model = {'replicas': 1, 'tensor_parallel': 4}
    result = hpg.throughput_summary(model, synthetic_throughput_arms(), gpu_stage_seconds=80)
    assert result['evaluation_seconds'] == 40 and result['total_output_tokens'] == 2000
    assert result['total_input_tokens'] == 3100 and result['aggregate_input_tokens_per_second'] == 77.5
    assert result['aggregate_output_tokens_per_second'] == 50
    assert result['output_tokens_per_gpu_second'] == 12.5
    assert result['benchmark_output_tokens_per_allocated_second'] == 25
    assert result['complete_benchmark_articles_per_hour'] == 90
    small = hpg.throughput_summary({'replicas': 2, 'tensor_parallel': 1}, synthetic_throughput_arms(), 80)
    assert small['gpu_count'] == 2 and small['output_tokens_per_gpu_second'] == 25


def test_resumed_or_unknown_usage_does_not_create_misleading_throughput():
    arms = synthetic_throughput_arms()
    arms['matched']['tokens']['fresh_measurement'] = False
    result = hpg.throughput_summary({'replicas': 1, 'tensor_parallel': 4}, arms, 80)
    assert not result['fresh_measurement']
    assert result['aggregate_output_tokens_per_second'] is None
    assert result['benchmark_output_tokens_per_allocated_second'] is None
    assert result['complete_benchmark_articles_per_hour'] is None
    arms = synthetic_throughput_arms()
    arms['matched']['tokens']['per_article'][0]['output_tokens'] = None
    result = hpg.throughput_summary({'replicas': 1, 'tensor_parallel': 4}, arms, 80)
    assert result['total_output_tokens'] is None and result['output_tokens_per_gpu_second'] is None
    assert result['aggregate_input_tokens_per_second'] == 77.5


def test_final_report_persists_model_throughput_and_displays_actual_gpu_counts(tmp_path, monkeypatch):
    hpg.prepare(ROOT / hpg.DEFAULT_CONFIG, ROOT, tmp_path / 'campaign')
    campaign = hpg.read_campaign(tmp_path / 'campaign')
    monkeypatch.delenv('SLURM_JOB_GPUS', raising=False)
    monkeypatch.delenv('SLURM_STEP_GPUS', raising=False)
    monkeypatch.setenv('SLURM_GPUS_ON_NODE', '0')
    monkeypatch.setenv('CUDA_VISIBLE_DEVICES', '')
    for model in campaign['config']['models']:
        output = Path(campaign['work']) / 'results' / model['name']
        hpg.write_json(output / 'gpu.json', {'status': 'completed', 'gpu_stage_seconds': 80})
        hpg.write_json(output / 'cleanup.json', {'status': 'deleted'})
        arms = synthetic_throughput_arms()
        for arm, data in arms.items():
            data.update(tasks=10, valid_tasks=10, scores={k: {'matched': 161, 'forbidden_violations': 0} for k in ('raw', 'delivered')})
            hpg.write_json(output / arm / 'report.json', data)
    result = hpg.report(campaign)
    summary = json.loads(Path(result['summary']).read_text())
    assert [m['throughput']['gpu_count'] for m in summary['models']] == [4, 2, 2, 2]
    assert all(m['throughput']['aggregate_output_tokens_per_second'] == 50 for m in summary['models'])
    for model in campaign['config']['models']:
        assert (Path(campaign['work']) / 'results' / model['name'] / 'throughput.json').exists()
    text = Path(result['markdown']).read_text()
    assert 'Output tokens/GPU-second' in text and 'Complete benchmark articles/hour' in text
    assert '| k2-375b-nvfp4 | 4 | 50.00 | 12.50 |' in text
    assert '| k2-7b-fp8 | 2 | 50.00 | 25.00 |' in text


def small_download_campaign(tmp_path, monkeypatch):
    hpg.prepare(ROOT / hpg.DEFAULT_CONFIG, ROOT, tmp_path / 'campaign')
    campaign = hpg.read_campaign(tmp_path / 'campaign')
    model = copy.deepcopy(campaign['config']['models'][0]); model['expected_bytes'] = 8
    campaign['config']['disk_headroom_gb'] = 0
    metadata = tmp_path / 'tiny-metadata.json'
    hpg.write_json(metadata, {'files': [{'rfilename': 'config.json', 'size': 2},
        {'rfilename': 'model.safetensors', 'size': 6, 'lfs': {'sha256': hashlib.sha256(b'WEIGHT').hexdigest()}}]})
    model['metadata'] = str(metadata)
    hpg.write_json(tmp_path / 'campaign/setup.json', {'status': 'ready'})
    for key in ('HF_HOME', 'HF_HUB_CACHE', 'HF_XET_CACHE', 'HF_MODULES_CACHE', 'HF_XET_CHUNK_CACHE_SIZE_BYTES', 'XDG_CACHE_HOME', 'HF_HUB_OFFLINE', 'TRANSFORMERS_OFFLINE', 'CUDA_VISIBLE_DEVICES'):
        monkeypatch.setenv(key, '')
    monkeypatch.delenv('SLURM_JOB_GPUS', raising=False); monkeypatch.delenv('SLURM_STEP_GPUS', raising=False)
    return campaign, model


def fake_download(**kwargs):
    assert len(kwargs['revision']) == 40
    path = Path(kwargs['local_dir']); path.mkdir()
    (path / 'config.json').write_bytes(b'{}'); (path / 'model.safetensors').write_bytes(b'WEIGHT')


def test_owned_cleanup_after_partial_download_preserves_results_and_allows_next_model(tmp_path, monkeypatch):
    campaign, model = small_download_campaign(tmp_path, monkeypatch)
    def interrupted(**kwargs):
        fake_download(**kwargs)
        raise RuntimeError('Interrupted download')
    with pytest.raises(RuntimeError, match='Interrupted'):
        hpg.download(campaign, model, interrupted)
    path = hpg.active_path(campaign)
    assert path.exists() and not (path / 'ready.json').exists()
    assert Path(__import__('os').environ['HF_HOME']).is_relative_to(path)
    with pytest.raises(ValueError, match='Previous checkpoint'): hpg.download(campaign, model, fake_download)
    sentinel = Path(campaign['work']) / 'results/keep.json'; sentinel.write_text('KEEP')
    with pytest.raises(ValueError, match='another model'):
        hpg.cleanup(campaign, {**model, 'name': 'another-model'})
    hpg.cleanup(campaign, model)
    assert not path.exists() and sentinel.read_text() == 'KEEP'
    result = hpg.download(campaign, model, fake_download)
    assert result['downloaded_bytes'] == 8 and (path / 'ready.json').exists()
    hpg.cleanup(campaign, model)
    assert not path.exists()


def test_corrupt_weights_fail_cpu_verification_and_are_still_removable(tmp_path, monkeypatch):
    campaign, model = small_download_campaign(tmp_path, monkeypatch)
    def corrupt(**kwargs):
        fake_download(**kwargs)
        (Path(kwargs['local_dir']) / 'model.safetensors').write_bytes(b'BADBAD')
    with pytest.raises(ValueError, match='SHA256'): hpg.download(campaign, model, corrupt)
    assert not (hpg.active_path(campaign) / 'ready.json').exists()
    hpg.cleanup(campaign, model)
    assert not hpg.active_path(campaign).exists()


@pytest.mark.parametrize('gpu_ids', ['0', '0,1,2,3,4,5,6,7'])
def test_download_on_gpu_allocation_is_rejected_before_creating_weights(tmp_path, monkeypatch, gpu_ids):
    campaign, model = small_download_campaign(tmp_path, monkeypatch)
    monkeypatch.setenv('SLURM_JOB_GPUS', gpu_ids)
    with pytest.raises(RuntimeError, match='CPU-only'): hpg.download(campaign, model, fake_download)
    assert not hpg.active_path(campaign).exists()


def test_running_inference_lock_prevents_cleanup(tmp_path, monkeypatch):
    campaign, model = small_download_campaign(tmp_path, monkeypatch)
    hpg.download(campaign, model, fake_download)
    with hpg.model_lock(campaign['work']):
        with pytest.raises(BlockingIOError): hpg.cleanup(campaign, model)
    assert hpg.active_path(campaign).exists()
    hpg.cleanup(campaign, model)


def test_packaging_excludes_credentials_weights_and_the_large_runs_tree(tmp_path):
    output = tmp_path / 'benchmark.tar.gz'
    result = hpg.package(ROOT, output)
    assert result['bytes'] < 5_000_000
    with tarfile.open(output) as archive:
        names = archive.getnames()
        assert any(n.endswith('fixtures/requests.jsonl') for n in names)
        assert any(n.endswith('scripts/hpg_gpu.sbatch') for n in names)
        assert not any('/runs/' in n or '/models/' in n or '.env' in n or '.venv' in n for n in names)


@pytest.mark.asyncio
async def test_replay_reproduces_historical_factual_scoring_without_vision_or_grammar(tmp_path):
    """Replay actual stored model outputs through the mock SSE API and frozen gates."""
    _, _, _, requests, _ = fixtures(FIXTURES)
    base = ROOT / 'runs/medical-fidelity-v1/comparison/meta--muse-glimmer-30b'
    responses = {}
    for r in requests:
        i = r['identity']; bundle = json.loads((base / (i['article_id'] + '-' + i['patient_id'] + '.json')).read_text())
        response = bundle['generations'][r['task']] if r['task'] not in ('summary', 'timeline') else bundle['companions'][r['task']]['attempt_responses'][-1]
        responses[r['messages_sha256']] = response
    sent = []
    def handler(request):
        body = json.loads(request.content)
        if request.url.path == '/tokenize': return httpx.Response(200, json={'count': 1000})
        assert 'response_format' not in body and 'structured_outputs' not in body
        assert all(isinstance(m['content'], str) for m in body['messages'])
        sent.append(body)
        messages = body['messages'][:2]
        response = responses[json_digest(messages)]
        if response.get('error'): return httpx.Response(500)
        event = {'choices': [{'index': 0, 'delta': {'content': response['content'],
                 'reasoning_content': response.get('reasoning_text') or ''}, 'finish_reason': response['finish_reason']}],
                 'usage': {'prompt_tokens': 1000, 'completion_tokens': 200}}
        return httpx.Response(200, text='data: ' + json.dumps(event) + '\n\ndata: [DONE]\n\n', headers={'content-type': 'text/event-stream'})
    config = hpg.load_campaign(ROOT / hpg.DEFAULT_CONFIG, ROOT); model = config['models'][0]
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        replay = Replay(FIXTURES, tmp_path / 'run', model, config['arms']['matched'],
                        ['http://127.0.0.1:8000/v1', 'http://127.0.0.1:8001/v1'], 8, 60, 65536, http=http)
        try:
            report = await replay.run()
            historical = json.loads((FIXTURES / 'historical-scores.json').read_text())['models']['meta/muse-glimmer-30b']
            assert report['scores']['raw']['matched'] == historical['raw']['matched']
            assert report['scores']['delivered']['matched'] == historical['delivered']['matched']
            assert report['tasks'] == 176 and len(sent) >= 176
            assert report['tokens']['per_article_stats_with_patients']['input_tokens']['n'] == 7
            assert report['tokens']['aggregate_output_tokens_per_second'] > 0
            assert report['tokens']['per_article_stats_with_patients']['reasoning_tokens']['unknown_articles'] == 7
            assert report['tokens']['per_article_stats_with_patients']['reasoning_tokens']['mean'] is None
            for p in (tmp_path / 'run').glob('PMC*-p*.json'):
                patient = json.loads(p.read_text())
                assert patient['model']['capabilities']['vision'] is False
                assert patient['vision']['status'] == 'unsupported_by_model'
                assert not patient['source']['multimedia']['pixels_inspected']
                assert not {'visual_findings', 'pixel_observations', 'pixel_interpretations'} & patient.keys()
                assert patient['source']['article_source']['license']
            again = await replay.run()
            assert again['resumed_tasks'] == 176
            assert again['tokens']['aggregate_output_tokens_per_second'] is None
        finally: await replay.close()


@pytest.mark.asyncio
async def test_context_guard_preserves_full_source_and_never_calls_completion(tmp_path):
    config = hpg.load_campaign(ROOT / hpg.DEFAULT_CONFIG, ROOT)
    sent = []
    def handler(request):
        sent.append(request.url.path)
        return httpx.Response(200, json={'count': 64000})
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        replay = Replay(FIXTURES, tmp_path / 'run', config['models'][0], config['arms']['matched'],
                        ['http://127.0.0.1:8000/v1'], 1, 60, 65536, http=http)
        original = copy.deepcopy(replay.requests[0]['messages'])
        try:
            result = await replay.call(replay.requests[0])
            assert result['status'] == 'failed' and 'context_guard' in result['errors'][0]
            assert sent == ['/tokenize'] and replay.requests[0]['messages'] == original
        finally: await replay.close()


@pytest.mark.asyncio
async def test_secondary_shared_figure_assignment_is_attached_to_each_patient_and_failures_stay_missing(tmp_path):
    from openpatients2.article_tasks import task_messages
    from openpatients2.figure_attribution import figure_messages
    _, articles, packets, _, _ = fixtures(FIXTURES)
    config = hpg.load_campaign(ROOT / hpg.DEFAULT_CONFIG, ROOT)
    output = tmp_path / 'run'; output.mkdir()
    rosters = {aid: json.loads((FIXTURES / 'rosters' / (aid + '.json')).read_text())['roster'] for aid in articles}
    responses = {}; shared = None; bad = None
    for aid, article in articles.items():
        roster = rosters[aid]
        responses[json_digest(task_messages('roster', article))] = roster
        for figure in article['figures']:
            fid = figure['figure_key']
            assignment = {'panel': None, 'patient_ids': [], 'scope': 'unresolved', 'subject': 'unresolved',
                          'evidence': [], 'rationale': 'Unresolved in this synthetic transport test.'}
            reference = next((f for f in roster['figures'] if f['figure_id'] == fid and f['scope'] == 'shared'), None)
            if reference and shared is None:
                assignment.update({'patient_ids': reference['patient_ids'], 'scope': 'shared', 'subject': 'patient',
                                   'evidence': reference['evidence']})
                shared = (aid, fid, reference['patient_ids'])
            responses[json_digest(figure_messages(article, roster, fid))] = {'figure_id': fid, 'assignments': [assignment], 'limitations': []}
            if bad is None and not reference and roster['patients']: bad = (aid, fid)
    assert shared is not None
    bad_key = json_digest(figure_messages(articles[bad[0]], rosters[bad[0]], bad[1]))
    for rid, packet in packets.items():
        hpg.write_json(output / (rid.replace(':', '-') + '.json'), {'source': packet, 'vision': {'status': 'unsupported_by_model'}})
    def handler(request):
        body = json.loads(request.content)
        if request.url.path == '/tokenize': return httpx.Response(200, json={'count': 1000})
        key = json_digest(body['messages'][:2])
        content = '{' if key == bad_key else json.dumps(responses[key])
        event = {'choices': [{'index': 0, 'delta': {'content': content}, 'finish_reason': 'stop'}],
                 'usage': {'prompt_tokens': 1000, 'completion_tokens': 200}}
        return httpx.Response(200, text='data: ' + json.dumps(event) + '\n\ndata: [DONE]\n\n')
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        replay = Replay(FIXTURES, output, config['models'][0], config['arms']['ifm_high'],
                        ['http://127.0.0.1:8000/v1'], 8, 60, 65536, http=http)
        try: report = await replay.secondary()
        finally: await replay.close()
    assert report['task_counts'] == {'roster': 9, 'figure_attribution': 31}
    assert report['figure_semantic_accuracy'] is None and report['pixel_accuracy'] is None
    for pid in shared[2]:
        p = json.loads((output / (shared[0] + '-' + pid + '.json')).read_text())
        assert any(a['figure_id'] == shared[1] and a['patient_ids'] == shared[2] for a in p['source']['figure_assignments'])
        assert any(f['figure_key'] == shared[1] and f['image_urls'] for f in p['source']['multimedia']['figures'])
        assert p['source']['multimedia']['pixels_inspected'] is False
    for patient in rosters[bad[0]]['patients']:
        p = json.loads((output / (bad[0] + '-' + patient['patient_id'] + '.json')).read_text())
        assert bad[1] in p['source']['multimedia']['unreviewed_figure_ids']
        assert not any(a['figure_id'] == bad[1] for a in p['source']['figure_assignments'])


@pytest.mark.asyncio
async def test_failed_gpu_start_stops_all_servers_and_leaves_checkpoint_for_cpu_cleanup(tmp_path, monkeypatch):
    campaign, model = small_download_campaign(tmp_path, monkeypatch)
    sif = tmp_path / 'fake.sif'; sif.write_bytes(b'SYNTHETIC IMAGE')
    hpg.write_json(Path(campaign['work']) / 'setup.json', {'sif': str(sif), 'sha256': hpg.sha256(sif)})
    hpg.download(campaign, model, fake_download)
    monkeypatch.setenv('SLURM_JOB_ID', 'SYNTHETIC_TEST')
    monkeypatch.setenv('CUDA_VISIBLE_DEVICES', '0,1,2,3')
    def run(command, **kwargs):
        if command[0] == 'apptainer':
            assert 'HF_HUB_OFFLINE=1' in command and 'TRANSFORMERS_OFFLINE=1' in command
            probe = command[-1]
            compile(probe, '<container-probe>', 'exec')
            assert 'torch.cuda.device_count()==4' in probe and 'range(8)' not in probe
        return subprocess.CompletedProcess(command, 0, stdout='SYNTHETIC GPU PROBE', stderr='')
    monkeypatch.setattr(hpg.subprocess, 'run', run)
    monkeypatch.setattr(hpg, 'environment_report', lambda: {'test_fixture': True})
    stopped = []
    class FailedServers:
        def __init__(self, *args): pass
        async def start(self, *args): raise RuntimeError('SYNTHETIC unsupported kernel')
        async def stop(self): stopped.append(True)
    monkeypatch.setattr(hpg, 'ServerGroup', FailedServers)
    with pytest.raises(RuntimeError, match='unsupported kernel'): await hpg.gpu(campaign, model)
    assert stopped == [True] and hpg.active_path(campaign).exists()
    hpg.cleanup(campaign, model)
    assert not hpg.active_path(campaign).exists()


def test_handled_download_failure_cancels_only_its_gpu_and_keeps_cleanup(tmp_path, monkeypatch):
    campaign, model = small_download_campaign(tmp_path, monkeypatch)
    work = Path(campaign['work'])
    hpg.write_json(work / 'jobs.json', {'submitted': True, 'jobs': [
        {'id': '101', 'stage': 'gpu', 'model': model['name']},
        {'id': '102', 'stage': 'cleanup', 'model': model['name']},
        {'id': '103', 'stage': 'gpu', 'model': 'another-model'}]})
    calls = []
    monkeypatch.setattr(hpg, 'read_campaign', lambda work: campaign)
    monkeypatch.setattr(hpg, 'download', lambda *args: (_ for _ in ()).throw(RuntimeError('SYNTHETIC failure')))
    monkeypatch.setattr(hpg.subprocess, 'run', lambda args, **kwargs: calls.append(args))
    with pytest.raises(RuntimeError, match='SYNTHETIC'): hpg.stage('download', work, model['name'])
    assert calls == [['scancel', '101']]
    assert json.loads((work / 'results' / model['name'] / 'download.json').read_text())['status'] == 'failed'
