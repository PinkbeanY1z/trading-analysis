<template>
  <div class="app">
    <header class="topbar"><div class="brand">Trading-Analysis｜行情结构分析智能体</div>
      <div class="controls"><select v-model="symbol" @change="load()"><option>QQQ</option><option>XAU/USD</option></select>
      <select v-model="interval" @change="load()"><option>1day</option><option>1h</option><option>4h</option></select>
      <button @click="load()" :disabled="loading">{{ loading ? '加载中' : '刷新行情' }}</button><label><input type="checkbox" v-model="legacy">显示 MA7/25/60</label></div></header>
    <p role="alert" v-if="error">{{ error }}（保留上次成功图表，请核对图表标题）</p>
    <main class="main"><section class="main-grid"><section class="chart-wrap">
      <div class="chart-grid"><article v-for="d in charts" :key="d.interval" class="chart-area">
        <div>{{ d.symbol }} · {{ d.interval }} {{ d.interval===interval?'主图':'' }} · {{ d.source==='mock'?'模拟行情':d.source }}</div>
        <small>数据时间 {{ d.last_updated }} · 获取时间 {{ d.fetched_at }}</small>
        <p v-if="d.assetClass==='gold'">成交量不可用</p>
        <KlineChart :legacy="legacy" :candles="d.candles" :ma20="d.indicators[legacy?'ma7':'ma20']" :ma50="d.indicators[legacy?'ma25':'ma50']" :ma200="d.indicators[legacy?'ma60':'ma200']" :rsi="d.indicators.rsi" :pdh="d.indicators.pdh" :pdl="d.indicators.pdl" :interval="d.interval" />
        <small>{{ legacy?'当前线条对应 MA7 / MA25 / MA60':'MA20 / MA50 / MA200' }}</small>
      </article></div><p v-if="!charts.length">暂无行情</p><p v-for="w in chartWarnings">{{w}}</p>
    </section><aside class="summary"><h3>Agent 研究对话</h3><small>刷新页面将新建会话；服务重启后请新建会话。工具记录在任务结束后展示。</small>
      <button @click="thread=null;messages=[]" :disabled="busy">新建会话</button>
      <article v-for="(m,i) in messages" :key="i" class="sidebar-card"><p>{{m.user}}</p>
        <p v-if="m.error" role="alert">{{m.error}}</p>
        <template v-if="m.result"><strong>{{statuses[m.result.status]}} · {{m.result.mode==='mock'?'Mock脚本演示（不具备自然语言理解）':'模型分析'}}</strong>
        <p>{{m.result.report.conclusion}}</p><section v-for="s in m.result.report.sections"><h4>{{s.title}}</h4><p>{{s.text}}</p></section>
        <p v-for="w in m.result.warnings">{{w}}</p>
        <details v-for="t in m.result.tool_executions"><summary>{{t.name}} · {{t.status}}</summary><pre>{{JSON.stringify(t,null,2)}}</pre></details>
        </template></article>
      <p v-if="busy">任务执行中，等待后端返回真实结果…</p>
      <form @submit.prevent="send"><textarea v-model="input" placeholder="分析 QQQ 的日线趋势" maxlength="4000" :disabled="busy"></textarea><button :disabled="busy || !input.trim()">发送</button></form>
    </aside></section></main>
  </div>
</template>
<script setup>
import {ref,onMounted} from 'vue';
import KlineChart from './components/KlineChart.vue';
const symbol=ref('QQQ'),interval=ref('1day'),legacy=ref(false),charts=ref([]),chartWarnings=ref([]),error=ref(''),loading=ref(false),busy=ref(false),input=ref(''),messages=ref([]),thread=ref(null);
const statuses={success:'成功',partial:'部分完成',failed:'失败',insufficient_data:'数据不足'};
const base=import.meta.env.VITE_API_BASE || '/api'; let generation=0;
async function request(path,options={}) {const controller=new AbortController(); const timer=setTimeout(()=>controller.abort(),150000);try{const r=await fetch(base+path,{...options,signal:controller.signal});const d=await r.json();if(!r.ok)throw Error(d.error||'请求失败');return d;}finally{clearTimeout(timer)}}
async function load(frames=[interval.value],evidence=[]) {const id=++generation;loading.value=true;error.value='';const selected=symbol.value;try{const result=await Promise.all(frames.map(i=>request('/market/kline?'+new URLSearchParams({symbol:selected,asset:selected==='XAU/USD'?'gold':'stock',interval:i}))));if(id!==generation)return;
if(result.some(d=>!d.candles?.length))throw Error('没有可用行情');charts.value=result;chartWarnings.value=[...new Set(result.flatMap(d=>d.warnings))];for(const e of evidence){const d=result.find(d=>d.interval===e.interval);if(d && (d.last_updated!==e.last_updated||d.source!==e.source))chartWarnings.value.push('报告与图表数据时间或来源不同，请重新分析。');}
}catch(e){if(id===generation)error.value=e.name==='AbortError'?'行情请求超时':e.message;}finally{if(id===generation)loading.value=false}}
async function send(){if(busy.value||!input.value.trim())return;busy.value=true;const m={user:input.value,result:null,error:null};messages.value.push(m);const index=messages.value.length-1;input.value='';const before=generation;
try{const r=await request('/agent/chat',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({message:m.user,thread_id:thread.value})});thread.value=r.thread_id;messages.value[index].result=r;
const e=r.report.evidence||[];if(before===generation&&r.status!=='failed'&&e.length&&r.symbol&&e.every(x=>x.symbol===r.symbol)&&['QQQ','XAU/USD'].includes(r.symbol)&&r.timeframes.every(x=>['1h','4h','1day'].includes(x))){symbol.value=r.symbol;interval.value=r.timeframes[0];await load(r.timeframes,e);}
}catch(e){messages.value[index].error=e.message;}finally{busy.value=false}}
onMounted(()=>load());
</script>
<style>
body{overflow:auto!important}.app{height:auto!important;min-height:100vh}.main-grid{align-items:start}.chart-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(310px,1fr));gap:16px}.chart-area{height:650px;display:flex;flex-direction:column;background:#131722;padding:12px;min-width:0}.summary{max-height:90vh;overflow:auto}textarea{width:100%;min-height:90px;background:#202534;color:#eee}pre{white-space:pre-wrap;overflow-wrap:anywhere;font-size:11px}small{color:#9aa6bc}button{cursor:pointer}article{margin-bottom:12px}@media(max-width:1000px){.main-grid{display:block}.summary{max-height:none}}
</style>
