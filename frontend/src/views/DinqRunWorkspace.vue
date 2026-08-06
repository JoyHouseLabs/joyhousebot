<template>
  <div class="page dinq-run-page">
    <header class="workspace-heading">
      <div>
        <span class="eyebrow">DINQ SEARCH WORKSPACE</span>
        <h1>{{ projection?.search.query || 'Dinq 人才搜索' }}</h1>
        <p>先补足查询条件，再开始搜索；过程、候选人和富化档案始终保留在同一会话。</p>
      </div>
      <div class="heading-actions">
        <span v-if="projection" :class="['status-pill', projection.search.status]">{{ statusLabel(projection.search.status) }}</span>
        <button class="secondary-button" type="button" @click="startNewSearch">新的搜索</button>
        <router-link class="secondary-button" :to="{ path: '/runs', query: { run: runId } }">通用诊断</router-link>
      </div>
    </header>

    <div v-if="error" class="notice error-notice">{{ error }}</div>
    <section v-if="loading && !projection" class="empty-state panel">正在读取 Dinq 搜索工作区…</section>
    <section v-else-if="projection" :class="['dinq-workspace', { 'sessions-collapsed': sessionsCollapsed }]">
      <aside class="workspace-sessions panel">
        <div class="section-heading">
          <div v-if="!sessionsCollapsed"><span class="eyebrow">SESSIONS</span><h2>历史会话</h2></div>
          <div class="session-actions"><button v-if="!sessionsCollapsed" class="icon-button" title="刷新会话" @click="loadSessions">↻</button><button class="icon-button" :title="sessionsCollapsed ? '展开历史会话' : '收起历史会话'" @click="sessionsCollapsed = !sessionsCollapsed">{{ sessionsCollapsed ? '›' : '‹' }}</button></div>
        </div>
        <div v-if="!sessionsCollapsed" class="session-list">
          <button v-for="session in sessions" :key="session.key" :class="['session-item', { active: session.latest_run_id === runId }]" @click="openSession(session.latest_run_id)">
            <strong>{{ sessionLabel(session.key) }}</strong><small>{{ statusLabel(session.latest_status || '') }} · {{ formatDate(session.updated_at) }}</small>
          </button>
          <p v-if="!sessions.length" class="muted">暂无历史会话</p>
        </div>
      </aside>

      <section class="workspace-dialogue panel">
        <div class="section-heading dialogue-heading"><div><span class="eyebrow">SEARCH CONVERSATION</span><h2>搜索与条件确认</h2></div><span class="metric">{{ projection.search.tool_calls }} 次工具调用</span></div>
        <div ref="dialogueScroll" class="dialogue-scroll">
          <div class="run-summary">
            <div><span>状态</span><strong>{{ statusLabel(projection.search.status) }}</strong></div>
            <div><span>候选人</span><strong>{{ projection.search.total_candidates }}</strong></div>
            <div><span>已验证</span><strong>{{ projection.search.verified_candidates }}</strong></div>
          </div>

          <section class="thought-panel">
            <button class="activity-toggle" type="button" :aria-expanded="activityExpanded" @click="toggleActivity">
              <span><i :class="['activity-toggle-dot', { active: !isTerminalStatus(projection.search.status) }]" />{{ activityExpanded ? 'Thought / 搜索过程' : 'Thought / 搜索过程（已收起）' }}</span>
              <small>{{ projection.activity.length }} 个事件 · {{ activityExpanded ? '收起 ⌃' : '展开 ⌄' }}</small>
            </button>
            <div v-show="activityExpanded" class="activity-list">
              <article v-for="item in projection.activity" :key="`${item.sequence}-${item.event_id}`" :class="['activity-item', item.status || '']">
                <span class="activity-dot" /><div><strong>{{ activityLabel(item) }}</strong><small>{{ item.phase || '运行' }} · {{ formatDate(item.created_at) }}</small><p v-if="item.summary && item.summary !== activityLabel(item)">{{ item.summary }}</p></div>
              </article>
              <p v-if="!projection.activity.length" class="muted">等待执行事件…</p>
            </div>
          </section>

          <div v-if="conversation.length" class="conversation-list">
            <article v-for="(message, index) in conversation" :key="`${message.run_id || 'message'}-${index}`" :class="['conversation-message', message.role]">
              <span class="message-role">{{ message.role === 'user' ? 'YOU' : 'DINQ' }}</span>
              <MarkdownContent :content="message.content" />
            </article>
          </div>

          <section v-if="pendingRequest" class="intake-card">
            <span class="eyebrow">COMPLETE SEARCH BRIEF</span>
            <div class="intake-title"><h3>{{ pendingRequest.question }}</h3><span v-if="questionProgress(pendingRequest)" class="metric">{{ questionProgress(pendingRequest) }}</span></div>
            <p v-if="pendingRequest.presentation?.help_text" class="intake-help">{{ pendingRequest.presentation.help_text }}</p>
            <form class="intake-fields" @submit.prevent="submitAnswers">
              <label v-for="field in pendingRequest.fields" :key="field.name" class="intake-field">
                <span>{{ field.description || field.name }}<b v-if="field.required"> *</b></span>
                <div v-if="inputMode(field) === 'single_choice'" class="choice-list">
                  <button v-for="option in inputOptions(field)" :key="option.value" type="button" :class="['choice-button', { selected: String(answerValues[field.name] || '') === option.value }]" @click="answerValues[field.name] = option.value">{{ option.label }}</button>
                  <input v-if="field.allow_other" v-model="otherValues[field.name]" type="text" placeholder="输入其他选项…">
                </div>
                <div v-else-if="inputMode(field) === 'multi_choice'" class="choice-list">
                  <button v-for="option in inputOptions(field)" :key="option.value" type="button" :class="['choice-button', { selected: selectedChoices(field).includes(option.value) }]" @click="toggleChoice(field, option.value)">{{ option.label }}</button>
                  <input v-if="field.allow_other" v-model="otherValues[field.name]" type="text" placeholder="输入其他选项…">
                </div>
                <textarea v-else-if="inputMode(field) === 'textarea'" v-model="answerValues[field.name]" rows="3" :placeholder="field.description || '请输入…'" />
                <input v-else :type="inputMode(field) === 'number' ? 'number' : 'text'" v-model="answerValues[field.name]" :placeholder="field.description || '请输入…'">
              </label>
              <button class="primary-button" type="submit" :disabled="answering">{{ answering ? '提交中…' : '确认并继续' }}</button>
            </form>
          </section>

          <section v-if="isTerminalStatus(projection.search.status)" class="run-result">
            <span class="eyebrow">SEARCH RESULT</span><strong>{{ statusLabel(projection.search.status) }} · {{ projection.search.total_candidates }} 位候选人</strong>
            <p>{{ projection.search.summary || `已完成 ${projection.search.tool_calls} 次工具调用，候选人列表已更新。` }}</p>
          </section>
        </div>

        <form class="workspace-composer" @submit.prevent="continueRun">
          <span class="composer-kicker">{{ pendingRequest ? '请先完成上方的结构化查询条件' : '继续当前搜索会话' }}</span>
          <textarea v-model="composerInput" rows="2" :placeholder="pendingRequest ? '完成条件确认后可继续补充搜索约束…' : '继续追问、补充约束，或让 Agent 深入富化候选人…'" :disabled="sending || Boolean(pendingRequest)" @keydown.enter.exact.prevent="continueRun" />
          <div><span>Enter 发送 · Shift + Enter 换行</span><button class="primary-button" type="submit" :disabled="sending || Boolean(pendingRequest) || !composerInput.trim()">{{ sending ? '提交中…' : '继续执行 ↑' }}</button></div>
        </form>
      </section>

      <section class="workspace-candidates panel">
        <div class="section-heading"><div><span class="eyebrow">CANDIDATES</span><h2>候选人</h2></div><span class="metric">{{ projection.candidates.length }} 命中</span></div>
        <div class="candidate-list">
          <button v-for="candidate in projection.candidates" :key="candidate.candidate_id" :class="['candidate-row', { selected: candidate.candidate_id === selectedId }]" @click="selectCandidate(candidate.candidate_id)">
            <span class="avatar">{{ initials(candidate.name) }}</span><span class="candidate-main"><strong>{{ candidate.name }}</strong><small>{{ candidate.title || '未声明职位' }}<template v-if="candidate.company"> · {{ candidate.company }}</template></small><em>{{ candidate.match_reasons[0] || '已通过来源匹配' }}</em></span><span v-if="candidate.match_score != null" class="score">{{ Math.round(candidate.match_score * (candidate.match_score <= 1 ? 100 : 1)) }}%</span>
          </button>
          <div v-if="!projection.candidates.length" class="empty-candidates"><strong>{{ pendingRequest ? '正在确认搜索条件' : '等待候选人产物' }}</strong><p>{{ pendingRequest ? '确认上方问题后会自动开始搜索。' : '运行完成后，候选人集合会写入 Artifact 并自动呈现。' }}</p></div>
        </div>
      </section>

      <aside class="workspace-detail panel">
        <div class="section-heading"><div><span class="eyebrow">ENRICHMENT</span><h2>候选人档案</h2></div><span v-if="selectedCandidate" :class="['enrichment-state', selectedCandidate.enrichment_status]">{{ enrichmentLabel(selectedCandidate.enrichment_status) }}</span></div>
        <template v-if="selectedCandidate">
          <div class="profile-header"><span class="profile-avatar">{{ initials(selectedCandidate.name) }}</span><div><h3>{{ selectedCandidate.name }}</h3><p>{{ selectedCandidate.title || '未声明职位' }}<template v-if="selectedCandidate.company"> · {{ selectedCandidate.company }}</template></p></div></div>
          <button class="primary-button enrich-button" :disabled="enriching" @click="enrichCandidate">{{ enriching ? '正在创建富化任务…' : '富化此候选人' }}</button>
          <div class="detail-block"><span class="detail-label">命中原因</span><ul><li v-for="reason in selectedCandidate.match_reasons" :key="reason">{{ reason }}</li><li v-if="!selectedCandidate.match_reasons.length">暂无结构化原因</li></ul></div>
          <div class="detail-block"><span class="detail-label">来源</span><div class="tag-list"><span v-for="source in selectedCandidate.sources" :key="String(source)">{{ sourceLabel(source) }}</span><span v-if="!selectedCandidate.sources.length">暂无来源</span></div></div>
          <div class="detail-block"><span class="detail-label">当前档案</span><pre v-if="selectedCandidate.enrichment || selectedCandidate.profile">{{ pretty(selectedCandidate.enrichment || selectedCandidate.profile) }}</pre><p v-else class="muted">尚未生成富化档案；可点击“富化此候选人”创建独立、可回放的核验 Run。</p></div>
          <form class="workspace-feedback" @submit.prevent="submitFeedback"><div class="feedback-heading"><span class="detail-label">人工反馈</span><select v-model="feedbackType"><option value="incorrect">结果不对</option><option value="missing_data">缺少数据</option><option value="needs_optimization">需要优化</option><option value="helpful">很有帮助</option></select></div><textarea v-model="feedbackComment" rows="3" placeholder="指出这个候选人或富化结果需要改进的地方…" :disabled="feedbackSent" /><button class="secondary-button" type="submit" :disabled="feedbackSaving || feedbackSent || !feedbackComment.trim()">{{ feedbackSent ? '✓ 已记录反馈' : feedbackSaving ? '提交中…' : '提交反馈到 Run' }}</button></form>
        </template>
        <div v-else class="empty-detail"><span class="profile-placeholder">◇</span><strong>选择一个候选人</strong><p>点击中间列表查看命中原因与档案；需要更多资料时可直接创建富化任务。</p></div>
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

