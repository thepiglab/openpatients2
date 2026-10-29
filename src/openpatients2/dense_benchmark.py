"""Frozen completed-article benchmark: CPU setup -> independent B200s -> CPU scoring.

Only prepare/worker/aggregate need the project dependencies. Submission, status,
verification and export also work with a plain Python 3.11+ on a login node.
No reviewed roster, facts, sidecar labels or supplement transcriptions enter inference.
"""
from __future__ import annotations

import argparse
import asyncio
from collections import Counter
import csv
import fcntl
import gzip
import hashlib
import itertools
import json
import math
import os
from pathlib import Path
import re
import shutil
import signal
import socket
import statistics
import subprocess
import tarfile
import time

MODEL = 'RedHatAI/Muse-Glimmer-30B-FP8-block'
REVISION = '1deb4641ff84f9a728dd11b27cac1f6a02a9ed14'
DRAFTER = 'meta-models/Muse-Glimmer-30B-assistant'
DRAFT_REVISION = 'e8192f3a8f617f74be2ce220360c89ef4789f39f'
IMAGE = 'docker://vllm/vllm-openai:v0.30.0'
TEMPLATE_SHA = 'cfc67e5f349f37690dfd31ed1f18bc4442a9dd32fe39a648f993cb4eb3cae678'
WORKERS = 4
CONTEXT = 65536
SCOPE = {
    'clinical': 'live discovery; original hardened pipeline; whole article; targeted source-aware repair',
    'figures': 'captions and all eligible cached original pixels; staged panel attribution',
    'ehr_stages': ['figure_ehr_label', 'figure_privacy_flags', 'measurement_series_extraction'],
    'unsupported': ['supplement classification', 'supplement clinical extraction'],
    'reference_use': 'selection and scoring only; no reference rosters, facts or transcripts in prompts',
    'interpretation': 'Development-set annotation agreement, not blinded or physician-verified accuracy.',
}


def read(path):
    return json.loads(Path(path).read_text())


def write(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + '.tmp')
    temp.write_text(json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False) + '\n')
    temp.replace(path)


