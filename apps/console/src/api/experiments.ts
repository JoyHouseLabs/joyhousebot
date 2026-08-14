import { apiFetch } from './http'
import { getIdentityHeaders } from './identity'

export interface Experiment {
  experiment_id: string
  name: string
  description: string
  target_type: 'agent'
  status: 'draft' | 'running' | 'paused' | 'stopped'
  traffic_basis_points: number
  variants: Array<{ variant_id: string; target_id: string; target_revision_id: string; weight_basis_points: number }>
  guardrails: Record<string, unknown>
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers)
  Object.entries(getIdentityHeaders()).forEach(([key, value]) => headers.set(key, value))
  const response = await apiFetch(`/v1/admin/experiments${path}`, { ...init, headers })
  const payload = await response.json().catch(() => ({}))
  if (!response.ok) throw new Error(payload?.error?.message ?? payload?.detail ?? 'Experiment API 调用失败')
  return payload as T
}

export async function listExperiments(): Promise<Experiment[]> {
  return (await request<{ items: Experiment[] }>('/')).items
}

export async function saveExperiment(value: Record<string, unknown>): Promise<Experiment> {
  return request(`/${encodeURIComponent(String(value.experiment_id))}`, {
    method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(value),
  })
}

export async function startExperiment(experimentId: string): Promise<Experiment> {
  return request(`/${encodeURIComponent(experimentId)}/start`, { method: 'POST' })
}

export async function setExperimentStatus(experimentId: string, status: 'paused' | 'stopped'): Promise<Experiment> {
  return request(`/${encodeURIComponent(experimentId)}/status`, {
    method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ status }),
  })
}

export async function experimentSummary(experimentId: string): Promise<Record<string, unknown>> {
  return request(`/${encodeURIComponent(experimentId)}/summary`)
}
