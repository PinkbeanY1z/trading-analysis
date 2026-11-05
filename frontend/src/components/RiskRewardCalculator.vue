<template>
  <section class="risk-calculator" aria-label="盈亏比测算">
    <header><h3>盈亏比测算</h3><span>假设方案 · 不执行交易</span></header>
    <p class="calculator-note">填写或调整价位，由 Python 计算。所有金额按同一报价货币每单位计，不代表当前可成交价格或有效止损。</p>
    <form @submit.prevent="calculate">
      <fieldset :disabled="busy">
        <label class="side-field">方向<select aria-label="测算方向" v-model="fields.side"><option value="long">做多</option><option value="short">做空</option></select></label>
        <div class="price-fields">
          <label>入场价<input aria-label="入场价" v-model="fields.entry" type="number" inputmode="decimal" step="any" min="0.00000001" required placeholder="填写价格"></label>
          <label>止损价<input aria-label="止损价" v-model="fields.stop" type="number" inputmode="decimal" step="any" min="0.00000001" required placeholder="填写价格"></label>
          <label>目标价<input aria-label="目标价" v-model="fields.target" type="number" inputmode="decimal" step="any" min="0.00000001" required placeholder="填写价格"></label>
        </div>
        <p class="order-hint">{{ fields.side === 'long' ? '做多：止损价 < 入场价 < 目标价' : '做空：目标价 < 入场价 < 止损价' }}</p>
        <details class="cost-inputs"><summary>可选成本与波动检查 <span>留空表示未计入</span></summary>
          <p>费用、点差与滑点均填写往返合计、每单位金额；百分比费率需先换算。明确填 0 才表示假设该项为零。</p>
          <div class="optional-fields">
            <label>手续费<input aria-label="往返每单位手续费" v-model="fields.commission_per_unit" type="number" inputmode="decimal" step="any" min="0" placeholder="未指定"></label>
            <label>点差<input aria-label="往返每单位点差" v-model="fields.spread_per_unit" type="number" inputmode="decimal" step="any" min="0" placeholder="未指定"></label>
            <label>滑点<input aria-label="往返每单位滑点" v-model="fields.slippage_per_unit" type="number" inputmode="decimal" step="any" min="0" placeholder="未指定"></label>
            <label>ATR<input aria-label="假设ATR" v-model="fields.atr" type="number" inputmode="decimal" step="any" min="0.00000001" placeholder="未指定"></label>
          </div>
        </details>
        <button type="submit" class="primary calculate-button" :disabled="busy">{{ busy ? '计算中…' : '计算盈亏比' }}</button>
      </fieldset>
    </form>
    <p v-if="error" role="alert" class="notice error">{{ error }}</p>
    <div v-if="result" class="calculation-result" role="status" data-testid="risk-reward-result">
      <p v-if="result.status === 'unfavorable_after_costs'" class="notice">计入已提供成本后，目标收益不为正，不能形成具有正目标收益的方案。</p>
      <div class="ratio-result"><span>收益 / 风险</span><strong>{{ displayNumber(result.net_reward_to_risk) }}</strong><small>仅扣除已提供成本</small></div>
      <dl><dt>入场 / 止损 / 目标</dt><dd>{{ displayNumber(result.entry) }} / {{ displayNumber(result.stop) }} / {{ displayNumber(result.target) }}</dd><dt>价格风险 / 目标收益</dt><dd>{{ displayNumber(result.gross_risk) }} / {{ displayNumber(result.gross_reward) }}</dd><dt>已计入往返成本</dt><dd>{{ displayNumber(result.costs.total_included_cost) }}</dd><dt>扣除已提供成本后的风险 / 收益</dt><dd>{{ displayNumber(result.net_risk) }} / {{ displayNumber(result.net_reward) }}</dd><dt>未计成本的收益 / 风险</dt><dd>{{ displayNumber(result.gross_reward_to_risk) }}</dd><dt v-if="result.stop_distance_atr !== null">止损距离 / ATR</dt><dd v-if="result.stop_distance_atr !== null">{{ displayNumber(result.stop_distance_atr) }}</dd></dl>
      <p class="cost-confirmation">{{ result.costs.all_costs_confirmed ? '全部成本均由用户明确指定，仍受成交假设限制。' : '成本尚未完整确认；未指定项未计入，并非完整净收益。' }}</p>
      <p v-for="warning in atrWarnings" :key="warning" class="notice">{{ warning }}</p>
      <div class="win-rate-lines"><p>实际历史胜率 <strong>未验证</strong></p><p>理论盈亏平衡所需胜率 <strong>{{ displayMetric(result.breakeven_win_rate_percent, 'percent') }}</strong></p><small>理论门槛由本方案盈亏计算，与策略真实胜率不同。</small></div>
      <details class="calculation-limits"><summary>测算假设与限制</summary><p v-for="warning in result.warnings" :key="warning">{{ warning }}</p><p v-for="assumption in result.assumptions" :key="assumption">{{ assumption }}</p></details>
    </div>
  </section>
</template>

