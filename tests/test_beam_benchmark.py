import asyncio
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from openpatients2 import beam_recipe as beam, dense_benchmark as b, dense_nemotron_super as s
from openpatients2.dense_benchmark_serving import render
from openpatients2.pilot_extract import PilotConfig, PilotRunner
from test_dense_nemotron_super import campaign


@pytest.mark.parametrize('name,gpus', [('beam-fp8-dflash2', 8), ('beam-nvfp4-dflash2', 4)])
def test_beam_native_recipe_template_stops_and_gpu_allocation(campaign, tmp_path, monkeypatch, name, gpus):
    work = tmp_path / name
    s.prepare(campaign[2], work, expected=3, profiles=[name], with_images=True)
    m = s.check(work)
    assert set(m['nemotron_super']['arms']) == {name + '-text'}
    assert set(m['nemotron_super']['pins']) == {beam.PROFILES[name]['model'], beam.DFLASH2}
    assert set(m['nemotron_super']['engines']) == {'beam'}
    assert not any('Beam' in key and key not in beam.PINS for key in m['nemotron_super']['pins'])
    monkeypatch.setenv('CUDA_VISIBLE_DEVICES', ','.join(str(i) for i in range(8)))
    cfg = s.serving(work, name + '-text')
    command = render(cfg, str(work / 'arms' / (name + '-text')))[0]
    assert cfg.image_reference == beam.IMAGE and '@sha256:' in cfg.image_reference
    assert cfg.tensor_parallel == gpus and not cfg.expert_parallel
    assert cfg.kv_cache_dtype == 'fp8' and cfg.reasoning_parser == 'beam'
    assert command['devices'] == [str(i) for i in range(gpus)]
    assert '--enable-expert-parallel' not in command['argv']
    assert cfg.speculative_config == {**beam.PROFILES[name]['speculation'],
                                     'model': str(work / 'runtime/weights/beam-draft')}
    assert 'VLLM_ENGINE_READY_TIMEOUT_S=5400' in command['argv']
    assert ('--moe-backend' in command['argv']) == (gpus == 8)
    assert cfg.extra_args[cfg.extra_args.index('--chat-template') + 1].endswith('/chat_template.jinja')
    jobs = s.submit(work, 'account', 'qos', dry_run=True)['commands_without_dependencies']
    worker = next(c for c in jobs if c[-1] == name + '-text')
    assert '--gres=gpu:b200:' + str(gpus) in worker and '--cpus-per-task=16' in worker
    config = b.read(work / 'arms' / (name + '-text') / 'manifest.json')['extraction']
    seen = []
    def tokenize(request):
        seen.append(json.loads(request.content))
        return httpx.Response(200, json={'count': 8, 'tokens': [1]*8, 'max_model_len': 65536})
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(tokenize)) as http:
            runner = PilotRunner(config, tmp_path / 'inference', ['http://localhost:8000/v1'], 65536, 'targeted', http=http)
            try:
                client = runner.clients[0]
                messages = [{'role': 'user', 'content': 'Extract supported clinical facts.'}]
                body = client.body('case_context', messages, 128)
                assert body['temperature'] == .7 and body['top_p'] == .99 and 'top_k' not in body
                assert body['stop_token_ids'] == [151674, 151675]
                assert body['chat_template_kwargs'] == {'reasoning_effort': 'max', 'allow_missing_thinking': False}
                assert client.config.save_reasoning and client.config.save_messages
                await runner.token_count(client, messages)
                assert seen[0]['chat_template_kwargs'] == body['chat_template_kwargs']
            finally:
                await runner.close()
    asyncio.run(run())
    for changes in [{'temperature': 1}, {'top_p': .95}, {'stop_token_ids': [151674]},
                    {'revision': b.REVISION}, {'reasoning_strength': 'high'}, {'tokenizer_contract': 'vllm/other'}]:
        with pytest.raises(ValueError, match='pinned recipe'):
            PilotConfig.model_validate({**config, **changes})
    with pytest.raises(ValueError, match='text-only'):
        PilotConfig.model_validate({**config, 'max_figures': 1})
    with pytest.raises(ValueError, match='drafter'):
        PilotConfig(model_id=beam.DFLASH2)


