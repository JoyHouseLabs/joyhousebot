<template>
  <div class="control-page">
    <div class="content-header">
      <h1 class="page-title">实例</h1>
      <p class="page-sub">已连接的客户端与 Gateway 实例</p>
    </div>
    <n-spin :show="presenceLoading">
      <div class="instances-toolbar">
        <n-button quaternary size="small" @click="loadPresence">刷新</n-button>
      </div>
      <n-data-table
        :columns="presenceColumns"
        :data="presenceList"
        :bordered="false"
        size="small"
        class="presence-table"
      />
      <div v-if="!presenceLoading && presenceList.length === 0" class="instances-empty">
        <p>暂无实例。</p>
        <p class="instances-empty-hint">使用 <code>joyhousebot gateway</code> 启动服务后，本页会显示当前 API/Gateway 实例；打开「对话」页的 WebSocket 连接也会在此列出。请确认后端已启动且前端请求的端口正确（如开发时代理到 18790）。</p>
      </div>
    </n-spin>
  </div>
</template>

<script setup lang="ts">
import { h, ref, onMounted, watch } from 'vue'
import { NTag, useMessage } from 'naive-ui'
import { useGatewayInject } from '../../composables/useGateway'
import type { PresenceEntry } from '../../api/control'

const message = useMessage()
const gateway = useGatewayInject()
const presenceList = ref<PresenceEntry[]>([])
const presenceLoading = ref(false)

function presenceStatus(ts: number): 'active' | 'idle' | 'stale' {
  const ageSeconds = (Date.now() - ts) / 1000
  if (ageSeconds < 60) return 'active'
  if (ageSeconds < 300) return 'idle'
  return 'stale'
}

function presenceStatusLabel(ts: number): string {
  const s = presenceStatus(ts)
  if (s === 'active') return '活跃'
  if (s === 'idle') return '空闲'
  return '过期'
}

function modeLabel(mode: string): string {
  const m: Record<string, string> = {
    backend: '后端',
    webchat: 'Web 对话',
    ui: '界面',
    cli: 'CLI',
    node: '节点',
    probe: '探针',
    test: '测试',
  }
  return m[mode] ?? mode
}

async function loadPresence() {
  if (!gateway?.request) return
  presenceLoading.value = true
  try {
    const res = await gateway.request<{ ok: boolean; presence: PresenceEntry[] }>('system-presence')
    presenceList.value = res?.presence ?? []
  } catch (e) {
    message.error(String(e))
  } finally {
    presenceLoading.value = false
  }
}

watch(() => gateway?.connected, (connected) => { if (connected) void loadPresence() })

const presenceColumns = [
  { title: '实例 ID', key: 'instance_id', width: 180, ellipsis: { tooltip: true } },
  { title: '模式', key: 'mode', width: 100, render: (row: PresenceEntry) => modeLabel(row.mode) },
  {
    title: '状态',
    key: 'status',
    width: 90,
    render: (row: PresenceEntry) => {
      const s = presenceStatus(row.ts)
      const type = s === 'active' ? 'success' : s === 'idle' ? 'warning' : 'default'
      return h(NTag, { type, size: 'small' }, () => presenceStatusLabel(row.ts))
    },
  },
  { title: '来源', key: 'reason', width: 90 },
  {
    title: '最后更新',
    key: 'ts',
    width: 165,
    render: (row: PresenceEntry) => {
      try {
        return new Date(row.ts).toLocaleString()
      } catch {
        return '—'
      }
    },
  },
  { title: 'Host', key: 'host', width: 120, ellipsis: { tooltip: true }, render: (row: PresenceEntry) => row.host ?? '—' },
  { title: 'IP', key: 'ip', width: 110, render: (row: PresenceEntry) => row.ip ?? '—' },
  {
    title: '距上次输入',
    key: 'last_input_seconds',
    width: 100,
    render: (row: PresenceEntry) =>
      row.last_input_seconds != null ? `${row.last_input_seconds}s` : '—',
  },
]

onMounted(loadPresence)
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
.instances-toolbar {
  display: flex;
  gap: 8px;
  margin-bottom: 12px;
}
.presence-table {
  margin-top: 8px;
}
.instances-empty {
  margin-top: 24px;
  padding: 16px;
  text-align: left;
  color: var(--text-muted, #666);
  font-size: 14px;
}
.instances-empty p {
  margin: 0 0 8px 0;
}
.instances-empty-hint {
  font-size: 13px;
  line-height: 1.5;
}
.instances-empty code {
  background: var(--bg-hover, #f0f0f0);
  padding: 2px 6px;
  border-radius: 4px;
  font-size: 12px;
}
</style>
