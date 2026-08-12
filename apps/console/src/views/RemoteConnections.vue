<template>
  <div class="page connections-page">
    <header class="page-heading">
      <div><span class="eyebrow">REMOTE CAPABILITY CONTROL PLANE</span><h1>远程能力</h1><p>把独立业务系统登记为版本化 Capability 服务；连接发布、Worker 预热、能力准入和 Agent 授权保持彼此独立。</p></div>
      <div class="heading-actions"><button class="primary-button" type="button" @click="newConnection">新建连接</button><button class="secondary-button" type="button" :disabled="loading" @click="load">{{ loading ? '刷新中…' : '刷新状态' }}</button></div>
    </header>

    <div v-if="error" class="notice error-notice">{{ error }}</div>
    <section class="readiness-strip panel">
      <div><span>连接</span><strong>{{ connections.length }}</strong><small>PostgreSQL 版本化事实源</small></div>
      <div><span>已生效</span><strong>{{ activeCount }}</strong><small>完成逐 Worker 预热</small></div>
      <div><span>可执行</span><strong>{{ readyCount }}</strong><small>连接与 Capability 均就绪</small></div>
      <div><span>待发布能力</span><strong>{{ pendingCapabilityCount }}</strong><small>仍不可授权给 Agent</small></div>
    </section>

    <div class="connections-layout">
      <aside class="panel connection-sidebar">
        <div class="panel-heading"><div><span class="eyebrow">SERVICES</span><h2>服务目录</h2></div><span>{{ connections.length }}</span></div>
        <button v-for="item in connections" :key="item.connection_id" type="button" class="connection-row" :class="{ active: selectedId === item.connection_id }" @click="selectConnection(item.connection_id)">
          <i :class="{ ready: item.execution_ready, configured: item.current_revision_id && !item.execution_ready }" />
          <span><strong>{{ item.name }}</strong><code>{{ item.connection_id }}</code><small>{{ connectionState(item) }}</small></span>
        </button>
        <div v-if="!connections.length && !loading" class="empty-state compact"><strong>尚无远程服务</strong><p>先安装并发布 HTTP Capability Connector，再登记业务服务。</p></div>
      </aside>

      <main class="connection-workspace">
        <section v-if="editing" class="panel editor-panel">
          <div class="panel-heading"><div><span class="eyebrow">{{ selected ? 'NEW REVISION' : 'NEW CONNECTION' }}</span><h2>{{ selected ? `更新 ${selected.name}` : '登记远程服务' }}</h2></div><button class="secondary-button" type="button" @click="cancelEdit">取消</button></div>
          <form class="connection-form" @submit.prevent="saveRevision">
            <label><span>Connection ID</span><input v-model.trim="draft.connection_id" required :disabled="!!selected" placeholder="crm" /><small>稳定标识，不随版本变化</small></label>
            <label><span>显示名称</span><input v-model.trim="draft.name" required placeholder="客户线索服务" /></label>
            <label class="wide"><span>说明</span><input v-model.trim="draft.description" placeholder="业务系统边界与负责人" /></label>
            <label class="wide"><span>Base URL</span><input v-model.trim="draft.base_url" required placeholder="https://crm.internal.example/joyhousebot/v1" /></label>
            <label><span>Key ID</span><input v-model.trim="draft.key_id" required placeholder="joyhousebot-prod-2026-01" /></label>
            <label><span>签名密钥引用</span><input v-model.trim="draft.signing_secret_ref" required placeholder="env://CRM_JOYHOUSEBOT_SIGNING_SECRET" /><small>只保存环境变量名，不保存密钥</small></label>
            <label><span>请求超时（秒）</span><input v-model.number="draft.timeout_seconds" type="number" min="1" max="3600" /></label>
            <label><span>最大响应（Bytes）</span><input v-model.number="draft.max_response_bytes" type="number" min="1024" max="52428800" /></label>
            <label class="switch-field"><input v-model="draft.enabled" type="checkbox" /><span><strong>启用连接</strong><small>停用也必须通过新 Revision 发布</small></span></label>
            <label class="switch-field"><input v-model="draft.require_response_signature" type="checkbox" /><span><strong>校验响应签名</strong><small>生产环境必须开启</small></span></label>
            <label class="switch-field wide"><input v-model="draft.allow_insecure_http" type="checkbox" /><span><strong>允许本机 HTTP</strong><small>仅 localhost、127.0.0.1 和 ::1；远程地址始终要求 HTTPS</small></span></label>
            <label class="wide catalog-editor"><span>Capability 目录 JSON</span><textarea v-model="capabilitiesText" rows="20" spellcheck="false" /><small>每项必须固定 capabilityId、version、implementationDigest、输入输出 Schema、权限和副作用。修改定义必须提升 Capability 版本。</small></label>
            <div class="form-actions wide"><button class="secondary-button" type="button" @click="formatCatalog">格式化 JSON</button><button class="primary-button" type="submit" :disabled="saving">{{ saving ? '保存中…' : selected ? '保存为新 Draft' : '创建 Draft' }}</button></div>
          </form>
        </section>

        <template v-else-if="selected">
          <section class="panel connection-hero">
            <div><span class="eyebrow">{{ selected.execution_ready ? 'EXECUTION READY' : 'CONFIGURATION PIPELINE' }}</span><h2>{{ selected.name }}</h2><p>{{ selected.description || '未填写服务说明' }}</p><code>{{ selected.connection_id }} · {{ selected.current_revision_id || '尚未生效' }}</code></div>
            <div class="hero-actions"><button class="secondary-button" type="button" @click="editSelected">创建新 Revision</button><button v-if="publishableRevision" class="primary-button" type="button" :disabled="publishing" @click="publishConnection">{{ publishing ? '发布中…' : '发布连接' }}</button></div>
          </section>

          <section class="pipeline-grid">
            <article v-for="step in pipeline" :key="step.label" class="panel pipeline-step" :class="step.state"><span>{{ step.index }}</span><div><strong>{{ step.label }}</strong><small>{{ step.summary }}</small></div></article>
          </section>

          <div v-if="selected.execution_blockers.length" class="notice blocker-notice"><strong>尚不可执行</strong><span v-for="item in selected.execution_blockers" :key="item">{{ item }}</span></div>

          <section class="detail-grid">
            <article class="panel connection-config">
              <div class="panel-heading"><div><span class="eyebrow">ACTIVE CONFIG</span><h2>连接参数</h2></div><span>{{ activeConfig ? `v${selected.current_revision?.version}` : '未生效' }}</span></div>
              <dl v-if="displayConfig"><dt>Endpoint</dt><dd><code>{{ displayConfig.base_url }}</code></dd><dt>Key ID</dt><dd>{{ displayConfig.key_id }}</dd><dt>Secret</dt><dd><code>{{ displayConfig.signing_secret_ref }}</code></dd><dt>响应签名</dt><dd>{{ displayConfig.require_response_signature ? '必须' : '未要求' }}</dd><dt>Timeout</dt><dd>{{ displayConfig.timeout_seconds }} 秒</dd><dt>响应上限</dt><dd>{{ formatBytes(displayConfig.max_response_bytes) }}</dd></dl>
              <p v-else class="muted">发布 Draft 并等待 Worker 预热后成为 Active 配置。</p>
            </article>
            <article class="panel rollout-panel">
              <div class="panel-heading"><div><span class="eyebrow">WORKER ACK</span><h2>最近发布</h2></div><span>{{ selected.latest_rollout?.status || '无' }}</span></div>
              <template v-if="selected.latest_rollout"><div class="progress-track"><i :style="{ width: rolloutProgress + '%' }" /></div><p>{{ selected.latest_rollout.acknowledged_worker_count }}/{{ selected.latest_rollout.target_worker_count }} Worker 已确认 · {{ selected.latest_rollout.failed_worker_count }} 失败</p><div class="target-list"><code v-for="target in selected.latest_rollout.targets" :key="target.worker_id">{{ target.worker_id }} · {{ target.status }} · attempt {{ target.attempt_count }}</code></div><button v-if="selected.latest_rollout.status === 'completed' && selected.latest_rollout.previous_revision_id" class="secondary-button danger-text" type="button" @click="rollbackConnection">回滚到 {{ selected.latest_rollout.previous_revision_id }}</button></template>
              <p v-else class="muted">尚未提交连接发布。</p>
            </article>
          </section>

          <section class="panel capabilities-panel">
            <div class="panel-heading"><div><span class="eyebrow">CAPABILITY CATALOG</span><h2>远程能力目录</h2></div><router-link class="secondary-button" to="/agents">配置 Agent 授权 →</router-link></div>
            <div class="capability-list">
              <article v-for="capability in selected.capabilities" :key="`${capability.capability_id}:${capability.version}`">
                <span class="capability-mark">C</span><div><strong>{{ capability.name }}</strong><code>{{ capability.capability_id }} · v{{ capability.version }}</code><small>{{ capability.permissions?.join(' · ') || '无权限声明' }} · {{ capability.side_effect }}</small></div><span class="status-badge" :class="capabilityStatusClass(capability.release_status)">{{ capabilityStatusLabel(capability.release_status) }}</span><button v-if="['discovered', 'not_loaded'].includes(capability.release_status || '')" class="primary-button" type="button" :disabled="capability.release_status === 'not_loaded' || capabilityPublishing === capability.capability_id" @click="publishCapability(capability)">{{ capability.release_status === 'not_loaded' ? '等待 Worker' : capabilityPublishing === capability.capability_id ? '发布中…' : '发布能力' }}</button>
              </article>
              <div v-if="!selected.capabilities.length" class="empty-state compact"><strong>能力尚未由 Worker 加载</strong><p>连接发布预热会校验并发现精确 Capability 定义。</p></div>
            </div>
          </section>

          <section class="panel revisions-panel">
            <div class="panel-heading"><div><span class="eyebrow">IMMUTABLE HISTORY</span><h2>Revision 历史</h2></div><span>{{ selected.revisions?.length || 0 }}</span></div>
            <div class="revision-list"><article v-for="revision in selected.revisions" :key="revision.revision_id"><div><strong>v{{ revision.version }}</strong><code>{{ revision.revision_id }}</code><small>{{ shortDigest(revision.fingerprint) }} · {{ formatDate(revision.created_at) }}</small></div><span class="status-badge" :class="revision.status">{{ revision.status }}</span></article></div>
          </section>
        </template>

        <section v-else class="panel empty-workspace"><span>⌘</span><strong>选择或创建一个远程服务</strong><p>配置不会直接生效。保存产生不可变 Draft，发布后由所有健康 Agent Worker 预热确认。</p></section>
      </main>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { useMessage } from 'naive-ui'
