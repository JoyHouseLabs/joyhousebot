import { apiFetch } from './http'

export type WorkflowNodeKind =
  | 'agent'
  | 'team'
  | 'scenario'
  | 'approval'
  | 'verify'
  | 'branch'
  | 'bounded_loop'

export interface WorkflowNode {
  id: string
  name: string
  objective: string
  kind: WorkflowNodeKind
  agent_id: string | null
  team_id?: string | null
  scenario_id?: string | null
  scenario_version?: number | null
  scenario_inputs?: Record<string, unknown>
  dependencies: string[]
  allowed_tools: string[]
  skills: string[]
  max_attempts: number
  configuration?: Record<string, unknown>
  subrun?: {
    mode: 'team' | 'scenario'
    team_id?: string
    team_revision_id?: string
    team_version?: number
    scenario_id?: string
    scenario_version?: number
    inputs?: Record<string, unknown>
  }
  output_schema?: Record<string, unknown> | null
  verification_policy?: Record<string, unknown>
}

export interface WorkflowGraph {
  schema_version: number
  name: string
  summary: string
  risk_level: 'low' | 'medium' | 'high'
  estimated_duration_minutes: number
  nodes: WorkflowNode[]
  edges: Array<{ source: string; target: string }>
  policies: { max_concurrent: number; fail_fast: boolean; aggregate: boolean }
}

export interface WorkflowRevision {
  revision_id: string
  workflow_id: string
  version: number
  status: 'draft' | 'published' | 'superseded'
  goal: string
  graph: WorkflowGraph
  change_note: string
  source_run_id?: string | null
  created_at?: string | null
  published_at?: string | null
}

export interface Workflow {
  workflow_id: string
  name: string
  description: string
  status: 'draft' | 'published'
  current_revision_id: string
  published_revision_id?: string | null
  created_at?: string | null
  updated_at?: string | null
  revision: WorkflowRevision
  revisions?: WorkflowRevision[]
}

export interface WorkflowDraft {
  name: string
  description: string
  goal: string
  graph: WorkflowGraph
}

export interface WorkflowGeneration {
  run_id: string
  status: string
  status_summary?: string | null
  error?: { message?: string } | string | null
  draft?: WorkflowDraft
}

interface SaveWorkflowInput {
  name: string
  description: string
  goal: string
  graph: WorkflowGraph
  change_note?: string
  source_run_id?: string | null
}

async function payload<T>(response: Response, fallback: string): Promise<T> {
  const body = response.status === 204 ? null : await response.json()
  if (!response.ok) throw new Error(body?.error?.message ?? body?.detail ?? fallback)
  return body as T
}

export async function listWorkflows(): Promise<Workflow[]> {
  const response = await apiFetch('/v1/workflows')
  return (await payload<{ items: Workflow[] }>(response, '读取工作流失败')).items
}

export async function getWorkflow(workflowId: string): Promise<Workflow> {
  return payload<Workflow>(
    await apiFetch(`/v1/workflows/${encodeURIComponent(workflowId)}`),
    '读取工作流详情失败',
  )
}

export async function startWorkflowGeneration(input: {
  goal: string
  instruction?: string
  workflow_id?: string
  base_graph?: WorkflowGraph
}): Promise<{ run_id: string; status: string }> {
  return payload(
    await apiFetch('/v1/workflows/generations', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'Idempotency-Key': crypto.randomUUID() },
      body: JSON.stringify(input),
    }),
    '提交工作流设计任务失败',
  )
}

export async function getWorkflowGeneration(runId: string): Promise<WorkflowGeneration> {
  return payload(
    await apiFetch(`/v1/workflows/generations/${encodeURIComponent(runId)}`),
    '读取工作流设计结果失败',
  )
}

export async function createWorkflow(input: SaveWorkflowInput): Promise<Workflow> {
  return payload(
    await apiFetch('/v1/workflows', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(input),
    }),
    '保存工作流失败',
  )
}

export async function createWorkflowRevision(
  workflowId: string,
  input: SaveWorkflowInput,
): Promise<Workflow> {
  return payload(
    await apiFetch(`/v1/workflows/${encodeURIComponent(workflowId)}/revisions`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(input),
    }),
    '保存工作流版本失败',
  )
}

export async function publishWorkflow(
  workflowId: string,
  revisionId: string,
): Promise<Workflow> {
  return payload(
    await apiFetch(`/v1/workflows/${encodeURIComponent(workflowId)}/publish`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ revision_id: revisionId }),
    }),
    '发布工作流失败',
  )
}

export async function executeWorkflow(
  workflowId: string,
  input: { revision_id?: string; input?: string; preview?: boolean },
): Promise<{ run_id: string; status: string }> {
  return payload(
    await apiFetch(`/v1/workflows/${encodeURIComponent(workflowId)}/runs`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'Idempotency-Key': crypto.randomUUID() },
      body: JSON.stringify(input),
    }),
    '启动工作流失败',
  )
}

export async function deleteWorkflow(workflowId: string): Promise<void> {
  await payload(
    await apiFetch(`/v1/workflows/${encodeURIComponent(workflowId)}`, { method: 'DELETE' }),
    '删除工作流失败',
  )
}
