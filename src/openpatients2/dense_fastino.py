"""Text-only Fastino benchmark; CPU setup/scoring/cleanup, independent B200 inference."""
from __future__ import annotations

import argparse
import asyncio
import copy
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import time

from . import dense_benchmark as b

MODEL = 'fastino/Fastino-Nemotron-3.5-Lightning-Healthcare'
REVISION = 'c602dcde62676c42bb9a31647d4fd8697e7c9e64'
ARMS = ('off', 'efficient', 'on')

TEMPLATE_SHA = '82753bef5cedc4932c1ed509b5c9a12be680fd86d1adb65bc3f7398d11c8eebc'
RUNTIME_PROBE = (
    'import torch,vllm; from vllm.model_executor.models import ModelRegistry; '
    'from vllm.reasoning import ReasoningParserManager; '
    'assert vllm.__version__ == "0.30.0"; '
    'assert "NemotronHForCausalLM" in ModelRegistry.get_supported_archs(); '
    'ReasoningParserManager.get_reasoning_parser("nemotron_v3"); '
    'assert torch.cuda.device_count()==1; assert torch.cuda.get_device_capability(0)==(10,0); '
    'print(vllm.__version__,torch.__version__,torch.cuda.get_device_name(0))'
)


def prepare(root, work, expected=260, hours=8, include_test=False):
    if not 1 <= hours <= 72:
        raise ValueError('hours must be between 1 and 72')
    result = b.prepare(root, work, expected, splits=None if include_test else ('train', 'validation'),
                       make_archive=False, freeze_images=False)
    from .pilot_extract import PilotConfig
    m = b.read(work / 'manifest.json')
    m['git_head'] = os.environ.get('DENSE_SOURCE_REVISION') or m['git_head']
    m['extraction'].update(model_id=MODEL, revision=REVISION, temperature=0.0,
                           max_figures=0, focused_pixels=False, summary_structured_output=False)
    PilotConfig.model_validate(m['extraction'])
    m.update(format='dense-fastino-benchmark/2', model={'id': MODEL, 'revision': REVISION},
             drafter=None, template_sha256=TEMPLATE_SHA, text_only=True,
             fastino={'hours': hours, 'include_test': include_test, 'arms': list(ARMS), 'reasoning_modes': {mode: PilotConfig.model_validate({**m['extraction'], 'fastino_reasoning': mode}).template_kwargs() for mode in ARMS},
                      'cleanup': 'Delete campaign-owned weights/download cache/container only after complete verified export'})
    m['scope'] = {**b.SCOPE, 'figures': 'caption attribution only; no pixels',
                  'ehr_stages': ['measurement_series_extraction'],
                  'unsupported': ['figure pixels', 'figure EHR labels', 'figure privacy flags',
                                  'supplement classification', 'supplement clinical extraction']}
    shutil.copyfile(root / 'docs/DENSE_FASTINO.md', work / 'code/docs/DENSE_FASTINO.md')
    m['files'] = b.inventory(work, ['code', 'inputs', 'reference'])
    for mode in ARMS:
        arm = work / 'arms' / mode
        arm.mkdir(parents=True)
        for folder in ('code', 'inputs', 'reference'):
            shutil.copytree(work / folder, arm / folder, copy_function=os.link)
        am = copy.deepcopy(m)
        am.pop('fastino')
        am['experiment_arm'] = mode
        am['extraction']['fastino_reasoning'] = mode
        am['files'] = b.inventory(arm, ['code', 'inputs', 'reference'])
        b.write(arm / 'manifest.json', am)
        m['files'][f'arms/{mode}/manifest.json'] = b.sha(arm / 'manifest.json')
    b.write(work / 'manifest.json', m)
    b.write(work / 'prepared.json', {'manifest_sha256': b.sha(work / 'manifest.json')})
    return {**result, 'archive': None, 'model': MODEL, 'hours': hours, 'arms': list(ARMS), 'article_runs': len(ARMS)*m['article_count']}


def check(work):
    m = b.check_snapshot(work)
    if m.get('format') not in ('dense-fastino-benchmark/1', 'dense-fastino-benchmark/2') or m['model'] != {'id': MODEL, 'revision': REVISION}:
        raise ValueError('Not a pinned Fastino campaign')
    return m


