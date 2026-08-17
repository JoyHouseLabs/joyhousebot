import { computed, reactive, ref } from 'vue'
// The returned object is wrapped in reactive() so views can use
// `composer.step` in both templates and script without manual .value access.
import {
  type AgentTeamMember,
  type AgentTeamRevision,
  type BlueprintPreset,
  type BlueprintValidation,
  type CollaborationBlueprint,
  type ConfigurationRolloutSummary,
  getBlueprintPresets,
  getTeamLatestRollout,
  listAgentTeamRevisions,
  listAgentTeams,
  migrateTeamBlueprint,
  saveAgentTeamRevision,
  publishAgentTeamRevision,
  validateBlueprint,
} from '../api/teams'
import { getAdminAgents, type AdminAgent } from '../api/admin'

/** Team Composer wizard state: members → preset → guardrails → publish. */

export interface ComposerMember extends AgentTeamMember {
  agentName: string
  published: boolean
}

export type ComposerStep = 0 | 1 | 2 | 3

const _DEFAULT_BUDGET = { max_tasks: 16, max_parallel_tasks: 4, max_handoffs: 16, max_review_rounds: 2 }
const _DEFAULT_CONTEXT = {
  workspace_enabled: true,
  default_visibility: 'team',
  workspace_entry_types: ['task_result', 'subagent_result'],
  workspace_fields: ['summary', 'content', 'structured_output', 'artifact_id', 'tools_used'],
  max_entries: 20,
  max_chars: 20000,
}
const _DEFAULT_APPROVAL = { require_result_approval: false }

