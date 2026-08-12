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
            <div class="editor-title-line">
              <h2>{{ draft.name || '新建 Agent' }}</h2>
              <span class="role-chip">{{ roleLabel(draft.role) }}</span>
            </div>
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
            <header><div><span>02</span><h3>模型策略</h3></div><p>Primary 模型、降级链和推理预算随版本冻结。<router-link to="/models">管理模型目录 →</router-link></p></header>
            <div class="form-grid">
              <label class="wide"><span>Primary Model</span><input v-model.trim="draft.primary_model" required list="active-model-catalog" placeholder="provider/exact-model-id" /></label>
              <datalist id="active-model-catalog"><option v-for="model in activeModels" :key="model.model_id" :value="model.model_id">{{ model.name }}</option></datalist>
              <label class="wide"><span>Fallback Models</span><input v-model.trim="draft.fallback_models" list="active-model-catalog" placeholder="每个模型用逗号分隔" /></label>
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
            <header><div><span>03</span><h3>工具与能力</h3></div><p>Agent 只能调用本版本冻结且当前可运行的能力；外部、计费与受限能力必须显式授权。</p></header>
            <div class="ability-toolbar">
              <label><span>能力模式</span><select v-model="draft.capability_mode"><option value="allowlist">严格白名单（推荐）</option><option value="catalog">安全目录 + 显式授权</option></select></label>
              <input v-model.trim="capabilitySearch" type="search" placeholder="筛选能力名称、ID 或说明" />
            </div>
            <div v-if="draft.capability_mode === 'catalog'" class="catalog-summary">
              <div><strong>自动纳入 {{ safeCatalogCapabilities.length }} 项普通能力</strong><p>目录模式只会自动纳入无外部副作用、非计费且非受限的能力。下面的高风险能力即使已发布，也只有勾选后才会进入 Agent 版本。</p></div>
              <button class="secondary-button" type="button" @click="draft.capability_mode = 'allowlist'">改为白名单</button>
              <div class="catalog-groups"><span v-for="group in catalogGroups" :key="group.name"><b>{{ group.count }}</b>{{ group.name }}</span></div>
            </div>
            <div v-if="draft.capability_mode === 'catalog' && protectedCapabilities.length" class="explicit-grant-heading">
              <div><strong>需要显式授权 · {{ protectedCapabilities.length }} 项</strong><small>选择代表你确认该 Agent 可以调用外部或可能产生费用的服务。</small></div>
              <span>{{ selectedProtectedCapabilities.length }} 项已授权</span>
            </div>
            <div class="capability-grid">
              <label
                v-for="item in filteredPolicyCapabilities"
                :key="item.ref.capability_id"
                class="capability-card"
                :class="{ selected: draft.allowed_capabilities.includes(item.ref.capability_id), blocked: !item.execution_ready, protected: item.requires_explicit_grant }"
              >
                <input v-model="draft.allowed_capabilities" type="checkbox" :value="item.ref.capability_id" />
                <span class="capability-icon">{{ kindIcon(item.ref.kind) }}</span>
                <span class="capability-card-copy">
                  <span class="capability-name"><strong>{{ item.name }}</strong><em>{{ kindLabel(item.ref.kind) }}</em></span>
                  <small>{{ item.ref.capability_id }} · {{ item.ref.version }}</small>
                  <span class="capability-states">
                    <b :class="item.execution_ready ? 'ready' : 'blocked'">{{ item.execution_ready ? '可运行' : '未就绪' }}</b>
                    <b v-if="item.requires_explicit_grant" class="explicit">显式授权</b>
                  </span>
                  <span v-if="item.execution_blockers.length" class="capability-blockers">{{ item.execution_blockers.join('；') }}</span>
                </span>
              </label>
              <div v-if="!filteredPolicyCapabilities.length" class="empty-state compact"><span>＋</span><strong>{{ draft.capability_mode === 'catalog' ? '没有需要显式授权的能力' : '目录中暂无匹配的可执行能力' }}</strong></div>
            </div>
            <div v-if="selectedBlockedCapabilities.length" class="ability-warning">
              <strong>当前版本包含 {{ selectedBlockedCapabilities.length }} 项未就绪能力</strong>
              <span>可保存 Draft，但发布前应在扩展中心完成启用、Worker 加载或凭据配置。</span>
            </div>
            <div v-if="permissionEntries.length" class="permission-grants">
              <header><div><strong>执行权限</strong><small>选择能力不会绕过权限边界；权限也会随 Agent Revision 与 Run 冻结。</small></div><span :class="missingCapabilityPermissions.length ? 'missing' : 'complete'">{{ missingCapabilityPermissions.length ? `${missingCapabilityPermissions.length} 项待授权` : '权限完整' }}</span></header>
              <div>
                <label v-for="entry in permissionEntries" :key="entry.permission" :class="{ unused: !entry.required }">
                  <input v-model="draft.granted_permissions" type="checkbox" :value="entry.permission" />
                  <span><code>{{ entry.permission }}</code><small>{{ entry.required ? `用于：${entry.capabilities.join('、')}` : '当前未被所选能力使用，可撤销' }}</small></span>
                </label>
              </div>
            </div>
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
              <label><span>最大重规划次数</span><input v-model.number="draft.max_replans" type="number" min="0" max="10" /><small class="field-hint">结构化计划校验失败后的替换计划上限；耗尽后 Run 明确失败。</small></label>
              <label><span>同轮工具调用</span><select v-model="draft.tool_execution_mode"><option value="sequential">严格串行（默认）</option><option value="parallel_safe">并发只读工具</option></select><small class="field-hint">仅允许能力声明为无副作用、幂等且可并发的调用；写入与未知能力仍会串行。</small></label>
              <label><span>同轮最大并发</span><input v-model.number="draft.max_parallel_calls" type="number" min="1" max="128" :disabled="draft.tool_execution_mode !== 'parallel_safe'" /><small class="field-hint">一个模型响应中独立 Tool Call 的上限。结果按原调用顺序回填。</small></label>
              <div class="memory-guide wide"><strong>使用建议</strong><span>简单检索或单一工具任务可设为 1–3 步、关闭子 Agent；需要调研、并行检索或交叉验证时再提高步骤与分支数。并发分支是单个 Run 的上限，实际执行仍受 Worker 槽位与租约控制。</span></div>
            </div>
          </section>
        </div>

        <div v-else-if="editorTab === 'monitor'" class="agent-form">
          <section class="form-section">
            <header><div><span>05</span><h3>Agent Monitor</h3></div><p>发布后，Runtime 会为使用此 Agent 的每位用户对账一个托管 Schedule；仍走统一 Occurrence、Run、投递与重试链路。</p></header>
            <div class="form-grid">
              <label class="switch-label wide"><input v-model="draft.monitor_enabled" type="checkbox" /><span><strong>启用托管 Monitor</strong><small>用户首次使用 Agent 时自动创建；新 Revision 发布后更新已有托管 Monitor。</small></span></label>
              <label><span>检查间隔（分钟）</span><input v-model.number="draft.monitor_every_minutes" type="number" min="1" :disabled="!draft.monitor_enabled" /></label>
              <label><span>上下文模式</span><select v-model="draft.monitor_context_mode" :disabled="!draft.monitor_enabled"><option value="light">Light：不注入历史、记忆和 Skill Prompt</option><option value="full">Full：完整上下文</option></select></label>
              <label><span>预检策略</span><select v-model="draft.monitor_preflight_mode" :disabled="!draft.monitor_enabled"><option value="runtime_attention">仅 Runtime attention 变化时运行</option><option value="always">每个有效 tick 运行</option></select></label>
              <label><span>会话模式</span><select v-model="draft.monitor_session_mode" :disabled="!draft.monitor_enabled"><option value="isolated">隔离 Monitor 会话</option><option value="main">用户 main 会话</option></select></label>
              <label><span>投递策略</span><select v-model="draft.monitor_delivery" :disabled="!draft.monitor_enabled"><option value="none">仅保留 Run 结果</option><option value="origin">投递到最近一次外部来源</option></select></label>
              <label class="wide"><span>Monitor 指令</span><textarea v-model="draft.monitor_message" rows="5" :disabled="!draft.monitor_enabled" placeholder="检查待处理事项、异常和需要用户关注的变化。" /></label>
              <label class="switch-label wide"><input v-model="draft.monitor_active_hours_enabled" type="checkbox" :disabled="!draft.monitor_enabled" /><span><strong>限制 Active Hours</strong><small>窗口外 occurrence 记录为 skipped_inactive_hours；手动 Run Now 不受限制。</small></span></label>
              <label><span>开始</span><input v-model="draft.monitor_active_hours_start" type="time" :disabled="!draft.monitor_enabled || !draft.monitor_active_hours_enabled" /></label>
              <label><span>结束</span><input v-model="draft.monitor_active_hours_end" type="time" :disabled="!draft.monitor_enabled || !draft.monitor_active_hours_enabled" /></label>
              <label class="wide"><span>IANA 时区</span><input v-model.trim="draft.monitor_active_hours_timezone" :disabled="!draft.monitor_enabled || !draft.monitor_active_hours_enabled" placeholder="Asia/Shanghai" /></label>
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

        <div v-else-if="editorTab === 'memoryData'" class="memory-data-pane">
          <section class="form-section">
            <header>
              <div><span>06</span><h3>记忆数据</h3></div>
              <p>查看当前用户在此 Agent 下真实落库的记忆文档与候选写入；数据不会跨用户或跨 Agent 展示。</p>
            </header>
            <div v-if="!selectedAgentId" class="empty-state"><span>◇</span><strong>请先保存 Agent</strong><p>Agent 建立后才能形成隔离的记忆空间。</p></div>
            <template v-else>
              <div class="memory-summary-grid">
                <button type="button" :class="{ active: memoryLayer === 'all' }" @click="chooseMemoryLayer('all')"><strong>{{ memorySummary.total }}</strong><span>全部文档</span></button>
                <button v-for="item in memoryLayerOptions" :key="item.id" type="button" :class="{ active: memoryLayer === item.id }" @click="chooseMemoryLayer(item.id)"><strong>{{ memorySummary.by_layer[item.id] || 0 }}</strong><span>{{ item.label }}</span></button>
                <div><strong>{{ pendingMemoryCandidates }}</strong><span>待确认候选</span></div>
              </div>
              <div class="memory-data-toolbar">
                <div class="segmented-control">
                  <button type="button" :class="{ active: memoryDataView === 'documents' }" @click="memoryDataView = 'documents'">已生效记忆</button>
                  <button type="button" :class="{ active: memoryDataView === 'candidates' }" @click="memoryDataView = 'candidates'">候选记忆</button>
                </div>
                <template v-if="memoryDataView === 'documents'">
                  <input v-model.trim="memorySearch" type="search" placeholder="搜索路径或记忆内容" @keyup.enter="loadMemoryDocuments" />
                  <button class="secondary-button" type="button" @click="loadMemoryDocuments">搜索</button>
                </template>
                <template v-else>
                  <select v-model="memoryCandidateStatus" @change="loadMemoryCandidates"><option value="all">全部状态</option><option value="pending">待确认</option><option value="conflicted">有冲突</option><option value="merged">已合并</option><option value="rejected">已拒绝</option><option value="expired">已过期</option></select>
                </template>
                <button class="secondary-button" type="button" :disabled="memoryLoading" @click="loadMemoryData">{{ memoryLoading ? '刷新中…' : '刷新数据' }}</button>
              </div>
              <div v-if="memoryError" class="notice error-notice memory-error">{{ memoryError }}</div>
              <div v-if="memoryDataView === 'documents'" class="memory-browser">
                <aside class="memory-document-list">
                  <button v-for="item in memoryDocuments" :key="`${item.scope_key}:${item.document_path}`" type="button" :class="{ active: selectedMemoryDocument?.scope_key === item.scope_key && selectedMemoryDocument?.document_path === item.document_path }" @click="selectMemoryDocument(item)">
                    <span class="memory-layer-tag">{{ memoryLayerLabel(item.layer) }}</span>
                    <strong>{{ item.document_path }}</strong>
                    <p>{{ item.preview || '空文档' }}</p>
                    <small>v{{ item.version }} · {{ formatBytes(item.size_bytes) }} · {{ formatTimestamp(item.updated_at_ms) }}</small>
                  </button>
                  <div v-if="!memoryDocuments.length && !memoryLoading" class="empty-state compact"><span>◇</span><strong>此范围暂无记忆</strong><p>运行中的 Agent 会按已发布策略写入文档或候选箱。</p></div>
                </aside>
                <section class="memory-document-detail">
                  <template v-if="memoryDocumentDetail">
                    <header><div><span class="memory-layer-tag">{{ memoryLayerLabel(memoryDocumentDetail.layer) }}</span><h4>{{ memoryDocumentDetail.document_path }}</h4></div><small>版本 {{ memoryDocumentDetail.version }} · {{ formatTimestamp(memoryDocumentDetail.updated_at_ms) }}</small></header>
                    <pre>{{ memoryDocumentDetail.content || '（空文档）' }}</pre>
                    <footer><code>{{ memoryDocumentDetail.scope_key }}</code><span>{{ formatBytes(memoryDocumentDetail.size_bytes) }}</span></footer>
                  </template>
                  <div v-else class="empty-state"><span>▤</span><strong>选择一份记忆文档</strong><p>右侧将展示完整内容、版本、作用域和更新时间。</p></div>
                </section>
              </div>
              <div v-else class="memory-candidate-list">
                <article v-for="item in memoryCandidates" :key="item.candidate_id">
                  <header><div><span class="memory-layer-tag">{{ memoryLayerLabel(item.layer) }}</span><strong>{{ item.document_path }}</strong></div><span class="status-badge" :class="memoryCandidateClass(item.status)">{{ memoryCandidateLabel(item.status) }}</span></header>
                  <pre>{{ item.content }}</pre>
                  <footer><span>{{ item.operation === 'replace' ? '替换' : '追加' }} · {{ item.source_kind }}<template v-if="item.confidence !== null && item.confidence !== undefined"> · 置信度 {{ Math.round(item.confidence * 100) }}%</template></span><code v-if="item.source_run_id">Run {{ item.source_run_id }}</code><span>{{ formatDate(item.created_at) }}</span></footer>
                </article>
                <div v-if="!memoryCandidates.length && !memoryLoading" class="empty-state"><span>✓</span><strong>当前没有候选记忆</strong><p>候选写入不会直接改变长期资产，确认后才会进入已生效记忆。</p></div>
              </div>
            </template>
          </section>
        </div>

        <div v-else-if="editorTab === 'skills'" class="skills-pane">
          <section class="form-section">
            <header><div><span>07</span><h3>Skill 绑定</h3></div><p v-if="draftSaved">Skill 绑定到已保存 Draft，发布后随 Agent 版本冻结。</p><p v-else-if="skillBindingsSourceRevision">当前展示 {{ skillBindingsSourceRevision }} 的冻结绑定；保存新的 Draft 后才能修改，已有绑定会自动继承。</p><p v-else>Skill 必须绑定到已保存的 Draft，发布后随 Agent 版本冻结。</p></header>
            <form class="skill-bind-form" @submit.prevent="addSkillBinding">
              <label><span>Skill</span><select v-model="skillDraft.skill_id" required><option value="" disabled>选择已发布 Skill</option><option v-for="skill in skillCapabilities" :key="skill.skill_id" :value="skill.skill_id">{{ skill.name }} · {{ skill.current?.version }}</option></select></label>
              <label><span>激活方式</span><select v-model="skillDraft.activation_mode"><option value="coordinator_selected">协调器选择</option><option value="always">始终启用</option><option value="scenario_required">场景要求</option></select></label>
              <label><span>优先级</span><input v-model.number="skillDraft.priority" type="number" min="0" max="10000" /></label>
              <button class="primary-button" type="submit" :disabled="!draftSaved || !skillDraft.skill_id">绑定 Skill</button>
            </form>
            <div class="binding-list">
              <article v-for="binding in skillBindings" :key="`${binding.skill_id}:${binding.skill_version}`">
                <span class="capability-icon">S</span>
                <div><strong>{{ skillName(binding.skill_id) }}</strong><small>{{ binding.skill_id }} · {{ binding.skill_version }} · {{ shortDigest(binding.content_sha256) }}</small></div>
                <span>{{ activationLabel(binding.activation_mode) }}</span><code>P{{ binding.priority }}</code>
                <button v-if="draftSaved" class="text-button danger" type="button" @click="removeSkillBinding(binding)">移除</button>
              </article>
              <div v-if="!skillBindings.length" class="empty-state compact"><span>◇</span><strong>{{ draftSaved ? '此 Draft 尚未绑定 Skill' : '当前发布版本未绑定 Skill' }}</strong><p>目录中有 {{ skillCapabilities.length }} 个可选 Skill；先保存 Draft，再添加需要的 Skill。</p></div>
            </div>
          </section>
        </div>

        <div v-else class="revision-pane">
          <section class="form-section">
            <header><div><span>08</span><h3>版本与发布</h3></div><p>发布会将 Draft 设为只读，并触发 Worker rollout。</p></header>
            <div class="release-policy-row"><label><span>生效方式</span><select v-model="releasePolicy.activation_mode"><option value="automatic">全部预热后自动生效</option><option value="manual">全部预热后等待批准</option></select></label><label><span>预热超时（秒）</span><input v-model.number="releasePolicy.timeout_seconds" type="number" min="10" max="86400" /></label><label class="release-check"><input v-model="releasePolicy.require_healthy_workers" type="checkbox" />必须存在健康 Worker</label><label class="release-check"><input v-model="releasePolicy.auto_rollback" type="checkbox" />失败保护旧版本</label></div>
            <div class="revision-list">
              <article v-for="revision in revisions" :key="revision.revision_id" :class="{ current: currentAgent?.current_revision_id === revision.revision_id }">
                <span class="revision-node" />
                <div><strong>v{{ revision.version }} · {{ revision.revision_id }}</strong><small>{{ revision.model_policy.primary || '未配置模型' }} · {{ formatDate(revision.published_at || revision.created_at) }}</small></div>
                <span class="status-badge" :class="revisionClass(revision.status)">{{ revision.status }}</span>
                <button v-if="revision.status === 'draft'" class="primary-button" type="button" :disabled="releaseBlocked" :title="releaseBlocked ? '存在未就绪能力或缺少执行权限，请先完成配置' : ''" @click="publish(revision)">发布</button>
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
  unbindAgentSkill,
  type AdminAgent,
  type AdminCapability,
  type AgentRevision,
  type AgentSkillBinding,
} from '../api/admin'
import { listSkills, type SkillSummary } from '../api/skills'
import {
  getMemoryCandidates,
  getMemoryDocument,
  getMemoryDocuments,
  type MemoryCandidate,
  type MemoryDocument,
  type MemoryDocumentListItem,
  type MemoryDocumentSummary,
  type MemoryLayer,
} from '../api/memory'
import { listActiveModels, type ModelCatalogItem } from '../api/modelProviders'

