import json

from openpatients2.client import Completion
from openpatients2.reasoning_lineage import link_fact_reasoning, export_fact_index
from test_pilot_extract import article, answer, setup_mock, run_pilot, input_file


def test_trace_links_include_repairs_and_exclude_other_patients_and_tasks():
    def task(name, patient, seq, mode='initial'):
        return {'task': name, 'identity': {'record_id': patient}, 'attempts': [
            {'ledger_sequence': seq, 'identity': {'record_id': patient}, 'mode': mode,
             'response': {'reasoning_status': 'returned_text'}, 'errors': []}]}
    rows = [{'record_id': 'PMC1.1:p1', 'task': 'conditions', 'candidate': {'name': 'finding'}}]
    link_fact_reasoning(rows, [task('conditions', 'PMC1.1:p1', 1),
        task('coverage_repair_conditions', 'PMC1.1:p1', 3, 'repair'),
        task('conditions', 'PMC1.1:p2', 4), task('medications', 'PMC1.1:p1', 5)],
        {'model_id': 'fixture/teacher', 'revision': 'pinned'})
    trace = rows[0]['generation_trace']
    assert [c['ledger_sequence'] for c in trace['calls']] == [1, 3]
    assert trace['scope'] == 'patient_task_calls' and trace['fact_specific_reasoning'] is False


async def test_saved_fact_citation_resolves_to_returned_reasoning_and_generation(tmp_path, monkeypatch):
    value = article()
    def response(task, messages):
        return Completion(content=json.dumps(answer(task, value)), finish_reason='stop',
            reasoning_text='Endpoint trace for ' + task,
            reasoning_fields={'reasoning_content': 'Endpoint trace for ' + task},
            reasoning_field_seen=True, prompt_tokens=10, completion_tokens=20, reasoning_tokens=3)
    http, _ = setup_mock(monkeypatch, value, responses=response)
    attempt = tmp_path / 'results/articles/PMC1.1/attempt-0000'
    output = attempt / 'extraction'
    async with http:
        await run_pilot({'max_articles': 1}, input_file(tmp_path, [value]), output,
                        ['http://127.0.0.1:8000/v1'], 4096, 'direct', http=http)
    index = tmp_path / 'fact-reasoning.jsonl'
    receipt = export_fact_index(tmp_path, index, [('PMC1.1', 'train', attempt)])
    assert receipt['facts_with_returned_reasoning_links'] > 0
    rows = [json.loads(line) for line in index.read_text().splitlines()]
    fact = next(r for r in rows if r['task'] == 'summary')
    assert fact['evidence_spans'] and fact['candidate']['text']
    assert fact['training_eligible'] is None and fact['semantic_correctness'] == 'unreviewed'
    call = fact['generation_trace']['calls'][0]
    ledger = [json.loads(line) for line in (tmp_path / call['ledger']).read_text().splitlines()]
    entry = next(row for row in ledger if row['ledger_sequence'] == call['ledger_sequence'])
    assert entry['response']['reasoning_text'] == 'Endpoint trace for summary'
    assert entry['generation']['save_reasoning'] is True
    assert entry['model']['revision'] == fact['generation_trace']['model']['revision']
    assert entry['request'] and entry['messages_sha256']


def test_missing_reasoning_keeps_fact_and_explicit_status():
    rows = [{'record_id': 'p1', 'task': 'conditions'}]
    tasks = [{'task': 'conditions', 'identity': {'record_id': 'p1'}, 'attempts': [
        {'ledger_sequence': 1, 'response': {'reasoning_status': 'not_returned'}}]}]
    link_fact_reasoning(rows, tasks, {'model_id': 'fixture/model', 'revision': 'fixed'})
    assert rows[0]['generation_trace']['calls'][0]['reasoning_status'] == 'not_returned'
    assert 'reasoning_text' not in rows[0]
