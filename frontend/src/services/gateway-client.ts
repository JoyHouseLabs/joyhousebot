/**
 * WebSocket RPC client for joyhousebot /ws/rpc (OpenClaw-aligned).
 * Connects, handles connect.challenge, sends connect with token/password, then request(method, params).
 */

export interface GatewayEventFrame {
  type: 'event'
  event: string
  payload?: unknown
  seq?: number
  stateVersion?: { presence: number; health: number }
}

export interface GatewayResponseFrame {
  type: 'res'
  id: string
  ok: boolean
  payload?: unknown
  error?: { code: string; message: string; details?: unknown }
}

export interface GatewayHelloOk {
  type: 'hello-ok'
  protocol: number
  features?: { methods?: string[]; events?: string[] }
  snapshot?: {
    presence?: unknown[]
    health?: unknown
    alerts?: unknown[]
    sessionDefaults?: unknown
  }
  auth?: {
    deviceToken?: string
    role?: string
    scopes?: string[]
    issuedAtMs?: number
  }
  policy?: { tickIntervalMs?: number }
}

type Pending = {
  resolve: (value: unknown) => void
  reject: (err: unknown) => void
}

export interface GatewayClientOptions {
  url: string
  token?: string
  password?: string
  clientName?: string
  clientVersion?: string
  mode?: string
  onHello?: (hello: GatewayHelloOk) => void
  onEvent?: (evt: GatewayEventFrame) => void
  onClose?: (info: { code: number; reason: string }) => void
}

function generateId(): string {
  return `${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 11)}`
}

export class GatewayClient {
  private ws: WebSocket | null = null
  private pending = new Map<string, Pending>()
  private closed = false
  private connectNonce: string | null = null
  private connectSent = false
  private connectTimer: number | null = null
  private backoffMs = 800

  constructor(private opts: GatewayClientOptions) {}

  start(): void {
    this.closed = false
    this.connect()
  }

  stop(): void {
    this.closed = true
    this.ws?.close()
    this.ws = null
    this.flushPending(new Error('gateway client stopped'))
  }

  get connected(): boolean {
    return this.ws?.readyState === WebSocket.OPEN
  }

  private connect(): void {
    if (this.closed) return
    this.ws = new WebSocket(this.opts.url)
    this.ws.addEventListener('open', () => this.queueConnect())
    this.ws.addEventListener('message', (ev) => this.handleMessage(String(ev.data ?? '')))
    this.ws.addEventListener('close', (ev) => {
      const reason = String(ev.reason ?? '')
      this.ws = null
      this.flushPending(new Error(`gateway closed (${ev.code}): ${reason}`))
      this.opts.onClose?.({ code: ev.code, reason })
      this.scheduleReconnect()
    })
    this.ws.addEventListener('error', () => {})
  }

  private scheduleReconnect(): void {
    if (this.closed) return
    const delay = this.backoffMs
    this.backoffMs = Math.min(this.backoffMs * 1.7, 15000)
    window.setTimeout(() => this.connect(), delay)
  }

  private flushPending(err: Error): void {
    for (const [, p] of this.pending) {
      p.reject(err)
    }
    this.pending.clear()
  }

  private async sendConnect(): Promise<void> {
    if (this.connectSent) return
    this.connectSent = true
    if (this.connectTimer != null) {
      window.clearTimeout(this.connectTimer)
      this.connectTimer = null
    }

    const role = 'operator'
    const scopes = ['operator.read', 'operator.write', 'operator.admin', 'operator.approvals', 'operator.pairing']
    const clientId = this.opts.clientName ?? 'control-ui'
    const clientVersion = this.opts.clientVersion ?? 'dev'
    const mode = this.opts.mode ?? 'webchat'

    const params: Record<string, unknown> = {
      minProtocol: 3,
      maxProtocol: 3,
      client: {
        id: clientId,
        version: clientVersion,
        platform: navigator.platform ?? 'web',
        mode,
      },
      role,
      scopes,
      userAgent: navigator.userAgent,
      locale: navigator.language,
    }

    const auth: { token?: string; password?: string } = {}
    if (this.opts.token) auth.token = this.opts.token
    if (this.opts.password) auth.password = this.opts.password
    if (Object.keys(auth).length) params.auth = auth

    try {
      const hello = await this.request<GatewayHelloOk>('connect', params)
      this.backoffMs = 800
      this.opts.onHello?.(hello as GatewayHelloOk)
    } catch {
      this.ws?.close(4008, 'connect failed')
    }
  }

  private handleMessage(raw: string): void {
    let parsed: unknown
    try {
      parsed = JSON.parse(raw)
    } catch {
      return
    }

    const frame = parsed as { type?: string }
    if (frame.type === 'event') {
      const evt = parsed as GatewayEventFrame
      if (evt.event === 'connect.challenge') {
        const payload = evt.payload as { nonce?: string } | undefined
        const nonce = payload?.nonce ?? null
        if (nonce) {
          this.connectNonce = nonce
          void this.sendConnect()
        }
        return
      }
      try {
        this.opts.onEvent?.(evt)
      } catch (err) {
        console.error('[gateway] event handler error:', err)
      }
      return
    }

    if (frame.type === 'res') {
      const res = parsed as GatewayResponseFrame
      const pending = this.pending.get(res.id)
      if (!pending) return
      this.pending.delete(res.id)
      if (res.ok) {
        pending.resolve(res.payload)
      } else {
        pending.reject(new Error(res.error?.message ?? 'request failed'))
      }
    }
  }

  request<T = unknown>(method: string, params?: unknown): Promise<T> {
    if (!this.ws || this.ws.readyState !== WebSocket.OPEN) {
      return Promise.reject(new Error('gateway not connected'))
    }
    const id = generateId()
    const frame = { type: 'req', id, method, params: params ?? {} }
    const p = new Promise<T>((resolve, reject) => {
      this.pending.set(id, { resolve: (v) => resolve(v as T), reject })
    })
    this.ws.send(JSON.stringify(frame))
    return p
  }

  private queueConnect(): void {
    this.connectNonce = null
    this.connectSent = false
    if (this.connectTimer != null) {
      window.clearTimeout(this.connectTimer)
    }
    this.connectTimer = window.setTimeout(() => {
      void this.sendConnect()
    }, 750)
  }
}

export function buildGatewayUrl(path = '/ws/rpc'): string {
  const protocol = location.protocol === 'https:' ? 'wss:' : 'ws:'
  return `${protocol}//${location.host}${path}`
}
