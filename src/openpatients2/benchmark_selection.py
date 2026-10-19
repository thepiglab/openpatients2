"""Reproducible source-stratified selection; no labels enter inference prompts."""
from collections import defaultdict, deque
import hashlib
import json


def select_articles(root, rows, limit=None, seed=42, article_ids=None):
    allowed = {r['article_id'] for r in rows}
    if article_ids is not None:
        if limit is not None or not article_ids or len(set(article_ids)) != len(article_ids):
            raise ValueError('Use unique article IDs or an article limit, not both')
        if not set(article_ids) <= allowed:
            raise ValueError('Requested article IDs are absent from the selected splits')
        return list(article_ids)
    if limit is None:
        return sorted(allowed)
    if limit < 1 or limit > len(rows):
        raise ValueError(f'Article limit must be between 1 and {len(rows)} for the selected splits')
    buckets = defaultdict(list)
    for row in rows:
        paths = list(root.glob(f'benchmarks/dense*/articles/{row["article_id"]}/source.json'))
        if len(paths) != 1:
            raise ValueError('Expected exactly one source for ' + row['article_id'])
        source = json.loads(paths[0].read_text())
        size = len(source['text'])
        key = (row['split'], bool(source.get('figures')),
               any(s.get('kind') == 'table_row' for s in source.get('segments', [])),
               0 if size < 15000 else 1 if size < 40000 else 2)
        buckets[key].append(row['article_id'])
    def rank(value):
        return hashlib.sha256(f'{seed}:{value}'.encode()).hexdigest()
    queues = [deque(sorted(buckets[k], key=rank)) for k in sorted(buckets, key=lambda k: rank(repr(k)))]
    selected = []
    while len(selected) < limit:
        for queue in queues:
            if queue:
                selected.append(queue.popleft())
                if len(selected) == limit:
                    break
    return selected
