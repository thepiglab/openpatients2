"""Parent deadlines must preserve returned traces without swallowing cancellation."""
import asyncio
import json

import httpx
import pytest

from openpatients2.client import APIClient
from openpatients2.config import APIConfig
from openpatients2.data import read_jsonl
from openpatients2.dense_benchmark import ledger_metrics
from openpatients2.pilot_extract import PilotRunner


class PartialStream(httpx.AsyncByteStream):
    def __init__(self, received):
        self.received = received

    async def __aiter__(self):
        event = {'id': 'partial-response', 'choices': [{'index': 0,
            'delta': {'reasoning_content': 'Actual partial endpoint trace', 'content': '{'}}],
            'usage': {'prompt_tokens': 12, 'completion_tokens': 1}}
        yield ('data: ' + json.dumps(event) + '\n\n').encode()
        self.received.set()
        await asyncio.Event().wait()


def stream_transport(received):
    def handle(request):
        if request.url.path == '/tokenize':
            return httpx.Response(200, json={'count': 12, 'tokens': [1] * 12, 'max_model_len': 8192})
        return httpx.Response(200, stream=PartialStream(received))
    return httpx.MockTransport(handle)


async def test_client_cancellation_attaches_partial_completion_and_propagates():
    received = asyncio.Event()
    async with httpx.AsyncClient(transport=stream_transport(received)) as http:
        client = APIClient(APIConfig(response_format='prompt_json', api_key_env='OP2_TEST_UNUSED'), http=http)
        task = asyncio.create_task(client.complete_once('http://localhost:8000/v1', 'conditions',
            [{'role': 'user', 'content': 'fixture'}], 256))
        await received.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError) as exc:
            await task
    partial = exc.value.partial_completion
    assert partial.interrupted and partial.error == 'cancelled'
    assert partial.reasoning_text == 'Actual partial endpoint trace' and partial.content == '{'
    assert partial.prompt_tokens is None and partial.completion_tokens is None
    assert partial.usage == {'prompt_tokens': 12, 'completion_tokens': 1}
    assert partial.latency_seconds >= 0


async def test_outer_timeout_keeps_interrupted_generation_ledger_and_unknown_cost(tmp_path):
    received = asyncio.Event()
    async with httpx.AsyncClient(transport=stream_transport(received)) as http:
        runner = PilotRunner({'max_output_tokens': 256, 'max_retry_tokens': 256}, tmp_path / 'calls',
            ['http://localhost:8000/v1'], 8192, 'targeted', http=http)
        # Scheduling the deadline after a real chunk arrives avoids a timing
        # assumption about machine speed or mock transport startup.
        async def expire(deadline):
            await received.wait()
            deadline.reschedule(asyncio.get_running_loop().time())
        with pytest.raises(TimeoutError):
            async with asyncio.timeout(None) as deadline:
                watcher = asyncio.create_task(expire(deadline))
                await runner.call('conditions', [{'role': 'user', 'content': 'fixture'}],
                    lambda value: value, {'article_id': 'PMC1.1'}, 0, [])
        await watcher
        assert runner.calls == 1 and runner.reserved_tokens == 0 and runner.active == [0]
        assert runner.accounted_tokens == 268
        await runner.close()
    ledger = tmp_path / 'calls/attempts.jsonl'
    row, = list(read_jsonl(ledger))
    assert row['cancelled_during'] == 'generation' and row['errors'] == ['cancelled']
    assert row['response']['reasoning_text'] == 'Actual partial endpoint trace'
    assert row['response']['content'] == '{' and row['response']['interrupted']
    assert row['metrics']['prompt_tokens'] is None
    usage = ledger_metrics([ledger])
    assert usage['model_calls'] == 1 and usage['failed_attempts'] == 1
    assert usage['prompt_tokens'] is None and usage['completion_tokens'] is None


async def test_cancelled_tokenizer_is_recorded_without_a_generation_charge(tmp_path):
    started = asyncio.Event()
    async def handle(request):
        assert request.url.path == '/tokenize'
        started.set()
        await asyncio.Event().wait()
    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as http:
        runner = PilotRunner({'max_output_tokens': 256, 'max_retry_tokens': 256}, tmp_path / 'calls',
            ['http://localhost:8000/v1'], 8192, 'targeted', http=http)
        task = asyncio.create_task(runner.call('conditions', [{'role': 'user', 'content': 'fixture'}],
            lambda value: value, {'article_id': 'PMC1.1'}, 0, []))
        await started.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert runner.calls == 0 and runner.accounted_tokens == 0 and runner.reserved_tokens == 0
        await runner.close()
    ledger = tmp_path / 'calls/attempts.jsonl'
    row, = list(read_jsonl(ledger))
    assert row['cancelled_during'] == 'tokenization' and row['response'] is None
    assert ledger_metrics([ledger])['model_calls'] == 0
