"""Aggregation may verify itself, but must not race any other campaign writer."""
import pytest

from openpatients2 import dense_benchmark as b, dense_nemotron_super as s, super_packing


@pytest.fixture
def frozen_summary(tmp_path, monkeypatch):
    work = tmp_path / 'campaign'
    manifest = {'nemotron_super': {'scheduler': {'mode': 'packed'}, 'arms': {'beam-text': {}}}}
    b.write(work / 'manifest.json', manifest)
    b.write(work / 'summary/comparison.json', {'complete': True, 'test_evaluated': False})
    b.write(work / 'submissions/submission.json', {'jobs': [
        {'id': 101, 'phase': 'worker'}, {'id': 102, 'phase': 'cleanup'},
        {'id': 103, 'phase': 'aggregate'}, {'id': 104, 'phase': 'aggregate'}]})
    b.write(work / 'summary/integrity.json', {
        'manifest_sha256': b.sha(work / 'manifest.json'),
        'files': {'summary/comparison.json': b.sha(work / 'summary/comparison.json')}})
    calls = []
    monkeypatch.setattr(s, 'check', lambda path: manifest)
    monkeypatch.setattr(b, 'status', lambda path: {'pending': []})
    monkeypatch.setattr(b, 'verify', lambda path: calls.append(('arm-verify', path)))
    monkeypatch.setattr(super_packing, 'collect', lambda path, m: calls.append(('collect', path)))
    monkeypatch.delenv('SLURM_JOB_ID', raising=False)
    return work, calls


@pytest.mark.parametrize('live', [[103], ['103']])
def test_internal_verify_ignores_only_own_registered_aggregate_without_recollecting(frozen_summary, monkeypatch, live):
    work, calls = frozen_summary
    monkeypatch.setenv('SLURM_JOB_ID', '103')
    monkeypatch.setattr(s, 'active_jobs', lambda path: live)
    assert s.verify(work, from_aggregate=True)['verified']
    assert calls == [('arm-verify', work / 'arms/beam-text')]


@pytest.mark.parametrize('live', [[103, 101], [103, 102], [103, 104], [104]])
def test_internal_verify_rejects_other_worker_cleanup_or_aggregate(frozen_summary, monkeypatch, live):
    work, calls = frozen_summary
    monkeypatch.setenv('SLURM_JOB_ID', '103')
    monkeypatch.setattr(s, 'active_jobs', lambda path: live)
    with pytest.raises(ValueError, match='jobs are still active'):
        s.verify(work, from_aggregate=True)
    assert calls == []  # Do not collect or inspect mutable article outputs.


@pytest.mark.parametrize('own,live', [('101', [101]), ('999', [999]), (None, [103])])
def test_internal_verify_requires_own_job_to_be_registered_as_aggregate(frozen_summary, monkeypatch, own, live):
    work, calls = frozen_summary
    if own is not None:
        monkeypatch.setenv('SLURM_JOB_ID', own)
    monkeypatch.setattr(s, 'active_jobs', lambda path: live)
    with pytest.raises(ValueError, match='jobs are still active'):
        s.verify(work, from_aggregate=True)
    assert calls == []


@pytest.mark.parametrize('live', [[101], [102], [103], [104]])
def test_public_verify_rejects_all_active_ids_even_its_own_aggregate(frozen_summary, monkeypatch, live):
    work, calls = frozen_summary
    monkeypatch.setenv('SLURM_JOB_ID', '103')
    monkeypatch.setattr(s, 'active_jobs', lambda path: live)
    with pytest.raises(ValueError, match='jobs are still active'):
        s.verify(work)
    assert calls == []


@pytest.mark.parametrize('internal', [False, True])
def test_completed_verify_collects_only_for_public_entrypoint_and_checks_integrity(frozen_summary, monkeypatch, internal):
    work, calls = frozen_summary
    monkeypatch.setattr(s, 'active_jobs', lambda path: [])
    assert s.verify(work, from_aggregate=internal)['verified']
    assert ('collect', work) in calls if not internal else ('collect', work) not in calls
    # Ignoring the aggregate's own job must never bypass artifact checks.
    b.write(work / 'summary/comparison.json', {'complete': True, 'test_evaluated': True})
    with pytest.raises(ValueError, match='Scored campaign output changed'):
        s.verify(work, from_aggregate=internal)
