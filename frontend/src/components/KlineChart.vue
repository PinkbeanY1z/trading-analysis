<template>
  <div class="kline-root">
    <div class="kline-legend">
      <span class="legend-item ma20">{{ legacy ? "MA7" : "MA20" }}({{ maText.ma20 }})</span>
      <span class="legend-item ma50">{{ legacy ? "MA25" : "MA50" }}({{ maText.ma50 }})</span>
      <span class="legend-item ma200">{{ legacy ? "MA60" : "MA200" }}({{ maText.ma200 }})</span>
    </div>
    <div class="kline-info">
      <span>时间：{{ info.time }}</span>
      <span>开：{{ info.open }}</span>
      <span>高：{{ info.high }}</span>
      <span>低：{{ info.low }}</span>
      <span>收：{{ info.close }}</span>
    </div>
    <div ref="priceEl" class="kline-price" aria-label="K线主图"></div>
    <div ref="rsiEl" class="kline-rsi" aria-label="RSI14 副图"></div>
    <div ref="volumeEl" class="kline-volume" aria-label="成交量副图"></div>
  </div>
</template>

<script setup>
import { onMounted, onBeforeUnmount, watch, ref } from "vue";
import { createChart } from "lightweight-charts";

const props = defineProps({
  legacy: Boolean,
  rsi: { type: Array, default: () => [] },
  candles: { type: Array, default: () => [] },
  ma20: { type: Array, default: () => [] },
  ma50: { type: Array, default: () => [] },
  ma200: { type: Array, default: () => [] },
  support: { type: Number, default: null },
  resistance: { type: Number, default: null },
  pdh: { type: Number, default: null },
  pdl: { type: Number, default: null },
  interval: { type: String, default: "1h" }
});

const rsiEl = ref(null);
let rsiChart, rsiSeries;
const priceEl = ref(null);
const volumeEl = ref(null);
let priceChart;
let volumeChart;
let candleSeries;
let ma20Series;
let ma50Series;
let ma200Series;
let volumeSeries;
let syncing = false;
let supportLine, resistanceLine;
const maText = ref({ ma20: "-", ma50: "-", ma200: "-" });
const info = ref({ time: "-", open: "-", high: "-", low: "-", close: "-" });

function toSeriesData(candles) {
  return candles.filter(c => Number.isFinite(Date.parse(c.t)) && [c.o,c.h,c.l,c.c].every(v=>Number.isFinite(v) && v>0)).map((c) => {
    // Fix for 1D chart: TradingView expects business days or timestamps.
    // If backend returns ISO string, ensure we parse it correctly.
    // For 1D, we should align to UTC midnight to avoid "1 week gap" visual issues if data is sparse or tz issue.
    // TwelveData returns YYYY-MM-DD for 1day.
    const time = new Date(c.t).getTime() / 1000; 
    return {
      time: time,
      open: c.o,
      high: c.h,
      low: c.l,
      close: c.c
    };
  });
}

function toVolumeData(candles) {
  return candles.map((c) => ({
    time: Math.floor(new Date(c.t).getTime() / 1000),
    ...(Number.isFinite(c.v) ? {value:c.v,
    color: c.c >= c.o ? "rgba(239, 68, 68, 0.5)" : "rgba(34, 197, 94, 0.5)"} : {})
  })).filter(p=>Number.isFinite(p.time));
}

function toLineData(candles, maValues) {
  return candles
    .map((c, idx) => ({
      time: Math.floor(new Date(c.t).getTime() / 1000),
      ...(Number.isFinite(maValues[idx]) ? {value:maValues[idx]} : {})
    }))
    .filter((p) => Number.isFinite(p.time));
}

