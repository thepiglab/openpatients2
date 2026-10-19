import asyncio
import hashlib
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from openpatients2 import dense_benchmark as b, dense_nemotron_super as s
from openpatients2 import muse_spark_recipe as spark
from openpatients2.dense_benchmark_serving import render
from openpatients2.pilot_extract import PilotConfig, PilotRunner
from test_dense_nemotron_super import campaign


@pytest.mark.parametrize('with_images,arm_count', [(False, 8), (True, 8)])
def test_default_includes_spark_access_image_and_eight_gpu_job(campaign, tmp_path, monkeypatch, with_images, arm_count):
    monkeypatch.setattr(b, 'freeze_media', lambda *a, **kw: {})
    work = tmp_path / 'default-models'
    s.prepare(campaign[2], work, expected=3, with_images=with_images)
    m = s.check(work)
    assert m['nemotron_super']['profiles'] == ['fastino', 'nvfp4', 'bf16', 'lilicorr', 'spark', 'redhat-fp8-dspark', 'beam-fp8-dflash2', 'beam-nvfp4-dflash2']
    assert len(m['nemotron_super']['arms']) == arm_count
    assert ('spark-vision' if with_images else 'spark-text') in m['nemotron_super']['arms']
    assert len({a['profile'] for a in m['nemotron_super']['arms'].values()}) == 8
    assert sum(a['pixels'] for a in m['nemotron_super']['arms'].values()) == (5 if with_images else 0)
    assert spark.SPARK in m['nemotron_super']['pins'] and spark.DSPARK in m['nemotron_super']['pins']
    assert set(m['nemotron_super']['engines']) == {'nemotron', 'spark', 'beam'}
    plan = s.submit(work, 'account', 'qos', dry_run=True)
    assert plan['maximum_simultaneous_gpus'] == 8
    assert sum('--gres=gpu:b200:8' in command for command in plan['commands_without_dependencies']) == 2
    assert sum(command[-3] == 'worker' for command in plan['commands_without_dependencies']) == 8


def test_explicit_spark_uses_its_own_image(campaign, tmp_path, monkeypatch):
    root = campaign[2]
    work = tmp_path / 'all-models'
    s.prepare(root, work, expected=3, profiles=list(s.DEFAULT_PROFILES), with_images=False)
    m = s.check(work)
    assert list(m['nemotron_super']['profiles']) == ['fastino', 'nvfp4', 'bf16', 'lilicorr', 'spark', 'redhat-fp8-dspark', 'beam-fp8-dflash2', 'beam-nvfp4-dflash2']
    assert len(m['nemotron_super']['arms']) == 8
    assert set(m['nemotron_super']['engines']) == {'nemotron', 'spark', 'beam'}
    assert spark.SPARK in m['nemotron_super']['pins'] and spark.DSPARK in m['nemotron_super']['pins']
    monkeypatch.setenv('CUDA_VISIBLE_DEVICES', '0,1,2,3,4,5,6,7')
    cfg = s.serving(work, 'spark-text')
    command = render(cfg, str(work / 'arms/spark-text'))[0]
    assert cfg.image_reference == spark.IMAGE and '@sha256:' in cfg.image_reference
    assert cfg.tensor_parallel == 8 and cfg.expert_parallel and cfg.reasoning_parser == 'muse'
    assert command['devices'] == [str(i) for i in range(8)]
    assert 'VLLM_USE_RUST_FRONTEND=1' in command['argv']
    assert '--enable-expert-parallel' in command['argv']
    assert cfg.speculative_config == {**spark.PROFILE['speculation'],
        'model': str(work / 'runtime/weights/spark-draft')}
    plan = s.submit(work, 'account', 'qos', dry_run=True)
    jobs = [cmd for cmd in plan['commands_without_dependencies'] if cmd[-1] == 'spark-text']
    assert len(jobs) == 1 and '--cpus-per-task=16' in jobs[0] and '--mem=200G' in jobs[0]
    assert plan['maximum_simultaneous_gpus'] == 8 and not plan['submitted']
    assert s.serving(work, 'nvfp4-text').image_reference != cfg.image_reference


def test_default_runs_each_profile_once_with_supported_modalities(campaign, tmp_path, monkeypatch):
    monkeypatch.setattr(b, 'freeze_media', lambda *a, **kw: {})
    work = tmp_path / 'vision'
    result = s.prepare(campaign[2], work, expected=3)
    m = s.check(work)
    assert len(m['nemotron_super']['arms']) == 8
    assert result['article_runs'] == 8  # training-only default, one training fixture
    assert m['nemotron_super']['input_policy'] == 'supported-modalities-once'
    assert set(m['nemotron_super']['arms']) == {
        'fastino-text', 'nvfp4-vision', 'bf16-vision', 'lilicorr-vision', 'spark-vision',
        'redhat-fp8-dspark-vision', 'beam-fp8-dflash2-text', 'beam-nvfp4-dflash2-text'}
    assert m['nemotron_super']['arms']['spark-vision'] == {'profile': 'spark', 'pixels': True}
    assert 'fastino-vision' not in m['nemotron_super']['arms']
    for name, definition in m['nemotron_super']['arms'].items():
        arm = b.read(work / 'arms' / name / 'manifest.json')
        assert arm['text_only'] is not definition['pixels']
        assert arm['extraction']['focused_pixels'] == definition['pixels']
        assert arm['extraction']['max_figures'] == (48 if definition['pixels'] else 0)


