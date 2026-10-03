"""Resumable, bounded CPU-only PMC acquisition via ESearch and official Cloud files.

No publisher scraping, GPU work, model calls, media or supplementary payloads.
One owner writes a campaign SQLite database; outputs are compressed and exportable.
"""
from __future__ import annotations

import argparse
import asyncio
import csv
from contextvars import ContextVar
import datetime as dt
import fcntl
import gzip
import hashlib
import io
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import time
from typing import Literal
from urllib.parse import parse_qs, unquote, urlparse

import httpx
import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

from .articles import license_decision, parse_article
from .corpus import ESEARCH, LICENSE_QUERY
from .corpus_policy import prioritize_article
from .literature_client import LiteratureClient, LiteratureConfig, LiteratureError, RateLimiter, retry_delay, utc_now
from .pmc_media import CLOUD, cloud_url, metadata_identifiers, metadata_flag
from .provenance import json_digest

VERSION = 'pmc-acquisition/2'
MAX_NETWORK_BYTES_TOTAL = 150_000_000_000


class Lane(BaseModel):
    model_config = ConfigDict(extra='forbid')
    name: str = Field(pattern=r'^[a-z][a-z0-9_-]*$')
    query: str = Field(min_length=1)
    start: dt.date
    end: dt.date
    max_candidates: int | None = Field(default=None, ge=1)
    stratum: str | None = None
    focus: Literal['case_focused', 'broader'] | None = None

    @model_validator(mode='after')
    def dates(self):
        if self.start > self.end:
            raise ValueError('Start date must precede end date')
        return self


class AcquisitionConfig(BaseModel):
    model_config = ConfigDict(extra='forbid')
    lanes: list[Lane] = Field(default_factory=list)
    max_candidates: int = Field(default=1000, ge=1)
    page_size: int = Field(default=100, ge=1, le=1000)
    search_partition_limit: int = Field(default=9000, ge=1, le=10000)
    max_storage_bytes: int = Field(default=256_000_000, ge=8_000_000)
    min_free_bytes: int = Field(default=1_000_000_000, ge=0)
    max_network_bytes_per_invocation: int = Field(default=32_000_000, ge=1024)
    max_network_bytes_total: int = Field(default=MAX_NETWORK_BYTES_TOTAL, ge=1024, le=MAX_NETWORK_BYTES_TOTAL)
    max_requests_per_invocation: int = Field(default=1000, ge=1)
    max_metadata_bytes: int = Field(default=2_000_000, ge=1024)
    max_xml_bytes: int = Field(default=5_000_000, ge=1024)
    max_article_json_bytes: int = Field(default=20_000_000, ge=1024)
    max_versions: int = Field(default=32, ge=1, le=256)
    search_requests_per_second: float = Field(default=2, gt=0, le=3)
    cloud_requests_per_second: float = Field(default=4, gt=0, le=20)
    timeout_seconds: float = Field(default=45, gt=0, le=180)
    retries: int = Field(default=2, ge=0, le=5)
    max_fetch_attempts: int = Field(default=3, ge=1, le=10)
    fetch_concurrency: int = Field(default=4, ge=1, le=8)

    @model_validator(mode='after')
    def unique_lanes(self):
        if len({x.name for x in self.lanes}) != len(self.lanes):
            raise ValueError('Discovery lane names must be unique')
        if self.page_size > self.search_partition_limit:
            raise ValueError('Page size cannot exceed partition limit')
        quotas = [x.max_candidates for x in self.lanes]
        if any(x is not None for x in quotas):
            if any(x is None for x in quotas) or sum(quotas) > self.max_candidates:
                raise ValueError('Give every lane a quota, with their sum no greater than max_candidates')
        return self


class BudgetStop(RuntimeError):
    pass


