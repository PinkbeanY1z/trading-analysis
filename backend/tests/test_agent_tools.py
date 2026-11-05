"""Native tool boundaries, provenance, specialisation and one-turn cache behaviour."""
import asyncio
from copy import deepcopy
from datetime import datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from app.agents.tools import RUN, build_tools, collect_evidence, candidate_assessment, extract_user_assumptions
from app.market import MarketService
from app.providers import MockProvider


class CountingMarket(MarketService):
    def __init__(self, provider=None):
        super().__init__(provider or MockProvider())
        self.calls = 0

    async def get(self, query):
        self.calls += 1
        return await super().get(query)


def run_tools(operation, message='', provider=None):
    async def run():
        market = CountingMarket(provider)
        context = {'cache': {}, 'market': market, 'lock': asyncio.Lock(), 'user_message': message}
        token = RUN.set(context)
        try:
            tools = {tool.name: tool for tool in build_tools()}
            return await operation(tools, market, context)
        finally:
            RUN.reset(token)
    return asyncio.run(run())


def record(name, result, call_id='a'):
    return {'call_id': call_id, 'name': name, 'status': 'success', 'result': result}


def test_native_tools_return_distinct_payloads_without_recomputing_same_snapshot():
    async def check(tools, market, context):
        query = {'symbol': 'QQQ'}
        prices = await tools['get_market_data'].ainvoke(query)
        indicators = await tools['calculate_technical_indicators'].ainvoke({**query, 'metrics': ['rsi']})
        structure = await tools['analyze_market_structure'].ainvoke(query)
        compared = await tools['compare_timeframes'].ainvoke({**query, 'intervals': ['1day', '4h']})
        assert market.calls == 2
        assert prices['snapshot_id'] == indicators['snapshot_id'] == structure['snapshot_id']
        assert 'ohlcv' in prices and 'technical_indicators' not in prices and 'analysis' not in prices
        assert set(indicators['technical_indicators']) == {'rsi'}
        assert 'ohlcv' not in indicators and 'analysis' not in indicators
        assert 'analysis' in structure and 'technical_indicators' not in structure and 'ohlcv' not in structure
        assert all('analysis' in item and 'technical_indicators' not in item for item in compared['timeframes'])
        assert len(compared['timeframes']) == 2
    run_tools(check)


def test_audit_evidence_is_exact_cached_snapshot_not_a_model_projection_or_plan():
    async def check(tools, market, context):
        projected = await tools['calculate_technical_indicators'].ainvoke({'symbol': 'QQQ', 'metrics': ['rsi']})
        evidence = collect_evidence([
            record('plan_research', {'anything': 'not evidence'}, 'plan'),
            record('calculate_technical_indicators', projected),
        ])
        assert len(evidence) == 1 and evidence[0]['call_id'] == 'a'
        data = context['cache'][('QQQ', 'stock', '1day')]
        assert evidence[0]['technical_indicators'] == data['indicators']['latest']
        assert evidence[0]['snapshot_id'] == projected['snapshot_id']
        evidence[0]['technical_indicators']['rsi'] = -1
        assert data['indicators']['latest']['rsi'] != -1
    run_tools(check)


def test_mismatched_market_snapshot_cannot_become_auditable_evidence():
    async def check(tools, market, context):
        result = await tools['get_market_data'].ainvoke({'symbol': 'QQQ'})
        result['snapshot_id'] = 'invented'
        with pytest.raises(ValueError, match='快照不一致'):
            collect_evidence([record('get_market_data', result)])
    run_tools(check)


@pytest.mark.parametrize('arguments', [
    {'symbol': 'QQQ', 'metrics': ['not_an_indicator']},
    {'symbol': 'QQQ', 'metrics': ['rsi', 'rsi']},
])
def test_indicator_selection_rejects_unknown_and_duplicate_metrics(arguments):
    async def check(tools, market, context):
        with pytest.raises(ValidationError):
            await tools['calculate_technical_indicators'].ainvoke(arguments)
        assert market.calls == 0
    run_tools(check)


