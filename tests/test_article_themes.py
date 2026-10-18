"""Theme scheme and the rule-based classifier that places the original articles in it."""
import hashlib
import json
from collections import Counter
from pathlib import Path

import pytest

from openpatients2.article_themes import FALLBACK, GROUPS, LANES, PRIORITY, assign, classify, scheme, xml_features
from openpatients2.viewer import THEME_FEATURES, Library

CORPUS = Path(__file__).resolve().parents[1] / 'benchmarks' / 'dense'
checked_out = pytest.mark.skipif(not (CORPUS / 'selection.json').exists(), reason='benchmarks not checked out')


def test_scheme_has_every_selection_lane_under_its_own_group():
    rows = json.loads((CORPUS / 'selection.json').read_text())
    for row in (r for r in rows if r.get('lane')):
        assert LANES[row['lane']][0] == row['group']
    groups = [g['group'] for g in scheme()]
    assert groups == list(GROUPS) and all(g['name'] and g['lanes'] for g in scheme())
    assert sorted(l['lane'] for g in scheme() for l in g['lanes']) == sorted(LANES)


@pytest.mark.parametrize('title,kwargs,lane', [
    ('Wellens syndrome on serial electrocardiograms', {}, 'ecg_waveform'),
    ('Optical coherence tomography in macular hole', {}, 'ophthalmic_imaging'),
    ('Diabetic ketoacidosis in type 1 diabetes', {}, 'diabetes_endocrine'),
    ('Fetal echocardiography in a twin pregnancy', {'stratum': 'obstetric'}, 'obstetric'),
    ('Paraquat ingestion', {'stratum': 'toxicology'}, 'toxicology_substance'),
    ('A rare tumour', {'ages': [72, 80]}, 'geriatric'),
    ('A rare tumour', {'ages': [4]}, 'pediatric_common'),
    ('A rare tumour', {'patients': 4}, 'case_series'),
    ('A rare tumour', {'affiliations': ['Department of Surgery, University of Lagos, Nigeria']}, 'africa'),
    ('A rare tumour', {}, FALLBACK),
    ('Complete heart block in acute myocarditis', {}, 'ecg_waveform'),
    ('Recurrent pneumothorax over a decade', {}, 'longitudinal_multiyear'),
    ('Pneumonia in an adult', {}, 'copd_asthma_pulm'),
    ('Septic shock in an elderly woman', {}, 'geriatric'),
    ('Kawasaki disease among Egyptian children', {'affiliations': ['Cairo University, Egypt']}, 'africa'),
    ('EEG findings in autism spectrum disorder', {}, 'neurophysiology'),
    ('Autism spectrum disorder in a girl', {'stratum': 'psychiatry'}, 'psychiatry'),
    ('Severe congenital neutropenia in a newborn', {'stratum': 'neonatal'}, 'serial_labs'),
])
def test_primary_lane(title, kwargs, lane):
    result = classify(title, **kwargs)
    assert result['lane'] == lane and result['group'] == LANES[lane][0] and result['tags'][0] == lane


def test_animals_are_veterinary_never_pediatric_or_geriatric():
    result = classify('Hypertrophic cardiomyopathy in a young cat', ages=[0.5], species=['cat'])
    assert result['lane'] == 'veterinary'
    assert {'pediatric_common', 'geriatric'}.isdisjoint(result['tags'])
    assert 'heart_failure_af_htn' in result['tags']


def test_tags_follow_priority_and_keywords_count():
    result = classify('A case report', keywords=['echocardiography', 'heart failure'], ages=[70],
                      affiliations=['Hospital, Lima, Peru'])
    assert result['tags'] == ['echo_cardiac', 'heart_failure_af_htn', 'geriatric', 'latin_america']
    assert result['tags'] == [lane for lane in PRIORITY if lane in result['tags']]


def test_new_mexico_and_new_guinea_are_not_latin_america_or_africa():
    tags = classify('A rare tumour', affiliations=['University of New Mexico, Albuquerque, NM, USA',
                                                    'Port Moresby, Papua New Guinea'])['tags']
    assert 'latin_america' not in tags and 'africa' not in tags and 'oceania_europe_east' in tags


def test_selection_lane_wins_and_rule_tags_stay_secondary():
    rules = classify('Serial electrocardiograms in an 81-year-old man', ages=[81])
    assert assign('serial_labs', rules) == {'lane': 'serial_labs', 'group': 'data_rich_x', 'basis': 'selection',
                                            'tags': ['serial_labs', 'ecg_waveform', 'geriatric']}
    assert assign(None, classify('Nothing'))['tags'] == [FALLBACK]
    assert assign('not_a_lane', rules)['basis'] == 'rules'


