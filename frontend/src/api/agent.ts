import { apiFetch } from './http'

const API_BASE = '/api'

export interface AgentInfo {
  id?: string
  model: string
  temperature: number
  max_tokens: number
  max_tool_iterations: number
  memory_window: number
  workspace: string
  provider_name: string
}

export interface AgentResponse {
  ok: boolean
  agent: AgentInfo
}

/** OpenClaw-style: one entry in agents list */
export interface AgentListItem {
  id: string
  name: string
  workspace: string
  model: string
  provider_name: string
  temperature: number
  max_tokens: number
  max_tool_iterations: number
  memory_window: number
  is_default: boolean
  /** If true, show in chat page agent radio; default true when absent */
  activated?: boolean
}

export interface AgentsListResponse {
  ok: boolean
  agents: AgentListItem[]
}

export async function getAgent(): Promise<AgentResponse> {
  const res = await apiFetch(`${API_BASE}/agent`)
  if (!res.ok) throw new Error(await res.text())
  return res.json()
}

export async function getAgents(): Promise<AgentsListResponse> {
  const res = await apiFetch(`${API_BASE}/agents`)
  if (!res.ok) throw new Error(await res.text())
  return res.json()
}

export interface AgentPatchBody {
  activated?: boolean
}

export interface AgentPatchResponse {
  ok: boolean
  agent: AgentListItem
}

export async function patchAgent(agentId: string, body: AgentPatchBody): Promise<AgentPatchResponse> {
  const res = await apiFetch(`${API_BASE}/agents/${encodeURIComponent(agentId)}`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
  if (!res.ok) throw new Error(await res.text())
  return res.json()
}

export interface AgentFile {
  name: string
  path: string
  size: number
  updatedAtMs: number
  mtimeMs: number
}

export interface AgentFilesResponse {
  agentId: string
  workspace: string
  files: AgentFile[]
}

export async function getAgentFiles(agentId: string): Promise<AgentFilesResponse> {
  const res = await apiFetch(`${API_BASE}/agent/files?agentId=${encodeURIComponent(agentId)}`)
  if (!res.ok) throw new Error(await res.text())
  return res.json()
}

export interface AgentFileContentResponse {
  agentId: string
  path: string
  content?: string
}

export async function getAgentFile(agentId: string, path: string): Promise<AgentFileContentResponse> {
  const res = await apiFetch(`${API_BASE}/agent/file?agentId=${encodeURIComponent(agentId)}&path=${encodeURIComponent(path)}`)
  if (!res.ok) throw new Error(await res.text())
  return res.json()
}
