/**
 * Composable for gateway WS RPC client. Use inside ControlLayout (provider).
 */

import { ref, shallowRef, onMounted, onUnmounted, inject, type InjectionKey } from 'vue'
import { GatewayClient, buildGatewayUrl, type GatewayHelloOk, type GatewayEventFrame } from '../services/gateway-client'

const defaultToken = typeof import.meta !== 'undefined' && import.meta.env?.VITE_GATEWAY_TOKEN
  ? String(import.meta.env.VITE_GATEWAY_TOKEN)
  : undefined

export function useGateway(options?: { url?: string; token?: string; password?: string }) {
  const url = options?.url ?? buildGatewayUrl()
  const token = options?.token ?? defaultToken
  const password = options?.password

  const connected = ref(false)
  const hello = shallowRef<GatewayHelloOk | null>(null)
  const lastError = ref<string | null>(null)
  const client = shallowRef<GatewayClient | null>(null)

  function onClose(info: { code: number; reason: string }) {
    connected.value = false
    if (info.code !== 1000 && info.reason) {
      lastError.value = info.reason
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
      onEvent: (_evt: GatewayEventFrame) => {
        // Consumers can subscribe via client ref or a separate event bus
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
