import { apiFetch } from './http'

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const headers = new Headers(init?.headers)
  if (init?.body !== undefined) headers.set('Content-Type', 'application/json')
  const response = await apiFetch(`/control/v1/admin/apps${path}`, { ...init, headers })
  const payload = await response.json().catch(() => ({}))
  if (!response.ok) throw new Error(payload?.detail || payload?.error?.message || 'App Package 请求失败')
  return payload as T
}

async function controlRequest<T>(path: string, init?: RequestInit): Promise<T> {
  const headers = new Headers(init?.headers)
  if (init?.body !== undefined) headers.set('Content-Type', 'application/json')
  const response = await apiFetch(`/control/v1${path}`, { ...init, headers })
  const payload = await response.json().catch(() => ({}))
  if (!response.ok) throw new Error(payload?.detail || payload?.error?.message || 'App 请求失败')
  return payload as T
}

export interface AppValidationReport {
  valid: boolean
  errors: string[]
  checks: Array<{ kind: string; reference: unknown; passed: boolean }>
  dependency_lock: Record<string, unknown[]>
}

export interface AppRelease {
  app_id: string
  version: string
  name: string
  description: string
  publisher: string
  status: 'draft' | 'published' | 'retired'
  manifest: Record<string, any>
  manifest_sha256: string
  validation_report: Partial<AppValidationReport>
  origin_ref?: Record<string, unknown>
  bundle_digest?: string
  updated_at?: string | null
  published_at?: string | null
}

export interface AppInstallation {
  installation_id: string
  app_id: string
  version: string
  previous_version?: string | null
  name: string
  description: string
  status: 'installed' | 'active' | 'disabled' | 'failed' | 'uninstalled'
  manifest: Record<string, any>
  dependency_lock: Record<string, unknown[]>
  updated_at?: string | null
}

export interface AppClient {
  client_id: string
  app_id: string
  name: string
  allowed_scopes: string[]
  enabled: boolean
  client_secret?: string
  last_used_at?: string | null
  created_at: string
}

export interface AppAuthorization {
  client_id: string
  installation_id: string
  scopes: string[]
  enabled: boolean
  expires_at: string
}

export interface AppCallback {
  callback_id: string
  installation_id: string
  endpoint: string
  secret_ref: string
  events: string[]
  max_attempts: number
  enabled: boolean
}

export interface AppCallbackDelivery {
  event_id: string
  run_id: string
  event_type: string
  endpoint: string
  status: 'pending' | 'sending' | 'sent' | 'dead'
  attempt: number
  max_attempts: number
  response_status?: number | null
  last_error?: string | null
  replay_of_event_id?: string | null
  replay_sequence: number
}

export interface AppUsage {
  installation_id: string
  app_id: string
  version: string
  period: { since: string; until: string }
  totals: { runs: number; model_invocations: number; input_tokens: number; output_tokens: number; billed_input_tokens: number; billed_output_tokens: number; missing_usage_invocations: number; partial_usage_invocations: number; missing_billing_invocations: number; usage_status: 'exact' | 'partial' | 'missing'; billing_status: 'exact' | 'partial' | 'missing'; model_cost_usd: number }
  statuses: Record<string, number>
  entrypoints: Array<{ entrypoint_id: string; runs: number; statuses: Record<string, number> }>
  models: Array<{ provider: string; model: string; invocations: number; input_tokens: number; output_tokens: number; billed_input_tokens: number; billed_output_tokens: number; missing_usage_invocations: number; partial_usage_invocations: number; missing_billing_invocations: number; cost_usd: number }>
  declared_meters: Array<Record<string, any>>
}

