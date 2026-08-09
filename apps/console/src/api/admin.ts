import { getApiHeaders } from './http'
import { getIdentityHeaders } from './identity'
import type { RunFeedback, RuntimeArtifact, RuntimeEvent, RuntimeInvocation, RuntimeLog, RuntimeRun, RuntimeTask } from './runtime'

async function adminFetch<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers)
  for (const [key, value] of Object.entries(getApiHeaders())) headers.set(key, value)
  for (const [key, value] of Object.entries(getIdentityHeaders())) headers.set(key, value)
  const response = await fetch(`/v1/admin${path}`, { ...init, headers })
  const payload = await response.json().catch(() => ({}))
  if (!response.ok) throw new Error(payload?.detail ?? payload?.error?.message ?? '管理 API 调用失败')
  return payload as T
}

export interface AdminOverview {
  runs: number
  users: number
  sessions: number
  active_runs: number
  workers: number
  healthy_workers: number
  statuses: Record<string, number>
  usage: { input_tokens: number; output_tokens: number; total_tokens: number; cost_usd: number }
}

export interface PlatformAdmin {
  user_id: string
  role: 'admin' | 'operator' | 'viewer'
  permissions: string[]
  enabled: boolean
  is_test_user: boolean
  created_by: string
  created_at: string
  updated_at: string
}

export interface RuntimeWorker {
  worker_id: string
  status: string
  healthy: boolean
  capabilities: Record<string, unknown>
  metadata: Record<string, unknown>
  started_at: string
  last_heartbeat: string
}

export interface PermissionCatalog {
  items: Array<{ permission: string; group: string; description: string }>
  roles: Record<PlatformAdmin['role'], string[]>
}

export interface ConfigurationRollout {
  rollout_id: string
  aggregate_type: string
  aggregate_id: string
  revision_id: string
  status: string
  created_by: string
  target_worker_count: number
  acknowledged_worker_count: number
  failed_worker_count: number
  previous_revision_id?: string | null
  activation_mode: 'automatic' | 'manual'
  timeout_seconds: number
  deadline_at?: string | null
  auto_rollback: boolean
  approved_by?: string | null
  approved_at?: string | null
  cancelled_by?: string | null
  cancelled_at?: string | null
  rollback_revision_id?: string | null
  created_at: string
  updated_at: string
  completed_at?: string | null
  targets: Array<{ worker_id: string; status: string; error?: Record<string, unknown> | null; acknowledged_at?: string | null; attempt_count: number }>
}

export interface RolloutPolicy {
  activation_mode: 'automatic' | 'manual'
  timeout_seconds: number
  auto_rollback: boolean
  require_healthy_workers: boolean
}

export interface ConfigurationEvent {
  sequence: number
  aggregate_type: string
  aggregate_id: string
  revision_id: string
  event_type: string
  actor_id: string
  created_at: string
}

export interface AccessToken {
  token_id: string
  user_id: string
  label: string
  enabled: boolean
  expires_at?: string | null
  created_by: string
  created_at: string
  last_used_at?: string | null
}

export interface AgentRevision {
  revision_id: string
  agent_id: string
  version: number
  persona: Record<string, unknown>
  instructions: string
  model_policy: Record<string, unknown>
  planning_policy: Record<string, unknown>
  capability_policy: Record<string, unknown>
  memory_policy: Record<string, unknown>
  output_policy: Record<string, unknown>
  monitor_policy: Record<string, unknown>
  status: 'draft' | 'published' | 'retired'
  created_by: string
  created_at?: string | null
  published_at?: string | null
}

export interface AdminAgent {
  agent_id: string
  name: string
  description: string
  role: 'coordinator' | 'executor' | 'specialist'
  status: 'active' | 'disabled' | 'archived'
  is_default: boolean
  current_revision_id?: string | null
  created_at?: string | null
  updated_at?: string | null
  revision?: AgentRevision | null
}

export interface AdminCapability {
  ref: { capability_id: string; version: string; kind: 'tool' | 'agent' | 'workflow' | 'skill' | 'connector' }
  name: string
  description: string
  adapter: string
  tags: string[]
  permissions: string[]
  configuration_schema?: Record<string, unknown>
}

