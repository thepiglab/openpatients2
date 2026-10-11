"""Dense numeric clinical series (`dense-measurement-series/1`).

Additive sidecar for tabular/high-frequency measurements from main-article
tables, article prose, reviewed supplemental transcriptions and supplement files.
Two layers stay separate, as the protocol requires:

* `cells`: literal raw transcriptions with provenance (segment/table grid cell,
  supplemental transcription id, or file sheet/row/column + asset SHA256);
* `series[].points[]`: patient, measurement, time and the deterministic
  `parse_measurement` result for one cell. Parsed values are recomputed by the
  validator, so no value can be typed in by hand. Units are never converted.

The existing typed fact contracts (TASK_MODELS, dense-supplemental-clinical-
reference/1) are unchanged; this sidecar can cite their transcriptions.
"""
from __future__ import annotations

from collections import Counter
import re
from typing import Literal

from pydantic import Field, model_validator

from .measurements import parse_measurement
from .provenance import json_digest
from .schemas import StrictModel

FORMAT = 'dense-measurement-series/1'
DRAFT_MARK = 'DRAFT:'
MISSING = {'', '-', '–', '—', 'na', 'n/a', 'nd', 'nr', 'not done', 'not available'}
REFERENCE_HEADER = re.compile(r'reference|normal|range|\bref\b|\bRI\b', re.I)


class Cell(StrictModel):
    cell_id: str = Field(min_length=1)
    origin: Literal['article_table', 'article_text', 'supplement_transcription', 'supplement_file']
    role: Literal['value', 'row_label', 'column_header', 'reference_range', 'context']
    segment_id: str | None
    table_id: str | None
    grid_row: Literal['header', 'data'] | None
    column_index: int | None = Field(ge=0)
    supplement_id: str | None
    transcription_id: str | None
    asset_url: str | None
    asset_sha256: str | None
    sheet: str | None
    row_index: int | None = Field(ge=0)
    page: int | None = Field(ge=1)
    raw_text: str


class TimePoint(StrictModel):
    time_cell_id: str | None
    time_text: str | None
    offset_value: float | None = Field(allow_inf_nan=False)
    offset_unit: Literal['seconds', 'minutes', 'hours', 'days', 'weeks', 'months', 'years'] | None
    offset_anchor_text: str | None
    offset_basis: str | None


# Documented literal cell meanings (additive, optional): a table legend/footnote that the source prints
# gives a cell token ('-', 'd/c') or footnote marker ('(a)') a meaning. Never inferred without that legend.
CellStatus = Literal['refused', 'discontinued', 'held', 'not_given', 'prn_or_emergency']
CELL_STATUS_WORDS = {
    'refused': r'refus|declin',
    'discontinued': r'discontinu|d/c|stopp|ceas',
    'held': r'\bheld\b|withheld|\bhold\b',
    'not_given': r'not (?:given|administered)|omitted|missed',
    'prn_or_emergency': r'emergen|as[- ]needed|\bprn\b|p\.r\.n|\bstat\b|rescue|urgent',
}
# Fields added after dense-measurement-series/1 was first signed. They default to null, so earlier documents
# are unchanged and valid; the contract fingerprint excludes them so existing receipts stay current.
ADDITIVE_POINT_FIELDS = ('cell_status', 'legend_cell_id')


class Point(StrictModel):
    point_id: str = Field(min_length=1)
    value_cell_id: str
    time: TimePoint
    parse_status: Literal['parsed', 'unparseable', 'missing_cell']
    parsed: dict | None
    unit_text: str | None
    unit_cell_id: str | None
    cell_status: CellStatus | None = None
    legend_cell_id: str | None = None


class OwnershipEvidence(StrictModel):
    segment_id: str | None
    cell_id: str | None
    quote: str = Field(min_length=1, max_length=1200)