def test_broken_rolling_low_is_not_called_valid_support_or_a_confirmed_buy():
    class BrokenLow(MockProvider):
        async def fetch(self, query):
            rows = await super().fetch(query)
            rows[-1].update(c=400, l=399)
            return rows
    async def check(tools, market, context):
        result = await tools['analyze_market_structure'].ainvoke({'symbol': 'QQQ'})
        analysis = result['analysis']
        assert analysis['rolling_levels']['lower']['status'] == 'broken'
        assert not analysis['rolling_levels']['lower']['confirmed_support_or_resistance']
        assert analysis['entry_assessment']['long'] == 'not_supported'
        candidate = await tools['evaluate_trade_plan'].ainvoke({'mode': 'candidate', 'symbol': 'QQQ', 'side': 'long'})
        assert not candidate['trade_plan']['available'] and candidate['trade_plan']['reasons']
        assert 'calculation' not in candidate or candidate['trade_plan'].get('calculation') is None
        assert market.calls == 1
    run_tools(check, provider=BrokenLow())


def test_user_only_calculation_has_no_fake_market_source_or_snapshot():
    async def check(tools, market, context):
        result = await tools['evaluate_trade_plan'].ainvoke({'mode': 'evaluate', 'side': 'long', 'entry': 690, 'stop': 680, 'target': 720})
        calculation = result['calculation']
        assert calculation['gross_risk'] == 10 and calculation['gross_reward'] == 30
        assert calculation['gross_reward_to_risk'] == 3
        assert calculation['historical_win_rate'] is None
        assert calculation['historical_win_rate_status'] == 'unverified'
        assert calculation['breakeven_win_rate_percent'] == 25
        assert result['source'] == 'user_assumption' and result['interval'] is None
        evidence = collect_evidence([record('evaluate_trade_plan', result)])
        assert evidence[0]['kind'] == 'calculation' and evidence[0]['snapshot_id'] is None
        assert evidence[0]['calculation'] == calculation and market.calls == 0
    run_tools(check, message='如果我在690买入、680止损、720止盈，盈亏比是多少？')


@pytest.mark.parametrize('changes', [
    {'entry': 691}, {'stop': 679}, {'target': 999}, {'commission_per_unit': .5}, {'atr': 12},
])
def test_model_invented_prices_costs_and_volatility_are_rejected(changes):
    async def check(tools, market, context):
        arguments = {'mode': 'evaluate', 'entry': 690, 'stop': 680, 'target': 720, **changes}
        with pytest.raises(ValueError, match='不是本轮用户'):
            await tools['evaluate_trade_plan'].ainvoke(arguments)
        assert market.calls == 0
    run_tools(check, message='690入场，680止损，720目标。')


def test_explicit_user_cost_assumptions_are_used_and_not_assumed_known():
    async def check(tools, market, context):
        result = await tools['evaluate_trade_plan'].ainvoke({'mode': 'evaluate', 'entry': 690, 'stop': 680, 'target': 720, 'commission_per_unit': .5, 'spread_per_unit': .2})
        calculation = result['calculation']
        assert calculation['net_risk'] == 10.7
        assert calculation['net_reward'] == 29.3
        assert calculation['costs']['slippage_per_unit'] is None
        assert not calculation['costs']['all_costs_confirmed']
    run_tools(check, message='690入场680止损720目标，往返每单位手续费0.5，点差0.2，滑点未知。')


def test_changed_calculation_cannot_become_auditable_evidence():
    async def check(tools, market, context):
        result = await tools['evaluate_trade_plan'].ainvoke({'mode': 'evaluate', 'entry': 690, 'stop': 680, 'target': 720})
        result['calculation']['net_reward_to_risk'] = 999
        with pytest.raises(ValueError, match='计算编号不一致'):
            collect_evidence([record('evaluate_trade_plan', result)])
    run_tools(check, message='690入场680止损720目标。')