import { rollbackConfigurationRollout } from '../api/admin'
import { createRemoteConnection, createRemoteConnectionRevision, getRemoteConnection, listRemoteConnections, publishRemoteCapability, publishRemoteConnectionRevision, type RemoteCapabilityDeclaration, type RemoteConnection } from '../api/remoteConnections'

const sampleCapability = [{ capability_id: 'crm.lead.read', version: '1.0.0', implementation_digest: `sha256:${'0'.repeat(64)}`, name: '读取销售线索', description: '按授权范围读取一条线索', input_schema: { type: 'object', properties: { lead_id: { type: 'string' } }, required: ['lead_id'], additionalProperties: false }, output_schema: { type: 'object' }, permissions: ['crm.lead.read'], side_effect: 'read', idempotent: true, retryable: true, data_classification: 'confidential' }]
const emptyDraft = () => ({ connection_id: '', name: '', description: '', enabled: true, base_url: '', key_id: '', signing_secret_ref: '', allow_insecure_http: false, require_response_signature: true, timeout_seconds: 60, max_response_bytes: 10 * 1024 * 1024 })
const message = useMessage(); const loading = ref(false); const saving = ref(false); const publishing = ref(false); const capabilityPublishing = ref(''); const error = ref(''); const connections = ref<RemoteConnection[]>([]); const selectedId = ref(''); const selected = ref<RemoteConnection | null>(null); const editing = ref(false); const draft = reactive(emptyDraft()); const capabilitiesText = ref(JSON.stringify(sampleCapability, null, 2))
const activeCount = computed(() => connections.value.filter((item) => item.current_revision_id).length); const readyCount = computed(() => connections.value.filter((item) => item.execution_ready).length); const pendingCapabilityCount = computed(() => connections.value.reduce((total, item) => total + item.capabilities.filter((capability) => capability.release_status !== 'published').length, 0))
const publishableRevision = computed(() => selected.value?.latest_revision?.status === 'draft' ? selected.value.latest_revision : null)
const activeConfig = computed(() => selected.value?.current_revision?.configuration || null); const displayConfig = computed(() => activeConfig.value || selected.value?.latest_revision?.configuration || null)
const rolloutProgress = computed(() => { const rollout = selected.value?.latest_rollout; return rollout?.target_worker_count ? Math.round(rollout.acknowledged_worker_count / rollout.target_worker_count * 100) : 100 })
const pipeline = computed(() => { const item = selected.value; const capabilitiesReady = !!item?.capabilities.length && item.capabilities.every((value) => value.release_status === 'published'); return [{ index: '01', label: 'Connector', summary: item?.connector_release ? `v${item.connector_release.version} 已生效` : '扩展未生效', state: item?.connector_release ? 'done' : 'blocked' }, { index: '02', label: '连接 Revision', summary: item?.current_revision_id || '等待发布', state: item?.current_revision_id ? 'done' : 'pending' }, { index: '03', label: 'Worker 加载', summary: `${item?.worker_summary.loaded || 0}/${item?.worker_summary.total || 0} 就绪`, state: item?.worker_summary.loaded ? 'done' : 'pending' }, { index: '04', label: '能力发布', summary: capabilitiesReady ? '全部通过门禁' : '仍有能力待发布', state: capabilitiesReady ? 'done' : 'pending' }, { index: '05', label: 'Agent 授权', summary: '在构建中心显式选择', state: capabilitiesReady ? 'action' : 'pending' }] })

