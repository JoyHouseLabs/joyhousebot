import { apiFetch } from './http'
import { getIdentityHeaders } from './identity'

export interface PromptSummary {
  prompt_id: string
  name: string
  description: string
  status: string
  tags: string[]
  current_revision_id?: string | null
  current?: { revision_id: string; version: number; status: string; content_sha256: string } | null
}

export interface PromptRevision {
  prompt_id: string
  revision_id: string
  version: number
  name: string
  description: string
  status: string
  content: string
  input_schema: Record<string, unknown>
  output_contract: Record<string, unknown>
  tags: string[]
  change_note: string
  content_sha256: string
  validation_report: Record<string, unknown>
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers)
  Object.entries(getIdentityHeaders()).forEach(([key, value]) => headers.set(key, value))
  const response = await apiFetch(`/v1/admin/prompts${path}`, { ...init, headers })
  const payload = await response.json().catch(() => ({}))
  if (!response.ok) throw new Error(payload?.error?.message ?? payload?.detail ?? 'Prompt API 调用失败')
  return payload as T
}

export async function listPrompts(): Promise<PromptSummary[]> {
  return (await request<{ items: PromptSummary[] }>('/')).items
}

export async function getPrompt(promptId: string): Promise<PromptSummary & { revisions: PromptRevision[] }> {
  return request(`/${encodeURIComponent(promptId)}`)
}

export async function savePrompt(value: Record<string, unknown>): Promise<PromptRevision> {
  return request(`/${encodeURIComponent(String(value.prompt_id))}/versions/${Number(value.version)}`, {
    method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(value),
  })
}

export async function validatePrompt(promptId: string, version: number): Promise<Record<string, unknown>> {
  return request(`/${encodeURIComponent(promptId)}/versions/${version}/validate`, { method: 'POST' })
}

export async function publishPrompt(promptId: string, version: number): Promise<PromptRevision> {
  return request(`/${encodeURIComponent(promptId)}/versions/${version}/publish`, { method: 'POST' })
}

export async function bindPrompt(value: Record<string, unknown>): Promise<Record<string, unknown>> {
  return request('/bindings', { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(value) })
}
