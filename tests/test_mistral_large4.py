import os
from pathlib import Path
import shutil
import subprocess

import pytest

from openpatients2 import dense_benchmark as b, dense_nemotron_super as s
from openpatients2.dense_benchmark_serving import ServerGroup, ServingConfig, validate_serving_artifacts
from openpatients2.mistral_large4_recipe import MODEL, PINS, PROFILE, ENGINE
from openpatients2.pilot_extract import PilotConfig
from test_dense_nemotron_super import campaign


def test_chonk_campaign_pins_native_config_and_embedded_draft(campaign, tmp_path):
    work = tmp_path / 'chonk'
    # Avoid actual figure downloads in unit tests; modality routing is asserted separately.
    s.prepare(campaign[2], work, expected=3, profiles=['le-chonk-nvfp4-dspark'],
              scheduler='packed', with_images=False)
    arm = work / 'arms/le-chonk-nvfp4-dspark-text'
    m = b.read(arm / 'manifest.json')
    assert m['drafter'] is None and s.profile_models('le-chonk-nvfp4-dspark') == [MODEL]
    assert m['context'] == 262144 and m['article_timeout_seconds'] == 14400
    assert PilotConfig.model_validate(m['extraction']).template_kwargs() == {'reasoning_effort': 'high'}
    cfg = s.serving(work, arm.name)
    assert cfg.speculative_config == {'method': 'dspark', 'num_speculative_tokens': 3}
    assert cfg.tensor_parallel == 8 and cfg.kv_cache_dtype == 'fp8_ds_mla'
    assert cfg.reasoning_parser == 'mistral'
    assert cfg.model_config_file == 'params.json'
    assert '--config-format' in cfg.extra_args and '--load-format' in cfg.extra_args
    jobs = s.submit(work, 'cai5724', 'cai5724', dry_run=True)
    assert jobs['maximum_simultaneous_gpus'] == 8
    assert all(any('le-chonk-🐱' in arg for arg in cmd) for cmd in jobs['commands_without_dependencies'])
    from openpatients2.nemotron_super_recipes import arm_definitions
    assert arm_definitions(True)['le-chonk-nvfp4-dspark-vision']['pixels']
    assert PINS[MODEL]['config_file'] == 'params.json'
    assert '@sha256:' in ENGINE['image'] and PROFILE['temperature'] == PROFILE['vision_temperature'] == 1


def test_native_parameters_are_validated_without_fabricating_config(tmp_path, monkeypatch):
    import hashlib, json
    params = json.dumps({'n_layers': 72, 'vision_encoder': {'hidden_size': 2048}}).encode()
    (tmp_path / 'params.json').write_bytes(params)
    (tmp_path / 'chat_template.jinja').write_text('native fixture')
    monkeypatch.setitem(s.PINS, MODEL, {'config_file': 'params.json',
        'config_sha256': hashlib.sha256(params).hexdigest(),
        'template_sha256': hashlib.sha256(b'native fixture').hexdigest()})
    s.validate_checkpoint(tmp_path, MODEL)
    assert not (tmp_path / 'config.json').exists()
    (tmp_path / 'params.json').write_bytes(params + b' ')
    import pytest
    with pytest.raises(ValueError, match='Unexpected model config'):
        s.validate_checkpoint(tmp_path, MODEL)
    compile(s.CPU_PROBE, '<cpu-probe>', 'exec')


@pytest.fixture
def native_serving(campaign, tmp_path, monkeypatch):
    work = tmp_path / 'native-serving'
    s.prepare(campaign[2], work, expected=3, profiles=['le-chonk-nvfp4-dspark'],
              scheduler='packed', with_images=False)
    cfg = s.serving(work, 'le-chonk-nvfp4-dspark-text')
    model = Path(cfg.model_path)
    b.write(model / 'params.json', {'n_layers': 72})
    b.write(model / 'op2_snapshot.json', {'model_id': MODEL, 'revision': PINS[MODEL]['revision']})
    Path(cfg.sif).write_bytes(b'fixture-sif')
    monkeypatch.delenv('CUDA_VISIBLE_DEVICES', raising=False)
    monkeypatch.setattr(shutil, 'which', lambda name: '/fixture/apptainer' if name == 'apptainer' else None)
    return cfg, ServerGroup(cfg, str(work), str(work / 'logs'))


def test_native_checkpoint_passes_both_cpu_and_worker_artifact_gate(native_serving):
    cfg, group = native_serving
    snapshot = validate_serving_artifacts(cfg, group.root)
    assert snapshot == group.preflight()
    assert snapshot['model_id'] == MODEL
    assert not (Path(cfg.model_path) / 'config.json').exists()


@pytest.mark.parametrize('missing', ['params.json', 'op2_snapshot.json', 'sif'])
def test_native_checkpoint_still_requires_all_serving_artifacts(native_serving, missing):
    cfg, group = native_serving
    path = Path(cfg.sif) if missing == 'sif' else Path(cfg.model_path) / missing
    path.unlink()
    with pytest.raises(ValueError, match='Missing local serving artifacts') as exc:
        group.preflight()
    assert str(path) in str(exc.value)


def test_native_checkpoint_still_checks_snapshot_identity(native_serving):
    cfg, group = native_serving
    b.write(Path(cfg.model_path) / 'op2_snapshot.json', {'model_id': 'wrong/model'})
    with pytest.raises(ValueError, match='model_id differs'):
        group.preflight()


def test_default_checkpoint_format_does_not_accept_params_as_substitute(native_serving):
    cfg, group = native_serving
    cfg = ServingConfig.model_validate(cfg.model_dump(exclude={'model_config_file'}))
    assert cfg.model_config_file == 'config.json'
    with pytest.raises(ValueError, match='config.json'):
        validate_serving_artifacts(cfg, group.root)
    b.write(Path(cfg.model_path) / 'config.json', {})
    assert validate_serving_artifacts(cfg, group.root)['model_id'] == MODEL


@pytest.mark.parametrize('mode,count,hours', [('smoke', '2', '8'), ('overnight', '24', '12')])
def test_chonk_launcher_clears_competition_mode(tmp_path, mode, count, hours):
    scripts = tmp_path / 'scripts'; scripts.mkdir()
    root = Path(__file__).resolve().parents[1]
    shutil.copyfile(root / 'scripts/run_dense_le_chonk.sh', scripts / 'run_dense_le_chonk.sh')
    (scripts / 'run_dense_nemotron_super.sh').write_text('env\n')
    env = {**os.environ, 'DENSE_SUPER_COMPETITION': '1', 'DENSE_SUPER_ARTICLE_IDS': 'PMC1',
           'DENSE_SUPER_BEAM_REPAIR': '1', 'DENSE_SUPER_PROFILES': 'beam-nvfp4-dflash2'}
    env.pop('DENSE_HOURS', None)
    output = subprocess.check_output(['bash', str(scripts / 'run_dense_le_chonk.sh'), mode], env=env, text=True)
    values = dict(line.split('=', 1) for line in output.splitlines() if '=' in line)
    assert values['DENSE_SUPER_COMPETITION'] == values['DENSE_SUPER_BEAM_REPAIR'] == '0'
    assert values['DENSE_SUPER_PROFILES'] == 'le-chonk-nvfp4-dspark'
    assert values['DENSE_SUPER_WITH_IMAGES'] == '1' and values['DENSE_SUPER_LIMIT_ARTICLES'] == count
    assert values['DENSE_HOURS'] == hours
    assert values['DENSE_SUPER_ARTICLE_IDS'] == ''
