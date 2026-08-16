<template>
  <div class="page event-page">
    <header class="page-heading">
      <div><span class="eyebrow">WEBHOOK / EXTERNAL EVENTS</span><h1>Webhook 与外部事件</h1><p>把外部系统事件安全映射为幂等 Run；规则、密钥轮换、投递结果和失败记录都可追踪。</p></div>
      <div class="heading-actions"><button class="secondary-button" type="button" :disabled="loading" @click="load">刷新</button><button class="primary-button" type="button" @click="startCreate">＋ 新建 Webhook</button></div>
    </header>

    <div class="webhook-contract"><span>外部系统</span><b>→</b><span>Secret 验证</span><b>→</b><span>Event Type 策略</span><b>→</b><span>Idempotency-Key 去重</span><b>→</b><span>Runtime Run</span></div>
    <div v-if="error" class="notice error-notice">{{ error }}</div>
    <div v-if="secretReveal" class="notice secret-notice"><div><strong>请立即保存新密钥</strong><span>密钥只在本次显示，服务端仅保存摘要。轮换后旧密钥立即失效。</span></div><code>{{ secretReveal }}</code><button class="secondary-button" type="button" @click="copy(secretReveal)">复制密钥</button><button class="text-button" type="button" @click="secretReveal = ''">我已保存</button></div>

    <section class="event-layout">
      <aside class="panel event-list-panel">
        <div class="panel-heading compact"><div><span class="eyebrow">RULES</span><h2>{{ triggers.length }} 条规则</h2></div><span class="active-count">{{ enabledCount }} 启用</span></div>
        <div v-if="triggers.length" class="event-list">
          <button v-for="item in triggers" :key="item.trigger_id" type="button" :class="{ active: selectedId === item.trigger_id }" @click="select(item)">
            <span class="state-dot" :class="{ on: item.enabled }" /><span><strong>{{ item.name }}</strong><small>{{ item.event_type_filter }}</small><em>{{ item.agent_id }} · Secret v{{ item.secret_version }}</em></span><time>{{ relativeTime(item.updated_at) }}</time>
          </button>
        </div>
        <div v-else class="empty-state"><span>H</span><strong>还没有 Webhook 规则</strong><p>创建后会获得一次性签名密钥和稳定的接收地址。</p></div>
      </aside>

      <main class="panel event-detail">
        <template v-if="editing">
          <div class="panel-heading"><div><span class="eyebrow">{{ createMode ? 'NEW WEBHOOK' : 'EDIT WEBHOOK' }}</span><h2>{{ createMode ? '创建外部事件入口' : '编辑 Webhook 规则' }}</h2></div><button class="text-button" type="button" @click="cancelEdit">取消</button></div>
          <form class="event-form" @submit.prevent="save">
            <label><span>规则名称</span><input v-model.trim="form.name" required maxlength="128" placeholder="例如：CRM 联系人变更" /></label>
            <label><span>执行 Agent</span><select v-model="form.agent_id"><option v-for="agent in agents" :key="agent.id" :value="agent.id">{{ agent.name }} · {{ agent.id }}</option><option v-if="!agents.length" value="default">default</option></select></label>
            <label><span>Event Type</span><input v-model.trim="form.event_type_filter" required pattern="^(\*|[A-Za-z0-9_.:-]+)$" placeholder="crm.contact.updated 或 *" /><small>* 表示接受所有事件类型；生产环境建议固定类型。</small></label>
            <label><span>会话隔离</span><select v-model="form.session_mode"><option value="per_event">每个事件独立会话</option><option value="shared">共享持续会话</option></select><small>独立会话适合业务事件；共享会话适合需要连续上下文的事件流。</small></label>
            <label v-if="form.session_mode === 'shared'"><span>共享 Session ID（可选）</span><input v-model.trim="form.session_id" pattern="^[A-Za-z0-9_.:-]{1,128}$" placeholder="留空则自动生成" /></label>
            <label class="wide"><span>事件处理指令</span><textarea v-model.trim="form.instruction" rows="7" required maxlength="4000" placeholder="说明 Agent 收到事件后要完成什么、如何验证，以及需要形成什么成果。" /></label>
            <label class="toggle-row wide"><input v-model="form.enabled" type="checkbox" /><span><strong>立即启用</strong><small>停用后入口保留，但所有新投递会被拒绝。</small></span></label>
            <div class="form-actions wide"><button class="primary-button" type="submit" :disabled="saving">{{ saving ? '保存中…' : (createMode ? '创建并生成密钥' : '保存修改') }}</button></div>
          </form>
        </template>

        <template v-else-if="selected">
          <div class="panel-heading detail-heading"><div><span class="eyebrow">EVENT ENTRY</span><h2>{{ selected.name }}</h2><p>{{ selected.instruction }}</p></div><span class="status-badge" :class="selected.enabled ? 'completed' : 'cancelled'">{{ selected.enabled ? '已启用' : '已停用' }}</span></div>
          <div class="trigger-facts"><div><span>Event Type</span><strong>{{ selected.event_type_filter }}</strong></div><div><span>Agent</span><strong>{{ selected.agent_id }}</strong></div><div><span>会话模式</span><strong>{{ selected.session_mode === 'per_event' ? '事件隔离' : '共享会话' }}</strong></div><div><span>Secret</span><strong>Version {{ selected.secret_version }}</strong></div></div>
          <section class="endpoint-card"><div><span class="eyebrow">ENDPOINT</span><code>{{ endpointUrl(selected) }}</code></div><button class="secondary-button" type="button" @click="copy(endpointUrl(selected))">复制地址</button></section>
          <div class="request-contract"><strong>请求契约</strong><code>POST {{ selected.endpoint_path }}</code><code>X-Porthouse-Webhook-Secret: &lt;secret&gt;</code><code>Idempotency-Key: &lt;stable-event-id&gt;</code><pre>{
  "event_type": "{{ selected.event_type_filter === '*' ? 'business.event' : selected.event_type_filter }}",
  "payload": { "...": "..." }
}</pre><p>相同 Idempotency-Key 与相同 Payload 会返回原 Run；同一键对应不同 Payload 会返回 409，避免重复或歧义执行。</p></div>
          <div class="detail-actions-row"><button class="primary-button" type="button" @click="startEdit">编辑规则</button><button class="secondary-button" type="button" :disabled="acting" @click="toggleEnabled">{{ selected.enabled ? '停用入口' : '启用入口' }}</button><button class="secondary-button" type="button" :disabled="acting" @click="rotateSecret">轮换密钥</button><button class="danger-button" type="button" :disabled="acting" @click="remove">删除</button></div>

          <section class="delivery-section"><div class="section-heading"><div><span class="eyebrow">DELIVERIES</span><h3>事件投递</h3></div><span>{{ deliveries.length }} 条</span></div><div v-if="deliveries.length" class="delivery-list"><article v-for="item in deliveries" :key="item.delivery_id"><span class="status-badge" :class="item.status">{{ deliveryStatus(item.status) }}</span><div><strong>{{ item.event_type }}</strong><small>{{ item.idempotency_key }} · 尝试 {{ item.attempt }} · {{ formatDate(item.received_at) }}</small><p v-if="item.error">{{ item.error }}</p></div><router-link v-if="item.run_id" :to="`/runs/${item.run_id}`">查看 Run →</router-link><span v-else>{{ item.status === 'processing' ? '处理中' : '未创建 Run' }}</span></article></div><div v-else class="empty-state compact"><strong>尚无外部事件</strong><p>投递成功后会在这里显示去重结果和关联 Run。</p></div></section>
        </template>

        <div v-else class="empty-state"><span>H</span><strong>选择一条 Webhook 规则</strong><p>查看接收地址、请求契约、密钥版本和投递审计。</p></div>
      </main>
    </section>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { getAgents, type AgentListItem } from '../api/agent'
