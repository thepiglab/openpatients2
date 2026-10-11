"""Acquire one article's declared supplements into a bounded, non-release cache.

Commands (run from the repo root, PYTHONPATH=src:benchmarks/dense500):
  supplement_assets.py acquire ROOT --article ID [--force]
  supplement_assets.py peek    ROOT --article ID      # recompute peeks from cached files
  supplement_assets.py status  [ROOT]                 # cache usage and caps
  supplement_assets.py cleanup ROOT --article ID      # only after dual-signed manifest
  supplement_assets.py extract ROOT --article ID --supplement SID   # PDF members of a ZIP
  supplement_assets.py extract-text ROOT --article ID --supplement SID   # DOCX/DOC/RTF/ODT (or such ZIP members)

The shared cache has a HARD 30 GB cap enforced under an exclusive lock, and a
per-file cap. Larger files are never fully downloaded: a HEAD request gives size
and type, then only a bounded head sample (plus a ZIP central-directory tail) is
kept. Downloading or peeking is not inspection; review is recorded separately in
the supplement manifest and its receipts. Writes `supplement-assets/ID.json`.
"""
import argparse
from datetime import datetime, timezone
import fcntl
import hashlib
import json
from pathlib import Path
import re
import shutil
from urllib.parse import urlparse

import httpx

from openpatients2.supplement_peek import PEEK_VERSION, extension, peek_file

CACHE = Path(__file__).resolve().parents[2] / 'data/.dense-supplement-cache'
MAX_CACHE_BYTES = 30_000_000_000      # hard cap for the whole shared cache (30 GB)
MAX_FILE_BYTES = 250_000_000          # larger files are sampled, never fully downloaded
HEAD_SAMPLE_BYTES = 2_000_000
TAIL_SAMPLE_BYTES = 4_000_000         # ZIP central directory only
ALLOWED_HOSTS = {'pmc-oa-opendata.s3.amazonaws.com', 'cdn.ncbi.nlm.nih.gov', 'pmc.ncbi.nlm.nih.gov'}
FORMAT = 'dense-supplement-acquisition/1'


class CacheFull(Exception):
    pass


def cache_usage(cache=None):
    cache = Path(cache or CACHE)
    return sum(p.stat().st_size for p in cache.rglob('*') if p.is_file() and p.name != '.lock') if cache.exists() else 0


def _safe_ext(name):
    ext = extension(name)
    return ext if re.fullmatch(r'\.[a-z0-9]{1,8}', ext) else '.bin'


def _stream(client, url, destination, limit, *, headers=None):
    """Write at most `limit` bytes; return (bytes_written, truncated, response headers)."""
    written = 0; truncated = False
    temporary = destination.with_name(destination.name + '.partial')
    try:
        with client.stream('GET', url, headers=headers or {}) as response:
            if response.status_code not in (200, 206):
                raise ValueError(f'HTTP {response.status_code}')
            with temporary.open('wb') as out:
                for chunk in response.iter_bytes():
                    room = limit - written
                    if len(chunk) > room:
                        out.write(chunk[:room]); written += room; truncated = True
                        break
                    out.write(chunk); written += len(chunk)
            response_headers = dict(response.headers)
        temporary.replace(destination)
        return written, truncated, response_headers
    finally:
        temporary.unlink(missing_ok=True)


def _probe(client, url):
    """Size and type without downloading: HEAD, falling back to a 1-byte range."""
    try:
        response = client.head(url)
        if response.status_code == 200:
            size = response.headers.get('content-length')
            return (int(size) if size and size.isdigit() else None), response.headers.get('content-type')
    except httpx.HTTPError:
        pass
    with client.stream('GET', url, headers={'Range': 'bytes=0-0'}) as response:
        if response.status_code == 206:
            match = re.search(r'/(\d+)$', response.headers.get('content-range', ''))
            return (int(match.group(1)) if match else None), response.headers.get('content-type')
        if response.status_code == 200:
            size = response.headers.get('content-length')
            return (int(size) if size and size.isdigit() else None), response.headers.get('content-type')
        raise ValueError(f'HTTP {response.status_code}')


