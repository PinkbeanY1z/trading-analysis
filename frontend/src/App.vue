<template>
  <div class="app">
    <header class="topbar">
      <div class="brand"><span class="brand-mark" aria-hidden="true">↗</span><span>Trading<span class="brand-light">Analysis</span><small>行情研究终端</small></span></div>
      <div class="controls">
        <label class="symbol-select"><span class="sr-only">品种</span><select aria-label="品种" v-model="symbol" @change="load()"><option v-for="s in symbols" :key="s.symbol" :value="s.symbol">{{ s.symbol === 'XAU/USD' ? 'XAU/USD · 黄金' : s.symbol }}</option></select></label>
        <label><span class="sr-only">主图周期</span><select aria-label="主图周期" v-model="interval" @change="load()"><option value="1day">日线</option><option value="4h">4小时</option><option value="1h">1小时</option></select></label>
        <span class="source-badge" :class="{ mock: currentChart?.source === 'mock' }"><span class="status-dot"></span>{{ currentChart ? sourceLabel(currentChart.source) : '等待行情' }}</span>
        <button class="refresh-button" @click="load()" :disabled="loading"><span aria-hidden="true" :class="{ spinning: loading }">↻</span><span>{{ loading ? '加载中' : '刷新' }}</span></button>
      </div>
      <button class="assistant-toggle" :aria-expanded="assistantOpen" aria-controls="assistant-pane" @click="assistantOpen = !assistantOpen">{{ assistantOpen ? '收起 AI' : '展开 AI' }} <span aria-hidden="true">{{ assistantOpen ? '→' : '←' }}</span></button>
    </header>
    <div class="global-notices" v-if="error || selectionDiffers">
      <p role="alert" class="notice error" v-if="error">{{ error }}。{{ charts.length ? '已保留上次成功图表，请以图表标题为准。' : '当前没有可用图表。' }}</p>
      <p class="notice" v-if="selectionDiffers">正在选择 {{ symbol }} / {{ timeframeLabel(interval) }}，下方仍是上次成功数据。</p>
    </div>
    <ResearchWorkspace v-model:assistant-open="assistantOpen" :busy="busy" :unread="unread">
      <template #market>
        <div class="market-content" :aria-busy="loading">
          <template v-if="currentChart">
            <header class="market-heading">
              <div><div class="eyebrow">{{ currentChart.assetClass === 'gold' ? '贵金属 · 美元报价' : '美股 · 美元报价' }}<span class="period-label">{{ timeframeLabel(currentChart.interval) }}</span></div><h1>{{ currentChart.symbol }} <span>{{ currentChart.symbol === 'QQQ' ? '纳斯达克 100 ETF' : currentChart.symbol === 'XAU/USD' ? '现货黄金' : '行情研究' }}</span></h1></div>
              <div class="quote"><strong>{{ displayNumber(currentChart.candles.at(-1)?.c) }}</strong><span>最新收盘价 · {{ currentChart.currency || 'USD' }}</span></div>
            </header>
            <div class="market-toolbar">
              <div class="period-switch" role="group" aria-label="快捷周期"><button v-for="frame in ['1h', '4h', '1day']" :key="frame" :aria-pressed="interval === frame" @click="interval = frame; load()">{{ timeframeLabel(frame) }}</button></div>
              <label class="legacy-toggle"><input type="checkbox" v-model="legacy"> MA7/25/60</label>
              <span class="toolbar-caption">{{ legacy ? '短周期均线' : 'MA20 / 50 / 200' }} · RSI14 · 成交量</span>
            </div>
            <article class="chart-area primary-chart" :data-testid="'chart-' + currentChart.interval">
              <div class="chart-caption"><span>{{ currentChart.symbol }} · {{ timeframeLabel(currentChart.interval) }} · 主图</span><time :datetime="currentChart.last_updated">{{ displayTime(currentChart.last_updated) }}</time></div>
              <p v-if="currentChart.indicators.insufficient_history" role="status" class="notice">历史不足，长期均线不可用，结构尚未确认。</p>
              <KlineChart v-bind="chartProps(currentChart)" />
            </article>
            <div class="market-metrics"><span>滚动区间低点 <b>{{ displayNumber(currentChart.rules.support) }}</b></span><span>滚动区间高点 <b>{{ displayNumber(currentChart.rules.resistance) }}</b></span><span>最新 RSI14 <b>{{ displayNumber(currentChart.indicators.latest.rsi) }}</b></span><span>{{ currentChart.assetClass === 'gold' || !currentChart.indicators.latest.volumeAvailable ? '成交量不可用' : '最新成交量 ' + displayNumber(currentChart.indicators.latest.volume, 0) }}</span></div>
            <p v-if="chartWarnings.includes(snapshotWarning)" role="alert" class="notice">{{ snapshotWarning }}</p>
            <p class="quality-caption">最新K线完成状态未确认 · 上涨绿 / 下跌红</p>
            <details v-if="comparisonCharts.length" class="comparison-group"><summary>查看比较图 <span>{{ comparisonCharts.map(d => timeframeLabel(d.interval)).join(' / ') }}</span></summary><article v-for="d in comparisonCharts" :key="d.symbol + d.interval" class="chart-area comparison-chart" :data-testid="'chart-' + d.interval"><div class="chart-caption"><strong>{{ d.symbol }} · {{ timeframeLabel(d.interval) }} · 比较图</strong><time :datetime="d.last_updated">{{ displayTime(d.last_updated) }}</time></div><KlineChart v-bind="chartProps(d)" /></article></details>
            <details class="data-details"><summary>数据与质量说明 <span>{{ chartWarnings.length ? chartWarnings.length + ' 条提示' : '来源及时间' }}</span></summary><p v-for="w in chartWarnings" :key="w" class="notice">{{ w }}</p><p>滚动区间极值取前20根K线（不含当前），尚非经多次测试确认的支撑阻力。最新K线是否完成：未确认。</p><dl v-for="d in charts" :key="d.interval"><dt>品种 / 周期</dt><dd>{{ d.symbol }} · {{ timeframeLabel(d.interval) }}</dd><dt>来源</dt><dd>{{ sourceLabel(d.source) }} · {{ d.source }}</dd><dt>数据时间</dt><dd>{{ displayTime(d.last_updated) }}</dd><dt>获取时间</dt><dd>{{ displayTime(d.fetched_at) }}</dd><dt>数据快照</dt><dd class="technical-id">{{ d.snapshot_id || '接口未提供' }}</dd></dl></details>
          </template>
          <div v-else class="empty-market" role="status"><span class="empty-glyph" aria-hidden="true">⌁</span><h2>{{ loading ? '正在连接行情' : '暂无可用行情' }}</h2><p>{{ loading ? '获取价格与后端指标，请稍候。' : '选择品种后刷新，错误原因会显示在上方。' }}</p><button v-if="!loading" @click="load()">重新加载</button></div>
        </div>
      </template>
      <template #assistant>
        <header class="assistant-heading"><div><span class="assistant-glyph" aria-hidden="true">✧</span><h2>研究助手</h2><span class="quiet-badge">AI</span></div><button class="text-button" @click="newConversation" :disabled="busy">＋ 新会话</button></header>
        <div ref="messageList" class="messages" role="region" aria-label="对话消息" tabindex="0" @scroll="onMessageScroll">
          <div v-if="!messages.length" class="chat-welcome"><span class="eyebrow">从一个问题开始</span><h2>看清结构，再做判断。</h2><p>围绕趋势、关键价位或周期差异提问。分析依据可展开查看。</p><div class="suggestions"><button v-for="prompt in suggestions" :key="prompt" @click="usePrompt(prompt)">{{ prompt }}<span aria-hidden="true">↗</span></button></div><p class="welcome-note">行情可能延迟，最新K线完成状态尚未确认。研究结果不构成交易执行指令。</p></div>
          <article v-for="(m, i) in messages" :key="i" class="chat-turn" data-testid="chat-message">
            <div class="user-message"><span class="message-role">你</span><p>{{ m.user }}</p></div>
            <p v-if="m.error" role="alert" class="notice error">{{ m.error }}</p>
            <div v-if="m.result" class="assistant-message">
              <div class="answer-meta"><span class="message-role">研究助手</span><span class="result-status" :class="m.result.status">{{ statuses[m.result.status] }}</span></div>
              <p v-if="m.result.mode === 'mock'" class="mock-banner">模拟演示 · 预设工具脚本，不代表模型自主理解</p>
              <p class="answer-conclusion">{{ m.result.report.conclusion }}</p>
              <div class="answer-context" v-if="m.result.symbol">{{ m.result.symbol }}<span v-if="m.result.primary_timeframe"> · {{ timeframeLabel(m.result.primary_timeframe) }}主图</span><span v-if="m.result.timeframes?.length > 1"> · 多周期研究</span></div>
              <details v-if="m.result.report.sections?.length" class="answer-evidence"><summary>查看分析依据 <span>{{ m.result.report.sections.length }} 项</span></summary><section v-for="(s, n) in m.result.report.sections" :key="n"><h3>{{ s.title }}</h3><p>{{ s.text }}</p></section></details>
              <p v-if="m.linkWarning" class="notice" role="status">{{ m.linkWarning }}</p>
              <p v-for="w in m.result.status !== 'success' ? m.result.warnings : []" :key="w" class="notice">{{ warningLabel(w) }}</p>
              <details class="technical-details">
                <summary>技术详情 <span>{{ m.result.tool_executions.length }} 次工具执行</span></summary>
                <p v-for="w in m.result.warnings" :key="w" class="notice">{{ w }}</p>
                <details v-for="t in m.result.tool_executions" :key="t.call_id" class="tool-record">
                  <summary>{{ toolLabel(t.name) }} <span>{{ statuses[t.status] || t.status }}</span></summary>
                  <dl><dt>工具</dt><dd>{{ t.name }}</dd><dt>执行编号</dt><dd class="technical-id">{{ t.call_id }}</dd><dt v-if="t.arguments?.symbol">品种</dt><dd v-if="t.arguments?.symbol">{{ t.arguments.symbol }}</dd><dt v-if="t.arguments?.interval">周期</dt><dd v-if="t.arguments?.interval">{{ timeframeLabel(t.arguments.interval) }}</dd><dt v-if="t.result?.error">错误</dt><dd v-if="t.result?.error">{{ t.result.error }}</dd></dl>
                </details>
                <details class="evidence-provenance"><summary>证据与数据来源</summary><dl v-for="e in m.result.report.evidence" :key="e.call_id + e.interval"><dt>证据编号</dt><dd class="technical-id">{{ e.call_id }}</dd><dt>品种 / 周期</dt><dd>{{ e.symbol }} · {{ timeframeLabel(e.interval) }}</dd><dt>来源</dt><dd>{{ sourceLabel(e.source) }}</dd><dt>数据时间</dt><dd>{{ displayTime(e.last_updated) }}</dd><dt>数据快照</dt><dd class="technical-id">{{ e.snapshot_id }}</dd></dl></details>
                <details class="session-details"><summary>会话说明</summary><p data-testid="thread-id">会话：{{ m.result.thread_id }}</p><p>刷新页面会开始新会话。后端使用内存上下文，重启后请新建会话。</p></details>
              </details>
            </div>
          </article>
          <div v-if="busy" class="agent-pending" role="status"><span class="pending-dot"></span><p>研究中<span>等待工具与模型完成，结果返回后展示真实执行记录。</span></p></div>
        </div>
        <div class="composer-wrap">
          <button v-if="unread" class="latest-button" @click="scrollToLatest(true)">查看最新消息 ↓</button>
          <form class="composer" @submit.prevent="send">
            <label for="research-message" class="sr-only">研究任务</label>
            <textarea ref="inputEl" id="research-message" v-model="input" rows="2" placeholder="问一个行情问题，或继续追问…" maxlength="4000" :disabled="busy" @keydown="onInputKey"></textarea>
            <div class="composer-footer"><span>{{ busy ? '任务执行中，请稍候' : 'Enter 发送 · Shift + Enter 换行' }}</span><button class="primary send-button" :disabled="busy || !input.trim()" aria-label="发送">{{ busy ? '研究中' : '发送' }} <span aria-hidden="true">↑</span></button></div>
          </form>
          <div class="session-hint">{{ thread ? '当前会话支持连续追问' : '新会话' }} · <details><summary>会话说明</summary><p>刷新页面开始新会话；后端重启后请新建会话。</p></details></div>
        </div>
      </template>
    </ResearchWorkspace>
  </div>
