"""Question-first reports through the actual StateGraph and native ToolNode loop.

Explicit AIMessage plans are offline test oracles, never evidence of natural
language understanding. Market data and calculations come from real services
backed by MockProvider, not fabricated report fixtures.
"""
import asyncio
import json

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from app.agents.graph import Agent
from app.agents.llm import ScriptedLLM
from app.agents.schemas import AgentSettings, ChatRequest
from app.market import MarketService
from app.providers import MockProvider


BUY_QUESTION = '现在买入QQQ是好时机吗？胜率和盈亏比怎么样？'
CALC_QUESTION = '如果我在690买入、680止损、720止盈，盈亏比是多少？数值仅作计算测试。'


class CountingMarket(MarketService):
    def __init__(self, provider=None):
        super().__init__(provider or MockProvider())
        self.get_count = 0

    async def get(self, query):
        self.get_count += 1
        return await super().get(query)


def tool(call_id, name='analyze_market_structure', **arguments):
    if name not in {'plan_research', 'evaluate_trade_plan'}:
        arguments = {'symbol': 'QQQ', **arguments}
    return AIMessage(content='', tool_calls=[{'id': call_id, 'name': name, 'args': arguments}])


def planning(kinds=('trend',), primary='1day', call_id='plan', proposed_tools=None):
    return tool(call_id, 'plan_research', questions=[
        {'id': f'q{index + 1}', 'kind': kind, 'question': {
            'entry': '当前是否支持立即买入', 'win_rate': '实际历史胜率是否已验证',
            'risk_reward': '能否形成方案并计算盈亏比', 'levels': '关键价位的方法及当前有效性',
            'watch': '最值得观察的信号', 'stabilization': '该周期是否已经企稳',
            'timeframes': '不同周期是否存在分歧', 'indicator': '指定指标读数',
        }.get(kind, '当前趋势及确认限制')}
        for index, kind in enumerate(kinds)
    ], primary_timeframe=primary, proposed_tools=proposed_tools or ['analyze_market_structure'])


def block(text, kind='direct_answer', ids=('structure',), questions=('q1',), title='直接回答', **changes):
    return dict(kind=kind, title=title, text=text, question_ids=list(questions), evidence_ids=list(ids), **changes)


def draft(blocks=None, ids=('structure',), primary='1day', **changes):
    return dict(protocol_version=2, primary_timeframe=primary, evidence_ids=list(ids),
                answer_blocks=blocks or [block('当前结构仅作参考，仍需等待确认。')], **changes)


def final(payload):
    return AIMessage(content=json.dumps(payload, ensure_ascii=False))


def settings(**changes):
    return AgentSettings(_env_file=None, agent_mode='mock', deepseek_api_key='',
        **{'agent_report_protocol': 'question_first', 'agent_report_retries': 0,
           'agent_max_rounds': 8, 'agent_tool_timeout': 1, 'agent_timeout': 10, **changes})


def execute(responses, message='分析QQQ日线走势。', market=None, provider=None, **config):
    async def run():
        market_service = market or CountingMarket(provider)
        llm = ScriptedLLM(responses)
        agent = Agent(market_service, settings(**config), llm)
        response = await agent.chat(ChatRequest(message=message))
        return response, llm, market_service
    return asyncio.run(run())


def texts(response):
    return '\n'.join(answer.title + '：' + answer.text for answer in response.report.answer_blocks)


def calculation_draft(**changes):
    return draft(ids=('calculation',), primary=None, trade_plan_evidence_id='calculation', blocks=[
        block('入场{{0.calculation.entry}}、止损{{0.calculation.stop}}、目标{{0.calculation.target}}；'
              '价格风险{{0.calculation.gross_risk}}，目标收益{{0.calculation.gross_reward}}，'
              '毛盈亏比{{0.calculation.gross_reward_to_risk}}。费用未指定，不代表已计入。',
              ids=('calculation',), facts=[{'evidence_id': 'calculation',
              'metric': 'calculation.gross_reward_to_risk', 'label': '毛盈亏比', 'format': 'ratio'}]),
    ], **changes)


