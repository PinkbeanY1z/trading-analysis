"""Opt-in live acceptance. No real API is called by default CI.

Only this project's backend/.env and the current process environment are loaded.
Missing credentials are SKIPPED; failed real requests never fall back to Mock.
"""
import argparse
import asyncio
import json
import logging
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))

import httpx
from openai import AsyncOpenAI
from langchain_core.messages import HumanMessage, ToolMessage
from app.models import Candle, MarketError, MarketQuery, Settings
from app.providers import MockProvider, TwelveDataProvider
from app.market import MarketService
from app.agents.schemas import AgentSettings, ChatRequest
from app.agents.graph import Agent
from app.agents.llm import model

TASKS = (
    ('A', '分析 QQQ 日线趋势和 MA20、MA50、MA200。', 'QQQ', {'1day'}),
    ('D', '那它的4小时呢？', 'QQQ', {'4h'}),
    ('B', '比较 QQQ 日线和1小时趋势。', 'QQQ', {'1day', '1h'}),
    ('C', '分析黄金的日线技术结构和 RSI。', 'XAU/USD', {'1day'}),
    ('E', 'QQQ 当前 RSI14 是多少？', 'QQQ', None),
)
KNOWN_TOOLS = {
    'get_market_data', 'calculate_technical_indicators',
    'analyze_market_structure', 'compare_timeframes',
}


def http_status(exc):
    code = getattr(exc, 'status_code', None)
    if code is None and getattr(exc, 'response', None) is not None:
        code = getattr(exc.response, 'status_code', None)
    return code if isinstance(code, int) else None


def error_category(exc):
    """Return a fixed error code. Never serialize exception text or request URLs."""
    if isinstance(exc, MarketError):
        return exc.code
    code = http_status(exc)
    categories = {
        400: 'model_parameters_or_tool_compatibility',
        401: 'authentication', 402: 'insufficient_balance', 403: 'permission',
        404: 'model_or_endpoint_unavailable', 422: 'model_parameters_or_tool_compatibility',
        429: 'rate_limit',
    }
    if code in categories:
        return categories[code]
    if isinstance(code, int) and code >= 500:
        return 'upstream_server'
    if isinstance(exc, (TimeoutError, httpx.TimeoutException)) or 'Timeout' in type(exc).__name__:
        return 'timeout'
    if isinstance(exc, httpx.RequestError) or 'Connection' in type(exc).__name__:
        return 'network'
    return 'validation_or_code_error'


async def probe_deepseek(config, client=None):
    """One minimal chat request, no retries, tools or market calls; never echo errors."""
    if not config.deepseek_api_key:
        return {'deepseek_authentication': 'SKIPPED', 'reason': 'missing_deepseek_key'}
    try:
        async with AsyncOpenAI(api_key=config.deepseek_api_key,
                               base_url=config.deepseek_base_url,
                               timeout=config.deepseek_timeout, max_retries=0,
                               http_client=client) as sdk:
            response = await sdk.chat.completions.with_raw_response.create(
                model=config.deepseek_model, messages=[{'role': 'user', 'content': 'Reply OK.'}],
                max_tokens=1, stream=False, extra_body={'thinking': {'type': 'disabled'}})
            return {'deepseek_authentication': 'PASSED', 'http_status': response.status_code,
                    'scope': 'minimal_chat_only; Function Calling and account balance not validated'}
    except Exception as exc:
        return {'deepseek_authentication': 'FAILED', 'http_status': http_status(exc),
                'error': error_category(exc)}


class ObservedLLM:
    """Observe real native calls and their return messages; don't synthesize tool calls."""
    def __init__(self, llm):
        self.llm = llm
        self.events = []
        self.errors = []
        self.error_details = []

    def bind_tools(self, tools):
        self.bound = self.llm.bind_tools(tools)
        return self

    async def ainvoke(self, messages):
        # Keep no message content, API URLs, response headers or key in diagnostics.
        returned = [m.tool_call_id for m in messages if isinstance(m, ToolMessage)]
        users = sum(isinstance(m, HumanMessage) for m in messages)
        try:
            answer = await self.bound.ainvoke(messages)
        except Exception as exc:
            category = error_category(exc)
            self.errors.append(category)
            self.error_details.append({'category': category, 'http_status': http_status(exc)})
            raise RuntimeError(category) from None
        self.events.append({
            'returned_tool_call_ids': returned,
            'user_message_count': users,
            'native_tool_calls': [
                {'call_id': tc['id'], 'name': tc['name']}
                for tc in answer.tool_calls
            ],
        })
        return answer


