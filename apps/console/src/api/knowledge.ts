import { apiFetch } from './http'
import { getIdentityHeaders } from './identity'

export type KnowledgeSourceType = 'url' | 'note'

export interface KnowledgeDocumentListItem {
  doc_id: string
  agent_id?: string | null
  source_type: KnowledgeSourceType | string
  source_url: string
  title: string
  metadata: Record<string, unknown>
  knowledge_base_ids: string[]
  chunk_count: number
  size_bytes: number
  created_at_ms: number
  updated_at_ms: number
}

export interface KnowledgeChunk {
  chunk_index: number
  page?: number | null
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
