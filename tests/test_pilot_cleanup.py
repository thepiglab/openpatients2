import fcntl
import json
from pathlib import Path

import pytest

from openpatients2.data import write_json
from openpatients2.pilot_cleanup import SCOPE, TARGETS, cleanup_sources
from openpatients2.provenance import json_digest


def campaign(tmp_path, *, engine=True):
    work = (tmp_path/'pilot').resolve(); work.mkdir()
    cfg = {'sample_size':48, 'source_config':'bounded.yaml'}
    value = {'work':str(work), 'config':cfg, 'config_sha256':json_digest(cfg),
             'runtime':{'changed.py':'oldhash'}}
    write_json(work/'pilot.json',value)
    write_json(work/'source-owner.json',{'work':str(work),'config_sha256':value['config_sha256'],'scope':SCOPE})
    write_json(work/'summary.json',{'gpu':'completed','cleanup':'deleted'})
    write_json(work/'gpu.json',{'status':'completed'})
    (work/'acquisition').mkdir(); (work/'acquisition'/'owner.lock').touch()
    (work/'acquisition'/'corpus.sqlite').write_bytes(b'owned source XML in SQLite')
    (work/'tokenizer'/'cache').mkdir(parents=True)
    (work/'tokenizer'/'tokenizer.json').write_text('tokenizer')
    (work/'tokenizer'/'cache'/'body').write_text('cache')
    (work/'profile').mkdir()
    for name in ('articles.jsonl.gz','license-review.jsonl.gz','profile/sample.jsonl.gz'):
        (work/name).write_text('article body')
    keep = ('cpu.json','extraction/result.json','logs/run.log','results/report.json',
            'profile/counts.jsonl.gz','profile/profile.json','profile/SUMMARY.md',
            'profile/histograms/distribution.svg','vision-assets/manifest.json','vision-assets/review.image')
    for name in keep:
        (work/name).parent.mkdir(parents=True,exist_ok=True)
        (work/name).write_text('preserved '+name)
    if engine:
        cfg = {'models':[{'name':'glimmer-fp8'}]}
        write_json(work/'engine'/'campaign.json',{'work':str(work/'engine'),'config':cfg,'config_sha256':json_digest(cfg)})
        write_json(work/'engine'/'results'/'glimmer-fp8'/'cleanup.json',{'status':'deleted'})
    return value, work, keep


def test_cleanup_owned_sources_preserves_results_pixels_and_is_idempotent(tmp_path):
    value, work, keep = campaign(tmp_path)
    originals = {name:(work/name).read_bytes() for name in keep}
    expected_bytes = sum(p.stat().st_size for name in TARGETS for p in
        ([work/name] if (work/name).is_file() else (work/name).rglob('*')) if p.is_file())
    result = cleanup_sources(value)
    assert result['status'] == 'deleted' and set(result['deleted_paths']) == set(TARGETS)
    assert result['bytes_freed'] == expected_bytes > 0
    assert all(not (work/name).exists() for name in TARGETS)
    assert all((work/name).read_bytes() == content for name,content in originals.items())
    assert json.loads((work/'source-cleanup.json').read_text()) == result
    prior = (work/'source-cleanup.json').read_bytes()
    assert cleanup_sources(value) == result
    assert (work/'source-cleanup.json').read_bytes() == prior
    assert (work/'source-owner.json').exists() and (work/'pilot.json').exists()


def test_cleanup_without_engine_keeps_source_ownership_proof(tmp_path):
    value, work, keep = campaign(tmp_path,engine=False)
    assert cleanup_sources(value)['status'] == 'deleted'
    assert not (work/'engine').exists()


@pytest.mark.parametrize('bad', ['owner','digest','scope'])
def test_forged_identity_is_refused_before_any_deletion(tmp_path,bad):
    value, work, keep = campaign(tmp_path)
    if bad == 'digest':
        value['config_sha256'] = '0'*64
    else:
        marker = json.loads((work/'source-owner.json').read_text())
        marker['work' if bad == 'owner' else 'scope'] = '/different' if bad == 'owner' else 'unowned'
        write_json(work/'source-owner.json',marker)
    with pytest.raises(ValueError): cleanup_sources(value)
    assert all((work/name).exists() for name in TARGETS)
    assert not (work/'source-cleanup.json').exists()


