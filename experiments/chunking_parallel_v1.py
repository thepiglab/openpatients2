"""Resume the unchanged prompts with independent patient-domain jobs in parallel.

Applies a logged, lossless envelope adapter before downstream reduction. The
original outputs and raw responses remain intact and are scored separately.
"""
import argparse
import asyncio
import hashlib
import json
import os
from pathlib import Path
import chunking_v1 as c
from chunking_adapter_v1 import normalize

original_union = c.union


def safe_union(outputs):
    return original_union([{**r, 'candidate': r['candidate'] if isinstance(r.get('candidate'),dict) else None} for r in outputs])


c.union = safe_union


class Study(c.Study):
    async def map_unit(self, model, unit, mode):
        row = await super().map_unit(model, unit, mode)
        normalized = normalize(row, self.articles[unit[0]])
        path = c.ROOT/'outcomes-normalized'/f"{model['id'].replace('/', '--')}-{unit[0]}-{unit[1]}-{unit[2]}-{mode}.json"
        c.write_json(path, normalized)
        return normalized


async def main(args):
    os.environ['OPENROUTER_API_KEY'] = Path('/tmp/op2-chunking-key').read_text().strip()
    study = Study(); study.exp.slots = asyncio.Semaphore(8)
    try:
        c.freeze(study)
        for p in (c.ROOT/'availability').glob('*.json'):
            d = json.loads(p.read_text())
            if 'http_404' in d.get('errors', []): study.exp.blocked_models[d['metrics']['model']] = 'HTTP 404 account privacy restriction confirmed by diagnostic'
        # Do not reopen known unavailable routes when resuming a process.
        if len([p for p in (c.ROOT/'calls/tasks').glob('*.json') if (d:=json.loads(p.read_text()))['metrics']['model']=='cohere/command-a-plus' and d['identity'].get('phase')=='map' and d.get('errors')]) >= 3:
            study.circuit.add('cohere/command-a-plus')
        paths = [Path(__file__), Path('experiments/chunking_adapter_v1.py')]
        amendment = {'reason': 'Schedule independent units concurrently to reduce idle time; accept losslessly unwrapped/split JSON outputs before downstream reduction. Original strict outputs retained.',
            'posthoc': 'Adapter written after observing Inkling output envelopes. Does not alter generation prompts, model settings, clinical values or source.',
            'concurrency': 8, 'hashes': {str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in paths},
            'comparison': 'Report strict and normalized scores separately. Normalized maps feed both hierarchy variants. No truncated or medically corrected outputs admitted by adapter.'}
        path = c.ROOT/'adapter-amendment.json'
        if path.exists(): assert json.loads(path.read_text()) == amendment
        else: c.write_json(path, amendment)
        jobs = asyncio.Semaphore(8)
        async def one(model, unit):
            async with jobs:
                if args.phase == 'maps':
                    for mode in ('whole','sections','windows'): await study.map_unit(model,unit,mode)
                else:
                    maps = await study.map_unit(model,unit,'sections')
                    await study.reduce_unit(model,unit,maps,bottleneck=args.phase=='bottleneck')
        # Round-robin model scheduling keeps budget access reasonably balanced.
        await asyncio.gather(*(one(m,u) for u in c.UNITS for m in study.models))
        c.write_json(c.ROOT/'budget-status.json',study.exp.budget.report())
    finally:
        await study.exp.close()


if __name__ == '__main__':
    parser=argparse.ArgumentParser(); parser.add_argument('--phase',required=True,choices=['maps','reduce','bottleneck'])
    asyncio.run(main(parser.parse_args()))
