import { apiFetch } from './http'
import { getIdentityHeaders } from './identity'

export type MemoryLayer = 'profile' | 'long_term' | 'episodic' | 'agent'

export interface MemoryDocumentListItem {
  scope_key: string
  document_path: string
  layer: MemoryLayer
  version: number
  size_bytes: number
  preview: string
  created_at_ms: number
  updated_at_ms: number
}

export interface MemoryDocument extends Omit<MemoryDocumentListItem, 'preview'> {
  content: string
}

export interface MemoryDocumentSummary {
  total: number
  by_layer: Record<MemoryLayer, number>
}

export interface MemoryCandidate {
  candidate_id: string
  agent_id: string
  scope_key: string
  document_path: string
  layer: MemoryLayer
  operation: 'replace' | 'append'
  content: string
  source_run_id?: string | null
  source_kind: string
  fact_type: string
  confidence?: number | null
  data_classification: string
  status: 'pending' | 'merged' | 'rejected' | 'expired' | 'conflicted'
  created_at: string
  expires_at: string
  resolved_at?: string | null
}

async function memoryFetch<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers)
  for (const [key, value] of Object.entries(getIdentityHeaders())) headers.set(key, value)
  const response = await apiFetch(`/v1/memory${path}`, { ...init, headers })
  const payload = await response.json().catch(() => ({}))
  if (!response.ok) throw new Error(payload?.detail ?? payload?.error?.message ?? '记忆数据读取失败')
  return payload as T
}

export function getMemoryDocuments(
  agentId: string,
  filters: { layer?: MemoryLayer | 'all'; search?: string; limit?: number } = {},
) {
  const query = new URLSearchParams({ agent_id: agentId })
  if (filters.layer && filters.layer !== 'all') query.set('layer', filters.layer)
  if (filters.search) query.set('search', filters.search)
  query.set('limit', String(filters.limit ?? 200))
  return memoryFetch<{ items: MemoryDocumentListItem[]; summary: MemoryDocumentSummary }>(`/documents?${query}`)
}

export function getMemoryDocument(agentId: string, item: MemoryDocumentListItem) {
  const query = new URLSearchParams({ agent_id: agentId, scope_key: item.scope_key })
  const path = item.document_path.split('/').map(encodeURIComponent).join('/')
  return memoryFetch<MemoryDocument>(`/documents/${path}?${query}`)
}

export function getMemoryCandidates(
  agentId: string,
  status: MemoryCandidate['status'] | 'all' = 'all',
) {
  const query = new URLSearchParams({ agent_id: agentId, status, limit: '200' })
  return memoryFetch<{ items: MemoryCandidate[] }>(`/candidates?${query}`)
}

export function resolveMemoryCandidate(
  candidateId: string,
  resolution: 'accept' | 'reject',
  note?: string,
) {
  return memoryFetch<MemoryCandidate>(`/candidates/${encodeURIComponent(candidateId)}/resolve`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ resolution, note: note || null }),
  })
}
