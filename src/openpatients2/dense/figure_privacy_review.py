"""Draft, validate and sign per-panel figure privacy/sensitivity flags (flag only).

  op2 dense figure_privacy_review ROOT --article ID --draft    # writes articles/ID/figure_privacy_flags.draft.json
  op2 dense figure_privacy_review ROOT --article ID            # validates articles/ID/figure_privacy_flags.json
  op2 dense figure_privacy_review ROOT --article ID --role agent|parent --reviewer NAME --notes NOTES

Separate from figure_ehr_labels.json (whose receipts stay valid); bound to the visual
submission. Drafts are pre-filled from reviewer-written figure-ehr rationales,
the visual record and known reports; every panel must be confirmed on pixels.
Flags never change EHR includability. Never transcribe identifier values.
"""
import argparse
import json
from pathlib import Path

from openpatients2.figure_privacy import FORMAT, contract_sha256, draft_flags, validate_flags
from openpatients2 import dense_store as store
from openpatients2.dense.sidecar_receipts import base_binding, load_inputs, present, sign, verify
from openpatients2.dense.validate import digest

KIND = 'figure-privacy'
STATUS = 'figure_privacy_flags_reviewed'


def _visual(root, aid):
    path = store.path(root, 'visual-submissions', aid)
    if not path.exists():
        raise ValueError('Privacy flags need the article visual submission first')
    return json.loads(path.read_text())


def binding(target, source, annotation, visual):
    out = base_binding(source, annotation, target, STATUS)
    out.update(visual_target_sha256=digest(visual),
               asset_sha256=digest([[f['figure_id'], sorted((a['url'], a['sha256']) for a in f.get('assets', []))]
                                    for f in visual['figures']]),
               contract_sha256=contract_sha256())
    return out


def write_draft(root, aid):
    root = Path(root)
    source, _ = load_inputs(root, aid)
    labels_path = store.path(root, 'figure-ehr-labels', aid)
    labels = json.loads(labels_path.read_text()) if labels_path.exists() else None
    draft = draft_flags(source, _visual(root, aid), labels)
    out = store.path(root, 'figure-privacy-flag-drafts', aid); out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(draft, ensure_ascii=False, indent=2) + '\n')
    return draft


def check(root, aid):
    source, annotation = load_inputs(root, aid)
    visual = _visual(root, aid)
    target = json.loads(store.path(root, 'figure-privacy-flags', aid).read_text())
    return target, source, annotation, visual, validate_flags(target, source, visual)


def sign_review(root, aid, *, role, reviewer, notes):
    from openpatients2.dense.signing_guard import check_signature
    check_signature(reviewer, notes)   # refuse probes before validation/signing
    target, source, annotation, visual, report = check(root, aid)
    return sign(root, KIND, aid, binding(target, source, annotation, visual), report,
                role=role, reviewer=reviewer, notes=notes)


def reviewed_privacy_flags(root, aid):
    """(flags, [agent, parent]) when dual-signed and current; None if unsigned; raises if stale."""
    root = Path(root)
    if not store.path(root, 'figure-privacy-flags', aid).exists() or not all(present(root, KIND, aid)):
        return None
    target, source, annotation, visual, report = check(root, aid)
    receipts = verify(root, KIND, aid, binding(target, source, annotation, visual), report)
    return (target, receipts) if receipts else None


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('root', type=Path); p.add_argument('--article', required=True)
    p.add_argument('--draft', action='store_true'); p.add_argument('--role', choices=['agent', 'parent'])
    p.add_argument('--reviewer'); p.add_argument('--notes'); args = p.parse_args()
    if args.draft:
        draft = write_draft(args.root, args.article)
        print(json.dumps({'draft': str(store.path(args.root, 'figure-privacy-flag-drafts', args.article)), 'format': FORMAT,
                          'prefilled': [[f['figure_id'], q['panel'], q['burned_in_identifiers'], q['identifiable_face'],
                                         q['sensitive_anatomy']] for f in draft['figures'] for q in f['panels']
                                        if q['burned_in_identifiers'] != 'none' or q['identifiable_face'] or q['sensitive_anatomy']]}))
    elif args.role:
        receipt = sign_review(args.root, args.article, role=args.role, reviewer=args.reviewer, notes=args.notes)
        print(json.dumps({k: receipt[k] for k in ('role', 'reviewer', 'panels')}))
    else:
        print(json.dumps(check(args.root, args.article)[-1]))
