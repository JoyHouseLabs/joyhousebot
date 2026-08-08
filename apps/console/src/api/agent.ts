import { apiFetch } from './http'
import { getIdentityHeaders } from './identity'

export interface AgentListItem {
  id: string
  name: string
  model: string
  description?: string
}

export interface AgentsListResponse { ok: boolean; agents: AgentListItem[] }

export async function getAgents(): Promise<AgentsListResponse> {
  const response = await apiFetch('/v1/agents', { headers: getIdentityHeaders() })
  if (!response.ok) throw new Error(await response.text())
  const payload = await response.json()
  const items = payload.items ?? []
  return { ok: true, agents: items as AgentListItem[] }
}
