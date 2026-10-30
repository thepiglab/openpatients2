"""Freeze a small, diverse Beam/FHIR development corpus without model scores.

Reference-derived counts are selection metadata only. They must never enter
extraction prompts or be presented as independent held-out benchmark results.
"""
from __future__ import annotations

from collections import Counter, defaultdict
import gzip
import hashlib
import json
import math
from pathlib import Path
import re

METHOD = 'beam-fhir-diverse-measurements/1'
SEED = 20261030
FAMILIES = {
    'cardiology': ('cardiac', 'echo_cardiac', 'heart_failure_af_htn', 'ecg_arrhythmia', 'ecg_waveform', 'acs_stroke'),
    'respiratory': ('respiratory', 'pulmonary_function', 'copd_asthma_pulm'),
    'endocrine': ('endocrine', 'diabetes_endocrine'),
    'nephrology': ('renal', 'ckd_renal'),
    'gastroenterology': ('gastrointestinal', 'endoscopy', 'liver_gi'),
    'hepatology': ('hepatic',),
    'neurology': ('neurology', 'neurophysiology', 'neuro_eeg'),
    'infectious_disease': ('infectious', 'infection'),
    'oncology': ('oncology', 'common_cancer'),
    'ophthalmology': ('ophthalmology', 'ophthalmic_imaging'),
    'ent_audiology': ('ent', 'audiology', 'ent_audiology'),
    'dermatology': ('dermatology', 'dermoscopy_skin'),
    'rheumatology': ('autoimmune',),
    'hematology': ('hematology',),
    'pediatrics': ('pediatric', 'pediatric_common'),
    'neonatology': ('neonatal',),
    'obstetrics': ('obstetric',),
    'gynecology': ('gynecologic',),
    'psychiatry': ('psychiatry',),
    'musculoskeletal': ('musculoskeletal',),
    'vascular': ('vascular',),
    'surgery': ('surgical',),
    'toxicology': ('toxicology', 'toxicology_substance'),
    'critical_care': ('icu_monitoring',),
    'dental': ('dental_oral',),
    'cross_specialty': ('serial_labs', 'longitudinal', 'longitudinal_multiyear', 'primary_care',
                        'case_series', 'geriatric', 'africa', 'latin_america', 'oceania_europe_east'),
}
FAMILY_FOR = {lane: family for family, lanes in FAMILIES.items() for lane in lanes}
CRITERIA = {
    'split': 'train', 'completed_reference_required': True,
    'license_codes': ['CC BY'], 'human_patients_only': True,
    'min_patients': 1, 'max_patients': 3, 'max_source_characters': 40000,
    'max_table_rows': 60, 'min_numeric_observations': 8,
    'max_articles_per_family': 3, 'target_multi_patient_articles': 10,
    'ambiguous_identity_label_pattern': r'\b(unidentified|unspecified|illustrative|representative|aggregate|cohort)\b',
}


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def features(row, source):
    patients = row.get('patients') or []
    def items(task):
        return [item for p in patients for item in (p.get('sections', {}).get(task) or {}).get('items', [])]
    observations = items('observations')
    numeric = [o for o in observations if o.get('numeric_value') is not None]
    series = (row.get('measurement_series') or {}).get('series') or []
    strata = row.get('primary_stratum') or 'unclassified'
    return {
        'primary_stratum': strata, 'specialty_family': FAMILY_FOR.get(strata, 'cross_specialty'),
        'patients': len(patients), 'source_characters': len(source['text']),
        'source_segments': len(source.get('segments', [])),
        'table_rows': sum(s.get('kind') == 'table_row' for s in source.get('segments', [])),
        'numeric_observations': len(numeric), 'observation_items': len(observations),
        'numeric_observation_names': len({str(o.get('name') or '').casefold() for o in numeric}),
        'series_points': sum(len(s.get('points') or []) for s in series),
        'condition_items': len(items('conditions')), 'medication_items': len(items('medications')),
        'procedure_items': len(items('procedures_devices')), 'outcome_items': len(items('outcomes')),
        'figure_captions': len(source.get('figures', [])),
        'measurement_evidence': 'reference counts, selection only; not independent clinical validation',
    }


