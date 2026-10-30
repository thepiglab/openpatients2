"""Offline R4 structure/reference checks; optional official HL7 validator gate.

JSON Schema is NOT a substitute for FHIRPath invariants, terminology validation,
implementation-guide validation, or clinical review. Report the levels separately.
"""
from functools import lru_cache
import gzip
import hashlib
from importlib.resources import files
import json
from pathlib import Path
import subprocess

from jsonschema import Draft6Validator

R4_SCHEMA_SHA256 = '2230406893b4cf002a4ee1e5e2bbeca22ac5d2d4931b3e9ef7b9594bbc376a01'


@lru_cache(maxsize=1)
def validator():
    raw = gzip.decompress(files('openpatients2').joinpath('rules/fhir-r4.schema.json.gz').read_bytes())
    if hashlib.sha256(raw).hexdigest() != R4_SCHEMA_SHA256:
        raise ValueError('Packaged HL7 R4 schema checksum mismatch')
    schema = json.loads(raw)
    return Draft6Validator({'$schema': schema['$schema'], '$ref': '#/definitions/Bundle',
                            'definitions': schema['definitions']})


def references(value):
    if isinstance(value, dict):
        if isinstance(value.get('reference'), str):
            yield value['reference']
        for child in value.values():
            yield from references(child)
    elif isinstance(value, list):
        for child in value:
            yield from references(child)


def validate_bundle(bundle):
    errors = [f'/{"/".join(map(str, e.absolute_path))}: {e.message[:300]}'
              for e in validator().iter_errors(bundle)]
    entries = bundle.get('entry', [])
    urls = [e.get('fullUrl') for e in entries]
    if len(set(urls)) != len(urls) or any(not isinstance(u, str) or not u.startswith('urn:uuid:') for u in urls):
        errors.append('Bundle fullUrls must be unique local UUID URNs')
    resources = {e.get('fullUrl'): e.get('resource', {}) for e in entries}
    patients = {u for u, r in resources.items() if r.get('resourceType') == 'Patient'}
    if len(patients) != 1:
        errors.append('Export bundle must contain exactly one patient')
    required = {'Observation': ['status'], 'Procedure': ['status'],
        'MedicationStatement': ['status', 'medicationCodeableConcept'],
        'MedicationRequest': ['status', 'intent', 'medicationCodeableConcept'],
        'ServiceRequest': ['status', 'intent'], 'CarePlan': ['status', 'intent'],
        'DocumentReference': ['status'], 'Provenance': ['recorded'],
        'FamilyMemberHistory': ['status'], 'DiagnosticReport': ['status']}
    statuses = {'Procedure': {'preparation', 'in-progress', 'not-done', 'on-hold', 'stopped', 'completed', 'entered-in-error', 'unknown'},
        'MedicationStatement': {'active', 'completed', 'entered-in-error', 'intended', 'stopped', 'on-hold', 'unknown', 'not-taken'},
        'MedicationRequest': {'active', 'on-hold', 'cancelled', 'completed', 'entered-in-error', 'stopped', 'draft', 'unknown'},
        'ServiceRequest': {'draft', 'active', 'on-hold', 'revoked', 'completed', 'entered-in-error', 'unknown'},
        'CarePlan': {'draft', 'active', 'on-hold', 'revoked', 'completed', 'entered-in-error', 'unknown'}}
    for url, r in resources.items():
        kind = r.get('resourceType')
        if url != 'urn:uuid:' + r.get('id', ''):
            errors.append(f'{kind}: fullUrl/id mismatch')
        for ref in references(r):
            if ref not in resources:
                errors.append(f'{kind}: unresolved reference {ref}')
        for key in required.get(kind, []):
            if not r.get(key):
                errors.append(f'{kind}: missing {key}')
        if kind in statuses and r.get('status') not in statuses[kind]:
            errors.append(f'{kind}: invalid status')
        for key in ('subject', 'patient'):
            if key in r and r[key].get('reference') not in patients:
                errors.append(f'{kind}: {key} does not reference this patient')
        if kind == 'Observation':
            values = [k for k in r if k.startswith('value')]
            if len(values) > 1 or (values and 'dataAbsentReason' in r):
                errors.append('Observation: conflicting value choices or dataAbsentReason')
        if kind == 'Condition' and 'abatementDateTime' in r:
            status = {c['code'] for c in r.get('clinicalStatus', {}).get('coding', [])}
            if not status & {'inactive', 'resolved', 'remission'}:
                errors.append('Condition: abatement requires inactive/resolved/remission status')
    return {'passed': not errors, 'errors': errors, 'fhir_version': '4.0.1',
            'schema_sha256': R4_SCHEMA_SHA256, 'official_validator_run': False,
            'scope': 'HL7 JSON Schema plus exporter reference/choice/status invariants; not full FHIR conformance or clinical validation'}


def official_validate(bundle_path, jar, *, output, terminology_server=None, timeout=300):
    """Run a locally supplied validator JAR. No clinical data leaves by default.

    -tx n/a disables remote terminology calls. A server is an explicit opt-in.
    Persist command, jar hash and OperationOutcome; a zero exit code alone is
    insufficient. Missing/unknown outcomes never become successful validation.
    """
    jar, output = Path(jar).resolve(), Path(output).resolve()
    if output.exists():
        raise ValueError('Choose a new official validation output path')
    cmd = ['java', '-Xmx2g', '-jar', str(jar), str(Path(bundle_path).resolve()), '-version', '4.0.1',
           '-tx', terminology_server or 'n/a', '-ig',
           str(files('openpatients2').joinpath('rules/fhir-extensions')), '-output', str(output)]
    result = subprocess.run(cmd, text=True, capture_output=True, timeout=timeout, check=False)
    outcome = json.loads(output.read_text()) if output.exists() else None
    issues = outcome.get('issue', []) if isinstance(outcome, dict) else []
    valid_issues = isinstance(issues, list) and bool(issues) and all(
        isinstance(i, dict) and i.get('severity') in {'information', 'warning', 'error', 'fatal'} for i in issues)
    return {'command': cmd, 'validator_sha256': hashlib.sha256(jar.read_bytes()).hexdigest(),
        'returncode': result.returncode, 'stdout': result.stdout, 'stderr': result.stderr,
        'outcome': outcome, 'official_validator_run': True,
        'terminology_validation': 'server' if terminology_server else 'disabled',
        'passed': result.returncode == 0 and isinstance(outcome, dict)
                  and outcome.get('resourceType') == 'OperationOutcome' and valid_issues
                  and not any(i.get('severity') in {'error', 'fatal'} for i in issues)}
