"""Reproducible ICD-10-CM research catalog from the official CDC FY2027 release.

This is not a claims-coding engine. Original tabular instructions are retained
for candidate review; code sequencing and encounter/billing rules are unevaluated.
"""
import argparse
import gzip
import hashlib
import io
import json
from pathlib import Path
from urllib.request import urlopen
import zipfile

from defusedxml import ElementTree as ET

RELEASE = '2027'
SYSTEM = 'http://hl7.org/fhir/sid/icd-10-cm'
BASE = 'https://ftp.cdc.gov/pub/Health_Statistics/NCHS/Publications/ICD10CM/2027/'
SOURCES = {
    'descriptions': {'url': BASE + 'icd10cm-code-descriptions-2027.zip',
        'sha256': '93e3ad6004badf470c55bfe679b748ae88fd9b2b421851e409eec382c7713b9a',
        'member': 'icd10cm-code-descriptions-2027/icd10cm-order-2027.txt'},
    'tabular': {'url': BASE + 'icd10cm-table-and-index-2027.zip',
        'sha256': '99b5c2be16ff53eab861cc0be4b1dacba27f687b8ff4bc700679944355037ee0',
        'member': 'icd10cm-table-and-index-2027/icd10cm-tabular_-2027.xml'},
}
NOTE_TAGS = {'includes', 'inclusionTerm', 'excludes1', 'excludes2', 'codeFirst',
             'useAdditionalCode', 'codeAlso', 'sevenChrNote', 'sevenChrDef'}


def read_release(kind, path=None):
    pin = SOURCES[kind]
    raw = Path(path).read_bytes() if path else urlopen(pin['url'], timeout=90).read()
    if hashlib.sha256(raw).hexdigest() != pin['sha256']:
        raise ValueError('Official release archive changed: ' + kind)
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        return archive.read(pin['member'])


def parse_release(order_raw, tabular_raw):
    """Parse content only; the public build entrypoint verifies archive hashes."""
    root = ET.fromstring(tabular_raw)
    if root.findtext('version') != RELEASE:
        raise ValueError('Expected FY2027 ICD-10-CM tabular XML')
    contexts = {}
    def walk(node, inherited):
        current = []
        for child in node:
            if child.tag in NOTE_TAGS:
                current.append({'kind': child.tag,
                    'scope': node.findtext('name') or node.findtext('desc') or node.tag,
                    'text': ' '.join(t.strip() for t in child.itertext() if t.strip())})
        notes = inherited + current
        if node.tag == 'diag':
            key = node.findtext('name')
            aliases = [n.text.strip() for tag in ('includes', 'inclusionTerm')
                       for n in node.findall(tag + '/note') if n.text and n.text.strip()]
            contexts[key] = {'tabular_instructions': notes, 'aliases': aliases}
        for child in node:
            if child.tag in {'chapter', 'section', 'diag'}:
                walk(child, notes)
    walk(root, [])
    concepts = []
    for line in order_raw.decode('utf-8-sig').splitlines():
        if not line.strip():
            continue
        if len(line) < 78 or not line[:5].isdigit() or line[14] not in {'0', '1'}:
            raise ValueError('Unexpected ICD-10-CM order-file row')
        if line[14] != '1':
            continue  # Category headers stay in ancestor context, not selectable leaf codes.
        raw_code = line[6:13].strip()
        code = raw_code[:3] + ('.' + raw_code[3:] if len(raw_code) > 3 else '')
        display = line[77:].strip()
        # The tabular XML defines some final characters through sevenChrDef;
        # their expanded leaf codes occur only in the order file.
        context_code = next((code[:n].rstrip('.') for n in range(len(code), 2, -1)
                             if code[:n].rstrip('.') in contexts), None)
        if not display or context_code is None:
            raise ValueError('Order/tabular mismatch for ' + code)
        context = contexts[context_code]
        concepts.append({'system': SYSTEM, 'version': RELEASE, 'code': code,
            'display': display, 'aliases': sorted(set(context['aliases'])),
            'tasks': ['conditions', 'symptoms_function', 'oncology', 'family_genetics'],
            'context': {'valid_leaf_in_release': True, 'tabular_code': context_code,
                        'tabular_instructions': context['tabular_instructions'],
                        'billing_and_sequencing_validated': False}})
    if len({c['code'] for c in concepts}) != len(concepts):
        raise ValueError('Duplicate codes in release')
    return {'format': 'openpatients2-terminology-catalog/1',
        'source': 'CDC/NCHS official ICD-10-CM FY2027 order and tabular release',
        'license': 'Official US government ICD-10-CM release; source notices and ICD-10-CM terms apply. No third-party terminology included.',
        'release': RELEASE, 'effective_from': '2026-10-01', 'effective_to': '2027-09-30',
        'sources': SOURCES, 'concepts': concepts,
        'policy': 'Fixed FY2027 research normalization across historical articles; not retrospective billing. '
                  'Leaf membership and retained instructions do not validate clinical equivalence or sequencing.'}


def build(output, *, descriptions=None, tabular=None):
    output = Path(output)
    if output.exists():
        raise ValueError('Do not overwrite a frozen terminology catalog')
    catalog = parse_release(read_release('descriptions', descriptions), read_release('tabular', tabular))
    if len(catalog['concepts']) != 74879:
        raise ValueError('Unexpected FY2027 leaf-code count')
    raw = json.dumps(catalog, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(gzip.compress(raw, mtime=0) if output.suffix == '.gz' else raw)
    return {'path': str(output), 'sha256': hashlib.sha256(output.read_bytes()).hexdigest(),
            'concepts': len(catalog['concepts']), 'release': RELEASE}


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--output', required=True)
    p.add_argument('--descriptions', help='Optional local pinned CDC descriptions ZIP')
    p.add_argument('--tabular', help='Optional local pinned CDC tabular ZIP')
    args = p.parse_args(argv)
    print(json.dumps(build(args.output, descriptions=args.descriptions, tabular=args.tabular), indent=2))


if __name__ == '__main__':
    main()
