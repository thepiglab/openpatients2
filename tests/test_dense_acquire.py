"""Corpus acquisition (selection files, network mocked), the permissive-only policy, and the one-root corpus."""
import asyncio
import hashlib
import json
from pathlib import Path
import shutil
from collections import Counter, defaultdict

import pytest

from openpatients2 import dense_store as store
from openpatients2.dense import CORPUS
from openpatients2.license_policy import (PERMISSIVE_ONLY_POLICY_VERSION, POLICY_VERSION, license_decision,
                                          recheck_license)

META = {'is_retracted': False, 'is_pmc_openaccess': True}


# ---- permissive-only policy -------------------------------------------------------------------------

@pytest.mark.parametrize('code', ['CC0 1.0', 'CC BY 4.0', 'CC BY-SA 4.0', 'PDM 1.0'])
def test_permissive_only_accepts_cc0_by_bysa_public_domain(code):
    result = license_decision({**META, 'license_code': code}, [], permissive_only=True)
    assert result['allowed'] and result['policy_version'] == PERMISSIVE_ONLY_POLICY_VERSION
    assert recheck_license(result)['allowed'] and recheck_license(result)['policy_version'] == PERMISSIVE_ONLY_POLICY_VERSION


@pytest.mark.parametrize('code', ['CC BY-NC 4.0', 'CC BY-NC-SA 4.0', 'CC BY-ND 4.0', 'CC BY-NC-ND 4.0', 'MIT'])
def test_permissive_only_rejects_nc_nd_and_software_terms(code):
    result = license_decision({**META, 'license_code': code}, [], permissive_only=True)
    assert not result['allowed'] and result['outcome'] == 'rejected'
    assert result['release_lane'] is None and not result['metadata_fetch_allowed']


def test_default_policy_is_unchanged():
    nc = license_decision({**META, 'license_code': 'CC BY-NC 4.0'}, [])
    assert nc['allowed'] and nc['policy_version'] == POLICY_VERSION
    # A default-policy decision is rechecked under the default policy, never the stricter one.
    assert recheck_license(nc)['allowed'] and recheck_license(nc)['policy_version'] == POLICY_VERSION
    # Unknown metadata terms still allow the bounded XML rights check under the strict flag.
    assert license_decision({**META, 'license_code': None}, [], permissive_only=True)['metadata_fetch_allowed']


# ---- selection-file acquisition (no network) ---------------------------------------------------------

def article(pmcid, code='CC BY 4.0', status=None, permissive_only=False):
    xml = f'<article><body><p>{pmcid}</p></body></article>'
    text = f'[s1] Case\nPatient {pmcid}.'
    lic = license_decision({**META, 'license_code': code}, [], permissive_only=permissive_only)
    return {'article_id': f'{pmcid}.1', 'pmcid': pmcid, 'title': f'Case report {pmcid}',
            'source_url': f'https://pmc.ncbi.nlm.nih.gov/articles/{pmcid}/', 'doi': None, 'license': lic,
            'status': status or ('eligible' if lic['allowed'] else 'license_rejected'),
            'segments': [{'segment_id': 's1', 'heading': 'Case', 'text': f'Patient {pmcid}.'}],
            'text': text, 'has_body_text': True, 'text_sha256': hashlib.sha256(text.encode()).hexdigest(),
            'xml_sha256': hashlib.sha256(xml.encode()).hexdigest()}, xml


class FakeClient:
    def __init__(self):
        self.stats = {'metadata_xml_bytes_received': 0}
        self.last_xml = None
        self.closed = False

    async def close(self):
        self.closed = True


def row(pmcid, lane, group, role):
    return {'pmcid': pmcid, 'lane': lane, 'group': group, 'role': role, 'title': f'Case report {pmcid}',
            'journal': 'J Test', 'year': 2024}


