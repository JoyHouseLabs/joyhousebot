import { apiFetch, getApiHeaders } from './http'
import { getIdentityHeaders } from './identity'

export { getRuntimeUserId } from './identity'

export interface RuntimeRun {
  run_id: string
  user_id: string
  session_id: string
  agent_id: string
  status: string
  current_phase?: string | null
  status_summary?: string | null
  status_reason?: string | null
  next_action?: string | null
  waiting_on?: string | null
  completed_task_count?: number
  total_task_count?: number
  last_event_sequence?: number
  created_at?: string
  started_at?: string | null
  finished_at?: string | null
  updated_at?: string
  kind?: string
  prompt?: string
  result?: {
    content?: string
    error?: string
    usage?: RuntimeUsage
    tools_used?: string[]
    stop_reason?: string
  } | null
  error?: { message?: string } | null
}

export interface RuntimeUsage {
  input_tokens?: number
  output_tokens?: number
  total_tokens?: number
  cost_usd?: number | null
  model?: string | null
}

export type RunFeedbackType = 'incorrect' | 'missing_data' | 'needs_optimization' | 'helpful' | 'other'

export interface RunFeedback {
  feedback_id: string
  run_id: string
  user_id: string
  agent_id: string
  session_id: string
  agent_revision_id?: string | null
  turn_id?: string | null
  message_id?: string | null
  feedback_type: RunFeedbackType
  rating?: 'positive' | 'negative' | 'neutral' | null
  comment: string
  output_excerpt?: string | null
  status: string
  metadata?: Record<string, unknown>
  created_at?: string | null
  updated_at?: string | null
}

export interface RuntimeTask {
  task_id: string
  run_id: string
  agent_id: string
  parent_task_id?: string | null
  name: string
  status: string
  payload?: Record<string, unknown>
  result?: Record<string, unknown> | null
  error?: Record<string, unknown> | null
  priority?: number
  attempt?: number
  max_attempts?: number
  created_at?: string
  started_at?: string | null
  finished_at?: string | null
}

export interface RuntimeLog {
  sequence: number
  run_id: string
  task_id?: string | null
  worker_id?: string | null
  level: string
  stage: string
  message: string
  data: Record<string, unknown>
  created_at: string
}

export interface RuntimeArtifact {
  artifact_id: string
  run_id: string
  task_id?: string | null
  name: string
  media_type: string
  content?: unknown
  uri?: string | null
  created_at: string
}

export interface RuntimeInvocation {
  invocation_id: string
  capability_id: string
  capability_version: string
  capability_kind: string
  run_id: string
  task_id?: string | null
  status: string
  input: Record<string, unknown>
  result?: Record<string, unknown> | null
  error?: Record<string, unknown> | null
  created_at: string
  finished_at?: string | null
}

export interface RunInputField {
  name: string
  value_type: 'string' | 'integer' | 'number' | 'boolean' | 'array' | 'object'
  label?: string
  description?: string
  placeholder?: string
  enum?: unknown[]
  input_mode?: 'auto' | 'text' | 'textarea' | 'single_choice' | 'multi_choice' | 'boolean' | 'number'
  options?: Array<{ value: string; label: string; description?: string; exclusive?: boolean }>
  allow_other?: boolean
  min_selections?: number | null
  max_selections?: number | null
  required?: boolean
  sensitive?: boolean
  suggestion_provider?: Record<string, unknown>
  normalization?: Record<string, unknown>
  visibility?: Record<string, unknown>
  constraint_policy?: { default_strength?: 'required' | 'preferred' | 'excluded'; [key: string]: unknown }
  confirmation_policy?: 'none' | 'inferred' | 'always' | 'sensitive'
  examples?: string[]
  group?: string
  order?: number
}

export interface PendingRunInput {
  input_request_id: string
  run_id: string
  node_id: string
  question: string
  fields: RunInputField[]
  presentation?: { help_text?: string; progress?: { current?: number; total?: number }; [key: string]: unknown }
  source?: 'scenario' | 'agent'
  created_at: string
}

