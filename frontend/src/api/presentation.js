// Presentation only: preserve backend values and never infer market data.
export function displayNumber(value, maximumFractionDigits = 2) {
  if (!Number.isFinite(value)) return '—';
  const precision = Number.isInteger(maximumFractionDigits)
    ? Math.max(0, Math.min(8, maximumFractionDigits))
    : 2;
  return new Intl.NumberFormat('zh-CN', {
    minimumFractionDigits: Math.min(2, precision),
    maximumFractionDigits: precision,
  }).format(Object.is(value, -0) ? 0 : value);
}

export function displayTime(value, { timeZone = 'UTC' } = {}) {
  // A timezone is required so the same response never changes meaning with the
  // browser's local timezone. Null and numeric values must not become epoch dates.
  if (typeof value !== 'string') return '时间未知';
  const iso = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})(?::(\d{2})(?:\.\d{1,9})?)?(?:Z|[+-](\d{2}):(\d{2}))$/i.exec(value);
  if (!iso) return '时间未知';
  const [year, month, day, hour, minute, second, offsetHour, offsetMinute] = iso.slice(1).map(part => Number(part ?? 0));
  const leap = year % 4 === 0 && (year % 100 !== 0 || year % 400 === 0);
  const days = [31, leap ? 29 : 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31];
  // Date.parse normalizes e.g. February 30 and 24:00; don't display those as valid data.
  if (month < 1 || month > 12 || day < 1 || day > days[month - 1]
    || hour > 23 || minute > 59 || second > 59 || offsetHour > 23 || offsetMinute > 59) return '时间未知';
  const date = new Date(value);
  if (!Number.isFinite(date.getTime())) return '时间未知';
  try {
    const formatter = new Intl.DateTimeFormat('zh-CN', {
      timeZone, year: 'numeric', month: '2-digit', day: '2-digit',
      hour: '2-digit', minute: '2-digit', hourCycle: 'h23',
    });
    const parts = Object.fromEntries(formatter.formatToParts(date)
      .filter(part => part.type !== 'literal').map(part => [part.type, part.value]));
    const zoneLabel = timeZone === 'Asia/Shanghai' ? '北京时间' : timeZone;
    return `${parts.year}/${parts.month}/${parts.day} ${parts.hour}:${parts.minute} ${zoneLabel}`;
  } catch {
    return '时间未知';
  }
}

export function timeframeLabel(value) {
  return knownLabel({ '1day': '日线', '4h': '4小时', '1h': '1小时' }, value,
    typeof value === 'string' && value.trim() ? value.trim() : '周期未知');
}

export function toolLabel(value) {
  return knownLabel({
    get_market_data: '查询行情',
    calculate_technical_indicators: '计算技术指标',
    analyze_market_structure: '分析市场结构',
    compare_timeframes: '比较多个周期',
    evaluate_trade_plan: '评估交易假设',
    plan_research: '规划研究任务',
  }, value, '未知工具');
}

export function sourceLabel(value) {
  return knownLabel({ mock: '模拟行情', twelve_data: '真实行情 · Twelve Data', user_assumption: '用户假设测算' }, value, '来源未确认');
}

export function displayVolume(value) {
  if (!Number.isFinite(value) || value < 0) return '不可用';
  if (value >= 1e8) return displayNumber(value / 1e8) + '亿';
  if (value >= 1e6) return displayNumber(value / 1e6) + '百万';
  if (value >= 1e4) return displayNumber(value / 1e4) + '万';
  return displayNumber(value, 0);
}

export function displayMetric(value, format = 'number', unit = '') {
  if (!Number.isFinite(value)) return '—';
  if (format === 'volume') return displayVolume(value);
  const formatted = displayNumber(value);
  if (format === 'percent') return formatted + '%';
  return formatted + (typeof unit === 'string' && unit ? ' ' + unit : '');
}

