"""Draft, validate and sign the per-article supplement content manifest.

  op2 dense supplement_manifest_review ROOT --article ID --draft    # writes articles/ID/supplements/manifest.draft.json
  op2 dense supplement_manifest_review ROOT --article ID            # validates articles/ID/supplements/manifest.json
  op2 dense supplement_manifest_review ROOT --article ID --role agent|parent --reviewer NAME --notes NOTES

Requires `op2 dense supplement_assets acquire` first. Machine blocks are copied from the
acquisition record and cannot be edited; reviewers complete every review block
after opening the cached file or peek. Signing re-hashes cached bytes; signed
manifests survive later cache cleanup. Articles without supplements need none.
"""
import argparse
import hashlib
import json
from pathlib import Path

from openpatients2.supplement_manifest import (FORMAT, contract_sha256, draft_manifest, validate_manifest)
from openpatients2.dense.sidecar_receipts import base_binding, load_inputs, sign, verify
from openpatients2.dense.validate import digest
from openpatients2 import dense_store as store

KIND = 'supplement-manifest'
STATUS = 'supplement_manifest_reviewed'


def _acquisition(root, aid):
    path = store.path(root, 'supplement-assets', aid)
    if not path.exists():
        raise ValueError('Run `op2 dense supplement_assets acquire` for this article first')
    return json.loads(path.read_text())


def _target(root, aid):
    return json.loads(store.path(root, 'supplement-manifests', aid).read_text())


def verify_cached_files(acquisition):
    for row in acquisition['files']:
        for path_key, hash_key in (('path', 'sha256'), ('sample_path', 'sample_sha256'), ('tail_path', 'tail_sha256')):
            if row.get(path_key):
                data = Path(row[path_key]).read_bytes()
                if hashlib.sha256(data).hexdigest() != row[hash_key]:
                    raise ValueError(f'{row["supplement_id"]}: cached supplement bytes changed after acquisition')


def binding(target, source, annotation, acquisition):
    out = base_binding(source, annotation, target, STATUS)
    out.update(source_supplements_sha256=digest(source.get('supplements', [])),
               asset_sha256=digest(sorted([[r['supplement_id'], r.get('url'), r.get('sha256'), r.get('sample_sha256'),
                                            r.get('bytes')] for r in acquisition['files']], key=str)),
               contract_sha256=contract_sha256())
    return out


def write_draft(root, aid):
    source, _ = load_inputs(root, aid)
    draft = draft_manifest(source, _acquisition(root, aid))
    out = store.path(root, 'supplement-manifest-drafts', aid); out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(draft, ensure_ascii=False, indent=2) + '\n')
    return draft


def check(root, aid, *, verify_files=True):
    source, annotation = load_inputs(root, aid)
    acquisition = _acquisition(root, aid)
    target = _target(root, aid)
    if verify_files:
        verify_cached_files(acquisition)
    return target, source, annotation, acquisition, validate_manifest(target, source, annotation, acquisition)


def sign_review(root, aid, *, role, reviewer, notes):
    from openpatients2.dense.signing_guard import check_signature
    check_signature(reviewer, notes)   # refuse probes before validation/signing
    target, source, annotation, acquisition, report = check(root, aid, verify_files=True)
    return sign(root, KIND, aid, binding(target, source, annotation, acquisition), report,
                role=role, reviewer=reviewer, notes=notes)


def reviewed_manifest(root, aid):
    """(manifest, [agent, parent]) when dual-signed and current; None if unsigned; raises if stale."""
    root = Path(root)
    if not store.path(root, 'supplement-manifests', aid).exists():
        return None
    from openpatients2.dense.sidecar_receipts import present
    if not all(present(root, KIND, aid)):
        return None
    target, source, annotation, acquisition, report = check(root, aid, verify_files=False)
    receipts = verify(root, KIND, aid, binding(target, source, annotation, acquisition), report)
    return (target, receipts) if receipts else None


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('root', type=Path); p.add_argument('--article', required=True)
    p.add_argument('--draft', action='store_true'); p.add_argument('--role', choices=['agent', 'parent'])
    p.add_argument('--reviewer'); p.add_argument('--notes'); args = p.parse_args()
    if args.draft:
        draft = write_draft(args.root, args.article)
        print(json.dumps({'draft': str(store.path(args.root, 'supplement-manifest-drafts', args.article)), 'files': len(draft['files']),
                          'format': FORMAT}))
    elif args.role:
        receipt = sign_review(args.root, args.article, role=args.role, reviewer=args.reviewer, notes=args.notes)
        print(json.dumps({k: receipt[k] for k in ('role', 'reviewer', 'files', 'ehr_includable_files')}))
    else:
        print(json.dumps(check(args.root, args.article)[-1]))
