"""Offline study integrity and preservation checks, not medical adjudication."""
from collections import Counter, defaultdict
import hashlib
import json
from pathlib import Path

from refinement_v1 import ROOT, OLD, section, split_items
from openpatients2.validation import validate


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def main():
    protocol = json.loads((ROOT / 'protocol.json').read_text())
    unchanged = {path: digest(path) == expected
                 for path, expected in protocol['source_hashes'].items()}
    unchanged['experiments/refinement_extensions_v1.py'] = (
        digest('experiments/refinement_extensions_v1.py') ==
        json.loads((ROOT / 'extension-protocol.json').read_text())['source_sha256'])
    unchanged['experiments/prepare_refinement_audit.py'] = (
        digest('experiments/prepare_refinement_audit.py') ==
        json.loads((ROOT / 'audit-protocol.json').read_text())['script_sha256'])
    assert all(unchanged.values()), unchanged

    scores = json.loads((ROOT / 'scores.json').read_text())
    assert scores['complete']
    outcomes = [json.loads(p.read_text()) for p in (ROOT / 'outcomes').glob('*.json')]
    assert len(outcomes) == 64
    assert all(len(row['arms']) == 7 for row in outcomes)
    articles = {a['pmcid']: a for line in (OLD / 'articles.jsonl').read_text().splitlines()
                if (a := json.loads(line))}
    preservation = []
    for row in outcomes:
        article = articles[row['pmcid']]
        kept, _ = split_items(row['task'], row['seed_result'].get('data'), article)
        remaining = list((row['arms']['local_repair']['delivered'] or {}).get('items', []))
        lost = []
        for index, item in kept.items():
            normalized = validate(row['task'], section([item]), article['text']).data['items'][0]
            if normalized in remaining:
                remaining.remove(normalized)
            else:
                lost.append(index)
        preservation.append({'model': row['model'], 'record_id': row['record_id'],
                             'task': row['task'], 'extraction': row['extraction'],
                             'initially_accepted': len(kept), 'lost_indices': lost})
    assert not any(p['lost_indices'] for p in preservation), preservation

    pending = json.loads((ROOT / 'claim-audit-pending.json').read_text())
    reviewed = json.loads((ROOT / 'claim-audit-reviewed.json').read_text())['reviews']
    assert len(pending['availability']) == 32
    assert {x['audit_id'] for x in pending['claims']} == set(reviewed)
    for claim in pending['claims']:
        assert all(reviewed[claim['audit_id']][k] == v for k, v in claim.items())
    summary = defaultdict(Counter)
    for row in reviewed.values():
        key = f"{row['model']} | {row['extraction']} + {row['arm']}"
        summary[key][row['verdict']] += 1
    availability = defaultdict(Counter)
    for row in pending['availability']:
        key = f"{row['model']} | {row['extraction']} + {row['arm']}"
        availability[key].update({'task_pairs': 1, 'sampled': row['sampled'],
                                  'sample_slots': row['target'],
                                  'empty_task_pairs': int(not row['candidate_items'])})
    audit = {'method': pending['method'], 'sampled_claims': len(reviewed),
             'all_sampled_claims_reviewed': True,
             'by_pipeline': {k: {**dict(availability[k]), **dict(v)} for k, v in summary.items()}}
    (ROOT / 'claim-audit-summary.json').write_text(json.dumps(audit, indent=2) + '\n')
    output = {'frozen_files_unchanged': unchanged, 'outcomes': len(outcomes),
              'model_strategy_cells': 56, 'task_strategy_cells': 448,
              'all_scores_complete': True, 'all_sampled_claims_reviewed': True,
              'local_repair_initial_neighbor_preservation': preservation,
              'total_initial_neighbors_preserved': sum(p['initially_accepted'] for p in preservation),
              'scope': 'Software/artifact integrity; unchanged neighbors can themselves contain medical errors.'}
    (ROOT / 'verification.json').write_text(json.dumps(output, indent=2) + '\n')
    print(json.dumps({'outcomes': len(outcomes), 'cells': 56,
                      'reviewed_claims': len(reviewed),
                      'preserved_neighbors': output['total_initial_neighbors_preserved']}))


if __name__ == '__main__':
    main()
