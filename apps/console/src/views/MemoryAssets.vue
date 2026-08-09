<template>
  <div class="page asset-page">
    <header class="asset-heading">
      <div>
        <router-link class="asset-back" to="/assets">◆ 资产中心</router-link>
        <span class="eyebrow">PRIVATE MEMORY</span>
        <h1>记忆中心</h1>
        <p>查看 Agent 长期保留了什么、依据什么写入，并在进入个人资产前完成确认与纠错。</p>
      </div>
      <div class="heading-actions">
        <router-link class="secondary-button" to="/agents">配置记忆策略</router-link>
        <button class="primary-button" type="button" :disabled="loading || !selectedAgentId" @click="loadAll">
          {{ loading ? '刷新中…' : '刷新记忆' }}
        </button>
      </div>
    </header>

    <section class="privacy-strip">
      <span class="privacy-mark">⌾</span>
      <div><strong>默认私有</strong><p>这里只展示当前用户在所选 Agent 下的记忆；候选内容确认后才会写入长期资产。</p></div>
      <code>user_id × agent_id × scope</code>
    </section>

    <div v-if="error" class="notice error-notice">{{ error }}</div>

    <div class="asset-layout">
      <aside class="asset-sidebar panel">
        <header><span class="eyebrow">AGENT SCOPE</span><strong>选择记忆空间</strong></header>
        <div class="agent-search"><input v-model.trim="agentSearch" type="search" placeholder="搜索 Agent" /></div>
        <div class="agent-list">
          <button
            v-for="agent in filteredAgents"
            :key="agent.agent_id"
            type="button"
            :class="{ active: selectedAgentId === agent.agent_id }"
            @click="selectAgent(agent.agent_id)"
          >
            <span>{{ agent.name.slice(0, 1).toUpperCase() }}</span>
            <div><strong>{{ agent.name }}</strong><small>{{ agent.agent_id }}</small></div>
            <i :class="agent.status" />
          </button>
          <div v-if="!filteredAgents.length && !loading" class="empty-state compact"><span>◇</span><strong>暂无 Agent</strong></div>
        </div>
        <footer>
          <span>记忆是运行资产</span>
          <p>策略随 Agent Revision 冻结，实际数据始终归当前用户所有。</p>
        </footer>
      </aside>

      <main class="asset-main">
        <section class="memory-metrics">
          <button :class="{ active: layer === 'all' }" type="button" @click="chooseLayer('all')"><span>全部文档</span><strong>{{ summary.total }}</strong><small>当前 Agent</small></button>
          <button v-for="item in layers" :key="item.id" :class="{ active: layer === item.id }" type="button" @click="chooseLayer(item.id)"><span>{{ item.label }}</span><strong>{{ summary.by_layer[item.id] || 0 }}</strong><small>{{ item.help }}</small></button>
          <button class="candidate-metric" :class="{ active: view === 'candidates' }" type="button" @click="view = 'candidates'"><span>待确认</span><strong>{{ pendingCount }}</strong><small>需要你的判断</small></button>
        </section>

        <section class="asset-workspace panel">
          <header class="workspace-toolbar">
            <div class="segmented">
              <button type="button" :class="{ active: view === 'documents' }" @click="view = 'documents'">已生效记忆</button>
              <button type="button" :class="{ active: view === 'candidates' }" @click="view = 'candidates'">候选收件箱 <span v-if="pendingCount">{{ pendingCount }}</span></button>
            </div>
            <template v-if="view === 'documents'">
              <input v-model.trim="search" type="search" placeholder="搜索路径或内容" @keyup.enter="loadDocuments" />
              <button class="secondary-button" type="button" @click="loadDocuments">搜索</button>
            </template>
            <select v-else v-model="candidateStatus">
              <option value="all">全部状态</option><option value="pending">待确认</option><option value="conflicted">有冲突</option><option value="merged">已合并</option><option value="rejected">已拒绝</option><option value="expired">已过期</option>
            </select>
          </header>

          <div v-if="!selectedAgentId" class="empty-state"><span>◇</span><strong>选择一个 Agent</strong><p>记忆按用户与 Agent 隔离，不会混合展示。</p></div>

          <div v-else-if="view === 'documents'" class="memory-browser">
            <div class="memory-list">
              <button v-for="item in documents" :key="`${item.scope_key}:${item.document_path}`" type="button" :class="{ active: sameDocument(item, selectedDocument) }" @click="selectDocument(item)">
                <div><span class="layer-tag">{{ layerLabel(item.layer) }}</span><small>v{{ item.version }}</small></div>
                <strong>{{ item.document_path }}</strong>
                <p>{{ item.preview || '空文档' }}</p>
                <time>{{ formatTime(item.updated_at_ms) }} · {{ formatBytes(item.size_bytes) }}</time>
              </button>
              <div v-if="!documents.length && !loading" class="empty-state"><span>◇</span><strong>还没有生效记忆</strong><p>Agent 会依据已发布策略创建候选或写入记忆。</p></div>
            </div>
            <article class="memory-detail">
              <template v-if="documentDetail">
                <header><div><span class="layer-tag">{{ layerLabel(documentDetail.layer) }}</span><h2>{{ documentDetail.document_path }}</h2></div><span>VERSION {{ documentDetail.version }}</span></header>
                <pre>{{ documentDetail.content || '（空文档）' }}</pre>
                <footer><div><span>作用域</span><code>{{ documentDetail.scope_key }}</code></div><div><span>更新时间</span><strong>{{ formatTime(documentDetail.updated_at_ms) }}</strong></div><div><span>大小</span><strong>{{ formatBytes(documentDetail.size_bytes) }}</strong></div></footer>
              </template>
              <div v-else class="empty-state"><span>▤</span><strong>选择一份记忆</strong><p>内容、版本与真实作用域会在这里展示。</p></div>
            </article>
          </div>

          <div v-else class="candidate-list">
            <article v-for="item in filteredCandidates" :key="item.candidate_id">
              <header>
                <div><span class="layer-tag">{{ layerLabel(item.layer) }}</span><strong>{{ item.document_path }}</strong></div>
                <span class="candidate-state" :class="item.status">{{ candidateLabel(item.status) }}</span>
              </header>
              <pre>{{ item.content }}</pre>
              <div class="candidate-evidence">
                <span>{{ item.operation === 'replace' ? '替换文档' : '追加内容' }}</span><span>{{ item.source_kind }}</span><span v-if="item.confidence != null">置信度 {{ Math.round(item.confidence * 100) }}%</span><router-link v-if="item.source_run_id" :to="`/runs/${item.source_run_id}`">查看来源 Run →</router-link>
              </div>
              <footer>
                <time>{{ formatDate(item.created_at) }}</time>
                <div v-if="item.status === 'pending' || item.status === 'conflicted'">
                  <button class="secondary-button reject-button" type="button" :disabled="resolvingId === item.candidate_id" @click="resolve(item, 'reject')">拒绝</button>
                  <button v-if="item.status === 'pending'" class="primary-button" type="button" :disabled="resolvingId === item.candidate_id" @click="resolve(item, 'accept')">确认写入</button>
                </div>
              </footer>
            </article>
            <div v-if="!filteredCandidates.length && !loading" class="empty-state"><span>✓</span><strong>此状态下没有候选</strong><p>没有符合当前筛选条件的长期记忆变更。</p></div>
          </div>
        </section>
      </main>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { useMessage } from 'naive-ui'
