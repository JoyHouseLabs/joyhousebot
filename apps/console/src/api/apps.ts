import { apiFetch } from './http'

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const headers = new Headers(init?.headers)
  if (init?.body !== undefined) headers.set('Content-Type', 'application/json')
  const response = await apiFetch(`/v1/admin/apps${path}`, { ...init, headers })
  const payload = await response.json().catch(() => ({}))
  if (!response.ok) throw new Error(payload?.detail || payload?.error?.message || 'App Pack 请求失败')
  return payload as T
}

async function publicRequest<T>(path: string, init?: RequestInit): Promise<T> {
  const headers = new Headers(init?.headers)
  if (init?.body !== undefined) headers.set('Content-Type', 'application/json')
  const response = await apiFetch(`/v1${path}`, { ...init, headers })
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

export interface MarketRegistry {
  registry_id: string
  market_id: string
  base_url: string
  status: 'active' | 'disabled' | 'compromised'
  protocol_version: string
  discovery: Record<string, any>
  policy: Record<string, any>
  auth_token_ref: string
  last_refreshed_at?: string | null
}

export interface MarketInstallationKey {
  registry_id: string
  user_id: string
  key_id: string
  public_key: string
  key_thumbprint: string
  status: string
}

export interface AppAcquisition {
  acquisition_id: string
  registry_id: string
  publisher_id: string
  app_id: string
  requested_version: string
  resolved_version: string
  channel: 'stable' | 'beta' | 'security'
  status: 'requested' | 'resolving' | 'fetching' | 'verifying' | 'staged' | 'awaiting_acceptance' | 'imported' | 'rejected' | 'quarantined' | 'failed'
  acquisition_policy: 'manual' | 'download' | 'stage'
  verification_report: Record<string, any>
  permission_diff: Record<string, any>
  bundle_digest: string
  error?: { type?: string; message?: string } | null
  updated_at?: string | null
}

export interface UpdateSubscription {
  subscription_id: string
  installation_id: string
  registry_id: string
  publisher_id: string
  app_id: string
  channel: 'stable' | 'beta' | 'security'
  version_constraint: string
  policy: 'notify' | 'download' | 'stage' | 'activate_safe'
  status: 'active' | 'paused' | 'removed'
  current_version: string
  latest_release: Record<string, any>
  last_error?: { type?: string; message?: string } | null
  last_checked_at?: string | null
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

export interface AppGrant {
  grant_id: string
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

export const listAppPacks = async () => (await request<{ items: AppRelease[] }>('')).items
export const listAppInstallations = async () => (
  await request<{ items: AppInstallation[] }>('/installations/mine')
).items
export const listMarketRegistries = async () => (
  await request<{ items: MarketRegistry[] }>('/market/registries')
).items
export const registerMarketRegistry = (value: {
  base_url: string
  trusted_root: Record<string, any>
  discovery: Record<string, any>
  auth_token_ref?: string
  policy?: Record<string, any>
}) => request<MarketRegistry>('/market/registries', {
  method: 'POST', body: JSON.stringify(value),
})
export const ensureMarketInstallationKey = (registryId: string) => request<MarketInstallationKey>(
  `/market/registries/${encodeURIComponent(registryId)}/installation-key`,
  { method: 'POST' },
)
export const listAppAcquisitions = async () => (
  await request<{ items: AppAcquisition[] }>('/market/acquisitions')
).items
export const acquireMarketApp = (value: {
  registry_id: string
  publisher_id: string
  app_id: string
  version?: string | null
  channel: 'stable' | 'beta' | 'security'
  offer_id?: string | null
  entitlement?: Record<string, any> | null
}) => request<AppAcquisition>('/market/acquisitions', {
  method: 'POST',
  headers: { 'Idempotency-Key': `console-market-${crypto.randomUUID()}` },
  body: JSON.stringify(value),
})
export const actOnAppAcquisition = (
  acquisitionId: string,
  action: 'accept' | 'reject',
) => request<AppAcquisition>(
  `/market/acquisitions/${encodeURIComponent(acquisitionId)}/actions`,
  { method: 'POST', body: JSON.stringify({ action }) },
)
export const listUpdateSubscriptions = async () => (
  await request<{ items: UpdateSubscription[] }>('/market/update-subscriptions')
).items
export const saveUpdateSubscription = (value: {
  installation_id: string
  registry_id: string
  publisher_id: string
  app_id: string
  channel: 'stable' | 'beta' | 'security'
  version_constraint: string
  policy: 'notify' | 'download' | 'stage'
  allow_security_patch_download: boolean
  allow_auto_stage: boolean
  allow_auto_activate: false
}) => request<UpdateSubscription>(
  `/market/update-subscriptions/${encodeURIComponent(value.installation_id)}`,
  { method: 'PUT', body: JSON.stringify(value) },
)
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
export const installAppPack = (release: AppRelease, configuration: Record<string, unknown> = {}) => request<AppInstallation>(
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
export const transitionAppPack = (
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
export const getAppUsage = (installationId: string) => publicRequest<AppUsage>(
  `/apps/${encodeURIComponent(installationId)}/usage`,
)
export const listAppCallbacks = async (installationId: string) => (
  await publicRequest<{ items: AppCallback[] }>(`/apps/${encodeURIComponent(installationId)}/callbacks`)
).items
export const registerAppCallback = (installationId: string, value: { endpoint: string; secret_ref: string; events: string[]; max_attempts: number }) => publicRequest<AppCallback>(
  `/apps/${encodeURIComponent(installationId)}/callbacks`, { method: 'POST', body: JSON.stringify(value) },
)
export const revokeAppCallback = (installationId: string, callbackId: string) => publicRequest<{ revoked: boolean }>(
  `/apps/${encodeURIComponent(installationId)}/callbacks/${encodeURIComponent(callbackId)}`, { method: 'DELETE' },
)
export const listAppGrants = async (installationId: string) => (
  await publicRequest<{ items: AppGrant[] }>(`/apps/${encodeURIComponent(installationId)}/delegations`)
).items
export const authorizeAppGrant = (installationId: string, value: { client_id: string; scopes: string[]; expires_at: string }) => publicRequest<AppGrant>(
  `/apps/${encodeURIComponent(installationId)}/delegations`, { method: 'POST', body: JSON.stringify(value) },
)
export const revokeAppGrant = (grantId: string) => publicRequest<{ revoked: boolean }>(
  `/apps/delegations/${encodeURIComponent(grantId)}`, { method: 'DELETE' },
)
export const listRunAppCallbacks = async (runId: string) => (
  await publicRequest<{ items: AppCallbackDelivery[] }>(`/runs/${encodeURIComponent(runId)}/app-callbacks`)
).items
export const replayRunAppCallback = (runId: string, eventId: string) => publicRequest<AppCallbackDelivery>(
  `/runs/${encodeURIComponent(runId)}/app-callbacks/${encodeURIComponent(eventId)}/replay`,
  { method: 'POST', headers: { 'Idempotency-Key': `console-replay-${crypto.randomUUID()}` } },
)
