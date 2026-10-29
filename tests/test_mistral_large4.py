from openpatients2 import dense_benchmark as b, dense_nemotron_super as s
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
