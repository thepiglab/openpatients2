"""Frozen-input component comparisons using the existing independent GPU DAG.

Each seed first runs the existing extractor once. All three component calls read
that same completed export; neither the previous component's output nor a new
discovery/pixel inference can become their input.
"""
from __future__ import annotations

import asyncio
import time
import os
from pathlib import Path

from . import frontier_campaign as fc
from .corpus_stats import sha256
from .data import write_json
from .provenance import json_digest

VARIANTS = ('live-complete', 'table-observations', 'attribute-audit', 'episode-rebuild')


def validate_plan(config):
    # Retain all existing scheduler/source/serving safeguards. Replacing the arm
    # list only for validation does not change the serialized controlled plan.
    original = {**config, 'campaign_kind': 'frontier', 'variants': list(fc.VARIANTS)}
    fc.validate_plan(original)
    if (config.get('gpu_workers'), config.get('worker_cpus'), config.get('worker_mem_gb'),
            config.get('gpu_minutes')) != (4, 8, 60, 90):
        raise ValueError('Controlled components require four independent 1-B200 / 8-CPU / 60-GB / 90-minute workers')
    if config.get('variants') != list(VARIANTS):
        raise ValueError('Keep the baseline and three independently frozen component arms')
    return True


def trial_config(campaign, variant, articles):
    if variant not in VARIANTS:
        raise ValueError('Unknown controlled component')
    original = {**campaign, 'config': {**campaign['config'], 'campaign_kind': 'frontier'}}
    # Components use the baseline configuration and its complete output. They do
    # not inherit the ledger architecture's disabled extraction/review stages.
    return fc.trial_config(original, 'live-complete', articles)


def freeze_inputs(baseline, report, assignment, *, work=None):
    """Pin source packets and actual predicted inputs, never gold rosters."""
    baseline = Path(baseline).resolve()
    files = {}
    for key in ('patients', 'predicted_rosters', 'visual_annotations'):
        path = Path(report['outputs'][key])
        if path.is_symlink() or not path.is_file() or not path.resolve().is_relative_to(baseline):
            raise ValueError('Baseline output is missing or escaped its trial: ' + key)
        files[str(path.resolve())] = sha256(path)
    # Save all figure task evidence used by the scorer. Its predictions are
    # copied by components and must never be silently rerun.
    for path in sorted((baseline / 'tasks').glob('*.json')):
        task = fc.read_json(path)
        if task.get('task') in {'figure_attribution', 'pixel_attribution', 'joint_figure'}:
            if path.is_symlink():
                raise ValueError('Baseline figure task is a symlink')
            files[str(path)] = sha256(path)
    for key in ('sample', 'media'):
        path = Path(assignment[key])
        if path.is_symlink() or not path.is_file():
            raise ValueError('Frozen source input is missing: ' + key)
        files[str(path.resolve())] = sha256(path)
    # Pixel bytes are covered by CPU readiness hashes. Include those hashes in
    # the trial receipt as well, so reviewers can identify the exact assets.
    media = fc.read_json(assignment['media'])
    if work is not None:
        root = Path(work).resolve()
        for asset in media.get('figures', []):
            if asset.get('status') != 'ready':
                continue
            path = root / asset['file']
            if path.is_symlink() or not path.resolve().is_relative_to(root / 'vision-assets'):
                raise ValueError('Frozen pixel asset escaped campaign')
            digest = sha256(path)
            if digest != asset['pixel_provenance']['sha256']:
                raise ValueError('Frozen pixel asset changed')
            files[str(path.resolve())] = digest
    receipt = {'version': 'controlled-inputs/1', 'baseline_dir': str(baseline),
               'articles': assignment['articles'], 'files': files,
               'media_manifest': media, 'origin': 'fresh baseline actual predictions'}
    receipt['sha256'] = json_digest(receipt)
    write_json(baseline / 'frozen-inputs.json', receipt)
    return receipt


def verify_frozen(receipt):
    if receipt['sha256'] != json_digest({k: v for k, v in receipt.items() if k != 'sha256'}):
        raise ValueError('Frozen baseline receipt changed')
    for name, digest in receipt['files'].items():
        path = Path(name)
        if path.is_symlink() or not path.is_file() or sha256(path) != digest:
            raise ValueError('Frozen baseline/source changed: ' + name)


def figure_outputs(trial):
    outputs = {}
    for path in sorted((Path(trial) / 'tasks').glob('*.json')):
        if path.is_symlink():
            raise ValueError('Figure task input is a symlink')
        task = fc.read_json(path)
        if task.get('task') in {'figure_attribution', 'pixel_attribution', 'joint_figure'}:
            outputs[path.name] = task
    return outputs


def figure_payloads(trial):
    return {name: {k: v for k, v in task.items() if k not in
                  {'attempts', 'origin', 'excluded_from_generated_task_metrics'}}
            for name, task in figure_outputs(trial).items()}


