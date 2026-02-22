/**
 * Config business logic: load, build update body, save.
 * Keeps Config.vue thin (binding only); payload building and API calls live here.
 */
import {
  getConfig,
  updateConfig,
  type ConfigData,
  type ConfigUpdateBody,
  type ConfigUpdateResponse,
  type WalletUpdatePayload,
} from '../api/config'

export async function loadConfig(): Promise<ConfigData | null> {
  try {
    const res = await getConfig()
    if (res.ok && res.data) return res.data
    return null
  } catch {
    return null
  }
}

/**
 * Build the full update body from current form state (local) and optional wallet payload.
 * Includes all sections that exist on local so the backend can merge.
 */
export function buildUpdateBody(
  local: ConfigData,
  walletPayload?: WalletUpdatePayload
): ConfigUpdateBody {
  const body: ConfigUpdateBody = {
    agents: local.agents,
    providers: local.providers,
    channels: local.channels,
    tools: local.tools,
    gateway: local.gateway,
  }
  if (walletPayload !== undefined) {
    body.wallet = walletPayload
  } else if (local.wallet) {
    body.wallet = { enabled: local.wallet.enabled }
  }
  if (local.auth !== undefined) body.auth = local.auth
  if (local.skills !== undefined) body.skills = local.skills
  if (local.plugins !== undefined) body.plugins = local.plugins
  if (local.approvals !== undefined) body.approvals = local.approvals
  if (local.browser !== undefined) body.browser = local.browser
  if (local.messages !== undefined) body.messages = local.messages
  if (local.commands !== undefined) body.commands = local.commands
  if (local.env !== undefined) body.env = local.env
  return body
}

export async function saveConfig(body: ConfigUpdateBody): Promise<ConfigUpdateResponse> {
  return updateConfig(body)
}

/**
 * Simple dirty check: whether local differs from the last loaded server config.
 * Compares JSON serialization of the payload we send (excludes transient wallet fields).
 */
export function isDirty(server: ConfigData | null, local: ConfigData | null): boolean {
  if (!server || !local) return false
  const a = buildUpdateBody(server)
  const b = buildUpdateBody(local)
  return JSON.stringify(a) !== JSON.stringify(b)
}
