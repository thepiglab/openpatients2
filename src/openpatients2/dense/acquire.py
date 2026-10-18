"""Bounded public-source acquisition for the dense reference annotation project.

This selects candidate text, never fabricates annotations or review receipts.
Run from the repository root using the existing project environment.

    op2 dense acquire acquire --selection-file SEL.json --round N \
        [--root benchmarks/dense] [--permissive-only] [--limit N] [--cache PATH] [--recheck-held]

(The round-1 discover/approved-titles stages that built the first 500 were retired once that round was
complete; they are in git history before the corpus was unified, and their records stay in acquisition/.)

Selection mode reads a JSON list of rows {pmcid, lane, group, role: primary|alternate, title, journal,
year} (optional: split, reason, patient_count_hint). Primaries are acquired in file order; a primary that
fails the license policy, acquisition, the title screen, or is already in the corpus (or any other root
given) is replaced by the next unused alternate of the same lane (same group first).
Rows are appended to <root>/selection.json with lane/group/slot metadata, the license policy version and
`selection_round` (--round); assign their splits once the round is complete (see benchmarks/dense/SELECTION.md);
every attempt is logged in <root>/acquisition/decisions.json. Rate limits and the byte budget are bounded; the metadata cache is per root (/tmp/op2-<root name>-metadata.sqlite) unless --cache.
--recheck-held re-attempts license_review holds logged under an older policy version, so unfilled
slots can be filled from alternates the current policy accepts; filled slots are not touched.
"""
import argparse
import asyncio
import hashlib
import json
from pathlib import Path
import re

from openpatients2.corpus import fetch_article
from openpatients2.literature_client import LiteratureClient, LiteratureConfig
from openpatients2 import dense_store as store
from openpatients2.license_policy import PERMISSIVE_ONLY_POLICY_VERSION, POLICY_VERSION

from openpatients2.dense import CORPUS

SELECTION_ROLES = ('primary', 'alternate')
EXCLUDED_TITLE = re.compile(r'^(retract|correction|corrigendum|erratum)|^\[', re.I)
def save(path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + '.tmp')
    temp.write_text(json.dumps(obj, ensure_ascii=False, indent=2) + '\n')
    temp.replace(path)


def default_cache(root):
    """Metadata cache per root."""
    return f'/tmp/op2-{Path(root).name}-metadata.sqlite'


class BudgetExceeded(RuntimeError):
    """The acquisition byte budget is spent: stop the run (never fall back to another article)."""


class BoundedClient(LiteratureClient):
    def __init__(self, cache=None):
        super().__init__(LiteratureConfig(
            cache=cache or default_cache(CORPUS), concurrency=1,
            cloud_requests_per_second=3, retries=1,
            max_xml_bytes=2_000_000, max_metadata_bytes=1_000_000,
            max_versions=8, max_listing_pages=2))
        self.last_xml = None

    async def _text(self, url, kind, params=None):
        # Serial acquisition leaves room for both bounded attempts of any request.
        if self.stats['metadata_xml_bytes_received'] > 145_000_000:
            raise BudgetExceeded('Acquisition byte budget reached; resume only after review')
        value = await super()._text(url, kind, params)
        if kind == 'xml':
            self.last_xml = value[0]
        return value


def write_article(root, article, xml):
    """Write source.json, raw.xml and source.txt for one eligible article; returns its article id."""
    article_id = article['article_id']
    if not xml or hashlib.sha256(xml.encode()).hexdigest() != article['xml_sha256']:
        raise ValueError('Raw XML hash disagrees with parsed source')
    save(store.path(root, 'sources', article_id), article)
    store.path(root, 'raw-xml', article_id).write_text(xml)
    text = article['title']+'\n\n'+'\n\n'.join(
        f"[{s['segment_id']}] {s.get('heading', '')}\n{s['text']}"
        for s in article['segments'])
    store.path(root, 'source-text', article_id).write_text(text)
    return article_id


