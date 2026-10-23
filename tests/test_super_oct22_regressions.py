"""Failures observed in the 2026-10-22 packed hardware smoke."""
import copy
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
from types import SimpleNamespace

from openpatients2 import dense_benchmark_telemetry as telemetry
from openpatients2.longitudinal import partial_timeline, PatientTimeline, audit_timeline
from test_robust_extraction import timeline, edge, event, SOURCES, RID


def test_failure_index_exposes_item_errors_and_keeps_successes_out(tmp_path):
    from openpatients2.benchmark_failures import extraction_failures
    attempt = tmp_path / 'results/articles/PMC1/attempt-0000'
    tasks = attempt / 'extraction/tasks'
    tasks.mkdir(parents=True)
    (tasks / 'partial.json').write_text(json.dumps({
        'task': 'observations', 'status': 'partial', 'errors': ['targeted_items_unresolved'],
        'identity': {'patient_id': 'p2'}, 'attempts': [
            {'item_repair': {'pending': [{'index': 3, 'item': {'name': 'FDP'},
                                        'errors': ['Refused field erasure: /unit']},
                                       {'index': 4, 'item': 'malformed draft', 'errors': ['not an object']}]}}]}))
    (tasks / 'valid.json').write_text(json.dumps({'task': 'conditions', 'status': 'valid'}))
    (attempt / 'receipt.json').write_text(json.dumps({'ehr_errors': {'series': 'length'}}))
    report = extraction_failures(tmp_path, [('PMC1', 'train', attempt)])
    assert report['by_task'] == {'observations': 1, 'ehr/series': 1}
    assert report['by_article'] == {'PMC1': 2}
    assert report['records'][0]['repair_history'][0]['pending_items'][0]['errors'] == ['Refused field erasure: /unit']
    assert report['records'][0]['task_file'].startswith('results/articles/')


def test_packed_aggregate_bootstraps_missing_parent_environment(tmp_path):
    work = tmp_path / 'campaign'
    (work / 'code/scripts').mkdir(parents=True)
    (work / 'code/scripts/benchmark_dense_nemotron_super.py').write_text(
        "import httpx,sys\nfrom pathlib import Path\n"
        "assert sys.argv[1] == 'aggregate'\n"
        "Path(sys.argv[3], 'scored').write_text('ok')\n")
    binaries = tmp_path / 'bin'; binaries.mkdir()
    uv = binaries / 'uv'
    uv.write_text(f'#!{sys.executable}\nimport os,sys,pathlib\n'
                  "assert sys.argv[1:] == ['sync','--locked','--no-dev','--python','3.12']\n"
                  "p=pathlib.Path(os.environ['UV_PROJECT_ENVIRONMENT'])/'bin'\n"
                  "p.mkdir(parents=True)\n"
                  f"(p/'python').write_text({('#!/bin/sh\nexec ' + shlex.quote(sys.executable) + ' "$@"\n')!r})\n"
                  "(p/'python').chmod(0o755)\n")
    uv.chmod(0o755)
    # Falling back to bare python3 is the actual old failure, not a valid path.
    python3 = binaries / 'python3'; python3.write_text('#!/bin/sh\nexit 89\n'); python3.chmod(0o755)
    script = Path(__file__).parents[1] / 'scripts/dense_nemotron_super.sbatch'
    subprocess.run(['bash', str(script), 'aggregate', str(work)], check=True,
                   env={**os.environ, 'PATH': str(binaries) + os.pathsep + os.environ['PATH']})
    assert (work / 'scored').read_text() == 'ok'


def test_remapped_gpu_indices_do_not_silently_drop_half_the_allocation(monkeypatch):
    monkeypatch.setenv('SLURM_STEP_GPUS', '1,3,5,7')
    output = '\n'.join(f'time,{i},GPU-{i},0,0,100,200,50' for i in range(4))
    monkeypatch.setattr(telemetry.subprocess, 'run', lambda *a, **k:
                        SimpleNamespace(stdout=output, stderr='', returncode=0))
    result = telemetry.sample()
    assert len(result['gpus']) == 4 and result['allocation_complete']
    assert result['allocation_match'] == 'visible_device_count'
    # If the whole node is visible, select just the allocated physical IDs.
    output += '\n' + '\n'.join(f'time,{i},GPU-{i},0,0,100,200,50' for i in range(4, 8))
    assert [r[1] for r in telemetry.sample()['gpus']] == ['1','3','5','7']


def test_cgroup_parent_limit_and_cache_are_reported_separately(monkeypatch):
    files = {'/proc/self/cgroup': '0::/slurm/job1/task_0',
             '/sys/fs/cgroup/slurm/job1/task_0/memory.current': '1000',
             '/sys/fs/cgroup/slurm/job1/task_0/memory.peak': '1100',
             '/sys/fs/cgroup/slurm/job1/task_0/memory.max': 'max',
             '/sys/fs/cgroup/slurm/job1/memory.max': '1200',
             '/sys/fs/cgroup/slurm/memory.max': '3000',
             '/sys/fs/cgroup/slurm/job1/task_0/memory.stat': 'anon 200\nfile 800\n',
             '/sys/fs/cgroup/slurm/job1/task_0/memory.events': 'oom 0\noom_kill 0\n'}
    def read(path, *a, **k):
        if str(path) not in files: raise FileNotFoundError(path)
        return files[str(path)]
    monkeypatch.setattr(Path, 'read_text', read)
    result = telemetry.host_memory()
    assert result['limit_bytes'] is None and result['effective_limit_bytes'] == 1200
    assert result['stat'] == {'anon': 200, 'file': 800}
    assert result['events']['oom_kill'] == 0


def test_timeline_cycle_preserves_unrelated_relations_without_picking_a_winner():
    value = timeline([edge('cycle1','admission','surgery'), edge('cycle2','surgery','admission'),
                      edge('keep','surgery','discharge')])
    original = copy.deepcopy(value)
    for edges in (value['edges'], list(reversed(value['edges']))):
        result, quarantine = partial_timeline({**value,'edges':edges}, SOURCES, {}, RID)
        assert [e['edge_id'] for e in result['edges']] == ['keep']
        assert {e['candidate']['edge_id'] for e in quarantine} == {'cycle1','cycle2'}
        assert audit_timeline(PatientTimeline.model_validate(result), SOURCES, {})['structural_source_gates_passed']
    assert value == original


def test_timeline_offset_conflict_preserves_disconnected_component():
    value = timeline([edge('r1','admission','surgery',2), edge('r2','surgery','discharge',3),
                      edge('r3','admission','discharge',4), edge('keep','followup1','followup2')])
    value['events'] += [event('followup1'), event('followup2')]
    result, quarantine = partial_timeline(value, SOURCES, {}, RID)
    assert [e['edge_id'] for e in result['edges']] == ['keep']
    assert len(quarantine) == 3
    assert audit_timeline(PatientTimeline.model_validate(result), SOURCES, {})['structural_source_gates_passed']


def test_equality_cycle_keeps_neither_competing_claim():
    value = timeline([edge('r1','admission','surgery'),
                      {**edge('r2','admission','surgery'), 'relation':'same_time'},
                      edge('keep','surgery','discharge')])
    result, quarantine = partial_timeline(value, SOURCES, {}, RID)
    assert [e['edge_id'] for e in result['edges']] == ['keep']
    assert {e['candidate']['edge_id'] for e in quarantine} == {'r1','r2'}
