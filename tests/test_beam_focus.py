import asyncio
import copy
import json
import os
from pathlib import Path
import subprocess
from types import SimpleNamespace

import httpx
import pytest

from openpatients2 import dense_benchmark as b, dense_nemotron_super as s
from openpatients2 import beam_recipe as beam, staged_timeline as timeline
from openpatients2.benchmark_reporting import throughput, paired_speculation
from openpatients2.super_packing import plan
from openpatients2.evidence_recovery import resolve_timeline_spans
from openpatients2.pilot_extract import sources_for
from test_dense_nemotron_super import campaign


def test_beam_smoke_rejects_server_with_old_context_limit(campaign, tmp_path, monkeypatch):
    config = b.read(campaign[0] / 'arms/nvfp4-text/manifest.json')['extraction']
    real_client = httpx.AsyncClient
    def handle(request):
        assert request.url.path == '/tokenize'
        return httpx.Response(200, json={'count': 10, 'tokens': [1] * 10, 'max_model_len': 65536})
    monkeypatch.setattr(httpx, 'AsyncClient', lambda **kw: real_client(transport=httpx.MockTransport(handle), **kw))
    with pytest.raises(ValueError, match='tokenizer contract'):
        asyncio.run(s.smoke('http://localhost:8000/v1', tmp_path, config, False, 131072))


def test_beam_eight_gpu_allocation_shared_weights_and_ablation(campaign, tmp_path):
    one = plan(['beam-nvfp4-dflash2'])
    assert one['resources']['beam-nvfp4-dflash2']['replicas'] == 2
    assert one['waves'][0]['totals'] == {'gpus': 8, 'cpus': 32, 'memory_gib': 192}
    assert one['waves'][0]['required_free_gib'] == 418  # one target shared by two replicas
    profiles = ['beam-nvfp4-dflash2', 'beam-nvfp4-no-spec']
    assert plan(profiles)['waves'][0]['totals'] == one['waves'][0]['totals']
    work = tmp_path / 'beam-only'
    s.prepare(campaign[2], work, expected=3, profiles=profiles, scheduler='packed')
    manifest = s.check(work)
    jobs = s.submit(work, 'account', 'qos', dry_run=True)
    workers = [c for c in jobs['commands_without_dependencies'] if c[-3] == 'worker']
    assert len(workers) == 2
    assert all('--gres=gpu:b200:4' in c and '--mem=96G' in c for c in workers)
    for name in profiles:
        arm = name + '-text'
        config = b.read(work / 'arms' / arm / 'manifest.json')
        assert config['context'] == 131072
        assert config['extraction']['timeline_bounded_context']
        cfg = s.serving(work, arm)
        assert cfg.max_model_len == 131072 and cfg.reasoning_parser == 'beam'
        assert (cfg.speculative_config is None) == (name.endswith('no-spec'))
    assert manifest['nemotron_super']['with_images'] is False
    control, trial = [beam.PROFILES[p] for p in profiles]
    exclude = {'drafter', 'speculation', 'adaptation'}
    assert {k:v for k,v in control.items() if k not in exclude} == {k:v for k,v in trial.items() if k not in exclude}
    assert s.profile_models(profiles[1]) == [beam.NVFP4]


def test_beam_launcher_pilot_rejects_stale_model_split_overrides(tmp_path):
    root = Path(__file__).resolve().parents[1]
    scripts = tmp_path / 'scripts'; scripts.mkdir()
    (scripts / 'run_dense_beam.sh').write_text((root / 'scripts/run_dense_beam.sh').read_text())
    (scripts / 'run_dense_nemotron_super.sh').write_text('env | sort\n')
    env = {**os.environ, 'DENSE_SUPER_PROFILES': 'spark', 'DENSE_INCLUDE_TEST': '1', 'DENSE_SUPER_ARTICLE_IDS': 'PMC1'}
    env.pop('DENSE_HOURS', None); env.pop('DENSE_BEAM_PILOT_ARTICLES', None)
    output = subprocess.check_output(['bash', str(scripts / 'run_dense_beam.sh'), 'pilot'], env=env, text=True)
    config = dict(line.split('=', 1) for line in output.splitlines() if '=' in line)
    assert config['DENSE_SUPER_PROFILES'] == 'beam-nvfp4-dflash2 beam-nvfp4-no-spec'
    assert config['DENSE_SUPER_LIMIT_ARTICLES'] == '48' and config['DENSE_HOURS'] == '24'
    assert config['DENSE_INCLUDE_TEST'] == '0' and config['DENSE_SUPER_SPLITS'] == 'train'


