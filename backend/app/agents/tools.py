"""Native tools share one market cache but return distinct, typed projections."""
import asyncio
from contextvars import ContextVar
from copy import deepcopy
from decimal import Decimal, InvalidOperation
import hashlib
import json
from math import isfinite
import re
import unicodedata

from langchain_core.tools import StructuredTool

from ..models import MarketQuery, MarketError
from ..services.trade_plan_service import RiskRewardRequest, evaluate_risk_reward
from .tool_schemas import (
    Args, DataArgs, IndicatorArgs, CompareArgs, TradePlanArgs,
    MarketMeta, MarketDataResult, IndicatorResult, StructureResult,
    ComparisonResult, CalculationResult, CandidateResult,
)

RUN = ContextVar('agent_run')


async def snapshot(symbol, asset_class, interval):
    run = RUN.get()
    query = MarketQuery(symbol=symbol, asset=asset_class, interval=interval)
    key = (query.symbol, query.asset, query.interval)
    async with run['lock']:
        if key not in run['cache']:
            value = await run['market'].get(query)
            if not value['candles']:
                raise MarketError('empty_data', '无行情', 404)
            run['cache'][key] = value
        return run['cache'][key]


def compact(d):
    """Legacy canonical audit view, not the payload of every model tool."""
    rows=d['candles']; prior=rows[-21:-1]
    return dict(symbol=d['symbol'],asset_class=d['assetClass'],interval=d['interval'],
                source=d['source'],last_updated=d['last_updated'],snapshot_id=d['snapshot_id'],
                warnings=d['warnings'],insufficient_history=d['indicators']['insufficient_history'],
                technical_indicators=d['indicators']['latest'],market_structure=d['rules'],
                support_resistance={'support':min(r['l'] for r in prior) if len(prior)==20 else None,
                                    'resistance':max(r['h'] for r in prior) if len(prior)==20 else None,
                                    'rule':'前20根K线（不含当前）最低/最高价'})


def metadata(data):
    return MarketMeta(
        symbol=data['symbol'], asset_class=data['assetClass'], interval=data['interval'],
        source=data['source'], last_updated=data['last_updated'], snapshot_id=data['snapshot_id'],
        warnings=data['warnings'], insufficient_history=data['indicators']['insufficient_history'],
    ).model_dump()


def structure_projection(data):
    values, rules = data['indicators']['latest'], data['rules']
    close = values.get('close')
    prior = data['candles'][-21:-1]
    low = min(row['l'] for row in prior) if len(prior) == 20 else None
    high = max(row['h'] for row in prior) if len(prior) == 20 else None
    lower_status = 'unconfirmed' if low is None else 'broken' if close < low else 'potential'
    upper_status = 'unconfirmed' if high is None else 'crossed' if close > high else 'potential'
    positions = rules.get('price_position', {})
    below = positions.get('ma20') == positions.get('ma50') == 'below'
    above = positions.get('ma20') == positions.get('ma50') == 'above'
    reasons = ['最新K线完成状态未确认，结构不能单独确认立即入场。']
    observations = []
    if lower_status == 'broken':
        reasons.append('价格已跌破前期滚动区间低点，该低点不能再视为当前有效支撑。')
        observations.append('观察能否收回已跌破的滚动区间低点并形成回踩确认。')
    elif upper_status == 'crossed':
        reasons.append('价格已越过前期滚动区间高点，仍需回踩和持续性确认。')
        observations.append('观察突破后的回踩能否维持在原区间高点上方。')
    else:
        observations.append('观察区间边界是否出现突破或回踩确认，而非仅触及极值。')
    if below:
        reasons.append('价格同时低于短中期均线，当前结构不支持已确认的多头入场。')
        observations.append('观察价格能否重新站上短中期均线并改善斜率。')
    elif above:
        reasons.append('价格位于短中期均线上方，但止损位置及目标仍需独立评估。')
        observations.append('观察短中期均线回踩是否守住，及斜率是否保持。')
    else:
        observations.append('观察短中期均线的位置和方向能否形成一致结构。')
    observations.append('观察动量是否持续改善，避免把超卖等同于已经企稳。')
    return StructureResult(**metadata(data), analysis={
        'trend': values.get('trendDirection', 'unknown'),
        'ma_alignment': values.get('maAlignment', 'unknown'),
        'price_positions': positions,
        'rsi_state': rules.get('rsi_state', 'unavailable'),
        'rsi_momentum': rules.get('rsi_momentum'),
        'rolling_levels': {
            'lower': {'price': low, 'status': lower_status},
            'upper': {'price': high, 'status': upper_status},
        },
        'entry_assessment': {
            'long': 'not_supported' if below or lower_status == 'broken' else 'unconfirmed',
            'short': 'not_supported' if above or upper_status == 'crossed' else 'unconfirmed',
            'reasons': reasons,
        },
        'observations': observations[:3],
    }).model_dump(mode='json')


