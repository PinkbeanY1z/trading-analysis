"""Offline coverage for opt-in live acceptance: never uses real credentials/network."""
import asyncio
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from langchain_core.messages import AIMessage
from app.agents.llm import ScriptedLLM
from app.agents.schemas import AgentSettings
from app.market import MarketService
from app.models import MarketError, MarketQuery, Settings
from app.providers import MockProvider, TwelveDataProvider

spec = importlib.util.spec_from_file_location('verify_live', Path(__file__).resolve().parents[2] / 'scripts/verify_live.py')
live = importlib.util.module_from_spec(spec)
spec.loader.exec_module(live)


@pytest.mark.parametrize('status,code', [(400, 'upstream_invalid_parameters'), (401, 'upstream_authentication'), (403, 'upstream_permission'), (404, 'empty_data'), (429, 'rate_limit')])
@pytest.mark.parametrize('body_error', [False, True])
def test_provider_safe_classification(status, code, body_error):
    calls = []
    def handler(request):
        calls.append(request)
        return httpx.Response(200 if body_error else status, json={'status':'error', 'code':status, 'message':'secret-key-or-upstream-url'})
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            await TwelveDataProvider(client, 'test-secret-not-a-real-key').fetch(MarketQuery(symbol='QQQ'))
    with pytest.raises(MarketError) as exc:
        asyncio.run(run())
    assert exc.value.code == code
    assert 'secret' not in exc.value.message
    assert len(calls) == 1


@pytest.mark.parametrize('status,category', [(401,'authentication'),(402,'insufficient_balance'),(403,'permission'),(404,'model_or_endpoint_unavailable'),(422,'model_parameters_or_tool_compatibility'),(429,'rate_limit'),(503,'upstream_server')])
def test_model_error_category(status, category):
    class Failure(Exception): pass
    error = Failure('secret key in upstream message')
    error.status_code = status
    assert live.error_category(error) == category


def test_missing_keys_skip_without_network():
    result = asyncio.run(live.verify(Settings(_env_file=None,twelvedata_api_key=''), AgentSettings(_env_file=None,agent_mode='deepseek',deepseek_api_key='')))
    assert result['twelve_data'] == result['deepseek_function_calling'] == result['multi_turn_natural_language'] == 'SKIPPED'
    assert not result['market_cases'] and not result['agent_cases']


def test_real_model_not_silently_given_mock_market():
    result = asyncio.run(live.verify(Settings(_env_file=None,twelvedata_api_key=''), AgentSettings(_env_file=None,agent_mode='deepseek',deepseek_api_key='test-only')))
    assert result['deepseek_function_calling'] == 'SKIPPED'
    assert result['market_source_for_agent'] == 'twelve_data'
    assert not result['agent_cases']


def plan():
    result = []
    for task, _, symbol, frames in live.TASKS:
        args = {'symbol':symbol,'asset_class':'gold' if symbol == 'XAU/USD' else 'stock'}
        if frames and len(frames) > 1:
            name='compare_timeframes'; args['intervals'] = ['1day','1h']
        else:
            name='analyze_market_structure'; args['interval'] = next(iter(frames)) if frames else '1day'
        result += [AIMessage(content='',tool_calls=[dict(id=task,name=name,args=args)]), AIMessage(content=json.dumps({'evidence_ids':[task],'conclusion':'需要更多证据'}))]
    return result


def test_explicit_mock_market_real_protocol_path_with_test_double():
    observed = live.ObservedLLM(ScriptedLLM(plan()))
    result = asyncio.run(live.verify(Settings(_env_file=None,twelvedata_api_key=''), AgentSettings(_env_file=None,agent_mode='deepseek',deepseek_api_key='test-only'), market_source='mock', observed=observed))
    # Only tests the verifier; this assertion is not a real-model acceptance claim.
    assert result['deepseek_function_calling'] == result['multi_turn_natural_language'] == 'PASSED'
    assert result['twelve_data'] == 'NOT_RUN'
    assert [c['task'] for c in result['agent_cases']] == ['A','D','B','C','E']
    assert result['agent_cases'][1]['model_rounds'][0]['user_message_count'] == 2
    assert result['market_source_for_agent'] == 'mock'


def test_wrong_symbol_or_frames_fails_even_with_success_response():
    response = SimpleNamespace(status='success', symbol='NDX', timeframes=['1h'], mode='deepseek', data_sources=['mock'], tool_executions=[], report=SimpleNamespace(evidence=[]))
    failures = live.check_task(response, 'QQQ', {'1day'}, 'mock', [])
    assert 'wrong_symbol' in failures and 'wrong_timeframes' in failures
    assert 'tool_results_not_returned_to_model' in failures


