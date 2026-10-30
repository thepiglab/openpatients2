"""Frozen competition workload and separately reported downstream stage outcomes."""
from collections import Counter
import json
from pathlib import Path
import time

from . import dense_benchmark as b
from .provenance import json_digest

CATALOG = 'configs/terminology/icd10cm-2027.json.gz'
CATALOG_SHA256 = '5a58583057090f62d58895c68a100e0a363f6f9c8eac81cbe522cc7634731c0d'


def configuration(root, selection):
    catalog = Path(root) / CATALOG
    if b.sha(catalog) != CATALOG_SHA256:
        raise ValueError('Competition ICD-10-CM catalog changed; review and version the pinned catalog')
    return {
        'format': 'beam-competition/1',
        'selection_sha256': json_digest(selection),
        'selection_path': 'code/configs/hipergator/nemotron-super/competition-50.json',
        'articles_per_arm': 50, 'article_runs': 100, 'reference_use': 'selection/scoring only',
        'policy': 'Training-only enrichment sample; not population prevalence or a held-out accuracy test.',
        'catalog_path': 'code/' + CATALOG, 'catalog_sha256': CATALOG_SHA256,
        'coding_system': 'http://hl7.org/fhir/sid/icd-10-cm', 'coding_release': '2027',
        'coding': {'max_batches': 16, 'batch_size': 8, 'concurrency': 4, 'time_budget_seconds': 300},
        'fhir_release': '4.0.1',
        'exports': ['uncoded', 'experimental-coded'],
        'official_validator_in_campaign': False,
        'validation_policy': 'Local R4 schema, references and source provenance are checked. '
            'Official HL7 validation is a separate CPU release gate. Model-selected codes are unreviewed.',
        'clinical_accuracy_verified': False, 'release_ready': False,
    }


def coding_ledgers(attempt):
    attempt = Path(attempt)
    return sorted(list((attempt / 'fhir/packet/coding-calls').glob('**/attempts.jsonl')) +
        list((attempt / 'fhir-history').glob('*/packet/coding-calls/**/attempts.jsonl')))


async def run_stage(work, attempt, endpoint, manifest, *, previous_packet=None):
    """Keep downstream failures auditable without discarding extracted facts."""
    from .fhir_campaign import run_article_stage
    settings = manifest['competition']
    output = attempt / 'fhir'
    start = time.monotonic()
    result = {'status': 'interrupted', 'complete': False, 'release_ready': False,
              'clinical_accuracy_verified': False, 'exports': {}, 'coding': {}}
    try:
        catalog = work / settings['catalog_path']
        if b.sha(catalog) != settings['catalog_sha256']:
            raise ValueError('Frozen competition coding catalog checksum mismatch')
        result = await run_article_stage(attempt / 'ehr/data/unified.jsonl', work, output,
            endpoint, work / 'manifest.json', catalog, **settings['coding'],
            **({'previous_packet': previous_packet,
                'previous_packet_original_location': output / 'packet'} if previous_packet else {}))
    except Exception as exc:
        result.update(status='error', error_type=type(exc).__name__, error=str(exc)[:2000])
    finally:
        # Also runs on cancellation. Original ledgers remain available if export fails.
        ledgers = coding_ledgers(attempt)
        result.update(elapsed_seconds=time.monotonic() - start,
            usage=b.ledger_metrics(ledgers), call_ledgers=[str(p.relative_to(attempt)) for p in ledgers],
            ledger_path_base='article_attempt')
        b.write(output / 'receipt.json', result)
    return result