def test_xml_features_read_front_matter_only():
    xml = ('<article><front><journal-title>Case Reports in <i>Cardiology</i></journal-title>'
           '<kwd>ECG</kwd><kwd> heart &amp; block </kwd><aff id="a1"><label>1</label>Lima, <country>Peru</country></aff></front>'
           '<body><kwd>not a keyword</kwd></body></article>')
    assert xml_features(xml) == {'journal': 'Case Reports in Cardiology', 'keywords': ['ECG', 'heart & block'],
                                 'affiliations': ['1 Lima, Peru']}
    assert xml_features(None) == {'journal': None, 'keywords': [], 'affiliations': []}


def _checked_in_articles():
    """(selection round, article id, selection row, raw XML or None) for every checked-in article."""
    rows = {r['article_id']: r for r in json.loads((CORPUS / 'selection.json').read_text())}
    for folder in sorted((CORPUS / 'articles').iterdir()):
        row = rows[folder.name]
        raw = folder / 'raw.xml'
        yield row['selection_round'], folder.name, row, raw.read_text(errors='replace') if raw.exists() else None


@checked_out
def test_checked_in_theme_features_cover_every_article_and_match_raw_xml():
    # Raw XML is gitignored; this table lets a fresh clone (the Pages build) classify themes as locally.
    table = json.loads((CORPUS / THEME_FEATURES).read_text())
    articles = list(_checked_in_articles())
    assert sorted(table) == sorted(aid for _, aid, _, _ in articles), 'run `op2 viewer --write-theme-features`'
    for _, aid, _, raw in articles:
        if raw is not None:
            assert table[aid] == xml_features(raw), f'{aid}: run `op2 viewer --write-theme-features`'


def _held_out(aid):
    """The fixed half of round 2 kept out of rule tuning (the other half was the development set)."""
    return int(hashlib.sha256(aid.encode()).hexdigest(), 16) % 2 == 1


@checked_out
def test_all_articles_get_a_theme_and_rules_recover_most_round2_lanes():
    features = json.loads((CORPUS / THEME_FEATURES).read_text())
    old, held, primary, tagged = Counter(), 0, 0, 0
    for selection_round, aid, row, _ in _checked_in_articles():
        rules = classify(row['title'], **features[aid], stratum=row.get('stratum'))
        final = assign(row.get('lane'), rules)
        assert final['lane'] in LANES and final['tags'][0] == final['lane']
        if selection_round == 2:
            assert final['basis'] == 'selection' and final['lane'] == row['lane']
            if _held_out(aid):
                # The stratum is the lane itself here, so the rules must do without it.
                blind = classify(row['title'], **features[aid])
                held += 1
                primary += blind['lane'] == row['lane']
                tagged += row['lane'] in blind['tags']
        else:
            assert final['basis'] == 'rules'
            old[final['group']] += 1
    assert held == 376 and sum(old.values()) == 500
    # Most old articles land in a real lane; the catch-all stays a minority.
    assert old['other'] < 100 and len(old) == len(GROUPS)
    # Held-out check: the rules alone pick the curated lane as primary for most of the new batch (0.52 before
    # tuning) and list it among the tags for nearly all.
    assert primary / held > 0.57 and tagged / held > 0.88


def test_catalog_carries_themes_scheme_and_checklist_completion(tmp_path):
    root, dataset = tmp_path / 'corpus', tmp_path / 'export'
    dataset.mkdir()
    for aid, title in (('PMC1.1', 'Holter monitoring in palpitations'), ('PMC2.1', 'A rare tumour'), ('PMC3.1', 'A rare tumour')):
        path = root / 'articles' / aid / 'source.json'
        path.parent.mkdir(parents=True)
        path.write_text(json.dumps({'article_id': aid, 'title': title, 'text_sha256': 'h', 'segments': []}))
    (root / 'selection.json').write_text(json.dumps([{'article_id': 'PMC2.1', 'stratum': 'x', 'lane': 'africa', 'selection_round': 2}]))
    # No raw XML here: PMC3.1's keywords come from the checked-in features table in the root.
    (root / THEME_FEATURES).write_text(json.dumps({'PMC3.1': {'journal': None, 'keywords': ['Echocardiography'], 'affiliations': []}}))
    checklist = tmp_path / 'article_checklist.json'
    checklist.write_text(json.dumps({'articles': {'PMC1.1': {'completed': True}}}))
    catalog = Library(dataset, [root], [checklist]).catalog()
    rows = {r['article_id']: r for r in catalog['articles']}
    assert rows['PMC1.1']['theme'] == {'lane': 'ecg_waveform', 'group': 'data_rich_x', 'tags': ['ecg_waveform'], 'basis': 'rules'}
    assert rows['PMC2.1']['theme']['lane'] == 'africa' and rows['PMC2.1']['theme']['basis'] == 'selection'
    assert rows['PMC3.1']['theme']['lane'] == 'echo_cardiac'
    assert rows['PMC1.1']['completed'] is True and 'completed' not in rows['PMC2.1']
    assert rows['PMC2.1']['selection_round'] == 2 and rows['PMC1.1']['selection_round'] is None
    assert catalog['themes'] == scheme()