import { useRoute, useRouter } from 'vue-router'
import { getAdminAgents, type AdminAgent } from '../api/admin'
import {
  getMemoryCandidates,
  getMemoryDocument,
  getMemoryDocuments,
  resolveMemoryCandidate,
  type MemoryCandidate,
  type MemoryDocument,
  type MemoryDocumentListItem,
  type MemoryLayer,
} from '../api/memory'

const message = useMessage()
const route = useRoute()
const router = useRouter()
const agents = ref<AdminAgent[]>([])
const selectedAgentId = ref('')
const agentSearch = ref('')
const view = ref<'documents' | 'candidates'>('documents')
const layer = ref<MemoryLayer | 'all'>('all')
const search = ref('')
const candidateStatus = ref<MemoryCandidate['status'] | 'all'>('all')
const documents = ref<MemoryDocumentListItem[]>([])
const selectedDocument = ref<MemoryDocumentListItem | null>(null)
const documentDetail = ref<MemoryDocument | null>(null)
const candidates = ref<MemoryCandidate[]>([])
const loading = ref(false)
const error = ref('')
const resolvingId = ref('')
const summary = ref({ total: 0, by_layer: { profile: 0, long_term: 0, episodic: 0, agent: 0 } as Record<MemoryLayer, number> })
const layers: Array<{ id: MemoryLayer; label: string; help: string }> = [
  { id: 'profile', label: '个人属性', help: '偏好与稳定事实' }, { id: 'long_term', label: '长期记忆', help: '项目与持续上下文' }, { id: 'episodic', label: '情景记忆', help: '历史摘要与经历' }, { id: 'agent', label: 'Agent 经验', help: '专属工作方法' },
]

