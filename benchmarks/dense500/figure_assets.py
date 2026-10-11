"""Fetch one article's declared images into a bounded, non-release cache.

Downloading is not inspection. Each viewer pass and review is recorded separately.
"""
import argparse
import asyncio
import base64
import fcntl
import hashlib
import json
from pathlib import Path
from urllib.parse import urlparse

from openpatients2.vision import fetch_pixels
import store

CACHE = Path(__file__).resolve().parents[2] / 'data/.dense-figure-cache'
MAX_IMAGE_BYTES = 8_000_000
MAX_CACHE_BYTES = 1_000_000_000


async def acquire(root, aid):
    source = json.loads(store.path(root, 'sources', aid).read_text())
    CACHE.mkdir(parents=True, exist_ok=True)
    rows = []
    # Different annotators share a bounded cache. Reserve and write under one lock.
    with (CACHE / '.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        used = sum(p.stat().st_size for p in CACHE.glob('*/*') if p.is_file())
        for figure in source.get('figures', []):
            row = {'figure_id': figure['figure_key'], 'assets': [], 'errors': []}
            for url in dict.fromkeys(figure.get('image_urls', [])):
                try:
                    parsed = urlparse(url)
                    if parsed.scheme != 'https' or parsed.hostname not in {
                        'pmc-oa-opendata.s3.amazonaws.com', 'cdn.ncbi.nlm.nih.gov',
                        'pmc.ncbi.nlm.nih.gov'}:
                        raise ValueError('Only declared HTTPS PMC image hosts are accepted')
                    destination = CACHE / aid / (hashlib.sha256(url.encode()).hexdigest() + '.image')
                    if not destination.exists():
                        if MAX_CACHE_BYTES - used <= 0:
                            raise ValueError('Shared one-GB cache limit reached; review and remove completed assets first')
                        data_url, provenance = await fetch_pixels(url, max_bytes=min(MAX_IMAGE_BYTES, MAX_CACHE_BYTES-used))
                        data = base64.b64decode(data_url.split(',', 1)[1])
                        destination.parent.mkdir(exist_ok=True)
                        destination.write_bytes(data)
                        used += len(data)
                    data = destination.read_bytes()
                    # The source manifest checksum is checked when provided.
                    declared = next((m.get('md5') for m in figure.get('media', []) if m.get('url') == url), None)
                    if declared and hashlib.md5(data).hexdigest() != declared:
                        raise ValueError('Source manifest MD5 mismatch')
                    row['assets'].append({'url': url, 'sha256': hashlib.sha256(data).hexdigest(),
                        'bytes': len(data), 'path': str(destination.resolve()),
                        'source_license': source['license'], 'rights_statements': figure.get('rights_statements', []),
                        'reuse_status': figure.get('reuse_status'), 'inspection_status': 'not_inspected'})
                except Exception as exc:
                    row['errors'].append({'url': url, 'error': str(exc)})
            if not figure.get('image_urls'):
                row['errors'].append({'url': None, 'error': 'No resolved image URL in source metadata'})
            rows.append(row)
        report = {'article_id': aid, 'source_text_sha256': source['text_sha256'],
            'figures': rows, 'cache_bytes_after': used, 'cache_limit_bytes': MAX_CACHE_BYTES,
            'download_is_not_inspection': True}
        out = store.path(root, 'figure-assets', aid); out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(report, ensure_ascii=False, indent=2)+'\n')
    return report


if __name__ == '__main__':
    p=argparse.ArgumentParser();p.add_argument('root',type=Path);p.add_argument('--article',required=True)
    args=p.parse_args();print(json.dumps(asyncio.run(acquire(args.root,args.article)),ensure_ascii=False,indent=2))
