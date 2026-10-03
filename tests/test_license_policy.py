import pytest

from openpatients2.articles import parse_article
from openpatients2.license_policy import license_decision, license_identity, normalize_license, recheck_license
from test_articles import XML


META = {'pmcid':'PMC1', 'version':1, 'is_retracted':False, 'is_pmc_openaccess':True, 'license_code':None}


@pytest.mark.parametrize('raw,code,version', [
    ('CC BY-NC-SA 4.0','CC BY-NC-SA','4.0'),
    ('CC-BY-3.0','CC BY','3.0'),
    ('CCBY-NC-ND','CC BY-NC-ND',None),
    ('Creative Commons Attribution-NonCommercial-ShareAlike 4.0 International','CC BY-NC-SA','4.0'),
    ('http://creativecommons.org/licenses/by-nc-sa/3.0/us/','CC BY-NC-SA','3.0'),
    ('https://creativecommons.org/licenses/by/4.0/deed.en','CC BY','4.0'),
    ('https://creativecommons.org/publicdomain/zero/1.0/','CC0','1.0'),
    ('https://creativecommons.org/publicdomain/mark/1.0/','PDM','1.0'),
    ('MIT','MIT',None), ('MIT-0','MIT-0',None),
    ('Apache License, Version 2.0','Apache-2.0','2.0'),
    ('https://www.apache.org/licenses/LICENSE-2.0','Apache-2.0','2.0'),
    ('https://opensource.org/licenses/MIT','MIT',None),
    ('https://spdx.org/licenses/BSD-3-Clause.html','BSD-3-Clause',None),
    ('BSD-2-Clause','BSD-2-Clause',None), ('ISC','ISC',None),
    ('0BSD','0BSD',None), ('https://unlicense.org/','Unlicense',None),
])
def test_exact_identities_preserve_versions(raw, code, version):
    identity = license_identity(raw)
    assert identity['code'] == normalize_license(raw) == code
    assert identity['version'] == version
    if raw.startswith('http'): assert identity['url'] == raw
    if raw.endswith('/us/'): assert identity['jurisdiction'] == 'us'


@pytest.mark.parametrize('code', ['CC0', 'CC BY 2.0', 'CC BY-NC 3.0', 'CC BY-NC-SA 4.0',
                                 'MIT', 'MIT-0', 'Apache-2.0', 'BSD-2-Clause', 'BSD-3-Clause', 'ISC', '0BSD', 'Unlicense', 'PDM-1.0'])
def test_named_metadata_grants_allowed(code):
    result = license_decision({**META, 'license_code':code}, [])
    assert result['allowed'] and result['outcome'] == 'accepted'
    assert recheck_license(result)['allowed']


@pytest.mark.parametrize('code', ['CC BY-ND', 'CC BY-NC-ND'])
def test_nd_rejected_before_download(code):
    result = license_decision({**META, 'license_code':code}, [])
    assert result['outcome'] == 'rejected' and not result['metadata_fetch_allowed']
    assert result['release_lane'] is None


def test_sharealike_is_not_automatically_relicensed():
    assert license_decision({**META, 'license_code':'CC BY-SA 4.0'}, [])['release_lane'] == 'sharealike_source_terms'
    old = license_decision({**META, 'license_code':'CC BY-NC-SA 1.0'}, [])
    assert old['allowed'] and old['release_lane'] == 'legacy_sharealike_source_terms'
    assert 'sharealike_under_source_version_rules' in old['obligations']
    apache = license_decision({**META, 'license_code':'Apache-2.0'}, [])
    assert 'retain_applicable_NOTICE_and_attributions' in apache['obligations']
    assert 'identify_changes' in apache['obligations']


@pytest.mark.parametrize('raw', [None, 'other', 'NO-CC'])
@pytest.mark.parametrize('license_text', ['This article is licensed under MIT.',
    'This article is distributed under the Apache License, Version 2.0.',
    'Creative Commons Attribution-NonCommercial-ShareAlike 4.0 International License'])
def test_unclassified_metadata_resolved_by_article_permissions(raw, license_text):
    result = license_decision({**META, 'license_code':raw}, [{'type':'license', 'text':license_text}])
    assert result['allowed'] and result['basis'] == 'article_permissions'
    assert result['license_evidence']
    assert recheck_license(result)['allowed']


