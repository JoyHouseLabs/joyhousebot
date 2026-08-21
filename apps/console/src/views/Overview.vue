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
        <router-link class="primary-button" to="/runs">查看 Runs</router-link>
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
        <span>工作 / 计费 Token</span><strong>{{ compactNumber(platform.usage.total_tokens) }} / {{ compactNumber(platform.usage.billed_total_tokens) }}</strong><small>计费输入 {{ compactNumber(platform.usage.billed_input_tokens) }} · 输出 {{ compactNumber(platform.usage.billed_output_tokens) }}</small>
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
          <span class="worker-summary-extension">{{ workerExtension(worker) }}</span>
          <time>{{ relativeTime(worker.last_heartbeat) }}</time>
        </article>
      </div>
      <div v-else class="empty-state compact"><strong>暂无健康 Worker 心跳</strong></div>
      <p class="worker-summary-note">健康 {{ platform.healthy_workers }} · 历史 / 陈旧记录 {{ Math.max(0, platform.workers - platform.healthy_workers) }} · 角色和扩展信息来自 Worker 心跳。</p>
    </section>

    <section class="panel capacity-panel">
      <div class="panel-heading">
        <div><span class="eyebrow">CAPACITY</span><h2>执行容量</h2><p>实时心跳与 PostgreSQL 聚合；用于调整 Worker 数和单 Worker 槽位。</p></div>
        <span class="capacity-updated">{{ metrics.capacity.reporting_workers }} 个 Worker 上报中</span>
      </div>
      <div class="capacity-grid">
        <article><span>Agent 槽位</span><strong>{{ metrics.capacity.agent_active }} / {{ metrics.capacity.agent_slots }}</strong><small>{{ metrics.capacity.agent_waiting }} 个已领取等待执行</small><i><b :style="{ width: capacityPercent(metrics.capacity.agent_active, metrics.capacity.agent_slots) + '%' }" /></i></article>
        <article><span>Graph 槽位</span><strong>{{ metrics.capacity.graph_active }} / {{ metrics.capacity.graph_slots }}</strong><small>本地缓冲 {{ metrics.capacity.graph_buffered }} 个 Task</small><i><b :style="{ width: capacityPercent(metrics.capacity.graph_active, metrics.capacity.graph_slots) + '%' }" /></i></article>
        <article :class="{ warning: metrics.queue.claim_delay_p95_ms >= 10_000 || metrics.queue.oldest_age_seconds >= 60 }"><span>队列延迟 P95</span><strong>{{ formatDuration(metrics.queue.claim_delay_p95_ms) }}</strong><small>排队 {{ metrics.queue.queued }} · 最久 {{ formatAge(metrics.queue.oldest_age_seconds) }}</small></article>
        <article :class="{ warning: metrics.database_pool.waiting > 0 }"><span>PostgreSQL 连接池</span><strong>{{ metrics.database_pool.size - metrics.database_pool.available }} / {{ metrics.database_pool.max_size }}</strong><small>可用 {{ metrics.database_pool.available }} · 等待 {{ metrics.database_pool.waiting }}</small></article>
        <article><span>Worker 进程</span><strong>{{ metrics.capacity.worker_cpu_percent_avg.toFixed(1) }}% CPU</strong><small>RSS {{ formatBytes(metrics.capacity.worker_rss_bytes) }}（全部 Worker）</small></article>
        <article :class="{ warning: providerFailureRate >= 5 }"><span>模型失败率（24h）</span><strong>{{ providerFailureRate.toFixed(1) }}%</strong><small>{{ providerFailureCount }} / {{ providerInvocationCount }} 次调用失败</small></article>
      </div>
      <div v-if="metrics.provider_errors_24h.length" class="capacity-provider-list"><span v-for="item in metrics.provider_errors_24h.slice(0, 4)" :key="`${item.provider}/${item.model}`" :class="{ warning: item.failure_rate >= 5 }">{{ item.provider }}/{{ item.model }} · {{ item.failure_rate.toFixed(1) }}% 失败</span></div>
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
            <span class="schedule-mark" :class="{ enabled: schedule.enabled && !schedule.paused }">{{ schedule.paused ? 'PAUSE' : schedule.enabled ? 'ON' : 'OFF' }}</span>
            <div><strong>{{ schedule.name }}</strong><small>{{ scheduleText(schedule) }}</small><p>{{ schedule.pause_reason || schedule.payload.message }}</p></div>
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
const platform = reactive<AdminOverview>({ runs: 0, users: 0, sessions: 0, active_runs: 0, workers: 0, healthy_workers: 0, statuses: {}, usage: { input_tokens: 0, output_tokens: 0, total_tokens: 0, billed_input_tokens: 0, billed_output_tokens: 0, billed_total_tokens: 0, cost_usd: 0, missing_usage_invocations: 0, missing_billing_invocations: 0 } })
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
const metrics = reactive<OperationalMetrics>({ runs: {}, tasks: {}, workers: {}, providers: [], channels: [], queue: { queued: 0, oldest_age_seconds: 0, expired_leases: 0, retried_tasks: 0, claim_delay_p95_ms: 0 }, capacity: { reporting_workers: 0, agent_slots: 0, agent_active: 0, agent_waiting: 0, graph_slots: 0, graph_active: 0, graph_buffered: 0, worker_cpu_percent_avg: 0, worker_rss_bytes: 0 }, database_pool: { min_size: 0, max_size: 0, size: 0, available: 0, waiting: 0 }, provider_errors_24h: [], workers_stale: 0 })
let timer: number | null = null

