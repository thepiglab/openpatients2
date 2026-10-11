"""The single map from (root, kind, article, role, supplement) to a file path.

Every per-article file of a dense benchmark root lives in one folder:

    <root>/articles/<ID>/
        source.json                      sources            (tracked)
        source.txt                       source-text        (cache, gitignored)
        raw.xml                          raw-xml            (cache, gitignored)
        annotation_draft.json            drafts             (annotator working copy)
        annotation.json                  annotations        (promoted, parent-reviewed annotation)
        schema_target.json               schema-submissions
        schema_target.draft.json         schema-drafts      (cache)
        figure_assets.json               figure-assets
        visual.json                      visual-submissions
        figure_ehr_labels.json           figure-ehr-labels
        figure_ehr_labels.draft.json     figure-ehr-label-drafts      (cache)
        figure_privacy_flags.json        figure-privacy-flags
        figure_privacy_flags.draft.json  figure-privacy-flag-drafts   (cache)
        measurement_series.json          measurement-series
        measurement_series.draft.json    measurement-series-drafts    (cache)
        needs_review.json                needs-review
        video_inspection.<FID>.<role>.json   video-inspections
        supplements/
            assets.json                  supplement-assets
            manifest.json                supplement-manifests
            manifest.draft.json          supplement-manifest-drafts   (cache)
            <SID>.visual.json            supplement-visual-submissions (old visual-submissions/ID.supplements*.json)
            <SID>.figure_assets.json     supplement-figure-assets (old figure-assets/ID.supplements.json)
            <SID>.document.json          document-submissions
            <SID>.document.draft.json    document-submission-drafts   (cache)
            <SID>.archive_members.json   supplement-archive-members   (cache)
        receipts/
            annotation.parent.json       reviews  (review_record.save_review)
            <kind>.<role>.json           <kind>-<role>-reviews, kind in RECEIPT_KINDS
            supplement.<SID>.<role>.json supplement-<role>-reviews

Kinds keep the old folder names (logical names), so CLI flags such as
`--folder drafts|annotations` and receipt fields such as
`annotation_folder: "annotations"` are unchanged. Nothing else in the
repository may build these paths by hand: use `path` / `receipt` (mkdir=True for writers),
`article_ids` / `matching_ids`, `supplement_ids` / `supplement_targets` and `article_dirs`.
Migration from the old layout: migrations/per_article_layout.py.
"""
from pathlib import Path
import sys
import re

ARTICLES = 'articles'

FILES = {
    'sources': 'source.json',
    'source-text': 'source.txt',
    'raw-xml': 'raw.xml',
    'drafts': 'annotation_draft.json',
    'annotations': 'annotation.json',
    'reviews': 'receipts/annotation.parent.json',
    'schema-submissions': 'schema_target.json',
    'schema-drafts': 'schema_target.draft.json',
    'figure-assets': 'figure_assets.json',
    'visual-submissions': 'visual.json',
    'figure-ehr-labels': 'figure_ehr_labels.json',
    'figure-ehr-label-drafts': 'figure_ehr_labels.draft.json',
    'figure-privacy-flags': 'figure_privacy_flags.json',
    'figure-privacy-flag-drafts': 'figure_privacy_flags.draft.json',
    'measurement-series': 'measurement_series.json',
    'measurement-series-drafts': 'measurement_series.draft.json',
    'needs-review': 'needs_review.json',
    'supplement-assets': 'supplements/assets.json',
    'supplement-manifests': 'supplements/manifest.json',
    'supplement-manifest-drafts': 'supplements/manifest.draft.json',
}
# Kinds with one file per supplement (sid); `{sid}` is the supplement id from the source.
SUPPLEMENT_FILES = {
    'supplement-visual-submissions': 'supplements/{sid}.visual.json',
    'supplement-figure-assets': 'supplements/{sid}.figure_assets.json',
    'document-submissions': 'supplements/{sid}.document.json',
    'document-submission-drafts': 'supplements/{sid}.document.draft.json',
    'supplement-archive-members': 'supplements/{sid}.archive_members.json',
}
# Agent/parent review receipts: logical folder `<kind>-<role>-reviews`.
RECEIPT_KINDS = ('schema', 'visual', 'figure-ehr', 'figure-privacy', 'measurement-series', 'supplement-manifest',
                 'supplement')
ROLES = ('agent', 'parent')
VIDEO = 'video-inspections'   # one file per (figure id, role): video_inspection.<FID>.<role>.json
# Regenerable files (gitignored): rebuilt by acquire.py, schema_targets.py, *_review.py --draft, supplement_assets.py.
CACHE_KINDS = ('source-text', 'raw-xml', 'schema-drafts', 'figure-ehr-label-drafts', 'figure-privacy-flag-drafts',
               'measurement-series-drafts', 'supplement-manifest-drafts', 'document-submission-drafts',
               'supplement-archive-members')
_RECEIPT_FOLDER = re.compile(r'^(?P<kind>.+)-(?P<role>agent|parent)-reviews$')
_NAME = re.compile(r'^[A-Za-z0-9][A-Za-z0-9_.-]*$')


def _check(name, what):
    if not name or not _NAME.fullmatch(str(name)) or '..' in str(name):
        raise ValueError(f'Invalid {what}: {name!r}')
    return str(name)


