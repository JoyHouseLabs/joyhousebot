import { apiFetch } from './http'

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const headers = new Headers(init?.headers)
  if (init?.body !== undefined) headers.set('Content-Type', 'application/json')
  const response = await apiFetch(`/control/v1/admin/remote-connections${path}`, { ...init, headers })
  const payload = await response.json().catch(() => ({}))
  if (!response.ok) throw new Error(payload?.detail || payload?.error?.message || '远程连接请求失败')
  return payload as T
}

export interface RemoteCapabilityDeclaration {
  capability_id: string
  version: string
  implementation_digest: string
  name: string
  description?: string
  input_schema: Record<string, unknown>
  output_schema: Record<string, unknown>
  permissions: string[]
  tags?: string[]
  side_effect: string
  idempotent: boolean
  retryable?: boolean
  data_classification: string
  timeout_seconds?: number
  expected_duration_seconds?: number
  invocation_concurrency?: string
  max_concurrent_invocations?: number
  cost_policy?: Record<string, unknown>
  execution_mode?: 'immediate' | 'durable'
  supports_stream?: boolean
  provenance?: Record<string, unknown>
  release_status?: string
  loaded_definition?: Record<string, unknown> | null
}

export interface RemoteConnectionConfiguration {
  service_profile: 'business' | 'extension_host'
  enabled: boolean
  base_url: string
  key_id: string
  signing_secret_ref: string
  signing_secret_variable?: string
  allow_insecure_http: boolean
  require_response_signature: boolean
  timeout_seconds: number
  max_response_bytes: number
  host_protocol_version: string
  expected_host_manifest_digest: string
  require_host_preflight: boolean
  capabilities: RemoteCapabilityDeclaration[]
}

export interface RemoteConnectionRevision {
  connection_id?: string
  revision_id: string
  version: number
  status: string
  fingerprint: string
  configuration: RemoteConnectionConfiguration
  created_by?: string
  created_at?: string
  published_at?: string | null
}

export interface RemoteConnectionRollout {
  rollout_id: string
  revision_id: string
  status: string
  target_worker_count: number
  acknowledged_worker_count: number
  failed_worker_count: number
  previous_revision_id?: string | null
  deadline_at?: string | null
  created_at: string
  targets: Array<{ worker_id: string; status: string; error?: Record<string, unknown> | null; attempt_count: number }>
}

export interface RemoteConnection {
  connection_id: string
  name: string
  description: string
  current_revision_id?: string | null
  current_revision?: RemoteConnectionRevision | null
  latest_revision?: RemoteConnectionRevision | null
  revisions?: RemoteConnectionRevision[]
  capabilities: RemoteCapabilityDeclaration[]
  connector_release?: { version: string; status: string } | null
  worker_summary: { total: number; loaded: number }
  latest_rollout?: RemoteConnectionRollout | null
  execution_ready: boolean
  execution_blockers: string[]
  created_at?: string
  updated_at?: string
}

export interface SaveRemoteConnection {
  connection_id?: string
  name: string
  description: string
  service_profile: 'business' | 'extension_host'
  enabled: boolean
  base_url: string
  key_id: string
  signing_secret_ref: string
  allow_insecure_http: boolean
  require_response_signature: boolean
  timeout_seconds: number
  max_response_bytes: number
  host_protocol_version: string
  expected_host_manifest_digest: string
  require_host_preflight: boolean
  capabilities: RemoteCapabilityDeclaration[]
}

const rolloutPolicy = {
  activation_mode: 'automatic',
  timeout_seconds: 300,
  auto_rollback: true,
  require_healthy_workers: true,
}

export const listRemoteConnections = async () => (await request<{ items: RemoteConnection[] }>('')).items
export const getRemoteConnection = (id: string) => request<RemoteConnection>(`/${encodeURIComponent(id)}`)
export const createRemoteConnection = (value: SaveRemoteConnection) => request<RemoteConnectionRevision>('', { method: 'POST', body: JSON.stringify(value) })
export const createRemoteConnectionRevision = (id: string, value: SaveRemoteConnection) => request<RemoteConnectionRevision>(`/${encodeURIComponent(id)}/revisions`, { method: 'POST', body: JSON.stringify(value) })
export const publishRemoteConnectionRevision = (id: string, revisionId: string) => request<Record<string, unknown>>(`/${encodeURIComponent(id)}/revisions/${encodeURIComponent(revisionId)}/publish`, { method: 'POST', body: JSON.stringify(rolloutPolicy) })
export const publishRemoteCapability = (id: string, capabilityId: string, version: string) => request<Record<string, unknown>>(`/${encodeURIComponent(id)}/capabilities/${encodeURIComponent(capabilityId)}/versions/${encodeURIComponent(version)}/publish`, { method: 'POST', body: JSON.stringify(rolloutPolicy) })
