"""Fixed diagnostic sample for two prospective candidate pipelines; no API calls."""
import hashlib
import json
from pathlib import Path
import random

from openpatients2.fidelity import without_evidence

ROOT = Path('runs/refinement-v1')
PIPELINES = [('direct', 'retry_local'), ('spans_batches', 'local_repair')]
SEED = 20260929


def main():
    audit, availability = [], []
    for path in sorted((ROOT/'outcomes').glob('*.json')):
        row = json.loads(path.read_text())
        for extraction, arm in PIPELINES:
            if row['extraction'] != extraction or arm not in row['arms']:
                continue
            candidate = row['arms'][arm]['delivered']
            items = (candidate or {}).get('items', [])
            rng = random.Random(f"{SEED}:{row['record_id']}:{row['task']}")
            chosen = rng.sample(range(len(items)), min(2, len(items)))
            availability.append({'model': row['model'], 'record_id': row['record_id'], 'task': row['task'],
                                 'extraction': extraction, 'arm': arm, 'sampled': len(chosen),
                                 'target': 2, 'candidate_items': len(items)})
            for index in chosen:
                ident = hashlib.sha256(f"{row['model']}:{row['record_id']}:{row['task']}:{extraction}:{arm}:{index}".encode()).hexdigest()[:16]
                audit.append({'audit_id': ident, 'model': row['model'], 'record_id': row['record_id'],
                              'task': row['task'], 'extraction': extraction, 'arm': arm, 'item_index': index,
                              'claim': without_evidence(items[index]), 'evidence': items[index].get('evidence', [])})
    data = {'method': 'Fixed-seed source review of up to two delivered facts per task for direct+retry+local and span-batches+local. Not blinded, independent clinical truth, or population precision. Availability differs.',
            'pipelines': PIPELINES, 'seed': SEED, 'claims': audit, 'availability': availability}
    (ROOT/'claim-audit-pending.json').write_text(json.dumps(data, indent=2, ensure_ascii=False)+'\n')
    print('Available sampled claims:', len(audit), 'pipeline-unit cells:', len(availability))


if __name__ == '__main__':
    main()
