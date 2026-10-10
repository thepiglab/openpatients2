"""Draft, validate and sign figure EHR-relevance labels (separate overlay sidecar).

  figure_ehr_review.py ROOT --article ID --draft    # writes figure-ehr-label-drafts/ID.json
  figure_ehr_review.py ROOT --article ID            # validates figure-ehr-labels/ID.json
  figure_ehr_review.py ROOT --article ID --role agent|parent --reviewer NAME --notes NOTES

Needs `visual-submissions/ID.json`. Defaults are derived deterministically from
the reviewed visual record and cannot be edited; reviewers confirm or override
each final label (with override_reason) after viewing the pixels or the reviewed
visual record. The visual submission and its receipts are never modified.
"""
import argparse
import json
from pathlib import Path

from openpatients2.figure_ehr import FORMAT, contract_sha256, draft_labels, validate_labels
from sidecar_receipts import base_binding, load_inputs, present, sign, verify
from validate import digest

KIND = 'figure-ehr'
STATUS = 'figure_ehr_labels_reviewed'


def _visual(root, aid):
    path = Path(root) / 'visual-submissions' / f'{aid}.json'
    if not path.exists():
        raise ValueError('Figure EHR labels need the article visual submission first')
    return json.loads(path.read_text())


def binding(target, source, annotation, visual):
    out = base_binding(source, annotation, target, STATUS)
    out.update(visual_target_sha256=digest(visual),
               asset_sha256=digest([[f['figure_id'], sorted((a['url'], a['sha256']) for a in f.get('assets', []))]
                                    for f in visual['figures']]),
               contract_sha256=contract_sha256())
    return out


def write_draft(root, aid):
    source, _ = load_inputs(root, aid)
    draft = draft_labels(source, _visual(root, aid))
    folder = Path(root) / 'figure-ehr-label-drafts'; folder.mkdir(exist_ok=True)
    (folder / f'{aid}.json').write_text(json.dumps(draft, ensure_ascii=False, indent=2) + '\n')
    return draft


def check(root, aid):
    source, annotation = load_inputs(root, aid)
    visual = _visual(root, aid)
    target = json.loads((Path(root) / 'figure-ehr-labels' / f'{aid}.json').read_text())
    return target, source, annotation, visual, validate_labels(target, source, visual)


def sign_review(root, aid, *, role, reviewer, notes):
    from signing_guard import check_signature
    check_signature(reviewer, notes)   # refuse probes before validation/signing
    target, source, annotation, visual, report = check(root, aid)
    return sign(root, KIND, aid, binding(target, source, annotation, visual), report,
                role=role, reviewer=reviewer, notes=notes)


def reviewed_figure_ehr(root, aid):
    """(labels, [agent, parent]) when dual-signed and current; None if unsigned; raises if stale."""
    root = Path(root)
    if not (root / 'figure-ehr-labels' / f'{aid}.json').exists() or not all(present(root, KIND, aid)):
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
        print(json.dumps({'draft': f'figure-ehr-label-drafts/{args.article}.json', 'format': FORMAT,
                          'panels': [[f['figure_id'], p['panel'], p['default']['ehr_category'], p['default']['ehr_includable']]
                                     for f in draft['figures'] for p in f['panels']]}))
    elif args.role:
        receipt = sign_review(args.root, args.article, role=args.role, reviewer=args.reviewer, notes=args.notes)
        print(json.dumps({k: receipt[k] for k in ('role', 'reviewer', 'panels', 'ehr_includable_panels')}))
    else:
        print(json.dumps(check(args.root, args.article)[-1]))
