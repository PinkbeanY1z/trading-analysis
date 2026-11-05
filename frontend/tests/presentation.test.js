import test from 'node:test';
import assert from 'node:assert/strict';
import { displayNumber, displayVolume, displayMetric, displayTime, timeframeLabel, toolLabel, sourceLabel, warningLabel, legacyReportSummary, reportBlocks, calculationOnly, riskRewardPayload } from '../src/api/presentation.js';

test('prices use grouped tabular-friendly decimals without floating point tails', () => {
  assert.equal(displayNumber(4071.4126945), '4,071.41');
  assert.equal(displayNumber(0), '0.00');
  assert.equal(displayNumber(-0), '0.00');
  assert.equal(displayNumber(-12.999), '-13.00');
});

test('unavailable numbers remain absent instead of coercing strings or null to prices', () => {
  for (const value of [null, undefined, NaN, Infinity, -Infinity, '12.3', '', false]) {
    assert.equal(displayNumber(value), '—');
  }
});

test('requested number precision supports volume and small values', () => {
  assert.equal(displayNumber(12345.67, 0), '12,346');
  assert.equal(displayNumber(0.00012345, 6), '0.000123');
  assert.equal(displayNumber(12.5, undefined), '12.50');
});

test('timestamps show UTC and minutes consistently across explicit timezone offsets', () => {
  assert.equal(displayTime('2026-10-30T17:30:00+00:00'), '2026/10/30 17:30 UTC');
  assert.equal(displayTime('2026-10-31T01:30:00+08:00'), '2026/10/30 17:30 UTC');
  assert.equal(displayTime('2026-10-30T17:30:00.123456Z'), '2026/10/30 17:30 UTC');
});

test('a selected display timezone changes the date and names the timezone explicitly', () => {
  assert.equal(displayTime('2026-10-30T17:30:00Z', { timeZone: 'Asia/Shanghai' }), '2026/10/31 01:30 北京时间');
  assert.equal(displayTime('2026-10-30T00:00:00Z'), '2026/10/30 00:00 UTC');
});

test('missing, invalid and timezone-ambiguous timestamps never become misleading dates', () => {
  for (const value of [null, undefined, 0, '', 'invalid', '2026-10-30T17:30:00', '2026-13-30T17:30:00Z',
    '2026-02-29T17:30:00Z', '2026-02-30T17:30:00Z', '2026-10-30T24:00:00Z']) {
    assert.equal(displayTime(value), '时间未知');
  }
  assert.equal(displayTime('2026-10-30T17:30:00Z', { timeZone: 'invalid' }), '时间未知');
  assert.equal(displayTime('2024-02-29T17:30:00Z'), '2024/02/29 17:30 UTC');
});

test('supported periods and tools have concise Chinese labels with explicit unknown fallbacks', () => {
  assert.equal(timeframeLabel('1day'), '日线');
  assert.equal(timeframeLabel('4h'), '4小时');
  assert.equal(timeframeLabel('1h'), '1小时');
  assert.equal(timeframeLabel('15min'), '15min');
  assert.equal(timeframeLabel(null), '周期未知');
  assert.equal(toolLabel('get_market_data'), '查询行情');
  assert.equal(toolLabel('calculate_technical_indicators'), '计算技术指标');
  assert.equal(toolLabel('analyze_market_structure'), '分析市场结构');
  assert.equal(toolLabel('compare_timeframes'), '比较多个周期');
  assert.equal(toolLabel('unknown_server_value'), '未知工具');
  assert.equal(toolLabel('constructor'), '未知工具');
  assert.equal(toolLabel('__proto__'), '未知工具');
});

test('source labels never present Mock or unknown sources as real data', () => {
  assert.equal(sourceLabel('mock'), '模拟行情');
  assert.equal(sourceLabel('twelve_data'), '真实行情 · Twelve Data');
  assert.equal(sourceLabel(null), '来源未确认');
  assert.equal(sourceLabel('other'), '来源未确认');
  assert.equal(sourceLabel('toString'), '来源未确认');
});

test('warning codes explain the actual failure while prose and numeric evidence stay untouched', () => {
  assert.equal(warningLabel('max_rounds'), '已达到本次研究的执行轮次上限。');
  assert.equal(warningLabel('model_error'), '模型服务请求失败，本次研究未完成。');
  assert.equal(warningLabel('report_repair_model_error'), '报告修复时模型请求失败，未接受未经验证的回答。');
  assert.equal(warningLabel('report_repaired'), '报告已修复并通过证据校验。');
  assert.equal(warningLabel('invalid_report'), '模型回答未通过报告校验，本次研究未完整完成。');
  assert.equal(warningLabel('agent_timeout_or_failure'), '研究超时或执行中断，请稍后重试。');
  assert.equal(warningLabel('unexpected_protocol_code'), '本次研究未完成，请在技术详情中查看原因。');
  assert.equal(warningLabel('constructor'), '本次研究未完成，请在技术详情中查看原因。');
  const prose = '最新K线可能尚未完成；MA20 为 4,071.41，时间 2026-10-30T17:30:00Z。';
  assert.equal(warningLabel(prose), prose);
  assert.equal(warningLabel('Mock LLM：预设工具调用脚本，不代表模型自主理解已验证。'), 'Mock LLM：预设工具调用脚本，不代表模型自主理解已验证。');
  assert.equal(warningLabel({ error: 'model_error', price: 123.45 }), '执行失败，请查看技术详情。');
  assert.equal(warningLabel(null), '执行失败，请查看技术详情。');
});

