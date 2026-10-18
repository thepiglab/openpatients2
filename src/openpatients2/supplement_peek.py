"""Deterministic, bounded peeks into cached supplementary files.

A peek is a literal machine copy of the first lines/rows/pages or container
metadata. It is never a clinical interpretation, and a peek of a head sample is
not an inspection of the whole file. Untrusted files are never executed or
extracted to disk; external tools run with timeouts on the cached path only.
"""
from __future__ import annotations

import csv
import io
import json
from pathlib import Path
import re
import shutil
import struct
import subprocess
import zipfile

PEEK_LINES = 20
PEEK_DOCUMENT_LINES = 40
PEEK_ROWS = 10
PEEK_COLUMNS = 30
PEEK_CHARS = 4000
MAX_MEMBERS = 200
MAX_XML_BYTES = 50_000_000
TOOL_TIMEOUT = 60

TEXT_EXTENSIONS = {'.csv': 'csv', '.tsv': 'tsv', '.tab': 'tsv', '.txt': 'text', '.md': 'text',
                   '.json': 'text', '.xml': 'text', '.fasta': 'text', '.fa': 'text', '.vcf': 'text',
                   '.r': 'text', '.py': 'text', '.m': 'text', '.edf': None}
SUPPORTED_TYPES = {'pdf', 'docx', 'doc', 'rtf', 'xlsx', 'csv', 'tsv', 'text', 'zip', 'dicom', 'video',
                   'pptx', 'ppt', 'odt', 'odp'}
# Peek engine version. Recorded beside (not inside) machine blocks, so manifests
# signed against earlier peeks stay valid; re-peeking a signed article is refused.
PEEK_VERSION = 'supplement-peek/2'
ANYDOC_VERSION = '0.2.4'
ANYDOC_FORMATS = {'docx', 'doc', 'pptx', 'ppt', 'rtf', 'odt', 'odp'}
ANYDOC_MAX_BYTES = 50_000_000
ANYDOC_MAX_TABLES = 50
ANYDOC_MAX_ROWS = 200
ANYDOC_MAX_COLUMNS = 50
ANYDOC_MAX_CELL_CHARS = 500
# Runs in a separate isolated interpreter (-I) for a hard timeout. Only the
# offline structured model is used: `to_document` has no network path, and the
# hosted-OCR uploader is replaced before anything else runs. Markdown output is
# never used because it is not literal (emphasis markers, <br>).
ANYDOC_WORKER = r'''
import json, sys
import anydoc
def _banned(*a, **k):
    raise RuntimeError("hosted OCR is banned")
anydoc._parse_hosted = _banned
anydoc.to_markdown = anydoc.to_markdown_bytes = _banned
path, fmt, max_tables, max_rows, max_cols, max_chars = sys.argv[1], sys.argv[2], *map(int, sys.argv[3:7])
def inline_text(items):
    out = []
    for i in items or []:
        if i.kind == "text" and i.text is not None:
            out.append(i.text)
        elif i.kind == "line_break":
            out.append("\n")
        elif i.kind == "link":
            out.append(inline_text(i.content))
    return "".join(out)
def block_lines(blocks, lines, tables):
    for b in blocks or []:
        if b.kind == "table" and b.table is not None:
            tables.append(b.table)
        elif b.kind in ("heading", "paragraph"):
            lines.append(inline_text(b.content))
        elif b.kind == "list" and b.list is not None:
            for item in b.list.items:
                block_lines(item.blocks, lines, tables)
        elif b.kind == "block_quote":
            block_lines(b.blocks, lines, tables)
        elif b.kind == "code_block" and b.text:
            lines.append(b.text)
try:
    doc = anydoc.to_document(open(path, "rb").read(), fmt)
except anydoc.ConvertError as exc:
    print(json.dumps({"error": type(exc).__name__})); sys.exit(0)
lines, found = [], []
block_lines(doc.blocks, lines, found)
tables = []
for index, t in enumerate(found[:max_tables]):
    rows, spans = [], []
    for r, slots in enumerate(t.grid[:max_rows]):
        row = []
        for c, slot in enumerate(slots[:max_cols]):
            if slot.kind == "covered" or slot.cell is None:
                row.append(None); continue
            texts = []
            for b in slot.cell.blocks:
                if b.kind in ("heading", "paragraph"):
                    texts.append(inline_text(b.content))
            row.append("\n".join(texts)[:max_chars])
            if slot.cell.row_span > 1 or slot.cell.col_span > 1:
                spans.append([r, c, slot.cell.row_span, slot.cell.col_span])
        rows.append(row)
    tables.append({"index": index, "kind": t.kind, "header_rows": t.header_rows, "row_count": len(t.grid),
                   "column_count": max((len(g) for g in t.grid), default=0), "rows": rows, "spans": spans})
print(json.dumps({"lines": lines, "tables": tables, "table_count": len(found),
                  "table_row_count": sum(len(t.grid) for t in found), "notes": len(doc.notes)}))
'''
VIDEO_EXTENSIONS ={'.mp4', '.m4v', '.mov', '.avi', '.mkv', '.webm', '.wmv', '.mpg', '.mpeg'}
# DICOM tags copied into a peek. Patient name/ID/birth date and free-text
# descriptions are deliberately excluded even when present.
DICOM_TAGS = {(0x0002, 0x0010): 'TransferSyntaxUID', (0x0008, 0x0016): 'SOPClassUID',
              (0x0008, 0x0060): 'Modality', (0x0018, 0x0015): 'BodyPartExamined',
              (0x0028, 0x0004): 'PhotometricInterpretation', (0x0028, 0x0008): 'NumberOfFrames',
              (0x0028, 0x0010): 'Rows', (0x0028, 0x0011): 'Columns'}


