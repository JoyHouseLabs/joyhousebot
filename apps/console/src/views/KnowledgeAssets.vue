<template>
  <div class="page knowledge-page">
    <header class="knowledge-heading">
      <div>
        <router-link class="asset-back" to="/assets">◆ 资产中心</router-link>
        <span class="eyebrow">PRIVATE KNOWLEDGE</span>
        <h1>知识中心</h1>
        <p>创建知识库组织可信资料，管理知识源、索引证据和长期演进边界。</p>
      </div>
      <div class="heading-actions">
        <button class="secondary-button" type="button" :disabled="loading" @click="loadAll">{{ loading ? '刷新中…' : '刷新索引' }}</button>
        <button class="primary-button" type="button" @click="openCreate">＋ 新建知识库</button>
        <router-link class="primary-button import-button" to="/chat">通过 Agent 导入</router-link>
      </div>
    </header>

    <section class="knowledge-boundary">
      <div class="boundary-icon">K</div>
      <div><span>当前访问边界</span><strong>知识库与知识源仅当前用户可见</strong><p>一个知识源可以加入多个知识库；删除知识库只移除集合和绑定，不会删除原始知识源。</p></div>
      <span class="boundary-state"><i /> PRIVATE</span>
    </section>

    <section class="knowledge-metrics">
      <article><span>知识库</span><strong>{{ bases.length }}</strong><small>{{ activeBaseCount }} 个正在使用</small></article>
      <article><span>知识源</span><strong>{{ summary.total }}</strong><small>独立、可重复归类</small></article>
      <article><span>文本分块</span><strong>{{ summary.chunks }}</strong><small>可用于带证据检索</small></article>
      <article><span>索引内容</span><strong>{{ formatBytes(summary.size_bytes) }}</strong><small>PostgreSQL 私有索引</small></article>
    </section>

    <div v-if="error" class="notice error-notice">{{ error }}</div>

    <section class="knowledge-workspace panel">
      <aside class="base-column">
        <header><div><span class="eyebrow">LIBRARIES</span><strong>知识库</strong></div><button type="button" title="新建知识库" @click="openCreate">＋</button></header>
        <div class="base-list">
          <button type="button" class="base-item all-base" :class="{ active: selectedBaseId === 'all' }" @click="selectBase('all')">
            <span class="base-icon">◇</span><div><strong>全部知识源</strong><small>不按知识库筛选</small></div><b>{{ summary.total }}</b>
          </button>
          <button v-for="item in bases" :key="item.knowledge_base_id" type="button" class="base-item" :class="{ active: selectedBaseId === item.knowledge_base_id, archived: item.status === 'archived' }" @click="selectBase(item.knowledge_base_id)">
            <span class="base-icon">{{ item.name.slice(0, 1).toUpperCase() }}</span>
            <div><strong>{{ item.name }}</strong><small>{{ item.description || '暂无说明' }}</small></div>
            <b>{{ item.document_count }}</b>
          </button>
          <div v-if="!bases.length && !loading" class="base-empty"><span>＋</span><strong>创建第一个知识库</strong><p>按项目、领域或长期目标组织知识源。</p><button type="button" @click="openCreate">新建知识库</button></div>
        </div>
        <footer v-if="selectedBase">
          <div><span :class="selectedBase.status">{{ selectedBase.status === 'active' ? '使用中' : '已归档' }}</span><small>{{ selectedBase.document_count }} SOURCES · {{ selectedBase.chunk_count }} CHUNKS</small></div>
          <div><button type="button" @click="openEdit(selectedBase)">编辑</button><button type="button" @click="toggleArchive(selectedBase)">{{ selectedBase.status === 'active' ? '归档' : '恢复' }}</button><button class="delete-link" type="button" @click="removeBase(selectedBase)">删除</button></div>
        </footer>
      </aside>

      <aside class="source-column">
        <header>
          <div><span class="eyebrow">SOURCES</span><strong>{{ selectedBase?.name || '全部知识源' }}</strong><p>{{ selectedBase?.description || '查看当前用户全部已索引资料' }}</p></div>
          <span>{{ documents.length }}</span>
        </header>
        <div class="source-toolbar">
          <input v-model.trim="search" type="search" placeholder="搜索标题或地址" @keyup.enter="loadDocuments" />
          <div class="source-filters"><button v-for="item in sourceFilters" :key="item.id" type="button" :class="{ active: sourceType === item.id }" @click="chooseSourceType(item.id)">{{ item.label }}</button></div>
        </div>
        <div class="source-list">
          <button v-for="item in documents" :key="item.doc_id" type="button" :class="{ active: selectedId === item.doc_id }" @click="selectDocument(item)">
            <span class="source-icon">{{ sourceIcon(item.source_type) }}</span><div><strong>{{ item.title }}</strong><p>{{ item.source_url || '本地笔记' }}</p><small>{{ item.chunk_count }} CHUNKS · {{ formatDate(item.updated_at_ms) }}</small></div><i>›</i>
          </button>
          <div v-if="!documents.length && !loading" class="empty-state"><span>◇</span><strong>{{ selectedBase ? '知识库还是空的' : '还没有知识源' }}</strong><p>{{ selectedBase ? '从全部知识源中选择资料加入此知识库。' : '让 Agent 使用知识导入工具后，来源会出现在这里。' }}</p><button v-if="selectedBase" class="secondary-button" type="button" @click="selectBase('all')">前往全部知识源</button></div>
        </div>
      </aside>

      <main class="knowledge-detail">
        <template v-if="detail">
          <header class="detail-title"><div><div class="detail-tags"><span>{{ sourceLabel(detail.source_type) }}</span><span>已索引</span><span>用户私有</span></div><h2>{{ detail.title }}</h2><a v-if="detail.source_url" :href="detail.source_url" target="_blank" rel="noopener noreferrer">{{ detail.source_url }} ↗</a><p v-else>该知识源没有外部 URL。</p></div><button class="danger-button" type="button" :disabled="deleting" @click="removeDocument">{{ deleting ? '移除中…' : '删除知识源' }}</button></header>

          <section class="membership-card">
            <header><div><span class="eyebrow">MEMBERSHIP</span><strong>所属知识库</strong></div><small>同一知识源可以被多个知识库复用</small></header>
            <div class="membership-content">
              <div class="membership-chips"><span v-for="baseId in detail.knowledge_base_ids" :key="baseId">{{ baseName(baseId) }}<button type="button" :disabled="binding" :title="`从 ${baseName(baseId)} 移出`" @click="removeFromBase(baseId)">×</button></span><em v-if="!detail.knowledge_base_ids.length">尚未加入任何知识库</em></div>
              <div v-if="availableBases.length" class="membership-add"><select v-model="targetBaseId"><option value="" disabled>选择知识库</option><option v-for="item in availableBases" :key="item.knowledge_base_id" :value="item.knowledge_base_id">{{ item.name }}</option></select><button class="secondary-button" type="button" :disabled="binding || !targetBaseId" @click="addToBase">加入</button></div>
              <button v-else class="text-button" type="button" @click="openCreate">＋ 新建知识库</button>
            </div>
          </section>

          <div class="detail-facts"><div><span>采集 Agent</span><strong>{{ agentName(detail.agent_id) }}</strong><code>{{ detail.agent_id || 'shared' }}</code></div><div><span>索引分块</span><strong>{{ detail.chunk_count }}</strong><code>{{ formatBytes(detail.size_bytes) }}</code></div><div><span>最近索引</span><strong>{{ formatDate(detail.updated_at_ms) }}</strong><code>{{ detail.doc_id }}</code></div></div>

          <section class="evidence-section"><header><div><span class="eyebrow">INDEX EVIDENCE</span><h3>索引内容与引用定位</h3></div><span>{{ detail.chunks.length }} 个分块</span></header><div class="chunk-list"><article v-for="chunk in detail.chunks" :key="chunk.chunk_index"><header><span>CHUNK {{ String(chunk.chunk_index + 1).padStart(2, '0') }}</span><span v-if="chunk.page != null">PAGE {{ chunk.page }}</span></header><p>{{ chunk.content }}</p></article><div v-if="!detail.chunks.length" class="empty-state compact"><span>◇</span><strong>此来源没有可检索分块</strong></div></div></section>
        </template>
        <div v-else class="empty-detail"><span class="empty-orbit"><i>K</i></span><strong>{{ loading ? '正在读取知识索引' : '选择一个知识源' }}</strong><p>这里会展示知识库归属、真实索引分块、来源地址和采集 Agent。</p><router-link v-if="!documents.length && !loading && !selectedBase" class="primary-button" to="/chat">开始导入知识</router-link></div>
      </main>
    </section>

    <div v-if="formOpen" class="modal-backdrop" @click.self="closeForm">
      <form class="base-modal panel" @submit.prevent="saveBase">
        <header><div><span class="eyebrow">{{ editingBase ? 'EDIT LIBRARY' : 'NEW LIBRARY' }}</span><h2>{{ editingBase ? '编辑知识库' : '新建知识库' }}</h2></div><button type="button" @click="closeForm">×</button></header>
        <label><span>名称</span><input v-model.trim="baseForm.name" maxlength="120" required placeholder="例如：JoyhouseBot 架构" /></label>
        <label><span>说明</span><textarea v-model.trim="baseForm.description" maxlength="1000" rows="4" placeholder="这个知识库解决什么问题，包含哪些可信资料？" /></label>
        <label v-if="editingBase"><span>状态</span><select v-model="baseForm.status"><option value="active">使用中</option><option value="archived">已归档</option></select></label>
        <div class="modal-note"><strong>资产边界</strong><p>知识库负责归类，知识源仍是独立资产。删除知识库不会删除其中的知识源。</p></div>
        <footer><button class="secondary-button" type="button" @click="closeForm">取消</button><button class="primary-button" type="submit" :disabled="savingBase || !baseForm.name">{{ savingBase ? '保存中…' : (editingBase ? '保存修改' : '创建知识库') }}</button></footer>
      </form>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { useMessage } from 'naive-ui'
