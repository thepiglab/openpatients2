"""Throughput of the actual extraction pipeline, with explicit denominators."""
from __future__ import annotations


def nonempty_valid(row):
    """Pipeline validity is not clinical accuracy; an empty roster is separate."""
    return bool(row.get('has_prediction') and (row.get('patients_extracted') or 0) > 0
                and row.get('all_extraction_tasks_valid') and 'ehr_errors' in row and not row['ehr_errors'])


def quality_summary(receipts, reference=None, predictions=None):
    rows = list(receipts)
    result = {
        'prediction_artifacts': sum(bool(r.get('has_prediction')) for r in rows),
        'nonempty_patient_articles': sum(bool(r.get('has_prediction') and (r.get('patients_extracted') or 0)>0) for r in rows),
        'empty_prediction_articles': sum(bool(r.get('has_prediction') and r.get('patients_extracted')==0) for r in rows),
        'patient_count_unavailable': sum(r.get('patients_extracted') is None for r in rows),
        'schema_valid_including_empty': sum(bool(r.get('all_extraction_tasks_valid')) for r in rows),
        'nonempty_pipeline_valid_articles': sum(nonempty_valid(r) for r in rows),
        'pipeline_validity_unavailable': sum('ehr_errors' not in r or r.get('patients_extracted') is None for r in rows),
        'semantic_accuracy_verified': False}
    if reference is not None and predictions is not None:
        result['empty_roster_reference_disagreements'] = sorted(aid for aid,p in predictions.items()
            if not p.get('patients') and reference.get(aid,{}).get('patients'))
        result['empty_roster_agreements'] = sorted(aid for aid,p in predictions.items()
            if not p.get('patients') and aid in reference and not reference[aid].get('patients'))
    return result


def task_usage_summary(receipts):
    rows = list(receipts)
    if not rows or any('task_usage' not in r for r in rows):
        return None  # Historical totals cannot be retrospectively apportioned.
    result = {}
    for task in sorted({t for r in rows for t in r['task_usage']}):
        items = [r['task_usage'][task] for r in rows if task in r['task_usage']]
        result[task] = {k: sum(i[k] for i in items) for k in
            ('model_calls', 'length_limited_calls', 'out_of_batch_candidates')}
        for k in ('prompt_tokens','completion_tokens','reasoning_tokens'):
            result[task][k] = sum(i[k] for i in items) if all(i[k] is not None for i in items) else None
    return result


def throughput(receipts):
    """Aggregate completed documents; never average per-document token rates.

    Includes discovery, all task calls/repairs, validation and EHR processing.
    Parallel task calls share article wall time. Summed article time is server
    service time, not peak decoding speed or concurrent multi-server wall time.
    """
    rows = list(receipts)
    seconds = sum(r['wall_seconds'] for r in rows)
    gpu_seconds = sum(r['wall_seconds'] * r.get('gpus_per_server', 1) for r in rows)

    def rate(value, denominator=seconds):
        return value / denominator if value is not None and denominator > 0 else None

    def total(key):
        values = [r.get(key) for r in rows]
        return sum(values) if rows and all(v is not None for v in values) else None

    output, reasoning = total('completion_tokens'), total('reasoning_tokens')
    def mean(key):
        value = total(key)
        return value / len(rows) if value is not None and rows else None
    # Some endpoints omit a reasoning breakdown. Never infer zero reasoning or
    # re-tokenize a different model's output to manufacture an answer-token count.
    answer = (output - reasoning if output is not None and reasoning is not None
              and all(0 <= r['reasoning_tokens'] <= r['completion_tokens'] for r in rows) else None)
    predicted = sum(bool(r.get('has_prediction')) for r in rows)
    valid = sum(bool(r.get('has_prediction') and r.get('all_extraction_tasks_valid')) for r in rows)
    return {
        'documents': len(rows), 'prediction_documents': predicted,
        'valid_extraction_documents': valid,
        'nonempty_pipeline_valid_documents': sum(nonempty_valid(r) for r in rows),
        'nonempty_pipeline_valid_documents_per_hour': rate(sum(nonempty_valid(r) for r in rows) * 3600),
        'article_seconds': seconds, 'article_gpu_seconds': gpu_seconds,
        'prompt_tokens': total('prompt_tokens'), 'completion_tokens': output,
        'reasoning_tokens': reasoning, 'answer_tokens': answer,
        'mean_input_tokens_per_article': mean('prompt_tokens'),
        'mean_output_tokens_per_article': mean('completion_tokens'),
        'mean_reasoning_tokens_per_article': mean('reasoning_tokens'),
        'mean_answer_tokens_per_article': answer / len(rows) if answer is not None and rows else None,
        'mean_model_calls_per_article': mean('model_calls'),
        'documents_missing_input_usage': sum(r.get('prompt_tokens') is None for r in rows),
        'documents_missing_completion_usage': sum(r.get('completion_tokens') is None for r in rows),
        'documents_missing_reasoning_usage': sum(r.get('reasoning_tokens') is None for r in rows),
        'output_tokens_per_second': rate(output),
        'answer_tokens_per_second': rate(answer), 'reasoning_tokens_per_second': rate(reasoning),
        'output_tokens_per_gpu_second': rate(output, gpu_seconds),
        'documents_per_second': rate(len(rows)), 'documents_per_hour': rate(len(rows) * 3600),
        'prediction_documents_per_second': rate(predicted),
        'prediction_documents_per_hour': rate(predicted * 3600),
        'valid_extraction_documents_per_second': rate(valid),
        'valid_extraction_documents_per_hour': rate(valid * 3600),
        'documents_per_gpu_hour': rate(len(rows) * 3600, gpu_seconds),
        'basis': 'sum completed-article tokens or document counts / sum article wall seconds; includes repairs, EHR and any enabled terminology/FHIR stage; excludes startup, queue and operationally failed attempts',
        'reasoning_is_subset_of_completion': True,
        'validity_is_not_semantic_accuracy': True,
    }