class Series(StrictModel):
    series_id: str = Field(min_length=1)
    measurement_name: str = Field(min_length=1)
    name_cell_id: str
    measurement_kind: Literal['laboratory', 'vital_sign', 'anthropometric', 'imaging_measurement',
                              'physiological_signal', 'clinical_score', 'drug_level', 'medication_dose', 'other']
    ownership: Literal['individual', 'shared', 'unresolved', 'aggregate']
    patient_ids: list[str]
    ownership_evidence: list[OwnershipEvidence]
    reference_range_cell_id: str | None
    points: list[Point] = Field(min_length=1)
    scoring: Literal['include', 'review_only']
    notes: str

    @model_validator(mode='after')
    def owners(self):
        n = len(self.patient_ids)
        if n != len(set(self.patient_ids)):
            raise ValueError('Duplicate series patient IDs')
        if ((self.ownership == 'individual' and n != 1) or (self.ownership == 'shared' and n < 2) or
                (self.ownership in ('unresolved', 'aggregate') and n)):
            raise ValueError('Series ownership and patient IDs disagree')
        if n and not self.ownership_evidence:
            raise ValueError('Patient ownership needs literal evidence')
        if self.ownership in ('unresolved', 'aggregate') and self.scoring != 'review_only':
            raise ValueError('Unresolved/aggregate series are review_only')
        return self


class ExcludedCell(StrictModel):
    cell_id: str
    reason: str = Field(min_length=1)


class MeasurementSeries(StrictModel):
    format: Literal['dense-measurement-series/1']
    article_id: str
    source_text_sha256: str
    coverage: Literal['complete', 'limited']
    cells: list[Cell]
    series: list[Series]
    excluded_cells: list[ExcludedCell]
    limitations: list[str]


def _cells_of(line):
    return [c.strip() for c in line.split('|')]


def table_grid(source):
    """Deterministic grid of table_row segments: {segment_id: {table_id, header_segment_id, header, row}}."""
    tables = {}
    for seg in source.get('segments', []):
        if seg.get('kind') == 'table_row':
            tables.setdefault(seg.get('table_id') or seg['segment_id'], []).append(seg)
    grid = {}
    for table_id, segments in tables.items():
        pipes = [[l for l in s['text'].split('\n') if '|' in l] for s in segments]
        header, header_sid = None, None
        for i, (seg, lines) in enumerate(zip(segments, pipes)):
            entry = {'table_id': table_id, 'header_segment_id': None, 'header': None, 'row': None}
            if len(lines) >= 2:
                header, header_sid = _cells_of(lines[-2]), seg['segment_id']
                entry.update(header_segment_id=header_sid, header=header, row=_cells_of(lines[-1]))
            elif len(lines) == 1:
                if i == 0 and len(segments) > 1 and len(pipes[1]) == 1:
                    header, header_sid = _cells_of(lines[0]), seg['segment_id']
                    entry.update(header_segment_id=header_sid, header=header)
                else:
                    entry.update(header_segment_id=header_sid, header=header, row=_cells_of(lines[0]))
            grid[seg['segment_id']] = entry
    return grid


# Trailing footnote markers on a table cell: '^(a)', '^(b,c)', '^a', '*', '†', '‡', '§', '¶'.
FOOTNOTE = re.compile(r'(?:\^\(\s*([A-Za-z0-9*†‡§¶#]{1,3}(?:\s*,\s*[A-Za-z0-9*†‡§¶#]{1,3})*)\s*\)|\^([A-Za-z*†‡§¶#])|([*†‡§¶#]))\s*$')
ROUTES = r'(?:PO|IM|IV|SC|SQ|SL|PR|INH|IN|TD|NG|PEG|IO|ID|po|im|iv|sc|sq|sl|pr)'
DOSE_PART = re.compile(r'(' + r'[+-]?(?:\d+(?:\.\d+)?|\.\d+)' + r')\s*(mg|µg|ug|g|mL|ml|IU|U|units?|mEq|mmol)?\s*(' + ROUTES + r')')
DOSE_CELL = re.compile(r'(?P<parts>' + DOSE_PART.pattern + r'(?:\s*\+\s*' + DOSE_PART.pattern + r')*)'
                       r'(?:\s*[×x]\s*(?P<times>\d{1,2}))?')


