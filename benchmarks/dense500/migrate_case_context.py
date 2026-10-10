"""Migrate per-patient case_context to the article-derived packet rule.

Rule (src/openpatients2/prompts/case_context.md, "Article-derived patient
packets"): every per-patient record uses case_kind=clinical_case (animal for a
nonhuman patient) and multiple_index_patients=false, even in multi-patient
articles. On each case_context fact this changes ONLY:
  fields.case_kind  multi_patient -> clinical_case | animal (other kinds untouched)
  fields.multiple_index_patients  anything but false (incl. null/absent) -> false
Nothing else in the annotation changes. Byte-identical drafts are kept identical.
For changed articles the schema target is regenerated into schema-drafts/ and the
existing schema-submissions/ edits are carried over: only the per-patient keys
that schema_review requires to equal the materialized target (case_context
section, provenance annotation hash, case_context state-index rows, quality,
validity flag) and the top-level annotation hash are replaced. A path diff
proves nothing else changed. Nothing is signed; receipts become stale.

  migrate_case_context.py                 # dry run over dense50 + dense500
  migrate_case_context.py --apply [--manifest-dir DIR] [--skip ID ...]
"""
import argparse
import copy
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import sys
import time

HERE = Path(__file__).resolve().parent
ROOTS = [HERE.parent / 'dense50', HERE]
QUIET_SECONDS = 15 * 60
STYLES = {'i2': lambda v: json.dumps(v, ensure_ascii=False, indent=2) + '\n',
          'i2n': lambda v: json.dumps(v, ensure_ascii=False, indent=2),
          'i1': lambda v: json.dumps(v, ensure_ascii=False, indent=1) + '\n'}
ALLOWED_SUBMISSION_PATHS = [re.compile(p) for p in (
    r'^/annotation_sha256$', r'^/source_gate_errors(/.*)?$',
    r'^/patients/\d+/sections/case_context(/.*)?$',
    r'^/patients/\d+/reference_provenance/annotation_sha256$',
    r'^/patients/\d+/patient_state_index/facts/\d+/value_as_(extracted|annotated)(/.*)?$',
    r'^/patients/\d+/quality/case_context(/.*)?$',
    r'^/patients/\d+/all_clinical_companion_tasks_valid$')]
ALLOWED_ANNOTATION_PATHS = [re.compile(r'^/patients/\d+/facts/\d+/fields/(case_kind|multiple_index_patients)$')]


def style_of(text):
    value = json.loads(text)
    return next((name for name, dump in STYLES.items() if dump(value) == text), None)


def diff_paths(a, b, path=''):
    if type(a) is not type(b):
        return [path or '/']
    if isinstance(a, dict):
        out = []
        for k in sorted(set(a) | set(b), key=str):
            if k not in a or k not in b:
                out.append(f'{path}/{k}')
            else:
                out += diff_paths(a[k], b[k], f'{path}/{k}')
        return out
    if isinstance(a, list):
        if len(a) != len(b):
            return [path or '/']
        return [p for i, (x, y) in enumerate(zip(a, b)) for p in diff_paths(x, y, f'{path}/{i}')]
    return [] if a == b else [path or '/']


def nonconforming(annotation):
    """[(patient_id, fact_id, old_fields_subset)] for case_context facts breaking the packet rule."""
    out = []
    for p in annotation['patients']:
        for f in p['facts']:
            if f['task'] != 'case_context':
                continue
            if f['fields'].get('case_kind') == 'multi_patient' or f['fields'].get('multiple_index_patients', None) is not False:
                out.append((p['patient_id'], f['fact_id'], {k: f['fields'].get(k, '<absent>') for k in ('case_kind', 'multiple_index_patients')}))
    return out


def migrate_annotation(annotation):
    new = copy.deepcopy(annotation); changes = []
    for p in new['patients']:
        for f in p['facts']:
            if f['task'] != 'case_context':
                continue
            old = {k: f['fields'].get(k, '<absent>') for k in ('case_kind', 'multiple_index_patients')}
            if f['fields'].get('case_kind') == 'multi_patient':
                f['fields']['case_kind'] = 'animal' if p.get('species') == 'nonhuman' else 'clinical_case'
            if f['fields'].get('multiple_index_patients', None) is not False:
                f['fields']['multiple_index_patients'] = False
            newer = {k: f['fields'].get(k) for k in ('case_kind', 'multiple_index_patients')}
            if old != newer:
                changes.append({'patient_id': p['patient_id'], 'fact_id': f['fact_id'], 'old': old, 'new': newer})
    return new, changes