function render() {
  if (!priceEl.value || !volumeEl.value) return;
  priceChart = createChart(priceEl.value, {
    autoSize: true,
    layout: {
      background: { color: "#131722" },
      textColor: "#e5e7eb"
    },
    grid: {
      vertLines: { color: "#1f2430" },
      horzLines: { color: "#1f2430" }
    },
    timeScale: { borderColor: "#2a2f3a" },
    rightPriceScale: { borderColor: "#2a2f3a" }
  });

  rsiChart=createChart(rsiEl.value,{autoSize:true,layout:{background:{color:"#131722"},textColor:"#ddd"}});
  rsiSeries=rsiChart.addLineSeries({color:"#c084fc",title:"RSI14"});
  candleSeries = priceChart.addCandlestickSeries({
    upColor: "#ef4444",
    downColor: "#22c55e",
    borderUpColor: "#dc2626",
    borderDownColor: "#16a34a",
    wickUpColor: "#dc2626",
    wickDownColor: "#16a34a"
  });

  ma20Series = priceChart.addLineSeries({
    color: "#2563eb",
    lineWidth: 1,
    lastValueVisible: false,
    priceLineVisible: false
  });
  ma50Series = priceChart.addLineSeries({
    color: "#7c3aed",
    lineWidth: 1,
    lastValueVisible: false,
    priceLineVisible: false
  });
  ma200Series = priceChart.addLineSeries({
    color: "#0ea5e9",
    lineWidth: 1,
    lastValueVisible: false,
    priceLineVisible: false
  });

  volumeChart = createChart(volumeEl.value, {
    autoSize: true,
    layout: {
      background: { color: "#131722" },
      textColor: "#e5e7eb"
    },
    grid: {
      vertLines: { color: "#1f2430" },
      horzLines: { color: "#1f2430" }
    },
    timeScale: { borderColor: "#2a2f3a" },
    rightPriceScale: { borderColor: "#2a2f3a" }
  });

  volumeSeries = volumeChart.addHistogramSeries({
    priceFormat: { type: "volume" },
    color: "rgba(34, 197, 94, 0.5)"
  });

  const syncRange = (source) => (range) => {
    if (syncing || !range) return;
    syncing = true;
    try {
      if(source !== priceChart) priceChart.timeScale().setVisibleRange(range);
      if(source !== rsiChart && props.rsi.some(Number.isFinite)) rsiChart.timeScale().setVisibleRange(range);
      if(source !== volumeChart && props.candles.some(c=>Number.isFinite(c.v))) volumeChart.timeScale().setVisibleRange(range);
    } finally { syncing=false; }
  };
  priceChart.timeScale().subscribeVisibleTimeRangeChange(syncRange(priceChart));
  rsiChart.timeScale().subscribeVisibleTimeRangeChange(syncRange(rsiChart));
  volumeChart.timeScale().subscribeVisibleTimeRangeChange(range=>{
    if(props.candles.some(c=>Number.isFinite(c.v))) syncRange(volumeChart)(range);
  });

  priceChart.subscribeCrosshairMove((param) => {
    const bar = param?.seriesData?.get(candleSeries);
    if (!bar || !param.time) return;
      const dateObj = new Date(param.time * 1000);
    // Display exchange session dates on daily charts, UTC+8 intraday.
    const options = {
       timeZone: props.interval === "1day" ? "UTC" : "Asia/Shanghai",
       year: "numeric",
       month: "2-digit",
       day: "2-digit",
       hour12: false
    };
    if (props.interval !== "1day") {
       options.hour = "2-digit";
    }
    const timeStr = new Intl.DateTimeFormat("zh-CN", options).format(dateObj);

    info.value = {
      time: timeStr,
      open: bar.open?.toFixed(2) ?? "-",
      high: bar.high?.toFixed(2) ?? "-",
      low: bar.low?.toFixed(2) ?? "-",
      close: bar.close?.toFixed(2) ?? "-"
    };
  });
}

function updateSeries() {
  if (!priceChart || !volumeChart) return;
  syncing=true;
  candleSeries.setData(toSeriesData(props.candles));
  volumeSeries.setData(toVolumeData(props.candles));
  ma20Series.setData(toLineData(props.candles, props.ma20));
  ma50Series.setData(toLineData(props.candles, props.ma50));
  ma200Series.setData(toLineData(props.candles, props.ma200));
  updateKeyLevels();
  rsiSeries.setData(toLineData(props.candles, props.rsi));
  syncing=false;
  
  // Set Visible Range based on Interval
  // For 1h: Show last ~3 days (approx 72 hours). 
  // For 1day: Show last ~30-60 days.
  const total = props.candles.length;
  if (total > 0) {
     let visibleBars = 50; 
     if (props.interval === "1h") visibleBars = 60; // ~2.5 days
     if (props.interval === "1day") visibleBars = 60; // 2 months
     
     const from = Math.floor(Date.parse(props.candles[Math.max(0,total-visibleBars)].t)/1000);
     const to = Math.floor(Date.parse(props.candles[total-1].t)/1000);
     if(Number.isFinite(from) && Number.isFinite(to) && from<to) {
       priceChart.timeScale().setVisibleRange({from,to});
       if(props.rsi.some(Number.isFinite)) rsiChart.timeScale().setVisibleRange({from,to});
       if(props.candles.some(c=>Number.isFinite(c.v))) volumeChart.timeScale().setVisibleRange({from,to});
     }
  } else {
     priceChart.timeScale().fitContent();
  }

  const last = (arr) => {
    for (let i = arr.length - 1; i >= 0; i -= 1) {
      if (Number.isFinite(arr[i])) return arr[i];
    }
    return null;
  };
  const ma20 = last(props.ma20);
  const ma50 = last(props.ma50);
  const ma200 = last(props.ma200);
  maText.value = {
    ma20: ma20 === null ? "-" : Number(ma20).toFixed(2),
    ma50: ma50 === null ? "-" : Number(ma50).toFixed(2),
    ma200: ma200 === null ? "-" : Number(ma200).toFixed(2)
  };

  const lastCandle = props.candles[props.candles.length - 1];
  if (lastCandle) {
    const dateObj = new Date(lastCandle.t);
    const timeStr = new Intl.DateTimeFormat("zh-CN", {
      timeZone: props.interval === "1day" ? "UTC" : "Asia/Shanghai",
      year: "numeric",
      month: "2-digit",
      day: "2-digit",
      hour: "2-digit",
      hour12: false
    }).format(dateObj);

    info.value = {
      time: timeStr,
      open: Number(lastCandle.o).toFixed(2),
      high: Number(lastCandle.h).toFixed(2),
      low: Number(lastCandle.l).toFixed(2),
      close: Number(lastCandle.c).toFixed(2)
    };

  }
}