import { getAdminAgents, type AdminAgent } from '../api/admin'
import {
  addKnowledgeDocumentToBase, createKnowledgeBase, deleteKnowledgeBase, deleteKnowledgeDocument,
  getKnowledgeBases, getKnowledgeDocument, getKnowledgeDocuments, removeKnowledgeDocumentFromBase,
  updateKnowledgeBase, type KnowledgeBase, type KnowledgeDocument, type KnowledgeDocumentListItem,
  type KnowledgeSourceType, type KnowledgeSummary,
} from '../api/knowledge'

const message = useMessage()
const agents = ref<AdminAgent[]>([])
const bases = ref<KnowledgeBase[]>([])
const documents = ref<KnowledgeDocumentListItem[]>([])
const detail = ref<KnowledgeDocument | null>(null)
const selectedBaseId = ref('all')
const selectedId = ref('')
const sourceType = ref<KnowledgeSourceType | 'all'>('all')
const search = ref('')
const targetBaseId = ref('')
const loading = ref(false)
const deleting = ref(false)
const binding = ref(false)
const error = ref('')
const formOpen = ref(false)
const editingBase = ref<KnowledgeBase | null>(null)
const savingBase = ref(false)
const baseForm = reactive<{ name: string; description: string; status: KnowledgeBase['status'] }>({ name: '', description: '', status: 'active' })
const summary = ref<KnowledgeSummary>({ bases: 0, total: 0, chunks: 0, size_bytes: 0, by_source: {} })
const sourceFilters: Array<{ id: KnowledgeSourceType | 'all'; label: string }> = [{ id: 'all', label: '全部' }, { id: 'url', label: '网页' }, { id: 'note', label: '笔记' }]
const selectedBase = computed(() => bases.value.find((item) => item.knowledge_base_id === selectedBaseId.value) || null)
const activeBaseCount = computed(() => bases.value.filter((item) => item.status === 'active').length)
const availableBases = computed(() => bases.value.filter((item) => item.status === 'active' && !detail.value?.knowledge_base_ids.includes(item.knowledge_base_id)))

