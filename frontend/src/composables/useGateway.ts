/**
 * Composable for gateway WS RPC client. Use inside ControlLayout (provider).
 */

import { ref, shallowRef, onMounted, onUnmounted, inject, type InjectionKey } from 'vue'
import { GatewayClient, buildGatewayUrl, type GatewayHelloOk, type GatewayEventFrame } from '../services/gateway-client'
import { getControlToken } from '../api/http'

const envGatewayToken = typeof import.meta !== 'undefined' && import.meta.env?.VITE_GATEWAY_TOKEN
  ? String(import.meta.env.VITE_GATEWAY_TOKEN)
  : undefined

/** 解析得到的 token：优先 options.token，其次 URL ?token=，最后环境变量 */
function resolveToken(optionsToken?: string): string | undefined {
  if (optionsToken != null && optionsToken !== '') return optionsToken
  const fromUrl = getControlToken()
  if (fromUrl) return fromUrl
  return envGatewayToken
}

export type GatewayEventCallback = (payload: unknown) => void

export function useGateway(options?: { url?: string; token?: string; password?: string }) {
  const url = options?.url ?? buildGatewayUrl()
  const token = resolveToken(options?.token)
  const password = options?.password

  const connected = ref(false)
  const hello = shallowRef<GatewayHelloOk | null>(null)
  const lastError = ref<string | null>(null)
  const client = shallowRef<GatewayClient | null>(null)

  const eventHandlers = new Map<string, Set<GatewayEventCallback>>()

  function onClose(info: { code: number; reason: string }) {
    connected.value = false
    if (info.code !== 1000 && info.reason) {
      lastError.value = info.reason
    }
  }

  function subscribe(event: string, callback: GatewayEventCallback): () => void {
    let set = eventHandlers.get(event)
    if (!set) {
      set = new Set()
      eventHandlers.set(event, set)
    }
    set.add(callback)
    return () => {
      set?.delete(callback)
    }
  }

  function createClient(): GatewayClient {
    const c = new GatewayClient({
      url,
      token,
      password,
      clientName: 'control-ui',
      clientVersion: '1',
      mode: 'webchat',
      onHello: (h) => {
        hello.value = h
        connected.value = true
        lastError.value = null
      },
      onEvent: (evt: GatewayEventFrame) => {
        const name = evt.event
        const handlers = eventHandlers.get(name)
        if (handlers) {
          for (const cb of handlers) {
            try {
              cb(evt.payload)
            } catch (err) {
              console.error(`[gateway] ${name} handler error:`, err)
            }
          }
        }
      },
      onClose,
    })
    client.value = c
    return c
  }

  function start(): void {
    const c = client.value ?? createClient()
    c.start()
  }

  function stop(): void {
    client.value?.stop()
    client.value = null
    connected.value = false
    hello.value = null
  }

  async function request<T = unknown>(method: string, params?: unknown): Promise<T> {
    const c = client.value
    if (!c) {
      return Promise.reject(new Error('gateway not started'))
    }
    try {
      return await c.request<T>(method, params)
    } catch (e) {
      lastError.value = e instanceof Error ? e.message : String(e)
      throw e
    }
  }

  onMounted(() => {
    start()
  })

  onUnmounted(() => {
    stop()
  })

  return {
    client,
    connected,
    hello,
    lastError,
    request,
    subscribe,
    start,
    stop,
  }
}

export type GatewayApi = ReturnType<typeof useGateway>

export const GatewayKey: InjectionKey<GatewayApi> = Symbol('gateway')

/** Use gateway from parent provider (e.g. ControlLayout). */
export function useGatewayInject(): GatewayApi | undefined {
  return inject(GatewayKey)
}
