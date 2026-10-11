"""Literal text units of word-processor supplements (DOCX/DOC/RTF/ODT), shared by both pipelines.

One extraction path for the dense reference (`supplement_review.py`, format
`dense-supplemental-text-document-reference/1`) and the runtime (local-LLM) pipeline
(`ehr_tasks.load_transcriptions` / `unit_transcriptions`): the offline firecrawl-anydoc
structured model (`to_document`, Inline.text runs only). Hosted OCR and Markdown output are
never used (the worker replaces them before anything runs); there is no lossy fallback, so a
document anydoc cannot read is not reviewable by this path.

Units, in document order:
  paragraph  one heading/paragraph/list/quote/code line (non-blank)
  table_row  one table row; cells joined by ' | ' (covered/merged slots are empty strings),
             with `table` and `row` coordinates
DOCX units are cross-checked against the raw w:t text of word/document.xml; a unit whose text
(or any cell) is not literal there is listed in `nonliteral_units` and may not be quoted.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess
import sys
import zipfile

from .supplement_peek import ANYDOC_VERSION, TOOL_TIMEOUT, _docx_text, anydoc_available

TEXT_DOCUMENT_TYPES = {
    'docx': 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
    'doc': 'application/msword',
    'rtf': 'application/rtf',
    'odt': 'application/vnd.oasis.opendocument.text',
}
MAX_BYTES = 50_000_000
TOOL = f'anydoc@{ANYDOC_VERSION}'
UNIT_WORKER = r'''
import json, sys
import anydoc
def _banned(*a, **k):
    raise RuntimeError("hosted OCR is banned")
anydoc._parse_hosted = _banned
anydoc.to_markdown = anydoc.to_markdown_bytes = _banned
path, fmt = sys.argv[1], sys.argv[2]
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
units, tables = [], [0]
def cell_text(cell):
    return "\n".join(inline_text(b.content) for b in cell.blocks if b.kind in ("heading", "paragraph"))
def walk(blocks):
    for b in blocks or []:
        if b.kind == "table" and b.table is not None:
            index = tables[0]; tables[0] += 1
            for r, slots in enumerate(b.table.grid):
                cells = ["" if (s.kind == "covered" or s.cell is None) else cell_text(s.cell) for s in slots]
                units.append({"kind": "table_row", "table": index, "row": r, "cells": cells})
        elif b.kind in ("heading", "paragraph"):
            for line in inline_text(b.content).split("\n"):
                if line.strip():
                    units.append({"kind": "paragraph", "text": line})
        elif b.kind == "list" and b.list is not None:
            for item in b.list.items:
                walk(item.blocks)
        elif b.kind == "block_quote":
            walk(b.blocks)
        elif b.kind == "code_block" and b.text:
            for line in b.text.split("\n"):
                if line.strip():
                    units.append({"kind": "paragraph", "text": line})
try:
    doc = anydoc.to_document(open(path, "rb").read(), fmt)
except anydoc.ConvertError as exc:
    print(json.dumps({"error": type(exc).__name__})); sys.exit(0)
walk(doc.blocks)
print(json.dumps({"units": units}))
'''


def units_digest(units):
    return hashlib.sha256(json.dumps(units, sort_keys=True, ensure_ascii=False, separators=(',', ':')).encode()).hexdigest()


def extract_units(path, fmt):
    """{'tool', 'units': [{unit, kind, text, table, row}], 'units_sha256', 'nonliteral_units'} for one document."""
    if fmt not in TEXT_DOCUMENT_TYPES:
        raise ValueError(f'Text-document review supports {sorted(TEXT_DOCUMENT_TYPES)}, not {fmt!r}')
    path = Path(path)
    if path.stat().st_size > MAX_BYTES:
        raise ValueError('Document exceeds the extraction size gate')
    if not anydoc_available():
        raise ValueError('anydoc (firecrawl-anydoc) is required for text-document review; no lossy fallback is used')
    try:
        result = subprocess.run([sys.executable, '-I', '-c', UNIT_WORKER, str(path), fmt], capture_output=True,
                                timeout=TOOL_TIMEOUT, check=True, env={'PATH': '/usr/bin:/bin'})   # no API keys
        data = json.loads(result.stdout)
    except (OSError, subprocess.SubprocessError, json.JSONDecodeError) as exc:
        raise ValueError(f'anydoc extraction failed: {type(exc).__name__}') from exc
    if 'error' in data:
        raise ValueError(f'anydoc could not read the document: {data["error"]}')
    units = []
    for n, u in enumerate(data['units'], 1):
        if u['kind'] == 'table_row':
            units.append({'unit': n, 'kind': 'table_row', 'table': u['table'], 'row': u['row'],
                          'text': ' | '.join(c.replace('\n', ' ') for c in u['cells']), 'cells': u['cells']})
        else:
            units.append({'unit': n, 'kind': 'paragraph', 'table': None, 'row': None, 'text': u['text'], 'cells': None})
    nonliteral = []
    if fmt == 'docx':
        try:
            with zipfile.ZipFile(path) as archive:
                raw = _docx_text(archive)[0]
        except (zipfile.BadZipFile, KeyError, ValueError):
            raw = None
        if raw is not None:
            for u in units:
                parts = [p for c in (u['cells'] or [u['text']]) for p in c.split('\n') if p]
                if any(p not in raw for p in parts):
                    nonliteral.append(u['unit'])
    return {'tool': TOOL, 'format': fmt, 'units': units, 'units_sha256': units_digest(units),
            'unit_count': len(units), 'table_count': len({u['table'] for u in units if u['table'] is not None}),
            'nonliteral_units': nonliteral,
            'literal_verification': ('verified_against_docx_xml' if fmt == 'docx' and not nonliteral else
                                     'partial' if nonliteral else 'unverified_format')}


def unit_transcription_id(asset_index, unit):
    """Transcription id of one extracted unit (asset order within the sidecar, 1-based unit number)."""
    return f'A{asset_index + 1}-U{unit:04d}'


def reviewed_text(target):
    """{(supplement_id, transcription_id): text} a reviewed supplement sidecar offers to other tasks: its
    reviewer-authored clinical transcriptions plus, for text documents, every literal extracted unit."""
    out = {(target['supplement_id'], t['transcription_id']): t['text']
           for t in (target.get('clinical_data') or {}).get('transcriptions', [])}
    if target.get('format') == 'dense-supplemental-text-document-reference/1':
        for key, text in unit_transcriptions(target).items():
            out.setdefault(key, text)
    return out


def unit_transcriptions(target):
    """{(supplement_id, transcription_id): unit text} for every literal unit of a text-document sidecar.

    The runtime pipeline reads exactly the text the reviewers inspected (same units, same ids)."""
    out = {}
    for i, asset in enumerate(target.get('assets', [])):
        skip = set(asset.get('extraction', {}).get('nonliteral_units') or [])
        for u in asset.get('units', []):
            if u['unit'] not in skip:
                out[(target['supplement_id'], unit_transcription_id(i, u['unit']))] = u['text']
    return out
