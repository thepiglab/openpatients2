import copy
import json
import os
from pathlib import Path
import signal
import select
import subprocess
import sys

import pytest
import yaml

from openpatients2 import glimmer_benchmark as g, glimmer_tuning as tuning, hipergator as hpg
from openpatients2.serving import ServerGroup, ServingConfig, render

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / 'configs/hipergator/glimmer-fp8-tuning.yaml'


def test_focused_plan_only_downloads_fp8_and_dflash_without_gpus(tmp_path):
    plan = hpg.prepare(CONFIG, ROOT, tmp_path / 'campaign')
    assert [j['stage'] for j in plan['jobs']] == ['setup', 'download', 'gpu', 'cleanup', 'report']
    config = hpg.read_campaign(tmp_path / 'campaign')['config']
    assert len(config['models']) == 1 and not config['container_plugins'] and 'dspark_metadata' not in config
    assert {a['seed'] for a in config['arms'].values()} == {42, 1729, 5724}
    assert len(config['layouts']) * len(config['throughput']['concurrency_per_replica']) == 40
    assert config['throughput']['subset_requests'] * config['throughput']['copies'] == 512
    for j in plan['jobs']:
        assert '--nodes=1' in j['command']
        assert ('--gres=gpu:b200:8' if j['stage'] == 'gpu' else '--gres=none') in j['command']


def test_tuning_profiles_change_real_flags_and_use_eight_disjoint_devices(tmp_path, monkeypatch):
    monkeypatch.setenv('CUDA_VISIBLE_DEVICES', ','.join(str(i) for i in range(8)))
    hpg.prepare(CONFIG, ROOT, tmp_path / 'campaign'); campaign = hpg.read_campaign(tmp_path / 'campaign')
    work = Path(campaign['work'])
    hpg.write_json(work / 'setup.json', {'sif': '/tmp/synthetic.sif', 'container_python': '/usr/bin/python3'})
    for layout in campaign['config']['layouts']:
        cfg = g.serving_config(campaign, campaign['config']['models'][0], layout)
        commands = render(cfg, str(work))
        assert sorted(d for c in commands for d in c['devices']) == list(map(str, range(8)))
        args = commands[0]['argv']
        assert int(args[args.index('--max-num-batched-tokens')+1]) == layout.get('max_batched_tokens', 16384)
        assert args[args.index('--kv-cache-dtype')+1] == layout.get('kv_cache_dtype', 'auto')
        assert ('--calculate-kv-scales' in args) == (layout.get('kv_cache_dtype') == 'fp8_e4m3')
        assert ('--enforce-eager' in args) == bool(layout.get('enforce_eager'))
        assert ('--no-enable-prefix-caching' in args) == (layout.get('prefix_cache') is False)
        if layout.get('speculation'):
            assert json.loads(args[args.index('--speculative-config')+1])['num_speculative_tokens'] == 15


def test_medium_and_three_seed_controls_required_before_submission(tmp_path):
    config = yaml.safe_load(CONFIG.read_text())
    config['arms']['medium_seed42']['reasoning_effort'] = 'high'
    bad = tmp_path / 'bad.yaml'; bad.write_text(yaml.safe_dump(config))
    with pytest.raises(ValueError, match='Keep medium'): hpg.load_campaign(bad, ROOT)
    config = yaml.safe_load(CONFIG.read_text())
    for arm in config['arms'].values(): arm['seed'] = 42
    bad.write_text(yaml.safe_dump(config))
    with pytest.raises(ValueError, match='three independent'): hpg.load_campaign(bad, ROOT)


def test_memory_and_new_compute_processes_both_gate_layout_reuse():
    baseline = {'devices': {'GPU-a': {'memory_used_mib': 100}}, 'processes': []}
    current = copy.deepcopy(baseline)
    current['devices']['GPU-a']['memory_used_mib'] = 900
    assert tuning.unreleased_gpus(baseline, current, 512) == ['GPU-a']
    current['devices']['GPU-a']['memory_used_mib'] = 110
    current['processes'] = [{'gpu_uuid': 'GPU-a', 'pid': 123}]
    assert tuning.unreleased_gpus(baseline, current, 512) == ['GPU-a']
    current['processes'] = []
    assert tuning.unreleased_gpus(baseline, current, 512) == []


