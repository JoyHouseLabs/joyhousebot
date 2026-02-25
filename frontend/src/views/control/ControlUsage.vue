<template>
  <div class="control-page">
    <div class="content-header">
      <h1 class="page-title">用量统计</h1>
      <p class="page-sub">按日期查看 Token 与成本、会话列表与会话详情</p>
    </div>
    <n-spin :show="loading">
      <div class="usage-toolbar">
        <div class="usage-dates">
          <n-date-picker
            v-model:value="startDateTs"
            type="date"
            clearable
            placeholder="开始日期"
            @update:value="onStartDateChange"
          />
          <span class="muted">至</span>
          <n-date-picker
            v-model:value="endDateTs"
            type="date"
            clearable
            placeholder="结束日期"
            @update:value="onEndDateChange"
          />
          <n-button size="small" @click="applyPreset(1)">今日</n-button>
          <n-button size="small" @click="applyPreset(7)">7 天</n-button>
          <n-button size="small" @click="applyPreset(30)">30 天</n-button>
          <n-button type="primary" size="small" :loading="loading" @click="loadUsage">刷新</n-button>
        </div>
        <div v-if="error" class="usage-error">
          <n-alert type="error" :title="error" closable @close="error = ''" />
        </div>
      </div>
      <div v-if="totals" class="usage-totals">
        <n-card size="small" class="usage-card">
          <template #header>Token</template>
          <div class="usage-value">{{ formatTokens(totals.totalTokens) }}</div>
        </n-card>
        <n-card size="small" class="usage-card">
          <template #header>成本</template>
          <div class="usage-value">{{ formatCost(totals.totalCost) }}</div>
        </n-card>
        <n-card size="small" class="usage-card">
          <template #header>会话数</template>
          <div class="usage-value">{{ sessions.length }}</div>
        </n-card>
      </div>
      <div v-if="costDaily.length > 0" class="usage-daily-section">
        <h3 class="section-title">每日汇总</h3>
        <n-data-table
          :columns="dailyColumns"
          :data="costDaily"
          :bordered="false"
          size="small"
          :scroll-x="600"
        />
      </div>
      <div class="usage-sessions-section">
        <h3 class="section-title">会话列表</h3>
        <n-data-table
          :columns="sessionColumns"
          :data="sortedSessions"
          :bordered="false"
          size="small"
          :row-key="(row: SessionUsageEntry) => row.key"
          :scroll-x="800"
          @update:sorter="onSessionSorter"
        />
      </div>
      <div v-if="selectedSessionKey" class="usage-detail-section">
        <h3 class="section-title">会话详情 · {{ selectedSessionKey }}</h3>
        <n-tabs type="line">
          <n-tab-pane name="timeseries" tab="时序">
            <div v-if="timeSeriesLoading" class="detail-loading">加载中…</div>
            <div v-else-if="timeSeries?.points?.length" class="timeseries-summary">
              <p>共 {{ timeSeries.points.length }} 个数据点，累计 Token {{ formatTokens(timeSeriesCumulativeTokens) }}，累计成本 {{ formatCost(timeSeriesCumulativeCost) }}</p>
            </div>
            <div v-else class="detail-empty">暂无时序数据</div>
          </n-tab-pane>
          <n-tab-pane name="logs" tab="日志">
            <div v-if="sessionLogsLoading" class="detail-loading">加载中…</div>
            <div v-else-if="sessionLogs?.length" class="logs-list">
              <div v-for="(log, i) in sessionLogs" :key="i" class="log-row">
                <span class="log-role">{{ log.role }}</span>
                <span class="log-content">{{ (log.content ?? '').slice(0, 200) }}{{ (log.content?.length ?? 0) > 200 ? '…' : '' }}</span>
                <span class="log-meta">{{ log.tokens ?? 0 }} tokens</span>
              </div>
            </div>
            <div v-else class="detail-empty">暂无日志</div>
          </n-tab-pane>
        </n-tabs>
      </div>
    </n-spin>
  </div>
