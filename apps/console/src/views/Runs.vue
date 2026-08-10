<template>
  <div class="page runs-page">
    <template v-if="!directRunId">
    <header class="page-heading">
      <div><span class="eyebrow">RUNTIME</span><h1>运行中心</h1><p>定位模型、Agent、Tool 与调度瓶颈，检查原始响应并创建可比较回放。</p></div>
      <div class="heading-actions"><button class="secondary-button" type="button" @click="loadRuns">刷新</button><router-link class="primary-button" to="/chat">创建 Run</router-link></div>
    </header>

    <section class="filter-bar">
      <label><span>状态</span><select v-model="statusFilter" @change="resetAndLoad"><option value="">全部状态</option><option v-for="item in statuses" :key="item" :value="item">{{ statusLabel(item) }}</option></select></label>
      <label><span>Agent</span><select v-model="agentFilter" @change="resetAndLoad"><option value="">全部 Agent</option><option v-for="agent in agents" :key="agent.id" :value="agent.id">{{ agent.name || agent.id }}</option></select></label>
      <label class="search-field"><span>筛选</span><div class="search-control"><input v-model="search" placeholder="Run ID、Session 或摘要" @keyup.enter="resetAndLoad" /><small>按 Enter 搜索</small></div></label>
      <span class="result-count">第 {{ page }} / {{ totalPages }} 页 · {{ totalRuns }} Runs</span>
    </section>

    <div v-if="error" class="notice error-notice">{{ error }}</div>
    <section class="panel run-table-panel">
      <div class="data-table-wrap">
        <table class="data-table">
          <thead><tr><th>状态</th><th>运行</th><th>Agent / Session</th><th>进度</th><th>更新时间</th><th></th></tr></thead>
          <tbody>
            <tr v-for="run in runs" :key="run.run_id" :class="{ selected: selected?.run_id === run.run_id }" @click="openRun(run)">
              <td><span class="status-badge" :class="run.status">{{ statusLabel(run.status) }}</span></td>
              <td><strong class="table-primary">{{ run.status_summary || truncate(run.prompt || 'Agent Run', 64) }}</strong><code>{{ shortId(run.run_id) }}</code></td>
              <td><strong>{{ run.agent_id }}</strong><small>{{ run.session_id }}</small></td>
              <td><div class="task-count"><span>{{ run.completed_task_count ?? 0 }}/{{ run.total_task_count ?? 0 }}</span><i><b :style="{ width: progress(run) + '%' }" /></i></div></td>
              <td><time>{{ formatDate(run.updated_at || run.created_at) }}</time></td>
              <td><button class="row-action" type="button">查看 →</button></td>
            </tr>
          </tbody>
        </table>
      </div>
      <div v-if="!loading && !runs.length" class="empty-state"><span>◎</span><strong>没有匹配的运行</strong><p>更改筛选条件，或从 Agent 试用创建新 Run。</p></div>
      <div v-if="loading" class="loading-state">正在读取 Runtime Store…</div>
      <footer v-if="totalRuns" class="pagination-bar">
        <span>每页 {{ pageSize }} 条，共 {{ totalRuns }} 条</span>
        <div><button class="secondary-button" type="button" :disabled="loading || page <= 1" @click="goToPage(page - 1)">上一页</button><strong>{{ page }} / {{ totalPages }}</strong><button class="secondary-button" type="button" :disabled="loading || page >= totalPages" @click="goToPage(page + 1)">下一页</button></div>
      </footer>
    </section>
    </template>

    <div v-if="selected" :class="directRunId ? 'run-detail-page' : 'detail-backdrop'" @click.self="!directRunId && closeDetail()">
      <aside class="run-detail" :class="{ standalone: Boolean(directRunId) }">
        <header class="detail-header">
          <div><span class="status-badge" :class="selected.status">{{ statusLabel(selected.status) }}</span><h2>{{ selected.status_summary || 'Run detail' }}</h2><code>{{ selected.run_id }}</code></div>
          <button class="icon-button" type="button" @click="closeDetail">×</button>
        </header>
        <div class="detail-facts">
          <div><span>Agent</span><strong>{{ selected.agent_id }}</strong></div><div><span>Session</span><strong>{{ selected.session_id }}</strong></div><div><span>类型</span><strong>{{ selected.kind || 'agent' }}</strong></div><div><span>更新时间</span><strong>{{ formatDate(selected.updated_at || selected.created_at) }}</strong></div>
        </div>
        <div class="trace-metrics">
          <div><span>模型调用</span><strong>{{ modelInvocations.length }}</strong></div>
          <div><span>模型耗时</span><strong>{{ totalModelDuration.toLocaleString() }} ms</strong></div>
          <div><span>Token</span><strong>{{ totalModelTokens.toLocaleString() }}</strong></div>
          <div><span>推理内容</span><strong>{{ reasoningChars.toLocaleString() }} 字符</strong></div>
        </div>
        <div class="detail-actions" v-if="isActive(selected.status)"><button class="danger-button" type="button" @click="cancelSelected">取消 Run</button></div>
        <nav class="detail-tabs"><button v-for="tab in tabs" :key="tab.key" :class="{ active: activeTab === tab.key }" @click="activeTab = tab.key">{{ tab.label }} <span>{{ tab.count }}</span></button></nav>
        <div class="detail-body">
          <section v-if="activeTab === 'timeline'">
            <ExecutionTimeline :events="events" :active="isActive(selected.status)" :expanded-by-default="true" />
            <div v-if="!events.length" class="empty-state compact">暂无公共事件</div>
          </section>
          <section v-else-if="activeTab === 'models'" class="model-call-list">
            <article v-for="item in modelInvocations" :key="item.invocation_id" class="model-call-card">
              <header>
                <div><span class="status-badge" :class="item.status">{{ item.status }}</span><strong>{{ item.provider }} / {{ item.model }}</strong></div>
                <code>{{ item.duration_ms ?? 0 }} ms · TTFT {{ item.ttft_ms ?? '—' }} ms</code>
              </header>
              <div class="model-call-facts">
                <span>TURN <b>{{ shortId(item.turn_id || '—') }}</b></span><span>ATTEMPT <b>{{ item.attempt }}</b></span><span>FINISH <b>{{ item.finish_reason || '—' }}</b></span><span>REASONING <b>{{ reasoningLabel(item.reasoning_availability) }}</b></span><span>CACHE <b>{{ item.cache_status }}</b></span>
              </div>
              <div class="model-call-usage"><code>{{ Number(item.usage.input_tokens || item.usage.prompt_tokens || 0).toLocaleString() }} input</code><code>{{ Number(item.usage.output_tokens || item.usage.completion_tokens || 0).toLocaleString() }} output</code><code>${{ Number(item.cost_usd || 0).toFixed(6) }}</code></div>
              <div class="raw-actions"><button type="button" :disabled="!item.request_blob_id || rawLoading" @click="openBlob(item.request_blob_id)">查看完整请求</button><button type="button" :disabled="!item.response_blob_id || rawLoading" @click="openBlob(item.response_blob_id)">查看原始响应</button></div>
            </article>
            <div v-if="!modelInvocations.length" class="empty-state compact">暂无模型调用。新 Run 将记录完整请求、响应与首 Token 时延。</div>
          </section>
          <section v-else-if="activeTab === 'reasoning'" class="reasoning-list">
            <div class="reasoning-notice"><strong>推理真实性分级</strong><p><code>provider_native</code> 是供应商实际返回内容；<code>model_declared</code> 是模型按协议声明的决策，不会冒充内部状态。</p></div>
            <article v-for="item in reasoning" :key="item.segment_id">
              <header><div><span class="reasoning-source" :class="item.source">{{ reasoningLabel(item.source) }}</span><strong>{{ item.kind }}</strong></div><small>{{ item.fidelity }} · #{{ item.sequence }} · {{ formatTime(item.created_at || undefined) }}</small></header>
              <pre>{{ item.content }}</pre>
            </article>
            <div v-if="!reasoning.length" class="empty-state compact"><span>∅</span><strong>Provider 没有返回可保存的原始推理</strong><p>模型调用页仍可查看完整请求与响应，记录会明确标记 unavailable。</p></div>
          </section>
          <section v-else-if="activeTab === 'spans'" class="span-waterfall">
            <header><strong>执行瀑布</strong><span>{{ Math.max(0, traceRange.end - traceRange.start).toLocaleString() }} ms 时间窗口</span></header>
            <article v-for="span in spans" :key="span.span_id">
              <div class="span-name"><span :class="`span-kind ${span.span_kind}`">{{ span.span_kind }}</span><strong>{{ span.name }}</strong><small>{{ span.duration_ms ?? 0 }} ms<span v-if="span.ttft_ms != null"> · TTFT {{ span.ttft_ms }} ms</span></small></div>
              <div class="span-track"><i :class="span.status" :style="{ left: spanLeft(span) + '%', width: spanWidth(span) + '%' }" /></div>
            </article>
            <div v-if="!spans.length" class="empty-state compact">暂无统一 Span</div>
          </section>
          <section v-else-if="activeTab === 'tasks'" class="detail-list">
            <article v-for="task in tasks" :key="task.task_id"><span class="status-badge" :class="task.status">{{ statusLabel(task.status) }}</span><div><strong>{{ task.name || shortId(task.task_id) }}</strong><small>{{ task.agent_id }} · attempt {{ task.attempt ?? 0 }}/{{ task.max_attempts ?? 1 }}</small></div></article>
            <div v-if="!tasks.length" class="empty-state compact">该 Run 没有 DAG Task</div>
          </section>
          <section v-else-if="activeTab === 'patches'" class="patch-proposal-list">
            <div class="reasoning-notice"><strong>GraphPatch 审批</strong><p>运行中的 Agent 可以提出图变更，但候选 Revision 必须经过独立批准后才能激活；拒绝不会改动当前执行图。</p></div>
            <article v-for="item in graphProposals" :key="item.proposal_id">
              <header><div><span class="status-badge" :class="item.status">{{ item.status }}</span><strong>{{ item.reason }}</strong></div><small>{{ formatDate(item.created_at) }}</small></header>
              <p>{{ item.proposer_type }}: {{ item.proposer_id }} · risk {{ item.risk_level }} · base {{ item.base_revision_id }}</p>
              <div v-if="item.status === 'pending'" class="proposal-actions"><button class="primary-button" @click="resolveProposal(item.proposal_id, 'approve')">批准并激活</button><button class="danger-button" @click="resolveProposal(item.proposal_id, 'reject')">拒绝</button></div>
              <small v-else>{{ item.resolver_id || 'system' }} · {{ item.resolution_note || '无备注' }}</small>
            </article>
            <div v-if="!graphProposals.length" class="empty-state compact">该 Run 没有待审批图变更</div>
          </section>
          <section v-else-if="activeTab === 'logs'" class="log-list">
            <article v-for="log in logs" :key="log.sequence"><time>{{ formatTime(log.created_at) }}</time><span :class="`log-level ${log.level}`">{{ log.level }}</span><code>{{ log.stage }}</code><p>{{ log.message }}</p><pre v-if="Object.keys(log.data || {}).length">{{ preview(log.data) }}</pre></article>
            <div v-if="!logs.length" class="empty-state compact">暂无结构化日志</div>
          </section>
          <section v-else-if="activeTab === 'invocations'" class="artifact-list">
            <article v-for="item in invocations" :key="item.invocation_id"><span>⌁</span><div><strong>{{ item.capability_id }}</strong><small>{{ item.status }} · {{ item.invocation_id }}</small><pre>{{ preview({ input: item.input, result: item.result, error: item.error }) }}</pre></div></article>
            <div v-if="!invocations.length" class="empty-state compact">暂无能力调用</div>
          </section>
          <section v-else-if="activeTab === 'feedback'" class="feedback-list">
            <div class="reasoning-notice"><strong>人工反馈</strong><p>反馈绑定到本次 Run、执行时的 Agent Revision 和用户看到的输出片段，可用于定位问题、回放和后续质量改进。</p></div>
            <article v-for="item in feedback" :key="item.feedback_id">
              <header><div><span class="status-badge" :class="item.status">{{ feedbackLabel(item.feedback_type) }}</span><strong>{{ item.rating || '未评分' }}</strong></div><small>{{ formatDate(item.created_at || undefined) }}</small></header>
              <p>{{ item.comment }}</p>
              <pre v-if="item.output_excerpt">输出快照：{{ item.output_excerpt }}</pre>
              <small>用户 {{ item.user_id }} · Revision {{ item.agent_revision_id || '—' }} · {{ item.feedback_id }}</small>
            </article>
            <div v-if="!feedback.length" class="empty-state compact">暂无人工反馈。可在 Agent 试用页面对 AI 输出提交意见。</div>
          </section>
          <section v-else-if="activeTab === 'replays'" class="replay-view">
            <div class="replay-form">
              <label><span>回放模式</span><select v-model="replayMode"><option value="offline">Offline · 使用存档验证</option><option value="frozen">Frozen · 冻结结果复现</option><option value="branch">Branch · 从原 Run 分支</option><option value="live">Live · 重新执行</option></select></label>
              <label><span>覆盖模型（可选）</span><input v-model="replayModel" placeholder="例如 provider/exact-model..." /></label>
              <label class="replay-prompt"><span>覆盖 Prompt（Branch/Live 可选）</span><textarea v-model="replayPrompt" rows="4" placeholder="留空使用原始输入" /></label>
              <button class="primary-button" type="button" :disabled="replaying" @click="createReplay">{{ replaying ? '创建中…' : '创建回放' }}</button>
            </div>
            <div class="replay-list">
              <article v-for="item in replays" :key="item.replay_id"><header><div><span class="status-badge" :class="item.status">{{ item.mode }}</span><strong>{{ item.replay_id }}</strong></div><small>{{ formatDate(item.created_at || undefined) }}</small></header><p v-if="item.new_run_id">新 Run：<router-link :to="{ query: { run: item.new_run_id } }">{{ item.new_run_id }}</router-link></p><pre v-if="item.comparison">{{ preview(item.comparison) }}</pre></article>
              <div v-if="!replays.length" class="empty-state compact">尚未创建回放实验</div>
            </div>
          </section>
          <section v-else-if="activeTab === 'traces'" class="artifact-list">
            <article v-for="(item, index) in traces" :key="String(item.event_id || index)"><span>↳</span><div><strong>{{ item.operation }} · {{ item.stage }}</strong><small>{{ item.transport }} · {{ item.status || 'event' }}</small><pre>{{ preview(item.data) }}</pre></div></article>
            <div v-if="!traces.length" class="empty-state compact">暂无请求链路 Trace</div>
          </section>
          <section v-else-if="activeTab === 'children'" class="detail-list">
            <article v-for="run in children" :key="run.run_id"><span class="status-badge" :class="run.status">{{ statusLabel(run.status) }}</span><div><strong>{{ run.status_summary || run.prompt }}</strong><small>{{ run.agent_id }} · {{ run.run_id }}</small></div></article>
            <div v-if="!children.length" class="empty-state compact">没有动态子 Agent Run</div>
          </section>
          <section v-else-if="activeTab === 'artifacts'" class="artifact-list">
            <article v-for="artifact in artifacts" :key="artifact.artifact_id"><span>▧</span><div><strong>{{ artifact.name }}</strong><small>{{ artifact.media_type }} · {{ formatDate(artifact.created_at) }}</small><pre v-if="artifact.content != null">{{ preview(artifact.content) }}</pre><router-link class="artifact-to-work" :to="{ path: '/works', query: { run: selected.run_id, artifact: artifact.artifact_id } }">形成成果作品 →</router-link></div></article>
            <div v-if="!artifacts.length" class="empty-state compact">暂无执行产物</div>
          </section>
          <section v-else class="result-view"><h3>输入</h3><pre>{{ selected.prompt }}</pre><h3>最终输出</h3><pre>{{ selected.result?.content || selected.error?.message || '尚未产生最终输出' }}</pre></section>
        </div>
      </aside>
    </div>
    <div v-else-if="directRunId && loading" class="loading-state">正在读取 Run 详情…</div>
    <div v-if="rawViewer" class="raw-backdrop" @click.self="rawViewer = null">
      <section class="raw-viewer"><header><div><span class="eyebrow">FULL FIDELITY</span><h2>{{ rawViewer.kind }}</h2><small>{{ formatBytes(rawViewer.size_bytes) }} · SHA-256 {{ rawViewer.sha256 }}</small></div><button class="icon-button" type="button" @click="rawViewer = null">×</button></header><pre>{{ JSON.stringify(rawViewer.content, null, 2) }}</pre></section>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useMessage } from 'naive-ui'