def test_buy_question_answers_each_subquestion_without_fixed_indicator_sections():
    answer = draft(ids=('structure', 'candidate'), entry_stance='await_confirmation', blocks=[
        block('价格位于短中期均线上方是支持线索，但最新K线未确认，仍缺少立即买入的充分确认。'),
        block('实际历史胜率未验证，缺少与此入场规则相匹配的历史回测。',
              kind='limitation', ids=(), questions=('q2',), title='胜率限制'),
        block('当前没有可核验的候选止损和目标锚点，无法形成可靠盈亏比；需先明确入场触发和结构失效位置。',
              kind='risk_reward', ids=('candidate',), questions=('q3',), title='方案与盈亏比'),
        block('观察价格回踩均线后能否守住，并核对动量持续性。',
              kind='watch', title='接下来观察'),
    ])
    response, _, market = execute([
        planning(('entry', 'win_rate', 'risk_reward'), proposed_tools=['analyze_market_structure', 'evaluate_trade_plan']),
        tool('structure'), tool('candidate', 'evaluate_trade_plan', mode='candidate', symbol='QQQ'), final(answer),
    ], BUY_QUESTION)
    assert response.status == 'success', response.status_reasons
    assert response.report.validation.valid and response.report.protocol_version == 2
    assert response.report.sections == []
    assert len(response.report.answer_blocks) == 4
    assert {question.kind for question in response.report.intent.questions} == {'entry', 'win_rate', 'risk_reward'}
    assert {qid for answer in response.report.answer_blocks for qid in answer.question_ids} == {'q1', 'q2', 'q3'}
    assert response.report.entry_stance == 'await_confirmation'
    assert '未验证' in texts(response)
    assert market.get_count == 1
    candidate = next(record for record in response.tool_executions if record.call_id == 'candidate')
    assert candidate.result['trade_plan']['available'] is False
    assert len(texts(response)) < 400


def test_explicit_arithmetic_uses_python_without_fetching_or_claiming_market_data():
    response, _, market = execute([
        planning(('risk_reward',), primary=None, proposed_tools=['evaluate_trade_plan']),
        tool('calculation', 'evaluate_trade_plan', mode='evaluate', entry=690, stop=680, target=720),
        final(calculation_draft()),
    ], CALC_QUESTION)
    assert response.status == 'success', response.status_reasons
    assert market.get_count == 0
    assert response.symbol is None and response.asset_class is None
    assert response.timeframes == [] and response.primary_timeframe is None
    assert response.data_sources == ['user_assumption']
    assert response.report.trade_plan['gross_reward_to_risk'] == 3
    assert response.report.trade_plan['gross_risk'] == 10
    assert response.report.trade_plan['gross_reward'] == 30
    assert response.report.trade_plan['historical_win_rate'] is None
    assert response.report.trade_plan['costs']['all_costs_confirmed'] is False
    assert response.report.answer_blocks[0].facts[0].value == 3
    assert '690.00' in texts(response) and '3.00' in texts(response)
    evidence = response.report.evidence[0]
    assert evidence['source'] == 'user_assumption'
    assert evidence['snapshot_id'] is None and evidence['last_updated'] is None
    assert evidence['calculation_id'].startswith('calc_')


def test_arithmetic_short_and_explicit_costs_are_actual_python_results():
    message = '做空入场690、止损700、目标660，手续费1、点差0.2、滑点0.3，盈亏比是多少？'
    response, _, market = execute([
        planning(('risk_reward',), primary=None, proposed_tools=['evaluate_trade_plan']),
        tool('calculation', 'evaluate_trade_plan', mode='evaluate', side='short', entry=690, stop=700,
             target=660, commission_per_unit=1, spread_per_unit=0.2, slippage_per_unit=0.3),
        final(calculation_draft()),
    ], message)
    assert response.status == 'success', response.status_reasons
    calculation = response.report.trade_plan
    assert market.get_count == 0
    assert calculation['net_risk'] == 11.5
    assert calculation['net_reward'] == 28.5
    assert calculation['net_reward_to_risk'] == pytest.approx(28.5 / 11.5)
    assert calculation['costs']['all_costs_confirmed'] is True
    assert calculation['historical_win_rate_status'] == 'unverified'