def _acquire_asset(client, aid, supplement, asset, used, cache, caps):
    max_cache, max_file = caps
    url = asset.get('url')
    filename = asset.get('filename') or (url or '').split('?')[0].rsplit('/', 1)[-1]
    row = {'supplement_id': supplement['supplement_id'], 'url': url, 'filename': filename,
           'original_manifest_url': asset.get('original_manifest_url'), 'declared_md5': asset.get('md5'),
           'declared_kind': asset.get('kind'), 'extension': extension(filename) or None,
           'http_content_type': None, 'bytes': None, 'download_status': None, 'download_reason': None,
           'sha256': None, 'md5_verified': None, 'path': None, 'sample_path': None, 'sample_sha256': None,
           'sample_bytes': None, 'tail_path': None, 'tail_sha256': None, 'tail_bytes': None,
           'detected_type': None, 'inspection_status': 'not_inspectable', 'peek': None, 'limitations': [],
           'errors': []}
    parsed = urlparse(url or '')
    if parsed.scheme != 'https' or parsed.hostname not in ALLOWED_HOSTS:
        row.update(download_status='rejected_host', errors=['Only declared HTTPS PMC hosts are accepted'])
        return row, used
    base = cache / aid / (hashlib.sha256(url.encode()).hexdigest()[:40])
    full = base.with_name(base.name + _safe_ext(filename))
    sample = base.with_name(base.name + '.head' + _safe_ext(filename))
    tail = base.with_name(base.name + '.tail')
    full.parent.mkdir(parents=True, exist_ok=True)
    try:
        size, ctype = _probe(client, url)
        row.update(bytes=size, http_content_type=ctype)
        if not full.exists():
            reason = ('size_unknown' if size is None else 'exceeds_per_file_cap' if size > max_file
                      else 'cache_cap_reached' if used + size > max_cache else None)
            if reason is None:
                written, truncated, _ = _stream(client, url, full, size)
                if truncated or written != size:
                    full.unlink(missing_ok=True)
                    raise ValueError('Downloaded byte count differs from declared size')
                used += written
            else:
                row['download_reason'] = reason
        if full.exists():
            data = full.read_bytes()
            row.update(download_status='downloaded', bytes=len(data), path=str(full.resolve()),
                       sha256=hashlib.sha256(data).hexdigest())
            if row['declared_md5']:
                row['md5_verified'] = hashlib.md5(data).hexdigest() == row['declared_md5']
                if not row['md5_verified']:
                    full.unlink(); row.update(download_status='unavailable', path=None, sha256=None)
                    raise ValueError('Source manifest MD5 mismatch; cached copy removed')
            row['detected_type'], row['inspection_status'], row['peek'], limits = peek_file(full, filename)
            row['limitations'] += limits
            return row, used
        # Bounded samples only. They also count against the hard cap.
        if not sample.exists():
            if used + HEAD_SAMPLE_BYTES > max_cache:
                raise CacheFull('Shared 30 GB supplement cache cap reached; clean up signed articles first')
            written, _, _ = _stream(client, url, sample, HEAD_SAMPLE_BYTES,
                                    headers={'Range': f'bytes=0-{HEAD_SAMPLE_BYTES - 1}'})
            used += written
        data = sample.read_bytes()
        row.update(download_status='head_sample_only', sample_path=str(sample.resolve()),
                   sample_sha256=hashlib.sha256(data).hexdigest(), sample_bytes=len(data))
        tail_data = None
        if data.startswith(b'PK') and size and size > HEAD_SAMPLE_BYTES:
            if not tail.exists() and used + TAIL_SAMPLE_BYTES <= max_cache:
                written, _, _ = _stream(client, url, tail, TAIL_SAMPLE_BYTES,
                                        headers={'Range': f'bytes=-{TAIL_SAMPLE_BYTES}'})
                used += written
            if tail.exists():
                tail_data = tail.read_bytes()
                row.update(tail_path=str(tail.resolve()), tail_sha256=hashlib.sha256(tail_data).hexdigest(),
                           tail_bytes=len(tail_data))
        row['detected_type'], row['inspection_status'], row['peek'], limits = peek_file(
            sample, filename, complete=False, tail=tail_data, total_bytes=size)
        row['limitations'] += limits + [f'Not fully downloaded: {row["download_reason"]}']
    except CacheFull as exc:
        row.update(download_status='unavailable', errors=[str(exc)])
    except (httpx.HTTPError, ValueError, OSError) as exc:
        row.update(download_status=row['download_status'] or 'unavailable', errors=[f'{type(exc).__name__}: {exc}'])
    return row, used


