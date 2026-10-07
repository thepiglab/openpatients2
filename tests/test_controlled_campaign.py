"""Component orchestration must preserve the intervention's causal inputs."""
import json
from pathlib import Path
import time

import pytest
import yaml

from openpatients2 import controlled_campaign as cc, frontier_campaign as fc
from openpatients2.data import write_json
from openpatients2.provenance import json_digest

ROOT = Path(__file__).resolve().parents[1]


def campaign(tmp_path):
    config = yaml.safe_load((ROOT / 'configs/pilot/controlled-components.yaml').read_text())
    for key in ('engine_config', 'extraction_config', 'fixed_source', 'fixed_rosters',
                'fidelity_reference', 'tokenizer_metadata', 'chat_template'):
        config[key] = str(ROOT / config[key])
    work = tmp_path / 'campaign'; work.mkdir()
    return {'root': str(ROOT), 'work': str(work), 'config': config, 'runtime': {},
            'config_sha256': json_digest(config)}


def outputs(path, patient=None):
    path.mkdir(parents=True, exist_ok=True)
    (path / 'patients.jsonl').write_text(json.dumps(patient or {'source': {'record_id': 'PMC1.1:p1'}}) + '\n')
    write_json(path / 'rosters.json', {'articles': [{'article_id': 'PMC1.1'}]})
    write_json(path / 'visual-annotations.json', [{'actual_prediction': 'frozen'}])
    return {'outputs': {'patients': str(path / 'patients.jsonl'),
                        'predicted_rosters': str(path / 'rosters.json'),
                        'visual_annotations': str(path / 'visual-annotations.json')},
            'wall_seconds': 1, 'tokens': {'output_tokens': 1, 'reported_output_tokens': 1,
                                         'unknown_output_usage_calls': 0}}


def assignment(c):
    work = Path(c['work']); base = work / 'shards/000'; base.mkdir(parents=True)
    (base / 'sample.jsonl.gz').write_bytes(b'fixed source')
    write_json(base / 'media.json', {'figures': [], 'complete': True})
    return {'articles': ['PMC1.1'], 'sample': str(base / 'sample.jsonl.gz'), 'media': str(base / 'media.json')}


def test_controlled_plan_preserves_bounded_scheduler_and_complete_baseline(tmp_path):
    c = campaign(tmp_path)
    assert fc.validate_plan(c['config']) and fc.variants(c) == cc.VARIANTS
    jobs = [fc.job_command(c, p, s) for p, s in fc.stages(c)]
    workers = [j for j in jobs if '--gres=gpu:b200:1' in j]
    assert len(workers) == 4
    assert all('--cpus-per-task=8' in j and '--mem=60G' in j and '--time=01:30:00' in j for j in workers)
    configs = [fc.trial_config(c, v, 8) for v in cc.VARIANTS]
    assert all(config == configs[0] for config in configs)
    assert configs[0]['experimental_pipeline'] == 'original'
    assert configs[0]['coverage_repair'] and configs[0]['audit_claims']


@pytest.mark.parametrize('field,value', [('gpu_workers', 8), ('worker_mem_gb', 63),
    ('gpu_minutes', 120), ('variants', list(fc.VARIANTS)), ('separate_gepa', True)])
def test_controlled_plan_refuses_resource_or_intervention_drift(tmp_path, field, value):
    c = campaign(tmp_path); c['config'][field] = value
    with pytest.raises(ValueError): fc.validate_plan(c['config'])


def test_frozen_receipt_rejects_prediction_or_source_mutation(tmp_path):
    c = campaign(tmp_path); a = assignment(c)
    baseline = Path(c['work']) / 'baseline'; report = outputs(baseline)
    receipt = cc.freeze_inputs(baseline, report, a)
    cc.verify_frozen(receipt)
    (baseline / 'rosters.json').write_text('{}')
    with pytest.raises(ValueError, match='changed'): cc.verify_frozen(receipt)


