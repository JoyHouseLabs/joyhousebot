import { apiFetch } from './http'
import { getIdentityHeaders } from './identity'

const identityHeaders = getIdentityHeaders

export interface RuntimeUsageSummary {
  runs: number
  input_tokens: number
  output_tokens: number
  total_tokens: number
  cost_usd: number
}

export interface RuntimeIdentity {
  subject: string
  user_id: string
  actor_user_id: string
  impersonating: boolean
  role: string
  permissions: string[]
  is_admin: boolean
}

export interface ScheduleItem {
  id: string
  name: string
  user_id: string
  agent_id: string
  enabled: boolean
  schedule: { kind: string; at_ms?: number | null; every_ms?: number | null; expr?: string | null; tz?: string | null }
  payload: {
    kind?: 'agent_turn' | 'agent_monitor'
    message: string
    deliver?: boolean
    channel?: string | null
    to?: string | null
    session_mode?: 'isolated' | 'main'
    session_id?: string | null
    quiet_token?: string
    defer_when_busy?: boolean
    busy_backoff_ms?: number
    preflight_mode?: 'always' | 'runtime_attention'
    context_mode?: 'full' | 'light'
    active_hours?: { start: string; end: string; timezone: string } | null
    managed_by?: 'user' | 'agent_revision'
    managed_revision_id?: string | null
  }
  state: { next_run_at_ms?: number | null; last_run_at_ms?: number | null; last_status?: string | null }
}

export interface ServiceHealth {
  api: boolean
  database: boolean
  databaseDetail: Record<string, unknown> | null
}

export interface OperationalMetrics {
  runs: Record<string, number>
  tasks: Record<string, number>
  workers: Record<string, number>
  providers: Array<{ provider: string; model: string; status: string; count: number; avg_duration_ms: number; avg_ttft_ms: number; p95_duration_ms: number; p95_ttft_ms: number; cost_usd: number }>
  channels: Array<{ channel: string; status: string; count: number }>
  queue: { queued: number; oldest_age_seconds: number; expired_leases: number; retried_tasks: number }
  workers_stale: number
}

async function jsonOrThrow<T>(response: Response, fallback: string): Promise<T> {
  const payload = await response.json().catch(() => ({}))
  if (!response.ok) throw new Error(payload?.error?.message ?? payload?.detail ?? fallback)
  return payload as T
}

export async function getUsage(): Promise<RuntimeUsageSummary> {
  return jsonOrThrow(await apiFetch('/v1/usage', { headers: identityHeaders() }), '读取用量失败')
}

export async function getIdentity(): Promise<RuntimeIdentity> {
  return jsonOrThrow(await apiFetch('/v1/me', { headers: identityHeaders() }), '读取身份失败')
}

export async function getSchedules(): Promise<ScheduleItem[]> {
  const payload = await jsonOrThrow<{ items: ScheduleItem[] }>(
    await apiFetch('/v1/schedules?include_disabled=true', { headers: identityHeaders() }),
    '读取调度失败',
  )
  return payload.items ?? []
}

export async function getServiceHealth(): Promise<ServiceHealth> {
  const [health, ready] = await Promise.allSettled([fetch('/healthz'), fetch('/readyz')])
  const api = health.status === 'fulfilled' && health.value.ok
  const database = ready.status === 'fulfilled' && ready.value.ok
  let databaseDetail: Record<string, unknown> | null = null
  if (ready.status === 'fulfilled') {
    databaseDetail = await ready.value.json().catch(() => null)
  }
  return { api, database, databaseDetail }
}

export async function getOperationalMetrics(): Promise<OperationalMetrics> {
  return jsonOrThrow(await apiFetch('/v1/system/metrics', { headers: identityHeaders() }), '读取运行指标失败')
}
