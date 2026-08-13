import { apiFetch } from './http'

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const headers = new Headers(init?.headers)
  if (init?.body !== undefined) headers.set('Content-Type', 'application/json')
  const response = await apiFetch(`/v1/admin/model-providers${path}`, { ...init, headers })
  const payload = await response.json().catch(() => ({}))
  if (!response.ok) throw new Error(payload?.detail || payload?.error?.message || '模型配置请求失败')
  return payload as T
}

export interface ModelCatalogItem {
  model_id: string
  provider_id?: string
  name: string
  description?: string
  kind: string
  enabled: boolean
  input_modalities: string[]
  context_window: number
  max_output_tokens: number
  supports_tools: boolean
  supports_reasoning: boolean
  supports_structured_output: boolean
  default_temperature: number
  tags: string[]
  dimensions: number
  input_cost_per_million_tokens?: number | null
}

export interface ModelProviderConfiguration {
  enabled: boolean
  extension_id: string
  api_base: string
  api_key_ref: string
  api_key_variable?: string
  allow_insecure_http: boolean
  credential_mode: string
  extra_header_refs: Record<string, string>
  extra_header_variables?: Record<string, string>
  request_timeout_seconds: number
  models: ModelCatalogItem[]
}

export interface ModelProviderRevision {
  provider_id?: string
  revision_id: string
  version: number
  status: string
  fingerprint: string
  configuration: ModelProviderConfiguration
  created_by?: string
  created_at?: string
  published_at?: string | null
}

export interface ModelProviderRollout {
  rollout_id: string
  revision_id: string
  status: string
  target_worker_count: number
  acknowledged_worker_count: number
  failed_worker_count: number
  previous_revision_id?: string | null
  created_at: string
  targets: Array<{ worker_id: string; status: string; attempt_count: number }>
}

export interface ModelProviderProfile {
  provider_id: string
  name: string
  description: string
  current_revision_id?: string | null
  current_revision?: ModelProviderRevision | null
  latest_revision?: ModelProviderRevision | null
  revisions?: ModelProviderRevision[]
  model_count: number
  extension_release?: { version: string; status: string } | null
  worker_summary: { total: number; loaded: number }
  latest_rollout?: ModelProviderRollout | null
  execution_ready: boolean
  execution_blockers: string[]
}

export interface SaveModelProvider {
  provider_id?: string
  name: string
  description: string
  enabled: boolean
  extension_id: string
  api_base: string
  api_key_ref: string
  allow_insecure_http: boolean
  credential_mode: string
  extra_header_refs: Record<string, string>
  request_timeout_seconds: number
  models: ModelCatalogItem[]
}

const rolloutPolicy = { activation_mode: 'automatic', timeout_seconds: 300, auto_rollback: true, require_healthy_workers: true }

export const listModelProviders = async () => (await request<{ items: ModelProviderProfile[] }>('')).items
export const getModelProvider = (id: string) => request<ModelProviderProfile>(`/${encodeURIComponent(id)}`)
export const listActiveModels = async () => (await request<{ items: ModelCatalogItem[] }>('/models')).items
export const createModelProvider = (value: SaveModelProvider) => request<ModelProviderRevision>('', { method: 'POST', body: JSON.stringify(value) })
export const createModelProviderRevision = (id: string, value: SaveModelProvider) => request<ModelProviderRevision>(`/${encodeURIComponent(id)}/revisions`, { method: 'POST', body: JSON.stringify(value) })
export const publishModelProviderRevision = (id: string, revisionId: string) => request<Record<string, unknown>>(`/${encodeURIComponent(id)}/revisions/${encodeURIComponent(revisionId)}/publish`, { method: 'POST', body: JSON.stringify(rolloutPolicy) })
