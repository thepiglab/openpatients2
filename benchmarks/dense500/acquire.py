"""Bounded public-source acquisition for the dense reference annotation project.

This selects candidate text, never fabricates annotations or review receipts.
Run from the repository root using the existing project environment.
"""
import argparse
import asyncio
import hashlib
import json
from pathlib import Path
import re

import httpx

from openpatients2.corpus import fetch_article, search_candidates
from openpatients2.literature_client import LiteratureClient, LiteratureConfig
import store

ROOT = Path(__file__).resolve().parent
LANES = {
    'cardiac': 'cardiac OR myocarditis OR cardiomyopathy OR arrhythmia',
    'vascular': 'aortic OR vascular OR thrombosis OR embolism',
    'respiratory': 'pulmonary OR pneumonia OR respiratory OR asthma',
    'gastrointestinal': 'gastric OR intestinal OR bowel OR esophageal',
    'hepatic': 'hepatic OR hepatitis OR liver OR biliary',
    'renal': 'renal OR kidney OR nephrotic OR glomerulonephritis',
    'endocrine': 'diabetes OR adrenal OR thyroid OR hypoglycemia',
    'neurology': 'encephalitis OR seizure OR neuropathy OR stroke',
    'psychiatry': 'psychosis OR schizophrenia OR depression OR catatonia',
    'infectious': 'infection OR meningitis OR tuberculosis OR sepsis',
    'autoimmune': 'lupus OR vasculitis OR autoimmune OR arthritis',
    'dermatology': 'dermatitis OR psoriasis OR cutaneous OR pemphigus',
    'hematology': 'hemolytic OR thrombocytopenia OR pancytopenia OR anemia',
    'oncology': 'carcinoma OR lymphoma OR leukemia OR sarcoma',
    'pediatric': 'child OR pediatric OR paediatric OR adolescent',
    'neonatal': 'neonatal OR newborn OR neonate OR congenital',
    'obstetric': 'pregnancy OR pregnant OR postpartum OR obstetric',
    'gynecologic': 'ovarian OR uterine OR endometriosis OR endometrial',
    'surgical': 'postoperative OR reconstruction OR surgical OR transplantation',
    'ophthalmology': 'ocular OR retinal OR corneal OR ophthalmic',
    'ent': 'laryngeal OR otitis OR sinusitis OR cochlear',
    'musculoskeletal': 'fracture OR osteomyelitis OR spinal OR tendon',
    'toxicology': 'poisoning OR overdose OR anaphylaxis OR drug-induced',
    'veterinary': 'dog OR canine OR cat OR feline OR equine OR camel',
}


def save(path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + '.tmp')
    temp.write_text(json.dumps(obj, ensure_ascii=False, indent=2) + '\n')
    temp.replace(path)


async def discover():
    output = ROOT / 'acquisition/candidates.json'
    rows = json.loads(output.read_text()) if output.exists() else []
    existing = {r['lane'] for r in rows}
    async with httpx.AsyncClient(timeout=60) as http:
        for lane, terms in LANES.items():
            if lane in existing:
                continue
            title_terms = ' OR '.join(t.strip()+'[Title]' for t in terms.split(' OR '))
            query = (f'({title_terms}) AND ("case report"[Title] OR "case series"[Title]) '
                     'AND ("2020/01/01"[Publication Date] : "2025/12/31"[Publication Date])')
            found = await search_candidates(query, limit=70, http=http)
            await asyncio.sleep(.6)
            response = await http.get('https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esummary.fcgi',
                params={'db': 'pmc', 'id': ','.join(x[3:] for x in found['pmcids']),
                        'retmode': 'json', 'tool': 'openpatients2'})
            response.raise_for_status()
            result = response.json()['result']
            lane_rows = [{'lane': lane, 'pmcid': 'PMC'+x, 'title': result[x].get('title'),
                          'pubdate': result[x].get('pubdate'), 'query': query}
                         for x in result.get('uids', [])]
            rows.extend(lane_rows)
            save(output, rows)
            print(f'{lane}: {len(lane_rows)} candidates', flush=True)
            await asyncio.sleep(.6)


class BoundedClient(LiteratureClient):
    def __init__(self):
        super().__init__(LiteratureConfig(
            cache='/tmp/op2-dense500-metadata.sqlite', concurrency=1,
            cloud_requests_per_second=3, retries=1,
            max_xml_bytes=2_000_000, max_metadata_bytes=1_000_000,
            max_versions=8, max_listing_pages=2))
        self.last_xml = None

    async def _text(self, url, kind, params=None):
        # Serial acquisition leaves room for both bounded attempts of any request.
        if self.stats['metadata_xml_bytes_received'] > 145_000_000:
            raise RuntimeError('Acquisition byte budget reached; resume only after review')
        value = await super()._text(url, kind, params)
        if kind == 'xml':
            self.last_xml = value[0]
        return value


