import { apiFetch } from './http'
import { getIdentityHeaders } from './identity'
import type { ScheduleItem } from './monitoring'

async function jsonOrThrow<T>(response: Response, fallback: string): Promise<T> {
  const payload = await response.json().catch(() => ({}))
  if (!response.ok) throw new Error(payload?.error?.message ?? payload?.detail ?? fallback)
  return payload as T
}

export interface ScheduleOccurrence {
  id: string
  jobId: string
  status: string
  runId?: string | null
  runIds: string[]
  attempt: number
  submitAttempt: number
  scheduledForMs: number
  nextAttemptAtMs?: number | null
  error?: string | null
  deliveryStatus?: string
  deliveryError?: string | null
  startedAtMs: number
  finishedAtMs?: number | null
}

export interface ScheduleWrite {
  name: string
  agent_id: string
  schedule: {
    kind: 'at' | 'every' | 'cron'
    at_ms?: number
    every_ms?: number
    cron_expr?: string
    timezone?: string
  }
  payload: {
    kind?: 'agent_turn'
    message: string
    deliver?: boolean
    channel?: string | null
    to?: string | null
  }
  enabled?: boolean
}

export async function listSchedules(): Promise<ScheduleItem[]> {
  const payload = await jsonOrThrow<{ items: ScheduleItem[] }>(
    await apiFetch('/v1/schedules?include_disabled=true', { headers: getIdentityHeaders() }),
    '读取自动化任务失败',
  )
  return payload.items ?? []
}

export async function createSchedule(value: ScheduleWrite): Promise<ScheduleItem> {
  return jsonOrThrow(
    await apiFetch('/v1/schedules', {
      method: 'POST',
      headers: { ...getIdentityHeaders(), 'Content-Type': 'application/json' },
      body: JSON.stringify(value),
    }),
    '创建自动化任务失败',
  )
}

export async function updateSchedule(
  scheduleId: string,
  value: Partial<ScheduleWrite> & { enabled?: boolean },
): Promise<ScheduleItem> {
  return jsonOrThrow(
    await apiFetch(`/v1/schedules/${encodeURIComponent(scheduleId)}`, {
      method: 'PATCH',
      headers: { ...getIdentityHeaders(), 'Content-Type': 'application/json' },
      body: JSON.stringify(value),
    }),
    '更新自动化任务失败',
  )
}

export async function deleteSchedule(scheduleId: string): Promise<void> {
  const response = await apiFetch(`/v1/schedules/${encodeURIComponent(scheduleId)}`, {
    method: 'DELETE',
    headers: getIdentityHeaders(),
  })
  if (!response.ok) throw await jsonOrThrow(response, '删除自动化任务失败')
}

export async function runScheduleNow(scheduleId: string): Promise<ScheduleOccurrence> {
  return jsonOrThrow(
    await apiFetch(`/v1/schedules/${encodeURIComponent(scheduleId)}/runs`, {
      method: 'POST',
      headers: { ...getIdentityHeaders(), 'Idempotency-Key': crypto.randomUUID() },
    }),
    '提交补跑失败',
  )
}

export async function listScheduleRuns(scheduleId?: string): Promise<ScheduleOccurrence[]> {
  const query = new URLSearchParams({ limit: '100' })
  if (scheduleId) query.set('schedule_id', scheduleId)
  const payload = await jsonOrThrow<{ items: ScheduleOccurrence[] }>(
    await apiFetch(`/v1/schedules/runs?${query}`, { headers: getIdentityHeaders() }),
    '读取触发历史失败',
  )
  return payload.items ?? []
}

export interface EventTrigger {
  trigger_id: string
  name: string
  agent_id: string
  event_type_filter: string
  instruction: string
  session_mode: 'shared' | 'per_event'
  session_id?: string | null
  enabled: boolean
  secret_version: number
  endpoint_path: string
  created_at: string
  updated_at: string
  signing_secret?: string
}

export interface EventTriggerDelivery {
  delivery_id: string
  trigger_id: string
  idempotency_key: string
  payload_hash: string
  event_type: string
  status: 'processing' | 'submitted' | 'failed'
  attempt: number
  run_id?: string | null
  error?: string | null
  received_at: string
  updated_at: string
}

export interface EventTriggerWrite {
  name: string
  agent_id: string
  event_type_filter: string
  instruction: string
  session_mode: 'shared' | 'per_event'
  session_id?: string | null
  enabled?: boolean
}

export async function listEventTriggers(): Promise<EventTrigger[]> {
  const payload = await jsonOrThrow<{ items: EventTrigger[] }>(
    await apiFetch('/v1/event-triggers', { headers: getIdentityHeaders() }),
    '读取 Webhook 规则失败',
  )
  return payload.items ?? []
}

export async function createEventTrigger(value: EventTriggerWrite): Promise<EventTrigger> {
  return jsonOrThrow(
    await apiFetch('/v1/event-triggers', {
      method: 'POST',
      headers: { ...getIdentityHeaders(), 'Content-Type': 'application/json' },
      body: JSON.stringify(value),
    }),
    '创建 Webhook 规则失败',
  )
}

export async function updateEventTrigger(
  triggerId: string,
  value: Partial<EventTriggerWrite>,
): Promise<EventTrigger> {
  return jsonOrThrow(
    await apiFetch(`/v1/event-triggers/${encodeURIComponent(triggerId)}`, {
      method: 'PATCH',
      headers: { ...getIdentityHeaders(), 'Content-Type': 'application/json' },
      body: JSON.stringify(value),
    }),
    '更新 Webhook 规则失败',
  )
}

export async function rotateEventTriggerSecret(triggerId: string): Promise<EventTrigger> {
  return jsonOrThrow(
    await apiFetch(`/v1/event-triggers/${encodeURIComponent(triggerId)}/rotate-secret`, {
      method: 'POST', headers: getIdentityHeaders(),
    }),
    '轮换 Webhook 密钥失败',
  )
}

export async function deleteEventTrigger(triggerId: string): Promise<void> {
  const response = await apiFetch(`/v1/event-triggers/${encodeURIComponent(triggerId)}`, {
    method: 'DELETE', headers: getIdentityHeaders(),
  })
  if (!response.ok) throw await jsonOrThrow(response, '删除 Webhook 规则失败')
}

export async function listEventTriggerDeliveries(
  triggerId?: string,
): Promise<EventTriggerDelivery[]> {
  const query = new URLSearchParams({ limit: '100' })
  if (triggerId) query.set('trigger_id', triggerId)
  const payload = await jsonOrThrow<{ items: EventTriggerDelivery[] }>(
    await apiFetch(`/v1/event-trigger-deliveries?${query}`, { headers: getIdentityHeaders() }),
    '读取 Webhook 投递记录失败',
  )
  return payload.items ?? []
}
