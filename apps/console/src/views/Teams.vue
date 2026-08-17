<template>
  <div class="page teams-page">
    <header class="page-heading">
      <div><span class="eyebrow">AGENTTEAM CONTROL PLANE</span><h1>高级 AgentTeam 配置</h1><p>平台管理员的底层控制面：冻结成员、委派关系、预算和共享上下文策略。方案设计请使用 <RouterLink to="/teams/compose">Team Composer 协作编排器</RouterLink>。</p></div>
      <div class="heading-actions"><button class="secondary-button" type="button" @click="createDraft">新建 Team</button><button class="secondary-button" type="button" :disabled="busy || immutable || !form.team_id" @click="migrate">迁移 Blueprint</button><RouterLink class="secondary-button composer-link" :to="`/teams/compose?team_id=${encodeURIComponent(form.team_id)}`">在 Team Composer 中编辑</RouterLink><button class="secondary-button" type="button" :disabled="loading" @click="load">刷新</button></div>
    </header>

    <section class="concept panel">
      <article><span>01</span><div><strong>AgentTeam Revision</strong><p>发布时精确绑定每个成员的 Agent Revision；Run 接受后继续冻结 Team Revision。</p></div></article>
      <article><span>02</span><div><strong>Typed Collaboration DAG</strong><p>每个步骤声明依赖、阶段、交付标准以及 produce、review、revise、synthesize 或 checkpoint 类型。</p></div></article>
      <article><span>03</span><div><strong>Shared Workspace</strong><p>任务结果、评审和证据追加到 Run 级 Workspace；它不是第二套聊天或状态机。</p></div></article>
    </section>
    <section class="context-contract panel">
      <header><div><span class="eyebrow">CONTEXT SYNC CONTRACT</span><strong>只同步完成协作所必需的上下文</strong></div><small>具体字段随 Team Revision 冻结</small></header>
      <div><article><strong>必须同步</strong><p>根目标 · 成员身份与职责 · 当前分配目标 · 已确认输入 · DAG 依赖结果 · Policy 快照</p></article><article><strong>按策略同步</strong><p>Workspace 结果与证据 · 可见性 · 条目类型 · 字段白名单 · 单条和总字符预算</p></article><article class="excluded"><strong>默认不共享</strong><p>完整会话 · 私有 Memory · System Prompt · 密钥 · 原始 Tool 参数 · 私有推理</p></article></div>
    </section>

    <div v-if="error" class="notice error-notice">{{ error }}</div>
    <section class="team-workspace">
      <aside class="panel team-list">
        <header><strong>发布目录</strong><span>{{ teams.length }}</span></header>
        <button v-for="team in teams" :key="team.revision_id" type="button" :class="{ active: form.team_id === team.team_id }" @click="selectTeam(team.team_id)">
          <span :class="['team-state', team.status]">{{ team.status }}</span><strong>{{ team.name }}</strong><small>{{ team.team_id }} · v{{ team.version }}</small>
        </button>
        <p v-if="!teams.length && !loading">还没有 AgentTeam。</p>
      </aside>

      <section class="panel editor">
        <header class="editor-heading"><div><span class="eyebrow">COLLABORATION CONTRACT</span><h2>{{ form.name || '新 AgentTeam' }}</h2><small class="blueprint-summary">Blueprint：{{ blueprintSummary }}</small></div><span :class="['status-badge', form.status]">{{ form.status }}</span></header>
        <div class="form-grid">
          <label>Team ID<input v-model="form.team_id" :disabled="immutable" placeholder="team.market-research" /></label>
          <label>Revision ID<input v-model="form.revision_id" :disabled="immutable" placeholder="team.market-research:v1" /></label>
          <label>版本<input v-model.number="form.version" :disabled="immutable" type="number" min="1" /></label>
          <label>名称<input v-model="form.name" :disabled="immutable" /></label>
          <label class="wide">说明<textarea v-model="form.description" :disabled="immutable" rows="2" /></label>
        </div>

        <div class="member-section">
          <header><div><strong>成员与委派</strong><small>成员必须指向当前已发布 Agent Revision</small></div><button class="secondary-button" type="button" :disabled="immutable" @click="addMember">增加成员</button></header>
          <article v-for="(member, index) in form.members" :key="index" class="member-card">
            <div class="member-row">
              <label>Member ID<input v-model="member.member_id" :disabled="immutable" /></label>
              <label>Agent<select :value="agentValue(member)" :disabled="immutable" @change="selectAgent(member, $event)"><option value="">选择 Agent</option><option v-for="agent in availableAgents" :key="agent.agent_id" :value="agent.agent_id">{{ agent.name }} · {{ agent.agent_id }}</option></select></label>
              <label>角色<input v-model="member.role" :disabled="immutable" placeholder="researcher" /></label>
              <button class="remove" type="button" :disabled="immutable || form.members.length <= 2" @click="removeMember(index)">移除</button>
            </div>
            <label>职责<textarea v-model="member.responsibility" :disabled="immutable" rows="2" /></label>
            <div class="handoff-row"><label><input v-model="member.can_delegate" :disabled="immutable" type="checkbox" /> 可继续委派</label><label>允许交接<select v-model="member.allowed_handoffs" :disabled="immutable || !member.can_delegate" multiple><option v-for="target in form.members.filter(item => item.member_id && item.member_id !== member.member_id)" :key="target.member_id" :value="target.member_id">{{ target.member_id }}</option></select></label><label><input v-model="form.coordinator_member_id" :disabled="immutable" type="radio" :value="member.member_id" /> 主协调器</label></div>
            <code>{{ member.agent_revision_id || '尚未绑定 Revision' }}</code>
          </article>
        </div>

        <div class="policy-grid">
          <label>Context Policy<textarea v-model="contextText" :disabled="immutable" rows="8" /></label>
          <label>Budget Policy<textarea v-model="budgetText" :disabled="immutable" rows="8" /></label>
          <label>Approval Policy<textarea v-model="approvalText" :disabled="immutable" rows="8" /></label>
        </div>
        <footer class="editor-actions"><button class="secondary-button" type="button" :disabled="busy || immutable" @click="save">保存 Draft</button><button class="primary-button" type="button" :disabled="busy || form.status !== 'draft' || !stored" @click="publish">发布 Team Revision</button><small>调用 Run 时使用 <code>execution.mode=team</code>；同一个根 Run 只有一个顶层编排权威。</small></footer>
      </section>
    </section>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { getAdminAgents, type AdminAgent } from '../api/admin'