@pytest.mark.parametrize('raw', ['GPL-3.0', 'Apache', 'BSD', 'MIT-CMU', 'TDM',
                               'MIT AND GPL-3.0-only', 'MIT OR Apache-2.0', 'Public domain', None])
def test_unknowns_and_expressions_retained_for_review(raw):
    result = license_decision({**META, 'license_code':raw}, [])
    assert not result['allowed'] and result['outcome'] == 'review'
    assert result['metadata_fetch_allowed'] and result['release_lane'] is None


@pytest.mark.parametrize('statement', [
    {'type':'license', 'text':'The software is distributed under the MIT license.'},
    {'type':'license', 'text':'The dataset is licensed under Apache-2.0.'},
    {'type':'license', 'text':'This article is CC BY 4.0; its code is MIT.'},
    {'type':'license', 'text':'This article is licensed under MIT AND GPL-3.0-only.'},
    {'type':'license', 'text':'This article is licensed under MIT and GPL-3.0-only.'},
    {'type':'license', 'text':'This article is not licensed under MIT.'},
    {'type':'license', 'text':'The authors work at MIT.'},
    {'type':'license', 'text':'It is not true that this article is in the public domain worldwide.'},
    {'type':'copyright-statement', 'text':'MIT code used by the authors.'},
])
def test_asset_grants_compounds_and_negation_are_not_article_permission(statement):
    result = license_decision(META, [statement])
    assert not result['allowed'] and result['outcome'] == 'review'


def test_public_domain_requires_explicit_scope():
    global_terms = {'type':'copyright-statement', 'text':'This article is in the public domain worldwide.'}
    assert license_decision({**META, 'license_code':'Public domain'}, [global_terms])['allowed']
    us_only = {'type':'copyright-statement', 'text':'This is a US Government work in the public domain in the USA.'}
    assert not license_decision(META, [us_only])['allowed']


def test_known_metadata_conflicts_cannot_be_overridden():
    result = license_decision({**META, 'license_code':'CC BY 4.0'}, [{'type':'license', 'text':'CC BY-NC-SA 4.0'}])
    assert not result['allowed'] and result['reason'] == 'conflicting_license_statements'
    unknown_named = license_decision({**META, 'license_code':'GPL-3.0'}, [{'type':'license', 'text':'CC BY 4.0'}])
    assert not unknown_named['allowed']


def test_conflicting_and_unknown_versions_do_not_silently_disappear():
    result = license_decision({**META, 'license_code':'CC BY 3.0'}, [{'type':'license','text':'CC BY 4.0'}])
    assert result['versions'] == ['3.0','4.0'] and not result['allowed']
    assert license_decision({**META, 'license_code':'CC BY 9.0'}, [])['outcome'] == 'review'
    assert 'resolve_version_before_release' in license_decision({**META, 'license_code':'CC BY'}, [])['obligations']


@pytest.mark.parametrize('url', ['https://evil.example/creativecommons.org/licenses/by/4.0/',
    'https://creativecommons.org.evil.example/licenses/by/4.0/', 'https://evil.example/licenses/MIT'])
def test_lookalike_license_urls_do_not_match(url):
    assert normalize_license(url) is None
    assert not license_decision(META, [{'type':'license','url':url,'text':url}])['allowed']


def test_nested_link_with_generic_label_is_preserved_and_recognized():
    start = XML.index('<permissions>'); end = XML.index('</permissions>')+len('</permissions>')
    xml = XML[:start] + '<permissions><license><license-p>This article is distributed under <ext-link xlink:href="https://spdx.org/licenses/MIT.html">these terms</ext-link>.</license-p></license></permissions>' + XML[end:]
    article = parse_article(xml, META)
    assert article['license']['allowed'] and article['license']['code'] == 'MIT'
    assert article['license']['statements'][0]['urls'] == ['https://spdx.org/licenses/MIT.html']
    # A link in clinical prose, a reference or figure can never supply the grant.
    unlicensed = xml.replace('<permissions>', '<unrelated>').replace('</permissions>','</unrelated>')
    assert not parse_article(unlicensed, META)['license']['allowed']


@pytest.mark.parametrize('field,value', [('is_retracted',True),('is_retracted',None),('is_pmc_openaccess',False),('is_pmc_openaccess',None)])
def test_rights_do_not_bypass_source_eligibility(field, value):
    result = license_decision({**META, 'license_code':'MIT', field:value}, [])
    assert not result['allowed'] and not result['metadata_fetch_allowed']
