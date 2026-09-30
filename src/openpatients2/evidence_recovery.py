"""Audited citation formatting recovery. This never repairs clinical content.

Only a cited, known segment can supply a replacement. No fuzzy matching, missing
quote inference, cross-segment concatenation, value/time/unit changes or OCR fixes.
"""
from __future__ import annotations

from copy import deepcopy
import re

VERSION = 'citation-formatting/1'


def packet_segments(record: dict) -> list[dict]:
    """Recover exact displayed blocks from a packet, without guessing boundaries."""
    spans = record.get('packet_spans') or record.get('original_row', {}).get('packet_spans', [])
    result = []
    for span in spans:
        start, end = span['start'], span['end']
        if not 0 <= start <= end <= len(record['text']):
            raise ValueError('Invalid patient packet source span')
        result.append({'segment_id':span['segment_id'], 'heading':span.get('heading', ''),
                       'text':record['text'][start:end], 'packet_start':start})
    return result


def recover_citations(candidate: dict, segments: list[dict]) -> tuple[dict, list[dict]]:
    value = deepcopy(candidate)
    audit = []
    lookup = {s['segment_id']:s for s in segments}
    if len(lookup) != len(segments):
        raise ValueError('Duplicate source segment IDs')
    labels = {label:s['segment_id'] for s in segments for label in
              (f"[{s['segment_id']}]", f"[{s['segment_id']}] {s.get('heading') or 'Article'}")}

    def visit(obj, path=''):
        if isinstance(obj, dict):
            key = 'segment_id' if 'segment_id' in obj else 'source_section'
            sid, quote = obj.get(key), obj.get('quote')
            if isinstance(sid, str) and isinstance(quote, str) and quote.strip():
                normalized = labels.get(sid, sid)
                segment = lookup.get(normalized)
                if segment:
                    text = segment['text']
                    replacement = quote if quote in text else None
                    start = text.find(quote) if replacement else -1
                    if replacement is None:
                        pattern = r'\s+'.join(re.escape(p) for p in quote.split())
                        matches = list(re.finditer(pattern, text))
                        # Ambiguous whitespace matches stay unresolved. Exact
                        # quotes already have multi-location reporting downstream.
                        if len(matches) == 1:
                            replacement, start = matches[0].group(), matches[0].start()
                    if replacement is not None:
                        for field, old, new in [(key, sid, normalized), ('quote', quote, replacement)]:
                            if old != new:
                                entry = {'path':path+'/'+field, 'original':old, 'replacement':new,
                                         'segment_id':normalized, 'source_span':[start, start+len(replacement)],
                                         'offset_unit':'Unicode code points; end exclusive', 'policy':VERSION}
                                if 'packet_start' in segment:
                                    entry['packet_span'] = [segment['packet_start']+x for x in entry['source_span']]
                                audit.append(entry)
                                obj[field] = new
            for key, child in obj.items():
                visit(child, path+'/'+key)
        elif isinstance(obj, list):
            for i, child in enumerate(obj):
                visit(child, path+'/'+str(i))
    visit(value)
    return value, audit


def segment_errors(value: dict, segments: list[dict]) -> list[str]:
    """Article packets require segment-local citations, not a quote elsewhere."""
    lookup = {s['segment_id']:s['text'] for s in segments}
    errors = []
    def visit(obj, path=''):
        if isinstance(obj, dict):
            if isinstance(obj.get('quote'), str) and 'source_section' in obj:
                text = lookup.get(obj['source_section']) if isinstance(obj['source_section'], str) else None
                if text is None or obj['quote'] not in text:
                    errors.append(path+': evidence must quote its displayed source segment ID')
            for k, v in obj.items():visit(v, path+'/'+k)
        elif isinstance(obj, list):
            for i, v in enumerate(obj):visit(v, path+'/'+str(i))
    visit(value)
    return errors
