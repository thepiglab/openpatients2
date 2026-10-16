"""Pinned publisher recipes, checked 2026-10-15; no GPU imports on login nodes.

NVIDIA's exact nightly is used because it is linked by both model cards. B200
profiles adapt the GB200 recipes; LiLiCorr adapts the Spark low-concurrency recipe.
"""
from __future__ import annotations

from .muse_spark_recipe import ENGINE as SPARK_ENGINE, PINS as SPARK_PINS, PROFILE as SPARK_PROFILE
from .redhat_nemotron_recipe import PINS as REDHAT_PINS, PROFILE as REDHAT_PROFILE
from .beam_recipe import ENGINE as BEAM_ENGINE, PINS as BEAM_PINS, PROFILES as BEAM_PROFILES

VLLM_COMMIT = '3f1bcfd82bb2e93fcd364fc7a5491718453a77e2'
VLLM_IMAGE = 'docker://vllm/vllm-openai:nightly-' + VLLM_COMMIT
TOKENIZER_CONTRACT = 'vllm/' + VLLM_COMMIT
TOKENIZER_SOURCE_SHA = '194f12a7f986aac45d23f8252eefcbb8a10d976710000b36614b08cf3359903c'
REGISTRY_SOURCE_SHA = 'e78b5de2bee451fadac7034ad23332bbb5ae675be4ff85cb8f4d5eb97d529bdb'

FASTINO = 'fastino/Fastino-Nemotron-3.5-Super-VL-Healthcare'
NVFP4 = 'nvidia/NVIDIA-Nemotron-3.5-Super-VL-120B-A12B-NVFP4'
BF16 = 'nvidia/NVIDIA-Nemotron-3.5-Super-VL-120B-A12B-BF16'
LILICORR = 'nvidia/NVIDIA-Nemotron-3.5-Super-VL-120B-A12B-NVFP4-LiLiCorr'

PINS = {
    FASTINO: {'revision': '5c1d68ad0df5fd38106b252bdc7a0b4a15b07771',
              'architecture': 'NemotronHForCausalLM',
              'template_sha256': '6521afc75afb0661e3558e7e6dee5f2d15de8be1c4dece8171935e778ea283f4'},
    NVFP4: {'revision': '82d93699acd3be9e4617c53360fe5b3f5dbeb1b3',
            'architecture': 'NemotronH_Omni_Reasoning_V3',
            'template_sha256': '1369e90b19976f99ce2070eb21ade8fcb4af640f008e14e82af5c5e89a9cc816'},
    BF16: {'revision': 'fc067b34f6b514014d2f8bddba855d2e4bfdfba6',
           'architecture': 'NemotronH_Omni_Reasoning_V3',
           'template_sha256': '1369e90b19976f99ce2070eb21ade8fcb4af640f008e14e82af5c5e89a9cc816'},
    LILICORR: {'revision': '7c7221aeb4cb3139307aa457f7dce11a8fb6ceb2',
               'architecture': 'LiLiCorrDraftModel'},
    **SPARK_PINS,
    **REDHAT_PINS,
    **BEAM_PINS,
}

ENGINES = {
    'nemotron': {'image': VLLM_IMAGE, 'version': 'nightly-' + VLLM_COMMIT,
                 'sif': 'vllm-' + VLLM_COMMIT + '.sif', 'tokenizer_contract': TOKENIZER_CONTRACT,
                 'tokenizer_source_sha': TOKENIZER_SOURCE_SHA, 'registry_source_sha': REGISTRY_SOURCE_SHA},
    'spark': SPARK_ENGINE,
    'beam': BEAM_ENGINE,
}


def engine(profile):
    return ENGINES[profile.get('engine', 'nemotron')]

MEDIA_ARGS = ['--limit-mm-per-prompt', '{"image":1,"video":1}',
              '--video-pruning-rate', '0.5', '--media-io-kwargs',
              '{"video":{"fps":2,"num_frames":256}}']
