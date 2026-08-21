const OWNER_ID_KEY = 'joyhousebot.starter.user_id'
const OWNER_TOKEN_KEY = 'joyhousebot.starter.token'

export function ownerIdentity() {
  return {
    userId: sessionStorage.getItem(OWNER_ID_KEY) || 'joyhousebot',
    token: sessionStorage.getItem(OWNER_TOKEN_KEY) || '',
  }
}

export async function request(path, options = {}) {
  const { token, userId } = ownerIdentity()
  const headers = new Headers(options.headers)
  if (options.body !== undefined && !headers.has('Content-Type')) headers.set('Content-Type', 'application/json')
  if (token) headers.set('Authorization', `Bearer ${token}`)
  else headers.set('X-User-ID', userId)
  if (options.idempotencyKey) headers.set('Idempotency-Key', options.idempotencyKey)
  const response = await fetch(path, { ...options, headers })
  const payload = await response.json().catch(() => ({}))
  if (!response.ok) throw new Error(payload.detail || payload?.error?.message || `${response.status} ${response.statusText}`)
  return payload
}

export function prettyContent(value) {
  if (typeof value === 'string') return value
  if (value === null || value === undefined) return ''
  return JSON.stringify(value, null, 2)
}