def setup(work):
    check(work)
    started = time.time()
    runtime = work / 'runtime'
    runtime.mkdir(exist_ok=True)
    if runtime.is_symlink():
        raise ValueError('Runtime must be campaign-owned, not a symlink')
    owner = {'work': str(work.resolve()), 'model': MODEL, 'revision': REVISION}
    marker = runtime / 'model-owner.json'
    if marker.exists() and b.read(marker) != owner:
        raise ValueError('Runtime ownership mismatch')
    # All download caches, including Xet chunks, are private to this campaign.
    os.environ['HF_HOME'] = str(runtime / 'download-cache')
    os.environ['HF_XET_CACHE'] = str(runtime / 'download-cache/xet')
    for name in ('weights', 'download-cache', 'vllm-0.30.0.sif'):
        if (runtime / name).is_symlink():
            raise ValueError('Refusing shared/symlink runtime artifact')
    b.write(marker, owner)
    from huggingface_hub import snapshot_download
    snapshot_download(repo_id=MODEL, revision=REVISION, local_dir=runtime / 'weights',
                      cache_dir=runtime / 'download-cache/hub',
                      allow_patterns=['*.json', '*.jinja', '*.safetensors'])
    weights = runtime / 'weights'
    config = b.read(weights / 'config.json')
    if config['architectures'] != ['NemotronHForCausalLM'] or config['max_position_embeddings'] < b.CONTEXT:
        raise ValueError('Unexpected model architecture/context')
    if b.sha(weights / 'chat_template.jinja') != TEMPLATE_SHA:
        raise ValueError('Pinned chat template changed')
    sif = runtime / 'vllm-0.30.0.sif'
    if not sif.exists():
        subprocess.run(['apptainer', 'pull', str(sif), b.IMAGE], check=True)
    # CPU compatibility/tokenizer check before allocating any GPU time.
    probe = (
        'import sys,vllm; from transformers import AutoTokenizer; '
        'from vllm.model_executor.models import ModelRegistry; '
        'from vllm.reasoning import ReasoningParserManager; '
        'assert vllm.__version__=="0.30.0"; '
        'assert "NemotronHForCausalLM" in ModelRegistry.get_supported_archs(); '
        'ReasoningParserManager.get_reasoning_parser("nemotron_v3"); '
        't=AutoTokenizer.from_pretrained(sys.argv[1],local_files_only=True); '
        's=t.apply_chat_template([{"role":"user","content":"test"}],tokenize=False,'
        'add_generation_prompt=True,enable_thinking=False); '
        'assert s.endswith("<|im_start|>assistant\\n<think></think>"); '
        'assert len(t.encode(s))>0; '
        'cases=[{}, {"medium_effort": True}]; '
        'rendered=[t.apply_chat_template([{"role":"user","content":"test"}],tokenize=False,'
        'add_generation_prompt=True,enable_thinking=True,**kw) for kw in cases]; '
        'assert all(x.endswith("<|im_start|>assistant\\n<think>\\n") for x in rendered); '
        'assert "{reasoning effort: efficient}" in rendered[1]; '
        'assert "{reasoning effort: efficient}" not in rendered[0]; '
        'assert len(set([s]+rendered))==3; print("All three native reasoning modes verified")'
    )
    result = subprocess.run(['apptainer', 'exec', '--cleanenv', '--bind', str(work)+':'+str(work),
                             '--env', 'HF_HUB_OFFLINE=1,TRANSFORMERS_OFFLINE=1', str(sif),
                             '/usr/bin/python3', '-c', probe, str(weights)], text=True, capture_output=True)
    b.write(work / 'results/setup-probe.json', {'returncode': result.returncode,
                                               'stdout': result.stdout, 'stderr': result.stderr})
    result.check_returncode()
    files = {}
    for path in [sif] + [p for p in weights.rglob('*') if p.is_file() and '.cache' not in p.parts]:
        files[str(path.relative_to(work))] = {'sha256': b.sha(path), 'bytes': path.stat().st_size,
                                            'mtime_ns': path.stat().st_mtime_ns}
    b.write(work / 'runtime.json', {'ready': True, 'files': files, 'image': b.IMAGE,
                                   'model_revision': REVISION, 'template_sha256': TEMPLATE_SHA,
                                   'setup_started_unix': started, 'setup_seconds': time.time()-started})
    return {'ready': True, 'runtime_files': len(files)}


