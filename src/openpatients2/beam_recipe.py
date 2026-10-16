"""Reflection Beam FP8/NVFP4 with the publisher's DFlash2 recipe, 2026-10-16.

The launch image is pinned by its Linux amd64 manifest. Its revision label is
not public on GitHub; CPU probes record installed source hashes instead.
"""
from __future__ import annotations

FP8 = 'reflection/Beam-1.0-501B-A23B-FP8'
NVFP4 = 'reflection/Beam-1.0-501B-A23B-NVFP4'
DFLASH2 = 'reflection/Beam-1.0-501B-A23B-DFlash2'
TARGETS = (FP8, NVFP4)
IMAGE_DIGEST = 'sha256:25635d363e0a86a099e16690f642e85301c4940ee11acf9335c4570657db9d9d'
IMAGE = 'docker://vllm/vllm-openai@' + IMAGE_DIGEST
TOKENIZER_CONTRACT = 'vllm/' + IMAGE_DIGEST
SOURCE = 'https://recipes.vllm.ai/reflection/Beam-1.0-501B-A23B'
STOP_TOKEN_IDS = [151674, 151675]  # <|return|>, <|call|>; never the analysis-turn <|end|>
TEMPLATE_SHA = '24371da068468a5d15940c9af90dbf3d2ce61a6aa99fc122384aed49fc6c3c68'
TOKENIZER_BLOBS = {'generation_config.json': '38065b46fa14b6098b38a3c00e170004491d76e8',
                   'tokenizer_config.json': 'c40a544488f57a4376ff04791c82189c35055c42'}
PINS = {
    FP8: {'revision': '077872da1757aa43e0330ab924b834825c54ecbc',
          'architecture': 'BeamForCausalLM',
          'config_sha256': '1aa51b26225c5edbc8bf8bfdd36ab0e681af07defe8f68b08a952f6dcaf3291a',
          'template_sha256': TEMPLATE_SHA, 'git_blobs': dict(TOKENIZER_BLOBS)},
    NVFP4: {'revision': '3bd1dccf13c8f7aa3d6732c5aaa48ba107a2c086',
            'architecture': 'BeamForCausalLM',
            'config_sha256': 'e3d672f8869420a6dd52cd6e2d1dd71aaa5190f1d45882c7a3e74b0bb40cbc88',
            'template_sha256': TEMPLATE_SHA, 'git_blobs': dict(TOKENIZER_BLOBS)},
    DFLASH2: {'revision': '163b276be744c8ce2b8385ea85de54edb3088211',
             'architecture': 'DFlash2BeamDraftModel',
             'config_sha256': 'baf609fba9dd9c8c42b5dd49deff50ee52d515ff8a25758ba166607b1385648b'},
}
ENGINE = {'image': IMAGE, 'version': 'reflection-beam',
          'sif': 'vllm-reflection-beam-' + IMAGE_DIGEST.split(':')[1][:16] + '.sif',
          'tokenizer_contract': TOKENIZER_CONTRACT,
          'tokenizer_source_sha': None, 'registry_source_sha': None}


def profile(model, gpus):
    return {
        'model': model, 'drafter': DFLASH2, 'engine': 'beam', 'gpus': gpus, 'cpus': 16,
        'host_memory': '200G', 'max_num_seqs': 32, 'max_batched_tokens': 32768,
        'gpu_memory': .9, 'concurrency': 32, 'temperature': .7, 'top_p': .99,
        'vision_temperature': None, 'supports_images': False, 'reasoning_effort': 'max',
        'stop_token_ids': list(STOP_TOKEN_IDS), 'reasoning_parser': 'beam',
        'served_model': model, 'kv_cache_dtype': 'fp8',
        'speculation': {'method': 'dflash', 'num_speculative_tokens': 7,
                        'attention_backend': 'FLASH_ATTN', 'enable_adaptive_verification': True},
        'extra_args': ['--enable-auto-tool-choice', '--tool-call-parser', 'beam',
                       '--chat-template-content-format', 'string', '--max-parallel-loading-workers', '2']
                      + (['--moe-backend', 'triton'] if model == FP8 else []),
        'environment': {'VLLM_ENGINE_READY_TIMEOUT_S': '3600', 'VLLM_BEAM_ALWAYS_CONSTRAIN_OUTPUT': '1'},
        'source': SOURCE,
        'adaptation': 'Official Beam launch image, pure TP (no EP), FP8 KV cache and Beam parsers. '
            f'{gpus}x B200 TP{gpus}; FP8 uses the recommended Triton MoE backend. '
            'DFlash2 proposes seven tokens with FLASH_ATTN and adaptive verification. '
            'Publisher generation config T=0.7/top_p=0.99; native max/512 reasoning. '
            'Project context 64K, prefill 32K, c32, two loading workers and 0.90 GPU utilization. '
            'Harmony analysis/final channels and native return/call stops; text only. Requires a real B200 smoke.',
    }


PROFILES = {'beam-fp8-dflash2': profile(FP8, 8), 'beam-nvfp4-dflash2': profile(NVFP4, 4)}