@pytest.mark.parametrize('message', [
    '如果我在690买入、680止损、720止盈，盈亏比是多少？',
    '入场价690止损价680目标价720。',
    '690入场680止损720目标。',
    '做多，入场价格设为690，止损价格680，目标价格720。',
    '690元买入，680美元作为止损，720美元作为目标价。',
    '入场价６９０，止损价６８０，目标价７２０，计算多头。',
])
def test_explicit_user_price_roles_support_prefix_suffix_and_dense_notation(message):
    async def check(tools, market, context):
        response = await tools['evaluate_trade_plan'].ainvoke({'mode': 'evaluate', 'side': 'long', 'entry': 690, 'stop': 680, 'target': 720})
        assert response['calculation']['gross_reward_to_risk'] == 3
        assert market.calls == 0
    run_tools(check, message=message)


@pytest.mark.parametrize('message', [
    '做空，入场690，止损720，目标680。',
    '我在690卖空、720止损、680止盈。',
    '开空，690入场720止损680目标。',
])
def test_explicit_short_user_price_roles_remain_short(message):
    async def check(tools, market, context):
        response = await tools['evaluate_trade_plan'].ainvoke({'mode': 'evaluate', 'side': 'short', 'entry': 690, 'stop': 720, 'target': 680})
        assert response['calculation']['gross_reward_to_risk'] == pytest.approx(1 / 3)
        assert response['calculation']['side'] == 'short' and market.calls == 0
    run_tools(check, message=message)


def test_matching_literal_set_cannot_reverse_user_direction_and_price_roles():
    async def check(tools, market, context):
        with pytest.raises(ValueError, match='方向不一致'):
            await tools['evaluate_trade_plan'].ainvoke({'mode': 'evaluate', 'side': 'short', 'entry': 690, 'stop': 720, 'target': 680})
        assert market.calls == 0
    run_tools(check, message='如果我在690买入、680止损、720止盈，盈亏比是多少？')


def test_a_number_in_an_unrelated_fact_cannot_replace_the_user_stop():
    async def check(tools, market, context):
        with pytest.raises(ValueError, match='对应关系不一致'):
            await tools['evaluate_trade_plan'].ainvoke({'mode': 'evaluate', 'side': 'long', 'entry': 690, 'stop': 680, 'target': 720})
        assert market.calls == 0
    run_tools(check, message='690买入685止损720目标，昨天低点680，不是我的止损。')


@pytest.mark.parametrize('message', [
    '690、680、720，计算盈亏比。',
    '入场690或700，止损680，目标720。',
    '690或700入场，680止损，720目标。',
    '入场690，入场700，止损680，目标720。',
])
def test_unlabelled_or_ambiguous_price_roles_need_clarification(message):
    async def check(tools, market, context):
        with pytest.raises(ValueError, match='对应关系'):
            await tools['evaluate_trade_plan'].ainvoke({'mode': 'evaluate', 'entry': 690, 'stop': 680, 'target': 720})
        assert market.calls == 0
    run_tools(check, message=message)


@pytest.mark.parametrize('value,message', [
    (0, '690买入680止损720目标，历史回测记录0条。'),
    (.5, '690买入680止损720目标，其他数字0.5，手续费未知。'),
    (.5, '690买入680止损720目标，滑点0.5，手续费未知。'),
    (.5, '690买入680止损720目标，手续费0.5%。'),
])
def test_bare_or_wrong_role_or_percentage_does_not_establish_cash_commission(value, message):
    async def check(tools, market, context):
        with pytest.raises(ValueError):
            await tools['evaluate_trade_plan'].ainvoke({'mode': 'evaluate', 'entry': 690, 'stop': 680, 'target': 720, 'commission_per_unit': value})
        assert market.calls == 0
    run_tools(check, message=message)


@pytest.mark.parametrize('cost_text', ['手续费为0', '零手续费', '手续费为零'])
def test_explicit_zero_commission_is_distinct_from_unprovided_other_costs(cost_text):
    async def check(tools, market, context):
        result = await tools['evaluate_trade_plan'].ainvoke({'mode': 'evaluate', 'entry': 690, 'stop': 680, 'target': 720, 'commission_per_unit': 0})
        costs = result['calculation']['costs']
        assert costs['commission_per_unit'] == 0
        assert costs['spread_per_unit'] is None and costs['slippage_per_unit'] is None
        assert not costs['all_costs_confirmed']
    run_tools(check, message='690买入680止损720目标，' + cost_text + '。')