def validate_market(data, query):
    rows = data['candles']
    if data['source'] != 'twelve_data':
        raise ValueError('unexpected data source')
    if data['symbol'] != query.symbol or data['interval'] != query.interval:
        raise ValueError('unexpected market identity')
    if len(rows) < 200 or data['indicators']['insufficient_history']:
        raise ValueError('insufficient MA200 history')
    stamps = []
    for row in rows:
        Candle.model_validate(row)
        stamp = datetime.fromisoformat(row['t'])
        if stamp.tzinfo is None:
            raise ValueError('naive timestamp')
        stamps.append(stamp)
        if query.asset == 'gold' and row.get('v') is not None:
            raise ValueError('gold volume must remain unavailable')
    if stamps != sorted(set(stamps)):
        raise ValueError('unordered or duplicate timestamps')
    if rows[-1]['t'] != data['last_updated']:
        raise ValueError('last_updated does not identify last candle')
    latest = data['indicators']['latest']
    if any(latest.get(k) is None for k in ('ma20', 'ma50', 'ma200', 'rsi', 'atr')):
        raise ValueError('required indicator unavailable')
    if not 0 <= latest['rsi'] <= 100 or latest['atr'] < 0:
        raise ValueError('invalid indicator range')


def check_task(response, symbol, intervals, source, events):
    """Check actual evidence and native transcript, not just a successful HTTP status."""
    failures = []
    if response.status != 'success':
        failures.append('agent_' + response.status)
    if response.symbol != symbol:
        failures.append('wrong_symbol')
    observed_intervals = set(response.timeframes)
    if not observed_intervals or not observed_intervals <= {'1h', '4h', '1day'}:
        failures.append('invalid_timeframes')
    if intervals is not None and observed_intervals != intervals:
        failures.append('wrong_timeframes')
    if response.mode != 'deepseek' or set(response.data_sources) != {source}:
        failures.append('unexpected_model_or_data_source')
    executions = response.tool_executions
    if not executions or any(t.status != 'success' or t.name not in KNOWN_TOOLS for t in executions):
        failures.append('tool_failure')
    native = [tc for event in events for tc in event['native_tool_calls']]
    if [(tc['call_id'], tc['name']) for tc in native] != [(t.call_id, t.name) for t in executions]:
        failures.append('native_execution_trace_mismatch')
    returned = {call_id for event in events for call_id in event['returned_tool_call_ids']}
    if not native or not {tc['call_id'] for tc in native} <= returned:
        failures.append('tool_results_not_returned_to_model')
    evidence = response.report.evidence
    if not evidence:
        failures.append('missing_evidence')
    for item in evidence:
        if item.get('symbol') != symbol or item.get('interval') not in observed_intervals or item.get('source') != source:
            failures.append('wrong_evidence_identity')
        execution = next((t for t in executions if t.call_id == item.get('call_id')), None)
        origins = execution.result.get('timeframes', [execution.result]) if execution else []
        origin = next((d for d in origins if d.get('symbol') == symbol and d.get('interval') == item.get('interval')), None)
        if origin is None or item.get('technical_indicators') != origin.get('technical_indicators'):
            failures.append('numeric_evidence_mismatch')
        if item.get('technical_indicators', {}).get('rsi') is None:
            failures.append('missing_rsi')
    return list(dict.fromkeys(failures))


