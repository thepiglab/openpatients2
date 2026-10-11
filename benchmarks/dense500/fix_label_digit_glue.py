"""Deterministic fixer for letter-digit glue in reviewer-written free text (GLUED-TEXT).

    # dry run (default): per-article diffs + receipts that would go stale; nothing is written
    PYTHONPATH=src:benchmarks/dense500 .venv/bin/python benchmarks/dense500/fix_label_digit_glue.py \
        [--root benchmarks/dense500] [--article ID] [--out report.json]
    # apply to ONE article (every listed receipt is then stale; reviewers re-sign)
    ... fix_label_digit_glue.py --apply --article ID

Only three whitelisted, purely typographic rewrites, applied outside quotation marks (printed text):
  1. label + number   (Case|Patient|Figure|Fig.|Table|Panel|Day|day|POD|Week|week|Month|month|Year|year|
                       Grade|grade|Stage|stage|Type|type|Cycle|cycle) immediately followed by digits -> "Case 1"
  2. number + unit    a number (not glued to a preceding letter/digit) immediately followed by one of
                       mm cm mL mg kg g h min days weeks months years                      -> "35 mm"
  3. punctuation      word,word / word;word -> "word, word" only when the spaced form is in the article source
Never touched: evidence/quotes, ids, enums, literal time text, URLs/filenames/identifiers, hashes, machine
blocks, and notation (HbA1c, VO2peak, IL6, T1/T2, 8Fr: none of them match the patterns).

Files (store kinds, see store.py): annotations (scan_glued_text categories exported/internal), visual-submissions,
figure-ehr-labels, figure-privacy-flags, supplement-manifests (reviewer free-text keys,
openpatients2.semantic_rules.SIDECAR_FREE_TEXT). When the visual submission changes, `visual_target_sha256`
in figure-ehr-labels and figure-privacy-flags is recomputed exactly as the tools do (json_digest of the
whole visual submission).
"""
from __future__ import annotations

import argparse
import copy
import json
from pathlib import Path
import re

from openpatients2.provenance import json_digest
from openpatients2.semantic_rules import QUOTED, SIDECAR_FREE_TEXT, SIDECAR_SKIP
import store

ROOT = Path(__file__).resolve().parent
LABEL_NUMBER = re.compile(r'\b(Case|Patient|Figure|Fig\.?|Table|Panel|Day|day|POD|Week|week|Month|month|Year|year|'
                          r'Grade|grade|Stage|stage|Type|type|Cycle|cycle)(\d+)')
NUMBER_UNIT = re.compile(r'(?<![A-Za-z0-9.])(\d+(?:\.\d+)?)(mm|cm|mL|mg|kg|g|h|min|days|weeks|months|years)\b')
PUNCT = re.compile(r'\b([A-Za-z]{2,})([,;])([A-Za-z]{2,})\b')
FILE_OR_ID = re.compile(r'\S*(?:://|@|_|\.(?:pdf|docx?|xlsx?|csv|tsv|txt|jpe?g|png|tiff?|gif|zip|mp4|mov|xml|json|html?)\b)\S*', re.I)
SIDECARS = (('visual', 'visual-submissions'), ('figure_ehr', 'figure-ehr-labels'), ('privacy', 'figure-privacy-flags'),
            ('supplement_manifest', 'supplement-manifests'))
RECEIPTS = {'annotations': ('reviews', 'schema-agent-reviews', 'schema-parent-reviews'),
            'identity': ('visual-agent-reviews', 'visual-parent-reviews', 'figure-ehr-agent-reviews',
                         'figure-ehr-parent-reviews', 'figure-privacy-agent-reviews', 'figure-privacy-parent-reviews',
                         'supplement-manifest-agent-reviews', 'supplement-manifest-parent-reviews',
                         'measurement-series-agent-reviews', 'measurement-series-parent-reviews'),
            'visual-submissions': ('visual-agent-reviews', 'visual-parent-reviews', 'figure-ehr-agent-reviews',
                                   'figure-ehr-parent-reviews', 'figure-privacy-agent-reviews', 'figure-privacy-parent-reviews'),
            'figure-ehr-labels': ('figure-ehr-agent-reviews', 'figure-ehr-parent-reviews'),
            'figure-privacy-flags': ('figure-privacy-agent-reviews', 'figure-privacy-parent-reviews'),
            'supplement-manifests': ('supplement-manifest-agent-reviews', 'supplement-manifest-parent-reviews')}


