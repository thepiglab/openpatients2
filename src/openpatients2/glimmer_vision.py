"""Bounded CPU figure acquisition and separately measured multimodal evaluation."""
from __future__ import annotations

import asyncio
import base64
from collections import Counter
import hashlib
import json
from pathlib import Path
import time
from urllib.parse import urlparse, parse_qs

import httpx

from .articles import recheck_license
from .data import write_json
from .figure_attribution import FigureReview, figure_messages, bind_figure_review, patient_media
from .figure_visuals import FigureVisuals, JointFigureAnalysis, visual_messages, validate_visuals, validate_joint, panel_columns
from .hpg_eval import Replay, fixtures
from .output_parser import parse_output
from .provenance import json_digest
from .vision import fetch_pixels


async def prepare_assets(campaign):
    """Fetch only listed eligible figures; at most 8 MB each / 64 MB total."""
    config = campaign['config']['vision_evaluation']; work = Path(campaign['work'])
    folder = work / 'vision-assets'; folder.mkdir(exist_ok=True)
    _, articles, _, _, _ = fixtures(Path(campaign['root']) / campaign['config']['fixtures'])
    rows = []; total = 0
    async with httpx.AsyncClient(timeout=40, follow_redirects=False) as client:
        for article in articles.values():
            allowed = recheck_license(article['license'])['allowed']
            for figure in article['figures']:
                row = {'article_id': article['article_id'], 'figure_id': figure['figure_key'],
                       'source_license': article['license'], 'status': 'missing_image'}
                rows.append(row)
                if not allowed or figure.get('rights_statements') or figure.get('reuse_status') == 'asset_rights_review':
                    row['status'] = 'asset_rights_review'; continue
                supported = [url for url in figure['image_urls'] if urlparse(url).path.lower().endswith(('.jpg','.jpeg','.png','.webp','.gif'))]
                if not supported:
                    row['status'] = 'conversion_required' if figure['image_urls'] else 'missing_image'; continue
                row['url'] = supported[0]
                try:
                    if total >= config['max_total_bytes']: raise ValueError('Total figure byte cap exhausted')
                    pixels, provenance = await fetch_pixels(supported[0], http=client,
                        max_bytes=min(config['max_image_bytes'], config['max_total_bytes']-total))
                    data = base64.b64decode(pixels.split(',',1)[1], validate=True)
                    expected_md5 = parse_qs(urlparse(supported[0]).query).get('md5', [None])[0]
                    if expected_md5 and hashlib.md5(data).hexdigest() != expected_md5:
                        raise ValueError('Figure bytes differ from the frozen PMC media MD5')
                    path = folder / (provenance['sha256'] + '.image')
                    if not path.exists(): path.write_bytes(data)
                    total += len(data)
                    row.update(status='ready', file=str(path.relative_to(work)),
                               pixel_provenance={**provenance, 'persisted_pixels': True})
                    del data, pixels
                except (httpx.HTTPError, ValueError) as exc:
                    row.update(status='fetch_failed', error_type=type(exc).__name__, error=str(exc))
                write_json(folder / 'manifest.json', {'figures': rows, 'bytes': total, 'complete': False})
    result = {'figures': rows, 'bytes': total, 'complete': True,
              'status_counts': dict(Counter(r['status'] for r in rows)),
              'notice': 'Frozen bounded evaluation pixels retained for reproducibility; no source files altered.'}
    write_json(folder / 'manifest.json', result)
    if not any(r['status'] == 'ready' for r in rows):
        raise ValueError('No eligible figure pixels acquired; do not allocate GPUs for an image-free vision test')
    return result


def asset_pixels(work, row):
    source = work / row['file']; path = source.resolve()
    if not path.is_relative_to((work / 'vision-assets').resolve()) or source.is_symlink():
        raise ValueError('Figure asset escaped its frozen input directory')
    provenance = row['pixel_provenance']
    if path.stat().st_size != provenance['bytes'] or provenance['bytes'] > 8_000_000:
        raise ValueError('Figure pixels changed after CPU acquisition or exceed the byte cap')
    data = path.read_bytes()
    if len(data) != provenance['bytes'] or hashlib.sha256(data).hexdigest() != provenance['sha256']:
        raise ValueError('Figure pixels changed after CPU acquisition')
    return 'data:' + provenance['mime_type'] + ';base64,' + base64.b64encode(data).decode()


