<script setup>
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { ownerIdentity, prettyContent, request } from './api'

const ACTIVE_RUN_KEY = 'joyhousebot.starter.active_run_id'
const RUN_GOALS_KEY = 'joyhousebot.starter.run_goals'
const providerPresets = {
  deepseek: { providerId: 'deepseek', apiBase: 'https://api.deepseek.com/v1', modelName: 'deepseek-v4-flash', apiKeyVariable: 'DEEPSEEK_API_KEY' },
  openai: { providerId: 'openai', apiBase: 'https://api.openai.com/v1', modelName: 'gpt-4.1-mini', apiKeyVariable: 'OPENAI_API_KEY' },
}

const tab = ref('workspace')
const entrypoints = ref([])
const selectedEntrypointId = ref('')
const goal = ref('')
const activeRun = ref(null)
const artifacts = ref([])
const inputs = ref([])
const approvals = ref([])
const busy = ref(false)
const loadingRun = ref(false)
const error = ref('')
const setupStatus = ref('尚未检查')
const setupLog = ref([])
const setup = ref({ preset: 'deepseek', ...providerPresets.deepseek })
const runGoals = ref(JSON.parse(sessionStorage.getItem(RUN_GOALS_KEY) || '{}'))
const consoleUrl = ref('http://127.0.0.1:18790/ui/')
let pollTimer

const identity = computed(ownerIdentity)
const runTerminal = computed(() => ['succeeded', 'failed', 'cancelled'].includes(activeRun.value?.status))
const finalArtifacts = computed(() => artifacts.value.filter((artifact) => artifact.name === 'final-output' || artifact.type === 'runtime.output'))
const otherArtifacts = computed(() => artifacts.value.filter((artifact) => !finalArtifacts.value.includes(artifact)))
const selectedEntrypoint = computed(() => entrypoints.value.find((item) => item.id === selectedEntrypointId.value))
const activeGoal = computed(() => activeRun.value ? runGoals.value[activeRun.value.id] || '' : '')
const attentionLabel = computed(() => {
  if (inputs.value.length) return '等待你的补充信息'
  if (approvals.value.length) return '等待你的授权决定'
  if (runTerminal.value) return finalArtifacts.value.length ? '成果已沉淀' : 'Run 已结束'
  return 'Worker 正在异步执行'
})
const demoGoals = [
  { title: '发布前审核', detail: '把“准备发布”变成有检查点的执行链', content: '为 joyhousebot 做一次发布前审核：列出应验证的 Runtime、Worker、权限、扩展发布和回滚检查项；给出按风险排序的执行计划，并标记需要我审批的动作。' },
  { title: '一周工作调度', detail: '把目标拆成能恢复的长程计划', content: '帮我规划本周工作：根据高影响、依赖关系和可委托性排序，生成每天的执行节奏、风险检查点和需要我确认的决策。' },
  { title: '个人知识整理', detail: '让结果成为可复用的个人资产', content: '设计一套个人知识整理流程：定义输入、分类、去重、复盘、最终成果和每周维护节奏。先说明需要哪些信息，再给出可执行的第一版。' },
]

function eventError(message) { error.value = message }
function log(message) { setupLog.value.push(`${new Date().toLocaleTimeString()} ${message}`) }
function formatTime(value) { return value ? new Date(value).toLocaleString() : '—' }
function shortId(value) { return value?.length > 18 ? `${value.slice(0, 10)}…${value.slice(-6)}` : value || '—' }
function artifactText(artifact) { return prettyContent(artifact.content) || '该成果不包含可展示的文本内容。' }
function saveRunGoals() { sessionStorage.setItem(RUN_GOALS_KEY, JSON.stringify(runGoals.value)) }
function chooseDemo(item) { goal.value = item.content; document.querySelector('.goal-input')?.focus() }

async function loadEntrypoints() {
  try {
    const payload = await request('/v2/entrypoints')
    entrypoints.value = payload.items || []
    selectedEntrypointId.value ||= entrypoints.value.find((item) => item.default)?.id || entrypoints.value[0]?.id || ''
  } catch (cause) { eventError(`无法读取执行入口：${cause.message}`) }
}

