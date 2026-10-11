"""Access to `benchmarks/dense500/store.py`, the single per-article path map, from package code.

The benchmark tools import it as `store` (PYTHONPATH=src:benchmarks/dense500). Package modules
(viewer, ehr_pipeline, ehr_tasks) call `load()` so the mapping stays defined in exactly one file.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path
import sys

STORE = Path(__file__).resolve().parents[2] / 'benchmarks' / 'dense500' / 'store.py'


def load():
    module = sys.modules.get('store')
    if module is not None and Path(getattr(module, '__file__', '') or '').resolve() == STORE:
        return module
    module = sys.modules.get('openpatients2._dense_store')
    if module is None:
        if not STORE.exists():
            raise ImportError(f'Dense benchmark store module not found at {STORE}')
        spec = importlib.util.spec_from_file_location('openpatients2._dense_store', STORE)
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
    return module
