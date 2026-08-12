import { apiFetch } from './http'
import { getIdentityHeaders } from './identity'

export type KnowledgeSourceType = 'url' | 'note' | 'web' | 'file' | 'image' | 'video' | 'email' | 'capture' | 'paper' | 'report'

export interface KnowledgeDocumentListItem {
  doc_id: string
  agent_id?: string | null
  source_type: KnowledgeSourceType | string
  source_url: string
  title: string
  source_system: string
  source_id: string
  source_version: string
  source_generation: number
  source_status: 'inbox' | 'active' | 'archived'
  content_sha256: string
  active_revision_id?: string | null
  index_status: 'indexing' | 'ready' | 'failed' | string
  metadata: Record<string, unknown>
  knowledge_base_ids: string[]
  chunk_count: number
  size_bytes: number
  created_at_ms: number
  updated_at_ms: number
}

export interface KnowledgeIndexRevision {
  revision_id: string
  doc_id: string
  source_version: string
  source_generation: number
  content_sha256: string
  index_profile_id: string
  parser_id: string
  parser_version: string
  chunker_id: string
  chunker_version: string
  embedding_profile_id?: string | null
  status: 'staging' | 'ready' | 'active' | 'superseded' | 'failed'
  run_id?: string | null
  error_code?: string | null
  error_message?: string | null
  chunk_count: number
  created_at_ms: number
  activated_at_ms?: number | null
}

export interface KnowledgeChunk {
  chunk_index: number
  revision_id: string
  page?: number | null
  section_path: string[]
  block_type: string
  char_start?: number | null
  char_end?: number | null
  content_sha256: string
  content: string
  created_at_ms: number
}

export interface KnowledgeDocument extends KnowledgeDocumentListItem {
  chunks: KnowledgeChunk[]
}

export interface KnowledgeSummary {
  bases: number
  total: number
  chunks: number
  size_bytes: number
  by_source: Record<string, number>
}

export interface KnowledgeIndexHealth {
  since_ms: number
  documents: {
    total: number
    ready: number
    indexing: number
    failed: number
    archived: number
    last_ready_at_ms?: number | null
  }
  revisions: { total: number; succeeded: number; failed: number; queue_depth: number }
  window: {
    total: number
    succeeded: number
    failed: number
    success_rate?: number | null
    avg_duration_ms?: number | null
    p95_duration_ms?: number | null
  }
  failure_codes: Array<{ error_code: string; count: number; last_failed_at_ms: number }>
}

export interface KnowledgeBase {
  knowledge_base_id: string
  name: string
  description: string
  status: 'active' | 'archived'
  created_by: string
  document_count: number
  chunk_count: number
  size_bytes: number
  created_at_ms: number
  updated_at_ms: number
}

async function knowledgeFetch<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers)
  for (const [key, value] of Object.entries(getIdentityHeaders())) headers.set(key, value)
  const response = await apiFetch(`/v1/knowledge${path}`, { ...init, headers })
  if (response.status === 204) return undefined as T
  const payload = await response.json().catch(() => ({}))
  if (!response.ok) throw new Error(payload?.detail ?? payload?.error?.message ?? '知识资产读取失败')
  return payload as T
}

export function getKnowledgeDocuments(
  filters: { knowledgeBaseId?: string; sourceType?: KnowledgeSourceType | 'all'; search?: string; limit?: number } = {},
) {
  const query = new URLSearchParams()
  if (filters.knowledgeBaseId) query.set('knowledge_base_id', filters.knowledgeBaseId)
  query.set('source_type', filters.sourceType || 'all')
  if (filters.search) query.set('search', filters.search)
  query.set('limit', String(filters.limit ?? 200))
  return knowledgeFetch<{ items: KnowledgeDocumentListItem[]; summary: KnowledgeSummary }>(`/documents?${query}`)
}

export function getKnowledgeDocument(docId: string) {
  return knowledgeFetch<KnowledgeDocument>(`/documents/${encodeURIComponent(docId)}`)
}

export function getKnowledgeIndexHealth(windowDays = 30) {
  return knowledgeFetch<KnowledgeIndexHealth>(`/health?window_days=${windowDays}`)
}

export async function getKnowledgeDocumentRevisions(docId: string) {
  const result = await knowledgeFetch<{ items: KnowledgeIndexRevision[] }>(
    `/documents/${encodeURIComponent(docId)}/revisions`,
  )
  return result.items
}

export function deleteKnowledgeDocument(docId: string) {
  return knowledgeFetch<void>(`/documents/${encodeURIComponent(docId)}`, { method: 'DELETE' })
}

export async function getKnowledgeBases(status: KnowledgeBase['status'] | 'all' = 'all') {
  const result = await knowledgeFetch<{ items: KnowledgeBase[] }>(`/bases?status=${encodeURIComponent(status)}`)
  return result.items
}

export function createKnowledgeBase(value: { name: string; description?: string }) {
  return knowledgeFetch<KnowledgeBase>('/bases', {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(value),
  })
}

export function updateKnowledgeBase(
  knowledgeBaseId: string,
  value: { name?: string; description?: string; status?: KnowledgeBase['status'] },
) {
  return knowledgeFetch<KnowledgeBase>(`/bases/${encodeURIComponent(knowledgeBaseId)}`, {
    method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(value),
  })
}

export function deleteKnowledgeBase(knowledgeBaseId: string) {
  return knowledgeFetch<void>(`/bases/${encodeURIComponent(knowledgeBaseId)}`, { method: 'DELETE' })
}

export function addKnowledgeDocumentToBase(knowledgeBaseId: string, docId: string) {
  return knowledgeFetch<{ bound: true; created: boolean }>(`/bases/${encodeURIComponent(knowledgeBaseId)}/documents/${encodeURIComponent(docId)}`, { method: 'PUT' })
}

export function removeKnowledgeDocumentFromBase(knowledgeBaseId: string, docId: string) {
  return knowledgeFetch<void>(`/bases/${encodeURIComponent(knowledgeBaseId)}/documents/${encodeURIComponent(docId)}`, { method: 'DELETE' })
}
