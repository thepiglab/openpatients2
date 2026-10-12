"""Score pipeline unified rows against reference unified rows.

    python -m openpatients2.unified_scoring --reference data/dense_clinical_cases_v1/data \
        --predicted OUT/data/unified.jsonl [--out score.json]

Design (see `openpatients2.scoring`):
- Patients are aligned per article by identity evidence, label, demographics and item content
  (Hungarian), never by `patient_id` strings; predicted ids are mapped to reference ids for panel
  attribution and series.
- Clinical items in all 14 sections are matched one-to-one per aligned patient by evidence-quote
  overlap with name similarity as tie-break (repeats are not collapsed). Observations,
  symptoms_function and procedures_devices are pooled (exam signs/scales and diagnostic imaging may
  sit in either section); cross-section matches and a strict same-section score are reported.
- Matched pairs are scored per attribute (assertion, status, value, unit, comparator, time,
  subject, temporality, category); a polarity flip is an assertion error.
- Reference items with `scoring: "review_only"` absorb predictions (neither TP nor FP) and are not
  required for recall.
- Reference articles missing from the prediction count as full misses; coverage is reported.
- Timeline events/relations, summary claims, roster, figure panels (normalised panel labels,
  EHR labels, attribution, privacy flags), supplement manifests and series points are scored too.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from .scoring.clinical import ClinicalScore
from .scoring.extras import (FigureScore, RosterScore, SeriesScore, SummaryScore, SupplementScore, TimelineScore,
                             align_patients)

SCHEMA_VERSION = 'unified-score/2'
EMPTY = {'patients': [], 'figures': [], 'supplements': [], 'measurement_series': {'status': 'missing', 'series': []}}


def load_rows(path):
    path = Path(path)
    files = sorted(path.glob('*.jsonl')) if path.is_dir() else [path]
    return {r['article_id']: r for f in files for r in map(json.loads, filter(str.strip, f.read_text().splitlines()))}


def score(reference, predicted):
    """Score predicted rows against reference rows; both are {article_id: unified row}."""
    clinical, roster, timeline, summary = ClinicalScore(), RosterScore(), TimelineScore(), SummaryScore()
    figures, supplements, series = FigureScore(), SupplementScore(), SeriesScore()
    missing = sorted(set(reference) - set(predicted))
    for aid in sorted(reference):
        ref, pred = reference[aid], predicted.get(aid) or EMPTY
        rps, pps = ref.get('patients') or [], pred.get('patients') or []
        aligned = align_patients(rps, pps)
        roster.add(aligned, len(rps), len(pps))
        pid_map = {p['patient_id']: r['patient_id'] for r, p, _ in aligned if r and p}
        for r, p, _ in aligned:
            clinical.add_patient(r, p); timeline.add(r, p); summary.add(r, p)
        figures.add(ref, pred, pid_map)
        supplements.add(ref, pred)
        series.add(ref, pred, pid_map)
    c, f, s = clinical.result(), figures.result(), supplements.result()
    t, ro, se = timeline.result(), roster.result(), series.result()
    n = len(reference)
    return {
        'schema_version': SCHEMA_VERSION,
        'coverage': {'reference_articles': n, 'predicted_articles': len(set(predicted) & set(reference)),
                     'coverage': (n - len(missing)) / n if n else None, 'missing_articles': missing,
                     'extra_predicted_articles_ignored': sorted(set(predicted) - set(reference))},
        'overall': {'clinical_items': {k: c[k] for k in ('tp', 'fp', 'fn', 'precision', 'recall', 'f1')},
                    'clinical_items_strict_section_f1': c['strict_section']['f1'],
                    'clinical_macro_f1_over_tasks': c['macro_f1_over_tasks'],
                    'attribute_accuracy': c['attribute_accuracy']['accuracy'],
                    'assertion_accuracy': c['per_attribute']['assertion']['accuracy'],
                    'roster_f1': ro['f1'], 'timeline_event_f1': t['events']['f1'],
                    'timeline_relation_f1': t['relations']['f1'], 'summary_claim_f1': summary.result()['f1'],
                    'panel_detection_f1': f['panel_detection']['f1'],
                    'panel_ehr_category_accuracy': f['panel_ehr_category']['accuracy'],
                    'series_points_f1': se['f1']},
        'clinical': c,
        'roster': ro,
        'timeline': t,
        'summary_claims': summary.result(),
        'figures': f,
        'supplements': s,
        'series_points': se,
        # Backward-compatible keys (unified-score/1).
        'articles_scored': n - len(missing), 'articles_missing_prediction': missing,
        'clinical_item_names': {**{k: c[k] for k in ('tp', 'fp', 'fn', 'precision', 'recall', 'f1')},
                                'by_task': {k: {kk: v[kk] for kk in ('tp', 'fp', 'fn', 'precision', 'recall', 'f1')}
                                            for k, v in c['by_task'].items()},
                                'definition': 'Alias of clinical (evidence-matched items, section-lenient).'},
        'panel_ehr_category_accuracy': f['panel_ehr_category']['accuracy'], 'panels_scored': f['panel_ehr_category']['total'],
        'panel_ehr_includable': f['panel_ehr_includable'], 'privacy_flags': f['privacy_flags'],
        'supplement_class_accuracy': s['class']['accuracy'], 'supplement_files_scored': s['class']['total'],
        'supplement_ehr_includable': s['ehr_includable'],
        'warning': 'Reference rows are AI reference annotations (not physician gold); scores are agreement, not accuracy.'}


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('--reference', required=True); p.add_argument('--predicted', required=True); p.add_argument('--out')
    args = p.parse_args(argv)
    result = score(load_rows(args.reference), load_rows(args.predicted))
    text = json.dumps(result, indent=2)
    if args.out:
        Path(args.out).write_text(text + '\n')
    print(text)


if __name__ == '__main__':
    main()
