import { apiFetch } from './http'

const API_BASE = '/api'

export interface WalletData {
  enabled: boolean
  address: string
}

export interface AgentEntryData {
  id: string
  name: string
  workspace: string
  model: string
  model_fallbacks?: string[]
  provider?: string
  max_tokens?: number
  temperature?: number
  max_tool_iterations?: number
  memory_window?: number
  max_context_tokens?: number | null
  default?: boolean
  activated?: boolean
}

export interface ConfigData {
  agents: {
    defaults: Record<string, unknown>
    list?: AgentEntryData[]
    default_id?: string | null
  }
  providers: Record<string, Record<string, unknown>>
  channels: Record<string, Record<string, unknown>>
  tools: Record<string, unknown>
  gateway: Record<string, unknown>
  wallet?: WalletData
  workspace_path: string
  provider_name: string
  auth?: Record<string, unknown>
  skills?: Record<string, unknown>
  plugins?: Record<string, unknown>
  approvals?: Record<string, unknown>
  browser?: Record<string, unknown>
  messages?: Record<string, unknown>
  commands?: Record<string, unknown>
  env?: Record<string, unknown>
}

export interface ConfigResponse {
  ok: boolean
  data: ConfigData
}

export interface WalletUpdatePayload {
  enabled: boolean
  password?: string
}

export interface ConfigUpdateBody {
  providers?: Record<string, Record<string, unknown>>
  agents?: Record<string, Record<string, unknown>>
  channels?: Record<string, Record<string, unknown>>
  tools?: Record<string, unknown>
  gateway?: Record<string, unknown>
  wallet?: WalletUpdatePayload
  auth?: Record<string, unknown>
  skills?: Record<string, unknown>
  plugins?: Record<string, unknown>
  approvals?: Record<string, unknown>
  browser?: Record<string, unknown>
  messages?: Record<string, unknown>
  commands?: Record<string, unknown>
  env?: Record<string, unknown>
}

export async function getConfig(): Promise<ConfigResponse> {
  const res = await apiFetch(`${API_BASE}/config`)
  if (!res.ok) throw new Error(await res.text())
  return res.json()
}

export interface ConfigUpdateResponse {
  ok: boolean
  message?: string
  wallet?: WalletData
}

export async function updateConfig(body: ConfigUpdateBody): Promise<ConfigUpdateResponse> {
  const res = await apiFetch(`${API_BASE}/config`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
  if (!res.ok) throw new Error(await res.text())
  return res.json()
}
