"""Move a dense root from ~45 parallel folders to one folder per article.

    PYTHONPATH=src:benchmarks/dense500 .venv/bin/python benchmarks/dense500/migrations/per_article_layout.py \
        --root benchmarks/dense500 [--apply]

Old layout: <root>/<kind>/<ID>.json (plus <kind>-<role>-reviews/, supplement and video
variants). New layout: <root>/articles/<ID>/... as defined by store.py (the only path map).
Without --apply it prints the planned moves. With --apply it moves every file (git-tracked
files are re-staged as renames; gitignored caches are moved plainly), checks that the
sha256 of every moved file is unchanged, and removes the emptied old folders. Content is
never edited, so receipts (which bind content hashes, not paths) stay valid. Idempotent:
a second run finds nothing to move.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import store  # noqa: E402

# Folders that are not per-article data and stay at the root.
KEEP = {'acquisition', 'assignments', 'claude_prompts', 'migrations', '__pycache__', store.ARTICLES}
AID = r'(?P<aid>[A-Za-z]+[0-9]+\.[0-9]+)'
SUPPLEMENT_TARGET = re.compile(AID + r'\.supplements(?:\.(?P<sid>.+))?\.json$')


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _target_sid(path, named):
    sid = json.loads(Path(path).read_text()).get('supplement_id')
    if not sid or (named is not None and named != sid):
        raise ValueError(f'{path}: supplement_id {sid!r} does not match file name {named!r}')
    return sid


def destination(root, folder, name):
    """New path for old <root>/<folder>/<name>, or raise for anything unmapped."""
    src = Path(root) / folder / name
    if folder in ('source-text', 'raw-xml'):
        m = re.fullmatch(AID + (r'\.txt' if folder == 'source-text' else r'\.xml'), name)
        if m:
            return store.path(root, folder, m['aid'])
    elif folder in ('visual-submissions', 'figure-assets', 'document-submissions', 'document-submission-drafts'):
        m = SUPPLEMENT_TARGET.fullmatch(name)
        if m:
            kind = {'visual-submissions': 'supplement-visual-submissions',
                    'figure-assets': 'supplement-figure-assets'}.get(folder, folder)
            if kind == folder and m['sid'] is None:
                raise ValueError(f'{src}: document target without supplement id in its name')
            return store.path(root, kind, m['aid'], sid=_target_sid(src, m['sid']))
        m = re.fullmatch(AID + r'\.json', name)
        if m and folder in ('visual-submissions', 'figure-assets'):
            return store.path(root, folder, m['aid'])
    elif folder in ('supplement-agent-reviews', 'supplement-parent-reviews', 'supplement-archive-members'):
        m = re.fullmatch(AID + r'\.(?P<sid>.+)\.json', name)
        if m:
            if folder == 'supplement-archive-members':
                if json.loads(src.read_text()).get('supplement_id') != m['sid']:
                    raise ValueError(f'{src}: supplement_id does not match file name')
                return store.path(root, folder, m['aid'], sid=m['sid'])
            return store.path(root, 'supplement', m['aid'], role=folder.split('-')[1], sid=m['sid'])
    elif folder == store.VIDEO:
        m = re.fullmatch(AID + r'\.(?P<fid>[^.]+)\.(?P<role>agent|parent)\.json', name)
        if m:
            return store.path(root, folder, m['aid'], role=m['role'], sid=m['fid'])
    else:
        m = re.fullmatch(AID + r'\.json', name)
        if m:
            return store.path(root, folder, m['aid'])   # raises for an unknown folder
    raise ValueError(f'Unmapped file {src}')


def plan(root):
    root = Path(root)
    moves, errors = [], []
    for folder in sorted(p for p in root.iterdir() if p.is_dir() and p.name not in KEEP):
        for src in sorted(p for p in folder.rglob('*') if p.is_file()):
            if src.parent != folder:
                errors.append(f'Nested file not mapped: {src}'); continue
            if src.name == '.DS_Store':
                continue
            try:
                moves.append((src, destination(root, folder.name, src.name)))
            except ValueError as exc:
                errors.append(str(exc))
    targets = [d for _, d in moves]
    if len(targets) != len(set(targets)):
        errors.append('Two old files map to the same new path')
    for src, dst in moves:
        if dst.exists():
            errors.append(f'Destination already exists: {dst} (from {src})')
    return moves, errors


def git(args, cwd, stdin=None):
    return subprocess.run(['git', *args], cwd=cwd, input=stdin, text=True, check=True, capture_output=True).stdout


def apply(root, moves):
    root = Path(root).resolve()
    try:
        top = Path(git(['rev-parse', '--show-toplevel'], root).strip())
        tracked = set(git(['ls-files', '-z', '--', str(root)], top).split('\0')) - {''}
    except subprocess.CalledProcessError:   # not a git checkout (e.g. a copied root): plain moves only
        top, tracked = root, set()
    rel = lambda p: str(Path(p).resolve().relative_to(top))  # noqa: E731
    before = {src: sha256(src) for src, _ in moves}
    old_tracked, new_tracked = [], []
    for src, dst in moves:
        dst.parent.mkdir(parents=True, exist_ok=True)
        os.replace(src, dst)
        if rel(src) in tracked:
            old_tracked.append(rel(src)); new_tracked.append(rel(dst))
    changed = [str(src) for src, dst in moves if sha256(dst) != before[src]]
    if changed:
        raise SystemExit(f'sha256 changed for {len(changed)} moved files: {changed[:5]}')
    # Equivalent of `git mv` for every tracked file (one index update instead of thousands of calls).
    if old_tracked:
        git(['rm', '--cached', '-q', '--pathspec-from-file=-', '--pathspec-file-nul'], top, '\0'.join(old_tracked))
        git(['add', '--pathspec-from-file=-', '--pathspec-file-nul'], top, '\0'.join(new_tracked))
    for folder in sorted({src.parent for src, _ in moves}, reverse=True):
        ds = folder / '.DS_Store'
        if ds.exists():
            ds.unlink()
        if folder.exists() and not any(folder.iterdir()):
            folder.rmdir()
    return {'moved': len(moves), 'tracked_renames': len(old_tracked), 'untracked_moves': len(moves) - len(old_tracked),
            'sha256_verified': len(moves)}


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('--root', type=Path, required=True); p.add_argument('--apply', action='store_true')
    p.add_argument('--verbose', action='store_true', help='List every planned move')
    args = p.parse_args()
    moves, errors = plan(args.root)
    if errors:
        print(json.dumps({'errors': errors[:50], 'error_count': len(errors)}, indent=2)); raise SystemExit(1)
    summary = {}
    for src, dst in moves:
        key = f'{src.parent.name}/ -> {dst.relative_to(store.article_dir(args.root, dst.relative_to(args.root / store.ARTICLES).parts[0]))}'
        key = re.sub(r'(supplement\.|video_inspection\.)[^/]+?\.(agent|parent)\.json$', r'\1<SID>.\2.json', key)
        key = re.sub(r'supplements/[^/]+\.(visual|document|document\.draft|archive_members|figure_assets)\.json$', r'supplements/<SID>.\1.json', key)
        summary[key] = summary.get(key, 0) + 1
    report = {'root': str(args.root), 'planned_moves': len(moves), 'by_kind': summary}
    if args.verbose:
        report['moves'] = [[str(s), str(d)] for s, d in moves]
    if args.apply and moves:
        report['applied'] = apply(args.root, moves)
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
