#!/usr/bin/env python3
"""Temporary tokenizer-only check on nine frozen articles; retain scalar report."""
import argparse
import json
from pathlib import Path
import tempfile

from openpatients2.corpus_stats import tokenizer_snapshot, profile


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',required=True,type=Path)
    args=parser.parse_args()
    if args.output.exists(): raise ValueError('Use a new report path')
    with tempfile.TemporaryDirectory(prefix='op2-tokenizer-smoke-') as folder:
        root=Path(folder)
        receipt=tokenizer_snapshot(root/'tokenizer')
        report=profile('benchmarks/hipergator-k2/fixtures/articles.jsonl',root/'tokenizer',root/'profile',
                       workers=2,sample_size=9,max_articles=9,benchmark_workers=(1,2))
        result={'passed':True,'purpose':'Nine frozen development articles, not population article lengths',
                'tokenizer':receipt,'statistics':report['statistics'],'context_fit_for_roster':report['context_fit_for_roster'],
                'cpu_worker_trials':report['cpu_worker_trials'],'weights_loaded':False,'token_ids_written':False,
                'temporary_bytes':sum(p.stat().st_size for p in root.rglob('*') if p.is_file())}
    result['temporary_directory_deleted']=not root.exists()
    args.output.write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps(result,indent=2))


if __name__=='__main__': main()
