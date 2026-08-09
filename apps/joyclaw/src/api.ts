export interface AgentSummary {
  id: string
  name: string
  description?: string
  is_default?: boolean
}

export interface RuntimeRun {
  run_id: string
  session_id: string
  agent_id: string
  status: string
  current_phase?: string | null
  status_summary?: string | null
  next_action?: string | null
  waiting_on?: string | null
  prompt?: string
  created_at?: string
  updated_at?: string
  finished_at?: string | null
  result?: { content?: string; error?: string } | null
  error?: { message?: string } | null
}

export interface WorkSummary {
  work_id: string
  public_slug: string
  title: string
  description: string
  status: 'draft' | 'published' | 'archived'
  visibility: 'private' | 'unlisted' | 'public'
  current_version: number
  updated_at: string
}

export interface ScheduleSummary {
  id: string
  name: string
  agent_id: string
  enabled: boolean
  schedule: {
    kind: 'at' | 'every' | 'cron'
    at_ms?: number | null
    every_ms?: number | null
    expr?: string | null
    tz?: string | null
  }
  payload: { message: string; kind?: string }
  state: { next_run_at_ms?: number | null; last_run_at_ms?: number | null; last_status?: string | null }
}

const TOKEN_KEY = 'joyclaw_api_token'
const USER_KEY = 'joyhousebot_user_id'
const AGENT_KEY = 'joyclaw_agent_id'
const DEFAULT_USER_ID = String(import.meta.env.VITE_DEFAULT_USER_ID || 'joyhousebot')

let memoryToken = ''

function tokenFromUrl(): string {
  if (typeof window === 'undefined') return ''
  const url = new URL(window.location.href)
  const token = (url.searchParams.get('token') || '').trim()
  if (!token) return ''
  setApiToken(token)
  url.searchParams.delete('token')
  window.history.replaceState(null, '', `${url.pathname}${url.search}${url.hash}`)
  return token
}

export function getApiToken(): string {
  const fromUrl = tokenFromUrl()
  if (fromUrl) return fromUrl
  try { return sessionStorage.getItem(TOKEN_KEY)?.trim() || memoryToken }
  catch { return memoryToken }
}

export function setApiToken(token: string): void {
  memoryToken = token.trim()
  try {
    if (memoryToken) sessionStorage.setItem(TOKEN_KEY, memoryToken)
    else sessionStorage.removeItem(TOKEN_KEY)
  } catch { /* session-only fallback remains in memory */ }
}

export function getUserId(): string {
  try { return localStorage.getItem(USER_KEY)?.trim() || DEFAULT_USER_ID }
  catch { return DEFAULT_USER_ID }
}

export function setUserId(userId: string): void {
  const normalized = userId.trim()
  if (!normalized) return
  try { localStorage.setItem(USER_KEY, normalized) } catch { /* local dev fallback */ }
}

export function getPreferredAgentId(): string {
  try { return localStorage.getItem(AGENT_KEY)?.trim() || '' }
  catch { return '' }
}

export function setPreferredAgentId(agentId: string): void {
  try { localStorage.setItem(AGENT_KEY, agentId.trim()) } catch { /* optional preference */ }
}

export function consoleUrl(path = ''): string {
  const configured = String(import.meta.env.VITE_CONSOLE_URL || '').trim()
  const base = configured || (import.meta.env.DEV ? 'http://127.0.0.1:5178/ui' : '/ui')
  return `${base.replace(/\/$/, '')}/${path.replace(/^\//, '')}`
}

function headers(json = false): Headers {
  const result = new Headers({ 'X-User-ID': getUserId() })
  const token = getApiToken()
  if (token) result.set('Authorization', `Bearer ${token}`)
  if (json) result.set('Content-Type', 'application/json')
  return result
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const merged = new Headers(init.headers)
  headers(Boolean(init.body)).forEach((value, key) => {
    if (!merged.has(key)) merged.set(key, value)
  })
  const response = await fetch(path, { ...init, headers: merged })
  const payload = await response.json().catch(() => ({}))
  if (!response.ok) {
    const message = payload?.error?.message ?? payload?.detail ?? `请求失败 (${response.status})`
    throw new Error(typeof message === 'string' ? message : JSON.stringify(message))
  }
  return payload as T
}

export async function checkHealth(): Promise<boolean> {
  try { return (await fetch('/healthz')).ok } catch { return false }
}

export async function listAgents(): Promise<AgentSummary[]> {
  return (await request<{ items: AgentSummary[] }>('/v1/agents')).items ?? []
}

export async function resolveDefaultAgent(): Promise<string> {
  const preferred = getPreferredAgentId()
  const agents = await listAgents()
  if (preferred && agents.some((item) => item.id === preferred)) return preferred
  const selected = agents.find((item) => item.is_default) ?? agents[0]
  return selected?.id || 'main-coordinator'
}

export async function submitGoal(goal: string): Promise<RuntimeRun> {
  const agentId = await resolveDefaultAgent()
  return request<RuntimeRun>('/v1/runs', {
    method: 'POST',
    headers: { 'Idempotency-Key': crypto.randomUUID() },
    body: JSON.stringify({
      input: { type: 'message', content: goal },
      session_id: 'joyclaw-main',
      agent_id: agentId,
      execution_mode: 'auto',
      metadata: { channel: 'joyclaw', chat_id: 'joyclaw-main', product: 'joyclaw' },
    }),
  })
}

export async function listRuns(limit = 50): Promise<RuntimeRun[]> {
  return (await request<{ items: RuntimeRun[] }>(`/v1/runs?limit=${limit}`)).items ?? []
}

export async function getRun(runId: string): Promise<RuntimeRun> {
  return request(`/v1/runs/${encodeURIComponent(runId)}`)
}

export async function cancelRun(runId: string): Promise<void> {
  await request(`/v1/runs/${encodeURIComponent(runId)}/cancel`, { method: 'POST' })
}

export async function listWorks(): Promise<WorkSummary[]> {
  return (await request<{ items: WorkSummary[] }>('/v1/works')).items ?? []
}

export async function listSchedules(): Promise<ScheduleSummary[]> {
  return (await request<{ items: ScheduleSummary[] }>('/v1/schedules?include_disabled=true')).items ?? []
}
