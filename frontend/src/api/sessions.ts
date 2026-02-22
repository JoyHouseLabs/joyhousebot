const API_BASE = '/api'

export interface SessionItem {
  key: string
  created_at: string | null
  updated_at: string | null
  path?: string
}

export interface SessionsResponse {
  ok: boolean
  sessions: SessionItem[]
}

export interface SessionHistoryMessage {
  role: string
  content: string
  timestamp?: string
}

export interface SessionHistoryResponse {
  ok: boolean
  key: string
  messages: SessionHistoryMessage[]
  updated_at: string | null
}

function _agentQuery(agentId?: string | null): string {
  if (agentId == null || agentId === '') return ''
  return `?agent_id=${encodeURIComponent(agentId)}`
}

export async function getSessions(agentId?: string | null): Promise<SessionsResponse> {
  const res = await fetch(`${API_BASE}/sessions${_agentQuery(agentId)}`)
  if (!res.ok) throw new Error(await res.text())
  return res.json()
}

export async function getSessionHistory(sessionKey: string, agentId?: string | null): Promise<SessionHistoryResponse> {
  const keyEnc = encodeURIComponent(sessionKey)
  const res = await fetch(`${API_BASE}/sessions/${keyEnc}/history${_agentQuery(agentId)}`)
  if (!res.ok) throw new Error(await res.text())
  return res.json()
}

export async function deleteSession(sessionKey: string, agentId?: string | null): Promise<{ ok: boolean; removed: boolean }> {
  const keyEnc = encodeURIComponent(sessionKey)
  const res = await fetch(`${API_BASE}/sessions/${keyEnc}${_agentQuery(agentId)}`, { method: 'DELETE' })
  if (!res.ok) throw new Error(await res.text())
  return res.json()
}
