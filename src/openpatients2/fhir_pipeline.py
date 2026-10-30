"""Two-stage local terminology selection and deterministic FHIR R4 export.

op2 fhir prepare --input unified.jsonl --sources CAMPAIGN --output WORK
op2 fhir export --work WORK --output EXPORT
All frozen inputs are hashed. Exports use new directories; refinements create
new versions rather than overwriting source facts or previously emitted bundles.
"""
import argparse
import asyncio
from collections import Counter
from datetime import datetime, timezone
import hashlib
from importlib.resources import files
import json
from pathlib import Path
import re
import subprocess
from urllib.parse import urlsplit

from .data import read_jsonl, write_json
from .fhir_export import VERSION, compile_patient, fact_entries, stable_id, term
from .fhir_validation import validate_bundle, official_validate
from .provenance import json_digest
from .terminology import Decision, candidates, load_catalog, selected_coding, validate_decision
from .unified_record import UnifiedArticle


def sha(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def new_directory(path):
    path = Path(path)
    path.mkdir(parents=True, exist_ok=False)
    return path


def source_for(root, aid):
    if not re.fullmatch(r'PMC[0-9]+\.[0-9]+', aid):
        raise ValueError('FHIR export requires versioned PMC article IDs')
    for path in (root / 'inputs' / aid / 'source.jsonl', root / 'articles' / aid / 'source.json', root / (aid + '.json')):
        if path.exists():
            if path.suffix == '.jsonl':
                rows = list(read_jsonl(path))
                if len(rows) != 1:
                    raise ValueError('One primary source per article required')
                return rows[0]
            return json.loads(path.read_text())
    raise ValueError(f'Missing primary source packet for {aid}')


def prepare(input_path, sources, output, catalog_path=None):
    catalog = load_catalog(catalog_path)
    output = new_directory(output)
    inventory = {}; article_ids = set(); count = 0; facts = 0
    catalog_name = 'catalog.json.gz' if catalog_path and str(catalog_path).endswith('.gz') else 'catalog.json'
    catalog_digest = catalog.digest
    systems_by_task = {}
    for concept in catalog['concepts']:
        for task in concept['tasks']:
            systems_by_task.setdefault(task, set()).add(concept['system'])
    if catalog_name.endswith('.gz'):
        # Copy the pinned compressed release rather than serializing it once
        # per fact or recompressing 75k concepts for every article.
        (output / catalog_name).write_bytes(Path(catalog_path).read_bytes())
    else:
        write_json(output / catalog_name, catalog)
    inventory[catalog_name] = sha(output / catalog_name)
    with (output / 'articles.jsonl').open('w') as data, (output / 'requests.jsonl').open('w') as requests:
        for value in read_jsonl(input_path):
            article = UnifiedArticle.model_validate(value).model_dump()
            aid = article['article_id']
            if aid in article_ids:
                raise ValueError('Duplicate article in export input')
            article_ids.add(aid)
            pids = [p['patient_id'] for p in article['patients']]
            if len(set(pids)) != len(pids):
                raise ValueError('Duplicate patient in article')
            source = source_for(Path(sources), aid)
            if (source.get('article_id') != aid or source.get('text_sha256') != article['text_sha256']
                    or hashlib.sha256(source['text'].encode()).hexdigest() != article['text_sha256']):
                raise ValueError(f'Primary source hash mismatch: {aid}')
            if any(s['text'] not in source['text'] for s in source['segments']):
                raise ValueError('Source segments do not belong to the hashed article text')
            path = output / 'sources' / (aid + '.json')
            write_json(path, source); inventory[str(path.relative_to(output))] = sha(path)
            data.write(json.dumps(article, ensure_ascii=False) + '\n')
            for patient in article['patients']:
                count += 1
                seen = set()
                for entry in fact_entries(article, patient):
                    if entry['fact_key'] in seen:
                        continue
                    seen.add(entry['fact_key'])
                    systems = sorted(systems_by_task.get(entry['task'], set())) or [None]
                    for system in systems:
                        request = {'id': json_digest([entry['fact_key'], system]), 'fact_key': entry['fact_key'],
                            'article_id': aid, 'patient_id': patient['patient_id'], 'system': system,
                            'text_sha256': article['text_sha256'], 'task': entry['task'],
                            'collection': entry['collection'], 'fact': entry['fact'],
                            'catalog_sha256': catalog_digest,
                            'candidates': candidates(catalog, entry['task'], term(entry), system=system)}
                        requests.write(json.dumps(request, ensure_ascii=False) + '\n'); facts += 1
    for name in ('articles.jsonl', 'requests.jsonl'):
        inventory[name] = sha(output / name)
    manifest = {'format': VERSION, 'created_at': datetime.now(timezone.utc).isoformat(),
        'input_sha256': sha(input_path), 'articles': len(article_ids), 'patients': count,
        'terminology_requests': facts, 'files': inventory, 'catalog_sha256': catalog_digest,
        'policy': 'Candidate selection is not reviewed coding. Default export attaches only explicitly approved equivalent selections.'}
    write_json(output / 'manifest.json', manifest)
    return manifest


def check(work):
    manifest = json.loads((work / 'manifest.json').read_text())
    if manifest.get('format') != VERSION:
        raise ValueError('Not a supported frozen FHIR work packet')
    for name, digest in manifest['files'].items():
        path = work / name
        if not path.resolve().is_relative_to(work.resolve()) or sha(path) != digest:
            raise ValueError('FHIR work packet was modified: ' + name)
    return manifest


def approve(work, request_id, decision, reviewer):
    check(work)
    if not reviewer.strip():
        raise ValueError('Reviewer attribution is required')
    request = next((r for r in read_jsonl(work / 'requests.jsonl') if r['id'] == request_id), None)
    if request is None:
        raise ValueError('Unknown request ID')
    decision = validate_decision(request, decision)
    receipt = {'request_sha256': json_digest(request), 'decision': decision, 'review_status': 'approved',
               'reviewer': reviewer, 'reviewed_at': datetime.now(timezone.utc).isoformat()}
    path = work / 'decisions' / (request_id + '.json')
    if path.exists():
        write_json(work / 'decision-history' / (request_id + '-' + sha(path) + '.json'), json.loads(path.read_text()))
    write_json(path, receipt)
    return receipt


async def select(work, endpoint, manifest_path, *, max_requests=128, concurrency=4):
    """Use the same pinned extraction client: native template/stops/reasoning capture.

    Local endpoints only. Forward a remote GPU port explicitly if needed.
    Model decisions remain unreviewed and cannot self-approve.
    """
    check(work)
    url = urlsplit(endpoint)
    if (url.scheme not in {'http', 'https'} or url.hostname not in {'localhost', '127.0.0.1', '::1'}
            or url.username or url.password or url.query or url.fragment):
        raise ValueError('Use a loopback OpenAI-compatible endpoint (or an explicit SSH port forward)')
    if not 1 <= max_requests <= 8192 or not 1 <= concurrency <= 32:
        raise ValueError('Request/concurrency limits are out of range')
    from .pilot_extract import PilotRunner, PilotConfig
    manifest = json.loads(Path(manifest_path).read_text())
    config = PilotConfig.model_validate(manifest['extraction']).model_copy(update={'concurrency_per_endpoint': concurrency})
    identity = {'config': config.model_dump(), 'context': manifest['context']}
    identity_path = work / 'coder-config.json'
    if identity_path.exists() and json.loads(identity_path.read_text()) != identity:
        raise ValueError('Coder settings changed; create a separate FHIR packet')
    write_json(identity_path, identity)
    call_dir = work / 'coding-calls' / datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    runner = PilotRunner(config.model_dump(), call_dir, [endpoint], manifest['context'], 'targeted')
    runner.schemas['terminology_selection'] = Decision.model_json_schema()
    requests = []
    for request in read_jsonl(work / 'requests.jsonl'):
        path = work / 'decisions' / (request['id'] + '.json')
        if path.exists() or not request['candidates']:
            continue
        requests.append(request)
        if len(requests) >= max_requests:
            break
    prompt = files('openpatients2').joinpath('prompts/terminology_selection.md').read_text()
    async def one(request):
        messages = [{'role': 'system', 'content': prompt}, {'role': 'user', 'content':
            json.dumps(request, ensure_ascii=False) + '\nSCHEMA:\n' + json.dumps(Decision.model_json_schema())}]
        result = await runner.call('terminology_selection', messages, lambda v: validate_decision(request, v),
            {'request_id': request['id'], 'article_id': request['article_id']}, 0, [])
        if result['status'] == 'valid':
            write_json(work / 'decisions' / (request['id'] + '.json'), {
                'request_sha256': json_digest(request), 'decision': result['data'], 'review_status': 'model_unreviewed',
                'coder_sha256': json_digest(identity), 'call_directory': str(call_dir.relative_to(work))})
        return result['status']
    try:
        outcomes = await asyncio.gather(*(one(r) for r in requests))
    finally:
        await runner.close()
    return {'attempted': len(requests), 'statuses': dict(Counter(outcomes)),
            'decisions_are_reviewed': False, 'call_directory': str(call_dir)}


def export(work, output, *, allow_unreviewed_coding=False, validator_jar=None, require_official=False):
    manifest = check(work)
    if require_official and not validator_jar:
        raise ValueError('--require-official needs --validator-jar; structural checks alone are not full validation')
    output = new_directory(output)
    requests = {r['id']: r for r in read_jsonl(work / 'requests.jsonl')}
    codings, coding_audit = {}, []
    for key, request in requests.items():
        path = work / 'decisions' / (key + '.json')
        receipt = json.loads(path.read_text()) if path.exists() else None
        coding = selected_coding(request, receipt, allow_unreviewed=allow_unreviewed_coding)
        if coding:
            codings.setdefault(request['fact_key'], []).append(coding)
        coding_audit.append({'request_id': key, 'fact_key': request['fact_key'], 'coding': coding, 'decision': receipt,
                             'candidates': request['candidates']})
    write_json(output / 'terminology-audit.json', coding_audit)
    rows = []; types = Counter(); deferred = Counter(); attached = 0
    for article in read_jsonl(work / 'articles.jsonl'):
        source = json.loads((work / 'sources' / (article['article_id'] + '.json')).read_text())
        for patient in article['patients']:
            identity = {'article_id': article['article_id'], 'patient_id': patient['patient_id']}
            # R4 human research export does not quietly convert veterinary cases.
            if patient.get('species') != 'human' or patient.get('non_human_patient') is not False:
                rows.append({**identity, 'status': 'skipped', 'reason': 'nonhuman_or_unresolved_species'})
                continue
            bundle, mapping = compile_patient(article, patient, source, recorded=manifest['created_at'], codings=codings)
            if allow_unreviewed_coding:
                from .fhir_export import BASE
                tag = {'system': BASE + '/CodeSystem/dataset-status', 'code': 'model-coding-unreviewed',
                       'display': 'Experimental model-selected terminology; not clinically reviewed'}
                bundle.setdefault('meta', {}).setdefault('tag', []).append(tag)
                for entry in bundle['entry']:
                    entry['resource'].setdefault('meta', {}).setdefault('tag', []).append(dict(tag))
            validation = validate_bundle(bundle)
            path = output / ('bundles' if validation['passed'] else 'quarantine') / (bundle['id'] + '.json')
            write_json(path, bundle)
            if validator_jar and validation['passed']:
                try:
                    validation['official'] = official_validate(path, validator_jar,
                        output=output / (bundle['id'] + '.operation-outcome.json'))
                    validation['official_validator_run'] = True
                except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
                    validation['official'] = {'passed': False, 'error_type': type(exc).__name__,
                        'error': str(exc)[:2000], 'outcome': None}
            passed = validation['passed'] and (not validator_jar or validation.get('official', {}).get('passed', False))
            if not passed and path.parent.name != 'quarantine':
                quarantined = output / 'quarantine' / path.name
                quarantined.parent.mkdir(exist_ok=True)
                path.rename(quarantined); path = quarantined
            write_json(output / 'audits' / (bundle['id'] + '.json'), {'mapping': mapping, 'validation': validation})
            deferred.update(r['reason'] for r in mapping['facts'] if r['status'] == 'deferred')
            attached += sum(len(codings.get(r['fact_key'], [])) for r in mapping['facts'] if r['coding_attached'])
            types.update(e['resource']['resourceType'] for e in bundle['entry'])
            rows.append({**identity, 'status': 'passed' if passed else 'quarantined',
                'bundle': str(path.relative_to(output)), 'sha256': sha(path), 'validation': validation,
                'mapped_facts': sum(r['status'] == 'mapped' for r in mapping['facts']),
                'deferred_facts': sum(r['status'] == 'deferred' for r in mapping['facts'])})
    report = {'format': VERSION, 'source_manifest_sha256': sha(work / 'manifest.json'),
        'compiler_sources': {p: sha(Path(__file__).with_name(p)) for p in
            ('fhir_pipeline.py', 'fhir_export.py', 'fhir_validation.py', 'terminology.py')},
        'complete': len(rows) == manifest['patients'], 'patients': rows,
        'statuses': dict(Counter(r['status'] for r in rows)), 'resource_counts': dict(types),
        'deferred_reasons': dict(deferred), 'codings_attached': attached,
        'unreviewed_coding_allowed': allow_unreviewed_coding,
        'official_validator_required': require_official, 'clinical_accuracy_verified': False,
        'release_ready': False,
        'next_gate': 'Review source attribution, deferred facts and coding; run HL7 validator with the intended profiles/terminology before release.'}
    write_json(output / 'report.json', report)
    return report


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest='command', required=True)
    cmd = sub.add_parser('prepare')
    cmd.add_argument('--input', required=True); cmd.add_argument('--sources', required=True)
    cmd.add_argument('--output', required=True); cmd.add_argument('--catalog')
    cmd = sub.add_parser('select')
    cmd.add_argument('--work', type=Path, required=True); cmd.add_argument('--endpoint', required=True)
    cmd.add_argument('--manifest', required=True, help='Frozen arm manifest containing extraction config and context')
    cmd.add_argument('--max-requests', type=int, default=128); cmd.add_argument('--concurrency', type=int, default=4)
    cmd = sub.add_parser('approve')
    cmd.add_argument('--work', type=Path, required=True); cmd.add_argument('--request-id', required=True)
    cmd.add_argument('--decision', required=True, help='Reviewed Decision JSON file'); cmd.add_argument('--reviewer', required=True)
    cmd = sub.add_parser('export')
    cmd.add_argument('--work', type=Path, required=True); cmd.add_argument('--output', required=True)
    cmd.add_argument('--allow-unreviewed-coding', action='store_true')
    cmd.add_argument('--validator-jar'); cmd.add_argument('--require-official', action='store_true')
    args = p.parse_args(argv)
    if args.command == 'prepare':
        result = prepare(args.input, args.sources, args.output, args.catalog)
    elif args.command == 'select':
        result = asyncio.run(select(args.work, args.endpoint, args.manifest, max_requests=args.max_requests, concurrency=args.concurrency))
    elif args.command == 'approve':
        result = approve(args.work, args.request_id, json.loads(Path(args.decision).read_text()), args.reviewer)
    else:
        result = export(args.work, args.output, allow_unreviewed_coding=args.allow_unreviewed_coding,
                        validator_jar=args.validator_jar, require_official=args.require_official)
    print(json.dumps(result, indent=2, ensure_ascii=False))
    if args.command == 'export' and result['statuses'].get('quarantined'):
        raise SystemExit(1)


if __name__ == '__main__':
    main()
