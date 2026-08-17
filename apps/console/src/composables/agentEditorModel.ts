import type { AdminAgent, AgentRevision } from '../api/admin'

export type AgentRole = AdminAgent['role']
export type DefinitionStatus = AdminAgent['status']

export interface AgentPolicyBase {
  persona: Record<string, unknown>
  model: Record<string, unknown>
  planning: Record<string, unknown>
  capability: Record<string, unknown>
  memory: Record<string, unknown>
  monitor: Record<string, unknown>
  output: Record<string, unknown>
}

export function createBlankAgentDraft() {
  return {
    agent_id: '', revision_id: '', version: 1, name: '', description: '',
    role: 'executor' as AgentRole, definition_status: 'active' as DefinitionStatus,
    tone: 'helpful', language: 'follow-user', instructions: '',
    primary_model: '', fallback_models: '', temperature: 0.3, max_tokens: 4096,
    max_tool_iterations: 20, reasoning_effort: 'none', thinking_budget_tokens: 0,
    capture_reasoning: false, cache_enabled: true, cache_ttl_seconds: 300, capability_mode: 'allowlist',
    allowed_capabilities: [] as string[], granted_permissions: [] as string[], allow_subagents: true, max_steps: 32,
    max_replans: 2,
    max_fan_out: 10, tool_execution_mode: 'sequential', max_parallel_calls: 4, memory_enabled: false, memory_mode: 'task_only', memory_scope: 'user_agent',
    memory_episodic: false, memory_profile: false, memory_long_term: false, memory_agent: false,
    memory_read_mode: 'none', memory_write: 'none', memory_top_k: 10, memory_max_tokens: 6000,
    rerank_enabled: false, rerank_version: '', rerank_candidate_limit: 20, rerank_top_k: 20,
    rerank_failure_mode: 'fallback',
    monitor_enabled: false, monitor_every_minutes: 30, monitor_context_mode: 'light',
    monitor_preflight_mode: 'runtime_attention', monitor_session_mode: 'isolated',
    monitor_delivery: 'none', monitor_message: 'Review Runtime attention and act if needed.',
    monitor_active_hours_enabled: false, monitor_active_hours_start: '08:00',
    monitor_active_hours_end: '22:00', monitor_active_hours_timezone: 'Asia/Shanghai',
  }
}

export type AgentDraft = ReturnType<typeof createBlankAgentDraft>

function splitList(value: string) {
  return value.split(',').map((item) => item.trim()).filter(Boolean)
}

function numberValue(value: unknown, fallback: number) {
  const parsed = Number(value)
  return Number.isFinite(parsed) ? parsed : fallback
}

function boolValue(value: unknown, fallback: boolean) {
  return typeof value === 'boolean' ? value : fallback
}

export function fillAgentDraft(
  draft: AgentDraft,
  policyBase: AgentPolicyBase,
  agent: AdminAgent | undefined,
  revision: AgentRevision | undefined,
  revisionId: string,
  version: number,
) {
  const model = revision?.model_policy || {}
  const planning = revision?.planning_policy || {}
  const ability = revision?.capability_policy || {}
  const memory = revision?.memory_policy || {}
  const monitor = revision?.monitor_policy || {}
  const persona = revision?.persona || {}
  Object.assign(policyBase, {
    persona: { ...persona }, model: { ...model }, planning: { ...planning },
    capability: { ...ability }, memory: { ...memory }, monitor: { ...monitor }, output: { ...(revision?.output_policy || {}) },
  })
  Object.assign(draft, createBlankAgentDraft(), {
    agent_id: agent?.agent_id || '', revision_id: revisionId, version,
    name: agent?.name || '', description: agent?.description || '', role: agent?.role || 'executor',
    definition_status: agent?.status || 'active', tone: String(persona.tone || 'helpful'),
    language: String(persona.language || 'follow-user'), instructions: revision?.instructions || '',
    primary_model: String(model.primary || ''), fallback_models: Array.isArray(model.fallbacks) ? model.fallbacks.join(', ') : '',
    temperature: numberValue(model.temperature, 0.3), max_tokens: numberValue(model.max_tokens, 4096),
    max_tool_iterations: numberValue(model.max_tool_iterations, 20), reasoning_effort: String(model.reasoning_effort || 'none'),
    thinking_budget_tokens: numberValue(model.thinking_budget_tokens, 0), capture_reasoning: boolValue(model.capture_reasoning, false),
    cache_enabled: boolValue(model.cache_enabled, true), cache_ttl_seconds: numberValue(model.cache_ttl_seconds, 300), capability_mode: String(ability.mode || 'catalog'),
    allowed_capabilities: Array.isArray(ability.allowed) ? ability.allowed.map(String) : [],
    granted_permissions: Array.isArray(ability.permissions) ? ability.permissions.map(String) : [],
    allow_subagents: boolValue(planning.allow_subagents, true), max_steps: numberValue(planning.max_steps, 32),
    max_replans: numberValue(planning.max_replans, 2), max_fan_out: numberValue(planning.max_fan_out, 10),
    tool_execution_mode: String((model.tool_execution as any)?.mode || 'sequential'),
    max_parallel_calls: numberValue((model.tool_execution as any)?.max_parallel_calls, 4),
    memory_enabled: memory.enabled !== false && memory.read !== false,
    memory_mode: String(memory.mode || (memory.enabled === false ? 'task_only' : 'personalized')),
    memory_scope: String(memory.scope || 'user_agent'),
    memory_episodic: boolValue(memory.layers && (memory.layers as any).episodic?.read, memory.read !== false),
    memory_profile: boolValue(memory.layers && (memory.layers as any).profile?.read, memory.read !== false),
    memory_long_term: boolValue(memory.layers && (memory.layers as any).long_term?.read, memory.read !== false),
    memory_agent: boolValue(memory.layers && (memory.layers as any).agent?.read, false),
    memory_read_mode: String(memory.read_mode || (memory.read === false ? 'none' : 'auto')),
    memory_write: String(memory.write_mode || (memory.write === false ? 'none' : 'candidate')),
    memory_top_k: numberValue((memory.retrieval as any)?.top_k, 10),
    memory_max_tokens: numberValue((memory.retrieval as any)?.max_tokens, 6000),
    rerank_enabled: Boolean((memory.retrieval as any)?.rerank?.enabled),
    rerank_version: String((memory.retrieval as any)?.rerank?.version || ''),
    rerank_candidate_limit: numberValue((memory.retrieval as any)?.rerank?.candidate_limit, 20),
    rerank_top_k: numberValue((memory.retrieval as any)?.rerank?.top_k, 20),
    rerank_failure_mode: String((memory.retrieval as any)?.rerank?.failure_mode || 'fallback'),
    monitor_enabled: monitor.enabled === true,
    monitor_every_minutes: Math.max(1, numberValue((monitor.schedule as any)?.every_ms, 1800000) / 60000),
    monitor_context_mode: String(monitor.context_mode || 'light'),
    monitor_preflight_mode: String(monitor.preflight_mode || 'runtime_attention'),
    monitor_session_mode: String(monitor.session_mode || 'isolated'),
    monitor_delivery: String(monitor.delivery || 'none'),
    monitor_message: String(monitor.message || 'Review Runtime attention and act if needed.'),
    monitor_active_hours_enabled: Boolean(monitor.active_hours),
    monitor_active_hours_start: String((monitor.active_hours as any)?.start || '08:00'),
    monitor_active_hours_end: String((monitor.active_hours as any)?.end || '22:00'),
    monitor_active_hours_timezone: String((monitor.active_hours as any)?.timezone || 'Asia/Shanghai'),
  })
}