const databaseLabel = computed(() => String(health.databaseDetail?.backend ?? health.databaseDetail?.database ?? 'PostgreSQL / Store ready'))
const healthyWorkers = computed(() => workerList.value.filter((worker) => worker.healthy))
const providerInvocationCount = computed(() => metrics.provider_errors_24h.reduce((total, item) => total + item.total, 0))
const providerFailureCount = computed(() => metrics.provider_errors_24h.reduce((total, item) => total + item.failed, 0))
const providerFailureRate = computed(() => providerFailureCount.value / Math.max(1, providerInvocationCount.value) * 100)

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
function formatDuration(milliseconds = 0) { if (milliseconds < 1_000) return `${Math.round(milliseconds)}ms`; if (milliseconds < 60_000) return `${(milliseconds / 1_000).toFixed(1)} 秒`; return `${(milliseconds / 60_000).toFixed(1)} 分钟` }
function formatBytes(bytes = 0) { if (!bytes) return '—'; if (bytes < 1024 * 1024) return `${Math.round(bytes / 1024)} KB`; return `${(bytes / 1024 / 1024).toFixed(0)} MB` }
function capacityPercent(active: number, slots: number) { return Math.min(100, Math.round(active / Math.max(1, slots) * 100)) }
function formatClock(value: Date) { return value.toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit', second: '2-digit' }) }
function relativeTime(value?: string) { if (!value) return '—'; const delta = Date.now() - new Date(value).getTime(); if (delta < 60_000) return '刚刚'; if (delta < 3_600_000) return `${Math.floor(delta / 60_000)} 分钟前`; if (delta < 86_400_000) return `${Math.floor(delta / 3_600_000)} 小时前`; return new Date(value).toLocaleDateString('zh-CN') }
function workerRole(worker: RuntimeWorker) { if (worker.capabilities?.scheduler) return 'Scheduler 调度节点'; if (worker.capabilities?.agent) return 'Agent 执行节点'; return 'Runtime Worker' }
function workerSlots(worker: RuntimeWorker) { return Number(worker.metadata?.task_worker_count || 0) || '—' }
function workerExtension(worker: RuntimeWorker) { const extensions = Array.isArray(worker.metadata?.extensions) ? worker.metadata.extensions as Array<Record<string, unknown>> : []; return String(extensions[0]?.name || '核心运行时') }
function scheduleText(item: ScheduleItem) { const monitor = item.payload.kind === 'agent_monitor'; const managed = item.payload.managed_by === 'agent_revision' ? '托管 · ' : ''; const light = monitor && item.payload.context_mode === 'light' ? 'Light · ' : ''; const guard = monitor && item.payload.preflight_mode === 'runtime_attention' ? '变化触发 · ' : ''; const hours = item.payload.active_hours ? `${item.payload.active_hours.start}–${item.payload.active_hours.end} ${item.payload.active_hours.timezone} · ` : ''; const prefix = monitor ? `Agent Monitor · ${managed}${light}${guard}${hours}` : ''; if (item.schedule.kind === 'cron') return `${prefix}${item.schedule.expr || 'cron'}`; if (item.schedule.kind === 'every') return `${prefix}每 ${Math.round(Number(item.schedule.every_ms || 0) / 1000)} 秒`; return `${prefix}单次执行` }
function nextRunText(item: ScheduleItem) { const value = item.state?.next_run_at_ms; if (item.paused) return '已熔断'; return item.enabled && value ? new Date(value).toLocaleString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' }) : '未计划' }

onMounted(() => { void refresh(); timer = window.setInterval(refresh, 10_000) })
onUnmounted(() => { if (timer) window.clearInterval(timer) })
</script>

<style scoped>
.capacity-panel { margin-bottom: 18px; }
.capacity-panel .panel-heading p { margin: 5px 0 0; color: var(--text-muted); font-size: 12px; }
.capacity-updated { color: var(--text-muted); font-size: 12px; }
.capacity-grid { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); border-top: 1px solid var(--border); }
.capacity-grid article { display: grid; gap: 5px; min-height: 126px; padding: 17px; border-right: 1px solid var(--border); border-bottom: 1px solid var(--border); }
.capacity-grid article:nth-child(3n) { border-right: 0; }
.capacity-grid article:nth-last-child(-n+3) { border-bottom: 0; }
.capacity-grid span { color: var(--text-muted); font-size: 12px; }
.capacity-grid strong { color: var(--text-strong); font-size: 23px; line-height: 1.15; }
.capacity-grid small { color: var(--text-muted); font-size: 12px; }
.capacity-grid article.warning strong,.capacity-provider-list .warning { color: #d86b21; }
.capacity-grid i { display: block; height: 5px; margin-top: auto; overflow: hidden; border-radius: 999px; background: var(--border); }
.capacity-grid i b { display: block; height: 100%; border-radius: inherit; background: var(--accent); transition: width .2s; }
.capacity-provider-list { display: flex; gap: 8px; flex-wrap: wrap; padding: 13px 17px; color: var(--text-muted); font-size: 12px; }
.capacity-provider-list span { padding: 5px 8px; border: 1px solid var(--border); border-radius: 999px; background: var(--surface-raised); }
@media (max-width: 980px) { .capacity-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); }.capacity-grid article:nth-child(3n) { border-right: 1px solid var(--border); }.capacity-grid article:nth-child(2n) { border-right: 0; }.capacity-grid article:nth-last-child(-n+3) { border-bottom: 1px solid var(--border); }.capacity-grid article:nth-last-child(-n+2) { border-bottom: 0; } }
@media (max-width: 620px) { .capacity-grid { grid-template-columns: 1fr; }.capacity-grid article,.capacity-grid article:nth-child(3n),.capacity-grid article:nth-child(2n) { border-right: 0; border-bottom: 1px solid var(--border); }.capacity-grid article:last-child { border-bottom: 0; } }
</style>
