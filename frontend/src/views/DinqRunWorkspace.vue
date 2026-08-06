<template>
  <div class="page dinq-run-page">
    <header class="workspace-heading">
      <div>
        <span class="eyebrow">DINQ RUN WORKSPACE</span>
        <h1>{{ projection?.search.query || 'Dinq 搜索执行' }}</h1>
        <p>左侧查看会话与执行过程，中间查看候选人命中原因，右侧查看选中候选人的富化档案。</p>
      </div>
      <div class="heading-actions">
        <span v-if="projection" :class="['status-pill', projection.search.status]">{{ statusLabel(projection.search.status) }}</span>
        <button class="secondary-button" type="button" @click="startNewSearch">新的搜索</button>
        <router-link class="secondary-button" :to="{ path: '/runs', query: { run: runId } }">通用诊断</router-link>
        <button class="secondary-button" :disabled="loading" @click="() => load()">{{ loading ? '刷新中…' : '刷新' }}</button>
      </div>
    </header>

    <div v-if="error" class="notice error-notice">{{ error }}</div>
    <section v-if="loading && !projection" class="empty-state panel">正在读取 Run 工作区…</section>
    <section v-else-if="projection" class="dinq-workspace">
      <aside class="workspace-sessions panel">
        <div class="section-heading"><div><span class="eyebrow">SESSIONS</span><h2>历史会话</h2></div><button class="icon-button" title="刷新会话" @click="loadSessions">↻</button></div>
        <div class="session-list">
          <button v-for="session in sessions" :key="session.key" :class="['session-item', { active: session.latest_run_id === runId }]" @click="openSession(session.latest_run_id)">
            <strong>{{ sessionLabel(session.key) }}</strong>
            <small>{{ session.latest_status || '—' }} · {{ formatDate(session.updated_at) }}</small>
          </button>
          <p v-if="!sessions.length" class="muted">暂无历史会话</p>
        </div>
      </aside>

      <section class="workspace-activity panel">
        <div class="section-heading"><div><span class="eyebrow">SEARCH ACTIVITY</span><h2>搜索执行</h2></div><span class="metric">{{ projection.search.tool_calls }} 次工具调用</span></div>
        <div class="run-summary">
          <div><span>阶段</span><strong>{{ projection.search.phase || '—' }}</strong></div>
          <div><span>候选人</span><strong>{{ projection.search.total_candidates }}</strong></div>
          <div><span>已验证</span><strong>{{ projection.search.verified_candidates }}</strong></div>
        </div>
        <p v-if="projection.search.summary" class="summary">{{ projection.search.summary }}</p>
        <button class="activity-toggle" type="button" :aria-expanded="activityExpanded" @click="toggleActivity">
          <span><i :class="['activity-toggle-dot', { active: !isTerminalStatus(projection.search.status) }]" />执行过程</span>
          <small>{{ projection.activity.length }} 个事件 · {{ activityExpanded ? '收起' : '展开' }} {{ activityExpanded ? '⌃' : '⌄' }}</small>
        </button>
        <div v-show="activityExpanded" class="activity-list">
          <article v-for="item in projection.activity" :key="`${item.sequence}-${item.event_id}`" :class="['activity-item', item.status || '']">
            <span class="activity-dot" />
            <div><strong>{{ activityLabel(item) }}</strong><small>{{ item.phase || '运行' }} · {{ formatDate(item.created_at) }}</small><p v-if="item.summary && item.summary !== activityLabel(item)">{{ item.summary }}</p></div>
          </article>
          <p v-if="!projection.activity.length" class="muted">暂无可展示的执行事件。</p>
        </div>
        <form class="workspace-composer" @submit.prevent="continueRun">
          <span class="composer-kicker">继续当前会话</span>
          <textarea v-model="composerInput" rows="3" placeholder="继续追问、补充约束，或让 Agent 深入富化候选人…" :disabled="sending" @keydown.enter.exact.prevent="continueRun" />
          <div><span>Enter 发送 · Shift + Enter 换行</span><button class="primary-button" type="submit" :disabled="sending || !composerInput.trim()">{{ sending ? '提交中…' : '继续执行 ↑' }}</button></div>
        </form>
      </section>

      <section class="workspace-candidates panel">
        <div class="section-heading"><div><span class="eyebrow">CANDIDATES</span><h2>候选人</h2></div><span class="metric">{{ projection.candidates.length }} 命中</span></div>
        <div class="candidate-list">
          <button v-for="candidate in projection.candidates" :key="candidate.candidate_id" :class="['candidate-row', { selected: candidate.candidate_id === selectedId }]" @click="selectCandidate(candidate.candidate_id)">
            <span class="avatar">{{ initials(candidate.name) }}</span>
            <span class="candidate-main"><strong>{{ candidate.name }}</strong><small>{{ candidate.title || '未声明职位' }}<template v-if="candidate.company"> · {{ candidate.company }}</template></small><em>{{ candidate.match_reasons[0] || '已通过来源匹配' }}</em></span>
            <span v-if="candidate.match_score != null" class="score">{{ Math.round(candidate.match_score * (candidate.match_score <= 1 ? 100 : 1)) }}%</span>
          </button>
          <div v-if="!projection.candidates.length" class="empty-candidates"><strong>等待候选人产物</strong><p>运行完成后，Dinq Plugin 会将候选人集合写入 Artifact，工作台会自动呈现。</p></div>
        </div>
      </section>

      <aside class="workspace-detail panel">
        <div class="section-heading"><div><span class="eyebrow">ENRICHMENT</span><h2>候选人档案</h2></div><span v-if="selectedCandidate" :class="['enrichment-state', selectedCandidate.enrichment_status]">{{ enrichmentLabel(selectedCandidate.enrichment_status) }}</span></div>
        <template v-if="selectedCandidate">
          <div class="profile-header"><span class="profile-avatar">{{ initials(selectedCandidate.name) }}</span><div><h3>{{ selectedCandidate.name }}</h3><p>{{ selectedCandidate.title || '未声明职位' }}<template v-if="selectedCandidate.company"> · {{ selectedCandidate.company }}</template></p></div></div>
          <div class="detail-block"><span class="detail-label">命中原因</span><ul><li v-for="reason in selectedCandidate.match_reasons" :key="reason">{{ reason }}</li><li v-if="!selectedCandidate.match_reasons.length">暂无结构化原因</li></ul></div>
          <div class="detail-block"><span class="detail-label">来源</span><div class="tag-list"><span v-for="source in selectedCandidate.sources" :key="String(source)">{{ sourceLabel(source) }}</span><span v-if="!selectedCandidate.sources.length">暂无来源</span></div></div>
          <div class="detail-block"><span class="detail-label">富化结果</span><pre v-if="selectedCandidate.enrichment || selectedCandidate.profile">{{ pretty(selectedCandidate.enrichment || selectedCandidate.profile) }}</pre><p v-else class="muted">尚未生成富化档案。</p></div>
          <div class="detail-block"><span class="detail-label">证据</span><pre v-if="selectedCandidate.evidence && (Array.isArray(selectedCandidate.evidence) ? selectedCandidate.evidence.length : true)">{{ pretty(selectedCandidate.evidence) }}</pre><p v-else class="muted">暂无独立证据。</p></div>
          <form class="workspace-feedback" @submit.prevent="submitFeedback">
            <div class="feedback-heading"><span class="detail-label">人工反馈</span><select v-model="feedbackType"><option value="incorrect">结果不对</option><option value="missing_data">缺少数据</option><option value="needs_optimization">需要优化</option><option value="helpful">很有帮助</option></select></div>
            <textarea v-model="feedbackComment" rows="3" placeholder="指出这个候选人或富化结果需要改进的地方…" :disabled="feedbackSent" />
            <button class="secondary-button" type="submit" :disabled="feedbackSaving || feedbackSent || !feedbackComment.trim()">{{ feedbackSent ? '✓ 已记录反馈' : feedbackSaving ? '提交中…' : '提交反馈到 Run' }}</button>
          </form>
        </template>
        <div v-else class="empty-detail"><span class="profile-placeholder">◇</span><strong>选择一个候选人</strong><p>点击中间列表查看命中原因、来源和富化档案。</p></div>
      </aside>
    </section>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { getDinqRunProjection, type DinqActivity, type DinqCandidate, type DinqRunProjection } from '../api/dinq'
