"""Opt-in, bounded CPU media preparation and audited citation formatting recovery."""
from __future__ import annotations

import base64
from collections import Counter
from copy import deepcopy
import gzip
import hashlib
import json
from pathlib import Path
import re
from urllib.parse import parse_qs, unquote, urlparse

import httpx

from .data import write_json
from .evidence_recovery import recover_citations
from .license_policy import recheck_license
from .pmc_media import cloud_url


def _mime(data):
    if data.startswith(b'\x89PNG\r\n\x1a\n'): return 'image/png'
    if data.startswith(b'\xff\xd8\xff'): return 'image/jpeg'
    if data.startswith((b'GIF87a', b'GIF89a')): return 'image/gif'
    if data.startswith(b'RIFF') and data[8:12] == b'WEBP': return 'image/webp'
    raise ValueError('Unsupported image magic; separate conversion required')


def _articles(path):
    """Bound decompressed JSONL input too, including gzip selected samples."""
    opener = gzip.open if str(path).endswith('.gz') else open
    result, total, seen = [], 0, set()
    with opener(path, 'rb') as handle:
        while line := handle.readline(8_000_001):
            total += len(line)
            if len(line) > 8_000_000 or total > 64_000_000:
                raise ValueError('Selected sample exceeds JSONL byte cap')
            if not line.strip(): continue
            row = json.loads(line)
            article = row.get('article', row) if isinstance(row, dict) else None
            if not isinstance(article, dict) or not re.fullmatch(r'PMC\d+\.\d+', str(article.get('article_id', ''))):
                raise ValueError('Selected sample needs versioned PMC article objects')
            if article['article_id'] in seen or len(result) >= 128:
                raise ValueError('Duplicate article or selected sample exceeds 128 articles')
            seen.add(article['article_id']); result.append(article)
    return result


def _listed_url(article, figure):
    urls = figure.get('image_urls', [])
    if not urls: return None, 'missing_image'
    supported = [u for u in urls if urlparse(u).path.lower().endswith(('.png', '.jpg', '.jpeg', '.gif', '.webp'))]
    if not supported: return None, 'conversion_required'
    url = cloud_url(supported[0])
    if not unquote(urlparse(url).path).startswith('/' + article['article_id'] + '/'):
        raise ValueError('Image URL belongs to another article version')
    listed = [m for m in figure.get('media', []) if m.get('availability') == 'listed_in_pmc_metadata'
              and m.get('kind') == 'image' and cloud_url(m['url']) == url]
    if not listed: raise ValueError('Image URL is not explicitly listed in official PMC media metadata')
    md5s = parse_qs(urlparse(url).query).get('md5', [])
    expected = md5s[0] if len(md5s) == 1 else listed[0].get('md5')
    if len(md5s) > 1 or expected and not re.fullmatch('[a-fA-F0-9]{32}', expected):
        raise ValueError('Malformed PMC media MD5')
    if listed[0].get('md5') and md5s and listed[0]['md5'].lower() != expected.lower():
        raise ValueError('Conflicting PMC media MD5')
    return (url, expected), None


async def _fetch(http, url, limit, budget):
    data = bytearray()
    def append(chunk):
        budget['received'] += len(chunk)
        if len(data) + len(chunk) > limit or budget['received'] > budget['limit']:
            raise ValueError('Image/total response byte cap exceeded')
        data.extend(chunk)
    async with http.stream('GET', url, headers={'Accept-Encoding': 'identity'}, follow_redirects=False) as response:
        response.raise_for_status()
        if response.headers.get('content-encoding', 'identity').lower() != 'identity':
            raise ValueError('Encoded HTTP response refused; image encoding must be bounded directly')
        length = int(response.headers.get('content-length', '0'))
        if length > limit or length > budget['limit'] - budget['received']:
            raise ValueError('Image/total response byte cap exceeded')
        if response.is_stream_consumed: append(response.content)  # Injected/mock response.
        else:
            async for chunk in response.aiter_raw(chunk_size=65536): append(chunk)
        declared = response.headers.get('content-type', '').split(';')[0].strip().lower()
    mime = _mime(data)
    if declared and declared not in {mime, 'application/octet-stream', 'binary/octet-stream'}:
        raise ValueError('Declared MIME disagrees with image magic')
    return bytes(data), mime


