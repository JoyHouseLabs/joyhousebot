<template>
  <div class="page workflow-page">
    <header class="page-heading">
      <div>
        <span class="eyebrow">AI WORKFLOW STUDIO</span>
        <h1>让 Agent 生成可执行流程</h1>
        <p>用自然语言组合 Agent、Team、Scenario 与质量控制节点；审查、试运行并发布为可恢复的执行 DAG。</p>
      </div>
      <div class="heading-actions">
        <button class="secondary-button" type="button" :disabled="loading" @click="loadDirectory">刷新</button>
        <button class="primary-button" type="button" @click="startNew">＋ 新建 Workflow</button>
      </div>
    </header>

    <div class="workflow-contract">
      <span>自然语言目标</span><b>→</b><span>Agent / Team / Scenario</span><b>→</b><span>分支 · 验证 · 有界循环 · 审批</span><b>→</b><span>冻结版本</span><b>→</b><span>统一 Runtime 执行</span>
    </div>
    <div v-if="error" class="notice error-notice">{{ error }}</div>
    <div v-if="notice" class="notice success-notice">{{ notice }}</div>

    <section class="studio-shell">
      <aside class="panel workflow-directory">
        <div class="panel-heading directory-heading">
          <div><span class="eyebrow">DIRECTORY</span><h2>{{ workflows.length }} 个 Workflow</h2></div>
          <button class="icon-add" type="button" title="新建 Workflow" @click="startNew">＋</button>
        </div>
        <div v-if="workflows.length" class="workflow-list">
          <button
            v-for="item in workflows"
            :key="item.workflow_id"
            type="button"
            :class="{ active: item.workflow_id === currentWorkflow?.workflow_id }"
            @click="selectWorkflow(item.workflow_id)"
          >
            <span class="workflow-mark">F</span>
            <span><strong>{{ item.name }}</strong><small>{{ item.description || item.revision?.goal }}</small><em>v{{ item.revision?.version || 1 }} · {{ item.status === 'published' ? '已发布' : '草稿' }}</em></span>
          </button>
        </div>
        <div v-else class="empty-state compact"><span>F</span><strong>还没有 Workflow</strong><p>先把一件想持续完成的事告诉 Agent。</p></div>
        <div class="directory-note"><strong>设计和执行分离</strong><p>设计 Run 禁止调用工具；发布后才编译为真实 TaskGraph。</p></div>
      </aside>

      <main class="panel workflow-studio">
        <div class="studio-toolbar">
          <div>
            <span class="eyebrow">{{ currentWorkflow ? `WORKFLOW · ${currentWorkflow.workflow_id}` : 'NEW WORKFLOW' }}</span>
            <h2>{{ draft?.name || '从一个清晰目标开始' }}</h2>
            <p v-if="draft">{{ draft.description || draft.graph.summary }}</p>
          </div>
          <div v-if="draft" class="studio-actions">
            <span v-if="dirty" class="unsaved-dot">未保存</span>
            <button class="secondary-button" type="button" :disabled="saving || generating" @click="saveDraft">{{ saving ? '保存中…' : '保存草稿' }}</button>
            <button class="secondary-button" type="button" :disabled="saving || generating" @click="runWorkflow">{{ runButtonLabel }}</button>
            <button class="primary-button" type="button" :disabled="saving || generating || selectedRevisionStatus === 'published'" @click="publishCurrent">{{ selectedRevisionStatus === 'published' ? '已发布' : '发布版本' }}</button>
          </div>
        </div>

        <div v-if="!draft" class="workflow-kickoff">
          <div class="kickoff-copy">
            <span class="kickoff-icon">✦</span>
            <span class="eyebrow">DESCRIBE THE OUTCOME</span>
            <h3>你说清楚结果，Agent 负责组织流程</h3>
            <p>系统会选择合适的 Agent、Team、固定 Scenario、Skill 与 Tool，并显式组织验证、分支、有界循环和人工确认。</p>
            <div class="principles"><span>设计执行分离</span><span>引用冻结版本</span><span>子运行可恢复</span><span>高风险加确认</span></div>
          </div>
          <form class="kickoff-form" @submit.prevent="generateInitial">
            <label><span>想可靠完成什么？</span><textarea v-model.trim="goalInput" rows="8" maxlength="4000" required placeholder="例如：每天汇总项目进展和风险，形成一份可核验的简报，发布前由我确认。" /></label>
            <div class="prompt-examples">
              <button v-for="example in examples" :key="example" type="button" @click="goalInput = example">{{ example }}</button>
            </div>
            <button class="primary-button generate-button" type="submit" :disabled="generating || !goalInput.trim()">{{ generating ? generationLabel : '让 Agent 设计流程 →' }}</button>
          </form>
        </div>

        <div v-else class="workflow-workspace">
          <aside class="design-dialogue">
            <div class="workspace-heading"><span class="eyebrow">DESIGN DIALOGUE</span><h3>和 Agent 一起调整</h3></div>
            <div class="dialogue-feed">
              <article v-for="(message, index) in messages" :key="index" :class="message.role">
                <span>{{ message.role === 'agent' ? 'JOY' : 'YOU' }}</span><p>{{ message.content }}</p>
              </article>
              <article v-if="generating" class="agent pending"><span>JOY</span><p>{{ generationLabel }}</p></article>
            </div>
            <form class="revision-prompt" @submit.prevent="requestRevision">
              <label><span>告诉 Agent 怎么改</span><textarea v-model.trim="changeInput" rows="5" maxlength="2000" placeholder="例如：研究和素材收集并行；发布前增加人工确认；去掉不必要的步骤。" /></label>
              <button class="primary-button" type="submit" :disabled="generating || !changeInput.trim()">{{ generating ? '生成中…' : '生成新方案' }}</button>
            </form>
          </aside>

          <section class="graph-panel">
            <div class="workspace-heading graph-heading">
              <div><span class="eyebrow">EXECUTABLE DAG</span><h3>生成的执行流程</h3></div>
              <div class="graph-stats"><span>{{ draft.graph.nodes.length }} 节点</span><span>{{ draft.graph.edges.length }} 依赖</span><span>{{ draft.graph.policies.max_concurrent }} 并发</span></div>
            </div>
            <div class="graph-viewport">
              <div class="graph-canvas" :style="{ width: `${graphSize.width}px`, height: `${graphSize.height}px` }">
                <svg :viewBox="`0 0 ${graphSize.width} ${graphSize.height}`" aria-hidden="true">
                  <defs><marker id="workflow-arrow" markerWidth="8" markerHeight="8" refX="7" refY="4" orient="auto"><path d="M0,0 L8,4 L0,8 Z" /></marker></defs>
                  <path v-for="edge in edgePositions" :key="`${edge.source}-${edge.target}`" class="graph-edge" :d="edge.path" marker-end="url(#workflow-arrow)" />
                </svg>
                <button
                  v-for="position in nodePositions"
                  :key="position.node.id"
                  type="button"
                  class="graph-node"
                  :class="[position.node.kind, { selected: selectedNodeId === position.node.id }]"
                  :style="{ left: `${position.x}px`, top: `${position.y}px` }"
                  @click="selectedNodeId = position.node.id"
                >
                  <span class="node-kind">{{ nodeKind(position.node).label }}</span>
                  <strong>{{ position.node.name }}</strong>
                  <small>{{ nodeExecutor(position.node) }}</small>
                  <em>{{ position.node.dependencies.length ? `等待 ${position.node.dependencies.length} 个上游` : '起始节点' }}</em>
                </button>
              </div>
            </div>
            <footer class="graph-footer">
              <span :class="`risk-${draft.graph.risk_level}`">{{ riskLabel }}</span>
              <span>预计 {{ draft.graph.estimated_duration_minutes || '—' }} 分钟</span>
              <span>{{ draft.graph.policies.fail_fast ? '失败即停止' : '允许独立分支继续' }}</span>
              <span>{{ draft.graph.policies.aggregate ? '汇总最终结果' : '分别保留结果' }}</span>
            </footer>
          </section>

          <aside class="workflow-inspector">
            <section class="inspector-block">
              <div class="workspace-heading"><span class="eyebrow">NODE INSPECTOR</span><h3>节点检查</h3></div>
              <template v-if="selectedNode">
                <div class="node-title"><span>{{ nodeKind(selectedNode).mark }}</span><div><strong>{{ selectedNode.name }}</strong><small>{{ selectedNode.id }} · {{ nodeKind(selectedNode).label }}</small></div></div>
                <dl>
                  <dt>执行目标</dt><dd>{{ selectedNode.objective }}</dd>
                  <dt>执行者</dt><dd>{{ nodeExecutor(selectedNode) }}</dd>
                  <dt>依赖</dt><dd>{{ selectedNode.dependencies.join('、') || '无，可立即开始' }}</dd>
                  <dt>策略</dt><dd>{{ nodePolicy(selectedNode) }}</dd>
                </dl>
                <div class="capability-tags"><span v-for="tool in selectedNode.allowed_tools" :key="tool">Tool · {{ tool }}</span><span v-for="skill in selectedNode.skills" :key="skill">Skill · {{ skill }}</span><small v-if="!selectedNode.allowed_tools.length && !selectedNode.skills.length">未绑定额外能力</small></div>
                <p class="inspector-hint">Team / Scenario 节点保存精确发布版本，执行时创建可独立追踪的子 Run；控制节点由 Runtime 确定性执行。</p>
              </template>
            </section>
            <section class="inspector-block revisions-block">
              <div class="workspace-heading"><span class="eyebrow">REVISIONS</span><h3>版本记录</h3></div>
              <div v-if="currentWorkflow?.revisions?.length" class="revision-list">
                <button v-for="revision in currentWorkflow.revisions" :key="revision.revision_id" type="button" :class="{ active: revision.revision_id === selectedRevisionId }" @click="selectRevision(revision)">
                  <span>v{{ revision.version }}</span><div><strong>{{ revision.status === 'published' ? '已发布' : revision.status === 'superseded' ? '历史发布' : '草稿' }}</strong><small>{{ formatDate(revision.created_at) }}</small></div>
                </button>
              </div>
              <p v-else class="no-revision">当前是尚未保存的新方案。</p>
              <button v-if="currentWorkflow" class="delete-link" type="button" @click="removeCurrent">删除 Workflow</button>
            </section>
          </aside>
        </div>
      </main>
    </section>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import {
  createWorkflow,
  createWorkflowRevision,
  deleteWorkflow,
  executeWorkflow,
  getWorkflow,
  getWorkflowGeneration,
  listWorkflows,
  publishWorkflow,
  startWorkflowGeneration,
  type Workflow,
  type WorkflowDraft,
  type WorkflowNode,
  type WorkflowRevision,
} from '../api/workflows'

