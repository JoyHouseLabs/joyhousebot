import { apiFetch } from './http'
import { getIdentityHeaders } from './identity'
import type { RunInputField, RuntimeRun } from './runtime'

export interface ActionRunReference {
  run_id: string
  agent_id: string
  status: string
  title: string
  updated_at: string
}

export interface InputActionItem {
  item_id: string
  kind: 'input'
  created_at: string
  expires_at?: string | null
  run: ActionRunReference
  input: {
    input_request_id: string
    question: string
    fields: RunInputField[]
    presentation?: Record<string, unknown>
    source: 'scenario' | 'agent'
  }
}

export interface ApprovalActionItem {
  item_id: string
  kind: 'approval'
  created_at: string
  expires_at?: string | null
  run: ActionRunReference
  approval: {
    approval_id: string
    subject_type: string
    subject: Record<string, unknown>
    capability_ref: { capability_id?: string; version?: string; [key: string]: unknown }
    input_preview: Record<string, unknown>
    risk: string
    data_classification: string
    required_role: string
    can_resolve: boolean
  }
}

export type ActionItem = InputActionItem | ApprovalActionItem

async function readError(response: Response, fallback: string): Promise<Error> {
  const payload = await response.json().catch(() => ({}))
  return new Error(payload?.error?.message ?? payload?.detail ?? fallback)
}

export async function listActionItems(): Promise<ActionItem[]> {
  const response = await apiFetch('/v1/action-items?limit=100', { headers: getIdentityHeaders() })
  if (!response.ok) throw await readError(response, '读取待处理事项失败')
  return (await response.json()).items ?? []
}

export async function resolveActionInput(
  item: InputActionItem,
  answers: Record<string, unknown>,
): Promise<RuntimeRun> {
  const response = await apiFetch(`/v1/runs/${encodeURIComponent(item.run.run_id)}/inputs`, {
    method: 'POST',
    headers: { ...getIdentityHeaders(), 'Content-Type': 'application/json', 'Idempotency-Key': crypto.randomUUID() },
    body: JSON.stringify({ input_request_id: item.input.input_request_id, answers }),
  })
  if (!response.ok) throw await readError(response, '提交补充信息失败')
  return (await response.json()).run
}

export async function resolveActionApproval(
  item: ApprovalActionItem,
  resolution: 'approve' | 'reject',
  note = '',
): Promise<RuntimeRun> {
  const response = await apiFetch(
    `/v1/runs/${encodeURIComponent(item.run.run_id)}/approvals/${encodeURIComponent(item.approval.approval_id)}/resolve`,
    {
      method: 'POST',
      headers: { ...getIdentityHeaders(), 'Content-Type': 'application/json', 'Idempotency-Key': crypto.randomUUID() },
      body: JSON.stringify({ resolution, note: note.trim() || null }),
    },
  )
  if (!response.ok) throw await readError(response, '处理审批失败')
  return (await response.json()).run
}