let pdhLine = null;
let pdlLine = null;

function updateKeyLevels() {
  if (!candleSeries) return;
  
  // Clean up
  if (pdhLine) {
     candleSeries.removePriceLine(pdhLine);
     pdhLine = null;
  }
  if (pdlLine) {
     candleSeries.removePriceLine(pdlLine);
     pdlLine = null;
  }
  
  if(supportLine) candleSeries.removePriceLine(supportLine);
  if(resistanceLine) candleSeries.removePriceLine(resistanceLine);
  supportLine=Number.isFinite(props.support) ? candleSeries.createPriceLine({price:props.support,color:"#26a69a",lineWidth:1,lineStyle:2,title:"支撑"}) : null;
  resistanceLine=Number.isFinite(props.resistance) ? candleSeries.createPriceLine({price:props.resistance,color:"#ef5350",lineWidth:1,lineStyle:2,title:"阻力"}) : null;
  // Draw
  if (Number.isFinite(props.pdh)) {
    pdhLine = candleSeries.createPriceLine({
      price: props.pdh,
      color: '#fbbf24', // Amber-400
      lineWidth: 2,
      lineStyle: 2, // Dashed
      axisLabelVisible: true,
      title: 'PDH',
    });
  }
  
  if (Number.isFinite(props.pdl)) {
    pdlLine = candleSeries.createPriceLine({
      price: props.pdl,
      color: '#f87171', // Red-400
      lineWidth: 2,
      lineStyle: 2, // Dashed
      axisLabelVisible: true,
      title: 'PDL',
    });
  }
}

onMounted(() => {
  render();
  updateSeries();
});

onBeforeUnmount(() => {
  if (rsiChart) rsiChart.remove();
  if (priceChart) priceChart.remove();
  if (volumeChart) volumeChart.remove();
});

watch(() => props.candles, updateSeries);
watch(() => props.ma20, updateSeries);
watch(() => props.ma50, updateSeries);
watch(() => props.ma200, updateSeries);
watch(() => props.pdh, updateSeries);
watch(() => props.pdl, updateSeries);
watch(() => props.rsi, updateSeries);
watch(() => props.support, updateKeyLevels);
watch(() => props.resistance, updateKeyLevels);
</script>

<style scoped>
.kline-root {
  width: 100%;
  height: auto;
  display: grid;
  grid-template-rows: auto auto 280px 110px 85px;
  grid-template-columns: minmax(0, 1fr);
  gap: 8px;
  position: relative;
}

.kline-price,
.kline-volume {
  width: 100%;
  height: 100%;
}
.kline-root > div { min-width: 0; max-width: 100%; }

.kline-legend {
  position: static;
  z-index: 2;
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
  font-size: 12px;
  background: rgba(19, 23, 34, 0.9);
  border: 1px solid #2a2f3a;
  border-radius: 8px;
  padding: 4px 8px;
}

.kline-info {
  position: static;
  z-index: 2;
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  font-size: 12px;
  background: rgba(19, 23, 34, 0.95);
  border: 1px solid #2a2f3a;
  border-radius: 8px;
  padding: 4px 8px;
  color: #e5e7eb;
}

.legend-item {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  color: #e5e7eb;
}

.legend-item::before {
  content: "";
  width: 10px;
  height: 2px;
  background: #94a3b8;
  display: inline-block;
}

.legend-item.ma20::before {
  background: #2563eb;
}
.legend-item.ma50::before {
  background: #7c3aed;
}
.legend-item.ma200::before {
  background: #0ea5e9;
}
</style>