async def verify(settings=None, config=None, market_source='twelve_data', client=None, observed=None,
                 run_market_checks=True, tasks=TASKS):
    settings = settings or Settings()
    config = config or AgentSettings(agent_mode='deepseek')
    results = {
        'twelve_data': 'SKIPPED', 'deepseek_function_calling': 'SKIPPED',
        'multi_turn_natural_language': 'SKIPPED',
        'market_source_for_agent': market_source,
        'requested_tasks': [task[0] for task in tasks],
        'reasons': {}, 'market_cases': [], 'agent_cases': [],
        'scope_notes': [
            'K线及指标验证不代表精确交易所日历、实时开闭市或半日市判断已验证。',
            '结构仍包含可能未完成的最新K线；报告须结合数据时间和警告。',
        ],
    }
    # The caller owns the injected client; main creates/closes the real client.
    market = MarketService(TwelveDataProvider(client, settings.twelvedata_api_key))
    if market_source == 'mock' or not run_market_checks:
        results['twelve_data'] = 'NOT_RUN'
        results['reasons']['twelve_data'] = 'explicit_mock_market' if market_source == 'mock' else 'market_matrix_disabled'
    elif not settings.twelvedata_api_key:
        results['reasons']['twelve_data'] = 'missing_twelvedata_key'
    else:
        results['twelve_data'] = 'PASSED'
        for symbol, asset in [('QQQ', 'stock'), ('XAU/USD', 'gold'), ('AAPL', 'stock')]:
            for interval in ('1h', '4h', '1day'):
                query = MarketQuery(symbol=symbol, asset=asset, interval=interval)
                case = {'symbol': symbol, 'interval': interval}
                try:
                    data = await market.get(query)
                    validate_market(data, query)
                    case.update(status='PASSED', source=data['source'], bars=len(data['candles']),
                                last_updated=data['last_updated'], warnings=data['warnings'])
                except Exception as exc:
                    case.update(status='FAILED', error=error_category(exc))
                    results['twelve_data'] = 'FAILED'
                results['market_cases'].append(case)
    if not config.deepseek_api_key:
        results['reasons']['deepseek_function_calling'] = 'missing_deepseek_key'
        results['reasons']['multi_turn_natural_language'] = 'missing_deepseek_key'
        return results
    if market_source == 'twelve_data' and not settings.twelvedata_api_key:
        results['reasons']['deepseek_function_calling'] = 'missing_twelvedata_key_for_real_market; explicitly select --market-source mock to test the real model protocol'
        results['reasons']['multi_turn_natural_language'] = 'missing_twelvedata_key_for_real_market'
        return results
    if market_source == 'mock':
        market = MarketService(MockProvider())
        results['scope_notes'].append('显式使用模拟行情；真实模型的调用与品种/周期理解可验证，真实行情端到端联调仍未验证。')
    observed = observed or ObservedLLM(model(config))
    agent = Agent(market, config, observed)
    protocol_successes = []
    thread = None
    for task_id, message, symbol, intervals in tasks:
        start = len(observed.events)
        error_start = len(observed.errors)
        response = await agent.chat(ChatRequest(message=message, thread_id=thread if task_id == 'D' else None))
        if task_id == 'A':
            thread = response.thread_id
        events = observed.events[start:]
        failures = check_task(response, symbol, intervals, market_source, events)
        if task_id == 'D':
            if response.thread_id != thread or not events or events[0]['user_message_count'] < 2:
                failures.append('session_context_not_reused')
        native = [tc for event in events for tc in event['native_tool_calls']]
        executed = [(t.call_id, t.name) for t in response.tool_executions if t.status == 'success']
        returned = {tc for event in events for tc in event['returned_tool_call_ids']}
        protocol_successes.append(bool(native) and all((tc['call_id'], tc['name']) in executed and tc['call_id'] in returned for tc in native))
        results['agent_cases'].append({
            'task': task_id, 'status': 'FAILED' if failures else 'PASSED',
            'symbol': response.symbol, 'timeframes': response.timeframes,
            'data_sources': response.data_sources, 'agent_status': response.status,
            'checks_failed': failures, 'model_errors': observed.errors[error_start:],
            'model_error_details': observed.error_details[error_start:],
            'tool_execution': [{'call_id': t.call_id, 'name': t.name, 'status': t.status} for t in response.tool_executions],
            'model_rounds': events,
        })
        # Do not repeat invalid credentials/unsupported model calls for all tasks.
        if observed.errors[error_start:] and observed.errors[-1] in {'authentication', 'permission', 'insufficient_balance', 'rate_limit', 'model_or_endpoint_unavailable', 'model_parameters_or_tool_compatibility'}:
            break
    results['deepseek_function_calling'] = 'PASSED' if protocol_successes and all(protocol_successes) else 'FAILED'
    tasks_passed = len(results['agent_cases']) == len(tasks) and all(c['status'] == 'PASSED' for c in results['agent_cases'])
    results['agent_tasks'] = 'PASSED' if tasks_passed else 'FAILED'
    if {'A', 'D'} <= set(results['requested_tasks']):
        results['multi_turn_natural_language'] = 'PASSED' if tasks_passed else 'FAILED'
    else:
        results['multi_turn_natural_language'] = 'NOT_RUN'
        results['reasons']['multi_turn_natural_language'] = 'followup_A_D_not_selected'
    return results


async def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--market-source', choices=('twelve_data', 'mock'), default='twelve_data',
                        help='Agent market source. Mock is explicit and never chosen as a fallback.')
    parser.add_argument('--deepseek-probe', action='store_true',
                        help='One minimal real chat request; no Agent, tools or Twelve Data queries.')
    parser.add_argument('--skip-market-checks', action='store_true',
                        help='Skip the nine-case market matrix; Agent tools still use --market-source.')
    parser.add_argument('--tasks', nargs='+', choices=[task[0] for task in TASKS],
                        default=[task[0] for task in TASKS], help='Only validate the listed tasks, in order. D requires earlier A.')
    args = parser.parse_args(argv)
    if len(set(args.tasks)) != len(args.tasks):
        parser.error('tasks must not contain duplicates')
    if 'D' in args.tasks and ('A' not in args.tasks or args.tasks.index('A') > args.tasks.index('D')):
        parser.error('follow-up task D requires earlier task A')
    # Keep request URLs (which contain the Twelve Data key) out of inherited log handlers.
    for name in ('httpx', 'httpcore', 'openai', 'langchain_openai'):
        logging.getLogger(name).setLevel(logging.CRITICAL)
    try:
        config = AgentSettings(agent_mode='deepseek')
        if args.deepseek_probe:
            results = await probe_deepseek(config)
        else:
            settings = Settings()
            tasks_by_id = {task[0]: task for task in TASKS}
            tasks = tuple(tasks_by_id[task_id] for task_id in args.tasks)
            async with httpx.AsyncClient(timeout=settings.upstream_timeout) as client:
                results = await verify(settings, config, args.market_source, client,
                                       run_market_checks=not args.skip_market_checks, tasks=tasks)
    except Exception as exc:
        results = {'verification': 'FAILED', 'error': error_category(exc)}
    print(json.dumps(results, ensure_ascii=False, indent=2))
    return 1 if any(value == 'FAILED' for value in results.values() if isinstance(value, str)) else 0


if __name__ == '__main__':
    sys.exit(asyncio.run(main()))
