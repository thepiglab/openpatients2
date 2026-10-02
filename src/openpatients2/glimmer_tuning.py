"""Focused Red Hat FP8/DFlash tuning on a single eight-B200 allocation."""
from __future__ import annotations

import asyncio
import csv
import json
import os
from pathlib import Path
import subprocess
import time

from .data import write_json
from .hpg_eval import Replay
from .provenance import json_digest
from .serving import ServerGroup


class GPUDrainError(RuntimeError):
    """A layout must never be reused after an ambiguous or incomplete release."""


def gpu_snapshot():
    """Record only the allocation's UUIDs, plus their compute-process owners."""
    def query(fields, kind='gpu'):
        try:
            result = subprocess.run(['nvidia-smi', f'--query-{kind}={fields}',
                '--format=csv,noheader,nounits'], capture_output=True, text=True, check=True, timeout=20)
        except (OSError, subprocess.SubprocessError) as exc:
            raise GPUDrainError('Cannot verify allocated GPU memory/process state') from exc
        return list(csv.reader(result.stdout.splitlines(), skipinitialspace=True))
    visible = os.environ['CUDA_VISIBLE_DEVICES'].split(',')
    devices = {uuid: {'index': index, 'memory_used_mib': int(memory)}
               for uuid, index, memory in query('uuid,index,memory.used')
               if index in visible or uuid in visible}
    if len(devices) != 8:
        raise GPUDrainError('Cannot identify exactly eight allocated GPU UUIDs; refusing an ambiguous cleanup check')
    processes = [{'gpu_uuid': uuid, 'pid': int(pid), 'name': name, 'memory_mib': memory}
                 for uuid, pid, name, memory in query('gpu_uuid,pid,process_name,used_gpu_memory', 'compute-apps')
                 if uuid in devices]
    return {'time_unix': time.time(), 'devices': devices, 'processes': processes}


def unreleased_gpus(baseline, current, tolerance_mib):
    if baseline['devices'].keys() != current['devices'].keys():
        raise GPUDrainError('Allocated GPU identity changed between layouts')
    old_pids = {(p['gpu_uuid'], p['pid']) for p in baseline['processes']}
    extra = {p['gpu_uuid'] for p in current['processes'] if (p['gpu_uuid'], p['pid']) not in old_pids}
    return sorted(extra | {uuid for uuid, d in current['devices'].items()
        if d['memory_used_mib'] > baseline['devices'][uuid]['memory_used_mib'] + tolerance_mib})


async def wait_for_release(baseline, output, timeout=120, tolerance_mib=512):
    deadline = time.monotonic() + timeout
    while True:
        current = gpu_snapshot()
        remaining = unreleased_gpus(baseline, current, tolerance_mib)
        write_json(output, {'status': 'waiting' if remaining else 'released',
                           'remaining_gpu_uuids': remaining, 'baseline': baseline, 'current': current})
        if not remaining: return
        if time.monotonic() >= deadline:
            raise GPUDrainError(f'GPU workers/memory did not drain; no next layout will start. Inspect {output}')
        await asyncio.sleep(2)


def shortlist(cells):
    """Pick speed candidates within KV/cache regimes, never treat speed as factuality."""
    best = {}
    for cell in cells:
        if not cell['eligible_for_selection']: continue
        layout = cell['layout']
        regime = (layout.get('kv_cache_dtype', 'auto'), layout.get('prefix_cache', True))
        rank = (cell['valid_tasks_per_second'], cell['aggregate_output_tokens_per_second'])
        if regime not in best or rank > (best[regime]['valid_tasks_per_second'],
                                         best[regime]['aggregate_output_tokens_per_second']):
            best[regime] = cell
    return sorted(best.values(), key=lambda c: c['valid_tasks_per_second'], reverse=True)