def worker_goodput(completed, workers, *, default_gpus=1, missing_receipts=0):
    """Useful document output per observed server time, including startup/retries.

    Missing receipts make the rates unavailable, rather than pretending a
    timed-out worker had no cost. Slurm sacct remains the allocation authority.
    """
    rows, workers = list(completed), list(workers)
    seconds = sum(w['worker_seconds'] for w in workers)
    gpu_seconds = sum(w['worker_seconds'] * w.get('gpus_per_server', default_gpus) for w in workers)
    available = bool(workers) and not missing_receipts and seconds > 0
    return {'worker_receipts': len(workers), 'missing_worker_receipts': missing_receipts,
        'observed_server_seconds': seconds, 'observed_gpu_seconds': gpu_seconds,
        'documents_per_second': len(rows) / seconds if available else None,
        'prediction_documents_per_second': sum(bool(r.get('has_prediction')) for r in rows) / seconds if available else None,
        'valid_extraction_documents_per_second': sum(bool(r.get('has_prediction') and r.get('all_extraction_tasks_valid')) for r in rows) / seconds if available else None,
        'nonempty_pipeline_valid_documents_per_second': sum(nonempty_valid(r) for r in rows) / seconds if available else None,
        'documents_per_gpu_hour': len(rows) * 3600 / gpu_seconds if available and gpu_seconds > 0 else None,
        'basis': 'completed documents / sum observed worker wall seconds; includes startup, probes, retries and cleanup; excludes queue/CPU setup; use sacct for exact allocated cost',
        'validity_is_not_semantic_accuracy': True}


def paired_speculation(receipts, definitions):
    """Matched document service time, never counter acceptance called speedup."""
    baseline = next((a for a, d in definitions.items() if d['profile'] == 'beam-nvfp4-no-spec'), None)
    if baseline is None:
        return []
    out = []
    for arm, definition in definitions.items():
        if definition['profile'] not in {'beam-nvfp4-dflash2', 'beam-nvfp4-dflash2-short'}:
            continue
        ids = sorted(aid for aid, row in receipts[baseline].items() if row and receipts[arm].get(aid))
        base = [receipts[baseline][aid] for aid in ids]
        trial = [receipts[arm][aid] for aid in ids]
        btime, ttime = sum(r['wall_seconds'] for r in base), sum(r['wall_seconds'] for r in trial)
        stages = {}
        for key in ('extraction_seconds', 'ehr_seconds', 'fhir_seconds'):
            if ids and all(r.get(key) is not None for r in base + trial):
                bs, ts = sum(r[key] for r in base), sum(r[key] for r in trial)
                stages[key] = {'baseline_seconds': bs, 'candidate_seconds': ts,
                               'speedup': bs / ts if ts > 0 else None}
        out.append({'articles': len(ids), 'article_ids': ids,
            'pipeline_speedup': btime / ttime if ids and ttime > 0 else None,
            'baseline_seconds': btime, 'candidate_seconds': ttime,
            'stage_comparison': stages,
            'baseline': {'arm': baseline, 'throughput': throughput(base)},
            'candidate': {'arm': arm, 'throughput': throughput(trial)},
            'basis': 'sum baseline article wall seconds / sum candidate article wall seconds on identical completed IDs; >1 is faster. Includes all repairs. Check quality, output lengths and host telemetry; not isolated decoding speedup.',
            'automatic_promotion': False})
    return out
