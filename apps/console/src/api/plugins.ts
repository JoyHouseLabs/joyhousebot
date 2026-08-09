import { getApiHeaders } from './http'
import { getIdentityHeaders } from './identity'

function headers(): Headers {
  const value = new Headers()
  for (const [key, item] of Object.entries(getApiHeaders())) value.set(key, item)
  for (const [key, item] of Object.entries(getIdentityHeaders())) value.set(key, item)
  return value
}

async function pluginFetch<T>(path: string): Promise<T> {
  const response = await fetch(`/v1/admin/plugins${path}`, { headers: headers() })
  const payload = await response.json().catch(() => ({}))
  if (!response.ok) throw new Error(payload?.detail || '插件控制面请求失败')
  return payload as T
}

async function pluginPost<T>(path: string, body?: unknown): Promise<T> {
  const requestHeaders = headers()
  if (body !== undefined) requestHeaders.set('Content-Type', 'application/json')
  const response = await fetch(`/v1/admin/plugins${path}`, {
    method: 'POST', headers: requestHeaders,
    body: body === undefined ? undefined : JSON.stringify(body),
  })
  const payload = await response.json().catch(() => ({}))
  if (!response.ok) throw new Error(payload?.detail || '插件诊断请求失败')
  return payload as T
}

export interface PluginComponent {
  component_id: string; component_type: string; name: string; description: string
  reference_id: string; reference_version: string; metadata: Record<string, unknown>
}
export interface PluginQuickstart {
  quickstart_id: string; title: string; description: string; prompt: string
  agent_id: string; scenario_id?: string | null; scenario_inputs?: Record<string, unknown>; capability_ids: string[]
  required_connection_ids: string[]; expected_outcome?: string
}
export interface PluginMetrics {
  hours: number; total: number; succeeded: number; failed: number; success_rate: number
  p50_duration_ms: number | null; p95_duration_ms: number | null
  by_component: Array<{ capability_id: string; total: number; succeeded: number; failed: number; p50_duration_ms: number | null; p95_duration_ms: number | null }>
}
export interface PluginListItem {
  plugin_id: string; version: string; name: string; description: string
  distribution_name: string; build_digest: string; status: string
  component_count: number; metrics: PluginMetrics
  manifest: { dependencies?: Array<Record<string, unknown>>; quickstarts?: PluginQuickstart[] }
  created_at: string; updated_at: string
}
export interface PluginOverview {
  release: { plugin_id: string; version: string; status: string; name: string; description: string; distribution_name: string; build_digest: string; manifest: { dependencies?: Array<Record<string, unknown>>; quickstarts?: PluginQuickstart[] } }
  releases: Array<{ plugin_id: string; version: string; status: string; build_digest: string; updated_at: string }>
  components: PluginComponent[]; metrics: PluginMetrics; worker_summary: { total: number; healthy_loaded: number }
}
export interface PluginTopology { nodes: Array<{ id: string; kind: string; label: string; data?: Record<string, unknown> }>; edges: Array<{ source: string; target: string; kind: string }> }
export interface PluginInvocation { invocation_id: string; capability_id: string; run_id: string; status: string; worker_id?: string | null; created_at: string; error?: Record<string, unknown> | null }
export interface PluginHealth { status: string; checks: Array<{ name: string; status: string; summary: string }> }
export interface PluginPlaygroundRun { run_id: string; user_id: string; status: string; session_id: string; agent_id: string; prompt: string }

export const listPlugins = () => pluginFetch<{ items: PluginListItem[] }>('')
export const getPlugin = (id: string) => pluginFetch<PluginOverview>(`/${encodeURIComponent(id)}`)
export const getPluginTopology = (id: string) => pluginFetch<PluginTopology>(`/${encodeURIComponent(id)}/topology`)
export const getPluginHealth = (id: string) => pluginFetch<PluginHealth>(`/${encodeURIComponent(id)}/health`)
export const getPluginInvocations = (id: string) => pluginFetch<{ items: PluginInvocation[] }>(`/${encodeURIComponent(id)}/invocations`)
export const runPluginDiagnostics = (id: string) => pluginPost<{ items: PluginHealth['checks'] }>(`/${encodeURIComponent(id)}/diagnostics`)
export const publishPluginRelease = (id: string, version: string) =>
  pluginPost<Record<string, unknown>>(`/${encodeURIComponent(id)}/versions/${encodeURIComponent(version)}/publish`, {
    activation_mode: 'automatic', timeout_seconds: 300, auto_rollback: true, require_healthy_workers: true,
  })
export const createPluginPlaygroundRun = (id: string, value: { capability_id: string; input: Record<string, unknown>; session_id?: string }) =>
  pluginPost<PluginPlaygroundRun>(`/${encodeURIComponent(id)}/playground/runs`, value)
