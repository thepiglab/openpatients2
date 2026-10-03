#!/usr/bin/env python3
"""Build a small allowlisted acquisition package for a separate cluster checkout."""
import argparse
import hashlib
import json
from pathlib import Path
import tarfile


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=Path('dist/openpatients2-pmc-acquisition.tar.gz'))
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    if args.output.exists():
        raise ValueError('Choose a new archive path')
    files = [root / name for name in ('pyproject.toml','uv.lock','LICENSE','docs/PMC_ACQUISITION.md','docs/ARTICLE_LICENSE_POLICY.md',
        'scripts/pmc_acquire.sbatch','scripts/smoke_pmc_acquisition.py','scripts/package_pmc_acquisition.py',
        'configs/sources/corpus-pilot.yaml','configs/sources/corpus-hpg.yaml',
        'configs/sources/corpus-stratified-pilot.yaml')]
    files += sorted(p for p in (root/'src/openpatients2').rglob('*') if p.is_file() and p.suffix in {'.py','.md'})
    args.output.parent.mkdir(parents=True,exist_ok=True)
    with tarfile.open(args.output, 'w:gz') as tar:
        for path in files:
            if path.is_symlink(): raise ValueError('Symlinks are not packaged')
            tar.add(path,arcname=str(path.relative_to(root)),recursive=False)
    report = {'archive':str(args.output),'bytes':args.output.stat().st_size,
              'sha256':hashlib.sha256(args.output.read_bytes()).hexdigest(),
              'files':{str(p.relative_to(root)):hashlib.sha256(p.read_bytes()).hexdigest() for p in files},
              'model_weights_included':False,'source_corpus_included':False}
    args.output.with_suffix(args.output.suffix+'.manifest.json').write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps({k:v for k,v in report.items() if k!='files'},indent=2))


if __name__=='__main__': main()