def exclusion_reasons(row, source, f):
    reasons = []
    if row.get('split') != 'train': reasons.append('held_out_split')
    if not row.get('completion', {}).get('completed'): reasons.append('reference_incomplete')
    license_ = source.get('license') or {}
    if not license_.get('allowed') or license_.get('code') not in CRITERIA['license_codes'] or license_.get('restrictive_prose'):
        reasons.append('outside_cc_by_release_scope')
    if row.get('text_sha256') != source.get('text_sha256'):
        reasons.append('reference_source_hash_mismatch')
    if row.get('species', {}).get('article_has_non_human_patient') or any(p.get('species') != 'human' for p in row.get('patients', [])):
        reasons.append('nonhuman_or_unspecified_species')
    if not CRITERIA['min_patients'] <= f['patients'] <= CRITERIA['max_patients']:
        reasons.append('patient_count_outside_1_to_3')
    if f['source_characters'] > CRITERIA['max_source_characters']: reasons.append('source_over_40000_characters')
    if f['table_rows'] > CRITERIA['max_table_rows']: reasons.append('table_rows_over_60')
    if f['numeric_observations'] < CRITERIA['min_numeric_observations']: reasons.append('fewer_than_8_numeric_observations')
    if any(re.search(CRITERIA['ambiguous_identity_label_pattern'], p.get('label') or '', re.I) for p in row.get('patients', [])):
        reasons.append('ambiguous_patient_identity_label')
    return reasons


def richness(f):
    """Saturating counts favor useful data without rewarding unbounded density."""
    return (2 * math.log1p(min(f['numeric_observations'], 100))
            + 1.5 * math.log1p(min(f['series_points'], 100))
            + .3 * sum(f[k] > 0 for k in ('condition_items', 'medication_items', 'procedure_items', 'outcome_items'))
            + .4 * (f['table_rows'] > 0)
            - f['source_characters'] / 20000 - .5 * (f['patients'] - 1))


def choose(candidates, count=50, seed=SEED):
    if count < 1 or count > len(candidates): raise ValueError('Insufficient eligible articles for requested selection')
    by_id = {r['article_id']: r for r in candidates}
    if len(by_id) != len(candidates): raise ValueError('Duplicate candidate ID')
    selected = []; families = Counter(); lanes = Counter()
    def rank(aid):
        f = by_id[aid]['features']
        novelty = 2 / (1 + lanes[f['primary_stratum']])
        return (-richness(f) - novelty, hashlib.sha256(f'{seed}:{aid}'.encode()).hexdigest())
    def add(aid):
        selected.append(aid); f = by_id[aid]['features']
        families[f['specialty_family']] += 1; lanes[f['primary_stratum']] += 1
    # A first seat per represented specialty family, then explicit small-series
    # diversity, then richness subject to a three-seat family ceiling.
    groups = defaultdict(list)
    for aid, r in by_id.items(): groups[r['features']['specialty_family']].append(aid)
    for family in sorted(groups):
        if len(selected) == count: break
        add(min(groups[family], key=rank))
    target_multi = min(CRITERIA['target_multi_patient_articles'], count // 5)
    while len(selected) < count:
        available = [aid for aid in by_id if aid not in selected and
                     families[by_id[aid]['features']['specialty_family']] < CRITERIA['max_articles_per_family']]
        if not available: raise ValueError('Specialty ceiling prevents requested sample size')
        if sum(by_id[a]['features']['patients'] > 1 for a in selected) < target_multi:
            multi = [aid for aid in available if by_id[aid]['features']['patients'] > 1]
            if multi: available = multi
        add(min(available, key=rank))
    return [by_id[aid] for aid in selected]


def build(root, count=50, seed=SEED):
    root = Path(root)
    input_path = root / 'data/dense_clinical_cases_v1/data/train.jsonl'
    rows = [json.loads(line) for line in input_path.read_text().splitlines() if line.strip()]
    if len({r['article_id'] for r in rows}) != len(rows): raise ValueError('Duplicate source reference article')
    candidates = []; excluded = []
    for row in sorted(rows, key=lambda r: r['article_id']):
        aid = row['article_id']; path = root / 'benchmarks/dense/articles' / aid / 'source.json'
        if not path.is_file():
            excluded.append({'article_id': aid, 'reasons': ['missing_local_source']}); continue
        source = json.loads(path.read_text()); f = features(row, source)
        reasons = exclusion_reasons(row, source, f)
        entry = {'article_id': aid, 'split': row['split'], 'title': row['title'],
                 'source_path': str(path.relative_to(root)), 'source_file_sha256': sha(path),
                 'source_text_sha256': source['text_sha256'], 'features': f,
                 'license': source['license'], 'source_url': source.get('source_url'),
                 'doi': source.get('doi'), 'score': round(richness(f), 6)}
        if reasons: excluded.append({'article_id': aid, 'reasons': reasons, 'features': f})
        else: candidates.append(entry)
    selected = choose(candidates, count, seed); ids = {s['article_id'] for s in selected}
    for s in selected:
        s['selection_reasons'] = ['specialty diversity', 'numeric measurement richness',
                                  'bounded source and patient count', 'existing article-level CC BY metadata']
        if s['features']['series_points']: s['selection_reasons'].append('serial measurements already annotated')
        if s['features']['patients'] > 1: s['selection_reasons'].append('small-series patient-attribution coverage')
    counts = {k: dict(sorted(Counter(str(s['features'][k]) for s in selected).items()))
              for k in ('specialty_family', 'primary_stratum', 'patients')}
    totals = {k: sum(s['features'][k] for s in selected) for k in
              ('patients', 'numeric_observations', 'observation_items', 'series_points', 'condition_items',
               'medication_items', 'procedure_items', 'outcome_items', 'table_rows', 'source_characters')}
    manifest = {'format': METHOD, 'seed': seed, 'article_count': count, 'article_ids': [s['article_id'] for s in selected],
                'criteria': CRITERIA, 'input_train_reference': {'path': str(input_path.relative_to(root)), 'sha256': sha(input_path), 'articles': len(rows)},
                'eligible_articles': len(candidates), 'counts': counts, 'totals': totals,
                'articles_with_tables': sum(s['features']['table_rows'] > 0 for s in selected),
                'articles_with_series': sum(s['features']['series_points'] > 0 for s in selected),
                'articles': selected, 'excluded': excluded,
                'eligible_not_selected': sorted(c['article_id'] for c in candidates if c['article_id'] not in ids),
                'selection_does_not_use': ['model predictions', 'model scores', 'validation references', 'test references'],
                'reference_metadata_use': 'Selection/scoring only; never include counts, reference patients or labels in inference prompts.',
                'release_scope': 'Text and derived clinical records; retain article attribution/license. Figure pixels and third-party exceptions require separate checks.',
                'clinical_accuracy_verified': False, 'population_representative': False}
    return manifest, [row for row in sorted(rows, key=lambda r: r['article_id']) if row['article_id'] in ids]


def freeze(root, manifest_path, reference_path, count=50, seed=SEED):
    root = Path(root).resolve(); manifest_path = Path(manifest_path); reference_path = Path(reference_path)
    manifest, rows = build(root, count, seed)
    payload = ''.join(json.dumps(r, ensure_ascii=False, sort_keys=True) + '\n' for r in rows).encode()
    reference_path.parent.mkdir(parents=True, exist_ok=True)
    reference_path.write_bytes(gzip.compress(payload, mtime=0))
    manifest['reference'] = {'path': str(reference_path.resolve().relative_to(root)), 'sha256': sha(reference_path),
                             'uncompressed_sha256': hashlib.sha256(payload).hexdigest(), 'articles': len(rows)}
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + '\n')
    return manifest