async function load() { loading.value = true; error.value = ''; try { connections.value = await listRemoteConnections(); if (selectedId.value) await selectConnection(selectedId.value); else if (connections.value.length) await selectConnection(connections.value[0].connection_id) } catch (cause) { error.value = errorText(cause) } finally { loading.value = false } }
async function selectConnection(id: string) { selectedId.value = id; editing.value = false; selected.value = await getRemoteConnection(id) }
function newConnection() { selected.value = null; selectedId.value = ''; Object.assign(draft, emptyDraft()); capabilitiesText.value = JSON.stringify(sampleCapability, null, 2); editing.value = true }
function editSelected() { const item = selected.value; if (!item) return; const configuration = item.latest_revision?.configuration || item.current_revision?.configuration; Object.assign(draft, { ...emptyDraft(), connection_id: item.connection_id, name: item.name, description: item.description, ...(configuration || {}) }); capabilitiesText.value = JSON.stringify(configuration?.capabilities || sampleCapability, null, 2); editing.value = true }
function cancelEdit() { editing.value = false; if (!selected.value && connections.value.length) void selectConnection(connections.value[0].connection_id) }
function parsedCapabilities(): RemoteCapabilityDeclaration[] { const value = JSON.parse(capabilitiesText.value); if (!Array.isArray(value) || !value.length) throw new Error('Capability 目录必须是非空 JSON 数组'); return value as RemoteCapabilityDeclaration[] }
function payload() { return { ...draft, capabilities: parsedCapabilities() } }
async function saveRevision() { saving.value = true; error.value = ''; try { const value = payload(); const id = draft.connection_id; if (selected.value) await createRemoteConnectionRevision(id, value); else await createRemoteConnection(value); message.success('不可变 Draft 已保存'); selectedId.value = id; editing.value = false; await load() } catch (cause) { error.value = errorText(cause) } finally { saving.value = false } }
async function publishConnection() { if (!selected.value || !publishableRevision.value) return; publishing.value = true; error.value = ''; try { await publishRemoteConnectionRevision(selected.value.connection_id, publishableRevision.value.revision_id); message.success('连接发布已进入 Worker 预热'); await load() } catch (cause) { error.value = errorText(cause) } finally { publishing.value = false } }
async function publishCapability(capability: RemoteCapabilityDeclaration) { if (!selected.value) return; capabilityPublishing.value = capability.capability_id; error.value = ''; try { await publishRemoteCapability(selected.value.connection_id, capability.capability_id, capability.version); message.success(`${capability.name} 已进入能力发布`); await load() } catch (cause) { error.value = errorText(cause) } finally { capabilityPublishing.value = '' } }
async function rollbackConnection() { const rollout = selected.value?.latest_rollout; if (!rollout || !window.confirm(`回滚连接到 ${rollout.previous_revision_id}？`)) return; try { await rollbackConfigurationRollout(rollout.rollout_id); message.success('连接回滚已进入 Worker 预热'); await load() } catch (cause) { error.value = errorText(cause) } }
function formatCatalog() { try { capabilitiesText.value = JSON.stringify(parsedCapabilities(), null, 2) } catch (cause) { error.value = errorText(cause) } }
function connectionState(item: RemoteConnection) { if (item.execution_ready) return '执行就绪'; if (item.latest_rollout && ['rolling_out', 'awaiting_approval'].includes(item.latest_rollout.status)) return 'Worker 预热中'; if (item.current_revision_id) return '已连接，待完成能力发布'; return item.latest_revision?.status === 'draft' ? 'Draft' : '未配置' }
function capabilityStatusLabel(value?: string) { return ({ published: '已发布', staged: '预热中', discovered: '待发布', not_loaded: '待加载' } as Record<string, string>)[value || ''] || value || '未知' }
function capabilityStatusClass(value?: string) { return value === 'published' ? 'completed' : value === 'staged' ? 'running' : 'cancelled' }
function shortDigest(value?: string) { return value ? `${value.slice(0, 18)}…` : '—' }
function formatBytes(value: number) { return value >= 1024 * 1024 ? `${(value / 1024 / 1024).toFixed(1)} MiB` : `${Math.round(value / 1024)} KiB` }
function formatDate(value?: string | null) { return value ? new Date(value).toLocaleString('zh-CN') : '—' }
function errorText(value: unknown) { return value instanceof Error ? value.message : '操作失败' }
onMounted(load)
</script>