def extension(name):
    return Path((name or '').split('?')[0]).suffix.lower()


def detect_type(head: bytes, filename: str | None) -> str:
    """Magic bytes first; the extension only disambiguates containers/text."""
    ext = extension(filename)
    if head.startswith(b'%PDF-'):
        return 'pdf'
    if len(head) >= 132 and head[128:132] == b'DICM':
        return 'dicom'
    if head.startswith(b'PK\x03\x04') or head.startswith(b'PK\x05\x06'):
        odf = re.match(rb'PK\x03\x04.{26}mimetypeapplication/vnd\.oasis\.opendocument\.(text|presentation|spreadsheet)', head, re.S)
        if odf:
            return {b'text': 'odt', b'presentation': 'odp', b'spreadsheet': 'ods'}[odf.group(1)]
        return {'.docx': 'docx', '.xlsx': 'xlsx', '.pptx': 'pptx', '.odt': 'odt', '.odp': 'odp',
                '.ods': 'ods', '.epub': 'epub'}.get(ext, 'zip')
    if head.startswith(b'\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1'):
        return {'.doc': 'doc', '.xls': 'xls', '.ppt': 'ppt'}.get(ext, 'ole_compound')
    if head.startswith(b'{\\rtf'):
        return 'rtf'
    if head.startswith(b'\x89PNG\r\n\x1a\n'):
        return 'image_png'
    if head.startswith(b'\xff\xd8\xff'):
        return 'image_jpeg'
    if head.startswith((b'GIF87a', b'GIF89a')):
        return 'image_gif'
    if head.startswith((b'II*\x00', b'MM\x00*')):
        return 'image_tiff'
    if head.startswith(b'RIFF') and head[8:12] == b'WEBP':
        return 'image_webp'
    if head.startswith(b'RIFF') and head[8:12] == b'AVI ':
        return 'video'
    if head[4:8] in (b'ftyp', b'moov', b'mdat', b'wide', b'free') or head.startswith(b'\x1aE\xdf\xa3'):
        return 'video'
    if head.startswith(b'\x00\x00\x01\xba') or head.startswith(b'\x00\x00\x01\xb3'):
        return 'video'
    if head.startswith(b'0       ') and ext == '.edf':
        return 'edf_signal'
    if head.startswith(b'\x1f\x8b'):
        return 'gzip'
    if b'\x00' not in head[:4096]:
        try:
            head[:4096].decode('utf-8')
        except UnicodeDecodeError as exc:
            # A multi-byte character may be cut at the sample boundary.
            if exc.start < len(head[:4096]) - 4:
                return 'binary_unknown'
        return TEXT_EXTENSIONS.get(ext) or 'text'
    return 'binary_unknown'


