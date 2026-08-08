<template>
  <div class="dinq-run-page">
    <header class="workspace-heading">
      <div>
        <span class="eyebrow">DINQ SEARCH WORKSPACE</span>
        <h1>{{ projection?.search.query || '正在准备人才搜索' }}</h1>
        <p>搜索过程、候选人命中证据与候选人富化档案保留在同一个可回放 Run。</p>
      </div>
      <div class="heading-actions">
        <span v-if="projection" :class="['status-pill', projection.search.status]">{{ statusLabel(projection.search.status) }}</span>
        <button class="secondary-button" type="button" @click="historyOpen = true">历史会话</button>
        <button class="secondary-button" type="button" @click="startNewSearch">新的搜索</button>
        <router-link class="secondary-button" :to="{ path: '/runs', query: { run: runId } }">通用诊断</router-link>
      </div>
    </header>

    <div v-if="error" class="notice error-notice">{{ error }}</div>
    <section v-if="loading && !projection" class="workspace-loading panel">正在恢复 Dinq 搜索工作台…</section>
    <section v-else-if="projection" class="dinq-workspace">
      <button v-if="historyOpen" class="drawer-scrim" type="button" aria-label="关闭历史会话" @click="historyOpen = false" />
      <aside v-if="historyOpen" class="workspace-sessions panel">
        <div class="session-toolbar">
          <span class="eyebrow">HISTORY</span><div><button class="plain-icon" type="button" title="刷新会话" @click="loadSessions">↻</button><button class="plain-icon" type="button" title="关闭历史会话" @click="historyOpen = false">×</button></div>
        </div>
        <div class="session-list">
          <button v-for="session in sessions" :key="session.key" :class="['session-item', { active: session.latest_run_id === runId }]" @click="openSession(session.latest_run_id)">
            <strong>{{ sessionLabel(session.key) }}</strong><small>{{ statusLabel(session.latest_status || '') }} · {{ formatDate(session.updated_at) }}</small>
          </button>
          <p v-if="!sessions.length" class="muted">暂无搜索会话</p>
        </div>
      </aside>

      <section class="workspace-dialogue panel">
        <div class="dialogue-heading">
          <div><span class="eyebrow">SEARCH CONVERSATION</span><h2>搜索与条件确认</h2></div>
          <span class="metric">{{ projection.search.tool_calls }} 次工具调用</span>
        </div>
        <div ref="dialogueScroll" class="dialogue-scroll">
          <div class="run-stats">
            <span><small>状态</small><b>{{ statusLabel(projection.search.status) }}</b></span>
            <span><small>候选人</small><b>{{ projection.search.total_candidates }}</b></span>
            <span><small>已验证</small><b>{{ projection.search.verified_candidates }}</b></span>
          </div>

          <section class="thought-panel">
            <button class="activity-toggle" type="button" :aria-expanded="activityExpanded" @click="activityExpanded = !activityExpanded">
              <span><i :class="['activity-dot', { active: !isTerminalStatus(projection.search.status) }]" />Thought / 搜索过程</span>
              <small>{{ projection.activity.length }} 个事件 · {{ activityExpanded ? '收起 ⌃' : '展开 ⌄' }}</small>
            </button>
            <div v-show="activityExpanded" class="activity-list">
              <article v-for="item in projection.activity" :key="`${item.sequence}-${item.event_id}`" :class="['activity-item', item.status || '']">
                <i class="event-dot" /><div><strong>{{ activityLabel(item) }}</strong><small>{{ item.phase || '运行' }} · {{ formatDate(item.created_at) }}</small><p v-if="item.summary && item.summary !== activityLabel(item)">{{ item.summary }}</p></div>
              </article>
              <p v-if="!projection.activity.length" class="muted">等待执行事件…</p>
            </div>
          </section>

          <div v-if="visibleConversation.length" class="conversation-list">
            <article v-for="(message, index) in visibleConversation" :key="`${message.run_id || 'message'}-${index}`" :class="['conversation-message', message.role]">
              <span>{{ message.role === 'user' ? 'YOU' : 'DINQ' }}</span><MarkdownContent :content="message.content" />
            </article>
          </div>

          <section v-if="confirmedConditions.length" class="condition-summary">
            <div><span class="eyebrow">CURRENT SEARCH BRIEF</span><b>{{ projection.search.missing_conditions?.length ? '正在补全条件' : '已确认的搜索条件' }}</b></div>
            <span v-for="item in confirmedConditions" :key="item.key"><small>{{ item.label }}</small>{{ item.value }}</span>
            <p v-if="projection.search.missing_conditions?.length">还需确认：{{ projection.search.missing_conditions.map(conditionLabel).join('、') }}</p>
          </section>

          <section v-if="isTerminalStatus(projection.search.status)" class="result-summary">
            <span class="eyebrow">SEARCH RESULT</span><b>{{ statusLabel(projection.search.status) }} · {{ projection.search.total_candidates }} 位候选人</b>
            <p>{{ projection.search.summary || `已完成 ${projection.search.tool_calls} 次工具调用，候选人列表已更新。` }}</p>
          </section>
        </div>

        <section v-if="pendingRequest" class="intake-card">
          <div class="intake-heading"><div><span class="eyebrow">COMPLETE SEARCH BRIEF</span><h3>{{ intakeTitle(pendingRequest) }}</h3><p>{{ pendingRequest.question }}</p></div><small v-if="questionProgress(pendingRequest)">{{ questionProgress(pendingRequest) }}</small></div>
          <p class="intake-help">{{ intakeBrief(pendingRequest) }}</p>
          <details v-if="pendingRequest.presentation?.help_text" class="intake-guidance"><summary>查看填写规则</summary><p>{{ pendingRequest.presentation.help_text }}</p></details>
          <form class="intake-fields" @submit.prevent="submitAnswers">
            <p v-if="isConfirmationOnly(pendingRequest)" class="confirmation-copy">条件已准备好。未填写的可选条件不会限制本次搜索。</p>
            <label v-for="field in visiblePendingFields" :key="field.name" class="intake-field">
              <span>{{ inputLabel(field) }}<b v-if="field.required"> *</b></span>
              <div v-if="inputMode(field) === 'single_choice'" class="choice-list">
                <button v-for="option in inputOptions(field)" :key="option.value" type="button" :class="['choice-button', { selected: String(answerValues[field.name] || '') === option.value }]" @click="answerValues[field.name] = option.value"><strong>{{ option.label }}</strong><small v-if="option.description">{{ option.description }}</small></button>
                <input v-if="field.allow_other" v-model="otherValues[field.name]" type="text" placeholder="输入其他选项…">
              </div>
              <div v-else-if="inputMode(field) === 'multi_choice'" class="choice-list">
                <button v-for="option in inputOptions(field)" :key="option.value" type="button" :class="['choice-button', { selected: selectedChoices(field).includes(option.value) }]" @click="toggleChoice(field, option.value)"><strong>{{ option.label }}</strong><small v-if="option.description">{{ option.description }}</small></button>
                <input v-if="field.allow_other" v-model="otherValues[field.name]" type="text" placeholder="输入其他必须满足的条件…">
              </div>
              <textarea v-else-if="inputMode(field) === 'textarea'" v-model="answerValues[field.name]" rows="2" :placeholder="inputPlaceholder(field)" />
              <input v-else :type="inputMode(field) === 'number' ? 'number' : 'text'" v-model="answerValues[field.name]" :placeholder="inputPlaceholder(field)">
            </label>
            <button class="primary-button" type="submit" :disabled="answering">{{ answering ? '确认中…' : String(pendingRequest.presentation?.submit_label || '确认并继续') }}</button>
          </form>
        </section>

        <form v-else class="workspace-composer" @submit.prevent="continueRun">
          <textarea v-model="composerInput" rows="3" placeholder="继续追问、补充约束，或让 Agent 根据当前结果调整搜索…" @keydown.meta.enter.prevent="continueRun" @keydown.ctrl.enter.prevent="continueRun" />
          <div><span>Enter 发送 · Shift + Enter 换行</span><button class="primary-button" type="submit" :disabled="sending || !composerInput.trim()">{{ sending ? '提交中…' : '继续搜索 ↑' }}</button></div>
        </form>
      </section>

      <section class="workspace-candidates panel">
        <div class="candidate-heading"><div><span class="eyebrow">SEARCH RESULTS</span><h2>候选人 <small>{{ projection.search.total_candidates }} 命中 · {{ projection.search.verified_candidates }} 已验证</small></h2></div><span class="table-hint">点击一行查看档案</span></div>
        <div v-if="projection.candidates.length" class="candidate-table" role="table" aria-label="候选人搜索结果">
          <div class="candidate-table-head" role="row"><span>候选人</span><span>职位 / 公司</span><span>匹配度</span><span>命中原因</span><span>来源</span></div>
          <button v-for="candidate in projection.candidates" :key="candidate.candidate_id" :class="['candidate-row', { selected: selectedId === candidate.candidate_id }]" type="button" @click="selectCandidate(candidate.candidate_id)">
            <span class="candidate-name"><i>{{ initials(candidate.name) }}</i><b>{{ candidate.name }}</b></span>
            <span class="candidate-role"><b>{{ candidate.title || '待核验职位' }}</b><small>{{ candidate.company || '来源待补全' }}</small></span>
            <span :class="['candidate-score', { empty: candidate.match_score == null }]">{{ candidate.match_score == null ? '—' : `${Math.round(candidate.match_score * 100)}%` }}</span>
            <span class="candidate-reason">{{ candidate.match_reasons[0] || '等待命中证据' }}</span>
            <span class="candidate-sources"><i v-for="source in candidate.sources.slice(0, 3)" :key="sourceLabel(source)" :title="sourceLabel(source)">{{ sourceLabel(source).slice(0, 1).toUpperCase() }}</i></span>
          </button>
        </div>
        <div v-else class="empty-candidates"><span>⌕</span><strong>{{ projection.search.status === 'waiting_input' ? '正在确认搜索条件' : '等待候选人产物' }}</strong><p>{{ projection.search.status === 'waiting_input' ? '确认左侧问题后，系统才会开始搜索。' : 'Dinq 会在工具调用完成后持续写入候选人。' }}</p></div>
      </section>

      <aside v-if="selectedCandidate" class="workspace-detail panel">
        <div class="detail-heading"><div><span class="eyebrow">ENRICHMENT</span><h2>候选人档案</h2></div><button v-if="selectedCandidate" class="plain-icon" type="button" title="关闭档案" @click="selectedId = null">×</button></div>
        <template v-if="selectedCandidate">
          <div class="profile-header"><i>{{ initials(selectedCandidate.name) }}</i><div><h3>{{ selectedCandidate.name }}</h3><p>{{ selectedCandidate.title || '职位待核验' }}<template v-if="selectedCandidate.company"> · {{ selectedCandidate.company }}</template></p><small>{{ enrichmentLabel(selectedCandidate.enrichment_status) }}</small></div></div>
          <div class="profile-actions"><button class="primary-button" type="button" :disabled="enriching" @click="enrichCandidate">{{ enriching ? '正在创建…' : '富化此候选人' }}</button><span v-if="selectedCandidate.match_score != null">匹配 {{ Math.round(selectedCandidate.match_score * 100) }}%</span></div>
          <section class="detail-block"><h4>命中背景</h4><p v-if="selectedCandidate.match_reasons.length">{{ selectedCandidate.match_reasons.join('；') }}</p><p v-else>尚未记录可展示的命中解释。</p></section>
          <section class="detail-block"><h4>来源与证据</h4><div class="source-tags"><span v-for="source in selectedCandidate.sources" :key="sourceLabel(source)">{{ sourceLabel(source) }}</span><span v-if="!selectedCandidate.sources.length">暂无来源</span></div></section>
          <section class="detail-block profile-data"><h4>当前档案</h4><pre v-if="selectedCandidate.enrichment || selectedCandidate.profile">{{ pretty(selectedCandidate.enrichment || selectedCandidate.profile) }}</pre><p v-else>还没有富化档案。点击上方按钮将创建独立、可回放的核验 Run。</p></section>
          <form class="workspace-feedback" @submit.prevent="submitFeedback"><div><h4>人工反馈</h4><select v-model="feedbackType"><option value="incorrect">结果不对</option><option value="missing_data">缺少数据</option><option value="needs_optimization">需要优化</option><option value="helpful">很有帮助</option></select></div><textarea v-model="feedbackComment" rows="3" placeholder="指出这个候选人或富化结果需要改进的地方…" :disabled="feedbackSent" /><button class="secondary-button" type="submit" :disabled="feedbackSaving || feedbackSent || !feedbackComment.trim()">{{ feedbackSent ? '✓ 已记录反馈' : feedbackSaving ? '提交中…' : '提交到 Run' }}</button></form>
        </template>
        <div v-else class="empty-detail"><span>◇</span><strong>选择一个候选人</strong><p>点击中间列表可查看命中原因与候选人档案；需要更多资料时可直接创建富化任务。</p></div>
      </aside>
    </section>
  </div>
