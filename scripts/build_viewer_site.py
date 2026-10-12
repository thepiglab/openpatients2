"""Build the existing viewer for Pages; optimize only generated files."""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import tempfile
from pathlib import Path

from openpatients2.viewer import ASSETS, STATIC, Library, build


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('output', type=Path, nargs='?', default=Path('_site'))
    args = parser.parse_args()
    if not shutil.which('bun'):
        parser.error('Bun is required to minify the generated JavaScript and CSS')

    repo = Path(__file__).resolve().parents[1]
    roots = [repo / 'benchmarks' / name for name in ('dense50', 'dense500')]
    expected = {p.parent.name for root in roots for p in root.glob('articles/*/source.json')}
    if not expected:
        raise ValueError('No article sources found; refusing to publish an empty library')

    # Local data/ exports are ignored by Git. Use the same checked-in sources and
    # receipt-gated records locally and on CI, without rebuilding annotations.
    with tempfile.TemporaryDirectory(prefix='op2-pages-') as empty_export:
        result = build(Library(Path(empty_export), roots), args.output)
    output = Path(result['output'])
    catalog = json.loads((output / 'api/catalog.json').read_text())
    if {row['article_id'] for row in catalog['articles']} != expected:
        raise ValueError('Static catalog does not contain every acquired article')
    for row in catalog['articles']:
        detail = json.loads((output / 'api/articles' / f"{row['article_id']}.json").read_text())
        if row['status'] == 'updating' or detail['revision'] != row['revision']:
            raise ValueError(f"Inconsistent article snapshot: {row['article_id']}")

    before = sum(p.stat().st_size for p in output.rglob('*') if p.is_file())
    # Keep the original filenames and classic deferred script loading unchanged.
    subprocess.run(['bun', 'build', str(STATIC / 'app.js'), '--target=browser',
                    '--format=iife', '--minify', '--outfile', str(output / 'app.js')], check=True)
    subprocess.run(['bun', 'build', str(STATIC / 'style.css'), '--minify',
                    '--outfile', str(output / 'style.css')], check=True)
    for path in [*output.rglob('*.json'), output / 'manifest.webmanifest']:
        value = json.loads(path.read_text(encoding='utf-8'))
        path.write_text(json.dumps(value, ensure_ascii=False, separators=(',', ':')), encoding='utf-8')
    (output / '.nojekyll').touch()
    for name in ASSETS:
        if not (output / name).stat().st_size:
            raise ValueError(f'Empty viewer asset: {name}')
    after = sum(p.stat().st_size for p in output.rglob('*') if p.is_file())
    if after >= 1_000_000_000:
        raise ValueError('Generated site exceeds the GitHub Pages 1 GB site limit')
    print(f"Built {result['articles']} articles in {output}; {before:,} → {after:,} bytes")


if __name__ == '__main__':
    main()
