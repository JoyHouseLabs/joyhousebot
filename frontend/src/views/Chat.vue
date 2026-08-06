<template>
  <div class="page playground-page">
    <header class="page-heading playground-heading">
      <div><span class="eyebrow">PLAYGROUND</span><h1>Agent 在线试用</h1><p>每条消息都会提交一个持久 Run；刷新页面后仍可恢复执行时间线。</p></div>
      <div class="heading-actions">
        <label class="inline-select"><span>Agent</span><select v-model="agentId" @change="changeAgent"><option v-for="agent in agents" :key="agent.id" :value="agent.id">{{ agent.name || agent.id }} · {{ agent.model }}</option></select></label>
        <button class="secondary-button" type="button" @click="newSession">{{ dinqMode ? '新的搜索' : '新会话' }}</button>
      </div>
    </header>

    <div class="playground-shell">
      <aside class="session-rail">
        <div class="rail-heading"><div><span class="eyebrow">SESSIONS</span><strong>会话</strong></div><button type="button" @click="loadSessions">↻</button></div>
        <button v-for="session in sessions" :key="session.key" type="button" class="session-item" :class="{ active: session.key === sessionId }" @click="selectSession(session.key)">
          <span>{{ session.key.replace(/^ui:/, '') }}</span><small>{{ session.latest_status || 'history' }} · {{ formatRelative(session.updated_at) }}</small>
        </button>
        <div v-if="!sessions.length" class="rail-empty">暂无历史会话</div>
      </aside>

      <section class="conversation-panel">
        <div class="conversation-topline">
          <div><span class="live-dot" :class="{ active: running }" /><strong>{{ currentAgent?.name || agentId || 'Agent' }}</strong><small>{{ currentAgent?.model || '未配置模型' }}</small></div>
          <div><code>{{ sessionId }}</code><button type="button" :disabled="running" @click="removeSession">删除</button></div>
        </div>

        <div ref="messageContainer" class="message-stream" @scroll="handleMessageScroll">
          <div v-if="!messages.length && !running" class="playground-welcome">
            <img :src="assistantAvatarUrl" alt="" />
            <span class="eyebrow">READY</span>
            <h2>把真实任务交给 Joyhousebot</h2>
            <p>支持工具调用、持久记忆、子 Agent 和并行 Task。执行过程会以安全摘要公开，不展示模型隐藏思维链。</p>
            <div class="prompt-suggestions"><button v-for="prompt in suggestions" :key="prompt" @click="input = prompt">{{ prompt }}</button></div>
          </div>

          <article v-for="messageItem in messages" :key="messageItem.id" class="message-block" :class="messageItem.role">
            <div class="message-author"><img v-if="messageItem.role === 'assistant'" :src="assistantAvatarUrl" alt="" /><span v-else>YOU</span></div>
            <div class="message-content"><strong>{{ messageItem.role === 'assistant' ? currentAgent?.name || 'Joyhousebot' : '你' }}</strong><MarkdownContent :content="messageItem.content" /><div v-if="messageItem.runId" class="message-feedback-toolbar"><button type="button" class="feedback-trigger" :class="{ submitted: feedbackSubmitted[messageItem.runId] }" @click="toggleFeedback(messageItem.runId)">{{ feedbackSubmitted[messageItem.runId] ? '✓ 已提交反馈' : '对这条输出反馈' }}</button><small>Run {{ shortId(messageItem.runId) }}</small></div><form v-if="messageItem.runId && feedbackOpen[messageItem.runId] && !feedbackSubmitted[messageItem.runId]" class="message-feedback-form" @submit.prevent="submitFeedback(messageItem)"><div class="feedback-type-list"><button v-for="item in feedbackTypes" :key="item.value" type="button" :class="{ selected: (feedbackType[messageItem.runId] || 'other') === item.value }" @click="feedbackType[messageItem.runId] = item.value">{{ item.label }}</button></div><textarea v-model="feedbackComment[messageItem.runId]" rows="3" required placeholder="指出不对的地方、缺少的数据或希望优化的方向…" /><div class="feedback-form-footer"><span>反馈会绑定到此 Run，并进入运行中心</span><button class="primary-button" type="submit" :disabled="feedbackSaving[messageItem.runId]">{{ feedbackSaving[messageItem.runId] ? '提交中…' : '提交反馈' }}</button></div></form></div>
          </article>

          <article v-if="running" class="message-block assistant active-run">
            <div class="message-author"><img :src="assistantAvatarUrl" alt="" /></div>
            <div class="message-content">
              <div class="active-run-heading"><strong>{{ currentAgent?.name || 'Joyhousebot' }}</strong><span>{{ latestRunSummary }}</span></div>
              <ExecutionTimeline :events="events" :active="true" />
              <div v-if="streaming !== null" class="streaming-answer"><span class="streaming-label">{{ streamingLabel }}</span><MarkdownContent :content="streaming" /><span class="cursor">▋</span></div>
              <div class="run-controls"><code v-if="runId">{{ runId }}</code><button type="button" @click="cancelRun">取消执行</button></div>
            </div>
          </article>

          <article v-if="pendingInputs.length" class="message-block assistant">
            <div class="message-author"><img :src="assistantAvatarUrl" alt="" /></div>
            <form class="message-content clarification-card" @submit.prevent="submitAnswers">
              <div class="clarification-heading"><div><span class="eyebrow">NEED INPUT</span><span class="clarification-kicker">补充信息后继续执行</span></div><small v-if="questionProgress(pendingInputs[0])">{{ questionProgress(pendingInputs[0]) }}</small></div>
              <div class="clarification-title"><h3>{{ pendingInputs[0].question }}</h3><p v-if="String(pendingInputs[0].presentation?.help_text || '')" class="clarification-help">{{ pendingInputs[0].presentation?.help_text }}</p></div>
              <div class="clarification-fields">
              <label v-for="field in pendingInputs[0].fields" :key="field.name"><span class="clarification-label"><strong>{{ field.description || field.name }}</strong><small v-if="field.required">必填</small></span>
                <div v-if="inputMode(field) === 'single_choice'" class="choice-list single"><button v-for="option in inputOptions(field)" :key="option.value" type="button" :class="{ selected: answerValues[field.name] === option.value }" @click="answerValues[field.name] = option.value"><strong>{{ option.label }}</strong><small v-if="option.description">{{ option.description }}</small></button></div>
                <div v-else-if="inputMode(field) === 'multi_choice'" class="choice-list multi"><button v-for="option in inputOptions(field)" :key="option.value" type="button" :class="{ selected: selectedChoices(field).includes(option.value) }" @click="toggleChoice(field, option.value)"><i>{{ selectedChoices(field).includes(option.value) ? '✓' : '' }}</i><span><strong>{{ option.label }}</strong><small v-if="option.description">{{ option.description }}</small></span></button></div>
                <select v-else-if="field.enum?.length" v-model="answerValues[field.name]" :required="field.required"><option value="">请选择</option><option v-for="option in field.enum" :key="String(option)" :value="String(option)">{{ option }}</option></select>
                <input v-else-if="field.value_type === 'boolean'" v-model="answerValues[field.name]" type="checkbox" />
                <textarea v-else-if="inputMode(field) === 'textarea'" v-model="answerValues[field.name]" rows="3" :required="field.required" :placeholder="field.name" />
                <input v-else v-model="answerValues[field.name]" :type="['integer','number'].includes(field.value_type) ? 'number' : 'text'" :required="field.required" :placeholder="field.name" />
                <input v-if="field.allow_other && ['single_choice', 'multi_choice'].includes(inputMode(field))" v-model="otherValues[field.name]" class="other-input" type="text" placeholder="Other：补充其他选项" />
              </label>
              </div>
              <div class="clarification-footer"><div><span>提交后将继续当前 Run</span><code>{{ shortId(runId || '') }}</code></div><button class="primary-button" type="submit" :disabled="answering">{{ answering ? '提交中…' : '提交并继续' }} <b>→</b></button></div>
            </form>
          </article>
        </div>
        <button v-if="!followLatest && (messages.length || running)" class="scroll-latest" type="button" @click="scrollBottom(true)">回到最新 ↓</button>

        <div v-if="pendingInputs.length" class="composer-locked">
          <span class="eyebrow">STRUCTURED INPUT</span>
          <strong>当前 Run 正在等待上面的字段</strong>
          <small>请使用追问表单提交；提交后会继续同一个 Run，不会创建新会话。</small>
        </div>
        <form v-else class="composer" @submit.prevent="send">
          <small v-if="pendingScenarioId" class="scenario-hint">本次将按预置场景执行：{{ pendingScenarioId }}</small>
          <textarea v-model="input" :disabled="busy || !agentId" rows="3" placeholder="描述目标、约束和希望得到的结果…" @keydown.enter.exact.prevent="send" />
          <div class="composer-footer"><span>Enter 发送 · Shift + Enter 换行</span><button type="submit" :disabled="busy || !input.trim() || !agentId">{{ busy ? '处理中' : '提交 Run' }} <b>↑</b></button></div>
        </form>
      </section>

      <aside class="run-inspector">
        <span class="eyebrow">LIVE RUN</span><h3>执行状态</h3>
        <dl><div><dt>状态</dt><dd><span class="status-badge" :class="runStatus">{{ statusLabel(runStatus) }}</span></dd></div><div><dt>阶段</dt><dd>{{ activeRun?.current_phase || '—' }}</dd></div><div><dt>Tasks</dt><dd>{{ activeRun?.completed_task_count ?? 0 }} / {{ activeRun?.total_task_count ?? 0 }}</dd></div><div><dt>下一步</dt><dd>{{ activeRun?.next_action || '—' }}</dd></div></dl>
        <div class="inspector-events"><span>最近事件</span><article v-for="event in visibleEvents" :key="event.event_id"><i :class="event.status" /><div><strong>{{ event.summary || event.type }}</strong><small>{{ formatTime(event.created_at) }}</small></div></article><p v-if="!visibleEvents.length">执行开始后在此显示。</p></div>
        <router-link v-if="runId" :to="`/runs?run=${runId}`" class="inspect-link">在运行中心打开 →</router-link>
      </aside>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, nextTick, onMounted, onUnmounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useMessage } from 'naive-ui'
