import { apiFetch } from './http'
import { getIdentityHeaders } from './identity'

export interface WorkVersion {
  version: number
  media_type: string
  content?: unknown
  uri?: string | null
  content_sha256: string
  change_note: string
  source_run_id?: string
  source_artifact_id?: string
}

export interface Work {
  work_id: string
  owner_user_id: string
  public_slug: string
  title: string
  description: string
  status: 'draft' | 'published' | 'archived'
  visibility: 'private' | 'unlisted' | 'public'
  data_classification: 'public' | 'internal' | 'confidential' | 'restricted'
  current_version: number
  published_version?: number | null
  metadata: Record<string, unknown>
  updated_at: string
  published_at?: string | null
  version?: WorkVersion | null
}

export interface WorkShare {
  share_id: string
  work_id: string
  version: number
  permission: 'view' | 'download'
  status: string
  expires_at?: string | null
  token?: string
  path?: string
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers)
  Object.entries(getIdentityHeaders()).forEach(([key, value]) => headers.set(key, value))
  const response = await apiFetch(`/v1${path}`, { ...init, headers })
  const payload = response.status === 204 ? null : await response.json().catch(() => ({}))
  if (!response.ok) throw new Error(payload?.error?.message ?? payload?.detail ?? '作品 API 调用失败')
  return payload as T
}

export async function listWorks(): Promise<Work[]> { return (await request<{ items: Work[] }>('/works')).items }
export async function getWork(id: string): Promise<Work> { return request(`/works/${encodeURIComponent(id)}`) }
export async function createWork(value: Record<string, unknown>): Promise<Work> {
  return request('/works', { method: 'POST', headers: { 'Content-Type': 'application/json', 'Idempotency-Key': crypto.randomUUID() }, body: JSON.stringify(value) })
}
export async function updateWork(id: string, value: Record<string, unknown>): Promise<Work> {
  return request(`/works/${encodeURIComponent(id)}`, { method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(value) })
}
export async function createWorkVersion(id: string, value: Record<string, unknown>): Promise<Work> {
  return request(`/works/${encodeURIComponent(id)}/versions`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(value) })
}
export async function listWorkShares(id: string): Promise<WorkShare[]> { return (await request<{ items: WorkShare[] }>(`/works/${encodeURIComponent(id)}/shares`)).items }
export async function createWorkShare(id: string, value: Record<string, unknown>): Promise<WorkShare> {
  return request(`/works/${encodeURIComponent(id)}/shares`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(value) })
}
export async function revokeWorkShare(id: string, shareId: string): Promise<WorkShare> {
  return request(`/works/${encodeURIComponent(id)}/shares/${encodeURIComponent(shareId)}/revoke`, { method: 'POST' })
}
export async function listWorkAudit(id: string): Promise<Array<Record<string, unknown>>> { return (await request<{ items: Array<Record<string, unknown>> }>(`/works/${encodeURIComponent(id)}/audit`)).items }
export async function listWorkCollaborators(id: string): Promise<Array<Record<string, string>>> { return (await request<{ items: Array<Record<string, string>> }>(`/works/${encodeURIComponent(id)}/collaborators`)).items }
export async function grantWorkCollaborator(id: string, userId: string, role: 'viewer' | 'editor'): Promise<Record<string, string>> {
  return request(`/works/${encodeURIComponent(id)}/collaborators/${encodeURIComponent(userId)}`, { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ role }) })
}
export async function revokeWorkCollaborator(id: string, userId: string): Promise<void> { await request(`/works/${encodeURIComponent(id)}/collaborators/${encodeURIComponent(userId)}`, { method: 'DELETE' }) }