import { listAgentTeamRevisions, listAgentTeams, migrateTeamBlueprint, publishAgentTeamRevision, saveAgentTeamRevision, type AgentTeamMember, type AgentTeamRevision } from '../api/teams'

const teams = ref<AgentTeamRevision[]>([])
const revisions = ref<AgentTeamRevision[]>([])
const agents = ref<AdminAgent[]>([])
const loading = ref(false)
const busy = ref(false)
const error = ref('')
const stored = ref(false)
const contextText = ref('')
const budgetText = ref('')
const approvalText = ref('')
const blankMember = (id: string, role: string): AgentTeamMember => ({ member_id: id, agent_id: '', agent_revision_id: '', role, responsibility: '', can_delegate: false, allowed_handoffs: [] })
const blank = (): AgentTeamRevision => ({ team_id: '', revision_id: '', version: 1, name: '', description: '', coordinator_member_id: 'coordinator', members: [blankMember('coordinator', 'coordinator'), blankMember('specialist', 'specialist')], context_policy: {}, budget_policy: {}, approval_policy: {}, status: 'draft', created_by: '' })
const form = reactive<AgentTeamRevision>(blank())
const immutable = computed(() => form.status !== 'draft')
const availableAgents = computed(() => agents.value.filter(item => item.current_revision_id && item.revision?.status === 'published'))