export interface CapabilityRuntimeSettings {
  capability_id: string
  enabled: boolean
  configuration: Record<string, unknown>
  configuration_schema: Record<string, unknown>
  updated_by?: string | null
  updated_at?: string | null
}

export interface MCPServerConfig {
  name: string
  enabled: boolean
  command: string
  args: string[]
  url: string
  env_keys: string[]
}

export interface AgentSkillBinding {
  agent_revision_id: string
  skill_id: string
  skill_version: string
  activation_mode: 'always' | 'coordinator_selected' | 'scenario_required'
  priority: number
  configuration: Record<string, unknown>
}

export interface RunDiagnostics {
  run: RuntimeRun
  tasks: RuntimeTask[]
  events: RuntimeEvent[]
  logs: RuntimeLog[]
  invocations: RuntimeInvocation[]
  artifacts: RuntimeArtifact[]
  traces: Array<Record<string, unknown>>
  children: RuntimeRun[]
  spans: ExecutionSpan[]
  model_invocations: ModelInvocation[]
  reasoning: ReasoningSegment[]
  trace_blobs: TraceBlob[]
  replays: ReplayRun[]
  feedback: RunFeedback[]
}

export interface ExecutionSpan {
  span_id: string
  trace_id: string
  parent_span_id?: string | null
  run_id: string
  task_id?: string | null
  turn_id?: string | null
  span_kind: string
  name: string
  status: string
  worker_id?: string | null
  attributes: Record<string, unknown>
  error?: Record<string, unknown> | null
  started_at?: string | null
  first_token_at?: string | null
  finished_at?: string | null
  duration_ms?: number | null
  ttft_ms?: number | null
}

export interface ModelInvocation {
  invocation_id: string
  run_id: string
  task_id?: string | null
  turn_id?: string | null
  span_id: string
  attempt: number
  provider: string
  model: string
  operation: string
  provider_request_id?: string | null
  request_blob_id?: string | null
  response_blob_id?: string | null
  status: string
  finish_reason?: string | null
  reasoning_availability: string
  usage: Record<string, number>
  cost_usd: number
  cache_status: string
  error?: Record<string, unknown> | null
  started_at?: string | null
  duration_ms?: number | null
  ttft_ms?: number | null
}

export interface ReasoningSegment {
  segment_id: string
  invocation_id: string
  run_id: string
  sequence: number
  source: 'provider_native' | 'provider_summary' | 'model_declared' | 'runtime_decision' | 'unavailable'
  kind: string
  content: string
  content_format: string
  fidelity: 'exact' | 'normalized' | 'generated' | 'unavailable'
  provider_block_type?: string | null
  token_count?: number | null
  created_at?: string | null
}

export interface TraceBlob {
  blob_id: string
  run_id: string
  invocation_id?: string | null
  kind: string
  content_type: string
  sha256: string
  size_bytes: number
  content?: unknown
  created_at?: string | null
}

export interface ReplayRun {
  replay_id: string
  source_run_id: string
  source_turn_id?: string | null
  new_run_id?: string | null
  mode: 'offline' | 'frozen' | 'branch' | 'live'
  overrides: Record<string, unknown>
  created_by: string
  status: string
  comparison?: Record<string, unknown> | null
  created_at?: string | null
  finished_at?: string | null
}

export const getAdminOverview = () => adminFetch<AdminOverview>('/overview')
export const getAdminWorkers = async () => (await adminFetch<{ items: RuntimeWorker[] }>('/workers')).items
export const getAdminConfig = () => adminFetch<Record<string, unknown>>('/config')
export const getMCPServers = async () => (await adminFetch<{ items: MCPServerConfig[] }>('/mcp-servers')).items
export const saveMCPServer = (name: string, value: { enabled: boolean; command: string; args: string[]; env: Record<string, string>; url: string }) =>
  adminFetch<MCPServerConfig>(`/mcp-servers/${encodeURIComponent(name)}`, { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(value) })