def test_mean_tokens_and_paired_speedup_use_common_work_not_acceptance():
    row = {'wall_seconds': 10, 'prompt_tokens': 100, 'completion_tokens': 40, 'reasoning_tokens': 30, 'model_calls': 2}
    rates = throughput([row, {**row, 'prompt_tokens': 300}])
    assert rates['mean_input_tokens_per_article'] == 200
    assert rates['mean_output_tokens_per_article'] == 40
    assert rates['mean_answer_tokens_per_article'] == 10
    assert rates['mean_model_calls_per_article'] == 2
    assert throughput([row, {**row, 'prompt_tokens': None}])['mean_input_tokens_per_article'] is None
    comp = paired_speculation({'off': {'a': row, 'b': {**row, 'wall_seconds': 999}},
                              'on': {'a': {**row, 'wall_seconds': 5}, 'b': None}},
        {'off': {'profile': 'beam-nvfp4-no-spec'}, 'on': {'profile': 'beam-nvfp4-dflash2'}})[0]
    assert comp['pipeline_speedup'] == 2 and comp['article_ids'] == ['a']


async def test_bounded_timeline_windows_keep_all_focus_segments_and_distant_pairs(tmp_path, monkeypatch):
    article = {'article_id': 'PMC1.1', 'segments': [
        {'segment_id': f's{i}', 'text': f'Patient had event {i}.'} for i in range(6)]}
    rid = 'PMC1.1:p1'; _, sources = sources_for(article)
    calls = []
    async def fits(runner, task, messages, replica):
        source = json.loads(messages[-1]['content'].split('SOURCE_JSON:\n')[1].split('\nTASK:\n')[0])
        return len(source['segments']) < 6
    monkeypatch.setattr(timeline, 'fits', fits)
    class Runner:
        config = SimpleNamespace(timeline_segment_batch_size=1, timeline_relation_batch_size=1,
            timeline_bounded_context=True, timeline_target_batch_size=2, refinement_policy='source_aware')
        output = tmp_path; arm = 'targeted'; results = []
        async def call(self, task, messages, checker, identity, replica, segments, **kwargs):
            calls.append(identity)
            if task == 'timeline_events':
                sid = identity['focus_segments'][0]; seg = next(s for s in segments if s['segment_id'] == sid)
                event = {'event_id': 'local', 'record_id': rid, 'episode_id': None, 'kind': 'other',
                    'occurrence': 'occurred', 'description': seg['text'], 'fact_ids': [],
                    'evidence': [{'segment_id': sid, 'quote': seg['text']}],
                    'attribution_evidence': [{'segment_id': sid, 'quote': seg['text']}], 'times': []}
                value = {'schema_version': 'patient-timeline/2', 'record_id': rid, 'events': [event], 'edges': [], 'limitations': []}
            else:
                assert len(identity['target_event_ids']) <= 2
                value = {'edges': [], 'limitations': []}
            value, _ = resolve_timeline_spans(value, 'PMC1.1:jats', segments)
            return {'task': task, 'identity': identity, 'status': 'valid', 'data': checker(value), 'attempts': []}
    (tmp_path / 'tasks').mkdir()
    result = await timeline.extract(Runner(), article, {'patient_id': 'p1'}, {}, [],
        {'article_id': 'PMC1.1', 'record_id': rid}, 0, sources)
    assert result['status'] == 'valid' and len(result['data']['events']) == 6
    assert {s for c in calls if c['timeline_stage'] == 'events' for s in c['focus_segments']} == {f's{i}' for i in range(6)}
    pairs = [frozenset(pair) for c in calls if c['timeline_stage'] == 'relations' for pair in c['candidate_event_pairs']]
    assert frozenset(('e1', 'e6')) in pairs and len(pairs) == len(set(pairs)) == 15
    assert any(c['source_scope'] == 'whole_segment_window' for c in calls)
    assert not result['semantic_coverage_verified']


