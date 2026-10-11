"""`op2 extract` parses its arguments and dispatches to pilot_extract.run_pilot (no network or model calls)."""
from openpatients2 import cli, pilot_extract


def test_extract_parses_and_dispatches_to_run_pilot(monkeypatch, tmp_path, capsys):
    seen = {}

    async def fake_run_pilot(config, articles, output, endpoints, context, arm, **kwargs):
        seen.update(config=config, articles=articles, output=output, endpoints=endpoints,
                    context=context, arm=arm, **kwargs)
        return {'arm': arm, 'selected_articles': 2, 'eligible_articles': 2, 'patients_extracted': 3,
                'model_calls': 0, 'outputs': {'patients': f'{output}/patients.jsonl'}, 'articles': ['omitted']}

    monkeypatch.setattr(pilot_extract, 'run_pilot', fake_run_pilot)
    config = tmp_path / 'extract.yaml'
    config.write_text('max_articles: 2\nseed: 7\n')
    code = cli.main(['extract', '--articles', 'sample.jsonl', '--output', str(tmp_path / 'RUN'),
                     '--endpoints', 'http://127.0.0.1:8000/v1', '--context', '65536',
                     '--config', str(config), '--input-scope', 'patient_sections', '--seed', '9'])
    assert code == 0
    assert isinstance(seen['config'], pilot_extract.PilotConfig) and seen['config'].max_articles == 2
    assert (seen['articles'], seen['endpoints'], seen['context'], seen['arm']) == \
        ('sample.jsonl', 'http://127.0.0.1:8000/v1', 65536, 'targeted')
    assert seen['input_scope'] == 'patient_sections' and seen['seed'] == 9
    assert seen['image_manifest'] is None and seen['frozen_rosters'] is None and seen['cached_rosters'] is None
    out = capsys.readouterr().out
    assert '"patients_extracted": 3' in out and 'omitted' not in out and 'ehr-pipeline' in out


def test_extract_defaults_config_and_rejects_unknown_arm(monkeypatch, tmp_path, capsys):
    seen = {}

    async def fake_run_pilot(config, *args, **kwargs):
        seen['config'] = config
        return {}

    monkeypatch.setattr(pilot_extract, 'run_pilot', fake_run_pilot)
    assert cli.main(['extract', '--articles', 'a.jsonl', '--output', str(tmp_path / 'R'), '--endpoints',
                     'http://localhost:8000/v1', '--context', '4096', '--arm', 'direct']) == 0
    assert seen['config'] == pilot_extract.PilotConfig()
    try:
        cli.main(['extract', '--articles', 'a', '--output', 'o', '--endpoints', 'e', '--context', '1', '--arm', 'x'])
    except SystemExit as exc:
        assert exc.code == 2
    else:
        raise AssertionError('unknown arm accepted')
