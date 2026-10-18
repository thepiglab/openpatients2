"""`op2 dense <tool>` dispatches to openpatients2.dense.<tool>; benchmarks/ holds no code."""
import importlib.util
from pathlib import Path

import pytest

from openpatients2 import cli
from openpatients2.dense import CORPUS, TOOLS

ROOT = Path(__file__).resolve().parents[1]


def test_every_listed_tool_is_a_runnable_module_and_roots_resolve_from_the_package():
    for tool in TOOLS:
        spec = importlib.util.find_spec(f'openpatients2.dense.{tool}')
        assert spec is not None and "__name__=='__main__'" in Path(spec.origin).read_text().replace(' ', '')
    assert CORPUS == ROOT / 'benchmarks/dense'
    assert not list((ROOT / 'benchmarks').rglob('*.py'))


# Other tests already imported the module; a real `op2 dense` process runs it fresh.
@pytest.mark.filterwarnings('ignore:.*found in sys.modules:RuntimeWarning')
def test_dispatch_runs_the_tool_with_its_own_arguments(capsys, tmp_path):
    assert cli.main(['dense', '--help']) == 0 and 'normalize' in capsys.readouterr().out
    assert cli.main(['dense', 'no_such_tool']) == 2
    try:
        cli.main(['dense', 'validate', str(tmp_path), '--folder', 'annotations'])
    except SystemExit as exc:
        assert exc.code is False or exc.code == 0
    assert '"checked": 0' in capsys.readouterr().out
