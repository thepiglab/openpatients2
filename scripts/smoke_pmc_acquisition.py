#!/usr/bin/env python3
"""Fetch at most three small articles, test resume/export, then delete all payloads."""
import argparse
import asyncio
import json
from pathlib import Path
import tempfile

import yaml
from openpatients2.acquisition import initialize, run, status, export
from openpatients2.data import read_jsonl


async def smoke():
    report = {'purpose':'Bounded acquisition integration test, not a medical quality evaluation'}
    with tempfile.TemporaryDirectory(prefix='op2-pmc-smoke-') as temp:
        root = Path(temp); work = root/'campaign'; config = root/'config.yaml'
        config.write_text(yaml.safe_dump({
            'lanes':[{'name':'case_pilot','query':'"case report"[Title]', 'start':'2024-01-01','end':'2024-01-07'}],
            'max_candidates':3, 'page_size':3, 'max_storage_bytes':16_000_000,
            'min_free_bytes':100_000_000, 'max_network_bytes_per_invocation':4_000_000,
            'max_network_bytes_total':4_000_000,
            'max_requests_per_invocation':30, 'max_metadata_bytes':500_000,
            'max_xml_bytes':500_000, 'max_article_json_bytes':2_000_000,
            'retries':1, 'timeout_seconds':30}))
        initialize(work, config)
        try:
            report['discovery'] = await run(work,'discover')
            report['fetch'] = await run(work,'fetch',fetch_limit=3)
            before = status(work)['cumulative_requests']
            report['resume'] = await run(work,'fetch',fetch_limit=3)
            report['resume_extra_requests'] = status(work)['cumulative_requests'] - before
            output = root/'articles.jsonl.gz'
            report['export'] = export(work,output,max_bytes=6_000_000)
            report['articles'] = [{'article_id':a['article_id'], 'source_url':a['source_url'],
                'license':a['license']['code'], 'xml_bytes':a['lengths']['xml_bytes'],
                'words':a['lengths']['words'], 'figures':len(a['figures']), 'supplements':len(a['supplements']),
                'md5_verified':a['retrieval']['xml']['manifest_md5_verified']}
                for a in read_jsonl(output)]
            report['passed'] = bool(report['articles']) and report['resume_extra_requests']==0
        except Exception as exc:
            report.update(passed=False,error=f'{type(exc).__name__}: {exc}')
        report['final_status'] = status(work)
        report['temporary_bytes_before_cleanup'] = sum(p.stat().st_size for p in root.rglob('*') if p.is_file())
        # Keep only aggregate diagnostics and source identifiers in the report.
        for value in report.values():
            if isinstance(value,dict):
                value.pop('work_dir',None); value.pop('output',None)
    report['temporary_directory_deleted'] = not root.exists()
    return report


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True,type=Path)
    args=parser.parse_args()
    if args.output.exists(): raise ValueError('Choose a new report path')
    report=asyncio.run(smoke())
    args.output.write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps(report,indent=2))
    return 0 if report['passed'] and report['temporary_directory_deleted'] else 2


if __name__=='__main__':
    raise SystemExit(main())
