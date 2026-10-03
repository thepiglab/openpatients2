"""Audited citation formatting recovery. This never repairs clinical content.

Only a cited, known segment can supply a replacement. No fuzzy matching, missing
quote inference, cross-segment concatenation, value/time/unit changes or OCR fixes.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import re

VERSION = 'citation-formatting/1'
SPAN_VERSION = 'unique-exact-source-span/1'


def resolve_timeline_spans(candidate: dict, source_id: str, segments: list[dict]) -> tuple[dict, dict]:
    """Derive provenance only for uniquely located, already exact evidence quotes.

    ``source_id`` and ``segments`` come from the canonical article, never the model.
    Missing provenance is filled; conflicting declared namespaces/digests are held
    for review. Incorrect offsets are replaced, including when they happen to point
    to another occurrence. Repeated (also overlapping) quotes are always refused.
    Clinical descriptions, time expressions, graph edges and patient IDs are never
    edited. Run the ordinary timeline/schema/attribution gates on the result.
    """
    if not isinstance(source_id, str) or not source_id:
        raise ValueError('Canonical source ID is required')
    lookup = {}
    for segment in segments:
        sid, text = segment.get('segment_id'), segment.get('text')
        if not isinstance(sid, str) or not sid or not isinstance(text, str) or sid in lookup:
            raise ValueError('Canonical segments require unique IDs and exact text')
        lookup[sid] = text
    value = deepcopy(candidate)
    audit = {'policy': SPAN_VERSION, 'source_id': source_id,
             'offset_unit': 'Unicode code points; end exclusive',
             'resolved': [], 'recoveries': [], 'unresolved': [],
             'clinical_content_changed': False, 'clinical_entailment_verified': False}

    def resolve(span, path):
        raw = deepcopy(span)
        sid, quote = span.get('segment_id'), span.get('quote')
        text = lookup.get(sid) if isinstance(sid, str) else None
        code = None
        if text is None:
            code = 'unknown_source_segment'
        elif not isinstance(quote, str) or not quote:
            code = 'missing_exact_quote'
        elif span.get('source_id') not in (None, source_id):
            code = 'conflicting_source_id'
        else:
            digest = hashlib.sha256(text.encode()).hexdigest()
            if span.get('segment_sha256') not in (None, digest):
                code = 'conflicting_source_digest'
            else:
                # Lookahead counts overlapping exact occurrences too.
                positions = [m.start() for m in re.finditer('(?=' + re.escape(quote) + ')', text)]
                if not positions:
                    code = 'nonliteral_quote'
                elif len(positions) != 1:
                    code = 'ambiguous_exact_quote'
                else:
                    start = positions[0]
                    derived = {'source_id': source_id, 'segment_sha256': digest,
                               'start': start, 'end': start + len(quote)}
                    changes = {key: {'original': raw.get(key), 'replacement': replacement}
                               for key, replacement in derived.items()
                               if type(raw.get(key)) is not type(replacement) or raw.get(key) != replacement}
                    span.update(derived)
                    entry = {'path': path, 'segment_id': sid, 'raw': raw,
                             'source_span': [start, start + len(quote)], 'source_sha256': digest,
                             'quote_sha256': hashlib.sha256(quote.encode()).hexdigest(),
                             'changes': changes}
                    audit['resolved'].append(entry)
                    if changes:
                        audit['recoveries'].append(deepcopy(entry))
        if code:
            audit['unresolved'].append({'path': path, 'code': code, 'raw': raw})

    def visit(obj, path=''):
        if isinstance(obj, dict):
            for key, child in obj.items():
                pointer = path + '/' + key.replace('~', '~0').replace('/', '~1')
                if key in {'evidence', 'attribution_evidence'} and isinstance(child, list):
                    for index, span in enumerate(child):
                        if isinstance(span, dict):
                            resolve(span, pointer + '/' + str(index))
                else:
                    visit(child, pointer)
        elif isinstance(obj, list):
            for index, child in enumerate(obj):
                visit(child, path + '/' + str(index))
    visit(value)
    return value, audit


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