def test_native_model_exception_diagnostic_is_sanitized():
    class Bad(ScriptedLLM):
        async def ainvoke(self, messages):
            failure = RuntimeError('do-not-print-secret')
            failure.status_code = 401
            raise failure
    observed = live.ObservedLLM(Bad())
    result = asyncio.run(live.verify(Settings(_env_file=None,twelvedata_api_key=''), AgentSettings(_env_file=None,agent_mode='deepseek',deepseek_api_key='test-only'), market_source='mock', observed=observed))
    assert result['deepseek_function_calling'] == result['multi_turn_natural_language'] == 'FAILED'
    assert result['agent_cases'][0]['model_errors'] == ['authentication']
    assert result['agent_cases'][0]['model_error_details'] == [{'category':'authentication','http_status':401}]
    assert len(result['agent_cases']) == 1
    assert 'do-not-print-secret' not in json.dumps(result)


def test_live_market_validation_requires_real_source_and_usable_history():
    async def run(): return await MarketService(MockProvider()).get(MarketQuery(symbol='QQQ',interval='1day'))
    data = asyncio.run(run())
    with pytest.raises(ValueError): live.validate_market(data, MarketQuery(symbol='QQQ',interval='1day'))
    data['source']='twelve_data'
    live.validate_market(data, MarketQuery(symbol='QQQ',interval='1day'))
    data['indicators']['latest']['ma200'] = None
    with pytest.raises(ValueError): live.validate_market(data, MarketQuery(symbol='QQQ',interval='1day'))


@pytest.mark.parametrize('status,category', [
    (200,None), (400,'model_parameters_or_tool_compatibility'), (401,'authentication'),
    (402,'insufficient_balance'), (403,'permission'), (429,'rate_limit'),
])
def test_minimal_auth_probe_has_one_request_and_no_secret_output(status, category):
    requests = []
    def handler(request):
        requests.append(request)
        assert request.url.path == '/chat/completions'
        assert request.headers['authorization'] == 'Bearer test-only-key'
        body = json.loads(request.content)
        assert body['max_tokens'] == 1 and body['thinking'] == {'type':'disabled'}
        assert 'tools' not in body
        return httpx.Response(status, json={'error':{'message':'upstream-secret-do-not-output'}})
    async def run():
        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        return await live.probe_deepseek(AgentSettings(_env_file=None, deepseek_api_key='test-only-key'), client)
    result = asyncio.run(run())
    assert len(requests) == 1
    assert result['http_status'] == status
    assert result['deepseek_authentication'] == ('PASSED' if status == 200 else 'FAILED')
    if category: assert result['error'] == category
    assert 'secret' not in json.dumps(result) and 'test-only-key' not in json.dumps(result)


def test_minimal_probe_distinguishes_network_failure():
    def handler(request):
        raise httpx.ConnectError('secret-upstream-url', request=request)
    async def run():
        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        return await live.probe_deepseek(AgentSettings(_env_file=None, deepseek_api_key='test-only-key'), client)
    result = asyncio.run(run())
    assert result['error'] == 'network' and result['http_status'] is None
    assert 'secret' not in json.dumps(result)


def test_mock_market_never_runs_real_matrix_even_with_configured_market_key(monkeypatch):
    async def forbidden(*args, **kwargs):
        pytest.fail('mock-market verification must not spend real market quota')
    monkeypatch.setattr(TwelveDataProvider, 'fetch', forbidden)
    observed = live.ObservedLLM(ScriptedLLM(plan()[:4]))
    result = asyncio.run(live.verify(
        Settings(_env_file=None,twelvedata_api_key='test-only-market-key'),
        AgentSettings(_env_file=None,agent_mode='deepseek',deepseek_api_key='test-only-model-key'),
        market_source='mock', observed=observed, tasks=live.TASKS[:2]))
    assert result['twelve_data'] == 'NOT_RUN' and not result['market_cases']
    assert result['requested_tasks'] == ['A','D']
    assert result['agent_tasks'] == result['multi_turn_natural_language'] == 'PASSED'
    assert result['agent_cases'][1]['model_rounds'][0]['user_message_count'] == 2


def test_skipping_matrix_only_fetches_data_requested_by_real_market_agent(monkeypatch):
    queries = []
    async def fetch(provider, query):
        queries.append((query.symbol,query.interval))
        return await MockProvider().fetch(query)
    monkeypatch.setattr(TwelveDataProvider, 'fetch', fetch)
    observed = live.ObservedLLM(ScriptedLLM(plan()[:2]))
    result = asyncio.run(live.verify(
        Settings(_env_file=None,twelvedata_api_key='test-only-market-key'),
        AgentSettings(_env_file=None,agent_mode='deepseek',deepseek_api_key='test-only-model-key'),
        observed=observed, run_market_checks=False, tasks=live.TASKS[:1]))
    assert queries == [('QQQ','1day')]
    assert result['twelve_data'] == result['multi_turn_natural_language'] == 'NOT_RUN'
    assert not result['market_cases'] and result['requested_tasks'] == ['A']
    assert result['deepseek_function_calling'] == result['agent_tasks'] == 'PASSED'


@pytest.mark.parametrize('tasks', [['D'], ['D','A'], ['A','A']])
def test_cli_rejects_invalid_followup_or_duplicate_tasks_without_requests(tasks):
    with pytest.raises(SystemExit) as exc:
        asyncio.run(live.main(['--tasks',*tasks]))
    assert exc.value.code == 2