@pytest.mark.asyncio
async def test_failed_gpu_drain_records_owners_and_aborts(tmp_path, monkeypatch):
    baseline = {'devices': {'GPU-a': {'memory_used_mib': 100}}, 'processes': []}
    dirty = {'devices': {'GPU-a': {'memory_used_mib': 2000}}, 'processes': [{'gpu_uuid': 'GPU-a', 'pid': 123}]}
    monkeypatch.setattr(tuning, 'gpu_snapshot', lambda: dirty)
    with pytest.raises(RuntimeError, match='no next layout'):
        await tuning.wait_for_release(baseline, tmp_path / 'drain.json', timeout=0)
    assert json.loads((tmp_path / 'drain.json').read_text())['current']['processes'][0]['pid'] == 123


def test_shortlist_keeps_prefix_and_kv_regimes_separate():
    def cell(name, rate, **layout):
        return {'eligible_for_selection': True, 'layout': {'name': name, **layout},
                'valid_tasks_per_second': rate, 'aggregate_output_tokens_per_second': rate*1000}
    rows = [cell('slow', 1), cell('warm', 4), cell('no-prefix', 2, prefix_cache=False),
            cell('fp8kv', 5, kv_cache_dtype='fp8_e4m3')]
    assert [c['layout']['name'] for c in tuning.shortlist(rows)] == ['fp8kv', 'warm', 'no-prefix']


def test_paired_quality_guard_rejects_lost_facts_and_new_forbidden_hits():
    def result(facts, valid=170, forbidden=0):
        return {'valid_tasks': valid, 'scores': {'delivered': {'matched': facts, 'forbidden_violations': forbidden}}}
    ref = {str(seed): result(120) for seed in (42,1729,5724)}
    good = {k: result(117) for k in ref}
    assert tuning.quality_stability(ref, good, 5, 4)['passed']
    good['42'] = result(114)
    assert not tuning.quality_stability(ref, good, 5, 4)['passed']
    good['42'] = result(120, forbidden=1)
    assert not tuning.quality_stability(ref, good, 5, 4)['passed']


def test_stop_signals_workers_even_after_launcher_exit(tmp_path):
    config = ServingConfig.load(str(ROOT / 'configs/serving/k2-vllm-tp8.yaml'))
    group = ServerGroup(config, str(tmp_path), str(tmp_path / 'logs'))
    pidfile = tmp_path / 'child.pid'
    program = "import subprocess,sys; from pathlib import Path; p=subprocess.Popen([sys.executable,'-c','import time; time.sleep(90)']); Path(sys.argv[1]).write_text(str(p.pid))"
    launcher = subprocess.Popen([sys.executable, '-c', program, str(pidfile)], start_new_session=True, stdout=subprocess.PIPE)
    launcher.wait(timeout=10); child = int(pidfile.read_text()); group.processes = [launcher]
    try:
        group.stop()
        # The sleeping child inherits this pipe. EOF proves it no longer holds
        # descriptors after its launcher exited, without privileged ps access.
        assert select.select([launcher.stdout], [], [], 5)[0]
        assert launcher.stdout.read() == b''
    finally:
        launcher.stdout.close()
        try: os.kill(child, signal.SIGKILL)
        except ProcessLookupError: pass


def test_tuning_report_includes_confirmation_and_missing_stages(tmp_path):
    hpg.prepare(CONFIG, ROOT, tmp_path / 'campaign'); campaign = hpg.read_campaign(tmp_path / 'campaign')
    result = tuning.report_tuning(campaign)
    assert result['failed_records'] == 1
    text = (Path(campaign['work']) / 'SUMMARY.md').read_text()
    assert 'missing' in text and 'pixel adjudication remains necessary' in text


