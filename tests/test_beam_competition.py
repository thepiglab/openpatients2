import asyncio
import gzip
import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest

from openpatients2 import competition_campaign as c, competition_selection
from openpatients2 import dense_benchmark as b, dense_nemotron_super as s
from openpatients2 import ehr_pipeline, fhir_campaign, pilot_extract
from openpatients2.benchmark_reporting import paired_speculation
from test_dense_benchmark import campaign as base_campaign
from test_dense_nemotron_super import campaign
from test_pilot_extract import article

ROOT = Path(__file__).resolve().parents[1]
PROFILES = ['beam-nvfp4-dflash2', 'beam-nvfp4-no-spec']


@pytest.mark.parametrize('mode', ['competition', 'overnight'])
def test_competition_launcher_resets_stale_selection_and_uses_twelve_hours(tmp_path, mode):
    scripts = tmp_path / 'scripts'; scripts.mkdir()
    shutil.copyfile(ROOT / 'scripts/run_dense_beam.sh', scripts / 'run_dense_beam.sh')
    (scripts / 'run_dense_nemotron_super.sh').write_text('env\n')
    env = {**os.environ, 'DENSE_SUPER_PROFILES': 'spark', 'DENSE_INCLUDE_TEST': '1',
        'DENSE_SUPER_SPLITS': 'test', 'DENSE_SUPER_ARTICLE_IDS': 'PMC1',
        'DENSE_SUPER_LIMIT_ARTICLES': '2', 'DENSE_SUPER_WITH_IMAGES': '1'}
    env.pop('DENSE_HOURS', None)
    result = subprocess.check_output(['bash', str(scripts / 'run_dense_beam.sh'), mode], env=env, text=True)
    values = dict(line.split('=', 1) for line in result.splitlines() if '=' in line)
    assert values['DENSE_HOURS'] == '12' and values['DENSE_SUPER_COMPETITION'] == '1'
    assert values['DENSE_SUPER_PROFILES'].split() == PROFILES
    assert values['DENSE_SUPER_ARTICLE_IDS'] == values['DENSE_SUPER_LIMIT_ARTICLES'] == ''
    assert values['DENSE_SUPER_SPLITS'] == 'train' and values['DENSE_INCLUDE_TEST'] == '0'
    assert values['DENSE_SUPER_WITH_IMAGES'] == '0'
    assert '--competition' in (ROOT / 'scripts/run_dense_nemotron_super.sh').read_text()


def test_prepare_uses_fresh_fifty_reference_and_freezes_both_end_to_end_arms(campaign, tmp_path, monkeypatch):
    _, old_refs, root = campaign
    selected = root / 'configs/hipergator/nemotron-super'; selected.mkdir(parents=True)
    refs = []
    ids = [f'PMC{2000+i}.1' for i in range(50)]
    for aid in ids:
        source = {**article(), 'article_id': aid}
        b.write(root / 'benchmarks/dense/articles' / aid / 'source.json', source)
        refs.append({**old_refs[0], 'article_id': aid, 'split': 'train', 'text_sha256': source['text_sha256']})
    refpath = selected / 'competition-50-reference.jsonl.gz'
    with gzip.open(refpath, 'wt') as out:
        out.write(''.join(json.dumps(r) + '\n' for r in refs))
    selection = {'article_ids': ids, 'reference': {'path': str(refpath.relative_to(root))}}
    b.write(selected / 'competition-50.json', selection)
    monkeypatch.setattr(competition_selection, 'validate_selection', lambda *a: {'verified': True})
    (root / c.CATALOG).parent.mkdir(parents=True)
    shutil.copyfile(ROOT / c.CATALOG, root / c.CATALOG)
    work = tmp_path / 'competition'
    result = s.prepare(root, work, profiles=PROFILES, competition=True, scheduler='packed',
                       with_images=False, hours=12)
    assert result['article_runs'] == 100
    manifest = s.check(work)
    assert manifest['article_count'] == 50 and manifest['splits'] == {'train': 50}
    assert set(b.rows(work / 'reference/unified.jsonl')) == set(ids)
    assert set(ids).isdisjoint(r['article_id'] for r in old_refs)
    assert manifest['nemotron_super']['scheduler']['waves'][0]['totals'] == {
        'gpus': 8, 'cpus': 32, 'memory_gib': 192}
    for name in PROFILES:
        arm = work / 'arms' / (name + '-text')
        cfg = b.read(arm / 'manifest.json')
        assert cfg['article_timeout_seconds'] == 3600
        assert all(cfg['extraction'][key] for key in ('bounded_clinical_extraction',
            'timeline_sparse_relations', 'literal_series_time_cells'))
        assert b.sha(arm / cfg['competition']['catalog_path']) == c.CATALOG_SHA256
        assert cfg['competition']['coding']['time_budget_seconds'] == 300
    plan = s.submit(work, 'cai5724', 'cai5724', dry_run=True)
    workers = [cmd for cmd in plan['commands_without_dependencies'] if cmd[-3] == 'worker']
    assert len(workers) == 2
    assert all('--gres=gpu:b200:4' in cmd and '--mem=96G' in cmd and '--time=12:00:00' in cmd for cmd in workers)
    with pytest.raises(ValueError, match='without selection overrides'):
        s.prepare(root, tmp_path / 'bad-selection', profiles=PROFILES, competition=True,
                  scheduler='packed', with_images=False, limit_articles=2)
    (root / c.CATALOG).write_bytes(b'changed')
    with pytest.raises(ValueError, match='catalog changed'):
        c.configuration(root, selection)


