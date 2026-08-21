import { apiFetch } from './http'

export interface ScenarioField {
  name: string
  value_type: 'string' | 'integer' | 'number' | 'boolean' | 'array' | 'object'
  required: boolean
  description: string
  default?: unknown
  enum: unknown[]
  input_mode: 'auto' | 'text' | 'textarea' | 'single_choice' | 'multi_choice' | 'boolean' | 'number'
  options: Array<{ value: string; label: string; description?: string }>
  allow_other: boolean
  min_selections?: number | null
  max_selections?: number | null
  validation: Record<string, unknown>
  sensitive: boolean
}

export interface ScenarioNode {
  node_id: string
  kind: 'question' | 'confirmation' | 'terminal'
  question: string
  field_names: string[]
  configuration: Record<string, unknown>
}

export interface ScenarioVersion {
  scenario_id: string
  version: number
  name: string
  description: string
  fields: ScenarioField[]
  nodes: ScenarioNode[]
  edges: Array<Record<string, unknown>>
  allowed_capabilities: CapabilityDefinition['ref'][]
  required_skills: ScenarioSkillRef[]
  planning_mode: 'fixed' | 'dynamic'
  execution_policy: Record<string, unknown>
  routing_rules: Array<Record<string, unknown>>
  status: 'draft' | 'published' | 'retired'
  published_at?: string | null
}

export interface CapabilityDefinition {
  ref: { capability_id: string; version: string; kind: 'tool' | 'connector'; extension_id: string; extension_version: string; extension_build_digest: string }
  name: string
  description: string
  execution_mode: string
}

export interface ScenarioSkillRef {
  skill_id: string
  version: string
  content_sha256: string
  name?: string
  description?: string
}

async function payload<T>(response: Response, fallback: string): Promise<T> {
  const body = await response.json()
  if (!response.ok) throw new Error(body?.detail ?? body?.error?.message ?? fallback)
  return body as T
}

export async function listScenarios(): Promise<ScenarioVersion[]> {
  const response = await apiFetch('/control/v1/admin/scenarios')
  return (await payload<{ items: ScenarioVersion[] }>(response, '读取场景失败')).items
}

export async function listScenarioCapabilities(): Promise<CapabilityDefinition[]> {
  const response = await apiFetch('/control/v1/admin/scenarios/capability-catalog')
  return (await payload<{ items: CapabilityDefinition[] }>(response, '读取能力目录失败')).items
}

export async function listScenarioSkills(): Promise<ScenarioSkillRef[]> {
  const response = await apiFetch('/control/v1/admin/scenarios/skill-catalog')
  return (await payload<{ items: ScenarioSkillRef[] }>(response, '读取 Skill 目录失败')).items
}

export async function saveScenario(scenario: ScenarioVersion): Promise<ScenarioVersion> {
  const response = await apiFetch(
    `/control/v1/admin/scenarios/${encodeURIComponent(scenario.scenario_id)}/versions/${scenario.version}`,
    {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(scenario),
    },
  )
  return payload<ScenarioVersion>(response, '保存场景失败')
}

export async function publishScenario(
  id: string,
  version: number,
  policy?: { activation_mode: 'automatic' | 'manual'; timeout_seconds: number; auto_rollback: boolean; require_healthy_workers: boolean },
): Promise<ScenarioVersion> {
  const response = await apiFetch(
    `/control/v1/admin/scenarios/${encodeURIComponent(id)}/versions/${version}/publish`,
    {
      method: 'POST',
      headers: policy ? { 'Content-Type': 'application/json' } : undefined,
      body: policy ? JSON.stringify(policy) : undefined,
    },
  )
  return payload<ScenarioVersion>(response, '发布场景失败')
}

export async function simulateScenario(
  id: string,
  version: number,
  prompt: string,
  inputs: Record<string, unknown> = {},
): Promise<Record<string, unknown>> {
  const response = await apiFetch(`/control/v1/admin/scenarios/${encodeURIComponent(id)}/simulate`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ version, prompt, inputs }),
  })
  return payload<Record<string, unknown>>(response, '模拟路由失败')
}