def calculation_id(calculation):
    serialized = json.dumps(calculation, sort_keys=True, separators=(',', ':'), allow_nan=False, ensure_ascii=True)
    return 'calc_' + hashlib.sha256(serialized.encode()).hexdigest()


def user_numbers():
    """Check provenance of supplied literals; never choose tools or infer intent."""
    message = unicodedata.normalize('NFKC', RUN.get().get('user_message', ''))
    tokens = re.findall(r'(?<![A-Za-z0-9_.])(?:[-+]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?(?:[eE][-+]?\d+)?|零)(?![A-Za-z0-9_.])', message)
    result = set()
    for token in tokens:
        try:
            result.add(Decimal('0' if token == '零' else token.replace(',', '')))
        except InvalidOperation:
            continue
    return result


def extract_user_assumptions(message):
    """Verify explicit labels, not intent routing or a substitute for an LLM.

    Consume non-overlapping label/value pairs in text order. This disambiguates
    dense prefix notation (入场690止损680目标720) from dense suffix notation
    (690入场680止损720目标), without binding a price to both adjacent labels.
    Anything ambiguous or unsupported is left unresolved and must be clarified.
    """
    text = unicodedata.normalize('NFKC', message)
    labels = {
        'entry': r'(?:入场|进场|建仓|开仓|买入|购入|做多|做空|卖空|卖出开仓)(?:价格|价)?|entry(?:\s*price)?',
        'stop': r'止损(?:价格|价)?|stop(?:\s*loss)?',
        'target': r'(?:止盈|目标)(?:价格|价)?|target(?:\s*price)?|take\s*profit',
        'commission_per_unit': r'手续费|佣金|commission|fee',
        'spread_per_unit': r'点差|spread',
        'slippage_per_unit': r'滑点|slippage',
        'atr': r'ATR(?:14)?|平均真实波幅',
    }
    role = '(?:' + '|'.join('(?:' + pattern + ')' for pattern in labels.values()) + ')'
    number = r'(?<![A-Za-z0-9_.])(?:[-+]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?(?:[eE][-+]?\d+)?|零)(?![A-Za-z0-9_.])'
    prefix_gap = r'\s*(?:(?:设置为|设置成|设为|设成|设在|定为|定在|放在|等于|每单位|每股|往返|合计|成本|为|是|取|约|按|在|=|:)\s*)*(?:(?:美元|美金|USD|\$)\s*)?'
    suffix_gap = r'\s*(?:(?:美元|美金|USD|元|\$)\s*)?(?:(?:作为|做为|用作|设置为|设为|设成|定为|为|是|处|的)\s*)*'
    pair = re.compile(r'(?P<prefix_role>' + role + ')' + prefix_gap + r'(?P<prefix_value>' + number + r')|(?P<suffix_value>' + number + ')' + suffix_gap + r'(?P<suffix_role>' + role + ')', re.IGNORECASE)
    values = {key: set() for key in labels}
    invalid_units = set()
    unresolved_ranges = set()
    for match in pair.finditer(text):
        label = match.group('prefix_role') or match.group('suffix_role')
        field = next(key for key, pattern in labels.items() if re.fullmatch(pattern, label, flags=re.IGNORECASE))
        token = match.group('prefix_value') or match.group('suffix_value')
        value_start = match.start('prefix_value') if match.group('prefix_value') is not None else match.start('suffix_value')
        value_end = match.end('prefix_value') if match.group('prefix_value') is not None else match.end('suffix_value')
        # The tool accepts price-currency amounts per unit, never an unconverted
        # percentage. A numerical match alone cannot establish the correct unit.
        if re.match(r'\s*%', text[value_end:]) or re.search(r'百分之\s*$', text[:value_start]):
            invalid_units.add(field)
            continue
        if re.search(r'(?:不是|并非|不要|不能|不在|不为)\s*$', text[:match.start()]):
            continue
        if (re.match(r'\s*(?:或|至|到|~|—|–|-)\s*' + number, text[value_end:])
                or re.search(number + r'\s*(?:或|至|到|~|—|–|-)\s*$', text[:value_start])):
            unresolved_ranges.add(field)
            continue
        values[field].add(Decimal('0' if token == '零' else token.replace(',', '')))
    long = bool(re.search(r'买入|购入|做多|多头|买多|开多|\blong\b', text, re.IGNORECASE))
    short = bool(re.search(r'做空|卖空|空头|开空|卖出开仓|\bshort\b', text, re.IGNORECASE))
    return {
        'values': {key: next(iter(found)) for key, found in values.items() if len(found) == 1},
        'ambiguous_roles': [key for key, found in values.items() if len(found) > 1 or key in unresolved_ranges],
        'invalid_units': sorted(invalid_units),
        'side': 'long' if long and not short else 'short' if short and not long else None,
        'ambiguous_side': long and short,
    }