async def evaluate_images(campaign, model, layout, endpoints, output):
    """Matched full-text/caption versus full-text/caption/pixel ownership plus pixel descriptions."""
    config = campaign['config']; work = Path(campaign['work'])
    manifest = json.loads((work / 'vision-assets/manifest.json').read_text())
    _, articles, packets, _, _ = fixtures(Path(campaign['root']) / config['fixtures'])
    output.mkdir(parents=True, exist_ok=True); all_rows = []; started = time.monotonic()
    for arm_name, arm in config['arms'].items():
        replay = Replay(Path(campaign['root']) / config['fixtures'], output / arm_name,
            model, arm, endpoints, config['vision_evaluation']['concurrency_per_replica'],
            config['request_timeout_seconds'], config['max_model_len'])
        for client in replay.clients:
            client.schema_overrides.update({'figure_attribution': FigureReview.model_json_schema(),
                'vision': FigureVisuals.model_json_schema(), 'figure_analysis': JointFigureAnalysis.model_json_schema()})
        slots = [asyncio.Semaphore(config['vision_evaluation']['concurrency_per_replica']) for _ in endpoints]
        async def figure(index, asset):
            article = articles[asset['article_id']]
            roster = json.loads((Path(campaign['root']) / config['fixtures'] / 'rosters' /
                                (article['article_id'] + '.json')).read_text())['roster']
            replica = index % len(endpoints); client = replay.clients[replica]
            record = {**asset, 'arm': arm_name, 'layout': layout, 'semantic_review': 'pending_source_and_pixel_adjudication'}
            if asset['status'] != 'ready': return record
            async with slots[replica]:
                pixels = asset_pixels(work, asset)
                async def call(task, messages, validate):
                    # Multimodal tokenizer includes visual tokens; enforce context before generation.
                    count = await replay.token_count(client, messages)
                    cap = config['vision_evaluation']['max_tokens']
                    if count + cap > config['max_model_len']: raise ValueError('Visual input exceeds context budget')
                    response = await client.complete_once(endpoints[replica], task, messages, cap)
                    result = {'messages_sha256': json_digest(messages), 'response': response.response(),
                              'metrics': response.metrics(), 'status': 'failed', 'data': None}
                    try:
                        if response.error or response.finish_reason not in {'stop','eos'}:
                            raise ValueError(response.error or 'incomplete_finish:' + str(response.finish_reason))
                        result['data'] = validate(parse_output(response.content).value)
                        result['status'] = 'valid'
                    except (ValueError, TypeError, KeyError) as exc: result['error'] = str(exc)
                    return result
                from .figure_attribution import validate_figure_review
                validator = lambda value: validate_figure_review(value, article, roster, asset['figure_id'])
                record['caption_attribution'] = await call('figure_attribution',
                    figure_messages(article, roster, asset['figure_id'], focused=False), validator)
                record['pixel_attribution'] = await call('figure_attribution',
                    figure_messages(article, roster, asset['figure_id'], focused=False, pixels=pixels), validator)
                # Independent visual pass cannot see the generated ownership hypotheses.
                record['description'] = await call('vision',
                    visual_messages(article, roster, asset['figure_id'], pixels),
                    lambda value: validate_visuals(value, article, asset['figure_id']))
                record['joint_analysis'] = await call('figure_analysis',
                    visual_messages(article, roster, asset['figure_id'], pixels, joint=True),
                    lambda value: validate_joint(value, article, roster, asset['figure_id']))
                record['status'] = 'valid' if all(record[k]['status'] == 'valid' for k in
                    ('caption_attribution','pixel_attribution','description')) else 'partial'
                # Store source text/provenance and results, never base64 request copies.
                write_json(output / arm_name / 'figures' / (json_digest([asset['article_id'],asset['figure_id']])+'.json'), record)
                return record
        try:
            async def safe_figure(index, asset):
                try: return await figure(index, asset)
                except Exception as exc:
                    failure = {**asset, 'arm':arm_name, 'status':'failed', 'error_type':type(exc).__name__,
                        'error':str(exc), 'semantic_review':'pending'}
                    write_json(output / arm_name / 'figures' / (json_digest([asset['article_id'],asset['figure_id']])+'.json'), failure)
                    return failure
            rows = await asyncio.gather(*(safe_figure(i, row) for i,row in enumerate(manifest['figures'])))
            all_rows.extend(rows)
            for rid in packets:
                aid, pid = rid.rsplit(':',1); article = articles[aid]
                roster = json.loads((Path(campaign['root']) / config['fixtures'] / 'rosters' / (aid+'.json')).read_text())['roster']
                reviews = [bind_figure_review(r['pixel_attribution']['data'], article, roster,
                    method='caption_full_case_text_and_pixels', pixel_provenance=r['pixel_provenance'])
                    for r in rows if r['article_id'] == aid and r.get('pixel_attribution',{}).get('status') == 'valid']
                assignments, media = patient_media(article, roster, pid, reviews)
                annotations = []
                for r in rows:
                    if r['article_id'] != aid or r.get('description',{}).get('status') != 'valid': continue
                    ownership = r.get('pixel_attribution',{}).get('data')
                    if ownership is None: continue
                    selected = [panel for panel in r['description']['data']['panels'] if any(
                        pid in a['patient_ids'] and (a['panel'] is None or a['panel'] == panel['panel'])
                        for a in ownership['assignments'])]
                    if selected: annotations.append({'figure_id':r['figure_id'], 'pixel_provenance':r['pixel_provenance'],
                        'general_description':r['description']['data']['general_description'],
                        'detailed_description':r['description']['data']['detailed_description'],
                        'description_scope':'whole_figure_context; only selected_panels attributed to this patient',
                        'selected_panels':selected,
                        'limitations':r['description']['data']['limitations'], 'clinical_fact_status':'unreviewed_visual_annotation'})
                write_json(output / arm_name / 'patient-media' / (rid.replace(':','-')+'.json'),
                    {'record_id':rid, 'source_license':article['license'], 'figure_assignments':assignments,
                     'multimedia':media, 'visual_annotations':annotations,
                     'attribution_roster':'frozen_reference_candidate_ids; not a discovery accuracy test',
                     'pixels_inspected':media['pixels_inspected'], 'semantic_review':'pending'})
        finally: await replay.close()
    tasks = ('caption_attribution','pixel_attribution','description','joint_analysis')
    metrics = [r[k]['metrics'] for r in all_rows for k in tasks if k in r]
    total = sum(m['completion_tokens'] for m in metrics) if metrics and all(type(m.get('completion_tokens')) is int for m in metrics) else None
    wall = time.monotonic()-started
    usable = any(r.get('pixel_attribution',{}).get('status') == 'valid' and
                 r.get('description',{}).get('status') == 'valid' for r in all_rows)
    comparable = [r for r in all_rows if r.get('pixel_attribution',{}).get('data') and
                  r.get('joint_analysis',{}).get('data')]
    def assignment_key(review):
        return sorted((str(a['panel']),tuple(sorted(a['patient_ids'])),a['scope'],a['subject']) for a in review['assignments'])
    agreement = sum(assignment_key(r['pixel_attribution']['data']) ==
                    assignment_key(r['joint_analysis']['data']['attribution']) for r in comparable)
    result = {'status':'completed' if usable else 'failed', 'figures_per_seed':len(manifest['figures']), 'seeds':len(config['arms']),
        'rows':len(all_rows), 'asset_status_counts':manifest['status_counts'],
        'task_status_counts':{k:dict(Counter(r[k]['status'] for r in all_rows if k in r)) for k in tasks},
        'wall_seconds':wall, 'output_tokens':total, 'aggregate_output_tokens_per_second':total/wall if total is not None else None,
        'joint_separate_attribution_agreement':{'comparable':len(comparable),'agree':agreement,
            'notice':'Agreement on panel/patient/scope/subject is not attribution accuracy.'},
        'semantic_review':'pending; schema/citation validity does not establish pixel accuracy or patient ownership'}
    write_json(output / 'report.json',result)
    columns = []
    for r in all_rows:
        for method in ('separate','joint'):
            visual = r.get('description',{}).get('data') if method == 'separate' else (r.get('joint_analysis',{}).get('data') or {}).get('visual')
            ownership = r.get('pixel_attribution',{}).get('data') if method == 'separate' else (r.get('joint_analysis',{}).get('data') or {}).get('attribution')
            if visual is None: continue
            for row in panel_columns(r['article_id'],visual):
                assignments = [a for a in (ownership or {}).get('assignments',[]) if a['panel'] is None or a['panel'] == row['panel']]
                row.update(arm=r['arm'], analysis_method=method, source_license=r['source_license'],
                    image_url=r['pixel_provenance']['url'], pixel_sha256=r['pixel_provenance']['sha256'],
                    patient_ids=sorted({pid for a in assignments for pid in a['patient_ids']}),
                    patient_assignments=assignments, attribution_status='source_validated_unreviewed' if ownership else 'unresolved')
                columns.append(row)
    with (output / 'figure-panels.jsonl').open('w') as handle:
        for row in columns: handle.write(json.dumps(row,ensure_ascii=False)+'\n')
    if columns:
        import csv
        with (output / 'figure-panels.csv').open('w',newline='') as handle:
            writer = csv.DictWriter(handle,fieldnames=list(columns[0]));writer.writeheader()
            for row in columns: writer.writerow({k:json.dumps(v,ensure_ascii=False) if isinstance(v,(list,dict)) else v for k,v in row.items()})
    write_json(output / 'source-pixel-review-pending.json', {'reviews':[{'article_id':r['article_id'],
        'figure_id':r['figure_id'], 'arm':r['arm'], 'pixel_provenance':r.get('pixel_provenance'),
        'verdict':None, 'separate_description_verdict':None, 'separate_attribution_verdict':None,
        'joint_description_verdict':None, 'joint_attribution_verdict':None,
        'rubric':['visible panel coverage','modality/submodality/domain/body part','legible labels and values',
            'pixel/caption separation','local/shared/external ownership','uncertainty and unreadability']} for r in all_rows]})
    return result