</template>

<script setup lang="ts">
import { h, ref, computed, onMounted, watch } from 'vue'
import { useGatewayInject } from '../../composables/useGateway'

/** Align with backend / OpenClaw sessions.usage and usage.cost response shapes */
export interface UsageTotals {
  input: number
  output: number
  cacheRead: number
  cacheWrite: number
  totalTokens: number
  totalCost: number
  inputCost?: number
  outputCost?: number
  cacheReadCost?: number
  cacheWriteCost?: number
  missingCostEntries?: number
}

export interface SessionUsageEntry {
  key: string
  label?: string
  sessionId?: string
  updatedAt?: number
  firstActivity?: number
  lastActivity?: number
  activityDates?: string[]
  usage: {
    input: number
    output: number
    cacheRead: number
    cacheWrite: number
    totalTokens: number
    totalCost: number
    messageCounts?: { total: number; user: number; assistant: number; toolCalls: number; toolResults: number; errors: number }
  } | null
}

export interface SessionsUsageResult {
  updatedAt: number
  startDate: string
  endDate: string
  sessions: SessionUsageEntry[]
  totals: UsageTotals
  aggregates?: {
    messages?: Record<string, number>
    tools?: { totalCalls: number; uniqueTools: number; tools: unknown[] }
    daily?: Array<{ date: string; totalTokens?: number; totalCost?: number; messages?: number; toolCalls?: number; errors?: number }>
  }
}

export interface CostUsageSummary {
  updatedAt?: number
  days?: number
  daily: Array<{ date: string; totalTokens?: number; totalCost?: number; messages?: number; toolCalls?: number; errors?: number }>
  totals: UsageTotals
}

export interface SessionUsageTimeSeries {
  sessionId?: string
  points: Array<{
    timestamp: number
    input: number
    output: number
    totalTokens: number
    cost: number
    cumulativeTokens: number
    cumulativeCost: number
  }>
}

export interface SessionLogEntry {
  timestamp?: string | number
  role: string
  content?: string
  tokens?: number
  cost?: number
}

const gateway = useGatewayInject()
const loading = ref(false)
const error = ref('')
const startDate = ref('')
const endDate = ref('')
const startDateTs = ref<number | null>(null)
const endDateTs = ref<number | null>(null)
const usageResult = ref<SessionsUsageResult | null>(null)
const costSummary = ref<CostUsageSummary | null>(null)
const selectedSessionKey = ref<string | null>(null)
const timeSeries = ref<SessionUsageTimeSeries | null>(null)
const timeSeriesLoading = ref(false)
const sessionLogs = ref<SessionLogEntry[] | null>(null)
const sessionLogsLoading = ref(false)
const sessionSortBy = ref<'tokens' | 'cost'>('tokens')
const sessionSortOrder = ref<'ascend' | 'descend'>('descend')

function toYMD(d: Date): string {
  const y = d.getFullYear()
  const m = String(d.getMonth() + 1).padStart(2, '0')
  const day = String(d.getDate()).padStart(2, '0')
  return `${y}-${m}-${day}`
}

function initDates() {
  const end = new Date()
  const start = new Date()
  start.setDate(start.getDate() - 29)
  startDate.value = toYMD(start)
  endDate.value = toYMD(end)
  startDateTs.value = start.getTime()
  endDateTs.value = end.getTime()
}

const sessions = computed(() => usageResult.value?.sessions ?? [])
const totals = computed(() => usageResult.value?.totals ?? null)
const costDaily = computed(() => {
  const d = costSummary.value?.daily ?? usageResult.value?.aggregates?.daily ?? []
  return d.map((row) => ({
    date: row.date,
    totalTokens: row.totalTokens ?? 0,
    totalCost: row.totalCost ?? 0,
    messages: row.messages ?? 0,
    toolCalls: row.toolCalls ?? 0,
    errors: row.errors ?? 0,
  }))
})

