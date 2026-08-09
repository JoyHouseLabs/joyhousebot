import type { RuntimeRun, ScheduleSummary } from './api'

export const terminalStatuses = new Set(['completed', 'failed', 'cancelled', 'timed_out'])

export function isActiveRun(run: RuntimeRun): boolean {
  return !terminalStatuses.has(run.status)
}

export function statusLabel(status: string): string {
  return ({
    queued: '准备中',
    pending: '准备中',
    running: '执行中',
    waiting_input: '等你补充',
    waiting_approval: '等你确认',
    completed: '已完成',
    failed: '需要处理',
    cancelled: '已取消',
    timed_out: '已超时',
  } as Record<string, string>)[status] || status
}

export function dateLabel(value?: string | null): string {
  if (!value) return '刚刚'
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return value
  return new Intl.DateTimeFormat('zh-CN', {
    month: 'numeric', day: 'numeric', hour: '2-digit', minute: '2-digit',
  }).format(date)
}

export function compactId(value: string): string {
  return value.length > 12 ? `${value.slice(0, 8)}…${value.slice(-4)}` : value
}

export function scheduleLabel(item: ScheduleSummary): string {
  if (item.schedule.kind === 'every') {
    const minutes = Math.max(1, Math.round(Number(item.schedule.every_ms || 0) / 60_000))
    return minutes >= 60 && minutes % 60 === 0 ? `每 ${minutes / 60} 小时` : `每 ${minutes} 分钟`
  }
  if (item.schedule.kind === 'cron') return item.schedule.expr || 'Cron'
  return item.schedule.at_ms ? new Date(item.schedule.at_ms).toLocaleString('zh-CN') : '单次执行'
}