<script setup>
import { computed, onBeforeUnmount, reactive, ref, watch } from 'vue';
import { displayMetric, displayNumber, riskRewardPayload } from '../api/presentation';
const props = defineProps({ apiBase: { type: String, default: '/api' }, initialPlan: { type: Object, default: null } });
const fields = reactive({ side: 'long', entry: '', stop: '', target: '', commission_per_unit: '', spread_per_unit: '', slippage_per_unit: '', atr: '' });
const busy = ref(false), error = ref(''), result = ref(null);
const atrWarnings = computed(() => (result.value?.warnings || []).filter(warning => typeof warning === 'string' && warning.startsWith('止损距离小于')));
let controller, requestNumber = 0;
watch(fields, () => { result.value = null; error.value = ''; });
watch(() => props.initialPlan, plan => {
  controller?.abort(); requestNumber++; busy.value = false;
  for (const key of Object.keys(fields)) fields[key] = key === 'side' ? (plan?.side === 'short' ? 'short' : 'long') : '';
  if (plan) {
    for (const key of ['entry', 'stop', 'target', 'atr']) if (Number.isFinite(plan[key])) fields[key] = plan[key];
    for (const key of ['commission_per_unit', 'spread_per_unit', 'slippage_per_unit']) if (Number.isFinite(plan.costs?.[key])) fields[key] = plan.costs[key];
  }
  result.value = null; error.value = '';
}, { immediate: true });
async function calculate() {
  if (busy.value) return;
  result.value = null; error.value = '';
  let body;
  try { body = riskRewardPayload(fields); } catch (failure) { error.value = failure.message; return; }
  busy.value = true; controller = new AbortController();
  const requestController = controller;
  const id = ++requestNumber;
  const timer = setTimeout(() => requestController.abort(), 15000);
  try {
    const response = await fetch(props.apiBase + '/analysis/risk-reward', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body), signal: requestController.signal });
    const data = await response.json().catch(() => null);
    if (!response.ok) throw Error(typeof data?.error === 'string' ? data.error : '测算失败，请检查价格顺序与可选成本。');
    if (!data || !Number.isFinite(data.net_reward_to_risk) || !data.costs || data.historical_win_rate_status !== 'unverified') throw Error('测算响应不完整，未显示未经确认的结果。');
    if (id === requestNumber) result.value = data;
  } catch (failure) {
    if (id === requestNumber) error.value = failure.name === 'AbortError' ? '测算请求超时，请稍后重试。' : failure instanceof TypeError ? '测算服务连接失败，请检查后端后重试。' : failure.message;
  } finally { clearTimeout(timer); if (id === requestNumber) busy.value = false; }
}
onBeforeUnmount(() => { requestNumber++; controller?.abort(); });
</script>

<style scoped>
.risk-calculator { border: 1px solid var(--border-strong); border-radius: 8px; padding: 14px; margin-bottom: 20px; }
header { display: flex; align-items: baseline; justify-content: space-between; flex-wrap: wrap; gap: 4px; }
header span, .calculator-note, .order-hint, .cost-inputs p, .cost-confirmation { font-size: 10px; color: var(--muted); line-height: 1.75; }
.calculator-note { margin: 8px 0 12px; }
fieldset { border: 0; margin: 0; padding: 0; min-width: 0; }
label { display: grid; gap: 4px; min-width: 0; color: var(--muted); font-size: 11px; }
input, select { width: 100%; min-width: 0; padding: 8px; border: 1px solid var(--border); border-radius: var(--radius); background: var(--bg); color: var(--text); font-family: var(--font-number); font-size: 12px; }
input:focus-visible { outline: 2px solid var(--primary-hover); outline-offset: 2px; }
.side-field { grid-template-columns: 40px minmax(0,1fr); align-items: center; margin-bottom: 10px; }
.price-fields { display: grid; grid-template-columns: repeat(3,minmax(0,1fr)); gap: 8px; }
.order-hint { margin: 6px 0 0; }
.optional-fields { display: grid; grid-template-columns: repeat(2,minmax(0,1fr)); gap: 8px; margin: 10px 0; }
.calculate-button { width: 100%; font-size: 12px; margin-top: 12px; }
.notice { margin-top: 12px; }
.calculation-result { margin-top: 16px; border-top: 1px solid var(--border); padding-top: 12px; }
.ratio-result { display: flex; align-items: baseline; flex-wrap: wrap; gap: 6px 12px; }
.ratio-result > span, .ratio-result small { font-size: 10px; color: var(--muted); }
.ratio-result strong { font-size: 24px; font-weight: 500; font-family: var(--font-number); }
dl { grid-template-columns: minmax(90px,1fr) minmax(0,1fr); font-size: 10px; margin: 12px 0; }
dd { text-align: right; font-family: var(--font-number); font-variant-numeric: tabular-nums; }
.win-rate-lines { border-top: 1px solid var(--border); margin-top: 12px; padding-top: 10px; }
.win-rate-lines p { display: flex; justify-content: space-between; gap: 10px; font-size: 11px; }
.win-rate-lines strong { font-weight: 500; white-space: nowrap; }
.win-rate-lines small, .calculation-limits p { color: var(--muted); font-size: 10px; line-height: 1.8; }
.calculation-limits { margin-top: 8px; }
@media (max-width: 1023px) { input, select { font-size: 16px; min-height: 44px; } .calculate-button { min-height: 44px; } }
</style>
