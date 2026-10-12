"""Unified scorer: invariance self-tests on reference rows plus focused synthetic checks."""
from copy import deepcopy
import json
from pathlib import Path
import re

import pytest

from openpatients2.scoring.assign import assign
from openpatients2.scoring.text import norm_figure_id, norm_panel
from openpatients2.unified_scoring import load_rows, main, score

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / 'data/dense_clinical_cases_v1/data'
# Committed, trimmed unified rows (2 two-patient articles, figure panels, a measurement series) so the
# invariance tests never depend on the live release, which may be empty while articles are re-signed.
FIXTURE = ROOT / 'tests/fixtures/unified_scoring_reference.jsonl'


def _live_release():
    files = sorted(DATA.glob('*.jsonl')) if DATA.exists() else []
    return load_rows(DATA) if any(f.stat().st_size for f in files) else {}


def _symbol_observation(template, value, quote):
    """Semi-quantitative results printed as symbols ('+++', '−') must still equal themselves."""
    return {**deepcopy(template), 'name': 'Biopsy IgG staining', 'text_value': value, 'numeric_value': None,
            'unit': None, 'comparator': 'unknown', 'evidence': [{'quote': quote, 'source_section': 'b99999'}]}


@pytest.fixture(scope='module')
def ref():
    rows = load_rows(FIXTURE)
    # Exercise the scoring contracts the live data now carries: symbol-only values and review_only items.
    patient = rows[sorted(rows)[0]]['patients'][0]
    obs = patient['sections']['observations']['items']
    obs += [_symbol_observation(obs[0], '+++', 'IgG +++ on immunofluorescence'),
            _symbol_observation(obs[0], '\u2212', 'IgA \u2212 on immunofluorescence')]
    review = deepcopy(obs[0]); review.update(name='cohort mean value', scoring='review_only',
                                              evidence=[{'quote': 'the cohort mean was 12', 'source_section': 'b99998'}])
    obs.append(review)
    return rows


def _all_ones(obj, path=''):
    """Every precision/recall/f1/accuracy in the score is 1.0 (or None when there is no denominator)."""
    bad = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k in ('precision', 'recall', 'f1', 'accuracy', 'coverage') or k.endswith('_f1') or k.endswith('accuracy'):
                if isinstance(v, (int, float)) and not isinstance(v, bool) and v != 1.0:
                    bad.append(f'{path}.{k}={v}')
            bad += _all_ones(v, f'{path}.{k}')
    return bad


def _items(row):
    for p in row['patients']:
        for sec in p['sections'].values():
            for coll in ('items', 'tumors', 'biomarkers', 'treatments'):
                yield from sec.get(coll) or []


def test_reference_vs_itself_is_perfect(ref):
    s = score(ref, deepcopy(ref))
    assert _all_ones(s) == []
    assert s['clinical']['tp'] > 0 and s['clinical']['fp'] == s['clinical']['fn'] == 0
    assert all(s['clinical']['by_task'][t]['fn'] == 0 for t in s['clinical']['by_task'])
    assert s['coverage']['coverage'] == 1.0 and s['roster']['tp'] == sum(len(r['patients']) for r in ref.values())
    assert s['clinical_item_names']['recall'] == 1.0 and s['panel_ehr_category_accuracy'] == 1.0


def test_renumbered_and_reordered_patients_unchanged(ref):
    pred = deepcopy(ref)
    for r in pred.values():
        ids = [p['patient_id'] for p in r['patients']]
        m = dict(zip(ids, ids[1:] + ids[:1]))
        for p in r['patients']:
            p['patient_id'] = m[p['patient_id']]
        r['patients'].reverse()
        for f in r['figures']:
            for pn in f['panels']:
                pn['patient_ids'] = sorted(m[x] for x in pn['patient_ids'])
        for s in r['measurement_series']['series']:
            s['patient_ids'] = sorted(m[x] for x in s['patient_ids'])
    assert _all_ones(score(ref, pred)) == []


def test_renamed_items_with_same_evidence_unchanged(ref):
    pred = deepcopy(ref)
    for r in pred.values():
        for item in _items(r):
            if isinstance(item.get('name'), str):
                item['name'] = re.sub(r'^(admission|initial|repeat|baseline)\s+', '', item['name'], flags=re.I).upper() + ' (as documented)'
        for p in r['patients']:
            for sec in p['sections'].values():
                if isinstance(sec.get('items'), list):
                    sec['items'].reverse()   # order must not matter either
    base, renamed = score(ref, ref), score(ref, pred)
    for key in ('tp', 'fp', 'fn'):
        assert renamed['clinical'][key] == base['clinical'][key]
    assert renamed['clinical']['attribute_accuracy']['accuracy'] == 1.0


def test_polarity_flip_lowers_assertion_accuracy_not_recall(ref):
    pred = deepcopy(ref)
    flipped = 0
    for r in pred.values():
        for p in r['patients']:
            for item in p['sections']['conditions']['items']:
                if item['assertion'] == 'present':
                    item['assertion'] = 'absent'; flipped += 1
    s = score(ref, pred)
    assert flipped and s['clinical']['recall'] == 1.0
    acc = s['clinical']['per_attribute']['assertion']
    assert acc['total'] - acc['correct'] == flipped
    assert s['clinical']['by_task']['conditions']['attributes']['assertion']['accuracy'] < 1.0


