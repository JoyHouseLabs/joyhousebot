<template>
  <div class="page agents-page">
    <header class="page-heading">
      <div>
        <span class="eyebrow">AGENT CATALOG</span>
        <h1>Agent 配置</h1>
        <p>管理角色、模型、工具与 Skill 策略；保存草稿后发布为不可变版本。</p>
      </div>
      <div class="heading-actions">
        <button class="secondary-button" type="button" @click="runUnitTest">单元测试</button>
        <button class="secondary-button" type="button" :disabled="loading" @click="loadCatalog">
          {{ loading ? '刷新中…' : '刷新目录' }}
        </button>
        <button class="primary-button" type="button" @click="createAgent">＋ 新建 Agent</button>
      </div>
    </header>

    <div v-if="error" class="notice error-notice">{{ error }}</div>
    <div v-if="unitTest" class="notice" :class="unitTest.ok ? 'test-success' : 'error-notice'"><strong>{{ unitTest.ok ? '✓ 配置单元测试通过' : '× 配置单元测试未通过' }}</strong><span v-for="item in unitTest.checks" :key="item" class="test-check">{{ item }}</span></div>

    <div class="agent-workspace">
      <aside class="panel agent-directory">
        <div class="directory-heading">
          <div><span class="eyebrow">DIRECTORY</span><strong>{{ agents.length }} 个 Agent</strong></div>
          <input v-model.trim="search" type="search" placeholder="搜索 Agent" />
        </div>
        <button
          v-for="agent in filteredAgents"
          :key="agent.agent_id"
          class="agent-row"
          :class="{ active: selectedAgentId === agent.agent_id }"
          type="button"
          @click="selectAgent(agent.agent_id)"
        >
          <span class="agent-avatar">{{ agent.name.slice(0, 1).toUpperCase() }}</span>
          <span class="agent-row-copy">
            <strong>{{ agent.name }}</strong>
            <small>{{ agent.agent_id }} · {{ roleLabel(agent.role) }}</small>
          </span>
          <span class="agent-row-state">
            <i :class="agent.status" />
            {{ agent.is_default ? '默认' : statusLabel(agent.status) }}
          </span>
        </button>
        <div v-if="!filteredAgents.length" class="empty-state compact">
          <span>◇</span><strong>没有匹配的 Agent</strong>
        </div>
      </aside>

      <main class="panel agent-editor">
        <div class="editor-header">
          <div>
            <span class="eyebrow">{{ selectedAgentId ? (draftSaved ? 'REVISION DRAFT' : 'NEW DRAFT FROM PUBLISHED') : 'NEW DEFINITION' }}</span>
            <h2>{{ draft.name || '新建 Agent' }}</h2>
            <p v-if="draft.agent_id"><code>{{ draft.revision_id }}</code> · v{{ draft.version }}<span v-if="skillBindingsSourceRevision && !draftSaved"> · 基于 {{ skillBindingsSourceRevision }}</span></p>
          </div>
          <div class="editor-actions">
            <span v-if="activeRevision" class="status-badge" :class="revisionClass(activeRevision.status)">{{ activeRevision.status }}</span>
            <button class="secondary-button" type="button" @click="resetDraft">重置</button>
            <button class="primary-button" type="button" :disabled="saving || !canSave" @click="saveDraft">
              {{ saving ? '保存中…' : '保存 Draft' }}
            </button>
          </div>
        </div>

        <nav class="editor-tabs" aria-label="Agent 配置分区">
          <button v-for="item in editorTabs" :key="item.id" type="button" :class="{ active: editorTab === item.id }" @click="editorTab = item.id">
            {{ item.label }}<small v-if="item.count !== undefined">{{ item.count }}</small>
          </button>
        </nav>

        <form v-if="editorTab === 'profile'" class="agent-form" @submit.prevent="saveDraft">
          <section class="form-section">
            <header><div><span>01</span><h3>身份与职责</h3></div><p>Agent 的稳定标识、目录展示信息和行为边界。</p></header>
            <div class="form-grid">
              <label><span>Agent ID</span><input v-model.trim="draft.agent_id" required :disabled="Boolean(selectedAgentId)" placeholder="research-agent" /></label>
              <label><span>名称</span><input v-model.trim="draft.name" required placeholder="Research Agent" /></label>
              <label><span>Revision ID</span><input v-model.trim="draft.revision_id" required placeholder="research-agent:v1" /></label>
              <label><span>版本号</span><input v-model.number="draft.version" required type="number" min="1" /></label>
              <label class="wide"><span>角色</span><select v-model="draft.role"><option value="coordinator">Coordinator · 协调器</option><option value="executor">Executor · 执行器</option><option value="specialist">Specialist · 专家</option></select></label>
              <div class="role-guide wide" aria-label="Agent 角色说明">
                <button v-for="item in roleDefinitions" :key="item.id" type="button" class="role-card" :class="{ selected: draft.role === item.id }" @click="draft.role = item.id">
                  <span class="role-card-top"><strong>{{ item.label }}</strong><small>{{ item.en }}</small></span>
                  <span>{{ item.summary }}</span>
                </button>
                <div class="role-detail"><strong>{{ selectedRoleDefinition.label }}：{{ selectedRoleDefinition.title }}</strong><p>{{ selectedRoleDefinition.detail }}</p><small><b>典型边界：</b>{{ selectedRoleDefinition.boundary }}</small></div>
              </div>
              <label><span>目录状态</span><select v-model="draft.definition_status"><option value="active">启用</option><option value="disabled">停用</option><option value="archived">归档</option></select></label>
              <label class="wide"><span>描述</span><input v-model.trim="draft.description" placeholder="说明这个 Agent 适合处理什么任务" /></label>
              <label><span>语气</span><select v-model="draft.tone"><option value="helpful">友好</option><option value="clear">清晰</option><option value="professional">专业</option><option value="concise">简洁</option></select></label>
              <label><span>语言策略</span><select v-model="draft.language"><option value="follow-user">跟随用户</option><option value="zh-CN">简体中文</option><option value="en">English</option></select></label>
              <label class="wide"><span>系统指令</span><textarea v-model="draft.instructions" rows="11" placeholder="定义职责、工作方式、限制和输出标准…" /></label>
            </div>
          </section>
        </form>

        <div v-else-if="editorTab === 'model'" class="agent-form">
          <section class="form-section">
            <header><div><span>02</span><h3>模型策略</h3></div><p>Primary 模型、降级链和推理预算随版本冻结。</p></header>
            <div class="form-grid">
              <label class="wide"><span>Primary Model</span><input v-model.trim="draft.primary_model" required placeholder="openrouter/deepseek/deepseek-v4-flash" /></label>
              <label class="wide"><span>Fallback Models</span><input v-model.trim="draft.fallback_models" placeholder="每个模型用逗号分隔" /></label>
              <label><span>Temperature</span><input v-model.number="draft.temperature" type="number" min="0" max="2" step="0.1" /></label>
              <label><span>Max Tokens</span><input v-model.number="draft.max_tokens" type="number" min="1" /></label>
              <label><span>工具迭代上限</span><input v-model.number="draft.max_tool_iterations" type="number" min="1" /></label>
              <label><span>推理强度</span><select v-model="draft.reasoning_effort"><option value="none">不启用（Flash 默认）</option><option value="low">Low</option><option value="medium">Medium</option><option value="high">High</option></select></label>
              <label><span>Thinking Budget</span><input v-model.number="draft.thinking_budget_tokens" type="number" min="0" /></label>
              <label class="switch-label"><input v-model="draft.capture_reasoning" type="checkbox" /><span><strong>记录推理摘要</strong><small>用于运行诊断与审计</small></span></label>
              <label class="switch-label wide"><input v-model="draft.cache_enabled" type="checkbox" /><span><strong>启用模型响应缓存</strong><small>仅匹配 Provider、模型、Agent Revision、完整消息、工具定义和参数都相同的请求；TTL 内直接复用 PostgreSQL 中的结果，不再调用模型。适合意图识别和固定规划；实时、个性化或有副作用的 Agent 不建议启用。</small></span></label>
              <label><span>缓存 TTL（秒）</span><input v-model.number="draft.cache_ttl_seconds" type="number" min="1" max="86400" :disabled="!draft.cache_enabled" /><small class="field-hint">默认 300 秒；缓存命中会记录在运行观测中。</small></label>
            </div>
          </section>
        </div>

        <div v-else-if="editorTab === 'abilities'" class="abilities-pane">
          <section class="form-section ability-section">
            <header><div><span>03</span><h3>工具与能力</h3></div><p>选择此 Agent 可调用的 Tool、Connector、Workflow 或子 Agent。</p></header>
            <div v-if="draft.capability_mode === 'catalog'" class="catalog-summary">
              <div><strong>使用整个已发布目录 · {{ effectiveCapabilityCount }} 项</strong><p>此 Agent 会随发布目录获得可用能力；无需逐项勾选。需要强边界时再切换为白名单。</p></div>
              <button class="secondary-button" type="button" @click="draft.capability_mode = 'allowlist'">改为白名单</button>
              <div class="catalog-groups"><span v-for="group in catalogGroups" :key="group.name"><b>{{ group.count }}</b>{{ group.name }}</span></div>
            </div>
            <template v-else>
            <div class="ability-toolbar">
              <label><span>能力模式</span><select v-model="draft.capability_mode"><option value="catalog">整个已发布目录</option><option value="allowlist">仅允许已选择能力</option></select></label>
              <input v-model.trim="capabilitySearch" type="search" placeholder="筛选能力" />
            </div>
            <div class="capability-grid">
              <label v-for="item in filteredExecutableCapabilities" :key="item.ref.capability_id" class="capability-card" :class="{ selected: draft.allowed_capabilities.includes(item.ref.capability_id) }">
                <input v-model="draft.allowed_capabilities" type="checkbox" :value="item.ref.capability_id" />
                <span class="capability-icon">{{ kindIcon(item.ref.kind) }}</span>
                <span><strong>{{ item.name }}</strong><small>{{ item.ref.capability_id }} · {{ item.ref.version }}</small><em>{{ kindLabel(item.ref.kind) }}</em></span>
              </label>
              <div v-if="!filteredExecutableCapabilities.length" class="empty-state compact"><span>＋</span><strong>目录中暂无可执行能力</strong></div>
            </div>
            </template>
          </section>
        </div>

        <div v-else-if="editorTab === 'planning'" class="agent-form">
          <section class="form-section">
            <header>
              <div><span>04</span><h3>规划策略</h3></div>
              <p>控制任务拆解、子 Agent 委派和并行规模；这些限制随 Agent Revision 冻结并可在运行记录中回放。</p>
            </header>
            <div class="form-grid">
              <label class="switch-label wide"><input v-model="draft.allow_subagents" type="checkbox" /><span><strong>允许派生子 Agent</strong><small>规划器可以将任务委派给其他 Agent</small></span></label>
              <label><span>最大规划步骤</span><input v-model.number="draft.max_steps" type="number" min="1" /></label>
              <label><span>最大并发分支</span><input v-model.number="draft.max_fan_out" type="number" min="1" /></label>
              <label><span>同轮工具调用</span><select v-model="draft.tool_execution_mode"><option value="sequential">严格串行（默认）</option><option value="parallel_safe">并发只读工具</option></select><small class="field-hint">仅允许能力声明为无副作用、幂等且可并发的调用；写入与未知能力仍会串行。</small></label>
              <label><span>同轮最大并发</span><input v-model.number="draft.max_parallel_calls" type="number" min="1" max="128" :disabled="draft.tool_execution_mode !== 'parallel_safe'" /><small class="field-hint">一个模型响应中独立 Tool Call 的上限。结果按原调用顺序回填。</small></label>
              <div class="memory-guide wide"><strong>使用建议</strong><span>简单检索或单一工具任务可设为 1–3 步、关闭子 Agent；需要调研、并行检索或交叉验证时再提高步骤与分支数。并发分支是单个 Run 的上限，实际执行仍受 Worker 槽位与租约控制。</span></div>
            </div>
          </section>
        </div>

        <div v-else-if="editorTab === 'memory'" class="agent-form">
          <section class="form-section">
            <header>
              <div><span>05</span><h3>记忆策略</h3></div>
              <p>记忆按 Agent Revision 生效。搜索类 Agent 建议只保留工作记忆和领域知识；只有个性化 Agent 才开启用户画像与长期记忆。</p>
            </header>
            <div class="form-grid">
              <label class="switch-label wide"><input v-model="draft.memory_enabled" type="checkbox" /><span><strong>启用持久记忆</strong><small>关闭后仍保留当前 Run 的工作记忆，但不会读取或写入用户长期记忆。</small></span></label>
              <label><span>记忆模式</span><select v-model="draft.memory_mode"><option value="task_only">任务型：仅当前任务</option><option value="personalized">个性化：启用用户记忆</option></select></label>
              <label><span>记忆范围</span><select v-model="draft.memory_scope"><option value="user_agent">用户 × Agent</option><option value="user">用户共享</option><option value="session">仅当前会话</option></select></label>
              <div class="memory-guide wide"><strong>记忆层说明</strong><span>工作记忆：当前 Run 计划与工具结果，不持久化；情景记忆：历史摘要与每日记录；个人属性：用户偏好与稳定事实；长期记忆：项目与持续上下文；Agent 记忆：该 Agent 的工作经验。</span></div>
              <label class="switch-label"><input v-model="draft.memory_episodic" type="checkbox" :disabled="!draft.memory_enabled" /><span><strong>情景记忆</strong><small>HISTORY.md 与每日摘要</small></span></label>
              <label class="switch-label"><input v-model="draft.memory_profile" type="checkbox" :disabled="!draft.memory_enabled" /><span><strong>个人属性</strong><small>PROFILE.md，用户偏好与稳定事实</small></span></label>
              <label class="switch-label"><input v-model="draft.memory_long_term" type="checkbox" :disabled="!draft.memory_enabled" /><span><strong>长期记忆</strong><small>MEMORY.md，项目和持续上下文</small></span></label>
              <label class="switch-label"><input v-model="draft.memory_agent" type="checkbox" :disabled="!draft.memory_enabled" /><span><strong>Agent 记忆</strong><small>仅在明确需要时启用</small></span></label>
              <label><span>读取方式</span><select v-model="draft.memory_read_mode" :disabled="!draft.memory_enabled"><option value="auto">自动注入 + 工具检索</option><option value="tool_only">仅通过工具读取</option><option value="none">禁止读取</option></select></label>
              <label><span>写入方式</span><select v-model="draft.memory_write" :disabled="!draft.memory_enabled"><option value="candidate">候选写入（推荐）</option><option value="direct">允许工具直接写入</option><option value="none">禁止写入</option></select></label>
              <label><span>检索 Top K</span><input v-model.number="draft.memory_top_k" type="number" min="1" max="50" :disabled="!draft.memory_enabled" /></label>
              <label><span>注入上限 Tokens</span><input v-model.number="draft.memory_max_tokens" type="number" min="256" max="20000" :disabled="!draft.memory_enabled" /></label>
            </div>
          </section>
        </div>

        <div v-else-if="editorTab === 'skills'" class="skills-pane">
          <section class="form-section">
            <header><div><span>06</span><h3>Skill 绑定</h3></div><p v-if="draftSaved">Skill 绑定到已保存 Draft，发布后随 Agent 版本冻结。</p><p v-else-if="skillBindingsSourceRevision">当前展示 {{ skillBindingsSourceRevision }} 的冻结绑定；保存新的 Draft 后才能修改，已有绑定会自动继承。</p><p v-else>Skill 必须绑定到已保存的 Draft，发布后随 Agent 版本冻结。</p></header>
            <form class="skill-bind-form" @submit.prevent="addSkillBinding">
              <label><span>Skill</span><select v-model="skillDraft.skill_id" required><option value="" disabled>选择已发布 Skill</option><option v-for="skill in skillCapabilities" :key="skill.ref.capability_id" :value="skill.ref.capability_id">{{ skill.name }} · {{ skill.ref.version }}</option></select></label>
              <label><span>激活方式</span><select v-model="skillDraft.activation_mode"><option value="coordinator_selected">协调器选择</option><option value="always">始终启用</option><option value="scenario_required">场景要求</option></select></label>
              <label><span>优先级</span><input v-model.number="skillDraft.priority" type="number" min="0" max="10000" /></label>
              <button class="primary-button" type="submit" :disabled="!draftSaved || !skillDraft.skill_id">绑定 Skill</button>
            </form>
            <div class="binding-list">
              <article v-for="binding in skillBindings" :key="`${binding.skill_id}:${binding.skill_version}`">
                <span class="capability-icon">S</span>
                <div><strong>{{ skillName(binding.skill_id) }}</strong><small>{{ binding.skill_id }} · {{ binding.skill_version }}</small></div>
                <span>{{ activationLabel(binding.activation_mode) }}</span><code>P{{ binding.priority }}</code>
              </article>
              <div v-if="!skillBindings.length" class="empty-state compact"><span>◇</span><strong>{{ draftSaved ? '此 Draft 尚未绑定 Skill' : '当前发布版本未绑定 Skill' }}</strong><p>目录中有 {{ skillCapabilities.length }} 个可选 Skill；先保存 Draft，再添加需要的 Skill。</p></div>
            </div>
          </section>
        </div>

        <div v-else class="revision-pane">
          <section class="form-section">
            <header><div><span>07</span><h3>版本与发布</h3></div><p>发布会将 Draft 设为只读，并触发 Worker rollout。</p></header>
            <div class="revision-list">
              <article v-for="revision in revisions" :key="revision.revision_id" :class="{ current: currentAgent?.current_revision_id === revision.revision_id }">
                <span class="revision-node" />
                <div><strong>v{{ revision.version }} · {{ revision.revision_id }}</strong><small>{{ revision.model_policy.primary || '未配置模型' }} · {{ formatDate(revision.published_at || revision.created_at) }}</small></div>
                <span class="status-badge" :class="revisionClass(revision.status)">{{ revision.status }}</span>
                <button v-if="revision.status === 'draft'" class="primary-button" type="button" @click="publish(revision)">发布</button>
                <span v-else-if="currentAgent?.current_revision_id === revision.revision_id" class="current-label">CURRENT</span>
              </article>
              <div v-if="!revisions.length" class="empty-state compact"><span>◷</span><strong>保存后生成第一个版本</strong></div>
            </div>
          </section>
        </div>
      </main>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { useMessage } from 'naive-ui'
