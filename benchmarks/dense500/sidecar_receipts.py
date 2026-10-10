"""Shared agent/parent receipts for the additive EHR sidecars.

Same style as the existing visual/supplement receipts: a receipt binds the
source, sidecar, asset and patient-identity hashes plus the sidecar contract
fingerprint, and becomes stale when any of them changes. Signing asserts actual
inspection; validation success is never substituted for review.
"""
from datetime import datetime, timezone
import json
from pathlib import Path

from validate import digest
from visual_review import identity_digest

KINDS = ('supplement-manifest', 'figure-ehr', 'measurement-series', 'figure-privacy')


def receipt_path(root, kind, role, aid):
    if kind not in KINDS or role not in ('agent', 'parent'):
        raise ValueError('Unknown sidecar receipt kind/role')
    return Path(root) / f'{kind}-{role}-reviews' / f'{aid}.json'


def base_binding(source, annotation, target, status):
    return {'article_id': source['article_id'], 'status': status, 'source_text_sha256': source['text_sha256'],
            'target_sha256': digest(target), 'patient_identity_sha256': identity_digest(annotation),
            'patient_ids': sorted(p['patient_id'] for p in annotation['patients'])}


def sign(root, kind, aid, binding, report, *, role, reviewer, notes):
    if role not in ('agent', 'parent') or not reviewer or not notes:
        raise ValueError('Review role, reviewer and actual inspection notes required')
    from signing_guard import check_signature
    check_signature(reviewer, notes)
    other = receipt_path(root, kind, 'parent' if role == 'agent' else 'agent', aid)
    if other.exists() and json.loads(other.read_text()).get('reviewer') == reviewer:
        raise ValueError('Sidecar review requires independent agent and parent reviewers')
    receipt = {'role': role, 'reviewer': reviewer, **binding,
               'reviewed_at': datetime.now(timezone.utc).isoformat(), 'inspection_notes': notes,
               'physician_adjudicated': False, **report}
    path = receipt_path(root, kind, role, aid)
    path.parent.mkdir(exist_ok=True)
    temporary = path.with_suffix('.json.tmp')
    temporary.write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + '\n')
    temporary.replace(path)
    return receipt


def present(root, kind, aid):
    return [receipt_path(root, kind, role, aid).exists() for role in ('agent', 'parent')]


def verify(root, kind, aid, binding, report):
    """Return [agent, parent] receipts, None when either is missing; raise when stale."""
    if not all(present(root, kind, aid)):
        return None
    receipts = []
    for role in ('agent', 'parent'):
        receipt = json.loads(receipt_path(root, kind, role, aid).read_text())
        for key, value in {'role': role, **binding, **report}.items():
            if receipt.get(key) != value:
                raise ValueError(f'{aid}: stale {kind} {role} receipt {key}')
        if not receipt.get('reviewer') or not receipt.get('inspection_notes') or receipt.get('physician_adjudicated') is not False:
            raise ValueError(f'{aid}: {kind} receipt lacks actual inspection metadata')
        from signing_guard import receipt_identity_errors
        if receipt_identity_errors(receipt):
            raise ValueError(f'{aid}: {kind} {role} receipt has a placeholder reviewer/notes')
        receipts.append(receipt)
    if receipts[0]['reviewer'] == receipts[1]['reviewer']:
        raise ValueError('Sidecar review requires independent agent and parent reviewers')
    return receipts


def load_inputs(root, aid):
    root = Path(root)
    source = json.loads((root / 'sources' / f'{aid}.json').read_text())
    folder = 'annotations' if (root / 'annotations' / f'{aid}.json').exists() else 'drafts'
    annotation = json.loads((root / folder / f'{aid}.json').read_text())
    return source, annotation