async def acquire(limit=477):
    candidates = json.loads((ROOT / 'acquisition/approved-titles.json').read_text())
    manifest_path = ROOT / 'selection.json'
    manifest = json.loads(manifest_path.read_text()) if manifest_path.exists() else []
    used = {x['pmcid'] for x in manifest}
    used.update(aid.split('.')[0] for aid in store.article_ids(ROOT.parent/'dense50', 'sources'))
    log_path = ROOT / 'acquisition/decisions.json'
    log = json.loads(log_path.read_text()) if log_path.exists() else []
    attempted = {x['pmcid'] for x in log}
    client = BoundedClient()
    network_path = ROOT/'acquisition/network.json'
    if network_path.exists():
        client.stats.update(json.loads(network_path.read_text()))
    retained = json.loads((ROOT/'retained-strata.json').read_text())
    initial_count = len(manifest)
    try:
        for lane in LANES:
            target = (30 if lane in ['pediatric','neonatal'] else 20)-list(retained.values()).count(lane)
            have = sum(x['stratum'] == lane for x in manifest)
            pool = [x for x in candidates if x['lane'] == lane]
            for candidate in pool:
                if len(manifest)-initial_count >= limit:
                    return
                if have >= target:
                    break
                pmcid = candidate['pmcid']
                if pmcid in used or pmcid in attempted:
                    continue
                if re.search(r'^(retract|correction|corrigendum|erratum)|^\[', candidate['title'], re.I):
                    log.append({**candidate, 'decision': 'exclude_title_or_nonenglish'})
                    attempted.add(pmcid)
                    continue
                if client.stats['metadata_xml_bytes_received'] > 140_000_000:
                    raise RuntimeError('150 MB total acquisition cap approached; stopping safely')
                client.last_xml = None
                try:
                    article = await fetch_article(client, pmcid)
                    decision = article['status']
                    if decision == 'eligible' and not article.get('has_body_text'):
                        decision = 'missing_body'
                    if decision == 'eligible' and not article.get('text', '').strip():
                        decision = 'empty_text'
                    if decision == 'eligible':
                        article_id = article['article_id']
                        xml = client.last_xml
                        if not xml or hashlib.sha256(xml.encode()).hexdigest() != article['xml_sha256']:
                            raise ValueError('Raw XML hash disagrees with parsed source')
                        save(store.path(ROOT, 'sources', article_id), article)
                        store.path(ROOT, 'raw-xml', article_id).write_text(xml)
                        text = article['title']+'\n\n'+'\n\n'.join(
                            f"[{s['segment_id']}] {s.get('heading', '')}\n{s['text']}"
                            for s in article['segments'])
                        store.path(ROOT, 'source-text', article_id).write_text(text)
                        manifest.append({'article_id': article_id, 'pmcid': pmcid,
                            'title': article['title'], 'stratum': lane,
                            'source_url': article['source_url'], 'doi': article.get('doi'),
                            'license': article['license'], 'text_sha256': article['text_sha256'],
                            'xml_sha256': article['xml_sha256'], 'selection_status': 'candidate_source_acquired',
                            'prior_benchmark_exposure': False, 'split': candidate['split'],
                            'source_family_check': 'pending_full_text_screen',
                            'selection_reason': candidate['reason'],
                            'patient_count_hint': candidate['patient_count_hint'],
                            'annotation_status': 'not_started', 'review_status': 'not_started'})
                        used.add(pmcid); have += 1
                        save(manifest_path, manifest)
                    log.append({**candidate, 'decision': decision,
                        **({'eligibility_details': article.get('rejected', []),
                            'versions': article.get('versions', [])}
                           if decision != 'eligible' else {})})
                except Exception as error:
                    log.append({**candidate, 'decision': 'fetch_error', 'error': str(error)})
                attempted.add(pmcid)
                save(log_path, log)
                save(ROOT/'acquisition/network.json', dict(client.stats))
                print(f'{lane} {have}/{target}; {len(manifest)}/477: {pmcid} {log[-1]["decision"]}', flush=True)
    finally:
        save(log_path, log)
        save(ROOT/'acquisition/network.json', dict(client.stats))
        await client.close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('stage', choices=['discover', 'acquire'])
    parser.add_argument('--limit', type=int, default=477)
    args = parser.parse_args()
    asyncio.run(discover() if args.stage == 'discover' else acquire(args.limit))