import {
  bindAgentSkill,
  getAdminAgents,
  getAdminCapabilities,
  getAgentRevisions,
  getAgentSkillBindings,
  publishAgentRevision,
  saveAgentRevision,
  type AdminAgent,
  type AdminCapability,
  type AgentRevision,
  type AgentSkillBinding,
} from '../api/admin'

type EditorTab = 'profile' | 'model' | 'abilities' | 'planning' | 'memory' | 'skills' | 'revisions'
type AgentRole = AdminAgent['role']
const roleDefinitions: Array<{ id: AgentRole; label: string; en: string; title: string; summary: string; detail: string; boundary: string }> = [
  { id: 'coordinator', label: '协调器', en: 'Coordinator', title: '拆解、路由与汇总', summary: '负责理解目标并组织执行', detail: '分析用户目标，拆成可执行步骤，选择合适的 Agent、Skill 或 Tool，并汇总结果。', boundary: '默认负责规划和委派，不直接承担大部分业务操作。' },
  { id: 'executor', label: '执行器', en: 'Executor', title: '完成具体任务', summary: '负责调用工具并产出结果', detail: '接收明确的任务，按指令调用已授权的工具、外部 MCP 或 Skill，返回可验证的执行结果。', boundary: '只使用绑定的能力，不负责重新规划整个任务。' },
  { id: 'specialist', label: '专家', en: 'Specialist', title: '处理受限领域问题', summary: '在专业范围内提供判断', detail: '围绕一个领域、知识库或工作流提供专业分析和建议，输出应遵循该领域的规则与格式。', boundary: '能力范围应通过 Skill、Tool 白名单和提示词明确限制。' },
]
const selectedRoleDefinition = computed(() => roleDefinitions.find((item) => item.id === draft.role) || roleDefinitions[1])
type DefinitionStatus = AdminAgent['status']