def test_dropping_an_article_lowers_recall_and_coverage(ref):
    dropped = sorted(ref)[0]
    pred = {a: r for a, r in deepcopy(ref).items() if a != dropped}
    s = score(ref, pred)
    n_items = sum(it.get('scoring') != 'review_only' for it in _items(ref[dropped])) + len(ref[dropped]['patients'])
    assert s['coverage']['missing_articles'] == [dropped] and s['coverage']['coverage'] < 1.0
    assert s['clinical']['fn'] == n_items and s['clinical']['recall'] < 1.0 and s['clinical']['precision'] == 1.0
    assert s['roster']['fn'] == len(ref[dropped]['patients'])


def test_section_moves_are_pooled_and_reported(ref):
    pred = deepcopy(ref)
    moved = 0
    for r in pred.values():
        for p in r['patients']:
            o, s = p['sections']['observations'], p['sections']['symptoms_function']
            move = [i for i in o['items'] if i['kind'] in ('other', 'physiologic')]
            o['items'] = [i for i in o['items'] if i not in move]
            s['items'] += [dict(i, kind='sign') for i in move]
            moved += sum(i.get('scoring') != 'review_only' for i in move)
            pd = p['sections']['procedures_devices']
            pd['items'] = [i for i in pd['items'] if not re.search(r'\b(CT|MRI|ultrasound|echocardiogra\w*)\b', i['name'], re.I)]
    s = score(ref, pred)
    assert moved and s['clinical']['cross_section_matches'].get('observations<->symptoms_function') == moved
    assert s['clinical']['precision'] == 1.0 and s['clinical']['strict_section']['f1'] < 1.0


# ---------------------------------------------------------------- synthetic

def _item(name, quote, seg='b1', **kw):
    base = {'subject': 'index_patient', 'assertion': 'present', 'temporality': 'unknown',
            'time': {'text': None, 'relation': 'unknown', 'anchor': None, 'date_iso': None},
            'evidence': [{'quote': quote, 'source_section': seg}], 'name': name}
    return {**base, **kw}


def _article(aid, patients, figures=()):
    from openpatients2.scoring.clinical import TASKS
    pats = []
    for pid, label, sections in patients:
        secs = {t: {'items': []} for t in TASKS}
        secs['case_context'] = {'case_kind': 'clinical_case'}
        secs.update(sections)
        pats.append({'patient_id': pid, 'label': label, 'identity_evidence': [{'segment_id': 'b0', 'quote': label}],
                     'sections': secs, 'timeline': None, 'summary': None})
    return {'article_id': aid, 'patients': pats, 'figures': list(figures), 'supplements': [],
            'measurement_series': {'status': 'not_started', 'series': []}}


@pytest.mark.skipif(not _live_release(), reason='live release is empty (articles withheld pending re-sign)')
def test_live_release_vs_itself_is_perfect():
    rows = _live_release()
    assert _all_ones(score(rows, deepcopy(rows))) == []


def test_reference_review_only_absorbs_but_is_not_required(ref):
    s = score(ref, deepcopy(ref))
    assert s['clinical']['review_only'] == {'predictions_absorbed': 1, 'reference_not_required': 0}
    pred = deepcopy(ref)
    for r in pred.values():
        for p in r['patients']:
            for sec in p['sections'].values():
                if isinstance(sec.get('items'), list):
                    sec['items'] = [i for i in sec['items'] if i.get('scoring') != 'review_only']
    s = score(ref, pred)
    assert s['clinical']['recall'] == 1.0 and s['clinical']['review_only']['reference_not_required'] == 1


def test_review_only_items_neither_tp_fp_nor_required():
    obs = lambda *a, **k: _item(*a, kind='laboratory', status='resulted', numeric_value=None, text_value=None,
                                unit=None, comparator='unknown', flag='unknown', **k)
    ref = {'A': _article('A', [('p1', 'a 40-year-old man', {'observations': {'items': [
        obs('CRP', 'CRP was 40 mg/L on admission'), obs('cohort mean ALT', 'mean ALT in the series was 50', scoring='review_only'),
        obs('cohort WBC', 'cohort WBC averaged 9', scoring='review_only')]}})])}
    pred = {'A': _article('A', [('p1', 'a 40-year-old man', {'observations': {'items': [
        obs('C-reactive protein (CRP)', 'CRP was 40 mg/L on admission'), obs('mean ALT', 'mean ALT in the series was 50')]}})])}
    c = score(ref, pred)['clinical']
    assert c['by_task']['observations']['tp'] == 1
    assert c['by_task']['observations']['fp'] == c['by_task']['observations']['fn'] == 0
    assert c['review_only'] == {'predictions_absorbed': 1, 'reference_not_required': 1}