const filteredAgents = computed(() => { const term = agentSearch.value.toLowerCase(); return agents.value.filter((item) => !term || `${item.name} ${item.agent_id}`.toLowerCase().includes(term)) })
const filteredCandidates = computed(() => candidateStatus.value === 'all' ? candidates.value : candidates.value.filter((item) => item.status === candidateStatus.value))
const pendingCount = computed(() => candidates.value.filter((item) => item.status === 'pending' || item.status === 'conflicted').length)

async function initialize() {
  loading.value = true; error.value = ''
  try {
    agents.value = await getAdminAgents()
    const requested = String(route.query.agent || '')
    selectedAgentId.value = agents.value.some((item) => item.agent_id === requested) ? requested : (agents.value.find((item) => item.status === 'active')?.agent_id || agents.value[0]?.agent_id || '')
    if (selectedAgentId.value) await loadAll()
  } catch (value) { error.value = errorText(value) } finally { loading.value = false }
}

async function loadDocuments() {
  if (!selectedAgentId.value) return
  const result = await getMemoryDocuments(selectedAgentId.value, { layer: layer.value, search: search.value })
  documents.value = result.items; summary.value = result.summary
  const next = result.items.find((item) => sameDocument(item, selectedDocument.value)) || result.items[0]
  if (next) await selectDocument(next)
  else { selectedDocument.value = null; documentDetail.value = null }
}

async function loadCandidates() { if (selectedAgentId.value) candidates.value = (await getMemoryCandidates(selectedAgentId.value, 'all')).items }
async function loadAll() { if (!selectedAgentId.value) return; loading.value = true; error.value = ''; try { await Promise.all([loadDocuments(), loadCandidates()]) } catch (value) { error.value = errorText(value) } finally { loading.value = false } }
async function selectAgent(agentId: string) { selectedAgentId.value = agentId; selectedDocument.value = null; documentDetail.value = null; await router.replace({ query: { ...route.query, agent: agentId } }); await loadAll() }
async function selectDocument(item: MemoryDocumentListItem) { selectedDocument.value = item; documentDetail.value = await getMemoryDocument(selectedAgentId.value, item) }
function chooseLayer(value: MemoryLayer | 'all') { layer.value = value; view.value = 'documents'; void loadDocuments() }
function sameDocument(left?: MemoryDocumentListItem | null, right?: MemoryDocumentListItem | null) { return Boolean(left && right && left.scope_key === right.scope_key && left.document_path === right.document_path) }