function parse(value: string, name: string) { try { return JSON.parse(value || '{}') as Record<string, unknown> } catch { throw new Error(`${name} 不是有效 JSON`) } }
function fill(value: AgentTeamRevision) { Object.assign(form, structuredClone(value)); contextText.value = JSON.stringify(value.context_policy || {}, null, 2); budgetText.value = JSON.stringify(value.budget_policy || {}, null, 2); approvalText.value = JSON.stringify(value.approval_policy || {}, null, 2); stored.value = true }
const blueprintSummary = computed(() => { const blueprint = form.collaboration_blueprint; if (!blueprint) return '隐式默认（parallel_synthesize，不约束 Coordinator）'; return `${blueprint.preset} · ${blueprint.phases.length} 阶段${blueprint.guardrails.require_plan_confirmation ? ' · 计划需确认' : ''}` })
async function migrate() { busy.value = true; error.value = ''; try { const created = await migrateTeamBlueprint(form.team_id); await selectTeam(created.team_id); await load() } catch (cause) { error.value = cause instanceof Error ? cause.message : '迁移失败' } finally { busy.value = false } }
function createDraft() { Object.assign(form, blank()); contextText.value = JSON.stringify({ workspace_enabled: true, default_visibility: 'team', workspace_entry_types: ['task_result', 'subagent_result', 'decision', 'evidence'], workspace_fields: ['summary', 'content', 'structured_output', 'artifact_id', 'tools_used'], max_entries: 20, max_chars: 20000, max_entry_chars: 6000 }, null, 2); budgetText.value = JSON.stringify({ max_tasks: 32, max_parallel_tasks: 4, max_handoffs: 16, max_review_rounds: 2 }, null, 2); approvalText.value = '{}'; stored.value = false; error.value = '' }
function addMember() { form.members.push(blankMember(`member-${form.members.length + 1}`, 'specialist')) }
function removeMember(index: number) { const removed = form.members[index]?.member_id; form.members.splice(index, 1); for (const member of form.members) member.allowed_handoffs = member.allowed_handoffs.filter(item => item !== removed); if (form.coordinator_member_id === removed) form.coordinator_member_id = form.members[0]?.member_id || '' }
function agentValue(member: AgentTeamMember) { return member.agent_id }
function selectAgent(member: AgentTeamMember, event: Event) { const id = (event.target as HTMLSelectElement).value; const agent = availableAgents.value.find(item => item.agent_id === id); member.agent_id = id; member.agent_revision_id = agent?.current_revision_id || '' }
async function load() { loading.value = true; error.value = ''; try { [teams.value, agents.value] = await Promise.all([listAgentTeams(), getAdminAgents()]); if (!form.team_id && teams.value.length) await selectTeam(teams.value[0].team_id); else if (!form.team_id) createDraft() } catch (cause) { error.value = cause instanceof Error ? cause.message : '加载 AgentTeam 失败' } finally { loading.value = false } }
async function selectTeam(teamId: string) { revisions.value = await listAgentTeamRevisions(teamId); const target = revisions.value.find(item => item.status === 'draft') || revisions.value.find(item => item.status === 'published') || revisions.value[0]; if (target) fill(target) }
async function save() { busy.value = true; error.value = ''; try { form.context_policy = parse(contextText.value, 'Context Policy'); form.budget_policy = parse(budgetText.value, 'Budget Policy'); form.approval_policy = parse(approvalText.value, 'Approval Policy'); const saved = await saveAgentTeamRevision({ ...form, members: form.members.map(item => ({ ...item, allowed_handoffs: item.can_delegate ? item.allowed_handoffs : [] })) }); fill(saved); await load() } catch (cause) { error.value = cause instanceof Error ? cause.message : '保存失败' } finally { busy.value = false } }
async function publish() { busy.value = true; error.value = ''; try { fill(await publishAgentTeamRevision(form.team_id, form.revision_id)); await load() } catch (cause) { error.value = cause instanceof Error ? cause.message : '发布失败' } finally { busy.value = false } }

onMounted(load)
</script>