import {
  createEventTrigger, deleteEventTrigger, listEventTriggerDeliveries, listEventTriggers,
  rotateEventTriggerSecret, updateEventTrigger, type EventTrigger,
  type EventTriggerDelivery, type EventTriggerWrite,
} from '../api/automation'

const triggers = ref<EventTrigger[]>([])
const deliveries = ref<EventTriggerDelivery[]>([])
const agents = ref<AgentListItem[]>([])
const selectedId = ref('')
const loading = ref(false)
const saving = ref(false)
const acting = ref(false)
const editing = ref(false)
const createMode = ref(false)
const error = ref('')
const secretReveal = ref('')
const form = reactive({ name: '', agent_id: 'default', event_type_filter: '*', instruction: '', session_mode: 'per_event' as 'shared' | 'per_event', session_id: '', enabled: true })

const selected = computed(() => triggers.value.find((item) => item.trigger_id === selectedId.value) || null)
const enabledCount = computed(() => triggers.value.filter((item) => item.enabled).length)
function resetForm() { Object.assign(form, { name: '', agent_id: agents.value[0]?.id || 'default', event_type_filter: '*', instruction: '', session_mode: 'per_event', session_id: '', enabled: true }) }
function startCreate() { resetForm(); editing.value = true; createMode.value = true; selectedId.value = ''; deliveries.value = []; secretReveal.value = '' }
function startEdit() { if (!selected.value) return; Object.assign(form, { name: selected.value.name, agent_id: selected.value.agent_id, event_type_filter: selected.value.event_type_filter, instruction: selected.value.instruction, session_mode: selected.value.session_mode, session_id: selected.value.session_id || '', enabled: selected.value.enabled }); editing.value = true; createMode.value = false }
function cancelEdit() { editing.value = false; if (createMode.value && triggers.value[0]) void select(triggers.value[0]); createMode.value = false }
function writePayload(): EventTriggerWrite { return { name: form.name, agent_id: form.agent_id, event_type_filter: form.event_type_filter, instruction: form.instruction, session_mode: form.session_mode, session_id: form.session_mode === 'shared' && form.session_id ? form.session_id : null, enabled: form.enabled } }