export const deleteMCPServer = (name: string) => adminFetch<{ deleted: boolean }>(`/mcp-servers/${encodeURIComponent(name)}`, { method: 'DELETE' })
export const testMCPServer = (name: string) => adminFetch<{ ok: boolean; message: string }>(`/mcp-servers/${encodeURIComponent(name)}/test`, { method: 'POST' })
export const getAdminAgents = async () => (await adminFetch<{ items: AdminAgent[] }>('/agents')).items
export const getAdminCapabilities = async () => (await adminFetch<{ items: AdminCapability[] }>('/capabilities')).items
export const getCapabilityRuntimeSettings = (capabilityId: string) =>
  adminFetch<CapabilityRuntimeSettings>(`/capabilities/${encodeURIComponent(capabilityId)}/runtime-settings`)
export const saveCapabilityRuntimeSettings = (capabilityId: string, value: Pick<CapabilityRuntimeSettings, 'enabled' | 'configuration'>) =>
  adminFetch<CapabilityRuntimeSettings>(`/capabilities/${encodeURIComponent(capabilityId)}/runtime-settings`, {
    method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(value),
  })
export const getPlatformAdmins = async () => (await adminFetch<{ items: PlatformAdmin[] }>('/users')).items
export const getPermissionCatalog = () => adminFetch<PermissionCatalog>('/permissions')
export const getConfigurationRollouts = async () => (await adminFetch<{ items: ConfigurationRollout[] }>('/rollouts')).items
export const approveConfigurationRollout = (rolloutId: string) =>
  adminFetch<ConfigurationRollout>(`/rollouts/${encodeURIComponent(rolloutId)}/approve`, { method: 'POST' })
export const cancelConfigurationRollout = (rolloutId: string) =>
  adminFetch<ConfigurationRollout>(`/rollouts/${encodeURIComponent(rolloutId)}/cancel`, { method: 'POST' })
export const retryConfigurationRollout = (rolloutId: string) =>
  adminFetch<ConfigurationRollout>(`/rollouts/${encodeURIComponent(rolloutId)}/retry`, { method: 'POST' })
export const rollbackConfigurationRollout = (rolloutId: string) =>
  adminFetch<ConfigurationRollout>(`/rollouts/${encodeURIComponent(rolloutId)}/rollback`, { method: 'POST' })
export const getConfigurationEvents = async () => (await adminFetch<{ items: ConfigurationEvent[] }>('/configuration-events')).items
export const getAccessEvents = async () => (await adminFetch<{ items: Array<Record<string, unknown>> }>('/access-events')).items
export const getAccessTokens = async () => (await adminFetch<{ items: AccessToken[] }>('/access-tokens')).items
export const createAccessToken = (value: { user_id: string; label?: string; expires_at?: string }) =>
  adminFetch<AccessToken & { token: string }>('/access-tokens', {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(value),
  })
export const revokeAccessToken = (tokenId: string) =>
  adminFetch<{ revoked: boolean }>(`/access-tokens/${encodeURIComponent(tokenId)}`, { method: 'DELETE' })

export const getAgentRevisions = async (agentId: string) =>
  (await adminFetch<{ items: AgentRevision[] }>(`/agents/${encodeURIComponent(agentId)}/revisions`)).items

export const getAgentSkillBindings = async (agentId: string, revisionId: string) =>
  (await adminFetch<{ items: AgentSkillBinding[] }>(
    `/agents/${encodeURIComponent(agentId)}/revisions/${encodeURIComponent(revisionId)}/skills`,
  )).items

export const bindAgentSkill = (
  agentId: string,
  revisionId: string,
  value: Omit<AgentSkillBinding, 'agent_revision_id'>,
) => adminFetch<{ saved: boolean }>(
  `/agents/${encodeURIComponent(agentId)}/revisions/${encodeURIComponent(revisionId)}/skills`,
  { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(value) },
)

export function saveAgentRevision(agentId: string, revisionId: string, value: Record<string, unknown>) {
  return adminFetch<Record<string, unknown>>(
    `/agents/${encodeURIComponent(agentId)}/revisions/${encodeURIComponent(revisionId)}`,
    { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(value) },
  )
}

export const publishAgentRevision = (agentId: string, revisionId: string, policy?: RolloutPolicy) =>
  adminFetch<Record<string, unknown>>(
    `/agents/${encodeURIComponent(agentId)}/revisions/${encodeURIComponent(revisionId)}/publish`,
    {
      method: 'POST',
      headers: policy ? { 'Content-Type': 'application/json' } : undefined,
      body: policy ? JSON.stringify(policy) : undefined,
    },
  )

