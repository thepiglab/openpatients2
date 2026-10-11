import copy
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from openpatients2.provenance import json_digest


ROOT=Path(__file__).resolve().parents[1]


def test_template_library_is_runtime_dependency():
    import tomllib
    project=tomllib.loads((ROOT/'pyproject.toml').read_text())
    assert 'jinja2==3.1.6' in project['project']['dependencies']
    assert 'jinja2==3.1.6' not in project['dependency-groups']['dev']