export interface RuntimeEvent {
  schema_version: number
  event_id: string
  sequence: number
  run_id: string
  root_run_id?: string | null
  parent_run_id?: string | null
  task_id?: string | null
  parent_task_id?: string | null
  agent_id?: string | null
  turn_id?: string | null
  span_id?: string | null
  tool_call_id?: string | null
  attempt?: number | null
  type: string
  phase?: string | null
  status?: string | null
  visibility: string
  summary?: string | null
  data: Record<string, unknown>
  created_at: string
}

async function readError(response: Response, fallback: string): Promise<Error> {
  try {
    const payload = await response.json()
    return new Error(payload?.error?.message ?? payload?.detail ?? fallback)
  } catch {
    return new Error(fallback)
  }
}

export async function submitRuntimeRun(input: {
  prompt: string
  sessionId: string
  agentId: string
  scenarioId?: string
  scenarioInputs?: Record<string, unknown>
  channel?: string
  chatId?: string
  idempotencyKey?: string
}): Promise<RuntimeRun> {
  const response = await apiFetch('/v1/runs', {
    method: 'POST',
    headers: {
      ...getIdentityHeaders(),
      'Content-Type': 'application/json',
      'Idempotency-Key': input.idempotencyKey ?? crypto.randomUUID(),
    },
    body: JSON.stringify({
      input: { type: 'message', content: input.prompt },
      session_id: input.sessionId,
      agent_id: input.agentId,
      scenario_id: input.scenarioId,
      scenario_inputs: input.scenarioInputs,
      metadata: { channel: input.channel ?? 'web', chat_id: input.chatId ?? input.sessionId },
    }),
  })
  const payload = await response.json()
  if (!response.ok) throw new Error(payload?.error?.message ?? payload?.detail ?? '提交任务失败')
  return payload as RuntimeRun
}

export async function getRuntimeRun(runId: string): Promise<RuntimeRun> {
  const response = await apiFetch(`/v1/runs/${encodeURIComponent(runId)}`, {
    headers: getIdentityHeaders(),
  })
  const payload = await response.json()
  if (!response.ok) throw new Error(payload?.error?.message ?? payload?.detail ?? '读取任务失败')
  return payload as RuntimeRun
}

export async function listRuntimeRuns(filters: {
  sessionId?: string
  agentId?: string
  status?: string
  limit?: number
} = {}): Promise<RuntimeRun[]> {
  const query = new URLSearchParams()
  if (filters.sessionId) query.set('session_id', filters.sessionId)
  if (filters.agentId) query.set('agent_id', filters.agentId)
  if (filters.status) query.set('status', filters.status)
  query.set('limit', String(filters.limit ?? 100))
  const response = await apiFetch(`/v1/runs?${query}`, {
    headers: getIdentityHeaders(),
  })
  if (!response.ok) throw await readError(response, '读取运行列表失败')
  const payload = await response.json()
  return payload.items ?? []
}

export async function getRuntimeTasks(runId: string): Promise<RuntimeTask[]> {
  const response = await apiFetch(`/v1/runs/${encodeURIComponent(runId)}/tasks`, {
    headers: getIdentityHeaders(),
  })
  if (!response.ok) throw await readError(response, '读取任务失败')
  return (await response.json()).items ?? []
}

export async function getRuntimeLogs(runId: string): Promise<RuntimeLog[]> {
  const response = await apiFetch(`/v1/runs/${encodeURIComponent(runId)}/logs`, {
    headers: getIdentityHeaders(),
  })
  if (!response.ok) throw await readError(response, '读取日志失败')
  return (await response.json()).items ?? []
}

export async function getRuntimeArtifacts(runId: string): Promise<RuntimeArtifact[]> {
  const response = await apiFetch(`/v1/runs/${encodeURIComponent(runId)}/artifacts`, {
    headers: getIdentityHeaders(),
  })
  if (!response.ok) throw await readError(response, '读取产物失败')
  return (await response.json()).items ?? []
}

export async function getRuntimeInvocations(runId: string): Promise<RuntimeInvocation[]> {
  const response = await apiFetch(`/v1/runs/${encodeURIComponent(runId)}/invocations`, {
    headers: getIdentityHeaders(),
  })
  if (!response.ok) throw await readError(response, '读取能力调用失败')
  return (await response.json()).items ?? []
}