async function refreshRun() {
  if (!activeRun.value?.id || loadingRun.value) return
  loadingRun.value = true
  try {
    const runId = activeRun.value.id
    const [run, artifactPage, inputPage, approvalPage] = await Promise.all([
      request(`/v2/runs/${encodeURIComponent(runId)}`),
      request(`/v2/runs/${encodeURIComponent(runId)}/artifacts`),
      request(`/v2/runs/${encodeURIComponent(runId)}/inputs`),
      request(`/v2/runs/${encodeURIComponent(runId)}/approvals`),
    ])
    activeRun.value = run
    artifacts.value = artifactPage.items || []
    inputs.value = inputPage.items || []
    approvals.value = approvalPage.items || []
  } catch (cause) { eventError(`无法刷新 Run：${cause.message}`) }
  finally { loadingRun.value = false }
}

async function submitGoal() {
  const content = goal.value.trim()
  if (!content || !selectedEntrypointId.value || busy.value) return
  busy.value = true; error.value = ''
  try {
    const idempotencyKey = `starter-${crypto.randomUUID()}`
    activeRun.value = await request(`/v2/entrypoints/${encodeURIComponent(selectedEntrypointId.value)}/runs`, {
      method: 'POST', idempotencyKey,
      body: JSON.stringify({ input: { content }, idempotency_key: idempotencyKey, client_context: { surface: 'joyhousebot-starter' } }),
    })
    runGoals.value = { ...runGoals.value, [activeRun.value.id]: content }
    saveRunGoals()
    sessionStorage.setItem(ACTIVE_RUN_KEY, activeRun.value.id)
    goal.value = ''
    await refreshRun()
  } catch (cause) { eventError(`无法启动目标：${cause.message}`) }
  finally { busy.value = false }
}

async function cancelRun() {
  if (!activeRun.value?.id || !window.confirm('确定取消这个 Run 吗？')) return
  try { activeRun.value = await request(`/v2/runs/${encodeURIComponent(activeRun.value.id)}/cancel`, { method: 'POST' }); await refreshRun() }
  catch (cause) { eventError(cause.message) }
}

async function resolveInput(input, answer) {
  const value = answer?.trim()
  if (!value || !activeRun.value?.id) return
  try {
    await request(`/v2/runs/${encodeURIComponent(activeRun.value.id)}/inputs`, {
      method: 'POST', body: JSON.stringify({ input_request_id: input.id, answers: { [input.fields?.[0]?.name || 'answer']: value } }),
    })
    await refreshRun()
  } catch (cause) { eventError(cause.message) }
}

async function decideApproval(approval, decision) {
  try {
    await request(`/v2/approvals/${encodeURIComponent(approval.id)}/decisions`, {
      method: 'POST', body: JSON.stringify({ decision, note: 'Decided in JoyHouseBot Starter' }),
    })
    await refreshRun()
  } catch (cause) { eventError(cause.message) }
}

function applyPreset() { Object.assign(setup.value, providerPresets[setup.value.preset] || {}) }
function modelSpec(providerId, modelName) {
  const flash = providerId === 'deepseek' && modelName === 'deepseek-v4-flash'
  return { model_id: `${providerId}/${modelName}`, name: modelName, description: 'Default model configured by JoyHouseBot Starter', kind: 'llm', enabled: true, input_modalities: ['text'], context_window: flash ? 1_000_000 : 128000, max_output_tokens: flash ? 384000 : 4096, supports_tools: true, supports_reasoning: flash, supports_structured_output: true, default_temperature: 0.3, tags: ['starter'], dimensions: 0, input_cost_per_million_tokens: flash ? 0.14 : null, output_cost_per_million_tokens: flash ? 0.28 : null, cached_input_cost_per_million_tokens: flash ? 0.0028 : null, cache_creation_input_cost_per_million_tokens: null }
}

async function waitForProvider(providerId) {
  for (let attempt = 1; attempt <= 24; attempt += 1) {
    const provider = await request(`/control/v1/admin/model-providers/${encodeURIComponent(providerId)}`)
    if (provider.execution_ready) return provider
    log(`等待 Worker 加载模型（${attempt}/24）：${(provider.execution_blockers || []).join('；') || '尚未就绪'}`)
    await new Promise((resolve) => setTimeout(resolve, 2500))
  }
  throw new Error('模型 Provider 未在 60 秒内就绪。请确认 .env.local 已配置密钥，并重启本地 Runtime。')
}