@pytest.fixture
def selection(tmp_path, monkeypatch):
    acquire = __import__('openpatients2.dense.acquire', fromlist=['x'])
    other = tmp_path / 'other'; other.mkdir()
    (other / 'selection.json').write_text(json.dumps([{'article_id': 'PMC20.1', 'pmcid': 'PMC20'}]))
    rows = [row('PMC10', 'renal', 'g1', 'primary'),       # NC: rejected under --permissive-only
            row('PMC20', 'renal', 'g2', 'primary'),       # already in another root
            row('PMC30', 'eye', 'g1', 'primary'),         # fetch error, no alternate in its lane
            row('PMC11', 'renal', 'g2', 'alternate'),     # fills PMC10 (other group, after none in g1)
            row('PMC12', 'renal', 'g2', 'alternate'),     # fills PMC20
            row('PMC40', 'skin', 'g1', 'alternate')]      # different lane: never used
    path = tmp_path / 'sel.json'; path.write_text(json.dumps(rows))
    calls = []
    licenses = {'PMC10': 'CC BY-NC 4.0'}

    async def fake_fetch(client, pmcid, permissive_only=False):
        calls.append((pmcid, permissive_only))
        if pmcid == 'PMC30':
            raise ValueError('XML URL does not match article/version')
        art, xml = article(pmcid, licenses.get(pmcid, 'CC BY 4.0'), permissive_only=permissive_only)
        client.last_xml = xml
        return art

    monkeypatch.setattr(acquire, 'fetch_article', fake_fetch)
    root = tmp_path / 'corpus'
    return acquire, root, path, other, calls


def test_selection_mode_falls_back_to_same_lane_alternates_and_dedupes_all_roots(selection):
    acquire, root, path, other, calls = selection
    client = FakeClient()
    summary = asyncio.run(acquire.acquire_selection(root, path, permissive_only=True, client=client,
                                                    other_roots=[other], selection_round=3))
    assert client.closed
    assert summary == {'acquired': ['PMC11.1', 'PMC12.1'], 'unfilled_slots': ['PMC30']}
    assert all(flag for _, flag in calls) and 'PMC20' not in [p for p, _ in calls]
    assert 'PMC40' not in [p for p, _ in calls]
    manifest = json.loads((root / 'selection.json').read_text())
    by_id = {r['pmcid']: r for r in manifest}
    assert set(by_id) == {'PMC11', 'PMC12'}
    assert by_id['PMC11']['selection_slot'] == 'PMC10' and by_id['PMC11']['replaces'] == 'PMC10'
    assert by_id['PMC12']['selection_slot'] == 'PMC20'
    for r in manifest:
        assert r['stratum'] == r['lane'] == 'renal' and r['selection_role'] == 'alternate'
        assert r['license_policy_version'] == PERMISSIVE_ONLY_POLICY_VERSION and r['permissive_only']
        assert r['selection_round'] == 3 and 'split' not in r
        assert r['selection_file_sha256'] == hashlib.sha256(path.read_bytes()).hexdigest()
        for kind in ('sources', 'raw-xml', 'source-text'):
            assert store.path(root, kind, r['article_id']).exists()
    decisions = {d['pmcid']: d['decision'] for d in json.loads((root / 'acquisition/decisions.json').read_text())}
    assert decisions == {'PMC10': 'license_rejected', 'PMC20': 'duplicate_existing_root', 'PMC30': 'fetch_error',
                         'PMC11': 'eligible', 'PMC12': 'eligible'}
    assert (root / 'acquisition/network.json').exists()
    # Resumable: a second run fetches nothing new and appends nothing.
    calls.clear()
    again = asyncio.run(acquire.acquire_selection(root, path, permissive_only=True, client=FakeClient(),
                                                  other_roots=[other]))
    assert calls == [] and again == {'acquired': [], 'unfilled_slots': ['PMC30']}
    assert len(json.loads((root / 'selection.json').read_text())) == 2


def test_selection_mode_respects_limit_and_validates_rows(selection, tmp_path):
    acquire, root, path, other, calls = selection
    summary = asyncio.run(acquire.acquire_selection(root, path, limit=1, client=FakeClient(), other_roots=[other]))
    assert summary['acquired'] == ['PMC10.1']   # default policy accepts CC BY-NC
    bad = tmp_path / 'bad.json'
    bad.write_text(json.dumps([{**row('PMC1', 'a', 'g', 'backup')}]))
    with pytest.raises(ValueError, match='role'):
        acquire.load_selection(bad)
    bad.write_text(json.dumps([{'pmcid': 'PMC1', 'lane': 'a'}]))
    with pytest.raises(ValueError, match='missing'):
        acquire.load_selection(bad)