async function resolve(item: MemoryCandidate, resolution: 'accept' | 'reject') {
  const verb = resolution === 'accept' ? '确认写入长期记忆' : '拒绝这条候选记忆'
  if (!window.confirm(`${verb}？此操作会记录在候选生命周期中。`)) return
  resolvingId.value = item.candidate_id
  try { await resolveMemoryCandidate(item.candidate_id, resolution); message.success(resolution === 'accept' ? '候选已写入记忆' : '候选已拒绝'); await loadAll() }
  catch (value) { message.error(errorText(value)) } finally { resolvingId.value = '' }
}

function layerLabel(value: MemoryLayer) { return ({ profile: '个人属性', long_term: '长期记忆', episodic: '情景记忆', agent: 'Agent 经验' } as const)[value] }
function candidateLabel(value: MemoryCandidate['status']) { return ({ pending: '待确认', merged: '已合并', rejected: '已拒绝', expired: '已过期', conflicted: '有冲突' } as const)[value] }
function errorText(value: unknown) { return value instanceof Error ? value.message : '记忆数据读取失败' }
function formatTime(value: number) { return new Date(value).toLocaleString('zh-CN') }
function formatDate(value: string) { return new Date(value).toLocaleString('zh-CN') }
function formatBytes(value: number) { return value < 1024 ? `${value} B` : value < 1048576 ? `${(value / 1024).toFixed(1)} KB` : `${(value / 1048576).toFixed(1)} MB` }

watch(view, (value) => { if (value === 'candidates' && selectedAgentId.value) void loadCandidates() })
onMounted(initialize)
</script>