export async function getPendingRunInputs(runId: string): Promise<PendingRunInput[]> {
  const response = await apiFetch(`/v1/runs/${encodeURIComponent(runId)}/inputs/pending`, {
    headers: getIdentityHeaders(),
  })
  if (!response.ok) throw await readError(response, '读取待补充信息失败')
  return (await response.json()).items ?? []
}

export async function resolveRunInput(
  runId: string,
  inputRequestId: string,
  answers: Record<string, unknown>,
): Promise<{ run: RuntimeRun; pending_inputs: PendingRunInput[] }> {
  const response = await apiFetch(`/v1/runs/${encodeURIComponent(runId)}/inputs`, {
    method: 'POST',
    headers: {
      ...getIdentityHeaders(),
      'Content-Type': 'application/json',
      'Idempotency-Key': crypto.randomUUID(),
    },
    body: JSON.stringify({ input_request_id: inputRequestId, answers }),
  })
  if (!response.ok) throw await readError(response, '提交补充信息失败')
  return response.json()
}

export async function cancelRuntimeRun(runId: string): Promise<void> {
  const response = await apiFetch(`/v1/runs/${encodeURIComponent(runId)}/cancel`, {
    method: 'POST',
    headers: getIdentityHeaders(),
  })
  if (!response.ok) throw new Error('取消任务失败')
}

export async function listRunFeedback(runId: string): Promise<RunFeedback[]> {
  const response = await apiFetch(`/v1/runs/${encodeURIComponent(runId)}/feedback`, {
    headers: getIdentityHeaders(),
  })
  if (!response.ok) throw await readError(response, '读取人工反馈失败')
  return (await response.json()).items ?? []
}

export async function createRunFeedback(
  runId: string,
  value: Pick<RunFeedback, 'feedback_type' | 'comment'> & Partial<Pick<RunFeedback, 'rating' | 'output_excerpt' | 'turn_id' | 'message_id'>>,
): Promise<RunFeedback> {
  const response = await apiFetch(`/v1/runs/${encodeURIComponent(runId)}/feedback`, {
    method: 'POST',
    headers: {
      ...getIdentityHeaders(),
      'Content-Type': 'application/json',
      'Idempotency-Key': crypto.randomUUID(),
    },
    body: JSON.stringify(value),
  })
  if (!response.ok) throw await readError(response, '提交人工反馈失败')
  return response.json()
}

/** Consume durable SSE with an explicit cursor and authenticated fetch. */
export async function streamRuntimeEvents(
  runId: string,
  onEvent: (event: RuntimeEvent) => void,
  options: { afterSequence?: number; signal?: AbortSignal } = {},
): Promise<number> {
  let cursor = Math.max(0, options.afterSequence ?? 0)
  const headers = new Headers(getApiHeaders())
  headers.set('Accept', 'text/event-stream')
  for (const [name, value] of Object.entries(getIdentityHeaders())) headers.set(name, value)
  if (cursor > 0) headers.set('Last-Event-ID', String(cursor))
  const response = await fetch(
    `/v1/runs/${encodeURIComponent(runId)}/events?after_sequence=${cursor}`,
    { headers, signal: options.signal },
  )
  if (!response.ok || !response.body) throw new Error(`事件流连接失败 (${response.status})`)

  const reader = response.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''
  while (true) {
    const { value, done } = await reader.read()
    buffer += decoder.decode(value, { stream: !done }).replace(/\r\n/g, '\n')
    let boundary = buffer.indexOf('\n\n')
    while (boundary >= 0) {
      const frame = buffer.slice(0, boundary)
      buffer = buffer.slice(boundary + 2)
      const lines = frame.split('\n')
      const id = lines.find((line) => line.startsWith('id:'))?.slice(3).trim()
      const data = lines
        .filter((line) => line.startsWith('data:'))
        .map((line) => line.slice(5).trimStart())
        .join('\n')
      if (data) {
        const event = JSON.parse(data) as RuntimeEvent
        cursor = Math.max(cursor, Number(id || event.sequence || 0))
        onEvent(event)
      }
      boundary = buffer.indexOf('\n\n')
    }
    if (done) break
  }
  return cursor
}