function starterManifest(revisionId, version) {
  return { schema_version: 1, app_id: 'app.personal-starter', version: `0.1.${Math.max(0, version - 2)}`, name: 'Personal Starter', description: 'Default personal goal execution entry point.', publisher: 'joyhousebot', core: { min_version: '2.0.0' }, extensions: [], capabilities: [], assets: { agents: [{ agent_id: 'default', revision_id: revisionId }], teams: [], skills: [], workflows: [], scenarios: [] }, entrypoints: [{ entrypoint_id: 'goal', name: 'Start a goal', default: true, execution: { mode: 'agent', agent_id: 'default', revision_id: revisionId }, interaction_mode: 'background' }], connections: [], permissions: ['runs.submit'], secrets: [] }
}

async function applySetup() {
  const { providerId, apiBase, modelName, apiKeyVariable } = setup.value
  if (!/^[a-z0-9][a-z0-9_-]{0,63}$/.test(providerId) || !apiBase || !modelName || !/^[A-Za-z_][A-Za-z0-9_]*$/.test(apiKeyVariable)) { eventError('请填写合法的 Provider ID、API Base、模型名称和环境变量名。'); return }
  busy.value = true; error.value = ''; setupLog.value = []; setupStatus.value = '进行中'
  const model = modelSpec(providerId, modelName)
  const provider = { provider_id: providerId, name: providerId, description: 'Configured by local Starter', enabled: true, extension_id: 'provider-openai-compatible', api_base: apiBase, api_key_ref: `env://${apiKeyVariable}`, allow_insecure_http: false, credential_mode: 'api_key', extra_header_refs: {}, request_timeout_seconds: 120, models: [model] }
  try {
    log('保存模型 Provider 草稿')
    let saved
    try { saved = await request('/control/v1/admin/model-providers', { method: 'POST', body: JSON.stringify(provider) }) }
    catch (cause) { saved = await request(`/control/v1/admin/model-providers/${encodeURIComponent(providerId)}/revisions`, { method: 'POST', body: JSON.stringify(provider) }); log(`已创建新 Provider revision（${cause.message}）`) }
    log(`发布模型 Provider ${saved.revision_id}`)
    await request(`/control/v1/admin/model-providers/${encodeURIComponent(providerId)}/revisions/${encodeURIComponent(saved.revision_id)}/publish`, { method: 'POST', body: JSON.stringify({ activation_mode: 'automatic', timeout_seconds: 300, auto_rollback: true, require_healthy_workers: true }) })
    await waitForProvider(providerId); log('模型 Provider 已由 Worker 加载')
    const agents = await request('/control/v1/admin/agents')
    const existing = (agents.items || []).find((item) => item.agent_id === 'default')
    const version = Math.max(1, Number(existing?.revision?.version || existing?.current_revision?.version || 0) + 1)
    const revisionId = `default:v${version}`
    log(`保存默认 Agent ${revisionId}`)
    await request(`/control/v1/admin/agents/default/revisions/${revisionId}`, { method: 'PUT', body: JSON.stringify({ revision_id: revisionId, version, name: 'Default Agent', description: 'Personal general-purpose agent created by JoyHouseBot Starter.', role: 'executor', definition_status: 'active', persona: { tone: 'helpful', language: 'follow-user' }, instructions: 'Help the owner complete their goal. State assumptions, keep progress clear, and return verified artifacts or a concise result.', model_policy: { primary: model.model_id, fallbacks: [], temperature: 0.3, max_tokens: 4096, max_tool_iterations: 20, tool_execution: { mode: 'sequential', max_parallel_calls: 4 } }, planning_policy: { allow_subagents: true, max_steps: 32, max_fan_out: 4, max_replans: 2 }, capability_policy: { mode: 'catalog', allowed: [], permissions: [] }, memory_policy: { enabled: true, mode: 'personalized', scope: 'user_agent', read_mode: 'auto', write_mode: 'candidate', layers: { working: { read: true, write: false, persist: false }, session: { read: true, write: true, persist: false }, episodic: { read: true, write: true, persist: true }, profile: { read: true, write: true, persist: true }, long_term: { read: true, write: true, persist: true }, agent: { read: false, write: false, persist: false } }, retrieval: { top_k: 10, max_tokens: 6000 } }, output_policy: {}, monitor_policy: { enabled: false }, extension_requirements: [] }) })
    await request(`/control/v1/admin/agents/default/revisions/${revisionId}/publish`, { method: 'POST', body: JSON.stringify({ activation_mode: 'automatic', timeout_seconds: 300, auto_rollback: true, require_healthy_workers: true }) })
    log('默认 Agent 已发布')
    const manifest = starterManifest(revisionId, version)
    log('保存 Starter App Package')
    await request(`/control/v1/admin/apps/${manifest.app_id}/releases/${manifest.version}`, { method: 'PUT', body: JSON.stringify({ manifest }) })
    const validation = await request(`/control/v1/admin/apps/${manifest.app_id}/releases/${manifest.version}/validate`, { method: 'POST' })
    if (!validation.valid) throw new Error(`Starter App 校验失败：${(validation.errors || []).join('；')}`)
    await request(`/control/v1/admin/apps/${manifest.app_id}/releases/${manifest.version}/publish`, { method: 'POST' })
    log('安装并激活 Starter App')
    const installed = await request(`/v2/apps/${manifest.app_id}/install`, { method: 'POST', body: JSON.stringify({ version: manifest.version, configuration: {} }) })
    log(`初始化完成：安装 ${installed.installation_id}`)
    setupStatus.value = '完成'; await loadEntrypoints(); tab.value = 'workspace'
  } catch (cause) { setupStatus.value = '失败'; log(`失败：${cause.message}`) }
  finally { busy.value = false }
}