// Older report text contains Python dictionaries and unformatted values. Never
// attempt to infer financial meaning by parsing that prose in the browser.
export function legacyReportSummary(report) {
  const conclusions = ['趋势偏多', '趋势偏空', '周期分歧', '需要更多证据'];
  return conclusions.includes(report?.conclusion)
    ? report.conclusion
    : '本次研究未形成可验证的完整回答，请查看状态与技术详情。';
}

export function reportBlocks(report) {
  if (report?.protocol_version !== 2 || (!report?.validation?.valid && report?.answer_origin !== 'deterministic_fallback') || !Array.isArray(report.answer_blocks)) return [];
  // Only server-validated answer blocks are rendered; legacy sections are never
  // substituted for a missing answer. Keep detailed evidence out of the summary.
  const kinds = new Set(['direct_answer', 'key_evidence', 'levels', 'risk_reward', 'limitation', 'watch']);
  return report.answer_blocks.filter(block => block && kinds.has(block.kind)
    && typeof block.text === 'string' && typeof block.title === 'string');
}

export function calculationOnly(result) {
  const evidence = result?.report?.evidence;
  return result?.report?.protocol_version === 2 && Array.isArray(evidence)
    && evidence.length > 0 && evidence.every(isCalculationEvidence);
}

export function isCalculationEvidence(item) {
  return item?.kind === 'calculation' && item.source === 'user_assumption'
    && item.symbol == null && item.asset_class == null && item.interval == null
    && typeof item.calculation_id === 'string' && Boolean(item.calculation_id);
}

export function riskRewardPayload(fields) {
  if (!['long', 'short'].includes(fields?.side)) throw Error('请选择做多或做空。');
  const body = { side: fields.side, origin: 'user_assumption' };
  for (const name of ['entry', 'stop', 'target', 'commission_per_unit', 'spread_per_unit', 'slippage_per_unit', 'atr']) {
    const value = fields[name];
    const empty = value === null || value === undefined || (typeof value === 'string' && !value.trim());
    if (empty && !['entry', 'stop', 'target'].includes(name)) continue;
    if (empty || !['number', 'string'].includes(typeof value)
      || (typeof value === 'string' && !/^[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:e[+-]?\d+)?$/i.test(value.trim()))
      || !Number.isFinite(Number(value))) {
      throw Error('请填写有效的入场价、止损价、目标价及可选成本。');
    }
    body[name] = Number(value);
  }
  return body;
}

export function warningLabel(value) {
  if (typeof value !== 'string' || !value.trim()) return '执行失败，请查看技术详情。';
  const labels = {
    max_rounds: '已达到本次研究的执行轮次上限。',
    too_many_tools: '模型请求了过多工具，本次研究已停止。',
    model_error: '模型服务请求失败，本次研究未完成。',
    report_repair_model_error: '报告修复时模型请求失败，未接受未经验证的回答。',
    report_repair_requested_tools: '报告修复未遵守输出要求，本次研究未完整完成。',
    report_repaired: '报告已修复并通过证据校验。',
    invalid_report: '模型回答未通过报告校验，本次研究未完整完成。',
    agent_timeout: '研究请求超时，请稍后重试。',
    agent_timeout_or_failure: '研究超时或执行中断，请稍后重试。',
    missing_deepseek_key: '模型服务尚未配置，请检查本地设置。',
    session_context_limit: '会话内容已达上限，请新建会话。',
    no_evidence: '尚未取得有效工具证据，无法生成可靠结论。',
    tool_execution_failed: '部分工具执行失败，请查看技术详情。',
    insufficient_history: '历史数据不足，部分指标或结论无法确认。',
    inconsistent_evidence_identity: '工具证据的品种或来源不一致，未接受分析结论。',
  };
  // Translate only stable protocol codes. Preserve prose and its numbers exactly.
  const fallback = /^[a-z][a-z0-9_]*$/.test(value)
    ? '本次研究未完成，请在技术详情中查看原因。'
    : value;
  return knownLabel(labels, value, fallback);
}

function knownLabel(labels, value, fallback) {
  return typeof value === 'string' && Object.hasOwn(labels, value) ? labels[value] : fallback;
}