import { getAgents, type AgentListItem } from '../api/agent'
import {
  cancelAdminRun,
  createRunReplay,
  getAdminRunDiagnostics,
  getTraceBlob,
  listAdminRuns,
  listRunReplays,
  streamAdminRunEvents,
  type ExecutionSpan,
  type ModelInvocation,
  type ReasoningSegment,
  type ReplayRun,
  type TraceBlob,
} from '../api/admin'
import { listGraphPatchProposals, resolveGraphPatchProposal, type GraphPatchProposal, type RunFeedback, type RuntimeArtifact, type RuntimeEvent, type RuntimeInvocation, type RuntimeLog, type RuntimeRun, type RuntimeTask } from '../api/runtime'
import ExecutionTimeline from '../components/ExecutionTimeline.vue'

const route = useRoute(); const router = useRouter(); const message = useMessage()
const statuses = ['queued', 'running', 'completed', 'failed', 'cancelled', 'timed_out', 'paused', 'waiting_input']
const runs = ref<RuntimeRun[]>([]); const agents = ref<AgentListItem[]>([]); const loading = ref(false); const error = ref('')
const statusFilter = ref(''); const agentFilter = ref(''); const search = ref(''); const selected = ref<RuntimeRun | null>(null)
const pageSize = 10; const page = ref(1); const totalRuns = ref(0); const totalPages = ref(1)
const directRunId = computed(() => typeof route.params.runId === 'string' ? route.params.runId : '')
const events = ref<RuntimeEvent[]>([]); const tasks = ref<RuntimeTask[]>([]); const logs = ref<RuntimeLog[]>([]); const artifacts = ref<RuntimeArtifact[]>([]); const activeTab = ref('timeline')
const invocations = ref<RuntimeInvocation[]>([]); const traces = ref<Array<Record<string, unknown>>>([]); const children = ref<RuntimeRun[]>([])
const spans = ref<ExecutionSpan[]>([]); const modelInvocations = ref<ModelInvocation[]>([]); const reasoning = ref<ReasoningSegment[]>([]); const replays = ref<ReplayRun[]>([])
const feedback = ref<RunFeedback[]>([])
const graphProposals = ref<GraphPatchProposal[]>([])
const rawViewer = ref<TraceBlob | null>(null); const rawLoading = ref(false); const replaying = ref(false); const replayMode = ref<ReplayRun['mode']>('offline'); const replayModel = ref(''); const replayPrompt = ref('')
let streamAbort: AbortController | null = null; const seenEvents = new Set<string>()