async function checkReadiness() {
  setupLog.value = []
  try { const health = await request('/readyz'); setupStatus.value = health.ok ? 'Runtime 就绪' : 'Runtime 未就绪'; log(`Runtime：${JSON.stringify(health)}`); await loadEntrypoints(); log(`可用 EntryPoint：${entrypoints.value.length}`) }
  catch (cause) { setupStatus.value = '无法连接'; log(`无法连接 Runtime：${cause.message}`) }
}

watch(activeRun, (run) => { if (!run?.id) sessionStorage.removeItem(ACTIVE_RUN_KEY) })
onMounted(async () => {
  try {
    const response = await fetch('/starter-config')
    if (response.ok) consoleUrl.value = (await response.json()).console_url || consoleUrl.value
  } catch { /* Vite-only development has no local helper; use the default runtime URL. */ }
  await loadEntrypoints()
  const runId = sessionStorage.getItem(ACTIVE_RUN_KEY)
  if (runId) { activeRun.value = { id: runId, status: 'running' }; await refreshRun() }
  pollTimer = window.setInterval(() => { if (activeRun.value && !runTerminal.value) void refreshRun() }, 2500)
})
onBeforeUnmount(() => window.clearInterval(pollTimer))
</script>

<template>
  <main class="shell">
    <header class="topbar">
      <a class="brand" href="#workspace"><span class="brand-mark">J</span><span>JoyHouseBot <b>Starter</b></span></a>
      <nav aria-label="主导航"><button :class="{ active: tab === 'workspace' }" @click="tab = 'workspace'">我的目标</button><button :class="{ active: tab === 'setup' }" @click="tab = 'setup'">本地初始化</button></nav>
      <a class="console-link" :href="consoleUrl" target="_blank" rel="noopener noreferrer">打开 Console ↗</a>
      <span class="connection" :class="identity.token ? 'secure' : 'local'">{{ identity.token ? 'Owner Token' : '本地开发身份' }}</span>
    </header>

    <template v-if="tab === 'workspace'">
      <section class="hero"><p class="eyebrow">OWNER WORKSPACE</p><h1>不是聊天记录，<br>是可交付的执行。</h1><p>把一个目标交给 Agent；它会进入可恢复的 Run，由 Worker 异步执行，在需要时向你要信息或请求批准，并把最终成果留作你的资产。</p></section>
      <p v-if="error" class="error" role="alert">{{ error }}</p>
      <section class="advantage-grid" aria-label="JoyHouseBot 的执行优势">
        <article class="advantage-card"><p class="eyebrow">01 · DURABLE</p><h2>目标不会消失在对话里</h2><p>每次提交都有 Run ID、状态和恢复路径。关掉页面、等待长任务或重启 Worker，执行仍以 PostgreSQL 为事实源。</p></article>
        <article class="advantage-card"><p class="eyebrow">02 · GOVERNED</p><h2>Agent 有边界，不是黑箱</h2><p>模型与工具只在 Worker 运行；外部动作可以停在你的输入和审批关口，权限、幂等与审计都留在同一执行链。</p></article>
        <article class="advantage-card"><p class="eyebrow">03 · OWNED</p><h2>结果有来源、可核验</h2><p>输出先成为带完整性哈希的 Artifact；它绑定所属 Run，不再是一段无法追溯的模型回复。</p></article>
      </section>
      <section class="demo-scenarios"><div><p class="eyebrow">TRY A LONG-RUN MISSION</p><h2>不知道从哪里开始？先选一个真实任务。</h2></div><div class="scenario-list"><button v-for="item in demoGoals" :key="item.title" class="scenario" @click="chooseDemo(item)"><strong>{{ item.title }}</strong><span>{{ item.detail }}</span></button></div></section>
      <section class="workspace">
        <article class="card composer-card">
          <div class="card-heading"><div><p class="eyebrow">NEW GOAL</p><h2>你想完成什么？</h2></div><button class="link-button" @click="loadEntrypoints">刷新入口</button></div>
          <textarea v-model="goal" class="goal-input" rows="7" autofocus placeholder="写下一个需要持续推进、有明确成果或需要你把关的目标。" @keydown.meta.enter.prevent="submitGoal" @keydown.ctrl.enter.prevent="submitGoal" />
          <div class="composer-footer"><label>执行入口 <select v-model="selectedEntrypointId"><option v-for="entrypoint in entrypoints" :key="entrypoint.id" :value="entrypoint.id">{{ entrypoint.name }} · {{ entrypoint.app_name || entrypoint.app_id }}</option><option v-if="!entrypoints.length" value="">请先完成本地初始化</option></select></label><button class="primary send-button" :disabled="busy || !goal.trim() || !selectedEntrypointId" @click="submitGoal">{{ busy ? '正在启动…' : '交给 Agent' }}</button></div>
          <p class="hint">⌘/Ctrl + Enter 快速发送。每次提交都会创建可恢复、可审计的 Run。</p>
        </article>

        <article class="card result-card">
          <div class="card-heading"><div><p class="eyebrow">CURRENT RUN</p><h2>{{ activeRun ? `Run ${shortId(activeRun.id)}` : '等待你的第一个目标' }}</h2></div><span v-if="activeRun" class="status" :class="activeRun.status">{{ activeRun.status }}</span></div>
          <template v-if="activeRun">
            <div class="progress"><span class="pulse" :class="{ done: runTerminal }"></span><span>{{ activeRun.progress?.summary || (runTerminal ? '执行已结束' : 'Agent 正在处理你的目标') }}</span></div>
            <section class="execution-proof">
              <div class="proof-heading"><div><p class="eyebrow">LIVE EXECUTION PROOF</p><h3>这条目标正在走什么链路？</h3></div><span>{{ attentionLabel }}</span></div>
              <ol class="proof-chain">
                <li class="done"><i>1</i><div><strong>你的目标</strong><p>{{ activeGoal || '此 Run 在当前浏览器会话之外创建；仍可查看其持久状态。' }}</p></div></li>
                <li class="done"><i>2</i><div><strong>持久 Run</strong><p><code>{{ shortId(activeRun.id) }}</code> · {{ activeRun.status }} · 可刷新、可恢复、可审计</p></div></li>
                <li :class="{ active: !runTerminal, done: runTerminal }"><i>3</i><div><strong>Worker 执行</strong><p>模型和能力调用不占用浏览器请求；长任务由 Runtime 调度。</p></div></li>
                <li :class="{ active: inputs.length || approvals.length, done: !inputs.length && !approvals.length && runTerminal }"><i>4</i><div><strong>你的关口</strong><p>{{ inputs.length ? `${inputs.length} 个待输入问题` : approvals.length ? `${approvals.length} 个待审批动作` : '没有待处理的输入或审批。' }}</p></div></li>
                <li :class="{ active: finalArtifacts.length, done: finalArtifacts.length }"><i>5</i><div><strong>可验证成果</strong><p>{{ finalArtifacts.length ? `${finalArtifacts.length} 个最终 Artifact 已生成` : '完成后，结果会作为 Artifact 留在这里。' }}</p></div></li>
              </ol>
            </section>
            <section v-if="finalArtifacts.length" class="conversation"><div v-for="artifact in finalArtifacts" :key="artifact.id" class="message assistant"><p class="message-label">AGENT 的结果</p><pre>{{ artifactText(artifact) }}</pre></div></section>
            <section v-else class="result-placeholder"><strong>{{ runTerminal ? 'Run 已结束，但没有文本成果。' : '正在等待 Agent 的结果…' }}</strong><p>页面会自动刷新；你也可以手动刷新状态。</p></section>
            <section v-if="inputs.length" class="resource-section"><h3>Agent 需要你补充</h3><form v-for="input in inputs" :key="input.id" class="input-request" @submit.prevent="resolveInput(input, $event.target.answer.value)"><p>{{ input.question }}</p><textarea name="answer" rows="3" placeholder="输入你的回答" /><button class="secondary">继续执行</button></form></section>
            <section v-if="approvals.length" class="resource-section"><h3>等待你的批准</h3><div v-for="approval in approvals" :key="approval.id" class="approval"><strong>{{ approval.summary }}</strong><p>风险：{{ approval.risk }}</p><button class="secondary" @click="decideApproval(approval, 'approve')">批准</button><button class="danger" @click="decideApproval(approval, 'reject')">拒绝</button></div></section>
            <details class="run-details"><summary>Run 详情与其他成果</summary><dl class="facts"><div><dt>Run ID</dt><dd>{{ activeRun.id }}</dd></div><div><dt>更新时间</dt><dd>{{ formatTime(activeRun.updated_at || activeRun.created_at) }}</dd></div><div><dt>阶段</dt><dd>{{ activeRun.progress?.phase || '—' }}</dd></div><div><dt>任务</dt><dd>{{ activeRun.progress?.completed || 0 }} / {{ activeRun.progress?.total || 0 }}</dd></div></dl><ul v-if="otherArtifacts.length" class="artifact-list"><li v-for="artifact in otherArtifacts" :key="artifact.id"><strong>{{ artifact.name }}</strong><pre>{{ artifactText(artifact) }}</pre></li></ul><p v-else class="empty">没有其他 Artifact。</p></details>
            <div class="run-actions"><button class="secondary" :disabled="loadingRun" @click="refreshRun">{{ loadingRun ? '刷新中…' : '刷新状态' }}</button><button class="danger" :disabled="runTerminal" @click="cancelRun">取消 Run</button></div>
          </template>
          <div v-else class="result-placeholder"><strong>输入一个目标后，结果会直接显示在这里。</strong><p>Starter 不是运维 Console；它只使用公开 EntryPoint 和 Run API。</p></div>
        </article>
      </section>
    </template>

    <template v-else>
      <section class="hero compact"><p class="eyebrow">LOCAL FORK ONBOARDING</p><h1>配置一个模型，发布默认 Agent。</h1><p>仅用于本机开发 fork。密钥保留在 <code>.env.local</code>，页面只保存 <code>env://</code> 引用。</p></section>
      <section class="setup-grid"><article class="card setup-card"><div class="card-heading"><div><p class="eyebrow">MODEL PROVIDER</p><h2>LLM 配置</h2></div><span class="status neutral">{{ setupStatus }}</span></div><label>快速预设<select v-model="setup.preset" @change="applyPreset"><option value="deepseek">DeepSeek V4 Flash（默认）</option><option value="openai">OpenAI</option></select></label><label>Provider ID<input v-model.trim="setup.providerId" pattern="[a-z0-9_-]+" /></label><label>API Base<input v-model.trim="setup.apiBase" /></label><label>模型名称<input v-model.trim="setup.modelName" /></label><label>环境变量名<input v-model.trim="setup.apiKeyVariable" pattern="[A-Za-z_][A-Za-z0-9_]*" /></label><aside class="notice"><strong>密钥不经过本页面。</strong><p>在根目录 <code>.env.local</code> 写入 <code>{{ setup.apiKeyVariable }}=…</code>，再启动 Runtime。</p></aside><button class="primary" :disabled="busy" @click="applySetup">{{ busy ? '正在初始化…' : '保存并发布默认配置' }}</button></article><article class="card setup-log"><div class="card-heading"><div><p class="eyebrow">SETUP LOG</p><h2>初始化记录</h2></div><button class="link-button" @click="checkReadiness">检查现状</button></div><pre>{{ setupLog.join('\n') || '尚未执行。' }}</pre></article></section>
    </template>
  </main>
</template>