@pytest.mark.parametrize('profile_name', list(s.PROFILES))
@pytest.mark.parametrize('alias', ['reasoning', 'reasoning_content', 'thinking'])
def test_every_profile_preserves_streamed_reasoning_with_production_settings(campaign, tmp_path, monkeypatch, profile_name, alias):
    work = tmp_path / profile_name
    s.prepare(campaign[2], work, expected=3, profiles=[profile_name], with_images=False)
    config = b.read(work / 'arms' / (profile_name + '-text') / 'manifest.json')['extraction']
    real_client = httpx.AsyncClient
    requests = []
    def handle(request):
        body = json.loads(request.content); requests.append(body)
        if request.url.path == '/tokenize':
            return httpx.Response(200, json={'count': 10, 'tokens': [1]*10, 'max_model_len': 65536})
        assert body['stream'] and body['stream_options']['include_usage']
        events = [{'id': 'probe-1', 'model': config['served_model'], 'choices': [{'index': 0, 'delta': {alias: text}}]}
                  for text in ['17 * 19 = 323. ', '323 + 23 = 346.']]
        events.append({'choices': [{'index': 0, 'delta': {'content': '346'}, 'finish_reason': 'stop'}],
                       'usage': {'prompt_tokens': 10, 'completion_tokens': 25,
                                 'completion_tokens_details': {'reasoning_tokens': 20}}})
        return httpx.Response(200, headers={'content-type': 'text/event-stream'},
            text=''.join('data: ' + json.dumps(e) + '\n\n' for e in events) + 'data: [DONE]\n\n')
    monkeypatch.setattr(httpx, 'AsyncClient', lambda **kw: real_client(transport=httpx.MockTransport(handle), **kw))
    asyncio.run(s.reasoning_smoke('http://localhost:8000/v1', tmp_path, config))
    receipt = b.read(tmp_path / 'reasoning-smoke.json')
    assert receipt['passed'] and receipt['outside_article_timing'] and receipt['synthetic']
    assert receipt['response']['reasoning_text'] == '17 * 19 = 323. 323 + 23 = 346.'
    assert receipt['response']['reasoning_fields'][alias] == receipt['response']['reasoning_text']
    assert receipt['response']['content'] == '346' and receipt['response']['reasoning_status'] == 'returned_text'
    assert receipt['metrics']['reasoning_tokens'] == 20 and receipt['revision'] == config['revision']
    assert requests[0]['chat_template_kwargs'] == requests[1]['chat_template_kwargs'] == PilotConfig.model_validate(config).template_kwargs()
    assert requests[1]['temperature'] == config['temperature'] and requests[1]['top_p'] == config['top_p']
    assert requests[1].get('stop_token_ids') == config.get('stop_token_ids')


@pytest.mark.parametrize('problem', ['no_reasoning', 'wrong_prompt_count', 'broken_stream', 'opaque_only'])
def test_reasoning_smoke_stops_before_articles_and_saves_failure(campaign, tmp_path, monkeypatch, problem):
    config = b.read(campaign[0] / 'arms/nvfp4-text/manifest.json')['extraction']
    real_client = httpx.AsyncClient
    def handle(request):
        if request.url.path == '/tokenize':
            return httpx.Response(200, json={'count': 10, 'tokens': [1]*10, 'max_model_len': 65536})
        delta = ({'reasoning_details': [{'type': 'encrypted', 'data': 'opaque'}]} if problem == 'opaque_only' else
                 {'reasoning': 'some text'} if problem != 'no_reasoning' else {'content': '<think>unparsed text</think>346'})
        choice = {'index': 0, 'delta': delta}
        if problem != 'broken_stream':
            choice['finish_reason'] = 'stop'
        event = {'choices': [choice], 'usage': {'prompt_tokens': 11 if problem == 'wrong_prompt_count' else 10}}
        return httpx.Response(200, text='data: ' + json.dumps(event) + '\n\ndata: [DONE]\n\n')
    monkeypatch.setattr(httpx, 'AsyncClient', lambda **kw: real_client(transport=httpx.MockTransport(handle), **kw))
    with pytest.raises(ValueError, match='Reasoning'):
        asyncio.run(s.reasoning_smoke('http://localhost:8000/v1', tmp_path, config))
    receipt = b.read(tmp_path / 'reasoning-smoke.json')
    assert not receipt['passed'] and receipt['response'] and receipt['error_type'] == 'ValueError'