def test_followup_reuses_thread_but_uses_only_current_turn_plan_and_evidence():
    async def run():
        first_draft = draft()
        second_draft = draft(ids=('four',), primary='4h', blocks=[
            block('动量读数仅是线索，不能据此宣称已经企稳；仍需价格回踩确认。', ids=('four',)),
        ])
        llm = ScriptedLLM([
            planning(), tool('structure'), final(first_draft),
            planning(('stabilization',), '4h', call_id='new-plan'),
            tool('four', interval='4h'), final(second_draft),
        ])
        agent = Agent(CountingMarket(), settings(), llm)
        first = await agent.chat(ChatRequest(message='分析QQQ日线走势。'))
        second = await agent.chat(ChatRequest(message='那4小时有没有出现企稳信号？', thread_id=first.thread_id))
        assert first.status == second.status == 'success'
        assert second.thread_id == first.thread_id
        assert second.timeframes == ['4h'] and second.primary_timeframe == '4h'
        assert second.report.intent.questions[0].kind == 'stabilization'
        assert {item['call_id'] for item in second.report.evidence} == {'four'}
        assert [item.call_id for item in second.tool_executions] == ['new-plan', 'four']
        messages = [item.content for item in llm.seen[-1] if isinstance(item, HumanMessage)]
        assert messages == ['分析QQQ日线走势。', '那4小时有没有出现企稳信号？']
    asyncio.run(run())


def test_observations_only_has_three_relevant_blocks_and_no_fixed_sections():
    watches = [block(text, kind='watch', title=title) for title, text in [
        ('价格确认', '观察价格回踩均线后能否守住。'),
        ('结构确认', '观察区间边界是否出现有效突破与回踩。'),
        ('动量确认', '观察动量能否持续改善，避免把超卖当作企稳。'),
    ]]
    response, _, _ = execute([planning(('watch',)), tool('structure'), final(draft(watches))],
                            '只告诉我最重要的三个观察信号，其他不要展开。')
    assert response.status == 'success', response.status_reasons
    assert len(response.report.answer_blocks) == 3
    assert all(item.kind == 'watch' and not item.detail for item in response.report.answer_blocks)
    assert response.report.sections == []
    assert len(texts(response)) < 160


def test_levels_are_named_rolling_extremes_and_broken_low_is_not_valid_support():
    class BrokenLow(MockProvider):
        async def fetch(self, query):
            rows = await super().fetch(query)
            close = min(row['l'] for row in rows[-21:-1]) - 30
            rows[-1] = {**rows[-1], 'o': close + .5, 'h': close + 1, 'l': close - 1, 'c': close}
            return rows
    response, _, _ = execute([
        planning(('levels',)), tool('structure'), final(draft(blocks=[
            block('滚动区间低点{{0.analysis.rolling_levels.lower.price}}、高点{{0.analysis.rolling_levels.upper.price}}'
                  '取自前20根K线且不含当前；低点状态为{{0.analysis.rolling_levels.lower.status}}，不能继续称为当前有效支撑。'
                  '高点仅为尚未确认的区间参考，不是经多次测试确认的阻力。', kind='levels', title='方法与有效性'),
        ])),
    ], '当前关键支撑和阻力是怎么得出来的？现在有效吗？', provider=BrokenLow())
    assert response.status == 'success', response.status_reasons
    analysis = response.report.evidence[0]['analysis']
    assert analysis['rolling_levels']['lower']['status'] == 'broken'
    assert analysis['rolling_levels']['lower']['confirmed_support_or_resistance'] is False
    assert '前20根' in texts(response) and '已跌破' in texts(response)
    assert f"{analysis['rolling_levels']['upper']['price']:,.2f}" in texts(response)


def test_requested_indicator_is_read_from_actual_result_and_formatted_once():
    response, _, _ = execute([
        planning(('indicator',), proposed_tools=['calculate_technical_indicators']),
        tool('structure', 'calculate_technical_indicators', metrics=['rsi']),
        final(draft([block('RSI14为{{0.rsi}}，仅作为动量线索。')])),
    ], 'QQQ日线RSI14是多少？')
    assert response.status == 'success', response.status_reasons
    raw = response.tool_executions[-1].result['technical_indicators']['rsi']
    assert f'{raw:,.2f}' in texts(response)
    assert set(response.tool_executions[-1].result['technical_indicators']) == {'rsi'}