@pytest.mark.asyncio
async def test_full_tuning_orchestration_confirms_each_cache_regime_and_cleans_every_group(tmp_path, monkeypatch):
    from openpatients2.provenance import json_digest
    monkeypatch.setenv('SLURM_JOB_ID', 'synthetic')
    monkeypatch.setenv('CUDA_VISIBLE_DEVICES', ','.join(map(str, range(8))))
    hpg.prepare(CONFIG, ROOT, tmp_path / 'campaign'); campaign = hpg.read_campaign(tmp_path / 'campaign')
    work = Path(campaign['work']); active = work / 'active-model'; active.mkdir()
    root = work / 'results/glimmer-fp8'; root.mkdir()
    hpg.write_json(work / 'vision-assets/manifest.json', {'synthetic':True})
    audit = {'files': [], 'vision_manifest_sha256':hpg.sha256(work / 'vision-assets/manifest.json')}
    hpg.write_json(root / 'download.json', audit)
    hpg.write_json(active / 'ready.json', {'campaign': campaign['id'], 'model': 'glimmer-fp8', 'audit_sha256': json_digest(audit)})
    hpg.write_json(work / 'setup.json', {'sif': '/tmp/synthetic.sif', 'container_python': '/usr/bin/python3'})
    monkeypatch.setattr(hpg, 'check_owner', lambda *args: None)
    monkeypatch.setattr(g, 'gpu_probe', lambda *args: None)
    monkeypatch.setattr(tuning, 'gpu_snapshot', lambda: {'devices': {'GPU-'+str(i): {'memory_used_mib': 0} for i in range(8)}, 'processes': []})
    groups = []
    class Group:
        def __init__(self, cfg, *args):
            self.commands = [{'endpoint': f'http://127.0.0.1:{8000+i}/v1'} for i in range(cfg.replicas)]
            self.stopped = False; groups.append(self)
        async def start(self, timeout): pass
        def stop(self): self.stopped = True
    class Monitor:
        def terminate(self): pass
        def wait(self, timeout): pass
    monkeypatch.setattr(tuning, 'ServerGroup', Group)
    monkeypatch.setattr(tuning.subprocess, 'Popen', lambda *a, **kw: Monitor())
    async def nothing(*args): pass
    monkeypatch.setattr(g, 'smoke', nothing); monkeypatch.setattr(g, 'article_lengths', nothing)
    async def cell(campaign, model, layout, endpoints, concurrency, output):
        return {'status': 'completed', 'eligible_for_selection': True, 'layout': layout,
            'concurrency_per_replica': concurrency, 'valid_tasks_per_second': concurrency,
            'aggregate_output_tokens_per_second': concurrency*1000}
    monkeypatch.setattr(g, 'throughput_cell', cell)
    quality_calls = []
    async def quality(campaign, model, layout, endpoints, concurrency, root, prefix, all_results):
        quality_calls.append((prefix, concurrency))
        results = {name: {'valid_tasks': 170, 'scores': {'delivered': {'matched': 120, 'forbidden_violations': 0}}}
                   for name in campaign['config']['arms']}
        all_results.update({prefix+name: result for name,result in results.items()})
        return results
    monkeypatch.setattr(tuning, 'quality_runs', quality)
    from openpatients2 import glimmer_vision
    async def vision(*args): return {'status':'completed', 'rows':93}
    monkeypatch.setattr(glimmer_vision, 'evaluate_images', vision)
    result = await tuning.gpu_tuning(campaign, campaign['config']['models'][0])
    assert result['status'] == 'completed' and len(result['confirmations']) == 3
    assert len(result['quality_arms']) == 12 and all(c['stability']['passed'] for c in result['confirmations'])
    assert quality_calls == [('', 8), ('confirm0_', 64), ('confirm1_', 64), ('confirm2_', 64)]
    assert len(groups) == 15 and all(group.stopped for group in groups)
    assert set(result['vision_evaluations']) == {'ordinary','dflash'}