</template>

<script setup lang="ts">
import { computed, nextTick, onMounted, onUnmounted, ref } from 'vue'
import { onBeforeRouteUpdate, useRoute, useRouter } from 'vue-router'
import MarkdownContent from '../components/MarkdownContent.vue'
import { getDinqRunProjection, type DinqActivity, type DinqCandidate, type DinqRunProjection } from '../api/dinq'
import { getSessionHistory, getSessions, type SessionHistoryMessage, type SessionItem } from '../api/sessions'
import { createRunFeedback, getPendingRunInputs, resolveRunInput, streamRuntimeEvents, submitRuntimeRun, type PendingRunInput, type RunFeedbackType, type RunInputField } from '../api/runtime'

const route = useRoute()
const router = useRouter()
const runId = ref(String(route.params.runId))
const projection = ref<DinqRunProjection | null>(null)
const sessions = ref<SessionItem[]>([])
const conversation = ref<SessionHistoryMessage[]>([])
const pendingInputs = ref<PendingRunInput[]>([])
const answerValues = ref<Record<string, unknown>>({})
const otherValues = ref<Record<string, string>>({})
const selectedId = ref<string | null>(null)
const loading = ref(false)
const error = ref('')
const composerInput = ref('')
const sending = ref(false)
const answering = ref(false)
const enriching = ref(false)
const feedbackType = ref<RunFeedbackType>('needs_optimization')
const feedbackComment = ref('')
const feedbackSaving = ref(false)
const feedbackSent = ref(false)
const activityExpanded = ref(true)
const historyOpen = ref(false)
const dialogueScroll = ref<HTMLElement | null>(null)
let abortController: AbortController | null = null
let refreshTimer: number | null = null

