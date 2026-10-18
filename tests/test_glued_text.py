"""GLUED-TEXT: free text with dropped spaces is detected (both pipelines), segmented and repaired conservatively."""
import copy
import json
from pathlib import Path

import pytest

from openpatients2.glued_text import apply_segmentations, find_glued
from openpatients2.semantic_rules import WARNING, annotation_violations, item_violations
from openpatients2.validation import validate

ROOT = Path(__file__).resolve().parents[1]
from openpatients2.dense_store import load as load_store
store = load_store()
SOURCE = ('A 50-year-old man underwent a right hemihepatectomy. Anti-HBs appeared more than seven months later. '
          'HbA1c was 7%, IL6 was raised and COVID19 testing was negative. Biopsy could not be performed.')


def segs(text, source=SOURCE):
    return {g.token: (g.segmentation, g.confidence) for g in find_glued(text, source)}


def test_glued_words_are_segmented_with_confidence():
    found = segs('Anti-HBs appeared aftersevenmonths; objectiveflow couldnotbeperformed bymonth9 '
                 'afterrighthemihepatectomy Clinicallysignificant infectionrelateddehydration')
    assert found['aftersevenmonths'] == ('after seven months', 'high')
    assert found['couldnotbeperformed'] == ('could not be performed', 'high')
    assert found['bymonth9'] == ('by month 9', 'high')
    assert found['afterrighthemihepatectomy'] == ('after right hemihepatectomy', 'high')
    assert found['Clinicallysignificant'] == ('Clinically significant', 'high')
    assert found['infectionrelateddehydration'][0] == 'infection related dehydration'


def test_words_compounds_notation_and_identifiers_are_not_glued():
    clean = ('ownership footnote header annotator hemihepatectomy nonpharmacologic midventricular hydroxyvitamin '
             'HbA1c IL6 COVID19 not_performed DataSheet1.pdf https://example.org/aftersevenmonths readout')
    assert segs(clean) == {}
    # letter/digit boundaries alone are low/medium (identifiers, notation), never high
    assert all(c != 'high' for _, c in segs('Table1 Case2 VO2peak Day25').values())


def test_pieces_quoted_in_evidence_are_never_flagged():
    assert not find_glued('aftersevenmonths', SOURCE, quotes='seen aftersevenmonths in the source')


def test_repair_replaces_whole_pieces_only():
    text = 'Seen aftersevenmonths (file aftersevenmonths.pdf); aftersevenmonthsx stays.'
    out = apply_segmentations(text, {'aftersevenmonths': 'after seven months'})
    assert out == 'Seen after seven months (file aftersevenmonths.pdf); aftersevenmonthsx stays.'


def test_runtime_validator_warns_on_glued_free_text_not_on_evidence():
    item = {'subject': 'index_patient', 'assertion': 'present', 'temporality': 'current', 'name': 'Anti-HBs aftersevenmonths',
            'kind': 'laboratory', 'status': 'resulted', 'text_value': 'positive', 'numeric_value': None,
            'comparator': 'unknown', 'unit': None, 'flag': 'unknown', 'reference_range_text': None, 'specimen': None,
            'method': None, 'body_site': None, 'result_absent_reason': None,
            'time': {'text': None, 'relation': 'unknown', 'anchor': None, 'date_iso': None},
            'evidence': [{'quote': 'Anti-HBs appeared more than seven months later', 'source_section': None}]}
    hits = [v for v in item_violations('observations', 'items', item, SOURCE, '/items/0') if v.rule_id == 'GLUED-TEXT']
    assert [(v.severity, v.path) for v in hits] == [(WARNING, '/items/0/name')]
    assert "'after seven months'" in hits[0].message
    clean = dict(item, name='Anti-HBs')
    assert not [v for v in item_violations('observations', 'items', clean, SOURCE) if v.rule_id == 'GLUED-TEXT']
    source = 'Creatinine was 1.2 mg/dL after seven months.'
    runtime = {'subject': 'index_patient', 'assertion': 'present', 'temporality': 'current',
               'time': {'text': None, 'relation': 'unknown', 'anchor': None, 'date_iso': None},
               'evidence': [{'quote': 'Creatinine was 1.2 mg/dL', 'source_section': None}], 'result_absent_reason': None,
               'name': 'creatinine aftersevenmonths', 'kind': 'laboratory', 'status': 'resulted', 'text_value': None,
               'numeric_value': 1.2, 'comparator': '=', 'unit': 'mg/dL', 'flag': 'unknown',
               'reference_range_text': None, 'specimen': None, 'method': None, 'body_site': None}
    result = validate('observations', {'coverage': 'complete', 'limitations': [], 'documentation_status': 'documented',
                                       'documentation_evidence': [], 'items': [runtime]}, source)
    assert not result.errors, result.errors
    assert any('[GLUED-TEXT]' in w for w in result.warnings)


@pytest.fixture
def dense():
    import importlib
    return (importlib.import_module('openpatients2.dense.schema_targets'), importlib.import_module('openpatients2.dense.scan_glued_text'))


def _reference():
    root = ROOT / 'benchmarks/dense'; aid = 'PMC13624771.1'
    return (json.loads(store.path(root, 'annotations', aid).read_text()),
            json.loads(store.path(root, 'sources', aid).read_text()))


def test_materializer_keeps_spaced_text_verbatim(dense):
    """Regression: no export path collapses whitespace (glued text came from annotator-authored literals)."""
    targets, _ = dense
    annotation, source = _reference()
    patient = annotation['patients'][0]
    spaced = 'Seen after  seven months and could not be performed'
    patient['events'][0]['description'] = spaced
    patient['summary'][0]['text'] = spaced + ' (summary)'
    target = json.dumps(targets.materialize(annotation, source), ensure_ascii=False)
    assert json.dumps(spaced)[1:-1] in target and json.dumps(spaced + ' (summary)')[1:-1] in target


def test_reference_warning_and_scan_categories(dense):
    _, scan = dense
    annotation, source = _reference()
    assert not [v for v in annotation_violations(annotation, source) if v.rule_id == 'GLUED-TEXT']
    bad = copy.deepcopy(annotation)
    p = bad['patients'][0]
    p['events'][0]['description'] = 'Initial presentation aftersevenmonths'
    p['facts'][0]['notes'] = 'Reviewed couldnotbeperformed'
    warned = [v for v in annotation_violations(bad, source) if v.rule_id == 'GLUED-TEXT']
    assert [v.path for v in warned] == [p['events'][0]['event_id'] + '/description']   # notes are internal: no warning
    findings = scan.scan_annotation(bad, source)
    cats = {f['token']: f['category'] for f in findings}
    assert cats['aftersevenmonths'] == 'exported' and cats['couldnotbeperformed'] == 'internal'


def test_cryo_prefix_compound_is_not_glued():
    from openpatients2.glued_text import glued_findings
    assert glued_findings('Embryos were cryopreserved for later transfer.') == []