const message = useMessage()
const loading = ref(false)
const saving = ref(false)
const error = ref('')
const unitTest = ref<{ ok: boolean; checks: string[] } | null>(null)
const agents = ref<AdminAgent[]>([])
const capabilities = ref<AdminCapability[]>([])
const revisions = ref<AgentRevision[]>([])
const skillBindings = ref<AgentSkillBinding[]>([])
const skillBindingsSourceRevision = ref('')
const selectedAgentId = ref('')
const editorTab = ref<EditorTab>('profile')
const search = ref('')
const capabilitySearch = ref('')

const blankDraft = () => ({
  agent_id: '', revision_id: '', version: 1, name: '', description: '',
  role: 'executor' as AgentRole, definition_status: 'active' as DefinitionStatus,
  tone: 'helpful', language: 'follow-user', instructions: '',
  primary_model: 'openrouter/deepseek/deepseek-v4-flash', fallback_models: '', temperature: 0.3, max_tokens: 4096,
  max_tool_iterations: 20, reasoning_effort: 'none', thinking_budget_tokens: 0,
  capture_reasoning: false, cache_enabled: true, cache_ttl_seconds: 300, capability_mode: 'catalog',
  allowed_capabilities: [] as string[], allow_subagents: true, max_steps: 32,
  max_fan_out: 10, tool_execution_mode: 'sequential', max_parallel_calls: 4, memory_enabled: false, memory_mode: 'task_only', memory_scope: 'user_agent',
  memory_episodic: false, memory_profile: false, memory_long_term: false, memory_agent: false,
  memory_read_mode: 'none', memory_write: 'none', memory_top_k: 10, memory_max_tokens: 6000,
})

