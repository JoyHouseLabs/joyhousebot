import { apiFetch } from './http'

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const headers = new Headers(init?.headers)
  if (init?.body !== undefined) headers.set('Content-Type', 'application/json')
  const response = await apiFetch(`/control/v1/admin/teams${path}`, { ...init, headers })
  const payload = await response.json().catch(() => ({}))
  if (!response.ok) throw new Error(payload?.detail || payload?.error?.message || 'AgentTeam 请求失败')
  return payload as T
}

export interface AgentTeamMember {
  member_id: string
  agent_id: string
  agent_revision_id: string
  role: string
  responsibility: string
  can_delegate: boolean
  allowed_handoffs: string[]
}

export interface BlueprintPhase {
  id: string
  kind: 'produce' | 'review' | 'revise' | 'synthesize' | 'checkpoint'
  participants: string[]
  mode: 'parallel' | 'sequential'
  depends_on: string[]
}

export interface BlueprintGuardrails {
  max_parallel_tasks: number
  require_review: boolean
  require_plan_confirmation: boolean
  require_final_confirmation: boolean
}

export interface CollaborationBlueprint {
  schema_version: number
  preset: string
  phases: BlueprintPhase[]
  guardrails: BlueprintGuardrails
}

export interface BlueprintPreset {
  preset: string
  label: string
  guidance: string
  phase_template: Array<{ id: string; kind: string }>
  bindings: string[]
}

export interface BlueprintValidation {
  ok: boolean
  errors: Array<{ code: string; message: string }>
  normalized: CollaborationBlueprint | null
}

export interface ConfigurationRolloutSummary {
  rollout_id: string
  aggregate_type: string
  aggregate_id: string
  revision_id: string
  status: string
  target_worker_count: number
  acknowledged_worker_count: number
  failed_worker_count: number
  activation_mode: string
  created_at?: string | null
  targets?: Array<{ worker_id: string; status: string; error?: unknown; acknowledged_at?: string | null; attempt_count: number }>
}

export interface AgentTeamRevision {
  team_id: string
  revision_id: string
  version: number
  name: string
  description: string
  coordinator_member_id: string
  members: AgentTeamMember[]
  context_policy: Record<string, unknown>
  budget_policy: Record<string, unknown>
  approval_policy: Record<string, unknown>
  collaboration_blueprint?: CollaborationBlueprint | null
  status: 'draft' | 'published' | 'retired'
  created_by: string
  created_at?: string | null
  published_at?: string | null
}

export const listAgentTeams = async () => (
  await request<{ items: AgentTeamRevision[] }>('')
).items
export const listAgentTeamRevisions = async (teamId: string) => (
  await request<{ items: AgentTeamRevision[] }>(`/${encodeURIComponent(teamId)}/revisions`)
).items
export const saveAgentTeamRevision = (value: AgentTeamRevision, roleBindings?: Record<string, string[]>) => request<AgentTeamRevision>(
  `/${encodeURIComponent(value.team_id)}/revisions/${encodeURIComponent(value.revision_id)}`,
  { method: 'PUT', body: JSON.stringify({ ...value, collaboration_blueprint: value.collaboration_blueprint ?? null, role_bindings: roleBindings ?? null }) },
)
export const publishAgentTeamRevision = (teamId: string, revisionId: string) => request<AgentTeamRevision>(
  `/${encodeURIComponent(teamId)}/revisions/${encodeURIComponent(revisionId)}/publish`,
  { method: 'POST' },
)
export const getBlueprintPresets = async () => (
  await request<{ items: BlueprintPreset[] }>('/blueprint-presets')
).items
export const validateBlueprint = (body: {
  blueprint: Record<string, unknown> | null
  members: Array<{ member_id: string; agent_id: string; agent_revision_id: string; role: string; responsibility: string }>
  coordinator_member_id: string
  budget_policy: Record<string, unknown>
}) => request<BlueprintValidation>('/blueprint-validate', { method: 'POST', body: JSON.stringify(body) })
export const migrateTeamBlueprint = (teamId: string) => request<AgentTeamRevision>(
  `/${encodeURIComponent(teamId)}/blueprint-migrate`,
  { method: 'POST' },
)
export const getTeamLatestRollout = async (teamId: string): Promise<ConfigurationRolloutSummary | null> => {
  try {
    return await request<ConfigurationRolloutSummary>(`/${encodeURIComponent(teamId)}/rollout/latest`)
  } catch {
    // No rollout history yet (legacy team or never published since the feature landed).
    return null
  }
}