async function initialize() { const [agentResult] = await Promise.allSettled([getAdminAgents()]); if (agentResult.status === 'fulfilled') agents.value = agentResult.value; await loadAll() }
async function loadBases() { bases.value = await getKnowledgeBases('all'); if (selectedBaseId.value !== 'all' && !bases.value.some((item) => item.knowledge_base_id === selectedBaseId.value)) selectedBaseId.value = 'all' }
async function loadDocuments() { loading.value = true; error.value = ''; try { const result = await getKnowledgeDocuments({ knowledgeBaseId: selectedBaseId.value === 'all' ? undefined : selectedBaseId.value, sourceType: sourceType.value, search: search.value }); documents.value = result.items; summary.value = result.summary; const next = result.items.find((item) => item.doc_id === selectedId.value) || result.items[0]; if (next) await selectDocument(next); else { selectedId.value = ''; detail.value = null } } catch (value) { error.value = errorText(value); documents.value = []; detail.value = null } finally { loading.value = false } }
async function loadAll() { loading.value = true; try { await loadBases(); await loadDocuments() } catch (value) { error.value = errorText(value); loading.value = false } }
async function selectBase(baseId: string) { selectedBaseId.value = baseId; selectedId.value = ''; detail.value = null; await loadDocuments() }
async function selectDocument(item: KnowledgeDocumentListItem) { selectedId.value = item.doc_id; error.value = ''; try { detail.value = await getKnowledgeDocument(item.doc_id); targetBaseId.value = availableBases.value[0]?.knowledge_base_id || '' } catch (value) { detail.value = null; error.value = errorText(value) } }
function chooseSourceType(value: KnowledgeSourceType | 'all') { sourceType.value = value; void loadDocuments() }