const draft = reactive(blankDraft())
const policyBase = reactive({
  persona: {} as Record<string, unknown>, model: {} as Record<string, unknown>,
  planning: {} as Record<string, unknown>, capability: {} as Record<string, unknown>,
  memory: {} as Record<string, unknown>, output: {} as Record<string, unknown>,
})
const savedFingerprint = ref('')
const skillDraft = reactive({ skill_id: '', activation_mode: 'coordinator_selected' as AgentSkillBinding['activation_mode'], priority: 100 })
const currentAgent = computed(() => agents.value.find((item) => item.agent_id === selectedAgentId.value))
const activeRevision = computed(() => revisions.value.find((item) => item.revision_id === draft.revision_id))
const filteredAgents = computed(() => { const term = search.value.toLowerCase(); return agents.value.filter((item) => !term || `${item.name} ${item.agent_id} ${item.description}`.toLowerCase().includes(term)) })
const skillCapabilities = computed(() => capabilities.value.filter((item) => item.ref.kind === 'skill'))
const executableCapabilities = computed(() => capabilities.value.filter((item) => item.ref.kind !== 'skill'))
const effectiveCapabilityCount = computed(() => draft.capability_mode === 'catalog' ? executableCapabilities.value.length : draft.allowed_capabilities.length)
const filteredExecutableCapabilities = computed(() => { const term = capabilitySearch.value.toLowerCase(); return executableCapabilities.value.filter((item) => !term || `${item.name} ${item.ref.capability_id} ${item.description}`.toLowerCase().includes(term)) })
const catalogGroups = computed(() => {
  const values = new Map<string, number>()
  for (const item of executableCapabilities.value) {
    const id = item.ref.capability_id
    const name = id.startsWith('dinq.') ? 'Dinq Discover' : kindLabel(item.ref.kind)
    values.set(name, (values.get(name) || 0) + 1)
  }
  return [...values].map(([name, count]) => ({ name, count })).sort((left, right) => right.count - left.count)
})
const draftSaved = computed(() => Boolean(activeRevision.value?.status === 'draft' && savedFingerprint.value === fingerprint()))
const canSave = computed(() => Boolean(draft.agent_id && draft.name && draft.revision_id && draft.primary_model))
const editorTabs = computed(() => [
  { id: 'profile' as const, label: '身份指令' }, { id: 'model' as const, label: '模型策略' },
  { id: 'abilities' as const, label: '工具与能力', count: effectiveCapabilityCount.value },
  { id: 'planning' as const, label: '规划策略' },
  { id: 'memory' as const, label: '记忆策略' },
  { id: 'skills' as const, label: 'Skills', count: skillBindings.value.length },
  { id: 'revisions' as const, label: '版本发布', count: revisions.value.length },
])