def test_legacy_dual_condition_campaign_remains_inspectable_and_cleanable(campaign, tmp_path, monkeypatch):
    work = tmp_path / 'legacy-dual'
    monkeypatch.setattr(b, 'freeze_media', lambda *a, **kw: {})
    current = s.arm_definitions
    def old_selection(with_images):
        return {**current(False), **(current(True) if with_images else {})}
    monkeypatch.setattr(s, 'arm_definitions', old_selection)
    s.prepare(campaign[2], work, expected=3)
    monkeypatch.setattr(s, 'arm_definitions', current)
    assert len(s.check(work)['nemotron_super']['arms']) == 13
    s.runtime_owner(work, create=True)
    target = s.weights_path(work, spark.SPARK)
    target.mkdir(parents=True)
    (target / 'fixture-weights').write_bytes(b'old weights')
    result = s.cleanup(work)
    assert not target.exists() and str(target.relative_to(work)) in result['deleted']
    assert (work / 'arms/spark-text/manifest.json').exists()
    assert (work / 'arms/spark-vision/manifest.json').exists()


@pytest.mark.parametrize('flags,expected', [([], True), (['--with-images'], True), (['--text-only'], False)])
def test_prepare_cli_defaults_to_supported_modalities(tmp_path, monkeypatch, capsys, flags, expected):
    def prepare(root, work, count, hours, with_images, *args):
        return {'with_images': with_images}
    monkeypatch.setattr(s, 'prepare', prepare)
    s.main(['prepare', '--work', str(tmp_path), *flags])
    assert json.loads(capsys.readouterr().out)['with_images'] is expected


def test_spark_native_template_sampling_and_stop_tokens(campaign, tmp_path):
    work = tmp_path / 'spark-only'
    s.prepare(campaign[2], work, expected=3, profiles=['spark'], with_images=False)
    config = b.read(work / 'arms/spark-text/manifest.json')['extraction']
    seen = []
    def tokenize(request):
        seen.append(json.loads(request.content))
        return httpx.Response(200, json={'count': 8, 'tokens': [1]*8, 'max_model_len': 65536})
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(tokenize)) as http:
            runner = PilotRunner(config, tmp_path / 'inference', ['http://localhost:8000/v1'], 65536, 'targeted', http=http)
            try:
                client = runner.clients[0]
                messages = [{'role': 'user', 'content': 'extract supported facts'}]
                body = client.body('case_context', messages, 128)
                assert body['temperature'] == 1 and body['top_p'] == 1 and 'top_k' not in body
                assert body['stop_token_ids'] == [200001, 200008]
                assert body['chat_template_kwargs'] == {'reasoning_effort': 'xhigh', 'current_date': '2026-10-15'}
                assert 'enable_thinking' not in body['chat_template_kwargs']
                await runner.token_count(client, messages)
                assert seen[0]['chat_template_kwargs'] == body['chat_template_kwargs']
                image = [{'role': 'user', 'content': [{'type': 'image_url', 'image_url': {'url': 'data:image/png;base64,fixture'}}]}]
                assert client.body('case_context', image, 128)['temperature'] == 1
            finally:
                await runner.close()
    asyncio.run(run())
    for changes in [{'top_p': .95}, {'stop_token_ids': [200007]}, {'revision': b.REVISION}]:
        with pytest.raises(ValueError, match='pinned recipe'):
            PilotConfig.model_validate({**config, **changes})
    with pytest.raises(ValueError, match='drafter'):
        PilotConfig(model_id=spark.DSPARK)


def test_git_blob_identity_rejects_modified_gated_file(tmp_path, monkeypatch):
    model = 'fixture/pinned-gated-model'
    path = tmp_path / 'model'
    b.write(path / 'config.json', {'architectures': ['FixtureArchitecture']})
    template = b'native template bytes'
    (path / 'chat_template.jinja').write_bytes(template)
    blob = hashlib.sha1(b'blob ' + str(len(template)).encode() + b'\0' + template).hexdigest()
    monkeypatch.setitem(s.PINS, model, {'git_blobs': {'chat_template.jinja': blob}})
    s.validate_checkpoint(path, model)
    (path / 'chat_template.jinja').write_bytes(template + b' changed')
    with pytest.raises(ValueError, match='Git blob differs'):
        s.validate_checkpoint(path, model)


def test_missing_spark_access_fails_before_any_weight_download(campaign, tmp_path, monkeypatch):
    work = tmp_path / 'access-failure'
    s.prepare(campaign[2], work, expected=3, profiles=['spark'], with_images=False)
    calls = []
    def download(**kwargs):
        calls.append(kwargs)
        if kwargs['repo_id'] == spark.SPARK:
            raise PermissionError('fixture gated access denial')
    monkeypatch.setitem(sys.modules, 'huggingface_hub', SimpleNamespace(snapshot_download=download, get_token=lambda: 'fixture-token'))
    monkeypatch.setattr(s, 'validate_checkpoint', lambda *a, **kw: None)
    for name in ('HF_HOME', 'HF_HUB_CACHE', 'HF_XET_CACHE', 'HF_XET_CHUNK_CACHE_SIZE_BYTES', 'APPTAINER_CACHEDIR', 'APPTAINER_TMPDIR'):
        monkeypatch.setenv(name, 'fixture-before-setup')
    with pytest.raises(PermissionError):
        s.setup(work)
    assert calls and not any('*.safetensors' in call['allow_patterns'] for call in calls)
    assert all(call['token'] == 'fixture-token' for call in calls)
    receipt = b.read(work / 'results/model-access-error.json')
    assert receipt['model_id'] == spark.SPARK and receipt['weights_download_started'] is False
    assert 'fixture-token' not in json.dumps(receipt)
    assert not (work / 'runtime.json').exists()