type EditorTab = 'profile' | 'model' | 'abilities' | 'planning' | 'monitor' | 'memory' | 'memoryData' | 'skills' | 'revisions'
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
const skills = ref<SkillSummary[]>([])
const activeModels = ref<ModelCatalogItem[]>([])
const revisions = ref<AgentRevision[]>([])
const skillBindings = ref<AgentSkillBinding[]>([])
const skillBindingsSourceRevision = ref('')
const selectedAgentId = ref('')
const editorTab = ref<EditorTab>('profile')
const search = ref('')
const capabilitySearch = ref('')
const memoryLoading = ref(false)
const memoryError = ref('')
const memoryDataView = ref<'documents' | 'candidates'>('documents')
const memoryLayer = ref<MemoryLayer | 'all'>('all')
const memorySearch = ref('')
const memoryCandidateStatus = ref<MemoryCandidate['status'] | 'all'>('all')
const memoryDocuments = ref<MemoryDocumentListItem[]>([])
const memoryDocumentDetail = ref<MemoryDocument | null>(null)
const selectedMemoryDocument = ref<MemoryDocumentListItem | null>(null)
const memoryCandidates = ref<MemoryCandidate[]>([])
const memorySummary = ref<MemoryDocumentSummary>({ total: 0, by_layer: { profile: 0, long_term: 0, episodic: 0, agent: 0 } })
const memoryLayerOptions: Array<{ id: MemoryLayer; label: string }> = [
  { id: 'profile', label: '个人属性' },
  { id: 'long_term', label: '长期记忆' },
  { id: 'episodic', label: '情景记忆' },
  { id: 'agent', label: 'Agent 经验' },
]