def quality_stability(reference, candidate, allowed_facts, allowed_tasks):
    """Matched-seed automatic checklist guard; broader source review remains pending."""
    rows = []
    if reference.keys() != candidate.keys() or len(reference) < 3:
        raise ValueError('Quality confirmation needs the same three or more seeds as its control')
    for arm, ref in reference.items():
        test = candidate[arm]
        facts_lost = ref['scores']['delivered']['matched'] - test['scores']['delivered']['matched']
        tasks_lost = ref['valid_tasks'] - test['valid_tasks']
        extra_forbidden = test['scores']['delivered']['forbidden_violations'] - ref['scores']['delivered']['forbidden_violations']
        rows.append({'arm': arm, 'reference_delivered': ref['scores']['delivered']['matched'],
            'candidate_delivered': test['scores']['delivered']['matched'], 'facts_lost': facts_lost,
            'valid_tasks_lost': tasks_lost, 'additional_forbidden_hits': extra_forbidden,
            'passed': facts_lost <= allowed_facts and tasks_lost <= allowed_tasks and extra_forbidden <= 0})
    return {'passed': all(r['passed'] for r in rows), 'paired_seeds': rows,
            'notice': 'Partial automatic checklist stability, not medical equivalence or a production deployment approval.'}


async def quality_runs(campaign, model, layout, endpoints, concurrency, root, prefix, quality):
    config = campaign['config']; results = {}
    for name, arm in config['arms'].items():
        label = prefix + name
        write_json(root / 'progress.json', {'phase': 'quality', 'arm': label,
            'layout': layout['name'], 'concurrency': concurrency, 'time_unix': time.time()})
        replay = Replay(Path(campaign['root']) / config['fixtures'], root / label,
            {**model, 'deployment': layout}, arm, endpoints, concurrency,
            config['request_timeout_seconds'], config['max_model_len'])
        try:
            result = await replay.run()
            await replay.secondary()
        finally:
            await replay.close()
        results[name] = result
        quality[label] = result
        write_json(root / label / 'tuning-context.json', {'layout': layout,
            'concurrency_per_replica': concurrency, 'sampling': arm})
    return results