def _bounded_text(text, max_lines=PEEK_LINES):
    text = text.replace('\r\n', '\n').replace('\r', '\n')
    lines = [l.rstrip() for l in text.split('\n') if l.strip()]
    first = []
    total = 0
    for line in lines[:max_lines]:
        line = line[:500]
        if total + len(line) > PEEK_CHARS:
            break
        first.append(line); total += len(line)
    return first, len(lines)


def _run(args):
    tool = shutil.which(args[0])
    if not tool:
        return None, f'{args[0]} unavailable'
    try:
        result = subprocess.run([tool, *args[1:]], capture_output=True, timeout=TOOL_TIMEOUT, check=True)
        return result.stdout, None
    except (OSError, subprocess.SubprocessError) as exc:
        return None, f'{args[0]} failed: {type(exc).__name__}'


def _pdf(path):
    out = {'kind': 'pdf', 'page_count': None, 'first_page_lines': [], 'tool': 'pdfinfo+pdftotext'}
    limits = []
    info, error = _run(['pdfinfo', str(path)])
    if info is not None:
        match = re.search(rb'^Pages:\s+(\d+)\s*$', info, re.MULTILINE)
        out['page_count'] = int(match.group(1)) if match else None
    else:
        limits.append(error)
    # Layout mode keeps printed table rows on one line.
    text, error = _run(['pdftotext', '-f', '1', '-l', '1', '-layout', '-enc', 'UTF-8', str(path), '-'])
    if text is not None:
        out['first_page_lines'], _ = _bounded_text(text.decode('utf-8', 'replace'), PEEK_DOCUMENT_LINES)
    else:
        limits.append(error)
    return out, limits


def _docx_text(archive):
    xml = archive.read('word/document.xml')
    if len(xml) > MAX_XML_BYTES:
        raise ValueError('document.xml exceeds peek bound')
    paragraphs = []
    for para in re.findall(rb'<w:p[ >].*?</w:p>', xml, re.S):
        texts = re.findall(rb'<w:t(?: [^>]*)?>([^<]*)</w:t>', para)
        paragraphs.append(b''.join(texts).decode('utf-8', 'replace'))
    import html
    text = html.unescape('\n'.join(paragraphs))
    tables = len(re.findall(rb'<w:tbl>', xml))
    rows = len(re.findall(rb'<w:tr[ >]', xml))
    pages = None
    if 'docProps/app.xml' in archive.namelist():
        match = re.search(rb'<Pages>(\d+)</Pages>', archive.read('docProps/app.xml'))
        pages = int(match.group(1)) if match else None
    return text, tables, rows, pages


def anydoc_available():
    import importlib.util
    return importlib.util.find_spec('anydoc') is not None