def test_long_floating_point_indicator_readings_do_not_leak_into_default_answer():
    response, _, _ = execute([
        planning(('indicator',), proposed_tools=['calculate_technical_indicators']),
        tool('structure', 'calculate_technical_indicators', metrics=['relativeVolume', 'ma20']),
        final(draft([block('均线读数{{0.ma20}}，相对成交量{{0.relativeVolume}}。')])),
    ], '均线与相对成交量当前是多少？')
    assert response.status == 'success', response.status_reasons
    raw = response.report.evidence[0]['technical_indicators']['relativeVolume']
    assert len(str(raw).split('.')[-1]) > 8
    assert str(raw) not in texts(response)
    assert f'{raw:,.2f}' in texts(response)
    assert 'relativeVolume' not in texts(response) and 'technical_indicators' not in texts(response)


def test_comparison_one_call_can_cite_separate_intervals_without_duplicate_snapshots():
    response, _, market = execute([
        planning(('timeframes',), '4h', proposed_tools=['compare_timeframes']),
        tool('comparison', 'compare_timeframes', intervals=['4h', '1day']),
        final(draft(ids=('comparison',), primary='4h', blocks=[
            block('主要周期价格{{0.close}}，日线价格{{1.close}}；背景周期不能替代主要周期确认。', ids=('comparison',), facts=[
                {'evidence_id': 'comparison', 'interval': '4h', 'metric': 'ma20', 'label': '主要周期均线', 'format': 'price'},
                {'evidence_id': 'comparison', 'interval': '1day', 'metric': 'ma20', 'label': '日线均线', 'format': 'price'},
            ]),
        ])),
    ], '比较QQQ四小时和日线，以四小时为主。')
    assert response.status == 'success', response.status_reasons
    assert response.timeframes == ['4h', '1day'] and market.get_count == 2
    assert len(response.tool_executions) == 2
    assert len(response.report.evidence) == 2
    assert len(response.report.answer_blocks[0].facts) == 2


def test_native_calls_match_execution_ledger_and_tool_messages_reach_next_model_round():
    responses = [planning(), tool('structure'), final(draft())]
    response, llm, _ = execute(responses)
    assert response.status == 'success'
    native = [call for answer in responses for call in answer.tool_calls]
    assert [call['id'] for call in native] == [record.call_id for record in response.tool_executions]
    returned = {message.tool_call_id: json.loads(message.content)
                for message in llm.seen[-1] if isinstance(message, ToolMessage)}
    for record in response.tool_executions:
        assert returned[record.call_id] == record.result
    assert response.tool_executions[0].result['kind'] == 'research_plan'
    assert response.report.evidence[0]['call_id'] == 'structure'


@pytest.mark.parametrize('mutation,reason', [
    ('naked_price', 'unverified_numeric_prose'),
    ('missing_ref', 'invalid_numeric_reference'),
    ('raw_dict', 'unformatted_protocol_content'),
    ('unknown_id', 'unknown_evidence_ids'),
])
def test_invalid_price_reference_or_protocol_content_never_becomes_success(mutation, reason):
    answer = draft()
    if mutation == 'naked_price': answer['answer_blocks'][0]['text'] = '现价999999，可以入场。'
    if mutation == 'missing_ref': answer['answer_blocks'][0]['text'] = '价格{{99.close}}。'
    if mutation == 'raw_dict': answer['answer_blocks'][0]['text'] = "市场状态{'support': 'unconfirmed'}。"
    if mutation == 'unknown_id': answer['evidence_ids'] = ['not-an-executed-call']
    response, _, _ = execute([planning(), tool('structure'), final(answer)])
    assert response.status == 'partial' and reason in response.status_reasons
    assert not response.report.validation.valid
    assert response.report.answer_origin == 'deterministic_fallback'
    assert '999999' not in texts(response)


def test_numeric_reference_must_belong_to_the_block_cited_tool():
    response, _, _ = execute([
        planning(), tool('structure'), tool('other'),
        final(draft([block('读数{{1.rsi}}。')])),
    ])
    assert response.status == 'partial'
    assert 'uncited_numeric_reference' in response.status_reasons


