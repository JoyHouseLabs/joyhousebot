import { apiFetch } from './http'

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await apiFetch(`/v1${path}`, init)
  const payload = await response.json().catch(() => ({}))
  if (!response.ok) throw new Error(payload?.detail || payload?.error?.message || '设备请求失败')
  return payload as T
}

export interface DeviceHost {
  user_id: string
  device_id: string
  display_name: string
  status: 'active' | 'revoked'
  host_revision: string
  host_manifest_digest: string
  is_default: boolean
  last_seen_at?: string | null
  updated_at: string
  capabilities: Array<{
    capability_id: string
    version: string
    implementation_digest: string
    portable: boolean
  }>
}

export interface HostModelGrant {
  grant_id: string
  delivery_id: string
  model_id: string
  status: string
  token_budget: number
  used_tokens: number
  cost_budget_micros: number
  used_cost_micros: number
  expires_at: string
}

export type DeviceHostControlAction =
  | 'preflight' | 'diagnose_opencli' | 'diagnose_pi'
  | 'enable_opencli' | 'disable_opencli' | 'enable_pi' | 'disable_pi' | 'restart_host'

export interface DeviceHostControlRequest {
  request_id: string
  device_id: string
  action: DeviceHostControlAction
  parameters: Record<string, string>
  status: 'queued' | 'claimed' | 'succeeded' | 'failed' | 'manual_required' | 'cancelled'
  result?: Record<string, unknown> | null
  error?: { code?: string; message?: string } | null
  created_at: string
  completed_at?: string | null
}

export const listDeviceHosts = async () =>
  (await request<{ items: DeviceHost[] }>('/device-hosts')).items

export const revokeDeviceHost = (deviceId: string) =>
  request<void>(`/device-hosts/${encodeURIComponent(deviceId)}`, { method: 'DELETE' })

export const rotateDeviceHostToken = (deviceId: string) =>
  request<{ device_id: string; device_token: string }>(
    `/device-hosts/${encodeURIComponent(deviceId)}/token:rotate`,
    { method: 'POST' },
  )

export const createDeviceHostControlRequest = (deviceId: string, action: DeviceHostControlAction, parameters: Record<string, string> = {}) =>
  request<{ control_request: DeviceHostControlRequest }>(
    `/device-hosts/${encodeURIComponent(deviceId)}/control-requests`,
    { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ action, parameters }) },
  )

export const listDeviceHostControlRequests = async (deviceId: string) =>
  (await request<{ items: DeviceHostControlRequest[] }>(
    `/device-hosts/${encodeURIComponent(deviceId)}/control-requests?limit=20`,
  )).items

export const listHostModelGrants = async () =>
  (await request<{ items: HostModelGrant[] }>('/model-grants?limit=100')).items
