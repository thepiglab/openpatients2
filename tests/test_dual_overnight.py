import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest


@pytest.mark.parametrize('free_gib', [1295, 1296])
def test_dual_launch_checks_combined_disk_before_submitting(tmp_path, free_gib):
    beam = tmp_path / 'beam'; mistral = tmp_path / 'mistral'
    for repo in (beam, mistral):
        (repo / 'scripts').mkdir(parents=True)
    root = Path(__file__).resolve().parents[1]
    launcher = beam / 'scripts/run_dense_dual_overnight.sh'
    shutil.copyfile(root / 'scripts/run_dense_dual_overnight.sh', launcher)
    for repo, name in [(beam, 'beam'), (mistral, 'le_chonk')]:
        (repo / f'scripts/run_dense_{name}.sh').write_text(
            'printf "%s %s %s %s\\n" "$DENSE_ACCOUNT" "$DENSE_QOS" "$DENSE_HOURS" "$1" >> "$TEST_CALLS"\n')
    binaries = tmp_path / 'bin'; binaries.mkdir()
    git = binaries / 'git'; git.write_text('#!/bin/sh\nexit 0\n'); git.chmod(0o755)
    python = binaries / 'python3'
    python.write_text(f'#!{sys.executable}\nimport sys, shutil\nfrom types import SimpleNamespace\n'
        f'shutil.disk_usage=lambda _: SimpleNamespace(free={free_gib}*2**30)\nexec(sys.stdin.read())\n')
    python.chmod(0o755)
    calls = tmp_path / 'calls'
    result = subprocess.run(['bash', str(launcher), str(mistral)], text=True, capture_output=True,
        env={**os.environ, 'PATH': str(binaries)+os.pathsep+os.environ['PATH'], 'TEST_CALLS': str(calls)})
    if free_gib < 1296:
        assert result.returncode != 0 and not calls.exists()
        assert 'No jobs submitted' in result.stderr
    else:
        assert result.returncode == 0, result.stderr
        assert calls.read_text().splitlines() == ['cai5724 cai5724 12 overnight', 'cai6734 cai6734 12 overnight']
