<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { useRoute } from 'vue-router'
import { cancelRun, consoleUrl, getRun, type RuntimeRun } from '../api'
import { compactId, dateLabel, isActiveRun, statusLabel } from '../presentation'

const route = useRoute()
const run = ref<RuntimeRun | null>(null)
const loading = ref(true)
const error = ref('')
let timer: number | undefined
const runId = computed(() => String(route.params.runId))
const resultHeading = computed(() => {
  if (!run.value) return ''
  if (isActiveRun(run.value)) return '正在形成结果'
  if (run.value.status === 'completed') return '已经完成'
  if (run.value.status === 'cancelled') return '已停止执行'
  return '执行没有正常完成'
})
const resultText = computed(() => {
  if (!run.value) return ''
  if (run.value.status === 'cancelled') return '这项任务已经按你的要求停止。'
  return run.value.result?.content || run.value.error?.message || run.value.result?.error || run.value.status_summary || '结果将在执行完成后出现在这里。'
})

async function load() {
  try {
    run.value = await getRun(runId.value)
    error.value = ''
    if (run.value && !isActiveRun(run.value) && timer) { window.clearInterval(timer); timer = undefined }
  } catch (cause) { error.value = cause instanceof Error ? cause.message : '读取任务失败' }
  finally { loading.value = false }
}

async function cancel() {
  if (!run.value || !isActiveRun(run.value)) return
  await cancelRun(run.value.run_id)
  await load()
}

onMounted(async () => { await load(); if (run.value && isActiveRun(run.value)) timer = window.setInterval(load, 2000) })
onBeforeUnmount(() => { if (timer) window.clearInterval(timer) })
</script>

<template>
  <section v-if="loading" class="empty-state">正在连接执行体…</section>
  <section v-else-if="error" class="empty-state"><span>!</span><strong>暂时无法读取</strong><p>{{ error }}</p><button class="secondary-button" @click="load">重试</button></section>
  <template v-else-if="run">
    <section class="run-hero">
      <div class="run-state-mark" :class="{ active: isActiveRun(run), failed: run.status === 'failed' || run.status === 'timed_out' }">{{ isActiveRun(run) ? '◷' : run.status === 'completed' ? '✓' : run.status === 'cancelled' ? '—' : '!' }}</div>
      <div class="run-hero-copy"><span class="eyebrow">RUN {{ compactId(run.run_id) }}</span><h1>{{ statusLabel(run.status) }}</h1><p>{{ run.status_summary || run.next_action || '执行体正在更新进度。' }}</p></div>
      <div class="run-actions"><button v-if="isActiveRun(run)" class="secondary-button" @click="cancel">取消执行</button><a :href="consoleUrl(`/runs/${run.run_id}`)" class="text-link">查看完整时间线 →</a></div>
    </section>

    <section class="detail-grid">
      <article class="detail-card goal-detail"><span class="eyebrow">GOAL</span><h2>你的目标</h2><p>{{ run.prompt || '当前任务' }}</p></article>
      <article class="detail-card"><span class="eyebrow">STATUS</span><h2>当前状态</h2><dl><div><dt>阶段</dt><dd>{{ run.current_phase || statusLabel(run.status) }}</dd></div><div><dt>Agent</dt><dd>{{ run.agent_id }}</dd></div><div><dt>更新时间</dt><dd>{{ dateLabel(run.updated_at || run.created_at) }}</dd></div></dl></article>
    </section>

    <section class="result-card" :class="{ waiting: isActiveRun(run) }">
      <span class="eyebrow">RESULT</span>
      <h2>{{ resultHeading }}</h2>
      <div v-if="isActiveRun(run)" class="progress-line"><i /><i /><i /></div>
      <p class="result-content">{{ resultText }}</p>
      <RouterLink v-if="run.status === 'completed'" to="/works" class="primary-button">查看成果资产 →</RouterLink>
    </section>
  </template>
</template>
