"""Official Mistral Large 4 NVFP4 + embedded DSpark3, pinned 2026-10-29."""
MODEL = 'mistralai/Mistral-Large-4-1T-A52B-NVFP4'
DIGEST = 'sha256:13bc2adaca593fddfa8f88e41f7cea53582d46d169d63bd86e1477157575fa52'
TOKENIZER_CONTRACT = 'vllm/' + DIGEST
PINS = {MODEL: {'revision': 'd5fdac874617651e8dac18fe8609075700ce8c93', 'config_file': 'params.json',
    'config_sha256': 'c09dc9ea714c8de78f42f8a86dc6c1ecec039f8a8c465eb39cc33994f2f89cfd', 'template_sha256': 'd98cb33c51a88da63c7929592fb6348b1f4c2efb1b936c18ab93bb4520e1c8fc'}}
ENGINE = {'image': 'docker://vllm/vllm-openai@' + DIGEST, 'version': 'mistral-large-4',
 'sif': 'vllm-mistral-large-4-' + DIGEST.split(':')[1][:16] + '.sif',
 'tokenizer_contract': TOKENIZER_CONTRACT, 'tokenizer_source_sha': None, 'registry_source_sha': None}
PROFILE = {'model': MODEL, 'engine': 'mistral', 'gpus': 8, 'cpus': 16, 'host_memory': '200G',
 'context': 262144, 'max_num_seqs': 16, 'max_batched_tokens': 16384,
 'gpu_memory': .85, 'concurrency': 16, 'temperature': 1.0, 'vision_temperature': 1.0,
 'top_p': .95, 'supports_images': True, 'reasoning_effort': 'high',
 'served_model': MODEL, 'reasoning_parser': 'mistral', 'kv_cache_dtype': 'fp8_ds_mla',
 'speculation': {'method': 'dspark', 'num_speculative_tokens': 3},
 'extra_args': ['--enable-auto-tool-choice', '--tool-call-parser', 'mistral',
                '--config-format', 'mistral', '--load-format', 'mistral',
                '--max-parallel-loading-workers', '2'],
 'source': 'https://huggingface.co/' + MODEL,
 'adaptation': 'Official NVFP4 TP8/DSpark3 recipe, native Mistral template/parsers, '
    'T1/top-p .95/high reasoning; project caps context at 256K and concurrency at 16. '
    'One eight-B200 node. GPU and host-memory fit require real hardware smoke.'}
