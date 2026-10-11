"""Draft, validate and sign dense numeric measurement series.

  measurement_series_review.py ROOT --article ID --draft   # writes articles/ID/measurement_series.draft.json
  measurement_series_review.py ROOT --article ID           # validates articles/ID/measurement_series.json
  measurement_series_review.py ROOT --article ID --role agent|parent --reviewer NAME --notes NOTES

The draft lists every literal main-article table cell (and CSV/TSV/XLSX cells of
downloaded supplements, capped) plus DRAFT candidate row series. Reviewers keep
only real series, assign patients with literal evidence, kinds, units and time
offsets, then copy the file to `articles/ID/measurement_series.json`. Parsed numbers are
recomputed from raw cells; they can never be typed in. Supplement transcription
cells must cite dual-reviewed `dense-supplemental-clinical-reference/1` rows.
"""
import argparse
import json
from pathlib import Path

from openpatients2.measurement_series import FORMAT, contract_sha256, draft_series, validate_series
from openpatients2.supplement_peek import read_delimited, read_xlsx
from sidecar_receipts import base_binding, load_inputs, present, sign, verify
from validate import digest
import store

KIND = 'measurement-series'
STATUS = 'measurement_series_reviewed'
MAX_SUPPLEMENT_CELLS = 5000


def _json(path):
    return json.loads(path.read_text()) if path.exists() else None


def reviewed_transcriptions(root, aid):
    from supplement_review import reviewed_supplement_targets
    out = {}
    from openpatients2.document_text import reviewed_text
    for target, _ in reviewed_supplement_targets(root, aid):
        out.update(reviewed_text(target))   # clinical transcriptions (+ extracted units of text documents)
    return out


def _acquired(root, aid):
    acquisition = _json(store.path(root, 'supplement-assets', aid)) or {'files': []}
    return {(r['supplement_id'], r.get('url')): r for r in acquisition['files']}


def file_reader(root, aid):
    acquired = _acquired(root, aid)
    def read(cell):
        row = acquired.get((cell.supplement_id, cell.asset_url))
        if not row or not row.get('path'):
            return False
        path = Path(row['path'])  # signing requires the cached bytes
        kind = row.get('detected_type')
        if kind in ('csv', 'tsv'):
            rows, _ = read_delimited(path, kind, max_rows=cell.row_index + 1, max_columns=10_000, max_chars=10_000)
        elif kind == 'xlsx':
            sheets = [s for s in read_xlsx(path, max_rows=cell.row_index + 1, max_columns=10_000, max_chars=10_000)
                      if s['name'] == cell.sheet]
            rows = sheets[0]['rows'] if sheets else []
        else:
            return False
        return (cell.row_index < len(rows) and cell.column_index is not None and
                cell.column_index < len(rows[cell.row_index]) and rows[cell.row_index][cell.column_index].strip() == cell.raw_text)
    return read


def supplement_file_cells(root, aid):
    cells = []
    for (sid, url), row in _acquired(root, aid).items():
        if row.get('download_status') != 'downloaded' or row.get('detected_type') not in ('csv', 'tsv', 'xlsx'):
            continue
        if row['detected_type'] == 'xlsx':
            tables = [(s['name'], s['rows']) for s in read_xlsx(row['path'], max_rows=500, max_columns=200, max_chars=10_000)]
        else:
            tables = [(None, read_delimited(row['path'], row['detected_type'], max_rows=500, max_columns=200, max_chars=10_000)[0])]
        for sheet, rows in tables:
            for i, values in enumerate(rows):
                for j, text in enumerate(values):
                    if not text.strip() or len(cells) >= MAX_SUPPLEMENT_CELLS:
                        continue
                    cells.append(dict(cell_id=f'{sid}-{len(cells):05d}', origin='supplement_file',
                                      role='column_header' if i == 0 else 'row_label' if j == 0 else 'value',
                                      segment_id=None, table_id=None, grid_row=None, column_index=j, supplement_id=sid,
                                      transcription_id=None, asset_url=url, asset_sha256=row['sha256'], sheet=sheet,
                                      row_index=i, page=None, raw_text=text.strip()))
    return cells


def write_draft(root, aid):
    source, _ = load_inputs(root, aid)
    draft = draft_series(source, supplement_cells=supplement_file_cells(root, aid))
    out = store.path(root, 'measurement-series-drafts', aid); out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(draft, ensure_ascii=False, indent=2) + '\n')
    return draft


def check(root, aid, *, verify_files=True):
    root = Path(root)
    source, annotation = load_inputs(root, aid)
    target = json.loads(store.path(root, 'measurement-series', aid).read_text())
    manifest = _json(store.path(root, 'supplement-manifests', aid))
    transcriptions = reviewed_transcriptions(root, aid)
    report = validate_series(target, source, annotation, transcriptions=transcriptions, manifest=manifest,
                             file_reader=file_reader(root, aid) if verify_files else None)
    return target, source, annotation, manifest, transcriptions, report


def binding(target, source, annotation, manifest, transcriptions):
    out = base_binding(source, annotation, target, STATUS)
    used = {c['supplement_id'] for c in target['cells'] if c['origin'] == 'supplement_transcription'}
    out.update(asset_sha256=digest(sorted({(c['asset_url'], c['asset_sha256']) for c in target['cells']
                                           if c['origin'] == 'supplement_file'})),
               transcription_sha256=digest(sorted([list(k) + [v] for k, v in transcriptions.items() if k[0] in used])),
               contract_sha256=contract_sha256())
    return out


def sign_review(root, aid, *, role, reviewer, notes):
    from signing_guard import check_signature
    check_signature(reviewer, notes)   # refuse probes before validation/signing
    target, source, annotation, manifest, transcriptions, report = check(root, aid, verify_files=True)
    return sign(root, KIND, aid, binding(target, source, annotation, manifest, transcriptions), report,
                role=role, reviewer=reviewer, notes=notes)


def reviewed_series(root, aid):
    root = Path(root)
    if not store.path(root, 'measurement-series', aid).exists() or not all(present(root, KIND, aid)):
        return None
    target, source, annotation, manifest, transcriptions, report = check(root, aid, verify_files=False)
    receipts = verify(root, KIND, aid, binding(target, source, annotation, manifest, transcriptions), report)
    return (target, receipts) if receipts else None


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('root', type=Path); p.add_argument('--article', required=True)
    p.add_argument('--draft', action='store_true'); p.add_argument('--role', choices=['agent', 'parent'])
    p.add_argument('--reviewer'); p.add_argument('--notes'); args = p.parse_args()
    if args.draft:
        draft = write_draft(args.root, args.article)
        print(json.dumps({'draft': str(store.path(args.root, 'measurement-series-drafts', args.article)), 'format': FORMAT,
                          'cells': len(draft['cells']), 'candidate_series': len(draft['series'])}))
    elif args.role:
        receipt = sign_review(args.root, args.article, role=args.role, reviewer=args.reviewer, notes=args.notes)
        print(json.dumps({k: receipt[k] for k in ('role', 'reviewer', 'series', 'points')}))
    else:
        print(json.dumps(check(args.root, args.article)[-1]))
