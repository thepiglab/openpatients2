import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from openpatients2 import dense_benchmark as b


ROOT = Path(__file__).resolve().parents[1]


def campaign(tmp_path, n=3):
    refs = b.rows(ROOT / 'tests/fixtures/unified_scoring_reference.jsonl')
    ref = next(iter(refs.values()))
    values = []
    for i in range(n):
        values.append({**ref, 'article_id': f'PMC{100+i}.1', 'split': ['train', 'validation', 'test'][i % 3]})
    b.write_rows(tmp_path / 'reference/unified.jsonl', values)
    items = [{'article_id': v['article_id'], 'split': v['split'], 'shard': i % 4} for i,v in enumerate(values)]
    m = {'articles': items, 'files': {}, 'scope': b.SCOPE, 'splits': {'train': 1, 'validation': 1, 'test': 1}, 'media': {}}
    b.write(tmp_path / 'manifest.json', m)
    return tmp_path, values, m


def finish(work, ref, seconds=10, prediction=True):
    aid = ref['article_id']
    folder = work / 'results/articles' / aid / 'attempt-0000'
    b.write_rows(folder / 'ehr/data/unified.jsonl', [ref] if prediction else [])
    receipt = {'article_id': aid, 'split': ref['split'], 'shard': 0, 'status': 'done', 'wall_seconds': seconds,
               'prediction_sha256': b.sha(folder / 'ehr/data/unified.jsonl'), 'has_prediction': prediction,
               'completion_tokens': 100, 'all_extraction_tasks_valid': prediction,
               'task_statuses': {'valid': 3} if prediction else {'failed': 1}}
    b.write(folder / 'receipt.json', receipt)
    return folder


def test_missing_articles_keep_full_denominator_and_timing(tmp_path):
    work, refs, _ = campaign(tmp_path)
    finish(work, refs[0], seconds=11)
    # Discovery failed: completed attempt with no row, and a third interrupted article.
    finish(work, refs[1], seconds=29, prediction=False)
    result = b.aggregate(work)
    assert result['completed'] == 2 and not result['complete']
    assert result['article_seconds'] == {'n': 2, 'mean': 20, 'median': 20, 'p95': 29, 'max': 29, 'sum': 40}
    scores = b.read(work / 'summary/scores.json')
    assert scores['coverage']['reference_articles'] == 3
    assert scores['coverage']['predicted_articles'] == 1
    assert set(scores['coverage']['missing_articles']) == {r['article_id'] for r in refs[1:]}
    assert scores['overall']['clinical_items']['recall'] == pytest.approx(1/3)
    assert len((work / 'summary/article-timing.csv').read_text().splitlines()) == 4
    with pytest.raises(ValueError, match='lack a completed attempt'):
        b.verify(work)


def test_complete_reaggregate_verify_and_tamper(tmp_path):
    work, refs, _ = campaign(tmp_path)
    for ref in refs:
        finish(work, ref)
    b.aggregate(work)
    b.aggregate(work)  # integrity must not hash itself from the previous report
    assert b.verify(work)['verified']
    (work / 'summary/scores.json').write_text('{}')
    with pytest.raises(ValueError, match='Scored output changed'):
        b.verify(work)


def test_completed_result_change_refused(tmp_path):
    work, refs, _ = campaign(tmp_path)
    folder = finish(work, refs[0])
    (folder / 'ehr/data/unified.jsonl').write_text('')
    with pytest.raises(ValueError, match='Completed result changed'):
        b.status(work)


def test_recipe_and_no_gold_conditioning(tmp_path, monkeypatch):
    monkeypatch.setenv('CUDA_VISIBLE_DEVICES', '3')
    from openpatients2.dense_benchmark_serving import render
    cfg = b.serving(tmp_path, 2, tmp_path)
    command = render(cfg, str(tmp_path))[0]
    assert command['devices'] == ['3']
    assert cfg.tensor_parallel == 1 and cfg.replicas == 1
    assert cfg.speculative_config['method'] == 'dflash'
    assert cfg.speculative_config['num_speculative_tokens'] == 15
    assert cfg.max_model_len == 65536 and cfg.max_batched_tokens == 32768 and cfg.max_num_seqs == 64
    assert cfg.gpu_memory_utilization == .9 and cfg.prefix_cache
    assert '--language-model-only' not in command['argv']
    assert '--quantization' not in command['argv']
    assert '--limit-mm-per-prompt' in command['argv']
    assert str(tmp_path / 'runtime/weights') + ':' + str(tmp_path / 'runtime/weights') + ':ro' in command['argv']
    extraction = b.extraction_config()
    assert extraction['max_articles'] == 1 and extraction['max_patients_per_article'] == 48
    assert extraction['reasoning_strength'] == 'medium' and extraction['max_repair_rounds'] == 2
    assert not extraction['prompt_overrides'] and not extraction['coverage_repair']
    assert b.sha(ROOT / 'configs/hipergator/dense-benchmark/chat_template.jinja') == b.TEMPLATE_SHA


def test_missing_usage_and_nonissued_failure_are_visible(tmp_path):
    p = tmp_path / 'attempts.jsonl'
    p.write_text('\n'.join(json.dumps(r) for r in [
        {'response': {}, 'metrics': {'prompt_tokens': 10, 'completion_tokens': 7}, 'errors': []},
        {'response': {'error': 'timeout'}, 'metrics': {}, 'errors': ['timeout']},
        {'response': None, 'metrics': {}, 'errors': ['context_overflow_no_source_truncation']},
    ]))
    r = b.ledger_metrics([p])
    assert r['model_calls'] == 2 and r['failed_attempts'] == 2
    assert r['completion_tokens'] is None and r['reported_completion_tokens'] == 7
    assert r['unknown_completion_tokens'] == 1