def _anydoc_document(path, detected):
    """Structured, literal text/tables via firecrawl-anydoc (offline only). Returns (peek|None, limits)."""
    import sys
    if detected not in ANYDOC_FORMATS:
        return None, []
    if not anydoc_available():
        return None, ['anydoc not installed; fell back']
    if Path(path).stat().st_size > ANYDOC_MAX_BYTES:
        return None, ['File exceeds anydoc size gate; fell back']
    try:
        result = subprocess.run([sys.executable, '-I', '-c', ANYDOC_WORKER, str(path), detected,
                                 str(ANYDOC_MAX_TABLES), str(ANYDOC_MAX_ROWS), str(ANYDOC_MAX_COLUMNS),
                                 str(ANYDOC_MAX_CELL_CHARS)],
                                capture_output=True, timeout=TOOL_TIMEOUT, check=True,
                                env={'PATH': '/usr/bin:/bin'})  # no API keys reach the worker
        data = json.loads(result.stdout)
    except (OSError, subprocess.SubprocessError, json.JSONDecodeError) as exc:
        return None, [f'anydoc failed: {type(exc).__name__}; fell back']
    if 'error' in data:
        return None, [f'anydoc {data["error"]}; fell back']
    first, count = _bounded_text('\n'.join(data['lines']), PEEK_DOCUMENT_LINES)
    limits = ['Text and table cells from anydoc Inline.text (literal runs); Markdown output not used']
    if data['table_count'] > ANYDOC_MAX_TABLES or any(t['row_count'] > ANYDOC_MAX_ROWS or t['column_count'] > ANYDOC_MAX_COLUMNS
                                                     for t in data['tables']):
        limits.append(f'Tables capped at {ANYDOC_MAX_TABLES} tables x {ANYDOC_MAX_ROWS} rows x {ANYDOC_MAX_COLUMNS} columns')
    peek = {'kind': 'document', 'format': detected, 'first_lines': first, 'nonblank_line_count': count,
            'table_count': data['table_count'], 'table_row_count': data['table_row_count'],
            'declared_page_count': None, 'tool': f'anydoc@{ANYDOC_VERSION}', 'tables': data['tables'],
            'note_count': data['notes'], 'literal_verification': 'unverified_format', 'nonliteral': []}
    if detected == 'docx':
        # Cross-check every emitted line against raw w:t text; anydoc may normalize
        # characters (e.g. NBSP -> space). Mismatches are listed, never silently kept.
        try:
            with zipfile.ZipFile(path) as archive:
                raw = _docx_text(archive)[0]
        except (zipfile.BadZipFile, KeyError, ValueError):
            raw = None
        if raw is not None:
            for i, line in enumerate(first):
                if line not in raw:
                    peek['nonliteral'].append({'where': 'first_lines', 'index': i})
            for t in data['tables']:
                for r, row in enumerate(t['rows']):
                    for c, cell in enumerate(row):
                        if cell and any(part not in raw for part in cell.split('\n')):
                            peek['nonliteral'].append({'where': 'table', 'table': t['index'], 'row': r, 'col': c})
            peek['literal_verification'] = 'verified_against_docx_xml' if not peek['nonliteral'] else 'partial'
            if peek['nonliteral']:
                limits.append(f'{len(peek["nonliteral"])} anydoc lines/cells are not literal in the DOCX XML (normalized characters); do not quote them')
    return peek, limits


def _document(path, detected):
    peek, limits = _anydoc_document(path, detected)
    if peek is not None:
        return peek, limits
    out = {'kind': 'document', 'format': detected, 'first_lines': [], 'nonblank_line_count': None,
           'table_count': None, 'table_row_count': None, 'declared_page_count': None, 'tool': None}
    text = None
    if detected == 'docx':
        try:
            with zipfile.ZipFile(path) as archive:
                text, out['table_count'], out['table_row_count'], out['declared_page_count'] = _docx_text(archive)
            out['tool'] = 'docx_xml'
        except (zipfile.BadZipFile, KeyError, ValueError) as exc:
            limits.append(f'DOCX parse failed: {type(exc).__name__}')
    if text is None and detected in ('docx', 'doc', 'rtf', 'odt'):
        raw, error = _run(['textutil', '-convert', 'txt', '-stdout', str(path)])
        if raw is not None:
            text = raw.decode('utf-8', 'replace'); out['tool'] = 'textutil'
        else:
            limits.append(error)
    if text is not None:
        out['first_lines'], out['nonblank_line_count'] = _bounded_text(text, PEEK_DOCUMENT_LINES)
    if out['declared_page_count'] is not None:
        limits.append('declared_page_count is DOCX metadata, not a rendered page count')
    if text is None:
        return None, limits + ['No offline text extractor succeeded']
    return out, limits


def _column_index(ref):
    letters = re.match(r'[A-Z]+', ref).group(0)
    value = 0
    for ch in letters:
        value = value * 26 + ord(ch) - 64
    return value - 1


XLSX_NS = {'m': 'http://schemas.openxmlformats.org/spreadsheetml/2006/main',
           'r': 'http://schemas.openxmlformats.org/officeDocument/2006/relationships'}
# Full (review) reads stream each part; a part above this uncompressed size is not read at all.
XLSX_MAX_PART_BYTES = 400_000_000


