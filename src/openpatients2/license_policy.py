"""Article reuse policy: named grants, preserved terms, and a visible review lane.

Discovery is deliberately broader than automatic acceptance. This module never
fetches license URLs, ranks arbitrary licenses, or grants rights over figure
exceptions. Only article metadata/permissions belong in its input statements.
"""
from __future__ import annotations

import re
from urllib.parse import urlparse

from .pmc_media import metadata_flag

POLICY_VERSION = 'article-adaptations-noncommercial/3'
CC_LICENSES = {'CC0', 'CC BY', 'CC BY-NC', 'CC BY-NC-SA', 'CC BY-SA'}
PERMISSIVE_LICENSES = {'MIT', 'MIT-0', 'Apache-2.0', 'BSD-2-Clause',
                       'BSD-3-Clause', 'ISC', '0BSD', 'Unlicense'}
ACCEPTED_LICENSES = CC_LICENSES | PERMISSIVE_LICENSES | {'PDM', 'Public domain worldwide'}
ND_LICENSES = {'CC BY-ND', 'CC BY-NC-ND'}
# These are absence-of-classification markers, not grants in their own right.
UNKNOWN_CODES = {'', 'unknown', 'other', 'custom', 'non-cc', 'non cc', 'no-cc', 'none', 'n/a',
                 'public domain', 'public-domain', 'pd'}
ALIASES = {code.casefold(): code for code in PERMISSIVE_LICENSES}
ALIASES.update({'mit license':'MIT', 'mit no attribution':'MIT-0',
                'apache license, version 2.0':'Apache-2.0', 'apache license 2.0':'Apache-2.0',
                'apache 2.0':'Apache-2.0', 'bsd 2-clause':'BSD-2-Clause',
                'bsd 3-clause':'BSD-3-Clause', 'isc license':'ISC', 'the unlicense':'Unlicense'})
CC_SHORT = r'CC[\s-]*BY(?:[\s-]*NC)?(?:[\s-]*(?:SA|ND))?'
CC_LONG = r'Creative Commons Attribution(?:[\s-]*Non[\s-]*Commercial)?(?:[\s-]*(?:Share[\s-]*Alike|No[\s-]*Derivatives))?'
CC_VERSION = r'(?:[\s-]+(?:License[\s,]*)?(?:v(?:ersion)?\s*)?(\d+\.\d+))?'
URLS = re.compile(r'(?:https?://|www\.)[^\s<>"\']+|(?<![\w./])creativecommons\.org/[^\s<>"\']+', re.I)
NONARTICLE = re.compile(r'\b(?:software|source code|code|datasets?|data sets?|figures?|images?|supplements?|supplementary material)\b', re.I)
ARTICLE = re.compile(r'\b(?:this|the|entire)\s+(?:article|paper|work|publication)\b', re.I)
WORLDWIDE_PD = re.compile(r'\b(?:this|the|entire)\s+(?:article|paper|work|publication)\s+is\s+(?:dedicated to|in)\s+the public domain\s+(?:worldwide|throughout the world)\b', re.I)
RESTRICTED = re.compile(r'\bno[\s-]*derivatives\b|\bcannot be changed\b|\ball rights reserved\b|\bnot (?:licensed|distributed|available) under\b|\b(?:additional|further) restrictions\b', re.I)