def validate_selection(root, path):
    """Validate only the frozen subset, allowing the live corpus to keep growing."""
    root = Path(root); manifest = json.loads(Path(path).read_text())
    if manifest.get('format') != METHOD: raise ValueError('Unknown competition selection format')
    ids = manifest['article_ids']
    if len(ids) != manifest['article_count'] or len(set(ids)) != len(ids): raise ValueError('Invalid selection IDs')
    ref_path = root / manifest['reference']['path']
    if sha(ref_path) != manifest['reference']['sha256']: raise ValueError('Frozen competition reference changed')
    with gzip.open(ref_path, 'rt') as f: rows = [json.loads(line) for line in f if line.strip()]
    if {r['article_id'] for r in rows} != set(ids) or len(rows) != len(ids): raise ValueError('Reference membership differs')
    by_id = {r['article_id']: r for r in rows}
    if {r['article_id'] for r in manifest['articles']} != set(ids): raise ValueError('Selection metadata differs')
    for selected in manifest['articles']:
        source_path = root / selected['source_path']
        if sha(source_path) != selected['source_file_sha256']: raise ValueError('Source file changed: ' + selected['article_id'])
        source = json.loads(source_path.read_text()); row = by_id[selected['article_id']]
        if row['split'] != 'train' or selected['split'] != 'train': raise ValueError('Held-out article in competition subset')
        if row['text_sha256'] != selected['source_text_sha256'] or source['text_sha256'] != selected['source_text_sha256']:
            raise ValueError('Source/reference identity differs')
    return manifest


def main():
    import argparse
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--root', type=Path, default=Path.cwd())
    p.add_argument('--check', action='store_true')
    args = p.parse_args(); folder = args.root / 'configs/hipergator/nemotron-super'
    path = folder / 'competition-50.json'
    result = validate_selection(args.root, path) if args.check else freeze(args.root, path, folder / 'competition-50-reference.jsonl.gz')
    print(json.dumps({'manifest': str(path), 'article_count': result['article_count'], 'totals': result['totals']}, indent=2))


if __name__ == '__main__': main()
