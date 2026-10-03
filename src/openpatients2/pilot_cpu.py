"""Bounded CPU pilot preparation: acquire, profile, then prepare selected pixels."""
from __future__ import annotations

import asyncio
import gzip
import json
from pathlib import Path
import shutil
import sqlite3
import time
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from . import acquisition
from .corpus_stats import profile, require_cpu, sha256, tokenizer_snapshot
from .data import read_jsonl, write_json
from .literature_client import utc_now
from .provenance import json_digest


class CPUConfig(BaseModel):
    model_config = ConfigDict(extra='ignore')
    source_mode: Literal['acquisition', 'fixed_fixture'] = 'acquisition'
    source_config: str | None = None
    fixed_source: str | None = None
    fixed_source_sha256: str | None = Field(default=None, pattern=r'^[a-f0-9]{64}$')
    fixed_rosters: str | None = None
    fixed_rosters_sha256: str | None = Field(default=None, pattern=r'^[a-f0-9]{64}$')
    fidelity_reference: str | None = None
    fidelity_reference_sha256: str | None = Field(default=None, pattern=r'^[a-f0-9]{64}$')
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
    prioritize_reference_figures: bool = False

    @model_validator(mode='after')
    def source(self):
        if self.source_mode == 'fixed_fixture':
            if not self.fixed_source or not self.fixed_source_sha256:
                raise ValueError('Fixed fixture needs a path and pinned SHA256')
            if not self.fixed_rosters or not self.fixed_rosters_sha256:
                raise ValueError('Fixed fixture needs pinned hand-reviewed rosters')
        elif not self.source_config:
            raise ValueError('Acquisition needs source_config')
        return self


def _fixture_exports(work, cfg, report):
    from .articles import recheck_license
    source = Path(cfg.fixed_source).resolve()
    if source.is_symlink() or sha256(source) != cfg.fixed_source_sha256:
        raise ValueError('Fixed source fixture hash changed')
    ids = set(); decoded = 0
    with gzip.open(source, 'rb') as handle:
        while line := handle.readline(min(8_000_000, cfg.export_max_bytes-decoded)+1):
            decoded += len(line)
            if len(line) > 8_000_000 or decoded > cfg.export_max_bytes:
                raise ValueError('Fixed source fixture exceeds decoded size cap')
            if not line.strip():
                continue
            article = json.loads(line)
            if article.get('status') != 'eligible' or not recheck_license(article.get('license', {}))['allowed']:
                raise ValueError('Fixed fixture must contain eligible licensed articles')
            ident = article['article_id']
            if ident in ids or len(ids) >= cfg.sample_size:
                raise ValueError('Fixed fixture contains duplicates or exceeds exact sample size')
            ids.add(ident)
    if len(ids) != cfg.sample_size:
        raise ValueError('Fixed fixture does not contain the exact requested sample')
    for filename in ('articles.jsonl.gz','license-review.jsonl.gz'):
        path = work/filename
        if path.exists():
            if report['hashes'].get(filename) != sha256(path):
                raise ValueError('Existing fixture export changed or is untracked')
        elif filename == 'articles.jsonl.gz':
            shutil.copyfile(source, path)
        else:
            with gzip.open(path, 'wb'):
                pass
        report['hashes'][filename] = sha256(path)
        report.setdefault('exports', {})[filename] = {'output':str(path), 'articles':len(ids) if filename.startswith('articles') else 0,
            'stored_bytes':path.stat().st_size, 'uncompressed_bytes':decoded if filename.startswith('articles') else 0}
    return {'source_mode':'fixed_fixture', 'fixture':str(source), 'fixture_sha256':cfg.fixed_source_sha256,
            'candidates':{'eligible':len(ids)}, 'stored_articles':len(ids), 'fetch_queue_complete':True,
            'discovery_complete_for_configured_queries':False, 'cumulative_decoded_network_bytes':0,
            'notice':'Pinned correctness fixture; no PMC rediscovery or article network downloads.'}


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
    for name in ('source_config', 'fixed_source', 'fixed_rosters', 'fidelity_reference', 'tokenizer_metadata', 'chat_template'):
        if settings[name]:
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
        for name in ('source_config', 'fixed_source', 'fixed_rosters', 'fidelity_reference', 'tokenizer_metadata', 'chat_template'):
            if settings[name]:
                pin(settings[name])
        if cfg.source_mode == 'fixed_fixture':
            save('fixture')
            report['source'] = _fixture_exports(work, cfg, report)
            from .patient_context import frozen_rosters
            from .pilot_extract import load_pilot_config, source_gate
            fixed_articles = list(read_jsonl(work/'articles.jsonl.gz'))
            extraction = load_pilot_config(config.get('extraction_config', {}))
            for article in fixed_articles:
                problems = source_gate(article, extraction)
                if problems:
                    raise ValueError('Invalid fixed article: '+article['article_id']+': '+','.join(problems))
            if sha256(settings['fixed_rosters']) != cfg.fixed_rosters_sha256:
                raise ValueError('Fixed hand-reviewed rosters hash changed')
            frozen_rosters(settings['fixed_rosters'], fixed_articles)
            if cfg.fidelity_reference:
                from .corpus_fidelity import validate_reference_sources
                if sha256(settings['fidelity_reference']) != cfg.fidelity_reference_sha256:
                    raise ValueError('Fidelity reference hash changed')
                validate_reference_sources(json.loads(Path(settings['fidelity_reference']).read_text()), fixed_articles)
            report['fixed_source_gates_passed'] = True
            report['acquisition_done'] = True
            save('export')
        else:
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
        if cfg.source_mode == 'fixed_fixture':
            if selected != cfg.sample_size:
                raise ValueError('Correctness profile must retain every fixed article')
            roster_source = Path(settings['fixed_rosters'])
            if sha256(roster_source) != cfg.fixed_rosters_sha256:
                raise ValueError('Fixed hand-reviewed rosters hash changed')
            from .patient_context import frozen_rosters
            frozen_rosters(roster_source, list(read_jsonl(sample)))
            shutil.copyfile(roster_source, work/'profile'/'rosters.json')
            pin(work/'profile'/'rosters.json')
        for name in ('sample.jsonl.gz', 'counts.jsonl.gz', 'profile.json', 'SUMMARY.md'):
            pin(work/'profile'/name)
        for name in report['profile'].get('histograms',{}).get('files',[]):
            pin(work/'profile'/name)
        save('media')
        from .pilot_media import prepare_media
        priorities = None
        if cfg.prioritize_reference_figures:
            if not cfg.fidelity_reference: raise ValueError('Figure priority needs a pinned reference')
            priorities = [{'article_id': row['article_id'], 'figure_id': row['figure_id']}
                          for row in json.loads(Path(cfg.fidelity_reference).read_text()).get('figure_checks', [])]
            priorities = list({(p['article_id'], p['figure_id']): p for p in priorities}.values())
        report['media'] = await prepare_media(sample, work, max_figures=cfg.max_figures,
            max_total_bytes=cfg.pixel_total_bytes, http=http, priority_figures=priorities)
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