def ledger(path, tokens, task):
    b.write_rows(path, [{'task': task, 'response': {'reasoning_status': 'returned_text', 'reasoning_text': 'raw trace'},
        'metrics': {'prompt_tokens': tokens, 'completion_tokens': 20, 'reasoning_tokens': 10, 'finish_reason': 'stop'}}])


@pytest.mark.parametrize('mapping_error', [False, True])
async def test_article_coding_uses_prediction_and_counts_all_tokens_even_on_export_error(tmp_path, monkeypatch, mapping_error):
    work, refs, manifest = base_campaign(tmp_path, 1)
    (work / 'code/configs/terminology').mkdir(parents=True, exist_ok=True)
    shutil.copyfile(ROOT / c.CATALOG, work / 'code' / c.CATALOG)
    manifest.update(extraction=b.extraction_config(), competition=c.configuration(ROOT, {'article_ids': ['fixture']}))
    b.write(work / 'manifest.json', manifest)
    class Runner:
        def __init__(self, config, output, *args): self.output = output
        async def run(self, source, **kwargs):
            ledger(self.output / 'attempts.jsonl', 100, 'observations')
            return {'task_statuses': {'valid': 1}, 'patients_extracted': 1, 'selected_articles': 1,
                    'eligible_articles': 1, 'articles': [{}], 'valid_tasks': 1, 'task_count': 1}
        async def close(self): pass
    async def ehr(config, source, run, out, *args, **kwargs):
        b.write_rows(out / 'data/unified.jsonl', refs)
        ledger(out / 'calls/attempts.jsonl', 200, 'measurement_series_extraction')
        return {'articles': [{'errors': {}}]}
    async def stage(input_path, sources_root, output, endpoint, arm_manifest, catalog_path, **kwargs):
        assert input_path.name == 'unified.jsonl' and 'reference' not in str(input_path)
        assert b.rows(input_path) == {r['article_id']: r for r in refs}
        assert sources_root == work and kwargs['time_budget_seconds'] == 300
        ledger(output / 'packet/coding-calls/batch/attempts.jsonl', 300, 'terminology_selection_batch')
        if mapping_error: raise ValueError('export fixture failure')
        return {'status': 'done', 'complete': True, 'exports': {}, 'coding': {}}
    monkeypatch.setattr(pilot_extract, 'PilotRunner', Runner)
    monkeypatch.setattr(ehr_pipeline, 'run_ehr_stages', ehr)
    monkeypatch.setattr(fhir_campaign, 'run_article_stage', stage)
    aid = manifest['articles'][0]['article_id']
    attempt = work / 'results/articles' / aid / 'attempt-0000'
    receipt = await b.run_article(work, manifest['articles'][0], 'http://localhost:8600/v1', attempt)
    assert receipt['status'] == 'done' and receipt['has_prediction']
    assert receipt['model_calls'] == 3 and receipt['prompt_tokens'] == 600
    assert receipt['completion_tokens'] == 60 and receipt['reasoning_tokens'] == 30
    assert receipt['extraction_and_ehr_usage']['prompt_tokens'] == 300
    assert receipt['fhir']['usage']['prompt_tokens'] == 300 and receipt['fhir_seconds'] >= 0
    assert receipt['fhir']['status'] == ('error' if mapping_error else 'done')
    assert bool(receipt['fhir']['complete']) is not mapping_error
    assert b.needs_article(work, aid) is mapping_error
    assert bool(b.status(work)['pending_fhir']) is mapping_error
    if mapping_error:
        before = b.sha(attempt / 'ehr/data/unified.jsonl')
        mapping_error = False
        retried = await b.run_article(work, manifest['articles'][0], 'http://localhost:8600/v1', attempt)
        assert retried['fhir']['complete'] and retried['fhir_resume_count'] == 1
        assert retried['model_calls'] == 4 and retried['prompt_tokens'] == 900
        assert retried['fhir']['usage']['prompt_tokens'] == 600
        assert b.sha(attempt / 'ehr/data/unified.jsonl') == before
        assert len(list((attempt / 'fhir-history').glob('*/article-receipt.json'))) == 1
        assert not b.needs_article(work, aid) and not b.status(work)['pending']
        assert retried['wall_seconds'] >= receipt['wall_seconds']


