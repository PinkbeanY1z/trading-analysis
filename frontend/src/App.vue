<template>
  <div class="app">
    <header class="topbar">
      <div class="brand">Trading-Analysis｜行情结构分析智能体</div>
      <div class="controls">
        <label>品种<select aria-label="品种" v-model="symbol" @change="load()"><option v-for="s in symbols" :key="s.symbol" :value="s.symbol">{{ s.symbol }}</option></select></label>
        <label>主图周期<select aria-label="主图周期" v-model="interval" @change="load()"><option value="1day">日线</option><option value="4h">4小时</option><option value="1h">1小时</option></select></label>
        <button @click="load()" :disabled="loading">{{ loading ? '加载中' : '刷新行情' }}</button>
        <label class="legacy-toggle"><input type="checkbox" v-model="legacy">MA7/25/60</label>
      </div>
    </header>
    <p role="alert" class="notice error" v-if="error">{{ error }}。{{ charts.length ? '已保留上次成功图表，请以图表标题为准。' : '当前没有可用图表。' }}</p>
    <p class="notice" v-if="selectionDiffers">正在选择 {{ symbol }} / {{ interval }}，下方仍是上次成功数据。</p>
    <main class="main"><section class="main-grid">
      <section class="chart-wrap" aria-label="行情图表">
        <div class="chart-grid">
          <article v-for="d in charts" :key="d.symbol + d.interval" class="chart-area" :data-testid="'chart-' + d.interval">
            <h2>{{ d.symbol }} · {{ d.interval }} {{ d.interval === interval ? '主图' : '比较图' }} · {{ d.source === 'mock' ? '模拟行情' : d.source === 'twelve_data' ? '真实行情 · Twelve Data' : d.source }}</h2>
            <small>来源 {{ d.source }} · 数据时间 {{ d.last_updated }}<br>获取时间 {{ d.fetched_at }}</small>
            <p v-if="d.indicators.insufficient_history" role="status">历史不足，长期均线不可用，结构尚未确认。</p>
            <KlineChart :legacy="legacy" :candles="d.candles" :ma20="d.indicators[legacy ? 'ma7' : 'ma20']" :ma50="d.indicators[legacy ? 'ma25' : 'ma50']" :ma200="d.indicators[legacy ? 'ma60' : 'ma200']" :rsi="d.indicators.rsi" :pdh="d.indicators.pdh" :pdl="d.indicators.pdl" :support="d.rules.support" :resistance="d.rules.resistance" :interval="d.interval" />
            <p class="levels">支撑 {{ format(d.rules.support) }} · 阻力 {{ format(d.rules.resistance) }} · PDH {{ format(d.indicators.pdh) }} · PDL {{ format(d.indicators.pdl) }}</p>
            <p>最新 RSI14 {{ format(d.indicators.latest.rsi) }} · {{ d.assetClass === 'gold' || !d.indicators.latest.volumeAvailable ? '成交量不可用' : '最新成交量 ' + format(d.indicators.latest.volume) }}</p>
          </article>
        </div>
        <p v-if="!charts.length">{{ loading ? '正在获取行情…' : '暂无行情' }}</p>
        <p v-for="w in chartWarnings" :key="w" class="notice">{{ w }}</p>
      </section>
      <aside class="summary" aria-label="Agent 研究对话">
        <h2>Agent 研究对话</h2>
        <small>工具记录在任务结束后显示。刷新页面将开始新会话；后端重启后请点击新建会话。</small>
        <small v-if="thread" data-testid="thread-id">会话：{{ thread }}</small>
        <button @click="thread = null; messages = []" :disabled="busy">新建会话</button>
        <article v-for="(m, i) in messages" :key="i" class="sidebar-card" data-testid="chat-message">
          <p class="user-message">{{ m.user }}</p>
          <p v-if="m.error" role="alert" class="error">{{ m.error }}</p>
          <template v-if="m.result">
            <strong>{{ statuses[m.result.status] }} · {{ m.result.mode === 'mock' ? 'Mock脚本（不具备自然语言理解）' : '模型分析' }}</strong>
            <p>{{ m.result.report.conclusion }}</p>
            <section v-for="(s, n) in m.result.report.sections" :key="n"><h3>{{ s.title }}</h3><p>{{ s.text }}</p></section>
            <p v-for="w in m.result.warnings" :key="w" class="notice">{{ w }}</p>
            <p v-if="m.linkWarning" role="status">{{ m.linkWarning }}</p>
            <details v-for="t in m.result.tool_executions" :key="t.call_id"><summary>{{ t.name }} · {{ t.status }}</summary><pre>{{ JSON.stringify(t, null, 2) }}</pre></details>
          </template>
        </article>
        <p v-if="busy" role="status">任务执行中，等待后端返回真实结果…</p>
        <form @submit.prevent="send">
          <label for="research-message">研究任务</label>
          <textarea id="research-message" v-model="input" placeholder="分析 QQQ 的日线趋势" maxlength="4000" :disabled="busy"></textarea>
          <button :disabled="busy || !input.trim()">发送</button>
        </form>
      </aside>
    </section></main>
  </div>