async def prepare_media(input_path, output_dir, max_figures=12, max_total_bytes=64_000_000,
                        max_image_bytes=8_000_000, http=None, priority_figures=None):
    """Select at most twelve figures round-robin; refuse existing media output.

    The total response budget includes bytes from failed integrity checks. HTTP
    compression is refused, so encoded and image-file limits cannot diverge.
    Skipped figures consume selection slots and remain visible in the manifest.
    """
    for value, cap in ((max_figures, 12), (max_total_bytes, 64_000_000), (max_image_bytes, 8_000_000)):
        if type(value) is not int or not 1 <= value <= cap: raise ValueError('Media limit outside pilot bounds')
    articles = _articles(input_path)
    selected = []
    known = {(a['article_id'], f['figure_key']): (a, f) for a in articles for f in a.get('figures', [])}
    priorities = [(row['article_id'], row['figure_id']) for row in priority_figures or []]
    if len(priorities) > max_figures or len(set(priorities)) != len(priorities) or set(priorities) - known.keys():
        raise ValueError('Priority figures must be unique canonical figures within the pixel cap')
    selected.extend(known[key] for key in priorities)
    selected_ids = set(priorities)
    index = 0
    while len(selected) < max_figures:
        added = False
        for article in articles:
            figures = article.get('figures', [])
            if index < len(figures):
                added = True
                key = article['article_id'], figures[index]['figure_key']
                if key in selected_ids: continue
                selected.append((article, figures[index])); added = True
                selected_ids.add(key)
                if len(selected) == max_figures: break
        if not added: break
        index += 1
    root = Path(output_dir); folder = root / 'vision-assets'
    if folder.exists() or folder.is_symlink(): raise ValueError('Existing vision-assets refused; use a fresh output')
    folder.mkdir(parents=True, exist_ok=False)
    manifest = {'figures': [], 'bytes': 0, 'response_bytes': 0, 'complete': False,
                'selection': 'reference-priority then round-robin' if priorities else 'round-robin figure index across selected articles',
                'priority_figures': [{'article_id': aid, 'figure_id': fid} for aid, fid in priorities],
                'limits': {'figures': max_figures, 'total_bytes': max_total_bytes, 'image_bytes': max_image_bytes}}
    budget = {'received': 0, 'limit': max_total_bytes}
    owns = http is None
    http = http or httpx.AsyncClient(timeout=40, follow_redirects=False)
    try:
        write_json(folder / 'manifest.json', manifest)
        for article, figure in selected:
            license = recheck_license(article.get('license', {}))
            row = {'article_id': article['article_id'], 'figure_id': figure['figure_key'],
                   'status': 'pending', 'source_license': license}
            manifest['figures'].append(row)
            try:
                if not license.get('allowed'): row['status'] = 'article_rights_review'
                elif (figure.get('rights_statements') or figure.get('reuse_status') == 'asset_rights_review'
                      or figure.get('fixture_asset_rights_review') == 'required_before_reuse'):
                    row['status'] = 'asset_rights_review'
                else:
                    asset, skip = _listed_url(article, figure)
                    if skip: row['status'] = skip
                    elif budget['received'] >= max_total_bytes: row['status'] = 'total_byte_cap_exhausted'
                    else:
                        url, expected = asset
                        data, mime = await _fetch(http, url, min(max_image_bytes, max_total_bytes-budget['received']), budget)
                        if expected and hashlib.md5(data).hexdigest() != expected.lower():
                            raise ValueError('Figure bytes differ from official PMC MD5')
                        digest = hashlib.sha256(data).hexdigest(); path = folder / (digest + '.image')
                        if not path.exists():
                            with path.open('xb') as handle: handle.write(data)
                            manifest['bytes'] += len(data)
                        row.update(status='ready', file=str(path.relative_to(root)), pixel_provenance={
                            'bytes': len(data), 'sha256': digest, 'mime_type': mime, 'url': url,
                            'persisted_pixels': True})
            except (httpx.HTTPError, ValueError, TypeError, KeyError) as exc:
                row.update(status='fetch_failed', error_type=type(exc).__name__, error=str(exc))
            finally:
                manifest['response_bytes'] = budget['received']
                write_json(folder / 'manifest.json', manifest)
        manifest.update(complete=True, status_counts=dict(Counter(row['status'] for row in manifest['figures'])))
        write_json(folder / 'manifest.json', manifest)
        return manifest
    finally:
        if owns: await http.aclose()