import { getAgents, type AgentListItem } from '../api/agent'
import { normalizeApiError } from '../api/error'
import { deleteSession, getSessionHistory, getSessions, type SessionItem } from '../api/sessions'
import { cancelRuntimeRun, createRunFeedback, getPendingRunInputs, getRuntimeRun, listRunFeedback, resolveRunInput, streamRuntimeEvents, submitRuntimeRun, type PendingRunInput, type RunFeedbackType, type RunInputField, type RuntimeEvent, type RuntimeRun } from '../api/runtime'
import ExecutionTimeline from '../components/ExecutionTimeline.vue'
import MarkdownContent from '../components/MarkdownContent.vue'

interface ChatMessage { id: string; role: 'user' | 'assistant'; content: string; runId?: string }
const route = useRoute(); const router = useRouter(); const toast = useMessage()
const assistantAvatarUrl = `${import.meta.env.BASE_URL}joyhouse.png`
const agents = ref<AgentListItem[]>([]); const sessions = ref<(SessionItem & { latest_status?: string })[]>([]); const agentId = ref(''); const sessionId = ref('ui:default')
const messages = ref<ChatMessage[]>([]); const input = ref(''); const running = ref(false); const streaming = ref<string | null>(null); const events = ref<RuntimeEvent[]>([]); const runId = ref<string | null>(null); const activeRun = ref<RuntimeRun | null>(null); const messageContainer = ref<HTMLElement | null>(null); const followLatest = ref(true); const pendingScenarioId = ref<string | null>(null); const pendingScenarioInputs = ref<Record<string, unknown>>({})
const pendingInputs = ref<PendingRunInput[]>([]); const answerValues = ref<Record<string, any>>({}); const otherValues = ref<Record<string, string>>({}); const answering = ref(false)
const feedbackOpen = ref<Record<string, boolean>>({}); const feedbackSubmitted = ref<Record<string, boolean>>({}); const feedbackSaving = ref<Record<string, boolean>>({}); const feedbackType = ref<Record<string, RunFeedbackType>>({}); const feedbackComment = ref<Record<string, string>>({})
const seenEvents = new Set<string>(); let streamAbort: AbortController | null = null; let lastSequence = 0; let activeTurnId: string | null = null
const suggestions = ['分析这个项目并给出三项可执行优化', '把一个复杂目标拆成可并行的多 Agent 任务', '总结我的长期偏好，并说明你会如何使用记忆']
const feedbackTypes: Array<{ value: RunFeedbackType; label: string }> = [{ value: 'incorrect', label: '结果不对' }, { value: 'missing_data', label: '缺少数据' }, { value: 'needs_optimization', label: '需要优化' }, { value: 'helpful', label: '很有帮助' }, { value: 'other', label: '其他' }]