def fix_text(text, source_text=''):
    """(new text, [rule names]) with the whitelisted rewrites applied outside quotes, URLs and identifiers."""
    protected = [m.span() for m in QUOTED.finditer(text)] + [m.span() for m in FILE_OR_ID.finditer(text)]
    def outside(span):
        return not any(a <= span[0] < b or a < span[1] <= b for a, b in protected)
    edits = []   # (start, end, replacement, rule)
    for m in LABEL_NUMBER.finditer(text):
        if outside(m.span()):
            edits.append((m.start(), m.end(), f'{m.group(1)} {m.group(2)}', 'label_number'))
    for m in NUMBER_UNIT.finditer(text):
        if outside(m.span()):
            edits.append((m.start(), m.end(), f'{m.group(1)} {m.group(2)}', 'number_unit'))
    for m in PUNCT.finditer(text):
        spaced = f'{m.group(1)}{m.group(2)} {m.group(3)}'
        if outside(m.span()) and source_text and spaced in source_text:
            edits.append((m.start(), m.end(), spaced, 'punctuation'))
    edits.sort()
    out, last, rules = [], 0, []
    for start, end, repl, rule in edits:
        if start < last:
            continue     # overlapping match: keep the first
        out += [text[last:start], repl]; last = end; rules.append(rule)
    return ''.join(out) + text[last:], rules


def _annotation_strings(annotation):
    """(pointer, value) for exported/internal free text (scan_glued_text categories)."""
    from scan_glued_text import classify, leaves
    for pointer, keys, value, _ in leaves(annotation):
        if classify(keys) in ('exported', 'internal'):
            yield pointer, value


def _sidecar_strings(kind, doc):
    keys = SIDECAR_FREE_TEXT[kind]
    def walk(obj, pointer, take):
        if isinstance(obj, dict):
            for k, v in obj.items():
                if k not in SIDECAR_SKIP:
                    yield from walk(v, f'{pointer}/{k}', k in keys)
        elif isinstance(obj, list):
            for i, v in enumerate(obj):
                yield from walk(v, f'{pointer}/{i}', take)
        elif isinstance(obj, str) and take:
            yield pointer, obj
    yield from walk(doc, '', False)


def _set(doc, pointer, value):
    parts = pointer.strip('/').split('/')
    node = doc
    for p in parts[:-1]:
        node = node[int(p)] if isinstance(node, list) else node[p]
    last = parts[-1]
    if isinstance(node, list):
        node[int(last)] = value
    else:
        node[last] = value


def plan_document(doc, strings, source_text):
    """(new doc, [{pointer, rules, before, after}])."""
    new = copy.deepcopy(doc); changes = []
    for pointer, value in list(strings(doc)):
        after, rules = fix_text(value, source_text)
        if after != value:
            _set(new, pointer, after)
            changes.append({'pointer': pointer, 'rules': rules, 'before': value, 'after': after})
    return new, changes


def _json(path):
    return json.loads(path.read_text()) if path.exists() else None


