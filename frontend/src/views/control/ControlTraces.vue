<template>
  <div class="control-page">
    <div class="content-header">
      <h1 class="page-title">轨迹</h1>
      <p class="page-sub">Agent 运行轨迹：按 run 查看推理步骤与工具调用</p>
    </div>
    <n-spin :show="loading">
      <div v-if="!detailTraceId" class="list-view">
        <div class="list-actions">
          <n-input
            v-model:value="sessionFilter"
            placeholder="按 session 筛选"
            clearable
            size="small"
            style="width: 200px"
            @keyup.enter="load"
          />
          <n-button quaternary size="small" @click="load" :loading="loading">刷新</n-button>
          <span class="muted" v-if="items.length">共 {{ items.length }} 条</span>
        </div>
        <n-data-table
          v-if="items.length"
          :columns="columns"
          :data="items"
          :bordered="false"
          size="small"
          class="traces-table"
          :row-props="rowProps"
        />
        <div v-else-if="!loading" class="empty-hint">暂无轨迹；通过 RPC chat.send/agent 触发的运行会自动记录</div>
        <div v-if="nextCursor" class="load-more">
          <n-button quaternary size="small" @click="loadMore">加载更多</n-button>
        </div>
      </div>
      <div v-else class="detail-view">
        <n-button quaternary size="small" @click="detailTraceId = null">← 返回列表</n-button>
        <div v-if="detail" class="detail-meta">
          <p><strong>RunId</strong> {{ detail.traceId }}</p>
          <p><strong>会话</strong> {{ detail.sessionKey }}</p>
          <p><strong>状态</strong> {{ detail.status }}</p>
          <p><strong>开始</strong> {{ formatTs(detail.startedAtMs) }}</p>
          <p><strong>结束</strong> {{ detail.endedAtMs != null ? formatTs(detail.endedAtMs) : '—' }}</p>
          <p v-if="detail.errorText"><strong>错误</strong> {{ detail.errorText }}</p>
          <p v-if="detail.messagePreview"><strong>首句</strong> {{ detail.messagePreview }}</p>
        </div>
        <div v-if="detail && steps.length" class="steps-timeline">
          <h3>步骤</h3>
          <div v-for="(step, i) in steps" :key="i" class="step-block">
            <span class="step-type">{{ step.type }}</span>
            <span class="step-ts">{{ formatTs(step.ts_ms) }}</span>
            <pre v-if="step.payload && Object.keys(step.payload).length" class="step-payload">{{ formatPayload(step.payload) }}</pre>
          </div>
        </div>
        <div v-else-if="detail && !steps.length" class="muted">无步骤记录</div>
      </div>
    </n-spin>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, watch, onMounted } from 'vue'
import { NButton, NDataTable, NInput, NSpin } from 'naive-ui'
import type { DataTableColumns } from 'naive-ui'
import { useGatewayInject } from '../../composables/useGateway'
import type { TraceItem, TraceDetail, TraceStep } from '../../api/control'
import { useMessage } from 'naive-ui'

const message = useMessage()
const gateway = useGatewayInject()
const loading = ref(false)
const items = ref<TraceItem[]>([])
const nextCursor = ref<string | null>(null)
const sessionFilter = ref('')
const detailTraceId = ref<string | null>(null)
const detail = ref<TraceDetail | null>(null)

const steps = computed(() => {
  if (!detail.value?.stepsJson) return []
  try {
    return JSON.parse(detail.value.stepsJson) as TraceStep[]
  } catch {
    return []
  }
})

const columns: DataTableColumns<TraceItem> = [
  { title: 'RunId', key: 'traceId', width: 140, ellipsis: { tooltip: true } },
  { title: '会话', key: 'sessionKey', width: 140, ellipsis: { tooltip: true } },
  { title: '状态', key: 'status', width: 80 },
  { title: '开始', key: 'startedAtMs', width: 100, render: (r) => formatTs(r.startedAtMs) },
  { title: '结束', key: 'endedAtMs', width: 100, render: (r) => r.endedAtMs != null ? formatTs(r.endedAtMs) : '—' },
  { title: '首句', key: 'messagePreview', ellipsis: { tooltip: true }, render: (r) => (r.messagePreview ?? '').slice(0, 60) + ((r.messagePreview?.length ?? 0) > 60 ? '…' : '') },
]

function formatTs(ts: number): string {
  try {
    return new Date(ts).toLocaleString()
  } catch {
    return String(ts)
  }
}

function formatPayload(p: Record<string, unknown>): string {
  try {
    return JSON.stringify(p, null, 2)
  } catch {
    return String(p)
  }
}

function rowProps(row: TraceItem) {
  return {
    style: { cursor: 'pointer' },
    onClick: () => {
      detailTraceId.value = row.traceId
    },
  }
}

async function load() {
  if (!gateway?.request) return
  loading.value = true
  try {
    const res = await gateway.request<{ items: TraceItem[]; nextCursor?: string }>('traces.list', {
      session_key: sessionFilter.value.trim() || undefined,
      limit: 30,
    })
    items.value = res?.items ?? []
    nextCursor.value = res?.nextCursor ?? null
  } catch (e) {
    message.error(String(e))
  } finally {
    loading.value = false
  }
}

async function loadMore() {
  if (!nextCursor.value || !gateway?.request) return
  loading.value = true
  try {
    const res = await gateway.request<{ items: TraceItem[]; nextCursor?: string }>('traces.list', {
      session_key: sessionFilter.value.trim() || undefined,
      limit: 30,
      cursor: nextCursor.value,
    })
    items.value = [...items.value, ...(res?.items ?? [])]
    nextCursor.value = res?.nextCursor ?? null
  } catch (e) {
    message.error(String(e))
  } finally {
    loading.value = false
  }
}

watch(detailTraceId, async (id) => {
  if (!id) {
    detail.value = null
    return
  }
  if (!gateway?.request) return
  loading.value = true
  try {
    detail.value = await gateway.request<TraceDetail>('traces.get', { traceId: id })
  } catch (e) {
    message.error(String(e))
    detailTraceId.value = null
  } finally {
    loading.value = false
  }
})

onMounted(() => { load() })
watch(() => gateway?.connected, (connected) => { if (connected) void load() })
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
.list-actions {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-bottom: 12px;
}
.muted {
  color: var(--text-muted);
  font-size: 12px;
}
.traces-table {
  margin-top: 8px;
}
.empty-hint {
  color: var(--text-muted);
  font-size: 13px;
  padding: 24px 0;
}
.load-more {
  margin-top: 12px;
}
.detail-view {
  margin-top: 12px;
}
.detail-meta {
  margin: 16px 0;
  font-size: 13px;
}
.detail-meta p {
  margin: 4px 0;
}
.steps-timeline {
  margin-top: 20px;
}
.steps-timeline h3 {
  font-size: 14px;
  margin-bottom: 8px;
}
.step-block {
  border: 1px solid var(--border-color, #eee);
  border-radius: 6px;
  padding: 8px 12px;
  margin-bottom: 8px;
  font-size: 12px;
}
.step-type {
  font-weight: 600;
  margin-right: 8px;
}
.step-ts {
  color: var(--text-muted);
  margin-right: 8px;
}
.step-payload {
  margin: 8px 0 0 0;
  font-size: 11px;
  white-space: pre-wrap;
  word-break: break-all;
  max-height: 200px;
  overflow: auto;
}
</style>