class XlsxReader:
    """Streaming XLSX reader (no formula evaluation: a formula cell yields its cached <v> text).

    `sheets` = [{'name', 'state', 'target', 'available'}] in workbook order; `rows(sheet)` yields
    (row_number, {column_index: text}) for every <row> element, parsed incrementally."""

    def __init__(self, path, *, max_part_bytes=XLSX_MAX_PART_BYTES):
        import xml.etree.ElementTree as ET
        self.ET, self.max_part_bytes = ET, max_part_bytes
        self.archive = zipfile.ZipFile(path)
        try:
            self._load()
        except BaseException:
            self.archive.close()
            raise

    def _load(self):
        ET = self.ET
        names = set(self.archive.namelist())
        self.shared = []
        if 'xl/sharedStrings.xml' in names:
            with self._open('xl/sharedStrings.xml') as stream:
                for _, elem in ET.iterparse(stream):
                    if elem.tag == f'{{{XLSX_NS["m"]}}}si':
                        self.shared.append(''.join(t.text or '' for t in elem.iter(f'{{{XLSX_NS["m"]}}}t')))
                        elem.clear()
        with self._open('xl/workbook.xml') as stream:
            workbook = ET.parse(stream).getroot()
        rels = {}
        if 'xl/_rels/workbook.xml.rels' in names:
            with self._open('xl/_rels/workbook.xml.rels') as stream:
                rels = {rel.get('Id'): rel.get('Target') for rel in ET.parse(stream).getroot()}
        self.sheets = []
        for sheet in workbook.iter(f'{{{XLSX_NS["m"]}}}sheet'):
            target = rels.get(sheet.get(f'{{{XLSX_NS["r"]}}}id'), '').lstrip('/')
            target = target if target.startswith('xl/') else 'xl/' + target
            self.sheets.append({'name': sheet.get('name'), 'state': sheet.get('state') or 'visible',
                                'target': target, 'available': target in names})

    def _open(self, name):
        if self.archive.getinfo(name).file_size > self.max_part_bytes:
            raise ValueError(f'{name} exceeds the XLSX part bound')
        return self.archive.open(name)

    def part_bytes(self, sheet):
        return self.archive.getinfo(sheet['target']).file_size

    def rows(self, sheet):
        m = XLSX_NS['m']
        with self._open(sheet['target']) as stream:
            parent = None; previous = 0
            for event, elem in self.ET.iterparse(stream, events=('start', 'end')):
                if event == 'start':
                    if elem.tag == f'{{{m}}}sheetData':
                        parent = elem
                    continue
                if elem.tag != f'{{{m}}}row':
                    continue
                number = int(elem.get('r')) if (elem.get('r') or '').isdigit() else previous + 1
                previous = number; values = {}
                for cell in elem.findall(f'{{{m}}}c'):
                    kind = cell.get('t'); v = cell.find(f'{{{m}}}v')
                    if kind == 's' and v is not None:
                        text = self.shared[int(v.text)] if v.text and int(v.text) < len(self.shared) else ''
                    elif kind == 'inlineStr':
                        text = ''.join(t.text or '' for t in cell.iter(f'{{{m}}}t'))
                    else:
                        text = v.text if v is not None and v.text is not None else ''
                    ref = cell.get('r')
                    values[_column_index(ref) if ref else max(values, default=-1) + 1] = text
                yield number, values
                if parent is not None:
                    parent.clear()

    def close(self):
        self.archive.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


def read_xlsx(path, *, max_rows=PEEK_ROWS, max_columns=PEEK_COLUMNS, max_chars=200):
    """Minimal XLSX peek (no formula evaluation): sheet -> (row_count, first rows). Parts above the
    peek bound raise, exactly as before; full reviews use XlsxReader with its larger streaming bound."""
    sheets = []
    with XlsxReader(path, max_part_bytes=MAX_XML_BYTES) as reader:
        for sheet in reader.sheets:
            if not sheet['available']:
                sheets.append({'name': sheet['name'], 'row_count': None, 'rows': []}); continue
            grid = []; count = 0
            for _, values in reader.rows(sheet):
                count += 1
                if count <= max_rows:
                    values = {i: t[:max_chars] for i, t in values.items() if i < max_columns}
                    width = max(values, default=-1) + 1
                    grid.append([values.get(i, '') for i in range(width)])
            sheets.append({'name': sheet['name'], 'row_count': count, 'rows': grid})
    return sheets


