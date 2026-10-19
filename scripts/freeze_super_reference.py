#!/usr/bin/env python3
"""Refresh the tracked Super reference from a completed local release (CPU only)."""
import argparse
from collections import Counter
import gzip
import hashlib
import json
from pathlib import Path


def freeze(root):
    release = root / 'data/dense_clinical_cases_v1/data'
    rows, seen, inputs = [], set(), {}
    for split in ('train', 'validation', 'test'):
        path = release / f'{split}.jsonl'
        inputs[split] = hashlib.sha256(path.read_bytes()).hexdigest()
        for line in path.read_text().splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            aid = row['article_id']
            if aid in seen or row['split'] != split:
                raise ValueError('Duplicate article or inconsistent split: ' + aid)
            seen.add(aid)
            completion = row.get('completion', {})
            if not completion.get('completed'):
                continue
            if not completion.get('checks') or not all(completion['checks'].values()):
                raise ValueError('Invalid completion checks: ' + aid)
            sources = list(root.glob(f'benchmarks/dense*/articles/{aid}/source.json'))
            if len(sources) != 1:
                raise ValueError('Expected exactly one source: ' + aid)
            source = json.loads(sources[0].read_text())
            if source['text_sha256'] != row['text_sha256']:
                raise ValueError('Stale reference source: ' + aid)
            rows.append(row)
    if not rows:
        raise ValueError('No completed references')
    data = ''.join(json.dumps(r, ensure_ascii=False, sort_keys=True) + '\n'
                   for r in sorted(rows, key=lambda r: r['article_id'])).encode()
    out = root / 'configs/hipergator/nemotron-super'
    out.mkdir(parents=True, exist_ok=True)
    packed = gzip.compress(data, mtime=0)
    (out / 'reference.jsonl.gz').write_bytes(packed)
    record = {'format': 'super-reference/1', 'articles': len(rows),
        'splits': dict(Counter(r['split'] for r in rows)),
        'sha256': hashlib.sha256(packed).hexdigest(), 'release_file_sha256': inputs,
        'policy': 'Only completed references with passing checks and matching current source hashes; splits preserved.'}
    (out / 'reference-manifest.json').write_text(json.dumps(record, indent=2) + '\n')
    return record


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=Path(__file__).resolve().parents[1])
    print(json.dumps(freeze(parser.parse_args().root.resolve()), indent=2))