const blankDraft = () => ({
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
  monitor_enabled: false, monitor_every_minutes: 30, monitor_context_mode: 'light',
  monitor_preflight_mode: 'runtime_attention', monitor_session_mode: 'isolated',
  monitor_delivery: 'none', monitor_message: 'Review Runtime attention and act if needed.',
  monitor_active_hours_enabled: false, monitor_active_hours_start: '08:00',
  monitor_active_hours_end: '22:00', monitor_active_hours_timezone: 'Asia/Shanghai',
})

const draft = reactive(blankDraft())
const policyBase = reactive({
  persona: {} as Record<string, unknown>, model: {} as Record<string, unknown>,
  planning: {} as Record<string, unknown>, capability: {} as Record<string, unknown>,
  memory: {} as Record<string, unknown>, monitor: {} as Record<string, unknown>, output: {} as Record<string, unknown>,
})
const savedFingerprint = ref('')
const skillDraft = reactive({ skill_id: '', activation_mode: 'coordinator_selected' as AgentSkillBinding['activation_mode'], priority: 100 })
const releasePolicy = reactive({ activation_mode: 'automatic' as 'automatic' | 'manual', timeout_seconds: 300, auto_rollback: true, require_healthy_workers: true })
const currentAgent = computed(() => agents.value.find((item) => item.agent_id === selectedAgentId.value))
const activeRevision = computed(() => revisions.value.find((item) => item.revision_id === draft.revision_id))
const filteredAgents = computed(() => { const term = search.value.toLowerCase(); return agents.value.filter((item) => !term || `${item.name} ${item.agent_id} ${item.description}`.toLowerCase().includes(term)) })
const skillCapabilities = computed(() => skills.value.filter((item) => item.status === 'active' && item.current))
const executableCapabilities = computed(() => capabilities.value)
const safeCatalogCapabilities = computed(() => executableCapabilities.value.filter((item) => !item.requires_explicit_grant))
const protectedCapabilities = computed(() => executableCapabilities.value.filter((item) => item.requires_explicit_grant))
const selectedProtectedCapabilities = computed(() => protectedCapabilities.value.filter((item) => draft.allowed_capabilities.includes(item.ref.capability_id)))
const effectiveCapabilities = computed(() => draft.capability_mode === 'catalog'
  ? [...safeCatalogCapabilities.value, ...selectedProtectedCapabilities.value]
  : executableCapabilities.value.filter((item) => draft.allowed_capabilities.includes(item.ref.capability_id)))
