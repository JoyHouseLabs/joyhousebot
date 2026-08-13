import { apiFetch } from './http'

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const headers = new Headers(init?.headers)
  if (init?.body !== undefined) headers.set('Content-Type', 'application/json')
  const response = await apiFetch(`/v1/admin/embedding-profiles${path}`, { ...init, headers })
  const payload = await response.json().catch(() => ({}))
  if (!response.ok) throw new Error(payload?.detail || payload?.error?.message || 'Embedding Profile 请求失败')
  return payload as T
}

export interface EmbeddingProfileConfiguration {
  provider_id: string
  provider_revision_id: string
  model_id: string
  dimensions: number
  normalization: 'none' | 'l2'
  batch_size: number
  max_input_tokens: number
  max_cost_usd: number
  requests_per_minute: number
  tokens_per_minute: number
  ann_min_rows: number
  hnsw_m: number
  hnsw_ef_construction: number
  hnsw_ef_search: number
}

export interface EmbeddingProfileRevision {
  profile_id: string
  revision_id: string
  version: number
  status: 'draft' | 'published' | 'retired'
  configuration: EmbeddingProfileConfiguration
  fingerprint: string
  make_default: boolean
  created_by: string
  created_at: string
  published_at?: string | null
}

export interface EmbeddingProfile {
  profile_id: string
  name: string
  description: string
  current_revision_id?: string | null
  current_revision?: EmbeddingProfileRevision | null
  is_default: boolean
  revisions?: EmbeddingProfileRevision[]
}

export interface EmbeddingReadiness {
  ready: boolean
  pgvector: {
    installed: boolean
    installed_version?: string | null
    available_version?: string | null
  }
  default_profile?: {
    profile_id: string
    revision_id: string
    model_id: string
    dimensions: number
  } | null
  vector_index?: {
    algorithm: 'exact' | 'hnsw'
    status: string
    row_count: number
    min_rows: number
    index_name?: string | null
  } | null
  blockers: string[]
}

export interface SaveEmbeddingProfile extends EmbeddingProfileConfiguration {
  profile_id?: string
  name: string
  description: string
  is_default: boolean
}

export const listEmbeddingProfiles = async () => (
  await request<{ items: EmbeddingProfile[] }>('')
).items
export const getEmbeddingProfile = (id: string) => request<EmbeddingProfile>(`/${encodeURIComponent(id)}`)
export const getEmbeddingReadiness = () => request<EmbeddingReadiness>('/readiness')
export const createEmbeddingProfile = (value: SaveEmbeddingProfile) => request<EmbeddingProfileRevision>('', { method: 'POST', body: JSON.stringify(value) })
export const createEmbeddingProfileRevision = (id: string, value: SaveEmbeddingProfile) => request<EmbeddingProfileRevision>(`/${encodeURIComponent(id)}/revisions`, { method: 'POST', body: JSON.stringify(value) })
export const publishEmbeddingProfileRevision = (id: string, revisionId: string) => request<EmbeddingProfileRevision>(`/${encodeURIComponent(id)}/revisions/${encodeURIComponent(revisionId)}/publish`, { method: 'POST' })
