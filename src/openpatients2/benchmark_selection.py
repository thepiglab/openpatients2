"""Reproducible diversity sampling; evaluation metadata never enters prompts."""
from collections import Counter, defaultdict
import hashlib
import json


METHOD = 'clinical-category-balanced-sha256/2'


def article_features(row, source):
    """Corpus categories are existing curation buckets, not a clinical ontology.

    Reference patient counts/species describe evaluation difficulty only. Never
    pass these features or reference rosters into extraction requests.
    """
    size = len(source['text'])
    figures = len(source.get('figures', []))
    patients = len(row.get('patients', []))
    return {'clinical_category': row.get('primary_stratum') or 'unclassified',
            'source_length': 'short' if size < 15000 else 'medium' if size < 40000 else 'long' if size < 120000 else 'very_long',
            'figures': 'none' if not figures else '1-3' if figures <= 3 else '4+',
            'tables': 'present' if any(s.get('kind') == 'table_row' for s in source.get('segments', [])) else 'absent',
            'reference_patients': 'none' if not patients else 'single' if patients == 1 else '2-4' if patients <= 4 else '5+',
            'species': 'nonhuman_or_mixed' if row.get('species', {}).get('article_has_non_human_patient') else 'human_or_unspecified'}


def source_for(root, aid):
    paths = list(root.glob(f'benchmarks/dense*/articles/{aid}/source.json'))
    if len(paths) != 1:
        raise ValueError('Expected exactly one source for ' + aid)
    return json.loads(paths[0].read_text())


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
    features = {}
    for row in rows:
        aid = row['article_id']
        features[aid] = article_features(row, source_for(root, aid))
        buckets[(row['split'], features[aid]['clinical_category'])].append(aid)
    def rank(value):
        return hashlib.sha256(f'{seed}:{value}'.encode()).hexdigest()
    queues = [list(buckets[k]) for k in sorted(buckets, key=lambda k: rank(repr(k)))]
    selected = []
    counts = Counter()
    while len(selected) < limit:
        for queue in queues:
            if queue:
                # Equal category turns first; within a category favor uncovered
                # difficulty features, with stable seeded hash tie-breaking.
                def priority(aid):
                    novelty = sum(1 / (1 + counts[(k, v)]) for k, v in features[aid].items()
                                  if k != 'clinical_category')
                    return (-novelty, rank(aid))
                aid = min(queue, key=priority)
                queue.remove(aid)
                selected.append(aid)
                counts.update(features[aid].items())
                if len(selected) == limit:
                    break
    return selected


def cohort_report(reference, predictions, receipts, features):
    from .unified_scoring import score
    from .benchmark_reporting import throughput, nonempty_valid
    groups = defaultdict(list)
    for aid, ref in reference.items():
        for dimension, category in features.get(aid, {}).items():
            groups[(ref['split'], dimension, category)].append(aid)
    result = []
    for (split, dimension, category), ids in sorted(groups.items()):
        subset = {aid: reference[aid] for aid in sorted(ids)}
        arms = {}
        for name, predicted in predictions.items():
            completed = [receipts[name][aid] for aid in ids if receipts[name].get(aid)]
            scores = score(subset, {aid: predicted[aid] for aid in ids if aid in predicted})
            arms[name] = {'completed': len(completed), 'predictions': sum(aid in predicted for aid in ids),
                'fully_valid': sum(r.get('all_extraction_tasks_valid', False) for r in completed),
                'nonempty_pipeline_valid': sum(nonempty_valid(r) for r in completed),
                'clinical': scores['overall']['clinical_items'],
                'attribute_accuracy': scores['overall']['attribute_accuracy'],
                'task_statuses': dict(sum((Counter(r.get('task_statuses', {})) for r in completed), Counter())),
                'throughput': throughput(completed)}
            if not completed:
                arms[name]['clinical'] = {**arms[name]['clinical'], 'precision': None, 'recall': None, 'f1': None}
        result.append({'split': split, 'dimension': dimension, 'category': category,
                       'article_ids': sorted(ids), 'arms': arms})
    return {'format': 'super-cohorts/1', 'groups': result,
            'note': 'Planned article denominators; unmeasured groups have null rates. Reference-derived evaluation metadata is never supplied to models.'}