def load_selection(path):
    """Validate a selection file: a list of rows with pmcid, lane, group, role, title, journal, year."""
    rows = json.loads(Path(path).read_text())
    if not isinstance(rows, list) or not rows:
        raise ValueError('Selection file must be a non-empty JSON list of rows')
    seen = set()
    for index, row in enumerate(rows):
        missing = [k for k in ('pmcid', 'lane', 'group', 'role', 'title', 'journal', 'year') if k not in row]
        if missing:
            raise ValueError(f'Selection row {index}: missing {missing}')
        if not re.fullmatch(r'PMC\d+', str(row['pmcid'])):
            raise ValueError(f'Selection row {index}: bad pmcid {row["pmcid"]!r}')
        if row['role'] not in SELECTION_ROLES:
            raise ValueError(f'Selection row {index}: role must be primary|alternate, not {row["role"]!r}')
        if row['pmcid'] in seen:
            raise ValueError(f'Selection row {index}: duplicate pmcid {row["pmcid"]}')
        seen.add(row['pmcid'])
    return rows


def existing_pmcids(roots):
    """PMCIDs already selected or acquired in any of these roots (selection.json and article folders)."""
    used = set()
    for root in roots:
        root = Path(root)
        if (root / 'selection.json').exists():
            used.update(x['pmcid'] for x in json.loads((root / 'selection.json').read_text()))
        used.update(aid.split('.')[0] for aid in store.article_ids(root, 'sources'))
    return used


HELD_DECISIONS = ('license_review',)


def stale_held(log, policy_version):
    """PMCIDs whose latest decision is a license hold made under a policy other than policy_version.

    version_selection_required is not a license decision (several published PMC versions; the
    corpus never guesses one), so it is not re-run: the same metadata would hold it again."""
    latest = {}
    for entry in log:
        latest[entry['pmcid']] = entry
    return {p for p, e in latest.items()
            if e['decision'] in HELD_DECISIONS and e.get('license_policy_version') != policy_version}