def license_identity(value: str | None) -> dict | None:
    """Recognize a complete identifier/name or official URL, never a substring.

    Keep versions and ported jurisdictions; a family name is not a version claim.
    Unsupported versions are identified here but sent for review by the policy.
    """
    if not isinstance(value, str) or not value.strip():
        return None
    raw = value.strip()
    token = re.sub(r'\s+', ' ', raw.replace('_', '-')).strip()
    code = version = jurisdiction = url = None
    if re.match(r'https?://|www\.|creativecommons\.org/', token, re.I):
        parsed = urlparse(token if '://' in token else 'https://'+token)
        if parsed.username or parsed.password:
            return None
        host, path = (parsed.hostname or '').lower().removeprefix('www.'), parsed.path.rstrip('/')
        url = raw
        if host == 'creativecommons.org':
            match = re.fullmatch(r'/licenses/(by(?:-nc)?(?:-sa|-nd)?)/(\d+\.\d+)(?:/([a-z]{2}|igo|legalcode|deed))?(?:/(?:legalcode|deed)(?:\.[a-z_-]+)?)?', path, re.I)
            if match:
                code, version = 'CC '+match[1].upper(), match[2]
                suffix=(match[3] or '').lower()
                jurisdiction = suffix if suffix and suffix not in {'legalcode','deed'} else None
            else:
                match = re.fullmatch(r'/publicdomain/(zero|mark)/(\d+\.\d+)(?:/(?:legalcode|deed)(?:\.[a-z_-]+)?)?', path, re.I)
                if match:
                    code, version = ('CC0' if match[1].lower() == 'zero' else 'PDM'), match[2]
        elif host == 'spdx.org' and path.startswith('/licenses/'):
            result = license_identity(path.removeprefix('/licenses/').removesuffix('.html'))
            return {**result, 'url':raw, 'as_documented':raw} if result else None
        elif host == 'opensource.org' and path.startswith('/licenses/'):
            result = license_identity(path.removeprefix('/licenses/').removesuffix('.php'))
            return {**result, 'url':raw, 'as_documented':raw} if result else None
        elif host == 'apache.org' and path in {'/licenses/LICENSE-2.0','/licenses/LICENSE-2.0.txt','/licenses/LICENSE-2.0.html'}:
            code, version = 'Apache-2.0', '2.0'
        elif host == 'unlicense.org' and path in {'','/UNLICENSE'}:
            code = 'Unlicense'
    else:
        match = re.fullmatch(rf'({CC_SHORT}|{CC_LONG}){CC_VERSION}(?:\s+(?:International|Unported|License))*', token, re.I)
        if match:
            compact = re.sub(r'[\s-]', '', match[1]).upper()
            code = 'CC BY'
            if 'NONCOMMERCIAL' in compact or compact.startswith('CCBYNC'): code += '-NC'
            if 'SHAREALIKE' in compact or compact.endswith('SA'): code += '-SA'
            if 'NODERIVATIVES' in compact or compact.endswith('ND'): code += '-ND'
            version = match[2]
        elif match := re.fullmatch(r'(CC[\s-]*0|PDM|Public Domain Mark)(?:[\s-]+(\d+\.\d+))?(?:\s+(?:Universal|License))*', token, re.I):
            code = 'CC0' if match[1].upper().startswith('CC') else 'PDM'
            version = match[2]
        else:
            code = ALIASES.get(token.casefold())
            if code == 'Apache-2.0': version = '2.0'
            if token.casefold() == 'public domain worldwide': code = 'Public domain worldwide'
    if not code:
        return None
    return {'code':code, 'version':version, 'jurisdiction':jurisdiction, 'url':url, 'as_documented':raw}


def normalize_license(value: str | None) -> str | None:
    identity = license_identity(value)
    return identity['code'] if identity else None


def _statement_identities(statement):
    text = str(statement.get('text') or '')
    records = []
    urls = [statement.get('url'), *statement.get('urls', [])]
    urls += [m[0].rstrip('.,);]') for m in URLS.finditer(text)]
    for url in urls:
        if identity := license_identity(url):
            records.append(identity)
    # Mask URLs so URL path components cannot masquerade as independent names.
    prose = URLS.sub(' ', text)
    alternatives = '|'.join(re.escape(s) for s in sorted(ALIASES, key=len, reverse=True))
    pattern = rf'(?<![\w-])(?:{CC_SHORT}|{CC_LONG}){CC_VERSION}(?![\w-])|(?<![\w-])(?:CC[\s-]*0|PDM|Public Domain Mark)(?:[\s-]+\d+\.\d+)?(?![\w-])|(?<![\w-])(?:{alternatives})(?![\w-])'
    for match in re.finditer(pattern, prose, re.I):
        if identity := license_identity(match[0]):
            records.append(identity)
    return records