async def run_trials(campaign, shard, assignment, endpoints, deadline, *, baseline_runner=None, component_runner=None,
                     warmup_runner=None):
    """Run baseline first; skip dependent arms explicitly if it is unavailable."""
    if baseline_runner is None:
        from .pilot_extract import run_pilot
        baseline_runner = run_pilot
    if component_runner is None:
        from .component_trial import run_component_trial
        component_runner = run_component_trial
    if warmup_runner is None:
        from .corpus_pilot import trial_warmup
        warmup_runner = trial_warmup
    work = Path(campaign['work']); base = work / 'shards' / f'{shard:03d}'
    rows = []
    for index, seed in enumerate(campaign['config']['seeds']):
        components = list(VARIANTS[1:])
        offset = (index + shard) % len(components)
        names = [VARIANTS[0], *components[offset:], *components[:offset]]
        baseline = base / 'trials' / f'seed{seed}' / VARIANTS[0]
        frozen = None
        for name in names:
            trial = base / 'trials' / f'seed{seed}' / name
            row = {'shard': shard, 'seed': seed, 'variant': name, 'articles': assignment['articles']}
            remaining = deadline - time.monotonic()
            if name != VARIANTS[0] and frozen is None:
                row.update(status='unavailable', reason='Baseline did not produce a frozen completed export')
            elif remaining < 180:
                row.update(status='deferred', reason='Worker deadline reserve')
            else:
                config = trial_config(campaign, name, len(assignment['articles']))
                try:
                    async def execute():
                        warmup = await warmup_runner(endpoints, config, seed)
                        if name == VARIANTS[0]:
                            result = await baseline_runner(config, assignment['sample'], trial, endpoints,
                                campaign['config']['context'], 'targeted', input_scope='patient_sections', seed=seed,
                                image_manifest={**fc.read_json(assignment['media']), 'output_dir': str(work)})
                        else:
                            verify_frozen(frozen)
                            result = await component_runner(config, assignment['sample'], trial, endpoints,
                                campaign['config']['context'], baseline_dir=baseline, component=name, seed=seed)
                            verify_frozen(frozen)
                            write_json(trial / 'frozen-inputs.json', frozen)
                        result['warmup'] = warmup
                        write_json(trial / 'report.json', result)
                        return result
                    result = await asyncio.wait_for(execute(), timeout=remaining)
                    if name == VARIANTS[0]:
                        frozen = freeze_inputs(baseline, result, assignment, work=work)
                    else:
                        # Discovery and visual exports must be byte-identical.
                        for key in ('predicted_rosters', 'visual_annotations'):
                            original_path = Path(frozen['baseline_dir']) / (
                                'rosters.json' if key == 'predicted_rosters' else 'visual-annotations.json')
                            if sha256(result['outputs'][key]) != sha256(original_path):
                                raise ValueError('Component changed frozen discovery/visual output: ' + key)
                        if figure_payloads(trial) != figure_payloads(baseline):
                            raise ValueError('Component changed frozen figure predictions')
                    row.update(status='completed', report=result, frozen_baseline_sha256=frozen['sha256'],
                               output_sha256={key: sha256(result['outputs'][key]) for key in
                                   ('patients', 'predicted_rosters', 'visual_annotations')},
                               figure_task_sha256={name: sha256(trial / 'tasks' / name)
                                                   for name in figure_outputs(trial)})
                except Exception as error:
                    if name == VARIANTS[0]:
                        frozen = None
                    row.update(status='failed', error_type=type(error).__name__, error=str(error))
            rows.append(row)
            write_json(base / 'trials.json', rows)
    return rows


async def run_worker(campaign, shard):
    from . import hipergator as hpg
    from .serving import ServerGroup
    from .gpu_telemetry import monitor
    if type(shard) is not int or not 0 <= shard < campaign['config']['gpu_workers']:
        raise ValueError('Worker shard is outside the plan')
    if len([v for v in os.environ.get('CUDA_VISIBLE_DEVICES', '').split(',') if v.strip()]) != 1:
        raise ValueError('Each worker requires exactly one allocated GPU')
    work = Path(campaign['work']); base = work / 'shards' / f'{shard:03d}'
    fc.verify_cpu(campaign)
    assignment = fc.read_json(work / 'shards.json')['shards'][shard]
    engine = hpg.read_campaign(work / 'engine')
    started = time.monotonic()
    deadline = started + campaign['config']['gpu_minutes'] * 60 - 150
    with fc.checkpoint_reader(engine['work']):
        model, _ = fc.verify_checkpoint(engine)
        cfg = fc.worker_serving_config(campaign, engine, model, shard)
        fc.probe_worker(engine, cfg, base / 'container-probe.json')
        group = ServerGroup(cfg, campaign['work'], str(base / 'servers'))
        async with monitor(base / 'telemetry.jsonl'):
            try:
                commands = await asyncio.wait_for(group.start(timeout=min(1200, deadline - time.monotonic())),
                                                  timeout=max(1, deadline - time.monotonic()))
                rows = await run_trials(campaign, shard, assignment, [c['endpoint'] for c in commands], deadline)
            finally:
                group.stop()
    result = {'status': 'completed' if all(r['status'] == 'completed' for r in rows) else 'partial',
              'shard': shard, 'trials': rows, 'wall_seconds': time.monotonic() - started,
              'allocated_gpus': 1, 'checkpoint_downloads': 0, 'comparison': 'frozen independent components'}
    write_json(base / 'worker.json', result)
    return result