export function publishCapability(capabilityId: string, version: string, value: Record<string, unknown>) {
  return adminFetch<Record<string, unknown>>(
    `/capabilities/${encodeURIComponent(capabilityId)}/versions/${encodeURIComponent(version)}`,
    { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(value) },
  )
}

export async function savePlatformAdmin(userId: string, value: Pick<PlatformAdmin, 'role' | 'permissions' | 'enabled' | 'is_test_user'>) {
  return adminFetch<PlatformAdmin>(`/users/${encodeURIComponent(userId)}`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(value),
  })
}

export async function deletePlatformAdmin(userId: string) {
  return adminFetch<{ deleted: boolean }>(`/users/${encodeURIComponent(userId)}`, { method: 'DELETE' })
}

export type AdminRunPage = {
  items: RuntimeRun[]
  pagination: { page: number; limit: number; total: number; total_pages: number }
}

export async function listAdminRuns(filters: { userId?: string; agentId?: string; status?: string; search?: string; page?: number; limit?: number } = {}) {
  const query = new URLSearchParams({ limit: String(filters.limit ?? 10), page: String(filters.page ?? 1) })
  if (filters.userId) query.set('user_id', filters.userId)
  if (filters.agentId) query.set('agent_id', filters.agentId)
  if (filters.status) query.set('status', filters.status)
  if (filters.search?.trim()) query.set('search', filters.search.trim())
  return adminFetch<AdminRunPage>(`/runs?${query}`)
}

export const getAdminRunDiagnostics = (runId: string) =>
  adminFetch<RunDiagnostics>(`/runs/${encodeURIComponent(runId)}/diagnostics`)

export const getTraceBlob = (runId: string, blobId: string) =>
  adminFetch<TraceBlob>(`/runs/${encodeURIComponent(runId)}/blobs/${encodeURIComponent(blobId)}`)

export const listRunReplays = async (runId: string) =>
  (await adminFetch<{ items: ReplayRun[] }>(`/runs/${encodeURIComponent(runId)}/replays`)).items

export const createRunReplay = (
  runId: string,
  value: { mode: ReplayRun['mode']; source_turn_id?: string; prompt?: string; model?: string; agent_id?: string },
) => adminFetch<ReplayRun>(`/runs/${encodeURIComponent(runId)}/replays`, {
  method: 'POST',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify(value),
})

export const cancelAdminRun = (runId: string) =>
  adminFetch<RuntimeRun>(`/runs/${encodeURIComponent(runId)}/cancel`, { method: 'POST' })

export async function streamAdminRunEvents(
  runId: string,
  onEvent: (event: RuntimeEvent) => void,
  options: { afterSequence?: number; signal?: AbortSignal } = {},
): Promise<number> {
  let cursor = Math.max(0, options.afterSequence ?? 0)
  const headers = new Headers({ Accept: 'text/event-stream', ...getApiHeaders(), ...getIdentityHeaders() })
  const response = await fetch(
    `/v1/admin/runs/${encodeURIComponent(runId)}/events?after_sequence=${cursor}`,
    { headers, signal: options.signal },
  )
  if (!response.ok || !response.body) throw new Error(`管理事件流连接失败 (${response.status})`)
  const reader = response.body.getReader(); const decoder = new TextDecoder(); let buffer = ''
  while (true) {
    const { value, done } = await reader.read()
    buffer += decoder.decode(value, { stream: !done }).replace(/\r\n/g, '\n')
    let boundary = buffer.indexOf('\n\n')
    while (boundary >= 0) {
      const frame = buffer.slice(0, boundary); buffer = buffer.slice(boundary + 2)
      const data = frame.split('\n').filter((line) => line.startsWith('data:')).map((line) => line.slice(5).trim()).join('\n')
      if (data) { const event = JSON.parse(data) as RuntimeEvent; cursor = Math.max(cursor, event.sequence || 0); onEvent(event) }
      boundary = buffer.indexOf('\n\n')
    }
    if (done) return cursor
  }
}
