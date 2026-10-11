"""Read-only scan for free text with dropped inter-word spaces ("aftersevenmonths") in dense annotations.

    PYTHONPATH=src:benchmarks/dense500 .venv/bin/python benchmarks/dense500/scan_glued_text.py \
        [--folder annotations] [--article PMC...] [--out findings.json]

Every string leaf of an annotation is classified by its path:

* skip      quotes, segment/fact/event/patient/figure ids, hashes (never scanned)
* literal   time.text / events[].time_text: must stay literal in the evidence (reported, never repaired)
* enum      categorical fields (reported, never repaired)
* internal  facts[].notes, domain_coverage/source_coverage notes, annotation_provenance (not exported)
* exported  every other free-text field (names, text values, clinical statements, event descriptions,
            summaries, limitations, ambiguities, figure captions/attribution notes ...)

Detection and segmentation: openpatients2.glued_text (shared with the GLUED-TEXT validator warning).
The article's source text and the annotation's own spaced words supply article-specific vocabulary.
Pieces that occur literally in the enclosing item's evidence quotes are never flagged.
"""
from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path

from openpatients2.glued_text import find_glued

ROOT = Path(__file__).resolve().parent
SKIP_KEYS = frozenset('''quote segment_id segment_ids fact_id fact_ids event_id from_event_id to_event_id patient_id
patient_ids figure_id alternate_representation_of article_id schema_version text_sha256 xml_sha256 panel tumor_ref
date_iso annotator_model'''.split())
ENUM_KEYS = frozenset('''certainty collection scoring task status disposition kind assertion flag comparator relation action
action_status plan_type role temporality verification_status clinical_status subject species scope visual_status
occurrence attribute domain case_kind laterality severity urgency unit dose_unit result_absent_reason
subject_state origin intent reaction_type category'''.split())
LITERAL_PATHS = ('fields/time/text', 'events/time_text')
INTERNAL_PREFIXES = ('annotation_provenance', 'source_coverage')


def _quotes(obj):
    ev = obj.get('evidence') if isinstance(obj, dict) else None
    out = [e.get('quote', '') for e in ev or [] if isinstance(e, dict)]
    for key in ('caption_evidence', 'identity_evidence'):
        out += [e.get('quote', '') for e in obj.get(key) or [] if isinstance(e, dict)] if isinstance(obj, dict) else []
    return '\n'.join(out)


def classify(keys):
    """Category for a string leaf at key path `keys` (list indices removed)."""
    leaf = keys[-1]
    joined = '/'.join(keys)
    if leaf in SKIP_KEYS or 'evidence' in keys:
        return 'skip'
    if joined.endswith(LITERAL_PATHS):
        return 'literal'
    if keys[0] in INTERNAL_PREFIXES or 'domain_coverage' in keys or (leaf == 'notes' and 'facts' in keys):
        return 'internal'
    if leaf in ENUM_KEYS:
        return 'enum'
    return 'exported'


def leaves(obj, pointer='', keys=(), quotes=''):
    """(json_pointer, key_path, value, enclosing_quotes) for every string leaf."""
    if isinstance(obj, dict):
        q = _quotes(obj) or quotes
        for k, v in obj.items():
            yield from leaves(v, f'{pointer}/{k}', keys + (k,), q)
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            yield from leaves(v, f'{pointer}/{i}', keys, quotes)
    elif isinstance(obj, str):
        yield pointer, keys, obj, quotes


def owner(annotation, pointer):
    """fact_id / event_id / patient_id / figure_id that owns a pointer (for the report)."""
    node, ids = annotation, []
    for part in pointer.strip('/').split('/'):
        node = node[int(part)] if isinstance(node, list) else node[part]
        if isinstance(node, dict):
            for key in ('fact_id', 'event_id', 'figure_id', 'patient_id'):
                if isinstance(node.get(key), str):
                    ids.append(node[key])
                    break
    return ids[-1] if ids else None


def source_text(source):
    return '\n'.join([source.get('title') or '', source.get('text') or '']
                     + [f.get('caption') or '' for f in source.get('figures') or []])


def scan_annotation(annotation, source):
    text = source_text(source)
    rows = list(leaves(annotation))
    extra = '\n'.join(v for _, keys, v, _ in rows if classify(keys) in ('exported', 'internal'))
    findings = []
    for pointer, keys, value, quotes in rows:
        category = classify(keys)
        if category == 'skip':
            continue
        for g in find_glued(value, text, quotes=quotes, extra_text=extra):
            i = value.find(g.token)
            findings.append({'article_id': annotation.get('article_id'), 'pointer': pointer,
                             'field': '/'.join(keys), 'category': category, 'owner': owner(annotation, pointer),
                             'token': g.token, 'segmentation': g.segmentation, 'confidence': g.confidence,
                             'reasons': list(g.reasons), 'margin': g.margin,
                             'context': value[max(0, i - 60): i + len(g.token) + 60]})
    return findings


def summarize(findings, scanned):
    def count(key):
        return dict(Counter(f[key] for f in findings).most_common())
    exported = [f for f in findings if f['category'] == 'exported']
    return {'articles_scanned': scanned, 'tokens': len(findings),
            'articles_affected': len({f['article_id'] for f in findings}),
            'articles_with_exported_tokens': len({f['article_id'] for f in exported}),
            'by_category': count('category'), 'by_confidence': count('confidence'),
            'by_category_confidence': dict(Counter(f"{f['category']}:{f['confidence']}" for f in findings).most_common()),
            'by_field': count('field'), 'by_article': count('article_id')}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--root', type=Path, default=ROOT)
    ap.add_argument('--folder', default='annotations')
    ap.add_argument('--article')
    ap.add_argument('--out', type=Path, help='write {summary, findings} JSON here')
    args = ap.parse_args(argv)
    findings, scanned = [], 0
    for path in sorted((args.root / args.folder).glob((args.article or '*') + '.json')):
        annotation = json.loads(path.read_text())
        source = json.loads((args.root / 'sources' / path.name).read_text())
        findings += scan_annotation(annotation, source)
        scanned += 1
    summary = summarize(findings, scanned)
    if args.out:
        args.out.write_text(json.dumps({'summary': summary, 'findings': findings}, ensure_ascii=False, indent=1) + '\n')
    print(json.dumps({k: v for k, v in summary.items() if k != 'by_field'}, ensure_ascii=False, indent=1))


if __name__ == '__main__':
    main()