function fingerprint() { return JSON.stringify(draft) }
function runUnitTest() {
  const checks: string[] = []; let ok = true
  if (!draft.agent_id || !draft.name || !draft.revision_id) { checks.push('Agent 身份字段不完整'); ok = false } else checks.push('Agent 身份字段完整')
  if (!draft.primary_model) { checks.push('未配置 Primary Model'); ok = false } else checks.push('Primary Model 已配置')
  if (!draft.instructions.trim()) { checks.push('Instructions 为空'); ok = false } else checks.push('Instructions 已配置')
  const unknown = draft.allowed_capabilities.filter((id) => !executableCapabilities.value.some((item) => item.ref.capability_id === id))
  if (unknown.length) { checks.push(`存在未发布能力：${unknown.join(', ')}`); ok = false } else checks.push('能力引用均来自已发布目录')
  unitTest.value = { ok, checks }
}
function splitList(value: string) { return value.split(',').map((item) => item.trim()).filter(Boolean) }
function numberValue(value: unknown, fallback: number) { const parsed = Number(value); return Number.isFinite(parsed) ? parsed : fallback }
function boolValue(value: unknown, fallback: boolean) { return typeof value === 'boolean' ? value : fallback }

function fillDraft(agent: AdminAgent | undefined, revision: AgentRevision | undefined, revisionId: string, version: number) {
  const model = revision?.model_policy || {}
  const planning = revision?.planning_policy || {}
  const ability = revision?.capability_policy || {}
  const memory = revision?.memory_policy || {}
  const persona = revision?.persona || {}
  Object.assign(policyBase, {
    persona: { ...persona }, model: { ...model }, planning: { ...planning },
    capability: { ...ability }, memory: { ...memory }, output: { ...(revision?.output_policy || {}) },
  })
  Object.assign(draft, blankDraft(), {
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
    allow_subagents: boolValue(planning.allow_subagents, true), max_steps: numberValue(planning.max_steps, 32),
    max_fan_out: numberValue(planning.max_fan_out, 10),
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
  })
}

async function loadCatalog() {
  loading.value = true; error.value = ''
  try {
    const [agentItems, capabilityItems] = await Promise.all([getAdminAgents(), getAdminCapabilities()])
    agents.value = agentItems; capabilities.value = capabilityItems
    if (!selectedAgentId.value && agentItems.length) await selectAgent(agentItems[0].agent_id)
    else if (selectedAgentId.value) await selectAgent(selectedAgentId.value)
  } catch (value) { error.value = errorText(value) } finally { loading.value = false }
}

async function selectAgent(agentId: string) {
  selectedAgentId.value = agentId; editorTab.value = 'profile'; skillBindings.value = []; skillBindingsSourceRevision.value = ''
  try {
    revisions.value = await getAgentRevisions(agentId)
    const agent = agents.value.find((item) => item.agent_id === agentId)
    const latestDraft = revisions.value.find((item) => item.status === 'draft')
    const base = latestDraft || agent?.revision || revisions.value.find((item) => item.status === 'published')
    const nextVersion = Math.max(0, ...revisions.value.map((item) => item.version)) + 1
    const targetId = latestDraft?.revision_id || `${agentId}:v${nextVersion}`
    fillDraft(agent, base, targetId, latestDraft?.version || nextVersion)
    if (base) {
      skillBindings.value = await getAgentSkillBindings(agentId, base.revision_id)
      skillBindingsSourceRevision.value = base.revision_id
    }
    savedFingerprint.value = latestDraft ? fingerprint() : ''
  } catch (value) { message.error(errorText(value)) }
}

function createAgent() {
  selectedAgentId.value = ''; revisions.value = []; skillBindings.value = []; skillBindingsSourceRevision.value = ''; savedFingerprint.value = ''
  Object.assign(draft, blankDraft()); Object.assign(policyBase, { persona: {}, model: {}, planning: {}, capability: {}, memory: {}, output: {} }); editorTab.value = 'profile'
}

