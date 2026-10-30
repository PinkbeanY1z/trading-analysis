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
    assert result['twelve_data'] == 'SKIPPED'
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
