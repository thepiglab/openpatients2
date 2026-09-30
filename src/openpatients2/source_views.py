"""Version-bound model inputs alongside canonical JATS provenance.

Official TXT is an alternative clinical reading surface. Roster, figure and
citation-link decisions still use JATS. PDF extraction is native-only and keeps
an explicit review queue; no OCR engines, weights or page images are downloaded.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import importlib.metadata
from pathlib import Path
import re
import tempfile
from urllib.parse import parse_qs, urlparse

from .articles import license_decision, recheck_license
from .literature_client import utc_now
from .pmc_media import cloud_url
from .provenance import json_digest

VIEW_VERSION = 'openpatients2.source-view/1'


class SourceLicenseError(ValueError):
    """A second article representation contradicts the acquisition license."""


def _hash(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _bound(article: dict, kind: str, data: bytes, segments: list, retrieval: dict,
           *, review_queue=None, exclusions=None, parser=None) -> dict:
    result = {'format':VIEW_VERSION, 'kind':kind, 'article_id':article['article_id'],
              'canonical_xml_sha256':article['xml_sha256'], 'asset_sha256':_hash(data),
              'segments':segments, 'retrieval':retrieval, 'parser':parser,
              'review_queue':review_queue or [], 'exclusions':exclusions or [],
              'clinical_accuracy':'unreviewed', 'pixels_inspected':False}
    result['view_digest'] = json_digest(result)
    return result


def validate_view(article: dict, view: dict) -> dict:
    if not recheck_license(article['license'])['allowed']:
        raise ValueError('Article license no longer allows extraction')
    if (view.get('format') != VIEW_VERSION or view.get('article_id') != article['article_id']
            or view.get('canonical_xml_sha256') != article['xml_sha256']):
        raise ValueError('Source view belongs to another article/version')
    if view.get('view_digest') != json_digest({k:v for k,v in view.items() if k != 'view_digest'}):
        raise ValueError('Source view integrity failure')
    segments = view['segments']
    if not segments or len({s['segment_id'] for s in segments}) != len(segments):
        raise ValueError('Missing or duplicate source segments')
    return view


def official_text_view(article: dict, data: bytes, retrieval: dict | None = None) -> dict:
    text = data.decode('utf-8-sig')
    marker = re.search(r'\x9f=+\x9f', text)
    if not marker:
        raise ValueError('Unrecognized official PMC TXT body boundary')
    header = text[:marker.start()]
    ids = re.findall(r'^PMCID:\s*(PMC\d+)\s*$', header, re.M)
    versions = re.findall(r'^Article version:\s*(\d+)\s*$', header, re.M)
    if ids != [article['pmcid']] or versions != [str(article['version'])]:
        raise ValueError('PMC TXT identity/version does not match canonical JATS')
    statements = [{'type':'license', 'text':m.group(1)} for m in
                  re.finditer(r'^License(?: URL)?:\s*(.+)$', header, re.M)]
    rights = license_decision(article['metadata'], article['license'].get('statements', [])+statements)
    if not rights['allowed'] or not recheck_license(article['license'])['allowed']:
        raise SourceLicenseError('PMC TXT/JATS license rejected or inconsistent')
    # Remove only bibliography lines corroborated by JATS. A heading alone is
    # not permission to discard late figures, tables or supplementary captions.
    def plain(s):return ''.join(c.casefold() for c in s if c.isalnum())
    references = [plain(r['citation_text']) for r in article.get('references', [])]
    reference_heading = re.search(r'(?im)^references[ \t\r]*$', text[marker.end():])
    ref_start = marker.end()+reference_heading.end() if reference_heading else len(text)
    excluded = [{'start':0, 'end':marker.end(), 'reason':'official_front_matter'}]
    kept = []
    # Blank-line paragraphs retain all internal tabs/spaces and row relationships.
    for block in re.finditer(r'\S[\s\S]*?(?=\r?\n[ \t]*\r?\n|\Z)', text[marker.end():]):
        start, end = marker.end()+block.start(), marker.end()+block.end()
        if start < ref_start:
            kept.append((start, end))
            continue
        cursor = start
        for line in text[start:end].splitlines(keepends=True):
            normalized = plain(line)
            if len(normalized) >= 40 and normalized in references:
                excluded.append({'start':cursor, 'end':cursor+len(line), 'reason':'JATS_corresponding_reference'})
            elif line.strip():
                kept.append((cursor, cursor+len(line.rstrip('\r\n'))))
            cursor += len(line)
    segments = [{'segment_id':f'txt-{i+1:05d}', 'heading':'PMC text', 'kind':'official_text',
                 'text':text[start:end], 'asset_span':[start,end], 'offset_unit':'decoded Unicode code points'}
                for i,(start,end) in enumerate(kept)]
    if not segments:
        raise ValueError('PMC TXT contains no article body')
    return _bound(article, 'pmc_text', data, segments, retrieval or {}, exclusions=excluded,
                  parser={'name':'official_pmc_text', 'version':1, 'whitespace':'preserved',
                          'bibliography_policy':'remove only JATS-corresponding lines; retain unmatched material'})


def firecrawl_pdf_view(article: dict, data: bytes, retrieval: dict | None = None,
                       *, max_bytes: int = 12_000_000, max_pages: int = 100) -> dict:
    if not recheck_license(article['license'])['allowed']:
        raise ValueError('Article license does not allow PDF extraction')
    if len(data) > max_bytes or not data.startswith(b'%PDF-'):
        raise ValueError('Invalid PDF or PDF exceeds byte cap')
    try:
        import pdf_inspector
    except ImportError as e:
        raise ValueError('Install the optional PDF extra: uv sync --extra pdf') from e
    # Temporary source is removed even on native parser failure; no page rendering.
    with tempfile.TemporaryDirectory(prefix='op2-pdf-') as directory:
        path = Path(directory)/'article.pdf'
        path.write_bytes(data)
        detected = pdf_inspector.detect_pdf(str(path))
        if detected.page_count > max_pages:
            raise ValueError('PDF exceeds page cap')
        result = pdf_inspector.extract_pages_markdown(str(path))
    queue, segments = [], []
    for page in result.pages:
        number = page.page+1
        segment = {'segment_id':f'pdf-page-{number}', 'heading':f'PDF page {number}',
                   'kind':'pdf_page', 'page':number, 'region':None, 'text':page.markdown,
                   'needs_ocr':page.needs_ocr, 'ocr_reason':page.ocr_reason}
        segments.append(segment)
        reasons = []
        if page.needs_ocr or not page.markdown.strip():reasons.append('native_text_missing_or_OCR_requested')
        if number in result.pages_with_tables:reasons.append('table_layout_requires_review')
        if number in result.pages_with_columns:reasons.append('column_reading_order_requires_review')
        if re.search(r'/[a-z]+\.tnum|\(cid:\d+\)|\ufffd', page.markdown):reasons.append('possible_font_encoding_loss')
        # Native flags missed merged table/prose in the pilot. Every PDF retains
        # a layout review requirement, even if no native OCR flag was raised.
        queue.append({'page':number, 'segment_id':segment['segment_id'],
                      'reasons':reasons or ['PDF_layout_unreviewed'], 'status':'unresolved',
                      'suggested_action':'review original page; extract reviewed regions or use approved vision model'})
    if not any(s['text'].strip() for s in segments):
        raise ValueError('No native PDF text; requires an approved OCR/vision stage')
    return _bound(article, 'pdf_firecrawl', data, segments, retrieval or {}, review_queue=queue,
                  parser={'name':'pdf-inspector', 'version':importlib.metadata.version('pdf-inspector'),
                          'function':'extract_pages_markdown', 'ocr_used':False,
                          'is_complex':result.is_complex, 'bibliography':'retained'})


async def download_asset(client, article: dict, kind: str, max_bytes: int) -> tuple[bytes, dict]:
    """Only a version-matched official manifest URL; streaming cap before storage."""
    if kind not in {'pmc_text', 'pdf_firecrawl'} or max_bytes < 1:
        raise ValueError('Unknown source asset or invalid byte cap')
    if client.config.offline:
        raise ValueError('Network disabled in offline mode')
    if not recheck_license(article['license'])['allowed']:
        raise ValueError('License gate rejected asset download')
    extension = 'txt' if kind == 'pmc_text' else 'pdf'
    url = cloud_url(article['metadata'].get('text_url' if extension == 'txt' else 'pdf_url') or '')
    parsed = urlparse(url)
    if parsed.path != f"/{article['article_id']}/{article['article_id']}.{extension}":
        raise ValueError('Source asset URL does not match canonical article/version')
    async with client.slots:
        await client.cloud_rate.wait()
        client.stats[kind+'_requests'] += 1
        async with client.http.stream('GET', url, follow_redirects=False, timeout=client.config.timeout_seconds) as response:
            response.raise_for_status()
            if response.status_code != 200:
                raise ValueError('Source download must return a complete HTTP 200 body')
            content_type = response.headers.get('content-type', '').lower()
            if 'html' in content_type:
                raise ValueError('HTML is not an official source asset')
            if int(response.headers.get('content-length', 0)) > max_bytes:
                raise ValueError('Source asset exceeds byte cap')
            parts, size = [], 0
            async for part in response.aiter_bytes():
                size += len(part)
                client.stats['source_asset_bytes_received'] += len(part)
                if size > max_bytes:raise ValueError('Source asset exceeds byte cap')
                parts.append(part)
            data = b''.join(parts)
    expected = parse_qs(parsed.query).get('md5', [])
    if expected and (len(expected) != 1 or hashlib.md5(data).hexdigest() != expected[0].lower()):
        raise ValueError('Source asset manifest MD5 mismatch')
    return data, {'url':url, 'retrieved_at':utc_now(), 'sha256':_hash(data), 'bytes':len(data),
                  'manifest_md5_verified':bool(expected)}


async def attach_source_view(client, article: dict, kind: str, *, max_bytes=12_000_000) -> dict:
    data, retrieval = await download_asset(client, article, kind, max_bytes)
    if kind == 'pmc_text':
        view = official_text_view(article, data, retrieval)
    else:
        import asyncio
        view = await asyncio.to_thread(firecrawl_pdf_view, article, data, retrieval, max_bytes=max_bytes)
    return {**article, 'source_views':{**article.get('source_views', {}), kind:view}}


def choose_view(article: dict, preferred: str, *, allow_pdf_review=False) -> tuple[dict | None, dict]:
    if preferred not in {'jats', 'pmc_text', 'pdf_firecrawl'}:
        raise ValueError('Unknown clinical source view')
    decision = {'requested':preferred, 'selected':'jats', 'reason':'canonical_source'}
    if preferred == 'jats':return None, decision
    view = article.get('source_views', {}).get(preferred)
    if view is None:
        return None, {**decision, 'reason':'requested_view_unavailable',
                      'acquisition_failures':article.get('source_view_failures', [])}
    validate_view(article, view)
    if view['kind'] != preferred:
        raise ValueError('Source view kind mismatch')
    if view['review_queue'] and not allow_pdf_review:
        return None, {**decision, 'reason':'PDF_layout_review_required', 'review_queue':deepcopy(view['review_queue'])}
    return view, {**decision, 'selected':preferred, 'reason':'explicit_source_selection',
                  'view_digest':view['view_digest'], 'review_queue':deepcopy(view['review_queue'])}