def plan_article(root, aid):
    """{'files': {folder: (old, new, changes)}, 'stale_receipts': [...], 'stale_targets': [...]}."""
    from visual_review import identity_digest
    root = Path(root)
    source = json.loads(store.path(root, 'sources', aid).read_text())
    text = '\n'.join([source.get('title') or ''] + [s['text'] for s in source.get('segments', [])]
                     + [f.get('caption') or '' for f in source.get('figures') or []])
    files = {}
    annotation = _json(store.path(root, 'annotations', aid))
    if annotation:
        new, changes = plan_document(annotation, _annotation_strings, text)
        files['annotations'] = (annotation, new, changes)
    for kind, folder in SIDECARS:
        doc = _json(store.path(root, folder, aid))
        if doc and ('figures' in doc or kind == 'supplement_manifest'):
            new, changes = plan_document(doc, lambda d, kind=kind: _sidecar_strings(kind, d), text)
            files[folder] = (doc, new, changes)
    # The visual target digest bound inside figure-EHR labels / privacy flags follows the new visual submission.
    if 'visual-submissions' in files and files['visual-submissions'][2]:
        digest = json_digest(files['visual-submissions'][1])
        for folder in ('figure-ehr-labels', 'figure-privacy-flags'):
            if folder in files and files[folder][1].get('visual_target_sha256') != digest:
                old, new, changes = files[folder]
                changes.append({'pointer': '/visual_target_sha256', 'rules': ['visual_target_digest'],
                                'before': new.get('visual_target_sha256'), 'after': digest})
                new['visual_target_sha256'] = digest
    changed = [f for f, (_, _, c) in files.items() if c]
    reasons = set(changed)
    if annotation and 'annotations' in changed and identity_digest(annotation) != identity_digest(files['annotations'][1]):
        reasons.add('identity')
    paths = {store.path(root, folder, aid) for r in reasons for folder in RECEIPTS.get(r, ())}
    if 'identity' in reasons:     # supplement receipts bind the patient identity digest
        paths |= {store.receipt(root, 'supplement', role, aid, sid) for role in ('agent', 'parent')
                  for sid in store.supplement_ids(root, 'supplement', aid, role=role)}
    stale = sorted(str(p.relative_to(root)) for p in paths if p.exists())
    targets = ['schema-submissions (annotation_sha256; regenerate)'] if 'annotations' in changed and \
        store.path(root, 'schema-submissions', aid).exists() else []
    return {'article_id': aid, 'files': files, 'changed_files': changed, 'stale_receipts': stale, 'stale_targets': targets}


def report(plan):
    return {'article_id': plan['article_id'], 'changed_files': plan['changed_files'],
            'changes': {f: c for f, (_, _, c) in plan['files'].items() if c},
            'counts': {f: len(c) for f, (_, _, c) in plan['files'].items() if c},
            'stale_receipts': plan['stale_receipts'], 'stale_targets': plan['stale_targets']}


def apply(root, plan):
    from migrate_case_context import STYLES, style_of
    root = Path(root)
    for folder, (old, new, changes) in plan['files'].items():
        if not changes:
            continue
        path = store.path(root, folder, plan['article_id'])
        text = path.read_text(encoding='utf-8')
        path.write_text(STYLES[style_of(text) or 'i2'](new), encoding='utf-8')


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--root', type=Path, default=ROOT); ap.add_argument('--article')
    ap.add_argument('--apply', action='store_true'); ap.add_argument('--out', type=Path)
    args = ap.parse_args(argv)
    if args.apply and not args.article:
        ap.error('--apply needs --article (one article at a time)')
    ids = [args.article] if args.article else store.article_ids(args.root, 'annotations')
    reports = []
    for aid in ids:
        plan = plan_article(args.root, aid)
        if args.apply:
            apply(args.root, plan)
        if plan['changed_files']:
            reports.append(report(plan))
    summary = {'articles_scanned': len(ids), 'articles_changed': len(reports),
               'edits': sum(sum(r['counts'].values()) for r in reports),
               'by_file': {f: sum(r['counts'].get(f, 0) for r in reports) for f in
                           ('annotations', 'visual-submissions', 'figure-ehr-labels', 'figure-privacy-flags', 'supplement-manifests')},
               'stale_receipts': sum(len(r['stale_receipts']) for r in reports), 'applied': args.apply}
    out = {'summary': summary, 'articles': reports}
    if args.out:
        args.out.write_text(json.dumps(out, ensure_ascii=False, indent=1) + '\n')
    print(json.dumps(summary, indent=1))


if __name__ == '__main__':
    main()
