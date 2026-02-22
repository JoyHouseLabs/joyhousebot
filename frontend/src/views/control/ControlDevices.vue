<template>
  <div class="control-page">
    <div class="content-header">
      <h1 class="page-title">设备与配对</h1>
      <p class="page-sub">待审批配对、已配对设备、令牌旋转与撤销</p>
    </div>
    <n-spin :show="loading">
      <div class="devices-actions">
        <n-button quaternary size="small" @click="load">刷新</n-button>
      </div>
      <n-tabs type="line" class="devices-tabs">
        <n-tab-pane name="pending" tab="待审批">
          <div v-if="pending.length === 0" class="empty-hint">暂无待审批配对请求</div>
          <n-data-table
            v-else
            :columns="pendingColumns"
            :data="pending"
            :bordered="false"
            size="small"
            class="devices-table"
          >
            <template #actions="{ row }">
              <n-space>
                <n-button size="tiny" type="primary" @click="approve(row)">通过</n-button>
                <n-button size="tiny" @click="reject(row)">拒绝</n-button>
              </n-space>
            </template>
          </n-data-table>
        </n-tab-pane>
        <n-tab-pane name="paired" tab="已配对">
          <div v-if="paired.length === 0" class="empty-hint">暂无已配对设备</div>
          <n-data-table
            v-else
            :columns="pairedColumns"
            :data="paired"
            :bordered="false"
            size="small"
            class="devices-table"
          >
            <template #actions="{ row }">
              <n-space>
                <n-button size="tiny" @click="rotateToken(row)">旋转令牌</n-button>
                <n-button size="tiny" type="error" quaternary @click="revokeToken(row)">撤销令牌</n-button>
              </n-space>
            </template>
          </n-data-table>
        </n-tab-pane>
      </n-tabs>
    </n-spin>
  </div>
</template>

<script setup lang="ts">
import { h, ref, onMounted, watch } from 'vue'
import { NButton, NDataTable, NSpace, NSpin, NTabs, NTabPane, useMessage } from 'naive-ui'
import type { DataTableColumns } from 'naive-ui'
import { useGatewayInject } from '../../composables/useGateway'

const message = useMessage()
const gateway = useGatewayInject()
const loading = ref(false)
const pending = ref<PairRequest[]>([])
const paired = ref<PairedEntry[]>([])

interface PairRequest {
  requestId: string
  deviceId: string
  displayName?: string
  platform?: string
  version?: string
  requestedAtMs?: number
}

interface PairedEntry {
  deviceId: string
  displayName?: string
  roles?: string[]
  scopes?: string[]
  approvedAtMs?: number
  tokens?: Record<string, { scopes?: string[]; createdAtMs?: number; revokedAtMs?: number }>
}

const pendingColumns: DataTableColumns<PairRequest> = [
  { title: 'RequestId', key: 'requestId', width: 140, ellipsis: { tooltip: true } },
  { title: 'DeviceId', key: 'deviceId', width: 160, ellipsis: { tooltip: true } },
  { title: '名称', key: 'displayName', width: 120 },
  { title: '平台', key: 'platform', width: 100 },
  { title: '操作', key: 'actions', width: 160, render: (_, row) => h('div', {}, [
    h(NButton, { size: 'tiny', type: 'primary', onClick: () => approve(row) }, () => '通过'),
    h(NButton, { size: 'tiny', onClick: () => reject(row), style: 'margin-left:8px' }, () => '拒绝'),
  ]) },
]

const pairedColumns: DataTableColumns<PairedEntry> = [
  { title: 'DeviceId', key: 'deviceId', width: 160, ellipsis: { tooltip: true } },
  { title: '名称', key: 'displayName', width: 120 },
  { title: 'Scopes', key: 'scopes', ellipsis: { tooltip: true }, render: (r) => (r.scopes ?? []).join(', ') || '—' },
  { title: '操作', key: 'actions', width: 200, render: (_, row) => h('div', {}, [
    h(NButton, { size: 'tiny', onClick: () => rotateToken(row) }, () => '旋转令牌'),
    h(NButton, { size: 'tiny', type: 'error', quaternary: true, onClick: () => revokeToken(row), style: 'margin-left:8px' }, () => '撤销令牌'),
  ]) },
]

async function load() {
  if (!gateway?.request) return
  loading.value = true
  try {
    const res = await gateway.request<{ pending: PairRequest[]; paired: PairedEntry[] }>('device.pair.list')
    pending.value = res?.pending ?? []
    paired.value = res?.paired ?? []
  } catch (e) {
    message.error(String(e))
  } finally {
    loading.value = false
  }
}

async function approve(row: PairRequest) {
  if (!gateway?.request) return
  try {
    await gateway.request('device.pair.approve', { requestId: row.requestId })
    message.success('已通过')
    await load()
  } catch (e) {
    message.error(String(e))
  }
}

async function reject(row: PairRequest) {
  if (!gateway?.request) return
  try {
    await gateway.request('device.pair.reject', { requestId: row.requestId })
    message.success('已拒绝')
    await load()
  } catch (e) {
    message.error(String(e))
  }
}

async function rotateToken(row: PairedEntry) {
  if (!gateway?.request) return
  try {
    const res = await gateway.request<{ token: string }>('device.token.rotate', { deviceId: row.deviceId, role: 'operator' })
    message.success('令牌已旋转，新 token 仅显示一次：' + (res?.token ?? '').slice(0, 20) + '…')
    await load()
  } catch (e) {
    message.error(String(e))
  }
}

async function revokeToken(row: PairedEntry) {
  if (!gateway?.request) return
  try {
    await gateway.request('device.token.revoke', { deviceId: row.deviceId, role: 'operator' })
    message.success('已撤销')
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
.devices-actions { margin-bottom: 12px; }
.devices-tabs { margin-top: 8px; }
.devices-table { margin-top: 8px; }
.empty-hint { color: var(--text-muted); font-size: 13px; padding: 16px 0; }
</style>
