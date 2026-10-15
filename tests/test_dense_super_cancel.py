import json
import os
from pathlib import Path
import subprocess
from types import SimpleNamespace

import pytest

from openpatients2 import dense_benchmark as b, dense_nemotron_super as s


def test_cancel_stops_bootstrap_before_newly_recorded_jobs_and_preserves_other_runs(tmp_path, monkeypatch):
    work = tmp_path / 'campaign'
    bootstrap = Path(str(work) + '.bootstrap/job-id.txt')
    bootstrap.parent.mkdir()
    bootstrap.write_text('100;cluster\n')
    b.write(work / 'submissions/first.json', {'jobs': [{'id': '200'}, {'id': '201'}]})
    active = {'100', '200', '201', '999'}
    cancelled = []
    def run(argv, **kwargs):
        if argv[0] == 'squeue':
            assert argv == ['squeue', '-h', '-u', 'fixture-user', '-o', '%i']
            return SimpleNamespace(stdout='\n'.join(active))
        assert argv[0] == 'scancel'
        cancelled.append(argv[1:])
        active.difference_update(argv[1:])
        if argv[1:] == ['100']:
            # The bootstrap has published another job before it finishes.
            b.write(work / 'submissions/last.json', {'jobs': [{'id': '202'}]})
            active.add('202')
        return SimpleNamespace(stdout='')
    monkeypatch.setenv('USER', 'fixture-user')
    monkeypatch.setattr(s.subprocess, 'run', run)
    monkeypatch.setattr(s.time, 'sleep', lambda _: None)
    result = s.cancel(work)
    assert cancelled == [['100'], ['200', '201', '202']]
    assert active == {'999'} and result['stopped']
    assert result['cancellation_requested'] == ['100', '200', '201', '202']
    assert b.read(work / 'submissions/first.json')['jobs'] == [{'id': '200'}, {'id': '201'}]


def test_cancel_reads_final_ledger_after_bootstrap_leaves_queue(tmp_path, monkeypatch):
    work = tmp_path / 'campaign'
    bootstrap = Path(str(work) + '.bootstrap/job-id.txt')
    bootstrap.parent.mkdir()
    bootstrap.write_text('100')
    active = {'201', '999'}
    cancelled = []
    def run(argv, **kwargs):
        if argv[0] == 'squeue':
            # Final ledger appears while the queue snapshot is being fetched.
            b.write(work / 'submissions/final.json', {'jobs': [{'id': '201'}]})
            return SimpleNamespace(stdout='\n'.join(active))
        cancelled.extend(argv[1:]); active.difference_update(argv[1:])
        return SimpleNamespace(stdout='')
    monkeypatch.setattr(s.subprocess, 'run', run)
    monkeypatch.setattr(s.time, 'sleep', lambda _: None)
    assert s.cancel(work)['stopped']
    assert cancelled == ['201'] and active == {'999'}


def test_cancel_finished_campaign_does_not_issue_scancel(tmp_path, monkeypatch):
    b.write(tmp_path / 'submissions/old.json', {'jobs': [{'id': '100'}]})
    calls = []
    def run(argv, **kwargs):
        calls.append(argv)
        return SimpleNamespace(stdout='999\n')
    monkeypatch.setattr(s.subprocess, 'run', run)
    assert s.cancel(tmp_path) == {'stopped': True, 'work': str(tmp_path.resolve()),
                                 'cancellation_requested': [], 'remaining_active': []}
    assert all(c[0] == 'squeue' for c in calls)


def test_cancel_timeout_prevents_reporting_success(tmp_path, monkeypatch):
    b.write(tmp_path / 'submissions/run.json', {'jobs': [{'id': '100'}]})
    calls = []
    def run(argv, **kwargs):
        calls.append(argv)
        return SimpleNamespace(stdout='100\n999\n')
    monkeypatch.setattr(s.subprocess, 'run', run)
    with pytest.raises(TimeoutError, match='still stopping'):
        s.cancel(tmp_path, timeout_seconds=0)
    assert [c for c in calls if c[0] == 'scancel'] == [['scancel', '100']]


def test_cancel_rejects_invalid_records_before_cancellation(tmp_path, monkeypatch):
    b.write(tmp_path / 'submissions/invalid.json', {'jobs': [{'id': '--user=somebody'}]})
    monkeypatch.setattr(s.subprocess, 'run', lambda *a, **kw: pytest.fail('Invalid job ID reached Slurm'))
    with pytest.raises(ValueError, match='Invalid campaign'):
        s.cancel(tmp_path)


def test_cancel_launcher_works_before_preparation_and_uses_current_code(tmp_path):
    root = Path(__file__).resolve().parents[1]
    scripts = tmp_path / 'scripts'; scripts.mkdir()
    launcher = scripts / 'run_dense_nemotron_super.sh'
    launcher.write_text((root / 'scripts' / launcher.name).read_text())
    cli = scripts / 'benchmark_dense_nemotron_super.py'
    cli.write_text('import json,sys; print(json.dumps(sys.argv[1:]))\n')
    work = tmp_path / 'not-yet-prepared'
    (tmp_path / 'runs').mkdir()
    (tmp_path / 'runs/dense-nemotron-super-last-work.txt').write_text(str(work))
    result = subprocess.run(['bash', str(launcher), 'cancel'], cwd=tmp_path,
                            text=True, capture_output=True, env=os.environ.copy())
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == ['cancel', '--work', str(work)]
