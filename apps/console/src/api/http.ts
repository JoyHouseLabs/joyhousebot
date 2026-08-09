/**
 * Shared HTTP client for the versioned cloud API. Adds Authorization when
 * token is set (from URL ?token=, sessionStorage, or env VITE_HTTP_API_TOKEN).
 * 首次通过 URL 传入的 token 会写入 sessionStorage，供同标签页后续请求复用。
 *
 * Control token 只存 sessionStorage、不落 localStorage：它是平台最高权限凭据，
 * localStorage 会跨会话长期驻留磁盘，任何 XSS 或本机数据残留都可窃取；
 * sessionStorage 随标签页关闭即销毁，把暴露窗口收敛到当前操作会话内。
 * 代价是打开新标签页需要重新登录，这是对 operator 凭据刻意的取舍。
 */

import { getIdentityHeaders } from './identity'

const CONTROL_TOKEN_STORAGE_KEY = 'joyhousebot_control_token'

let volatileControlToken: string | null = null

function getTokenFromEnv(): string {
  if (typeof import.meta === 'undefined' || !import.meta.env?.VITE_HTTP_API_TOKEN) return ''
  return String(import.meta.env.VITE_HTTP_API_TOKEN).trim()
}

/** 从当前 URL 查询参数读取 token（如 ?token=your-secret-token-for-controls） */
function getTokenFromUrl(): string {
  if (typeof window === 'undefined') return ''
  const t = new URLSearchParams(window.location.search).get('token')
  return (t ?? '').trim()
}

/** 从 sessionStorage 读取已保存的 control token */
function getTokenFromStorage(): string {
  if (typeof window === 'undefined') return ''
  try {
    const t = sessionStorage.getItem(CONTROL_TOKEN_STORAGE_KEY)
    return (t ?? '').trim()
  } catch {
    return volatileControlToken ?? ''
  }
}

/** 清除旧版本写入 localStorage 的 token 副本（一次性迁移清理） */
function purgeLegacyLocalToken(): void {
  if (typeof window === 'undefined') return
  try { localStorage.removeItem(CONTROL_TOKEN_STORAGE_KEY) } catch { /* ignore */ }
}

/** 将 token 写入 sessionStorage（登录或首次从 URL 带入时调用） */
export function setControlToken(token: string): void {
  if (typeof window === 'undefined' || !token) return
  purgeLegacyLocalToken()
  try {
    sessionStorage.setItem(CONTROL_TOKEN_STORAGE_KEY, token)
  } catch {
    volatileControlToken = token
  }
}

export function clearControlToken(): void {
  if (typeof window === 'undefined') return
  purgeLegacyLocalToken()
  volatileControlToken = null
  try { sessionStorage.removeItem(CONTROL_TOKEN_STORAGE_KEY) } catch { /* ignore */ }
}

/** 从当前 URL 中移除 token 参数，避免长期暴露在地址栏 */
function removeTokenFromUrl(): void {
  if (typeof window === 'undefined') return
  try {
    const u = new URL(window.location.href)
    if (u.searchParams.has('token')) {
      u.searchParams.delete('token')
      const newUrl = u.pathname + u.search + u.hash
      window.history.replaceState(null, '', newUrl)
    }
  } catch {
    /* ignore */
  }
}

/**
 * 获取 bearer token，供 HTTP 与 SSE 请求共用。
 * 优先级：URL ?token= > sessionStorage（登录或首次从 URL 带入时写入）> 环境变量
 * 首次从 URL 读取到 token 时会写入 sessionStorage 并从地址栏移除 token 参数，
 * 同时清除历史版本残留在 localStorage 中的副本。
 */
export function getControlToken(): string {
  const fromUrl = getTokenFromUrl()
  if (fromUrl) {
    setControlToken(fromUrl)
    removeTokenFromUrl()
    return fromUrl
  }
  const fromStorage = getTokenFromStorage()
  if (fromStorage) return fromStorage
  return getTokenFromEnv()
}

export function getApiHeaders(): Record<string, string> {
  const token = getControlToken()
  if (!token) return {}
  return { Authorization: `Bearer ${token}` }
}

export async function apiFetch(url: string, init?: RequestInit): Promise<Response> {
  const auth = getApiHeaders()
  const headers = new Headers(init?.headers)
  for (const [k, v] of Object.entries(getIdentityHeaders())) {
    headers.set(k, v)
  }
  for (const [k, v] of Object.entries(auth)) {
    headers.set(k, v)
  }
  return fetch(url, { ...init, headers })
}
