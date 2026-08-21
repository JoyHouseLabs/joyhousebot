import { getApiHeaders } from './http'
import { getIdentityHeaders } from './identity'

function headers(): Headers {
  const value = new Headers()
  for (const [key, item] of Object.entries(getApiHeaders())) value.set(key, item)
  for (const [key, item] of Object.entries(getIdentityHeaders())) value.set(key, item)
  return value
}

async function extensionFetch<T>(path: string): Promise<T> {
  const response = await fetch(`/control/v1/admin/extensions${path}`, { headers: headers() })
  const payload = await response.json().catch(() => ({}))
  if (!response.ok) throw new Error(payload?.detail || '扩展控制面请求失败')
  return payload as T
}

async function extensionPost<T>(path: string, body?: unknown): Promise<T> {
  const requestHeaders = headers()
  if (body !== undefined) requestHeaders.set('Content-Type', 'application/json')
  const response = await fetch(`/control/v1/admin/extensions${path}`, {
    method: 'POST', headers: requestHeaders,
    body: body === undefined ? undefined : JSON.stringify(body),
  })
  const payload = await response.json().catch(() => ({}))
  if (!response.ok) throw new Error(payload?.detail || '扩展诊断请求失败')
  return payload as T
}

export interface ExtensionComponent {
  component_id: string; component_type: string; name: string; description: string
  reference_id: string; reference_version: string; metadata: Record<string, unknown>
}
export interface ExtensionMetrics {
  hours: number; total: number; succeeded: number; failed: number; success_rate: number
  p50_duration_ms: number | null; p95_duration_ms: number | null
  by_component: Array<{ capability_id: string; total: number; succeeded: number; failed: number; p50_duration_ms: number | null; p95_duration_ms: number | null }>
}
export interface ExtensionRelease {
  extension_id: string; version: string; name: string; description: string
  distribution_name: string; build_digest: string; status: string
  component_count: number; metrics: ExtensionMetrics
  manifest: { dependencies?: Array<Record<string, unknown>> }
  created_at: string; updated_at: string
}
export interface ExtensionInventoryItem {
  extension_id: string; name: string; description: string; source_version: string
  extension_types: string[]; distribution_name: string; distribution_version: string
  source_location: string; source_digest: string; source_available: boolean; installed: boolean
  deployment_allowed: boolean; desired_active: boolean; effective_active: boolean
  state: 'active' | 'activating' | 'installed' | 'available' | 'unavailable'
  activation_blockers: string[]; metadata: Record<string, unknown>
  release?: ExtensionRelease | null
  worker_summary: { loaded: number; total: number }
}
export interface ExtensionOverview {
  release: { extension_id: string; version: string; status: string; name: string; description: string; distribution_name: string; build_digest: string; manifest: { dependencies?: Array<Record<string, unknown>> } }
  releases: Array<{ extension_id: string; version: string; status: string; build_digest: string; updated_at: string }>
  components: ExtensionComponent[]; metrics: ExtensionMetrics; worker_summary: { total: number; healthy_loaded: number }
}
export interface ExtensionTopology { nodes: Array<{ id: string; kind: string; label: string; data?: Record<string, unknown> }>; edges: Array<{ source: string; target: string; kind: string }> }
export interface ExtensionInvocation { invocation_id: string; capability_id: string; run_id: string; status: string; worker_id?: string | null; created_at: string; error?: Record<string, unknown> | null }
export interface ExtensionHealth { status: string; checks: Array<{ name: string; status: string; summary: string }> }
export interface ExtensionPlaygroundRun { run_id: string; user_id: string; status: string; session_id: string; agent_id: string; prompt: string }

export const listExtensionReleases = () => extensionFetch<{ items: ExtensionRelease[] }>('')
export const listExtensionInventory = () => extensionFetch<{ console_activation_allowed: boolean; items: ExtensionInventoryItem[] }>('/inventory')
export const scanExtensionInventory = () => extensionPost<{ items: ExtensionInventoryItem[] }>('/scan')
export const activateExtension = (id: string) => extensionPost<ExtensionInventoryItem>(`/${encodeURIComponent(id)}/activate`)
export const deactivateExtension = (id: string) => extensionPost<ExtensionInventoryItem>(`/${encodeURIComponent(id)}/deactivate`)
export const getExtension = (id: string) => extensionFetch<ExtensionOverview>(`/${encodeURIComponent(id)}`)
export const getExtensionTopology = (id: string) => extensionFetch<ExtensionTopology>(`/${encodeURIComponent(id)}/topology`)
export const getExtensionHealth = (id: string) => extensionFetch<ExtensionHealth>(`/${encodeURIComponent(id)}/health`)
export const getExtensionInvocations = (id: string) => extensionFetch<{ items: ExtensionInvocation[] }>(`/${encodeURIComponent(id)}/invocations`)
export const publishExtensionRelease = (id: string, version: string) =>
  extensionPost<Record<string, unknown>>(`/${encodeURIComponent(id)}/versions/${encodeURIComponent(version)}/publish`, {
    activation_mode: 'automatic', timeout_seconds: 300, auto_rollback: true, require_healthy_workers: true,
  })
export const createExtensionPlaygroundRun = (id: string, value: { capability_id: string; input: Record<string, unknown>; session_id?: string }) =>
  extensionPost<ExtensionPlaygroundRun>(`/${encodeURIComponent(id)}/playground/runs`, value)