async def gpu_tuning(campaign, model):
    from . import glimmer_benchmark as g
    from .hipergator import active_path, model_lock, check_owner, sha256
    config = campaign['config']; work = Path(campaign['work']); path = active_path(campaign)
    if not os.environ.get('SLURM_JOB_ID') or not os.environ.get('CUDA_VISIBLE_DEVICES'):
        raise RuntimeError('FP8 tuning requires a Slurm allocation of eight B200s')
    root = work / 'results' / model['name']; started = time.monotonic()
    if any(root.glob('*/report.json')): raise ValueError('Use a fresh tuning campaign; measurements are not overwritten')
    cells = []; failures = []; quality = {}; confirmations = []; vision_results = {}
    with model_lock(work):
        check_owner(path, campaign, model)
        audit = json.loads((root / 'download.json').read_text())
        ready = json.loads((path / 'ready.json').read_text())
        if ready != {'campaign': campaign['id'], 'model': model['name'], 'audit_sha256': json_digest(audit)}:
            raise ValueError('Checkpoint download audit is incomplete')
        if config.get('vision_evaluation') and audit.get('vision_manifest_sha256') != sha256(work / 'vision-assets/manifest.json'):
            raise ValueError('Vision asset manifest changed after CPU verification')
        for asset in audit['files']:
            stat = (path / asset['folder'] / asset['file']).stat()
            if stat.st_size != asset['bytes'] or stat.st_mtime_ns != asset['mtime_ns']:
                raise ValueError('Checkpoint changed after CPU verification')
        control = next(l for l in config['layouts'] if l['name'] == config['quality_layout'])
        g.gpu_probe(campaign, model, g.serving_config(campaign, model, control))
        baseline = gpu_snapshot(); write_json(root / 'allocation-baseline.json', baseline)
        monitor_log = (root / 'gpu-utilization.csv').open('w')
        monitor = subprocess.Popen(['nvidia-smi', '--query-gpu=timestamp,index,uuid,utilization.gpu,memory.used,power.draw',
            '--format=csv', '-l', '1'], stdout=monitor_log, stderr=subprocess.STDOUT)
        async def start(layout, folder):
            await wait_for_release(baseline, folder / 'gpu-before.json', config['cleanup_timeout_seconds'])
            group = ServerGroup(g.serving_config(campaign, model, layout), str(work), str(folder / 'servers'))
            try:
                await group.start(config['startup_timeout_seconds'])
                endpoints = [c['endpoint'] for c in group.commands]
                await g.smoke(endpoints, model, config, folder / 'smoke.json')
                return group, endpoints
            except BaseException:
                group.stop()
                await wait_for_release(baseline, folder / 'gpu-after.json', config['cleanup_timeout_seconds'])
                raise
        async def stop(group, folder):
            group.stop()
            await wait_for_release(baseline, folder / 'gpu-after.json', config['cleanup_timeout_seconds'])
        try:
            for layout in config['layouts']:
                folder = root / 'layouts' / layout['name']; group = None
                write_json(root / 'progress.json', {'phase': 'starting', 'layout': layout['name'], 'time_unix': time.time()})
                try:
                    group, endpoints = await start(layout, folder)
                    if layout == control:
                        await quality_runs(campaign, model, layout, endpoints, 8, root, '', quality)
                        await g.article_lengths(campaign, endpoints[0], root)
                    for concurrency in config['throughput']['concurrency_per_replica']:
                        write_json(root / 'progress.json', {'phase': 'throughput', 'layout': layout['name'],
                            'concurrency_per_replica': concurrency, 'time_unix': time.time()})
                        cells.append(await g.throughput_cell(campaign, model, layout, endpoints, concurrency,
                            folder / f'concurrency-{concurrency}'))
                    write_json(folder / 'status.json', {'status': 'completed'})
                except GPUDrainError:
                    raise  # Optional kernel failures are tolerable; leaked GPU ownership is not.
                except Exception as exc:
                    row = {'status': 'failed', 'layout': layout['name'], 'experimental': layout.get('experimental', False),
                           'error_type': type(exc).__name__, 'error': str(exc)}
                    failures.append(row); write_json(folder / 'status.json', row)
                    if layout == control: raise
                finally:
                    if group is not None: await stop(group, folder)
                if layout == control and config.get('vision_evaluation'):
                    # Exercise real pixels early, before the long text-only speed sweep.
                    from .glimmer_vision import evaluate_images
                    for method in ('ordinary', 'dflash'):
                        vision_layout = {**control, 'name':'vision-'+method, 'vision':True, 'max_num_seqs':8}
                        if method == 'dflash': vision_layout['speculation'] = 'dflash'
                        folder = root / 'vision' / method; group = None
                        write_json(root / 'progress.json', {'phase':'vision', 'method':method, 'time_unix':time.time()})
                        try:
                            group, endpoints = await start(vision_layout, folder)
                            vision_results[method] = await evaluate_images(campaign, model, vision_layout, endpoints, folder)
                        except GPUDrainError: raise
                        except Exception as exc:
                            vision_results[method] = {'status':'failed', 'error_type':type(exc).__name__, 'error':str(exc)}
                            write_json(folder / 'report.json', vision_results[method])
                        finally:
                            if group is not None: await stop(group, folder)
            reference = {name: quality[name] for name in config['arms']}
            for index, cell in enumerate(shortlist(cells)):
                layout = cell['layout']; folder = root / 'confirmations' / str(index)
                group, endpoints = await start(layout, folder)
                try:
                    candidate = await quality_runs(campaign, model, layout, endpoints,
                        cell['concurrency_per_replica'], root, f'confirm{index}_', quality)
                    guard = quality_stability(reference, candidate, config['quality_max_lost_checks'], config['quality_max_lost_tasks'])
                    row = {'layout': layout, 'concurrency_per_replica': cell['concurrency_per_replica'],
                        'aggregate_output_tokens_per_second': cell['aggregate_output_tokens_per_second'],
                        'valid_tasks_per_second': cell['valid_tasks_per_second'], 'stability': guard}
                    confirmations.append(row); write_json(folder / 'quality-stability.json', row)
                finally: await stop(group, folder)
            accepted = [c for c in confirmations if c['stability']['passed']]
            if config.get('vision_evaluation'):
                from .glimmer_vision import integrate_bundles
                integrate_bundles(root, quality, vision_results)
            status = 'partial' if (any(not f['experimental'] for f in failures)
                or any(v.get('status') != 'completed' for v in vision_results.values())) else 'completed'
            result = {'status': status, 'model': model['name'], 'gpu_count': 8,
                'gpu_stage_seconds': time.monotonic() - started, 'quality_arms': list(quality),
                'layout_failures': failures, 'confirmations': confirmations,
                'vision_evaluations': vision_results,
                'best_measured_layout': accepted[0] if accepted else None,
                'selection_basis': 'Highest first-pass valid tasks/s within each KV/prefix regime, then matched-seed full checklist confirmation. Source review remains pending.'}
            write_json(root / 'progress.json', {'phase': status, 'time_unix': time.time()})
            return result
        finally:
            monitor.terminate()
            try: monitor.wait(timeout=10)
            except subprocess.TimeoutExpired: monitor.kill(); monitor.wait(timeout=10)
            monitor_log.close()


