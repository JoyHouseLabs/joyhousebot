<template>
  <div class="page monitor-page">
    <header class="page-heading">
      <div>
        <span class="eyebrow">MONITOR</span>
        <h1>运行监控概览</h1>
        <p>平台全局运行状态、Worker 集群、用户规模与资源用量。</p>
      </div>
      <div class="heading-actions">
        <span class="refresh-note">{{ lastUpdated ? `更新于 ${formatClock(lastUpdated)}` : '尚未更新' }}</span>
        <button class="secondary-button" type="button" :disabled="loading" @click="refresh">{{ loading ? '刷新中…' : '刷新' }}</button>
        <router-link class="primary-button" to="/chat">试用 Agent</router-link>
      </div>
    </header>

    <div v-if="error" class="notice error-notice">{{ error }}</div>

    <section class="health-strip">
      <div class="health-copy">
        <span class="state-dot" :class="{ on: health.api }" />
        <div><strong>FastAPI Gateway</strong><small>{{ health.api ? '请求入口正常' : '无法连接' }}</small></div>
      </div>
      <div class="health-copy">
        <span class="state-dot" :class="{ on: health.database }" />
        <div><strong>Runtime Store</strong><small>{{ health.database ? databaseLabel : '数据库未就绪' }}</small></div>
      </div>
      <div class="health-copy identity-health">
        <span class="health-symbol">@</span>
        <div><strong>{{ identity.user_id || runtimeUserId }}</strong><small>{{ identity.role || 'development user' }} · {{ identity.is_admin ? '管理权限已启用' : '普通用户' }}</small></div>
      </div>
    </section>

    <section class="metric-grid">
      <article class="metric-card">
        <span>平台 Runs</span><strong>{{ platform.runs }}</strong><small>{{ platform.active_runs }} 个正在执行</small>
      </article>
      <article class="metric-card">
        <span>Token 用量</span><strong>{{ compactNumber(platform.usage.total_tokens) }}</strong><small>输入 {{ compactNumber(platform.usage.input_tokens) }} · 输出 {{ compactNumber(platform.usage.output_tokens) }}</small>
      </article>
      <article class="metric-card">
        <span>用户 / 会话</span><strong>{{ platform.users }}</strong><small>{{ platform.sessions }} 个会话</small>
      </article>
      <article class="metric-card">
        <span>Worker 集群</span><strong>{{ platform.healthy_workers }}</strong><small>健康运行 · {{ Math.max(0, platform.workers - platform.healthy_workers) }} 条历史 / 陈旧记录</small>
      </article>
    </section>

    <section class="panel worker-summary-panel">
      <div class="panel-heading">
        <div><span class="eyebrow">WORKERS</span><h2>当前执行节点</h2></div>
        <router-link to="/platform">查看执行集群 →</router-link>
      </div>
      <div v-if="healthyWorkers.length" class="worker-summary-list">
        <article v-for="worker in healthyWorkers.slice(0, 8)" :key="worker.worker_id" class="worker-summary-row">
          <span class="state-dot" :class="{ on: worker.healthy }" />
          <div class="worker-summary-main"><strong>{{ workerRole(worker) }}</strong><small>{{ worker.worker_id }} · {{ worker.healthy ? '健康' : '陈旧 / 离线' }}</small></div>
          <span class="worker-summary-capacity">{{ workerSlots(worker) }} 槽位</span>
          <span class="worker-summary-plugin">{{ workerPlugin(worker) }}</span>
          <time>{{ relativeTime(worker.last_heartbeat) }}</time>
        </article>
      </div>
      <div v-else class="empty-state compact"><strong>暂无健康 Worker 心跳</strong></div>
      <p class="worker-summary-note">健康 {{ platform.healthy_workers }} · 历史 / 陈旧记录 {{ Math.max(0, platform.workers - platform.healthy_workers) }} · 角色和插件信息来自 Worker 心跳。</p>
    </section>

    <section class="monitor-grid ops-metrics-grid">
      <section class="panel">
        <div class="panel-heading"><div><span class="eyebrow">PROVIDERS</span><h2>模型与 Provider</h2></div></div>
        <div v-if="metrics.providers.length" class="ops-table">
          <div v-for="item in metrics.providers.slice(0, 8)" :key="`${item.provider}:${item.model}:${item.status}`" class="ops-row">
            <div><strong>{{ item.provider }}</strong><small>{{ item.model }}</small></div>
            <span class="status-badge" :class="item.status">{{ item.status }}</span>
            <span>{{ item.count }} 次 · {{ Math.round(item.avg_duration_ms) }}ms</span>
            <span>P95 {{ Math.round(item.p95_duration_ms) }}ms · TTFT {{ Math.round(item.avg_ttft_ms) }}ms</span>
          </div>
        </div>
        <div v-else class="empty-state compact"><strong>暂无 Provider 调用</strong></div>
      </section>
      <section class="panel">
        <div class="panel-heading"><div><span class="eyebrow">QUEUE / CHANNEL</span><h2>队列与通道</h2></div></div>
        <div class="ops-summary"><span v-for="(value, key) in metrics.tasks" :key="`task-${key}`">Task {{ key }} <strong>{{ value }}</strong></span></div>
        <div class="ops-summary"><span v-for="(value, key) in metrics.workers" :key="`worker-${key}`">Worker {{ key }} <strong>{{ value }}</strong></span></div>
        <div class="ops-summary"><span>排队 <strong>{{ metrics.queue.queued }}</strong></span><span>最长等待 <strong>{{ formatAge(metrics.queue.oldest_age_seconds) }}</strong></span><span>过期租约 <strong>{{ metrics.queue.expired_leases }}</strong></span><span>重试 <strong>{{ metrics.queue.retried_tasks }}</strong></span><span>陈旧 Worker <strong>{{ metrics.workers_stale }}</strong></span></div>
        <div v-if="metrics.channels.length" class="ops-table">
          <div v-for="item in metrics.channels" :key="`${item.channel}:${item.status}`" class="ops-row"><strong>{{ item.channel }}</strong><span>{{ item.status }}</span><b>{{ item.count }}</b></div>
        </div>
        <div v-else class="empty-state compact"><strong>暂无 Channel outbox</strong></div>
      </section>
    </section>

    <div class="monitor-grid">
      <section class="panel recent-panel">
        <div class="panel-heading">
          <div><span class="eyebrow">RUNS</span><h2>最近运行</h2></div>
          <router-link to="/runs">查看全部 →</router-link>
        </div>
        <div v-if="runs.length" class="run-list">
          <router-link v-for="run in runs.slice(0, 8)" :key="run.run_id" :to="`/runs?run=${run.run_id}`" class="run-row">
            <span class="status-badge" :class="run.status">{{ statusLabel(run.status) }}</span>
            <div class="run-main"><strong>{{ run.status_summary || run.prompt || 'Agent Run' }}</strong><small>{{ run.agent_id }} · {{ run.session_id }}</small></div>
            <div class="run-progress" v-if="run.total_task_count"><span :style="{ width: taskProgress(run) + '%' }" /></div>
            <time>{{ relativeTime(run.updated_at || run.created_at) }}</time>
          </router-link>
        </div>
        <div v-else class="empty-state"><span>◇</span><strong>还没有运行记录</strong><p>前往 Agent 试用提交第一个 Run。</p></div>
      </section>

      <section class="panel schedule-panel">
        <div class="panel-heading"><div><span class="eyebrow">SCHEDULE</span><h2>调度任务</h2></div></div>
        <div v-if="schedules.length" class="schedule-list">
          <article v-for="schedule in schedules.slice(0, 6)" :key="schedule.id">
            <span class="schedule-mark" :class="{ enabled: schedule.enabled }">{{ schedule.enabled ? 'ON' : 'OFF' }}</span>
            <div><strong>{{ schedule.name }}</strong><small>{{ scheduleText(schedule) }}</small><p>{{ schedule.payload.message }}</p></div>
            <time>{{ nextRunText(schedule) }}</time>
          </article>
        </div>
        <div v-else class="empty-state compact"><span>◷</span><strong>没有调度任务</strong><p>Agent 可通过 cron 工具创建用户级计划任务。</p></div>
      </section>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, onUnmounted, reactive, ref } from 'vue'
