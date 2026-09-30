import pytest
from openpatients2.client import APIClient, Completion
from openpatients2.config import APIConfig
from openpatients2.experiment import Experiment


def test_prompt_json_does_not_send_grammar_enforcement():
    client=APIClient(APIConfig(model='fixture',response_format='prompt_json'))
    body=client.body('demographics',[{'role':'user','content':'extract'}],100)
    assert 'response_format' not in body and 'structured_outputs' not in body


def config(tmp_path):
    return {'output':str(tmp_path/'run'),'budget_usd':1,'models':[{'id':'fixture','context_length':10000,
        'response_format':'prompt_json','max_input_per_million':1,'max_output_per_million':1}],
        'validation_retries':0}


async def test_access_error_blocks_queued_calls_and_retains_cost_reservation(tmp_path):
    exp=Experiment(config(tmp_path));calls=[]
    async def denied(*args):
        calls.append(args);return Completion(error='http_403',http_status=403)
    exp.clients['fixture'].complete_once=denied
    try:
        m=exp.config['models'][0]
        a=await exp.call(m,'roster',[{'role':'user','content':'first'}],lambda x:x,{},max_tokens=10)
        b=await exp.call(m,'roster',[{'role':'user','content':'second'}],lambda x:x,{},max_tokens=10)
        assert len(calls)==1 and a['status']==b['status']=='failed'
        assert 'model_access_blocked' in b['errors'][0]
        assert exp.budget.report()['unknown_or_inflight']==1
    finally:await exp.close()


@pytest.mark.parametrize('error', ['TimeoutError', 'total_request_deadline'])
async def test_total_timeout_retries_are_bounded_and_unknown_charges_stay_reserved(tmp_path, error):
    cfg = config(tmp_path)
    cfg['validation_retries'] = 1
    exp = Experiment(cfg)
    calls = []

    async def timeout(*args):
        calls.append(args)
        return Completion(error=error, http_status=200)

    exp.clients['fixture'].complete_once = timeout
    try:
        result = await exp.call(exp.config['models'][0], 'roster',
                                [{'role': 'user', 'content': 'extract'}], lambda x: x, {}, max_tokens=10)
        assert result['status'] == 'failed' and result['data'] is None
        assert result['errors'] == [error]
        assert len(calls) == result['metrics']['attempts'] == 2
        budget = exp.budget.report()
        assert budget['requests'] == budget['unknown_or_inflight'] == 2
        assert budget['reported_cost_usd'] == 0 and budget['accounted_usd'] > 0
    finally:
        await exp.close()


async def test_cached_result_failing_current_validation_is_not_counted_valid(tmp_path):
    exp=Experiment(config(tmp_path))
    async def success(*args):
        return Completion(content='{"ok":true}',finish_reason='stop',usage={'cost':0.001})
    exp.clients['fixture'].complete_once=success
    try:
        m=exp.config['models'][0];messages=[{'role':'user','content':'extract'}]
        assert (await exp.call(m,'roster',messages,lambda x:x,{},max_tokens=10))['status']=='valid'
        def reject(x):raise ValueError('new evidence check failed')
        result=await exp.call(m,'roster',messages,reject,{},max_tokens=10)
        assert result['status']=='failed' and not exp.metrics[-1]['valid']
        assert exp.budget.report()['requests']==1
    finally:await exp.close()


async def test_stale_license_gate_prevents_any_model_call(tmp_path):
    exp=Experiment(config(tmp_path))
    async def forbidden(*args,**kwargs):raise AssertionError('Must reject before inference')
    exp.call=forbidden
    try:
        article={'license':{'allowed':True,'code':'CC BY','statements':[{'type':'license','text':'CCBY-NC-ND'}]}}
        assert await exp.article(exp.config['models'][0],article)==[]
        assert exp.budget.report()['requests']==0
    finally:await exp.close()


def test_cohere_wire_projection_keeps_contract_and_full_application_checks():
    from openpatients2.schemas import provider_schema
    from openpatients2.article_tasks import Roster
    import json
    schema=Roster.model_json_schema();projected=provider_schema(schema,'cohere')
    assert projected['required']==schema['required']
    assert projected['properties']['disposition']==schema['properties']['disposition']
    assert 'minimum' not in json.dumps(projected) and 'pattern' not in json.dumps(projected)
    assert 'minimum' in json.dumps(schema) and 'pattern' in json.dumps(schema)
    # Keys named like a constraint are data properties, never removed.
    s={'type':'object','properties':{'minimum':{'type':'integer','minimum':0}},'required':['minimum']}
    p=provider_schema(s,'cohere')
    assert p['properties']['minimum']=={'type':'integer'} and p['required']==['minimum']