async function load() { loading.value = true; error.value = ''; try { const [items, agentItems] = await Promise.all([listEventTriggers(), getAgents().catch(() => ({ ok: false, agents: [] }))]); triggers.value = items; agents.value = agentItems.agents; if (selectedId.value) { const fresh = triggers.value.find((item) => item.trigger_id === selectedId.value); if (fresh) await select(fresh); else selectedId.value = '' } } catch (cause) { error.value = cause instanceof Error ? cause.message : '读取 Webhook 规则失败' } finally { loading.value = false } }
async function select(item: EventTrigger) { selectedId.value = item.trigger_id; editing.value = false; createMode.value = false; error.value = ''; try { deliveries.value = await listEventTriggerDeliveries(item.trigger_id) } catch (cause) { error.value = cause instanceof Error ? cause.message : '读取投递记录失败' } }
async function save() { saving.value = true; error.value = ''; try { const saved = createMode.value ? await createEventTrigger(writePayload()) : await updateEventTrigger(selectedId.value, writePayload()); secretReveal.value = saved.signing_secret || ''; await load(); const current = triggers.value.find((item) => item.trigger_id === saved.trigger_id); if (current) await select(current) } catch (cause) { error.value = cause instanceof Error ? cause.message : '保存 Webhook 规则失败' } finally { saving.value = false } }
async function toggleEnabled() { if (!selected.value) return; acting.value = true; error.value = ''; try { await updateEventTrigger(selected.value.trigger_id, { enabled: !selected.value.enabled }); await load() } catch (cause) { error.value = cause instanceof Error ? cause.message : '更新入口状态失败' } finally { acting.value = false } }
async function rotateSecret() { if (!selected.value || !window.confirm('轮换后旧密钥会立即失效，确认继续？')) return; acting.value = true; error.value = ''; try { const rotated = await rotateEventTriggerSecret(selected.value.trigger_id); secretReveal.value = rotated.signing_secret || ''; await load() } catch (cause) { error.value = cause instanceof Error ? cause.message : '轮换密钥失败' } finally { acting.value = false } }
async function remove() { if (!selected.value || !window.confirm(`删除 Webhook 规则“${selected.value.name}”及其投递审计？`)) return; acting.value = true; error.value = ''; try { await deleteEventTrigger(selected.value.trigger_id); selectedId.value = ''; deliveries.value = []; await load() } catch (cause) { error.value = cause instanceof Error ? cause.message : '删除 Webhook 规则失败' } finally { acting.value = false } }
async function copy(value: string) { try { await navigator.clipboard.writeText(value) } catch { error.value = '浏览器未允许复制，请手动选择内容。' } }
function endpointUrl(item: EventTrigger) { return `${window.location.origin}${item.endpoint_path}` }
function deliveryStatus(value: string) { return ({ processing: '处理中', submitted: '已提交', failed: '失败' } as Record<string, string>)[value] || value }
function formatDate(value: string) { return new Date(value).toLocaleString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' }) }
function relativeTime(value: string) { const delta = Date.now() - new Date(value).getTime(); if (delta < 60_000) return '刚刚'; if (delta < 3_600_000) return `${Math.floor(delta / 60_000)} 分钟前`; if (delta < 86_400_000) return `${Math.floor(delta / 3_600_000)} 小时前`; return new Date(value).toLocaleDateString('zh-CN') }
onMounted(async () => { await load(); if (triggers.value[0]) await select(triggers.value[0]) })
</script>

<style scoped>
.event-page { display: grid; gap: 16px; }.webhook-contract { display: flex; flex-wrap: wrap; align-items: center; gap: 9px; padding: 12px 16px; color: var(--text-muted); background: var(--surface-raised); border: 1px solid var(--border); border-radius: var(--radius-md); font-size: 11px; }.webhook-contract span { padding: 6px 9px; color: var(--text); background: var(--surface); border-radius: 7px; }.webhook-contract b { color: var(--accent); }.secret-notice { display: grid; grid-template-columns: minmax(210px, .7fr) minmax(260px, 1fr) auto auto; align-items: center; gap: 12px; color: var(--text-muted); background: var(--accent-subtle); border: 1px solid var(--accent-border); }.secret-notice div { display: grid; gap: 3px; }.secret-notice strong { color: var(--text-strong); }.secret-notice span { font-size: 10px; }.secret-notice code { overflow: hidden; padding: 8px 10px; color: var(--accent); background: var(--surface); border-radius: 8px; text-overflow: ellipsis; white-space: nowrap; }.event-layout { display: grid; grid-template-columns: minmax(290px, .72fr) minmax(0, 1.55fr); gap: 16px; min-height: 650px; }.event-list-panel,.event-detail { overflow: hidden; }.panel-heading.compact { padding-bottom: 12px; }.active-count { color: var(--success); font: 10px var(--font-mono); }.event-list { display: grid; padding: 0 10px 12px; }.event-list button { display: grid; grid-template-columns: 12px minmax(0,1fr) auto; gap: 10px; align-items: center; min-height: 82px; padding: 12px; color: var(--text); background: transparent; border: 0; border-top: 1px solid var(--border); text-align: left; cursor: pointer; }.event-list button:hover,.event-list button.active { background: var(--surface-hover); border-radius: 10px; }.event-list button>span:nth-child(2) { display: grid; min-width: 0; gap: 3px; }.event-list strong,.event-list small,.event-list em { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }.event-list strong { color: var(--text-strong); font-size: 12px; }.event-list small,.event-list em,.event-list time { color: var(--text-muted); font-size: 9px; font-style: normal; }.event-list time { align-self: start; white-space: nowrap; }.event-detail>.empty-state { min-height: 520px; }.event-form { display: grid; grid-template-columns: 1fr 1fr; gap: 15px; padding: 0 22px 24px; }.event-form label { display: grid; align-content: start; gap: 6px; }.event-form label>span { color: var(--text-muted); font-size: 10px; }.event-form label>small { color: var(--text-muted); font-size: 9px; line-height: 1.5; }.event-form input,.event-form select,.event-form textarea { width: 100%; padding: 10px 11px; color: var(--text); background: var(--input); border: 1px solid var(--border); border-radius: 9px; outline: none; }.event-form input:focus,.event-form select:focus,.event-form textarea:focus { border-color: var(--accent-border); }.event-form .wide { grid-column: 1 / -1; }.toggle-row { grid-template-columns: auto 1fr; align-items: center; padding: 12px; background: var(--surface-raised); border-radius: 10px; }.toggle-row input { width: auto; }.toggle-row span { display: grid; gap: 2px; }.toggle-row strong { color: var(--text-strong); font-size: 11px; }.toggle-row small { font-size: 9px; }.form-actions { display: flex; justify-content: flex-end; }.detail-heading p { max-width: 680px; margin: 7px 0 0; color: var(--text-muted); font-size: 11px; }.trigger-facts { display: grid; grid-template-columns: repeat(4,1fr); border-block: 1px solid var(--border); }.trigger-facts div { min-width: 0; padding: 13px 17px; border-right: 1px solid var(--border); }.trigger-facts div:last-child { border-right: 0; }.trigger-facts span,.trigger-facts strong { display: block; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }.trigger-facts span { color: var(--text-muted); font: 9px var(--font-mono); }.trigger-facts strong { margin-top: 5px; color: var(--text-strong); font-size: 11px; }.endpoint-card { display: flex; align-items: center; justify-content: space-between; gap: 14px; margin: 18px 22px 12px; padding: 14px; background: var(--accent-subtle); border: 1px solid var(--accent-border); border-radius: 11px; }.endpoint-card div { display: grid; min-width: 0; gap: 6px; }.endpoint-card code { overflow: hidden; color: var(--text-strong); text-overflow: ellipsis; white-space: nowrap; }.request-contract { display: grid; gap: 7px; margin: 0 22px 15px; padding: 14px; background: var(--surface-raised); border-radius: 11px; }.request-contract strong { color: var(--text-strong); font-size: 12px; }.request-contract code { color: var(--accent); font-size: 10px; }.request-contract pre { margin: 2px 0; padding: 10px; overflow: auto; color: var(--text); background: var(--input); border: 1px solid var(--border); border-radius: 8px; font: 10px/1.6 var(--font-mono); }.request-contract p { margin: 0; color: var(--text-muted); font-size: 9px; line-height: 1.6; }.detail-actions-row { display: flex; flex-wrap: wrap; gap: 8px; padding: 0 22px 18px; }.danger-button { padding: 9px 14px; color: var(--danger); background: rgba(227,93,106,.07); border: 1px solid rgba(227,93,106,.2); border-radius: 9px; cursor: pointer; }.delivery-section { border-top: 1px solid var(--border); }.section-heading { display: flex; justify-content: space-between; align-items: center; padding: 18px 22px 11px; }.section-heading h3 { margin: 4px 0 0; color: var(--text-strong); font-size: 15px; }.section-heading>span { color: var(--text-muted); font: 10px var(--font-mono); }.delivery-list { display: grid; padding: 0 12px 14px; }.delivery-list article { display: grid; grid-template-columns: 76px minmax(0,1fr) auto; gap: 11px; align-items: center; min-height: 64px; padding: 9px 10px; border-top: 1px solid var(--border); }.delivery-list article div { min-width: 0; }.delivery-list strong,.delivery-list small { display: block; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }.delivery-list strong { color: var(--text-strong); font-size: 11px; }.delivery-list small,.delivery-list p,.delivery-list article>span:last-child { color: var(--text-muted); font-size: 9px; }.delivery-list p { margin: 4px 0 0; color: var(--danger); }.delivery-list a { color: var(--accent); font-size: 10px; white-space: nowrap; }
@media (max-width: 1100px) { .event-layout { grid-template-columns: 1fr; }.event-list-panel { max-height: 430px; overflow-y: auto; }.secret-notice { grid-template-columns: 1fr auto; }.secret-notice code { grid-column: 1 / -1; } }
@media (max-width: 700px) { .event-form,.trigger-facts { grid-template-columns: 1fr; }.trigger-facts div { border-right: 0; border-bottom: 1px solid var(--border); }.endpoint-card { align-items: flex-start; flex-direction: column; }.delivery-list article { grid-template-columns: 70px minmax(0,1fr); }.delivery-list article a,.delivery-list article>span:last-child { grid-column: 2; } }
</style>