def sha(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as f:
        for block in iter(lambda: f.read(4 * 1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def rows(path):
    found = {}
    for line in Path(path).read_text().splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        aid = row['article_id']
        if aid in found or not re.fullmatch(r'PMC\d+\.\d+', aid):
            raise ValueError(f'Duplicate/invalid article ID: {aid}')
        found[aid] = row
    return found


def write_rows(path, values):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(''.join(json.dumps(v, ensure_ascii=False) + '\n' for v in values))


def extraction_config():
    from .pilot_extract import PilotConfig
    # Serving settings follow the measured clinical recipe. These are generous
    # per-article safety ceilings, never counts read from the annotated roster.
    return PilotConfig(max_articles=1, max_patients_per_article=48, max_patients=48,
                       max_figures_per_article=48, max_figures=12, max_calls=8192,
                       max_total_tokens=128_000_000, max_repair_rounds=2,
                       timeout_seconds=600, isolate_secondary_cases=True,
                       focused_pixels=True, pixel_strategy='staged').model_dump()


def inventory(root, folders):
    return {str(p.relative_to(root)): sha(p) for folder in folders
            for p in sorted((root / folder).rglob('*')) if p.is_file()
            and '__pycache__' not in p.parts and '.venv' not in p.parts
            and str(p.relative_to(root)) != 'summary/integrity.json'
            and not p.name.endswith(('.pyc', '.pyo'))}


def check_snapshot(work):
    m = read(work / 'manifest.json')
    for name, digest in m['files'].items():
        if sha(work / name) != digest:
            raise ValueError(f'Frozen input/code changed: {name}')
    return m


def freeze_media(source, article_dir, out, download=False, max_figures=12):
    from .pilot_media import _mime, load_asset
    from .license_policy import recheck_license
    raw_path = article_dir / 'figure_assets.json'
    raw = read(raw_path) if raw_path.exists() else {'figures': []}
    if raw.get('source_text_sha256', source['text_sha256']) != source['text_sha256']:
        raise ValueError(f'Stale image manifest: {source["article_id"]}')
    by_id = {f['figure_id']: f for f in raw['figures']}
    result = []
    if not 1 <= max_figures <= 48 or len(source['figures']) > max_figures:
        raise ValueError(f'More than {max_figures} figures requires an explicit campaign design change; refusing truncation')
    for figure in source['figures']:
        row = {'article_id': source['article_id'], 'figure_id': figure['figure_key'],
               'source_license': source['license']}
        result.append(row)
        if not figure.get('image_urls'):
            row['status'] = 'missing_image'
            continue
        if (figure.get('rights_statements') or figure.get('reuse_status') == 'asset_rights_review'
                or figure.get('fixture_asset_rights_review') == 'required_before_reuse'):
            row['status'] = 'asset_rights_review'
            continue
        candidates = by_id.get(figure['figure_key'], {}).get('assets', [])
        asset = next((a for a in candidates if a['url'] in figure['image_urls'] and not a.get('rights_statements')
                      and a.get('reuse_status') != 'asset_rights_review'
                      and recheck_license(a.get('source_license', source['license'])).get('allowed')), None)
        if asset is None:
            row['status'] = 'cache_unavailable'
            continue
        path = Path(asset['path'])
        if not path.exists():
            # Cached manifests can outlive the Mac checkout location.
            path = article_dir.parents[3] / 'data/.dense-figure-cache' / source['article_id'] / path.name
        name = 'vision-assets/' + asset['sha256'] + '.image'
        target = out / name
        target.parent.mkdir(parents=True, exist_ok=True)
        if not path.is_file() and download:
            # CPU preparation only. Fetch the exact source-listed asset and
            # require the reviewed acquisition hash; never rewrite its manifest.
            import httpx
            data = bytearray()
            with httpx.stream('GET', asset['url'], timeout=60, follow_redirects=False,
                              headers={'Accept-Encoding': 'identity'}) as response:
                response.raise_for_status()
                if response.headers.get('content-encoding', 'identity').lower() != 'identity':
                    raise ValueError('Unexpected compressed image response')
                for block in response.iter_raw():
                    data.extend(block)
                    if len(data) > 8_000_000:
                        raise ValueError('Image exceeds byte cap')
            if len(data) != asset['bytes'] or hashlib.sha256(data).hexdigest() != asset['sha256']:
                raise ValueError(f'Downloaded image differs from frozen acquisition: {asset["url"]}')
            target.write_bytes(data)
        else:
            if not path.is_file() or sha(path) != asset['sha256'] or path.stat().st_size != asset['bytes']:
                raise ValueError(f'Missing/corrupt cached asset: {path}; use --download-media on a CPU node')
            shutil.copyfile(path, target)
        row.update(status='ready', file=name, pixel_provenance={
            'sha256': asset['sha256'], 'bytes': asset['bytes'], 'mime_type': _mime(target.read_bytes()),
            'url': asset['url'], 'persisted_pixels': True})
        load_asset(out, row)
    write(out / 'vision-assets/manifest.json', {'figures': result, 'complete': True})
    return Counter(r['status'] for r in result)


def prepare(root, work, expected, download_media=False, *, splits=None, make_archive=True, article_selector=None,
            freeze_images=True, reference_path=None, figure_limit=12):
    from .pilot_extract import PilotConfig, source_gate
    root, work = root.resolve(), work.resolve()
    if work.exists():
        raise ValueError('Use a new output directory; frozen campaigns are never overwritten')
    release = root / 'data/dense_clinical_cases_v1/data'
    frozen = root / 'configs/hipergator/dense-benchmark/reference.jsonl.gz'
    sources = []
    if reference_path is not None:
        reference_path = Path(reference_path)
        with gzip.open(reference_path, 'rt') as f:
            sources.append(('explicit_snapshot', [json.loads(line) for line in f if line.strip()]))
    elif all((release / f'{s}.jsonl').exists() for s in ('train', 'validation', 'test')):
        sources.append(('release', [row for split in ('train', 'validation', 'test')
                                    for row in rows(release / f'{split}.jsonl').values()]))
    if reference_path is None and frozen.exists():
        with gzip.open(frozen, 'rt') as f:
            sources.append(('frozen', [json.loads(line) for line in f if line.strip()]))
    if not sources:
        raise ValueError('Neither completed release nor tracked frozen reference exists')
    # The corpus keeps growing, so the local release can outgrow a campaign; the tracked frozen packet
    # is what an `expected` count (e.g. the 260-article campaigns) was frozen on. Use the first source
    # whose completed count matches, release first.
    found = {}
    for name, reference_rows in sources:
        refs = {}
        for row in reference_rows:
            aid = row['article_id']
            if not row.get('completion', {}).get('completed'):
                continue
            if aid in refs or row['split'] not in ('train', 'validation', 'test') or not all(row['completion']['checks'].values()):
                raise ValueError(f'Duplicate or inconsistent completed reference: {aid}')
            refs[aid] = row
        found[name] = len(refs)
        if len(refs) == expected or (expected is None and reference_path is not None and refs):
            break
    else:
        counts = ', '.join(f'{n} {c}' for n, c in found.items())
        raise ValueError(f'Expected {expected} completed articles, found {counts}; inspect before changing --expected')
    reference_source, completed_reference_count = name, len(refs)
    if splits is not None:
        if not set(splits) <= {'train', 'validation', 'test'} or not splits:
            raise ValueError('Invalid split selection')
        refs = {k: v for k, v in refs.items() if v['split'] in splits}
    if article_selector is not None:
        # Selection may use corpus categories and difficulty metadata. This stays
        # in CPU preparation; reference records never enter inference prompts.
        selected = set(article_selector(list(refs.values())))
        if not selected or not selected <= set(refs):
            raise ValueError('Invalid article selection')
        refs = {k: v for k, v in refs.items() if k in selected}
    work.mkdir(parents=True)
    code = work / 'code'
    code.mkdir()
    for name in ('src', 'scripts', 'configs'):
        shutil.copytree(root / name, code / name, ignore=shutil.ignore_patterns('__pycache__', '*.pyc', '*.egg-info'))
    for name in ('pyproject.toml', 'uv.lock', 'README.md', 'LICENSE'):
        shutil.copyfile(root / name, code / name)
    (code / 'docs').mkdir()
    shutil.copyfile(root / 'docs/DENSE260_HIPERGATOR_BENCHMARK.md', code / 'docs/DENSE260_HIPERGATOR_BENCHMARK.md')
    if sha(code / 'configs/hipergator/dense-benchmark/chat_template.jinja') != TEMPLATE_SHA:
        raise ValueError('Official pinned chat template changed')
    config = extraction_config()
    items, media = [], Counter()
    for aid, ref in sorted(refs.items()):
        paths = [p for p in root.glob(f'benchmarks/dense*/articles/{aid}/source.json')]
        if len(paths) != 1:
            raise ValueError(f'Expected exactly one source for {aid}')
        source = read(paths[0])
        errors = source_gate(source, PilotConfig.model_validate(config))
        if errors or ref['text_sha256'] != source['text_sha256']:
            raise ValueError(f'Source gate failed: {aid}: {errors}')
        out = work / 'inputs' / aid
        write_rows(out / 'source.jsonl', [source])
        if freeze_images:
            media.update(freeze_media(source, paths[0].parent, out, download=download_media, max_figures=figure_limit))
        items.append({'article_id': aid, 'split': ref['split'], 'source_characters': len(source['text']),
                      'figures': len(source['figures']), 'source_text_sha256': source['text_sha256']})
    # Greedy work assignment uses SOURCE size and image count, never annotated
    # patient/fact counts. One article at a time per GPU makes latency attributable.
    loads = [0] * WORKERS
    for item in sorted(items, key=lambda r: (-r['source_characters'] - 10000*r['figures'], r['article_id'])):
        shard = min(range(WORKERS), key=lambda i: (loads[i], i))
        item['shard'] = shard
        loads[shard] += item['source_characters'] + 10000 * item['figures']
    write_rows(work / 'reference/unified.jsonl', [refs[k] for k in sorted(refs)])
    git = subprocess.run(['git', '-C', str(root), 'rev-parse', 'HEAD'], text=True, capture_output=True)
    manifest = {'format': 'dense-local-benchmark/1', 'created_unix': time.time(),
                'git_head': git.stdout.strip(), 'code_identity': 'file hashes capture working-tree code, including uncommitted changes',
                'articles': items, 'article_count': len(items), 'splits': dict(Counter(r['split'] for r in items)),
                'media': dict(media), 'workers': WORKERS, 'context': CONTEXT, 'extraction': config, 'scope': SCOPE,
                'model': {'id': MODEL, 'revision': REVISION}, 'drafter': {'id': DRAFTER, 'revision': DRAFT_REVISION},
                'container': IMAGE, 'template_sha256': TEMPLATE_SHA,
                'files': inventory(work, ['code', 'inputs', 'reference'])}
    manifest['reference_selection'] = {'source': reference_source, 'completed_articles_before_selection': completed_reference_count,
        'requested_expected': expected,
        'snapshot_sha256': sha(reference_path) if reference_path is not None else None}
    write(work / 'manifest.json', manifest)
    # A portable input archive: never include raw checkout, secrets, venv, models.
    archive = work.with_suffix('.inputs.tar.gz')
    if make_archive:
        with tarfile.open(archive, 'w:gz') as tf:
            for name in ('manifest.json', 'code', 'inputs', 'reference'):
                tf.add(work / name, arcname=work.name + '/' + name)
        checksum(archive)
    return {'work': str(work), 'archive': str(archive), 'articles': len(items), 'media': dict(media),
            'split_counts': manifest['splits'], 'workers': dict(Counter(x['shard'] for x in items))}


def checksum(path):
    path.with_name(path.name + '.sha256').write_text(sha(path) + '  ' + path.name + '\n')


def setup(work):
    check_snapshot(work)
    from huggingface_hub import snapshot_download
    runtime = work / 'runtime'
    runtime.mkdir(exist_ok=True)
    # Cache and partial downloads are CPU-only; the API resolves exact revisions.
    for folder, model, revision in (('weights', MODEL, REVISION), ('drafter', DRAFTER, DRAFT_REVISION)):
        dest = runtime / folder
        snapshot_download(repo_id=model, revision=revision, local_dir=dest)
        write(dest / 'op2_snapshot.json', {'model_id': model, 'revision': revision})
    sif = runtime / 'vllm-0.30.0.sif'
    if not sif.exists():
        subprocess.run(['apptainer', 'pull', str(sif), IMAGE], check=True)
    # Hash large artifacts on CPU once, then require unchanged sizes/mtimes on GPU.
    files = {}
    for p in [sif] + [p for d in ('weights', 'drafter') for p in (runtime / d).rglob('*')
                       if p.is_file() and '.cache' not in p.parts]:
        files[str(p.relative_to(work))] = {'sha256': sha(p), 'bytes': p.stat().st_size,
                                        'mtime_ns': p.stat().st_mtime_ns}
    write(work / 'runtime.json', {'ready': True, 'files': files, 'image': IMAGE,
                                'model_revision': REVISION, 'drafter_revision': DRAFT_REVISION})
    return {'ready': True, 'runtime_files': len(files)}


def serving(work, shard, logdir):
    from .dense_benchmark_serving import ServingConfig
    cache = work / f'runtime/worker-{shard}'
    cache.mkdir(parents=True, exist_ok=True)
    base = work / 'runtime'
    return ServingConfig(name='dense-fp8-dflash', backend='vllm', version='0.30.0', image_reference=IMAGE,
        sif=str(base / 'vllm-0.30.0.sif'), model_path=str(base / 'weights'), model_id=MODEL,
        container_python='/usr/bin/python3', replicas=1, tensor_parallel=1, expert_parallel=False,
        reasoning_parser='muse_glimmer', max_model_len=CONTEXT, max_num_seqs=64, max_batched_tokens=32768,
        gpu_memory_utilization=.9, port=8600+shard, rpc_port=18000+100*shard,
        speculative_config={'model': str(base / 'drafter'), 'method': 'dflash',
                            'num_speculative_tokens': 15, 'draft_sample_method': 'probabilistic'},
        bind_files={str(base / k): str(base / k) for k in ('weights', 'drafter')},
        extra_args=['--model-impl', 'vllm', '--chat-template-content-format', 'string',
                    '--generation-config', 'vllm', '--chat-template',
                    str(work / 'code/configs/hipergator/dense-benchmark/chat_template.jinja'),
                    '--enable-auto-tool-choice', '--tool-call-parser', 'muse_glimmer',
                    '--compilation-config', '{"cudagraph_mode":"PIECEWISE"}',
                    '--limit-mm-per-prompt', '{"image":1,"video":0}'],
        environment={'PYTHONDONTWRITEBYTECODE': '1', 'VLLM_WORKER_MULTIPROC_METHOD': 'spawn',
                     'HF_HOME': str(cache / 'hf'), 'HF_HUB_CACHE': str(cache / 'hf/hub'),
                     'HF_XET_CACHE': str(cache / 'hf/xet'), 'HF_MODULES_CACHE': str(cache / 'hf/modules'),
                     'VLLM_CACHE_ROOT': str(cache / 'vllm'), 'TRITON_CACHE_DIR': str(cache / 'triton'),
                     'TORCHINDUCTOR_CACHE_DIR': str(cache / 'inductor'), 'XDG_CACHE_HOME': str(cache / 'xdg')})


def ledger_metrics(paths):
    metrics = {'model_calls': 0, 'failed_attempts': 0}
    by_task = {}
    reasoning_statuses = Counter()
    finish_reasons = Counter()
    for k in ('prompt_tokens', 'completion_tokens', 'reasoning_tokens'):
        metrics[k] = 0
        metrics['unknown_' + k] = 0
    for path in paths:
        if not path.exists():
            continue
        for line in path.read_text().splitlines():
            entry = json.loads(line)
            response = entry.get('response')
            if entry.get('errors'):
                metrics['failed_attempts'] += 1
            if response is None:
                continue
            metrics['model_calls'] += 1
            task = by_task.setdefault(entry.get('task', 'unknown_legacy'), {'model_calls':0, 'length_limited_calls':0,
                'out_of_batch_candidates':0, 'prompt_tokens':0, 'completion_tokens':0, 'reasoning_tokens':0})
            task['model_calls'] += 1
            task['length_limited_calls'] += entry.get('metrics', {}).get('finish_reason') == 'length'
            task['out_of_batch_candidates'] += len(entry.get('scope_routing', []))
            finish_reasons[entry.get('metrics', {}).get('finish_reason') or 'unknown'] += 1
            reasoning_statuses[response.get('reasoning_status', 'unknown_legacy')] += 1
            if response.get('error') and not entry.get('errors'):
                metrics['failed_attempts'] += 1
            for k in ('prompt_tokens', 'completion_tokens', 'reasoning_tokens'):
                value = entry.get('metrics', {}).get(k)
                task[k] = task[k] + value if task[k] is not None and value is not None else None
                if value is None:
                    metrics['unknown_' + k] += 1
                else:
                    metrics[k] += value
    for k in ('prompt_tokens', 'completion_tokens', 'reasoning_tokens'):
        metrics['reported_' + k] = metrics[k]
        if metrics['unknown_' + k]:
            metrics[k] = None
    metrics['reasoning_statuses'] = dict(reasoning_statuses)
    metrics['finish_reasons'] = dict(finish_reasons)
    metrics['task_usage'] = by_task
    return metrics


def infrastructure_failures(attempt):
    """Terminal task transport failures; recovered retries are not failures.

    Inspect task results rather than every ledger entry, so a successful retry
    does not invalidate an otherwise completed article. Keep all raw outputs.
    """
    failures = []
    transient = {'ReadTimeout', 'ConnectTimeout', 'ConnectError', 'ReadError',
                 'RemoteProtocolError', 'TimeoutError', 'total_request_deadline'}
    for folder in ('extraction', 'ehr'):
        for path in (attempt / folder).rglob('tasks/*.json'):
            task = read(path)
            if task.get('status') == 'valid':
                continue
            for entry in task.get('attempts', [])[-1:]:
                response = entry.get('response') or {}
                status = response.get('http_status')
                error = response.get('error')
                if (entry.get('metrics', {}).get('transport_error') or
                        status in {408, 429} or isinstance(status, int) and status >= 500 or
                        error in transient or error in {'http_408', 'http_429'} or
                        isinstance(error, str) and error.startswith('http_5')):
                    failures.append({'task': task.get('task'), 'file': str(path.relative_to(attempt)),
                                     'error': error or 'transport_error'})
    return failures


async def run_article(work, item, endpoint, attempt):
    from .pilot_extract import PilotRunner
    from .ehr_pipeline import run_ehr_stages
    class ArticleRunner(PilotRunner):
        # The measured vision workload used c2. Keep the text pipeline at c64.
        async def visual(self, *args, **kwargs):
            async with self.vision_slots:
                return await super().visual(*args, **kwargs)
    aid = item['article_id']
    source = work / 'inputs' / aid / 'source.jsonl'
    media = source.parent / 'vision-assets/manifest.json'
    manifest = read(work / 'manifest.json')
    cfg = manifest['extraction']
    text_only = manifest.get('text_only', False)
    if text_only:
        media = None
    start, wall = time.monotonic(), time.time()
    receipt = {'article_id': aid, 'shard': item['shard'], 'split': item['split'], 'started_unix': wall,
               'status': 'error', 'attempt': attempt.name, 'gpus_per_server': cfg.get('gpus_per_endpoint', 1),
               'timing_basis': 'single active article per server; includes source/token validation and repairs; excludes server startup and queue'}
    runner = None
    spec_before = None
    if manifest.get('capture_speculation_metrics'):
        from .speculation_metrics import snapshot
        spec_before = await snapshot(endpoint)
    try:
        context = manifest.get('context', CONTEXT)
        runner = ArticleRunner(cfg, attempt / 'extraction', [endpoint], context, 'targeted')
        runner.vision_slots = asyncio.Semaphore(min(2, cfg.get('concurrency_per_endpoint', 64)))
        report = await runner.run(source, image_manifest=media)
        if infrastructure_failures(attempt):
            raise RuntimeError('Extraction backend failed; article must be retried')
        receipt['extraction_seconds'] = time.monotonic() - start
        receipt['task_statuses'] = report['task_statuses']
        receipt['patients_extracted'] = report['patients_extracted']
        if report['selected_articles'] != 1 or report['eligible_articles'] != 1:
            raise ValueError('Article was not selected/eligible; refusing silent partial coverage')
        if any(a.get('omitted_patient_ids_due_to_cap') for a in report['articles']):
            raise ValueError('Discovered patient cap exceeded; do not silently benchmark a truncated roster')
        side_start = time.monotonic()
        ehr = await run_ehr_stages({'figure_ehr_label': not text_only, 'figure_privacy_flags': not text_only,
                'measurement_series_extraction': True, 'extraction_config': {**cfg, 'concurrency_per_endpoint': min(2, cfg.get('concurrency_per_endpoint', 64))},
                'default_split': item['split']}, source, attempt / 'extraction', attempt / 'ehr',
                [endpoint], context, image_manifest=media)
        receipt['ehr_seconds'] = time.monotonic() - side_start
        receipt['ehr_errors'] = ehr['articles'][0]['errors']
        predictions = rows(attempt / 'ehr/data/unified.jsonl')
        if set(predictions) - {aid}:
            raise ValueError('Unexpected prediction identity')
        # An invalid roster legitimately produces NO prediction and scores as a
        # full miss. This differs from an interrupted operational attempt.
        receipt.update(status='done', has_prediction=aid in predictions,
                       prediction_sha256=sha(attempt / 'ehr/data/unified.jsonl'),
                       all_extraction_tasks_valid=report['valid_tasks'] == report['task_count'])
        from .benchmark_reporting import nonempty_valid
        receipt['nonempty_pipeline_valid'] = nonempty_valid(receipt)
    except Exception as exc:
        receipt.update(error_type=type(exc).__name__, error=str(exc)[:2000])
    finally:
        if runner:
            await runner.close()
        receipt.update(wall_seconds=time.monotonic() - start, ended_unix=time.time())
        if spec_before is not None:
            from .speculation_metrics import snapshot, delta
            receipt['speculation'] = delta(spec_before, await snapshot(endpoint))
        receipt.update(ledger_metrics([attempt / 'extraction/attempts.jsonl', attempt / 'ehr/calls/attempts.jsonl']))
        failures = infrastructure_failures(attempt)
        if failures:
            receipt.update(status='error', error_type='InfrastructureError', retryable=True,
                           infrastructure_failures=failures,
                           error='Backend request failed; retained outputs are incomplete, retry this article')
        write(attempt / 'receipt.json', receipt)
    return receipt


def completed_attempt(work, aid):
    candidates = sorted((work / 'results/articles' / aid).glob('attempt-*/receipt.json'))
    for p in candidates:
        r = read(p)
        if r['status'] == 'done':
            if r['article_id'] != aid or sha(p.parent / 'ehr/data/unified.jsonl') != r['prediction_sha256']:
                raise ValueError(f'Completed result changed: {aid}')
            return p.parent, r
    return None, None


def paired_jobs(work, campaigns, shard):
    """Claim whole article pairs on shared storage; fast workers take more work.

    Atomic mkdir works across nodes without relying on flock mount options.
    Claims span both arms and are released on generator close. A hard kill can
    leave a claim; explicit resume uses a fresh submission namespace only after
    all previous jobs are terminal.
    Skip busy pairs rather than keeping a GPU allocated waiting for another job.
    A failed/incomplete arm is retried only by an explicit later resume.
    """
    epoch = os.environ.get('OP2_SUBMISSION_ID', 'local')
    if not re.fullmatch(r'[A-Za-z0-9_-]+', epoch):
        raise ValueError('Invalid queue submission identity')
    claims = work / 'runtime/article-claims' / epoch
    claims.mkdir(parents=True, exist_ok=True)
    items = read(work / 'manifest.json')['articles']
    for item in sorted(items, key=lambda x: (-x.get('source_characters', 0) - 10000*x.get('figures', 0), x['article_id'])):
        aid = item['article_id']
        claim = claims / aid
        attempted = claims / (aid + '.attempted')
        if attempted.exists():
            continue
        try:
            claim.mkdir()
        except FileExistsError:
            continue
        try:
            if attempted.exists():
                continue
            rank = int(hashlib.sha256(aid.encode()).hexdigest(), 16)
            if len(campaigns) == 2:  # preserve the established paired experiment
                order = campaigns if rank % 2 else list(reversed(campaigns))
            else:
                offset = rank % len(campaigns)
                order = campaigns[offset:] + campaigns[:offset]
                if (rank // len(campaigns)) % 2:
                    order = list(reversed(order))
            for campaign in order:
                if not completed_attempt(campaign, aid)[0]:
                    yield campaign, {**item, 'shard': shard}
            # Do not let another worker reroll operational errors from a pair
            # already attempted in this submission. Resume gets a fresh epoch.
            attempted.touch()
        finally:
            claim.rmdir()


async def worker(work, shard, *, campaigns=None, budget_seconds=None, article_timeout=None, workload=None,
                 serving_factory=None, runtime_probe=None, startup_check=None):
    from .dense_benchmark_serving import ServerGroup
    from .dense_benchmark_telemetry import monitor
    m = check_snapshot(work)
    if shard not in range(WORKERS):
        raise ValueError('Invalid worker shard')
    if not os.environ.get('SLURM_JOB_ID') or not os.environ.get('CUDA_VISIBLE_DEVICES'):
        raise ValueError('Worker requires an allocated Slurm GPU; never runs locally/login node')
    if campaigns is not None and not os.environ.get('OP2_SUBMISSION_ID'):
        raise ValueError('Paired workers require the submission queue identity from the launcher')
    rt = read(work / 'runtime.json')
    for name, info in rt['files'].items():
        stat = (work / name).stat()
        if stat.st_size != info['bytes'] or stat.st_mtime_ns != info['mtime_ns']:
            raise ValueError(f'CPU-prepared artifact changed: {name}')
    logs = work / f'results/workers/{shard}/job-{os.environ["SLURM_JOB_ID"]}'
    logs.mkdir(parents=True, exist_ok=False)
    lock = (work / f'runtime/worker-{shard}.lock').open('a')
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    claimed = None
    if campaigns is None:
        pending = [(work, x) for x in m['articles']
                   if x['shard'] == shard and not completed_attempt(work, x['article_id'])[0]]
    else:
        for campaign in campaigns:
            check_snapshot(campaign)
        claimed = paired_jobs(work, campaigns, shard)
        pending = claimed
    cfg = (serving_factory or serving)(work, shard, logs)
    # Independent Slurm allocations may share a host with another campaign.
    # Choose a free loopback port rather than assuming 8600 is unused.
    for _ in range(100):
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', 0))
            port = sock.getsockname()[1]
        if 1024 <= port <= 60000:
            cfg.port = port
            break
    else:
        raise RuntimeError('Could not select an available loopback port')
    group = ServerGroup(cfg, str(work), str(logs / 'server'))
    started = time.time()
    monotonic_start = time.monotonic()
    summary = {'shard': shard, 'job_id': os.environ['SLURM_JOB_ID'], 'started_unix': started,
               'gpus_per_server': cfg.gpus,
               'articles': [], 'status': 'interrupted'}
    try:
        if claimed is not None:
            first = next(claimed, None)
            pending = itertools.chain([first], claimed) if first else []
        if not pending and workload is None:
            summary['status'] = 'done'
            return summary
        probe = runtime_probe or ('import torch,vllm; from vllm.model_executor.models import ModelRegistry; '
                 'from vllm.reasoning import ReasoningParserManager; '
                 'assert vllm.__version__ == "0.30.0"; '
                 'assert "MuseGlimmerForConditionalGeneration" in ModelRegistry.get_supported_archs(); '
                 'ReasoningParserManager.get_reasoning_parser("muse_glimmer"); '
                 'assert torch.cuda.device_count()==1; assert torch.cuda.get_device_capability(0)==(10,0); '
                 'print(vllm.__version__,torch.__version__,torch.cuda.get_device_name(0))')
        argv = group.commands[0]['argv']
        prefix = argv[:argv.index(group.config.sif)+1]
        result = subprocess.run(prefix + ['/usr/bin/python3', '-c', probe], text=True, capture_output=True)
        write(logs / 'probe.json', {'returncode': result.returncode, 'stdout': result.stdout, 'stderr': result.stderr})
        result.check_returncode()
        async with monitor(logs / 'gpu-telemetry.jsonl'):
            begin = time.monotonic()
            endpoints = await group.start()
            summary['startup_seconds'] = time.monotonic() - begin
            if startup_check is not None:
                await startup_check(endpoints[0]['endpoint'], logs)
            # Capture engine counters before/after, including actual DFlash counters.
            import httpx
            async with httpx.AsyncClient(timeout=10) as http:
                async def counters(name):
                    try:
                        r = await http.get(endpoints[0]['endpoint'].removesuffix('/v1') + '/metrics')
                        r.raise_for_status()
                        (logs / name).write_text(r.text)
                    except httpx.HTTPError as exc:
                        write(logs / (name + '.error.json'), {'error': str(exc)})
                await counters('metrics-before.txt')
                budget_exhausted = False
                if workload is not None:
                    await workload(endpoints[0]['endpoint'], logs)
                else:
                    budget_exhausted = False
                    for campaign, item in pending:
                        if any(p.poll() is not None for p in group.processes):
                            raise RuntimeError('Model server exited; remaining articles are pending')
                        if budget_seconds is not None and time.monotonic() - monotonic_start + (article_timeout or 900) >= budget_seconds:
                            budget_exhausted = True
                            break
                        aid = item['article_id']
                        write(logs / 'progress.json', {'article_id': aid, 'campaign': campaign.name, 'started_unix': time.time()})
                        base = campaign / 'results/articles' / aid
                        base.mkdir(parents=True, exist_ok=True)
                        attempt = base / f'attempt-{len(list(base.glob("attempt-*"))):04d}'
                        attempt.mkdir()
                        if article_timeout is None:
                            receipt = await run_article(campaign, item, endpoints[0]['endpoint'], attempt)
                        else:
                            try:
                                receipt = await asyncio.wait_for(run_article(campaign, item, endpoints[0]['endpoint'], attempt), article_timeout)
                            except TimeoutError:
                                receipt = read(attempt / 'receipt.json')
                                receipt.update(error_type='TimeoutError', error='Per-article overnight time budget exceeded')
                                write(attempt / 'receipt.json', receipt)
                        summary['articles'].append({'article_id': aid, 'campaign': campaign.name, 'status': receipt['status']})
                        print(json.dumps({k: receipt[k] for k in ('article_id', 'status', 'wall_seconds', 'model_calls')}), flush=True)
                        if receipt.get('error_type') == 'InfrastructureError':
                            raise RuntimeError('Backend request failed; stopping worker before consuming more articles. Resume retries the affected article.')
                        if any(p.poll() is not None for p in group.processes):
                            raise RuntimeError('Model server exited; remaining articles are pending')
                await counters('metrics-after.txt')
            summary['status'] = ('budget_exhausted' if budget_exhausted else
                                 'done' if all(x['status'] == 'done' for x in summary['articles']) else 'errors')
    finally:
        group.stop()
        if claimed is not None:
            claimed.close()
        summary.update(ended_unix=time.time(), worker_seconds=time.monotonic()-monotonic_start)
        write(logs / 'receipt.json', summary)
        lock.close()
    return summary


def stats(values):
    v = sorted(values)
    return {'n': len(v), 'mean': statistics.mean(v) if v else None,
            'median': statistics.median(v) if v else None,
            'p95': v[max(0, math.ceil(.95*len(v))-1)] if v else None, 'max': max(v) if v else None,
            'sum': sum(v)}


def status(work):
    m = read(work / 'manifest.json')
    done, pending = [], []
    latest_errors = {}
    for item in m['articles']:
        _, r = completed_attempt(work, item['article_id'])
        (done if r else pending).append(r or item['article_id'])
        if not r:
            attempts = sorted((work / 'results/articles' / item['article_id']).glob('attempt-*/receipt.json'))
            if attempts:
                latest = read(attempts[-1])
                latest_errors[item['article_id']] = latest.get('error_type') or latest.get('status', 'unknown')
    workers = [read(p) for p in (work / 'results/workers').glob('*/job-*/receipt.json')]
    from .benchmark_reporting import quality_summary
    return {'expected': len(m['articles']), 'completed': len(done), 'pending': pending,
            'quality_summary': quality_summary(done),
            'completed_without_prediction': sum(not r['has_prediction'] for r in done),
            'completed_without_model_calls': sum(r.get('model_calls') == 0 for r in done),
            'pending_with_failed_attempt': latest_errors,
            'worker_outcomes': dict(Counter(r.get('status', 'unknown') for r in workers)),
            'completion_note': 'Completed counts finished attempts, including extraction failures; worker outcomes are historical receipts, not live queue state.',
            'completed_with_prediction': sum(r['has_prediction'] for r in done),
            'article_seconds': stats([r['wall_seconds'] for r in done]),
            'all_extraction_tasks_valid': sum(r.get('all_extraction_tasks_valid', False) for r in done)}


def aggregate(work):
    m = check_snapshot(work)
    from .unified_scoring import score
    from .benchmark_reporting import throughput, worker_goodput, quality_summary, task_usage_summary
    from .reasoning_lineage import export_fact_index
    from .speculation_metrics import summarize as speculation_summary
    reference = rows(work / 'reference/unified.jsonl')
    if set(reference) != {x['article_id'] for x in m['articles']}:
        raise ValueError('Reference membership differs from frozen campaign')
    predictions, timing, all_attempts, completed_paths = {}, [], [], []
    for item in m['articles']:
        aid = item['article_id']
        path, receipt = completed_attempt(work, aid)
        if receipt:
            predictions.update(rows(path / 'ehr/data/unified.jsonl'))
            timing.append(receipt)
            completed_paths.append((aid, item['split'], path))
        for r in sorted((work / 'results/articles' / aid).glob('attempt-*/receipt.json')):
            all_attempts.append(read(r))
    out = work / 'summary'
    out.mkdir(exist_ok=True)
    reasoning_index = export_fact_index(work, out / 'fact-reasoning.jsonl', completed_paths)
    from .benchmark_failures import extraction_failures
    failures = extraction_failures(work, completed_paths)
    write(out / 'extraction-failures.json', failures)
    write_rows(out / 'predictions.jsonl', [predictions[k] for k in sorted(predictions)])
    scores = score(reference, predictions)
    write(out / 'scores.json', scores)
    for split in ('train', 'validation', 'test'):
        subset = {k: v for k, v in reference.items() if v['split'] == split}
        write(out / f'scores-{split}.json', score(subset, {k:v for k,v in predictions.items() if k in subset}))
    write(out / 'scores-per-article.json', {aid: score({aid: ref}, {aid: predictions[aid]} if aid in predictions else {})['overall']
                                          for aid, ref in reference.items()})
    write_rows(out / 'timing-all-attempts.jsonl', all_attempts)
    fields = ['article_id', 'split', 'shard', 'status', 'wall_seconds', 'extraction_seconds', 'ehr_seconds',
              'model_calls', 'prompt_tokens', 'completion_tokens', 'reasoning_tokens', 'failed_attempts',
              'patients_extracted', 'has_prediction', 'all_extraction_tasks_valid',
              'output_tokens_per_second', 'answer_tokens_per_second', 'documents_per_second',
              'output_tokens_per_gpu_second']
    with (out / 'article-timing.csv').open('w', newline='') as f:
        writer = csv.DictWriter(f, fields, extrasaction='ignore')
        writer.writeheader()
        by_id = {r['article_id']: {**r, **{k: v for k, v in throughput([r]).items() if k in fields}}
                 for r in timing}
        writer.writerows(by_id.get(a['article_id'], {**a, 'status': 'pending'}) for a in m['articles'])
    workers = [read(p) for p in (work / 'results/workers').glob('*/job-*/receipt.json')]
    progress = status(work)
    rates = throughput(timing)
    worker_dirs = list((work / 'results/workers').glob('*/job-*'))
    missing_worker_receipts = sum(not (p / 'receipt.json').exists() for p in worker_dirs)
    report = {**progress, 'scope': m['scope'], 'complete': not progress['pending'],
              'quality_summary': quality_summary(timing, reference, predictions),
              'task_usage': task_usage_summary(timing),
              'score_overall': scores['overall'], 'split_counts': m['splits'], 'media': m['media'],
              'startup_seconds': stats([r['startup_seconds'] for r in workers if 'startup_seconds' in r]),
              'summed_worker_seconds': sum(r['worker_seconds'] for r in workers),
              'observed_worker_span_seconds': max(r['ended_unix'] for r in workers)-min(r['started_unix'] for r in workers) if workers else None,
              'all_attempt_seconds': sum(r['wall_seconds'] for r in all_attempts),
              'extraction_task_statuses': dict(sum((Counter(r.get('task_statuses', {})) for r in timing), Counter())),
              'articles_with_ehr_errors': sum(bool(r.get('ehr_errors')) for r in timing),
              'generation_finish_reasons': dict(sum((Counter(r.get('finish_reasons', {})) for r in timing), Counter())),
              'length_limited_calls': (sum(r['finish_reasons'].get('length', 0) for r in timing)
                                       if timing and all('finish_reasons' in r for r in timing) else None),
              'operational_error_attempts': sum(r['status'] != 'done' for r in all_attempts),
              'throughput': rates,
              'speculation': [dict(article_id=r['article_id'], **r['speculation']) for r in timing if 'speculation' in r],
              'speculation_summary': speculation_summary(timing),
              'worker_goodput': worker_goodput(timing, workers,
                  default_gpus=m.get('extraction', {}).get('gpus_per_endpoint', 1), missing_receipts=missing_worker_receipts),
              'reasoning_capture': dict(sum((Counter(r.get('reasoning_statuses', {})) for r in all_attempts), Counter())),
              'fact_reasoning_index': reasoning_index,
              'extraction_failures': {'artifact': 'summary/extraction-failures.json',
                                     'by_task': failures['by_task'], 'by_article': failures['by_article']},
              'output_tokens_per_gpu_article_second': rates['output_tokens_per_gpu_second'],
              'slowest_articles': sorted([{'article_id': r['article_id'], 'seconds': r['wall_seconds']} for r in timing],
                                         key=lambda r: -r['seconds'])[:10],
              'timing_notes': [('Wall time per article includes live discovery, text/caption extraction, repair, text EHR stages and serialization; no pixels.'
                                if m.get('text_only') else 'Wall time per article includes live discovery, pixels, extraction, repair, EHR stages and serialization.'),
                              'Startup/queue and any recorded synthetic startup checks are excluded from article latency.',
                              'Sequential articles per worker favor attributable latency over maximum cross-article throughput.',
                              'Summed worker time includes probe/startup/cleanup/retries; interrupted jobs without final receipt require sacct.',
                              'Worker span excludes initial queue/CPU setup; never sum worker rates as simultaneous throughput.',
                              'Use sacct for queue and scheduler allocation duration. Missing token usage stays null.']}
    write(out / 'report.json', report)
    (out / 'report.md').write_text('# Dense article benchmark\n\n' +
        f"Completed: {progress['completed']}/{progress['expected']}; predictions: {len(predictions)}.\n\n" +
        'Article seconds (excluding startup/queue):\n\n```json\n' + json.dumps(progress['article_seconds'], indent=2) +
        '\n```\n\nAnnotation agreement:\n\n```json\n' + json.dumps(scores['overall'], indent=2) +
        '\n```\n\nPipeline throughput (completed articles):\n\n```json\n' + json.dumps(rates, indent=2) +
        '\n```\n\nWorker goodput (including startup/retries):\n\n```json\n' + json.dumps(report['worker_goodput'], indent=2) +
        '\n```\n\n' + '\n'.join('- '+x for x in report['timing_notes']) +
        '\n\nSupplement classification/clinical extraction are unsupported; these reference targets remain in the full denominator.\n')
    hashes = inventory(work, ['results', 'summary', 'submissions'])
    if (work / 'runtime.json').exists():
        hashes['runtime.json'] = sha(work / 'runtime.json')
    write(out / 'integrity.json', {'manifest_sha256': sha(work / 'manifest.json'), 'files': hashes})
    return report


def verify(work):
    check_snapshot(work)
    progress = status(work)
    if progress['pending']:
        raise ValueError(f'{len(progress["pending"])} articles lack a completed attempt: {progress["pending"][:8]}')
    integrity = read(work / 'summary/integrity.json')
    if integrity['manifest_sha256'] != sha(work / 'manifest.json'):
        raise ValueError('Frozen manifest changed after scoring')
    for name, digest in integrity['files'].items():
        if sha(work / name) != digest:
            raise ValueError('Scored output changed: ' + name)
    report = read(work / 'summary/report.json')
    if not report['complete'] or report['completed'] != progress['expected']:
        raise ValueError('Summary is stale/incomplete; aggregate again')
    return {'verified': True, 'articles_attempted_to_completion': progress['completed'],
            'articles_with_prediction': progress['completed_with_prediction'],
            'note': 'Verification checks execution coverage and integrity, not model correctness; model failures remain scored misses.'}


def export(work):
    # Export partial failures too; verify remains nonzero until coverage is complete.
    archive = work / 'results.tar.gz'
    with tarfile.open(archive, 'w:gz') as tf:
        for name in ('manifest.json', 'runtime.json', 'submissions', 'results', 'summary', 'code', 'inputs', 'reference', 'logs'):
            path = work / name
            if path.exists():
                tf.add(path, arcname=work.name + '/' + name,
                       filter=lambda info: None if any(x in Path(info.name).parts for x in ('__pycache__', '.venv')) else info)
    checksum(archive)
    return {'archive': str(archive), 'bytes': archive.stat().st_size}


def submit(work, account, qos, hours, resume=False, dry_run=False, *, script_name='dense_benchmark.sbatch', campaigns=None, worker_shards=None,
           gpu_memory='60G'):
    check_snapshot(work)
    if not 1 <= hours <= 72:
        raise ValueError('--hours must be between 1 and 72; Slurm submission also checks current policy')
    ledger = work / 'submissions'
    if ledger.exists() and any(ledger.iterdir()) and not resume:
        raise ValueError('Already submitted. Use --resume after previous jobs are terminal')
    if resume and ledger.exists():
        old_ids = [str(j['id']) for p in ledger.glob('*.json') for j in read(p)['jobs']]
        if old_ids:
            # Finished IDs may already be purged from squeue. Inspect the user
            # queue and match only this campaign's IDs; unrelated runs are fine.
            live = subprocess.run(['squeue', '-h', '-u', os.environ['USER'], '-o', '%i'], check=True, text=True, capture_output=True)
            if set(old_ids) & set(live.stdout.split()):
                raise ValueError('Previous campaign jobs are still active; wait before --resume')
    (work / 'logs').mkdir(exist_ok=True)
    script = work / 'code/scripts' / script_name
    common = ['sbatch', '--parsable', '--hold', '--nodes=1', '--ntasks=1', '--no-requeue',
              '--account='+account, '--qos='+qos, '--chdir='+str(work / 'code'),
              '--export=ALL,OP2_SUBMISSION_ID='+str(time.time_ns())]
    phases = [('setup', None)] if not resume or not (work / 'runtime.json').exists() else []
    if worker_shards is not None:
        phases += [('worker', i) for i in worker_shards]
    elif campaigns is None:
        phases += [('worker', i) for i in range(WORKERS)
                   if any(x['shard'] == i and not completed_attempt(work, x['article_id'])[0]
                          for x in read(work / 'manifest.json')['articles'])]
    else:
        for campaign in campaigns:
            check_snapshot(campaign)
        pending_pairs = sum(any(not completed_attempt(c, x['article_id'])[0] for c in campaigns)
                            for x in read(work / 'manifest.json')['articles'])
        phases += [('worker', i) for i in range(min(WORKERS, pending_pairs))]
    phases += [('aggregate', None)]
    def command(phase, shard):
        gpu = phase == 'worker'
        return common + ['--job-name=op2-dense-'+phase+('' if shard is None else str(shard)),
               '--partition='+('hpg-b200' if gpu else 'hpg-default'), '--cpus-per-task='+('8' if phase != 'aggregate' else '2'),
               '--mem='+(gpu_memory if gpu else '32G'), '--time='+ (f'{hours:02d}:00:00' if gpu else '04:00:00'),
               '--output='+str(work / 'logs' / (phase+'-%j.log')), '--signal=B:TERM@120'] + (
               ['--gres=gpu:b200:1'] if gpu else []) + [str(script), phase, str(work), str(shard) if shard is not None else '-']
    env = {k: v for k, v in os.environ.items() if not k.startswith('SBATCH_')}
    commands = [command(*p) for p in phases]
    if dry_run:
        return {'commands_without_dependencies': commands, 'dependency_policy': 'held DAG; afterany setup/workers; release downstream first', 'submitted': False}
    # Real sbatch validates each request while allowing pending jobs. Do not
    # gate submission on --test-only's availability/start-time prediction.
    # Every job stays held until the whole DAG has been accepted; submission
    # failure cancels only this attempt's held jobs below.
    ledger.mkdir(exist_ok=True)
    receipt_path = ledger / f'{time.time_ns()}.json'
    jobs = []
    write(receipt_path, {'jobs': jobs, 'status': 'submitting'})
    try:
        for (phase, shard), cmd in zip(phases, commands):
            deps = [str(j['id']) for j in jobs if j['phase'] == ('setup' if phase == 'worker' else 'worker')]
            if phase == 'aggregate' and not deps:
                deps = [str(j['id']) for j in jobs]
            if deps:
                cmd.insert(1, '--dependency=afterany:'+':'.join(deps))
            result = subprocess.run(cmd, check=True, env=env, text=True, capture_output=True)
            jid = result.stdout.strip().split(';')[0]
            if not jid.isdigit():
                raise ValueError('Unrecognized sbatch job ID: '+result.stdout)
            jobs.append({'id': jid, 'phase': phase, 'shard': shard, 'argv': cmd})
            write(receipt_path, {'jobs': jobs, 'status': 'held'})
        for job in reversed(jobs):
            subprocess.run(['scontrol', 'release', job['id']], check=True)
        write(receipt_path, {'jobs': jobs, 'status': 'released'})
    except BaseException:
        for job in jobs:
            subprocess.run(['scancel', job['id']], check=False)
        write(receipt_path, {'jobs': jobs, 'status': 'submission_failed_cancel_requested'})
        raise
    return {'jobs': jobs, 'ledger': str(receipt_path)}


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest='command', required=True)
    a = sub.add_parser('prepare')
    a.add_argument('--root', type=Path, default=Path.cwd())
    a.add_argument('--work', required=True, type=Path)
    a.add_argument('--expected', type=int, default=260)
    a.add_argument('--download-media', action='store_true', help='CPU only: download missing source-listed images and verify frozen SHA-256')
    for command in ('submit', 'setup', 'worker', 'aggregate', 'status', 'verify', 'export'):
        a = sub.add_parser(command)
        a.add_argument('--work', required=True, type=Path)
        if command == 'submit':
            a.add_argument('--account', default='cai5724'); a.add_argument('--qos', default='cai5724')
            a.add_argument('--hours', type=int, default=12)
            a.add_argument('--resume', action='store_true'); a.add_argument('--dry-run', action='store_true')
        if command == 'worker':
            a.add_argument('--shard', type=int, required=True)
    args = p.parse_args(argv)
    work = args.work.resolve()
    if args.command == 'prepare':
        result = prepare(args.root, work, args.expected, args.download_media)
    elif args.command == 'submit':
        result = submit(work, args.account, args.qos, args.hours, args.resume, args.dry_run)
    elif args.command == 'worker':
        def terminate(signum, frame):
            raise KeyboardInterrupt('Slurm termination; retain partial attempts for resume')
        signal.signal(signal.SIGTERM, terminate)
        result = asyncio.run(worker(work, args.shard))
        if result['status'] != 'done':
            raise SystemExit('Worker finished with operational errors; inspect receipts, then resume')
    elif args.command == 'aggregate':
        try:
            result = aggregate(work)
        except Exception as exc:
            write(work / 'summary/aggregation-error.json', {'error': repr(exc)})
            export(work)
            raise
        export(work)
        if not result['complete']:
            raise SystemExit('Partial campaign exported; inspect status and resume pending articles')
    else:
        result = globals()[args.command](work)
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