const pendingRequest = computed(() => pendingInputs.value[0] || null)
const visiblePendingFields = computed(() => (pendingRequest.value?.fields || []).filter((field) => field.name !== 'brief_confirmation'))
const confirmedConditions = computed(() => projection.value?.search.condition_display?.length
  ? projection.value.search.condition_display.filter((item) => !['limit', 'brief_confirmation'].includes(item.key) && item.display_value).map((item) => ({ key: item.key, label: item.label, value: item.display_value }))
  : Object.entries(projection.value?.search.conditions || {}).filter(([key, value]) => !['limit', 'brief_confirmation'].includes(key) && value !== undefined && value !== null && value !== '').map(([key, value]) => ({ key, label: conditionLabel(key), value: conditionValue(value) })))
const selectedCandidate = computed<DinqCandidate | null>(() => projection.value?.selected_candidate || projection.value?.candidates.find((item) => item.candidate_id === selectedId.value) || null)
const visibleConversation = computed<SessionHistoryMessage[]>(() => conversation.value.length ? conversation.value : projection.value?.run.prompt ? [{ role: 'user', content: projection.value.run.prompt }] : [])

function initialiseAnswers(request?: PendingRunInput | null) {
  answerValues.value = Object.fromEntries((request?.fields || []).map((field) => [field.name, inputMode(field) === 'multi_choice' ? [] : field.value_type === 'boolean' ? false : '']))
  otherValues.value = {}
}
async function load(candidateId = selectedId.value) {
  loading.value = true
  error.value = ''
  try {
    projection.value = await getDinqRunProjection(runId.value, candidateId)
    selectedId.value = projection.value.selected_candidate_id || selectedId.value || null
    pendingInputs.value = projection.value.search.status === 'waiting_input' ? await getPendingRunInputs(runId.value) : []
    initialiseAnswers(pendingInputs.value[0])
    await loadConversation()
  } catch (cause) { error.value = cause instanceof Error ? cause.message : '读取 Dinq 工作区失败' } finally { loading.value = false; await scrollDialogue() }
}
async function loadConversation() {
  if (!projection.value?.session.session_id) { conversation.value = []; return }
  try { conversation.value = (await getSessionHistory(projection.value.session.session_id, projection.value.session.agent_id)).messages } catch { conversation.value = [] }
}
async function loadSessions() { try { sessions.value = (await getSessions(projection.value?.session.agent_id)).sessions } catch { /* supplementary history */ } }
function selectCandidate(id: string) { selectedId.value = id; feedbackSent.value = false; feedbackComment.value = ''; void load(id) }
function openSession(id?: string) { if (id && id !== runId.value) void router.push(`/dinq/runs/${encodeURIComponent(id)}`) }
function startNewSearch() { void router.push('/dinq/search') }
async function continueRun() {
  const prompt = composerInput.value.trim()
  if (!prompt || !projection.value || sending.value || pendingRequest.value) return
  sending.value = true; error.value = ''
  try {
    const scenarioInputs = { ...(projection.value.search.conditions || {}) }
    delete scenarioInputs.brief_confirmation
    const sessionId = projection.value.session.session_id || `ui:dinq-search-${Date.now()}`
    const run = await submitRuntimeRun({ prompt, sessionId, agentId: projection.value.session.agent_id || 'main-coordinator', scenarioId: 'dinq.discover.search', scenarioInputs, channel: 'dinq-search', chatId: sessionId })
    composerInput.value = ''; await router.push(`/dinq/runs/${encodeURIComponent(run.run_id)}`)
  } catch (cause) { error.value = cause instanceof Error ? cause.message : '提交继续搜索失败' } finally { sending.value = false }
}
async function enrichCandidate() {
  const candidate = selectedCandidate.value
  if (!candidate || !projection.value || enriching.value) return
  enriching.value = true
  try {
    const sessionId = projection.value.session.session_id || `ui:dinq-search-${Date.now()}`
    const run = await submitRuntimeRun({ prompt: `请核验并富化候选人：${candidate.name}`, sessionId, agentId: projection.value.session.agent_id || 'main-coordinator', scenarioId: 'dinq.candidate.enrich', scenarioInputs: { identifier: candidate.candidate_id || candidate.name }, channel: 'dinq-search', chatId: sessionId })
    await router.push(`/dinq/runs/${encodeURIComponent(run.run_id)}`)
  } catch (cause) { error.value = cause instanceof Error ? cause.message : '创建候选人富化任务失败' } finally { enriching.value = false }
}
async function submitFeedback() {
  if (!feedbackComment.value.trim() || feedbackSaving.value) return
  feedbackSaving.value = true
  try { await createRunFeedback(runId.value, { feedback_type: feedbackType.value, comment: feedbackComment.value.trim(), output_excerpt: selectedCandidate.value ? pretty(selectedCandidate.value).slice(0, 4000) : undefined }); feedbackSent.value = true } catch (cause) { error.value = cause instanceof Error ? cause.message : '提交反馈失败' } finally { feedbackSaving.value = false }
}
function inputMode(field: RunInputField) { if (field.input_mode && field.input_mode !== 'auto') return field.input_mode; return field.value_type === 'array' ? 'multi_choice' : field.options?.length || field.enum?.length ? 'single_choice' : field.value_type === 'number' || field.value_type === 'integer' ? 'number' : 'text' }
function inputLabel(field: RunInputField) { return field.label || field.name }
function inputPlaceholder(field: RunInputField) { return field.placeholder || field.examples?.[0] || '请输入…' }
function inputOptions(field: RunInputField): Array<{ value: string; label: string; description?: string; exclusive?: boolean }> { return field.options?.length ? field.options : (field.enum || []).map((value) => ({ value: String(value), label: String(value) })) }
function selectedChoices(field: RunInputField) { const value = answerValues.value[field.name]; return Array.isArray(value) ? value.map(String) : [] }
function toggleChoice(field: RunInputField, value: string) {
  const selected = selectedChoices(field); const options = inputOptions(field); const chosen = options.find((item) => item.value === value); const exclusive = new Set(options.filter((item) => item.exclusive).map((item) => item.value)); const maximum = Number(field.max_selections || 0)
  if (selected.includes(value)) { answerValues.value[field.name] = selected.filter((item) => item !== value); return }
  if (chosen?.exclusive) { answerValues.value[field.name] = [value]; return }
  const next = selected.filter((item) => !exclusive.has(item)); answerValues.value[field.name] = maximum > 0 && next.length >= maximum ? next : [...next, value]
}
function intakeTitle(request: PendingRunInput) { return String(request.presentation?.title || '请补充搜索条件') }
function intakeBrief(request: PendingRunInput) { return String(request.presentation?.description || request.presentation?.help_text || '补足这一项后，系统会继续收集剩余条件，再开始搜索。') }
function questionProgress(request: PendingRunInput) { const progress = request.presentation?.progress; const current = Number(progress?.current || 0); const total = Number(progress?.total || 0); return current > 0 && total > 0 ? `问题 ${current} / ${total}` : '' }
async function submitAnswers() {
  const request = pendingRequest.value
  if (!request || answering.value) return
  answering.value = true
  try {
    const answers: Record<string, unknown> = {}
    for (const field of request.fields) {
      if (field.name === 'brief_confirmation') { answers[field.name] = 'confirmed'; continue }
      const raw = answerValues.value[field.name]
      const other = otherValues.value[field.name]?.trim()
      if (inputMode(field) === 'multi_choice') answers[field.name] = [...selectedChoices(field), ...(other ? [other] : [])]
      else if (inputMode(field) === 'single_choice' && other) answers[field.name] = other
      else if (field.value_type === 'integer') answers[field.name] = Number.parseInt(String(raw), 10)
      else if (field.value_type === 'number') answers[field.name] = Number(raw)
      else answers[field.name] = raw
    }
    const result = await resolveRunInput(runId.value, request.input_request_id, answers)
    pendingInputs.value = result.pending_inputs; initialiseAnswers(pendingInputs.value[0]); await load()
    if (!pendingInputs.value.length && !isTerminalStatus(result.run.status)) void startStream()
  } catch (cause) { error.value = cause instanceof Error ? cause.message : '提交条件失败' } finally { answering.value = false }
}
function isConfirmationOnly(request: PendingRunInput) { return request.fields.length > 0 && request.fields.every((field) => field.name === 'brief_confirmation') }
function conditionLabel(key: string) { return ({ research_topic: '搜索方向', candidate_type: '候选人类型', region: '地区偏好', must_have: '硬约束' } as Record<string, string>)[key] || key }
function conditionValue(value: unknown) { return Array.isArray(value) ? value.join('、') : String(value) }
function statusLabel(value: string) { return ({ completed: '已完成', failed: '失败', running: '搜索中', queued: '排队中', waiting_input: '等待确认', cancelled: '已取消' } as Record<string, string>)[value] || value || '—' }
function enrichmentLabel(value: string) { return ({ not_requested: '未富化', ready: '已富化', verified: '已验证', completed: '已完成', failed: '失败' } as Record<string, string>)[value] || value }
function activityLabel(item: DinqActivity) { return ({ 'run.created': '请求已接受', 'run.queued': '任务已进入执行队列', 'run.started': '开始执行任务', 'run.completed': '任务执行完成', 'run.failed': '任务执行失败', 'task.created': '子任务已创建', 'task.started': '子任务开始执行', 'task.completed': '子任务执行完成', 'capability.started': '开始调用能力', 'capability.completed': '能力调用完成' } as Record<string, string>)[item.type] || item.summary || item.type }
function isTerminalStatus(value: string) { return ['completed', 'failed', 'cancelled', 'timed_out'].includes(value) }
function sessionLabel(value: string) { const text = value.replace(/^ui:/, ''); return text.length > 22 ? `${text.slice(0, 22)}…` : text }
function formatDate(value?: string | null) { return value ? new Date(value).toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit', second: '2-digit' }) : '—' }
function initials(value: string) { return value.split(/\s+/).map((item) => item[0]).join('').slice(0, 2).toUpperCase() || 'D' }
function sourceLabel(value: unknown) { if (typeof value === 'string') return value.replace(/^https?:\/\//, '').split('/')[0]; if (value && typeof value === 'object') return String((value as Record<string, unknown>).source || (value as Record<string, unknown>).name || 'source'); return String(value) }
function pretty(value: unknown) { return JSON.stringify(value, null, 2) }
async function scrollDialogue() { await nextTick(); if (dialogueScroll.value) dialogueScroll.value.scrollTop = dialogueScroll.value.scrollHeight }
async function startStream() { abortController?.abort(); abortController = new AbortController(); try { await streamRuntimeEvents(runId.value, () => { if (refreshTimer) window.clearTimeout(refreshTimer); refreshTimer = window.setTimeout(() => void load(selectedId.value), 120) }, { afterSequence: projection.value?.events_cursor || 0, signal: abortController.signal }) } catch { /* next active view reconnects */ } }
onMounted(async () => { await load(); await loadSessions(); if (projection.value && !isTerminalStatus(projection.value.search.status) && projection.value.search.status !== 'waiting_input') void startStream() })
onBeforeRouteUpdate(async (to) => { const nextRunId = String(to.params.runId); if (!nextRunId || nextRunId === runId.value) return; abortController?.abort(); if (refreshTimer) window.clearTimeout(refreshTimer); runId.value = nextRunId; projection.value = null; conversation.value = []; pendingInputs.value = []; selectedId.value = null; feedbackSent.value = false; feedbackComment.value = ''; await load(); await loadSessions(); if (projection.value && !isTerminalStatus(projection.value.search.status) && projection.value.search.status !== 'waiting_input') void startStream() })
onUnmounted(() => { abortController?.abort(); if (refreshTimer) window.clearTimeout(refreshTimer) })
</script>

<style scoped>
.dinq-run-page{display:flex;flex-direction:column;gap:14px;min-height:calc(100vh - var(--topbar-height));padding:25px 30px 20px;box-sizing:border-box;overflow:hidden}.workspace-heading{display:flex;justify-content:space-between;align-items:flex-end;gap:20px;flex:none}.workspace-heading h1{max-width:900px;margin:5px 0;color:var(--text-strong);font-size:28px;line-height:1.15;letter-spacing:-.035em}.workspace-heading p{margin:0;color:var(--text-muted);font-size:12px}.heading-actions{display:flex;align-items:center;gap:8px;flex-wrap:wrap}.status-pill{padding:6px 9px;border-radius:99px;color:var(--text-muted);background:var(--surface-raised);font-size:10px}.status-pill.completed{color:var(--success);background:var(--success-subtle)}.status-pill.failed{color:var(--danger);background:var(--danger-subtle)}.workspace-loading{display:grid;place-items:center;flex:1;min-height:300px;color:var(--text-muted)}.dinq-workspace{display:grid;grid-template-columns:48px minmax(330px,.8fr) minmax(500px,1.45fr) minmax(310px,.82fr);gap:10px;flex:1;min-height:0}.dinq-workspace.sessions-expanded{grid-template-columns:182px minmax(330px,.8fr) minmax(500px,1.45fr) minmax(310px,.82fr)}.panel{min-height:0;border:1px solid var(--border);border-radius:14px;background:var(--surface)}.workspace-sessions{overflow:hidden;padding:10px 7px}.session-toolbar{display:flex;align-items:center;justify-content:space-between;gap:4px}.session-toolbar .eyebrow{margin-left:5px}.plain-icon{display:inline-grid;place-items:center;width:30px;height:30px;padding:0;border:1px solid var(--border);border-radius:9px;color:var(--text-muted);background:var(--surface);font-size:17px;cursor:pointer}.plain-icon:hover{color:var(--text-strong);background:var(--surface-hover)}.session-list{display:grid;gap:5px;margin-top:14px}.session-item{display:grid;gap:4px;width:100%;padding:9px;border:1px solid transparent;border-radius:9px;color:var(--text);background:transparent;text-align:left;cursor:pointer}.session-item:hover,.session-item.active{border-color:var(--accent-border);background:var(--accent-subtle)}.session-item strong,.session-item small{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.session-item strong{font-size:11px}.session-item small{color:var(--text-muted);font-size:9px}.workspace-dialogue{display:flex;flex-direction:column;overflow:hidden;padding:16px}.dialogue-heading,.candidate-heading,.detail-heading{display:flex;align-items:flex-start;justify-content:space-between;gap:10px;padding-bottom:12px;border-bottom:1px solid var(--border)}.dialogue-heading h2,.candidate-heading h2,.detail-heading h2{margin:5px 0 0;color:var(--text-strong);font-size:17px;letter-spacing:-.02em}.metric,.table-hint{color:var(--text-muted);font:10px var(--font-mono)}.dialogue-scroll{min-height:0;flex:1;overflow:auto;padding:0 2px 12px}.run-stats{display:grid;grid-template-columns:repeat(3,1fr);gap:7px;margin:12px 0}.run-stats span{display:grid;gap:4px;padding:8px;border-radius:9px;background:var(--surface-raised)}.run-stats small{color:var(--text-muted);font-size:9px}.run-stats b{color:var(--text-strong);font-size:13px}.thought-panel{margin-bottom:10px;border:1px solid var(--border);border-radius:10px;background:var(--surface-raised);overflow:hidden}.activity-toggle{display:flex;align-items:center;justify-content:space-between;gap:10px;width:100%;padding:9px 10px;border:0;color:var(--text-strong);background:transparent;font-size:11px;text-align:left;cursor:pointer}.activity-toggle>span{display:flex;align-items:center;gap:7px}.activity-toggle small{color:var(--text-muted);font:9px var(--font-mono)}.activity-dot,.event-dot{display:block;width:7px;height:7px;border-radius:50%;background:var(--success)}.activity-dot.active{animation:pulse 1.35s ease-in-out infinite}.activity-list{max-height:210px;overflow:auto;padding:0 10px}.activity-item{display:flex;gap:8px;padding:6px 0;border-top:1px solid var(--border)}.activity-item .event-dot{flex:none;margin-top:3px;background:var(--warning)}.activity-item.completed .event-dot,.activity-item.succeeded .event-dot{background:var(--success)}.activity-item.failed .event-dot{background:var(--danger)}.activity-item>div{min-width:0}.activity-item strong{display:block;color:var(--text-strong);font-size:10px}.activity-item small,.activity-item p{color:var(--text-muted);font-size:9px;line-height:1.35}.activity-item p{margin:2px 0 0}.conversation-list{display:grid;gap:8px}.conversation-message{padding:9px 10px;border-radius:10px;color:var(--text);font-size:11px;line-height:1.55}.conversation-message.user{margin-left:22px;background:var(--accent-subtle)}.conversation-message.assistant{margin-right:12px;background:var(--surface-raised)}.conversation-message>span{display:block;margin-bottom:4px;color:var(--text-muted);font:9px var(--font-mono);letter-spacing:.08em}.condition-summary,.result-summary{display:grid;gap:7px;margin:10px 0;padding:10px;border:1px solid var(--accent-border);border-radius:10px;background:var(--accent-subtle)}.condition-summary>div{display:flex;justify-content:space-between;align-items:center;gap:8px}.condition-summary b,.result-summary b{color:var(--text-strong);font-size:11px}.condition-summary>span{display:flex;gap:5px;align-items:baseline;color:var(--text);font-size:10px}.condition-summary>span small{min-width:58px;color:var(--text-muted)}.condition-summary p,.result-summary p{margin:0;color:var(--text-muted);font-size:10px;line-height:1.45}.result-summary{border-color:var(--border);background:var(--surface-raised)}.intake-card{flex:none;margin-top:10px;padding:12px;border:1px solid var(--accent-border);border-radius:12px;background:var(--accent-subtle);max-height:48%;overflow:auto}.intake-heading{display:flex;justify-content:space-between;gap:10px}.intake-heading h3{margin:4px 0;color:var(--text-strong);font-size:14px}.intake-heading p,.intake-help{margin:0;color:var(--text-muted);font-size:10px;line-height:1.45}.intake-heading>small{color:var(--text-muted);font:10px var(--font-mono);white-space:nowrap}.intake-help{margin-top:7px}.intake-guidance{margin:6px 0;color:var(--text-muted);font-size:10px}.intake-guidance summary{color:var(--accent);cursor:pointer}.intake-guidance p{margin:4px 0}.intake-fields{display:grid;gap:7px;margin-top:10px}.intake-field{display:grid;gap:5px;color:var(--text-strong);font-size:10px}.intake-field b{color:var(--danger)}.choice-list{display:flex;gap:5px;flex-wrap:wrap}.choice-button{display:flex;align-items:center;gap:6px;padding:7px 8px;border:1px solid var(--border-strong);border-radius:9px;color:var(--text);background:var(--surface);font-size:10px;text-align:left;cursor:pointer}.choice-button small{color:var(--text-muted);font-size:9px}.choice-button.selected{border-color:var(--accent);color:#fff;background:var(--accent)}.choice-button.selected small{color:rgba(255,255,255,.85)}.intake-field input,.intake-field textarea{box-sizing:border-box;width:100%;padding:8px;color:var(--text);background:var(--input);border:1px solid var(--border-strong);border-radius:8px;font:11px/1.4 var(--font-sans);resize:vertical}.workspace-composer{display:grid;gap:7px;flex:none;margin-top:8px;padding-top:10px;border-top:1px solid var(--border)}.workspace-composer textarea{box-sizing:border-box;width:100%;min-height:70px;padding:10px;color:var(--text);background:var(--input);border:1px solid var(--border-strong);border-radius:11px;font:11px/1.45 var(--font-sans);resize:none}.workspace-composer div{display:flex;align-items:center;justify-content:space-between;gap:8px}.workspace-composer div>span{color:var(--text-muted);font-size:9px}.workspace-candidates{display:flex;flex-direction:column;overflow:hidden}.candidate-heading{flex:none;padding:16px 16px 12px}.candidate-heading h2 small{margin-left:5px;color:var(--text-muted);font:10px var(--font-mono);letter-spacing:0}.candidate-table{min-height:0;flex:1;overflow:auto}.candidate-table-head,.candidate-row{display:grid;grid-template-columns:minmax(150px,1fr) minmax(150px,1.05fr) 58px minmax(220px,1.55fr) 72px;gap:10px;align-items:center;min-width:760px}.candidate-table-head{position:sticky;top:0;z-index:1;padding:9px 16px;border-bottom:1px solid var(--border);color:var(--text-muted);background:var(--surface);font-size:9px}.candidate-row{width:100%;padding:9px 16px;border:0;border-bottom:1px solid var(--border);color:var(--text);background:transparent;text-align:left;cursor:pointer;transition:background .15s}.candidate-row:hover,.candidate-row.selected{background:var(--accent-subtle)}.candidate-name{display:flex;align-items:center;gap:8px;min-width:0}.candidate-name i{display:grid;place-items:center;flex:0 0 25px;width:25px;height:25px;border-radius:50%;color:var(--accent);background:var(--accent-subtle);font:700 9px var(--font-mono);font-style:normal}.candidate-name b,.candidate-role b,.candidate-role small,.candidate-reason{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.candidate-name b,.candidate-role b{color:var(--text-strong);font-size:11px}.candidate-role{display:grid;gap:2px;min-width:0}.candidate-role small{color:var(--text-muted);font-size:9px}.candidate-score{color:var(--success);font:700 11px var(--font-mono)}.candidate-score.empty{color:var(--text-muted)}.candidate-reason{color:var(--text-muted);font-size:9px;line-height:1.35}.candidate-sources{display:flex;gap:3px}.candidate-sources i{display:grid;place-items:center;width:18px;height:18px;overflow:hidden;border-radius:50%;color:var(--accent);background:var(--accent-subtle);font:700 8px var(--font-mono);font-style:normal}.empty-candidates,.empty-detail{display:grid;place-items:center;align-content:center;gap:8px;min-height:250px;padding:20px;color:var(--text-muted);text-align:center}.empty-candidates>span,.empty-detail>span{color:var(--accent);font-size:28px}.empty-candidates strong,.empty-detail strong{color:var(--text-strong);font-size:13px}.empty-candidates p,.empty-detail p{max-width:270px;margin:0;font-size:10px;line-height:1.55}.workspace-detail{display:flex;flex-direction:column;overflow:auto;padding:16px}.detail-heading{flex:none}.profile-header{display:flex;gap:10px;align-items:center;padding:15px 0;border-bottom:1px solid var(--border)}.profile-header>i{display:grid;place-items:center;flex:0 0 48px;width:48px;height:48px;border-radius:50%;color:var(--accent);background:var(--accent-subtle);font:700 16px var(--font-mono);font-style:normal}.profile-header h3{margin:0;color:var(--text-strong);font-size:17px}.profile-header p,.profile-header small{margin:4px 0 0;color:var(--text-muted);font-size:10px;line-height:1.4}.profile-header small{color:var(--success)}.profile-actions{display:flex;align-items:center;gap:8px;padding:12px 0;border-bottom:1px solid var(--border)}.profile-actions .primary-button{flex:1;min-height:34px;font-size:11px}.profile-actions>span{color:var(--success);font:10px var(--font-mono);white-space:nowrap}.detail-block{padding:13px 0;border-bottom:1px solid var(--border)}.detail-block h4,.workspace-feedback h4{margin:0 0 7px;color:var(--text-muted);font:10px var(--font-mono);letter-spacing:.06em;text-transform:uppercase}.detail-block p{margin:0;color:var(--text);font-size:11px;line-height:1.55}.source-tags{display:flex;gap:5px;flex-wrap:wrap}.source-tags span{padding:4px 6px;border:1px solid var(--border);border-radius:99px;color:var(--text-muted);font-size:9px}.profile-data pre{max-height:240px;overflow:auto;margin:0;padding:9px;border-radius:8px;color:var(--text);background:var(--surface-raised);font:9px/1.45 var(--font-mono);white-space:pre-wrap}.workspace-feedback{display:grid;gap:8px;padding:13px 0}.workspace-feedback>div{display:flex;align-items:center;justify-content:space-between;gap:8px}.workspace-feedback h4{margin:0}.workspace-feedback select,.workspace-feedback textarea{box-sizing:border-box;color:var(--text);background:var(--input);border:1px solid var(--border);border-radius:8px;font:10px/1.45 var(--font-sans)}.workspace-feedback select{padding:5px}.workspace-feedback textarea{width:100%;padding:8px;resize:vertical}.workspace-feedback .secondary-button{min-height:32px;font-size:10px}.muted{color:var(--text-muted);font-size:10px}@keyframes pulse{50%{opacity:.35;transform:scale(.78)}}@media(max-width:1280px){.dinq-workspace,.dinq-workspace.sessions-expanded{grid-template-columns:48px minmax(320px,.9fr) minmax(480px,1.4fr)}.workspace-detail{grid-column:2/-1;max-height:390px}.workspace-candidates{min-height:620px}}@media(max-width:900px){.dinq-run-page{min-height:calc(100vh - var(--topbar-height));padding:20px;overflow:visible}.workspace-heading{align-items:flex-start;flex-direction:column}.dinq-workspace,.dinq-workspace.sessions-expanded{display:grid;grid-template-columns:1fr;min-height:0}.workspace-sessions{display:none}.workspace-dialogue{min-height:680px}.workspace-candidates{min-height:500px}.workspace-detail{grid-column:auto;max-height:none}.candidate-table-head,.candidate-row{grid-template-columns:minmax(150px,1fr) minmax(130px,1fr) 58px minmax(200px,1.4fr) 56px}}@media(max-width:560px){.dinq-run-page{padding:16px}.workspace-heading h1{font-size:23px}.heading-actions{width:100%}.heading-actions .secondary-button{flex:1;padding:0 10px;font-size:11px}.workspace-dialogue,.workspace-detail{padding:13px}.candidate-heading{padding:13px}.run-stats{gap:5px}.candidate-table-head,.candidate-row{min-width:670px}.intake-card{max-height:54%}}
</style>
<style scoped>
 .confirmation-copy{margin:0;padding:8px 9px;border-radius:8px;color:var(--text);background:var(--surface-raised);font-size:10px;line-height:1.45}
/* Product workspace: history and profile are contextual drawers, so the
   working surface is always a spacious conversation + result table. */
.dinq-workspace{position:relative;grid-template-columns:minmax(370px,.72fr) minmax(620px,1.42fr);gap:12px;overflow:hidden}
.drawer-scrim{position:absolute;z-index:9;inset:0;width:100%;border:0;background:rgba(24,27,32,.16);cursor:default}
.workspace-sessions{position:absolute;z-index:10;inset:0 auto 0 0;width:276px;padding:14px;box-shadow:18px 0 42px rgba(15,20,30,.18);animation:drawer-enter-left .18s ease-out}
.session-toolbar>div{display:flex;gap:5px}.workspace-sessions .session-list{max-height:calc(100% - 46px);overflow:auto}.workspace-detail{position:absolute;z-index:11;inset:0 0 0 auto;width:min(430px,38%);padding:18px;background:var(--surface);box-shadow:-18px 0 42px rgba(15,20,30,.16);animation:drawer-enter-right .18s ease-out}
.workspace-candidates{min-width:0}.candidate-table-head,.candidate-row{grid-template-columns:minmax(156px,.95fr) minmax(170px,1.05fr) 62px minmax(260px,1.65fr) 76px;min-width:790px}
.workspace-dialogue{min-width:0}.dialogue-scroll{padding-right:4px}.workspace-composer{background:var(--surface)}
@keyframes drawer-enter-left{from{opacity:0;transform:translateX(-16px)}to{opacity:1;transform:translateX(0)}}
@keyframes drawer-enter-right{from{opacity:0;transform:translateX(16px)}to{opacity:1;transform:translateX(0)}}
@media(max-width:1280px){.dinq-workspace{grid-template-columns:minmax(330px,.78fr) minmax(540px,1.35fr)}.workspace-detail{width:min(410px,48%)}}
@media(max-width:900px){.dinq-workspace{display:grid;grid-template-columns:1fr;overflow:visible}.workspace-candidates{min-height:500px}.workspace-sessions{position:fixed;z-index:30;inset:var(--topbar-height,0) auto 0 0;width:min(310px,86vw)}.drawer-scrim{position:fixed;z-index:29}.workspace-detail{position:fixed;z-index:31;inset:var(--topbar-height,0) 0 0 auto;width:min(410px,92vw);border-radius:0;box-shadow:-18px 0 42px rgba(15,20,30,.22)}}
</style>