@pytest.mark.asyncio
async def test_every_component_receives_same_seed_baseline_without_pixel_or_roster_reinference(tmp_path):
    c = campaign(tmp_path); a = assignment(c); calls = []
    async def baseline(config, sample, out, endpoints, context, arm, **kwargs):
        calls.append(('baseline', kwargs['seed']))
        assert kwargs.get('frozen_rosters') is None
        return outputs(out, {'source': {'record_id': 'PMC1.1:p1'}, 'baseline_seed': kwargs['seed']})
    async def component(config, sample, out, endpoints, context, *, baseline_dir, component, seed):
        calls.append((component, seed, str(baseline_dir)))
        patient = json.loads((baseline_dir / 'patients.jsonl').read_text())
        assert patient['baseline_seed'] == seed
        assert baseline_dir.name == 'live-complete'
        return outputs(out, patient)
    async def warmup(*args): return {}
    rows = await cc.run_trials(c, 0, a, ['http://test'], time.monotonic() + 600,
                              baseline_runner=baseline, component_runner=component, warmup_runner=warmup)
    assert len(rows) == 8 and all(r['status'] == 'completed' for r in rows)
    assert [r for r in calls if r[0] == 'baseline'] == [('baseline', 42), ('baseline', 43)]
    for seed in (42, 43):
        subset = [r for r in rows if r['seed'] == seed]
        assert subset[0]['variant'] == 'live-complete'
        assert len({r['frozen_baseline_sha256'] for r in subset}) == 1
        assert all('output_sha256' in r for r in subset)
        assert len({r[2] for r in calls if len(r) == 3 and r[1] == seed}) == 1


@pytest.mark.asyncio
async def test_failed_baseline_marks_dependent_arms_unavailable(tmp_path):
    c = campaign(tmp_path); a = assignment(c)
    async def baseline(*args, **kwargs): raise ValueError('baseline failed')
    async def component(*args, **kwargs): pytest.fail('Dependent component must not run')
    async def warmup(*args): return {}
    rows = await cc.run_trials(c, 0, a, [], time.monotonic() + 600,
                              baseline_runner=baseline, component_runner=component, warmup_runner=warmup)
    assert sum(r['status'] == 'failed' for r in rows) == 2
    assert sum(r['status'] == 'unavailable' for r in rows) == 6


@pytest.mark.asyncio
async def test_component_cannot_change_frozen_figure_predictions(tmp_path):
    c = campaign(tmp_path); a = assignment(c)
    async def baseline(config, sample, out, *args, **kwargs):
        report = outputs(out)
        write_json(out / 'tasks/figure.json', {'task': 'figure_attribution', 'data': {'owner': 'p1'}, 'attempts': []})
        return report
    async def component(config, sample, out, *args, **kwargs):
        report = outputs(out)
        write_json(out / 'tasks/figure.json', {'task': 'figure_attribution', 'data': {'owner': 'p2'}, 'attempts': [],
                   'origin': 'frozen_baseline', 'excluded_from_generated_task_metrics': True})
        return report
    async def warmup(*args): return {}
    rows = await cc.run_trials(c, 0, a, [], time.monotonic() + 600,
                              baseline_runner=baseline, component_runner=component, warmup_runner=warmup)
    assert sum(r['status'] == 'completed' for r in rows) == 2
    assert all('frozen figure' in r['error'] for r in rows if r['status'] == 'failed')


def test_empty_controlled_campaign_scores_all_gold_for_every_component(tmp_path):
    c = campaign(tmp_path)
    result = fc.aggregate(c)
    required = sum(r['kind'] == 'required' for r in fc.reviewed_reference(c)['checks'])
    assert result['planned_trials'] == 32 and len(result['results']) == 8
    assert result['status'] == 'failed' and not result['paired_comparison_available']
    assert len(result['paired_comparisons']) == 6
    assert all(r['bundle_quality']['clinical']['required'] == required for r in result['results'])
    assert all(not r['paired_comparison_available'] for r in result['paired_comparisons'])


