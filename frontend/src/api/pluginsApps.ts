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
}

export interface PluginsAppsResponse {
  ok: boolean
  apps: PluginApp[]
}

export async function listPluginsApps(): Promise<PluginsAppsResponse> {
  const res = await fetch(`${API_BASE}/plugins/apps`)
  if (!res.ok) throw new Error(`plugins apps: ${res.status}`)
  return res.json()
}