import { getAdminOverview, getAdminWorkers, listAdminRuns, type AdminOverview, type RuntimeWorker } from '../api/admin'
import { getIdentity, getOperationalMetrics, getSchedules, getServiceHealth, type OperationalMetrics, type RuntimeIdentity, type ScheduleItem, type ServiceHealth } from '../api/monitoring'
import { getRuntimeUserId } from '../api/identity'
import type { RuntimeRun } from '../api/runtime'

const runtimeUserId = getRuntimeUserId()
const loading = ref(false)
const error = ref('')
const lastUpdated = ref<Date | null>(null)
const runs = ref<RuntimeRun[]>([])
const schedules = ref<ScheduleItem[]>([])
const workerList = ref<RuntimeWorker[]>([])
const platform = reactive<AdminOverview>({ runs: 0, users: 0, sessions: 0, active_runs: 0, workers: 0, healthy_workers: 0, statuses: {}, usage: { input_tokens: 0, output_tokens: 0, total_tokens: 0, cost_usd: 0 } })
const identity = reactive<RuntimeIdentity>({
  subject: '',
  user_id: runtimeUserId,
  actor_user_id: runtimeUserId,
  impersonating: false,
  role: '',
  permissions: [],
  is_admin: false,
})
const health = reactive<ServiceHealth>({ api: false, database: false, databaseDetail: null })
const metrics = reactive<OperationalMetrics>({ runs: {}, tasks: {}, workers: {}, providers: [], channels: [], queue: { queued: 0, oldest_age_seconds: 0, expired_leases: 0, retried_tasks: 0 }, workers_stale: 0 })
let timer: number | null = null

