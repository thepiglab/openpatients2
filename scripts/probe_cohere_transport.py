"""Two bounded non-streaming controls for the saved Cohere streaming failures."""
import argparse
import asyncio
import json
import os
from pathlib import Path
import time

import httpx
import yaml

from openpatients2.article_tasks import ARTICLE_TASKS, check_article_task
from openpatients2.client import APIClient
from openpatients2.config import APIConfig
from openpatients2.data import write_json
from openpatients2.experiment import Budget
from openpatients2.output_parser import parse_output
from openpatients2.validation import validate


async def main(key_file=None):
    key = Path(key_file).read_text().strip() if key_file else os.environ['OPENROUTER_API_KEY']
    config = yaml.safe_load(Path('configs/experiments/cohere-clinical-schema.yaml').read_text())
    root = Path(config['output'])
    output = Path('runs/article-pilot/research/cohere-transport-control.json')
    if output.exists():
        raise ValueError('Control already recorded; use its saved results')
    model = config['models'][0]
    budget = Budget(Path(config['budget_file']), config['budget_usd'])
    api = APIClient(APIConfig(model=model['id'], model_id=model['id'], response_format='json_schema',
                             schema_profile='cohere', extra_body=model['extra_body']),
                    schema_overrides={k: v.model_json_schema() for k, v in ARTICLE_TASKS.items()})
    article = next(json.loads(l) for l in Path(config['input']).read_text().splitlines()
                   if json.loads(l)['pmcid'] == 'PMC13294519')
    from openpatients2.articles import recheck_license
    if not recheck_license(article['license'])['allowed']:
        raise ValueError('Control article no longer passes license checks')
    patient = json.loads((root / 'cohere--command-a-plus/PMC13294519.1-p1.json').read_text())
    records = []
    try:
        async with httpx.AsyncClient(timeout=90) as http:
            for task in ['summary', 'observations']:
                prior = next(d for p in (root/'tasks').glob('*.json')
                             if (d := json.loads(p.read_text()))['identity'] == {'article_id':'PMC13294519.1','patient_id':'p1'}
                             and d['task'] == task)
                attempt_path = next((root/'attempts').glob(prior['metrics']['signature']+'-0-*.json'))
                messages = json.loads(attempt_path.read_text())['request']
                body = api.body(task, messages, 8192)
                body['stream'] = False
                body.pop('stream_options', None)
                charge = budget.reserve('cohere-nonstream-control:'+task,
                                        (192000*model['max_input_per_million']+8192*model['max_output_per_million'])/1e6)
                record = {'task':task, 'stream':False, 'original_attempt':str(attempt_path), 'charge_id':charge}
                start = time.monotonic()
                try:
                    async with asyncio.timeout(95):
                        response = await http.post(config['endpoint']+'/chat/completions',
                                                   headers={'Authorization':'Bearer '+key}, json=body)
                    data = response.json()
                    data.pop('user_id', None)
                    record.update(http_status=response.status_code, response=data)
                    budget.settle(charge, (data.get('usage') or {}).get('cost'))
                    if response.is_error or data.get('error'):
                        record['valid'] = False
                    else:
                        choice = data['choices'][0]
                        parsed = parse_output(choice['message']['content'], finish_reason=choice['finish_reason']).value
                        if task == 'summary':
                            check_article_task(task, parsed, article, patient['source']['patient_target'])
                            record['valid'] = True
                        else:
                            checked = validate(task, parsed, patient['source']['text'])
                            record.update(valid=checked.valid, validation_errors=checked.errors)
                except Exception as exc:
                    # An exception after settlement must not erase a reported cost.
                    if not record.get('response'):
                        budget.settle(charge, None)
                    record.update(valid=False, error=type(exc).__name__+': '+str(exc)[:1500])
                record['latency_seconds'] = time.monotonic()-start
                records.append(record)
                write_json(output, {'controls':records, 'budget':budget.report()})
                print(json.dumps({k:v for k,v in record.items() if k not in {'response','validation_errors'}}), flush=True)
    finally:
        await api.close()
        budget.db.close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--key-file')
    asyncio.run(main(parser.parse_args().key_file))
