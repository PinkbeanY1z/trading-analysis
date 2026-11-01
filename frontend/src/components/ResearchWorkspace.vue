<template>
  <div ref="root" class="workspace-shell" :class="{ 'assistant-hidden': !assistantOpen, resizing: dragging }" :style="{ '--assistant-width': panelWidth + 'px' }">
    <div class="mobile-tabs" role="tablist" aria-label="工作区">
      <button ref="marketTab" id="market-tab" role="tab" :tabindex="activeTab === 'market' ? 0 : -1" :aria-selected="activeTab === 'market'" aria-controls="market-pane" @keydown="tabKey" @click="activeTab = 'market'">行情图表</button>
      <button ref="analysisTab" id="analysis-tab" role="tab" :tabindex="activeTab === 'analysis' ? 0 : -1" :aria-selected="activeTab === 'analysis'" aria-controls="assistant-pane" @keydown="tabKey" @click="activeTab = 'analysis'; $emit('update:assistantOpen', true)">研究对话 <span v-if="busy" class="tab-status">执行中</span><span v-else-if="unread" class="tab-status">新结果</span></button>
    </div>
    <div class="workspace" :data-active-tab="activeTab">
      <section id="market-pane" class="market-pane" :role="width < 1024 ? 'tabpanel' : 'region'" :aria-labelledby="width < 1024 ? 'market-tab' : undefined" aria-label="行情图表" tabindex="0"><slot name="market" /></section>
      <div class="panel-divider" role="separator" tabindex="0" aria-label="调整图表与对话宽度" aria-orientation="vertical" aria-controls="market-pane assistant-pane" :aria-valuemin="bounds.min" :aria-valuemax="bounds.max" :aria-valuenow="panelWidth" :aria-valuetext="'对话面板宽度 ' + panelWidth + ' 像素'" @pointerdown="startDrag" @pointermove="moveDrag" @pointerup="stopDrag" @pointercancel="stopDrag" @lostpointercapture="dragging = false" @keydown="resizeKey" @dblclick="panelWidth = clampPanelWidth(420, width)"><span></span></div>
      <aside id="assistant-pane" class="assistant-pane" :role="width < 1024 ? 'tabpanel' : 'complementary'" :aria-labelledby="width < 1024 ? 'analysis-tab' : undefined" aria-label="Agent 研究对话"><slot name="assistant" :active="activeTab === 'analysis'" /></aside>
    </div>
  </div>
</template>

<script setup>
import { computed, onBeforeUnmount, onMounted, ref } from 'vue';
import { clampPanelWidth, keyboardPanelWidth, panelBounds } from '../api/workspace';
defineProps({ assistantOpen: Boolean, busy: Boolean, unread: Boolean });
const emit = defineEmits(['update:assistantOpen']);
const root = ref(null), width = ref(1440), panelWidth = ref(420), dragging = ref(false), activeTab = ref('market');
const marketTab = ref(null), analysisTab = ref(null);
const bounds = computed(() => panelBounds(width.value));
let observer;
function tabKey(event) {
  if (!['ArrowLeft','ArrowRight','Home','End'].includes(event.key)) return;
  event.preventDefault();
  activeTab.value = event.key === 'Home' ? 'market' : event.key === 'End' ? 'analysis' : activeTab.value === 'market' ? 'analysis' : 'market';
  if (activeTab.value === 'analysis') emit('update:assistantOpen', true);
  (activeTab.value === 'market' ? marketTab.value : analysisTab.value)?.focus();
}
function startDrag(event) {
  if (event.button !== 0) return;
  event.preventDefault();
  dragging.value = true;
  event.currentTarget.setPointerCapture(event.pointerId);
}
function moveDrag(event) {
  if (!dragging.value) return;
  panelWidth.value = clampPanelWidth(root.value.getBoundingClientRect().right - event.clientX, width.value);
}
function stopDrag(event) {
  dragging.value = false;
  if (event.currentTarget.hasPointerCapture(event.pointerId)) event.currentTarget.releasePointerCapture(event.pointerId);
}
function resizeKey(event) {
  const value = keyboardPanelWidth(event.key, panelWidth.value, width.value);
  if (value === null) return;
  event.preventDefault(); panelWidth.value = value;
}
onMounted(() => {
  observer = new ResizeObserver(([entry]) => {
    width.value = entry.contentRect.width;
    if (width.value >= 1024) panelWidth.value = clampPanelWidth(panelWidth.value, width.value);
  });
  observer.observe(root.value);
});
onBeforeUnmount(() => observer?.disconnect());
</script>
