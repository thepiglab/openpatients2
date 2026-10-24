import json
import os
from pathlib import Path
import subprocess

import pytest

from openpatients2.dense_benchmark import infrastructure_failures
from openpatients2.nemotron_super_recipes import DEFAULT_PROFILES
from openpatients2.super_packing import plan


@pytest.mark.parametrize('entry', [
    {'response': {'http_status': 500, 'error': 'http_500'}},
    {'response': {'error': 'ConnectError'}},
    {'response': None, 'metrics': {'transport_error': True}},
])
def test_terminal_transport_failures_are_retryable(tmp_path, entry):
    tasks = tmp_path / 'ehr/calls/tasks'
    tasks.mkdir(parents=True)
    path = tasks / 'one.json'
    path.write_text(json.dumps({'task': 'observations', 'status': 'failed', 'attempts': [entry]}))
    assert len(infrastructure_failures(tmp_path)) == 1
    # A recovered retry or validation-only failure is not infrastructure failure.
    path.write_text(json.dumps({'task': 'observations', 'status': 'partial',
                               'attempts': [entry, {'errors': ['nonliteral_quote'], 'response': {'http_status': 200}}]}))
    assert infrastructure_failures(tmp_path) == []
    path.write_text(json.dumps({'task': 'observations', 'status': 'valid', 'attempts': [entry]}))
    assert infrastructure_failures(tmp_path) == []


def test_focused_defaults_fit_single_wave():
    assert set(DEFAULT_PROFILES) == {'nvfp4', 'bf16', 'beam-nvfp4-dflash2'}
    waves = plan(DEFAULT_PROFILES)['waves']
    assert len(waves) == 1
    assert waves[0]['totals'] == {'gpus': 8, 'cpus': 32, 'memory_gib': 224}


def test_long_launcher_clears_stale_pilot_selection(tmp_path):
    root = Path(__file__).resolve().parents[1]
    scripts = tmp_path / 'scripts'
    scripts.mkdir()
    (scripts / 'run_dense_super_long.sh').write_text((root / 'scripts/run_dense_super_long.sh').read_text())
    (scripts / 'run_dense_nemotron_super.sh').write_text(
        'env | sort\n')
    env = {**os.environ, 'DENSE_SUPER_ARTICLE_IDS': 'PMC1', 'DENSE_SUPER_LIMIT_ARTICLES': '2',
           'DENSE_INCLUDE_TEST': '1', 'DENSE_SUPER_PROFILES': 'spark'}
    env.pop('DENSE_HOURS', None)
    result = subprocess.run(['bash', str(scripts / 'run_dense_super_long.sh')], env=env,
                            check=True, capture_output=True, text=True)
    values = dict(line.split('=', 1) for line in result.stdout.splitlines() if '=' in line)
    assert values['DENSE_HOURS'] == '72'
    assert values['DENSE_SUPER_LIMIT_ARTICLES'] == values['DENSE_SUPER_ARTICLE_IDS'] == ''
    assert values['DENSE_SUPER_SPLITS'] == 'train validation'
    assert values['DENSE_INCLUDE_TEST'] == '0'
    assert values['DENSE_SUPER_PROFILES'] == 'nvfp4 bf16 beam-nvfp4-dflash2'