def load_asset(root, row):
    """Return a data URI only after local path, size, digest and MIME verification."""
    if row.get('status') != 'ready': raise ValueError('Asset is not ready')
    p = row['pixel_provenance']; digest = p['sha256']; size = p['bytes']
    if not re.fullmatch('[a-f0-9]{64}', digest) or type(size) is not int or not 1 <= size <= 8_000_000:
        raise ValueError('Invalid asset provenance')
    root = Path(root); folder = root / 'vision-assets'; path = root / row['file']
    if folder.is_symlink() or path.is_symlink() or row['file'] != 'vision-assets/' + digest + '.image':
        raise ValueError('Asset path escaped frozen vision-assets')
    if path.resolve().parent != folder.resolve() or path.stat().st_size != size:
        raise ValueError('Asset path/size changed')
    with path.open('rb') as handle: data = handle.read(size + 1)
    if len(data) != size or hashlib.sha256(data).hexdigest() != digest or _mime(data) != p['mime_type']:
        raise ValueError('Asset digest/MIME changed')
    return 'data:' + p['mime_type'] + ';base64,' + base64.b64encode(data).decode()


def repair_visual_citations(candidate, segments):
    """Recover only unique whitespace-equivalent contiguous quotes; no semantics."""
    lookup = {s['segment_id']: s for s in segments}
    if len(lookup) != len(segments): raise ValueError('Duplicate source segment IDs')
    value = deepcopy(candidate)
    audit = {'policy': 'pilot-visual-whitespace/1', 'raw': deepcopy(candidate), 'recoveries': [], 'unresolved': []}
    def visit(obj, path=''):
        if isinstance(obj, dict):
            sid, quote = obj.get('segment_id'), obj.get('quote')
            if isinstance(quote, str) and quote.strip() and isinstance(sid, str):
                text = lookup.get(sid, {}).get('text', '')
                if quote not in text:
                    pattern = r'\s+'.join(re.escape(word) for word in quote.split())
                    # Lookahead counts overlapping occurrences too.
                    matches = list(re.finditer('(?=(' + pattern + '))', text))
                    if len(matches) == 1:
                        recovered, _ = recover_citations({'segment_id': sid, 'quote': quote}, [lookup[sid]])
                        exact = recovered['quote']; start, end = matches[0].span(1)
                        if exact != text[start:end]: raise ValueError('Citation recovery disagrees with exact source span')
                        obj['quote'] = exact
                        audit['recoveries'].append({'path': path+'/quote', 'segment_id': sid, 'original': quote,
                            'replacement': exact, 'start': start, 'end': end, 'offset_unit': 'Unicode code points; end exclusive',
                            'source_sha256': hashlib.sha256(text.encode()).hexdigest(),
                            'quote_sha256': hashlib.sha256(exact.encode()).hexdigest()})
                    else:
                        audit['unresolved'].append({'path': path+'/quote', 'segment_id': sid,
                            'reason': 'ambiguous_contiguous_match' if matches else 'no_contiguous_whitespace_match'})
            for key, child in obj.items(): visit(child, path+'/'+key)
        elif isinstance(obj, list):
            for index, child in enumerate(obj): visit(child, path+'/'+str(index))
    visit(value)
    return value, audit
