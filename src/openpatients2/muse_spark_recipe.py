"""Meta's launch-day Spark Open NVFP4 + DSpark recipe, checked 2026-10-15.

The published TP4 deployment uses B300s. Our TP8/EP8 profile uses B200s;
this is a memory-conservative adaptation, not a hardware-validated result.
"""

SPARK = 'meta-models/Muse-Spark-Open-816B-A42B-NVFP4'
DSPARK = 'Inferact/Muse-Spark-Open-DSpark'
IMAGE_DIGEST = 'sha256:2790bff92e9f2a796dcb65682dc5c2e9559f967a4b773f6ae4ca09dbd31c1081'
IMAGE = 'docker://vllm/vllm-openai@' + IMAGE_DIGEST
TOKENIZER_CONTRACT = 'vllm/' + IMAGE_DIGEST
SIF_NAME = 'vllm-muse-spark-open-' + IMAGE_DIGEST.split(':')[1][:16] + '.sif'
SOURCE = 'https://dev.meta.ai/docs/muse-spark-open/deploy#vllm'
TEMPLATE_DATE = '2026-10-15'
STOP_TOKEN_IDS = [200001, 200008]  # end-of-text and end-of-turn, never <|eom|>

PINS = {
    SPARK: {'revision': '4239e4db19376904f2e82c167e78f70abb5455fe',
            'family': 'muse_spark',
            # Gated file bytes could not be read locally. Public HF Git blob IDs
            # pin the exact bytes and are checked after authorized CPU download.
            'git_blobs': {'config.json': 'd2cd088c7fe91bb7c720fb283c68d37a56715e17',
                          'chat_template.jinja': '2500a42b6dd93185735e6895a1a638f179592a4b',
                          'generation_config.json': 'b79d6e4978ec47474ed7aa8f69cf192b9dea83cb',
                          'l4_200k_base': '6d1ad50551f0efec536ebd576778dece66c13b99'}},
    DSPARK: {'revision': '6f7aed1cac8d8b1c02a902988876172b39e49fc0',
             'architecture': 'MuseSparkDSparkModel',
             'config_sha256': '588298a4cd6e5179467656644c33d29c31f33f4c951f5c26d2b43aaa390b9af7'},
}

ENGINE = {'image': IMAGE, 'version': 'muse-spark-open', 'sif': SIF_NAME,
          'tokenizer_contract': TOKENIZER_CONTRACT,
          # The OCI digest pins this launch image. Its source-revision label
          # currently points to a nonpublic commit, so record source hashes at
          # setup and require live /tokenize versus generation agreement.
          'tokenizer_source_sha': None, 'registry_source_sha': None}

PROFILE = {
    'model': SPARK, 'drafter': DSPARK, 'engine': 'spark', 'gpus': 8, 'cpus': 16,
    'host_memory': '200G', 'max_num_seqs': 32, 'max_batched_tokens': 32768,
    'gpu_memory': .9, 'concurrency': 32, 'temperature': 1.0, 'top_p': 1.0,
    'vision_temperature': None, 'reasoning_effort': 'xhigh', 'template_date': TEMPLATE_DATE,
    'stop_token_ids': STOP_TOKEN_IDS, 'reasoning_parser': 'muse', 'expert_parallel': True,
    'served_model': 'muse-spark-open',
    'speculation': {'method': 'dspark', 'num_speculative_tokens': 7,
                    'draft_sample_method': 'probabilistic', 'rejection_sample_method': 'block',
                    'enable_adaptive_verification': True},
    'extra_args': ['--enable-auto-tool-choice', '--tool-call-parser', 'muse',
                   '--max-parallel-loading-workers', '2', '--limit-mm-per-prompt', '{"image":1,"video":0,"audio":0}'],
    'environment': {'VLLM_USE_RUST_FRONTEND': '1'},
    'source': SOURCE,
    'adaptation': 'Official NVFP4 + DSpark7 image/EP/muse parsers/Rust frontend. '
        'TP8/EP8 on eight B200s replaces TP4 on four B300s. Project context 64K, c32, '
        'prefill 32K and two parallel loading workers bound memory. Requires a real B200 smoke.',
}
