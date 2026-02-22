const API_BASE = '/api'

export interface ChatResponse {
  ok: boolean
  response: string
  session_id: string
}

/** 非流式：等完整回复后返回（POST /chat） */
export async function sendMessage(message: string, sessionId: string = 'ui:default'): Promise<ChatResponse> {
  const res = await fetch(`${API_BASE}/chat`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ message, session_id: sessionId }),
  })
  if (!res.ok) {
    const text = await res.text()
    throw new Error(text || `HTTP ${res.status}`)
  }
  return res.json()
}

export interface OpenAppNavigate {
  ok: boolean
  app_id: string
  route?: string
  params?: Record<string, unknown>
  navigate_to: string
}

/**
 * 流式：通过 SSE 逐 chunk 回调（POST /v1/chat/completions stream=true）。
 * 与 OpenClaw 一致，边生成边展示。session_id 指定会话（默认 ui:default）；agent_id 指定 agent（多 agent 时）。
 * 若服务端返回 open_app_navigate，会调用 onOpenAppNavigate（用于自动跳转到应用）。
 */
export async function sendMessageStream(
  message: string,
  onDelta: (chunk: string) => void,
  sessionId: string = 'ui:default',
  agentId?: string | null,
  onOpenAppNavigate?: (data: OpenAppNavigate) => void,
): Promise<void> {
  const body: Record<string, unknown> = {
    model: 'joyhousebot',
    messages: [{ role: 'user' as const, content: message }],
    stream: true,
    session_id: sessionId,
  }
  if (agentId != null && agentId !== '') body.agent_id = agentId
  const res = await fetch(`${API_BASE}/v1/chat/completions`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
  if (!res.ok) {
    const text = await res.text()
    throw new Error(text || `HTTP ${res.status}`)
  }
  const reader = res.body?.getReader()
  if (!reader) throw new Error('No response body')
  const decoder = new TextDecoder()
  let buffer = ''
  while (true) {
    const { done, value } = await reader.read()
    if (done) break
    buffer += decoder.decode(value, { stream: true })
    const lines = buffer.split('\n')
    buffer = lines.pop() ?? ''
    for (const line of lines) {
      if (line.startsWith('data: ')) {
        const data = line.slice(6)
        if (data === '[DONE]') return
        try {
          const obj = JSON.parse(data) as {
            choices?: Array<{ delta?: { content?: string } }>
            open_app_navigate?: OpenAppNavigate
          }
          if (obj.open_app_navigate?.navigate_to && onOpenAppNavigate) {
            onOpenAppNavigate(obj.open_app_navigate)
          }
          const content = obj.choices?.[0]?.delta?.content
          if (typeof content === 'string') onDelta(content)
        } catch {
          // ignore parse error for non-JSON lines
        }
      }
    }
  }
  // flush remaining
  if (buffer.startsWith('data: ') && buffer.slice(6) !== '[DONE]') {
    try {
      const obj = JSON.parse(buffer.slice(6)) as {
        choices?: Array<{ delta?: { content?: string } }>
        open_app_navigate?: OpenAppNavigate
      }
      if (obj.open_app_navigate?.navigate_to && onOpenAppNavigate) {
        onOpenAppNavigate(obj.open_app_navigate)
      }
      const content = obj.choices?.[0]?.delta?.content
      if (typeof content === 'string') onDelta(content)
    } catch {
      // ignore
    }
  }
}