def _xlsx(path):
    try:
        sheets = read_xlsx(path)
        return {'kind': 'spreadsheet', 'format': 'xlsx', 'sheets': sheets,
                'row_count': sum(s['row_count'] or 0 for s in sheets), 'tool': 'xlsx_xml'}, []
    except (zipfile.BadZipFile, KeyError, ValueError, SyntaxError) as exc:
        return {'kind': 'spreadsheet', 'format': 'xlsx', 'sheets': [], 'row_count': None,
                'tool': 'xlsx_xml'}, [f'XLSX parse failed: {type(exc).__name__}']


def read_delimited(path, detected, *, max_rows=PEEK_ROWS, complete=True, max_columns=PEEK_COLUMNS, max_chars=200):
    data = Path(path).read_bytes()
    text = data.decode('utf-8-sig', 'replace')
    delimiter = '\t' if detected == 'tsv' else ','
    rows = []
    reader = csv.reader(io.StringIO(text), delimiter=delimiter)
    try:
        for row in reader:
            if len(rows) >= max_rows:
                break
            rows.append([c[:max_chars] for c in row[:max_columns]])
    except csv.Error:
        pass
    count = sum(1 for line in text.splitlines() if line.strip()) if complete else None
    return rows, count


def _delimited(path, detected, complete):
    rows, count = read_delimited(path, detected, complete=complete)
    limits = [] if complete else ['Head sample only; total row count unknown and final sampled row may be truncated']
    return {'kind': 'table_text', 'format': detected, 'rows': rows, 'row_count': count, 'tool': 'csv'}, limits


def _text(path, complete):
    first, count = _bounded_text(Path(path).read_bytes().decode('utf-8', 'replace'))
    return ({'kind': 'text', 'first_lines': first, 'nonblank_line_count': count if complete else None, 'tool': 'utf-8'},
            [] if complete else ['Head sample only; total line count unknown'])


class _SparseTail(io.RawIOBase):
    """Expose a ranged tail sample at its true offset so zipfile reads the central directory."""
    def __init__(self, tail, size):
        self.tail, self.size, self.start, self.pos = tail, size, size - len(tail), 0
    def readable(self): return True
    def seekable(self): return True
    def tell(self): return self.pos
    def seek(self, offset, whence=0):
        self.pos = {0: offset, 1: self.pos + offset, 2: self.size + offset}[whence]; return self.pos
    def read(self, n=-1):
        n = self.size - self.pos if n is None or n < 0 else n
        if self.pos < self.start:
            raise OSError('Requested bytes outside the bounded tail sample')
        chunk = self.tail[self.pos - self.start:self.pos - self.start + n]
        self.pos += len(chunk); return chunk
    def readinto(self, b):
        data = self.read(len(b)); b[:len(data)] = data; return len(data)


def _zip(path=None, tail=None, size=None):
    out = {'kind': 'archive', 'format': 'zip', 'member_count': None, 'uncompressed_bytes': None,
           'members': [], 'member_extensions': {}, 'tool': 'zipfile'}
    try:
        archive = zipfile.ZipFile(path) if path else zipfile.ZipFile(_SparseTail(tail, size))
        with archive:
            infos = [i for i in archive.infolist() if not i.is_dir()]
    except (zipfile.BadZipFile, OSError, ValueError) as exc:
        return out, [f'ZIP central directory unreadable: {type(exc).__name__}']
    out['member_count'] = len(infos)
    out['uncompressed_bytes'] = sum(i.file_size for i in infos)
    out['members'] = [{'name': i.filename[:300], 'bytes': i.file_size} for i in infos[:MAX_MEMBERS]]
    counts = {}
    for i in infos:
        ext = extension(i.filename) or '<none>'
        counts[ext] = counts.get(ext, 0) + 1
    out['member_extensions'] = dict(sorted(counts.items()))
    out['dicomdir_present'] = any(Path(i.filename).name.upper() == 'DICOMDIR' for i in infos)
    limits = ['Members listed from the central directory only; nothing extracted']
    if len(infos) > MAX_MEMBERS:
        limits.append(f'Only first {MAX_MEMBERS} members listed')
    return out, limits


