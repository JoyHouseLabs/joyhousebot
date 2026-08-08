import { apiFetch } from './http'
import { getIdentityHeaders, getRuntimeUserId } from './identity'

export interface SessionItem {
  key: string
  user_id?: string
  agent_id?: string
  run_count?: number
  latest_run_id?: string
  latest_status?: string
  created_at: string | null
  updated_at: string | null
}

export interface SessionsResponse { ok: boolean; user_id?: string; sessions: SessionItem[] }
export interface SessionHistoryMessage { role: string; content: string; run_id?: string }
export interface SessionHistoryResponse { ok: boolean; key: string; user_id?: string; messages: SessionHistoryMessage[]; updated_at: string | null }

const identityHeaders = getIdentityHeaders

export async function getSessions(agentId?: string | null): Promise<SessionsResponse> {
  const query = agentId ? `?agent_id=${encodeURIComponent(agentId)}` : ''
  const response = await apiFetch(`/v1/sessions${query}`, { headers: identityHeaders() })
  if (!response.ok) throw new Error(await response.text())
  const payload = await response.json()
  return {
    ok: true,
    user_id: getRuntimeUserId(),
    sessions: (payload.items ?? []).map((item: Record<string, string>) => ({
      key: item.session_id,
      agent_id: item.agent_id,
      latest_run_id: item.latest_run_id,
      latest_status: item.latest_status,
      created_at: null,
      updated_at: item.updated_at ?? null,
    })),
  }
}

export async function getSessionHistory(sessionId: string, agentId?: string | null): Promise<SessionHistoryResponse> {
  const agent = agentId || 'default'
  const response = await apiFetch(`/v1/sessions/${encodeURIComponent(agent)}/${encodeURIComponent(sessionId)}/history`, { headers: identityHeaders() })
  if (!response.ok) throw new Error(await response.text())
  const payload = await response.json()
  return { ok: true, key: sessionId, user_id: getRuntimeUserId(), messages: payload.items ?? [], updated_at: null }
}

export async function deleteSession(sessionId: string, agentId?: string | null): Promise<{ ok: boolean; removed: boolean }> {
  const agent = agentId || 'default'
  const response = await apiFetch(`/v1/sessions/${encodeURIComponent(agent)}/${encodeURIComponent(sessionId)}`, {
    method: 'DELETE', headers: identityHeaders(),
  })
  if (!response.ok) throw new Error(await response.text())
  const payload = await response.json()
  return { ok: true, removed: Number(payload.deleted ?? 0) > 0 }
}
