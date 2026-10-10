"""Optional EHR stages after a baseline extraction run, emitting unified article rows.

All four stages are OFF by default (`EhrStageConfig`). They run on a finished
`PilotRunner` output directory (patients.jsonl, rosters.json,
visual-annotations.json), reuse the runner's bounded call/repair/ledger
machinery and the extraction config's serving limits, and write:

  OUTPUT/figure-ehr-labels/ID.json      dense-figure-ehr-labels/1
  OUTPUT/figure-privacy-flags/ID.json   dense-figure-privacy-flags/1   (needs pixels)
  OUTPUT/supplement-manifests/ID.json   dense-supplement-manifest/1    (needs acquisition records)
  OUTPUT/measurement-series/ID.json     dense-measurement-series/1
  OUTPUT/data/unified.jsonl             openpatients2-unified-article/1 (same builder as the release)

    python -m openpatients2.ehr_pipeline --config ehr.yaml --articles sample.jsonl --run-dir RUN \
        --output OUT --endpoints http://127.0.0.1:8000/v1 --context 131072 \
        [--image-manifest vision-assets/manifest.json] [--supplement-assets DIR] [--supplement-transcriptions DIR]

Pipeline rows are unreviewed model output (`completion.completed=false`).
"""
from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
from typing import Literal

import yaml

from . import ehr_tasks
from .config import ConfigModel
from .unified_record import build_record

STAGES = ('figure_ehr_label', 'figure_privacy_flags', 'supplement_classification', 'measurement_series_extraction')
PRESENT = 'model_output_unreviewed'


class EhrStageConfig(ConfigModel):
    figure_ehr_label: bool = False
    figure_privacy_flags: bool = False
    supplement_classification: bool = False
    measurement_series_extraction: bool = False
    extraction_config: str | dict | None = None
    default_split: Literal['train', 'validation', 'test'] = 'test'


def visual_target_from_pilot(article, visual_rows):
    """PilotRunner visual-annotations rows -> the dense-figure-reference/1 shape the sidecars bind."""
    rows = {r['identity']['figure_id']: r for r in visual_rows if r['identity']['article_id'] == article['article_id']}
    figures = []
    for figure in article.get('figures', []):
        fid = figure.get('figure_key') or figure.get('figure_id')
        row = rows.get(fid)
        visual = ((row or {}).get('visual') or {}).get('data')
        attribution = ((row or {}).get('attribution') or {}).get('data')
        provenance = (row or {}).get('pixel_provenance') or {}
        if row and row.get('status') == 'valid' and visual and attribution:
            figures.append({'figure_id': fid, 'status': 'inspected',
                            'assets': [{'url': provenance.get('url'), 'sha256': provenance.get('sha256')}] if provenance else [],
                            'analysis': {'visual': visual, 'attribution': attribution}, 'limitations': []})
        else:
            figures.append({'figure_id': fid, 'status': 'unavailable', 'assets': [], 'analysis': None,
                            'limitations': ['No valid pipeline visual analysis for this figure']})
    return {'format': 'dense-figure-reference/1', 'article_id': article['article_id'],
            'source_text_sha256': article['text_sha256'], 'figures': figures}


def pilot_outputs(run_dir):
    run_dir = Path(run_dir)
    bundles = [json.loads(line) for line in (run_dir / 'patients.jsonl').read_text().splitlines() if line.strip()]
    rosters = {a['article_id']: a['roster'] for a in json.loads((run_dir / 'rosters.json').read_text())['articles']
               if a.get('roster')}
    visual = json.loads((run_dir / 'visual-annotations.json').read_text()) if (run_dir / 'visual-annotations.json').exists() else []
    return bundles, rosters, visual