def test_submission_preflight_dependencies_and_reverse_release(tmp_path, monkeypatch):
    work, _, _ = campaign(tmp_path, 4)
    seen, counter = [], 100
    def run(argv, **kwargs):
        nonlocal counter
        seen.append(argv[:])
        if argv[0] == 'sbatch' and '--test-only' not in argv:
            counter += 1
            return SimpleNamespace(stdout=str(counter)+'\n')
        return SimpleNamespace(stdout='')
    monkeypatch.setattr(b.subprocess, 'run', run)
    result = b.submit(work, 'account', 'qos', 12)
    assert len(result['jobs']) == 6
    assert all('--test-only' in c for c in seen[:6])
    jobs = result['jobs']
    assert all('--dependency=afterany:101' in j['argv'] for j in jobs[1:5])
    assert '--dependency=afterany:102:103:104:105' in jobs[5]['argv']
    assert [c[-1] for c in seen if c[:2] == ['scontrol', 'release']] == ['106','105','104','103','102','101']


def test_submission_failure_cancels_only_own_jobs(tmp_path, monkeypatch):
    work, _, _ = campaign(tmp_path)
    seen, submits = [], 0
    def run(argv, **kwargs):
        nonlocal submits
        seen.append(argv)
        if argv[0] == 'sbatch' and '--test-only' not in argv:
            submits += 1
            if submits == 3:
                raise RuntimeError('submission denied')
            return SimpleNamespace(stdout=str(800+submits))
        return SimpleNamespace(stdout='')
    monkeypatch.setattr(b.subprocess, 'run', run)
    with pytest.raises(RuntimeError, match='denied'):
        b.submit(work, 'a', 'q', 12)
    assert [c for c in seen if c[0] == 'scancel'] == [['scancel','801'], ['scancel','802']]
    assert not any(c[:2] == ['scontrol','release'] for c in seen)


def test_resume_skips_completed_articles_and_requires_old_jobs_terminal(tmp_path, monkeypatch):
    work, refs, _ = campaign(tmp_path)
    finish(work, refs[0])
    b.write(work / 'runtime.json', {'ready': True})
    b.write(work / 'submissions/old.json', {'jobs': [{'id': '123'}]})
    monkeypatch.setattr(b.subprocess, 'run', lambda *a, **k: SimpleNamespace(stdout='123\n'))
    with pytest.raises(ValueError, match='still active'):
        b.submit(work, 'a', 'q', 12, resume=True, dry_run=True)
    monkeypatch.setattr(b.subprocess, 'run', lambda *a, **k: SimpleNamespace(stdout=''))
    plan = b.submit(work, 'a', 'q', 12, resume=True, dry_run=True)
    commands = plan['commands_without_dependencies']
    assert len(commands) == 3  # workers 1/2 and aggregate; no setup or worker 0


def test_export_excludes_runtime_and_preserves_raw_attempts(tmp_path):
    import tarfile
    work, refs, _ = campaign(tmp_path)
    folder = finish(work, refs[0])
    b.write(folder / 'extraction/raw.json', {'raw': 'retained'})
    b.write(work / 'runtime/weights/config.json', {'huge': 'exclude'})
    result = b.export(work)
    with tarfile.open(result['archive']) as tf:
        names = tf.getnames()
    assert any(n.endswith('extraction/raw.json') for n in names)
    assert not any('/runtime/' in n for n in names)
    assert (work / 'results.tar.gz.sha256').exists()


def test_article_drives_source_only_and_times_ehr(tmp_path, monkeypatch):
    from openpatients2 import pilot_extract, ehr_pipeline
    work, refs, m = campaign(tmp_path, 1)
    m['extraction'] = b.extraction_config()
    b.write(work / 'manifest.json', m)
    received = []
    class FakeRunner:
        def __init__(self, config, output, endpoints, context, arm):
            self.output = output
        async def run(self, source, *, image_manifest):
            received.append((source, image_manifest))
            assert '/inputs/' in str(source) and 'reference' not in str(source)
            return {'task_statuses': {'valid': 1}, 'patients_extracted': 1, 'selected_articles': 1,
                    'eligible_articles': 1, 'articles': [{}], 'valid_tasks': 1, 'task_count': 1}
        async def close(self):
            pass
    async def ehr(config, source, run, out, endpoints, context, **kwargs):
        assert not kwargs.get('supplement_transcriptions') and not kwargs.get('supplement_assets')
        b.write_rows(out / 'data/unified.jsonl', refs)
        return {'articles': [{'errors': {}}]}
    monkeypatch.setattr(pilot_extract, 'PilotRunner', FakeRunner)
    monkeypatch.setattr(ehr_pipeline, 'run_ehr_stages', ehr)
    attempt = tmp_path / 'attempt-0000'
    r = asyncio.run(b.run_article(work, m['articles'][0], 'http://127.0.0.1:8600/v1', attempt))
    assert r['status'] == 'done' and r['has_prediction']
    assert r['wall_seconds'] >= r['extraction_seconds'] + r['ehr_seconds']
    assert received and r['prediction_sha256']
