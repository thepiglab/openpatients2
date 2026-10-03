"""Bounded CPU pilot preparation: acquire, profile, then prepare selected pixels."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
import sqlite3
import time

from pydantic import BaseModel, ConfigDict, Field

from . import acquisition
from .corpus_stats import profile, require_cpu, sha256, tokenizer_snapshot
from .data import read_jsonl, write_json
from .literature_client import utc_now
from .provenance import json_digest


class CPUConfig(BaseModel):
    model_config = ConfigDict(extra='ignore')
    source_config: str
    tokenizer_metadata: str
    chat_template: str
    workers: int = Field(default=32, ge=1, le=32)
    sample_size: int = Field(default=48, ge=1, le=128)
    seed: int = 5724
    fetch_batch: int = Field(default=25, ge=1, le=1000)
    max_invocations: int = Field(default=100, ge=1, le=1000)
    export_max_bytes: int = Field(default=1_000_000_000, ge=1, le=150_000_000_000)
    max_figures: int = Field(default=12, ge=1, le=12)
    pixel_total_bytes: int = Field(default=64_000_000, ge=1, le=64_000_000)


def _progress(work):
    """Compare queue cursors and attempts, including count-only search progress."""
    db = sqlite3.connect((Path(work)/'corpus.sqlite').as_uri()+'?mode=ro', uri=True)
    try:
        return json_digest({
            'partitions':list(db.execute('SELECT id,status,count,offset FROM partitions ORDER BY id')),
            'candidates':list(db.execute('SELECT id,state,attempts FROM candidates ORDER BY id'))})
    finally:
        db.close()


def _finished(source, action):
    if action == 'discover':
        return source.get('search_partitions', {}).get('pending', 0) == 0
    return source.get('fetch_queue_complete', False)


async def run_cpu(work, config, *, http=None):
    """Prepare one bounded sample; never submit jobs or perform GPU inference.

    Acquisition checkpoints may resume. Completed CPU outputs and any existing
    profile output are immutable; a profile failure needs a new work directory.
    """
    require_cpu()
    cfg = CPUConfig.model_validate(config)
    settings = cfg.model_dump()
    for name in ('source_config', 'tokenizer_metadata', 'chat_template'):
        settings[name] = str(Path(settings[name]).resolve())
    work = Path(work).resolve()
    summary = work/'cpu.json'
    old = json.loads(summary.read_text()) if summary.exists() else {}
    if old.get('status') in {'ready', 'completed'}:
        raise ValueError('Completed CPU pilot refused; use a new work directory')
    if (work/'profile').exists() or (work/'profile').is_symlink():
        raise ValueError('Existing profile output refused; preserve it and use a new work directory')
    identity = json_digest(settings)
    if old and old.get('config_sha256') != identity:
        raise ValueError('CPU pilot configuration changed; use a new work directory')
    work.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    report = {**old, 'status':'running', 'stage':'initialize', 'started_at':utc_now(),
        'config':settings, 'config_sha256':identity, 'work_dir':str(work),
        'errors':list(old.get('errors', [])), 'acquisition_invocations':list(old.get('acquisition_invocations', [])),
        'hashes':dict(old.get('hashes', {})), 'weights_loaded':False, 'gpu_inference_run':False,
        'completion_scope':'Bounded licensed sample prepared; not exhaustive PMC coverage.'}

    def save(stage=None):
        if stage:
            report['stage'] = stage
        report['elapsed_seconds'] = time.monotonic()-started
        write_json(summary, report)

    def pin(path):
        path = Path(path)
        digest = sha256(path)
        key = str(path.relative_to(work)) if path.is_relative_to(work) else str(path)
        expected = report['hashes'].get(key)
        if expected and expected != digest:
            raise ValueError('CPU checkpoint artifact changed: '+key)
        report['hashes'][key] = digest

    try:
        save()
        for name in ('source_config', 'tokenizer_metadata', 'chat_template'):
            pin(settings[name])
        campaign = work/'acquisition'
        source = acquisition.initialize(campaign, settings['source_config'])
        if not report.get('acquisition_done'):
            # One shared invocation bound covers discovery AND fetch. Requests,
            # bytes, storage and attempt ceilings are separately enforced below.
            remaining = max(0, cfg.max_invocations-len(report['acquisition_invocations']))
            for action in ('discover', 'fetch'):
                save(action)
                reason = 'configured_queries_finished' if action == 'discover' else 'fetch_queue_finished'
                while not _finished(source, action):
                    if not remaining:
                        reason = 'invocation_limit'
                        break
                    before = _progress(campaign)
                    report['acquisition_invocations'].append({'action':action, 'status':'running', 'started_at':utc_now()})
                    save()
                    source = await acquisition.run(campaign, action, fetch_limit=cfg.fetch_batch, http=http)
                    remaining -= 1
                    report['acquisition_invocations'][-1] = {'action':action, **source.get('last_run', {})}
                    report['source'] = source
                    save()
                    if source.get('remaining_network_bytes', 1) <= 0:
                        reason = 'cumulative_network_cap'
                        break
                    if _progress(campaign) == before:
                        reason = 'no_queue_progress'
                        break
                report.setdefault('acquisition_stops', {})[action] = reason
            report['acquisition_done'] = True
        report['source'] = source
        save('export')
        for filename, review in (('articles.jsonl.gz', False), ('license-review.jsonl.gz', True)):
            path = work/filename
            if path.exists():
                if filename not in report['hashes']:
                    raise ValueError('Existing untracked export refused: '+filename)
                pin(path)
            else:
                result = acquisition.export(campaign, path, cfg.export_max_bytes, license_review=review)
                report.setdefault('exports', {})[filename] = result
                pin(path)
            save()
        if not report['exports']['articles.jsonl.gz']['articles']:
            report.update(status='empty', finished_at=utc_now())
            save('finished')
            return report
        save('tokenizer')
        report['tokenizer'] = tokenizer_snapshot(work/'tokenizer', settings['tokenizer_metadata'])
        for name in ('tokenizer.json', 'tokenizer_config.json', 'tokenizer-manifest.json'):
            pin(work/'tokenizer'/name)
        save('profile')
        report['profile'] = profile(work/'articles.jsonl.gz', work/'tokenizer', work/'profile',
            workers=cfg.workers, sample_size=cfg.sample_size, seed=cfg.seed,
            benchmark_workers=tuple(n for n in (1,2,4,8,16,32) if n<=cfg.workers),
            template=settings['chat_template'], metadata_path=settings['tokenizer_metadata'])
        sample = work/'profile'/'sample.jsonl.gz'
        # A profiler receipt alone cannot establish a nonempty eligible sample.
        selected = 0
        for article in read_jsonl(sample):
            selected += 1
            if article.get('status') != 'eligible':
                raise ValueError('CPU profile did not produce an eligible sample')
            if selected > cfg.sample_size:
                raise ValueError('CPU profile exceeded the requested sample size')
        if not selected:
            raise ValueError('CPU profile did not produce a nonempty eligible sample')
        for name in ('sample.jsonl.gz', 'counts.jsonl.gz', 'profile.json', 'SUMMARY.md'):
            pin(work/'profile'/name)
        for name in report['profile'].get('histograms',{}).get('files',[]):
            pin(work/'profile'/name)
        save('media')
        from .pilot_media import prepare_media
        report['media'] = await prepare_media(sample, work, max_figures=cfg.max_figures,
            max_total_bytes=cfg.pixel_total_bytes, http=http)
        if not report['media'].get('complete'):
            raise ValueError('Selected media preparation did not complete')
        pin(work/'vision-assets'/'manifest.json')
        for row in report['media'].get('figures', []):
            if row.get('status') == 'ready':
                pin(work/row['file'])
        report.update(status='ready', sample_size=selected, finished_at=utc_now(),
            artifacts={'articles':str(work/'articles.jsonl.gz'), 'license_review':str(work/'license-review.jsonl.gz'),
                       'sample':str(sample), 'profile':str(work/'profile'/'profile.json'),
                       'media_manifest':str(work/'vision-assets'/'manifest.json'), 'tokenizer':str(work/'tokenizer')})
        save('finished')
        return report
    except BaseException as exc:
        report['status'] = 'interrupted' if isinstance(exc, (KeyboardInterrupt, asyncio.CancelledError)) else 'failed'
        report['errors'].append({'stage':report['stage'], 'type':type(exc).__name__, 'error':str(exc)[:2000], 'at':utc_now()})
        report['finished_at'] = utc_now()
        save()
        raise
