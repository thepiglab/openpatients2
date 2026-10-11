"""Deterministic letter-digit glue fixer: whitelist only, quotes/ids/notation untouched, visual digests recomputed."""
import json
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
AID = 'PMC-glue-test.1'


@pytest.fixture
def fixer(monkeypatch):
    monkeypatch.syspath_prepend(str(ROOT / 'benchmarks/dense500'))
    sys.modules.pop('fix_label_digit_glue', None)
    import fix_label_digit_glue
    return fix_label_digit_glue


def test_whitelisted_rewrites_only(fixer):
    fix = lambda s, src='': fixer.fix_text(s, src)[0]
    assert fix('Case1: 28-year-old') == 'Case 1: 28-year-old' and fix('see Fig.2 and Table1') == 'see Fig. 2 and Table 1'
    assert fix('a 35mm lesion over 3days, on day1–4') == 'a 35 mm lesion over 3 days, on day 1–4'
    assert fix('HbA1c 7%, VO2peak 20, IL6, T1/T2, 8Fr, 100mmHg, subtype2') == 'HbA1c 7%, VO2peak 20, IL6, T1/T2, 8Fr, 100mmHg, subtype2'
    assert fix('legend "Case1 Day3" printed; Case1') == 'legend "Case1 Day3" printed; Case 1'     # quoted printed text kept
    assert fix('file Case1_scan.png at https://x.org/Day3') == 'file Case1_scan.png at https://x.org/Day3'
    assert fix('HST,CPAP') == 'HST,CPAP' and fix('HST,CPAP', 'used HST, CPAP') == 'HST, CPAP'   # only if source spaces it


def test_plan_apply_recomputes_visual_digest_and_lists_stale_receipts(fixer, tmp_path):
    from openpatients2.provenance import json_digest
    root = tmp_path
    source = {'article_id': AID, 'title': 'T', 'segments': [{'segment_id': 'b1', 'text': 'Case 1 had a 35 mm cyst.'}], 'figures': []}
    annotation = {'article_id': AID, 'patients': [{'patient_id': 'p1', 'label': 'Case1', 'species': 'human',
        'identity_evidence': [{'segment_id': 'b1', 'quote': 'Case 1'}],
        'facts': [{'fact_id': 'p1-f1', 'task': 'conditions', 'fields': {'name': 'cyst', 'text_value': '35mm cyst'},
                   'evidence': [{'segment_id': 'b1', 'quote': 'Case 1 had a 35 mm cyst.'}]}]}]}
    visual = {'article_id': AID, 'figures': [{'figure_id': 'F1', 'analysis': {'visual': {
        'general_description': 'Case1 axial CT', 'detailed_description': 'Printed "Day3" label', 'panels': []}}}]}
    labels = {'visual_target_sha256': json_digest(visual), 'figures': [{'figure_id': 'F1', 'panels': [
        {'panel': None, 'label': {'rationale': 'Case1 imaging', 'ehr_category': 'clinical_imaging'}}]}]}
    privacy = {'visual_target_sha256': json_digest(visual), 'figures': [{'figure_id': 'F1', 'panels': [{'panel': None, 'notes': 'none'}]}]}
    for folder, doc in (('sources', source), ('annotations', annotation), ('visual-submissions', visual),
                        ('figure-ehr-labels', labels), ('figure-privacy-flags', privacy)):
        (root / folder).mkdir(); (root / folder / f'{AID}.json').write_text(json.dumps(doc, indent=2) + '\n')
    for folder in ('reviews', 'schema-agent-reviews', 'visual-agent-reviews', 'figure-ehr-parent-reviews', 'figure-privacy-agent-reviews'):
        (root / folder).mkdir(); (root / folder / f'{AID}.json').write_text('{}')
    before = {p: p.read_bytes() for p in root.rglob('*.json')}
    plan = fixer.plan_article(root, AID)
    assert {p: p.read_bytes() for p in root.rglob('*.json')} == before          # planning writes nothing
    assert set(plan['changed_files']) == {'annotations', 'visual-submissions', 'figure-ehr-labels', 'figure-privacy-flags'}
    assert plan['stale_receipts'] == sorted(f'{f}/{AID}.json' for f in ('reviews', 'schema-agent-reviews', 'visual-agent-reviews',
                                                                         'figure-ehr-parent-reviews', 'figure-privacy-agent-reviews'))
    fixer.apply(root, plan)
    new_annotation = json.loads((root / 'annotations' / f'{AID}.json').read_text())
    assert new_annotation['patients'][0]['label'] == 'Case 1'
    assert new_annotation['patients'][0]['facts'][0]['fields']['text_value'] == '35 mm cyst'
    assert new_annotation['patients'][0]['facts'][0]['evidence'] == annotation['patients'][0]['facts'][0]['evidence']
    new_visual = json.loads((root / 'visual-submissions' / f'{AID}.json').read_text())
    assert new_visual['figures'][0]['analysis']['visual']['general_description'] == 'Case 1 axial CT'
    assert new_visual['figures'][0]['analysis']['visual']['detailed_description'] == 'Printed "Day3" label'
    for folder in ('figure-ehr-labels', 'figure-privacy-flags'):
        assert json.loads((root / folder / f'{AID}.json').read_text())['visual_target_sha256'] == json_digest(new_visual)
    assert json.loads((root / 'figure-ehr-labels' / f'{AID}.json').read_text())['figures'][0]['panels'][0]['label']['rationale'] == 'Case 1 imaging'
    assert not fixer.plan_article(root, AID)['changed_files']                     # idempotent