interface DialogueMessage { role: 'user' | 'agent'; content: string }
interface NodePosition { node: WorkflowNode; x: number; y: number; level: number }

const router = useRouter()
const workflows = ref<Workflow[]>([])
const currentWorkflow = ref<Workflow | null>(null)
const draft = ref<WorkflowDraft | null>(null)
const selectedRevisionId = ref('')
const selectedNodeId = ref('')
const goalInput = ref('')
const changeInput = ref('')
const sourceRunId = ref<string | null>(null)
const messages = ref<DialogueMessage[]>([])
const loading = ref(false)
const saving = ref(false)
const generating = ref(false)
const generationStatus = ref('')
const dirty = ref(false)
const error = ref('')
const notice = ref('')
let pollTimer: number | null = null

const examples = [
  '每天汇总项目进展和风险，生成简报，发布前由我确认。',
  '研究一个主题，交叉核验来源，形成可以分享的成果文章。',
  '收到客户需求后，分析优先级、形成方案并等待我批准执行。',
]

const selectedRevision = computed(() => currentWorkflow.value?.revisions?.find((item) => item.revision_id === selectedRevisionId.value) || null)
const selectedRevisionStatus = computed(() => selectedRevision.value?.status || '')
const selectedNode = computed(() => draft.value?.graph.nodes.find((item) => item.id === selectedNodeId.value) || null)
const generationLabel = computed(() => generationStatus.value === 'running' ? 'Agent 正在推演节点与依赖…' : '等待执行节点领取设计任务…')
const runButtonLabel = computed(() => selectedRevisionStatus.value === 'published' && !dirty.value ? '运行 Workflow' : '试运行草稿')
const riskLabel = computed(() => ({ low: '低风险', medium: '中风险', high: '高风险' })[draft.value?.graph.risk_level || 'medium'])