def serving(work, shard, logdir):
    from .dense_benchmark_serving import ServingConfig
    cache = work / f'runtime/worker-{shard}'
    cache.mkdir(parents=True, exist_ok=True)
    return ServingConfig(name='fastino-bf16-text', backend='vllm', version='0.30.0', image_reference=b.IMAGE,
        sif=str(work / 'runtime/vllm-0.30.0.sif'), model_path=str(work / 'runtime/weights'), model_id=MODEL,
        container_python='/usr/bin/python3', replicas=1, tensor_parallel=1, expert_parallel=False,
        reasoning_parser='nemotron_v3', max_model_len=b.CONTEXT, max_num_seqs=64, max_batched_tokens=32768,
        gpu_memory_utilization=.9, port=8600+shard, rpc_port=18000+100*shard,
        extra_args=['--model-impl', 'vllm', '--chat-template-content-format', 'string',
                    '--generation-config', 'vllm', '--chat-template',
                    str(work / 'runtime/weights/chat_template.jinja')],
        environment={'PYTHONDONTWRITEBYTECODE': '1', 'VLLM_WORKER_MULTIPROC_METHOD': 'spawn',
                     'HF_HOME': str(cache / 'hf'), 'HF_HUB_CACHE': str(cache / 'hf/hub'),
                     'HF_XET_CACHE': str(cache / 'hf/xet'), 'HF_MODULES_CACHE': str(cache / 'hf/modules'),
                     'VLLM_CACHE_ROOT': str(cache / 'vllm'), 'TRITON_CACHE_DIR': str(cache / 'triton'),
                     'TORCHINDUCTOR_CACHE_DIR': str(cache / 'inductor'), 'XDG_CACHE_HOME': str(cache / 'xdg')})


def assert_idle(work):
    ids = [str(j['id']) for p in (work / 'submissions').glob('*.json') for j in b.read(p)['jobs']
           if j['phase'] in ('setup', 'worker')]
    if ids:
        # Query the user queue rather than stale job IDs that Slurm may have purged.
        result = subprocess.run(['squeue', '-h', '-u', os.environ['USER'], '-o', '%i'],
                                text=True, capture_output=True, check=True)
        if set(ids) & set(result.stdout.split()):
            raise ValueError('Setup/GPU jobs are still active; cannot score/export/clean up yet')


def cleanup(work):
    check(work)
    assert_idle(work)
    verify(work)
    archive = work / 'results.tar.gz'
    if b.sha(archive) != archive.with_name(archive.name + '.sha256').read_text().split()[0]:
        raise ValueError('Result archive checksum mismatch; retaining model')
    runtime = work / 'runtime'
    if runtime.is_symlink() or b.read(runtime / 'model-owner.json') != {
            'work': str(work.resolve()), 'model': MODEL, 'revision': REVISION}:
        raise ValueError('Runtime ownership mismatch; retaining model')
    targets = [runtime / name for name in ('weights', 'download-cache', 'vllm-0.30.0.sif')]
    if any(p.is_symlink() or p.resolve().parent != runtime.resolve() for p in targets):
        raise ValueError('Cleanup target is not campaign-owned')
    for path in targets:
        if path.is_dir():
            shutil.rmtree(path)
        elif path.exists():
            path.unlink()
    receipt = {'completed_unix': time.time(), 'removed': [str(p.relative_to(work)) for p in targets],
               'archive_sha256': b.sha(archive)}
    b.write(work / 'cleanup.json', receipt)
    return receipt