</template>
<script setup>
import { ref, computed, onMounted } from 'vue';
import KlineChart from './components/KlineChart.vue';
import { chartPlan, evidenceMatches } from './api/linkage';
const symbols = ref([{symbol:'QQQ', asset:'stock'}, {symbol:'XAU/USD', asset:'gold'}]);
const symbol=ref('QQQ'), interval=ref('1day'), legacy=ref(false), charts=ref([]), chartWarnings=ref([]);
const error=ref(''), loading=ref(false), busy=ref(false), input=ref(''), messages=ref([]), thread=ref(null);
const selectionDiffers=computed(()=>charts.value.length && !charts.value.some(d=>d.symbol===symbol.value && d.interval===interval.value));
const statuses={success:'成功',partial:'部分完成',failed:'失败',insufficient_data:'数据不足'};
const base=import.meta.env.VITE_API_BASE || '/api';
let generation=0;
const format=v=>Number.isFinite(v) ? Number(v).toFixed(2) : '不可用';
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
  input.value='';
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
  finally { busy.value=false; }
}
onMounted(()=>load());
</script>
<style>
body{overflow:auto!important;height:auto}.app{height:auto;min-height:100vh}
.topbar{height:auto;min-height:68px;gap:16px;flex-wrap:wrap;padding:16px 24px}.brand{font-size:17px;overflow-wrap:anywhere}
.controls{display:flex;flex-wrap:wrap;align-items:end;gap:10px}.controls label{display:grid;gap:4px;font-size:12px}.controls .legacy-toggle{display:flex;align-items:center;align-self:center}
select,button{min-width:0}button:disabled{cursor:wait;opacity:.6}button{cursor:pointer}.main{overflow:visible}
.main-grid{grid-template-columns:minmax(0,7fr) minmax(300px,3fr);align-items:start;height:auto}.chart-wrap,.summary,.chart-area{min-width:0;height:auto}
.chart-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,330px),1fr));gap:16px}.chart-area{display:flex;flex-direction:column;background:#131722;padding:10px;border:1px solid var(--border);border-radius:8px;overflow:hidden}
h2{font-size:16px;margin:8px 0}h3{font-size:14px;margin:14px 0 6px}.chart-area small,.levels{overflow-wrap:anywhere}
.summary{max-height:none;gap:12px}.sidebar-card{padding:12px;background:var(--panel);border:1px solid var(--border);border-radius:8px;min-width:0;overflow-wrap:anywhere}.user-message{font-weight:600;color:#93c5fd}
textarea{width:100%;max-width:100%;min-height:100px;resize:vertical;background:#202534;color:#eee;border:1px solid var(--border);padding:10px;font:inherit;border-radius:8px}form{display:grid;gap:8px}form button{justify-self:end}
pre{white-space:pre-wrap;overflow-wrap:anywhere;font-size:11px}small{color:#9aa6bc;overflow-wrap:anywhere}.notice{font-size:13px;color:#ffcf80;overflow-wrap:anywhere}.error{color:#fca5a5}.app> .notice{margin:12px 24px}
@media(max-width:1000px){.main-grid{grid-template-columns:minmax(0,1fr)}.main{padding:12px}.chart-wrap{padding:10px}.topbar{padding:12px}.controls{width:100%}.controls>label:not(.legacy-toggle){flex:1;min-width:100px}.controls select{width:100%}}
.app .topbar{height:auto;min-height:68px;padding:16px 24px;flex-shrink:0}
.app .controls{display:flex;flex-wrap:wrap;align-items:end}
.app .main{overflow:visible}.app .chart-wrap,.app .summary{height:auto}
@media(max-width:1000px){.app .topbar{padding:12px}.app .main{padding:12px}.app .chart-wrap{padding:10px}.app .main-grid{height:auto;grid-template-columns:minmax(0,1fr)}}
</style>
