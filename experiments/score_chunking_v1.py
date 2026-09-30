"""Offline checklists and token accounting, separate from the frozen generator."""
from collections import defaultdict
import argparse
import csv
import hashlib
import json
from pathlib import Path
import random
from statistics import mean, median
from openpatients2.fidelity import evaluate, summarize, without_evidence
from score_refinement_v1 import probe_hits

ROOT = Path('runs/chunking-v1')


def stats(values):
    if not values: return None
    x = sorted(values); j = (len(x)-1)*.95; lo = int(j)
    return {'n': len(x), 'mean': mean(x), 'median': median(x), 'p95': x[lo]+(x[min(lo+1,len(x)-1)]-x[lo])*(j-lo), 'max': x[-1], 'sum': sum(x)}


def run(normalized=False):
    protocol = json.loads((ROOT/'protocol.json').read_text())
    for name, digest in protocol['hashes'].items():
        assert hashlib.sha256(Path(name).read_bytes()).hexdigest() == digest, name
    reference = json.loads(Path('runs/medical-fidelity-v1/reference.json').read_text())
    probes = json.loads(Path('runs/refinement-v1/semantic-probes.json').read_text())
    calls = {p.stem: json.loads(p.read_text()) for p in (ROOT/'calls/tasks').glob('*.json')}
    groups = defaultdict(list)
    paths = {p.name:p for p in (ROOT/'outcomes').glob('*.json')}
    if normalized:
        paths.update({p.name:p for p in (ROOT/'outcomes-normalized').glob('*.json')})
    for p in sorted(paths.values()):
        r = json.loads(p.read_text()); groups[(r['model'], r['strategy'])].append(r)
    output, audit, edits, misses = [], [], [], []
    for (model, strategy), rows in groups.items():
        all_scores, all_probes, cells = [], [], []
        signatures = set(); summaries = []
        for r in rows:
            ref = {**reference, 'checks': [c for c in reference['checks'] if (c['record_id'], c['task']) == (r['record_id'], r['task'])]}
            scores = evaluate(ref, {r['record_id']: {'raw': {r['task']: r['candidate']}, 'delivered': {r['task']: r['delivered']}}})
            all_scores += scores; signatures.update(r['dependencies'])
            for score in scores:
                if score['kind'] == 'required' and not score['delivered']['matched']:
                    check = next(c for c in ref['checks'] if c['id'] == score['check_id'])
                    misses.append({'model': model, 'strategy': strategy, 'task': r['task'], 'check': check, 'available': score['delivered']['available']})
            for probe in probes:
                if (probe['pmcid'], probe['patient_id'], probe['task']) == (r['pmcid'], r['patient_id'], r['task']):
                    all_probes.append({'id': probe['id'], 'hits': probe_hits(probe['id'], r['delivered'])})
            for claim in r['summary']['claims']:
                summaries.append({'record_id': r['record_id'], 'task': r['task'], 'claim': claim})
            items = (r['delivered'] or {}).get('items', [])
            material = [json.dumps(without_evidence(i), sort_keys=True) for i in items]
            cells.append({'record_id': r['record_id'], 'task': r['task'], 'raw': summarize(scores, 'raw'), 'delivered': summarize(scores, 'delivered'),
                          'delivered_items': len(items), 'material_duplicate_excess': len(material)-len(set(material)), 'complete_source_pass': r['complete_source_pass']})
            for t in r.get('trace', []):
                if t.get('applied'):
                    edits.append({'model': model, 'strategy': strategy, 'record_id': r['record_id'], 'task': r['task'], **t})
        nodes = [calls[s]['metrics'] for s in signatures if s in calls and calls[s]['metrics'].get('attempts')]
        maps = [n for n in nodes if n.get('phase') == 'map']
        r = {'model': model, 'strategy': strategy, 'units': len(rows), 'complete_units': sum(c['complete_source_pass'] for c in cells),
             'raw': summarize(all_scores, 'raw'), 'delivered': summarize(all_scores, 'delivered'), 'cells': cells, 'probes': all_probes,
             'probe_violations': sum(bool(p['hits']) for p in all_probes), 'probe_unavailable': sum(p['hits'] is None for p in all_probes),
             'calls': len(nodes), 'cost_usd': sum(n['cost_usd'] for n in nodes),
             'calls_missing_token_usage': sum(not n['prompt_tokens'] for n in nodes),
             'prompt_tokens': stats([n['prompt_tokens'] for n in nodes if n['prompt_tokens']]), 'completion_tokens': stats([n['completion_tokens'] for n in nodes if n['prompt_tokens']]),
             'reasoning_tokens': sum(n['reasoning_tokens'] for n in nodes), 'map_prompt_tokens': stats([n['prompt_tokens'] for n in maps if n['prompt_tokens']]),
             'call_latency_seconds': stats([n['latency_seconds'] for n in nodes]), 'failed_calls': sum(not n['valid'] for n in nodes),
             'material_duplicate_excess': sum(c['material_duplicate_excess'] for c in cells), 'summary_claims': len(summaries)}
        # Aggregate billed dependency tokens at the ARTICLE level, counting its
        # multiple patient-domain units once per shared node. Only tested domains.
        articles = []
        for pmcid in sorted({v['pmcid'] for v in rows}):
            sigs = {s for v in rows if v['pmcid'] == pmcid for s in v['dependencies']}
            ns = [calls[s]['metrics'] for s in sigs if s in calls and calls[s]['metrics'].get('attempts')]
            articles.append({'pmcid': pmcid, 'input_tokens': sum(n['prompt_tokens'] for n in ns), 'output_tokens': sum(n['completion_tokens'] for n in ns)})
        r['article_tokens_tested_domains_only'] = {'input': stats([v['input_tokens'] for v in articles]), 'output': stats([v['output_tokens'] for v in articles]), 'articles': articles}
        output.append(r)
        chosen = random.Random('chunking-v1:'+model+':'+strategy).sample(summaries, min(2,len(summaries)))
        audit += [{'audit_id': hashlib.sha256(json.dumps([model,strategy,s],sort_keys=True).encode()).hexdigest()[:16], 'model':model,'strategy':strategy,**s,'verdict':None,'rationale':None} for s in chosen]
    prefix = 'normalized-' if normalized else ''
    for filename, value in [('scores.json', output), ('summary-audit-sample.json', audit), ('applied-edits.json', edits), ('checklist-misses.json', misses)]:
        (ROOT/(prefix+filename)).write_text(json.dumps(value, indent=2, ensure_ascii=False)+'\n')
    with (ROOT/(prefix+'comparison.csv')).open('w') as f:
        w = csv.writer(f); w.writerow(['model','strategy','units','complete','raw_hits','delivered_hits','required','forbidden','probe_errors','probe_unavailable','calls','USD','input_tokens','output_tokens'])
        for r in output:
            w.writerow([r['model'],r['strategy'],r['units'],r['complete_units'],r['raw']['matched'],r['delivered']['matched'],r['delivered']['required_checks'],r['delivered']['forbidden_violations'],r['probe_violations'],r['probe_unavailable'],r['calls'],round(r['cost_usd'],5), (r['prompt_tokens'] or {}).get('sum',0),(r['completion_tokens'] or {}).get('sum',0)])
            print(r['model'],r['strategy'],f"complete={r['complete_units']}/{r['units']}",f"hits={r['delivered']['matched']}/{r['delivered']['required_checks']}",f"probes={r['probe_violations']}",f"USD={r['cost_usd']:.3f}")


if __name__ == '__main__':
    parser = argparse.ArgumentParser(); parser.add_argument('--normalized',action='store_true')
    run(parser.parse_args().normalized)
