import { apiFetch } from './http'

export interface AuthenticatedLogin {
  status: 'authenticated'
  token: string
  token_type: 'bearer'
  expires_at: string
  user_id: string
  must_change_password: boolean
  totp_enabled: boolean
}

export interface MfaRequiredLogin {
  status: 'mfa_required'
  challenge_token: string
  expires_at: string
  user_id: string
}

export type LoginResult = AuthenticatedLogin | MfaRequiredLogin

export interface AuthStatus {
  user_id: string
  must_change_password: boolean
  totp_enabled: boolean
  password_changed_at: string | null
  last_login_at: string | null
  recovery_codes_remaining: number
  session_authenticated: boolean
}

export interface TotpSetup {
  secret: string
  otpauth_uri: string
  expires_at: string
  algorithm: string
  digits: number
  period: number
}

async function jsonOrThrow<T>(response: Response, fallback: string): Promise<T> {
  const payload = await response.json().catch(() => ({}))
  if (!response.ok) throw new Error(payload?.error?.message ?? payload?.detail ?? fallback)
  return payload as T
}

export async function loginWithPassword(userId: string, password: string): Promise<LoginResult> {
  return jsonOrThrow(await fetch('/control/v1/auth/login', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ user_id: userId, password }),
  }), '登录失败')
}

export async function verifyLoginMfa(challengeToken: string, code: string): Promise<AuthenticatedLogin> {
  return jsonOrThrow(await fetch('/control/v1/auth/mfa/verify', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ challenge_token: challengeToken, code }),
  }), '验证码校验失败')
}

export async function getAuthStatus(): Promise<AuthStatus> {
  return jsonOrThrow(await apiFetch('/control/v1/auth/status'), '读取账户安全状态失败')
}

export async function changeAdminPassword(currentPassword: string, newPassword: string): Promise<AuthenticatedLogin | { status: 'password_changed'; user_id: string }> {
  return jsonOrThrow(await apiFetch('/control/v1/auth/password', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ current_password: currentPassword, new_password: newPassword }),
  }), '修改密码失败')
}

export async function prepareTotp(): Promise<TotpSetup> {
  return jsonOrThrow(await apiFetch('/control/v1/auth/totp/setup', { method: 'POST' }), '创建验证器配置失败')
}

export async function confirmTotp(code: string): Promise<{ enabled: true; recovery_codes: string[]; message: string }> {
  return jsonOrThrow(await apiFetch('/control/v1/auth/totp/confirm', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ code }),
  }), '激活验证器失败')
}

export async function disableTotp(password: string, code: string): Promise<{ enabled: false }> {
  return jsonOrThrow(await apiFetch('/control/v1/auth/totp/disable', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ password, code }),
  }), '停用验证器失败')
}

export async function logoutAdmin(): Promise<void> {
  await apiFetch('/control/v1/auth/logout', { method: 'POST' })
}
