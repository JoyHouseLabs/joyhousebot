<script setup lang="ts">
import { onUnmounted, ref } from 'vue'
import { type RunPlan, actRunPlan, getRunPlan } from '../../api/plans'
import StageGraph from './StageGraph.vue'

/** Trial-run panel: create a planning Run, preview the plan, confirm/regenerate/cancel. */

const props = defineProps<{ teamId: string; prompt: string }>()
const emit = defineEmits<{ error: [message: string] }>()

const runId = ref('')
const plan = ref<RunPlan | null>(null)
const loading = ref(false)
const acting = ref('')
const feedback = ref('')
const showFeedback = ref(false)
const terminal = ref('')
let timer: number | null = null

async function start() {
  if (!props.teamId || !props.prompt.trim()) {
    emit('error', '请先保存并发布 Team，再填写试运行目标')
    return
  }
  loading.value = true
  try {
    const { submitRuntimeRun } = await import('../../api/runtime')
    const sessionId = `ui:team-composer-${Date.now()}`
    const run = await submitRuntimeRun({
      prompt: props.prompt,
      sessionId,
      execution: { mode: 'team', team_id: props.teamId },
      channel: 'team-composer',
      chatId: sessionId,
    })
    runId.value = run.run_id
    terminal.value = ''
    plan.value = null
    schedulePoll()
  } catch (cause) {
    emit('error', cause instanceof Error ? cause.message : '试运行提交失败')
  } finally {
    loading.value = false
  }
}

function schedulePoll() {
  stopPoll()
  timer = window.setInterval(poll, 1500)
}

function stopPoll() {
  if (timer !== null) { window.clearInterval(timer); timer = null }
}

async function poll() {
  if (!runId.value) return
  try {
    plan.value = await getRunPlan(runId.value)
    if (plan.value.status === 'confirmed' || plan.value.status === 'regenerate_requested' || plan.value.status === 'cancelled' || plan.value.status === 'expired') {
      terminal.value = plan.value.status
      stopPoll()
    }
  } catch {
    // plan_not_ready until the coordinator freezes the first preview.
  }
}

async function act(action: 'confirm' | 'regenerate' | 'cancel') {
  if (!runId.value) return
  if (action === 'regenerate' && !feedback.value.trim()) {
    showFeedback.value = true
    emit('error', '重新生成需要填写反馈')
    return
  }
  acting.value = action
  try {
    await actRunPlan(runId.value, action, action === 'regenerate' ? feedback.value.trim() : undefined)
    feedback.value = ''
    showFeedback.value = false
    if (action === 'regenerate') { terminal.value = ''; schedulePoll() }
    else { terminal.value = action; stopPoll() }
  } catch (cause) {
    emit('error', cause instanceof Error ? cause.message : '计划操作失败')
  } finally {
    acting.value = ''
  }
}

onUnmounted(stopPoll)
</script>

<template>
  <section class="plan-preview panel">
    <div class="panel-heading">
      <div><span class="eyebrow">TRIAL RUN</span><h3>试运行计划预览</h3><p>创建一次真实的 planning Run：先看阶段图与预算，再决定执行。未确认前不会有任何外部写操作。</p></div>
      <button type="button" class="primary-button" :disabled="loading || Boolean(runId)" @click="start">{{ loading ? '提交中…' : runId ? `Run ${runId.slice(0, 12)}…` : '开始试运行' }}</button>
    </div>
    <template v-if="plan">
      <div class="preview-status" :class="{ awaiting: plan.awaiting_confirmation }">
        <span>{{ plan.awaiting_confirmation ? `计划 v${plan.plan_version} · 等待确认` : `计划 v${plan.plan_version} · ${terminal || plan.status}` }}</span>
        <small v-if="plan.plan.summary">{{ plan.plan.summary }}</small>
        <small>{{ plan.estimate.task_count }} 个任务 · {{ plan.estimate.phase_count }} 个阶段 · 并行上限 {{ plan.estimate.max_concurrent ?? '—' }}</small>
      </div>
      <StageGraph :phases="plan.stage_graph.phases" compact />
      <div class="preview-actions" v-if="plan.awaiting_confirmation">
        <button type="button" class="primary-button" :disabled="Boolean(acting)" @click="act('confirm')">{{ acting === 'confirm' ? '确认中…' : '确认执行' }}</button>
        <button type="button" class="secondary-button" :disabled="Boolean(acting)" @click="showFeedback = !showFeedback">带反馈重新生成</button>
        <button type="button" class="danger-button" :disabled="Boolean(acting)" @click="act('cancel')">{{ acting === 'cancel' ? '取消中…' : '取消计划' }}</button>
      </div>
      <div v-if="showFeedback && plan.awaiting_confirmation" class="feedback-box">
        <label><span>修改反馈（必填，会注入 Coordinator 的下一次规划）</span>
          <textarea v-model="feedback" rows="3" placeholder="例如：请聚焦教育场景，并让复核者检查年龄段适配" />
        </label>
        <button type="button" class="primary-button" :disabled="Boolean(acting)" @click="act('regenerate')">{{ acting === 'regenerate' ? '重新生成中…' : '提交反馈并重新生成' }}</button>
      </div>
      <p v-else-if="terminal" class="preview-note">
        {{ terminal === 'confirmed' ? '已确认，Run 正在执行；可在“Run 与 Task”查看进度。' : terminal === 'regenerate_requested' ? '已请求重新生成，等待新的计划。' : terminal === 'cancelled' ? '计划已取消，未创建任何执行 Task。' : '确认窗口已过期，Run 已失败关闭。' }}
      </p>
    </template>
    <p v-else class="preview-empty">{{ runId ? '正在等待 Coordinator 生成计划预览…' : '发布后填写目标并开始试运行。' }}</p>
  </section>
</template>

<style scoped>
.plan-preview { display: grid; gap: 12px; }
.preview-status { display: flex; flex-wrap: wrap; gap: 8px; align-items: baseline; margin: 0 14px; padding: 10px 12px; background: var(--surface); border: 1px solid var(--border); border-radius: 10px; }
.preview-status span { color: var(--text-strong); font-size: 11px; }
.preview-status.awaiting span { color: var(--accent); }
.preview-status small { color: var(--text-muted); font-size: 9px; }
.preview-actions { display: flex; flex-wrap: wrap; gap: 8px; margin: 0 14px; }
.feedback-box { display: grid; gap: 8px; margin: 0 14px; padding: 12px; background: var(--surface); border: 1px dashed var(--accent-border); border-radius: 10px; }
.feedback-box span { color: var(--text-strong); font-size: 10px; }
.feedback-box textarea { width: 100%; padding: 9px 11px; color: var(--text); background: var(--input); border: 1px solid var(--border-strong); border-radius: 8px; resize: vertical; font-size: 10px; }
.preview-note, .preview-empty { margin: 0 14px 14px; color: var(--text-muted); font-size: 10px; }
</style>