async def retry_stage(work, attempt, endpoint, manifest):
    """Explicit resume reuses extraction and validated decisions, retaining old exports."""
    receipt = b.read(attempt / 'receipt.json')
    start = time.monotonic()
    history = attempt / 'fhir-history'
    history.mkdir(exist_ok=True)
    previous = history / f'attempt-{len(list(history.glob("attempt-*"))):04d}'
    if (attempt / 'fhir').exists():
        (attempt / 'fhir').rename(previous)
    else:
        previous.mkdir()
    b.write(previous / 'article-receipt.json', receipt)
    packet = previous / 'packet'
    # Only a successfully frozen packet can carry decisions forward.
    if not (packet / 'manifest.json').exists():
        packet = next((p / 'packet' for p in sorted(history.glob('attempt-*'), reverse=True)
                       if (p / 'packet/manifest.json').exists()), None)
    before = None
    if manifest.get('capture_speculation_metrics'):
        from .speculation_metrics import snapshot
        before = await snapshot(endpoint)
    try:
        await run_stage(work, attempt, endpoint, manifest, previous_packet=packet)
    finally:
        elapsed = time.monotonic() - start
        receipt['fhir'] = b.read(attempt / 'fhir/receipt.json')
        receipt['fhir_seconds'] = (receipt.get('fhir_seconds') or 0) + elapsed
        receipt['fhir']['elapsed_seconds'] = receipt['fhir_seconds']
        receipt['fhir_resume_count'] = receipt.get('fhir_resume_count', 0) + 1
        receipt['wall_seconds'] += elapsed
        receipt['ended_unix'] = time.time()
        receipt.update(b.ledger_metrics([attempt / 'extraction/attempts.jsonl',
            attempt / 'ehr/calls/attempts.jsonl', *coding_ledgers(attempt)]))
        if before is not None:
            from .speculation_metrics import snapshot, delta
            old = receipt.get('speculation', {'available': False})
            parts = old.get('segments', [old]) + [delta(before, await snapshot(endpoint))]
            valid = all(p.get('available') for p in parts)
            drafted = sum(p['drafted_tokens'] for p in parts) if valid else None
            accepted = sum(p['accepted_tokens'] for p in parts) if valid else None
            receipt['speculation'] = {'available': valid, 'drafted_tokens': drafted,
                'accepted_tokens': accepted, 'acceptance_fraction': accepted / drafted if drafted else None,
                'segments': parts, 'basis': 'Original article and explicit downstream-only resumes; no startup probes.'}
        b.write(attempt / 'receipt.json', receipt)
    return receipt


def summarize(receipts, expected):
    """Execution coverage, machine validation and clinical review are distinct."""
    rows = list(receipts)
    stages = [r['fhir'] for r in rows if r.get('fhir')]
    exports = {}
    for name in ('uncoded', 'experimental-coded'):
        parts = [s.get('exports', {}).get(name) for s in stages]
        present = [p for p in parts if p is not None]
        exports[name] = {
            'article_exports': len(present),
            'patient_bundle_statuses': dict(sum((Counter(p['statuses']) for p in present), Counter())),
            'resource_counts': dict(sum((Counter(p['resource_counts']) for p in present), Counter())),
            'deferred_reasons': dict(sum((Counter(p['deferred_reasons']) for p in present), Counter())),
            'codings_attached': sum(p['codings_attached'] for p in present),
            'official_validator_articles': sum(bool(p.get('official_validator_run')) for p in present),
        }
    coding = [s.get('coding', {}) for s in stages]
    usage_rows = [s['usage'] for s in stages if 'usage' in s]
    usage = {}
    for key in ('model_calls', 'prompt_tokens', 'completion_tokens', 'reasoning_tokens'):
        values = [u.get(key) for u in usage_rows]
        usage[key] = sum(values) if values and all(v is not None for v in values) else None
    completed = sum(bool(s.get('complete')) for s in stages)
    return {
        'planned_articles': expected, 'stage_attempts': len(stages),
        'stage_complete_articles': completed, 'stage_missing_articles': expected - len(stages),
        'stage_statuses': dict(Counter(s['status'] for s in stages)),
        'complete': completed == expected, 'exports': exports,
        'coding_decisions': dict(sum((Counter(c.get('decisions', {})) for c in coding), Counter())),
        'coding_requests': sum(c.get('requests', 0) for c in coding),
        'coding_pending_requests': sum(len(c.get('pending_requests', [])) for c in coding),
        'coding_usage': usage, 'stage_seconds': b.stats([s['elapsed_seconds'] for s in stages]),
        'extraction_seconds': b.stats([r['extraction_seconds'] for r in rows if 'extraction_seconds' in r]),
        'ehr_seconds': b.stats([r['ehr_seconds'] for r in rows if 'ehr_seconds' in r]),
        'clinical_accuracy_verified': False, 'release_ready': False,
        'interpretation': 'Complete means stage execution coverage; schema validation does not establish '
            'correct patient attribution, terminology equivalence or clinical truth. Inspect source and coding audits.',
    }