def merge_submission(submission, regenerated):
    new = copy.deepcopy(submission)
    new['annotation_sha256'] = regenerated['annotation_sha256']
    new['source_gate_errors'] = copy.deepcopy(regenerated['source_gate_errors'])
    for got, want in zip(new['patients'], regenerated['patients']):
        if got['source']['patient_target']['patient_id'] != want['source']['patient_target']['patient_id']:
            raise ValueError('Patient order differs between submission and regenerated target')
        for key in ('sections', 'reference_provenance', 'patient_state_index', 'quality', 'all_clinical_companion_tasks_valid'):
            got[key] = copy.deepcopy(want[key])
    return new


def _mtime_recent(paths, now):
    return any(p.exists() and now - p.stat().st_mtime < QUIET_SECONDS for p in paths)


def stability(root, aid, promoted, annotation, digest, now):
    files = [root / f / f'{aid}.json' for f in ('annotations', 'drafts', 'reviews', 'schema-submissions', 'schema-drafts',
             'schema-agent-reviews', 'schema-parent-reviews', 'visual-submissions', 'visual-parent-reviews')]
    if _mtime_recent(files, now):
        return 'recent_activity_within_15_minutes'
    if promoted:
        review = json.loads((root / 'reviews' / f'{aid}.json').read_text()) if (root / 'reviews' / f'{aid}.json').exists() else None
        if not review or review.get('annotation_sha256') != digest(annotation):
            return 'parent_source_receipt_not_current'
        parent = root / 'schema-parent-reviews' / f'{aid}.json'
        if parent.exists():
            receipt = json.loads(parent.read_text())
            submission = root / 'schema-submissions' / f'{aid}.json'
            if (receipt.get('annotation_sha256') != digest(annotation) or not submission.exists()
                    or receipt.get('target_sha256') != digest(json.loads(submission.read_text()))):
                return 'parent_schema_receipt_not_current'
        else:
            return 'promoted_without_parent_schema_receipt'
    elif (root / 'reviews' / f'{aid}.json').exists() or (root / 'schema-parent-reviews' / f'{aid}.json').exists():
        return 'draft_with_parent_activity'
    return None


def stale_receipts(root, aid, promoted):
    """Which receipts bind the annotation hash (stale) vs only patient identity/targets (unchanged)."""
    out = []
    if promoted and (root / 'reviews' / f'{aid}.json').exists():
        out.append('reviews (annotation_sha256)')
    for role in ('agent', 'parent'):
        if (root / f'schema-{role}-reviews' / f'{aid}.json').exists():
            out.append(f'schema-{role} (annotation_sha256, target_sha256)')
    return out