<style scoped>
.asset-page{display:grid;gap:18px}.asset-heading{display:flex;align-items:flex-end;justify-content:space-between;gap:28px}.asset-heading h1{margin:7px 0 7px;color:var(--text-strong);font-size:clamp(29px,3vw,42px);line-height:1;letter-spacing:-.05em}.asset-heading p{max-width:760px;margin:0;color:var(--text-muted);font-size:13px}.asset-back{display:inline-flex;margin-bottom:18px;color:var(--text-muted);font:10px var(--font-mono)}.asset-back:hover{color:var(--accent)}.privacy-strip{display:grid;grid-template-columns:auto minmax(0,1fr) auto;gap:13px;align-items:center;padding:13px 16px;background:linear-gradient(90deg,var(--accent-subtle),transparent);border:1px solid var(--accent-border);border-radius:12px}.privacy-mark{display:grid;width:32px;height:32px;place-items:center;color:var(--accent);background:var(--surface);border-radius:9px}.privacy-strip div{display:flex;align-items:baseline;gap:9px}.privacy-strip strong{color:var(--text-strong);font-size:11px}.privacy-strip p{margin:0;color:var(--text-muted);font-size:10px}.privacy-strip code{color:var(--accent);font-size:9px}.asset-layout{display:grid;grid-template-columns:258px minmax(0,1fr);gap:15px;align-items:start}.asset-sidebar{position:sticky;top:calc(var(--topbar-height) + 18px);overflow:hidden}.asset-sidebar>header{display:grid;gap:6px;padding:17px;border-bottom:1px solid var(--border)}.asset-sidebar>header strong{color:var(--text-strong);font-size:13px}.agent-search{padding:11px}.agent-search input,.workspace-toolbar input,.workspace-toolbar select{width:100%;height:36px;padding:0 10px;color:var(--text);background:var(--input);border:1px solid var(--border);border-radius:8px;outline:none}.agent-list{max-height:430px;overflow:auto;padding:0 8px 10px}.agent-list button{display:grid;width:100%;grid-template-columns:34px minmax(0,1fr) auto;gap:9px;align-items:center;padding:10px;color:var(--text);background:transparent;border:1px solid transparent;border-radius:9px;text-align:left;cursor:pointer}.agent-list button:hover,.agent-list button.active{background:var(--accent-subtle);border-color:var(--accent-border)}.agent-list button>span{display:grid;width:34px;height:34px;place-items:center;color:var(--accent);background:var(--surface-raised);border:1px solid var(--border);border-radius:9px;font:600 11px var(--font-mono)}.agent-list button>div{min-width:0;display:grid}.agent-list strong,.agent-list small{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.agent-list strong{color:var(--text-strong);font-size:11px}.agent-list small{color:var(--text-muted);font:9px var(--font-mono)}.agent-list i{width:6px;height:6px;background:var(--success);border-radius:50%}.agent-list i.disabled{background:var(--warning)}.agent-list i.archived{background:var(--text-muted)}.asset-sidebar>footer{padding:14px 16px;background:var(--surface-raised);border-top:1px solid var(--border)}.asset-sidebar>footer span{color:var(--text-strong);font-size:10px;font-weight:600}.asset-sidebar>footer p{margin:4px 0 0;color:var(--text-muted);font-size:9px;line-height:1.6}.asset-main{min-width:0;display:grid;gap:13px}.memory-metrics{display:grid;grid-template-columns:repeat(6,minmax(0,1fr));gap:8px}.memory-metrics button{min-width:0;padding:13px;color:var(--text-muted);background:var(--surface);border:1px solid var(--border);border-radius:11px;text-align:left;cursor:pointer}.memory-metrics button:hover,.memory-metrics button.active{border-color:var(--accent-border);background:var(--accent-subtle)}.memory-metrics span,.memory-metrics strong,.memory-metrics small{display:block;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.memory-metrics span{font-size:9px}.memory-metrics strong{margin:6px 0 3px;color:var(--text-strong);font:600 22px var(--font-mono)}.memory-metrics small{font-size:8px}.memory-metrics .candidate-metric strong{color:var(--accent)}.asset-workspace{min-height:570px;overflow:hidden}.workspace-toolbar{display:grid;grid-template-columns:auto minmax(180px,1fr) auto;gap:9px;align-items:center;padding:12px;border-bottom:1px solid var(--border);background:var(--surface-raised)}.workspace-toolbar select{justify-self:end;max-width:180px}.segmented{display:flex;padding:3px;background:var(--input);border:1px solid var(--border);border-radius:9px}.segmented button{padding:7px 10px;color:var(--text-muted);background:transparent;border:0;border-radius:6px;font-size:10px;cursor:pointer}.segmented button.active{color:var(--text-strong);background:var(--surface-hover)}.segmented span{margin-left:4px;padding:1px 5px;color:var(--accent);background:var(--accent-subtle);border-radius:99px;font:8px var(--font-mono)}.memory-browser{display:grid;grid-template-columns:minmax(250px,.75fr) minmax(0,1.4fr);min-height:520px}.memory-list{max-height:650px;overflow:auto;border-right:1px solid var(--border)}.memory-list>button{display:grid;width:100%;gap:6px;padding:14px 16px;color:var(--text);background:transparent;border:0;border-bottom:1px solid var(--border);text-align:left;cursor:pointer}.memory-list>button:hover,.memory-list>button.active{background:var(--surface-hover);box-shadow:inset 2px 0 var(--accent)}.memory-list>button>div{display:flex;align-items:center;justify-content:space-between}.memory-list strong{color:var(--text-strong);font-size:11px}.memory-list p{display:-webkit-box;overflow:hidden;margin:0;color:var(--text-muted);font-size:10px;line-height:1.55;-webkit-box-orient:vertical;-webkit-line-clamp:2}.memory-list time,.memory-list small{color:var(--text-muted);font:8px var(--font-mono)}.layer-tag{display:inline-flex;padding:3px 7px;color:var(--accent);background:var(--accent-subtle);border-radius:6px;font:8px var(--font-mono)}.memory-detail{min-width:0;display:flex;flex-direction:column}.memory-detail>header{display:flex;align-items:flex-start;justify-content:space-between;gap:15px;padding:17px 19px;border-bottom:1px solid var(--border)}.memory-detail h2{margin:7px 0 0;color:var(--text-strong);font-size:14px}.memory-detail>header>span{color:var(--text-muted);font:8px var(--font-mono)}.memory-detail pre{min-height:380px;max-height:560px;overflow:auto;margin:0;padding:21px;white-space:pre-wrap;word-break:break-word;color:var(--text);background:var(--input);font-size:11px;line-height:1.75}.memory-detail>footer{display:grid;grid-template-columns:2fr 1fr 1fr;border-top:1px solid var(--border)}.memory-detail>footer div{min-width:0;display:grid;gap:3px;padding:12px 15px;border-right:1px solid var(--border)}.memory-detail>footer div:last-child{border:0}.memory-detail>footer span{color:var(--text-muted);font:8px var(--font-mono)}.memory-detail>footer code,.memory-detail>footer strong{overflow:hidden;color:var(--text);font-size:9px;text-overflow:ellipsis;white-space:nowrap}.candidate-list{display:grid;gap:10px;padding:13px}.candidate-list>article{padding:15px;background:var(--surface-raised);border:1px solid var(--border);border-radius:11px}.candidate-list article>header,.candidate-list article>footer{display:flex;align-items:center;justify-content:space-between;gap:12px}.candidate-list header>div{display:flex;align-items:center;gap:8px}.candidate-list header strong{color:var(--text-strong);font-size:11px}.candidate-state{padding:3px 7px;color:var(--warning);background:rgba(233,162,59,.1);border-radius:99px;font:8px var(--font-mono)}.candidate-state.merged{color:var(--success);background:rgba(50,182,122,.1)}.candidate-state.rejected,.candidate-state.expired{color:var(--text-muted);background:var(--surface-hover)}.candidate-state.conflicted{color:var(--danger);background:rgba(227,93,106,.1)}.candidate-list pre{max-height:230px;overflow:auto;margin:12px 0;padding:12px;white-space:pre-wrap;word-break:break-word;color:var(--text);background:var(--input);border:1px solid var(--border);border-radius:8px;font-size:10px;line-height:1.65}.candidate-evidence{display:flex;flex-wrap:wrap;gap:7px;margin-bottom:13px}.candidate-evidence span,.candidate-evidence a{padding:4px 7px;color:var(--text-muted);background:var(--surface);border:1px solid var(--border);border-radius:6px;font-size:8px}.candidate-evidence a{color:var(--accent)}.candidate-list time{color:var(--text-muted);font:8px var(--font-mono)}.candidate-list footer>div{display:flex;gap:7px}.candidate-list .primary-button,.candidate-list .secondary-button{min-height:31px;padding:0 11px;font-size:9px}.reject-button{color:var(--danger)}
@media(max-width:1100px){.asset-layout{grid-template-columns:220px minmax(0,1fr)}.memory-metrics{grid-template-columns:repeat(3,1fr)}.memory-browser{grid-template-columns:1fr}.memory-list{max-height:290px;border-right:0;border-bottom:1px solid var(--border)}}
@media(max-width:760px){.asset-heading{align-items:flex-start;flex-direction:column}.privacy-strip{grid-template-columns:auto 1fr}.privacy-strip code{display:none}.privacy-strip div{display:grid}.asset-layout{grid-template-columns:1fr}.asset-sidebar{position:static}.agent-list{max-height:210px}.memory-metrics{grid-template-columns:repeat(2,1fr)}.workspace-toolbar{grid-template-columns:1fr}.workspace-toolbar select{justify-self:stretch;max-width:none}.memory-detail>footer{grid-template-columns:1fr}.memory-detail>footer div{border-right:0;border-bottom:1px solid var(--border)}}
</style>
