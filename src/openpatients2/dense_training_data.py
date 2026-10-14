"""CPU-only supervised-data audit for the progressive GEPA campaign.

No annotation repair, guessed targets, truncation, model download or GPU training.
Rejected examples stay in the audit. Article partitions are inherited unchanged.
"""
import asyncio
import copy
import json
from collections import Counter

from . import dense_benchmark as b


def has_review_only(value):
    if isinstance(value, dict):
        return value.get('scoring') == 'review_only' or any(has_review_only(x) for x in value.values())
    return isinstance(value, list) and any(has_review_only(x) for x in value)


async def export(work, tasks):
    from .pilot_extract import PilotRunner
    from .schemas import TASK_MODELS
    plan = b.read(work / 'manifest.json')['gepa']
    refs = b.rows(work / 'reference/unified.jsonl')
    destination = work / 'inputs/training-data'
    cfg = {**b.extraction_config(), 'max_figures': 0, 'summary_structured_output': False,
           'overnight_repairs': False, 'repair_components': []}
    runner = PilotRunner(cfg, destination / 'prompt-builder', ['http://localhost:1/v1'], b.CONTEXT, 'targeted')
    accepted, rejected = Counter(), []
    try:
        for split, partition in [('train', 'train'), ('eval', 'search_validation')]:
            rows = []
            for aid in plan['partition'][partition]:
                reference = refs[aid]
                patients = {p['patient_id']: p for p in reference['patients']}
                for row in b.read(work / 'inputs' / aid / 'gepa-packets.json'):
                    packet, segments = row['packet'], row['segments']
                    pid = packet['patient_target']['patient_id']
                    for task in tasks:
                        identity = {'article_id': aid, 'patient_id': pid, 'task': task, 'split': split}
                        target = copy.deepcopy(patients[pid]['sections'].get(task))
                        try:
                            if target is None: raise ValueError('missing_target_section')
                            if has_review_only(target): raise ValueError('review_only_facts_require_manual_resolution')
                            # Validate the entire gold target; never silently discard
                            # troublesome fields/items to make a training example pass.
                            target = TASK_MODELS[task].model_validate(target).model_dump(mode='json')
                            target = runner.clinical_checker(task, packet, segments)(target)
                        except (ValueError, TypeError, KeyError) as exc:
                            rejected.append(identity | {'reason': str(exc)[:3000]}); continue
                        messages = runner.prepared_messages(task, runner.clinical_messages(task, packet))
                        completion = [{'role': 'assistant', 'content': json.dumps(target, ensure_ascii=False)}]
                        rows.append(identity | {'prompt': messages, 'completion': completion})
                        accepted[f'{split}:{task}'] += 1
            b.write_rows(destination / f'{split}.jsonl', rows)
    finally:
        await runner.close()
    b.write_rows(destination / 'rejected.jsonl', rejected)
    report = {'format': 'dense-supervised-candidates/1', 'accepted': dict(accepted),
              'rejected': len(rejected), 'articles': {k: len(plan['partition'][v]) for k, v in [('train','train'),('eval','search_validation')]},
              'test_exported': False, 'confirmation_exported': False,
              'ready_for_training': False,
              'remaining_preflight': ['pin BF16 base and training environment',
                'render native template and verify completion-only loss masking',
                'tokenize without truncation and audit length exclusions',
                'B200 forward/backward adapter smoke test and generation check'],
              'note': 'Validated supervised candidates, not a proven training recipe. No reasoning traces were fabricated.'}
    b.write(destination / 'audit.json', report)
    return report


def export_sync(work, tasks):
    return asyncio.run(export(work, tasks))