def run(apply=False, skip=(), manifest_dir=None, roots=ROOTS, now=None):
    sys.path.insert(0, str(HERE))
    from validate import digest, validate
    from schema_targets import materialize
    from schema_review import validate_submission
    now = now or time.time()
    report = {'mode': 'apply' if apply else 'dry_run', 'articles': [], 'counts': {}}
    for root in roots:
        candidates = {}
        for folder in ('annotations', 'drafts'):
            for path in sorted((root / folder).glob('*.json')):
                aid = path.stem
                if folder == 'drafts' and (root / 'annotations' / path.name).exists():
                    continue
                candidates.setdefault(aid, (folder, path))
        for aid, (folder, path) in sorted(candidates.items()):
            text = path.read_text(encoding='utf-8'); annotation = json.loads(text)
            if not nonconforming(annotation):
                continue
            promoted = folder == 'annotations'
            entry = {'article_id': aid, 'root': root.name, 'folder': folder, 'promoted': promoted}
            new, changes = migrate_annotation(annotation)
            entry['changes'] = changes
            entry['changed_annotation_paths'] = diff_paths(annotation, new)
            assert all(any(r.match(p) for r in ALLOWED_ANNOTATION_PATHS) for p in entry['changed_annotation_paths'])
            entry['receipts_going_stale'] = stale_receipts(root, aid, promoted)
            entry['receipts_unaffected'] = ['visual/supplement (bind patient identity + their own targets)',
                                            'figure-ehr, figure-privacy, supplement-manifest, measurement-series (identity only)']
            reason = 'listed_in_flight' if aid in skip else stability(root, aid, promoted, annotation, digest, now)
            source = json.loads((root / 'sources' / f'{aid}.json').read_text())
            style = style_of(text)
            if reason is None and style is None:
                reason = 'unrecognised_json_style'
            errors = validate(new, source)
            if errors:
                reason = reason or 'migrated_annotation_invalid'; entry['validation_errors'] = errors[:5]
            draft_path = root / 'drafts' / f'{aid}.json'
            entry['draft_identical'] = promoted and draft_path.exists() and draft_path.read_bytes() == path.read_bytes()
            sub_path = root / 'schema-submissions' / f'{aid}.json'
            try:
                regenerated = materialize(new, source)
                entry['regenerated_source_gate_errors'] = regenerated['source_gate_errors']
                if sub_path.exists():
                    sub_text = sub_path.read_text(encoding='utf-8'); submission = json.loads(sub_text)
                    merged = merge_submission(submission, regenerated)
                    paths = diff_paths(submission, merged)
                    bad = [p for p in paths if not any(r.match(p) for r in ALLOWED_SUBMISSION_PATHS)]
                    entry['changed_submission_paths'] = paths
                    if bad:
                        reason = reason or 'submission_diff_outside_case_context'; entry['unexpected_paths'] = bad[:10]
                    else:
                        validate_submission(merged, new, source)
                    entry['submission_case_context_equal_to_regenerated'] = all(
                        g['sections']['case_context'] == w['sections']['case_context']
                        for g, w in zip(merged['patients'], regenerated['patients']))
            except (ValueError, KeyError) as exc:
                reason = reason or 'schema_regeneration_failed'; entry['schema_error'] = str(exc)[:400]
            entry['skipped_reason'] = reason
            if apply and reason is None:
                path.write_text(STYLES[style](new), encoding='utf-8')
                if entry['draft_identical']:
                    draft_path.write_bytes(path.read_bytes())
                out = root / 'schema-drafts' / f'{aid}.json'; out.parent.mkdir(exist_ok=True)
                out.write_text(json.dumps(regenerated, ensure_ascii=False, indent=2) + '\n')
                if sub_path.exists():
                    sub_style = style_of(sub_text) or 'i2'
                    sub_path.write_text(STYLES[sub_style](merged), encoding='utf-8')
                entry['applied'] = True
            report['articles'].append(entry)
            if manifest_dir:
                Path(manifest_dir).mkdir(parents=True, exist_ok=True)
                (Path(manifest_dir) / f'{aid}.json').write_text(json.dumps(entry, ensure_ascii=False, indent=2) + '\n')
    rows = report['articles']
    report['counts'] = {'nonconforming_articles': len(rows),
                        'promoted': sum(r['promoted'] for r in rows), 'drafts': sum(not r['promoted'] for r in rows),
                        'eligible': sum(r['skipped_reason'] is None for r in rows),
                        'applied': sum(bool(r.get('applied')) for r in rows),
                        'skipped': {k: sum(r['skipped_reason'] == k for r in rows) for k in {r['skipped_reason'] for r in rows} if k},
                        'facts_changed': sum(len(r['changes']) for r in rows)}
    report['generated_at'] = datetime.now(timezone.utc).isoformat()
    return report


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('--apply', action='store_true'); p.add_argument('--skip', nargs='*', default=[])
    p.add_argument('--manifest-dir')
    args = p.parse_args()
    result = run(apply=args.apply, skip=set(args.skip), manifest_dir=args.manifest_dir)
    print(json.dumps({'counts': result['counts'], 'articles': [{k: r.get(k) for k in ('article_id', 'folder', 'skipped_reason', 'applied')}
                                                               for r in result['articles']]}, indent=1))