<style scoped>
.readiness-strip{display:grid;grid-template-columns:repeat(4,1fr);margin-bottom:18px}.readiness-strip>div{display:grid;gap:5px;padding:16px 18px;border-right:1px solid var(--border)}.readiness-strip>div:last-child{border:0}.readiness-strip span,.readiness-strip small{color:var(--text-muted);font-size:10px}.readiness-strip strong{color:var(--text-strong);font:600 28px var(--font-mono)}.connections-layout{display:grid;grid-template-columns:300px minmax(0,1fr);gap:18px}.connection-sidebar{align-self:start;overflow:hidden}.connection-row{display:flex;width:100%;gap:11px;align-items:flex-start;padding:13px 16px;color:var(--text);background:transparent;border:0;border-top:1px solid var(--border);text-align:left;cursor:pointer}.connection-row.active{background:var(--accent-subtle)}.connection-row i{width:9px;height:9px;flex:none;margin-top:5px;border-radius:50%;background:var(--warning)}.connection-row i.configured{background:#69a9d2}.connection-row i.ready{background:var(--success)}.connection-row span{display:grid;min-width:0;gap:3px}.connection-row strong{color:var(--text-strong);font-size:12px}.connection-row code,.connection-row small{overflow:hidden;color:var(--text-muted);font-size:9px;text-overflow:ellipsis}.connection-workspace{display:grid;gap:16px;min-width:0}.connection-hero{display:flex;align-items:flex-start;justify-content:space-between;gap:18px;padding:24px}.connection-hero h2{margin:7px 0 5px;color:var(--text-strong);font-size:25px}.connection-hero p{margin:0 0 8px;color:var(--text-muted)}.connection-hero code{color:var(--text-muted);font-size:10px}.hero-actions,.heading-actions,.form-actions{display:flex;gap:8px;align-items:center}.pipeline-grid{display:grid;grid-template-columns:repeat(5,1fr);gap:8px}.pipeline-step{display:flex;gap:9px;min-height:78px;padding:12px}.pipeline-step>span{display:grid;width:24px;height:24px;flex:none;place-items:center;border-radius:50%;color:var(--text-muted);background:var(--surface-raised);font:9px var(--font-mono)}.pipeline-step div{display:grid;align-content:start;gap:5px}.pipeline-step strong{color:var(--text-strong);font-size:11px}.pipeline-step small{color:var(--text-muted);font-size:9px;line-height:1.45}.pipeline-step.done{border-color:color-mix(in srgb,var(--success) 42%,var(--border))}.pipeline-step.done>span{color:var(--success);background:color-mix(in srgb,var(--success) 10%,transparent)}.pipeline-step.action{border-color:var(--accent-border)}.pipeline-step.blocked{border-color:color-mix(in srgb,var(--danger) 42%,var(--border))}.blocker-notice{display:grid;gap:4px}.blocker-notice span{font-size:11px}.detail-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:16px}.connection-config dl{display:grid;grid-template-columns:110px minmax(0,1fr);margin:0;padding:0 18px 18px}.connection-config dt,.connection-config dd{margin:0;padding:9px 0;border-top:1px solid var(--border);font-size:11px}.connection-config dt{color:var(--text-muted)}.connection-config dd{overflow-wrap:anywhere;color:var(--text-strong)}.rollout-panel{padding-bottom:16px}.rollout-panel>p,.rollout-panel>button,.rollout-panel>.target-list,.rollout-panel>.progress-track{margin-inline:18px}.progress-track{height:5px;overflow:hidden;border-radius:8px;background:var(--border)}.progress-track i{display:block;height:100%;background:var(--accent)}.rollout-panel p{color:var(--text-muted);font-size:11px}.target-list{display:grid;gap:5px;margin-bottom:13px}.target-list code{color:var(--text-muted);font-size:9px}.capability-list{display:grid}.capability-list article{display:grid;grid-template-columns:34px minmax(0,1fr) auto auto;align-items:center;gap:12px;padding:13px 18px;border-top:1px solid var(--border)}.capability-mark{display:grid;width:32px;height:32px;place-items:center;color:var(--accent);background:var(--accent-subtle);border-radius:9px;font:600 11px var(--font-mono)}.capability-list article>div{display:grid;min-width:0;gap:3px}.capability-list strong{color:var(--text-strong);font-size:12px}.capability-list code,.capability-list small{overflow:hidden;color:var(--text-muted);font-size:9px;text-overflow:ellipsis;white-space:nowrap}.capability-list button{font-size:10px}.revision-list{padding:0 18px 18px}.revision-list article{display:flex;align-items:center;justify-content:space-between;gap:12px;padding:11px 0;border-top:1px solid var(--border)}.revision-list article>div{display:flex;align-items:center;gap:10px}.revision-list code,.revision-list small{color:var(--text-muted);font-size:9px}.connection-form{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:14px;padding:18px;border-top:1px solid var(--border)}.connection-form label{display:grid;gap:6px;color:var(--text-muted);font-size:11px}.connection-form input,.connection-form textarea{width:100%;box-sizing:border-box;padding:10px;color:var(--text-strong);background:var(--input);border:1px solid var(--border);border-radius:8px}.connection-form textarea{font:10px/1.55 var(--font-mono)}.connection-form small{color:var(--text-muted);font-size:9px}.connection-form .wide{grid-column:1/-1}.switch-field{display:flex!important;align-items:flex-start;gap:9px;padding:9px;border:1px solid var(--border);border-radius:8px}.switch-field input{width:auto}.switch-field span{display:grid;gap:2px}.switch-field strong{color:var(--text-strong)}.form-actions{justify-content:flex-end}.empty-workspace{display:grid;min-height:420px;place-items:center;align-content:center;gap:10px;text-align:center}.empty-workspace>span{font-size:40px;color:var(--accent)}.empty-workspace p{max-width:510px;color:var(--text-muted)}.muted{padding:0 18px 18px;color:var(--text-muted)}.danger-text{color:var(--danger)}
@media(max-width:1100px){.connections-layout{grid-template-columns:1fr}.pipeline-grid{grid-template-columns:repeat(3,1fr)}}@media(max-width:720px){.readiness-strip,.detail-grid,.connection-form,.pipeline-grid{grid-template-columns:1fr}.readiness-strip>div{border-right:0;border-bottom:1px solid var(--border)}.connection-hero{flex-direction:column}.capability-list article{grid-template-columns:34px minmax(0,1fr)}.capability-list article>.status-badge,.capability-list article>button{grid-column:2;justify-self:start}}
</style>