const databaseLabel = computed(() => String(health.databaseDetail?.backend ?? health.databaseDetail?.database ?? 'PostgreSQL / Store ready'))
const healthyWorkers = computed(() => workerList.value.filter((worker) => worker.healthy))

async function refresh() {
  if (loading.value) return
  loading.value = true
  error.value = ''
  const results = await Promise.allSettled([getServiceHealth(), getIdentity(), getAdminOverview(), getAdminWorkers(), listAdminRuns({ limit: 100 }), getSchedules(), getOperationalMetrics()])
  const [healthResult, identityResult, platformResult, workersResult, runsResult, schedulesResult, metricsResult] = results
  if (healthResult.status === 'fulfilled') Object.assign(health, healthResult.value)
  if (identityResult.status === 'fulfilled') Object.assign(identity, identityResult.value)
  if (platformResult.status === 'fulfilled') Object.assign(platform, platformResult.value)
  if (workersResult.status === 'fulfilled') workerList.value = workersResult.value
  if (runsResult.status === 'fulfilled') runs.value = runsResult.value.items
  if (schedulesResult.status === 'fulfilled') schedules.value = schedulesResult.value
  if (metricsResult.status === 'fulfilled') Object.assign(metrics, metricsResult.value)
  const rejected = results.find((item) => item.status === 'rejected') as PromiseRejectedResult | undefined
  if (rejected) error.value = rejected.reason instanceof Error ? rejected.reason.message : '部分监控数据读取失败'
  lastUpdated.value = new Date()
  loading.value = false
}

function statusLabel(status: string) { return ({ queued: '排队', running: '运行中', completed: '完成', failed: '失败', cancelled: '取消', timed_out: '超时', paused: '暂停', waiting_input: '等待输入' } as Record<string, string>)[status] ?? status }
function taskProgress(run: RuntimeRun) { return Math.round(((run.completed_task_count ?? 0) / Math.max(1, run.total_task_count ?? 0)) * 100) }
function compactNumber(value = 0) { return new Intl.NumberFormat('zh-CN', { notation: value >= 10_000 ? 'compact' : 'standard', maximumFractionDigits: 1 }).format(value) }
function formatAge(seconds = 0) { if (seconds < 60) return `${Math.round(seconds)} 秒`; if (seconds < 3600) return `${Math.floor(seconds / 60)} 分钟`; return `${(seconds / 3600).toFixed(1)} 小时` }
function formatClock(value: Date) { return value.toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit', second: '2-digit' }) }
function relativeTime(value?: string) { if (!value) return '—'; const delta = Date.now() - new Date(value).getTime(); if (delta < 60_000) return '刚刚'; if (delta < 3_600_000) return `${Math.floor(delta / 60_000)} 分钟前`; if (delta < 86_400_000) return `${Math.floor(delta / 3_600_000)} 小时前`; return new Date(value).toLocaleDateString('zh-CN') }
function workerRole(worker: RuntimeWorker) { if (worker.capabilities?.scheduler) return 'Scheduler 调度节点'; if (worker.capabilities?.agent) return 'Agent 执行节点'; return 'Runtime Worker' }
function workerSlots(worker: RuntimeWorker) { return Number(worker.metadata?.task_worker_count || 0) || '—' }
function workerPlugin(worker: RuntimeWorker) { const extensions = Array.isArray(worker.metadata?.extensions) ? worker.metadata.extensions as Array<Record<string, unknown>> : []; return String(extensions[0]?.name || '核心运行时') }
function scheduleText(item: ScheduleItem) { const monitor = item.payload.kind === 'agent_monitor'; const managed = item.payload.managed_by === 'agent_revision' ? '托管 · ' : ''; const light = monitor && item.payload.context_mode === 'light' ? 'Light · ' : ''; const guard = monitor && item.payload.preflight_mode === 'runtime_attention' ? '变化触发 · ' : ''; const hours = item.payload.active_hours ? `${item.payload.active_hours.start}–${item.payload.active_hours.end} ${item.payload.active_hours.timezone} · ` : ''; const prefix = monitor ? `Agent Monitor · ${managed}${light}${guard}${hours}` : ''; if (item.schedule.kind === 'cron') return `${prefix}${item.schedule.expr || 'cron'}`; if (item.schedule.kind === 'every') return `${prefix}每 ${Math.round(Number(item.schedule.every_ms || 0) / 1000)} 秒`; return `${prefix}单次执行` }
function nextRunText(item: ScheduleItem) { const value = item.state?.next_run_at_ms; return item.enabled && value ? new Date(value).toLocaleString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' }) : '未计划' }

onMounted(() => { void refresh(); timer = window.setInterval(refresh, 10_000) })
onUnmounted(() => { if (timer) window.clearInterval(timer) })
</script>