def split_footnotes(raw_text):
    """(core text, [footnote markers]) with trailing markers removed; the raw cell stays literal elsewhere."""
    core, markers = raw_text.strip(), []
    while True:
        m = FOOTNOTE.search(core)
        if not m or m.start() == 0:
            break
        found = m.group(1) or m.group(2) or m.group(3)
        markers = [x.strip() for x in found.split(',')] + markers
        core = core[:m.start()].rstrip()
    return core, markers


def parse_dose_cell(core):
    """'5 IM', '10 PO', '5 IM × 3', '5 IM + 5 PO' -> components with literal route and multiplicity."""
    m = DOSE_CELL.fullmatch(core)
    if not m:
        return None
    components = [{'magnitude': n, 'unit': u or None, 'route': r} for n, u, r in DOSE_PART.findall(m.group('parts'))]
    return {'kind': 'dose_components', 'components': components,
            'multiplicity': int(m.group('times')) if m.group('times') else None}


def expected_parse(raw_text):
    """Deterministic parse of one literal cell (recomputed by the validator; never typed by hand).

    Order (backward compatible): missing token -> scalar measurement of the raw cell -> after stripping
    trailing footnote markers, missing token / scalar / dose components (value + literal route, '× n'
    multiplicity, '+' combinations). Footnote markers are kept in `parsed.footnote_markers`; their meaning
    comes only from a documented legend (Point.cell_status + legend_cell_id)."""
    if raw_text.strip().casefold() in MISSING:
        return 'missing_cell', None
    measured = parse_measurement(raw_text.strip())
    if measured:
        return 'parsed', measured.model_dump()
    core, markers = split_footnotes(raw_text)
    if markers and core.casefold() in MISSING:
        return 'missing_cell', None
    if markers:
        measured = parse_measurement(core)
        if measured:
            return 'parsed', {**measured.model_dump(), 'raw_text': raw_text, 'footnote_markers': markers}
    dose = parse_dose_cell(core)
    if dose:
        return 'parsed', {'raw_text': raw_text, **dose, 'footnote_markers': markers}
    return 'unparseable', None


def cell_status_errors(point, value_cell, legend_cell):
    """A documented cell status needs a literal legend cell naming the cell token or one of its markers and
    wording that states the status."""
    if point.cell_status is None:
        return ['legend_cell_id without cell_status'] if point.legend_cell_id else []
    if legend_cell is None:
        return [f'{point.point_id}: cell_status {point.cell_status!r} needs legend_cell_id (the printed legend/footnote)']
    if legend_cell.role != 'context':
        return [f'{point.point_id}: legend cell must be a context cell (the printed legend/footnote)']
    legend = legend_cell.raw_text
    core, markers = split_footnotes(value_cell.raw_text)
    names_token = bool(core) and not re.fullmatch(r'[\d.\s]+', core) and (
        re.search(r'(?<!\w)' + re.escape(core) + r'(?!\w)', legend) or
        (core in ('-', '–', '—') and re.search(r'[-–—]', legend)))
    names_marker = any(re.search(r'(?:\^\(?|\(|^|\s)' + re.escape(m) + r'(?:\)|\s|[:.=,])', legend) for m in markers)
    if not (names_token or names_marker):
        return [f'{point.point_id}: legend does not name the cell token {core!r} or its markers {markers}']
    if not re.search(CELL_STATUS_WORDS[point.cell_status], legend, re.I):
        return [f'{point.point_id}: legend does not state {point.cell_status!r}']
    return []


