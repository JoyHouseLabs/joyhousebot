import { apiFetch } from './http'

const API_BASE = '/api'

export interface SandboxContainerItem {
  id: string
  idFull?: string
  names?: string
  image?: string
  browser?: boolean
}

export interface SandboxContainersResponse {
  ok: boolean
  items: SandboxContainerItem[]
}

export async function listSandboxContainers(
  browserOnly = false
): Promise<SandboxContainersResponse> {
  const res = await apiFetch(
    `${API_BASE}/sandbox/containers?browser_only=${browserOnly}`
  )
  if (!res.ok) throw new Error(await res.text())
  return res.json()
}

export interface SandboxPolicy {
  restrict_to_workspace?: boolean
  exec_timeout?: number
  exec_shell_mode?: boolean
  container_enabled?: boolean
  container_image?: string
}

export interface SandboxExplainResponse {
  session: string
  agent: string
  policy: SandboxPolicy
  custom_policy?: Record<string, unknown>
  docker_available: boolean
  backend: 'docker' | 'direct'
  containers_count: number
}

export async function getSandboxExplain(
  session = '',
  agent = ''
): Promise<SandboxExplainResponse> {
  const params = new URLSearchParams()
  if (session) params.set('session', session)
  if (agent) params.set('agent', agent)
  const qs = params.toString()
  const res = await apiFetch(`${API_BASE}/sandbox/explain${qs ? `?${qs}` : ''}`)
  if (!res.ok) throw new Error(await res.text())
  return res.json()
}

export interface SandboxRecreateBody {
  all?: boolean
  session?: string | null
  agent?: string | null
  browser_only?: boolean
  force?: boolean
}

export interface SandboxRecreateResponse {
  ok: boolean
  operation: {
    requestedAtMs: number
    all: boolean
    session: string | null
    agent: string | null
    browserOnly: boolean
    force: boolean
    removed: string[]
    dockerAvailable: boolean
  }
  removed: string[]
}

export async function sandboxRecreate(
  body: SandboxRecreateBody
): Promise<SandboxRecreateResponse> {
  const res = await apiFetch(`${API_BASE}/sandbox/recreate`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      all: body.all ?? false,
      session: body.session ?? null,
      agent: body.agent ?? null,
      browser_only: body.browser_only ?? false,
      force: body.force ?? false,
    }),
  })
  if (!res.ok) throw new Error(await res.text())
  return res.json()
}
