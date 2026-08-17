<template>
  <div v-if="visibleEvents.length || active" class="execution-timeline">
    <button class="timeline-header" type="button" @click="expanded = !expanded">
      <span class="timeline-pulse" :class="{ active }" />
      <span class="timeline-title">{{ latestSummary }}</span>
      <span class="timeline-toggle">{{ expanded ? '收起' : '详情' }}</span>
    </button>
    <div v-if="expanded" class="timeline-list">
      <div
        v-for="(event, index) in visibleEvents"
        :key="event.event_id"
        class="timeline-row"
        :class="[event.status, { child: Boolean(event.task_id || event.parent_run_id) }]"
      >
        <span class="timeline-icon">{{ icon(event) }}</span>
        <div class="timeline-copy">
          <div class="timeline-summary">{{ event.summary || event.type }}</div>
          <div class="timeline-meta">
            <span>{{ phaseLabel(event.phase) }}</span>
            <span v-if="event.data?.tool">{{ event.data.tool }}</span>
            <span v-if="event.attempt">第 {{ event.attempt }} 次</span>
            <span class="timeline-duration">{{ durationLabel(event, index) }}</span>
            <span>{{ formatTime(event.created_at) }}</span>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, ref } from 'vue'
import type { RuntimeEvent } from '../api/runtime'

const props = withDefaults(defineProps<{ events: RuntimeEvent[]; active?: boolean; expandedByDefault?: boolean }>(), {
  active: false,
  expandedByDefault: true,
})
const expanded = ref(props.expandedByDefault)
const visibleEvents = computed(() =>
  props.events
    .filter((event) => !['message.delta', 'usage.updated'].includes(event.type))
    .slice(-80),
)
const latestSummary = computed(() =>
  [...visibleEvents.value].reverse().find((event) => event.summary)?.summary
    ?? (props.active ? '正在准备执行' : '执行记录'),
)

function icon(event: RuntimeEvent): string {
  if (event.status === 'failed' || event.type.endsWith('.failed')) return '×'
  if (event.status === 'completed' || /\.(completed|passed)$/.test(event.type)) return '✓'
  if (event.type.startsWith('plan.confirmation')) return '◷'
  if (event.type.includes('permission') || event.type.includes('input')) return '◷'
  if (event.type.startsWith('subagent.') || event.type.startsWith('task.')) return '◇'
  return '●'
}

function phaseLabel(phase?: string | null): string {
  return ({
    planning: '规划', thinking: '分析', acting: '执行', delegating: '协同',
    waiting: '等待', verifying: '验证', finalizing: '收尾', recovering: '恢复',
    responding: '回复', execution: '运行',
  } as Record<string, string>)[phase ?? ''] ?? ''
}

function formatTime(value: string): string {
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? '' : date.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' })
}

function durationLabel(event: RuntimeEvent, index: number): string {
  const reported = Number(event.data?.duration_ms)
  if (Number.isFinite(reported) && reported >= 0) return `执行 ${Math.round(reported)} ms`
  if (index === 0) return '起点 0 ms'
  const previous = new Date(visibleEvents.value[index - 1].created_at).getTime()
  const current = new Date(event.created_at).getTime()
  if (!Number.isFinite(previous) || !Number.isFinite(current)) return '间隔 — ms'
  return `间隔 ${Math.max(0, Math.round(current - previous))} ms`
}
</script>

<style scoped>
.execution-timeline { width: 100%; min-width: 0; border: 1px solid var(--border); border-radius: 12px; background: var(--surface-raised); overflow: hidden; }
.timeline-header { width: 100%; border: 0; background: transparent; color: var(--text-strong); display: flex; align-items: center; gap: 8px; padding: 10px 12px; cursor: pointer; text-align: left; }
.timeline-title { flex: 1; font-size: 13px; }
.timeline-toggle { color: var(--text-muted); font-size: 12px; }
.timeline-pulse { width: 8px; height: 8px; border-radius: 50%; background: #18a058; }
.timeline-pulse.active { animation: pulse 1.4s ease-in-out infinite; }
.timeline-list { border-top: 1px solid var(--border); padding: 6px 0; max-height: 340px; overflow: auto; }
.timeline-row { display: flex; gap: 9px; padding: 7px 12px; }
.timeline-row.child { padding-left: 28px; }
.timeline-icon { width: 14px; color: var(--accent); font-weight: 700; }
.timeline-row.failed .timeline-icon { color: #d03050; }
.timeline-copy { min-width: 0; }
.timeline-summary { color: var(--text-strong); font-size: 13px; line-height: 1.35; }
.timeline-meta { display: flex; flex-wrap: wrap; gap: 8px; color: var(--text-muted); font-size: 11px; margin-top: 2px; }
.timeline-duration { color: var(--accent); font-family: var(--font-mono); }
@keyframes pulse { 50% { opacity: .35; transform: scale(.8); } }
</style>