def test_calculation_context_symbol_does_not_create_fake_market_identity():
    async def check(tools, market, context):
        result = await tools['evaluate_trade_plan'].ainvoke({'mode': 'evaluate', 'symbol': 'QQQ', 'entry': 690, 'stop': 680, 'target': 720})
        assert result['symbol'] is result['asset_class'] is result['interval'] is None
        assert result['source'] == 'user_assumption' and market.calls == 0
    run_tools(check, message='QQQ如果690入场680止损720目标。')


def test_candidate_does_not_reverse_explicit_user_direction_or_assume_zero_cost():
    async def check(tools, market, context):
        with pytest.raises(ValueError, match='方向不一致'):
            await tools['evaluate_trade_plan'].ainvoke({'mode': 'candidate', 'symbol': 'QQQ', 'side': 'short'})
        with pytest.raises(ValueError, match='费用类型'):
            await tools['evaluate_trade_plan'].ainvoke({'mode': 'candidate', 'symbol': 'QQQ', 'side': 'long', 'commission_per_unit': 0})
        assert market.calls == 0
    run_tools(check, message='现在买入QQQ合适吗？我的回测记录是0条。')


@pytest.mark.parametrize('key,value', [('entry', True), ('stop', '680'), ('target', float('inf'))])
def test_risk_tool_schema_rejects_non_numbers_and_nonfinite_values(key, value):
    async def check(tools, market, context):
        with pytest.raises(ValidationError):
            await tools['evaluate_trade_plan'].ainvoke({'mode': 'evaluate', 'entry': 690, 'stop': 680, 'target': 720, key: value})
        assert market.calls == 0
    run_tools(check, message='690，680，720')


def test_candidate_mode_rejects_model_invented_prices_before_any_market_fetch():
    async def check(tools, market, context):
        with pytest.raises(ValueError, match='不接受模型'):
            await tools['evaluate_trade_plan'].ainvoke({'mode': 'candidate', 'symbol': 'QQQ', 'entry': 690})
        assert market.calls == 0
    run_tools(check)


def candidate_data():
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    rows = [{'t': (start + timedelta(days=index)).isoformat(), 'o': 101, 'h': 103, 'l': 99, 'c': 101, 'v': 1} for index in range(20)]
    rows[5].update(l=90)
    rows[10].update(h=120)
    return {'candles': rows, 'indicators': {'latest': {'close': 100, 'ma20': 97, 'ma50': 95, 'slopes': {'ma20': .2, 'ma50': .1}, 'atr': 5}},
        'rules': {'local_lows': [{'t': rows[5]['t'], 'price': 90}], 'local_highs': [{'t': rows[10]['t'], 'price': 120}]}}


def test_candidate_uses_verified_local_pivots_and_remains_an_unconfirmed_hypothesis():
    data = candidate_data()
    result = candidate_assessment(data, 'long', {})
    assert result['available'] and result['hypothetical']
    assert result['calculation']['entry'] == 100
    assert result['calculation']['stop'] == 90 and result['calculation']['target'] == 120
    assert result['calculation']['historical_win_rate'] is None
    assert result['anchors'][0]['t'] == data['candles'][5]['t']


@pytest.mark.parametrize('failure', ['recent_unfinished_confirmation', 'wrong_pivot_price', 'missing_target', 'too_tight', 'opposing_slope'])
def test_candidate_refuses_unverified_or_unreasonable_structure(failure):
    data = candidate_data()
    if failure == 'recent_unfinished_confirmation':
        data['rules']['local_lows'][0]['t'] = data['candles'][-3]['t']
    elif failure == 'wrong_pivot_price':
        data['rules']['local_lows'][0]['price'] = 89
    elif failure == 'missing_target':
        data['rules']['local_highs'] = []
    elif failure == 'too_tight':
        data['indicators']['latest']['atr'] = 30
    else:
        data['indicators']['latest']['slopes']['ma50'] = -.1
    result = candidate_assessment(data, 'long', {})
    assert not result['available'] and result['reasons'] and 'calculation' not in result