const nodePositions = computed<NodePosition[]>(() => {
  const nodes = draft.value?.graph.nodes || []
  const levels = new Map<string, number>()
  for (let pass = 0; pass < nodes.length + 1; pass += 1) {
    for (const node of nodes) {
      const dependencies = node.dependencies.map((id) => levels.get(id))
      if (!node.dependencies.length) levels.set(node.id, 0)
      else if (dependencies.every((value) => value !== undefined)) levels.set(node.id, Math.max(...dependencies.map(Number)) + 1)
    }
  }
  const groups = new Map<number, WorkflowNode[]>()
  for (const node of nodes) {
    const level = levels.get(node.id) ?? 0
    groups.set(level, [...(groups.get(level) || []), node])
  }
  return [...groups.entries()].flatMap(([level, items]) => items.map((node, index) => ({ node, level, x: 34 + level * 252, y: 36 + index * 142 })))
})

const graphSize = computed(() => {
  const maxLevel = Math.max(0, ...nodePositions.value.map((item) => item.level))
  const counts = new Map<number, number>()
  for (const item of nodePositions.value) counts.set(item.level, (counts.get(item.level) || 0) + 1)
  return { width: Math.max(760, 34 + (maxLevel + 1) * 252), height: Math.max(470, 70 + Math.max(1, ...counts.values()) * 142) }
})

