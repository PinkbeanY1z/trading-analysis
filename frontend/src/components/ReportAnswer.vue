<template>
  <div class="research-answer" data-testid="research-answer">
    <template v-if="blocks.length">
      <p v-if="report.answer_origin === 'deterministic_fallback'" class="legacy-note">模型回答未通过完整校验，以下仅展示服务器核对后的限制说明。</p>
      <section v-for="block in summaryBlocks" :key="block.id" class="answer-block" :class="block.kind">
        <h3 v-if="block.kind !== 'direct_answer' && block.title">{{ block.title }}</h3>
        <p>{{ block.text }}</p>
        <dl v-if="block.facts?.length" class="fact-list">
          <template v-for="(fact, index) in block.facts" :key="index"><dt>{{ fact.label }}</dt><dd>{{ displayMetric(fact.value, fact.format, fact.unit) }}</dd></template>
        </dl>
      </section>
      <p v-if="needsWinRateLabel && !summaryMentionsWinRate" class="unverified">实际历史胜率：未验证。没有匹配策略的可靠回测，不能据此估计胜率。</p>
      <button v-if="report.trade_plan" class="edit-plan" @click="$emit('edit-plan', report.trade_plan)">调整假设价位与成本 ↗</button>
      <details v-if="detailBlocks.length" class="supporting-evidence">
        <summary>查看依据 <span>{{ detailBlocks.length }} 项</span></summary>
        <section v-for="block in detailBlocks" :key="block.id" class="answer-block detail-block">
          <h3>{{ block.title }}</h3><p>{{ block.text }}</p>
          <dl v-if="block.facts?.length" class="fact-list"><template v-for="(fact, index) in block.facts" :key="index"><dt>{{ fact.label }}</dt><dd>{{ displayMetric(fact.value, fact.format, fact.unit) }}</dd></template></dl>
        </section>
      </details>
    </template>
    <template v-else>
      <p class="legacy-conclusion">{{ legacyReportSummary(report) }}</p>
      <p v-if="report.protocol_version !== 2" class="legacy-note">这条回复使用旧报告协议。固定指标章节已收起，重新提问可获取问题优先回答。</p>
    </template>
  </div>
</template>

<script setup>
import { computed } from 'vue';
import { displayMetric, legacyReportSummary, reportBlocks } from '../api/presentation';
const props = defineProps({ report: { type: Object, required: true } });
defineEmits(['edit-plan']);
const blocks = computed(() => reportBlocks(props.report));
const summaryBlocks = computed(() => {
  let watches = 0;
  return blocks.value.filter(block => !block.detail && (block.kind !== 'watch' || ++watches <= 3));
});
const detailBlocks = computed(() => blocks.value.filter(block => block.detail));
const needsWinRateLabel = computed(() => props.report.intent?.questions?.some(question => ['buy_decision', 'entry', 'win_rate', 'winrate'].includes(question.kind)));
const summaryMentionsWinRate = computed(() => summaryBlocks.value.some(block => block.text.includes('胜率') && block.text.includes('未验证')));
</script>

<style scoped>
.research-answer { padding: 6px 0 10px; }
.answer-block + .answer-block { margin-top: 14px; }
.answer-block h3 { margin-bottom: 4px; color: var(--muted); font-size: 11px; font-weight: 500; }
.answer-block p { font-size: 13px; line-height: 1.85; white-space: pre-wrap; overflow-wrap: anywhere; }
.answer-block.direct_answer p { font-size: 14px; color: var(--text); }
.answer-block.limitation p, .unverified { color: var(--muted); font-size: 12px; line-height: 1.8; }
.fact-list { margin: 10px 0 0; grid-template-columns: minmax(80px,1fr) auto; padding: 8px 0; border-top: 1px solid var(--border); gap: 6px 16px; }
.fact-list dd { font-family: var(--font-number); font-variant-numeric: tabular-nums; text-align: right; }
.unverified { margin-top: 12px; }
.supporting-evidence { margin-top: 14px; border-top: 1px solid var(--border); }
.detail-block { padding: 8px 0; }
.detail-block p { color: var(--muted); font-size: 12px; }
.edit-plan { margin-top: 10px; color: var(--primary-hover); font-size: 12px; padding-left: 0; }
.legacy-conclusion { margin: 4px 0 8px; font-size: 15px; font-weight: 500; }
.legacy-note { color: var(--muted); font-size: 11px; line-height: 1.7; }
</style>
