#!/usr/bin/python3
"""Forward the startup timeout through the pinned Rust frontend's JSON bootstrap.

The launch image deserializes --args-json without clap's environment handling.
Keep inherited socket FDs/process identity by exec'ing the original binary.
No weights, templates, sampling, or request handling are changed.
"""
import json
import os
from pathlib import Path
import sys


def bootstrap_args(argv, timeout):
    argv = list(argv)
    if timeout <= 0:
        raise ValueError('Rust engine startup timeout must be positive')
    if argv.count('--args-json') != 1:
        raise ValueError('Expected exactly one Rust frontend --args-json argument')
    index = argv.index('--args-json') + 1
    payload = json.loads(argv[index])
    payload['engine_ready_timeout_secs'] = timeout
    argv[index] = json.dumps(payload)
    return argv


def main():
    argv = bootstrap_args(sys.argv[1:], int(os.environ['VLLM_ENGINE_READY_TIMEOUT_S']))
    # Resolve the image's default binary, not this wrapper, in this fresh process.
    os.environ.pop('VLLM_RUST_FRONTEND_PATH', None)
    from vllm import envs
    binary = envs.VLLM_RUST_FRONTEND_PATH
    if not binary or Path(binary).resolve() == Path(__file__).resolve():
        raise RuntimeError('Cannot resolve the original Rust frontend binary')
    os.execv(binary, [binary, *argv])


if __name__ == '__main__':
    main()
