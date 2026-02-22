<template>
  <div class="control-page">
    <div class="content-header">
      <h1 class="page-title">执行审批</h1>
      <p class="page-sub">待处理的 exec 审批请求，通过/拒绝</p>
    </div>
    <n-spin :show="loading">
      <div class="approvals-actions">
        <n-button quaternary size="small" @click="load">刷新</n-button>
      </div>
      <div v-if="pending.length === 0" class="empty-hint">暂无待审批请求</div>
      <n-data-table
        v-else
        :columns="columns"
        :data="pending"
        :bordered="false"
        size="small"
        class="approvals-table"
      >
        <template #actions="{ row }">
          <n-space>
            <n-button size="tiny" type="primary" @click="resolve(row, 'allow-once')">通过(一次)</n-button>
            <n-button size="tiny" @click="resolve(row, 'deny')">拒绝</n-button>
          </n-space>
        </template>
      </n-data-table>
    </n-spin>
  </div>
</template>

<script setup lang="ts">
import { h, ref, onMounted, watch } from 'vue'
import { NButton, NDataTable, NSpace, NSpin, useMessage } from 'naive-ui'
import type { DataTableColumns } from 'naive-ui'
import { useGatewayInject } from '../../composables/useGateway'

const message = useMessage()
const gateway = useGatewayInject()
const loading = ref(false)
const pending = ref<ApprovalPendingItem[]>([])

interface ApprovalRequest {
  command?: string
  cwd?: string
  host?: string
  agentId?: string
  sessionKey?: string
}

interface ApprovalPendingItem {
  id: string
  request: ApprovalRequest
  createdAtMs: number
  expiresAtMs: number
  status: string
}

const columns: DataTableColumns<ApprovalPendingItem> = [
  { title: 'ID', key: 'id', width: 120, ellipsis: { tooltip: true } },
  { title: '命令', key: 'command', ellipsis: { tooltip: true }, render: (r) => r.request?.command ?? '—' },
  { title: '会话', key: 'sessionKey', width: 140, ellipsis: { tooltip: true }, render: (r) => r.request?.sessionKey ?? '—' },
  { title: '创建', key: 'createdAtMs', width: 140, render: (r) => formatTs(r.createdAtMs) },
  { title: '过期', key: 'expiresAtMs', width: 140, render: (r) => formatTs(r.expiresAtMs) },
  { title: '操作', key: 'actions', width: 180, render: (_, row) => h('div', {}, [
    h(NButton, { size: 'tiny', type: 'primary', onClick: () => resolve(row, 'allow-once') }, () => '通过(一次)'),
    h(NButton, { size: 'tiny', onClick: () => resolve(row, 'deny'), style: 'margin-left:8px' }, () => '拒绝'),
  ]) },
]

function formatTs(ts: number): string {
  try {
    return new Date(ts).toLocaleString()
  } catch {
    return String(ts)
  }
}

async function load() {
  if (!gateway?.request) return
  loading.value = true
  try {
    const res = await gateway.request<{ pending: ApprovalPendingItem[] }>('exec.approvals.pending')
    pending.value = res?.pending ?? []
  } catch (e) {
    message.error(String(e))
  } finally {
    loading.value = false
  }
}

async function resolve(row: ApprovalPendingItem, decision: 'allow-once' | 'allow-always' | 'deny') {
  if (!gateway?.request) return
  try {
    await gateway.request('exec.approval.resolve', { requestId: row.id, decision })
    message.success(decision === 'deny' ? '已拒绝' : '已通过')
    await load()
  } catch (e) {
    message.error(String(e))
  }
}

onMounted(load)
watch(() => gateway?.connected, (connected) => { if (connected) void load() })
</script>

<style scoped>
.control-page { padding: 16px; max-width: 960px; }
.content-header { margin-bottom: 16px; }
.page-title { font-size: 1.25rem; font-weight: 600; margin: 0 0 4px 0; }
.page-sub { font-size: 0.875rem; color: var(--text-muted, #666); margin: 0; }
.approvals-actions { margin-bottom: 12px; }
.approvals-table { margin-top: 8px; }
.empty-hint { color: var(--text-muted); font-size: 13px; padding: 16px 0; }
</style>
