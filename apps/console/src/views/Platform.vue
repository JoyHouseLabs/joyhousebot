<template>
  <div class="page platform-page">
    <header class="page-heading">
      <div><span class="eyebrow">PLATFORM CONTROL</span><h1>平台</h1><p>只管理平台级权限、Worker 集群、配置发布和审计；Agent、Skills、Tools 与 MCP 在配置子菜单中维护。</p></div>
      <button class="secondary-button" type="button" :disabled="loading" @click="refresh">{{ loading ? '刷新中…' : '刷新全部' }}</button>
    </header>
    <div v-if="error" class="notice error-notice">{{ error }}</div>

    <nav class="platform-tabs" aria-label="平台管理栏目">
      <button v-for="item in tabs" :key="item.id" type="button" :class="{ active: tab === item.id }" @click="tab = item.id">{{ item.label }}<small>{{ item.count }}</small></button>
    </nav>

    <section v-if="tab === 'access'" class="panel">
      <div class="panel-heading"><div><span class="eyebrow">RBAC</span><h2>管理员与权限</h2></div><button class="primary-button" type="button" @click="showAdminForm = !showAdminForm">{{ showAdminForm ? '收起' : '添加授权' }}</button></div>
      <form v-if="showAdminForm" class="editor-form" @submit.prevent="createAdmin">
        <label><span>User ID</span><input v-model.trim="adminDraft.user_id" required placeholder="user-id" /></label>
        <label><span>角色模板</span><select v-model="adminDraft.role" @change="applyRole"><option value="admin">Admin</option><option value="operator">Operator</option><option value="viewer">Viewer</option></select></label>
        <label class="check-field"><input v-model="adminDraft.is_test_user" type="checkbox" /><span>测试用户</span></label>
        <div class="permission-grid full-field">
          <label v-for="item in permissionCatalog.items" :key="item.permission" class="permission-option"><input v-model="adminDraft.permissions" type="checkbox" :value="item.permission" /><span><strong>{{ item.permission }}</strong><small>{{ item.description }}</small></span></label>
        </div>
        <button class="primary-button" type="submit">保存授权</button>
      </form>
      <div class="data-table-wrap"><table class="data-table"><thead><tr><th>用户</th><th>角色</th><th>权限</th><th>状态</th><th>更新时间</th><th></th></tr></thead><tbody>
        <tr v-for="item in admins" :key="item.user_id"><td><strong>{{ item.user_id }}</strong><small v-if="item.is_test_user">TEST USER</small></td><td>{{ item.role }}</td><td><code>{{ item.permissions.join(', ') }}</code></td><td><span class="status-badge" :class="item.enabled ? 'completed' : 'cancelled'">{{ item.enabled ? '启用' : '停用' }}</span></td><td>{{ formatDate(item.updated_at) }}</td><td><button class="row-action danger-text" type="button" @click="removeAdmin(item)">移除</button></td></tr>
      </tbody></table></div>
      <div class="panel-heading subsection-heading"><div><span class="eyebrow">HASHED TOKENS</span><h2>API 访问令牌</h2></div><span>数据库仅保存 SHA-256 指纹</span></div>
      <form class="editor-form" @submit.prevent="issueToken"><label><span>User ID</span><input v-model.trim="tokenDraft.user_id" required /></label><label><span>标签</span><input v-model.trim="tokenDraft.label" placeholder="production / test" /></label><button class="primary-button" type="submit">签发令牌</button></form>
      <div v-if="issuedToken" class="issued-token"><strong>仅显示一次，请立即保存</strong><code>{{ issuedToken }}</code><button type="button" @click="copyIssuedToken">复制</button></div>
      <div class="data-table-wrap"><table class="data-table"><thead><tr><th>Token ID</th><th>用户</th><th>标签</th><th>最近使用</th><th>状态</th><th></th></tr></thead><tbody><tr v-for="item in accessTokens" :key="item.token_id"><td><code>{{ item.token_id }}</code></td><td>{{ item.user_id }}</td><td>{{ item.label || '—' }}</td><td>{{ formatDate(item.last_used_at) }}</td><td><span class="status-badge" :class="item.enabled ? 'completed' : 'cancelled'">{{ item.enabled ? '有效' : '已吊销' }}</span></td><td><button v-if="item.enabled" class="row-action danger-text" type="button" @click="revokeToken(item.token_id)">吊销</button></td></tr></tbody></table></div>
    </section>

    <section v-else-if="tab === 'cluster'" class="cluster-layout">
      <div class="panel">
        <div class="panel-heading"><div><span class="eyebrow">WORKERS</span><h2>执行集群</h2></div><span>{{ healthyWorkers }}/{{ workers.length }} 健康</span></div>
        <div class="management-list"><article v-for="worker in workers" :key="worker.worker_id"><span class="state-dot" :class="{ on: worker.healthy }"/><div><strong>{{ worker.worker_id }}</strong><small>{{ worker.status }} · {{ formatDate(worker.last_heartbeat) }}</small><code>{{ compact(worker.capabilities) }}</code></div></article><div v-if="!workers.length" class="empty-state compact">暂无 Worker 心跳</div></div>
      </div>
      <div class="panel">
        <div class="panel-heading"><div><span class="eyebrow">ROLLOUTS</span><h2>配置发布</h2></div><span>{{ activeRollouts }} 进行中</span></div>
        <div class="rollout-list">
          <article v-for="item in rollouts" :key="item.rollout_id">
            <header><div><span class="status-badge" :class="rolloutClass(item.status)">{{ rolloutLabel(item.status) }}</span><strong>{{ item.aggregate_type }} · {{ item.aggregate_id }} · {{ item.revision_id }}</strong></div><small>{{ formatDate(item.created_at) }}</small></header>
            <div class="progress-track"><i :style="{ width: rolloutProgress(item) + '%' }"/></div>
            <p>{{ item.acknowledged_worker_count }}/{{ item.target_worker_count }} Worker ACK <span v-if="item.failed_worker_count">· {{ item.failed_worker_count }} 失败</span><span v-if="item.previous_revision_id"> · 前一版本 {{ item.previous_revision_id }}</span></p>
            <p v-if="item.status === 'rolling_out'">截止 {{ formatDate(item.deadline_at) }} · {{ item.activation_mode === 'manual' ? '加载后等待批准' : '加载后自动生效' }}</p>
            <p v-if="item.rollback_revision_id">已保护/回滚至 {{ item.rollback_revision_id }}</p>
            <div class="rollout-actions">
              <button v-if="item.status === 'awaiting_approval'" class="primary-button" type="button" @click="operateRollout(item, 'approve')">批准生效</button>
              <button v-if="['rolling_out', 'awaiting_approval'].includes(item.status)" class="secondary-button" type="button" @click="operateRollout(item, 'cancel')">取消</button>
              <button v-if="['failed', 'timed_out'].includes(item.status)" class="secondary-button" type="button" @click="operateRollout(item, 'retry')">重试失败节点</button>
              <button v-if="item.status === 'completed' && item.previous_revision_id" class="secondary-button danger-text" type="button" @click="operateRollout(item, 'rollback')">回滚</button>
            </div>
            <details v-if="item.targets.length"><summary>逐机状态</summary><code v-for="target in item.targets" :key="target.worker_id">{{ target.worker_id }} · {{ target.status }} · attempt {{ target.attempt_count }}</code></details>
          </article>
          <div v-if="!rollouts.length" class="empty-state compact"><strong>尚无配置发布记录</strong><p>发布 Agent、Capability 或 Scenario 后会在这里记录逐机预热、批准与回滚。</p></div>
        </div>
      </div>
    </section>

    <section v-else-if="tab === 'audit'" class="panel"><div class="panel-heading"><div><span class="eyebrow">AUDIT LOG</span><h2>配置变更事件</h2></div><span>只追加</span></div><div class="event-list"><article v-for="item in configurationEvents" :key="item.sequence"><time>#{{ item.sequence }}</time><span class="catalog-mark">{{ item.aggregate_type.slice(0, 1).toUpperCase() }}</span><div><strong>{{ item.event_type }} · {{ item.aggregate_id }}</strong><small>{{ item.revision_id }} · {{ item.actor_id }} · {{ formatDate(item.created_at) }}</small></div></article><div v-if="!configurationEvents.length" class="empty-state compact">暂无配置事件</div></div></section>

    <section v-else class="panel config-panel"><div class="panel-heading"><div><span class="eyebrow">SAFE VIEW</span><h2>运行配置摘要</h2></div><span>凭据值不会返回浏览器</span></div><pre>{{ JSON.stringify(config, null, 2) }}</pre></section>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { useMessage } from 'naive-ui'