def aggregate_single(work):
    check(work)
    assert_idle(work)
    report = b.aggregate(work)
    c = report['score_overall']['clinical_items']
    denominator = 5*c['tp'] + 4*c['fn'] + c['fp']
    report['clinical_f2'] = 5*c['tp']/denominator if denominator else 1.0
    report['timing_notes'][0] = 'Article latency includes live discovery, text/caption extraction, repairs and text measurement series; no pixels.'
    report['comparison_note'] = ('Compare clinical precision/recall/F1/F2 on identical article IDs and splits. '
        'Full reference denominators remain intact. Figure pixel/EHR/privacy and supplements are unsupported. '
        'Glimmer pixel-inclusive latency is not an apples-to-apples text-only speed comparison.')
    report['clinical_by_split'] = {}
    for split in b.read(work / 'manifest.json')['splits']:
        counts = b.read(work / f'summary/scores-{split}.json')['overall']['clinical_items']
        denom = 5*counts['tp'] + 4*counts['fn'] + counts['fp']
        report['clinical_by_split'][split] = {**counts, 'f2': 5*counts['tp']/denom if denom else 1.0}
    b.write(work / 'summary/report.json', report)
    (work / 'summary/report.md').write_text('# Fastino text-only benchmark\n\n' + report['comparison_note'] +
        '\n\n```json\n' + json.dumps(report, indent=2) + '\n```\n')
    hashes = b.inventory(work, ['results', 'summary', 'submissions'])
    if (work / 'runtime.json').exists():
        hashes['runtime.json'] = b.sha(work / 'runtime.json')
    b.write(work / 'summary/integrity.json', {'manifest_sha256': b.sha(work / 'manifest.json'), 'files': hashes})
    if report['complete']:
        b.verify(work)
    b.export(work)
    if report['complete']:
        cleanup(work)
    return report


def campaigns(work):
    modes = b.read(work / 'manifest.json')['fastino'].get('arms')
    return [work / 'arms' / mode for mode in modes] if modes else [work]


def status(work):
    if campaigns(work) == [work]:
        return b.status(work)
    reports = {arm.name: b.status(arm) for arm in campaigns(work)}
    return {'arms': {mode: {**r, 'pending': len(r['pending']), 'pending_sample': r['pending'][:8]}
                     for mode, r in reports.items()},
            'complete': all(not r['pending'] for r in reports.values()),
            'archive_available': (work / 'results.tar.gz.sha256').exists(),
            'report': str(work / 'summary/comparison.md')}


def verify(work):
    if campaigns(work) == [work]:
        return b.verify(work)
    check(work)
    for arm in campaigns(work):
        b.verify(arm)
    integrity = b.read(work / 'summary/integrity.json')
    if integrity['manifest_sha256'] != b.sha(work / 'manifest.json'):
        raise ValueError('Campaign manifest changed after scoring')
    for path, digest in integrity['files'].items():
        if b.sha(work / path) != digest:
            raise ValueError('Scored campaign file changed: ' + path)
    result = b.read(work / 'summary/comparison.json')
    if not result['complete']:
        raise ValueError('Incomplete sweep; inspect arm/worker receipts')
    return {'verified': True, 'completed_article_triplets': result['completed_article_triplets'],
            'arms': list(result['arms']), 'test_evaluated': result['test_evaluated']}


def export(work):
    if campaigns(work) == [work]:
        return b.export(work)
    from .dense_overnight import export as export_arms
    return export_arms(work)


def f2(counts):
    denom = 5*counts['tp'] + 4*counts['fn'] + counts['fp']
    return 5*counts['tp']/denom if denom else 1.0


