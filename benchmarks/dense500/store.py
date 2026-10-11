"""Per-article path map for the dense reference sets; the implementation is `openpatients2.dense_store`.

Benchmark tools `import store`; this alias makes that the very same module object.
"""
import sys

from openpatients2 import dense_store

sys.modules[__name__] = dense_store