function resetDraft() { if (selectedAgentId.value) void selectAgent(selectedAgentId.value); else createAgent() }

function payload() {
  return {
    revision_id: draft.revision_id, version: draft.version, name: draft.name, description: draft.description,
    role: draft.role, definition_status: draft.definition_status,
    persona: { ...policyBase.persona, tone: draft.tone, language: draft.language }, instructions: draft.instructions,
    model_policy: {
      ...policyBase.model, primary: draft.primary_model, fallbacks: splitList(draft.fallback_models), temperature: draft.temperature,
      max_tokens: draft.max_tokens, max_tool_iterations: draft.max_tool_iterations,
      capture_reasoning: draft.capture_reasoning, thinking_budget_tokens: draft.thinking_budget_tokens,
      reasoning_effort: draft.reasoning_effort, cache_enabled: draft.cache_enabled, cache_ttl_seconds: draft.cache_ttl_seconds,
      tool_execution: {
        mode: draft.tool_execution_mode,
        max_parallel_calls: Math.max(1, Math.min(128, Number(draft.max_parallel_calls) || 1)),
      },
    },
    planning_policy: { ...policyBase.planning, allow_subagents: draft.allow_subagents, max_steps: draft.max_steps, max_fan_out: draft.max_fan_out },
    capability_policy: { ...policyBase.capability, mode: draft.capability_mode, allowed: draft.allowed_capabilities },
    memory_policy: {
      ...policyBase.memory,
      enabled: draft.memory_enabled,
      mode: draft.memory_mode,
      scope: draft.memory_scope,
      read_mode: draft.memory_enabled ? draft.memory_read_mode : 'none',
      write_mode: draft.memory_enabled ? draft.memory_write : 'none',
      layers: {
        working: { read: true, write: false, persist: false },
        session: { read: true, write: false, persist: true },
        episodic: { read: draft.memory_enabled && draft.memory_episodic, write: draft.memory_enabled && draft.memory_write !== 'none', persist: true },
        profile: { read: draft.memory_enabled && draft.memory_profile, write: draft.memory_enabled && draft.memory_write !== 'none', persist: true },
        long_term: { read: draft.memory_enabled && draft.memory_long_term, write: draft.memory_enabled && draft.memory_write !== 'none', persist: true },
        agent: { read: draft.memory_enabled && draft.memory_agent, write: draft.memory_enabled && draft.memory_write !== 'none', persist: true },
      },
      retrieval: { top_k: draft.memory_top_k, max_tokens: draft.memory_max_tokens },
    },
    output_policy: { ...policyBase.output },
  }
}

async function saveDraft() {
  if (!canSave.value) return
  saving.value = true
  try {
    const inheritedBindings = !activeRevision.value
      ? skillBindings.value.map((item) => ({ ...item, configuration: { ...item.configuration } }))
      : []
    await saveAgentRevision(draft.agent_id, draft.revision_id, payload())
    if (inheritedBindings.length) {
      await Promise.all(inheritedBindings.map((item) => bindAgentSkill(draft.agent_id, draft.revision_id, {
        skill_id: item.skill_id, skill_version: item.skill_version,
        activation_mode: item.activation_mode, priority: item.priority, configuration: item.configuration,
      })))
    }
    selectedAgentId.value = draft.agent_id; message.success('Agent Draft 已保存')
    const [agentItems, revisionItems] = await Promise.all([getAdminAgents(), getAgentRevisions(draft.agent_id)])
    agents.value = agentItems; revisions.value = revisionItems; skillBindings.value = await getAgentSkillBindings(draft.agent_id, draft.revision_id); skillBindingsSourceRevision.value = draft.revision_id; savedFingerprint.value = fingerprint()
  } catch (value) { message.error(errorText(value)) } finally { saving.value = false }
}

async function addSkillBinding() {
  const skill = skillCapabilities.value.find((item) => item.ref.capability_id === skillDraft.skill_id)
  if (!skill || !draftSaved.value) return
  try {
    await bindAgentSkill(draft.agent_id, draft.revision_id, {
      skill_id: skill.ref.capability_id, skill_version: skill.ref.version,
      activation_mode: skillDraft.activation_mode, priority: skillDraft.priority, configuration: {},
    })
    skillBindings.value = await getAgentSkillBindings(draft.agent_id, draft.revision_id)
    skillDraft.skill_id = ''; message.success('Skill 已绑定到当前 Draft')
  } catch (value) { message.error(errorText(value)) }
}

async function publish(revision: AgentRevision) {
  try {
    await publishAgentRevision(draft.agent_id, revision.revision_id)
    message.success('Agent 版本已发布，Worker rollout 已启动')
    await loadCatalog(); editorTab.value = 'revisions'
  } catch (value) { message.error(errorText(value)) }
}

function errorText(value: unknown) { return value instanceof Error ? value.message : '操作失败' }
function roleLabel(value: AgentRole) { return ({ coordinator: '协调器', executor: '执行器', specialist: '专家' } as const)[value] }
function statusLabel(value: DefinitionStatus) { return ({ active: '启用', disabled: '停用', archived: '归档' } as const)[value] }
function revisionClass(value: AgentRevision['status']) { return value === 'published' ? 'completed' : value === 'retired' ? 'cancelled' : 'queued' }
function kindIcon(value: string) { return ({ tool: 'T', connector: 'C', workflow: 'W', agent: 'A' } as Record<string, string>)[value] || '◇' }
function kindLabel(value: string) { return ({ tool: '工具', connector: '连接器', workflow: '工作流', agent: '子 Agent' } as Record<string, string>)[value] || value }
function activationLabel(value: AgentSkillBinding['activation_mode']) { return ({ always: '始终启用', coordinator_selected: '协调器选择', scenario_required: '场景要求' } as const)[value] }
function skillName(id: string) { return skillCapabilities.value.find((item) => item.ref.capability_id === id)?.name || id }
function formatDate(value?: string | null) { return value ? new Date(value).toLocaleString('zh-CN') : '—' }

