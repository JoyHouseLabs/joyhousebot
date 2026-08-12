import { apiFetch } from './http'

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const headers = new Headers(init?.headers)
  if (init?.body !== undefined) headers.set('Content-Type', 'application/json')
  const response = await apiFetch(`/v1/admin/teams${path}`, { ...init, headers })
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
export const saveAgentTeamRevision = (value: AgentTeamRevision) => request<AgentTeamRevision>(
  `/${encodeURIComponent(value.team_id)}/revisions/${encodeURIComponent(value.revision_id)}`,
  { method: 'PUT', body: JSON.stringify(value) },
)
export const publishAgentTeamRevision = (teamId: string, revisionId: string) => request<AgentTeamRevision>(
  `/${encodeURIComponent(teamId)}/revisions/${encodeURIComponent(revisionId)}/publish`,
  { method: 'POST' },
)