@pytest.mark.parametrize('name',['acquisition','tokenizer','articles.jsonl.gz','source-owner.json'])
def test_top_target_or_marker_symlink_is_refused(tmp_path,name):
    import shutil
    value, work, keep = campaign(tmp_path)
    external = tmp_path/'outside'; external.mkdir(); (external/'keep').write_text('keep')
    target = work/name
    if target.is_dir(): shutil.rmtree(target)
    else: target.unlink()
    target.symlink_to(external, target_is_directory=True)
    with pytest.raises(ValueError,match='symlink'): cleanup_sources(value)
    assert (external/'keep').read_text() == 'keep'
    assert (work/'profile'/'sample.jsonl.gz').exists()


def test_nested_symlink_preflight_does_not_partially_delete_sources(tmp_path):
    value, work, keep = campaign(tmp_path)
    outside = tmp_path/'outside'; outside.write_text('external')
    (work/'tokenizer'/'cache'/'bad').symlink_to(outside)
    with pytest.raises(ValueError,match='nested symlink'): cleanup_sources(value)
    assert all((work/name).exists() for name in TARGETS)
    assert outside.read_text() == 'external'


def test_symlinked_work_directory_is_refused(tmp_path):
    value, work, keep = campaign(tmp_path)
    alias = tmp_path/'alias'; alias.symlink_to(work,target_is_directory=True)
    value['work'] = str(alias)
    with pytest.raises(ValueError,match='actual absolute'): cleanup_sources(value)
    assert (work/'acquisition').exists()


@pytest.mark.parametrize('proof',['summary.json','gpu.json','engine/results/glimmer-fp8/cleanup.json'])
def test_missing_completion_proof_blocks_cleanup(tmp_path,proof):
    value, work, keep = campaign(tmp_path); (work/proof).unlink()
    with pytest.raises(ValueError,match='missing'): cleanup_sources(value)
    assert (work/'acquisition').exists() and (work/'articles.jsonl.gz').exists()


def test_undeleted_checkpoint_blocks_source_cleanup(tmp_path):
    value, work, keep = campaign(tmp_path)
    write_json(work/'engine/results/glimmer-fp8/cleanup.json',{'status':'failed'})
    with pytest.raises(ValueError,match='Model cleanup must finish'): cleanup_sources(value)
    assert (work/'articles.jsonl.gz').exists()


@pytest.mark.parametrize('state',['running','missing','ready'])
def test_status_report_alone_cannot_authorize_cleanup_before_gpu_ends(tmp_path,state):
    value, work, keep = campaign(tmp_path)
    write_json(work/'gpu.json',{'status':state})
    with pytest.raises(ValueError,match='Benchmark attempt has not ended'): cleanup_sources(value)
    assert all((work/name).exists() for name in TARGETS)


@pytest.mark.parametrize('lock,error',[('engine/.model.lock','Active GPU'),('acquisition/owner.lock','Active acquisition')])
def test_active_writer_locks_block_all_deletions(tmp_path,lock,error):
    value, work, keep = campaign(tmp_path)
    with (work/lock).open('a') as handle:
        fcntl.flock(handle,fcntl.LOCK_EX|fcntl.LOCK_NB)
        with pytest.raises(ValueError,match=error): cleanup_sources(value)
    assert all((work/name).exists() for name in TARGETS)
    assert not (work/'source-cleanup.json').exists()


def test_repeated_cleanup_still_requires_matching_ownership(tmp_path):
    value, work, keep = campaign(tmp_path)
    cleanup_sources(value)
    write_json(work/'source-owner.json',{'scope':'forged'})
    with pytest.raises(ValueError,match='ownership marker'): cleanup_sources(value)