@pytest.mark.parametrize('claim', ['历史胜率为75%，可以放心。', '胜率较高，实际历史胜率未验证。'])
def test_fabricated_actual_win_rate_or_unsupported_probability_is_rejected(claim):
    response, _, _ = execute([
        planning(('win_rate',)), tool('structure'), final(draft([block(claim, kind='limitation')])),
    ], '实际胜率是多少？')
    assert response.status == 'partial'
    assert any(reason in response.status_reasons for reason in ['unverified_numeric_prose', 'unsupported_trading_claim'])
    assert response.report.trade_plan is None


def test_theoretical_breakeven_rate_cannot_be_mislabeled_as_actual_win_rate():
    answer = calculation_draft()
    answer['answer_blocks'][0]['text'] = '历史胜率为{{0.calculation.breakeven_win_rate_percent}}。'
    response, _, _ = execute([
        planning(('risk_reward',), primary=None, proposed_tools=['evaluate_trade_plan']),
        tool('calculation', 'evaluate_trade_plan', mode='evaluate', entry=690, stop=680, target=720), final(answer),
    ], CALC_QUESTION)
    assert response.status == 'partial'
    assert 'theoretical_rate_mislabeled' in response.status_reasons


def test_incomplete_model_plan_cannot_silently_omit_user_subquestions():
    response, _, _ = execute([planning(), tool('structure'), final(draft())], BUY_QUESTION)
    assert response.status == 'partial'
    assert 'missing_user_subquestion' in response.status_reasons


def test_missing_research_plan_is_rejected_even_with_valid_market_evidence():
    response, _, _ = execute([tool('structure'), final(draft())])
    assert response.status == 'partial'
    assert 'missing_research_plan' in response.status_reasons


def test_more_than_three_watch_conditions_is_rejected():
    blocks = [block('观察价格与结构能否形成确认。', kind='watch', title='观察条件') for _ in range(4)]
    response, _, _ = execute([planning(('watch',)), tool('structure'), final(draft(blocks))],
                            '只告诉我最重要的三个观察信号。')
    assert response.status == 'partial'
    assert 'too_many_observation_conditions' in response.status_reasons


@pytest.mark.parametrize('omitted', ['current_evidence', 'current_plan'])
def test_previous_turn_evidence_or_plan_cannot_substitute_for_current_turn(omitted):
    async def run():
        second_steps = [planning(('stabilization',), '4h', call_id='new-plan')] if omitted != 'current_plan' else []
        current_id = 'structure' if omitted == 'current_evidence' else 'four'
        second_steps.extend([tool('four', interval='4h'), final(draft(ids=(current_id,), primary='4h', blocks=[
            block('仍需观察企稳确认。', ids=(current_id,)),
        ]))])
        llm = ScriptedLLM([planning(), tool('structure'), final(draft()), *second_steps])
        agent = Agent(CountingMarket(), settings(), llm)
        first = await agent.chat(ChatRequest(message='分析QQQ日线。'))
        second = await agent.chat(ChatRequest(message='那四小时呢？', thread_id=first.thread_id))
        assert first.status == 'success' and second.status == 'partial'
        expected = 'unknown_evidence_ids' if omitted == 'current_evidence' else 'missing_research_plan'
        assert expected in second.status_reasons
        assert {item['call_id'] for item in second.report.evidence} == {'four'}
    asyncio.run(run())


def test_invalid_price_order_fails_tool_instead_of_returning_a_calculated_plan():
    response, _, market = execute([
        planning(('risk_reward',), primary=None, proposed_tools=['evaluate_trade_plan']),
        tool('calculation', 'evaluate_trade_plan', mode='evaluate', entry=690, stop=700, target=720),
        final(calculation_draft()),
    ], '入场690，止损700，目标720，请算多头盈亏比。')
    assert response.status == 'failed' and market.get_count == 0
    assert response.tool_executions[-1].status == 'failed'
    assert response.report.trade_plan is None