TOOL_ARGS = ['--enable-auto-tool-choice', '--tool-call-parser', 'qwen3_coder']
MAMBA_ARGS = ['--mamba-backend', 'flashinfer', '--mamba-ssm-cache-dtype', 'float16',
              '--enable-mamba-cache-stochastic-rounding', '--mamba-cache-philox-rounds', '5',
              '--enable-mamba-shared-prefix-checkpoint']

# GPU counts and memory are per server/Slurm allocation, not active parameters.
PROFILES = {
    'fastino': {'model': FASTINO, 'gpus': 2, 'host_memory': '200G', 'max_num_seqs': 32,
                'max_batched_tokens': 32768, 'gpu_memory': .85, 'concurrency': 32,
                'temperature': 0.0, 'vision_temperature': None, 'speculation': None,
                'extra_args': ['--chat-template-content-format', 'string'],
                'source': 'https://huggingface.co/' + FASTINO,
                'adaptation': 'Fastino Super quickstart: BF16 TP2 on B200; greedy sampling. No unvalidated MTP head.'},
    'nvfp4': {'model': NVFP4, 'gpus': 1, 'host_memory': '96G', 'max_num_seqs': 512,
               'max_batched_tokens': 32768, 'gpu_memory': .9, 'concurrency': 64,
               'temperature': 1.0, 'vision_temperature': .6,
               'speculation': {'method': 'mtp', 'num_speculative_tokens': 5},
               'extra_args': MEDIA_ARGS + TOOL_ARGS + MAMBA_ARGS + [
                   '--prefix-match-unit', '16', '--async-scheduling', '--mamba-cache-mode', 'align',
                   '--kernel-config', '{"linear_backend_per_quant":{"fp8_w8a8":"torch"}}',
                   '--compilation-config', '{"pass_config":{"fuse_attn_quant":true},"use_inductor_graph_partition":true,"cudagraph_mode":"FULL_AND_PIECEWISE"}'],
               'source': 'https://huggingface.co/' + NVFP4 + '#vllm',
               'adaptation': 'Official 1x GB200 recipe on 1x B200; explicit 0.90 memory utilization.'},
    'bf16': {'model': BF16, 'gpus': 2, 'host_memory': '200G', 'max_num_seqs': 32,
              'max_batched_tokens': 32768, 'gpu_memory': .9, 'concurrency': 32,
              'temperature': 1.0, 'vision_temperature': .6,
              'speculation': {'method': 'mtp', 'num_speculative_tokens': 5},
              'extra_args': MEDIA_ARGS + TOOL_ARGS + ['--async-scheduling', '--enable-mamba-shared-prefix-checkpoint'],
              'source': 'https://huggingface.co/' + BF16 + '#vllm',
              'adaptation': 'Official detailed 2x GB200 recipe on 2x B200 (MTP5, not the quickstart MTP3). Explicit prefill and memory caps.'},
    'lilicorr': {'model': NVFP4, 'drafter': LILICORR, 'gpus': 1, 'host_memory': '96G',
                 'max_num_seqs': 1, 'max_batched_tokens': 8192, 'gpu_memory': .75, 'concurrency': 1,
                 'temperature': 1.0, 'vision_temperature': .6,
                 'speculation': {'method': 'dflash', 'num_speculative_tokens': 7},
                 'extra_args': MEDIA_ARGS + TOOL_ARGS + MAMBA_ARGS + ['--block-size', '64', '--prefix-match-unit', '64'],
                 'source': 'https://huggingface.co/' + NVFP4 + '#quick-start',
                 'adaptation': 'Official Spark TP1/c1 LiLiCorr7 recipe adapted to B200. Not a published B200 performance claim.'},
    'spark': SPARK_PROFILE,
    'redhat-fp8-dspark': REDHAT_PROFILE,
    **BEAM_PROFILES,
}

# Every publisher configuration, including Muse Spark, participates by default.
DEFAULT_PROFILES = tuple(PROFILES)


def arm_definitions(with_images=False):
    arms = {name + '-text': {'profile': name, 'pixels': False} for name in PROFILES}
    if with_images:
        arms.update({name + '-vision': {'profile': name, 'pixels': True}
                     for name, profile in PROFILES.items() if name != 'fastino' and profile.get('supports_images', True)})
    return arms