function openCreate() { editingBase.value = null; Object.assign(baseForm, { name: '', description: '', status: 'active' }); formOpen.value = true }
function openEdit(item: KnowledgeBase) { editingBase.value = item; Object.assign(baseForm, { name: item.name, description: item.description, status: item.status }); formOpen.value = true }
function closeForm() { if (!savingBase.value) formOpen.value = false }
async function saveBase() { savingBase.value = true; try { const item = editingBase.value ? await updateKnowledgeBase(editingBase.value.knowledge_base_id, { name: baseForm.name, description: baseForm.description, status: baseForm.status }) : await createKnowledgeBase({ name: baseForm.name, description: baseForm.description }); message.success(editingBase.value ? '知识库已更新' : '知识库已创建'); formOpen.value = false; selectedBaseId.value = item.knowledge_base_id; await loadAll() } catch (value) { message.error(errorText(value)) } finally { savingBase.value = false } }
async function toggleArchive(item: KnowledgeBase) { try { await updateKnowledgeBase(item.knowledge_base_id, { status: item.status === 'active' ? 'archived' : 'active' }); message.success(item.status === 'active' ? '知识库已归档' : '知识库已恢复'); await loadBases() } catch (value) { message.error(errorText(value)) } }
async function removeBase(item: KnowledgeBase) { if (!window.confirm(`删除知识库“${item.name}”？其中 ${item.document_count} 个知识源会保留在全部知识源中。`)) return; try { await deleteKnowledgeBase(item.knowledge_base_id); message.success('知识库已删除，原始知识源已保留'); selectedBaseId.value = 'all'; await loadAll() } catch (value) { message.error(errorText(value)) } }
async function addToBase() { if (!detail.value || !targetBaseId.value) return; binding.value = true; try { await addKnowledgeDocumentToBase(targetBaseId.value, detail.value.doc_id); message.success(`已加入 ${baseName(targetBaseId.value)}`); await loadBases(); detail.value = await getKnowledgeDocument(detail.value.doc_id); targetBaseId.value = availableBases.value[0]?.knowledge_base_id || '' } catch (value) { message.error(errorText(value)) } finally { binding.value = false } }
async function removeFromBase(baseId: string) { if (!detail.value) return; binding.value = true; try { await removeKnowledgeDocumentFromBase(baseId, detail.value.doc_id); message.success(`已从 ${baseName(baseId)} 移出`); await loadBases(); if (selectedBaseId.value === baseId) await loadDocuments(); else detail.value = await getKnowledgeDocument(detail.value.doc_id) } catch (value) { message.error(errorText(value)) } finally { binding.value = false } }
async function removeDocument() { if (!detail.value || !window.confirm(`删除知识源“${detail.value.title}”？索引分块和所有知识库绑定会一并删除。`)) return; deleting.value = true; try { await deleteKnowledgeDocument(detail.value.doc_id); message.success('知识源已删除'); selectedId.value = ''; detail.value = null; await loadAll() } catch (value) { message.error(errorText(value)) } finally { deleting.value = false } }

