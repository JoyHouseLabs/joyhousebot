const USER_STORAGE_KEY = 'joyhousebot_user_id'
const IMPERSONATE_STORAGE_KEY = 'joyhousebot_impersonate_user_id'

let volatileUserId: string | null = null
let volatileImpersonateUserId: string | null = null

const DEFAULT_TEST_USER_ID = String(import.meta.env.VITE_DEFAULT_USER_ID || 'joyhousebot')
const LEGACY_LOCAL_USER_IDS = new Set(['local-dev', 'browser-qa-admin'])

/**
 * Development identity and operator identity for this browser.
 * A regular user token remains authoritative; the backend ignores the
 * identity header for that authentication mode.
 */
export function getRuntimeUserId(): string {
  try {
    const existing = localStorage.getItem(USER_STORAGE_KEY)?.trim()
    if (existing && !existing.startsWith('web-') && !LEGACY_LOCAL_USER_IDS.has(existing)) return existing
    // Migrate identities from the previous console and browser QA sessions.
    // The platform console now has one explicit development identity instead.
    localStorage.setItem(USER_STORAGE_KEY, DEFAULT_TEST_USER_ID)
    return DEFAULT_TEST_USER_ID
  } catch {
    volatileUserId ??= DEFAULT_TEST_USER_ID
    return volatileUserId
  }
}

export function setRuntimeUserId(userId: string): void {
  const normalized = String(userId || '').trim()
  if (!normalized) return
  try { localStorage.setItem(USER_STORAGE_KEY, normalized) } catch { volatileUserId = normalized }
}

/** 从 URL 查询参数读取一次性的代操作目标（?impersonate_user=alice） */
function getImpersonateTargetFromUrl(): string {
  if (typeof window === 'undefined') return ''
  const target = new URLSearchParams(window.location.search).get('impersonate_user')
  return (target ?? '').trim()
}

/** 从地址栏移除 impersonate_user 参数，避免长期暴露 */
function removeImpersonateTargetFromUrl(): void {
  if (typeof window === 'undefined') return
  try {
    const url = new URL(window.location.href)
    if (url.searchParams.has('impersonate_user')) {
      url.searchParams.delete('impersonate_user')
      window.history.replaceState(null, '', url.pathname + url.search + url.hash)
    }
  } catch {
    /* ignore */
  }
}

/**
 * Explicit impersonation target chosen by the operator, or null when the
 * console acts as the operator's own identity.  Stored in sessionStorage so
 * the deliberate choice survives a refresh of the same tab but never leaks
 * into other tabs or persists across browser sessions.
 */
export function getImpersonationTarget(): string | null {
  const fromUrl = getImpersonateTargetFromUrl()
  if (fromUrl) {
    setImpersonationTarget(fromUrl)
    removeImpersonateTargetFromUrl()
    return fromUrl
  }
  try {
    const stored = sessionStorage.getItem(IMPERSONATE_STORAGE_KEY)?.trim()
    if (stored) return stored
  } catch {
    if (volatileImpersonateUserId) return volatileImpersonateUserId
  }
  return null
}

export function setImpersonationTarget(userId: string | null | undefined): void {
  const normalized = String(userId || '').trim()
  try {
    if (normalized) sessionStorage.setItem(IMPERSONATE_STORAGE_KEY, normalized)
    else sessionStorage.removeItem(IMPERSONATE_STORAGE_KEY)
  } catch {
    volatileImpersonateUserId = normalized || null
  }
}

export function clearImpersonationTarget(): void {
  setImpersonationTarget(null)
}

/**
 * Identity headers for API requests.  `X-User-ID` always carries the
 * operator's own identity.  `X-Impersonate-User-ID` is sent only after the
 * operator explicitly opted into impersonation (floating control or
 * ?impersonate_user= URL parameter), so acting on behalf of another user —
 * and the corresponding backend warning audit per request — never happens
 * silently.
 */
export function getIdentityHeaders(): Record<string, string> {
  const headers: Record<string, string> = { 'X-User-ID': getRuntimeUserId() }
  const target = getImpersonationTarget()
  if (target) headers['X-Impersonate-User-ID'] = target
  return headers
}
