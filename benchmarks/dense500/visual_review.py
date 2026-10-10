"""Bind actual figure inspection to image bytes, source text and patient roster.

Validation is not inspection. Call --role only after personally viewing every
listed asset and reading every submitted description against image and article.
"""
import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

from openpatients2.figure_visuals import validate_joint
from validate import digest


def identity_digest(annotation):
    return digest([{k:p[k] for k in ('patient_id','label','species','identity_evidence')} for p in annotation['patients']])


def validate_submission(target, source, annotation, assets, *, verify_files=True):
    aid=source['article_id']
    if target['article_id']!=aid or target['source_text_sha256']!=source['text_sha256']:
        raise ValueError('Visual target has stale/wrong article source')
    if target['format']!='dense-figure-reference/1':raise ValueError('Unknown visual format')
    expected={f['figure_key'] for f in source.get('figures',[])}
    if len(target['figures'])!=len(expected) or {f['figure_id'] for f in target['figures']}!=expected:
        raise ValueError('Every source figure must be represented exactly once')
    if assets['source_text_sha256']!=source['text_sha256']:raise ValueError('Stale assets manifest')
    manifest={f['figure_id']:f for f in assets['figures']}
    roster={'patients':[{'patient_id':p['patient_id']} for p in annotation['patients']]}
    for row in target['figures']:
        declared=manifest[row['figure_id']]
        if row['status']=='unavailable':
            if declared['assets'] or row.get('analysis') is not None or not row.get('limitations'):
                raise ValueError('Available assets need inspection; unavailable figures cannot carry invented visual analysis')
            continue
        if row['status']!='inspected' or not declared['assets']:
            raise ValueError('Figure inspection requires available image assets')
        actual={(a['url'],a['sha256']) for a in declared['assets']}
        if {(a['url'],a['sha256']) for a in row['assets']}!=actual:
            raise ValueError('Inspection must bind all declared image assets')
        for asset in declared['assets']:
            if verify_files and hashlib.sha256(Path(asset['path']).read_bytes()).hexdigest()!=asset['sha256']:
                raise ValueError('Image bytes changed after acquisition')
        validate_joint(row['analysis'],source,roster,row['figure_id'])
        assignments=row['analysis']['attribution']['assignments']
        if not any(a['panel'] is None for a in assignments):
            if {a['panel'] for a in assignments}!={p['panel'] for p in row['analysis']['visual']['panels']}:
                raise ValueError('Every visual panel needs attribution or an explicit unresolved assignment')
    return {'figures':len(expected),'inspected_figures':sum(r['status']=='inspected' for r in target['figures']),
        'unavailable_figures':sum(r['status']=='unavailable' for r in target['figures']),
        'pixel_accuracy_measured':False}


def load_inputs(root,aid):
    def read(folder):return json.loads((root/folder/f'{aid}.json').read_text())
    annotation=read('annotations' if (root/'annotations'/f'{aid}.json').exists() else 'drafts')
    return read('visual-submissions'),read('sources'),annotation,read('figure-assets')


def reviewed_visual_target(root,aid):
    if not (root/'visual-parent-reviews'/f'{aid}.json').exists():return None
    target,source,annotation,assets=load_inputs(root,aid)
    # Receipts bind previously inspected bytes. Completed cache assets may have
    # been deleted to honor the disk cap; signing itself always checks the files.
    validate_submission(target,source,annotation,assets,verify_files=False)
    receipts=[]
    for role in ('agent','parent'):
        receipt=json.loads((root/f'visual-{role}-reviews'/f'{aid}.json').read_text())
        for key,value in [('role',role),('article_id',aid),('status','source_and_pixels_reviewed'),
            ('source_text_sha256',source['text_sha256']),('target_sha256',digest(target)),
            ('patient_identity_sha256',identity_digest(annotation)),
            ('patient_ids',sorted(p['patient_id'] for p in annotation['patients']))]:
            if receipt.get(key)!=value:raise ValueError(f'{aid}: stale visual {role} receipt {key}')
        from signing_guard import receipt_identity_errors
        if receipt_identity_errors(receipt):raise ValueError(f'{aid}: visual {role} receipt has a placeholder reviewer/notes: {receipt_identity_errors(receipt)}')
        receipts.append(receipt)
    return target,receipts


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('root',type=Path);p.add_argument('--article',required=True)
    p.add_argument('--role',choices=['agent','parent']);p.add_argument('--reviewer');p.add_argument('--notes')
    args=p.parse_args()
    if args.role:
        from signing_guard import check_signature
        check_signature(args.reviewer,args.notes)   # refuse probes before anything is read or written
    target,source,annotation,assets=load_inputs(args.root,args.article)
    report=validate_submission(target,source,annotation,assets)
    if args.role:
        if not args.reviewer or not args.notes:raise ValueError('Actual inspection notes and reviewer required')
        receipt={'role':args.role,'reviewer':args.reviewer,'article_id':args.article,
            'status':'source_and_pixels_reviewed','source_text_sha256':source['text_sha256'],
            'target_sha256':digest(target),'patient_ids':sorted(p['patient_id'] for p in annotation['patients']),
            'patient_identity_sha256':identity_digest(annotation),
            'reviewed_at':datetime.now(timezone.utc).isoformat(),'inspection_notes':args.notes,
            'physician_adjudicated':False,**report}
        folder=args.root/f'visual-{args.role}-reviews';folder.mkdir(exist_ok=True)
        (folder/f'{args.article}.json').write_text(json.dumps(receipt,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps(report))