def image_dimensions(head):
    if head.startswith(b'\x89PNG\r\n\x1a\n') and len(head) >= 24:
        return struct.unpack('>II', head[16:24])
    if head.startswith((b'GIF87a', b'GIF89a')) and len(head) >= 10:
        return struct.unpack('<HH', head[6:10])
    if head.startswith(b'\xff\xd8'):
        i = 2
        while i + 9 < len(head):
            if head[i] != 0xFF:
                i += 1; continue
            marker = head[i + 1]
            if marker in (0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF):
                h, w = struct.unpack('>HH', head[i + 5:i + 9]); return w, h
            if marker in (0xD8, 0x01) or 0xD0 <= marker <= 0xD7:
                i += 2; continue
            i += 2 + struct.unpack('>H', head[i + 2:i + 4])[0]
    return None


def _image(path, detected, head):
    out = {'kind': 'image', 'format': detected.removeprefix('image_'), 'width': None, 'height': None, 'tool': 'header'}
    dims = image_dimensions(head)
    limits = []
    if dims:
        out['width'], out['height'] = dims
    else:
        raw, error = _run(['sips', '-g', 'pixelWidth', '-g', 'pixelHeight', str(path)])
        if raw is not None:
            w = re.search(rb'pixelWidth:\s*(\d+)', raw); h = re.search(rb'pixelHeight:\s*(\d+)', raw)
            out['width'] = int(w.group(1)) if w else None; out['height'] = int(h.group(1)) if h else None
            out['tool'] = 'sips'
        else:
            limits.append(error)
    limits.append('Dimensions only; pixels must be viewed separately')
    return out, limits


def dicom_tags(data):
    """Whitelisted header tags from explicit/implicit VR little-endian DICOM."""
    tags = {}
    pos = 132; explicit = True
    long_vrs = {b'OB', b'OW', b'OF', b'SQ', b'UT', b'UN', b'OD', b'OL', b'UC', b'UR', b'OV', b'SV', b'UV'}
    while pos + 8 <= len(data) and pos < 1_000_000:
        group, element = struct.unpack('<HH', data[pos:pos + 4])
        if (group, element) == (0x7FE0, 0x0010):
            break
        use_explicit = explicit or group == 0x0002
        vr = data[pos + 4:pos + 6]
        if use_explicit and re.fullmatch(rb'[A-Z]{2}', vr):
            if vr in long_vrs:
                if pos + 12 > len(data): break
                length = struct.unpack('<I', data[pos + 8:pos + 12])[0]; start = pos + 12
            else:
                length = struct.unpack('<H', data[pos + 6:pos + 8])[0]; start = pos + 8
        else:
            vr = None; length = struct.unpack('<I', data[pos + 4:pos + 8])[0]; start = pos + 8
        if length == 0xFFFFFFFF:
            break  # undefined-length sequence: stop rather than mis-parse
        value = data[start:start + length]
        name = DICOM_TAGS.get((group, element))
        if name:
            if name in ('Rows', 'Columns') and len(value) == 2:
                tags[name] = struct.unpack('<H', value)[0]
            else:
                tags[name] = value.decode('ascii', 'replace').strip('\x00 ').strip()
        if (group, element) == (0x0002, 0x0010):
            explicit = tags.get('TransferSyntaxUID') != '1.2.840.10008.1.2'
        pos = start + length
    return tags