export function useTeamComposer() {
  const step = ref<ComposerStep>(0)
  const busy = ref(false)
  const error = ref('')
  const notice = ref('')
  const presets = ref<BlueprintPreset[]>([])
  const agents = ref<AdminAgent[]>([])
  const teams = ref<AgentTeamRevision[]>([])
  const validation = ref<BlueprintValidation | null>(null)
  const rollout = ref<ConfigurationRolloutSummary | null>(null)
  const publishedRevision = ref<AgentTeamRevision | null>(null)

  const form = reactive({
    team_id: '',
    revision_id: '',
    version: 1,
    name: '',
    description: '',
    coordinator_member_id: 'coordinator',
    members: [] as ComposerMember[],
    context_policy: { ..._DEFAULT_CONTEXT } as Record<string, unknown>,
    budget_policy: { ..._DEFAULT_BUDGET } as Record<string, unknown>,
    approval_policy: { ..._DEFAULT_APPROVAL } as Record<string, unknown>,
    preset: 'parallel_review_revise_synthesize',
    role_bindings: {} as Record<string, string[]>,
    guardrails: {
      max_parallel_tasks: 4,
      require_review: true,
      require_plan_confirmation: true,
      require_final_confirmation: false,
    },
  })

  const draft = computed<AgentTeamRevision>(() => ({
    team_id: form.team_id,
    revision_id: form.revision_id,
    version: form.version,
    name: form.name,
    description: form.description,
    coordinator_member_id: form.coordinator_member_id,
    members: form.members.map(({ agentName: _agentName, published: _published, ...member }) => member),
    context_policy: form.context_policy,
    budget_policy: form.budget_policy,
    approval_policy: form.approval_policy,
    collaboration_blueprint: {
      schema_version: 1,
      preset: form.preset,
      role_bindings: form.role_bindings,
      guardrails: form.guardrails,
    } as unknown as CollaborationBlueprint,
    status: 'draft',
    created_by: 'console',
  }))

  const memberIds = computed(() => form.members.map((item) => item.member_id))
  const stored = ref(false)
  const canSave = computed(() => Boolean(form.team_id && form.revision_id && form.name.trim() && form.members.length >= 2 && form.members.every((item) => item.agent_id && item.role.trim() && item.responsibility.trim())))
  const currentPreset = computed(() => presets.value.find((item) => item.preset === form.preset) || null)

  function suggestIds() {
    if (!form.team_id) form.team_id = `team.${Date.now().toString(36)}`
    if (!form.revision_id) form.revision_id = `${form.team_id}:v${form.version}`
  }

  function addMember(agent: AdminAgent) {
    if (form.members.some((item) => item.agent_id === agent.agent_id && item.agent_revision_id === agent.current_revision_id)) return
    const base = (agent.name || agent.agent_id).toLowerCase().replace(/[^a-z0-9]+/g, '_').replace(/^_|_$/g, '') || 'member'
    let member_id = base
    let suffix = 2
    while (form.members.some((item) => item.member_id === member_id)) member_id = `${base}_${suffix++}`
    form.members.push({
      member_id,
      agent_id: agent.agent_id,
      agent_revision_id: agent.current_revision_id || agent.revision?.revision_id || '',
      role: agent.name || agent.agent_id,
      responsibility: agent.description || '负责一个明确的协作职责。',
      can_delegate: false,
      allowed_handoffs: [],
      agentName: agent.name || agent.agent_id,
      published: Boolean(agent.current_revision_id && agent.revision?.status === 'published'),
    })
    rebuildBindings()
  }

  function removeMember(memberId: string) {
    form.members = form.members.filter((item) => item.member_id !== memberId)
    if (form.coordinator_member_id === memberId && form.members[0]) form.coordinator_member_id = form.members[0].member_id
    rebuildBindings()
  }

  /** Keep role bindings pointing at live members; default every member to a producer. */
  function rebuildBindings() {
    const bindings: Record<string, string[]> = {}
    const slots = currentPreset.value?.bindings || ['producers']
    const reviewerSlot = slots.find((item) => item !== 'producers' && item !== 'chain')
    for (const slot of slots) bindings[slot] = []
    const coordinator = form.coordinator_member_id
    for (const member of form.members) {
      if (member.member_id === coordinator) continue
      if (reviewerSlot && !bindings[reviewerSlot].length) bindings[reviewerSlot].push(member.member_id)
      else bindings.producers?.push(member.member_id)
    }
    if (reviewerSlot && !bindings[reviewerSlot].length && form.members.length > 1) {
      const candidate = form.members.find((item) => item.member_id !== coordinator)
      if (candidate) {
        bindings.producers = (bindings.producers || []).filter((id) => id !== candidate.member_id)
        bindings[reviewerSlot].push(candidate.member_id)
      }
    }
    if (slots.includes('chain')) bindings.chain = memberIds.value.filter((id) => id !== coordinator)
    form.role_bindings = bindings
  }

  function applyPreset(preset: string) {
    form.preset = preset
    rebuildBindings()
    validation.value = null
  }

  async function loadCatalog() {
    busy.value = true
    error.value = ''
    try {
      const [presetItems, agentItems, teamItems] = await Promise.all([getBlueprintPresets(), getAdminAgents(), listAgentTeams()])
      presets.value = presetItems
      agents.value = agentItems
      teams.value = teamItems
    } catch (cause) {
      error.value = cause instanceof Error ? cause.message : '读取目录失败'
    } finally {
      busy.value = false
    }
  }

  async function runValidation(): Promise<boolean> {
    if (form.members.length < 2) {
      validation.value = { ok: false, errors: [{ code: 'members', message: '至少需要两名成员' }], normalized: null }
      return false
    }
    try {
      validation.value = await validateBlueprint({
        blueprint: {
          schema_version: 1,
          preset: form.preset,
          role_bindings: form.role_bindings,
          guardrails: form.guardrails,
        },
        members: draft.value.members,
        coordinator_member_id: form.coordinator_member_id,
        budget_policy: form.budget_policy,
      })
      return Boolean(validation.value?.ok)
    } catch (cause) {
      error.value = cause instanceof Error ? cause.message : 'Blueprint 校验失败'
      return false
    }
  }

  async function save() {
    suggestIds()
    if (!canSave.value) throw new Error('请先完整填写成员、角色与职责')
    const saved = await saveAgentTeamRevision(draft.value, form.role_bindings)
    stored.value = true
    notice.value = `草稿已保存：${saved.revision_id}`
    await refreshRollout(saved.team_id)
    return saved
  }

  async function publish() {
    if (!(await runValidation())) throw new Error('Blueprint 校验未通过，无法发布')
    const saved = await save()
    const published = await publishAgentTeamRevision(saved.team_id, saved.revision_id)
    publishedRevision.value = published
    notice.value = `已发布 ${published.revision_id}，Worker 预热 rollout 已启动`
    await refreshRollout(published.team_id)
    return published
  }

  async function refreshRollout(teamId: string) {
    rollout.value = await getTeamLatestRollout(teamId)
  }

  async function loadExistingTeam(teamId: string) {
    const revisions = await listAgentTeamRevisions(teamId)
    const chosen = revisions.find((item) => item.status === 'draft') || revisions.find((item) => item.status === 'published')
    if (!chosen) throw new Error('未找到可用 Team 版本')
    form.team_id = chosen.team_id
    form.revision_id = chosen.revision_id
    form.version = chosen.version
    form.name = chosen.name
    form.description = chosen.description
    form.coordinator_member_id = chosen.coordinator_member_id
    form.context_policy = { ...chosen.context_policy }
    form.budget_policy = { ...chosen.budget_policy, ..._DEFAULT_BUDGET, ...chosen.budget_policy }
    form.approval_policy = { ..._DEFAULT_APPROVAL, ...chosen.approval_policy }
    form.members = chosen.members.map((member) => ({
      ...member,
      agentName: member.agent_id,
      published: true,
    }))
    const blueprint = chosen.collaboration_blueprint
    if (blueprint) {
      form.preset = blueprint.preset
      form.guardrails = { ...form.guardrails, ...blueprint.guardrails }
    }
    stored.value = chosen.status === 'draft'
    await refreshRollout(teamId)
    return chosen
  }

  async function migrateLegacyTeam(teamId: string) {
    const created = await migrateTeamBlueprint(teamId)
    notice.value = `已生成显式 Blueprint 草稿：${created.revision_id}`
    await loadExistingTeam(teamId)
    return created
  }

  return reactive({
    step, busy, error, notice, presets, agents, teams, validation, rollout, publishedRevision,
    form, draft, memberIds, stored, canSave, currentPreset,
    suggestIds, addMember, removeMember, rebuildBindings, applyPreset,
    loadCatalog, runValidation, save, publish, refreshRollout, loadExistingTeam, migrateLegacyTeam,
  })
}
