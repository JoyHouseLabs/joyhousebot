const API_BASE = '/api'

export interface CronSchedule {
  kind: 'at' | 'every' | 'cron'
  at_ms?: number | null
  every_ms?: number | null
  expr?: string | null
  tz?: string | null
}

export interface CronJobState {
  next_run_at_ms: number | null
  last_run_at_ms: number | null
  last_status: string | null
  last_error: string | null
}

export interface CronJobPayload {
  kind: string
  message: string
  deliver: boolean
  channel: string | null
  to: string | null
}

export interface CronJobItem {
  id: string
  name: string
  enabled: boolean
  /** OpenClaw agentId: which agent runs this job; null = default agent */
  agent_id: string | null
  schedule: CronSchedule
  payload: CronJobPayload
  state: CronJobState
  created_at_ms: number
  updated_at_ms: number
  delete_after_run: boolean
}

export interface CronListResponse {
  ok: boolean
  jobs: CronJobItem[]
  message?: string
}

export async function listCronJobs(includeDisabled = true): Promise<CronListResponse> {
  const res = await fetch(`${API_BASE}/cron/jobs?include_disabled=${includeDisabled}`)
  if (!res.ok) throw new Error(await res.text())
  return res.json()
}

export interface CronScheduleBody {
  kind: 'at' | 'every' | 'cron'
  at_ms?: number | null
  every_ms?: number | null
  every_seconds?: number | null
  expr?: string | null
  tz?: string | null
}

/** OpenClaw-aligned: sessionTarget = payload.kind, agent_id = which agent runs the job */
export interface CronJobCreateBody {
  name: string
  schedule: CronScheduleBody
  message?: string
  deliver?: boolean
  channel?: string | null
  to?: string | null
  delete_after_run?: boolean
  /** Which agent runs this job; omit = default agent */
  agent_id?: string | null
}

export async function addCronJob(body: CronJobCreateBody): Promise<{ ok: boolean; job: CronJobItem }> {
  const res = await fetch(`${API_BASE}/cron/jobs`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
  if (!res.ok) throw new Error(await res.text())
  return res.json()
}

export async function patchCronJob(jobId: string, enabled: boolean): Promise<{ ok: boolean; job: CronJobItem }> {
  const res = await fetch(`${API_BASE}/cron/jobs/${encodeURIComponent(jobId)}`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ enabled }),
  })
  if (!res.ok) throw new Error(await res.text())
  return res.json()
}

export async function deleteCronJob(jobId: string): Promise<{ ok: boolean; removed: boolean }> {
  const res = await fetch(`${API_BASE}/cron/jobs/${encodeURIComponent(jobId)}`, { method: 'DELETE' })
  if (!res.ok) throw new Error(await res.text())
  return res.json()
}

export async function runCronJob(jobId: string, force = false): Promise<{ ok: boolean; message: string }> {
  const res = await fetch(
    `${API_BASE}/cron/jobs/${encodeURIComponent(jobId)}/run?force=${force}`,
    { method: 'POST' }
  )
  if (!res.ok) throw new Error(await res.text())
  return res.json()
}