const edgePositions = computed(() => {
  const positions = new Map(nodePositions.value.map((item) => [item.node.id, item]))
  return (draft.value?.graph.edges || []).flatMap((edge) => {
    const source = positions.get(edge.source)
    const target = positions.get(edge.target)
    if (!source || !target) return []
    const x1 = source.x + 198
    const y1 = source.y + 54
    const x2 = target.x
    const y2 = target.y + 54
    const bend = Math.max(36, (x2 - x1) / 2)
    return [{ ...edge, path: `M ${x1} ${y1} C ${x1 + bend} ${y1}, ${x2 - bend} ${y2}, ${x2} ${y2}` }]
  })
})

function clearMessages() { error.value = ''; notice.value = '' }
function errorText(cause: unknown, fallback: string) { return cause instanceof Error ? cause.message : fallback }
function formatDate(value?: string | null) { return value ? new Date(value).toLocaleString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' }) : '—' }
const nodeKinds = {
  agent: { label: 'AGENT TASK', mark: 'A' },
  team: { label: 'TEAM SUBRUN', mark: 'T' },
  scenario: { label: 'SCENARIO SUBRUN', mark: 'S' },
  approval: { label: 'HUMAN GATE', mark: 'H' },
  verify: { label: 'QUALITY GATE', mark: 'V' },
  branch: { label: 'CONDITION BRANCH', mark: 'B' },
  bounded_loop: { label: 'BOUNDED LOOP', mark: 'L' },
} as const
function nodeKind(node: WorkflowNode) { return nodeKinds[node.kind] }
function nodeExecutor(node: WorkflowNode) {
  if (node.kind === 'team') return `Team · ${node.subrun?.team_id || node.team_id || '冻结版本'}`
  if (node.kind === 'scenario') return `Scenario · ${node.subrun?.scenario_id || node.scenario_id || '冻结版本'}${node.subrun?.scenario_version || node.scenario_version ? ` v${node.subrun?.scenario_version || node.scenario_version}` : ''}`
  if (node.kind === 'agent') return node.agent_id || '默认 Agent'
  if (node.kind === 'approval') return '当前用户（人工确认）'
  return 'Runtime 控制面'
}
function nodePolicy(node: WorkflowNode) {
  if (node.kind === 'agent') return `失败最多尝试 ${node.max_attempts} 次`
  if (node.kind === 'bounded_loop') return `最多 ${Number(node.configuration?.max_iterations || 1)} 轮`
  if (node.kind === 'team' || node.kind === 'scenario') return '子 Run 失败向父 Workflow 传播'
  if (node.kind === 'approval') return '等待审批，不自动重试'
  return '确定性执行，不自动重试'
}
function applyDraft(value: WorkflowDraft, options: { dirty: boolean; sourceRunId?: string | null }) {
  draft.value = structuredClone(value)
  goalInput.value = value.goal
  sourceRunId.value = options.sourceRunId || null
  dirty.value = options.dirty
  selectedNodeId.value = value.graph.nodes[0]?.id || ''
}
function applyRevision(workflow: Workflow, revision: WorkflowRevision) {
  applyDraft({ name: workflow.name, description: workflow.description, goal: revision.goal, graph: revision.graph }, { dirty: false, sourceRunId: revision.source_run_id })
  selectedRevisionId.value = revision.revision_id
}

async function loadDirectory() {
  loading.value = true
  clearMessages()
  try { workflows.value = await listWorkflows() } catch (cause) { error.value = errorText(cause, '读取 Workflow 失败') } finally { loading.value = false }
}
async function selectWorkflow(workflowId: string) {
  clearMessages()
  try {
    const workflow = await getWorkflow(workflowId)
    currentWorkflow.value = workflow
    applyRevision(workflow, workflow.revision)
    messages.value = [{ role: 'agent', content: `已打开 v${workflow.revision.version}。你可以审查节点，也可以直接告诉我下一版要怎么改。` }]
  } catch (cause) { error.value = errorText(cause, '读取 Workflow 失败') }
}
function selectRevision(revision: WorkflowRevision) {
  if (!currentWorkflow.value) return
  applyRevision(currentWorkflow.value, revision)
  messages.value.push({ role: 'agent', content: `已切换到 v${revision.version} 供你审查；任何修改都会保存为新版本。` })
}
function startNew() {
  if (dirty.value && !window.confirm('当前方案尚未保存，仍要新建 Workflow 吗？')) return
  currentWorkflow.value = null
  draft.value = null
  selectedRevisionId.value = ''
  selectedNodeId.value = ''
  goalInput.value = ''
  changeInput.value = ''
  sourceRunId.value = null
  messages.value = []
  dirty.value = false
  clearMessages()
}

async function generateInitial() { await submitGeneration('请生成第一版，保持节点少而清晰。') }
async function requestRevision() {
  if (!changeInput.value.trim()) return
  const instruction = changeInput.value.trim()
  messages.value.push({ role: 'user', content: instruction })
  changeInput.value = ''
  await submitGeneration(instruction)
}
async function submitGeneration(instruction: string) {
  if (!goalInput.value.trim() || generating.value) return
  clearMessages()
  generating.value = true
  generationStatus.value = 'queued'
  try {
    const run = await startWorkflowGeneration({
      goal: goalInput.value.trim(),
      instruction,
      workflow_id: currentWorkflow.value?.workflow_id,
      base_graph: draft.value?.graph,
    })
    sourceRunId.value = run.run_id
    await pollGeneration(run.run_id)
  } catch (cause) {
    generating.value = false
    error.value = errorText(cause, '提交 Workflow 设计任务失败')
  }
}
async function pollGeneration(runId: string) {
  try {
    const result = await getWorkflowGeneration(runId)
    generationStatus.value = result.status
    if (result.status === 'completed' && result.draft) {
      applyDraft(result.draft, { dirty: true, sourceRunId: runId })
      messages.value.push({ role: 'agent', content: `已生成“${result.draft.name}”：${result.draft.graph.nodes.length} 个节点、${result.draft.graph.edges.length} 条依赖。请先审查流程，再保存或继续告诉我怎么改。` })
      generating.value = false
      return
    }
    if (['failed', 'cancelled', 'timed_out'].includes(result.status)) {
      const detail = typeof result.error === 'string' ? result.error : result.error?.message
      throw new Error(detail || result.status_summary || 'Workflow 设计失败')
    }
    pollTimer = window.setTimeout(() => void pollGeneration(runId), 1300)
  } catch (cause) {
    generating.value = false
    error.value = errorText(cause, '读取 Workflow 设计结果失败')
  }
}

function savePayload() {
  if (!draft.value) throw new Error('没有可保存的 Workflow 草稿')
  return { name: draft.value.name, description: draft.value.description, goal: draft.value.goal, graph: draft.value.graph, change_note: currentWorkflow.value ? 'AI-assisted conversational revision' : 'AI-generated initial draft', source_run_id: sourceRunId.value }
}
async function persistDraft(): Promise<Workflow | null> {
  if (!draft.value) return null
  if (!dirty.value && currentWorkflow.value) return currentWorkflow.value
  saving.value = true
  clearMessages()
  try {
    const saved = currentWorkflow.value
      ? await createWorkflowRevision(currentWorkflow.value.workflow_id, savePayload())
      : await createWorkflow(savePayload())
    currentWorkflow.value = saved
    applyRevision(saved, saved.revision)
    await loadDirectory()
    notice.value = `已保存不可变版本 v${saved.revision.version}`
    return saved
  } catch (cause) {
    error.value = errorText(cause, '保存 Workflow 失败')
    return null
  } finally { saving.value = false }
}
async function saveDraft() { await persistDraft() }
async function publishCurrent() {
  const workflow = await persistDraft()
  if (!workflow) return
  const revisionId = workflow.current_revision_id
  saving.value = true
  clearMessages()
  try {
    const published = await publishWorkflow(workflow.workflow_id, revisionId)
    currentWorkflow.value = published
    applyRevision(published, published.revision)
    await loadDirectory()
    notice.value = `v${published.revision.version} 已发布，可作为可靠执行入口复用。`
  } catch (cause) { error.value = errorText(cause, '发布 Workflow 失败') } finally { saving.value = false }
}
async function runWorkflow() {
  const workflow = await persistDraft()
  if (!workflow) return
  const revision = workflow.revisions?.find((item) => item.revision_id === workflow.current_revision_id) || workflow.revision
  saving.value = true
  clearMessages()
  try {
    const run = await executeWorkflow(workflow.workflow_id, { revision_id: revision.revision_id, preview: revision.status !== 'published' })
    await router.push(`/runs/${run.run_id}`)
  } catch (cause) { error.value = errorText(cause, '启动 Workflow 失败') } finally { saving.value = false }
}
async function removeCurrent() {
  const workflow = currentWorkflow.value
  if (!workflow || !window.confirm(`删除 Workflow“${workflow.name}”？历史 Run 不会删除。`)) return
  try { await deleteWorkflow(workflow.workflow_id); startNew(); await loadDirectory() } catch (cause) { error.value = errorText(cause, '删除 Workflow 失败') }
}

onMounted(async () => { await loadDirectory(); if (workflows.value[0]) await selectWorkflow(workflows.value[0].workflow_id) })
onUnmounted(() => { if (pollTimer) window.clearTimeout(pollTimer) })
</script>

<style scoped>
.workflow-page{display:grid;gap:16px;max-width:1760px}.workflow-page .page-heading{margin-bottom:0}.workflow-contract{display:flex;flex-wrap:wrap;align-items:center;gap:9px;padding:11px 14px;color:var(--text-muted);background:var(--surface-raised);border:1px solid var(--border);border-radius:var(--radius-md);font-size:10px}.workflow-contract span{padding:5px 8px;color:var(--text);background:var(--surface);border-radius:6px}.workflow-contract b{color:var(--accent)}.success-notice{color:#66d7a2;background:rgba(50,182,122,.08);border:1px solid rgba(50,182,122,.22)}.studio-shell{display:grid;grid-template-columns:248px minmax(0,1fr);gap:16px;min-height:720px}.workflow-directory{display:flex;min-height:720px;flex-direction:column;overflow:hidden}.directory-heading{padding-bottom:12px}.icon-add{width:31px;height:31px;color:var(--accent);background:var(--accent-subtle);border:1px solid var(--accent-border);border-radius:8px;cursor:pointer}.workflow-list{display:grid;padding:0 9px}.workflow-list>button{display:grid;grid-template-columns:34px minmax(0,1fr);gap:10px;padding:13px 11px;color:var(--text);background:transparent;border:0;border-top:1px solid var(--border);border-radius:9px;text-align:left;cursor:pointer}.workflow-list>button:hover,.workflow-list>button.active{background:var(--surface-hover)}.workflow-list>button.active{box-shadow:inset 2px 0 var(--accent)}.workflow-mark{display:grid;width:32px;height:32px;place-items:center;color:var(--accent);background:var(--accent-subtle);border:1px solid var(--accent-border);border-radius:9px;font:600 11px var(--font-mono)}.workflow-list button>span:last-child{display:grid;min-width:0;gap:3px}.workflow-list strong,.workflow-list small,.workflow-list em{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.workflow-list strong{color:var(--text-strong);font-size:11px}.workflow-list small{color:var(--text-muted);font-size:9px}.workflow-list em{color:var(--accent);font:normal 8px var(--font-mono)}.directory-note{margin:auto 12px 12px;padding:12px;color:var(--text-muted);background:var(--surface-raised);border-radius:9px}.directory-note strong{color:var(--text-strong);font-size:10px}.directory-note p{margin:4px 0 0;font-size:9px;line-height:1.6}.workflow-studio{min-width:0;overflow:hidden}.studio-toolbar{display:flex;min-height:100px;align-items:center;justify-content:space-between;gap:18px;padding:20px 24px;border-bottom:1px solid var(--border)}.studio-toolbar h2{margin:5px 0 2px;color:var(--text-strong);font-size:20px}.studio-toolbar p{max-width:660px;margin:0;color:var(--text-muted);font-size:11px}.studio-actions{display:flex;align-items:center;gap:8px}.unsaved-dot{color:var(--warning);font:9px var(--font-mono)}.workflow-kickoff{display:grid;grid-template-columns:minmax(0,.9fr) minmax(360px,1fr);gap:48px;align-items:center;min-height:610px;padding:60px}.kickoff-copy{max-width:510px}.kickoff-icon{display:grid;width:54px;height:54px;margin-bottom:28px;place-items:center;color:#fff;background:linear-gradient(135deg,var(--accent-strong),#b84ee0);border-radius:16px;box-shadow:0 18px 40px rgba(217,94,33,.22);font-size:22px}.kickoff-copy h3{margin:10px 0;color:var(--text-strong);font-size:28px;line-height:1.17}.kickoff-copy p{color:var(--text-muted);line-height:1.8}.principles{display:flex;flex-wrap:wrap;gap:7px;margin-top:24px}.principles span{padding:6px 9px;color:var(--text-muted);background:var(--surface-raised);border:1px solid var(--border);border-radius:7px;font-size:9px}.kickoff-form{display:grid;gap:13px;padding:24px;background:var(--surface-raised);border:1px solid var(--border);border-radius:16px}.kickoff-form label,.revision-prompt label{display:grid;gap:7px}.kickoff-form label>span,.revision-prompt label>span{color:var(--text-strong);font-size:11px;font-weight:600}.kickoff-form textarea,.revision-prompt textarea{width:100%;padding:13px;color:var(--text);background:var(--input);border:1px solid var(--border-strong);border-radius:11px;outline:none;resize:vertical}.kickoff-form textarea:focus,.revision-prompt textarea:focus{border-color:var(--accent-border)}.prompt-examples{display:grid;gap:6px}.prompt-examples button{padding:8px 10px;color:var(--text-muted);background:transparent;border:1px solid var(--border);border-radius:8px;text-align:left;cursor:pointer;font-size:9px}.prompt-examples button:hover{color:var(--text);background:var(--surface-hover)}.generate-button{width:100%}.workflow-workspace{display:grid;grid-template-columns:280px minmax(520px,1fr) 250px;min-height:620px}.design-dialogue,.workflow-inspector{display:flex;min-width:0;flex-direction:column;background:var(--surface-raised)}.design-dialogue{border-right:1px solid var(--border)}.workflow-inspector{border-left:1px solid var(--border)}.workspace-heading{padding:16px}.workspace-heading h3{margin:5px 0 0;color:var(--text-strong);font-size:14px}.dialogue-feed{display:flex;max-height:430px;min-height:250px;flex:1;flex-direction:column;gap:12px;overflow:auto;padding:0 14px 14px}.dialogue-feed article{display:grid;grid-template-columns:28px 1fr;gap:8px;align-items:start}.dialogue-feed article>span{display:grid;width:26px;height:26px;place-items:center;color:var(--text-muted);background:var(--surface);border:1px solid var(--border);border-radius:8px;font:8px var(--font-mono)}.dialogue-feed article.agent>span{color:var(--accent);background:var(--accent-subtle);border-color:var(--accent-border)}.dialogue-feed p{margin:0;padding:9px 10px;color:var(--text);background:var(--surface);border:1px solid var(--border);border-radius:3px 10px 10px;font-size:10px;line-height:1.6}.dialogue-feed .user p{background:var(--accent-subtle);border-color:var(--accent-border)}.dialogue-feed .pending p{color:var(--text-muted)}.revision-prompt{display:grid;gap:10px;margin-top:auto;padding:14px;border-top:1px solid var(--border)}.revision-prompt textarea{padding:10px;font-size:10px}.graph-panel{min-width:0;background:var(--surface)}.graph-heading{display:flex;align-items:center;justify-content:space-between;border-bottom:1px solid var(--border)}.graph-stats{display:flex;gap:6px}.graph-stats span{padding:4px 6px;color:var(--text-muted);background:var(--surface-raised);border-radius:5px;font:8px var(--font-mono)}.graph-viewport{height:510px;overflow:auto;background-image:linear-gradient(var(--border) 1px,transparent 1px),linear-gradient(90deg,var(--border) 1px,transparent 1px);background-size:24px 24px;background-position:-1px -1px}.graph-canvas{position:relative;min-width:100%;min-height:100%}.graph-canvas svg{position:absolute;inset:0;width:100%;height:100%;pointer-events:none}.graph-edge{fill:none;stroke:var(--border-strong);stroke-width:1.5}.graph-canvas marker path{fill:var(--border-strong)}.graph-node{position:absolute;display:flex;width:198px;min-height:108px;flex-direction:column;align-items:flex-start;padding:13px;color:var(--text);background:var(--surface);border:1px solid var(--border-strong);border-radius:12px;box-shadow:0 8px 18px rgba(0,0,0,.08);text-align:left;cursor:pointer}.graph-node:hover,.graph-node.selected{border-color:var(--accent);box-shadow:0 0 0 2px var(--accent-subtle),0 10px 28px rgba(0,0,0,.12)}.graph-node.approval{border-style:dashed}.node-kind{color:var(--accent);font:8px var(--font-mono);letter-spacing:.08em}.graph-node.approval .node-kind{color:var(--warning)}.graph-node strong{max-width:100%;margin-top:7px;overflow:hidden;color:var(--text-strong);font-size:12px;text-overflow:ellipsis;white-space:nowrap}.graph-node small{max-width:100%;margin-top:2px;overflow:hidden;color:var(--text-muted);font:8px var(--font-mono);text-overflow:ellipsis;white-space:nowrap}.graph-node em{margin-top:auto;color:var(--text-muted);font-size:8px;font-style:normal}.graph-footer{display:flex;flex-wrap:wrap;gap:7px;padding:12px 16px;border-top:1px solid var(--border)}.graph-footer span{padding:5px 7px;color:var(--text-muted);background:var(--surface-raised);border-radius:6px;font-size:8px}.graph-footer .risk-high{color:var(--danger)}.graph-footer .risk-medium{color:var(--warning)}.graph-footer .risk-low{color:var(--success)}.inspector-block{border-bottom:1px solid var(--border)}.node-title{display:flex;gap:10px;align-items:center;padding:0 16px 14px}.node-title>span{display:grid;width:34px;height:34px;place-items:center;color:var(--accent);background:var(--accent-subtle);border:1px solid var(--accent-border);border-radius:9px;font:600 10px var(--font-mono)}.node-title>div{display:grid;min-width:0}.node-title strong{overflow:hidden;color:var(--text-strong);font-size:11px;text-overflow:ellipsis;white-space:nowrap}.node-title small{color:var(--text-muted);font:8px var(--font-mono)}.inspector-block dl{display:grid;grid-template-columns:56px 1fr;gap:9px;margin:0;padding:0 16px 14px;font-size:9px}.inspector-block dt{color:var(--text-muted)}.inspector-block dd{margin:0;color:var(--text);line-height:1.55;overflow-wrap:anywhere}.capability-tags{display:flex;flex-wrap:wrap;gap:5px;padding:0 16px 14px}.capability-tags span{padding:4px 6px;color:var(--accent);background:var(--accent-subtle);border-radius:5px;font:8px var(--font-mono)}.capability-tags small{color:var(--text-muted);font-size:9px}.inspector-hint{margin:0 16px 16px;padding:9px;color:var(--text-muted);background:var(--surface);border-left:2px solid var(--accent);font-size:9px;line-height:1.6}.revisions-block{display:flex;min-height:0;flex:1;flex-direction:column}.revision-list{display:grid;max-height:250px;overflow:auto;padding:0 10px}.revision-list button{display:grid;grid-template-columns:34px 1fr;gap:9px;align-items:center;padding:9px;color:var(--text);background:transparent;border:0;border-top:1px solid var(--border);border-radius:8px;text-align:left;cursor:pointer}.revision-list button:hover,.revision-list button.active{background:var(--surface)}.revision-list button>span{color:var(--accent);font:10px var(--font-mono)}.revision-list div{display:grid}.revision-list strong{color:var(--text-strong);font-size:9px}.revision-list small{color:var(--text-muted);font-size:8px}.no-revision{padding:0 16px;color:var(--text-muted);font-size:9px}.delete-link{margin:auto 14px 14px;padding:7px;color:var(--danger);background:transparent;border:0;cursor:pointer;font-size:9px;text-align:left}
@media(max-width:1650px){.workflow-workspace{grid-template-columns:240px minmax(480px,1fr)}.workflow-inspector{grid-column:1/-1;display:grid;grid-template-columns:1fr 1fr;border-top:1px solid var(--border);border-left:0}.revisions-block{border-left:1px solid var(--border)}}
@media(max-width:1280px){.studio-shell{grid-template-columns:1fr}.workflow-directory{min-height:0;max-height:310px}.workflow-workspace{grid-template-columns:260px minmax(500px,1fr)}.workflow-kickoff{padding:38px}.directory-note{display:none}}
@media(max-width:860px){.workflow-kickoff,.workflow-workspace,.workflow-inspector{grid-template-columns:1fr}.workflow-kickoff{padding:24px}.design-dialogue{border-right:0;border-bottom:1px solid var(--border)}.graph-panel{min-width:680px}.workflow-studio{overflow:auto}.studio-toolbar{align-items:flex-start;flex-direction:column}.studio-actions{flex-wrap:wrap}.workflow-inspector{min-width:680px}.revisions-block{border-left:0}}
</style>
