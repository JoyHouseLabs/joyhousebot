import { apiFetch } from './http'

const API_BASE = '/api'

export interface HouseIdentity {
  house_id: string
  house_name: string
  machine_fingerprint: string
  public_key: string
  registered: boolean
  bound_user_id: string | null
  created_at: string
}

export interface RegisterResponse {
  ok: boolean
  message?: string
  house?: HouseIdentity
}

export interface BindResponse {
  ok: boolean
  message?: string
}

export interface CloudConnectConfig {
  enabled: boolean
  backend_url: string
  house_name: string
  description: string
  auto_reconnect: boolean
  reconnect_interval: number
  capabilities: CapabilityItem[]
}

export interface CapabilityItem {
  id: string
  name: string
  description: string
  version: string
  enabled: boolean
}

export interface ConnectionStatus {
  connected: boolean
  authenticated: boolean
  house_id: string | null
  last_connected: string | null
  error: string | null
}

export async function getHouseIdentity(): Promise<HouseIdentity> {
  const res = await apiFetch(`${API_BASE}/house/identity`)
  if (!res.ok) throw new Error(await res.text())
  const json = await res.json()
  if (!json.ok) throw new Error(json.message || '获取身份信息失败')
  return json.data
}

export async function registerHouse(): Promise<RegisterResponse> {
  const res = await apiFetch(`${API_BASE}/house/register`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
  })
  if (!res.ok) throw new Error(await res.text())
  return res.json()
}

export async function bindHouse(userId: string): Promise<BindResponse> {
  const res = await apiFetch(`${API_BASE}/house/bind`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ user_id: userId }),
  })
  if (!res.ok) throw new Error(await res.text())
  return res.json()
}

export async function getCloudConnectConfig(): Promise<CloudConnectConfig> {
  const res = await apiFetch(`${API_BASE}/config`)
  if (!res.ok) throw new Error(await res.text())
  const json = await res.json()
  return json.data.cloud_connect || {
    enabled: false,
    backend_url: 'ws://localhost:8000/ws/cloud-connect',
    house_name: '',
    description: '',
    auto_reconnect: true,
    reconnect_interval: 30,
    capabilities: [],
  }
}

export async function updateCloudConnectConfig(config: CloudConnectConfig): Promise<{ ok: boolean; message?: string }> {
  const res = await apiFetch(`${API_BASE}/config`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ cloud_connect: config }),
  })
  if (!res.ok) throw new Error(await res.text())
  return res.json()
}

export async function getConnectionStatus(): Promise<ConnectionStatus> {
  const res = await apiFetch(`${API_BASE}/cloud-connect/status`)
  if (!res.ok) throw new Error(await res.text())
  const json = await res.json()
  return json.data
}

export async function startCloudConnect(): Promise<{ ok: boolean; message?: string }> {
  const res = await apiFetch(`${API_BASE}/cloud-connect/start`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
  })
  if (!res.ok) throw new Error(await res.text())
  return res.json()
}

export async function stopCloudConnect(): Promise<{ ok: boolean; message?: string }> {
  const res = await apiFetch(`${API_BASE}/cloud-connect/stop`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
  })
  if (!res.ok) throw new Error(await res.text())
  return res.json()
}
