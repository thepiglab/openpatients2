"""Small source-audited positive controls for the semantic repair mechanism."""
import asyncio
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import argparse

import yaml
from refinement_v1 import Study, ROOT, OLD, section
from openpatients2.data import write_json
from openpatients2.experiment import Experiment
from openpatients2.fidelity import load_predictions


def prepare():
    def old(model, rid, task):
        row = load_predictions(OLD/'comparison'/model.replace('/', '--'))[rid]
        return row['raw'][task]['items'], row['patient']['source']
    blood, source1 = old('thinkingmachines/inkling', 'PMC12285374.1:p1', 'medications')
    fresh1 = json.loads((ROOT/'outcomes/meta--muse-glimmer-30b-PMC12285374-p1-medications-direct.json').read_text())
    dialysis, source2 = old('google/gemma-4-31b-it', 'PMC12802722.1:p3', 'procedures_devices')
    fresh2 = json.loads((ROOT/'outcomes/meta--muse-glimmer-30b-PMC12802722-p3-procedures_devices-direct.json').read_text())
    obs, source3 = old('meta/muse-glimmer-30b', 'PMC13294519.1:p1', 'observations')
    fixtures = [
        {'id': 'wrong_patient_transfusion', 'pmcid': 'PMC12285374', 'pid': 'p1', 'task': 'medications',
         'items': [blood[1], fresh1['arms']['none']['candidate']['items'][0]], 'known_error_index': 0,
         'supported_neighbor_indices': [1], 'source_packet': source1},
        {'id': 'dialysis_declined', 'pmcid': 'PMC12802722', 'pid': 'p3', 'task': 'procedures_devices',
         'items': [dialysis[0], fresh2['arms']['none']['candidate']['items'][0]], 'known_error_index': 0,
         'supported_neighbor_indices': [1], 'source_packet': source2},
        {'id': 'anti_xa_as_result', 'pmcid': 'PMC13294519', 'pid': 'p1', 'task': 'observations',
         'items': [obs[19], obs[6], obs[15]], 'known_error_index': 0,
         'supported_neighbor_indices': [1, 2], 'source_packet': source3},
    ]
    p = ROOT/'error-replay/fixtures.json'
    assert not p.exists(), 'Do not overwrite frozen controls'
    write_json(p, {'method': 'Known errors from old outputs plus source-checked correct neighbors. Artificial positive controls, not natural error-rate estimates. Error labels never enter model prompts.', 'fixtures': fixtures})
    write_json(ROOT/'error-replay/protocol.json', {'script_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        'fixtures_sha256': hashlib.sha256(p.read_bytes()).hexdigest(), 'calls': 6,
        'spend': 'Same shared $10 study ledger; no additional allowance'})


async def run(key_file):
    os.environ['OPENROUTER_API_KEY'] = Path(key_file).read_text().strip()
    fixtures = json.loads((ROOT/'error-replay/fixtures.json').read_text())['fixtures']
    config = yaml.safe_load(Path('configs/experiments/medical-fidelity-v1.yaml').read_text())
    config.update(output=str(ROOT/'error-replay/calls'), budget_file=str(ROOT/'budget.sqlite'),
                  budget_usd=10, concurrency=2, validation_retries=0, request_deadline_seconds=120, retry_failed=False)
    config['models'] = [m for m in config['models'] if m['id'] in {'meta/muse-glimmer-30b', 'meta/muse-spark-1.2'}]
    study = Study.__new__(Study)
    study.exp = Experiment(config)
    study.articles = {a['pmcid']: a for l in (OLD/'articles.jsonl').read_text().splitlines() if (a := json.loads(l))}
    study.source_packets = {(f['pmcid'], f['pid']): f['source_packet'] for f in fixtures}

    async def check(model, fixture):
        output, trace = await study.semantic_review(model, fixture['pmcid'], fixture['pid'], fixture['task'],
                               section(deepcopy(fixture['items'])), 'known-error-replay')
        write_json(ROOT/'error-replay'/f"{model['id'].replace('/', '--')}-{fixture['id']}.json",
                   {'model': model['id'], 'fixture_id': fixture['id'], 'output': output, 'trace': trace})

    try:
        await asyncio.gather(*(check(m, f) for m in config['models'] for f in fixtures))
        write_json(ROOT/'error-replay/report.json', {'metrics': study.exp.metrics, 'budget': study.exp.budget.report()})
    finally:
        await study.exp.close()


if __name__ == '__main__':
    p = argparse.ArgumentParser(); p.add_argument('--prepare', action='store_true'); p.add_argument('--key-file')
    args = p.parse_args()
    if args.prepare: prepare()
    else: asyncio.run(run(args.key_file))