def _check_cell(cell, source, grid, segments, transcriptions, manifest_files, file_reader):
    if cell.origin == 'article_table':
        entry = grid.get(cell.segment_id)
        if not entry or entry['table_id'] != cell.table_id or cell.column_index is None or cell.grid_row is None:
            raise ValueError(f'{cell.cell_id}: unknown article table cell')
        values = entry['header'] if cell.grid_row == 'header' else entry['row']
        if values is None or cell.column_index >= len(values) or values[cell.column_index] != cell.raw_text:
            raise ValueError(f'{cell.cell_id}: raw_text is not the literal table cell')
        if cell.grid_row == 'header' and entry['header_segment_id'] != cell.segment_id:
            raise ValueError(f'{cell.cell_id}: header cells cite the segment that prints the header')
    elif cell.origin == 'article_text':
        if not cell.raw_text or cell.raw_text not in segments.get(cell.segment_id, ''):
            raise ValueError(f'{cell.cell_id}: article text cell must be literal')
    elif cell.origin == 'supplement_transcription':
        text = transcriptions.get((cell.supplement_id, cell.transcription_id))
        if text is None or not cell.raw_text or cell.raw_text not in text:
            raise ValueError(f'{cell.cell_id}: must quote a reviewed supplemental transcription literally')
    else:
        if cell.row_index is None or cell.column_index is None:
            raise ValueError(f'{cell.cell_id}: supplement file cells need row_index and column_index')
        row = manifest_files.get((cell.supplement_id, cell.asset_url))
        if not row or row['machine'].get('download_status') != 'downloaded' or row['machine'].get('sha256') != cell.asset_sha256:
            raise ValueError(f'{cell.cell_id}: supplement file cell needs a downloaded manifest file with matching SHA256')
        if file_reader is not None and not file_reader(cell):
            raise ValueError(f'{cell.cell_id}: raw_text not found at that supplement file location')


def validate_series(target, source, annotation, *, transcriptions=None, manifest=None, file_reader=None):
    """Provenance and parse validation; it does not attest clinical meaning or completeness."""
    data = MeasurementSeries.model_validate(target)
    if data.article_id != source['article_id'] or data.source_text_sha256 != source['text_sha256']:
        raise ValueError('Measurement series has stale/wrong article source')
    if annotation.get('article_id') != source['article_id'] or annotation.get('text_sha256') != source['text_sha256']:
        raise ValueError('Wrong/stale patient annotation')
    if data.coverage == 'limited' and not data.limitations:
        raise ValueError('Limited series coverage needs limitations')
    transcriptions = transcriptions or {}
    manifest_files = {(r['supplement_id'], r['url']): r for r in (manifest or {}).get('files', [])}
    segments = {s['segment_id']: s['text'] for s in source.get('segments', [])}
    grid = table_grid(source)
    cells = {}
    for cell in data.cells:
        if cell.cell_id in cells:
            raise ValueError('Duplicate cell id')
        _check_cell(cell, source, grid, segments, transcriptions, manifest_files, file_reader)
        cells[cell.cell_id] = cell
    patients = {p['patient_id'] for p in annotation['patients']}
    used = Counter(); series_ids = set(); point_ids = set(); kinds = Counter()
    def need(cid):
        if cid not in cells:
            raise ValueError(f'Unknown cell id {cid}')
        return cells[cid]
    for series in data.series:
        if series.series_id in series_ids:
            raise ValueError('Duplicate series id')
        series_ids.add(series.series_id)
        if series.notes.startswith(DRAFT_MARK):
            raise ValueError(f'{series.series_id}: machine draft series must be reviewed (replace DRAFT notes)')
        if set(series.patient_ids) - patients:
            raise ValueError('Unknown series patient identity')
        if series.measurement_name not in need(series.name_cell_id).raw_text:
            raise ValueError(f'{series.series_id}: measurement_name must be literal in its name cell')
        for e in series.ownership_evidence:
            if (e.segment_id is None) == (e.cell_id is None):
                raise ValueError('Ownership evidence cites exactly one segment or cell')
            text = segments.get(e.segment_id, '') if e.segment_id else need(e.cell_id).raw_text
            if e.quote not in text:
                raise ValueError('Ownership evidence must be literal')
        if series.reference_range_cell_id:
            need(series.reference_range_cell_id)
        for point in series.points:
            if point.point_id in point_ids:
                raise ValueError('Duplicate point id')
            point_ids.add(point.point_id)
            value = need(point.value_cell_id)
            if value.role != 'value':
                raise ValueError(f'{point.point_id}: points read value cells only')
            used[point.value_cell_id] += 1
            status, parsed = expected_parse(value.raw_text)
            if (point.parse_status, point.parsed) != (status, parsed):
                raise ValueError(f'{point.point_id}: parsed value must equal the deterministic parse of the raw cell ({status})')
            problems = cell_status_errors(point, value, need(point.legend_cell_id) if point.legend_cell_id else None)
            if problems:
                raise ValueError(problems[0])
            if point.unit_text is not None:
                if point.unit_cell_id is None or point.unit_text not in need(point.unit_cell_id).raw_text:
                    raise ValueError(f'{point.point_id}: unit_text must be literal in its unit cell')
            elif point.unit_cell_id is not None:
                raise ValueError('unit_cell_id without unit_text')
            t = point.time
            if t.time_text is not None and (t.time_cell_id is None or t.time_text not in need(t.time_cell_id).raw_text):
                raise ValueError(f'{point.point_id}: time_text must be literal in its time cell')
            if t.offset_value is not None:
                if not t.offset_unit or not (t.offset_basis or '').strip():
                    raise ValueError('Time offsets need a unit and a stated basis')
                numbers = [float(n) for n in re.findall(r'\d+(?:\.\d+)?', t.time_text or '')]
                if t.offset_value != 0 and t.offset_value not in numbers:
                    raise ValueError(f'{point.point_id}: nonzero offset must be printed in time_text (no invented timing)')
            elif t.offset_unit is not None:
                raise ValueError('offset_unit without offset_value')
        kinds[series.measurement_kind] += 1
    if any(n > 1 for n in used.values()):
        raise ValueError('A value cell may feed only one point')
    excluded = {e.cell_id for e in data.excluded_cells}
    for cid in excluded:
        need(cid)
    if excluded & {c for c, n in used.items() if n}:
        raise ValueError('Excluded cells cannot also be used')
    if data.coverage == 'complete':
        missing = [c.cell_id for c in data.cells if c.role == 'value' and not used[c.cell_id] and c.cell_id not in excluded]
        if missing:
            raise ValueError(f'Complete coverage leaves value cells unaccounted: {missing[:5]}')
    points = [p for s in data.series for p in s.points]
    return {'cells': len(data.cells), 'series': len(data.series), 'points': len(points),
            'parsed_points': sum(p.parse_status == 'parsed' for p in points),
            'attributed_series': sum(bool(s.patient_ids) for s in data.series),
            'measurement_kinds': dict(sorted(kinds.items())), 'coverage': data.coverage,
            'clinical_accuracy_measured': False}