class Store:
    def __init__(self, work: Path, config: AcquisitionConfig | None = None):
        self.work = Path(work).resolve()
        if config is not None:
            self.work.mkdir(parents=True, exist_ok=True)
        if not self.work.is_dir():
            raise ValueError('Initialize this work directory first')
        self.lock = (self.work / 'owner.lock').open('a')
        try:
            fcntl.flock(self.lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            self.lock.close()
            raise ValueError('Another acquisition process owns this work directory') from None
        self.db = sqlite3.connect(self.work / 'corpus.sqlite', timeout=30)
        self.db.row_factory = sqlite3.Row
        try:
            self.db.executescript('''
                PRAGMA journal_mode=DELETE;
                PRAGMA synchronous=FULL;
                PRAGMA temp_store=MEMORY;
                CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS partitions(
                    id TEXT PRIMARY KEY, lane TEXT NOT NULL, query TEXT NOT NULL,
                    start TEXT NOT NULL, end TEXT NOT NULL, status TEXT NOT NULL,
                    count INTEGER, offset INTEGER NOT NULL DEFAULT 0, translation TEXT, error TEXT);
                CREATE TABLE IF NOT EXISTS candidates(
                    id TEXT PRIMARY KEY, pmcid TEXT NOT NULL, version INTEGER,
                    state TEXT NOT NULL DEFAULT 'pending', attempts INTEGER NOT NULL DEFAULT 0,
                    result TEXT, article_id TEXT, error TEXT, updated TEXT);
                CREATE TABLE IF NOT EXISTS origins(candidate TEXT, origin TEXT, PRIMARY KEY(candidate,origin));
                CREATE TABLE IF NOT EXISTS articles(
                    article_id TEXT PRIMARY KEY, json_gz BLOB NOT NULL, xml_gz BLOB NOT NULL,
                    json_sha256 TEXT NOT NULL, xml_sha256 TEXT NOT NULL, route TEXT NOT NULL, stored_at TEXT NOT NULL);
            ''')
            saved = self.get('config')
            if saved is None:
                if config is None:
                    raise ValueError('No acquisition config in work directory')
                self.put('config', config.model_dump(mode='json'))
                self.put('version', VERSION)
                self.put('network_bytes', 0); self.put('requests', 0)
                self.put('network_reserved_bytes', 0)
            elif config and saved != config.model_dump(mode='json'):
                raise ValueError('Campaign configuration changed; use the saved config or a new directory')
            if self.get('version') != VERSION:
                raise ValueError('Campaign format mismatch')
            implementation = {name:hashlib.sha256(Path(__file__).with_name(name).read_bytes()).hexdigest()
                              for name in ('acquisition.py','articles.py','license_policy.py','corpus.py',
                                           'pmc_media.py','jats_links.py','corpus_policy.py')}
            if self.get('implementation') is None:
                self.put('implementation', implementation)
            elif self.get('implementation') != implementation:
                raise ValueError('Acquisition code changed; export existing data and start a new campaign')
            self.config = AcquisitionConfig.model_validate(self.get('config'))
            self.db.executescript('''CREATE TABLE IF NOT EXISTS discovery_responses(
                sequence INTEGER PRIMARY KEY, partition_id TEXT NOT NULL,
                params TEXT NOT NULL, retrieval TEXT NOT NULL, count INTEGER NOT NULL, ids TEXT NOT NULL);
                CREATE INDEX IF NOT EXISTS discovery_responses_partition ON discovery_responses(partition_id);
                CREATE INDEX IF NOT EXISTS origins_origin ON origins(origin);
                CREATE INDEX IF NOT EXISTS partitions_lane ON partitions(lane);
            ''')
            self.db.execute("INSERT OR IGNORE INTO settings SELECT 'candidate_count',CAST(count(*) AS TEXT) FROM candidates")
            self.db.executescript('''CREATE TRIGGER IF NOT EXISTS count_candidate_insert AFTER INSERT ON candidates
                BEGIN UPDATE settings SET value=CAST(CAST(value AS INTEGER)+1 AS TEXT) WHERE key='candidate_count'; END;''')
            # Reserve room for SQLite rollback journals plus config/reports/cache.
            pages = (self.config.max_storage_bytes - 1_000_000) // 2 // 4096
            self.db.execute(f'PRAGMA max_page_count={pages}')
            self.db.commit()
        except BaseException:
            self.close()
            raise

    def close(self):
        self.db.close(); self.lock.close()

    def get(self, key, default=None):
        row = self.db.execute('SELECT value FROM settings WHERE key=?', (key,)).fetchone()
        return json.loads(row[0]) if row else default

    def put(self, key, value):
        self.db.execute('INSERT OR REPLACE INTO settings VALUES(?,?)', (key, json.dumps(value, ensure_ascii=False)))
        self.db.commit()

    def check_space(self):
        size = sum(p.stat().st_size for p in self.work.rglob('*') if p.is_file())
        if size >= self.config.max_storage_bytes or shutil.disk_usage(self.work).free < self.config.min_free_bytes:
            raise BudgetStop('Storage or free-space limit reached')

    def network_remaining(self):
        return self.config.max_network_bytes_total - self.get('network_bytes', 0) - self.get('network_reserved_bytes', 0)

    def reserve_network(self, size):
        if size > self.network_remaining():
            raise BudgetStop('Cumulative network budget exhausted; use existing stored data')
        self.put('network_reserved_bytes', self.get('network_reserved_bytes', 0) + size)

    def settle_network(self, reserved, received):
        # One committed transaction: a crash leaves the reservation charged.
        with self.db:
            self.db.execute('UPDATE settings SET value=? WHERE key=?',
                (json.dumps(self.get('network_bytes', 0) + received), 'network_bytes'))
            self.db.execute('UPDATE settings SET value=? WHERE key=?',
                (json.dumps(self.get('network_reserved_bytes', 0) - reserved), 'network_reserved_bytes'))

    def lane_count(self, lane):
        return self.db.execute('''SELECT count(DISTINCT o.candidate) FROM origins o
            JOIN partitions p ON o.origin='esearch:'||p.id WHERE p.lane=?''', (lane,)).fetchone()[0]

    def candidate_provenance(self, key):
        lanes = {x.name:x.model_dump(mode='json') for x in self.config.lanes}
        origins = [row[0] for row in self.db.execute('SELECT origin FROM origins WHERE candidate=? ORDER BY origin', (key,))]
        partitions = [dict(row) for row in self.db.execute('''SELECT p.* FROM partitions p JOIN origins o
            ON o.origin='esearch:'||p.id WHERE o.candidate=? ORDER BY p.lane,p.start,p.id''', (key,))]
        receipts = []
        for partition in partitions:
            for row in self.db.execute('SELECT sequence,params,retrieval,count,ids FROM discovery_responses WHERE partition_id=? ORDER BY sequence', (partition['id'],)):
                if key.removeprefix('PMC').split('.')[0] in json.loads(row['ids']):
                    receipts.append({'sequence':row['sequence'], 'partition_id':partition['id'],
                        'params':json.loads(row['params']), 'retrieval':json.loads(row['retrieval']),
                        'result_count':row['count']})
        return {'candidate_id':key, 'origins':origins, 'search_partitions':partitions,
                'discovery_receipts':receipts,
                'lanes':[lanes[name] for name in sorted({p['lane'] for p in partitions})],
                'selection_method':'bounded publication-date ordered query prefixes; purposive, not random',
                'config_sha256':json_digest(self.config.model_dump(mode='json'))}

    def add(self, pmcid, version, origin):
        if not re.fullmatch(r'PMC[1-9][0-9]*', pmcid) or (version is not None and version < 1):
            raise ValueError('Invalid canonical PMCID/version')
        key = pmcid + ('.'+str(version) if version is not None else '')
        exists = self.db.execute('SELECT 1 FROM candidates WHERE id=?', (key,)).fetchone()
        if not exists and self.get('candidate_count', 0) >= self.config.max_candidates:
            raise BudgetStop('Candidate cap reached; discovery remains incomplete')
        self.db.execute('INSERT OR IGNORE INTO candidates(id,pmcid,version) VALUES(?,?,?)', (key, pmcid, version))
        self.db.execute('INSERT OR IGNORE INTO origins VALUES(?,?)', (key, origin))
        return key


def initialize(work, config_path):
    config = AcquisitionConfig.model_validate(yaml.safe_load(Path(config_path).read_text()))
    store = Store(Path(work), config)
    try:
        for lane in config.lanes:
            add_partition(store, lane.name, lane.query, lane.start, lane.end)
        store.db.commit()
    finally:
        store.close()
    return status(work)


def add_partition(store, lane, query, start, end):
    values = [lane, query, str(start), str(end)]
    key = json_digest(values)
    store.db.execute('INSERT OR IGNORE INTO partitions(id,lane,query,start,end,status) VALUES(?,?,?,?,?,?)',
                     (key, *values, 'pending'))


class AcquisitionClient(LiteratureClient):
    """Reuse version listing; implement bounded requests without a growing body cache."""
    def __init__(self, store, http=None):
        cfg = store.config
        super().__init__(LiteratureConfig(cache=str(store.work / 'lookup-cache.sqlite'),
            max_versions=cfg.max_versions, max_listing_pages=5, retries=cfg.retries), http)
        self.store = store
        self.cfg = cfg
        self.search_rate = RateLimiter(cfg.search_requests_per_second)
        self.cloud_rate = RateLimiter(cfg.cloud_requests_per_second)
        self.used_bytes = self.used_requests = 0
        self.reserved_bytes = 0
        self._last_xml = ContextVar('acquisition_raw_xml', default=None)
        self.last_xml = None

    @property
    def last_xml(self):
        return self._last_xml.get()

    @last_xml.setter
    def last_xml(self, value):
        self._last_xml.set(value)

    def invocation_network_remaining(self):
        return self.cfg.max_network_bytes_per_invocation-self.used_bytes-self.reserved_bytes

    def check_network(self):
        if self.invocation_network_remaining() <= 0:
            raise BudgetStop('Per-invocation network budget exhausted; resume with the same command')
        if self.store.network_remaining() <= 0:
            raise BudgetStop('Cumulative network budget exhausted; use existing stored data')

    async def _text(self, url, kind, params=None):
        if kind == 'search':
            if url != ESEARCH:
                raise ValueError('Only official ESearch is allowed')
            limiter = self.search_rate
        else:
            parsed = urlparse(cloud_url(url))
            if kind == 'listing':
                if parsed.path not in {'', '/'} or not params or params.get('list-type') != '2':
                    raise ValueError('Invalid Cloud listing request')
            elif kind not in {'metadata', 'xml'} or not parsed.path.endswith('.json' if kind == 'metadata' else '.xml'):
                raise ValueError('Only metadata and article XML may be downloaded')
            limiter = self.cloud_rate
        cap = self.cfg.max_xml_bytes if kind == 'xml' else self.cfg.max_metadata_bytes
        expected_md5 = parse_qs(urlparse(url).query).get('md5', [])
        if expected_md5 and (len(expected_md5) != 1 or not re.fullmatch('[0-9a-fA-F]{32}', expected_md5[0])):
            raise ValueError('Invalid manifest MD5')
        for attempt in range(self.cfg.retries + 1):
            self.store.check_space()
            if self.used_requests >= self.cfg.max_requests_per_invocation:
                raise BudgetStop('Per-invocation network budget exhausted; resume with the same command')
            self.check_network()
            await limiter.wait()
            # Several tasks can be waiting on the same rate limiter. Recheck
            # and claim the shared request allowance synchronously after it.
            if self.used_requests >= self.cfg.max_requests_per_invocation:
                raise BudgetStop('Per-invocation network budget exhausted; resume with the same command')
            self.check_network()
            self.used_requests += 1
            self.store.put('requests', self.store.get('requests', 0) + 1)
            started = time.monotonic()
            try:
                async with self.http.stream('GET', url, params=params, timeout=self.cfg.timeout_seconds,
                                            follow_redirects=False, headers={'Accept-Encoding':'identity'}) as response:
                    if response.status_code in {429, 500, 502, 503, 504}:
                        if attempt == self.cfg.retries:
                            raise LiteratureError(f'Temporary HTTP {response.status_code} after retries')
                        delay = min(60., retry_delay(response.headers.get('Retry-After'), 2.**attempt))
                    else:
                        response.raise_for_status()
                        content_type = response.headers.get('content-type', '').lower()
                        if any(t in content_type for t in ('image/', 'video/', 'audio/', 'text/html', 'application/pdf', 'application/zip')):
                            raise ValueError('Unexpected response type; payload refused: '+content_type)
                        length = response.headers.get('content-length')
                        if length and int(length) > cap:
                            raise ValueError('Response exceeds per-file cap')
                        if length and int(length) > self.invocation_network_remaining():
                            raise BudgetStop('Response would exceed remaining network allowance')
                        if length and int(length) > self.store.network_remaining():
                            raise BudgetStop('Response would exceed cumulative network allowance')
                        self.check_network()
                        chunks = []; size = 0
                        chunk_size = min(65536, self.store.network_remaining(),
                                         self.invocation_network_remaining())
                        iterator = response.aiter_bytes(chunk_size=chunk_size).__aiter__()
                        while True:
                            if chunk_size > self.invocation_network_remaining():
                                raise BudgetStop('Per-invocation decoded network allowance reached')
                            self.store.reserve_network(chunk_size)
                            self.reserved_bytes += chunk_size
                            try:
                                chunk = await anext(iterator)
                            except StopAsyncIteration:
                                self.store.settle_network(chunk_size, 0)
                                self.reserved_bytes -= chunk_size
                                break
                            # Exceptions/cancellation keep the unconfirmed reservation
                            # charged, so restarting cannot bypass the campaign cap.
                            size += len(chunk); self.used_bytes += len(chunk)
                            self.store.settle_network(chunk_size, len(chunk))
                            self.reserved_bytes -= chunk_size
                            if size > cap:
                                raise ValueError('Decoded response exceeds per-file cap')
                            chunks.append(chunk)
                            # HTTPX emits a short fixed-size decoded chunk only
                            # when its byte chunker flushes at end of stream.
                            if len(chunk) < chunk_size:
                                break
                        body = b''.join(chunks)
                        if expected_md5 and hashlib.md5(body).hexdigest() != expected_md5[0].lower():
                            raise ValueError('Manifest MD5 mismatch')
                        if kind == 'xml':
                            self.last_xml = body
                        return body.decode('utf-8-sig'), {'url':url, 'retrieved_at':utc_now(),
                            'sha256':hashlib.sha256(body).hexdigest(), 'bytes':size,
                            'etag':response.headers.get('etag'), 'last_modified':response.headers.get('last-modified'),
                            'elapsed_seconds':time.monotonic()-started,
                            'manifest_md5_verified':bool(expected_md5)}
                await asyncio.sleep(delay)
            except (httpx.TimeoutException, httpx.TransportError):
                if attempt == self.cfg.retries:
                    raise
                await asyncio.sleep(2.**attempt)
        raise AssertionError('Unreachable request state')

    async def article(self, pmcid, version=None):
        # Explicit version seeds (inventory/citation lists) need no extra S3 listing.
        objects = [(version, f'{CLOUD}/metadata/{pmcid}.{version}.json')] if version else await self._version_objects(pmcid)
        eligible, rejected = [], []
        for v, url in objects:
            raw, info = await self._text(url, 'metadata')
            metadata = json.loads(raw)
            metadata_identifiers(metadata, pmcid, v)
            decision = license_decision(metadata, [])
            if not decision['metadata_fetch_allowed'] or not metadata.get('xml_url'):
                rejected.append({'version':v, 'reason':decision['reason'] if not decision['metadata_fetch_allowed'] else 'xml_unavailable',
                                 'license':decision, 'retrieval':info})
            else:
                eligible.append((v, metadata, info))
        if not eligible:
            needs_review = any(x['license']['outcome'] == 'review' for x in rejected)
            return {'pmcid':pmcid, 'status':'license_review' if needs_review else 'ineligible_or_unavailable', 'rejected':rejected}
        published = [x for x in eligible if metadata_flag(x[1].get('is_manuscript')) is False]
        choices = published or eligible
        if len(choices) != 1:
            return {'pmcid':pmcid, 'status':'version_selection_required', 'versions':[x[0] for x in choices]}
        v, metadata, meta_info = choices[0]
        url = cloud_url(metadata['xml_url'])
        if urlparse(url).path != f'/{pmcid}.{v}/{pmcid}.{v}.xml':
            raise ValueError('XML URL article/version mismatch')
        xml, xml_info = await self._text(url, 'xml')
        article = parse_article(xml, metadata, {'metadata':meta_info, 'xml':xml_info})
        article['status'] = ('eligible' if article['license']['allowed'] else
                             'license_review' if article['license']['outcome'] == 'review' else 'license_rejected')
        if article['status'] == 'eligible' and not article['has_body_text']:
            article['status'] = 'no_usable_body'
        article['acquisition'] = {'version':VERSION, 'raw_xml_sha256':xml_info['sha256'],
            'media_payloads_downloaded':False, 'supplements_downloaded':False,
            'triage':prioritize_article(article)}
        return article


async def discover(store, client):
    """Recursively split dates before paging; never silently truncate a dense day."""
    while True:
        last_lane = store.get('last_discovery_lane', '')
        lanes = store.config.lanes
        names = [x.name for x in lanes]
        begin = (names.index(last_lane)+1) % len(names) if last_lane in names else 0
        partition = None
        for lane in lanes[begin:] + lanes[:begin]:
            if lane.max_candidates is not None and store.lane_count(lane.name) >= lane.max_candidates:
                with store.db:
                    store.db.execute("UPDATE partitions SET status='quota_reached',error=? WHERE lane=? AND status='pending'",
                        ('Purposive lane quota reached; query coverage is incomplete', lane.name))
                continue
            partition = store.db.execute("SELECT * FROM partitions WHERE status='pending' AND lane=? ORDER BY end DESC,start DESC,rowid LIMIT 1", (lane.name,)).fetchone()
            if partition:
                break
        if not partition:
            return
        term = (f'({partition["query"]}) AND ({LICENSE_QUERY}) AND '
                f'("{partition["start"]}"[pdat] : "{partition["end"]}"[pdat])')
        remaining = store.config.max_candidates - store.get('candidate_count', 0)
        if lane.max_candidates is not None:
            remaining = min(remaining, lane.max_candidates - store.lane_count(lane.name))
        if remaining <= 0:
            raise BudgetStop('Candidate cap reached; search coverage is incomplete')
        # Count-only first, then bounded pages. Never fetch an inaccessible tail.
        retmax = 0 if partition['count'] is None else min(store.config.page_size, remaining)
        params = {'db':'pmc', 'term':term, 'retmode':'json', 'retmax':retmax,
                  'retstart':partition['offset'], 'sort':'pub date', 'tool':'openpatients2'}
        if os.environ.get('NCBI_EMAIL'):
            params['email'] = os.environ['NCBI_EMAIL']
        raw, info = await client._text(ESEARCH, 'search', params)
        data = json.loads(raw)['esearchresult']
        if any(data.get('errorlist', {}).values()) or data.get('ERROR'):
            raise ValueError('NCBI rejected query: '+str(data.get('errorlist') or data.get('ERROR')))
        count = int(data['count']); start = dt.date.fromisoformat(partition['start']); end = dt.date.fromisoformat(partition['end'])
        with store.db:
            store.db.execute('INSERT OR REPLACE INTO settings VALUES(?,?)',
                ('last_discovery_lane', json.dumps(partition['lane'])))
            store.db.execute('INSERT INTO discovery_responses(partition_id,params,retrieval,count,ids) VALUES(?,?,?,?,?)',
                (partition['id'],json.dumps(params),json.dumps(info),count,json.dumps(data.get('idlist', []))))
            if count > store.config.search_partition_limit:
                if partition['offset']:
                    store.db.execute("UPDATE partitions SET status='unstable',error=? WHERE id=?",
                                     ('Count increased beyond partition limit mid-pagination', partition['id']))
                elif start == end:
                    store.db.execute("UPDATE partitions SET status='needs_inventory',count=?,error=? WHERE id=?",
                                     (count, 'One publication day exceeds the search partition limit; import inventory instead', partition['id']))
                else:
                    middle = start + (end-start)//2
                    for a, b in ((start,middle),(middle+dt.timedelta(days=1),end)):
                        add_partition(store, partition['lane'], partition['query'], a, b)
                    store.db.execute("UPDATE partitions SET status='split',count=? WHERE id=?", (count,partition['id']))
                continue
            if partition['count'] is not None and partition['count'] != count:
                store.db.execute("UPDATE partitions SET status='unstable',error=? WHERE id=?",
                                 ('Search count changed during pagination; do not claim a snapshot census',partition['id']))
                continue
            if retmax == 0:
                store.db.execute('UPDATE partitions SET count=?,translation=?,status=? WHERE id=?',
                                 (count,data.get('querytranslation'), 'done' if count == 0 else 'pending', partition['id']))
                continue
            ids = data.get('idlist', [])
            if not ids and partition['offset'] < count:
                store.db.execute("UPDATE partitions SET status='unstable',error=? WHERE id=?", ('Unexpected empty page',partition['id']))
                continue
            if len(ids) > retmax or any(not re.fullmatch(r'[1-9][0-9]*', str(x)) for x in ids):
                raise ValueError('Malformed or oversized ESearch ID page')
            origin = 'esearch:'+partition['id']
            for ident in ids:
                store.add('PMC'+str(ident), None, origin)
            offset = partition['offset'] + len(ids)
            state = 'done' if offset >= count else 'pending'
            error = None
            if state == 'done' and store.db.execute('SELECT count(*) FROM origins WHERE origin=?', (origin,)).fetchone()[0] != count:
                state, error = 'unstable', 'Distinct ID count differs from search count'
            store.db.execute('UPDATE partitions SET offset=?,status=?,error=?,translation=? WHERE id=?',
                             (offset,state,error,data.get('querytranslation'),partition['id']))


def import_candidates(store, input_path, format='ids', file_schema='Bucket, Key, LastModifiedDate, ETag'):
    """Stream user-supplied IDs or a PMC inventory CSV(.gz); never download a census.

    Inventory column order comes from the pinned manifest's fileSchema. Versions
    remain distinct and inventory provenance does not grant permission to reuse.
    """
    path = Path(input_path).resolve()
    stat = path.stat()
    identity = {'path':str(path), 'bytes':stat.st_size, 'mtime_ns':stat.st_mtime_ns,
                'format':format, 'file_schema':file_schema}
    key = 'import:'+json_digest(identity)
    progress = store.get(key, {'next_row':0, 'status':'pending', 'invalid_rows':0})
    if progress['status'] == 'done':
        return progress
    columns = [c.strip() for c in file_schema.split(',')]
    if format == 'inventory' and ('Bucket' not in columns or 'Key' not in columns):
        raise ValueError('Use the inventory manifest fileSchema including Bucket and Key')
    opener = gzip.open if path.suffix == '.gz' else open
    with opener(path, 'rt', newline='') as f:
        rows = csv.reader(f) if format == 'inventory' else ([line.strip()] for line in f)
        for index, row in enumerate(rows):
            if index < progress['next_row']:
                continue
            store.check_space()
            match = None
            if format == 'inventory':
                if len(row) != len(columns):
                    raise ValueError('Inventory column count differs from manifest fileSchema')
                values = dict(zip(columns, row))
                if values['Bucket'] == 'pmc-oa-opendata':
                    match = re.fullmatch(r'metadata/(PMC[1-9][0-9]*)\.([1-9][0-9]*)\.json', unquote(values['Key']))
            elif row and row[0] and not row[0].startswith('#'):
                match = re.fullmatch(r'(PMC[1-9][0-9]*)(?:\.([1-9][0-9]*))?', row[0])
            with store.db:
                if match:
                    store.add(match[1], int(match[2]) if match[2] else None, key)
                elif format == 'ids' and row and row[0] and not row[0].startswith('#'):
                    progress['invalid_rows'] += 1
                progress['next_row'] = index+1
                # Part of the same transaction as candidate insertion.
                store.db.execute('INSERT OR REPLACE INTO settings VALUES(?,?)', (key,json.dumps(progress)))
    progress.update(status='done', input=identity)
    store.put(key, progress)
    return progress


async def fetch_pending(store, client, limit):
    """Fetch bounded waves on one event loop; commit each article atomically."""
    if limit < 1:
        raise ValueError('Fetch limit must be positive')
    completed = 0
    candidates = store.db.execute("SELECT * FROM candidates WHERE state IN ('pending','retryable') AND attempts < ? ORDER BY rowid LIMIT ?",
                                  (store.config.max_fetch_attempts, limit)).fetchall()
    async def fetch_one(candidate):
        store.check_space()
        client.last_xml = None
        try:
            started = time.monotonic()
            article = await client.article(candidate['pmcid'], candidate['version'])
            state = article['status']
            # Retain small rejection/version decisions, not a rejected full payload.
            details = {k: article[k] for k in ('status','pmcid','article_id','license','rejected','versions','retrieval') if k in article}
            if state == 'eligible':
                article['acquisition']['sample'] = store.candidate_provenance(candidate['id'])
                article['acquisition']['discovery_lanes'] = [x['name'] for x in article['acquisition']['sample']['lanes']]
                article['acquisition']['origins'] = article['acquisition']['sample']['origins']
                article['acquisition']['elapsed_seconds'] = time.monotonic() - started
                raw = json.dumps(article, ensure_ascii=False, allow_nan=False).encode()
                if len(raw) > store.config.max_article_json_bytes:
                    raise ValueError('Parsed article exceeds JSON size cap')
                if not client.last_xml:
                    raise ValueError('Raw XML missing from verified download')
                packed = gzip.compress(raw, mtime=0)
                xml_packed = gzip.compress(client.last_xml, mtime=0)
                with store.db:
                    prior = store.db.execute('SELECT xml_sha256 FROM articles WHERE article_id=?', (article['article_id'],)).fetchone()
                    xml_hash = hashlib.sha256(client.last_xml).hexdigest()
                    if prior and prior[0] != xml_hash:
                        raise ValueError('Source changed within this campaign; use a new versioned acquisition')
                    store.db.execute('INSERT OR IGNORE INTO articles VALUES(?,?,?,?,?,?,?)',
                        (article['article_id'],packed,xml_packed,hashlib.sha256(raw).hexdigest(),xml_hash,
                         article['acquisition']['triage']['route'],utc_now()))
                    store.db.execute('UPDATE candidates SET state=?,attempts=attempts+1,result=?,article_id=?,error=NULL,updated=? WHERE id=?',
                        (state,json.dumps(details),article['article_id'],utc_now(),candidate['id']))
            else:
                with store.db:
                    store.db.execute('UPDATE candidates SET state=?,attempts=attempts+1,result=?,error=NULL,updated=? WHERE id=?',
                        (state,json.dumps(details),utc_now(),candidate['id']))
            return 1
        except BudgetStop:
            raise  # leave row resumable, not a false rejection
        except (ValueError, UnicodeError, LiteratureError, httpx.HTTPError) as exc:
            retryable = isinstance(exc, (LiteratureError, httpx.TimeoutException, httpx.TransportError)) or (
                isinstance(exc, httpx.HTTPStatusError) and (exc.response.status_code == 429 or exc.response.status_code >= 500))
            state = 'retryable' if retryable and candidate['attempts']+1 < store.config.max_fetch_attempts else 'failed'
            with store.db:
                store.db.execute('UPDATE candidates SET state=?,attempts=attempts+1,error=?,updated=? WHERE id=?',
                                 (state,f'{type(exc).__name__}: {str(exc)[:1500]}',utc_now(),candidate['id']))
            return 0
    for begin in range(0, len(candidates), store.config.fetch_concurrency):
        tasks = [asyncio.create_task(fetch_one(candidate), name='pmc-fetch:'+candidate['id'])
                 for candidate in candidates[begin:begin+store.config.fetch_concurrency]]
        try:
            completed += sum(await asyncio.gather(*tasks))
        except BaseException:
            # gather propagates the first stop/error promptly; other downloads
            # must be drained before the caller closes HTTP clients or SQLite.
            for task in tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            raise
    return completed


def status(work):
    work = Path(work).resolve()
    uri = (work / 'corpus.sqlite').as_uri() + '?mode=ro'
    db = sqlite3.connect(uri, uri=True)
    try:
        states = dict(db.execute('SELECT state,count(*) FROM candidates GROUP BY state'))
        partitions = dict(db.execute('SELECT status,count(*) FROM partitions GROUP BY status'))
        settings = {k:json.loads(v) for k,v in db.execute('SELECT key,value FROM settings')}
        config = settings.get('config', {})
        lane_reports = []
        for lane in config.get('lanes', []):
            lane_states = dict(db.execute('''SELECT c.state,count(DISTINCT c.id) FROM candidates c
                JOIN origins o ON o.candidate=c.id JOIN partitions p ON o.origin='esearch:'||p.id
                WHERE p.lane=? GROUP BY c.state''', (lane['name'],)))
            lane_reports.append({**lane, 'selected_candidates':sum(lane_states.values()), 'candidate_states':lane_states,
                'search_partitions':dict(db.execute('SELECT status,count(*) FROM partitions WHERE lane=? GROUP BY status', (lane['name'],)))})
        charged = settings.get('network_bytes',0) + settings.get('network_reserved_bytes',0)
        network_cap = config.get('max_network_bytes_total', MAX_NETWORK_BYTES_TOTAL)
        return {'work_dir':str(work), 'version':settings.get('version'), 'candidates':states,
                'stored_articles':db.execute('SELECT count(*) FROM articles').fetchone()[0],
                'screening_routes':dict(db.execute('SELECT route,count(*) FROM articles GROUP BY route')),
                'search_partitions':partitions,
                'discovery_complete_for_configured_queries':bool(partitions) and set(partitions) <= {'done','split'},
                'fetch_queue_complete':not any(states.get(x,0) for x in ('pending','retryable')),
                'cumulative_decoded_network_bytes':settings.get('network_bytes',0),
                'unconfirmed_reserved_network_bytes':settings.get('network_reserved_bytes',0),
                'cumulative_network_budget_charged_bytes':charged,
                'max_network_bytes_total':network_cap, 'remaining_network_bytes':max(0,network_cap-charged),
                'discovery_lanes':lane_reports,
                'sample_method':'bounded publication-date ordered query prefixes; purposive, not random',
                'config_sha256':json_digest(config),
                'cumulative_requests':settings.get('requests',0), 'last_run':settings.get('last_run'),
                'imports':[{ 'id':key, **value} for key,value in settings.items() if key.startswith('import:')],
                'storage_bytes':sum(p.stat().st_size for p in work.rglob('*') if p.is_file()),
                'notice':'Eligible means acquisition/rights gates passed, not verified original patient cases. No pixels or supplements inspected.'}
    finally:
        db.close()


async def run(work, action='run', fetch_limit=20, http=None):
    store = Store(Path(work)); client = None; stop = None
    started = time.monotonic(); initial_bytes = store.get('network_bytes', 0)
    try:
        client = AcquisitionClient(store, http)
        store.put('last_run', {'status':'running', 'action':action, 'started_at':utc_now()})
        if action in {'discover','run'}:
            try:
                await discover(store, client)
            except BudgetStop as exc:
                stop = str(exc)
                # Candidate count is a queue bound, so downloaded queued rows may
                # still be processed. Network/storage exhaustion cannot be ignored.
                if not stop.startswith('Candidate cap') or action == 'discover':
                    raise
        if action in {'fetch','run'}:
            await fetch_pending(store, client, fetch_limit)
        store.put('last_run', {'status':'bounded_stop' if stop else 'completed_invocation', 'action':action,
                              'reason':stop, 'finished_at':utc_now()})
    except BudgetStop as exc:
        store.put('last_run', {'status':'bounded_stop', 'action':action, 'reason':str(exc), 'finished_at':utc_now()})
    except sqlite3.OperationalError as exc:
        if 'full' not in str(exc).lower():
            raise
        store.db.rollback()
        store.put('last_run', {'status':'bounded_stop', 'reason':'SQLite storage allowance reached', 'finished_at':utc_now()})
    except BaseException as exc:
        store.db.rollback()
        store.put('last_run', {'status':'interrupted' if isinstance(exc, (KeyboardInterrupt,asyncio.CancelledError)) else 'failed',
                              'action':action, 'error':str(exc)[:1500], 'finished_at':utc_now()})
        raise
    finally:
        last_run = store.get('last_run', {})
        elapsed = time.monotonic() - started
        received = store.get('network_bytes', 0) - initial_bytes
        store.put('last_run', {**last_run, 'elapsed_seconds':elapsed,
            'decoded_network_bytes':received, 'requests':client.used_requests if client else 0,
            'decoded_bytes_per_second':received/elapsed if elapsed else 0})
        if client:
            await client.close()
        store.close()
    return status(work)


def export(work, output, max_bytes=256_000_000, *, license_review=False):
    """Read-only streaming JSONL(.gz), with checksum and decompression limits."""
    output = Path(output)
    if output.exists():
        raise ValueError('Use a new export filename')
    if max_bytes < 1:
        raise ValueError('Positive uncompressed export limit required')
    db = sqlite3.connect((Path(work).resolve() / 'corpus.sqlite').as_uri()+'?mode=ro', uri=True)
    article_cap = json.loads(db.execute("SELECT value FROM settings WHERE key='config'").fetchone()[0])['max_article_json_bytes']
    count = total = 0
    created = False
    try:
        # Exclusive output creation; remove only our own partial export on failure.
        with output.open('xb') as raw_file:
            created = True
            sink = gzip.GzipFile(fileobj=raw_file, mode='wb', mtime=0) if output.suffix == '.gz' else raw_file
            try:
                rows = (db.execute("SELECT result,NULL FROM candidates WHERE state='license_review' ORDER BY id")
                        if license_review else db.execute('SELECT json_gz,json_sha256 FROM articles ORDER BY article_id'))
                for packed, expected in rows:
                    if license_review:
                        data = packed.encode('utf-8')
                    else:
                        with gzip.GzipFile(fileobj=io.BytesIO(packed)) as article:
                            data = article.read(min(article_cap, max_bytes-total)+1)
                    if len(data) > min(article_cap, max_bytes-total):
                        raise BudgetStop('Export byte limit reached; choose an explicit larger limit')
                    if expected is not None and hashlib.sha256(data).hexdigest() != expected:
                        raise ValueError('Stored article checksum mismatch')
                    total += len(data)+1
                    if total > max_bytes:
                        raise BudgetStop('Export byte limit reached; choose an explicit larger limit')
                    sink.write(data+b'\n'); count += 1
            finally:
                if sink is not raw_file:
                    sink.close()
    except BaseException:
        if created:
            output.unlink(missing_ok=True)
        raise
    finally:
        db.close()
    return {'output':str(output), 'articles':count, 'kind':'license_review' if license_review else 'eligible_articles',
            'uncompressed_bytes':total, 'stored_bytes':output.stat().st_size}


def add_parser(sub):
    cmd = sub.add_parser('corpus', help='Resumable bounded CPU-only PMC discovery/acquisition')
    actions = cmd.add_subparsers(dest='corpus_action', required=True)
    for action in ('init','run','discover','fetch','status','import','export'):
        p = actions.add_parser(action)
        p.add_argument('--work-dir', required=True)
        if action in {'init','run'}:
            p.add_argument('--config', required=action=='init', help='Required for a new campaign; resume uses saved config')
        if action in {'run','fetch'}:
            p.add_argument('--limit', type=int, default=20, help='Maximum fetch attempts this invocation')
        if action == 'import':
            p.add_argument('--input', required=True)
            p.add_argument('--format', choices=['ids','inventory'], default='ids')
            p.add_argument('--file-schema', default='Bucket, Key, LastModifiedDate, ETag')
        if action == 'export':
            p.add_argument('--output', required=True)
            p.add_argument('--max-bytes', type=int, default=256_000_000)
            p.add_argument('--license-review', action='store_true', help='Export deferred rights evidence instead of eligible article bodies')


def dispatch(args):
    action = args.corpus_action
    if action == 'init':
        return initialize(args.work_dir, args.config)
    if action == 'status':
        return status(args.work_dir)
    if action == 'export':
        return export(args.work_dir, args.output, args.max_bytes, license_review=args.license_review)
    if action == 'import':
        store = Store(Path(args.work_dir))
        try:
            try:
                report = import_candidates(store, args.input, args.format, args.file_schema)
            except BudgetStop as exc:
                report = {'status':'bounded_stop','reason':str(exc)}
        finally:
            store.close()
        return {'import':report, **status(args.work_dir)}
    if action == 'run' and args.config:
        initialize(args.work_dir, args.config)
    return asyncio.run(run(args.work_dir, action, getattr(args, 'limit', 20)))