def test_dedupe_reads_the_corpus_and_caches_are_per_root():
    acquire = __import__('openpatients2.dense.acquire', fromlist=['x'])
    used = acquire.existing_pmcids([CORPUS])
    assert {x['pmcid'] for x in json.loads((CORPUS / 'selection.json').read_text())} <= used
    assert {aid.split('.')[0] for aid in store.article_ids(CORPUS, 'sources')} <= used
    assert acquire.default_cache(CORPUS) == '/tmp/op2-dense-metadata.sqlite'


# ---- the one-root corpus ------------------------------------------------------------------------------

def test_selection_covers_every_article_once_with_round_and_split():
    rows = json.loads((CORPUS / 'selection.json').read_text())
    ids = [r['article_id'] for r in rows]
    assert len(ids) == len(set(ids)) and set(ids) == set(store.article_ids(CORPUS, 'sources'))
    assert all(r['selection_round'] in (1, 2) and r['split'] in ('train', 'validation', 'test') for r in rows)
    # Round 1 (the original 500) keeps its frozen split; round 2 is 60/20/20 within every selection lane.
    assert Counter(r['split'] for r in rows if r['selection_round'] == 1) == {'train': 300, 'validation': 100, 'test': 100}
    lanes = defaultdict(Counter)
    for r in rows:
        if r['selection_round'] == 2:
            lanes[r['lane']][r['split']] += 1
    for lane, counts in lanes.items():
        n = sum(counts.values())
        assert counts == {'train': 3 * n // 5, 'validation': n // 5, 'test': n // 5}, lane
    assert not [p for p in (CORPUS.parent).iterdir() if p.is_dir() and p.name != 'dense']


AIDS = ('PMC12134816.1', 'PMC12605513.1')   # completed corpus articles (figures; document supplement)


@pytest.fixture
def copied(tmp_path):
    root = tmp_path / 'copy'
    for aid in AIDS:
        if not store.path(CORPUS, 'source-text', aid).exists():
            pytest.skip('corpus caches unavailable')
        shutil.copytree(store.article_dir(CORPUS, aid), store.article_dir(root, aid))
    selection = {r['article_id']: r for r in json.loads((CORPUS / 'selection.json').read_text())}
    (root / 'selection.json').write_text(json.dumps([selection[a] for a in AIDS]))
    return root


def test_tools_give_the_same_verdicts_at_any_root(copied):
    """Receipts bind content, not paths: a copied article folder passes the same gates."""
    from openpatients2.dense.reference_gates import run_gates
    from openpatients2.dense.validate import validate
    from openpatients2.dense.visual_review import reviewed_visual_target
    from openpatients2.dense.supplement_review import reviewed_supplement_targets
    for aid in AIDS:
        here, there = run_gates(CORPUS, aid), run_gates(copied, aid)
        assert here['passed'] == there['passed'] and len(here['errors']) == len(there['errors'])
        source = json.loads(store.path(copied, 'sources', aid).read_text())
        assert validate(json.loads(store.path(copied, 'annotations', aid).read_text()), source) == []
        assert reviewed_visual_target(copied, aid) is not None
        assert len(reviewed_supplement_targets(copied, aid)) == len(reviewed_supplement_targets(CORPUS, aid))


def test_export_carries_selection_round_as_data(copied, tmp_path, monkeypatch):
    exporter = __import__('openpatients2.dense.export', fromlist=['x'])
    assert exporter.ROOT == CORPUS and exporter.OUT == exporter.REPO / 'data/dense_clinical_cases_v1'
    out = tmp_path / 'release'
    monkeypatch.setattr(exporter, 'ROOT', copied)
    monkeypatch.setattr(exporter, 'OUT', out)
    exporter.main()
    selection = {r['article_id']: r for r in json.loads((copied / 'selection.json').read_text())}
    manifest = [json.loads(x) for x in (out / 'legacy/manifest.jsonl').read_text().splitlines()]
    assert sorted(r['article_id'] for r in manifest) == list(AIDS)
    for r in manifest:
        assert r['selection_round'] == selection[r['article_id']]['selection_round']
        assert r['split'] == selection[r['article_id']]['split'] and 'reference_set' not in r
    unified = [json.loads(x) for split in ('train', 'validation', 'test')
               for x in (out / f'data/{split}.jsonl').read_text().splitlines()]
    assert unified and all(r['provenance']['selection_round'] == 1 and 'reference_root' not in r['provenance']
                           for r in unified)
    assert json.loads((out / 'statistics.json').read_text())['target_articles'] == len(AIDS)


# ---- permissive-only/2: article CC BY + data-only CC0 waiver (BMC/Springer) --------------------------

BY = 'http://creativecommons.org/licenses/by/4.0/'
CC0 = 'http://creativecommons.org/publicdomain/zero/1.0/'
WAIVER = f'The Creative Commons Public Domain Dedication waiver ({CC0}) applies to the data made available in this article, unless otherwise stated.'
BMC_2016 = (f'https://creativecommons.org/licenses/by/4.0/ Open AccessThis article is distributed under the terms of the '
            f'Creative Commons Attribution 4.0 International License ({BY}), which permits unrestricted use, distribution, '
            f'and reproduction in any medium, provided you give appropriate credit to the original author(s) and the source, '
            f'provide a link to the Creative Commons license, and indicate if changes were made. {WAIVER}')
BMC_2020 = (f'https://creativecommons.org/licenses/by/4.0/Open AccessThis article is licensed under a Creative Commons '
            f'Attribution 4.0 International License, which permits use, sharing, adaptation, distribution and reproduction '
            f'in any medium or format, as long as you give appropriate credit to the original author(s) and the source, '
            f"provide a link to the Creative Commons licence, and indicate if changes were made. The images or other third "
            f"party material in this article are included in the article's Creative Commons licence, unless indicated "
            f"otherwise in a credit line to the material. To view a copy of this licence, visit {BY}. "
            + WAIVER.replace('stated.', 'stated in a credit line to the data.'))


def bmc(text, urls=(BY, CC0)):
    return [{'type': 'copyright-statement', 'text': '© The Author(s) 2016', 'url': None, 'urls': []},
            {'type': 'license', 'text': text, 'url': None, 'urls': list(urls)}]


@pytest.mark.parametrize('text,default_reason', [(BMC_2016, 'conflicting_license_statements'),
                                                 (BMC_2020, 'article_license_scope_requires_review')])
@pytest.mark.parametrize('code', [None, 'CC BY'])
def test_article_cc_by_with_data_only_cc0_waiver_is_cc_by_under_permissive_only(text, default_reason, code):
    statements = bmc(text)
    result = license_decision({**META, 'license_code': code}, statements, permissive_only=True)
    assert result['allowed'] and result['code'] == 'CC BY' and result['version'] == '4.0'
    assert result['reason'] == 'article_cc_by_with_cc0_data_waiver'
    assert result['policy_version'] == PERMISSIVE_ONLY_POLICY_VERSION == 'article-adaptations-permissive-only/2'
    assert result['statements'] == statements and result['data_waivers'][0]['scope'] == 'data_made_available_in_article'
    assert 'attribution' in result['obligations'] and 'cc0_waiver_covers_data_only_not_article_text' in result['obligations']
    again = recheck_license(result)
    assert again['allowed'] and again['reason'] == result['reason'] and again['policy_version'] == result['policy_version']
    # The default policy is not loosened.
    default = license_decision({**META, 'license_code': code}, statements)
    assert not default['allowed'] and default['reason'] == default_reason and default['policy_version'] == POLICY_VERSION


def test_older_permissive_only_decisions_are_rechecked_under_permissive_only():
    old = {**license_decision({**META, 'license_code': 'CC BY-NC 4.0'}, []), 'policy_version': 'article-adaptations-permissive-only/1'}
    assert not recheck_license(old)['allowed']


@pytest.mark.parametrize('text,urls', [
    # A real article-level conflict stays held.
    (f'This article is distributed under the Creative Commons Attribution 4.0 License ({BY}). '
     'This article is licensed under CC BY-NC 4.0 (http://creativecommons.org/licenses/by-nc/4.0/).',
     (BY, 'http://creativecommons.org/licenses/by-nc/4.0/')),
    # NC article + data waiver: the waiver rule only rescues CC BY.
    (BMC_2016.replace('Attribution 4.0', 'Attribution-NonCommercial 4.0').replace(BY, 'http://creativecommons.org/licenses/by-nc/4.0/')
     .replace('https://creativecommons.org/licenses/by/4.0/', 'https://creativecommons.org/licenses/by-nc/4.0/'),
     ('http://creativecommons.org/licenses/by-nc/4.0/', CC0)),
    # CC0 scoped to the article (or unscoped) is not a data-only waiver.
    (BMC_2016.replace('applies to the data made available in this article', 'applies to this article'), (BY, CC0)),
    (BMC_2016.replace(WAIVER, f'This article is also dedicated under CC0 ({CC0}).'), (BY, CC0)),
    # A second CC0 mention outside the waiver sentence keeps it held.
    (BMC_2016 + f' Figures are available under CC0 ({CC0}).', (BY, CC0)),
    # Conflicting CC BY versions are still a version conflict.
    (BMC_2016.replace('4.0 International License (' + BY, '3.0 License (http://creativecommons.org/licenses/by/3.0/'),
     (BY, 'http://creativecommons.org/licenses/by/3.0/', CC0)),
])
def test_conflicts_other_than_the_data_waiver_stay_held(text, urls):
    result = license_decision({**META, 'license_code': None}, bmc(text, urls), permissive_only=True)
    assert not result['allowed'] and result['reason'] != 'article_cc_by_with_cc0_data_waiver'


def test_metadata_nc_with_data_waiver_is_not_rescued():
    result = license_decision({**META, 'license_code': 'CC BY-NC 4.0'}, bmc(BMC_2016), permissive_only=True)
    assert not result['allowed']


def test_recheck_held_refills_unfilled_slots_only(selection):
    acquire, root, path, other, calls = selection
    rows = json.loads(path.read_text())
    rows.append(row('PMC31', 'eye', 'g1', 'alternate'))   # held for PMC30 under /1
    rows.append(row('PMC13', 'renal', 'g2', 'alternate')) # held, but its lane's slots fill without it
    path.write_text(json.dumps(rows))
    (root / 'acquisition').mkdir(parents=True)
    old = 'article-adaptations-permissive-only/1'
    (root / 'acquisition/decisions.json').write_text(json.dumps([
        {**row('PMC31', 'eye', 'g1', 'alternate'), 'selection_slot': 'PMC30', 'decision': 'license_review',
         'license_reason': 'conflicting_license_statements', 'license_policy_version': old},
        {**row('PMC13', 'renal', 'g2', 'alternate'), 'selection_slot': 'PMC10', 'decision': 'license_review',
         'license_reason': 'conflicting_license_statements', 'license_policy_version': old},
        {**row('PMC33', 'eye', 'g1', 'alternate'), 'selection_slot': 'PMC30', 'decision': 'version_selection_required',
         'versions': [1, 2], 'license_reason': None, 'license_policy_version': None}]))
    run = lambda **kw: asyncio.run(acquire.acquire_selection(root, path, permissive_only=True, client=FakeClient(),
                                                             other_roots=[other], **kw))
    first = run()
    assert 'PMC31' not in [p for p, _ in calls] and first['unfilled_slots'] == ['PMC30']
    filled = json.loads((root / 'selection.json').read_text())
    calls.clear()
    second = run(recheck_held=True)
    assert second['recheckable_held'] == 2 and second['acquired'] == ['PMC31.1'] and second['unfilled_slots'] == []
    assert [p for p, _ in calls] == ['PMC31']   # PMC30's fetch error and PMC33's version hold are not re-run
    manifest = json.loads((root / 'selection.json').read_text())
    assert manifest[:len(filled)] == filled and manifest[-1]['selection_slot'] == 'PMC30'
    log = json.loads((root / 'acquisition/decisions.json').read_text())
    assert log[-1]['pmcid'] == 'PMC31' and log[-1]['decision'] == 'eligible' and log[-1]['recheck'] is True
    assert len([e for e in log if e['pmcid'] == 'PMC31']) == 2   # the earlier hold is kept as history
    assert len([e for e in log if e['pmcid'] == 'PMC13']) == 1   # its slot is filled: not refetched
    # Idempotent: holds re-decided under the current policy are not rechecked again.
    calls.clear()
    assert run(recheck_held=True)['recheckable_held'] == 1 and calls == []