def report_tuning(campaign):
    work = Path(campaign['work']); model = campaign['config']['models'][0]
    root = work / 'results' / model['name']
    def read(path): return json.loads(path.read_text()) if path.exists() else {'status': 'missing'}
    gpu = read(root / 'gpu.json'); cleanup = read(root / 'cleanup.json')
    arms = {p.parent.name: read(p) for p in sorted(root.glob('*/report.json'))}
    cells = [read(p) for p in sorted(root.glob('layouts/*/concurrency-*/report.json'))]
    failed = gpu.get('status') != 'completed' or cleanup.get('status') != 'deleted'
    lines = ['# Red Hat Glimmer FP8 / B200 tuning', '',
        f'GPU stage: {gpu.get("status")}; checkpoint cleanup: {cleanup.get("status")}.', '',
        '| Arm | Required raw / delivered of 161 | Valid / invalid of 176 |', '| --- | --- | --- |']
    for name, row in arms.items():
        if 'scores' in row:
            lines.append(f'| {name} | {row["scores"]["raw"]["matched"]} / {row["scores"]["delivered"]["matched"]} | {row["valid_tasks"]} / {row["tasks"]-row["valid_tasks"]} |')
    lines += ['', '| Layout | Concurrent / replica | Output tok/s, all 8 GPUs | Valid tasks/s | Repeats |',
              '| --- | ---: | ---: | ---: | ---: |']
    for c in sorted(cells, key=lambda c: c.get('valid_tasks_per_second', 0), reverse=True):
        if c.get('status') == 'completed':
            rate = c['aggregate_output_tokens_per_second']
            lines.append(f'| {c["layout"]["name"]} | {c["concurrency_per_replica"]} | {rate:.1f}'
                         f' | {c["valid_tasks_per_second"]:.3f} | {len(c["rounds"])} |' if rate is not None else
                         f'| {c["layout"]["name"]} | {c["concurrency_per_replica"]} | unavailable | {c["valid_tasks_per_second"]:.3f} | {len(c["rounds"])} |')
    lines += ['', '## Quality confirmation', '']
    for c in gpu.get('confirmations', []):
        lines.append(f'- {c["layout"]["name"]}, concurrency {c["concurrency_per_replica"]}: checklist stability {c["stability"]["passed"]}.')
    lines += ['', '## Image pixels, captions and patient/panel attribution', '']
    for method, result in gpu.get('vision_evaluations', {}).items():
        lines.append(f'- {method}: {json.dumps(result)}')
    lines += ['', 'Best confirmed speed candidate: ' + json.dumps(gpu.get('best_measured_layout')), '',
        'Optional layout failures: ' + json.dumps(gpu.get('layout_failures', [])), '',
        'Rates include reasoning and answer tokens, exclude startup/warmup/queue time, and aggregate eight GPUs. '
        'Warm-prefix and disabled-prefix cells are separate regimes. All cells use identical real prompts and seeds; '
        'full natural completion, medium reasoning, T=1/top_p=.95/top_k=64, 32k output caps, no schema-constrained decoding. '
        'Repeat-level token rates and latency/TTFT/TTFA distributions are saved per cell. Whole GPU-stage duration includes initialization and every experiment.', '',
        'Confirmation uses the same PMC fixtures and three seeds, including patient discovery and figure attribution. '
        'The 161 required and 36 forbidden checks are partial development tests, not full medical accuracy. '
        'Actual image pixels are evaluated separately with ordinary decoding and DFlash, matched against full-text/caption-only attribution. '
        'Pixel descriptions and caption claims are separate, with per-patient/panel media bundles and URLs/hashes/licenses. '
        'Source and pixel adjudication remains necessary; no production default is changed.']
    (work / 'SUMMARY.md').write_text('\n'.join(lines) + '\n')
    write_json(work / 'summary.json', {'models': [{'model': model, 'gpu': gpu, 'cleanup': cleanup, 'arms': arms, 'cells': cells}],
        'failed_models': [model['name']] if failed else [], 'failed_records': int(failed),
        'weight_storage_exists': (work / 'active-model').exists(), 'fixture_manifest_sha256': campaign['fixture_manifest_sha256']})
    return {'summary': str(work / 'summary.json'), 'markdown': str(work / 'SUMMARY.md'), 'failed_records': int(failed)}