def acquire(root, aid, *, client=None, cache=None, caps=None, force=False):
    root = Path(root); cache = Path(cache or CACHE); caps = caps or (MAX_CACHE_BYTES, MAX_FILE_BYTES)
    source = json.loads((root / 'sources' / f'{aid}.json').read_text())
    out_path = root / 'supplement-assets' / f'{aid}.json'
    signed = [root / f'supplement-manifest-{r}-reviews' / f'{aid}.json' for r in ('agent', 'parent')]
    if out_path.exists() and any(p.exists() for p in signed) and not force:
        raise ValueError('A signed supplement manifest binds this acquisition; pass --force to re-acquire (receipts become stale)')
    cache.mkdir(parents=True, exist_ok=True)
    owns = client is None
    client = client or httpx.Client(timeout=120, follow_redirects=False)
    rows = []
    try:
        # Different annotators share one capped cache. Reserve and write under one lock.
        with (cache / '.lock').open('a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            used = cache_usage(cache)
            for supplement in source.get('supplements', []):
                if not supplement.get('assets'):
                    rows.append({'supplement_id': supplement['supplement_id'], 'url': None,
                                 'filename': None, 'download_status': 'no_declared_asset',
                                 'inspection_status': 'not_inspectable', 'peek': None,
                                 'limitations': ['Source metadata lists no downloadable asset'], 'errors': []})
                    continue
                for asset in supplement['assets']:
                    row, used = _acquire_asset(client, aid, supplement, asset, used, cache, caps)
                    rows.append(row)
            report = {'format': FORMAT, 'article_id': aid, 'source_text_sha256': source['text_sha256'],
                      'files': rows, 'cache_bytes_after': cache_usage(cache), 'cache_limit_bytes': caps[0],
                      'per_file_limit_bytes': caps[1], 'acquired_at': datetime.now(timezone.utc).isoformat(),
                      'peek_version': PEEK_VERSION,
                      'download_is_not_inspection': True}
            out_path.parent.mkdir(exist_ok=True)
            temporary = out_path.with_suffix('.json.tmp')
            temporary.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
            temporary.replace(out_path)
    finally:
        if owns:
            client.close()
    return report


def repeek(root, aid, *, force=False):
    """Recompute peeks from cached files (e.g. after installing a tool).

    Refused once any manifest receipt exists: a new peek engine would make the
    signed machine blocks stale. Existing records keep their original peeks.
    """
    path = Path(root) / 'supplement-assets' / f'{aid}.json'
    signed = [Path(root) / f'supplement-manifest-{r}-reviews' / f'{aid}.json' for r in ('agent', 'parent')]
    if any(p.exists() for p in signed) and not force:
        raise ValueError('A signed supplement manifest binds these peeks; pass --force to re-peek (receipts become stale)')
    report = json.loads(path.read_text())
    report['peek_version'] = PEEK_VERSION
    for row in report['files']:
        if row.get('path') and Path(row['path']).exists():
            row['detected_type'], row['inspection_status'], row['peek'], row['limitations'] = peek_file(row['path'], row['filename'])
        elif row.get('sample_path') and Path(row['sample_path']).exists():
            tail = Path(row['tail_path']).read_bytes() if row.get('tail_path') and Path(row['tail_path']).exists() else None
            row['detected_type'], row['inspection_status'], row['peek'], limits = peek_file(
                row['sample_path'], row['filename'], complete=False, tail=tail, total_bytes=row.get('bytes'))
            row['limitations'] = limits + [f'Not fully downloaded: {row.get("download_reason")}']
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
    return report


def extract_archive(root, aid, sid, *, cache=None, max_cache=MAX_CACHE_BYTES):
    """Bounded extraction of a downloaded ZIP supplement's PDF members into the cache.

    Writes `supplement-archive-members/ID.SID.json` and a review skeleton in
    `document-submission-drafts/ID.supplements.SID.json`
    (`dense-supplemental-archive-document-reference/1`). Only PDF members are
    written, each under a hashed name inside this article's cache folder.
    """
    from supplement_review import (ARCHIVE_FORMAT, MAX_MEMBER_BYTES, member_url, pdf_page_count,
                                   zip_member_bytes, zip_members)
    root = Path(root); cache = Path(cache or CACHE)
    source = json.loads((root / 'sources' / f'{aid}.json').read_text())
    acquisition = json.loads((root / 'supplement-assets' / f'{aid}.json').read_text())
    rows = [r for r in acquisition['files'] if r['supplement_id'] == sid]
    if len(rows) != 1 or rows[0].get('download_status') != 'downloaded' or rows[0].get('detected_type') != 'zip':
        raise ValueError('Extraction needs exactly one fully downloaded ZIP asset for this supplement')
    row = rows[0]
    if hashlib.sha256(Path(row['path']).read_bytes()).hexdigest() != row['sha256']:
        raise ValueError('Cached ZIP bytes changed after acquisition')
    pdfs, others = zip_members(row['path'])
    folder = cache / aid / 'members'
    members = []
    cache.mkdir(parents=True, exist_ok=True)
    with (cache / '.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        used = cache_usage(cache)
        folder.mkdir(parents=True, exist_ok=True)
        for name in pdfs:
            data = zip_member_bytes(row['path'], name, MAX_MEMBER_BYTES)
            if not data.startswith(b'%PDF-'):
                raise ValueError(f'Member {name} is not a PDF')
            target = folder / (hashlib.sha256(f'{sid}/{name}'.encode()).hexdigest()[:40] + '.pdf')
            if not target.exists():
                if used + len(data) > max_cache:
                    raise CacheFull('Shared 30 GB supplement cache cap reached; clean up signed articles first')
                target.write_bytes(data); used += len(data)
            members.append({'url': member_url(row['url'], name), 'member': name,
                            'sha256': hashlib.sha256(data).hexdigest(), 'bytes': len(data),
                            'path': str(target.resolve()), 'media_type': 'application/pdf',
                            'page_count': pdf_page_count(target)})
    archive = {'url': row['url'], 'original_manifest_url': row.get('original_manifest_url'), 'filename': row['filename'],
               'md5': row.get('declared_md5'), 'sha256': row['sha256'], 'bytes': row['bytes'], 'path': row['path'],
               'source_license': source['license'], 'media_type': 'application/zip', 'members': pdfs, 'other_members': others}
    record = {'article_id': aid, 'supplement_id': sid, 'archive': archive, 'members': members,
              'extraction_is_not_inspection': True}
    out = root / 'supplement-archive-members'; out.mkdir(exist_ok=True)
    (out / f'{aid}.{sid}.json').write_text(json.dumps(record, ensure_ascii=False, indent=2) + '\n')
    skeleton = {'format': ARCHIVE_FORMAT, 'article_id': aid, 'source_text_sha256': source['text_sha256'],
                'supplement_id': sid, 'status': 'partial', 'document_classification': 'other',
                'contents_summary': 'DRAFT: describe after viewing every member page', 'archive': archive,
                'assets': [{**m, 'pages': []} for m in members],
                'limitations': ['DRAFT skeleton: pages not yet inspected'] + (
                    [f'Non-PDF members not reviewable by this path: {others}'] if others else [])}
    drafts = root / 'document-submission-drafts'; drafts.mkdir(exist_ok=True)
    (drafts / f'{aid}.supplements.{sid}.json').write_text(json.dumps(skeleton, ensure_ascii=False, indent=2) + '\n')
    return record


def extract_text_documents(root, aid, sid, *, cache=None, max_cache=MAX_CACHE_BYTES):
    """Review skeleton for a word-processor supplement (DOCX/DOC/RTF/ODT, or such members of one ZIP).

    Units are extracted offline by openpatients2.document_text (anydoc Inline.text; no hosted OCR).
    ZIP members are written under hashed names into this article's cache folder. Writes the skeleton
    `document-submission-drafts/ID.supplements.SID.json` (`dense-supplemental-text-document-reference/1`).
    Extraction is not inspection: the reviewer reads every unit and fills `blocks`.
    """
    from supplement_review import (MAX_MEMBER_BYTES, _text_format, draft_text_document, zip_member_bytes,
                                   zip_text_members)
    root = Path(root); cache = Path(cache or CACHE)
    source = json.loads((root / 'sources' / f'{aid}.json').read_text())
    acquisition = json.loads((root / 'supplement-assets' / f'{aid}.json').read_text())
    rows = [r for r in acquisition['files'] if r['supplement_id'] == sid]
    if not rows or any(r.get('download_status') != 'downloaded' for r in rows):
        raise ValueError('Text-document extraction needs fully downloaded supplement assets')
    for r in rows:
        if hashlib.sha256(Path(r['path']).read_bytes()).hexdigest() != r['sha256']:
            raise ValueError('Cached supplement bytes changed after acquisition')
    declared = {a['url']: a for s in source.get('supplements', []) if s['supplement_id'] == sid for a in s.get('assets', [])}
    archive = None
    if len(rows) == 1 and rows[0].get('detected_type') == 'zip':
        row = rows[0]
        docs, others = zip_text_members(row['path'])
        if not docs:
            raise ValueError('ZIP has no DOCX/DOC/RTF/ODT members')
        folder = cache / aid / 'members'; files = []
        cache.mkdir(parents=True, exist_ok=True)
        with (cache / '.lock').open('a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            used = cache_usage(cache); folder.mkdir(parents=True, exist_ok=True)
            for name in docs:
                data = zip_member_bytes(row['path'], name, MAX_MEMBER_BYTES)
                target = folder / (hashlib.sha256(f'{sid}/{name}'.encode()).hexdigest()[:40] + '.' + _text_format(name))
                if not target.exists():
                    if used + len(data) > max_cache:
                        raise CacheFull('Shared 30 GB supplement cache cap reached; clean up signed articles first')
                    target.write_bytes(data); used += len(data)
                files.append(({'url': row['url']}, target, name))
        archive = {'url': row['url'], 'original_manifest_url': row.get('original_manifest_url'), 'filename': row['filename'],
                   'md5': row.get('declared_md5'), 'sha256': row['sha256'], 'bytes': row['bytes'], 'path': row['path'],
                   'source_license': source['license'], 'media_type': 'application/zip', 'members': docs, 'other_members': others}
    else:
        files = []
        for r in rows:
            if _text_format(r.get('filename') or r['url']) is None:
                raise ValueError(f"{r.get('filename')}: not a DOCX/DOC/RTF/ODT asset")
            files.append(({**declared.get(r['url'], {}), 'url': r['url']}, Path(r['path']), None))
    skeleton = draft_text_document(source, sid, files)
    skeleton['archive'] = archive
    if archive and archive['other_members']:
        skeleton['limitations'].append(f"Non-document members not reviewable by this path: {archive['other_members']}")
    drafts = root / 'document-submission-drafts'; drafts.mkdir(exist_ok=True)
    (drafts / f'{aid}.supplements.{sid}.json').write_text(json.dumps(skeleton, ensure_ascii=False, indent=2) + '\n')
    return {'draft': f'document-submission-drafts/{aid}.supplements.{sid}.json',
            'assets': [{'url': a['url'], 'units': len(a['units']), 'nonliteral_units': a['extraction']['nonliteral_units']}
                       for a in skeleton['assets']]}


def cleanup(root, aid, *, cache=None, verify=None):
    """Remove an article's cached supplement files once its manifest is dual-signed and current."""
    cache = Path(cache or CACHE)
    if verify is None:
        from supplement_manifest_review import reviewed_manifest
        verify = reviewed_manifest
    if verify(Path(root), aid) is None:
        raise ValueError('Supplement manifest lacks valid agent and parent receipts; cache kept')
    with (cache / '.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        folder = cache / aid
        freed = cache_usage(folder) if folder.exists() else 0
        if folder.exists():
            shutil.rmtree(folder)
    return {'article_id': aid, 'freed_bytes': freed, 'cache_bytes_after': cache_usage(cache)}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('command', choices=['acquire', 'peek', 'status', 'cleanup', 'extract', 'extract-text'])
    parser.add_argument('root', type=Path, nargs='?', default=Path(__file__).resolve().parent)
    parser.add_argument('--article'); parser.add_argument('--force', action='store_true')
    parser.add_argument('--supplement')
    args = parser.parse_args()
    if args.command == 'status':
        result = {'cache': str(CACHE), 'cache_bytes': cache_usage(), 'cache_limit_bytes': MAX_CACHE_BYTES,
                  'per_file_limit_bytes': MAX_FILE_BYTES,
                  'articles': sorted(p.name for p in CACHE.iterdir() if p.is_dir()) if CACHE.exists() else []}
    elif not args.article:
        parser.error('--article is required')
    elif args.command == 'acquire':
        result = acquire(args.root, args.article, force=args.force)
        result = {**result, 'files': [{k: f.get(k) for k in ('supplement_id', 'filename', 'bytes', 'download_status',
                  'download_reason', 'detected_type', 'inspection_status', 'path', 'sample_path', 'errors')}
                  for f in result['files']]}
    elif args.command == 'extract':
        if not args.supplement:
            parser.error('--supplement is required')
        result = extract_archive(args.root, args.article, args.supplement)
    elif args.command == 'extract-text':
        if not args.supplement:
            parser.error('--supplement is required')
        result = extract_text_documents(args.root, args.article, args.supplement)
    elif args.command == 'peek':
        result = repeek(args.root, args.article, force=args.force)
    else:
        result = cleanup(args.root, args.article)
    print(json.dumps(result, ensure_ascii=False, indent=2))