def article_dir(root, aid):
    return Path(root) / ARTICLES / _check(aid, 'article id')


def relative(kind, aid=None, role=None, sid=None):
    """Path of one file relative to `articles/<ID>/`."""
    match = _RECEIPT_FOLDER.fullmatch(kind)
    if match:                                        # legacy receipt folder name, e.g. 'schema-agent-reviews'
        if role not in (None, match['role']):
            raise ValueError(f'{kind} conflicts with role {role!r}')
        kind, role = match['kind'], match['role']
    if role is not None:
        if role not in ROLES:
            raise ValueError(f'Unknown review role {role!r}')
        if kind == 'annotation' and role == 'parent':
            return FILES['reviews']
        if kind not in RECEIPT_KINDS:
            raise ValueError(f'Unknown receipt kind {kind!r}')
        if kind == 'supplement':
            return f'receipts/supplement.{_check(sid, "supplement id")}.{role}.json'
        if sid is not None:
            raise ValueError(f'{kind} receipts are per article, not per supplement')
        return f'receipts/{kind}.{role}.json'
    if kind == VIDEO:
        raise ValueError('video-inspections need sid=<figure id> and role')
    if kind in SUPPLEMENT_FILES:
        return SUPPLEMENT_FILES[kind].format(sid=_check(sid, 'supplement id'))
    if kind in FILES:
        if sid is not None:
            raise ValueError(f'{kind} is per article, not per supplement')
        return FILES[kind]
    raise ValueError(f'Unknown store kind {kind!r}')


def path(root, kind, aid, role=None, sid=None, *, mkdir=False):
    """articles/<aid>/<file> for a logical kind (old folder name), receipt kind+role, or supplement kind+sid.

    mkdir=True creates the parent folders (for writers)."""
    if kind == VIDEO:
        out = article_dir(root, aid) / f'video_inspection.{_check(sid, "figure id")}.{_check(role, "role")}.json'
    else:
        out = article_dir(root, aid) / relative(kind, aid, role, sid)
    if mkdir:
        out.parent.mkdir(parents=True, exist_ok=True)
    return out


def receipt(root, kind, role, aid, sid=None, *, mkdir=False):
    return path(root, kind, aid, role=role, sid=sid, mkdir=mkdir)


def article_dirs(root):
    folder = Path(root) / ARTICLES
    return sorted(p for p in folder.iterdir() if p.is_dir() and _NAME.fullmatch(p.name)) if folder.is_dir() else []


def _per_supplement(kind):
    match = _RECEIPT_FOLDER.fullmatch(kind)
    return kind in SUPPLEMENT_FILES or kind == 'supplement' or bool(match and match['kind'] == 'supplement')


def article_ids(root, kind='sources', role=None):
    """Sorted article ids that have a file of this kind (any supplement for per-supplement kinds)."""
    if _per_supplement(kind):
        return [d.name for d in article_dirs(root) if supplement_ids(root, kind, d.name, role=role)]
    return [d.name for d in article_dirs(root) if path(root, kind, d.name, role=role).exists()]


def matching_ids(root, kind, pattern=None):
    """article_ids filtered by an optional shell-style pattern (an exact id or e.g. 'PMC128*')."""
    import fnmatch
    return [aid for aid in article_ids(root, kind) if fnmatch.fnmatchcase(aid, pattern or '*')]


def supplement_ids(root, kind, aid, role=None):
    """Sorted supplement ids with a file of this per-supplement kind (or supplement receipt for `role`)."""
    match = _RECEIPT_FOLDER.fullmatch(kind)
    if match:
        kind, role = match['kind'], match['role']
    if kind == 'supplement':
        if role not in ROLES:
            raise ValueError('Supplement receipts need role agent|parent')
        pattern, prefix, suffix = f'receipts/supplement.*.{role}.json', 'supplement.', f'.{role}.json'
    else:
        template = SUPPLEMENT_FILES[kind]
        pattern = template.format(sid='*')
        prefix, suffix = template.split('/')[-1].split('{sid}')
    out = []
    for p in article_dir(root, aid).glob(pattern):
        sid = p.name[len(prefix):len(p.name) - len(suffix)]
        if sid and _NAME.fullmatch(sid) and p.name == relative(kind, aid, role, sid).split('/')[-1]:
            out.append(sid)
    return sorted(out)


def supplement_targets(root, aid):
    """Paths of reviewable supplement sidecars: document targets first, then image targets (each by sid)."""
    return [path(root, kind, aid, sid=sid) for kind in ('document-submissions', 'supplement-visual-submissions')
            for sid in supplement_ids(root, kind, aid)]


def video_inspections(root, aid):
    return sorted(article_dir(root, aid).glob('video_inspection.*.json'))


def root_of(file_path):
    """Benchmark root that contains a store file (inverse of `path`)."""
    p = Path(file_path)
    for parent in p.parents:
        if parent.parent.name == ARTICLES:
            return parent.parent.parent
    raise ValueError(f'{file_path} is not inside a <root>/{ARTICLES}/<ID>/ folder')



def load():
    """Backward-compatible accessor (package modules used to load the store by file path)."""
    return sys.modules[__name__]