async def acquire_selection(root, selection_file, *, limit=None, permissive_only=False, cache=None,
                            client=None, other_roots=None, recheck_held=False, selection_round=None):
    """Acquire the selection file's primaries in order, falling back to same-lane alternates.

    recheck_held=True makes license holds recorded under an older policy version attemptable again,
    so unfilled slots can draw on alternates the current policy now accepts. Filled slots are
    never revisited; the earlier log entry stays and the new attempt is appended with recheck=True."""
    root = Path(root)
    rows = load_selection(selection_file)
    selection_sha = hashlib.sha256(Path(selection_file).read_bytes()).hexdigest()
    manifest_path = root / 'selection.json'
    manifest = json.loads(manifest_path.read_text()) if manifest_path.exists() else []
    roots = list(dict.fromkeys(Path(r).resolve() for r in [*(other_roots or ()), root]))
    used = existing_pmcids(roots)
    log_path = root / 'acquisition/decisions.json'
    log = json.loads(log_path.read_text()) if log_path.exists() else []
    attempted = {x['pmcid'] for x in log}
    current_policy = PERMISSIVE_ONLY_POLICY_VERSION if permissive_only else POLICY_VERSION
    rechecks = stale_held(log, current_policy) if recheck_held else set()
    attempted -= rechecks
    network_path = root / 'acquisition/network.json'
    client = client or BoundedClient(cache or default_cache(root))
    if network_path.exists():
        client.stats.update(json.loads(network_path.read_text()))
    filled = {x.get('selection_slot') for x in manifest}
    alternates = [r for r in rows if r['role'] == 'alternate']
    acquired = 0
    summary = {'acquired': [], 'unfilled_slots': []}
    if recheck_held:
        summary['recheckable_held'] = len(rechecks)

    def log_decision(row, slot, decision, **extra):
        log.append({**row, 'selection_slot': slot, 'decision': decision,
                    **({'recheck': True} if row['pmcid'] in rechecks else {}), **extra})
        attempted.add(row['pmcid'])
        save(log_path, log)

    async def attempt(row, slot):
        """True when the row is acquired into the slot."""
        pmcid = row['pmcid']
        if pmcid in attempted:
            return False
        if pmcid in used:
            log_decision(row, slot, 'duplicate_existing_root'); return False
        if EXCLUDED_TITLE.search(str(row['title'])):
            log_decision(row, slot, 'exclude_title_or_nonenglish'); return False
        if client.stats['metadata_xml_bytes_received'] > 140_000_000:
            raise BudgetExceeded('150 MB total acquisition cap approached; stopping safely')
        client.last_xml = None
        try:
            article = await fetch_article(client, pmcid, permissive_only=permissive_only)
            decision = article['status']
            if decision == 'eligible' and not article.get('has_body_text'):
                decision = 'missing_body'
            if decision == 'eligible' and not article.get('text', '').strip():
                decision = 'empty_text'
            if decision == 'eligible':
                article_id = write_article(root, article, client.last_xml)
                policy = article['license'].get('policy_version')
                manifest.append({'article_id': article_id, 'pmcid': pmcid,
                    'title': article['title'], 'stratum': row['lane'], 'lane': row['lane'], 'group': row['group'],
                    'selection_role': row['role'], 'selection_slot': slot,
                    **({'replaces': slot} if pmcid != slot else {}),
                    'selection_title': row['title'], 'journal': row['journal'], 'year': row['year'],
                    'source_url': article['source_url'], 'doi': article.get('doi'),
                    'license': article['license'], 'license_policy_version': policy,
                    'permissive_only': permissive_only,
                    'text_sha256': article['text_sha256'], 'xml_sha256': article['xml_sha256'],
                    'selection_status': 'candidate_source_acquired', 'prior_benchmark_exposure': False,
                    **({'split': row['split']} if row.get('split') else {}),
                    **({'selection_round': selection_round} if selection_round is not None else {}),
                    'source_family_check': 'pending_full_text_screen',
                    'selection_reason': row.get('reason') or f'Supplied selection list: lane {row["lane"]}, group {row["group"]} ({row["role"]}).',
                    'patient_count_hint': row.get('patient_count_hint'),
                    'selection_file': Path(selection_file).name, 'selection_file_sha256': selection_sha,
                    'annotation_status': 'not_started', 'review_status': 'not_started'})
                used.add(pmcid); filled.add(slot)
                save(manifest_path, manifest)
            log_decision(row, slot, decision, **({'eligibility_details': article.get('rejected', []),
                'versions': article.get('versions', []), 'license_reason': (article.get('license') or {}).get('reason'),
                'license_policy_version': (article.get('license') or {}).get('policy_version')}
                if decision != 'eligible' else {}))
            return decision == 'eligible'
        except BudgetExceeded:
            raise
        except Exception as error:
            log_decision(row, slot, 'fetch_error', error=str(error)); return False
        finally:
            save(network_path, dict(client.stats))

    try:
        for primary in (r for r in rows if r['role'] == 'primary'):
            slot = primary['pmcid']
            if slot in filled:
                continue
            if limit is not None and acquired >= limit:
                break
            pool = sorted((r for r in alternates if r['lane'] == primary['lane']),
                          key=lambda r: r['group'] != primary['group'])   # same group first, then file order
            for row in [primary, *pool]:
                if await attempt(row, slot):
                    acquired += 1; summary['acquired'].append(manifest[-1]['article_id'])
                    print(f"{primary['lane']}/{primary['group']}: {row['pmcid']} acquired for slot {slot}", flush=True)
                    break
            else:
                summary['unfilled_slots'].append(slot)
                print(f"{primary['lane']}/{primary['group']}: slot {slot} unfilled (alternates exhausted)", flush=True)
    finally:
        save(log_path, log)
        save(network_path, dict(client.stats))
        await client.close()
    return summary


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('stage', choices=['acquire'])
    parser.add_argument('--selection-file', type=Path, required=True, help='JSON list of selection rows (see module docstring)')
    parser.add_argument('--round', type=int, required=True, dest='selection_round',
                        help='selection round recorded on every acquired row (the next after selection.json\'s highest)')
    parser.add_argument('--root', type=Path, default=CORPUS, help='target root (default benchmarks/dense)')
    parser.add_argument('--limit', type=int, default=None, help='max new articles this run (default: all slots)')
    parser.add_argument('--permissive-only', action='store_true',
                        help='accept only CC0, CC BY, CC BY-SA and public domain (reject NC/ND)')
    parser.add_argument('--recheck-held', action='store_true',
                        help='re-attempt license holds recorded under an older policy version (unfilled slots only)')
    parser.add_argument('--cache', help='metadata cache path (default /tmp/op2-<root name>-metadata.sqlite)')
    args = parser.parse_args()
    print(json.dumps(asyncio.run(acquire_selection(args.root, args.selection_file, limit=args.limit,
        permissive_only=args.permissive_only, cache=args.cache, recheck_held=args.recheck_held,
        selection_round=args.selection_round)), indent=2))