<style scoped>
.teams-page{display:grid;gap:16px}.heading-actions,.editor-actions{display:flex;gap:8px;align-items:center}.concept{display:grid;grid-template-columns:repeat(3,1fr);overflow:hidden}.concept article{display:grid;grid-template-columns:34px 1fr;gap:10px;padding:16px}.concept article+article{border-left:1px solid var(--border)}.concept span{color:var(--accent);font:10px var(--font-mono)}.concept strong{color:var(--text-strong);font-size:11px}.concept p,.team-list p{margin:4px 0 0;color:var(--text-muted);font-size:9px;line-height:1.6}.context-contract{overflow:hidden}.context-contract header{display:flex;align-items:center;justify-content:space-between;padding:14px 16px;border-bottom:1px solid var(--border)}.context-contract header div{display:grid;gap:4px}.context-contract header small{color:var(--text-muted)}.context-contract>div{display:grid;grid-template-columns:repeat(3,1fr)}.context-contract article{padding:14px 16px}.context-contract article+article{border-left:1px solid var(--border)}.context-contract article strong{font-size:10px;color:var(--text-strong)}.context-contract article p{margin:5px 0 0;color:var(--text-muted);font-size:9px;line-height:1.6}.context-contract .excluded strong{color:var(--warning)}.team-workspace{display:grid;grid-template-columns:280px minmax(0,1fr);gap:16px;align-items:start}.team-list{overflow:hidden}.team-list header,.editor-heading,.member-section>header{display:flex;align-items:center;justify-content:space-between;padding:16px;border-bottom:1px solid var(--border)}.team-list button{display:grid;width:100%;grid-template-columns:auto 1fr;gap:4px 9px;padding:13px 15px;color:var(--text);background:transparent;border:0;border-bottom:1px solid var(--border);text-align:left}.team-list button.active{background:var(--accent-subtle)}.team-list button small{grid-column:2;color:var(--text-muted);font:8px var(--font-mono)}.team-state{padding:3px 5px;border-radius:5px;font:7px var(--font-mono)}.team-state.published{color:var(--success);background:color-mix(in srgb,var(--success) 10%,transparent)}.team-state.draft{color:var(--warning);background:var(--warning-subtle)}.editor{overflow:hidden}.editor-heading h2{margin:5px 0 0}.blueprint-summary{color:var(--text-muted);font-size:9px}.composer-link{display:inline-flex;align-items:center;padding:9px 14px;text-decoration:none}.form-grid,.policy-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:12px;padding:16px}.form-grid label,.policy-grid label,.member-card label{display:grid;gap:5px;color:var(--text-muted);font-size:9px}.wide{grid-column:1/-1}input,select,textarea{width:100%;padding:9px;color:var(--text);background:var(--input);border:1px solid var(--border-strong);border-radius:8px}textarea{resize:vertical}.member-section{border-block:1px solid var(--border)}.member-section>header div{display:grid;gap:3px}.member-section>header small{color:var(--text-muted)}.member-card{display:grid;gap:10px;padding:15px 16px;border-bottom:1px solid var(--border)}.member-row{display:grid;grid-template-columns:1fr 1.4fr 1fr auto;gap:10px;align-items:end}.remove{padding:9px;color:var(--danger);background:transparent;border:1px solid var(--border);border-radius:8px}.handoff-row{display:grid;grid-template-columns:160px minmax(220px,1fr) 140px;gap:12px;align-items:center}.handoff-row>label:first-child,.handoff-row>label:last-child{display:flex;grid-auto-flow:column;justify-content:start;align-items:center}.handoff-row input[type=checkbox],.handoff-row input[type=radio]{width:auto}.member-card code{color:var(--text-muted);font-size:8px}.policy-grid{grid-template-columns:repeat(3,1fr)}.policy-grid textarea{font:9px/1.5 var(--font-mono)}.editor-actions{padding:16px}.editor-actions small{margin-left:auto;color:var(--text-muted)}
@media(max-width:1000px){.team-workspace,.concept,.context-contract>div{grid-template-columns:1fr}.concept article+article,.context-contract article+article{border-left:0;border-top:1px solid var(--border)}.policy-grid{grid-template-columns:1fr}}@media(max-width:720px){.member-row,.handoff-row,.form-grid{grid-template-columns:1fr}.wide{grid-column:auto}.editor-actions{align-items:flex-start;flex-direction:column}.editor-actions small{margin-left:0}}
</style>
