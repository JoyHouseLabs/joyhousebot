<template>
  <div class="control-page">
    <div class="content-header">
      <h1 class="page-title">队列</h1>
      <p class="page-sub">按会话的 Lane 运行中 / 排队深度、头部等待时长</p>
    </div>
    <n-spin :show="loading">
      <div class="queue-summary" v-if="data">
        <n-card size="small" class="summary-card">
          <template #header>运行中</template>
          <span class="summary-value">{{ data.summary?.runningSessions ?? 0 }}</span>
          <span class="muted">会话</span>
        </n-card>
        <n-card size="small" class="summary-card">
          <template #header>有排队</template>
          <span class="summary-value">{{ data.summary?.queuedSessions ?? 0 }}</span>
          <span class="muted">会话</span>
        </n-card>
        <n-card size="small" class="summary-card">
          <template #header>总排队数</template>
          <span class="summary-value">{{ data.summary?.totalQueued ?? 0 }}</span>
          <span class="muted">条</span>
        </n-card>
      </div>
      <div class="queue-actions">
        <n-button quaternary size="small" @click="load" :loading="loading">刷新</n-button>
        <span class="muted" v-if="data?.ts">更新于 {{ formatTs(data.ts) }}</span>
      </div>
      <n-data-table
        v-if="data?.sessions?.length"
        :columns="columns"
        :data="filteredSessions"
        :bordered="false"
        size="small"
        class="queue-table"
      />
      <div v-else-if="data && !data.sessions?.length" class="empty-hint">暂无运行中或排队的会话</div>
    </n-spin>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted, onUnmounted, watch } from 'vue'
import { NButton, NCard, NDataTable, NSpin } from 'naive-ui'
import type { DataTableColumns } from 'naive-ui'
import { useGatewayInject } from '../../composables/useGateway'
import type { ControlQueueResponse, QueueLaneSession } from '../../api/control'
import { useMessage } from 'naive-ui'

const message = useMessage()
const gateway = useGatewayInject()
const data = ref<ControlQueueResponse | null>(null)
const loading = ref(false)
const pollIntervalMs = 4000
let pollTimer: ReturnType<typeof setInterval> | null = null

const columns: DataTableColumns<QueueLaneSession> = [
  { title: '会话', key: 'sessionKey', width: 180, ellipsis: { tooltip: true } },
  { title: '运行中 RunId', key: 'runningRunId', width: 140, ellipsis: { tooltip: true }, render: (r) => r.runningRunId ?? '—' },
  { title: '排队数', key: 'queued', width: 90 },
  { title: '队列深度', key: 'queueDepth', width: 90 },
  { title: '头部等待 (ms)', key: 'headWaitMs', width: 120, render: (r) => r.headWaitMs != null ? r.headWaitMs : '—' },
  { title: '最早入队时间', key: 'oldestEnqueuedAt', width: 160, render: (r) => r.oldestEnqueuedAt != null ? formatTs(r.oldestEnqueuedAt) : '—' },
]

const filteredSessions = computed(() => {
  const sessions = data.value?.sessions ?? []
  return sessions
})

function formatTs(ts: number): string {
  try {
    return new Date(ts).toLocaleTimeString()
  } catch {
    return String(ts)
  }
}

async function load() {
  if (!gateway?.request) return
  loading.value = true
  try {
    data.value = await gateway.request<ControlQueueResponse>('lanes.status')
  } catch (e) {
    message.error(String(e))
  } finally {
    loading.value = false
  }
}

function startPolling() {
  if (pollTimer) return
  pollTimer = setInterval(load, pollIntervalMs)
}
function stopPolling() {
  if (pollTimer) {
    clearInterval(pollTimer)
    pollTimer = null
  }
}

onMounted(() => {
  load()
  startPolling()
})
watch(() => gateway?.connected, (connected) => {
  if (connected) {
    void load()
    startPolling()
  } else {
    stopPolling()
  }
})
onUnmounted(stopPolling)
</script>

<style scoped>
.control-page {
  padding: 16px;
  max-width: 960px;
}
.content-header {
  margin-bottom: 16px;
}
.page-title {
  font-size: 1.25rem;
  font-weight: 600;
  margin: 0 0 4px 0;
}
.page-sub {
  font-size: 0.875rem;
  color: var(--text-muted, #666);
  margin: 0;
}
.queue-summary {
  display: flex;
  gap: 16px;
  margin-bottom: 16px;
  flex-wrap: wrap;
}
.summary-card {
  min-width: 120px;
}
.summary-card :deep(.n-card-header) {
  font-weight: 600;
  font-size: 12px;
}
.summary-value {
  font-size: 1.25rem;
  font-weight: 600;
  margin-right: 4px;
}
.queue-actions {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-bottom: 12px;
}
.muted {
  color: var(--text-muted);
  font-size: 12px;
}
.queue-table {
  margin-top: 8px;
}
.empty-hint {
  color: var(--text-muted);
  font-size: 13px;
  padding: 24px 0;
}
</style>
