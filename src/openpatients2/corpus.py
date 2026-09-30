"""Streaming PMC discovery and bounded per-article acquisition (current S3 layout)."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
import random
import statistics

import httpx

from .articles import parse_article, license_decision
from .data import write_json, read_jsonl
from .literature_client import LiteratureClient, LiteratureConfig, RateLimiter
from .pmc_media import cloud_url, metadata_flag

ESEARCH = 'https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi'
LICENSE_QUERY = '(cc0 license[filter] OR cc by license[filter] OR cc by-nc license[filter] OR cc by-nc-sa license[filter] OR cc by-sa license[filter]) NOT pmc embargo[filter]'


async def search_candidates(query: str, limit: int = 100, offset: int = 0, *, http=None) -> dict:
    if not 1 <= limit <= 1000 or not 0 <= offset or offset + limit > 10000:
        raise ValueError('ESearch bounds: 1..1000 per request, at most 10000 per partition. Split queries or use inventory.')
    owns = http is None
    http = http or httpx.AsyncClient(timeout=45)
    try:
        r = await http.get(ESEARCH, params={'db':'pmc','term':f'({query}) AND ({LICENSE_QUERY})',
            'retmode':'json','retmax':limit,'retstart':offset,'tool':'openpatients2'})
        r.raise_for_status(); d = r.json()['esearchresult']
        if any(d.get('errorlist', {}).values()) or d.get('ERROR'):
            raise ValueError('NCBI rejected search: '+str(d.get('errorlist') or d.get('ERROR')))
        return {'query':query,'license_query':LICENSE_QUERY, 'query_translation':d.get('querytranslation'),
                'total':int(d['count']), 'offset':offset, 'pmcids':['PMC'+x for x in d['idlist']],
                'census_complete':int(d['count']) <= len(d['idlist']) and offset == 0}
    finally:
        if owns:
            await http.aclose()


async def fetch_article(client: LiteratureClient, pmcid: str, version: int | None = None, *,
                        source_views: tuple[str, ...] = (), max_asset_bytes: int = 12_000_000) -> dict:
    """Select explicitly; never call the largest numeric version 'latest'."""
    if set(source_views) - {'pmc_text', 'pdf_firecrawl'}:
        raise ValueError('Unknown requested source view')
    objects = await client._version_objects(pmcid)
    candidates, rejected = [], []
    for v, url in objects:
        if version is not None and v != version:
            continue
        raw, retrieval = await client._text(url, 'metadata')
        meta = json.loads(raw)
        decision = license_decision(meta, [])
        if not decision['allowed'] or not meta.get('xml_url'):
            rejected.append({'version':v,'reason':decision['reason']}); continue
        candidates.append((v, meta, retrieval))
    if not candidates:
        return {'pmcid':pmcid, 'status':'ineligible_or_unavailable', 'rejected':rejected}
    # Prefer an unambiguous published article over a manuscript. If several
    # published versions exist require an explicit version instead of guessing.
    published = [x for x in candidates if metadata_flag(x[1].get('is_manuscript')) is False]
    choices = published or candidates
    if len(choices) != 1:
        return {'pmcid':pmcid,'status':'version_selection_required','versions':[x[0] for x in choices]}
    v, meta, meta_info = choices[0]
    url = cloud_url(meta['xml_url'])
    from urllib.parse import urlparse
    if urlparse(url).path != f'/{pmcid}.{v}/{pmcid}.{v}.xml':
        raise ValueError('XML URL does not match article/version')
    xml, info = await client._text(url, 'xml')
    article = parse_article(xml, meta, {'metadata':meta_info,'xml':info})
    article['status'] = 'eligible' if article['license']['allowed'] else 'license_rejected'
    if article['status'] == 'eligible':
        from .source_views import attach_source_view, SourceLicenseError
        for kind in dict.fromkeys(source_views):
            try:
                article = await attach_source_view(client, article, kind, max_bytes=max_asset_bytes)
            except SourceLicenseError as e:
                article['license'] = {**article['license'], 'allowed':False,
                                      'reason':'conflicting_article_representation_license', 'notice':str(e)}
                article['status'] = 'license_rejected'
                break
            except (ValueError, RuntimeError, httpx.HTTPError) as e:
                # Preserve the canonical article, and record the failed optional
                # acquisition. Extraction selection will explicitly fall back.
                article.setdefault('source_view_failures', []).append({'kind':kind, 'error':str(e)})
    return article


async def acquire(pmcids: list[str], output: str, max_articles: int = 100,
                  max_total_bytes: int = 100_000_000, version: int | None = None, *,
                  source_views: tuple[str, ...] = (), max_asset_bytes: int = 12_000_000) -> dict:
    if max_articles < 1 or max_total_bytes < 1 or max_asset_bytes < 1:
        raise ValueError('Article count and output byte caps must be positive')
    out = Path(output)
    if out.exists():
        raise ValueError('Use a new article output file')
    out.parent.mkdir(parents=True, exist_ok=True)
    client = LiteratureClient(LiteratureConfig(cache=str(out.with_suffix('.cache.sqlite')),max_xml_bytes=5_000_000))
    counts, size = {}, 0
    temp = out.with_suffix(out.suffix+'.tmp')
    try:
        with temp.open('w') as f:
            for pmcid in list(dict.fromkeys(pmcids))[:max_articles]:
                try:
                    row = await fetch_article(client, pmcid, version, source_views=source_views,
                                              max_asset_bytes=max_asset_bytes)
                except (ValueError, httpx.HTTPError, RuntimeError) as e:
                    row = {'pmcid':pmcid,'status':'fetch_failed','error':str(e)}
                line = json.dumps(row, ensure_ascii=False)+'\n'; size += len(line.encode())
                if size > max_total_bytes:
                    raise ValueError('Article output exceeds storage cap; partial output retained as .tmp')
                f.write(line); f.flush()
                counts[row['status']] = counts.get(row['status'],0)+1
        temp.replace(out)
        report = {'counts':counts,'output_bytes':size,'network':dict(client.stats)}
        write_json(out.with_suffix('.report.json'), report)
        return report
    finally:
        await client.close()


def length_report(path: str) -> dict:
    values = [r['lengths']['words'] for r in read_jsonl(path) if r.get('status') == 'eligible']
    def quantile(p):
        if not values: return None
        v=sorted(values); i=(len(v)-1)*p; a=int(i); b=min(a+1,len(v)-1)
        return v[a]+(v[b]-v[a])*(i-a)
    return {'n':len(values),'mean_words':statistics.mean(values) if values else None,
        'median_words':statistics.median(values) if values else None,'p95_words':quantile(.95),
        'min_words':min(values) if values else None,'max_words':max(values) if values else None,
        'quantile_method':'linear interpolation (n-1)*p',
        'population':'Only eligible articles in the supplied file. Purposive pilots do not estimate the PMC population.'}