import { approveConfigurationRollout, cancelConfigurationRollout, createAccessToken, deletePlatformAdmin, getAccessTokens, getAdminConfig, getAdminWorkers, getConfigurationEvents, getConfigurationRollouts, getPermissionCatalog, getPlatformAdmins, retryConfigurationRollout, revokeAccessToken, rollbackConfigurationRollout, savePlatformAdmin, type AccessToken, type ConfigurationRollout, type PermissionCatalog, type PlatformAdmin, type RuntimeWorker } from '../api/admin'

type TabId = 'access' | 'cluster' | 'audit' | 'settings'
const message = useMessage(); const loading = ref(false); const error = ref(''); const tab = ref<TabId>('access'); const showAdminForm = ref(false)
const admins = ref<PlatformAdmin[]>([]); const workers = ref<RuntimeWorker[]>([]); const rollouts = ref<ConfigurationRollout[]>([]); const configurationEvents = ref<any[]>([]); const config = ref<Record<string, unknown>>({}); const permissionCatalog = ref<PermissionCatalog>({ items: [], roles: { admin: [], operator: [], viewer: [] } })
const adminDraft = reactive({ user_id: '', role: 'viewer' as PlatformAdmin['role'], permissions: [] as string[], is_test_user: false })
const accessTokens = ref<AccessToken[]>([]); const tokenDraft = reactive({ user_id: '', label: '' }); const issuedToken = ref('')
const healthyWorkers = computed(() => workers.value.filter((item) => item.healthy).length); const activeRollouts = computed(() => rollouts.value.filter((item) => ['rolling_out', 'awaiting_approval'].includes(item.status)).length)
const tabs = computed(() => [{ id: 'access' as const, label: '访问控制', count: admins.value.length + accessTokens.value.length }, { id: 'cluster' as const, label: '执行集群', count: healthyWorkers.value }, { id: 'audit' as const, label: '审计', count: configurationEvents.value.length }, { id: 'settings' as const, label: '运行摘要', count: '' }])