const currentAgent = computed(() => agents.value.find((agent) => agent.id === agentId.value))
const dinqMode = computed(() => agentId.value === 'main-coordinator' || String(route.query.plugin || '') === 'dinq')
const runStatus = computed(() => activeRun.value?.status || (running.value ? 'queued' : 'idle'))
const visibleEvents = computed(() => events.value.filter((event) => !['message.delta', 'usage.updated'].includes(event.type)).slice(-6).reverse())
const latestRunSummary = computed(() => visibleEvents.value[0]?.summary || activeRun.value?.status_summary || '正在提交到 Runtime')
const busy = computed(() => running.value || pendingInputs.value.length > 0)
const streamingLabel = computed(() => events.value.some((event) => event.type === 'plan.created') ? '执行回复 · 实时输出' : '协调计划 · 实时输出')

function storageKey() { return `joyhousebot_active_run:${agentId.value}:${sessionId.value}` }
function syncRoute() { void router.replace({ query: { agent: agentId.value, session: sessionId.value, ...(pendingScenarioId.value ? { scenario: pendingScenarioId.value } : {}) } }) }
async function loadAgents() { const response = await getAgents(); agents.value = response.agents; const requested = typeof route.query.agent === 'string' ? route.query.agent : ''; agentId.value = agents.value.some((agent) => agent.id === requested) ? requested : (agents.value[0]?.id || '') }
async function loadSessions() { const response = await getSessions(agentId.value); sessions.value = response.sessions as (SessionItem & { latest_status?: string })[] }
async function loadHistory() { try { const response = await getSessionHistory(sessionId.value, agentId.value); messages.value = response.messages.map((item, index) => ({ id: `${index}-${item.role}`, role: item.role === 'assistant' ? 'assistant' : 'user', content: item.content, runId: (item as { run_id?: string }).run_id })); const runIds = [...new Set(messages.value.map((item) => item.runId).filter((value): value is string => Boolean(value)))]; const results = await Promise.all(runIds.map(async (id) => [id, await listRunFeedback(id).catch(() => [])] as const)); feedbackSubmitted.value = Object.fromEntries(results.filter(([, items]) => items.length).map(([id]) => [id, true])) } catch { messages.value = [] } await scrollBottom(true) }
async function changeAgent() { streamAbort?.abort(); running.value = false; runId.value = null; newSession(); await loadSessions() }
async function selectSession(value: string) { if (value === sessionId.value) return; streamAbort?.abort(); running.value = false; sessionId.value = value; events.value = []; syncRoute(); await loadHistory(); await resumeRun() }
function newSession() { streamAbort?.abort(); sessionId.value = `ui:${Date.now()}`; messages.value = []; events.value = []; streaming.value = null; runId.value = null; activeRun.value = null; pendingInputs.value = []; answerValues.value = {}; otherValues.value = {}; pendingScenarioId.value = null; pendingScenarioInputs.value = {}; running.value = false; followLatest.value = true; syncRoute() }
async function removeSession() { try { const result = await deleteSession(sessionId.value, agentId.value); if (result.removed) { toast.success('会话已删除'); newSession(); await loadSessions() } else toast.warning('会话不存在或仍有运行中的 Run') } catch (error) { toast.error(normalizeApiError(error)) } }