export function buildAgentRevisionPayload(draft: AgentDraft, policyBase: AgentPolicyBase) {
  return {
    revision_id: draft.revision_id, version: draft.version, name: draft.name, description: draft.description,
    role: draft.role, definition_status: draft.definition_status,
    persona: { ...policyBase.persona, tone: draft.tone, language: draft.language }, instructions: draft.instructions,
    model_policy: {
      ...policyBase.model, primary: draft.primary_model, fallbacks: splitList(draft.fallback_models), temperature: draft.temperature,
      max_tokens: draft.max_tokens, max_tool_iterations: draft.max_tool_iterations,
      capture_reasoning: draft.capture_reasoning, thinking_budget_tokens: draft.thinking_budget_tokens,
      reasoning_effort: draft.reasoning_effort, cache_enabled: draft.cache_enabled, cache_ttl_seconds: draft.cache_ttl_seconds,
      tool_execution: { mode: draft.tool_execution_mode, max_parallel_calls: Math.max(1, Math.min(128, Number(draft.max_parallel_calls) || 1)) },
    },
    planning_policy: {
      ...policyBase.planning, allow_subagents: draft.allow_subagents, max_steps: draft.max_steps,
      max_fan_out: draft.max_fan_out, max_replans: Math.max(0, Math.min(10, Number(draft.max_replans) || 0)),
    },
    capability_policy: { ...policyBase.capability, mode: draft.capability_mode, allowed: draft.allowed_capabilities, permissions: draft.granted_permissions },
    memory_policy: {
      ...policyBase.memory, enabled: draft.memory_enabled, mode: draft.memory_mode, scope: draft.memory_scope,
      read_mode: draft.memory_enabled ? draft.memory_read_mode : 'none', write_mode: draft.memory_enabled ? draft.memory_write : 'none',
      layers: {
        working: { read: true, write: false, persist: false }, session: { read: true, write: false, persist: true },
        episodic: { read: draft.memory_enabled && draft.memory_episodic, write: draft.memory_enabled && draft.memory_write !== 'none', persist: true },
        profile: { read: draft.memory_enabled && draft.memory_profile, write: draft.memory_enabled && draft.memory_write !== 'none', persist: true },
        long_term: { read: draft.memory_enabled && draft.memory_long_term, write: draft.memory_enabled && draft.memory_write !== 'none', persist: true },
        agent: { read: draft.memory_enabled && draft.memory_agent, write: draft.memory_enabled && draft.memory_write !== 'none', persist: true },
      },
      retrieval: {
        top_k: draft.memory_top_k, max_tokens: draft.memory_max_tokens,
        ...(draft.rerank_enabled ? { rerank: {
          enabled: true, capability_id: 'retrieval.rerank', version: draft.rerank_version,
          candidate_limit: Math.max(1, Math.min(50, Number(draft.rerank_candidate_limit) || 20)),
          top_k: Math.max(1, Math.min(Number(draft.rerank_candidate_limit) || 20, Number(draft.rerank_top_k) || 20)),
          failure_mode: draft.rerank_failure_mode,
        } } : {}),
      },
    },
    monitor_policy: {
      ...policyBase.monitor, enabled: draft.monitor_enabled,
      schedule: { kind: 'every', every_ms: Math.max(60000, Math.round(Number(draft.monitor_every_minutes) * 60000)) },
      message: draft.monitor_message, context_mode: draft.monitor_context_mode, preflight_mode: draft.monitor_preflight_mode,
      session_mode: draft.monitor_session_mode, delivery: draft.monitor_delivery,
      active_hours: draft.monitor_active_hours_enabled ? {
        start: draft.monitor_active_hours_start, end: draft.monitor_active_hours_end, timezone: draft.monitor_active_hours_timezone,
      } : null,
    },
    output_policy: { ...policyBase.output },
  }
}
