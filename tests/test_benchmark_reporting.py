from openpatients2 import dense_benchmark as b
from openpatients2.benchmark_reporting import throughput, worker_goodput
from test_dense_benchmark import campaign as dense_campaign, finish
from test_dense_nemotron_super import campaign


def test_rates_use_summed_wall_time_and_do_not_double_count_reasoning():
    rows = [
        {'wall_seconds': 10, 'gpus_per_server': 2, 'completion_tokens': 100, 'reasoning_tokens': 60,
         'prompt_tokens': 500, 'has_prediction': True, 'all_extraction_tasks_valid': True},
        {'wall_seconds': 30, 'gpus_per_server': 2, 'completion_tokens': 300, 'reasoning_tokens': 100,
         'prompt_tokens': 1000, 'has_prediction': False, 'all_extraction_tasks_valid': False},
    ]
    result = throughput(rows)
    assert result['output_tokens_per_second'] == 10
    assert result['output_tokens_per_gpu_second'] == 5
    assert result['answer_tokens_per_second'] == 6
    assert result['reasoning_tokens_per_second'] == 4
    assert result['documents_per_second'] == .05
    assert result['documents_per_hour'] == 180
    assert result['documents_per_gpu_hour'] == 90
    assert result['valid_extraction_documents_per_second'] == .025
    assert result['prediction_documents'] == 1


def test_missing_usage_zero_time_and_inconsistent_breakdown_are_not_fabricated():
    rows = [{'wall_seconds': 10, 'completion_tokens': 100}, {'wall_seconds': 30, 'completion_tokens': None}]
    result = throughput(rows)
    assert result['output_tokens_per_second'] is None and result['answer_tokens_per_second'] is None
    assert result['documents_missing_completion_usage'] == 1
    assert result['documents_per_second'] == .05
    assert throughput([])['documents_per_second'] is None
    assert throughput([{'wall_seconds': 0, 'completion_tokens': 0}])['output_tokens_per_second'] is None
    assert throughput([{'wall_seconds': 10, 'completion_tokens': 10, 'reasoning_tokens': 20}])['answer_tokens_per_second'] is None


def test_worker_goodput_includes_startup_retries_and_marks_missing_receipts():
    docs = [{'has_prediction': True, 'all_extraction_tasks_valid': True}, {'has_prediction': False}]
    workers = [{'worker_seconds': 50, 'gpus_per_server': 2}, {'worker_seconds': 150, 'gpus_per_server': 2}]
    result = worker_goodput(docs, workers)
    assert result['documents_per_second'] == .01
    assert result['prediction_documents_per_second'] == .005
    assert result['documents_per_gpu_hour'] == 18
    assert worker_goodput(docs, workers, missing_receipts=1)['documents_per_second'] is None
    assert worker_goodput(docs, [])['documents_per_second'] is None


def test_pipeline_report_exports_rates_and_rejects_missing_worker_cost(tmp_path):
    work, refs, _ = dense_campaign(tmp_path)
    finish(work, refs[0], seconds=10)
    finish(work, refs[1], seconds=30, prediction=False)
    b.write(work / 'results/workers/0/job-100/receipt.json', {
        'worker_seconds': 100, 'started_unix': 0, 'ended_unix': 100, 'gpus_per_server': 1})
    (work / 'results/workers/0/job-101').mkdir(parents=True)
    report = b.aggregate(work)
    assert report['throughput']['output_tokens_per_second'] == 5
    assert report['throughput']['documents_per_second'] == .05
    assert report['worker_goodput']['missing_worker_receipts'] == 1
    assert report['worker_goodput']['documents_per_second'] is None
    assert 'output_tokens_per_second' in (work / 'summary/article-timing.csv').read_text()
    assert (work / 'summary/fact-reasoning.jsonl').exists()


def test_comparison_contains_split_and_matched_document_rates(campaign):
    # This test uses the original four-model campaign fixture from its module.
    from openpatients2 import dense_nemotron_super as s
    work, refs, _ = campaign
    for name in b.read(work / 'manifest.json')['nemotron_super']['arms']:
        finish(work / 'arms' / name, refs[0], seconds=10)
    report = s.aggregate(work)
    values = report['metrics']['text']['train']['fastino-text']['throughput']
    paired = report['matched']['text']['by_split']['train']['fastino-text']['throughput']
    assert values['output_tokens_per_second'] == 10
    assert values['output_tokens_per_gpu_second'] == 5
    assert paired['documents_per_second'] == .1
    markdown = (work / 'summary/comparison.md').read_text()
    assert 'Output tok/s' in markdown and 'Doc/s' in markdown and 'Item F1' in markdown
    assert 'Matched completed articles in this split: 1.' in markdown
    assert 'Matched item F1' in markdown


def test_status_distinguishes_failed_extraction_and_exhausted_workers(tmp_path):
    work, refs, _ = dense_campaign(tmp_path)
    folder = finish(work, refs[0], prediction=False)
    receipt = b.read(folder / 'receipt.json')
    b.write(folder / 'receipt.json', {**receipt, 'model_calls': 0})
    b.write(work / 'results/articles' / refs[1]['article_id'] / 'attempt-0000/receipt.json',
            {'status': 'error', 'error_type': 'TimeoutError'})
    b.write(work / 'results/workers/0/job-100/receipt.json', {'status': 'budget_exhausted'})
    result = b.status(work)
    assert result['completed'] == 1
    assert result['completed_without_prediction'] == 1
    assert result['completed_without_model_calls'] == 1
    assert result['worker_outcomes'] == {'budget_exhausted': 1}
    assert result['pending_with_failed_attempt'] == {refs[1]['article_id']: 'TimeoutError'}
    assert len(result['pending']) == 2