def validate_user_direction(side, assumptions):
    if assumptions['ambiguous_side']:
        raise ValueError('本轮同时出现多空方向，请明确本次计算对应哪个方向。')
    if assumptions['side'] is not None and side != assumptions['side']:
        raise ValueError('工具方向与用户明确给出的买入/做多或做空方向不一致，拒绝反向计算。')


def validate_user_prices(arguments, side):
    literals = user_numbers()
    for name in ('entry', 'stop', 'target', 'commission_per_unit', 'spread_per_unit', 'slippage_per_unit', 'atr'):
        value = arguments.get(name)
        if name in ('entry', 'stop', 'target') and value is None:
            raise ValueError('需提供用户明确给出的入场、止损和目标价；不能由模型补造。')
        if value is not None and Decimal(str(value)) not in literals:
            raise ValueError('工具数值不是本轮用户明确提供的假设，拒绝计算。')
    assumptions = extract_user_assumptions(RUN.get().get('user_message', ''))
    validate_user_direction(side, assumptions)
    for name, value in arguments.items():
        if value is None:
            continue
        if name in assumptions['invalid_units']:
            raise ValueError('费用、ATR与价格必须明确为报价币种每单位金额，不能把百分比直接作为价格计算。')
        if name in assumptions['ambiguous_roles'] or name not in assumptions['values']:
            raise ValueError('请明确入场价、止损价、目标价及所提供费用的对应关系；不能从裸数字猜测。')
        if Decimal(str(value)) != assumptions['values'][name]:
            raise ValueError('工具价位或费用角色与用户明确给出的对应关系不一致，拒绝调换。')


