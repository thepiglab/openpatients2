"""Cross the same hierarchical reducer with chunk-local repaired maps.

Use the frozen reducer implementation in an isolated output directory, sharing
the original call cache and budget. This avoids replacing any primary outcome.
"""
import asyncio
import hashlib
import json
import os
from pathlib import Path
import chunking_v1 as c
from chunking_parallel_v1 import Study


async def main():
    os.environ['OPENROUTER_API_KEY']=Path('/tmp/op2-chunking-key').read_text().strip()
    root=c.ROOT;study=Study()
    try:
        c.freeze(study)
        protocol={'secondary_posthoc':True,'purpose':'Cross the identical hierarchical reducer with section maps after one local repair pass',
                  'concurrency':4,'runner_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                  'source':'sections_repair outcomes; same summaries, repaired schemas; original citation-only reducer unchanged'}
        p=root/'repaired-hierarchy-protocol.json'
        if p.exists():assert json.loads(p.read_text())==protocol
        else:c.write_json(p,protocol)
        c.ROOT=root/'repaired-hierarchy'
        jobs=asyncio.Semaphore(4)
        async def one(m,u):
            async with jobs:
                stem=f"{m['id'].replace('/', '--')}-{u[0]}-{u[1]}-{u[2]}"
                maps=json.loads((root/'outcomes'/f'{stem}-sections_repair.json').read_text())
                maps['maps']=maps['repaired_maps']
                row=await study.reduce_unit(m,u,maps)
                row['strategy']='repaired_hierarchical'
                c.write_json(root/'outcomes'/f'{stem}-repaired_hierarchical.json',row)
        await asyncio.gather(*(one(m,u) for u in c.UNITS for m in study.models))
        c.write_json(root/'budget-status.json',study.exp.budget.report())
    finally:
        c.ROOT=root
        await study.exp.close()


if __name__=='__main__':asyncio.run(main())
