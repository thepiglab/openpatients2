"""Index terminal task failures without hiding them behind completed articles."""
from collections import Counter
import json


def extraction_failures(work, completed):
    records = []
    for article_id, split, attempt in completed:
        for path in sorted((attempt / 'extraction/tasks').glob('*.json')):
            task = json.loads(path.read_text())
            if task.get('status') == 'valid' or task.get('superseded_by_split'):
                continue
            records.append({'article_id': article_id, 'split': split,
                'task': task['task'], 'status': task.get('status'),
                'identity': task.get('identity'), 'errors': task.get('errors', []),
                'quarantined_candidates': len(task.get('quarantine', [])),
                'repair_history': [
                    {'attempt': i, 'errors': a.get('errors', []),
                     'pending_items': [
                         {'index': row.get('index'),
                          'name': row['item'].get('name') if isinstance(row.get('item'), dict) else None,
                          'errors': row.get('errors', [])}
                         for row in (a.get('item_repair') or {}).get('pending', [])]}
                    for i, a in enumerate(task.get('attempts', []))
                    if a.get('errors') or (a.get('item_repair') or {}).get('pending')],
                'task_file': str(path.relative_to(work))})
        receipt = json.loads((attempt / 'receipt.json').read_text())
        for task, error in receipt.get('ehr_errors', {}).items():
            records.append({'article_id': article_id, 'split': split,
                'task': 'ehr/' + task, 'status': 'failed', 'errors': [error],
                'task_file': str((attempt / 'receipt.json').relative_to(work))})
    return {'schema_version': 'extraction-failures/1', 'records': records,
        'by_task': dict(Counter(r['task'] for r in records)),
        'by_article': dict(Counter(r['article_id'] for r in records)),
        'note': 'Terminal task records, including staged assembly wrappers and dependent blocked tasks; '
                'counts are not independent root causes. Raw attempts and quarantined candidates remain in task files.'}