export const listAppReleases = async () => (await request<{ items: AppRelease[] }>('')).items
export const listAppInstallations = async () => (
  await request<{ items: AppInstallation[] }>('/installations/mine')
).items
export const saveAppRelease = (manifest: Record<string, any>) => request<AppRelease>(
  `/${encodeURIComponent(manifest.app_id)}/releases/${encodeURIComponent(manifest.version)}`,
  { method: 'PUT', body: JSON.stringify({ manifest }) },
)
export const validateAppRelease = (appId: string, version: string) => request<AppValidationReport>(
  `/${encodeURIComponent(appId)}/releases/${encodeURIComponent(version)}/validate`,
  { method: 'POST' },
)
export const publishAppRelease = (appId: string, version: string) => request<AppRelease>(
  `/${encodeURIComponent(appId)}/releases/${encodeURIComponent(version)}/publish`,
  { method: 'POST' },
)
export const installAppRelease = (release: AppRelease, configuration: Record<string, unknown> = {}) => request<AppInstallation>(
  `/${encodeURIComponent(release.app_id)}/install`,
  {
    method: 'POST',
    body: JSON.stringify({
      version: release.version,
      configuration,
      granted_permissions: release.manifest.permissions || [],
    }),
  },
)
export const transitionAppInstallation = (
  installationId: string,
  action: 'activate' | 'disable' | 'rollback' | 'uninstall',
) => request<AppInstallation>(
  `/installations/${encodeURIComponent(installationId)}/actions`,
  { method: 'POST', body: JSON.stringify({ action }) },
)

export const listAppClients = async (appId?: string) => (
  await request<{ items: AppClient[] }>(`/clients${appId ? `?app_id=${encodeURIComponent(appId)}` : ''}`)
).items
export const createAppClient = (value: { app_id: string; name: string; allowed_scopes: string[] }) => request<AppClient>(
  '/clients', { method: 'POST', body: JSON.stringify(value) },
)
export const rotateAppClientSecret = (clientId: string) => request<AppClient>(
  `/clients/${encodeURIComponent(clientId)}/rotate-secret`, { method: 'POST' },
)
export const revokeAppClient = (clientId: string) => request<{ revoked: boolean }>(
  `/clients/${encodeURIComponent(clientId)}`, { method: 'DELETE' },
)
export const getAppUsage = (installationId: string) => controlRequest<AppUsage>(
  `/apps/${encodeURIComponent(installationId)}/usage`,
)
export const listAppCallbacks = async (installationId: string) => (
  await controlRequest<{ items: AppCallback[] }>(`/apps/${encodeURIComponent(installationId)}/callbacks`)
).items
export const registerAppCallback = (installationId: string, value: { endpoint: string; secret_ref: string; events: string[]; max_attempts: number }) => controlRequest<AppCallback>(
  `/apps/${encodeURIComponent(installationId)}/callbacks`, { method: 'POST', body: JSON.stringify(value) },
)
export const revokeAppCallback = (installationId: string, callbackId: string) => controlRequest<{ revoked: boolean }>(
  `/apps/${encodeURIComponent(installationId)}/callbacks/${encodeURIComponent(callbackId)}`, { method: 'DELETE' },
)
export const listAppAuthorizations = async (installationId: string) => (
  await controlRequest<{ items: AppAuthorization[] }>(`/apps/${encodeURIComponent(installationId)}/authorizations`)
).items
export const authorizeAppInstallation = (installationId: string, value: { client_id: string; scopes: string[]; expires_at: string }) => controlRequest<AppAuthorization>(
  `/apps/${encodeURIComponent(installationId)}/authorizations/${encodeURIComponent(value.client_id)}`,
  { method: 'PUT', body: JSON.stringify({ scopes: value.scopes, expires_at: value.expires_at }) },
)
export const revokeAppAuthorization = (installationId: string, clientId: string) => controlRequest<{ revoked: boolean }>(
  `/apps/${encodeURIComponent(installationId)}/authorizations/${encodeURIComponent(clientId)}`,
  { method: 'DELETE' },
)
export const listRunAppCallbacks = async (runId: string) => (
  await controlRequest<{ items: AppCallbackDelivery[] }>(`/runs/${encodeURIComponent(runId)}/app-callbacks`)
).items
export const replayRunAppCallback = (runId: string, eventId: string) => controlRequest<AppCallbackDelivery>(
  `/runs/${encodeURIComponent(runId)}/app-callbacks/${encodeURIComponent(eventId)}/replay`,
  { method: 'POST', headers: { 'Idempotency-Key': `console-replay-${crypto.randomUUID()}` } },
)
