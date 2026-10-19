"""Best-effort article-boundary counters; unavailable metrics never become zero."""
import math
import re
import time
import httpx

COUNTERS = ('vllm:spec_decode_num_draft_tokens_total', 'vllm:spec_decode_num_accepted_tokens_total')


def parse(text):
    result = {}
    for line in text.splitlines():
        match = re.fullmatch(r'(vllm:spec_decode_num_(?:draft|accepted)_tokens_total)(?:\{[^\n]*\})?\s+([^\s]+)(?:\s+\d+)?', line)
        if match:
            value = float(match[2])
            if not math.isfinite(value) or value < 0:
                raise ValueError('Invalid speculative counter')
            result[match[1]] = result.get(match[1], 0) + value
    return result


async def snapshot(endpoint):
    try:
        url = endpoint.rstrip('/').removesuffix('/v1') + '/metrics'
        async with httpx.AsyncClient(timeout=5) as client:
            response = await client.get(url)
            response.raise_for_status()
        return {'time_unix': time.time(), 'counters': parse(response.text)}
    except (httpx.HTTPError, ValueError) as exc:
        return {'time_unix': time.time(), 'counters': {}, 'error_type': type(exc).__name__}


def delta(before, after):
    b, a = before.get('counters', {}), after.get('counters', {})
    available = all(k in b and k in a and a[k] >= b[k] for k in COUNTERS)
    draft = a[COUNTERS[0]] - b[COUNTERS[0]] if available else None
    accepted = a[COUNTERS[1]] - b[COUNTERS[1]] if available else None
    valid = available and accepted <= draft
    return {'available': valid, 'drafted_tokens': draft if valid else None,
        'accepted_tokens': accepted if valid else None,
        'acceptance_fraction': accepted / draft if valid and draft else None,
        'before': before, 'after': after,
        'basis': 'server counter difference across one article; no synthetic startup probes; reset/missing counters unavailable'}


def summarize(receipts):
    rows = list(receipts)
    measured = [r['speculation'] for r in rows if r.get('speculation', {}).get('available')]
    complete = bool(rows) and len(measured) == len(rows)
    draft = sum(r['drafted_tokens'] for r in measured) if complete else None
    accepted = sum(r['accepted_tokens'] for r in measured) if complete else None
    return {'articles_measured': len(measured), 'articles_expected': len(rows),
            'drafted_tokens': draft, 'accepted_tokens': accepted,
            'acceptance_fraction': accepted / draft if draft else None,
            'basis': 'sum accepted / sum drafted across all completed article boundaries; missing coverage stays unavailable'}
