"""Red Hat's FP8 Nemotron verifier with its own DSpark8 drafter, 2026-10-15.

The FP8 card explicitly pairs these checkpoints. The DSpark serving command is
the authority for drafting settings; Muse Spark's DSpark defaults do not apply.
"""
import copy


def quantization_overrides(config):
    """Preserve the checkpoint's BF16 embedding exclusion after vLLM renames it.

    The pinned vLLM mapper preserves regex targets verbatim. The checkpoint's
    backbone.embeddings regex consequently misses language_model.model.embed_tokens.
    --hf-overrides replaces dict fields, so send the ENTIRE original quant config.
    """
    quant = copy.deepcopy(config['quantization_config'])
    if (quant.get('quant_method') != 'compressed-tensors'
            or r're:.*backbone\.embeddings$' not in quant.get('ignore', [])):
        raise ValueError('Unexpected RedHat embedding quantization exclusion')
    alias = r're:.*(?:^|\.)model\.embed_tokens$'
    if alias not in quant['ignore']:
        quant['ignore'].append(alias)
    return {'quantization_config': quant}

FP8 = 'RedHatAI/NVIDIA-Nemotron-3.5-Super-VL-120B-A12B-FP8'
DSPARK = 'RedHatAI/NVIDIA-Nemotron-3.5-Super-VL-120B-A12B.dspark'

PINS = {
    FP8: {'revision': '38effd54b334bfda8fce082f4290aa56d86b7533',
          'architecture': 'NemotronH_Omni_Reasoning_V3',
          'config_sha256': '5ed5d901b48fa2d5ed2c698881530e08b03982eb150864c2426e12571b840baf',
          'template_sha256': '1369e90b19976f99ce2070eb21ade8fcb4af640f008e14e82af5c5e89a9cc816'},
    DSPARK: {'revision': '4ff45128272e4e5c00ab82d2886946650229ced7',
             'architecture': 'DSparkDraftModel',
             'config_sha256': '88462ccd207ac7238f656bcab777cc320ef0a006e66702ac3b09736da6404365'},
}

PROFILE = {
    'model': FP8, 'drafter': DSPARK, 'gpus': 2, 'host_memory': '200G',
    'max_num_seqs': 128, 'max_batched_tokens': 16384, 'gpu_memory': .89,
    'concurrency': 64, 'temperature': 1.0, 'vision_temperature': .6,
    'expert_parallel': True,
    'speculation': {'method': 'dspark', 'num_speculative_tokens': 8,
                    'draft_tensor_parallel_size': 1, 'draft_sample_method': 'greedy',
                    'rejection_sample_method': 'standard', 'enable_adaptive_verification': False},
    'extra_args': [
        '--prefix-match-unit', '64', '--block-size', '64',
        '--compilation-config', '{"max_cudagraph_capture_size":192}',
        '--async-scheduling', '--mamba-backend', 'flashinfer',
        '--mamba-ssm-cache-dtype', 'float16', '--enable-mamba-cache-stochastic-rounding',
        '--mamba-cache-philox-rounds', '5', '--chat-template-content-format', 'string',
        '--mamba-cache-mode', 'align', '--enable-auto-tool-choice',
        '--tool-call-parser', 'qwen3_coder', '--limit-mm-per-prompt', '{"image":1,"video":0}',
    ],
    'source': 'https://huggingface.co/' + DSPARK + '#vllm-serving',
    'adaptation': 'Red Hat FP8 target TP2/EP2 plus the explicitly paired DSpark8 command: '
        'draft TP1, greedy draft sampling, standard rejection, adaptive verification off. '
        'B200 replaces the documented H100 hardware; context 64K replaces 114688. '
        'Client concurrency is capped at the existing benchmark limit of 64. NVIDIA parent vision sampling '
        'T=0.6/top_p=0.95 is retained; videos disabled for this image/text benchmark. '
        'Uses the already pinned NVIDIA official nightly with Qwen3 DSpark support. '
        'Adds the vLLM-renamed embedding alias to the checkpoint quantization ignore list; '
        'original weights/config remain byte-identical. '
        'Requires a real B200 smoke; no publisher speedup is claimed.',
}
