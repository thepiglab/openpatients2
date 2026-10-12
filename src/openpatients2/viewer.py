"""Read-only article browser: live reviewed targets plus portable dataset exports.

No annotation/export jobs run here. Receipts bind live targets to their source;
partially written or stale targets are withheld rather than shown as reviewed.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import threading
from collections import defaultdict
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlsplit

from .dense_store import load as _load_store

STATIC = Path(__file__).with_name('viewer_static')
ASSETS = ('index.html', 'app.js', 'style.css', 'manifest.webmanifest', 'icon-180.png', 'icon-512.png')
TYPES = {'html': 'text/html; charset=utf-8', 'js': 'text/javascript; charset=utf-8', 'css': 'text/css; charset=utf-8',
         'webmanifest': 'application/manifest+json; charset=utf-8', 'png': 'image/png'}
ID = re.compile(r'^[A-Za-z0-9][A-Za-z0-9_.-]{0,120}$')
FILES = ('manifest', 'annotations', 'patients', 'article_bundles', 'figure_visuals',
         'supplement_visuals', 'supplement_documents')
LIVE = ('sources', 'annotations', 'reviews', 'schema-submissions', 'schema-agent-reviews',
        'schema-parent-reviews', 'visual-submissions', 'visual-agent-reviews', 'visual-parent-reviews',
        'figure-ehr-labels', 'figure-ehr-agent-reviews', 'figure-ehr-parent-reviews',
        'figure-privacy-flags', 'figure-privacy-agent-reviews', 'figure-privacy-parent-reviews')


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                     separators=(',', ':')).encode()).hexdigest()


def read(path):
    return json.loads(path.read_text()) if path.exists() else None


def stamp(path):
    try:
        stat = path.stat()
        return (stat.st_mtime_ns, stat.st_size)
    except FileNotFoundError:
        return None


def scrub(value):
    """Drop workstation file paths (e.g. cached figure copies) from anything sent to a browser."""
    if isinstance(value, dict):
        return {k: scrub(v) for k, v in value.items()
                if not (isinstance(v, str) and (k == 'path' or k.endswith('_path')) and Path(v).is_absolute())}
    if isinstance(value, list):
        return [scrub(v) for v in value]
    return value


def bound(receipt, **expected):
    return bool(receipt) and all(receipt.get(key) == value for key, value in expected.items())


class Library:
    def __init__(self, dataset: Path, source_roots: list[Path]):
        self.dataset = dataset.resolve()
        # The release is now unified rows in data/; the viewer reads the per-type
        # back-compat files the exporter keeps under legacy/.
        if not (self.dataset / 'manifest.jsonl').exists() and (self.dataset / 'legacy').is_dir():
            self.dataset = self.dataset / 'legacy'
        self.roots = [root.resolve() for root in source_roots]
        self.lock = threading.RLock()
        self.export_stamp = None
        self.exports = {}
        self.entries = {}

    def _exports(self):
        signature = tuple(stamp(self.dataset / f'{name}.jsonl') for name in FILES)
        if signature != self.export_stamp:
            fresh = {}
            for name in FILES:
                rows = defaultdict(list)
                path = self.dataset / f'{name}.jsonl'
                if path.exists():
                    with path.open() as stream:
                        for line in stream:
                            if line.strip():
                                row = json.loads(line)
                                rows[row['article_id']].append(row)
                fresh[name] = rows
            # Do not install a snapshot if any input changed during the read.
            if signature != tuple(stamp(self.dataset / f'{name}.jsonl') for name in FILES):
                raise ValueError('Dataset is being updated; retry shortly')
            self.exports, self.export_stamp = fresh, signature

    def _source(self, aid):
        store = _load_store()
        paths = [(store.path(root, 'sources', aid), root) for root in self.roots]
        paths = [(path, root) for path, root in paths if path.exists()]
        if not paths:
            return None, None
        path, root = max(paths, key=lambda p: p[0].stat().st_mtime_ns)
        source = read(path)
        if source.get('article_id') != aid:
            raise ValueError('Source identity mismatch')
        return source, root

    def _live(self, source, root):
        aid, sha = source['article_id'], source['text_sha256']
        store = _load_store()
        annotation = read(store.path(root, 'annotations', aid))
        review = read(store.path(root, 'reviews', aid))
        annotation_ok = bool(annotation) and annotation.get('article_id') == aid and bound(review, article_id=aid,
            status='source_reviewed', source_read_in_full=True, source_text_sha256=sha,
            annotation_sha256=digest(annotation)) and annotation.get('text_sha256') == sha
        target = read(store.path(root, 'schema-submissions', aid))
        schema_ok = (annotation_ok and bool(target) and target.get('article_id') == aid
                     and target.get('source_text_sha256') == sha
                     and target.get('annotation_sha256') == digest(annotation)
                     and not target.get('source_gate_errors'))
        if schema_ok:
            for role in ('agent', 'parent'):
                receipt = read(store.receipt(root, 'schema', role, aid))
                schema_ok &= bound(receipt, article_id=aid, role=role, status='schema_source_reviewed',
                    source_text_sha256=sha, annotation_sha256=digest(annotation), target_sha256=digest(target))
        visual = read(store.path(root, 'visual-submissions', aid))
        visual_ok = (annotation_ok and bool(visual) and visual.get('article_id') == aid
                     and visual.get('source_text_sha256') == sha)
        if visual_ok:
            identity = digest([{k: p[k] for k in ('patient_id', 'label', 'species', 'identity_evidence')}
                               for p in annotation['patients']])
            for role in ('agent', 'parent'):
                visual_ok &= bound(read(store.receipt(root, 'visual', role, aid)),
                    article_id=aid, role=role, status='source_and_pixels_reviewed',
                    source_text_sha256=sha, target_sha256=digest(visual), patient_identity_sha256=identity,
                    patient_ids=sorted(p['patient_id'] for p in annotation['patients']))
        return (annotation if annotation_ok else None, target if schema_ok else None,
                visual if visual_ok else None)

    @staticmethod
    def _overlays(root, aid):
        """Dual-signed, current figure EHR labels and privacy flags; stale or unsigned ones are withheld."""
        from .dense.figure_ehr_review import reviewed_figure_ehr
        from .dense.figure_privacy_review import reviewed_privacy_flags
        out = []
        for reviewed in (reviewed_figure_ehr, reviewed_privacy_flags):
            try:
                result = reviewed(root, aid)
            except (ValueError, KeyError, OSError, TypeError):
                result = None
            out.append(result[0] if result else None)
        return out

    @staticmethod
    def _supplements(root, aid):
        """Live dual-signed supplement sidecars (documents, visuals) and the reviewed supplement manifest."""
        from .dense.supplement_review import DOCUMENT_FORMATS, reviewed_supplement_targets
        from .dense.supplement_manifest_review import reviewed_manifest
        try:
            targets = [target for target, _ in reviewed_supplement_targets(root, aid)]
        except (ValueError, KeyError, OSError, TypeError):
            targets = []
        try:
            manifest = reviewed_manifest(root, aid)
        except (ValueError, KeyError, OSError, TypeError):
            manifest = None
        documents = [t for t in targets if t.get('format') in DOCUMENT_FORMATS or 'document_classification' in t]
        visuals = [t for t in targets if t not in documents]
        return documents, visuals, manifest[0] if manifest else None

    def _detail(self, aid):
        source, root = self._source(aid)
        manifest = next(iter(self.exports['manifest'].get(aid, [])), {})
        annotation, target, visual = self._live(source, root) if source else (None, None, None)
        ehr_labels, privacy_flags = self._overlays(root, aid) if visual else (None, None)
        published_annotation = next(iter(self.exports['annotations'].get(aid, [])), None)
        published_patients = self.exports['patients'].get(aid, [])
        published_article = next(iter(self.exports['article_bundles'].get(aid, [])), {})
        # An export is already reviewed by its exporter. Its text identity must
        # still match the current reading surface, and live receipts take priority.
        published_ok = bool(published_annotation) and (not source or
            published_annotation.get('text_sha256') == source.get('text_sha256'))
        if root:
            # Never resurrect a revoked/stale live receipt using an older export.
            patients = target['patients'] if target else []
            article = target if target else {}
        else:
            patients = published_patients if published_ok else []
            article = published_article if published_ok else {}
            annotation = published_annotation if published_ok else None
            visual = next(iter(self.exports['figure_visuals'].get(aid, [])), None) if published_ok else None
        # Use enriched exported patients only if their schema receipts bind the
        # very same live target. They include normalized measurements/media.
        if target and published_patients and all(any(r.get('target_sha256') == digest(target)
            for r in p.get('review', {}).get('full_schema', [])) for p in published_patients):
            patients = published_patients
        note = []
        if not source:
            note.append('Source text is not installed on this server. The original article is linked below.')
        roster = article.get('roster') or {}
        schema_reviewed = bool(patients or roster)
        if schema_reviewed and not patients:
            count = roster.get('reported_individual_count')
            note.append(f"No individual patient records: the article reports {count or 'its'} patients "
                        'only as an aggregate series.')
        elif not patients:
            note.append('Reviewed full-schema extraction is not yet available for this article.')
        if source and not annotation and _load_store().path(root, 'reviews', aid).exists():
            note.append('Source review is stale or being updated; its extraction is withheld until receipts match.')
        supplementary = []
        documents = []
        published_current = (published_ok and annotation and (not root or
                             published_annotation.get('review', {}).get('annotation_sha256') == digest(annotation)))
        manifest_review = None
        if root and annotation and source.get('supplements'):
            documents, supplementary, manifest_review = self._supplements(root, aid)
        if published_current and not (documents or supplementary):
            supplementary = self.exports['supplement_visuals'].get(aid, [])
            documents = self.exports['supplement_documents'].get(aid, [])
        source_view = {k: source.get(k) for k in ('article_id', 'title', 'authors', 'pmcid', 'pmid',
            'doi', 'source_url', 'license', 'text_sha256', 'segments', 'figures', 'supplements', 'limitations',
            'references')} if source else None
        # Source reading surfaces contain captions/URLs but no workstation paths.
        meta = {**manifest, 'article_id': aid}
        if source:
            meta.update(title=source['title'], article_url=source.get('source_url'),
                        source_license=source.get('license'), authors=manifest.get('authors', source.get('authors', [])))
        meta.update(status='schema_reviewed' if schema_reviewed else 'source_reviewed' if annotation else 'pending',
                    patient_count=len(patients), figure_reviewed=bool(visual),
                    disposition=roster.get('disposition'),
                    figure_count=sum(bool(f.get('media')) for f in (source or {}).get('figures') or []),
                    has_tables=any(s.get('kind') == 'table_row' for s in (source or {}).get('segments') or []),
                    supplement_count=len((source or {}).get('supplements') or []),
                    article_type=(source or {}).get('article_type') or manifest.get('article_type'),
                    figure_ehr_labeled=bool(ehr_labels),
                    supplements_extracted=len(documents) + len(supplementary),
                    privacy_concern=any(p.get('identifiable_face') or p.get('sensitive_anatomy')
                                        or p.get('burned_in_identifiers') == 'direct_identifiers'
                                        for f in (privacy_flags or {}).get('figures', []) for p in f.get('panels', [])),
                    text_available=bool(source))
        return scrub({'article_id': aid, 'metadata': meta, 'source': source_view, 'patients': patients,
                'annotation': annotation, 'roster': article.get('roster'),
                'figure_attribution': article.get('figure_attribution'), 'visual': visual,
                'figure_ehr_labels': ehr_labels, 'figure_privacy_flags': privacy_flags,
                'supplement_visuals': supplementary, 'supplement_documents': documents,
                'supplement_manifest': manifest_review, 'notices': note, 'revision': None})

    def catalog(self):
        with self.lock:
            self._exports()
            ids = set(self.exports['manifest'])
            store = _load_store()
            ids.update(aid for root in self.roots for aid in store.article_ids(root, 'sources'))
            rows = []
            for aid in sorted(ids):
                if not ID.fullmatch(aid):
                    continue
                # Per-supplement sidecars and receipts are many files; their folders' mtimes cover adds/replaces.
                signature = (self.export_stamp, tuple(stamp(store.path(root, kind, aid))
                             for root in self.roots for kind in LIVE),
                             tuple(stamp(store.article_dir(root, aid) / sub) for root in self.roots
                                   for sub in ('supplements', 'receipts')))
                cached = self.entries.get(aid)
                if cached is None or cached[0] != signature:
                    try:
                        detail = self._detail(aid)
                        meta = detail['metadata']
                        row = {k: meta.get(k) for k in ('article_id', 'title', 'primary_stratum', 'status',
                               'patient_count', 'figure_reviewed', 'text_available', 'article_url',
                               'disposition', 'figure_count', 'has_tables', 'supplement_count', 'article_type',
                               'figure_ehr_labeled', 'privacy_concern', 'supplements_extracted')}
                        row['license'] = (meta.get('source_license') or {}).get('code', 'Unknown')
                        row['revision'] = digest(signature)
                        self.entries[aid] = (signature, row)
                    except (ValueError, KeyError, OSError, TypeError):
                        # Writer may be between annotation and receipt updates.
                        row = {'article_id': aid, 'title': aid, 'status': 'updating', 'patient_count': 0,
                               'revision': digest(signature), 'text_available': False}
                else:
                    row = cached[1]
                rows.append(row)
            revision = digest(rows)
            return {'articles': rows, 'revision': revision,
                    'updated_at': datetime.now(timezone.utc).isoformat(),
                    'note': 'AI source-reviewed reference annotations; not physician-adjudicated gold.'}

    def article(self, aid):
        if not ID.fullmatch(aid):
            raise KeyError(aid)
        with self.lock:
            catalog = self.catalog()
            row = next((r for r in catalog['articles'] if r['article_id'] == aid), None)
            if not row:
                raise KeyError(aid)
            result = self._detail(aid)
            result['revision'] = row['revision']
            return result


def handler(library):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            path = unquote(urlsplit(self.path).path)
            try:
                if path == '/api/catalog.json':
                    return self.send_json(library.catalog())
                if path.startswith('/api/articles/') and path.endswith('.json'):
                    return self.send_json(library.article(path[len('/api/articles/'):-5]))
                name = 'index.html' if path == '/' else path[1:]
                if name not in ASSETS:
                    return self.send_json({'error': 'Not found'}, 404)
                file = STATIC / name
                self.send_bytes(file.read_bytes(), TYPES[file.suffix[1:]])
            except KeyError:
                self.send_json({'error': 'Unknown article'}, 404)
            except (ValueError, OSError, TypeError):
                self.send_json({'error': 'Article data is being updated. Retrying shortly.'}, 503)

        def send_json(self, data, status=200):
            self.send_bytes(json.dumps(data, ensure_ascii=False).encode(), 'application/json; charset=utf-8', status)

        def send_bytes(self, data, kind, status=200):
            self.send_response(status)
            self.send_header('Content-Type', kind)
            self.send_header('Content-Length', str(len(data)))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header('Content-Security-Policy', "default-src 'self'; img-src 'self' https:; style-src 'self'; script-src 'self'; connect-src 'self'; object-src 'none'; base-uri 'self'; frame-ancestors 'none'")
            self.end_headers()
            self.wfile.write(data)
    return Handler


def build(library, output):
    """Publish only the viewer/data; never expose drafts or arbitrary local files."""
    output = output.resolve()
    if output.exists() and any(output.iterdir()):
        raise ValueError('Static output must be new or empty; existing files are never overwritten')
    output.mkdir(parents=True, exist_ok=True)
    for name in ASSETS:
        shutil.copyfile(STATIC / name, output / name)
    api = output / 'api'
    (api / 'articles').mkdir(parents=True, exist_ok=True)
    catalog = library.catalog()
    with library.lock:
        for row in catalog['articles']:
            detail = library._detail(row['article_id'])
            detail['revision'] = row['revision']
            (api / 'articles' / f"{row['article_id']}.json").write_text(json.dumps(detail, ensure_ascii=False))
        if library.catalog()['revision'] != catalog['revision']:
            raise ValueError('Inputs changed during build; rebuild into a new directory for a consistent snapshot')
    (api / 'catalog.json').write_text(json.dumps(catalog, ensure_ascii=False))
    return {'output': str(output), 'articles': len(catalog['articles'])}


def add_parser(sub):
    parser = sub.add_parser('viewer', help='Browse every article and its latest reviewed extraction')
    parser.add_argument('--dataset', type=Path, default=Path('data/dense_clinical_cases_v1'))
    parser.add_argument('--source-root', type=Path, action='append', help='Annotation root (repeatable); defaults to dense50 and dense500')
    parser.add_argument('--host', default='127.0.0.1')
    parser.add_argument('--port', type=int, default=8765)
    parser.add_argument('--build', type=Path, help='Write a portable static website into a new/empty directory')


def dispatch(args):
    library = Library(args.dataset, args.source_root or [Path('benchmarks/dense50'), Path('benchmarks/dense500')])
    if args.build:
        return build(library, args.build)
    server = ThreadingHTTPServer((args.host, args.port), handler(library))
    print(f'Article browser: http://{args.host}:{server.server_port} (refreshes every 15 seconds)', flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return {'status': 'stopped'}