function baseName(id: string) { return bases.value.find((item) => item.knowledge_base_id === id)?.name || '未知知识库' }
function sourceIcon(value: string) { return value === 'url' ? '↗' : 'N' }
function sourceLabel(value: string) { return value === 'url' ? '网页来源' : value === 'note' ? '笔记' : value }
function agentName(agentId?: string | null) { return agents.value.find((item) => item.agent_id === agentId)?.name || (agentId ? '未知 Agent' : '用户共享') }
function errorText(value: unknown) { return value instanceof Error ? value.message : '知识资产操作失败' }
function formatDate(value: number) { return new Date(value).toLocaleString('zh-CN') }
function formatBytes(value: number) { return value < 1024 ? `${value} B` : value < 1048576 ? `${(value / 1024).toFixed(1)} KB` : `${(value / 1048576).toFixed(1)} MB` }
onMounted(initialize)
</script>

<style scoped>
.knowledge-page{display:grid;gap:18px}.knowledge-heading{display:flex;align-items:flex-end;justify-content:space-between;gap:28px}.knowledge-heading h1{margin:7px 0;color:var(--text-strong);font-size:clamp(29px,3vw,42px);line-height:1;letter-spacing:-.05em}.knowledge-heading p{max-width:760px;margin:0;color:var(--text-muted);font-size:13px}.asset-back{display:inline-flex;margin-bottom:18px;color:var(--text-muted);font:10px var(--font-mono)}.asset-back:hover{color:var(--accent)}.import-button{color:var(--accent);background:var(--accent-subtle);border-color:var(--accent-border);box-shadow:none}.knowledge-boundary{display:grid;grid-template-columns:auto minmax(0,1fr) auto;gap:14px;align-items:center;padding:15px 17px;background:linear-gradient(100deg,rgba(50,182,122,.08),transparent 60%);border:1px solid rgba(50,182,122,.24);border-radius:13px}.boundary-icon{display:grid;width:38px;height:38px;place-items:center;color:var(--success);background:rgba(50,182,122,.1);border:1px solid rgba(50,182,122,.22);border-radius:10px;font:600 12px var(--font-mono)}.knowledge-boundary>div:nth-child(2){display:grid;grid-template-columns:auto auto 1fr;gap:5px 9px;align-items:baseline}.knowledge-boundary span{color:var(--text-muted);font:8px var(--font-mono);letter-spacing:.08em}.knowledge-boundary strong{color:var(--text-strong);font-size:11px}.knowledge-boundary p{grid-column:1/-1;margin:0;color:var(--text-muted);font-size:9px}.boundary-state{display:flex;align-items:center;gap:6px!important;color:var(--success)!important}.boundary-state i{width:6px;height:6px;background:var(--success);border-radius:50%}.knowledge-metrics{display:grid;grid-template-columns:repeat(4,1fr);gap:11px}.knowledge-metrics article{position:relative;min-height:108px;overflow:hidden;padding:17px;background:var(--surface);border:1px solid var(--border);border-radius:13px}.knowledge-metrics article::after{content:"";position:absolute;right:-30px;top:-35px;width:88px;height:88px;background:var(--accent-subtle);border-radius:50%}.knowledge-metrics span,.knowledge-metrics strong,.knowledge-metrics small{display:block}.knowledge-metrics span{color:var(--text-muted);font-size:9px}.knowledge-metrics strong{margin:7px 0 3px;color:var(--text-strong);font:600 25px var(--font-mono);letter-spacing:-.05em}.knowledge-metrics small{color:var(--text-muted);font-size:8px}.knowledge-workspace{display:grid;grid-template-columns:230px 305px minmax(0,1fr);min-height:680px;overflow:hidden}.base-column,.source-column{min-width:0;border-right:1px solid var(--border)}.base-column>header,.source-column>header{display:flex;align-items:center;justify-content:space-between;gap:10px;padding:15px;border-bottom:1px solid var(--border)}.base-column>header>div,.source-column>header>div{min-width:0;display:grid;gap:4px}.base-column>header strong,.source-column>header strong{overflow:hidden;color:var(--text-strong);font-size:12px;text-overflow:ellipsis;white-space:nowrap}.base-column>header button{display:grid;width:27px;height:27px;place-items:center;color:var(--accent);background:var(--accent-subtle);border:1px solid var(--accent-border);border-radius:8px;cursor:pointer}.source-column>header>span{display:grid;min-width:27px;height:27px;place-items:center;color:var(--accent);background:var(--accent-subtle);border-radius:8px;font:600 9px var(--font-mono)}.source-column>header p{overflow:hidden;margin:0;color:var(--text-muted);font-size:8px;text-overflow:ellipsis;white-space:nowrap}.base-list{max-height:545px;overflow:auto;padding:8px}.base-item{display:grid;width:100%;grid-template-columns:32px minmax(0,1fr) auto;gap:9px;align-items:center;padding:10px;color:var(--text);background:transparent;border:1px solid transparent;border-radius:9px;text-align:left;cursor:pointer}.base-item:hover,.base-item.active{background:var(--accent-subtle);border-color:var(--accent-border)}.base-item.archived{opacity:.62}.base-icon{display:grid;width:32px;height:32px;place-items:center;color:var(--accent);background:var(--surface-raised);border:1px solid var(--border);border-radius:8px;font:600 9px var(--font-mono)}.base-item>div{min-width:0;display:grid}.base-item strong,.base-item small{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.base-item strong{color:var(--text-strong);font-size:10px}.base-item small{color:var(--text-muted);font-size:8px}.base-item b{color:var(--text-muted);font:8px var(--font-mono)}.all-base{margin-bottom:7px;border-bottom-color:var(--border)}.base-empty{display:flex;min-height:220px;align-items:center;justify-content:center;flex-direction:column;text-align:center}.base-empty>span{color:var(--accent);font-size:22px}.base-empty strong{margin-top:7px;color:var(--text-strong);font-size:10px}.base-empty p{margin:4px 12px;color:var(--text-muted);font-size:8px}.base-empty button{margin-top:8px;padding:6px 9px;color:var(--accent);background:var(--accent-subtle);border:1px solid var(--accent-border);border-radius:7px;font-size:8px;cursor:pointer}.base-column>footer{padding:12px;background:var(--surface-raised);border-top:1px solid var(--border)}.base-column>footer>div{display:flex;align-items:center;justify-content:space-between;gap:5px}.base-column>footer span{padding:3px 6px;color:var(--success);background:rgba(50,182,122,.09);border-radius:99px;font:7px var(--font-mono)}.base-column>footer span.archived{color:var(--warning);background:rgba(233,162,59,.09)}.base-column>footer small{color:var(--text-muted);font:6px var(--font-mono)}.base-column>footer button{margin-top:9px;padding:0;color:var(--text-muted);background:transparent;border:0;font-size:8px;cursor:pointer}.base-column>footer button:hover{color:var(--accent)}.base-column>footer .delete-link:hover{color:var(--danger)}.source-toolbar{display:grid;gap:9px;padding:11px;background:var(--surface-raised);border-bottom:1px solid var(--border)}.source-toolbar input{width:100%;height:34px;padding:0 9px;color:var(--text);background:var(--input);border:1px solid var(--border);border-radius:8px;outline:none}.source-filters{display:flex;gap:5px}.source-filters button{padding:4px 7px;color:var(--text-muted);background:transparent;border:1px solid transparent;border-radius:6px;font-size:8px;cursor:pointer}.source-filters button.active{color:var(--text-strong);background:var(--surface);border-color:var(--border)}.source-list{max-height:610px;overflow:auto}.source-list>button{display:grid;width:100%;grid-template-columns:32px minmax(0,1fr) auto;gap:9px;align-items:start;padding:13px 12px;color:var(--text);background:transparent;border:0;border-bottom:1px solid var(--border);text-align:left;cursor:pointer}.source-list>button:hover,.source-list>button.active{background:var(--surface-hover);box-shadow:inset 2px 0 var(--accent)}.source-icon{display:grid;width:32px;height:32px;place-items:center;color:var(--accent);background:var(--accent-subtle);border:1px solid var(--accent-border);border-radius:8px;font:600 10px var(--font-mono)}.source-list button>div{min-width:0;display:grid;gap:3px}.source-list strong,.source-list p{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.source-list strong{color:var(--text-strong);font-size:10px}.source-list p{margin:0;color:var(--text-muted);font-size:8px}.source-list small{color:var(--text-muted);font:7px var(--font-mono)}.source-list i{color:var(--text-muted);font-style:normal}.knowledge-detail{min-width:0;background:var(--surface)}.detail-title{display:flex;align-items:flex-start;justify-content:space-between;gap:16px;padding:20px;border-bottom:1px solid var(--border)}.detail-title>div{min-width:0}.detail-tags{display:flex;gap:5px}.detail-tags span{padding:3px 6px;color:var(--success);background:rgba(50,182,122,.09);border-radius:99px;font:7px var(--font-mono)}.detail-tags span:first-child{color:var(--accent);background:var(--accent-subtle)}.detail-title h2{margin:9px 0 4px;color:var(--text-strong);font-size:18px;letter-spacing:-.03em}.detail-title a,.detail-title p{display:block;max-width:510px;overflow:hidden;margin:0;color:var(--text-muted);font:8px var(--font-mono);text-overflow:ellipsis;white-space:nowrap}.detail-title .danger-button{min-height:31px;padding:0 10px;font-size:8px}.membership-card{border-bottom:1px solid var(--border);background:var(--surface-raised)}.membership-card>header{display:flex;align-items:flex-end;justify-content:space-between;padding:12px 16px 8px}.membership-card>header>div{display:grid;gap:3px}.membership-card strong{color:var(--text-strong);font-size:10px}.membership-card>header small{color:var(--text-muted);font-size:7px}.membership-content{display:flex;align-items:center;justify-content:space-between;gap:10px;padding:0 16px 13px}.membership-chips{display:flex;min-width:0;flex-wrap:wrap;gap:5px}.membership-chips>span{display:flex;align-items:center;gap:5px;padding:4px 7px;color:var(--accent);background:var(--accent-subtle);border:1px solid var(--accent-border);border-radius:7px;font-size:8px}.membership-chips button{padding:0;color:var(--text-muted);background:transparent;border:0;cursor:pointer}.membership-chips em{color:var(--text-muted);font-size:8px;font-style:normal}.membership-add{display:flex;flex:none;gap:5px}.membership-add select{max-width:145px;height:30px;padding:0 7px;color:var(--text);background:var(--input);border:1px solid var(--border);border-radius:7px;font-size:8px}.membership-add .secondary-button{min-height:30px;padding:0 9px;font-size:8px}.text-button{padding:4px;color:var(--accent);background:transparent;border:0;font-size:8px;cursor:pointer}.detail-facts{display:grid;grid-template-columns:repeat(3,1fr);background:var(--surface-raised);border-bottom:1px solid var(--border)}.detail-facts>div{min-width:0;display:grid;gap:3px;padding:12px 15px;border-right:1px solid var(--border)}.detail-facts>div:last-child{border:0}.detail-facts span{color:var(--text-muted);font:7px var(--font-mono)}.detail-facts strong{overflow:hidden;color:var(--text-strong);font-size:9px;text-overflow:ellipsis;white-space:nowrap}.detail-facts code{overflow:hidden;color:var(--text-muted);font-size:7px;text-overflow:ellipsis;white-space:nowrap}.evidence-section{padding:17px 18px}.evidence-section>header{display:flex;align-items:flex-end;justify-content:space-between;margin-bottom:10px}.evidence-section h3{margin:4px 0 0;color:var(--text-strong);font-size:12px}.evidence-section>header>span{color:var(--text-muted);font:7px var(--font-mono)}.chunk-list{display:grid;gap:7px;max-height:330px;overflow:auto}.chunk-list article{padding:11px 12px;background:var(--input);border:1px solid var(--border);border-radius:8px}.chunk-list article>header{display:flex;gap:6px}.chunk-list header span{color:var(--accent);font:7px var(--font-mono)}.chunk-list header span+span{color:var(--text-muted)}.chunk-list p{margin:7px 0 0;white-space:pre-wrap;color:var(--text);font-size:9px;line-height:1.6}.empty-detail{min-height:680px;display:flex;align-items:center;justify-content:center;flex-direction:column;text-align:center}.empty-orbit{display:grid;width:82px;height:82px;place-items:center;border:1px solid var(--accent-border);border-radius:50%;box-shadow:0 0 0 18px var(--accent-subtle)}.empty-orbit i{display:grid;width:44px;height:44px;place-items:center;color:var(--accent);background:var(--surface-raised);border-radius:13px;font:600 15px var(--font-mono);font-style:normal}.empty-detail>strong{margin-top:34px;color:var(--text-strong);font-size:13px}.empty-detail>p{max-width:360px;margin:5px 0 16px;color:var(--text-muted);font-size:9px}.modal-backdrop{position:fixed;inset:0;z-index:90;display:grid;place-items:center;padding:20px;background:rgba(0,0,0,.5);backdrop-filter:blur(5px)}.base-modal{width:min(470px,100%);overflow:hidden;box-shadow:var(--shadow)}.base-modal>header{display:flex;align-items:flex-start;justify-content:space-between;padding:18px 20px;border-bottom:1px solid var(--border)}.base-modal h2{margin:5px 0 0;color:var(--text-strong);font-size:17px}.base-modal>header button{padding:0;color:var(--text-muted);background:transparent;border:0;font-size:20px;cursor:pointer}.base-modal>label{display:grid;gap:6px;padding:14px 20px 0;color:var(--text-muted);font-size:9px}.base-modal input,.base-modal textarea,.base-modal select{width:100%;padding:9px 10px;color:var(--text);background:var(--input);border:1px solid var(--border);border-radius:8px;outline:none}.base-modal textarea{resize:vertical}.modal-note{margin:15px 20px 0;padding:11px;background:var(--accent-subtle);border:1px solid var(--accent-border);border-radius:9px}.modal-note strong{color:var(--text-strong);font-size:9px}.modal-note p{margin:3px 0 0;color:var(--text-muted);font-size:8px}.base-modal>footer{display:flex;justify-content:flex-end;gap:8px;padding:16px 20px}.base-modal>footer button{min-height:34px;font-size:9px}
@media(max-width:1180px){.knowledge-workspace{grid-template-columns:200px 280px minmax(0,1fr)}.membership-content{align-items:flex-start;flex-direction:column}.membership-add{width:100%}.membership-add select{max-width:none;flex:1}}
@media(max-width:900px){.knowledge-workspace{grid-template-columns:220px minmax(0,1fr)}.knowledge-detail{grid-column:1/-1;border-top:1px solid var(--border)}.empty-detail{min-height:430px}.base-list,.source-list{max-height:380px}}
@media(max-width:760px){.knowledge-heading{align-items:flex-start;flex-direction:column}.heading-actions{display:grid;grid-template-columns:1fr 1fr}.heading-actions>*{width:100%}.knowledge-boundary{grid-template-columns:auto 1fr}.boundary-state{display:none}.knowledge-boundary>div:nth-child(2){grid-template-columns:1fr}.knowledge-boundary p{grid-column:auto}.knowledge-metrics{grid-template-columns:repeat(2,1fr)}.knowledge-workspace{grid-template-columns:1fr}.base-column,.source-column{border-right:0;border-bottom:1px solid var(--border)}.knowledge-detail{grid-column:auto}.base-list,.source-list{max-height:300px}.detail-title{flex-direction:column}.membership-card>header{align-items:flex-start;flex-direction:column;gap:4px}.detail-facts{grid-template-columns:1fr}.detail-facts>div{border-right:0;border-bottom:1px solid var(--border)}}
</style>