const tabs = computed(() => [{ key: 'timeline', label: '时间线', count: events.value.length }, { key: 'models', label: '模型调用', count: modelInvocations.value.length }, { key: 'reasoning', label: '原始推理', count: reasoning.value.length }, { key: 'spans', label: '性能', count: spans.value.length }, { key: 'tasks', label: 'Tasks', count: tasks.value.length }, { key: 'patches', label: '图变更', count: graphProposals.value.length }, { key: 'invocations', label: '工具', count: invocations.value.length }, { key: 'feedback', label: '人工反馈', count: feedback.value.length }, { key: 'replays', label: '回放', count: replays.value.length }, { key: 'traces', label: 'HTTP Trace', count: traces.value.length }, { key: 'children', label: '子 Agent', count: children.value.length }, { key: 'logs', label: '日志', count: logs.value.length }, { key: 'artifacts', label: '产物', count: artifacts.value.length }, { key: 'result', label: '输入 / 输出', count: 0 }])
const totalModelTokens = computed(() => modelInvocations.value.reduce((sum, item) => sum + Number(item.usage?.total_tokens || 0), 0))
const totalModelDuration = computed(() => modelInvocations.value.reduce((sum, item) => sum + Number(item.duration_ms || 0), 0))
const reasoningChars = computed(() => reasoning.value.reduce((sum, item) => sum + item.content.length, 0))
const traceRange = computed(() => {
  const starts = spans.value.map((item) => item.started_at ? Date.parse(item.started_at) : NaN).filter(Number.isFinite)
  const ends = spans.value.map((item) => item.finished_at ? Date.parse(item.finished_at) : (item.started_at ? Date.parse(item.started_at) + Number(item.duration_ms || 1) : NaN)).filter(Number.isFinite)
  return { start: starts.length ? Math.min(...starts) : 0, end: ends.length ? Math.max(...ends) : 1 }
})

