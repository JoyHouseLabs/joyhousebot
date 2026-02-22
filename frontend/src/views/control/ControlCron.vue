<template>
  <div class="control-page">
    <div class="content-header">
      <h1 class="page-title">定时任务</h1>
      <p class="page-sub">OpenClaw 风格：调度(schedule)、会话目标、投递(delivery)、单次删除</p>
    </div>
    <n-spin :show="cronLoading">
      <div class="cron-toolbar">
        <n-button type="primary" size="small" @click="goToNew">新建任务</n-button>
        <n-button quaternary size="small" @click="loadCronJobs">刷新</n-button>
      </div>
      <n-data-table
        :columns="cronColumns"
        :data="cronJobs"
        :bordered="false"
        size="small"
        class="cron-table"
        :scroll-x="1000"
      />
    </n-spin>
  </div>
</template>

<script setup lang="ts">
import { h, ref, onMounted, watch } from 'vue'
import { useRouter } from 'vue-router'
import { NButton, NTag, useMessage, useDialog } from 'naive-ui'
import {
  listCronJobs,
  patchCronJob,
  deleteCronJob,
  runCronJob,
  type CronJobItem,
} from '../../api/cron'
import { useGatewayInject } from '../../composables/useGateway'

const router = useRouter()
const message = useMessage()
const dialog = useDialog()
const gateway = useGatewayInject()
const cronJobs = ref<CronJobItem[]>([])
const cronLoading = ref(false)

function goToNew() {
  router.push({ name: 'ControlCronNew' })
}

function formatNextRun(ms: number): string {
  try {
    return new Date(ms).toLocaleString()
  } catch {
    return String(ms)
  }
}

async function loadCronJobs() {
  cronLoading.value = true
  try {
    const res = await listCronJobs(true)
    cronJobs.value = res.jobs ?? []
  } catch (e) {
    message.error(String(e))
  } finally {
    cronLoading.value = false
  }
}

/** OpenClaw: payload.kind → 会话目标 */
function sessionTargetLabel(kind: string): string {
  return kind === 'system_event' ? '主会话' : '独立会话'
}

/** OpenClaw: 投递 delivery (channel + to) */
function deliverySummary(payload: CronJobItem['payload']): string {
  if (!payload.deliver) return '无'
  const parts = [payload.channel || '', payload.to || ''].filter(Boolean)
  return parts.length ? parts.join(' / ') : '已启用'
}

/** 执行者：OpenClaw agentId，空为默认 Agent */
function agentDisplay(agentId: string | null | undefined): string {
  return (agentId && agentId.trim()) ? agentId : '默认'
}

const cronColumns = [
  { title: '名称', key: 'name', width: 120, ellipsis: { tooltip: true } },
  { title: '执行者', key: 'agent_id', width: 88, render: (row: CronJobItem) => agentDisplay(row.agent_id) },
  {
    title: '调度',
    key: 'schedule',
    width: 150,
    render: (row: CronJobItem) => {
      const s = row.schedule
      if (s.kind === 'every' && s.every_ms) return `每 ${(s.every_ms / 1000).toFixed(0)} 秒`
      if (s.kind === 'cron' && s.expr) return (s.tz ? `[${s.tz}] ` : '') + s.expr
      if (s.kind === 'at' && s.at_ms) return new Date(s.at_ms).toLocaleString()
      return s.kind
    },
  },
  { title: '会话', key: 'session', width: 80, render: (row: CronJobItem) => sessionTargetLabel(row.payload?.kind ?? 'agent_turn') },
  { title: '投递', key: 'delivery', width: 120, ellipsis: { tooltip: true }, render: (row: CronJobItem) => deliverySummary(row.payload || { kind: '', message: '', deliver: false, channel: null, to: null }) },
  { title: '时区', key: 'tz', width: 100, ellipsis: { tooltip: true }, render: (row: CronJobItem) => row.schedule?.tz || '—' },
  {
    title: '下次运行',
    key: 'next_run',
    width: 155,
    render: (row: CronJobItem) =>
      row.state?.next_run_at_ms
        ? formatNextRun(row.state.next_run_at_ms)
        : '—',
  },
  {
    title: '上次状态',
    key: 'last_status',
    width: 80,
    render: (row: CronJobItem) => {
      const s = row.state?.last_status
      if (!s) return '—'
      if (s === 'ok') return h(NTag, { type: 'success', size: 'small' }, () => 'ok')
      if (s === 'error') return h(NTag, { type: 'error', size: 'small' }, () => 'error')
      return s
    },
  },
  { title: '单次删', key: 'delete_after_run', width: 68, render: (row: CronJobItem) => row.delete_after_run ? '是' : '否' },
  {
    title: '启用',
    key: 'enabled',
    width: 60,
    render: (row: CronJobItem) =>
      row.enabled ? h(NTag, { type: 'success', size: 'small' }, () => '是') : h(NTag, { size: 'small' }, () => '否'),
  },
  {
    title: '操作',
    key: 'actions',
    width: 200,
    render: (row: CronJobItem) =>
      h('div', { class: 'cron-actions' }, [
        h(
          NButton,
          { size: 'tiny', quaternary: true, onClick: () => runJob(row.id) },
          () => '运行'
        ),
        h(
          NButton,
          {
            size: 'tiny',
            quaternary: true,
            onClick: () => toggleJob(row.id, !row.enabled),
          },
          () => (row.enabled ? '禁用' : '启用')
        ),
        h(
          NButton,
          {
            size: 'tiny',
            quaternary: true,
            type: 'error',
            onClick: () => confirmRemoveJob(row.id, row.name),
          },
          () => '删除'
        ),
      ]),
  },
]

async function runJob(id: string) {
  try {
    await runCronJob(id, true)
    message.success('已触发运行')
    await loadCronJobs()
    await gateway?.request?.('health').catch(() => {})
  } catch (e) {
    message.error(String(e))
  }
}

async function toggleJob(id: string, enabled: boolean) {
  try {
    await patchCronJob(id, enabled)
    message.success(enabled ? '已启用' : '已禁用')
    await loadCronJobs()
    await gateway?.request?.('health').catch(() => {})
  } catch (e) {
    message.error(String(e))
  }
}

function confirmRemoveJob(id: string, name: string) {
  dialog.warning({
    title: '删除定时任务',
    content: `确定要删除「${name}」吗？`,
    positiveText: '删除',
    negativeText: '取消',
    positiveButtonProps: { type: 'error' },
    onPositiveClick: async () => {
      try {
        await deleteCronJob(id)
        message.success('已删除')
        await loadCronJobs()
        await gateway?.request?.('health').catch(() => {})
      } catch (e) {
        message.error(String(e))
      }
    },
  })
}

onMounted(loadCronJobs)
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
.cron-toolbar {
  display: flex;
  gap: 8px;
  margin-bottom: 12px;
}
.cron-table {
  margin-top: 8px;
}
.cron-actions {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}
.form-hint {
  font-size: 12px;
  color: var(--text-muted, #999);
  margin-left: 8px;
}
</style>
