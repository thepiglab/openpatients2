"""Summarize existing source lengths and provider usage; no inference calls.

Article-only tokenizer counts are supplied in article-counts.json. Their model
revision and tokenizer hash are recorded separately; counts exclude chat tokens.
"""
from collections import defaultdict
import argparse
import hashlib
import json
from pathlib import Path
import statistics

ROOT = Path('runs/token-accounting')
FIDS = Path('runs/medical-fidelity-v1')
MODELS = ['meta/muse-glimmer-30b', 'meta/muse-spark-1.2']
TOKEN_KEYS = ['prompt_tokens', 'completion_tokens', 'reasoning_tokens', 'cached_tokens']


def stats(xs):
    xs = sorted(xs)
    index = (len(xs) - 1) * .95
    lo = int(index)
    return {'n': len(xs), 'mean': statistics.mean(xs), 'median': statistics.median(xs),
            'p95': xs[lo] + (xs[min(lo + 1, len(xs)-1)] - xs[lo]) * (index-lo),
            'min': xs[0], 'max': xs[-1]}


def summarize(rows, keys):
    return {key: stats([r[key] for r in rows]) for key in keys}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--tokenizer', type=Path,
                        help='Optional pinned tokenizer.json; rebuild article-only counts with tokenizers installed.')
    args = parser.parse_args()
    if args.tokenizer:
        from tokenizers import Tokenizer
        spec = json.loads((ROOT/'tokenizer-revision.json').read_text())
        assert hashlib.sha256(args.tokenizer.read_bytes()).hexdigest() == spec['tokenizer_sha256']
        tokenizer = Tokenizer.from_file(str(args.tokenizer))
        tokenizer.no_truncation()
        tokenizer.no_padding()
        counted = []
        for line in (FIDS/'articles.jsonl').read_text().splitlines():
            article = json.loads(line)
            if article['pmcid'] in {'PMC12807056', 'PMC13304996'}:
                continue  # Frozen article-level controls without source cases.
            source = '\n'.join(s['text'] for s in article['segments'])
            words = len(source.split())
            tokens = len(tokenizer.encode(source, add_special_tokens=False).ids)
            counted.append({'pmcid': article['pmcid'], 'source_sha256': article['text_sha256'],
                            'words': words, 'glimmer_source_tokens': tokens,
                            'glimmer_segment_tagged_tokens': len(tokenizer.encode(article['text'], add_special_tokens=False).ids),
                            'tokens_per_word': tokens/words,
                            'conference_abstract': article['pmcid'] == 'PMC12773240'})
        (ROOT/'article-counts.json').write_text(json.dumps(counted, indent=2)+'\n')
    articles = json.loads((ROOT/'article-counts.json').read_text())
    counts = {a['pmcid']: a for a in articles}
    for line in (FIDS/'articles.jsonl').read_text().splitlines():
        a = json.loads(line)
        if a['pmcid'] in counts:
            assert counts[a['pmcid']]['source_sha256'] == a['text_sha256']
            assert counts[a['pmcid']]['words'] == sum(len(s['text'].split()) for s in a['segments'])
    keys = ['words', 'glimmer_source_tokens', 'glimmer_segment_tagged_tokens']
    result = {'quantiles': 'Linear interpolation at (n-1)*p; tiny purposive samples, not population estimates.',
              'article_only': {'case_documents_7': summarize(articles, keys),
                               'full_papers_6': summarize([a for a in articles if not a['conference_abstract']], keys)},
              'source_tokens_per_word_pooled': sum(a['glimmer_source_tokens'] for a in articles)/sum(a['words'] for a in articles),
              'historical_full_text_workflow': {}, 'recent_partial_repair_workflow': {}, 'provenance': {}}
    metrics = json.loads((FIDS/'comparison/report.json').read_text())['metrics']
    roster_metrics = json.loads((FIDS/'rosters/report.json').read_text())['metrics']
    for model in MODELS:
        rows = [m for m in metrics if m['model'] == model]
        assert len(rows) == len({m['signature'] for m in rows}) == 176
        groups = defaultdict(lambda: defaultdict(int))
        patient_groups = defaultdict(lambda: defaultdict(int))
        missing = []
        for m in rows:
            task = json.loads((FIDS/'comparison/tasks'/f"{m['signature']}.json").read_text())
            for k in TOKEN_KEYS:
                assert m[k] == task['metrics'][k]
                groups[m['article_id']][k] += m[k]
                patient_groups[(m['article_id'], m['patient_id'])][k] += m[k]
            groups[m['article_id']]['attempts'] += m['attempts']
            groups[m['article_id']]['tasks'] += 1
            for a in task['attempt_responses']:
                u = a.get('usage', {})
                if not all(isinstance(u.get(k), int) for k in ['prompt_tokens', 'completion_tokens']):
                    missing.append({'article': m['article_id'], 'task': m['task'], 'error': a.get('error')})
        for g in groups.values():
            g['uncached_prompt_tokens'] = g['prompt_tokens'] - g['cached_tokens']
            g['nonreasoning_completion_tokens'] = g['completion_tokens'] - g['reasoning_tokens']
            g['patients'] = g['tasks']//16
        allkeys = TOKEN_KEYS + ['uncached_prompt_tokens', 'nonreasoning_completion_tokens', 'attempts']
        roster_rows = [m for m in roster_metrics if m['model'] == model and m['article_id'] in groups]
        assert len(roster_rows) == 7
        plus_roster = []
        for m in roster_rows:
            g = groups[m['article_id']]
            plus_roster.append({'article_id': m['article_id'], **{k: g[k]+m[k] for k in TOKEN_KEYS}})
        result['historical_full_text_workflow'][model] = {
            'scope': '14 clinical tasks plus summary and timeline for each of 11 reference-roster patients in 7 licensed case documents. Includes all saved task retries; excludes roster/image calls in main totals. Not the new local-repair strategy.',
            'all_attempt_usage_known': not missing, 'unknown_usage_attempts': missing,
            'by_article': [{'article_id': aid, **g} for aid, g in groups.items()],
            'per_article': summarize(list(groups.values()), allkeys),
            'per_patient_excluding_roster': summarize(list(patient_groups.values()), TOKEN_KEYS),
            'adding_separately_measured_roster_stage': summarize(plus_roster, TOKEN_KEYS),
            'roster_note': 'Separate roster experiment overhead, not an observed automatic end-to-end run; clinical extraction used fixed reference rosters. Images, ontology linking, human review and failed discovery replacements excluded.'}
        # Reconstruct ONLY the chosen latest experimental arm's dependencies.
        from score_refinement_v1 import cost_nodes
        new_groups, tasks, nodes = defaultdict(lambda: defaultdict(int)), [], {}
        for p in Path('runs/refinement-v1/outcomes').glob('*.json'):
            row = json.loads(p.read_text())
            if row['model'] != model or row['extraction'] != 'direct':
                continue
            totals = defaultdict(int)
            for call in cost_nodes(row, 'retry_local'):
                m = call.get('metrics', {})
                if not m.get('signature') or m['signature'] in nodes:
                    continue
                nodes[m['signature']] = m
                for k in TOKEN_KEYS:
                    totals[k] += m[k]
                    new_groups[row['pmcid']][k] += m[k]
            new_groups[row['pmcid']]['task_pairs'] += 1
            tasks.append(dict(totals))
        result['recent_partial_repair_workflow'][model] = {
            'scope': 'Direct + retry/recovery + local repair, eight task/patient pairs across five articles. These are incomplete article runs and must not be advertised as full-pipeline article usage.',
            'calls': len(nodes), 'per_task_pair_including_repairs': summarize(tasks, TOKEN_KEYS),
            'per_api_call': summarize(list(nodes.values()), TOKEN_KEYS),
            'by_partially_processed_article': [{'pmcid': aid, **g} for aid, g in new_groups.items()]}
    for path in [FIDS/'articles.jsonl', FIDS/'comparison/report.json', FIDS/'rosters/report.json', ROOT/'article-counts.json', ROOT/'tokenizer-revision.json']:
        result['provenance'][str(path)] = hashlib.sha256(path.read_bytes()).hexdigest()
    (ROOT/'summary.json').write_text(json.dumps(result, indent=2)+'\n')
    print(json.dumps({'article_only': result['article_only'], 'models': {
        m: {k: v['per_article'][k] for k in ['prompt_tokens','completion_tokens','nonreasoning_completion_tokens','uncached_prompt_tokens']}
        for m,v in result['historical_full_text_workflow'].items()}}, indent=2))


if __name__ == '__main__':
    main()