function handleEvent(event: RuntimeEvent) { if (seenEvents.has(event.event_id)) return; seenEvents.add(event.event_id); events.value.push(event); lastSequence = Math.max(lastSequence, Number(event.sequence || 0)); localStorage.setItem(storageKey(), JSON.stringify({ runId: runId.value, sequence: lastSequence })); if (event.type === 'model.request.started' && event.turn_id !== activeTurnId) { activeTurnId = event.turn_id ?? null; streaming.value = '' } else if (event.type === 'message.delta') { if (event.turn_id && event.turn_id !== activeTurnId) { activeTurnId = event.turn_id; streaming.value = '' } const delta = event.data?.content; if (typeof delta === 'string') streaming.value = (streaming.value ?? '') + delta } else if (event.type === 'message.completed' && typeof event.data?.content === 'string') streaming.value = event.data.content; else if (event.type === 'user_input.requested') void loadPendingInput(event.run_id); void scrollBottom() }
function inputMode(field: RunInputField) { if (field.input_mode && field.input_mode !== 'auto') return field.input_mode; return field.value_type === 'array' ? 'multi_choice' : field.options?.length || field.enum?.length ? 'single_choice' : field.value_type === 'boolean' ? 'boolean' : field.value_type === 'number' || field.value_type === 'integer' ? 'number' : 'text' }
function inputOptions(field: RunInputField): Array<{ value: string; label: string; description?: string }> { return field.options?.length ? field.options : (field.enum || []).map((value) => ({ value: String(value), label: String(value) })) }
function selectedChoices(field: RunInputField) { const value = answerValues.value[field.name]; return Array.isArray(value) ? value.map(String) : [] }
function toggleChoice(field: RunInputField, value: string) { const selected = selectedChoices(field); const maximum = Number(field.max_selections || 0); answerValues.value[field.name] = selected.includes(value) ? selected.filter((item) => item !== value) : maximum > 0 && selected.length >= maximum ? selected : [...selected, value] }
function questionProgress(request: PendingRunInput) { const progress = request.presentation?.progress; const current = Number(progress?.current || 0); const total = Number(progress?.total || 0); return current > 0 && total > 0 ? `问题 ${current} / ${total}` : '' }
async function loadPendingInput(id: string) { streamAbort?.abort(); runId.value = id; activeRun.value = await getRuntimeRun(id); pendingInputs.value = await getPendingRunInputs(id); answerValues.value = Object.fromEntries((pendingInputs.value[0]?.fields || []).map((field) => [field.name, inputMode(field) === 'multi_choice' ? [] : field.value_type === 'boolean' ? false : ''])); otherValues.value = {}; running.value = false; await scrollBottom() }
async function attachRun(id: string, cursor = 0) { streamAbort?.abort(); streamAbort = new AbortController(); runId.value = id; running.value = true; try { await streamRuntimeEvents(id, handleEvent, { afterSequence: cursor, signal: streamAbort.signal }); await finalizeRun(id) } catch (error) { if ((error as DOMException)?.name === 'AbortError') return; try { activeRun.value = await getRuntimeRun(id); if (isTerminal(activeRun.value.status)) await finalizeRun(id); else window.setTimeout(() => void attachRun(id, lastSequence), 1000) } catch (retryError) { running.value = false; toast.error(normalizeApiError(retryError)) } } }
async function finalizeRun(id: string) { activeRun.value = await getRuntimeRun(id); if (!isTerminal(activeRun.value.status)) return; const content = activeRun.value.result?.content || streaming.value || ''; if (activeRun.value.status === 'completed' && content && !messages.value.some((item) => item.runId === id && item.role === 'assistant')) messages.value.push({ id: crypto.randomUUID(), role: 'assistant', content, runId: id }); else if (activeRun.value.status !== 'completed') toast.error(activeRun.value.error?.message || `Run ${statusLabel(activeRun.value.status)}`); localStorage.removeItem(storageKey()); running.value = false; streaming.value = null; await loadSessions(); await scrollBottom(); if (dinqMode.value) await router.replace(`/dinq/runs/${encodeURIComponent(id)}`) }
function toggleFeedback(id: string) { feedbackOpen.value[id] = !feedbackOpen.value[id]; if (!feedbackType.value[id]) feedbackType.value[id] = 'other' }
async function submitFeedback(messageItem: ChatMessage) { const id = messageItem.runId; if (!id) return; const comment = (feedbackComment.value[id] || '').trim(); if (!comment) return; feedbackSaving.value[id] = true; try { await createRunFeedback(id, { feedback_type: feedbackType.value[id] || 'other', comment, output_excerpt: messageItem.content.slice(0, 4000), message_id: messageItem.id, turn_id: activeTurnId || undefined }); feedbackSubmitted.value[id] = true; feedbackOpen.value[id] = false; toast.success('反馈已记录到 Run'); } catch (error) { toast.error(normalizeApiError(error)) } finally { feedbackSaving.value[id] = false } }
async function resumeRun() { try { const stored = JSON.parse(localStorage.getItem(storageKey()) || '{}') as { runId?: string }; if (!stored.runId) return; activeRun.value = await getRuntimeRun(stored.runId); runId.value = stored.runId; if (isTerminal(activeRun.value.status)) await finalizeRun(stored.runId); else if (activeRun.value.status === 'waiting_input') await loadPendingInput(stored.runId); else { events.value = []; seenEvents.clear(); lastSequence = 0; void attachRun(stored.runId, 0) } } catch { localStorage.removeItem(storageKey()) } }
async function send() { const prompt = input.value.trim(); if (!prompt || busy.value || !agentId.value) return; const scenarioId = pendingScenarioId.value || (dinqMode.value ? 'dinq.discover.search' : undefined); const scenarioInputs = { ...pendingScenarioInputs.value }; pendingScenarioId.value = null; pendingScenarioInputs.value = {}; messages.value.push({ id: crypto.randomUUID(), role: 'user', content: prompt }); input.value = ''; pendingInputs.value = []; events.value = []; seenEvents.clear(); lastSequence = 0; activeTurnId = null; streaming.value = null; activeRun.value = null; running.value = true; syncRoute(); await scrollBottom(); try { const run = await submitRuntimeRun({ prompt, sessionId: sessionId.value, agentId: agentId.value, scenarioId, scenarioInputs, channel: 'web-playground', chatId: sessionId.value }); runId.value = run.run_id; activeRun.value = run; localStorage.setItem(storageKey(), JSON.stringify({ runId: run.run_id, sequence: 0 })); if (dinqMode.value) await router.replace(`/dinq/runs/${encodeURIComponent(run.run_id)}`); else if (run.status === 'waiting_input') await loadPendingInput(run.run_id); else if (isTerminal(run.status)) await finalizeRun(run.run_id); else void attachRun(run.run_id) } catch (error) { running.value = false; toast.error(normalizeApiError(error)) } }
async function submitAnswers() { const request = pendingInputs.value[0]; if (!request || !runId.value) return; answering.value = true; try { const answers: Record<string, unknown> = {}; for (const field of request.fields) { const raw = answerValues.value[field.name]; const other = otherValues.value[field.name]?.trim(); if (inputMode(field) === 'multi_choice') answers[field.name] = [...selectedChoices(field), ...(other ? [other] : [])]; else if (inputMode(field) === 'single_choice' && other) answers[field.name] = other; else if (field.value_type === 'integer') answers[field.name] = Number.parseInt(String(raw), 10); else if (field.value_type === 'number') answers[field.name] = Number(raw); else if (field.value_type === 'boolean') answers[field.name] = Boolean(raw); else if (field.value_type === 'object') answers[field.name] = JSON.parse(String(raw)); else answers[field.name] = raw } const result = await resolveRunInput(runId.value, request.input_request_id, answers); activeRun.value = result.run; pendingInputs.value = result.pending_inputs; if (pendingInputs.value.length) await loadPendingInput(runId.value); else { running.value = true; void attachRun(runId.value, lastSequence) } } catch (error) { toast.error(normalizeApiError(error)) } finally { answering.value = false } }
async function cancelRun() { if (!runId.value) return; try { await cancelRuntimeRun(runId.value); toast.info('已请求取消') } catch (error) { toast.error(normalizeApiError(error)) } }
function isTerminal(status: string) { return ['completed', 'failed', 'cancelled', 'timed_out'].includes(status) }
function statusLabel(status: string) { return ({ idle: '空闲', queued: '排队', running: '运行中', completed: '完成', failed: '失败', cancelled: '取消', timed_out: '超时', paused: '暂停', waiting_input: '等待输入' } as Record<string, string>)[status] ?? status }
function shortId(value: string) { return value.length > 16 ? `${value.slice(0, 8)}…${value.slice(-5)}` : value }
function formatTime(value: string) { return new Date(value).toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit', second: '2-digit' }) }
function formatRelative(value?: string | null) { if (!value) return '新会话'; const delta = Date.now() - new Date(value).getTime(); return delta < 60_000 ? '刚刚' : delta < 3_600_000 ? `${Math.floor(delta / 60_000)} 分钟前` : new Date(value).toLocaleDateString('zh-CN') }
function handleMessageScroll() { const element = messageContainer.value; if (!element) return; followLatest.value = element.scrollHeight - element.scrollTop - element.clientHeight < 56 }
async function scrollBottom(force = false) { if (!force && !followLatest.value) return; await nextTick(); const element = messageContainer.value; if (!element) return; element.scrollTop = element.scrollHeight; followLatest.value = true }

onMounted(async () => { try { await loadAgents(); if (typeof route.query.session === 'string' && route.query.session.trim()) sessionId.value = route.query.session; if (typeof route.query.prompt === 'string' && route.query.prompt.trim()) input.value = route.query.prompt.trim(); if (typeof route.query.scenario === 'string' && route.query.scenario.trim()) pendingScenarioId.value = route.query.scenario.trim(); if (typeof route.query.scenarioInputs === 'string' && route.query.scenarioInputs.trim()) { try { const parsed = JSON.parse(route.query.scenarioInputs); if (parsed && !Array.isArray(parsed) && typeof parsed === 'object') pendingScenarioInputs.value = parsed as Record<string, unknown> } catch { /* A malformed deep link must not block the playground. */ } } await loadSessions(); await loadHistory(); const latestDinqRun = [...messages.value].reverse().find((item) => item.runId)?.runId; if (dinqMode.value && latestDinqRun && !route.query.prompt && !route.query.workspace) { await router.replace(`/dinq/runs/${encodeURIComponent(latestDinqRun)}`); return } syncRoute(); await resumeRun() } catch (error) { toast.error(normalizeApiError(error)) } })
onUnmounted(() => streamAbort?.abort())
</script>