def aggregate(work):
    if campaigns(work) == [work]:
        return aggregate_single(work)
    from .unified_scoring import score
    from .dense_overnight import score_metrics
    started = time.time()
    m = check(work)
    assert_idle(work)
    arms = campaigns(work)
    reports = {arm.name: b.aggregate(arm) for arm in arms}
    refs = b.rows(work / 'reference/unified.jsonl')
    predictions = {arm.name: b.rows(arm / 'summary/predictions.jsonl') for arm in arms}
    receipts = {arm.name: {aid: b.completed_attempt(arm, aid)[1] for aid in refs} for arm in arms}
    paired = {aid for aid in refs if all(receipts[arm.name][aid] for arm in arms)}
    metrics, full_scores, paired_scores = {}, {}, {}
    for split in m['splits']:
        subset = {aid: ref for aid, ref in refs.items() if ref['split'] == split}
        common = {aid: ref for aid, ref in subset.items() if aid in paired}
        full_scores[split], paired_scores[split], metrics[split] = {}, {}, {}
        for arm in arms:
            name = arm.name
            scores = score(subset, {aid: pred for aid, pred in predictions[name].items() if aid in subset})
            full_scores[split][name] = scores
            paired_scores[split][name] = (score(common, {aid: pred for aid, pred in predictions[name].items() if aid in common})
                                         if common else None)
            metrics[split][name] = {**score_metrics(scores), 'clinical_f2': f2(scores['overall']['clinical_items'])}
    timing = []
    for aid, ref in refs.items():
        row = {'article_id': aid, 'split': ref['split'], 'all_modes_completed': aid in paired}
        for arm in arms:
            r = receipts[arm.name][aid]
            row[arm.name + '_seconds'] = r['wall_seconds'] if r else None
        timing.append(row)
    # Ratios use identical completed IDs. Full-run means can be misleading if a
    # slower mode reaches fewer articles before Slurm ends the allocation.
    paired_timing = {}
    for split in m['splits']:
        ids = [aid for aid in paired if refs[aid]['split'] == split]
        sums = {arm.name: sum(receipts[arm.name][aid]['wall_seconds'] for aid in ids) for arm in arms}
        paired_timing[split] = {'articles': len(ids), 'seconds': sums,
            'speedup_vs_off': {name: sums['off']/value if value else None for name, value in sums.items()}}
    workers = [b.read(p) for p in (work / 'results/workers').glob('*/job-*/receipt.json')]
    complete = all(r['complete'] for r in reports.values())
    runtime = b.read(work / 'runtime.json') if (work / 'runtime.json').exists() else {}
    result = {'format': 'fastino-reasoning-comparison/1', 'complete': complete,
        'arms': m['fastino']['reasoning_modes'], 'planned_articles_per_arm': len(refs),
        'completed_article_triplets': len(paired), 'metrics': metrics,
        'all_scores': full_scores, 'scores_completed_triplets': paired_scores,
        'article_seconds': {name: r['article_seconds'] for name, r in reports.items()},
        'paired_timing': paired_timing,
        'output_tokens_per_article_second': {name: r['output_tokens_per_gpu_article_second'] for name, r in reports.items()},
        'operational_error_attempts': {name: r['operational_error_attempts'] for name, r in reports.items()},
        'task_statuses': {name: r['extraction_task_statuses'] for name, r in reports.items()},
        'startup_seconds': b.stats([w['startup_seconds'] for w in workers if 'startup_seconds' in w]),
        'summed_worker_seconds': sum(w['worker_seconds'] for w in workers),
        'observed_worker_span_seconds': max(w['ended_unix'] for w in workers)-min(w['started_unix'] for w in workers) if workers else None,
        'setup_seconds': runtime.get('setup_seconds'), 'scoring_seconds': time.time()-started,
        'test_evaluated': m['fastino']['include_test'],
        'comparison_status': 'complete development comparison' if complete else 'inconclusive: partial coverage',
        'interpretation': 'Same text-only pipeline, sources, greedy decoding, context/output caps and frozen references. '
            'Arm order rotates/reverses by article hash; prefix caches are shared between modes on each worker. '
            'Full scores count unfinished articles as misses; completed-triplet scores may be biased on partial runs. '
            'Article times include repairs/EHR and exclude startup/queue; worker span includes startup and is not CPU/end-to-end duration. '
            'No new Glimmer run. Historical Glimmer used pixels; compare matching IDs/splits for quality and do not call its latency a controlled speed comparison. '
            'No automatic model/prompt promotion; one seed, no claim of statistical significance.'}
    out = work / 'summary'
    b.write_rows(out / 'paired-article-timing.jsonl', timing)
    b.write(out / 'comparison.json', result)
    lines = ['# Fastino reasoning comparison', '', f'Complete: {complete}; complete article triplets: {len(paired)}/{len(refs)}.', '']
    for split, values in metrics.items():
        lines += [f'## {split}', '', '| Mode | Precision | Recall | F1 | F2 | Mean seconds (completed) | p95 seconds |',
                  '|---|---:|---:|---:|---:|---:|---:|']
        for name, values_for_arm in values.items():
            seconds = b.stats([r['wall_seconds'] for aid, r in receipts[name].items() if r and refs[aid]['split'] == split])
            quality = ' | '.join('unavailable' if values_for_arm[k] is None else f'{100*values_for_arm[k]:.2f}%' for k in ('clinical_precision','clinical_recall','clinical_f1','clinical_f2'))
            lines.append(f"| {name} | {quality} | {seconds['mean']} | {seconds['p95']} |")
        lines += ['', 'Matched-triplet timing (speedup >1 means faster than off):', '',
                  '```json', json.dumps(paired_timing[split], indent=2), '```', '']
    lines += [result['interpretation'], '', 'Full metrics, token rates, startup/setup/scoring times and failures: comparison.json.',
              'Per-article CSVs and all original scores: arms/*/summary. CPU export/cleanup duration: cleanup.json beside the archive.', '']
    (out / 'comparison.md').write_text('\n'.join(lines))
    files = b.inventory(work, ['arms', 'results', 'summary', 'submissions'])
    if (work / 'runtime.json').exists():
        files['runtime.json'] = b.sha(work / 'runtime.json')
    b.write(out / 'integrity.json', {'manifest_sha256': b.sha(work / 'manifest.json'), 'files': files})
    if complete:
        verify(work)
    export(work)
    if complete:
        cleanup(work)
        receipt = b.read(work / 'cleanup.json')
        receipt.update(cpu_aggregate_export_cleanup_seconds=time.time()-started,
                       campaign_elapsed_through_cleanup_seconds=time.time()-m['created_unix'])
        b.write(work / 'cleanup.json', receipt)
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    for command in ('prepare', 'setup', 'submit', 'worker', 'aggregate', 'status', 'verify', 'cleanup'):
        p = sub.add_parser(command)
        p.add_argument('--work', type=Path, required=True)
        if command == 'prepare':
            p.add_argument('--root', type=Path, default=Path.cwd())
            p.add_argument('--expected', type=int, default=260)
            p.add_argument('--hours', type=int, default=8)
            p.add_argument('--include-test', action='store_true')
        if command == 'submit':
            p.add_argument('--account', default='cai5724'); p.add_argument('--qos', default='cai5724')
            p.add_argument('--resume', action='store_true'); p.add_argument('--dry-run', action='store_true')
        if command == 'worker':
            p.add_argument('--shard', type=int, required=True)
    args = parser.parse_args(argv)
    work = args.work.resolve()
    if args.command == 'prepare':
        result = prepare(args.root.resolve(), work, args.expected, args.hours, args.include_test)
    else:
        m = check(work)
        if args.command == 'submit':
            result = b.submit(work, args.account, args.qos, m['fastino']['hours'], args.resume, args.dry_run,
                              script_name='dense_fastino.sbatch', campaigns=campaigns(work), gpu_memory='128G')
        elif args.command == 'worker':
            def terminate(signum, frame):
                raise KeyboardInterrupt('Slurm termination; retain partial attempts for resume')
            signal.signal(signal.SIGTERM, terminate)
            result = asyncio.run(b.worker(work, args.shard, campaigns=campaigns(work), serving_factory=serving,
                                          runtime_probe=RUNTIME_PROBE))
            if result['status'] != 'done':
                raise SystemExit('Worker has operational errors; inspect receipts and resume')
        elif args.command in ('status', 'verify'):
            result = globals()[args.command](work)
            if (work / 'cleanup.json').exists():
                result['cleanup'] = b.read(work / 'cleanup.json')
        elif args.command == 'aggregate':
            assert_idle(work)
            try:
                result = aggregate(work)
            except Exception as exc:
                b.write(work / 'summary/aggregation-error.json', {'error': repr(exc)})
                # Preserve the original verified archive if only cleanup failed.
                if not (work / 'results.tar.gz').exists():
                    export(work)
                raise
            if not result['complete']:
                raise SystemExit('Partial results exported; weights retained for resume')
        else:
            result = globals()[args.command](work)
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