def markdown(reports):
    lines = ['', '## Terminology and FHIR', '',
        'ICD-10-CM FY2027 is a fixed research normalization vocabulary. Default bundles remain uncoded; '
        'model codes appear only in the tagged experimental export. These are unreviewed research artifacts.', '',
        '| Arm | Stage complete / planned | Passed / quarantined coded bundles | Codes attached | Coding pending | Coding input / output tokens |',
        '|---|---:|---:|---:|---:|---:|']
    for name, report in reports.items():
        coded = report['exports']['experimental-coded']
        statuses = coded['patient_bundle_statuses']
        usage = report['coding_usage']
        lines.append(f'| {name} | {report["stage_complete_articles"]} / {report["planned_articles"]} | '
            f'{statuses.get("passed", 0)} / {statuses.get("quarantined", 0)} | {coded["codings_attached"]} | '
            f'{report["coding_pending_requests"]} | {usage["prompt_tokens"]} / {usage["completion_tokens"]} |')
    lines += ['', 'Article timing and token totals include coding. Stage-specific totals are in competition-fhir.json; '
        'original coding prompts, responses and reasoning are in each attempt’s fhir/packet/coding-calls ledger. '
        'A passed bundle here passed local schema/reference checks; official HL7 validation and clinical review are separate release gates.', '']
    return lines


def export_dataset(work, arm, article_ids):
    """One standard Bundle per NDJSON line; failures remain in the index."""
    folder = work / 'summary/fhir-dataset' / arm
    folder.mkdir(parents=True, exist_ok=True)
    manifest = {'format': 'beam-competition-fhir-dataset/1', 'arm': arm,
        'release_ready': False, 'clinical_accuracy_verified': False, 'articles': [], 'files': {}}
    streams = {name: (folder / (name + '.bundles.jsonl')).open('w')
               for name in ('uncoded', 'experimental-coded')}
    try:
        for aid in article_ids:
            attempt, receipt = b.completed_attempt(work / 'arms' / arm, aid)
            row = {'article_id': aid, 'status': 'pending', 'exports': {},
                   'extraction_nonempty_pipeline_valid': bool(receipt and receipt.get('nonempty_pipeline_valid'))}
            manifest['articles'].append(row)
            if not receipt:
                continue
            row['status'] = receipt.get('fhir', {}).get('status', 'stage_missing')
            row['attempt'] = str(attempt.relative_to(work))
            for name, stream in streams.items():
                report_path = attempt / 'fhir' / name / 'report.json'
                if not report_path.exists():
                    continue
                report = b.read(report_path)
                row['exports'][name] = {'report': str(report_path.relative_to(work)),
                    'statuses': report['statuses'], 'patients': []}
                for patient in report['patients']:
                    entry = {'patient_id': patient['patient_id'], 'status': patient['status']}
                    row['exports'][name]['patients'].append(entry)
                    if patient.get('bundle'):
                        bundle = report_path.parent / patient['bundle']
                        if b.sha(bundle) != patient['sha256']:
                            raise ValueError('FHIR output changed: ' + str(bundle))
                        entry.update(bundle=str(bundle.relative_to(work)), sha256=patient['sha256'])
                        if patient['status'] == 'passed':
                            stream.write(json.dumps(b.read(bundle), ensure_ascii=False) + '\n')
    finally:
        for stream in streams.values():
            stream.close()
    manifest['files'] = {name + '.bundles.jsonl': b.sha(folder / (name + '.bundles.jsonl')) for name in streams}
    manifest['policy'] = ('Only bundles passing the declared local validation are combined; quarantined/skipped/pending '
        'patients remain in this index. Local validation and extraction task validity do not establish clinical truth.')
    b.write(folder / 'manifest.json', manifest)
    return str(folder.relative_to(work))