def test_partial_fhir_and_missing_usage_do_not_look_like_full_success():
    row = {'fhir': {'status': 'partial', 'complete': False, 'elapsed_seconds': 10, 'exports': {},
        'coding': {'decisions': {'abstain': 2}, 'requests': 5, 'pending_requests': ['a','b','c']},
        'usage': {'model_calls': 1, 'prompt_tokens': None, 'completion_tokens': 10, 'reasoning_tokens': None}}}
    report = c.summarize([row], 50)
    assert not report['complete'] and report['stage_missing_articles'] == 49
    assert report['coding_usage']['prompt_tokens'] is None and report['coding_pending_requests'] == 3
    assert not report['release_ready'] and not report['clinical_accuracy_verified']


def test_paired_speculation_separates_extraction_from_fhir_time():
    row = {'wall_seconds': 10, 'extraction_seconds': 8, 'ehr_seconds': 1, 'fhir_seconds': 1}
    result = paired_speculation({'off': {'a': row}, 'on': {'a': {**row, 'wall_seconds': 8, 'extraction_seconds': 6}}},
        {'off': {'profile': PROFILES[1]}, 'on': {'profile': PROFILES[0]}})[0]
    assert result['stage_comparison']['extraction_seconds']['speedup'] == 8/6
    assert result['stage_comparison']['fhir_seconds']['speedup'] == 1


def test_competition_new_completed_attempt_supersedes_old_no_prediction(tmp_path):
    b.write(tmp_path / 'manifest.json', {'competition': {'format': 'fixture'}})
    for number, predicted in [(0, False), (1, True)]:
        p = tmp_path / 'results/articles/PMC1' / f'attempt-{number:04d}'
        b.write_rows(p / 'ehr/data/unified.jsonl', [{'article_id': 'PMC1'}] if predicted else [])
        b.write(p / 'receipt.json', {'article_id': 'PMC1', 'status': 'done', 'has_prediction': predicted,
            'prediction_sha256': b.sha(p / 'ehr/data/unified.jsonl'), 'fhir': {'complete': predicted}})
    path, row = b.completed_attempt(tmp_path, 'PMC1')
    assert path.name == 'attempt-0001' and row['has_prediction']
    assert not b.needs_article(tmp_path, 'PMC1')


def test_competition_dataset_exports_passed_bundles_and_indexes_omissions(tmp_path, monkeypatch):
    attempt = tmp_path / 'arms/beam/results/articles/PMC1/attempt-0000'
    bundle = {'resourceType': 'Bundle', 'id': 'patient-one', 'type': 'collection'}
    export = attempt / 'fhir/uncoded'
    b.write(export / 'bundles/p1.json', bundle)
    b.write(export / 'quarantine/p2.json', {**bundle, 'id': 'patient-two'})
    patients = [{'patient_id': pid, 'status': status, 'bundle': file, 'sha256': b.sha(export / file)}
        for pid, status, file in [('p1', 'passed', 'bundles/p1.json'), ('p2', 'quarantined', 'quarantine/p2.json')]]
    b.write(export / 'report.json', {'statuses': {'passed': 1, 'quarantined': 1}, 'patients': patients})
    monkeypatch.setattr(b, 'completed_attempt', lambda work, aid:
        (attempt, {'fhir': {'status': 'partial'}, 'nonempty_pipeline_valid': False}) if aid == 'PMC1' else (None, None))
    folder = tmp_path / c.export_dataset(tmp_path, 'beam', ['PMC1', 'PMC2'])
    assert [json.loads(line) for line in (folder / 'uncoded.bundles.jsonl').read_text().splitlines()] == [bundle]
    index = b.read(folder / 'manifest.json')
    assert not index['release_ready'] and index['articles'][1]['status'] == 'pending'
    assert index['articles'][0]['exports']['uncoded']['patients'][1]['status'] == 'quarantined'
    assert index['files']['uncoded.bundles.jsonl'] == b.sha(folder / 'uncoded.bundles.jsonl')
    b.write(export / 'bundles/p1.json', {**bundle, 'id': 'changed'})
    with pytest.raises(ValueError, match='FHIR output changed'):
        c.export_dataset(tmp_path, 'beam', ['PMC1', 'PMC2'])
