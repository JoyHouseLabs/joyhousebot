const USER_STORAGE_KEY = 'joyhousebot_user_id'

let volatileUserId: string | null = null

const DEFAULT_TEST_USER_ID = String(import.meta.env.VITE_DEFAULT_USER_ID || 'local-dev')

/**
 * Development identity and operator impersonation target for this browser.
 * A regular user token remains authoritative; the backend ignores both
 * identity headers for that authentication mode.
 */
export function getRuntimeUserId(): string {
  try {
    const existing = localStorage.getItem(USER_STORAGE_KEY)?.trim()
    if (existing && !existing.startsWith('web-')) return existing
    // Migrate browser-random identities created by the previous user console.
    // The platform console has one explicit development test identity instead.
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

export function getIdentityHeaders(): Record<string, string> {
  const userId = getRuntimeUserId()
  return {
    'X-User-ID': userId,
    'X-Impersonate-User-ID': userId,
  }
}