def candidate_assessment(data, side, costs):
    latest, rules = data['indicators']['latest'], data['rules']
    entry, atr = latest.get('close'), latest.get('atr')
    method = '历史局部极值（两侧各两根）提供候选失效/目标锚点；价格及MA20/50斜率同向，止损距离至少为ATR的一半。滚动区间极值不替代局部极值。'
    out = dict(available=False, hypothetical=True, reasons=[], method=method, anchors=[])
    direction = 1 if side == 'long' else -1
    if not all(isinstance(value, (int, float)) and isfinite(value) and value > 0 for value in (entry, atr)):
        out['reasons'].append('缺少有效当前价格或ATR，无法核对波动与止损。')
        return out
    averages = [latest.get(key) for key in ('ma20', 'ma50')]
    slopes = [latest.get('slopes', {}).get(key) for key in ('ma20', 'ma50')]
    if any(not isinstance(value, (int, float)) or not isfinite(value) for value in [*averages, *slopes]):
        out['reasons'].append('短中期均线或斜率不足，不能形成有结构依据的候选方案。')
        return out
    if not all((entry - value) * direction > 0 for value in averages) or not all(value * direction > 0 for value in slopes):
        out['reasons'].append('当前价格位置与短中期均线方向未支持该方向；需等待企稳或回踩确认。')
        return out
    # The newest bar may be open. Do not use a pivot whose right-side confirmation
    # needs that bar, and never replace a missing pivot with a rolling range extreme.
    rows = data['candles']
    times = {row['t']: index for index, row in enumerate(rows)}
    def prior_pivots(key):
        field = 'l' if key == 'local_lows' else 'h'
        result = []
        for point in rules.get(key, []):
            index = times.get(point.get('t'))
            value = point.get('price')
            if (index is None or index < 2 or index + 2 >= len(rows) - 1
                    or not isinstance(value, (int, float)) or not isfinite(value) or value <= 0
                    or value != rows[index][field]):
                continue
            neighbors = [rows[other][field] for other in range(index - 2, index + 3) if other != index]
            if all(value < neighbor if field == 'l' else value > neighbor for neighbor in neighbors):
                result.append(point)
        return result
    lows, highs = prior_pivots('local_lows'), prior_pivots('local_highs')
    stop_points = [point for point in (lows if side == 'long' else highs) if (entry - point['price']) * direction > 0]
    target_points = [point for point in (highs if side == 'long' else lows) if (point['price'] - entry) * direction > 0]
    if not stop_points or not target_points:
        out['reasons'].append('缺少当前价格两侧、无需最新K线确认的历史局部极值锚点；不把滚动区间低点直接当止损。')
        return out
    stop = min(stop_points, key=lambda point: abs(entry - point['price']))
    target = min(target_points, key=lambda point: abs(entry - point['price']))
    if abs(entry - stop['price']) < atr * .5:
        out['reasons'].append('候选结构止损距离小于ATR的一半，容易被正常波动触及；不人为扩大目标制造高盈亏比。')
        return out
    request = RiskRewardRequest(side=side, entry=entry, stop=stop['price'], target=target['price'], atr=atr, origin='structural_hypothesis', **costs)
    calculation = evaluate_risk_reward(request).model_dump(mode='json')
    if calculation['status'] != 'evaluated':
        out['reasons'].append('计入已提供成本后潜在收益不为正，无法形成合理候选方案。')
        return out
    out.update(available=True, calculation=calculation, calculation_id=calculation_id(calculation), anchors=[
        {'role': 'stop_hypothesis', **stop}, {'role': 'target_hypothesis', **target},
    ])
    out['reasons'] = ['仅为可编辑结构假设；当前K线是否完成、入场触发及实际交易成本仍需确认。', '未做策略回测，历史胜率未验证。']
    return out


def collect_evidence(records):
    """Hydrate server audit evidence only from the exact cached snapshot executed."""
    run = RUN.get()
    evidence = []
    for record in records:
        if record['status'] != 'success' or record['name'] == 'plan_research':
            continue
        result = record['result']
        if result.get('kind') == 'calculation':
            if result.get('calculation_id') != calculation_id(result['calculation']):
                raise ValueError('工具计算结果与计算编号不一致。')
            evidence.append(dict(call_id=record['call_id'], kind='calculation',
                symbol=result.get('symbol'), asset_class=result.get('asset_class'), interval=None,
                source='user_assumption', last_updated=None, snapshot_id=None,
                calculation_id=result['calculation_id'], calculation=deepcopy(result['calculation']),
                warnings=deepcopy(result['calculation'].get('warnings', [])), insufficient_history=False))
            continue
        for projection in result.get('timeframes', [result]):
            key = (projection.get('symbol'), projection.get('asset_class'), projection.get('interval'))
            data = run['cache'].get(key)
            if data is None or any(projection.get(field) != data.get(source) for field, source in (
                ('symbol', 'symbol'), ('asset_class', 'assetClass'), ('interval', 'interval'),
                ('source', 'source'), ('last_updated', 'last_updated'), ('snapshot_id', 'snapshot_id'),
            )):
                raise ValueError('工具证据与本轮数据快照不一致。')
            item = {'call_id': record['call_id'], **deepcopy(compact(data))}
            item['analysis'] = deepcopy(projection.get('analysis') or structure_projection(data)['analysis'])
            if 'trade_plan' in projection:
                item['trade_plan'] = deepcopy(projection['trade_plan'])
                if projection['trade_plan'].get('calculation'):
                    item['calculation'] = deepcopy(projection['trade_plan']['calculation'])
                    item['calculation_id'] = projection['trade_plan']['calculation_id']
                    item['warnings'] = list(dict.fromkeys([*item['warnings'], *item['calculation'].get('warnings', [])]))
            evidence.append(item)
    return evidence