async function loadRuns() { loading.value = true; error.value = ''; try { const response = await listAdminRuns({ status: statusFilter.value || undefined, agentId: agentFilter.value || undefined, search: search.value || undefined, page: page.value, limit: pageSize }); runs.value = response.items; page.value = response.pagination.page; totalRuns.value = response.pagination.total; totalPages.value = response.pagination.total_pages; if (selected.value) selected.value = runs.value.find((item) => item.run_id === selected.value?.run_id) ?? selected.value } catch (e) { error.value = e instanceof Error ? e.message : '读取平台运行失败' } finally { loading.value = false } }
function resetAndLoad() { page.value = 1; void loadRuns() }
function goToPage(value: number) { page.value = Math.max(1, Math.min(totalPages.value, value)); void loadRuns() }
async function openRun(run: Pick<RuntimeRun, 'run_id'>) { streamAbort?.abort(); loading.value = true; try { const detail = await getAdminRunDiagnostics(run.run_id); selected.value = detail.run; events.value = detail.events; tasks.value = detail.tasks; logs.value = detail.logs; artifacts.value = detail.artifacts; invocations.value = detail.invocations; traces.value = detail.traces; children.value = detail.children; spans.value = detail.spans || []; modelInvocations.value = detail.model_invocations || []; reasoning.value = detail.reasoning || []; replays.value = detail.replays || []; feedback.value = detail.feedback || []; graphProposals.value = await listGraphPatchProposals(run.run_id).catch(() => []); rawViewer.value = null; replayPrompt.value = ''; seenEvents.clear(); events.value.forEach((event) => seenEvents.add(event.event_id)); activeTab.value = 'timeline'; if (!directRunId.value) await router.push({ name: 'RunDetail', params: { runId: run.run_id } }); streamAbort = new AbortController(); const cursor = Math.max(0, ...events.value.map((event) => event.sequence || 0)); void streamAdminRunEvents(run.run_id, (event) => { if (!seenEvents.has(event.event_id)) { seenEvents.add(event.event_id); events.value.push(event); if (event.type === 'model.reasoning.delta' && event.data.content) reasoning.value.push({ segment_id: event.event_id, invocation_id: '', run_id: run.run_id, sequence: event.sequence, source: 'provider_native', kind: 'analysis', content_format: 'text', fidelity: 'exact', content: String(event.data.content), created_at: event.created_at }) } }, { afterSequence: cursor, signal: streamAbort.signal }).catch(() => undefined) } catch (e) { error.value = e instanceof Error ? e.message : '读取 Run 详情失败' } finally { loading.value = false } }
function closeDetail() { streamAbort?.abort(); selected.value = null; if (directRunId.value) { void router.push({ name: 'Runs' }); return } void router.replace({ query: { ...route.query, run: undefined } }) }
async function cancelSelected() { if (!selected.value) return; try { selected.value = await cancelAdminRun(selected.value.run_id); message.success('已提交平台取消请求'); await loadRuns() } catch (e) { message.error(e instanceof Error ? e.message : '取消失败') } }
function statusLabel(status: string) { return ({ queued: '排队', blocked: '阻塞', running: '运行中', completed: '完成', failed: '失败', cancelled: '取消', timed_out: '超时', skipped: '跳过', paused: '暂停', waiting_input: '等待输入' } as Record<string, string>)[status] ?? status }
function isActive(status: string) { return ['queued', 'running', 'paused', 'waiting_input'].includes(status) }
function progress(run: RuntimeRun) { return run.total_task_count ? Math.round(((run.completed_task_count ?? 0) / run.total_task_count) * 100) : (run.status === 'completed' ? 100 : 0) }
function shortId(value: string) { return value.length > 16 ? `${value.slice(0, 8)}…${value.slice(-5)}` : value }
function truncate(value: string, size: number) { return value.length > size ? `${value.slice(0, size)}…` : value }
function formatDate(value?: string) { return value ? new Date(value).toLocaleString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' }) : '—' }
function formatTime(value?: string) { return value ? new Date(value).toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit', second: '2-digit' }) : '—' }
function preview(value: unknown) { const text = typeof value === 'string' ? value : JSON.stringify(value, null, 2); return truncate(text, 2000) }
function formatBytes(value: number) { if (value < 1024) return `${value} B`; if (value < 1024 * 1024) return `${(value / 1024).toFixed(1)} KB`; return `${(value / 1024 / 1024).toFixed(1)} MB` }
function reasoningLabel(value: string) { return ({ provider_native: 'Provider 原始推理', provider_summary: 'Provider 推理摘要', model_declared: '模型决策记录', runtime_decision: '系统决策', unavailable: '不可获取' } as Record<string, string>)[value] || value }
function feedbackLabel(value: string) { return ({ incorrect: '结果不对', missing_data: '缺少数据', needs_optimization: '需要优化', helpful: '很有帮助', other: '其他' } as Record<string, string>)[value] || value }
function spanLeft(span: ExecutionSpan) { const start = span.started_at ? Date.parse(span.started_at) : traceRange.value.start; const range = Math.max(1, traceRange.value.end - traceRange.value.start); return Math.max(0, Math.min(98, ((start - traceRange.value.start) / range) * 100)) }
function spanWidth(span: ExecutionSpan) { const range = Math.max(1, traceRange.value.end - traceRange.value.start); return Math.max(1, Math.min(100, (Number(span.duration_ms || 1) / range) * 100)) }
async function openBlob(blobId?: string | null) { if (!selected.value || !blobId) return; rawLoading.value = true; try { rawViewer.value = await getTraceBlob(selected.value.run_id, blobId) } catch (e) { message.error(e instanceof Error ? e.message : '读取原始数据失败') } finally { rawLoading.value = false } }
async function createReplay() { if (!selected.value) return; replaying.value = true; try { const replay = await createRunReplay(selected.value.run_id, { mode: replayMode.value, model: replayModel.value.trim() || undefined, prompt: replayPrompt.value.trim() || undefined }); message.success(replay.new_run_id ? `回放 Run 已创建：${shortId(replay.new_run_id)}` : '离线回放快照已创建'); replays.value = await listRunReplays(selected.value.run_id) } catch (e) { message.error(e instanceof Error ? e.message : '创建回放失败') } finally { replaying.value = false } }
async function resolveProposal(proposalId: string, resolution: 'approve' | 'reject') { if (!selected.value) return; try { await resolveGraphPatchProposal(selected.value.run_id, proposalId, resolution); graphProposals.value = await listGraphPatchProposals(selected.value.run_id); message.success(resolution === 'approve' ? '图变更已批准并激活' : '图变更已拒绝') } catch (e) { message.error(e instanceof Error ? e.message : 'GraphPatch 审批失败') } }

onMounted(async () => { const legacyRunId = typeof route.query.run === 'string' ? route.query.run : ''; const runId = directRunId.value || legacyRunId; if (runId) { if (!directRunId.value) await router.replace({ name: 'RunDetail', params: { runId } }); await openRun({ run_id: runId }); return } const response = await getAgents().catch(() => ({ ok: false, agents: [] })); agents.value = response.agents; await loadRuns() })
onUnmounted(() => { streamAbort?.abort() })
</script>

<style>
.pagination-bar { display: flex; align-items: center; justify-content: space-between; gap: 16px; padding: 16px 20px; border-top: 1px solid var(--border); color: var(--muted); font-size: 13px; }
.pagination-bar > div { display: flex; align-items: center; gap: 12px; }
.pagination-bar .secondary-button { min-height: 34px; padding: 6px 12px; }
.run-detail-page { min-height: calc(100vh - 110px); }
.run-detail.standalone { position: relative; inset: auto; width: 100%; height: auto; min-height: calc(100vh - 110px); border: 1px solid var(--border); border-radius: 18px; box-shadow: none; }
.patch-proposal-list { display: grid; gap: 10px; }.patch-proposal-list article { padding: 13px; border: 1px solid var(--border); border-radius: 10px; background: var(--surface-raised); }.patch-proposal-list header,.proposal-actions { display: flex; align-items: center; justify-content: space-between; gap: 9px; }.patch-proposal-list header>div { display: flex; align-items: center; gap: 8px; }.patch-proposal-list p,.patch-proposal-list small { color: var(--text-muted); font-size: 10px; }.proposal-actions { justify-content: flex-start; margin-top: 10px; }.artifact-to-work { display: inline-block; margin-top: 7px; color: var(--accent); font-size: 10px; }
@media (max-width: 700px) { .pagination-bar { align-items: flex-start; flex-direction: column; } }
</style>