def unified_from_pipeline(article, *, bundles, roster, visual_target, outputs, split, statuses, generator='openpatients2_pipeline'):
    """Assemble the same unified row the release uses, from pipeline outputs."""
    identities = list((roster or {}).get('patients', []))
    article_bundles = [b for b in bundles if (b['source'].get('article_source') or {}).get('article_id', article['article_id']) == article['article_id']
                       and b['source'].get('record_id', '').startswith(article['article_id'] + ':')]
    return build_record(
        article={'title': article.get('title'), 'authors': article.get('authors'), 'article_url': article.get('source_url'),
                 'license': article.get('license')},
        split=split, identities=identities, patient_bundles=article_bundles, source=article,
        visual_target=visual_target, visual_status='model_output_unreviewed' if visual_target else 'not_run',
        labels=outputs.get('figure_ehr_label'), privacy=outputs.get('figure_privacy_flags'),
        manifest=outputs.get('supplement_classification'), supplement_reviews=(),
        series=outputs.get('measurement_series_extraction'), statuses=statuses,
        provenance={'generator': generator, 'receipts': [], 'physician_adjudicated': False,
                    'note': 'Automated model output; no agent or parent review.'},
        completion={'completed': False, 'checks': {}, 'note': 'Unreviewed pipeline output'},
        present_status=PRESENT)