const sortedSessions = computed(() => {
  const list = [...sessions.value]
  const key = sessionSortBy.value
  const dir = sessionSortOrder.value === 'ascend' ? 1 : -1
  list.sort((a, b) => {
    const va = key === 'tokens' ? (a.usage?.totalTokens ?? 0) : (a.usage?.totalCost ?? 0)
    const vb = key === 'tokens' ? (b.usage?.totalTokens ?? 0) : (b.usage?.totalCost ?? 0)
    return dir * (vb - va)
  })
  return list
})

const timeSeriesCumulativeTokens = computed(() => {
  const pts = timeSeries.value?.points
  if (!pts?.length) return 0
  return pts[pts.length - 1]?.cumulativeTokens ?? 0
})
const timeSeriesCumulativeCost = computed(() => {
  const pts = timeSeries.value?.points
  if (!pts?.length) return 0
  return pts[pts.length - 1]?.cumulativeCost ?? 0
})

function formatTokens(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}K`
  return String(n)
}

function formatCost(n: number): string {
  if (n === 0) return '0'
  if (n < 0.01 && n > 0) return n.toFixed(4)
  return n.toFixed(2)
}

function applyPreset(days: number) {
  const end = new Date()
  const start = new Date()
  start.setDate(start.getDate() - (days - 1))
  startDate.value = toYMD(start)
  endDate.value = toYMD(end)
  startDateTs.value = start.getTime()
  endDateTs.value = end.getTime()
  loadUsage()
}

function onStartDateChange(v: string | number | null) {
  if (v == null) return
  if (typeof v === 'number') startDate.value = toYMD(new Date(v))
  else if (typeof v === 'string') startDate.value = v.slice(0, 10)
  loadUsage()
}

function onEndDateChange(v: string | number | null) {
  if (v == null) return
  if (typeof v === 'number') endDate.value = toYMD(new Date(v))
  else if (typeof v === 'string') endDate.value = v.slice(0, 10)
  loadUsage()
}

function onSessionSorter(sorter: { columnKey: string; order: 'ascend' | 'descend' | false }) {
  if (sorter.columnKey === 'totalTokens') {
    sessionSortBy.value = 'tokens'
    sessionSortOrder.value = sorter.order === 'ascend' ? 'ascend' : 'descend'
  } else if (sorter.columnKey === 'totalCost') {
    sessionSortBy.value = 'cost'
    sessionSortOrder.value = sorter.order === 'ascend' ? 'ascend' : 'descend'
  }
}

async function loadUsage() {
  if (!gateway?.request) return
  const startStr = startDate.value || (startDateTs.value ? toYMD(new Date(startDateTs.value)) : '')
  const endStr = endDate.value || (endDateTs.value ? toYMD(new Date(endDateTs.value)) : '')
  if (!startStr || !endStr) return
  loading.value = true
  error.value = ''
  try {
    const [sessionsRes, costRes] = await Promise.all([
      gateway.request<SessionsUsageResult>('sessions.usage', {
        startDate: startStr,
        endDate: endStr,
        limit: 1000,
      }),
      gateway.request<CostUsageSummary>('usage.cost', {
        startDate: startStr,
        endDate: endStr,
      }),
    ])
    usageResult.value = sessionsRes ?? null
    costSummary.value = costRes ?? null
  } catch (e) {
    error.value = e instanceof Error ? e.message : String(e)
  } finally {
    loading.value = false
  }
}

async function loadTimeSeries(key: string) {
  if (!gateway?.request) return
  timeSeriesLoading.value = true
  timeSeries.value = null
  try {
    const res = await gateway.request<SessionUsageTimeSeries>('sessions.usage.timeseries', { key })
    timeSeries.value = res ?? null
  } catch {
    timeSeries.value = null
  } finally {
    timeSeriesLoading.value = false
  }
}

async function loadSessionLogs(key: string) {
  if (!gateway?.request) return
  sessionLogsLoading.value = true
  sessionLogs.value = null
  try {
    const res = await gateway.request<{ logs: SessionLogEntry[] }>('sessions.usage.logs', { key, limit: 500 })
    sessionLogs.value = res?.logs ?? null
  } catch {
    sessionLogs.value = null
  } finally {
    sessionLogsLoading.value = false
  }
}

const dailyColumns = [
  { title: '日期', key: 'date', width: 120 },
  { title: 'Token', key: 'totalTokens', width: 100, render: (row: { totalTokens: number }) => formatTokens(row.totalTokens) },
  { title: '成本', key: 'totalCost', width: 90, render: (row: { totalCost: number }) => formatCost(row.totalCost) },
  { title: '消息数', key: 'messages', width: 80 },
  { title: 'Tool 调用', key: 'toolCalls', width: 90 },
  { title: '错误', key: 'errors', width: 70 },
]

const sessionColumns = [
  { title: '会话', key: 'key', width: 200, ellipsis: { tooltip: true } },
  {
    title: 'Token',
    key: 'totalTokens',
    width: 100,
    sorter: true,
    sortOrder: sessionSortBy.value === 'tokens' ? sessionSortOrder.value : false,
    render: (row: SessionUsageEntry) => formatTokens(row.usage?.totalTokens ?? 0),
  },
  {
    title: '成本',
    key: 'totalCost',
    width: 90,
    sorter: true,
    sortOrder: sessionSortBy.value === 'cost' ? sessionSortOrder.value : false,
    render: (row: SessionUsageEntry) => formatCost(row.usage?.totalCost ?? 0),
  },
  {
    title: '消息数',
    key: 'messages',
    width: 90,
    render: (row: SessionUsageEntry) => row.usage?.messageCounts?.total ?? 0,
  },
  {
    title: '',
    key: 'action',
    width: 80,
    render: (row: SessionUsageEntry) => {
      return h('button', {
        class: 'n-button n-button--small-type',
        onClick: () => {
          selectedSessionKey.value = row.key
          loadTimeSeries(row.key)
          loadSessionLogs(row.key)
        },
      }, '详情')
    },
  },
]

onMounted(() => {
  initDates()
  loadUsage()
})
watch(() => gateway?.connected, (connected) => {
  if (connected) void loadUsage()
})
</script>

<style scoped>
.control-page {
  padding: 16px;
  max-width: 1200px;
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
.usage-toolbar {
  margin-bottom: 16px;
}
.usage-dates {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
}
.usage-dates .muted {
  color: var(--text-muted, #666);
  font-size: 13px;
}
.usage-error {
  margin-top: 10px;
}
.usage-totals {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(160px, 1fr));
  gap: 12px;
  margin-bottom: 20px;
}
.usage-card :deep(.n-card-header) {
  font-weight: 600;
  font-size: 13px;
}
.usage-value {
  font-size: 1.1rem;
  font-weight: 500;
}
.usage-daily-section,
.usage-sessions-section,
.usage-detail-section {
  margin-bottom: 24px;
}
.section-title {
  font-size: 0.95rem;
  font-weight: 600;
  margin: 0 0 10px 0;
}
.detail-loading,
.detail-empty {
  color: var(--text-muted, #666);
  font-size: 13px;
  padding: 12px 0;
}
.timeseries-summary p,
.logs-list {
  font-size: 13px;
}
.log-row {
  display: flex;
  gap: 8px;
  padding: 4px 0;
  border-bottom: 1px solid var(--border-color, #eee);
}
.log-role {
  min-width: 80px;
  font-weight: 500;
}
.log-content {
  flex: 1;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.log-meta {
  color: var(--text-muted, #666);
  font-size: 12px;
}
</style>