</template>
<script setup>
import { ref, computed, onMounted, nextTick } from 'vue';
import KlineChart from './components/KlineChart.vue';
import ResearchWorkspace from './components/ResearchWorkspace.vue';
import { chartPlan, evidenceMatches } from './api/linkage';
import { displayNumber, displayTime, timeframeLabel, sourceLabel, toolLabel, warningLabel } from './api/presentation';
import { shouldSubmit } from './api/workspace';
const symbols = ref([{symbol:'QQQ', asset:'stock'}, {symbol:'XAU/USD', asset:'gold'}]);
const symbol=ref('QQQ'), interval=ref('1day'), legacy=ref(false), charts=ref([]), chartWarnings=ref([]);
const error=ref(''), loading=ref(false), busy=ref(false), input=ref(''), messages=ref([]), thread=ref(null);
const assistantOpen=ref(true), messageList=ref(null), inputEl=ref(null), unread=ref(false);
let nearBottom=true;
const currentChart=computed(()=>charts.value.find(d=>d.interval===interval.value) || charts.value[0]);
const comparisonCharts=computed(()=>charts.value.filter(d=>d!==currentChart.value));
const suggestions=['分析 QQQ 日线趋势', '比较 QQQ 日线和1小时', '黄金的技术结构与风险'];
const selectionDiffers=computed(()=>charts.value.length && !charts.value.some(d=>d.symbol===symbol.value && d.interval===interval.value));
const statuses={success:'完成',partial:'部分完成',failed:'失败',insufficient_data:'数据不足'};
const base=import.meta.env.VITE_API_BASE || '/api';
const snapshotWarning='报告与图表数据快照不一致，可能已更新或修订，请重新分析。';
let generation=0;
function chartProps(d) { return {legacy:legacy.value,candles:d.candles,ma20:d.indicators[legacy.value?'ma7':'ma20'],ma50:d.indicators[legacy.value?'ma25':'ma50'],ma200:d.indicators[legacy.value?'ma60':'ma200'],rsi:d.indicators.rsi,pdh:d.indicators.pdh,pdl:d.indicators.pdl,support:d.rules.support,resistance:d.rules.resistance,interval:d.interval}; }
function onMessageScroll() { const el=messageList.value; nearBottom=el.scrollHeight-el.scrollTop-el.clientHeight<80; if(nearBottom) unread.value=false; }
async function scrollToLatest(force=false) { await nextTick(); if((force || nearBottom) && messageList.value?.clientHeight) { messageList.value.scrollTop=messageList.value.scrollHeight; unread.value=false; } else unread.value=true; }
async function newConversation() { if(busy.value) return; thread.value=null; messages.value=[]; unread.value=false; nearBottom=true; await nextTick(); inputEl.value?.focus(); }
async function usePrompt(prompt) { input.value=prompt; await nextTick(); inputEl.value?.focus(); }
function onInputKey(event) { if(shouldSubmit(event)) { event.preventDefault(); send(); } }
async function request(path, options={}) {
  const controller=new AbortController();
  const timer=setTimeout(()=>controller.abort(),150000);
  try {
    const response=await fetch(base+path,{...options,signal:controller.signal});
    const data=await response.json().catch(()=>null);
    if(!response.ok) throw Error(data?.error || `请求失败（HTTP ${response.status}）`);
    if(!data) throw Error('接口返回了无效数据');
    return data;
  } catch(e) {
    if(e.name==='AbortError') throw Error('请求超时');
    if(e instanceof TypeError) throw Error('网络连接失败，请检查服务后重试');
    throw e;
  } finally { clearTimeout(timer); }
}
async function load(frames=[interval.value], evidence=[]) {
  const id=++generation;
  loading.value=true; error.value='';
  const selected=symbol.value, asset=symbols.value.find(s=>s.symbol===selected)?.asset;
  try {
    const result=await Promise.all(frames.map(i=>request('/market/kline?'+new URLSearchParams({symbol:selected,asset,interval:i}))));
    if(id!==generation) return;
    if(result.some(d=>!d.candles?.length)) throw Error('没有可用行情');
    if(result.some(d=>d.symbol!==selected || !frames.includes(d.interval))) throw Error('返回的行情与所选品种或周期不一致');
    charts.value=result;
    chartWarnings.value=[...new Set(result.flatMap(d=>d.warnings || []))];
    if(evidence.some(e=>!result.some(d=>evidenceMatches(d,e)))) chartWarnings.value.push('报告与图表数据快照不一致，可能已更新或修订，请重新分析。');
  } catch(e) { if(id===generation) error.value=e.message; }
  finally { if(id===generation) loading.value=false; }
}
async function send() {
  if(busy.value || !input.value.trim()) return;
  busy.value=true;
  const index=messages.value.length;
  messages.value.push({user:input.value,result:null,error:null,linkWarning:''});
  input.value=''; scrollToLatest(true);
  const before=generation;
  try {
    const result=await request('/agent/chat',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({message:messages.value[index].user,thread_id:thread.value})});
    thread.value=result.thread_id;
    messages.value[index].result=result;
    const plan=chartPlan(result);
    if(plan && before===generation) {
      if(!symbols.value.some(s=>s.symbol===plan.symbol)) symbols.value.push({symbol:plan.symbol,asset:plan.asset});
      symbol.value=plan.symbol; interval.value=plan.frames[0];
      await load(plan.frames,plan.evidence);
    } else if(result.status!=='failed') {
      messages.value[index].linkWarning=before!==generation ? '分析期间已手动切换行情，保留当前选择。' : '响应缺少可靠的品种、周期或快照信息，保留当前图表。';
    }
  } catch(e) { messages.value[index].error=e.message; }
  finally { busy.value=false; scrollToLatest(); await nextTick(); if(document.activeElement === document.body) inputEl.value?.focus(); }
}
onMounted(()=>load());
</script>