import { getSessions, type SessionItem } from '../api/sessions'
import { createRunFeedback, streamRuntimeEvents, submitRuntimeRun, type RunFeedbackType } from '../api/runtime'

const route = useRoute(); const router = useRouter(); const runId = String(route.params.runId)
const projection = ref<DinqRunProjection | null>(null); const sessions = ref<SessionItem[]>([]); const selectedId = ref<string | null>(null); const loading = ref(false); const error = ref(''); const composerInput = ref(''); const sending = ref(false); const feedbackType = ref<RunFeedbackType>('needs_optimization'); const feedbackComment = ref(''); const feedbackSaving = ref(false); const feedbackSent = ref(false); const activityExpanded = ref(false); const activityPreference = ref<boolean | null>(null)
const selectedCandidate = computed<DinqCandidate | null>(() => projection.value?.selected_candidate || projection.value?.candidates.find((item) => item.candidate_id === selectedId.value) || null)
let abortController: AbortController | null = null
let refreshTimer: number | null = null

async function load(candidateId = selectedId.value) { loading.value = true; error.value = ''; try { const firstLoad = !projection.value; projection.value = await getDinqRunProjection(runId, candidateId); selectedId.value = projection.value.selected_candidate_id || selectedId.value || projection.value.candidates[0]?.candidate_id || null; if (firstLoad && activityPreference.value === null) activityExpanded.value = !isTerminalStatus(projection.value.search.status) } catch (cause) { error.value = cause instanceof Error ? cause.message : '读取 Dinq 工作区失败' } finally { loading.value = false } }
async function loadSessions() { try { sessions.value = (await getSessions(projection.value?.session.agent_id)).sessions } catch { /* session rail is optional */ } }
function selectCandidate(id: string) { selectedId.value = id; feedbackSent.value = false; feedbackComment.value = ''; void load(id) }
function submitFeedback() {
  if (!feedbackComment.value.trim() || feedbackSaving.value) return
  feedbackSaving.value = true
  void createRunFeedback(runId, { feedback_type: feedbackType.value, comment: feedbackComment.value.trim(), output_excerpt: selectedCandidate.value ? pretty(selectedCandidate.value).slice(0, 4000) : undefined }).then(() => { feedbackSent.value = true }).catch((cause) => { error.value = cause instanceof Error ? cause.message : '提交反馈失败' }).finally(() => { feedbackSaving.value = false })
}
function openSession(id?: string) { if (id && id !== runId) void router.push(`/dinq/runs/${encodeURIComponent(id)}`) }
function startNewSearch() { void router.push({ name: 'Chat', query: { agent: projection.value?.session.agent_id || 'main-coordinator', session: `ui:dinq-${Date.now()}`, plugin: 'dinq', workspace: 'new' } }) }
function toggleActivity() { activityExpanded.value = !activityExpanded.value; activityPreference.value = activityExpanded.value }
async function continueRun() {
  const prompt = composerInput.value.trim()
  if (!prompt || !projection.value || sending.value) return
  sending.value = true; error.value = ''
  try {
    const run = await submitRuntimeRun({
      prompt,
      sessionId: projection.value.session.session_id || `ui:dinq-${Date.now()}`,
      agentId: projection.value.session.agent_id || 'main-coordinator',
      channel: 'web-playground',
      chatId: projection.value.session.session_id || undefined,
    })
    composerInput.value = ''
    await router.push(`/dinq/runs/${encodeURIComponent(run.run_id)}`)
  } catch (cause) { error.value = cause instanceof Error ? cause.message : '提交继续执行失败' } finally { sending.value = false }
}
function sessionLabel(value: string) { const text = value.replace(/^ui:/, ''); return text.length > 24 ? `${text.slice(0, 24)}…` : text }
function statusLabel(value: string) { return ({ completed: '已完成', failed: '失败', running: '执行中', queued: '排队中', waiting_input: '等待输入', cancelled: '已取消' } as Record<string, string>)[value] || value }
function isTerminalStatus(value: string) { return ['completed', 'failed', 'cancelled', 'timed_out'].includes(value) }
function enrichmentLabel(value: string) { return ({ not_requested: '未富化', ready: '已富化', verified: '已验证', completed: '已完成', failed: '失败' } as Record<string, string>)[value] || value }
function activityLabel(item: DinqActivity) { return ({ 'run.created': '请求已接受', 'run.queued': '任务已进入执行队列', 'run.started': '开始执行任务', 'run.completed': '任务执行完成', 'run.failed': '任务执行失败', 'task.created': '子任务已创建', 'task.started': '子任务开始执行', 'task.completed': '子任务执行完成', 'capability.started': '开始调用能力', 'capability.completed': '能力调用完成' } as Record<string, string>)[item.type] || item.summary || item.type }
function formatDate(value?: string | null) { return value ? new Date(value).toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit', second: '2-digit' }) : '—' }
function initials(value: string) { return value.split(/\s+/).map((item) => item[0]).join('').slice(0, 2).toUpperCase() || 'D' }
function sourceLabel(value: unknown) { if (typeof value === 'string') return value.replace(/^https?:\/\//, '').split('/')[0]; if (value && typeof value === 'object') return String((value as Record<string, unknown>).source || (value as Record<string, unknown>).name || 'source'); return String(value) }
function pretty(value: unknown) { return JSON.stringify(value, null, 2) }
onMounted(async () => {
  await load(); await loadSessions()
  if (projection.value && ['completed', 'failed', 'cancelled', 'timed_out'].includes(projection.value.search.status)) return
  abortController = new AbortController()
  try {
    await streamRuntimeEvents(runId, () => {
      if (refreshTimer) window.clearTimeout(refreshTimer)
      refreshTimer = window.setTimeout(() => { void load(selectedId.value) }, 120)
    }, { afterSequence: projection.value?.events_cursor || 0, signal: abortController.signal })
  } catch { /* completed streams and disconnects are expected */ }
})
onUnmounted(() => { abortController?.abort(); if (refreshTimer) window.clearTimeout(refreshTimer) })
</script>

<style scoped>
.dinq-run-page{display:flex;flex-direction:column;gap:16px;min-height:calc(100vh - 100px)}.workspace-heading{display:flex;justify-content:space-between;align-items:flex-start;gap:20px}.workspace-heading h1{margin:7px 0 6px;color:var(--text-strong);font-size:28px}.workspace-heading p{margin:0;color:var(--text-muted);font-size:12px}.heading-actions{display:flex;align-items:center;gap:8px;flex-wrap:wrap}.status-pill,.enrichment-state{padding:6px 9px;border-radius:999px;background:var(--surface-raised);color:var(--text-muted);font-size:11px}.status-pill.completed,.enrichment-state.ready,.enrichment-state.verified,.enrichment-state.completed{color:var(--success);background:var(--success-subtle)}.status-pill.failed,.enrichment-state.failed{color:var(--danger);background:var(--danger-subtle)}.dinq-workspace{display:grid;grid-template-columns:190px minmax(245px,1fr) minmax(300px,1.25fr) minmax(285px,1fr);gap:10px;align-items:stretch;min-height:650px}.panel{padding:15px;border:1px solid var(--border);border-radius:12px;background:var(--surface)}.section-heading{display:flex;justify-content:space-between;align-items:flex-start;gap:8px;padding-bottom:12px;border-bottom:1px solid var(--border)}.section-heading h2{margin:4px 0 0;color:var(--text-strong);font-size:16px}.metric{color:var(--text-muted);font:10px var(--font-mono)}.session-list,.activity-list,.candidate-list{display:grid;gap:7px;margin-top:12px}.session-item,.candidate-row{width:100%;padding:10px;border:1px solid transparent;border-radius:9px;background:transparent;color:var(--text);text-align:left;cursor:pointer}.session-item:hover,.candidate-row:hover,.session-item.active,.candidate-row.selected{border-color:var(--accent-border);background:var(--accent-subtle)}.session-item{display:grid;gap:5px}.session-item strong{overflow:hidden;color:var(--text-strong);font-size:11px;text-overflow:ellipsis;white-space:nowrap}.session-item small,.candidate-main small,.candidate-main em,.activity-item small,.activity-item p{color:var(--text-muted);font-size:10px}.workspace-activity{display:flex;min-height:0;flex-direction:column}.run-summary{display:grid;grid-template-columns:repeat(3,1fr);gap:7px;margin:13px 0}.run-summary div{display:grid;gap:4px;padding:9px;background:var(--surface-raised);border-radius:8px}.run-summary span{color:var(--text-muted);font-size:10px}.run-summary strong{color:var(--text-strong);font-size:14px}.summary{padding:9px;margin:0 0 8px;color:var(--text);background:var(--surface-raised);border-radius:8px;font-size:11px;line-height:1.5}.activity-toggle{display:flex;justify-content:space-between;align-items:center;gap:10px;width:100%;margin:4px 0 0;padding:10px 0;color:var(--text-strong);background:transparent;border:0;border-top:1px solid var(--border);font-size:11px;text-align:left;cursor:pointer}.activity-toggle>span{display:flex;align-items:center;gap:8px}.activity-toggle small{color:var(--text-muted);font:10px var(--font-mono)}.activity-toggle-dot{width:7px;height:7px;border-radius:50%;background:var(--success)}.activity-toggle-dot.active{animation:workspace-pulse 1.4s ease-in-out infinite}.activity-list{flex:1;min-height:0;max-height:none;overflow:auto;margin-top:0;padding-right:3px}.activity-item{display:flex;gap:9px;padding:8px 0;border-bottom:1px solid var(--border)}.activity-dot{flex:0 0 8px;width:8px;height:8px;margin-top:4px;border-radius:50%;background:var(--warning)}.activity-item.completed .activity-dot,.activity-item.succeeded .activity-dot{background:var(--success)}.activity-item.failed .activity-dot{background:var(--danger)}.activity-item>div{min-width:0}.activity-item strong{display:block;color:var(--text-strong);font-size:11px}.activity-item p{margin:4px 0 0;line-height:1.4}.workspace-composer{position:sticky;bottom:0;z-index:2;display:grid;gap:7px;margin-top:auto;padding:12px 0 0;background:linear-gradient(to bottom, color-mix(in srgb, var(--surface) 0%, transparent), var(--surface) 22%)}.composer-kicker{color:var(--text-muted);font:9px var(--font-mono);letter-spacing:.08em;text-transform:uppercase}.workspace-composer textarea{width:100%;box-sizing:border-box;resize:none;padding:11px;color:var(--text);background:var(--input);border:1px solid var(--border-strong);border-radius:10px;font:12px/1.55 var(--font-sans);box-shadow:0 8px 24px rgba(0,0,0,.08)}.workspace-composer div{display:flex;justify-content:space-between;align-items:center;gap:8px}.workspace-composer div>span{color:var(--text-muted);font-size:10px}.candidate-list{max-height:585px;overflow:auto}.candidate-row{display:flex;align-items:center;gap:9px}.avatar,.profile-avatar{display:grid;place-items:center;flex:0 0 30px;width:30px;height:30px;border-radius:50%;background:var(--accent-subtle);color:var(--accent);font:bold 10px var(--font-mono)}.candidate-main{display:grid;min-width:0;flex:1;gap:3px}.candidate-main strong{overflow:hidden;color:var(--text-strong);font-size:12px;text-overflow:ellipsis;white-space:nowrap}.candidate-main small,.candidate-main em{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.candidate-main em{font-style:normal;color:var(--accent)}.score{color:var(--success);font:11px var(--font-mono)}.empty-candidates,.empty-detail{display:grid;place-items:center;gap:8px;padding:50px 15px;text-align:center}.empty-candidates strong,.empty-detail strong{color:var(--text-strong);font-size:13px}.empty-candidates p,.empty-detail p{margin:0;color:var(--text-muted);font-size:11px;line-height:1.5}.profile-header{display:flex;gap:10px;align-items:center;padding:15px 0;border-bottom:1px solid var(--border)}.profile-avatar{width:42px;height:42px;font-size:13px}.profile-header h3{margin:0;color:var(--text-strong);font-size:16px}.profile-header p{margin:4px 0 0;color:var(--text-muted);font-size:11px}.detail-block{padding:13px 0;border-bottom:1px solid var(--border)}.detail-label{display:block;margin-bottom:8px;color:var(--text-muted);font:10px var(--font-mono);text-transform:uppercase}.detail-block ul{margin:0;padding-left:17px;color:var(--text);font-size:11px;line-height:1.65}.tag-list{display:flex;gap:6px;flex-wrap:wrap}.tag-list span{padding:4px 7px;border:1px solid var(--border);border-radius:999px;color:var(--text-muted);font-size:10px}.detail-block pre{max-height:180px;overflow:auto;margin:0;padding:9px;background:var(--surface-raised);border-radius:8px;color:var(--text);font:10px/1.5 var(--font-mono);white-space:pre-wrap}.profile-placeholder{color:var(--accent);font-size:30px}.muted{color:var(--text-muted);font-size:11px}@keyframes workspace-pulse{50%{opacity:.35;transform:scale(.8)}}@media(max-width:1250px){.dinq-workspace{grid-template-columns:180px minmax(240px,1fr) minmax(300px,1.2fr)}.workspace-detail{grid-column:2/-1}}@media(max-width:850px){.workspace-heading{flex-direction:column}.dinq-workspace{grid-template-columns:1fr}.workspace-detail{grid-column:auto}.workspace-sessions{order:-1}.activity-list,.candidate-list{max-height:none}}
.workspace-feedback{display:grid;gap:8px;padding-top:13px}.feedback-heading{display:flex;justify-content:space-between;align-items:flex-start;gap:8px}.feedback-heading .detail-label{margin:0}.workspace-feedback select{padding:5px;color:var(--text);background:var(--input);border:1px solid var(--border);border-radius:7px;font-size:10px}.workspace-feedback textarea{width:100%;box-sizing:border-box;resize:vertical;padding:9px;color:var(--text);background:var(--input);border:1px solid var(--border-strong);border-radius:8px;font:11px/1.5 var(--font-sans)}
</style>