def build_tools():
    async def get_market_data(symbol, asset_class='stock', interval='1day', limit=5):
        data = await snapshot(symbol, asset_class, interval)
        return MarketDataResult(**metadata(data), latest_close=data['candles'][-1]['c'],
            available_bars=len(data['candles']), ohlcv=data['candles'][-limit:]).model_dump(mode='json')

    async def calculate_technical_indicators(symbol, asset_class='stock', interval='1day', metrics=None):
        data = await snapshot(symbol, asset_class, interval)
        selected = metrics if metrics is not None else ['ma20', 'ma50', 'ma200', 'rsi', 'atr']
        return IndicatorResult(**metadata(data), technical_indicators={key: data['indicators']['latest'].get(key) for key in selected}).model_dump(mode='json')

    async def analyze_market_structure(symbol, asset_class='stock', interval='1day'):
        return structure_projection(await snapshot(symbol, asset_class, interval))

    async def compare_timeframes(symbol, asset_class='stock', intervals=None):
        if len(set(intervals)) < 2:
            raise ValueError('distinct intervals required')
        values = [structure_projection(await snapshot(symbol, asset_class, frame)) for frame in intervals]
        signatures = [(value['analysis']['ma_alignment'], tuple(value['analysis']['price_positions'].items()),
                       value['analysis']['rolling_levels']['lower']['status'], value['analysis']['rolling_levels']['upper']['status'])
                      for value in values]
        unknown = any(value['analysis']['trend'] == 'unknown' for value in values)
        return ComparisonResult(symbol=values[0]['symbol'], asset_class=values[0]['asset_class'], timeframes=values,
            alignment='unknown' if unknown else 'aligned' if len(set(signatures)) == 1 else 'divergent').model_dump(mode='json')

    async def evaluate_trade_plan(mode, side='long', entry=None, stop=None, target=None,
                                  commission_per_unit=None, spread_per_unit=None, slippage_per_unit=None,
                                  atr=None, symbol=None, asset_class='stock', interval='1day'):
        costs = dict(commission_per_unit=commission_per_unit, spread_per_unit=spread_per_unit, slippage_per_unit=slippage_per_unit)
        if mode == 'candidate':
            if not symbol:
                raise ValueError('结构候选方案需要明确品种。')
            if any(value is not None for value in (entry, stop, target, atr)):
                raise ValueError('结构模式仅从Python快照与局部极值取价，不接受模型给定价位。')
            assumptions = extract_user_assumptions(RUN.get().get('user_message', ''))
            validate_user_direction(side, assumptions)
            # Costs still need explicit cost roles in structural mode; a bare
            # zero elsewhere in the request does not establish zero commission.
            for name, value in costs.items():
                if value is not None and (name in assumptions['ambiguous_roles']
                        or name in assumptions['invalid_units']
                        or Decimal(str(value)) != assumptions['values'].get(name)):
                    raise ValueError('交易成本必须为本轮用户按费用类型明确给定的金额假设。')
            data = await snapshot(symbol, asset_class, interval)
            return CandidateResult(**metadata(data), trade_plan=candidate_assessment(data, side, costs)).model_dump(mode='json')
        arguments = dict(entry=entry, stop=stop, target=target, atr=atr, **costs)
        validate_user_prices(arguments, side)
        request = RiskRewardRequest(side=side, origin='user_assumption', **arguments)
        calculation = evaluate_risk_reward(request).model_dump(mode='json')
        if symbol:
            MarketQuery(symbol=symbol, asset=asset_class, interval=interval)
        # A calculation has no market source, market timestamp or market snapshot.
        return CalculationResult(calculation_id=calculation_id(calculation), calculation=calculation).model_dump(mode='json')

    specifications = [
        (get_market_data, DataArgs, '仅获取最近OHLCV、当前收盘和来源；不返回指标或结构。仅需要原始价格或K线时使用。'),
        (calculate_technical_indicators, IndicatorArgs, '仅获取所需Python指标，可用metrics选择RSI/ATR/均线等；不返回结构或原始K线。结构研究不必重复调用此工具。'),
        (analyze_market_structure, Args, '研究趋势/价格位置/动量、滚动区间极值及其是否破位、入场确认限制和观察条件；不重复整份指标快照。'),
        (compare_timeframes, CompareArgs, '只在问题需要周期比较时比较各周期结构；返回精简结构与差异，不再逐周期重复调用其他工具。'),
        (evaluate_trade_plan, TradePlanArgs, '独立风险收益评估：evaluate只计算用户明确提供的价位/成本；candidate检查当前结构是否足以产生可编辑假设，可能明确无方案。Python计算盈亏比，绝不推测历史胜率。'),
    ]
    return [StructuredTool.from_function(coroutine=function, name=function.__name__, args_schema=schema, description=description)
            for function, schema, description in specifications]
