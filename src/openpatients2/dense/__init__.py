"""Tools for the dense clinical reference sets (benchmarks/dense50, benchmarks/dense500, benchmarks/dense500b).

The benchmark folders hold only data and docs; every tool lives here and runs as
`op2 dense <tool> ...` (or `python -m openpatients2.dense.<tool> ...`). Per-article paths come
from `openpatients2.dense_store`. Default roots resolve from this package's location in the
repository checkout, never from the current directory.
"""
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
DENSE500 = REPO / 'benchmarks' / 'dense500'
DENSE50 = REPO / 'benchmarks' / 'dense50'
# Batch 2 (permissive-licensed, gap-targeted lanes; acquired from a supplied selection list).
# Same per-article layout and annotation protocol as dense500; exported as its own dataset.
DENSE500B = REPO / 'benchmarks' / 'dense500b'
ROOTS = [DENSE50, DENSE500, DENSE500B]

# `op2 dense <tool>`: name -> one-line description (the module docstring has the details).
# Library-only modules (review_record, sidecar_receipts, signing_guard, coverage_audit) are imported, not run.
TOOLS = {
    'export': 'Build data/dense_clinical_cases_v1 from dense50+dense500 (--set dense500b: its own dataset)',
    'reference_gates': 'Runtime checker gates for a reference annotation (ROOT --article ID)',
    'validate': 'Validate annotations against their sources (ROOT [--folder drafts|annotations])',
    'normalize': 'Apply the shared annotation rules and regenerate schema targets (dry run unless --apply)',
    'schema_targets': 'Materialize the runtime-schema target draft for an article',
    'schema_review': 'Validate / sign a full schema target',
    'visual_review': 'Validate / sign a figure pixel (visual) target',
    'figure_assets': 'Fetch figure pixels into the shared cache and record figure_assets.json',
    'figure_ehr_review': 'Draft / validate / sign figure EHR labels',
    'figure_privacy_review': 'Draft / validate / sign figure privacy flags',
    'measurement_series_review': 'Draft / validate / sign measurement series',
    'video_frames': 'Download a source-listed PMC video into the figure cache and sample frames for review (--list, --cleanup)',
    'supplement_assets': 'Acquire / peek / extract supplementary files (cache-managed)',
    'supplement_manifest_review': 'Draft / validate / sign the supplement manifest',
    'supplement_review': 'Validate / sign a supplement content review',
    'acquire': 'Discover or acquire candidate articles (discover|acquire [--limit N]; acquire --root R --selection-file F; network)',
    'scan_glued_text': 'Report glued-word tokens in annotations with proposed segmentations',
}