test('financial metrics render concise ratios, percentages and readable unavailable volumes', () => {
  assert.equal(displayNumber(728.18749989999999), '728.19');
  assert.equal(displayMetric(34.88359951875802, 'number'), '34.88');
  assert.equal(displayMetric(3, 'ratio'), '3.00');
  assert.equal(displayMetric(25, 'percent'), '25.00%');
  assert.equal(displayMetric(null, 'percent'), '—');
  assert.equal(displayMetric(10, 'price', 'USD'), '10.00 USD');
  assert.equal(displayVolume(85108900), '85.11百万');
  assert.equal(displayVolume(13590), '1.36万');
  assert.equal(displayVolume(100000000), '1.00亿');
  assert.equal(displayVolume(3000), '3,000');
  for (const missing of [null, undefined, -1, NaN, Infinity]) assert.equal(displayVolume(missing), '不可用');
});

test('legacy prose never becomes a default dictionary or floating point dump', () => {
  const legacy = { conclusion: '周期分歧', sections: [{ title: '全部指标', text: "{'ma20': 728.18749989999999}" }] };
  assert.equal(legacyReportSummary(legacy), '周期分歧');
  assert.deepEqual(reportBlocks(legacy), []);
  assert.equal(legacyReportSummary({ conclusion: "{'price': 123.456789}" }), '本次研究未形成可验证的完整回答，请查看状态与技术详情。');
});

test('only validated question-first answers or explicit server fallback are displayable', () => {
  const direct = { kind: 'direct_answer', title: '能否入场', text: '当前缺少确认。', detail: false };
  const report = { protocol_version: 2, validation: { valid: true }, answer_blocks: [direct] };
  assert.deepEqual(reportBlocks(report), [direct]);
  assert.deepEqual(reportBlocks({ ...report, validation: { valid: false } }), []);
  assert.deepEqual(reportBlocks({ ...report, validation: { valid: false }, answer_origin: 'deterministic_fallback' }), [direct]);
  assert.deepEqual(reportBlocks({ ...report, answer_blocks: [{ kind: 'untrusted', title: 'raw', text: '{}' }, { ...direct, text: {} }] }), []);
});

test('pure hypothetical calculation is distinguished from market identity', () => {
  const calculation = { kind: 'calculation', source: 'user_assumption', calculation_id: 'computed-snapshot', symbol: null, asset_class: null, interval: null };
  const report = { protocol_version: 2, evidence: [calculation] };
  assert.equal(calculationOnly({ report }), true);
  assert.equal(calculationOnly({ report: { ...report, evidence: [] } }), false);
  assert.equal(calculationOnly({ report: { ...report, evidence: [{ ...calculation, source: 'mock' }] } }), false);
  assert.equal(calculationOnly({ report: { ...report, evidence: [{ ...calculation, symbol: 'QQQ', interval: '1day' }] } }), false);
});

test('calculator sends user prices unrounded and omits unconfirmed costs instead of zeroing them', () => {
  const payload = riskRewardPayload({ side: 'long', entry: '690.123456789', stop: '680', target: '720', commission_per_unit: '', spread_per_unit: '0', slippage_per_unit: null, atr: undefined });
  assert.deepEqual(payload, { side: 'long', origin: 'user_assumption', entry: 690.123456789, stop: 680, target: 720, spread_per_unit: 0 });
  assert.equal(Object.hasOwn(payload, 'commission_per_unit'), false);
  assert.equal(Object.hasOwn(payload, 'slippage_per_unit'), false);
  assert.equal(Object.hasOwn(payload, 'net_reward_to_risk'), false);
});

test('calculator does not manufacture prices from empty fields or malformed values', () => {
  const base = { side: 'short', entry: '690', stop: '700', target: '660' };
  assert.deepEqual(riskRewardPayload(base), { side: 'short', origin: 'user_assumption', entry: 690, stop: 700, target: 660 });
  for (const value of ['', ' ', false, {}, [], '0x10', 'Infinity', Infinity]) {
    assert.throws(() => riskRewardPayload({ ...base, entry: value }));
  }
  assert.throws(() => riskRewardPayload({ ...base, side: 'other' }));
});

test('planning and trade evaluation tools have specific labels', () => {
  assert.equal(toolLabel('plan_research'), '规划研究任务');
  assert.equal(toolLabel('evaluate_trade_plan'), '评估交易假设');
  assert.equal(sourceLabel('user_assumption'), '用户假设测算');
});
