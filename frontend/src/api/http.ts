/**
 * Shared HTTP client for /api requests. Adds Authorization header when
 * token is set (from URL ?token=, localStorage, or env VITE_HTTP_API_TOKEN).
 * 首次通过 URL 传入的 token 会写入 localStorage，后续请求与 WS 从本地读取。
 */

const CONTROL_TOKEN_STORAGE_KEY = 'joyhousebot_control_token'

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

/** 从 localStorage 读取已保存的 control token */
function getTokenFromStorage(): string {
  if (typeof window === 'undefined') return ''
  try {
    const t = localStorage.getItem(CONTROL_TOKEN_STORAGE_KEY)
    return (t ?? '').trim()
  } catch {
    return ''
  }
}

/** 将 token 写入 localStorage（首次从 URL 带入时调用） */
function setControlTokenToStorage(token: string): void {
  if (typeof window === 'undefined' || !token) return
  try {
    localStorage.setItem(CONTROL_TOKEN_STORAGE_KEY, token)
  } catch {
    /* ignore */
  }
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
 * 获取 control token，供 HTTP 与 WebSocket 共用。
 * 优先级：URL ?token= > localStorage（首次从 URL 带入时会写入）> 环境变量
 * 首次从 URL 读取到 token 时会写入 localStorage 并从地址栏移除 token 参数。
 */
export function getControlToken(): string {
  const fromUrl = getTokenFromUrl()
  if (fromUrl) {
    setControlTokenToStorage(fromUrl)
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
  for (const [k, v] of Object.entries(auth)) {
    headers.set(k, v)
  }
  return fetch(url, { ...init, headers })
}