def integrate_bundles(root, quality, vision_results):
    """Preserve scored originals; add matched-seed visual sidecars to review bundles."""
    for label in quality:
        seed_arm = next((a for a in ('medium_seed42','medium_seed1729','medium_seed5724') if label.endswith(a)), None)
        if seed_arm is None: continue
        method = 'dflash' if vision_results.get('dflash',{}).get('status') == 'completed' else 'ordinary'
        for source in (root / label).glob('PMC*-p*.json'):
            media_path = root / 'vision' / method / seed_arm / 'patient-media' / source.name
            if not media_path.exists(): continue
            patient = json.loads(source.read_text()); media = json.loads(media_path.read_text())
            patient['source']['figure_assignments'] = media['figure_assignments']
            patient['source']['multimedia'] = media['multimedia']
            patient['vision'] = {'status':'figures_inspected_unreviewed' if media['pixels_inspected'] else 'no_attributed_pixel_annotation',
                'pixels_inspected':media['pixels_inspected'], 'visual_annotations':media['visual_annotations'],
                'attribution_roster':media['attribution_roster'], 'source_media_bundle':str(media_path.relative_to(root)),
                'clinical_fact_status':'unreviewed_visual_annotations_not_promoted_into_clinical_fields'}
            patient['figure_attribution'] = {'method':'caption_full_case_text_and_pixels', 'semantic_review':'pending'}
            write_json(root / 'integrated-patients' / label / source.name, patient)