const effectiveCapabilityCount = computed(() => effectiveCapabilities.value.length)
const filteredPolicyCapabilities = computed(() => {
  const term = capabilitySearch.value.toLowerCase()
  const source = draft.capability_mode === 'catalog' ? protectedCapabilities.value : executableCapabilities.value
  return source.filter((item) => !term || `${item.name} ${item.ref.capability_id} ${item.description}`.toLowerCase().includes(term))
})
const selectedBlockedCapabilities = computed(() => effectiveCapabilities.value.filter((item) => !item.execution_ready))
const requiredPermissionIds = computed(() => [...new Set(effectiveCapabilities.value.flatMap((item) => item.permissions || []))].sort())
function permissionGranted(permission: string) {
  return draft.granted_permissions.some((grant) => grant === '*' || grant === permission || (grant.endsWith('.*') && permission.startsWith(grant.slice(0, -1))))
}
const missingCapabilityPermissions = computed(() => requiredPermissionIds.value.filter((permission) => !permissionGranted(permission)))
const permissionEntries = computed(() => [...new Set([...requiredPermissionIds.value, ...draft.granted_permissions])].sort().map((permission) => ({
  permission,
  required: requiredPermissionIds.value.includes(permission),
  capabilities: effectiveCapabilities.value.filter((item) => (item.permissions || []).includes(permission)).map((item) => item.name),
})))
const releaseBlocked = computed(() => selectedBlockedCapabilities.value.length > 0 || missingCapabilityPermissions.value.length > 0)
const catalogGroups = computed(() => {
  const values = new Map<string, number>()
  for (const item of safeCatalogCapabilities.value) {
    const name = kindLabel(item.ref.kind)
    values.set(name, (values.get(name) || 0) + 1)
  }
  return [...values].map(([name, count]) => ({ name, count })).sort((left, right) => right.count - left.count)
})
const draftSaved = computed(() => Boolean(activeRevision.value?.status === 'draft' && savedFingerprint.value === fingerprint()))
const pendingMemoryCandidates = computed(() => memoryCandidates.value.filter((item) => item.status === 'pending' || item.status === 'conflicted').length)
const canSave = computed(() => Boolean(draft.agent_id && draft.name && draft.revision_id && draft.primary_model))
const editorTabs = computed(() => [
  { id: 'profile' as const, label: '身份指令' }, { id: 'model' as const, label: '模型策略' },
  { id: 'abilities' as const, label: '工具与能力', count: effectiveCapabilityCount.value },
  { id: 'planning' as const, label: '规划策略' },
  { id: 'monitor' as const, label: 'Monitor' },
  { id: 'memory' as const, label: '记忆策略' },
  { id: 'memoryData' as const, label: '记忆数据', count: memorySummary.value.total + pendingMemoryCandidates.value },
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
  if (selectedBlockedCapabilities.value.length) {
    checks.push(`存在未就绪能力：${selectedBlockedCapabilities.value.map((item) => item.ref.capability_id).join(', ')}`)
    ok = false
  } else checks.push('当前有效能力均已启用并由 Worker 加载')
  if (missingCapabilityPermissions.value.length) {
    checks.push(`缺少执行权限：${missingCapabilityPermissions.value.join(', ')}`)
    ok = false
  } else checks.push('能力声明的执行权限均已显式授予')
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
  const monitor = revision?.monitor_policy || {}
  const persona = revision?.persona || {}
  Object.assign(policyBase, {
    persona: { ...persona }, model: { ...model }, planning: { ...planning },
    capability: { ...ability }, memory: { ...memory }, monitor: { ...monitor }, output: { ...(revision?.output_policy || {}) },
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
    granted_permissions: Array.isArray(ability.permissions) ? ability.permissions.map(String) : [],
    allow_subagents: boolValue(planning.allow_subagents, true), max_steps: numberValue(planning.max_steps, 32),
    max_replans: numberValue(planning.max_replans, 2),
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

async function loadCatalog() {
  loading.value = true; error.value = ''
  try {
    const [agentItems, capabilityItems, skillItems, modelItems] = await Promise.all([getAdminAgents(), getAdminCapabilities(), listSkills(), listActiveModels().catch(() => [])])
    agents.value = agentItems; capabilities.value = capabilityItems; skills.value = skillItems; activeModels.value = modelItems
    if (!selectedAgentId.value && agentItems.length) await selectAgent(agentItems[0].agent_id)
    else if (selectedAgentId.value) await selectAgent(selectedAgentId.value)
  } catch (value) { error.value = errorText(value) } finally { loading.value = false }
}

async function selectAgent(agentId: string) {
  selectedAgentId.value = agentId; editorTab.value = 'profile'; skillBindings.value = []; skillBindingsSourceRevision.value = ''; resetMemoryData()
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
  Object.assign(draft, blankDraft()); Object.assign(policyBase, { persona: {}, model: {}, planning: {}, capability: {}, memory: {}, monitor: {}, output: {} }); resetMemoryData(); editorTab.value = 'profile'
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
    planning_policy: { ...policyBase.planning, allow_subagents: draft.allow_subagents, max_steps: draft.max_steps, max_fan_out: draft.max_fan_out, max_replans: Math.max(0, Math.min(10, Number(draft.max_replans) || 0)) },
    capability_policy: { ...policyBase.capability, mode: draft.capability_mode, allowed: draft.allowed_capabilities, permissions: draft.granted_permissions },
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
    monitor_policy: {
      ...policyBase.monitor,
      enabled: draft.monitor_enabled,
      schedule: { kind: 'every', every_ms: Math.max(60000, Math.round(Number(draft.monitor_every_minutes) * 60000)) },
      message: draft.monitor_message,
      context_mode: draft.monitor_context_mode,
      preflight_mode: draft.monitor_preflight_mode,
      session_mode: draft.monitor_session_mode,
      delivery: draft.monitor_delivery,
      active_hours: draft.monitor_active_hours_enabled ? {
        start: draft.monitor_active_hours_start,
        end: draft.monitor_active_hours_end,
        timezone: draft.monitor_active_hours_timezone,
      } : null,
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
  const skill = skillCapabilities.value.find((item) => item.skill_id === skillDraft.skill_id)
  if (!skill || !draftSaved.value) return
  try {
    await bindAgentSkill(draft.agent_id, draft.revision_id, {
      skill_id: skill.skill_id, skill_version: String(skill.current?.version || ''),
      activation_mode: skillDraft.activation_mode, priority: skillDraft.priority, configuration: {},
    })
    skillBindings.value = await getAgentSkillBindings(draft.agent_id, draft.revision_id)
    skillDraft.skill_id = ''; message.success('Skill 已绑定到当前 Draft')
  } catch (value) { message.error(errorText(value)) }
}

async function removeSkillBinding(binding: AgentSkillBinding) {
  if (!draftSaved.value) return
  try {
    await unbindAgentSkill(draft.agent_id, draft.revision_id, binding.skill_id, binding.skill_version)
    skillBindings.value = await getAgentSkillBindings(draft.agent_id, draft.revision_id)
    message.success('Skill 绑定已移除')
  } catch (value) { message.error(errorText(value)) }
}

async function publish(revision: AgentRevision) {
  try {
    await publishAgentRevision(draft.agent_id, revision.revision_id, releasePolicy)
    message.success('Agent 版本已发布，Worker rollout 已启动')
    await loadCatalog(); editorTab.value = 'revisions'
  } catch (value) { message.error(errorText(value)) }
}

function resetMemoryData() {
  memoryDocuments.value = []
  memoryCandidates.value = []
  memoryDocumentDetail.value = null
  selectedMemoryDocument.value = null
  memorySummary.value = { total: 0, by_layer: { profile: 0, long_term: 0, episodic: 0, agent: 0 } }
  memoryError.value = ''
}

async function loadMemoryDocuments() {
  if (!selectedAgentId.value) return
  memoryLoading.value = true; memoryError.value = ''
  try {
    const result = await getMemoryDocuments(selectedAgentId.value, { layer: memoryLayer.value, search: memorySearch.value })
    memoryDocuments.value = result.items; memorySummary.value = result.summary
    const previous = selectedMemoryDocument.value
    const next = result.items.find((item) => item.scope_key === previous?.scope_key && item.document_path === previous?.document_path) || result.items[0]
    if (next) await selectMemoryDocument(next)
    else { selectedMemoryDocument.value = null; memoryDocumentDetail.value = null }
  } catch (value) { memoryError.value = errorText(value) } finally { memoryLoading.value = false }
}

async function loadMemoryCandidates() {
  if (!selectedAgentId.value) return
  memoryLoading.value = true; memoryError.value = ''
  try { memoryCandidates.value = (await getMemoryCandidates(selectedAgentId.value, memoryCandidateStatus.value)).items }
  catch (value) { memoryError.value = errorText(value) } finally { memoryLoading.value = false }
}

async function loadMemoryData() {
  if (!selectedAgentId.value) return
  await Promise.all([loadMemoryDocuments(), loadMemoryCandidates()])
}

async function selectMemoryDocument(item: MemoryDocumentListItem) {
  if (!selectedAgentId.value) return
  selectedMemoryDocument.value = item
  try { memoryDocumentDetail.value = await getMemoryDocument(selectedAgentId.value, item) }
  catch (value) { memoryDocumentDetail.value = null; memoryError.value = errorText(value) }
}

function chooseMemoryLayer(layer: MemoryLayer | 'all') { memoryLayer.value = layer; void loadMemoryDocuments() }

function errorText(value: unknown) { return value instanceof Error ? value.message : '操作失败' }
function roleLabel(value: AgentRole) { return ({ coordinator: '协调器', executor: '执行器', specialist: '专家' } as const)[value] }
function statusLabel(value: DefinitionStatus) { return ({ active: '启用', disabled: '停用', archived: '归档' } as const)[value] }
function memoryLayerLabel(value: MemoryLayer) { return ({ profile: '个人属性', long_term: '长期记忆', episodic: '情景记忆', agent: 'Agent 经验' } as const)[value] }
function memoryCandidateLabel(value: MemoryCandidate['status']) { return ({ pending: '待确认', conflicted: '有冲突', merged: '已合并', rejected: '已拒绝', expired: '已过期' } as const)[value] }
function memoryCandidateClass(value: MemoryCandidate['status']) { return value === 'merged' ? 'completed' : value === 'pending' ? 'queued' : 'cancelled' }
function revisionClass(value: AgentRevision['status']) { return value === 'published' ? 'completed' : value === 'retired' ? 'cancelled' : 'queued' }
function kindIcon(value: string) { return ({ tool: 'T', connector: 'C', workflow: 'W', agent: 'A' } as Record<string, string>)[value] || '◇' }
function kindLabel(value: string) { return ({ tool: '工具', connector: '连接器', workflow: '工作流', agent: '子 Agent' } as Record<string, string>)[value] || value }
function activationLabel(value: AgentSkillBinding['activation_mode']) { return ({ always: '始终启用', coordinator_selected: '协调器选择', scenario_required: '场景要求' } as const)[value] }
function skillName(id: string) { return skillCapabilities.value.find((item) => item.skill_id === id)?.name || id }
function shortDigest(value: string) { return value ? `${value.slice(0, 14)}…${value.slice(-6)}` : 'no-digest' }
function formatDate(value?: string | null) { return value ? new Date(value).toLocaleString('zh-CN') : '—' }
function formatTimestamp(value?: number | null) { return value ? new Date(value).toLocaleString('zh-CN') : '—' }
function formatBytes(value: number) { return value < 1024 ? `${value} B` : value < 1024 * 1024 ? `${(value / 1024).toFixed(1)} KB` : `${(value / 1024 / 1024).toFixed(1)} MB` }

onMounted(loadCatalog)
watch(() => draft.agent_id, (agentId, previous) => {
  if (selectedAgentId.value) return
  if (!draft.revision_id || draft.revision_id === `${previous}:v1`) draft.revision_id = agentId ? `${agentId}:v1` : ''
})
watch(editorTab, (tab) => { if (tab === 'memoryData') void loadMemoryData() })
</script>

<style scoped>
.agent-workspace { display: grid; grid-template-columns: 288px minmax(0, 1fr); gap: 16px; align-items: start; }
.test-success { color: var(--success); background: rgba(50,182,122,.08); border: 1px solid rgba(50,182,122,.22); }.test-check { display: block; margin-top: 3px; color: var(--text-muted); font-size: 11px; }
.agent-directory { position: sticky; top: calc(var(--topbar-height) + 18px); max-height: calc(100vh - var(--topbar-height) - 36px); overflow: auto; }
.directory-heading { display: grid; gap: 14px; padding: 20px 16px 15px; }.directory-heading>div { display: flex; align-items: center; justify-content: space-between; }.directory-heading strong { color: var(--text-strong); font-size: 12px; }.directory-heading input,.ability-toolbar input { width: 100%; padding: 9px 10px; color: var(--text); background: var(--input); border: 1px solid var(--border); border-radius: 9px; outline: none; }
.agent-row { display: grid; width: 100%; grid-template-columns: 35px minmax(0,1fr) auto; gap: 10px; align-items: center; padding: 12px 15px; color: var(--text); background: transparent; border: 0; border-top: 1px solid var(--border); text-align: left; cursor: pointer; }.agent-row:hover,.agent-row.active { background: var(--surface-hover); }.agent-row.active { box-shadow: inset 3px 0 var(--accent); }.agent-avatar { display: grid; width: 35px; height: 35px; place-items: center; color: var(--accent); background: var(--accent-subtle); border: 1px solid var(--accent-border); border-radius: 10px; font-weight: 700; }.agent-row-copy { min-width: 0; display: grid; gap: 2px; }.agent-row-copy strong,.agent-row-copy small { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }.agent-row-copy strong { color: var(--text-strong); font-size: 12px; }.agent-row-copy small { color: var(--text-muted); font: 9px var(--font-mono); }.agent-row-state { display: flex; align-items: center; gap: 5px; color: var(--text-muted); font-size: 9px; }.agent-row-state i { width: 6px; height: 6px; border-radius: 50%; background: var(--success); }.agent-row-state i.disabled { background: var(--warning); }.agent-row-state i.archived { background: var(--text-muted); }
.agent-editor { min-width: 0; overflow: hidden; }.editor-header { display: flex; min-height: 92px; align-items: center; justify-content: space-between; gap: 20px; padding: 18px 22px; }.editor-header h2 { margin: 5px 0 2px; color: var(--text-strong); font-size: 20px; }.editor-header p { margin: 0; color: var(--text-muted); font-size: 10px; }.editor-actions { display: flex; align-items: center; gap: 8px; }.editor-title-line { display: flex; align-items: center; gap: 9px; }.role-chip { padding: 3px 8px; color: var(--accent); background: var(--accent-subtle); border: 1px solid var(--accent-border); border-radius: 99px; font-size: 9px; font-weight: 600; }
.editor-tabs { display: flex; overflow-x: auto; padding: 0 18px; border-top: 1px solid var(--border); border-bottom: 1px solid var(--border); }.editor-tabs button { display: flex; align-items: center; gap: 6px; padding: 13px 12px 11px; color: var(--text-muted); background: transparent; border: 0; border-bottom: 2px solid transparent; white-space: nowrap; cursor: pointer; }.editor-tabs button.active { color: var(--text-strong); border-bottom-color: var(--accent); }.editor-tabs small { min-width: 17px; padding: 1px 4px; color: var(--accent); background: var(--accent-subtle); border-radius: 99px; font: 9px var(--font-mono); }
.agent-form,.skills-pane,.revision-pane { padding: 22px; }.form-section { border: 1px solid var(--border); border-radius: 13px; overflow: hidden; }.form-section>header { display: flex; align-items: flex-start; justify-content: space-between; gap: 20px; padding: 16px 18px; background: var(--surface-raised); border-bottom: 1px solid var(--border); }.form-section>header>div { display: flex; align-items: center; gap: 9px; }.form-section>header span { color: var(--accent); font: 600 9px var(--font-mono); }.form-section h3 { margin: 0; color: var(--text-strong); font-size: 14px; }.form-section>header p { margin: 0; color: var(--text-muted); font-size: 10px; text-align: right; }
.form-grid { display: grid; grid-template-columns: repeat(2,minmax(0,1fr)); gap: 15px; padding: 18px; }.form-grid label,.skill-bind-form label,.ability-toolbar label { display: grid; gap: 6px; color: var(--text-muted); font-size: 10px; }.form-grid label.wide { grid-column: 1/-1; }.form-grid input,.form-grid select,.form-grid textarea,.skill-bind-form input,.skill-bind-form select,.ability-toolbar select { width: 100%; padding: 9px 10px; color: var(--text); background: var(--input); border: 1px solid var(--border-strong); border-radius: 9px; outline: none; }.form-grid textarea { resize: vertical; line-height: 1.65; }.form-grid input:focus,.form-grid select:focus,.form-grid textarea:focus { border-color: var(--accent-border); box-shadow: 0 0 0 3px var(--accent-subtle); }.switch-label { display: flex !important; flex-direction: row !important; align-items: center; gap: 10px !important; min-height: 54px; padding: 9px 11px; background: var(--surface-raised); border: 1px solid var(--border); border-radius: 10px; }.switch-label input { width: auto; }.switch-label span { display: grid; gap: 2px; }.switch-label strong { color: var(--text); font-size: 11px; }.switch-label small { color: var(--text-muted); }
.abilities-pane { padding: 22px; }.catalog-summary { display:grid; grid-template-columns:minmax(0,1fr) auto; gap:14px; padding:18px 20px;border-bottom:1px solid var(--border) }.catalog-summary strong{color:var(--text-strong);font-size:14px}.catalog-summary p{margin:6px 0 0;color:var(--text-muted);font-size:11px;line-height:1.6}.catalog-groups{grid-column:1/-1;display:flex;gap:8px;flex-wrap:wrap}.catalog-groups span{display:flex;gap:6px;align-items:center;padding:7px 10px;color:var(--text-muted);background:var(--surface-raised);border:1px solid var(--border);border-radius:8px;font-size:10px}.catalog-groups b{color:var(--accent);font:600 11px var(--font-mono)}.ability-toolbar { display: grid; grid-template-columns: minmax(220px,.7fr) 1fr; gap: 12px; align-items: end; padding: 15px 18px; border-bottom: 1px solid var(--border); }.explicit-grant-heading{display:flex;align-items:center;justify-content:space-between;gap:14px;padding:14px 18px 0}.explicit-grant-heading>div{display:grid;gap:3px}.explicit-grant-heading strong{color:var(--text-strong);font-size:11px}.explicit-grant-heading small{color:var(--text-muted);font-size:9px}.explicit-grant-heading>span{padding:4px 7px;color:var(--accent);background:var(--accent-subtle);border-radius:6px;font-size:9px}.capability-grid { display: grid; grid-template-columns: repeat(3,minmax(0,1fr)); gap: 9px; padding: 15px 18px 18px; }.capability-card { display: grid; grid-template-columns: auto 32px minmax(0,1fr); gap: 9px; align-items: start; padding: 11px; background: var(--surface-raised); border: 1px solid var(--border); border-radius: 10px; cursor: pointer; }.capability-card.selected { background: var(--accent-subtle); border-color: var(--accent-border); }.capability-card.blocked{border-color:color-mix(in srgb,var(--warning) 35%,var(--border))}.capability-card-copy{min-width:0;display:grid;gap:4px}.capability-name{display:flex;align-items:center;justify-content:space-between;gap:6px}.capability-card strong,.capability-card small { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }.capability-card strong { color: var(--text-strong); font-size: 11px; }.capability-card small { color: var(--text-muted); font: 8px var(--font-mono); }.capability-card em { color: var(--accent); font-size: 8px; font-style: normal; }.capability-icon { display: grid; width: 30px; height: 30px; place-items: center; color: var(--accent); background: var(--accent-subtle); border-radius: 8px; font-weight: 700; }.capability-states{display:flex;gap:5px;flex-wrap:wrap}.capability-states b{padding:2px 5px;border-radius:4px;font-size:8px;font-weight:500}.capability-states .ready{color:var(--success);background:color-mix(in srgb,var(--success) 12%,transparent)}.capability-states .blocked{color:var(--warning);background:color-mix(in srgb,var(--warning) 12%,transparent)}.capability-states .explicit{color:var(--accent);background:var(--accent-subtle)}.capability-blockers{color:var(--warning);font-size:8px;line-height:1.45}.ability-warning{display:grid;gap:4px;margin:0 18px 18px;padding:11px 13px;color:var(--warning);background:color-mix(in srgb,var(--warning) 8%,transparent);border:1px solid color-mix(in srgb,var(--warning) 25%,var(--border));border-radius:9px}.ability-warning strong{font-size:10px}.ability-warning span{font-size:9px;line-height:1.5}.permission-grants{margin:0 18px 18px;overflow:hidden;border:1px solid var(--border);border-radius:10px}.permission-grants>header{display:flex;align-items:center;justify-content:space-between;gap:12px;padding:11px 13px;background:var(--surface-raised);border-bottom:1px solid var(--border)}.permission-grants>header>div{display:grid;gap:3px}.permission-grants>header strong{color:var(--text-strong);font-size:10px}.permission-grants>header small{color:var(--text-muted);font-size:9px}.permission-grants>header>span{padding:3px 6px;border-radius:5px;font-size:8px}.permission-grants .complete{color:var(--success);background:color-mix(in srgb,var(--success) 12%,transparent)}.permission-grants .missing{color:var(--warning);background:color-mix(in srgb,var(--warning) 12%,transparent)}.permission-grants>div{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:0 14px;padding:5px 13px}.permission-grants label{display:flex;align-items:flex-start;gap:8px;padding:9px 0;border-bottom:1px solid var(--border)}.permission-grants label.unused{opacity:.65}.permission-grants label>span{display:grid;gap:2px}.permission-grants code{color:var(--text-strong);font-size:9px}.permission-grants label small{color:var(--text-muted);font-size:8px;line-height:1.45}
.skill-bind-form { display: grid; grid-template-columns: 1.3fr 1fr 110px auto; gap: 12px; align-items: end; padding: 18px; border-bottom: 1px solid var(--border); }.binding-list article { display: grid; grid-template-columns: 32px minmax(0,1fr) auto auto auto; gap: 11px; align-items: center; padding: 13px 18px; border-bottom: 1px solid var(--border); }.binding-list article:last-child { border-bottom: 0; }.binding-list article>div { min-width: 0; display: grid; gap: 2px; }.binding-list strong { color: var(--text-strong); font-size: 11px; }.binding-list small,.binding-list article>span:nth-last-child(3),.binding-list code { color: var(--text-muted); font-size: 9px; }.text-button.danger{color:var(--danger)}
.revision-list { padding: 4px 18px 18px; }.revision-list article { position: relative; display: grid; grid-template-columns: 12px minmax(0,1fr) auto auto; gap: 12px; align-items: center; min-height: 66px; padding: 11px 0; border-bottom: 1px solid var(--border); }.revision-list article.current { background: linear-gradient(90deg,var(--accent-subtle),transparent); }.revision-node { width: 9px; height: 9px; border: 2px solid var(--accent); border-radius: 50%; }.revision-list article>div { min-width: 0; display: grid; gap: 3px; }.revision-list strong { color: var(--text-strong); font-size: 11px; }.revision-list small { color: var(--text-muted); font: 9px var(--font-mono); }.current-label { color: var(--accent); font: 600 8px var(--font-mono); }
.release-policy-row { display:grid;grid-template-columns:minmax(220px,1fr) 150px auto auto;gap:12px;align-items:end;padding:15px 18px;border-bottom:1px solid var(--border) }.release-policy-row label { display:grid;gap:6px;color:var(--text-muted);font-size:10px }.release-policy-row input,.release-policy-row select { width:100%;padding:9px 10px;color:var(--text);background:var(--input);border:1px solid var(--border-strong);border-radius:9px }.release-policy-row .release-check { display:flex;align-items:center;gap:7px;padding-bottom:10px;white-space:nowrap }.release-policy-row .release-check input { width:auto }
.role-guide { display: grid; grid-template-columns: repeat(3,minmax(0,1fr)); gap: 8px; margin-top: -2px; }
.role-card { display: grid; gap: 7px; min-height: 82px; padding: 11px; border: 1px solid var(--border); border-radius: 9px; background: var(--surface); color: var(--text-muted); text-align: left; cursor: pointer; }
.role-card:hover,.role-card.selected { border-color: var(--accent); background: var(--accent-subtle); color: var(--text-strong); }
.role-card-top { display: flex; align-items: baseline; justify-content: space-between; gap: 8px; color: var(--text-strong); }.role-card-top small { color: var(--text-muted); font-size: 10px; }.role-card>span:last-child { font-size: 12px; line-height: 1.45; }
.role-detail { grid-column: 1/-1; padding: 11px 13px; border-left: 3px solid var(--accent); border-radius: 5px; background: var(--surface-raised); }.role-detail p { margin: 5px 0; color: var(--text-muted); font-size: 12px; line-height: 1.55; }.role-detail small { color: var(--text-muted); font-size: 11px; }
.memory-guide { display: grid; gap: 5px; padding: 11px 13px; color: var(--text-muted); background: var(--surface-raised); border-left: 3px solid var(--accent); border-radius: 5px; font-size: 11px; line-height: 1.55; }.memory-guide strong { color: var(--text-strong); font-size: 11px; }
.memory-data-pane { padding: 22px; }.memory-summary-grid { display: grid; grid-template-columns: repeat(6,minmax(0,1fr)); gap: 8px; padding: 16px 18px; border-bottom: 1px solid var(--border); }.memory-summary-grid button,.memory-summary-grid>div { display: grid; gap: 3px; padding: 11px 12px; color: var(--text-muted); background: var(--surface-raised); border: 1px solid var(--border); border-radius: 9px; text-align: left; }.memory-summary-grid button { cursor: pointer; }.memory-summary-grid button:hover,.memory-summary-grid button.active { background: var(--accent-subtle); border-color: var(--accent-border); }.memory-summary-grid strong { color: var(--text-strong); font: 600 18px var(--font-mono); }.memory-summary-grid span { font-size: 10px; }.memory-data-toolbar { display: flex; align-items: center; gap: 8px; padding: 12px 18px; border-bottom: 1px solid var(--border); }.memory-data-toolbar input { min-width: 180px; flex: 1; }.memory-data-toolbar input,.memory-data-toolbar select { padding: 9px 10px; color: var(--text); background: var(--input); border: 1px solid var(--border-strong); border-radius: 9px; }.segmented-control { display: flex; padding: 3px; background: var(--surface-raised); border: 1px solid var(--border); border-radius: 9px; }.segmented-control button { padding: 7px 10px; color: var(--text-muted); background: transparent; border: 0; border-radius: 6px; cursor: pointer; white-space: nowrap; }.segmented-control button.active { color: var(--text-strong); background: var(--surface); box-shadow: var(--shadow-sm); }.memory-error { margin: 12px 18px 0; }
.memory-browser { display: grid; grid-template-columns: minmax(250px,.8fr) minmax(0,1.3fr); min-height: 440px; }.memory-document-list { max-height: 580px; overflow: auto; border-right: 1px solid var(--border); }.memory-document-list>button { display: grid; width: 100%; gap: 5px; padding: 13px 16px; color: var(--text-muted); background: transparent; border: 0; border-bottom: 1px solid var(--border); text-align: left; cursor: pointer; }.memory-document-list>button:hover,.memory-document-list>button.active { background: var(--surface-hover); }.memory-document-list>button.active { box-shadow: inset 3px 0 var(--accent); }.memory-document-list strong { color: var(--text-strong); font: 600 11px var(--font-mono); }.memory-document-list p { display: -webkit-box; overflow: hidden; margin: 0; line-height: 1.5; -webkit-box-orient: vertical; -webkit-line-clamp: 2; font-size: 10px; white-space: pre-wrap; }.memory-document-list small { font-size: 8px; }.memory-layer-tag { width: max-content; padding: 2px 6px; color: var(--accent); background: var(--accent-subtle); border-radius: 99px; font-size: 8px; }
.memory-document-detail { min-width: 0; background: var(--surface-raised); }.memory-document-detail>header { display: flex; align-items: center; justify-content: space-between; gap: 12px; padding: 14px 16px; border-bottom: 1px solid var(--border); }.memory-document-detail>header>div { min-width: 0; display: flex; align-items: center; gap: 8px; }.memory-document-detail h4 { overflow: hidden; margin: 0; color: var(--text-strong); font: 600 12px var(--font-mono); text-overflow: ellipsis; white-space: nowrap; }.memory-document-detail>header small { color: var(--text-muted); font-size: 9px; white-space: nowrap; }.memory-document-detail pre,.memory-candidate-list pre { overflow: auto; margin: 0; color: var(--text); font: 11px/1.65 var(--font-mono); white-space: pre-wrap; overflow-wrap: anywhere; }.memory-document-detail pre { min-height: 330px; max-height: 500px; padding: 18px; }.memory-document-detail>footer { display: flex; justify-content: space-between; gap: 12px; padding: 10px 16px; color: var(--text-muted); border-top: 1px solid var(--border); font-size: 8px; }.memory-document-detail>footer code { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.memory-candidate-list { display: grid; gap: 10px; padding: 16px 18px 18px; }.memory-candidate-list article { overflow: hidden; border: 1px solid var(--border); border-radius: 10px; }.memory-candidate-list article>header,.memory-candidate-list article>footer { display: flex; align-items: center; justify-content: space-between; gap: 10px; padding: 10px 12px; background: var(--surface-raised); }.memory-candidate-list article>header>div { display: flex; align-items: center; gap: 8px; }.memory-candidate-list article>header strong { color: var(--text-strong); font: 600 10px var(--font-mono); }.memory-candidate-list pre { max-height: 240px; padding: 13px; border-top: 1px solid var(--border); border-bottom: 1px solid var(--border); }.memory-candidate-list article>footer { color: var(--text-muted); font-size: 8px; }.memory-candidate-list article>footer code { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
@media (max-width: 1120px) { .agent-workspace { grid-template-columns: 1fr; }.agent-directory { position: static; max-height: 280px; }.capability-grid { grid-template-columns: repeat(2,minmax(0,1fr)); }.memory-browser { grid-template-columns: minmax(230px,.75fr) minmax(0,1.25fr); } }
@media (max-width: 900px) { .agent-workspace { grid-template-columns: 1fr; }.agent-directory { position: static; max-height: 300px; }.capability-grid { grid-template-columns: repeat(2,minmax(0,1fr)); }.skill-bind-form { grid-template-columns: 1fr 1fr; }.memory-summary-grid { grid-template-columns: repeat(3,minmax(0,1fr)); }.memory-browser { grid-template-columns: 1fr; }.memory-document-list { max-height: 300px; border-right: 0; border-bottom: 1px solid var(--border); } }
@media (max-width: 650px) { .editor-header { align-items: flex-start; flex-direction: column; }.editor-actions { width: 100%; flex-wrap: wrap; }.form-grid,.capability-grid,.ability-toolbar,.skill-bind-form,.role-guide,.memory-summary-grid,.permission-grants>div { grid-template-columns: 1fr; }.abilities-pane,.agent-form,.skills-pane,.revision-pane,.memory-data-pane { padding: 14px; }.form-section>header { flex-direction: column; }.form-section>header p { text-align: left; }.revision-list article { grid-template-columns: 12px minmax(0,1fr) auto; }.revision-list article button,.current-label { grid-column: 2/-1; justify-self: start; }.memory-data-toolbar { align-items: stretch; flex-direction: column; }.memory-candidate-list article>footer { align-items: flex-start; flex-direction: column; } }
</style>