def test_tool_timeout_keeps_failed_status_and_execution_trace():
    class Slow(MockProvider):
        async def fetch(self, query):
            await asyncio.sleep(.1)
            return await super().fetch(query)
    response, _, _ = execute([planning(), tool('structure'), final(draft())], provider=Slow(), agent_tool_timeout=.01)
    assert response.status == 'failed'
    assert response.tool_executions[-1].status == 'failed'
    assert response.report.answer_origin == 'deterministic_fallback'


def test_insufficient_history_never_returns_a_successful_looking_research_report():
    class Short(MockProvider):
        async def fetch(self, query):
            return (await super().fetch(query))[-25:]
    response, _, _ = execute([planning(), tool('structure'), final(draft())], provider=Short())
    assert response.status == 'insufficient_data'
    assert 'insufficient_history' in response.status_reasons
    assert response.report.answer_origin == 'deterministic_fallback'
    assert all(answer.kind == 'limitation' for answer in response.report.answer_blocks)


def test_bounded_report_repair_reuses_evidence_without_duplicating_tools():
    invalid = draft()
    invalid['answer_blocks'][0]['text'] = '现价999999。'
    response, llm, market = execute([
        planning(), tool('structure'), final(invalid), final(draft()),
    ], agent_report_retries=1)
    assert response.status == 'success', response.status_reasons
    assert len(response.report.validation.attempts) == 2
    assert not response.report.validation.attempts[0].valid and response.report.validation.attempts[1].valid
    assert [record.call_id for record in response.tool_executions] == ['plan', 'structure']
    assert market.get_count == 1 and len(llm.seen) == 4
    assert 'report_repaired' in response.warnings


def test_failed_bounded_repair_remains_partial_and_drops_unverified_answer():
    invalid = draft([block('现价999999。')])
    response, llm, _ = execute([
        planning(), tool('structure'), final(invalid), final(invalid),
    ], agent_report_retries=1)
    assert response.status == 'partial'
    assert len(response.report.validation.attempts) == 2 and len(llm.seen) == 4
    assert len(response.tool_executions) == 2
    assert '999999' not in texts(response)


def test_model_round_limit_terminates_after_real_planning_and_tools():
    response, _, _ = execute([planning(), tool('structure'), final(draft())], agent_max_rounds=2)
    assert response.status == 'partial'
    assert 'max_rounds' in response.warnings
    assert [record.call_id for record in response.tool_executions] == ['plan', 'structure']


def test_question_first_mode_rejects_downgrade_to_legacy_report_protocol():
    legacy = {'evidence_ids': ['structure'], 'primary_timeframe': '1day', 'conclusion': '需要更多证据'}
    response, _, _ = execute([tool('structure'), final(legacy)])
    assert response.status == 'partial'
    assert response.report.protocol_version == 2
    assert not response.report.validation.valid


def test_user_price_roles_and_direction_cannot_be_silently_reversed():
    # All three numbers are present, but merely checking their set would allow a
    # short trade that reverses the user's explicitly named stop and target.
    response, _, market = execute([
        planning(('risk_reward',), primary=None, proposed_tools=['evaluate_trade_plan']),
        tool('calculation', 'evaluate_trade_plan', mode='evaluate', side='short', entry=690, stop=720, target=680),
        final(calculation_draft()),
    ], CALC_QUESTION)
    assert response.status == 'failed'
    assert response.tool_executions[-1].status == 'failed'
    assert market.get_count == 0 and response.report.trade_plan is None


@pytest.mark.parametrize('as_fact', [False, True])
def test_real_rsi_cannot_be_relabelled_as_historical_win_rate(as_fact):
    answer = block('实际历史胜率未验证。' if as_fact else '实际历史胜率为{{0.rsi}}%，但未验证。',
                   facts=[{'evidence_id': 'structure', 'metric': 'rsi', 'label': '历史胜率', 'format': 'percent'}] if as_fact else [])
    response, _, _ = execute([planning(('win_rate',)), tool('structure'), final(draft([answer]))], '实际胜率是多少？')
    assert response.status == 'partial'
    assert 'unverified_historical_win_rate' in response.status_reasons


def test_user_question_cannot_be_answered_only_inside_collapsed_details():
    response, _, _ = execute([planning(), tool('structure'), final(draft([block('结构仍需确认。', detail=True)]))])
    assert response.status == 'partial'
    assert 'unanswered_subquestion' in response.status_reasons
