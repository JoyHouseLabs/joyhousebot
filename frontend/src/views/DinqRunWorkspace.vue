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
        <div class="activity-list">
          <article v-for="item in projection.activity" :key="`${item.sequence}-${item.event_id}`" :class="['activity-item', item.status || '']">
            <span class="activity-dot" />
            <div><strong>{{ activityLabel(item) }}</strong><small>{{ item.phase || '运行' }} · {{ formatDate(item.created_at) }}</small><p v-if="item.summary && item.summary !== activityLabel(item)">{{ item.summary }}</p></div>
          </article>
          <p v-if="!projection.activity.length" class="muted">暂无可展示的执行事件。</p>
        </div>
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
import { streamRuntimeEvents } from '../api/runtime'

const route = useRoute(); const router = useRouter(); const runId = String(route.params.runId)
const projection = ref<DinqRunProjection | null>(null); const sessions = ref<SessionItem[]>([]); const selectedId = ref<string | null>(null); const loading = ref(false); const error = ref('')
const selectedCandidate = computed<DinqCandidate | null>(() => projection.value?.selected_candidate || projection.value?.candidates.find((item) => item.candidate_id === selectedId.value) || null)
let abortController: AbortController | null = null
let refreshTimer: number | null = null

async function load(candidateId = selectedId.value) { loading.value = true; error.value = ''; try { projection.value = await getDinqRunProjection(runId, candidateId); selectedId.value = projection.value.selected_candidate_id || selectedId.value || projection.value.candidates[0]?.candidate_id || null } catch (cause) { error.value = cause instanceof Error ? cause.message : '读取 Dinq 工作区失败' } finally { loading.value = false } }
async function loadSessions() { try { sessions.value = (await getSessions(projection.value?.session.agent_id)).sessions } catch { /* session rail is optional */ } }
function selectCandidate(id: string) { selectedId.value = id; void load(id) }
function openSession(id?: string) { if (id && id !== runId) void router.push(`/dinq/runs/${encodeURIComponent(id)}`) }
function sessionLabel(value: string) { const text = value.replace(/^ui:/, ''); return text.length > 24 ? `${text.slice(0, 24)}…` : text }
function statusLabel(value: string) { return ({ completed: '已完成', failed: '失败', running: '执行中', queued: '排队中', waiting_input: '等待输入', cancelled: '已取消' } as Record<string, string>)[value] || value }
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
.dinq-run-page{display:flex;flex-direction:column;gap:16px;min-height:calc(100vh - 100px)}.workspace-heading{display:flex;justify-content:space-between;align-items:flex-start;gap:20px}.workspace-heading h1{margin:7px 0 6px;color:var(--text-strong);font-size:28px}.workspace-heading p{margin:0;color:var(--text-muted);font-size:12px}.heading-actions{display:flex;align-items:center;gap:8px}.status-pill,.enrichment-state{padding:6px 9px;border-radius:999px;background:var(--surface-raised);color:var(--text-muted);font-size:11px}.status-pill.completed,.enrichment-state.ready,.enrichment-state.verified,.enrichment-state.completed{color:var(--success);background:var(--success-subtle)}.status-pill.failed,.enrichment-state.failed{color:var(--danger);background:var(--danger-subtle)}.dinq-workspace{display:grid;grid-template-columns:190px minmax(245px,1fr) minmax(300px,1.25fr) minmax(285px,1fr);gap:10px;align-items:stretch;min-height:650px}.panel{padding:15px;border:1px solid var(--border);border-radius:12px;background:var(--surface)}.section-heading{display:flex;justify-content:space-between;align-items:flex-start;gap:8px;padding-bottom:12px;border-bottom:1px solid var(--border)}.section-heading h2{margin:4px 0 0;color:var(--text-strong);font-size:16px}.metric{color:var(--text-muted);font:10px var(--font-mono)}.session-list,.activity-list,.candidate-list{display:grid;gap:7px;margin-top:12px}.session-item,.candidate-row{width:100%;padding:10px;border:1px solid transparent;border-radius:9px;background:transparent;color:var(--text);text-align:left;cursor:pointer}.session-item:hover,.candidate-row:hover,.session-item.active,.candidate-row.selected{border-color:var(--accent-border);background:var(--accent-subtle)}.session-item{display:grid;gap:5px}.session-item strong{overflow:hidden;color:var(--text-strong);font-size:11px;text-overflow:ellipsis;white-space:nowrap}.session-item small,.candidate-main small,.candidate-main em,.activity-item small,.activity-item p{color:var(--text-muted);font-size:10px}.run-summary{display:grid;grid-template-columns:repeat(3,1fr);gap:7px;margin:13px 0}.run-summary div{display:grid;gap:4px;padding:9px;background:var(--surface-raised);border-radius:8px}.run-summary span{color:var(--text-muted);font-size:10px}.run-summary strong{color:var(--text-strong);font-size:14px}.summary{padding:9px;margin:0 0 8px;color:var(--text);background:var(--surface-raised);border-radius:8px;font-size:11px;line-height:1.5}.activity-list{max-height:530px;overflow:auto}.activity-item{display:flex;gap:9px;padding:8px 0;border-bottom:1px solid var(--border)}.activity-dot{flex:0 0 8px;width:8px;height:8px;margin-top:4px;border-radius:50%;background:var(--warning)}.activity-item.completed .activity-dot,.activity-item.succeeded .activity-dot{background:var(--success)}.activity-item.failed .activity-dot{background:var(--danger)}.activity-item>div{min-width:0}.activity-item strong{display:block;color:var(--text-strong);font-size:11px}.activity-item p{margin:4px 0 0;line-height:1.4}.candidate-list{max-height:585px;overflow:auto}.candidate-row{display:flex;align-items:center;gap:9px}.avatar,.profile-avatar{display:grid;place-items:center;flex:0 0 30px;width:30px;height:30px;border-radius:50%;background:var(--accent-subtle);color:var(--accent);font:bold 10px var(--font-mono)}.candidate-main{display:grid;min-width:0;flex:1;gap:3px}.candidate-main strong{overflow:hidden;color:var(--text-strong);font-size:12px;text-overflow:ellipsis;white-space:nowrap}.candidate-main small,.candidate-main em{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.candidate-main em{font-style:normal;color:var(--accent)}.score{color:var(--success);font:11px var(--font-mono)}.empty-candidates,.empty-detail{display:grid;place-items:center;gap:8px;padding:50px 15px;text-align:center}.empty-candidates strong,.empty-detail strong{color:var(--text-strong);font-size:13px}.empty-candidates p,.empty-detail p{margin:0;color:var(--text-muted);font-size:11px;line-height:1.5}.profile-header{display:flex;gap:10px;align-items:center;padding:15px 0;border-bottom:1px solid var(--border)}.profile-avatar{width:42px;height:42px;font-size:13px}.profile-header h3{margin:0;color:var(--text-strong);font-size:16px}.profile-header p{margin:4px 0 0;color:var(--text-muted);font-size:11px}.detail-block{padding:13px 0;border-bottom:1px solid var(--border)}.detail-label{display:block;margin-bottom:8px;color:var(--text-muted);font:10px var(--font-mono);text-transform:uppercase}.detail-block ul{margin:0;padding-left:17px;color:var(--text);font-size:11px;line-height:1.65}.tag-list{display:flex;gap:6px;flex-wrap:wrap}.tag-list span{padding:4px 7px;border:1px solid var(--border);border-radius:999px;color:var(--text-muted);font-size:10px}.detail-block pre{max-height:180px;overflow:auto;margin:0;padding:9px;background:var(--surface-raised);border-radius:8px;color:var(--text);font:10px/1.5 var(--font-mono);white-space:pre-wrap}.profile-placeholder{color:var(--accent);font-size:30px}.muted{color:var(--text-muted);font-size:11px}@media(max-width:1250px){.dinq-workspace{grid-template-columns:180px minmax(240px,1fr) minmax(300px,1.2fr)}.workspace-detail{grid-column:2/-1}}@media(max-width:850px){.workspace-heading{flex-direction:column}.dinq-workspace{grid-template-columns:1fr}.workspace-detail{grid-column:auto}.workspace-sessions{order:-1}.activity-list,.candidate-list{max-height:none}}
</style>