LEGEND_SPLIT = re.compile(r'(?<=[.;]["”’])\s+|(?<=[.;])\s+')


def legend_cells(source):
    """Literal `context` cells for table footnote/legend segments: the whole footnote plus each sentence/entry,
    so a point's cell_status can cite the printed legend (legend_cell_id) in both pipelines."""
    out = []
    for seg in source.get('segments', []):
        if seg.get('kind') != 'table_footnote' or not seg.get('text', '').strip():
            continue
        text = seg['text']
        pieces = [x.strip() for x in LEGEND_SPLIT.split(text) if x and x.strip()]
        for k, piece in enumerate([text] + (pieces if len(pieces) > 1 else [])):
            if piece not in text:
                continue
            out.append(dict(cell_id=f'{seg["segment_id"]}-fn' + ('' if k == 0 else str(k)), origin='article_text',
                            role='context', segment_id=seg['segment_id'], table_id=None, grid_row=None, column_index=None,
                            supplement_id=None, transcription_id=None, asset_url=None, asset_sha256=None, sheet=None,
                            row_index=None, page=None, raw_text=piece))
    return out


def draft_series(source, *, supplement_cells=()):
    """Machine draft: literal cells from every article table plus candidate row series.

    Candidates are unresolved/review_only with DRAFT notes; a reviewer assigns
    patients, kinds, time offsets and units, prunes non-series rows, then signs.
    """
    cells = []; series = []
    grid = table_grid(source)
    seen_headers = set()
    for sid, entry in grid.items():
        header = entry['header'] or []
        if entry['header_segment_id'] and entry['header_segment_id'] not in seen_headers:
            seen_headers.add(entry['header_segment_id'])
            for j, text in enumerate(header):
                cells.append(dict(cell_id=f'{entry["header_segment_id"]}-h{j}', origin='article_table', role='column_header',
                                  segment_id=entry['header_segment_id'], table_id=entry['table_id'], grid_row='header',
                                  column_index=j, supplement_id=None, transcription_id=None, asset_url=None,
                                  asset_sha256=None, sheet=None, row_index=None, page=None, raw_text=text))
        if entry['row'] is None:
            continue
        reference_col = next((j for j, h in enumerate(header) if REFERENCE_HEADER.search(h)), None)
        row_cells = []
        for j, text in enumerate(entry['row']):
            role = 'row_label' if j == 0 else 'reference_range' if j == reference_col else 'value'
            cell = dict(cell_id=f'{sid}-c{j}', origin='article_table', role=role, segment_id=sid,
                        table_id=entry['table_id'], grid_row='data', column_index=j, supplement_id=None,
                        transcription_id=None, asset_url=None, asset_sha256=None, sheet=None, row_index=None,
                        page=None, raw_text=text)
            cells.append(cell); row_cells.append(cell)
        values = [c for c in row_cells if c['role'] == 'value' and expected_parse(c['raw_text'])[0] == 'parsed']
        if not row_cells[0]['raw_text'] or not values:
            continue
        points = []
        for c in values:
            j = c['column_index']
            time_text = header[j] if j < len(header) and header[j] else None
            status, parsed = expected_parse(c['raw_text'])
            points.append(dict(point_id=f'{c["cell_id"]}-pt', value_cell_id=c['cell_id'],
                               time=dict(time_cell_id=f'{entry["header_segment_id"]}-h{j}' if time_text else None,
                                         time_text=time_text, offset_value=None, offset_unit=None,
                                         offset_anchor_text=None, offset_basis=None),
                               parse_status=status, parsed=parsed, unit_text=None, unit_cell_id=None))
        series.append(dict(series_id=f'{sid}-s', measurement_name=row_cells[0]['raw_text'],
                           name_cell_id=row_cells[0]['cell_id'], measurement_kind='other', ownership='unresolved',
                           patient_ids=[], ownership_evidence=[],
                           reference_range_cell_id=f'{sid}-c{reference_col}' if reference_col is not None and reference_col < len(row_cells) else None,
                           points=points, scoring='review_only',
                           notes=f'{DRAFT_MARK} candidate row series; confirm it is a series, assign owner/kind/units/offsets or delete'))
    cells += legend_cells(source)
    cells += list(supplement_cells)
    return {'format': FORMAT, 'article_id': source['article_id'], 'source_text_sha256': source['text_sha256'],
            'coverage': 'limited', 'cells': cells, 'series': series, 'excluded_cells': [],
            'limitations': ['Machine draft from table grids; header detection is heuristic and must be checked.']}


def series_rows(article_id, target, split=None):
    cells = {c['cell_id']: c for c in target['cells']}
    for s in target['series']:
        referenced = {s['name_cell_id'], s.get('reference_range_cell_id')} | {
            cid for p in s['points'] for cid in (p['value_cell_id'], p['time']['time_cell_id'], p['unit_cell_id'],
                                                  p.get('legend_cell_id'))}
        yield {'format': FORMAT, 'article_id': article_id, **s, 'split': split,
               'cells': [cells[c] for c in sorted(c for c in referenced if c)]}


def contract_sha256():
    # Additive optional fields (ADDITIVE_POINT_FIELDS) are excluded: documents without them are unchanged,
    # so receipts signed before they existed stay current; documents using them are bound by target_sha256.
    schema = MeasurementSeries.model_json_schema()
    point = schema['$defs']['Point']
    for name in ADDITIVE_POINT_FIELDS:
        point['properties'].pop(name, None)
    return json_digest(schema)