def test_complete_controlled_pairs_keep_gold_and_validate_baseline_receipts(tmp_path, monkeypatch):
    c = campaign(tmp_path); work = Path(c['work']); score_calls = []
    for shard in range(4):
        base = work / 'shards' / f'{shard:03d}'; base.mkdir(parents=True)
        (base / 'sample.jsonl.gz').write_bytes(b'fixed source')
        write_json(base / 'media.json', {'figures': [], 'complete': True})
        a = {'sample': str(base / 'sample.jsonl.gz'), 'media': str(base / 'media.json'),
             'articles': [f'PMC{shard+1}.1']}
        rows = []
        for seed in (42, 43):
            baseline = base / 'trials' / f'seed{seed}' / 'live-complete'
            reports = {}
            for name in cc.VARIANTS:
                trial = base / 'trials' / f'seed{seed}' / name
                report = outputs(trial, {'source': {'record_id': f'PMC{shard+1}.1:p1'}})
                write_json(trial / 'rosters.json', {'articles': [{'article_id': f'PMC{shard+1}.1'}]})
                reports[name] = report
            frozen = cc.freeze_inputs(baseline, reports['live-complete'], a)
            for name in cc.VARIANTS:
                rows.append({'shard': shard, 'seed': seed, 'variant': name, 'status': 'completed',
                             'report': reports[name], 'frozen_baseline_sha256': frozen['sha256'],
                             'output_sha256': {k: cc.sha256(v) for k, v in reports[name]['outputs'].items()}})
        write_json(base / 'trials.json', rows)
    def score(reference, patients, **kwargs):
        assert len(reference['articles']) == 31 and len(patients) == 4
        score_calls.append(reference)
        return {'score': 0, 'clinical': {'required': 259, 'matched': 0}}
    monkeypatch.setattr('openpatients2.bundle_quality.score_bundle', score)
    result = fc.aggregate(c)
    assert len(score_calls) == 8
    assert result['status'] == 'completed' and result['paired_comparison_available']
    assert all(p['paired_comparison_available'] for p in result['paired_comparisons'])
    receipt = work / 'shards/000/trials/seed42/live-complete/frozen-inputs.json'
    contents = fc.read_json(receipt); contents['origin'] = 'changed'; write_json(receipt, contents)
    with pytest.raises(ValueError, match='receipt changed'): fc.aggregate(c)


def test_component_without_baseline_is_unavailable_even_if_export_exists(tmp_path):
    c = campaign(tmp_path); work = Path(c['work'])
    report = outputs(work / 'shards/000/trials/seed42/attribute-audit')
    write_json(work / 'shards/000/trials.json', [{'shard': 0, 'seed': 42,
               'variant': 'attribute-audit', 'status': 'completed', 'report': report,
               'frozen_baseline_sha256': 'invented'}])
    result = fc.aggregate(c)
    candidate = next(r for r in result['results'] if r['variant'] == 'attribute-audit' and r['seed'] == 42)
    assert candidate['status'] == 'partial' and candidate['delivered_patients'] == 0
    assert candidate['missing_shards'][0]['status'] == 'unavailable'


def test_controlled_export_retains_provenance_and_cells_without_runtime_cache(tmp_path):
    import tarfile
    c = campaign(tmp_path); work = Path(c['work'])
    names = ['shards/000/trials/seed42/live-complete/frozen-inputs.json',
             'shards/000/trials/seed42/table-observations/component-provenance.json',
             'shards/000/trials/seed42/table-observations/table-cells/PMC1.1.json']
    for name in names: write_json(work / name, {'retained': True})
    write_json(work / 'shards/000/runtime-cache/compiled.json', {'excluded': True})
    result = fc.export_results(c, tmp_path / 'review.tar.gz')
    with tarfile.open(result['output']) as archive:
        saved = set(archive.getnames())
    assert set(names) <= saved
    assert not any('runtime-cache' in name for name in saved)
