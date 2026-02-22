/**
 * Control API types and HTTP helpers.
 * Control pages use WS RPC via useGateway/gateway-client; these fetch() helpers
 * are kept for type definitions and non-control / fallback use only.
 */
const API_BASE = '/api'

export interface ControlOverview {
  ok: boolean
  health: boolean
  uptime_seconds: number | null
  gateway: { host: string; port: number }
  sessions_count: number
  presence_count?: number
  cron: {
    enabled: boolean
    jobs: number
    next_wake_at_ms: number | null
  } | null
  channels: {
    count: number
    running: number
    channels: Record<string, { enabled: boolean; running: boolean }>
  } | null
}

export async function getOverview(): Promise<ControlOverview> {
  const res = await fetch(`${API_BASE}/control/overview`)
  if (!res.ok) throw new Error(await res.text())
  return res.json()
}

export interface ChannelStatus {
  name: string
  enabled: boolean
  running: boolean
}

export interface ControlChannelsResponse {
  ok: boolean
  channels: ChannelStatus[]
}

export async function getChannels(): Promise<ControlChannelsResponse> {
  const res = await fetch(`${API_BASE}/control/channels`)
  if (!res.ok) throw new Error(await res.text())
  return res.json()
}

export interface PresenceEntry {
  instance_id: string
  ts: number
  reason: string
  mode: string
  last_input_seconds: number | null
  ip: string | null
  host: string | null
  version: string | null
  device_family: string | null
  model_identifier: string | null
}

export interface ControlPresenceResponse {
  ok: boolean
  presence: PresenceEntry[]
}

export async function getPresence(): Promise<ControlPresenceResponse> {
  const res = await fetch(`${API_BASE}/control/presence`)
  if (!res.ok) throw new Error(await res.text())
  return res.json()
}

export interface QueueLaneSession {
  sessionKey: string
  runningRunId: string | null
  queued: number
  queueDepth: number
  headWaitMs: number | null
  oldestEnqueuedAt: number | null
}

export interface QueueSummary {
  runningSessions?: number
  queuedSessions?: number
  totalQueued?: number
}

export interface ControlQueueResponse {
  ok: boolean
  sessions: QueueLaneSession[]
  summary: QueueSummary
  ts: number
}

export async function getQueueMetrics(): Promise<ControlQueueResponse> {
  const res = await fetch(`${API_BASE}/control/queue`)
  if (!res.ok) throw new Error(await res.text())
  return res.json()
}

// ---------- Traces (agent run observability) ----------

export interface TraceItem {
  traceId: string
  sessionKey: string
  status: string
  startedAtMs: number
  endedAtMs: number | null
  errorText: string | null
  toolsUsed: string
  messagePreview: string | null
}

export interface TraceStep {
  type: string
  payload: Record<string, unknown>
  ts_ms: number
}

export interface TraceDetail extends TraceItem {
  stepsJson: string
  updatedAt: string
}

export interface TracesListResponse {
  items: TraceItem[]
  nextCursor?: string
}

export async function getTraces(params?: {
  session_key?: string
  limit?: number
  cursor?: string
}): Promise<TracesListResponse> {
  const sp = new URLSearchParams()
  if (params?.session_key) sp.set('session_key', params.session_key)
  if (params?.limit != null) sp.set('limit', String(params.limit))
  if (params?.cursor) sp.set('cursor', params.cursor)
  const q = sp.toString()
  const url = `${API_BASE}/traces${q ? `?${q}` : ''}`
  const res = await fetch(url)
  if (!res.ok) throw new Error(await res.text())
  return res.json()
}

export async function getTrace(traceId: string): Promise<TraceDetail> {
  const res = await fetch(`${API_BASE}/traces/${encodeURIComponent(traceId)}`)
  if (!res.ok) throw new Error(await res.text())
  return res.json()
}