const route = useRoute(); const router = useRouter(); const runId = ref(String(route.params.runId))
const projection = ref<DinqRunProjection | null>(null); const sessions = ref<SessionItem[]>([]); const conversation = ref<SessionHistoryMessage[]>([]); const pendingInputs = ref<PendingRunInput[]>([]); const answerValues = ref<Record<string, any>>({}); const otherValues = ref<Record<string, string>>({}); const selectedId = ref<string | null>(null); const loading = ref(false); const error = ref(''); const composerInput = ref(''); const sending = ref(false); const answering = ref(false); const enriching = ref(false); const feedbackType = ref<RunFeedbackType>('needs_optimization'); const feedbackComment = ref(''); const feedbackSaving = ref(false); const feedbackSent = ref(false); const activityExpanded = ref(true); const activityPreference = ref<boolean | null>(null); const sessionsCollapsed = ref(true); const dialogueScroll = ref<HTMLElement | null>(null)
const pendingRequest = computed(() => pendingInputs.value[0] || null)
const selectedCandidate = computed<DinqCandidate | null>(() => projection.value?.selected_candidate || projection.value?.candidates.find((item) => item.candidate_id === selectedId.value) || null)
let abortController: AbortController | null = null; let refreshTimer: number | null = null

function initialiseAnswers(request?: PendingRunInput | null) { answerValues.value = Object.fromEntries((request?.fields || []).map((field) => [field.name, inputMode(field) === 'multi_choice' ? [] : field.value_type === 'boolean' ? false : ''])); otherValues.value = {} }
async function load(candidateId = selectedId.value) { loading.value = true; error.value = ''; try { const firstLoad = !projection.value; projection.value = await getDinqRunProjection(runId.value, candidateId); selectedId.value = projection.value.selected_candidate_id || selectedId.value || projection.value.candidates[0]?.candidate_id || null; pendingInputs.value = projection.value.search.status === 'waiting_input' ? await getPendingRunInputs(runId.value) : []; initialiseAnswers(pendingInputs.value[0]); if (firstLoad && activityPreference.value === null) activityExpanded.value = true; await loadConversation() } catch (cause) { error.value = cause instanceof Error ? cause.message : '读取 Dinq 工作区失败' } finally { loading.value = false; await scrollDialogue() } }
async function loadConversation() { if (!projection.value?.session.session_id) { conversation.value = []; return } try { conversation.value = (await getSessionHistory(projection.value.session.session_id, projection.value.session.agent_id)).messages } catch { conversation.value = [] } }
async function loadSessions() { try { sessions.value = (await getSessions(projection.value?.session.agent_id)).sessions } catch { /* The session rail is supplementary. */ } }
function selectCandidate(id: string) { selectedId.value = id; feedbackSent.value = false; feedbackComment.value = ''; void load(id) }
async function submitFeedback() { if (!feedbackComment.value.trim() || feedbackSaving.value) return; feedbackSaving.value = true; try { await createRunFeedback(runId.value, { feedback_type: feedbackType.value, comment: feedbackComment.value.trim(), output_excerpt: selectedCandidate.value ? pretty(selectedCandidate.value).slice(0, 4000) : undefined }); feedbackSent.value = true } catch (cause) { error.value = cause instanceof Error ? cause.message : '提交反馈失败' } finally { feedbackSaving.value = false } }
function openSession(id?: string) { if (id && id !== runId.value) void router.push(`/dinq/runs/${encodeURIComponent(id)}`) }
function startNewSearch() { void router.push({ name: 'Chat', query: { agent: projection.value?.session.agent_id || 'main-coordinator', session: `ui:dinq-${Date.now()}`, plugin: 'dinq', scenario: 'dinq.discover.search', workspace: 'new' } }) }
function toggleActivity() { activityExpanded.value = !activityExpanded.value; activityPreference.value = activityExpanded.value }
async function continueRun() { const prompt = composerInput.value.trim(); if (!prompt || !projection.value || sending.value || pendingRequest.value) return; sending.value = true; error.value = ''; try { const run = await submitRuntimeRun({ prompt, sessionId: projection.value.session.session_id || `ui:dinq-${Date.now()}`, agentId: projection.value.session.agent_id || 'main-coordinator', scenarioId: 'dinq.discover.search', channel: 'web-playground', chatId: projection.value.session.session_id || undefined }); composerInput.value = ''; await router.push(`/dinq/runs/${encodeURIComponent(run.run_id)}`) } catch (cause) { error.value = cause instanceof Error ? cause.message : '提交继续执行失败' } finally { sending.value = false } }
async function enrichCandidate() { const candidate = selectedCandidate.value; if (!candidate || !projection.value || enriching.value) return; enriching.value = true; try { const run = await submitRuntimeRun({ prompt: `请核验并富化候选人：${candidate.name}`, sessionId: projection.value.session.session_id || `ui:dinq-${Date.now()}`, agentId: projection.value.session.agent_id || 'main-coordinator', scenarioId: 'dinq.candidate.enrich', scenarioInputs: { identifier: candidate.candidate_id || candidate.name }, channel: 'web-playground', chatId: projection.value.session.session_id || undefined }); await router.push(`/dinq/runs/${encodeURIComponent(run.run_id)}`) } catch (cause) { error.value = cause instanceof Error ? cause.message : '创建候选人富化任务失败' } finally { enriching.value = false } }
function inputMode(field: RunInputField) { if (field.input_mode && field.input_mode !== 'auto') return field.input_mode; return field.value_type === 'array' ? 'multi_choice' : field.options?.length || field.enum?.length ? 'single_choice' : field.value_type === 'number' || field.value_type === 'integer' ? 'number' : 'text' }
function inputOptions(field: RunInputField): Array<{ value: string; label: string; description?: string }> { return field.options?.length ? field.options : (field.enum || []).map((value) => ({ value: String(value), label: String(value) })) }
function selectedChoices(field: RunInputField) { const value = answerValues.value[field.name]; return Array.isArray(value) ? value.map(String) : [] }
function toggleChoice(field: RunInputField, value: string) { const selected = selectedChoices(field); const maximum = Number(field.max_selections || 0); answerValues.value[field.name] = selected.includes(value) ? selected.filter((item) => item !== value) : maximum > 0 && selected.length >= maximum ? selected : [...selected, value] }
function questionProgress(request: PendingRunInput) { const progress = request.presentation?.progress; const current = Number(progress?.current || 0); const total = Number(progress?.total || 0); return current > 0 && total > 0 ? `问题 ${current} / ${total}` : '' }
async function submitAnswers() { const request = pendingRequest.value; if (!request || answering.value) return; answering.value = true; try { const answers: Record<string, unknown> = {}; for (const field of request.fields) { const raw = answerValues.value[field.name]; const other = otherValues.value[field.name]?.trim(); if (inputMode(field) === 'multi_choice') answers[field.name] = [...selectedChoices(field), ...(other ? [other] : [])]; else if (inputMode(field) === 'single_choice' && other) answers[field.name] = other; else if (field.value_type === 'integer') answers[field.name] = Number.parseInt(String(raw), 10); else if (field.value_type === 'number') answers[field.name] = Number(raw); else answers[field.name] = raw } const result = await resolveRunInput(runId.value, request.input_request_id, answers); pendingInputs.value = result.pending_inputs; initialiseAnswers(pendingInputs.value[0]); await load(); if (!pendingInputs.value.length && !isTerminalStatus(result.run.status)) void startStream() } catch (cause) { error.value = cause instanceof Error ? cause.message : '提交条件失败' } finally { answering.value = false } }
function sessionLabel(value: string) { const text = value.replace(/^ui:/, ''); return text.length > 24 ? `${text.slice(0, 24)}…` : text }
function statusLabel(value: string) { return ({ completed: '已完成', failed: '失败', running: '执行中', queued: '排队中', waiting_input: '等待确认', cancelled: '已取消' } as Record<string, string>)[value] || value || '—' }
function isTerminalStatus(value: string) { return ['completed', 'failed', 'cancelled', 'timed_out'].includes(value) }
function enrichmentLabel(value: string) { return ({ not_requested: '未富化', ready: '已富化', verified: '已验证', completed: '已完成', failed: '失败' } as Record<string, string>)[value] || value }
function activityLabel(item: DinqActivity) { return ({ 'run.created': '请求已接受', 'run.queued': '任务已进入执行队列', 'run.started': '开始执行任务', 'run.completed': '任务执行完成', 'run.failed': '任务执行失败', 'task.created': '子任务已创建', 'task.started': '子任务开始执行', 'task.completed': '子任务执行完成', 'capability.started': '开始调用能力', 'capability.completed': '能力调用完成' } as Record<string, string>)[item.type] || item.summary || item.type }
function formatDate(value?: string | null) { return value ? new Date(value).toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit', second: '2-digit' }) : '—' }
function initials(value: string) { return value.split(/\s+/).map((item) => item[0]).join('').slice(0, 2).toUpperCase() || 'D' }
function sourceLabel(value: unknown) { if (typeof value === 'string') return value.replace(/^https?:\/\//, '').split('/')[0]; if (value && typeof value === 'object') return String((value as Record<string, unknown>).source || (value as Record<string, unknown>).name || 'source'); return String(value) }
function pretty(value: unknown) { return JSON.stringify(value, null, 2) }
async function scrollDialogue() { await nextTick(); const element = dialogueScroll.value; if (element) element.scrollTop = element.scrollHeight }
async function startStream() { abortController?.abort(); abortController = new AbortController(); try { await streamRuntimeEvents(runId.value, () => { if (refreshTimer) window.clearTimeout(refreshTimer); refreshTimer = window.setTimeout(() => { void load(selectedId.value) }, 120) }, { afterSequence: projection.value?.events_cursor || 0, signal: abortController.signal }) } catch { /* durable stream reconnect happens on the next active view */ } }
onMounted(async () => { await load(); await loadSessions(); if (projection.value && !isTerminalStatus(projection.value.search.status) && projection.value.search.status !== 'waiting_input') void startStream() })
onBeforeRouteUpdate(async (to) => { const nextRunId = String(to.params.runId); if (!nextRunId || nextRunId === runId.value) return; abortController?.abort(); if (refreshTimer) window.clearTimeout(refreshTimer); runId.value = nextRunId; projection.value = null; conversation.value = []; pendingInputs.value = []; selectedId.value = null; activityPreference.value = null; activityExpanded.value = true; feedbackSent.value = false; feedbackComment.value = ''; await load(); await loadSessions(); const nextProjection = projection.value as DinqRunProjection | null; if (nextProjection && !isTerminalStatus(nextProjection.search.status) && nextProjection.search.status !== 'waiting_input') void startStream() })
onUnmounted(() => { abortController?.abort(); if (refreshTimer) window.clearTimeout(refreshTimer) })
</script>

<style scoped>
.dinq-run-page{display:flex;flex-direction:column;gap:14px;height:calc(100vh - var(--topbar-height));min-height:0;box-sizing:border-box;overflow:hidden}.workspace-heading{display:flex;justify-content:space-between;align-items:flex-start;gap:20px}.workspace-heading h1{margin:7px 0 5px;color:var(--text-strong);font-size:27px}.workspace-heading p{margin:0;color:var(--text-muted);font-size:12px}.heading-actions{display:flex;align-items:center;gap:8px;flex-wrap:wrap}.status-pill,.enrichment-state{padding:6px 9px;border-radius:999px;background:var(--surface-raised);color:var(--text-muted);font-size:11px}.status-pill.completed,.enrichment-state.ready,.enrichment-state.verified,.enrichment-state.completed{color:var(--success);background:var(--success-subtle)}.status-pill.failed,.enrichment-state.failed{color:var(--danger);background:var(--danger-subtle)}.dinq-workspace{display:grid;grid-template-columns:190px minmax(330px,.95fr) minmax(340px,1.12fr) minmax(280px,.88fr);gap:10px;flex:1;min-height:0}.panel{min-height:0;padding:14px;border:1px solid var(--border);border-radius:12px;background:var(--surface)}.section-heading{display:flex;justify-content:space-between;align-items:flex-start;gap:8px;padding-bottom:11px;border-bottom:1px solid var(--border)}.section-heading h2{margin:4px 0 0;color:var(--text-strong);font-size:16px}.metric{color:var(--text-muted);font:10px var(--font-mono)}.workspace-sessions,.workspace-candidates,.workspace-detail{overflow:auto}.session-actions{display:flex;align-items:center;gap:2px}.session-list,.candidate-list{display:grid;gap:6px;margin-top:10px}.session-item,.candidate-row{width:100%;padding:9px;border:1px solid transparent;border-radius:9px;background:transparent;color:var(--text);text-align:left;cursor:pointer}.session-item:hover,.candidate-row:hover,.session-item.active,.candidate-row.selected{border-color:var(--accent-border);background:var(--accent-subtle)}.session-item{display:grid;gap:4px}.session-item strong{overflow:hidden;color:var(--text-strong);font-size:11px;text-overflow:ellipsis;white-space:nowrap}.session-item small,.candidate-main small,.candidate-main em,.activity-item small,.activity-item p{color:var(--text-muted);font-size:10px}.workspace-dialogue{display:flex;flex-direction:column;overflow:hidden}.dialogue-scroll{flex:1;min-height:0;overflow:auto;padding:0 2px 12px}.run-summary{display:grid;grid-template-columns:repeat(3,1fr);gap:7px;margin:12px 0}.run-summary div{display:grid;gap:4px;padding:8px;background:var(--surface-raised);border-radius:8px}.run-summary span{color:var(--text-muted);font-size:10px}.run-summary strong{color:var(--text-strong);font-size:13px}.thought-panel{margin-bottom:12px;border:1px solid var(--border);border-radius:10px;background:var(--surface-raised);overflow:hidden}.activity-toggle{display:flex;justify-content:space-between;align-items:center;gap:10px;width:100%;padding:9px 11px;color:var(--text-strong);background:transparent;border:0;text-align:left;cursor:pointer}.activity-toggle>span{display:flex;align-items:center;gap:8px;font-size:11px}.activity-toggle small{color:var(--text-muted);font:10px var(--font-mono)}.activity-toggle-dot{width:7px;height:7px;border-radius:50%;background:var(--success)}.activity-toggle-dot.active{animation:workspace-pulse 1.4s ease-in-out infinite}.activity-list{max-height:235px;overflow:auto;padding:0 11px}.activity-item{display:flex;gap:8px;padding:6px 0;border-top:1px solid var(--border)}.activity-dot{flex:0 0 8px;width:8px;height:8px;margin-top:3px;border-radius:50%;background:var(--warning)}.activity-item.completed .activity-dot,.activity-item.succeeded .activity-dot{background:var(--success)}.activity-item.failed .activity-dot{background:var(--danger)}.activity-item>div{min-width:0}.activity-item strong{display:block;color:var(--text-strong);font-size:11px}.activity-item p{margin:2px 0 0;line-height:1.35}.conversation-list{display:grid;gap:9px}.conversation-message{padding:9px 11px;border-radius:10px;font-size:12px;line-height:1.55}.conversation-message.user{margin-left:22px;background:var(--accent-subtle)}.conversation-message.assistant{margin-right:12px;background:var(--surface-raised)}.message-role{display:block;margin-bottom:4px;color:var(--text-muted);font:9px var(--font-mono);letter-spacing:.08em}.intake-card{margin:10px 0;padding:13px;border:1px solid var(--accent-border);border-radius:11px;background:var(--accent-subtle)}.intake-title{display:flex;justify-content:space-between;gap:10px;align-items:flex-start}.intake-title h3{margin:6px 0;color:var(--text-strong);font-size:15px;line-height:1.4}.intake-help{margin:0 0 10px;color:var(--text-muted);font-size:11px;line-height:1.5}.intake-fields{display:grid;gap:10px}.intake-field{display:grid;gap:6px;color:var(--text-strong);font-size:11px}.intake-field b{color:var(--danger)}.choice-list{display:flex;gap:6px;flex-wrap:wrap}.choice-button{padding:7px 9px;border:1px solid var(--border-strong);border-radius:999px;background:var(--surface);color:var(--text);font-size:11px;cursor:pointer}.choice-button.selected{border-color:var(--accent);background:var(--accent);color:var(--accent-contrast,#fff)}.intake-field input,.intake-field textarea{box-sizing:border-box;width:100%;padding:8px;color:var(--text);background:var(--input);border:1px solid var(--border-strong);border-radius:8px;font:11px/1.5 var(--font-sans);resize:vertical}.run-result{margin:10px 0;padding:11px;border:1px solid var(--border);border-radius:10px;background:var(--surface-raised)}.run-result .eyebrow{display:block;margin-bottom:4px;color:var(--success);font-size:9px}.run-result strong{color:var(--text-strong);font-size:12px}.run-result p{margin:4px 0 0;color:var(--text-muted);font-size:11px;line-height:1.45}.workspace-composer{display:grid;gap:6px;flex:none;padding-top:10px;border-top:1px solid var(--border);background:var(--surface)}.composer-kicker{color:var(--text-muted);font:9px var(--font-mono);letter-spacing:.08em;text-transform:uppercase}.workspace-composer textarea{width:100%;box-sizing:border-box;resize:none;padding:10px;color:var(--text);background:var(--input);border:1px solid var(--border-strong);border-radius:10px;font:12px/1.45 var(--font-sans)}.workspace-composer div{display:flex;justify-content:space-between;align-items:center;gap:8px}.workspace-composer div>span{color:var(--text-muted);font-size:10px}.candidate-list{margin-top:10px}.candidate-row{display:flex;align-items:center;gap:8px}.avatar,.profile-avatar{display:grid;place-items:center;flex:0 0 29px;width:29px;height:29px;border-radius:50%;background:var(--accent-subtle);color:var(--accent);font:bold 10px var(--font-mono)}.candidate-main{display:grid;min-width:0;flex:1;gap:2px}.candidate-main strong,.candidate-main small,.candidate-main em{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.candidate-main strong{color:var(--text-strong);font-size:12px}.candidate-main em{font-style:normal;color:var(--accent)}.score{color:var(--success);font:11px var(--font-mono)}.empty-candidates,.empty-detail{display:grid;place-items:center;gap:8px;padding:50px 15px;text-align:center}.empty-candidates strong,.empty-detail strong{color:var(--text-strong);font-size:13px}.empty-candidates p,.empty-detail p{margin:0;color:var(--text-muted);font-size:11px;line-height:1.5}.profile-header{display:flex;gap:10px;align-items:center;padding:13px 0;border-bottom:1px solid var(--border)}.profile-avatar{width:42px;height:42px;font-size:13px}.profile-header h3{margin:0;color:var(--text-strong);font-size:16px}.profile-header p{margin:4px 0 0;color:var(--text-muted);font-size:11px}.enrich-button{width:100%;margin:11px 0 0}.detail-block{padding:12px 0;border-bottom:1px solid var(--border)}.detail-label{display:block;margin-bottom:7px;color:var(--text-muted);font:10px var(--font-mono);text-transform:uppercase}.detail-block ul{margin:0;padding-left:17px;color:var(--text);font-size:11px;line-height:1.65}.tag-list{display:flex;gap:6px;flex-wrap:wrap}.tag-list span{padding:4px 7px;border:1px solid var(--border);border-radius:999px;color:var(--text-muted);font-size:10px}.detail-block pre{max-height:190px;overflow:auto;margin:0;padding:9px;background:var(--surface-raised);border-radius:8px;color:var(--text);font:10px/1.5 var(--font-mono);white-space:pre-wrap}.workspace-feedback{display:grid;gap:8px;padding-top:12px}.feedback-heading{display:flex;justify-content:space-between;align-items:flex-start;gap:8px}.feedback-heading .detail-label{margin:0}.workspace-feedback select,.workspace-feedback textarea{box-sizing:border-box;color:var(--text);background:var(--input);border:1px solid var(--border);border-radius:8px;font:11px/1.5 var(--font-sans)}.workspace-feedback select{padding:5px}.workspace-feedback textarea{width:100%;padding:8px;resize:vertical}.profile-placeholder{color:var(--accent);font-size:30px}.muted{color:var(--text-muted);font-size:11px}@keyframes workspace-pulse{50%{opacity:.35;transform:scale(.8)}}.dinq-workspace.sessions-collapsed{grid-template-columns:48px minmax(330px,.95fr) minmax(340px,1.12fr) minmax(280px,.88fr)}.sessions-collapsed .workspace-sessions{padding:9px 7px;overflow:hidden}.sessions-collapsed .workspace-sessions .section-heading{justify-content:center;border:0}.sessions-collapsed .workspace-sessions .session-actions{justify-content:center}@media(max-width:1250px){.dinq-workspace,.dinq-workspace.sessions-collapsed{grid-template-columns:48px minmax(320px,1fr) minmax(320px,1fr)}.workspace-detail{grid-column:2/-1;max-height:360px}}@media(max-width:850px){.dinq-run-page{height:auto;min-height:calc(100vh - var(--topbar-height));overflow:visible}.workspace-heading{flex-direction:column}.dinq-workspace,.dinq-workspace.sessions-collapsed{display:grid;grid-template-columns:1fr;flex:none}.workspace-sessions{display:none}.workspace-dialogue{min-height:650px}.workspace-detail{grid-column:auto;max-height:none}.workspace-candidates{max-height:500px}}
</style>