def _lane(code, version):
    if code == 'CC BY-SA': return 'sharealike_source_terms'
    if code == 'CC BY-NC-SA' and version == '1.0': return 'legacy_sharealike_source_terms'
    if code == 'PDM': return 'public_domain_mark'
    if code == 'Public domain worldwide': return 'public_domain_statement'
    if code in PERMISSIVE_LICENSES: return 'permissive_source_terms'
    return 'noncommercial_compatible'


def license_decision(metadata: dict, statements: list[dict]) -> dict:
    """Allow known adaptation grants; leave unknown/conflicting terms for review.

    allowed is the extraction gate. metadata_fetch_allowed is ONLY permission to
    retrieve bounded official OA XML to inspect its rights, not to export it.
    """
    raw = metadata.get('license_code')
    meta = license_identity(raw)
    records, explicit, ambiguous_scope, restricted, compound = [], set(), False, False, False
    for index, statement in enumerate(statements):
        if statement.get('type') not in {'license','copyright-statement'}:
            continue
        text = str(statement.get('text') or '')
        # Copyright lines can assert public-domain status; other embedded names
        # there are not enough to establish a license grant.
        found = _statement_identities(statement) if statement.get('type') == 'license' else []
        if match := WORLDWIDE_PD.search(text):
            found.append({'code':'Public domain worldwide', 'version':None, 'jurisdiction':'worldwide',
                          'url':None, 'as_documented':match[0]})
        # A license paragraph about associated code/assets is not an article grant.
        # Mixed scopes are reviewed instead of assuming which name applies where.
        scope_uncertain = bool(found and NONARTICLE.search(text) and (
            not ARTICLE.search(text) or len({r['code'] for r in found}) > 1))
        named_permissive = any(r['code'] in PERMISSIVE_LICENSES and not r['url'] for r in found)
        if named_permissive and not any(r['url'] for r in found):
            # MIT is also an institution/acronym. Mentioning the name is not a
            # grant; accept exact labels or actual licensing language here.
            scope_uncertain |= not (normalize_license(text.rstrip('.')) in PERMISSIVE_LICENSES or
                re.search(r'\b(?:licensed|distributed|released|published)\b.{0,60}\bunder\b|\bunder the terms of\b', text, re.I))
        ambiguous_scope |= scope_uncertain
        if statement.get('type') == 'license' or found:
            restricted |= bool(RESTRICTED.search(text))
        if any(r['code'] == 'Public domain worldwide' for r in found):
            restricted |= bool(re.search(r'\b(?:not|never|no longer)\b', text, re.I))
        compound |= bool(re.search(rf'\bdual[ -]licens|\blicensed under both\b|\b(?:{CC_SHORT}|MIT(?:-0)?|Apache-2\.0|BSD-[23]-Clause|ISC|0BSD|Unlicense)\s+(?:AND|OR|WITH)\s+\w', text, re.I))
        for identity in found:
            records.append({**identity, 'statement_index':index, 'scope_uncertain':scope_uncertain})
            if not scope_uncertain:
                explicit.add(identity['code'])
    code = meta['code'] if meta else (next(iter(explicit)) if len(explicit) == 1 else None)
    identities = ([meta] if meta else []) + [r for r in records if not r['scope_uncertain']]
    versions = sorted({r['version'] for r in identities if r['code'] == code and r['version']})
    jurisdictions = sorted({r['jurisdiction'] for r in identities if r['code'] == code and r['jurisdiction']})
    version = versions[0] if len(versions) == 1 else None
    reason, outcome = 'explicit_allowlist', 'accepted'
    eligible_metadata = (metadata_flag(metadata.get('is_retracted')) is False
                         and metadata_flag(metadata.get('is_pmc_openaccess')) is True)
    if metadata_flag(metadata.get('is_retracted')) is not False:
        reason, outcome = 'retracted_or_retraction_status_unknown', 'rejected'
    elif metadata_flag(metadata.get('is_pmc_openaccess')) is not True:
        reason, outcome = 'not_verified_open_access', 'rejected'
    elif explicit and (len(explicit) > 1 or meta and explicit != {meta['code']}):
        reason, outcome = 'conflicting_license_statements', 'review'
    elif len(versions) > 1 or len(jurisdictions) > 1:
        reason, outcome = 'conflicting_license_versions_or_jurisdictions', 'review'
    elif ambiguous_scope:
        reason, outcome = 'article_license_scope_requires_review', 'review'
    elif compound:
        reason, outcome = 'compound_license_terms_require_review', 'review'
    elif code in ND_LICENSES:
        reason, outcome = 'license_disallows_adaptations', 'rejected'
    elif restricted:
        reason, outcome = 'restrictive_license_prose_requires_review', 'review'
    elif not meta and str(raw or '').strip().casefold() not in UNKNOWN_CODES:
        # TDM, GPL, unknown SPDX expressions and bespoke names are not blanks.
        reason, outcome = 'unrecognized_metadata_terms_require_review', 'review'
    elif code not in ACCEPTED_LICENSES:
        reason, outcome = 'license_unknown_requires_review', 'review'
    elif version and ((code in CC_LICENSES - {'CC0'} and version not in {'1.0','2.0','2.5','3.0','4.0'})
                      or (code in {'CC0','PDM'} and version != '1.0')):
        reason, outcome = 'license_version_requires_review', 'review'
    allowed = outcome == 'accepted'
    obligations = ['preserve_source_citation_and_rights_evidence', 'check_asset_exceptions']
    if code in CC_LICENSES - {'CC0'}: obligations += ['attribution', 'identify_changes', 'retain_license_link']
    if code and '-NC' in code: obligations.append('noncommercial')
    if code and '-SA' in code: obligations.append('sharealike_under_source_version_rules')
    if code in PERMISSIVE_LICENSES: obligations.append('retain_full_license_and_copyright_notices')
    if code == 'Apache-2.0': obligations += ['identify_changes', 'retain_applicable_NOTICE_and_attributions']
    if code == 'BSD-3-Clause': obligations.append('no_endorsement')
    if code in CC_LICENSES | {'PDM'} and not version: obligations.append('resolve_version_before_release')
    return {'policy_version':POLICY_VERSION, 'allowed':allowed, 'outcome':outcome, 'reason':reason,
        'metadata_fetch_allowed':eligible_metadata and (not meta or meta['code'] not in ND_LICENSES),
        'code':code, 'version':version, 'versions':versions, 'jurisdictions':jurisdictions,
        'raw_code':raw, 'metadata_identity':meta, 'statements':statements,
        'statement_codes':sorted(explicit), 'license_evidence':records,
        'restrictive_prose':restricted, 'scope_uncertain':ambiguous_scope,
        'basis':'metadata_and_article' if meta and explicit else 'metadata' if meta else 'article_permissions',
        'release_lane':_lane(code, version) if allowed else None, 'obligations':obligations,
        'notice':'Keep source terms, versions, jurisdiction, authors and changes; acceptance is not blanket relicensing. Asset exceptions are separate.'}


def recheck_license(decision: dict) -> dict:
    """Offline recheck; never rehabilitate a rejected/reviewed acquisition."""
    if not decision.get('allowed'):
        return decision
    # raw_code=None is meaningful for grants recovered from JATS permissions.
    raw = decision.get('raw_code') if 'raw_code' in decision else decision.get('code')
    return license_decision({'license_code':raw, 'is_retracted':False, 'is_pmc_openaccess':True},
                            decision.get('statements', []))
