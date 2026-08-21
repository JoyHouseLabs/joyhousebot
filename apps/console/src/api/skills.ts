import { apiFetch } from './http'

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const headers = new Headers(init?.headers)
  if (init?.body !== undefined) headers.set('Content-Type', 'application/json')
  const response = await apiFetch(`/control/v1/admin/skills${path}`, { ...init, headers })
  const payload = await response.json().catch(() => ({}))
  if (!response.ok) throw new Error(payload?.detail || payload?.error?.message || 'Skill 管理请求失败')
  return payload as T
}

export interface SkillRef {
  skill_id: string
  version: string
  content_sha256: string
}

export interface SkillValidationReport {
  valid: boolean
  errors: string[]
  warnings: string[]
  checks: Array<{ check: string; passed: boolean; count?: number; missing?: string[] }>
  content_sha256?: string
}

export interface SkillVersion {
  skill_id: string
  version: string
  name: string
  description: string
  definition_status: 'active' | 'disabled' | 'archived'
  status: 'draft' | 'staged' | 'published' | 'retired'
  instruction_content: string
  tags: string[]
  input_schema: Record<string, unknown>
  output_schema: Record<string, unknown>
  required_capabilities: Array<{ capability_id: string; version: string }>
  required_connections: string[]
  examples: Array<Record<string, unknown>>
  eval_cases: Array<Record<string, unknown>>
  templates: Array<Record<string, unknown>>
  change_note: string
  source: Record<string, unknown>
  content_sha256: string
  validation_report: SkillValidationReport | Record<string, never>
  created_by: string
  created_at?: string | null
  updated_at?: string | null
  validated_at?: string | null
  published_at?: string | null
  rollout_id?: string
}

export interface SkillSummary {
  skill_id: string
  name: string
  description: string
  status: 'active' | 'disabled' | 'archived'
  current_version?: string | null
  tags: string[]
  current?: { version: string; status: string; content_sha256: string; published_at?: string | null } | null
  latest?: { version: string; status: string; content_sha256: string; updated_at?: string | null } | null
  versions?: SkillVersion[]
  created_at?: string | null
  updated_at?: string | null
}

export interface SaveSkillDraft {
  skill_id: string
  version: string
  name: string
  description: string
  instruction_content: string
  tags: string[]
  input_schema: Record<string, unknown>
  output_schema: Record<string, unknown>
  required_capabilities: Array<{ capability_id: string; version: string }>
  required_connections: string[]
  examples: Array<Record<string, unknown>>
  eval_cases: Array<Record<string, unknown>>
  templates: Array<Record<string, unknown>>
  change_note: string
  source: Record<string, unknown>
}

export interface SkillRolloutPolicy {
  activation_mode: 'automatic' | 'manual'
  timeout_seconds: number
  auto_rollback: boolean
  require_healthy_workers: boolean
}

export const listSkills = async () => (await request<{ items: SkillSummary[] }>('')).items
export const getSkill = (skillId: string) => request<SkillSummary>(`/${encodeURIComponent(skillId)}`)
export const saveSkillDraft = (value: SaveSkillDraft) => request<SkillVersion>(
  `/${encodeURIComponent(value.skill_id)}/versions/${encodeURIComponent(value.version)}`,
  { method: 'PUT', body: JSON.stringify(value) },
)
export const validateSkillVersion = (skillId: string, version: string) => request<SkillValidationReport>(
  `/${encodeURIComponent(skillId)}/versions/${encodeURIComponent(version)}/validate`,
  { method: 'POST' },
)
export const publishSkillVersion = (skillId: string, version: string, policy: SkillRolloutPolicy) => request<SkillVersion>(
  `/${encodeURIComponent(skillId)}/versions/${encodeURIComponent(version)}/publish`,
  { method: 'POST', body: JSON.stringify(policy) },
)
export const setSkillStatus = (skillId: string, status: SkillSummary['status']) => request<SkillSummary>(
  `/${encodeURIComponent(skillId)}/status`,
  { method: 'PUT', body: JSON.stringify({ status }) },
)