async function refresh() { loading.value = true; error.value = ''; const results = await Promise.allSettled([getPlatformAdmins(), getAccessTokens(), getAdminWorkers(), getAdminConfig(), getPermissionCatalog(), getConfigurationRollouts(), getConfigurationEvents()]); const setters = [(v: any) => admins.value = v, (v: any) => accessTokens.value = v, (v: any) => workers.value = v, (v: any) => config.value = v, (v: any) => permissionCatalog.value = v, (v: any) => rollouts.value = v, (v: any) => configurationEvents.value = v]; results.forEach((result, index) => { if (result.status === 'fulfilled') setters[index](result.value) }); const failed = results.find((item) => item.status === 'rejected') as PromiseRejectedResult | undefined; if (failed) error.value = failed.reason instanceof Error ? failed.reason.message : '部分控制面数据读取失败'; loading.value = false }
function applyRole() { adminDraft.permissions = [...(permissionCatalog.value.roles[adminDraft.role] || [])] }
async function createAdmin() { try { await savePlatformAdmin(adminDraft.user_id, { role: adminDraft.role, permissions: adminDraft.permissions, enabled: true, is_test_user: adminDraft.is_test_user }); message.success('管理员授权已保存'); adminDraft.user_id = ''; adminDraft.is_test_user = false; showAdminForm.value = false; await refresh() } catch (e) { message.error(errorText(e)) } }
async function removeAdmin(item: PlatformAdmin) { try { await deletePlatformAdmin(item.user_id); message.success('管理员已移除'); await refresh() } catch (e) { message.error(errorText(e)) } }
async function issueToken() { try { const result = await createAccessToken(tokenDraft); issuedToken.value = result.token; tokenDraft.label = ''; message.success('访问令牌已签发'); await refresh() } catch (e) { message.error(errorText(e)) } }
async function revokeToken(tokenId: string) { try { await revokeAccessToken(tokenId); message.success('访问令牌已吊销'); await refresh() } catch (e) { message.error(errorText(e)) } }
async function copyIssuedToken() { await navigator.clipboard.writeText(issuedToken.value); message.success('令牌已复制') }
function rolloutProgress(item: ConfigurationRollout) { return item.target_worker_count ? Math.round(item.acknowledged_worker_count / item.target_worker_count * 100) : 100 }
function rolloutClass(status: string) { return status === 'completed' ? 'completed' : ['failed', 'timed_out'].includes(status) ? 'failed' : ['cancelled', 'rolled_back'].includes(status) ? 'cancelled' : 'running' }
function rolloutLabel(status: string) { return ({ rolling_out: '预热中', awaiting_approval: '等待批准', completed: '已生效', failed: '预热失败', timed_out: '已超时', cancelled: '已取消', rolled_back: '已回滚' } as Record<string, string>)[status] || status }
async function operateRollout(item: ConfigurationRollout, action: 'approve' | 'cancel' | 'retry' | 'rollback') { const labels = { approve: '批准该版本生效', cancel: '取消本次发布', retry: '重试失败或超时节点', rollback: `回滚到 ${item.previous_revision_id}` }; if (!window.confirm(`${labels[action]}？`)) return; try { const calls = { approve: approveConfigurationRollout, cancel: cancelConfigurationRollout, retry: retryConfigurationRollout, rollback: rollbackConfigurationRollout }; await calls[action](item.rollout_id); message.success('发布状态已更新'); await refresh() } catch (e) { message.error(errorText(e)) } }
function compact(value: unknown) { const text = JSON.stringify(value ?? {}); return text.length > 120 ? `${text.slice(0, 120)}…` : text }
function formatDate(value?: string | null) { return value ? new Date(value).toLocaleString('zh-CN') : '—' }
function errorText(value: unknown) { return value instanceof Error ? value.message : '操作失败' }
onMounted(async () => { await refresh(); applyRole() })
</script>

