import { apiFetch } from './http'

const API_BASE = '/api'

export interface SkillItem {
  name: string
  source: string
  description: string
  available: boolean
  enabled: boolean
}

export interface SkillsResponse {
  ok: boolean
  skills: SkillItem[]
}

export async function getSkills(): Promise<SkillsResponse> {
  const res = await apiFetch(`${API_BASE}/skills`)
  if (!res.ok) throw new Error(await res.text())
  return res.json()
}

export async function patchSkill(name: string, enabled: boolean): Promise<{ ok: boolean; name: string; enabled: boolean }> {
  const res = await apiFetch(`${API_BASE}/skills/${encodeURIComponent(name)}`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ enabled }),
  })
  if (!res.ok) throw new Error(await res.text())
  return res.json()
}
