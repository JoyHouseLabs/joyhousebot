import { apiFetch } from './http'

const API_BASE = '/api'

export interface PluginApp {
  app_id: string
  name: string
  route: string
  entry: string
  plugin_id: string
  base_path: string
  base_url?: string
  enabled: boolean
  /** URL for opening app in new tab (standalone page). */
  app_link?: string
  /** Icon image URL. */
  icon_url?: string
  /** Short description for the app. */
  description?: string
  /** User-facing activation phrase, e.g. "打开应用". */
  activation_command?: string
}

export interface PluginsAppsResponse {
  ok: boolean
  apps: PluginApp[]
}

export async function listPluginsApps(): Promise<PluginsAppsResponse> {
  const res = await apiFetch(`${API_BASE}/plugins/apps`)
  if (!res.ok) throw new Error(`plugins apps: ${res.status}`)
  return res.json()
}