def _video(path, complete):
    out = {'kind': 'video', 'duration_seconds': None, 'width': None, 'height': None, 'codec': None,
           'frame_count': None, 'frame_rate': None, 'tool': 'ffprobe'}
    raw, error = _run(['ffprobe', '-v', 'error', '-print_format', 'json', '-show_format', '-show_streams', str(path)])
    limits = [] if complete else ['Head sample only; ffprobe metadata may be incomplete']
    if raw is None:
        return None, limits + [error]
    try:
        info = json.loads(raw)
    except json.JSONDecodeError:
        return None, limits + ['ffprobe output unreadable']
    stream = next((s for s in info.get('streams', []) if s.get('codec_type') == 'video'), {})
    duration = info.get('format', {}).get('duration') or stream.get('duration')
    out.update(duration_seconds=round(float(duration), 3) if duration else None,
               width=stream.get('width'), height=stream.get('height'), codec=stream.get('codec_name'),
               frame_count=int(stream['nb_frames']) if str(stream.get('nb_frames', '')).isdigit() else None,
               frame_rate=stream.get('avg_frame_rate'))
    limits.append('Container metadata only; frames must be viewed separately')
    return out, limits


def peek_file(path, filename, *, complete=True, tail=None, total_bytes=None):
    """Return (detected_type, inspection_status, peek, limitations).

    complete=False means `path` holds only a head sample of a larger file.
    """
    path = Path(path)
    with path.open('rb') as stream:
        head = stream.read(1_000_000)
    detected = detect_type(head, filename)
    limits = [] if complete else ['Peek derived from a bounded head sample, not the complete file']
    peek = None
    if detected == 'pdf':
        if complete:
            peek, more = _pdf(path)
        else:
            more = ['PDF page count/text need the complete file']
    elif detected in ('docx', 'doc', 'rtf') or detected in ANYDOC_FORMATS:
        if complete:
            peek, more = _document(path, detected)
        else:
            more = ['Word documents need the complete file for text extraction']
    elif detected == 'xlsx':
        peek, more = _xlsx(path) if complete else (None, ['XLSX needs the complete file'])
    elif detected in ('csv', 'tsv'):
        peek, more = _delimited(path, detected, complete)
    elif detected == 'text':
        peek, more = _text(path, complete)
    elif detected == 'zip':
        if complete:
            peek, more = _zip(path=path)
        elif tail is not None and total_bytes:
            peek, more = _zip(tail=tail, size=total_bytes)
        else:
            more = ['ZIP central directory not sampled']
    elif detected.startswith('image_'):
        peek, more = _image(path, detected, head)
    elif detected == 'dicom':
        peek = {'kind': 'dicom', 'tags': dicom_tags(head), 'tool': 'dicom_header'}
        more = ['Whitelisted non-identifying header tags only']
    elif detected == 'video':
        peek, more = _video(path, complete)
    else:
        more = [f'No peek implemented for detected type {detected}']
    limits += [m for m in more if m]
    if peek is None:
        # Supported formats that could not be peeked (head sample, tool missing)
        # keep metadata; anything else is explicitly unsupported, never dropped.
        status = 'metadata_only' if detected in SUPPORTED_TYPES or detected.startswith('image_') else 'unsupported_type'
    else:
        status = 'peeked' if complete else 'peeked_head_sample'
    return detected, status, peek, limits


def peek_text(peek) -> str:
    """Flattened literal peek text, used to check quoted supplement evidence."""
    if not peek:
        return ''
    parts = []
    # Lines/cells a cross-check found non-literal are never quotable.
    skip = {(n['where'], n.get('index'), n.get('table'), n.get('row'), n.get('col')) for n in peek.get('nonliteral') or []}
    parts += [l for i, l in enumerate(peek.get('first_lines') or []) if ('first_lines', i, None, None, None) not in skip]
    parts += peek.get('first_page_lines') or []
    for row in peek.get('rows') or []:
        parts.append(' | '.join(row))
    for sheet in peek.get('sheets') or []:
        parts.append(sheet.get('name') or '')
        for row in sheet.get('rows') or []:
            parts.append(' | '.join(row))
    for table in peek.get('tables') or []:
        for r, row in enumerate(table.get('rows') or []):
            parts += [c for k, c in enumerate(row) if c and ('table', None, table['index'], r, k) not in skip]
    for member in peek.get('members') or []:
        parts.append(member['name'])
    return '\n'.join(parts)