def test_repeated_items_not_collapsed_and_value_unit_comparator_scored():
    obs = lambda q, v, u='mg/L', c='=': _item('CRP', q, kind='laboratory', status='resulted', numeric_value=v,
                                              text_value=None, unit=u, comparator=c, flag='high')
    ref = {'A': _article('A', [('p1', 'patient', {'observations': {'items': [obs('CRP was 40 on admission', 40.0),
                                                                             obs('CRP fell to 5 by day 7', 5.0)]}})])}
    pred = deepcopy(ref)
    items = pred['A']['patients'][0]['sections']['observations']['items']
    items[1].update(numeric_value=6.0, unit='mg/dl', comparator='<')
    c = score(ref, pred)['clinical']
    o = c['by_task']['observations']
    assert o['tp'] == 2 and o['fp'] == o['fn'] == 0
    for g in ('value', 'unit', 'comparator'):
        assert c['per_attribute'][g] == {'correct': 1, 'total': 2, 'accuracy': 0.5}


def test_multi_patient_alignment_by_identity_not_ids():
    def pat(pid, label, cond):
        return (pid, label, {'conditions': {'items': [_item(cond, f'{label} had {cond}', clinical_status='active')]}})
    ref = {'A': _article('A', [pat('p1', 'patient I on aripiprazole', 'tremor'), pat('p2', 'patient II on clozapine', 'seizure')])}
    pred = {'A': _article('A', [pat('p1', 'patient II on clozapine', 'seizure'), pat('p2', 'patient I on aripiprazole', 'tremor')])}
    s = score(ref, pred)
    assert s['roster']['f1'] == 1.0 and s['clinical']['f1'] == 1.0


def test_timeline_relations_through_event_matching():
    def tl(events, edges):
        return {'events': [{'event_id': e, 'kind': 'test', 'occurrence': 'occurred', 'description': d, 'times': [],
                            'evidence': [{'segment_id': 'b1', 'quote': d}]} for e, d in events],
                'edges': [{'from_event_id': a, 'to_event_id': b, 'relation': r} for a, b, r in edges]}
    ref = {'A': _article('A', [('p1', 'pt', {})])}
    pred = deepcopy(ref)
    ref['A']['patients'][0]['timeline'] = tl([('e1', 'admission with fever'), ('e2', 'CT showed abscess')], [('e1', 'e2', 'before')])
    pred['A']['patients'][0]['timeline'] = tl([('x9', 'CT showed abscess'), ('x1', 'admission with fever')], [('x9', 'x1', 'after')])
    t = score(ref, pred)['timeline']
    assert t['events']['f1'] == 1.0 and t['relations']['f1'] == 1.0


def test_panel_label_normalisation_and_figure_ids():
    assert norm_panel('(A)') == norm_panel('a') == norm_panel('Panel A') == norm_panel('1a') == 'a'
    assert norm_panel(None) == '_whole'
    assert norm_panel('Upper-left inset') == norm_panel('top left inset')
    assert norm_figure_id('FIG1') == norm_figure_id('fig1') == norm_figure_id('Figure 1') == norm_figure_id('F1')

    def fig(fid, panel, cat, owner):
        return {'figure_id': fid, 'figure_ehr_label': None, 'panels': [{
            'panel_key': f'{fid}/{panel}', 'panel': panel, 'visual': {}, 'patient_ids': [owner],
            'attribution': {'scopes': ['individual'], 'subjects': ['patient'], 'assignments': [{}]},
            'ehr': {'status': 'agent_and_parent_reviewed', 'ehr_category': cat, 'ehr_includable': True},
            'privacy': {'status': 'agent_and_parent_reviewed', 'burned_in_identifiers': 'none',
                        'sensitive_anatomy': False, 'identifiable_face': False}}]}
    ref = {'A': _article('A', [('p1', 'pt', {})], [fig('FIG1', 'A', 'clinical_imaging', 'p1')])}
    pred = {'A': _article('A', [('q7', 'pt', {})], [fig('fig1', '(a)', 'pathology', 'q7')])}
    f = score(ref, pred)['figures']
    assert f['panel_detection']['f1'] == 1.0 and f['panels_matched_only_after_label_normalisation'] == 1
    assert f['panel_ehr_category']['accuracy'] == 0.0 and f['attribution']['patient_owners_exact']['accuracy'] == 1.0


def test_assign_is_one_to_one_and_optimal():
    pairs = {(0, 0): 0.9, (0, 1): 0.8, (1, 0): 0.85}
    assert assign(pairs) == [(0, 1), (1, 0)]


def test_cli_writes_json(tmp_path):
    ref = {'A': _article('A', [('p1', 'pt', {'conditions': {'items': [_item('asthma', 'known asthma')]}})])}
    path = tmp_path / 'ref.jsonl'; path.write_text(json.dumps(ref['A']) + '\n')
    main(['--reference', str(path), '--predicted', str(path), '--out', str(tmp_path / 'score.json')])
    out = json.loads((tmp_path / 'score.json').read_text())
    assert out['schema_version'] == 'unified-score/2' and out['overall']['clinical_items']['f1'] == 1.0
