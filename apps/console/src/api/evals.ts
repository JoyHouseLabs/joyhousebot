import { apiFetch } from './http'
import { getIdentityHeaders } from './identity'

export interface EvalSuite {
  suite_id: string
  version: number
  name: string
  description: string
  status: string
  target_types: string[]
  thresholds: Record<string, number>
  case_count: number
  cases?: Array<Record<string, unknown>>
}

export interface EvalRun {
  eval_run_id: string
  suite_id: string
  suite_version: number
  target_type: string
  target_id: string
  target_revision_id: string
  status: string
  metrics: Record<string, number>
  results: Array<Record<string, unknown>>
  created_at: string
  completed_at?: string | null
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers)
  Object.entries(getIdentityHeaders()).forEach(([key, value]) => headers.set(key, value))
  const response = await apiFetch(`/v1/admin${path}`, { ...init, headers })
  const payload = await response.json().catch(() => ({}))
  if (!response.ok) throw new Error(payload?.error?.message ?? payload?.detail ?? '评测 API 调用失败')
  return payload as T
}

export async function listEvalSuites(): Promise<EvalSuite[]> {
  return (await request<{ items: EvalSuite[] }>('/eval-suites')).items
}

export async function saveEvalSuite(value: Record<string, unknown>): Promise<EvalSuite> {
  return request(`/eval-suites/${encodeURIComponent(String(value.suite_id))}/versions/${Number(value.version)}`, {
    method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(value),
  })
}

export async function listEvalRuns(): Promise<EvalRun[]> {
  return (await request<{ items: EvalRun[] }>('/eval-runs?limit=300')).items
}

export async function createEvalRun(value: Record<string, unknown>): Promise<EvalRun> {
  return request('/eval-runs', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(value) })
}

export async function recordEvalObservation(evalRunId: string, value: Record<string, unknown>): Promise<Record<string, unknown>> {
  return request(`/eval-runs/${encodeURIComponent(evalRunId)}/observations`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(value) })
}

export async function finalizeEvalRun(evalRunId: string): Promise<EvalRun> {
  return request(`/eval-runs/${encodeURIComponent(evalRunId)}/finalize`, { method: 'POST' })
}

export async function saveReleaseGate(targetType: string, targetId: string, revisionId: string, value: Record<string, unknown>): Promise<Record<string, unknown>> {
  return request(`/release-gates/${encodeURIComponent(targetType)}/${encodeURIComponent(targetId)}/${encodeURIComponent(revisionId)}`, {
    method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(value),
  })
}
