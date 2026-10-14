"""Exercise launchers without a real Slurm controller or repository snapshot."""
import os
from pathlib import Path
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize('campaign', ['dense_gepa', 'dense_fastino', 'dense_gepa_next'])
@pytest.mark.parametrize('reject', [False, True])
def test_launch_queues_without_start_time_estimate(tmp_path, campaign, reject):
    scripts = tmp_path / 'scripts'
    scripts.mkdir()
    launcher = scripts / f'run_{campaign}.sh'
    launcher.write_text((ROOT / 'scripts' / launcher.name).read_text())
    binary = tmp_path / 'bin'
    binary.mkdir()
    git = binary / 'git'
    git.write_text('#!/usr/bin/env bash\ncase " $* " in *" rev-parse "*) echo 0123456789012345678901234567890123456789;; esac\n')
    git.chmod(0o755)
    sbatch = binary / 'sbatch'
    sbatch.write_text('''#!/usr/bin/env bash
printf '%s\n' "$*" >> "$LAUNCH_TEST_CALLS"
if [[ "$*" == *--test-only* ]]; then
  echo 'allocation failure: Resource temporarily unavailable' >&2
  exit 1
fi
if [[ "$LAUNCH_TEST_REJECT" == 1 ]]; then
  echo 'sbatch: error: Invalid qos specification' >&2
  exit 1
fi
echo 90001
''')
    sbatch.chmod(0o755)
    calls = tmp_path / 'sbatch-calls.txt'
    work = tmp_path / 'campaign'
    result = subprocess.run(['bash', str(launcher), str(work)], cwd=tmp_path,
        text=True, capture_output=True, env={**os.environ, 'PATH': str(binary)+':'+os.environ['PATH'],
            'LAUNCH_TEST_CALLS': str(calls), 'LAUNCH_TEST_REJECT': str(int(reject))})
    actual = calls.read_text().splitlines()
    assert len(actual) == 1 and '--test-only' not in actual[0]
    job_file = Path(str(work)+'.bootstrap') / 'job-id.txt'
    assert not (tmp_path / 'runs' / (campaign.replace('_', '-')+'-launch.lock')).exists()
    if reject:
        assert result.returncode != 0 and 'Invalid qos' in result.stderr
        assert not job_file.exists()
    else:
        assert result.returncode == 0, result.stderr
        assert 'Preparation job: 90001' in result.stdout
        assert job_file.read_text().strip() == '90001'
