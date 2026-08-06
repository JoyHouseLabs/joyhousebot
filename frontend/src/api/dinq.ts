import { apiFetch } from './http'
import { getIdentityHeaders } from './identity'
import type { RuntimeEvent, RuntimeRun } from './runtime'

export interface DinqCandidate {
  candidate_id: string
  name: string
  title?: string | null
  company?: string | null
  match_score?: number | null
  match_reasons: string[]
  sources: unknown[]
  profile?: Record<string, unknown> | null
  enrichment?: Record<string, unknown> | null
  enrichment_status: string
  evidence?: unknown
}

export interface DinqActivity {
  event_id?: string
  sequence?: number
  type: string
  phase?: string | null
  status?: string | null
  summary?: string | null
  data?: Record<string, unknown>
  created_at?: string
}

export interface DinqRunProjection {
  schema_version: number
  view: 'dinq.search' | string
  run: RuntimeRun & { options?: Record<string, unknown> }
  session: { session_id?: string | null; agent_id?: string | null }
  search: {
    query: string
    status: string
    phase?: string | null
    next_action?: string | null
    summary?: string | null
    conditions?: Record<string, unknown>
    missing_conditions?: string[]
    total_candidates: number
    verified_candidates: number
    tool_calls: number
  }
  candidates: DinqCandidate[]
  selected_candidate_id?: string | null
  selected_candidate?: DinqCandidate | null
  activity: DinqActivity[]
  events_cursor: number
}

export async function getDinqRunProjection(runId: string, candidateId?: string | null): Promise<DinqRunProjection> {
  const query = new URLSearchParams({ view: 'dinq.search' })
  if (candidateId) query.set('candidate_id', candidateId)
  const response = await apiFetch(`/v1/runs/${encodeURIComponent(runId)}/projection?${query.toString()}`, {
    headers: getIdentityHeaders(),
  })
  if (!response.ok) {
    let message = '读取 Dinq 运行工作台失败'
    try {
      const payload = await response.json()
      message = payload?.detail || payload?.error?.message || message
    } catch { /* keep fallback */ }
    throw new Error(message)
  }
  return response.json()
}

export type { RuntimeEvent }