async def run_ehr_stages(config, articles_path, run_dir, output, endpoints, context, *, image_manifest=None,
                         supplement_assets=None, supplement_transcriptions=None, http=None):
    from .pilot_extract import PilotRunner
    from .pilot_media import _articles
    config = EhrStageConfig.model_validate(yaml.safe_load(Path(config).read_text()) if isinstance(config, (str, Path)) else config)
    output = Path(output)
    runner = PilotRunner(config.extraction_config or {}, output / 'calls', endpoints, context, 'direct', http=http)
    runner.schemas.update(ehr_tasks.schemas())  # shared dict; clients see the new task schemas
    bundles, rosters, visual_rows = pilot_outputs(run_dir)
    images = {}
    for row in runner.image_rows(image_manifest):
        images.setdefault((row['article_id'], row['figure_id']), []).append(row)
    rows = []; report = {'stages': {s: getattr(config, s) for s in STAGES}, 'articles': []}
    try:
        for article in _articles(articles_path):
            aid = article['article_id']; roster = rosters.get(aid)
            visual_target = visual_target_from_pilot(article, visual_rows)
            annotation = {'article_id': aid, 'text_sha256': article['text_sha256'],
                          'patients': [{'patient_id': p['patient_id'], 'label': p.get('label'), 'species': p.get('species'),
                                        'identity_evidence': p.get('identity_evidence')} for p in (roster or {}).get('patients', [])]}
            outputs = {}; statuses = {}; errors = {}
            inspected = [f for f in visual_target['figures'] if f['status'] == 'inspected']
            async def figure_stage(task, builder, checker, assemble, need_pixels):
                results = []
                for f in inspected:
                    pixels = [runner.pixels(r, article) for r in images.get((aid, f['figure_id']), [])]
                    if need_pixels and not pixels:
                        raise ValueError(f'{task} needs pixels for {f["figure_id"]}')
                    result = await runner.call(task, builder(article, f, pixels), lambda v, f=f: checker(v, f),
                                               {'article_id': aid, 'figure_id': f['figure_id']}, 0, article['segments'])
                    if result['status'] != 'valid':
                        raise ValueError(f'{task} invalid for {f["figure_id"]}: {result["errors"][:2]}')
                    results.append(result['data'])
                return assemble(article, visual_target, results)
            jobs = []
            if config.figure_ehr_label:
                jobs.append(('figure_ehr_label', 'figure-ehr-labels', lambda: figure_stage(
                    'figure_ehr_label', lambda a, f, px: ehr_tasks.figure_ehr_messages(a, f, pixels=px),
                    ehr_tasks.check_figure_ehr, ehr_tasks.labels_document, False)))
            if config.figure_privacy_flags:
                jobs.append(('figure_privacy_flags', 'figure-privacy-flags', lambda: figure_stage(
                    'figure_privacy_flags', ehr_tasks.privacy_messages, ehr_tasks.check_privacy, ehr_tasks.flags_document, True)))
            if config.supplement_classification and article.get('supplements'):
                acquisition = json.loads((Path(supplement_assets) / f'{aid}.json').read_text()) if supplement_assets else None
                async def manifest_stage(acquisition=acquisition):
                    if acquisition is None:
                        raise ValueError('supplement_classification needs supplement_assets acquisition records')
                    result = await runner.call('supplement_classification', ehr_tasks.supplement_messages(article, roster, acquisition),
                                               lambda v: ehr_tasks.check_supplements(v, article, annotation, acquisition),
                                               {'article_id': aid}, 0, article['segments'])
                    if result['status'] != 'valid':
                        raise ValueError(f'supplement_classification invalid: {result["errors"][:2]}')
                    return result['data']
                jobs.append(('supplement_classification', 'supplement-manifests', manifest_stage))
            transcriptions = ehr_tasks.load_transcriptions(supplement_transcriptions, aid)
            if config.measurement_series_extraction and (transcriptions or any(s.get('kind') == 'table_row' for s in article['segments'])):
                draft = ehr_tasks.series_draft(article, transcriptions)
                async def series_stage(draft=draft, transcriptions=transcriptions):
                    # Cells quoting reviewed supplement transcriptions are validated against them.
                    result = await runner.call('measurement_series_extraction', ehr_tasks.series_messages(article, roster, draft),
                                               lambda v: ehr_tasks.check_series(v, article, annotation, draft, transcriptions),
                                               {'article_id': aid}, 0, article['segments'])
                    if result['status'] != 'valid':
                        raise ValueError(f'measurement_series_extraction invalid: {result["errors"][:2]}')
                    return result['data']
                jobs.append(('measurement_series_extraction', 'measurement-series', series_stage))
            for task, folder, job in jobs:
                try:
                    if roster is None:
                        raise ValueError('No valid roster for this article')
                    document = await job()
                    outputs[task] = document; statuses[task] = PRESENT
                    (output / folder).mkdir(parents=True, exist_ok=True)
                    (output / folder / f'{aid}.json').write_text(json.dumps(document, ensure_ascii=False, indent=2) + '\n')
                except (ValueError, KeyError, OSError) as exc:
                    statuses[task] = 'model_output_failed'; errors[task] = str(exc)[:500]
            sidecar_status = {'figure_ehr_labels': statuses.get('figure_ehr_label', 'stage_disabled'),
                              'figure_privacy_flags': statuses.get('figure_privacy_flags', 'stage_disabled'),
                              'supplement_manifest': statuses.get('supplement_classification', 'stage_disabled'),
                              'measurement_series': statuses.get('measurement_series_extraction', 'stage_disabled')}
            if roster is not None:
                rows.append(unified_from_pipeline(article, bundles=bundles, roster=roster, visual_target=visual_target,
                                                  outputs=outputs, split=article.get('split', config.default_split),
                                                  statuses=sidecar_status))
            report['articles'].append({'article_id': aid, 'status': sidecar_status, 'errors': errors})
    finally:
        await runner.close()
    (output / 'data').mkdir(parents=True, exist_ok=True)
    (output / 'data' / 'unified.jsonl').write_text(''.join(json.dumps(r, ensure_ascii=False) + '\n' for r in rows))
    (output / 'ehr-report.json').write_text(json.dumps(report, indent=2) + '\n')
    return report


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('--config', required=True); p.add_argument('--articles', required=True)
    p.add_argument('--run-dir', required=True); p.add_argument('--output', required=True)
    p.add_argument('--endpoints', required=True); p.add_argument('--context', type=int, required=True)
    p.add_argument('--image-manifest'); p.add_argument('--supplement-assets')
    p.add_argument('--supplement-transcriptions', help='Folder of reviewed supplement targets (<ID>*.json with clinical_data.transcriptions)')
    args = p.parse_args(argv)
    report = asyncio.run(run_ehr_stages(args.config, args.articles, args.run_dir, args.output, args.endpoints, args.context,
                                        image_manifest=args.image_manifest, supplement_assets=args.supplement_assets,
                                        supplement_transcriptions=args.supplement_transcriptions))
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
