/**
 * 将 API/网络错误转为用户可读文案（如后端未启动时的 ECONNREFUSED 经代理后变为 fetch 失败）
 */
const BACKEND_HINT = '请先启动 joyhousebot gateway（端口 18790）'

export function normalizeApiError(e: unknown): string {
  const msg = e instanceof Error ? e.message : String(e)
  const lower = msg.toLowerCase()
  if (
    lower.includes('failed to fetch') ||
    lower.includes('load failed') ||
    lower.includes('networkerror') ||
    lower.includes('connection refused') ||
    lower.includes('econnrefused')
  ) {
    return `无法连接后端，${BACKEND_HINT}`
  }
  if (msg) return msg
  return `请求失败，${BACKEND_HINT}`
}
