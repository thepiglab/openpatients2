"""Delete only owned pilot source files after the benchmark report is written."""
from __future__ import annotations

from contextlib import ExitStack
import fcntl
import json
from pathlib import Path
import re
import shutil
import stat

from .data import write_json
from .literature_client import utc_now
from .provenance import json_digest


SCOPE = 'corpus-pilot-sources/1'
TARGETS = ('acquisition', 'articles.jsonl.gz', 'license-review.jsonl.gz',
           'profile/sample.jsonl.gz', 'tokenizer')
DIRECTORIES = {'acquisition', 'tokenizer'}


def _safe_path(work, relative):
    path = work/relative
    cursor = path
    while cursor != work:
        if cursor.is_symlink():
            raise ValueError('Source cleanup refuses symlink: '+str(cursor))
        cursor = cursor.parent
    if not path.resolve().is_relative_to(work):
        raise ValueError('Source cleanup path escaped campaign')
    return path


def _read(work, relative):
    path = _safe_path(work, relative)
    if not path.is_file():
        raise ValueError('Required source cleanup proof is missing: '+relative)
    result = json.loads(path.read_text())
    if not isinstance(result, dict):
        raise ValueError('Invalid source cleanup proof: '+relative)
    return result


def _inventory(path):
    """Preflight all descendants without following links, before any deletion."""
    metadata = path.lstat()
    if stat.S_ISLNK(metadata.st_mode):
        raise ValueError('Source cleanup refuses a nested symlink: '+str(path))
    if stat.S_ISREG(metadata.st_mode):
        if metadata.st_nlink != 1:
            raise ValueError('Source cleanup refuses a hard-linked source: '+str(path))
        return metadata.st_size
    if not stat.S_ISDIR(metadata.st_mode):
        raise ValueError('Source cleanup refuses a special file: '+str(path))
    return sum(_inventory(child) for child in path.iterdir())


def cleanup_sources(campaign):
    """Verify ownership and locks, then delete an exact source-only allowlist.

    Runtime/package changes do not prevent authorized cleanup. Ownership/config
    identity, report and model-cleanup proofs are still mandatory on every call.
    """
    work = Path(campaign['work'])
    if not work.is_absolute() or work.is_symlink() or not work.is_dir() or work != work.resolve():
        raise ValueError('Source cleanup needs the actual absolute campaign directory')
    digest = campaign.get('config_sha256')
    if not isinstance(digest, str) or not re.fullmatch(r'[a-f0-9]{64}', digest) or digest != json_digest(campaign['config']):
        raise ValueError('Invalid source cleanup campaign configuration digest')
    expected = {'work':str(work), 'config_sha256':digest, 'scope':SCOPE}
    if _read(work, 'source-owner.json') != expected:
        raise ValueError('Source ownership marker does not match this campaign')
    _read(work, 'summary.json')
    if _read(work, 'gpu.json').get('status') not in {'completed', 'partial', 'failed'}:
        raise ValueError('Benchmark attempt has not ended; source cleanup is deferred')
    receipt_path = _safe_path(work, 'source-cleanup.json')
    prior = _read(work, 'source-cleanup.json') if receipt_path.exists() else None
    if prior and prior.get('owner') != expected:
        raise ValueError('Previous source cleanup receipt belongs to another campaign')
    engine = _safe_path(work, 'engine')
    if engine.exists() and not engine.is_dir():
        raise ValueError('Invalid engine directory')
    if engine.exists():
        _safe_path(work, 'engine/.model.lock')
    if _safe_path(work, 'engine/campaign.json').exists():
        engine_campaign = _read(work, 'engine/campaign.json')
        if (engine_campaign.get('work') != str(engine) or
                engine_campaign.get('config_sha256') != json_digest(engine_campaign.get('config'))):
            raise ValueError('Invalid owned engine campaign')
        if _read(work, 'engine/results/glimmer-fp8/cleanup.json').get('status') != 'deleted':
            raise ValueError('Model cleanup must finish before source cleanup')
    targets = list(TARGETS); directories = set(DIRECTORIES)
    holdout = campaign['config'].get('holdout_source_config') and (work/'holdout').exists()
    if holdout:
        proof = {'work':str(work/'holdout'),'config_sha256':digest,'scope':'corpus-pilot-holdout/1'}
        if _read(work,'holdout/source-owner.json') != proof:
            raise ValueError('Holdout source ownership marker does not match this campaign')
        targets += ['holdout/'+name for name in TARGETS]
        directories |= {'holdout/'+name for name in DIRECTORIES}
    paths = [(relative, _safe_path(work, relative)) for relative in targets]
    # Validate every target and descendant first: no partial delete on a bad link.
    inventory = {}
    for relative, path in paths:
        if not path.exists():
            continue
        if path.is_dir() != (relative in directories):
            raise ValueError('Source cleanup target has unexpected type: '+relative)
        inventory[relative] = _inventory(path)
    with ExitStack() as stack:
        if engine.exists():
            from .hipergator import model_lock
            try:
                stack.enter_context(model_lock(engine))
            except BlockingIOError:
                raise ValueError('Active GPU/model operation prevents source cleanup') from None
        for acquisition in [work/'acquisition', *([work/'holdout/acquisition'] if holdout else [])]:
            if acquisition.is_dir():
                owner = stack.enter_context((acquisition/'owner.lock').open('a'))
                try:
                    fcntl.flock(owner, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    raise ValueError('Active acquisition prevents source cleanup') from None
        # Recheck ownership/report while holding both relevant writer locks.
        if _read(work, 'source-owner.json') != expected:
            raise ValueError('Source ownership changed before cleanup')
        if holdout and _read(work,'holdout/source-owner.json') != proof:
            raise ValueError('Holdout ownership changed before cleanup')
        _read(work, 'summary.json')
        if _read(work, 'gpu.json').get('status') not in {'completed', 'partial', 'failed'}:
            raise ValueError('Benchmark attempt has not ended; source cleanup is deferred')
        if (engine/'campaign.json').exists():
            if _read(work, 'engine/results/glimmer-fp8/cleanup.json').get('status') != 'deleted':
                raise ValueError('Model cleanup must finish before source cleanup')
        # Recompute the entire deletion inventory under writer locks, before
        # deleting the first target, to avoid a partial cleanup on a changed tree.
        inventory = {relative:_inventory(path) for relative,path in paths if path.exists()}
        deleted = []
        for relative, path in paths:
            if relative not in inventory:
                continue
            _safe_path(work, relative)
            _inventory(path)
            if relative in directories:
                shutil.rmtree(path)
            else:
                path.unlink()
            deleted.append(relative)
        if prior and prior.get('status') == 'deleted' and not deleted:
            return prior
        result = {'status':'deleted', 'owner':expected, 'finished_at':utc_now(),
            'deleted_paths':deleted, 'bytes_freed':sum(inventory.values()),
            'prior_bytes_freed':prior.get('bytes_freed', 0) if prior else 0,
            'preserved':'cpu.json, pilot.json, extraction/results/logs, scalar counts and histograms, '
                        'profile reports, and vision-assets (bounded review pixels) remain. '
                        'Only acquisition XML/database, article exports, selected source sample and tokenizer files were deleted.'}
        write_json(receipt_path, result)
        return result
