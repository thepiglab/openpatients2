#!/usr/bin/env python3
"""Allowlisted CPU/GPU pilot transfer; no archives, weights, caches or keys."""
import argparse
import hashlib
import json
from pathlib import Path
import tarfile


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,default=Path('dist/openpatients2-corpus-pilot.tar.gz'))
    args=parser.parse_args();root=Path(__file__).resolve().parents[1]
    if args.output.exists(): raise ValueError('Choose a new package filename')
    names=['README.md','CHANGELOG.md','pyproject.toml','uv.lock','LICENSE',
        'docs/OVERNIGHT_CLINICAL_BENCHMARK.md','docs/CORPUS_PILOT.md','docs/CORPUS_CORRECTNESS.md','docs/CORPUS_REFINEMENT.md','docs/PMC_ACQUISITION.md','docs/ARTICLE_LICENSE_POLICY.md',
        'docs/ROBUST_EXTRACTION_DESIGN.md','docs/PAPERCLIP_EVALUATION.md',
        'reports/GLIMMER_TUNING_20261002.md','reports/GLIMMER_TOKENIZER_SMOKE.json',
        'reports/CORPUS_CORRECTNESS_20261003.md','reports/CORPUS_CORRECTNESS_20261003.json',
        'reports/PMC_ACQUISITION_V2_LIVE_SMOKE.json','reports/PMC_ACQUISITION_PARALLEL_SMOKE.json',
        'reports/glimmer-token-lengths-nine/token-histograms.png',
        'reports/glimmer-token-lengths-nine/token-histograms.svg',
        'reports/glimmer-token-lengths-nine/token-histograms.json',
        'scripts/corpus_pilot.sbatch','scripts/package_corpus_pilot.py',
        'scripts/smoke_pmc_acquisition.py','scripts/smoke_glimmer_tokenizer.py',
        'scripts/probe_paperclip.py','scripts/run_overnight_pilot.sh','scripts/resume_overnight_pilot.sh',
        'scripts/run_gepa_clinical_overnight.sh','docs/GEPA_CLINICAL_OVERNIGHT.md']
    files={root/n for n in names}
    files.update(p for p in root.glob('scripts/hpg_*') if p.is_file() and p.suffix in {'.py','.sbatch','.sh'})
    for folder in ['src/openpatients2','configs/pilot','configs/sources','configs/hipergator',
                   'benchmarks/hipergator-k2','benchmarks/corpus-correctness','schemas']:
        files.update(p for p in (root/folder).rglob('*') if p.is_file() and p.suffix in
                     {'.py','.md','.yaml','.json','.jsonl','.jinja','.sha256','.txt'})
    # This small reviewed input is intentional, rather than a downloaded corpus.
    fixture = root/'benchmarks/corpus-correctness/articles.jsonl.gz'
    if fixture.is_file():
        if fixture.stat().st_size > 2_000_000: raise ValueError('Correctness fixture exceeds 2 MB package cap')
        files.add(fixture)
    if any(p.is_symlink() for p in files): raise ValueError('No symlinks in transfer package')
    args.output.parent.mkdir(parents=True,exist_ok=True)
    with tarfile.open(args.output,'w:gz') as tar:
        for path in sorted(files): tar.add(path,arcname=str(path.relative_to(root)),recursive=False)
    manifest={'archive':str(args.output),'bytes':args.output.stat().st_size,
        'sha256':hashlib.sha256(args.output.read_bytes()).hexdigest(),
        'files':{str(p.relative_to(root)):hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(files)},
        'weights_included':False,'source_corpus_included':False,
        'small_correctness_fixture_included':fixture in files,'credentials_included':False}
    args.output.with_suffix('.gz.manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
    print(json.dumps({k:v for k,v in manifest.items() if k!='files'},indent=2))


if __name__=='__main__': main()