async def test_length_failure_splits_focus_without_discarding_attempts(tmp_path, monkeypatch):
    article = {'article_id': 'PMC1.1', 'segments': [{'segment_id': f's{i}', 'text': f'text {i}'} for i in range(2)]}
    _, sources = sources_for(article)
    async def fits(*args): return True
    monkeypatch.setattr(timeline, 'fits', fits)
    class Runner:
        config = SimpleNamespace(timeline_segment_batch_size=2, timeline_relation_batch_size=1,
            timeline_bounded_context=True, timeline_target_batch_size=2, refinement_policy='source_aware')
        output = tmp_path; arm = 'targeted'; results = []
        async def call(self, task, messages, checker, identity, replica, segments, **kwargs):
            failed = len(identity['focus_segments']) == 2
            data = None if failed else {'schema_version': 'patient-timeline/2', 'record_id': 'PMC1.1:p1', 'events': [], 'edges': [], 'limitations': []}
            return {'task': task, 'identity': identity, 'status': 'failed' if failed else 'valid',
                'data': data, 'errors': ['incomplete_finish:length'] if failed else [],
                'attempts': [{'response': {'reasoning_text': 'preserved'}, 'failed': failed}]}
    (tmp_path / 'tasks').mkdir()
    result = await timeline.extract(Runner(), article, {'patient_id': 'p1'}, {}, [],
        {'article_id': 'PMC1.1', 'record_id': 'PMC1.1:p1'}, 0, sources)
    assert result['status'] == 'valid' and len(result['attempts']) == 3
    assert result['stage_coverage'][0]['status'] == 'replaced_by_split'


def test_shared_artifacts_survive_donor_cleanup(tmp_path, monkeypatch):
    from contextlib import nullcontext
    root = tmp_path / 'campaign'
    donor = root / 'profiles/beam-nvfp4-dflash2'
    target = root / 'profiles/beam-nvfp4-no-spec'
    for p in (donor, target):
        (p / 'runtime').mkdir(parents=True)
    b.write(donor / 'runtime/active-profile.json', {'ready': True})
    source = s.weights_path(donor, beam.NVFP4)
    source.mkdir(parents=True)
    (source / 'weights.safetensors').write_bytes(b'fixture')
    (source / '.cache').mkdir()
    (source / '.cache/private').write_text('not shared')
    image = s.engine(beam.PROFILES['beam-nvfp4-dflash2'])['sif']
    (donor / 'runtime' / image).write_bytes(b'image')
    manifest = {'nemotron_super': {'profiles': ['beam-nvfp4-dflash2', 'beam-nvfp4-no-spec'], 'arms': {'arm': {}}}}
    monkeypatch.setattr(s, 'check', lambda p: manifest)
    monkeypatch.setattr(s, 'runtime_lock', lambda *a, **kw: nullcontext())
    monkeypatch.setattr(s, 'validate_runtime', lambda *a: None)
    assert s.reuse_packed_artifacts(target, 'beam-nvfp4-no-spec') == {beam.NVFP4}
    dest = s.weights_path(target, beam.NVFP4)
    assert (dest / 'weights.safetensors').stat().st_ino == (source / 'weights.safetensors').stat().st_ino
    assert not (dest / '.cache').exists()
    import shutil
    shutil.rmtree(donor)
    assert (dest / 'weights.safetensors').read_bytes() == b'fixture'
    assert (target / 'runtime' / image).read_bytes() == b'image'
