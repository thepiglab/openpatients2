#!/usr/bin/env python3
"""Prepare source-only classification questions; never downloads/calls models."""
import argparse
import gzip
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from openpatients2.article_profile import prepare, result_schema, evidence_candidates


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--source', type=Path, help='Normalized source article JSON or JSONL (optionally .gz)')
    p.add_argument('--article-id', help='Required when selecting from JSONL')
    p.add_argument('--scope', choices=['title_abstract', 'full_text'], default='title_abstract')
    p.add_argument('--fields', nargs='+', help='Catalog fields; omit for the full diagnostic schema')
    p.add_argument('--out', required=True, type=Path, help='New output directory')
    args = p.parse_args()
    schema = result_schema(args.fields)
    prepared = None
    if args.source:
        opener = gzip.open if args.source.suffix == '.gz' else open
        with opener(args.source, 'rt') as f:
            if '.jsonl' in args.source.suffixes:
                if not args.article_id: p.error('--article-id is required for JSONL')
                matches = [r for line in f if line.strip()
                           if (r := json.loads(line))['article_id'] == args.article_id]
                if len(matches) != 1: p.error('Expected exactly one matching source article')
                article = matches[0]
            else:
                article = json.load(f)
                if args.article_id and article['article_id'] != args.article_id:
                    p.error('Source article ID does not match')
        prepared = prepare(article, scope=args.scope, field_ids=args.fields)
    args.out.mkdir(parents=True, exist_ok=False)
    (args.out / 'result.schema.json').write_text(json.dumps(schema, indent=2) + '\n')
    if prepared:
        (args.out / 'prepared.json').write_text(json.dumps(prepared, indent=2, ensure_ascii=False) + '\n')
        candidates = evidence_candidates(prepared['request']['state'])
        (args.out / 'evidence-candidates.json').write_text(json.dumps(candidates, indent=2, ensure_ascii=False) + '\n')
    print(json.dumps({'output': str(args.out.resolve()), 'model_calls': 0,
                      'questions': len(prepared['request']['questions']) if prepared else 0}))


if __name__ == '__main__':
    main()