def test_beam_cpu_probe_checks_native_modes_and_draft_without_cuda(tmp_path, monkeypatch, capsys):
    target, draft = tmp_path / 'target', tmp_path / 'draft'
    b.write(target / 'config.json', {'architectures': ['BeamForCausalLM'], 'model_type': 'beam'})
    b.write(target / 'generation_config.json', {'eos_token_id': [151674, 151675]})
    b.write(draft / 'config.json', {'architectures': ['DFlash2BeamDraftModel'], 'num_hidden_layers': 5,
        'num_target_layers': 52, 'draft_vocab_size': 151936, 'vocab_size': 151936,
        'dflash_config': {'block_size': 16, 'target_layer_ids': [4, 16, 28, 40, 46],
                          'selector_top_k': 16, 'conv_kernel_size': 2}})
    calls = []
    class Tokenizer:
        def apply_chat_template(self, messages, **kwargs):
            calls.append(kwargs)
            assert kwargs['allow_missing_thinking'] is False
            return 'Reasoning effort: ' + ('32' if kwargs['reasoning_effort'] == 'low' else '512') + '<|start|>assistant'
        def convert_tokens_to_ids(self, token):
            return {'<|return|>': 151674, '<|call|>': 151675, '<|end|>': 151670}[token]
        def encode(self, text):return [1]
    monkeypatch.setitem(sys.modules, 'vllm', SimpleNamespace(__version__='fixture', __file__=str(tmp_path / '__init__.py')))
    monkeypatch.setitem(sys.modules, 'vllm.model_executor.models', SimpleNamespace(ModelRegistry=SimpleNamespace(
        get_supported_archs=lambda: ['BeamForCausalLM', 'DFlash2BeamDraftModel'])))
    monkeypatch.setitem(sys.modules, 'vllm.reasoning', SimpleNamespace(ReasoningParserManager=SimpleNamespace(
        get_reasoning_parser=lambda name: name == 'beam')))
    monkeypatch.setitem(sys.modules, 'transformers', SimpleNamespace(AutoTokenizer=SimpleNamespace(
        from_pretrained=lambda *a, **kw: Tokenizer())))
    monkeypatch.setattr(sys, 'argv', ['probe', json.dumps({str(target): 'beam', str(draft): 'beam'}), '', '', 'beam', '{}'])
    exec(s.CPU_PROBE, {})
    assert [c['reasoning_effort'] for c in calls] == ['low', 'max']
    assert json.loads(capsys.readouterr().out)['native_thinking']
    bad = b.read(draft / 'config.json'); bad['dflash_config']['block_size'] = 8
    b.write(draft / 'config.json', bad)
    with pytest.raises(AssertionError):
        exec(s.CPU_PROBE, {})


def test_beam_lifecycle_cleans_target_and_shared_drafter_before_next_profile(campaign, tmp_path, monkeypatch):
    work = tmp_path / 'beam-lifecycle'
    # Synthetic pins let this exercise downloads, inventories and cleanup
    # without copying publisher weights or depending on live Hub access.
    for model, pin in beam.PINS.items():
        monkeypatch.setitem(s.PINS, model, {k: pin[k] for k in ('revision', 'architecture')})
    s.prepare(campaign[2], work, expected=3, profiles=list(beam.PROFILES))
    calls = []
    def download(**kwargs):
        calls.append(kwargs)
        path = kwargs['local_dir']
        b.write(path / 'config.json', {'architectures': [s.PINS[kwargs['repo_id']]['architecture']]})
        if '*.safetensors' in kwargs['allow_patterns']:
            (path / 'model.safetensors').write_bytes(b'fixture')
    def run(argv, **kwargs):
        assert '--help=all' not in argv, 'CLI help cannot run on a CPU node'
        if argv[:2] == ['apptainer', 'build'] and '--help' not in argv:
            Path(argv[-2]).write_bytes(b'fixture image')
        stdout = '--mksquashfs-args' if argv == ['apptainer', 'build', '--help'] else '{"vllm_version":"beam-fixture"}'
        return SimpleNamespace(returncode=0, stdout=stdout, stderr='', check_returncode=lambda: None)
    monkeypatch.setitem(sys.modules, 'huggingface_hub', SimpleNamespace(snapshot_download=download, get_token=lambda: None))
    monkeypatch.setattr(s.subprocess, 'run', run)
    for name in ('HF_HOME', 'HF_HUB_CACHE', 'HF_XET_CACHE', 'HF_XET_CHUNK_CACHE_SIZE_BYTES', 'APPTAINER_CACHEDIR', 'APPTAINER_TMPDIR'):
        monkeypatch.setenv(name, 'fixture')
    assert s.setup(work)['ready'] and len(calls) == 3
    assert not any('*.safetensors' in c['allow_patterns'] for c in calls)
    assert s.download(work, 'beam-fp8-dflash2')['ready']
    s.validate_runtime(work, 'beam-fp8-dflash2-text')
    s.cleanup(work, 'beam-fp8-dflash2')
    assert not s.weights_path(work, beam.FP8).exists() and not s.weights_path(work, beam.DFLASH2).exists()
    assert not (work / 'runtime' / beam.ENGINE['sif']).exists()
    assert s.download(work, 'beam-nvfp4-dflash2')['ready']
    s.validate_runtime(work, 'beam-nvfp4-dflash2-text')
    assert [c['repo_id'] for c in calls[-4:]] == [beam.FP8, beam.DFLASH2, beam.NVFP4, beam.DFLASH2]
    assert all(c['revision'] == s.PINS[c['repo_id']]['revision'] for c in calls)
