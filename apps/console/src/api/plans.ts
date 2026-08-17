import { apiFetch } from './http'

/** Public plan-preview and confirmation contract for team planning Runs. */

export interface PlanStep {
  id: string
  name: string
  objective: string
  phase: string
  kind: 'produce' | 'review' | 'revise' | 'synthesize' | 'checkpoint'
  member_id: string
  depends_on: string[]
  acceptance_criteria: string[]
}

export interface StagePhase {
  id: string
  kind: string
  participants: string[]
  mode: string
  depends_on: string[]
  step_ids: string[]
}

export interface RunPlan {
  run_id: string
  plan_version: number
  status: string
  awaiting_confirmation: boolean
  actions: Array<'confirm' | 'regenerate' | 'cancel'>
  plan: { intent?: string; summary?: string; planned_steps: PlanStep[]; estimated_duration_seconds?: number }
  stage_graph: { phases: StagePhase[]; unassigned_step_ids: string[] }
  estimate: { task_count: number; phase_count: number; max_concurrent?: number }
  confirmation: {
    requested_at?: string | null
    expires_at?: string | null
    feedback?: string | null
    action_at?: string | null
    action_by?: string | null
    team_id: string
    team_revision_id: string
  }
}

async function jsonOrThrow<T>(response: Response, fallback: string): Promise<T> {
  const payload = await response.json().catch(() => ({}))
  if (!response.ok) throw new Error(payload?.detail || payload?.error?.message || fallback)
  return payload as T
}

export const getRunPlan = async (runId: string) => jsonOrThrow<RunPlan>(
  await apiFetch(`/v1/runs/${encodeURIComponent(runId)}/plan`),
  '读取执行计划失败',
)

export const actRunPlan = async (runId: string, action: 'confirm' | 'regenerate' | 'cancel', feedback?: string) => jsonOrThrow<{ run: Record<string, unknown>; plan_confirmation: Record<string, unknown>; no_op?: boolean }>(
  await apiFetch(`/v1/runs/${encodeURIComponent(runId)}/plan/confirmation`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ action, feedback: feedback || null }),
  }),
  '提交计划确认失败',
)
