<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { listRuns, type RuntimeRun } from '../api'
import { dateLabel, isActiveRun, statusLabel } from '../presentation'

const runs = ref<RuntimeRun[]>([])
const loading = ref(true)
const error = ref('')
const filter = ref<'active' | 'all'>('active')
const visible = computed(() => filter.value === 'all' ? runs.value : runs.value.filter(isActiveRun))

async function load() {
  loading.value = true
  error.value = ''
  try { runs.value = await listRuns(100) }
  catch (cause) { error.value = cause instanceof Error ? cause.message : '读取执行记录失败' }
  finally { loading.value = false }
}

onMounted(load)
</script>

<template>
  <section class="page-header">
    <div><span class="eyebrow">ACTIVITY</span><h1>正在推进的事情</h1><p>只呈现你需要关心的状态；完整事件、Trace 和回放仍保留在高级控制台。</p></div>
    <button class="secondary-button" @click="load">刷新</button>
  </section>
  <div class="segmented"><button :class="{ selected: filter === 'active' }" @click="filter = 'active'">需要关注</button><button :class="{ selected: filter === 'all' }" @click="filter = 'all'">全部记录</button></div>
  <p v-if="error" class="error-message">{{ error }}</p>
  <div v-if="loading" class="empty-state">正在读取执行记录…</div>
  <div v-else-if="!visible.length" class="empty-state"><span>✓</span><strong>{{ filter === 'active' ? '没有需要处理的事情' : '还没有执行记录' }}</strong><p>从首页交代一个目标，执行记录会出现在这里。</p><RouterLink to="/" class="primary-button">开始一件事</RouterLink></div>
  <div v-else class="activity-list">
    <RouterLink v-for="run in visible" :key="run.run_id" :to="`/runs/${run.run_id}`" class="activity-row">
      <span class="activity-dot" :class="{ active: isActiveRun(run), failed: run.status === 'failed' }" />
      <div class="activity-copy"><strong>{{ run.prompt || run.status_summary || '未命名任务' }}</strong><p>{{ run.status_summary || run.next_action || '查看执行详情' }}</p></div>
      <span class="status-pill" :class="{ active: isActiveRun(run), failed: run.status === 'failed' }">{{ statusLabel(run.status) }}</span>
      <time>{{ dateLabel(run.updated_at || run.created_at) }}</time><span class="row-arrow">→</span>
    </RouterLink>
  </div>
</template>