<style scoped>
.platform-tabs { display: flex; gap: 8px; margin-bottom: 18px; overflow-x: auto; }.platform-tabs button { display: flex; gap: 8px; align-items: center; border: 1px solid var(--border); border-radius: 10px; padding: 9px 13px; background: var(--surface); color: var(--text-muted); white-space: nowrap; }.platform-tabs button.active { color: var(--text-strong); border-color: var(--accent); background: var(--accent-soft); }.platform-tabs small { min-width: 18px; text-align: center; }
.editor-form { display: grid; grid-template-columns: 1.2fr .8fr auto; gap: 14px; align-items: end; padding: 18px; border-top: 1px solid var(--border); }.editor-form.two-column { grid-template-columns: repeat(2,minmax(0,1fr)); }.editor-form label { display: grid; gap: 6px; color: var(--text-muted); font-size: 12px; }.editor-form input,.editor-form select,.editor-form textarea { width: 100%; border: 1px solid var(--border); border-radius: 8px; background: var(--surface); color: var(--text-strong); padding: 9px 10px; }.check-field { display: flex !important; align-items: center; padding-bottom: 10px; }.check-field input { width: auto; }.full-field { grid-column: 1/-1; }
.permission-grid { display: grid; grid-template-columns: repeat(3,minmax(0,1fr)); gap: 8px; }.permission-option { display: flex !important; align-items: flex-start; gap: 9px !important; border: 1px solid var(--border); border-radius: 8px; padding: 9px; }.permission-option input { width: auto; }.permission-option span { display: grid; gap: 2px; }.permission-option small { color: var(--text-muted); }
.subsection-heading { margin-top: 18px; border-top: 1px solid var(--border); }.issued-token { display: flex; align-items: center; gap: 12px; margin: 0 18px 18px; padding: 12px; border: 1px solid var(--accent); border-radius: 9px; background: var(--accent-soft); }.issued-token code { flex: 1; overflow-wrap: anywhere; }
.cluster-layout,.catalog-layout { display: grid; grid-template-columns: repeat(2,minmax(0,1fr)); gap: 18px; }.catalog-layout { grid-template-columns: minmax(280px,.72fr) minmax(0,1.5fr); }.management-list,.rollout-list,.event-list { display: grid; max-height: 600px; overflow: auto; }.management-list article,.event-list article { display: flex; gap: 12px; align-items: flex-start; padding: 13px 16px; border-top: 1px solid var(--border); }.management-list article>div,.event-list article>div { min-width: 0; display: grid; gap: 3px; }.management-list small,.management-list code,.event-list small { color: var(--text-muted); overflow: hidden; text-overflow: ellipsis; }
.rollout-list article { padding: 15px 16px; border-top: 1px solid var(--border); }.rollout-list header,.rollout-list header>div { display: flex; justify-content: space-between; align-items: center; gap: 8px; }.rollout-list header>div { justify-content: flex-start; }.rollout-list p { margin: 7px 0 0; color: var(--text-muted); font-size: 12px; }.rollout-list details code { display: block; margin-top: 6px; }.rollout-actions { display:flex;gap:8px;flex-wrap:wrap;margin-top:10px }.rollout-actions button { padding:7px 10px;font-size:11px }.progress-track { height: 5px; margin-top: 12px; overflow: hidden; border-radius: 99px; background: var(--border); }.progress-track i { display: block; height: 100%; background: var(--accent); transition: width .2s; }
.catalog-sidebar { align-self: start; }.catalog-row { display: flex; width: 100%; gap: 11px; align-items: center; text-align: left; padding: 12px 16px; border: 0; border-top: 1px solid var(--border); background: transparent; color: var(--text-strong); }.catalog-row.active { background: var(--accent-soft); }.catalog-row.static { cursor: default; }.catalog-row>div { display: grid; gap: 3px; }.catalog-row small { color: var(--text-muted); }.catalog-mark { display: grid; place-items: center; flex: 0 0 auto; width: 27px; height: 27px; border-radius: 8px; background: var(--accent-soft); color: var(--accent); font-weight: 700; }
.role-guide { display: grid; grid-template-columns: repeat(3,minmax(0,1fr)); gap: 8px; }.role-card { display: grid; gap: 7px; min-height: 78px; padding: 11px; border: 1px solid var(--border); border-radius: 9px; background: var(--surface); color: var(--text-muted); text-align: left; cursor: pointer; }.role-card:hover,.role-card.selected { border-color: var(--accent); background: var(--accent-soft); color: var(--text-strong); }.role-card-top { display: flex; align-items: baseline; justify-content: space-between; gap: 8px; color: var(--text-strong); }.role-card-top small { color: var(--text-muted); font-size: 10px; }.role-card>span:last-child { font-size: 12px; line-height: 1.45; }.role-detail { grid-column: 1/-1; padding: 11px 13px; border-left: 3px solid var(--accent); border-radius: 5px; background: var(--surface-raised); }.role-detail p { margin: 5px 0; color: var(--text-muted); font-size: 12px; line-height: 1.55; }.role-detail small { color: var(--text-muted); font-size: 11px; }
.revision-list { padding: 0 18px 18px; }.revision-list article { display: flex; justify-content: space-between; align-items: center; gap: 12px; padding: 11px 0; border-top: 1px solid var(--border); }.revision-list article>div { display: flex; align-items: center; gap: 9px; }.revision-list small { color: var(--text-muted); }.config-panel pre { margin: 0; padding: 18px; max-height: 620px; overflow: auto; color: var(--text-muted); }.danger-text { color: #d03050; } td small { display: block; color: var(--accent); }.event-list time { color: var(--text-muted); font-family: monospace; font-size: 12px; }
@media (max-width: 980px) { .cluster-layout,.catalog-layout { grid-template-columns: 1fr; }.permission-grid { grid-template-columns: 1fr 1fr; }.role-guide { grid-template-columns: 1fr; } } @media (max-width: 680px) { .editor-form,.editor-form.two-column,.permission-grid { grid-template-columns: 1fr; } }
</style>
