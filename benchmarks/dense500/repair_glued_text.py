"""Repair glued free text ("aftersevenmonths" -> "after seven months") in dense annotations.

    # dry run (default): per-article diffs of the HIGH-confidence repairs, nothing written
    PYTHONPATH=src:benchmarks/dense500 .venv/bin/python benchmarks/dense500/repair_glued_text.py \
        [--article PMC...] [--out DIR]
    # apply to ONE article (the annotation's review signature is then stale: an Opus reviewer
    # verifies the diff and re-signs)
    ... repair_glued_text.py --apply --article PMC... [--out DIR]

Only findings of scan_glued_text with confidence=high in exported or internal free-text fields are
applied; quotes, evidence, ids, enums and literal time text are never touched (scan categories
skip/enum/literal). Each changed value is rewritten by replacing the glued token (as a whole
letters/digits piece outside filenames/URLs/identifiers) with its segmentation; everything else in the string is kept byte for byte.
--apply refuses to write when the repaired annotation has validator errors that the original did not.
"""
from __future__ import annotations

import argparse
import copy
import json
from collections import defaultdict
from pathlib import Path

from openpatients2.glued_text import apply_segmentations
from scan_glued_text import ROOT, SKIP_KEYS, scan_annotation

REPAIRABLE = ('exported', 'internal')


def _resolve(obj, pointer):
    parts = pointer.strip('/').split('/')
    for part in parts[:-1]:
        obj = obj[int(part)] if isinstance(obj, list) else obj[part]
    last = parts[-1]
    return obj, int(last) if isinstance(obj, list) else last


def plan(annotation, source):
    """[{pointer, field, category, owner, tokens, before, after}] for high-confidence repairs."""
    by_pointer = defaultdict(list)
    for f in scan_annotation(annotation, source):
        leaf = f['pointer'].rsplit('/', 1)[-1]
        if f['confidence'] == 'high' and f['category'] in REPAIRABLE and leaf not in SKIP_KEYS \
                and '/evidence/' not in f['pointer']:
            by_pointer[f['pointer']].append(f)
    changes = []
    for pointer, findings in by_pointer.items():
        parent, key = _resolve(annotation, pointer)
        before = parent[key]
        mapping = {f['token']: f['segmentation'] for f in findings}
        after = apply_segmentations(before, mapping)
        if after != before:
            changes.append({'pointer': pointer, 'field': findings[0]['field'], 'category': findings[0]['category'],
                            'owner': findings[0]['owner'], 'tokens': mapping, 'before': before, 'after': after})
    return changes


def apply(annotation, changes):
    out = copy.deepcopy(annotation)
    for c in changes:
        parent, key = _resolve(out, c['pointer'])
        if parent[key] != c['before']:
            raise ValueError(f"{c['pointer']}: value changed since the plan was made")
        parent[key] = c['after']
    return out


def _errors(annotation, source):
    from validate import case_context_errors, semantic_errors, validate
    return set(validate(annotation, source) + case_context_errors(annotation) + semantic_errors(annotation, source))


def diff_text(article_id, changes):
    lines = [f'# {article_id}: {len(changes)} value(s), '
             f'{sum(len(c["tokens"]) for c in changes)} token(s) (high confidence only)']
    for c in changes:
        lines += [f'@@ {c["pointer"]}  [{c["category"]}] owner={c["owner"]}  '
                  + ', '.join(f'{k} -> {v}' for k, v in c['tokens'].items()),
                  f'- {c["before"]}', f'+ {c["after"]}']
    return '\n'.join(lines) + '\n'


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--root', type=Path, default=ROOT)
    ap.add_argument('--folder', default='annotations')
    ap.add_argument('--article')
    ap.add_argument('--apply', action='store_true', help='write the repaired annotation (requires --article)')
    ap.add_argument('--out', type=Path, help='directory for <article>.diff and <article>.json change lists')
    args = ap.parse_args(argv)
    if args.apply and not args.article:
        ap.error('--apply requires --article (one article at a time, then re-review)')
    if args.out:
        args.out.mkdir(parents=True, exist_ok=True)
    summary = {}
    for path in sorted((args.root / args.folder).glob((args.article or '*') + '.json')):
        annotation = json.loads(path.read_text())
        source = json.loads((args.root / 'sources' / path.name).read_text())
        changes = plan(annotation, source)
        if not changes:
            continue
        aid = path.stem
        summary[aid] = {'values': len(changes), 'tokens': sum(len(c['tokens']) for c in changes),
                        'exported_values': sum(c['category'] == 'exported' for c in changes)}
        if args.out:
            (args.out / f'{aid}.diff').write_text(diff_text(aid, changes))
            (args.out / f'{aid}.json').write_text(json.dumps(changes, ensure_ascii=False, indent=1) + '\n')
        elif args.article:
            print(diff_text(aid, changes))
        if args.apply:
            repaired = apply(annotation, changes)
            new_errors = _errors(repaired, source) - _errors(annotation, source)
            if new_errors:
                raise SystemExit(f'{aid}: repair would introduce validator errors, not written: {sorted(new_errors)}')
            tmp = path.with_suffix('.tmp')
            tmp.write_text(json.dumps(repaired, ensure_ascii=False, indent=2) + '\n')
            tmp.replace(path)
            summary[aid]['applied'] = True
    print(json.dumps({'mode': 'apply' if args.apply else 'dry_run', 'articles': len(summary),
                      'values': sum(s['values'] for s in summary.values()),
                      'tokens': sum(s['tokens'] for s in summary.values()), 'by_article': summary}, indent=1))


if __name__ == '__main__':
    main()