onMounted(loadCatalog)
watch(() => draft.agent_id, (agentId, previous) => {
  if (selectedAgentId.value) return
  if (!draft.revision_id || draft.revision_id === `${previous}:v1`) draft.revision_id = agentId ? `${agentId}:v1` : ''
})
</script>

<style scoped>
.agent-workspace { display: grid; grid-template-columns: 288px minmax(0, 1fr); gap: 16px; align-items: start; }
.test-success { color: var(--success); background: rgba(50,182,122,.08); border: 1px solid rgba(50,182,122,.22); }.test-check { display: block; margin-top: 3px; color: var(--text-muted); font-size: 11px; }
.agent-directory { position: sticky; top: calc(var(--topbar-height) + 18px); max-height: calc(100vh - var(--topbar-height) - 36px); overflow: auto; }
.directory-heading { display: grid; gap: 14px; padding: 20px 16px 15px; }.directory-heading>div { display: flex; align-items: center; justify-content: space-between; }.directory-heading strong { color: var(--text-strong); font-size: 12px; }.directory-heading input,.ability-toolbar input { width: 100%; padding: 9px 10px; color: var(--text); background: var(--input); border: 1px solid var(--border); border-radius: 9px; outline: none; }
.agent-row { display: grid; width: 100%; grid-template-columns: 35px minmax(0,1fr) auto; gap: 10px; align-items: center; padding: 12px 15px; color: var(--text); background: transparent; border: 0; border-top: 1px solid var(--border); text-align: left; cursor: pointer; }.agent-row:hover,.agent-row.active { background: var(--surface-hover); }.agent-row.active { box-shadow: inset 3px 0 var(--accent); }.agent-avatar { display: grid; width: 35px; height: 35px; place-items: center; color: var(--accent); background: var(--accent-subtle); border: 1px solid var(--accent-border); border-radius: 10px; font-weight: 700; }.agent-row-copy { min-width: 0; display: grid; gap: 2px; }.agent-row-copy strong,.agent-row-copy small { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }.agent-row-copy strong { color: var(--text-strong); font-size: 12px; }.agent-row-copy small { color: var(--text-muted); font: 9px var(--font-mono); }.agent-row-state { display: flex; align-items: center; gap: 5px; color: var(--text-muted); font-size: 9px; }.agent-row-state i { width: 6px; height: 6px; border-radius: 50%; background: var(--success); }.agent-row-state i.disabled { background: var(--warning); }.agent-row-state i.archived { background: var(--text-muted); }
.agent-editor { min-width: 0; overflow: hidden; }.editor-header { display: flex; min-height: 92px; align-items: center; justify-content: space-between; gap: 20px; padding: 18px 22px; }.editor-header h2 { margin: 5px 0 2px; color: var(--text-strong); font-size: 20px; }.editor-header p { margin: 0; color: var(--text-muted); font-size: 10px; }.editor-actions { display: flex; align-items: center; gap: 8px; }
.editor-tabs { display: flex; overflow-x: auto; padding: 0 18px; border-top: 1px solid var(--border); border-bottom: 1px solid var(--border); }.editor-tabs button { display: flex; align-items: center; gap: 6px; padding: 13px 12px 11px; color: var(--text-muted); background: transparent; border: 0; border-bottom: 2px solid transparent; white-space: nowrap; cursor: pointer; }.editor-tabs button.active { color: var(--text-strong); border-bottom-color: var(--accent); }.editor-tabs small { min-width: 17px; padding: 1px 4px; color: var(--accent); background: var(--accent-subtle); border-radius: 99px; font: 9px var(--font-mono); }
.agent-form,.skills-pane,.revision-pane { padding: 22px; }.form-section { border: 1px solid var(--border); border-radius: 13px; overflow: hidden; }.form-section>header { display: flex; align-items: flex-start; justify-content: space-between; gap: 20px; padding: 16px 18px; background: var(--surface-raised); border-bottom: 1px solid var(--border); }.form-section>header>div { display: flex; align-items: center; gap: 9px; }.form-section>header span { color: var(--accent); font: 600 9px var(--font-mono); }.form-section h3 { margin: 0; color: var(--text-strong); font-size: 14px; }.form-section>header p { margin: 0; color: var(--text-muted); font-size: 10px; text-align: right; }
.form-grid { display: grid; grid-template-columns: repeat(2,minmax(0,1fr)); gap: 15px; padding: 18px; }.form-grid label,.skill-bind-form label,.ability-toolbar label { display: grid; gap: 6px; color: var(--text-muted); font-size: 10px; }.form-grid label.wide { grid-column: 1/-1; }.form-grid input,.form-grid select,.form-grid textarea,.skill-bind-form input,.skill-bind-form select,.ability-toolbar select { width: 100%; padding: 9px 10px; color: var(--text); background: var(--input); border: 1px solid var(--border-strong); border-radius: 9px; outline: none; }.form-grid textarea { resize: vertical; line-height: 1.65; }.form-grid input:focus,.form-grid select:focus,.form-grid textarea:focus { border-color: var(--accent-border); box-shadow: 0 0 0 3px var(--accent-subtle); }.switch-label { display: flex !important; flex-direction: row !important; align-items: center; gap: 10px !important; min-height: 54px; padding: 9px 11px; background: var(--surface-raised); border: 1px solid var(--border); border-radius: 10px; }.switch-label input { width: auto; }.switch-label span { display: grid; gap: 2px; }.switch-label strong { color: var(--text); font-size: 11px; }.switch-label small { color: var(--text-muted); }
.abilities-pane { padding: 22px; }.catalog-summary { display:grid; grid-template-columns:minmax(0,1fr) auto; gap:14px; padding:20px; }.catalog-summary strong{color:var(--text-strong);font-size:14px}.catalog-summary p{margin:6px 0 0;color:var(--text-muted);font-size:11px;line-height:1.6}.catalog-groups{grid-column:1/-1;display:flex;gap:8px;flex-wrap:wrap}.catalog-groups span{display:flex;gap:6px;align-items:center;padding:7px 10px;color:var(--text-muted);background:var(--surface-raised);border:1px solid var(--border);border-radius:8px;font-size:10px}.catalog-groups b{color:var(--accent);font:600 11px var(--font-mono)}.ability-toolbar { display: grid; grid-template-columns: minmax(190px,.7fr) 1fr; gap: 12px; align-items: end; padding: 15px 18px; border-bottom: 1px solid var(--border); }.capability-grid { display: grid; grid-template-columns: repeat(3,minmax(0,1fr)); gap: 9px; padding: 15px 18px 18px; }.capability-card { display: grid; grid-template-columns: auto 32px minmax(0,1fr); gap: 9px; align-items: center; padding: 11px; background: var(--surface-raised); border: 1px solid var(--border); border-radius: 10px; cursor: pointer; }.capability-card.selected { background: var(--accent-subtle); border-color: var(--accent-border); }.capability-card>span:last-child { min-width: 0; display: grid; grid-template-columns: 1fr auto; gap: 2px 6px; }.capability-card strong,.capability-card small { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }.capability-card strong { color: var(--text-strong); font-size: 11px; }.capability-card small { grid-column: 1/-1; color: var(--text-muted); font: 8px var(--font-mono); }.capability-card em { color: var(--accent); font-size: 8px; font-style: normal; }.capability-icon { display: grid; width: 30px; height: 30px; place-items: center; color: var(--accent); background: var(--accent-subtle); border-radius: 8px; font-weight: 700; }
.skill-bind-form { display: grid; grid-template-columns: 1.3fr 1fr 110px auto; gap: 12px; align-items: end; padding: 18px; border-bottom: 1px solid var(--border); }.binding-list article { display: grid; grid-template-columns: 32px minmax(0,1fr) auto auto; gap: 11px; align-items: center; padding: 13px 18px; border-bottom: 1px solid var(--border); }.binding-list article:last-child { border-bottom: 0; }.binding-list article>div { min-width: 0; display: grid; gap: 2px; }.binding-list strong { color: var(--text-strong); font-size: 11px; }.binding-list small,.binding-list article>span:nth-last-child(2),.binding-list code { color: var(--text-muted); font-size: 9px; }
.revision-list { padding: 4px 18px 18px; }.revision-list article { position: relative; display: grid; grid-template-columns: 12px minmax(0,1fr) auto auto; gap: 12px; align-items: center; min-height: 66px; padding: 11px 0; border-bottom: 1px solid var(--border); }.revision-list article.current { background: linear-gradient(90deg,var(--accent-subtle),transparent); }.revision-node { width: 9px; height: 9px; border: 2px solid var(--accent); border-radius: 50%; }.revision-list article>div { min-width: 0; display: grid; gap: 3px; }.revision-list strong { color: var(--text-strong); font-size: 11px; }.revision-list small { color: var(--text-muted); font: 9px var(--font-mono); }.current-label { color: var(--accent); font: 600 8px var(--font-mono); }
.role-guide { display: grid; grid-template-columns: repeat(3,minmax(0,1fr)); gap: 8px; margin-top: -2px; }
.role-card { display: grid; gap: 7px; min-height: 82px; padding: 11px; border: 1px solid var(--border); border-radius: 9px; background: var(--surface); color: var(--text-muted); text-align: left; cursor: pointer; }
.role-card:hover,.role-card.selected { border-color: var(--accent); background: var(--accent-subtle); color: var(--text-strong); }
.role-card-top { display: flex; align-items: baseline; justify-content: space-between; gap: 8px; color: var(--text-strong); }.role-card-top small { color: var(--text-muted); font-size: 10px; }.role-card>span:last-child { font-size: 12px; line-height: 1.45; }
.role-detail { grid-column: 1/-1; padding: 11px 13px; border-left: 3px solid var(--accent); border-radius: 5px; background: var(--surface-raised); }.role-detail p { margin: 5px 0; color: var(--text-muted); font-size: 12px; line-height: 1.55; }.role-detail small { color: var(--text-muted); font-size: 11px; }
.memory-guide { display: grid; gap: 5px; padding: 11px 13px; color: var(--text-muted); background: var(--surface-raised); border-left: 3px solid var(--accent); border-radius: 5px; font-size: 11px; line-height: 1.55; }.memory-guide strong { color: var(--text-strong); font-size: 11px; }
@media (max-width: 1120px) { .capability-grid { grid-template-columns: repeat(2,minmax(0,1fr)); } }
@media (max-width: 900px) { .agent-workspace { grid-template-columns: 1fr; }.agent-directory { position: static; max-height: 300px; }.capability-grid { grid-template-columns: repeat(2,minmax(0,1fr)); }.skill-bind-form { grid-template-columns: 1fr 1fr; } }
@media (max-width: 650px) { .editor-header { align-items: flex-start; flex-direction: column; }.editor-actions { width: 100%; flex-wrap: wrap; }.form-grid,.capability-grid,.ability-toolbar,.skill-bind-form,.role-guide { grid-template-columns: 1fr; }.abilities-pane,.agent-form,.skills-pane,.revision-pane { padding: 14px; }.form-section>header { flex-direction: column; }.form-section>header p { text-align: left; }.revision-list article { grid-template-columns: 12px minmax(0,1fr) auto; }.revision-list article button,.current-label { grid-column: 2/-1; justify-self: start; } }
</style>
