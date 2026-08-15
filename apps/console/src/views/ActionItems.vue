<template>
  <div class="page action-items-page">
    <header class="page-heading">
      <div><span class="eyebrow">HUMAN ACTIONS</span><h1>统一待办与审批</h1><p>这里只汇总 Runtime 正在等待你的补充信息或明确审批；不创建第二套待办状态。</p></div>
      <button class="secondary-button" type="button" :disabled="loading" @click="load">{{ loading ? '刷新中…' : '刷新' }}</button>
    </header>

    <div v-if="error" class="notice error-notice">{{ error }}</div>
    <section class="action-summary panel">
      <article><span>待补充</span><strong>{{ inputs.length }}</strong><small>Run 在收到信息后继续执行</small></article>
      <article><span>待审批</span><strong>{{ approvals.length }}</strong><small>高风险操作仍受权限与审计约束</small></article>
      <article><span>总待处理</span><strong>{{ items.length }}</strong><small>处理后自动从这里消失</small></article>
    </section>

    <section v-if="items.length" class="action-list">
      <article v-for="item in items" :key="item.item_id" class="panel action-card" :class="item.kind">
        <header>
          <div><span class="action-kind">{{ item.kind === 'input' ? '需要补充' : '需要审批' }}</span><h2>{{ item.run.title }}</h2><p>{{ item.run.agent_id }} · {{ formatDate(item.created_at) }}<span v-if="item.expires_at"> · 截止 {{ formatDate(item.expires_at) }}</span></p></div>
          <router-link class="secondary-button" :to="`/runs/${item.run.run_id}`">查看 Run</router-link>
        </header>

        <form v-if="item.kind === 'input'" class="input-action" @submit.prevent="submitInput(item)">
          <p class="action-question">{{ item.input.question }}</p>
          <div class="action-fields">
            <label v-for="field in orderedFields(item.input.fields)" :key="field.name">
              <span>{{ field.label || field.name }}<b v-if="field.required">*</b></span>
              <small v-if="field.description">{{ field.description }}</small>
              <select v-if="field.options?.length" v-model="inputValues[item.item_id][field.name]">
                <option value="">请选择</option><option v-for="option in field.options" :key="option.value" :value="option.value">{{ option.label }}</option>
              </select>
              <select v-else-if="field.enum?.length" v-model="inputValues[item.item_id][field.name]">
                <option value="">请选择</option><option v-for="option in field.enum" :key="String(option)" :value="String(option)">{{ String(option) }}</option>
              </select>
              <textarea v-else-if="field.input_mode === 'textarea' || field.value_type === 'object' || field.value_type === 'array'" v-model="inputValues[item.item_id][field.name]" rows="4" :placeholder="field.placeholder || '填写你的回答'" />
              <input v-else-if="field.value_type === 'number' || field.value_type === 'integer'" v-model="inputValues[item.item_id][field.name]" type="number" :placeholder="field.placeholder || '填写数字'" />
              <label v-else-if="field.value_type === 'boolean'" class="boolean-field"><input :checked="booleanValues[item.item_id]?.[field.name] || false" type="checkbox" @change="setBoolean(item.item_id, field.name, $event)" /><span>是</span></label>
              <input v-else v-model="inputValues[item.item_id][field.name]" :placeholder="field.placeholder || '填写你的回答'" />
            </label>
          </div>
          <footer><small>{{ item.input.source === 'agent' ? 'Agent 正在等待你的补充后恢复执行。' : '场景将在信息完整后继续执行。' }}</small><button class="primary-button" type="submit" :disabled="busyItem === item.item_id">{{ busyItem === item.item_id ? '提交中…' : '提交并继续执行' }}</button></footer>
        </form>

        <div v-else class="approval-action">
          <div class="approval-facts"><span>能力 <strong>{{ capabilityLabel(item) }}</strong></span><span>风险 <strong>{{ item.approval.risk }}</strong></span><span>数据 <strong>{{ item.approval.data_classification }}</strong></span><span>角色 <strong>{{ item.approval.required_role }}</strong></span></div>
          <pre v-if="Object.keys(item.approval.input_preview).length">{{ JSON.stringify(item.approval.input_preview, null, 2) }}</pre>
          <footer><small v-if="!item.approval.can_resolve">此审批需要 operator 权限，当前身份只能查看。</small><small v-else>批准后 Runtime 会恢复同一冻结 Action，不会重新生成写入身份。</small><div><button class="secondary-button" type="button" :disabled="busyItem === item.item_id || !item.approval.can_resolve" @click="resolveApproval(item, 'reject')">拒绝</button><button class="primary-button" type="button" :disabled="busyItem === item.item_id || !item.approval.can_resolve" @click="resolveApproval(item, 'approve')">{{ busyItem === item.item_id ? '处理中…' : '批准并继续' }}</button></div></footer>
        </div>
      </article>
    </section>
    <section v-else-if="!loading" class="panel empty-state"><span>✓</span><strong>现在没有需要你处理的事项</strong><p>当 Run 需要补充信息或高风险操作需要确认时，会自动出现在这里。</p></section>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { useMessage } from 'naive-ui'
import { listActionItems, resolveActionApproval, resolveActionInput, type ActionItem, type ApprovalActionItem, type InputActionItem } from '../api/actionItems'
import type { RunInputField } from '../api/runtime'

const message = useMessage()
const items = ref<ActionItem[]>([])
const loading = ref(false)
const error = ref('')
const busyItem = ref('')
const inputValues = reactive<Record<string, Record<string, string>>>({})
const booleanValues = reactive<Record<string, Record<string, boolean>>>({})
const inputs = computed(() => items.value.filter((item): item is InputActionItem => item.kind === 'input'))
const approvals = computed(() => items.value.filter((item): item is ApprovalActionItem => item.kind === 'approval'))

function orderedFields(fields: RunInputField[]) { return [...fields].sort((a, b) => Number(a.order || 0) - Number(b.order || 0)) }
function formatDate(value?: string | null) { return value ? new Intl.DateTimeFormat('zh-CN', { dateStyle: 'medium', timeStyle: 'short' }).format(new Date(value)) : '—' }
function capabilityLabel(item: ApprovalActionItem) { const ref = item.approval.capability_ref; return `${ref.capability_id || item.approval.subject_type}${ref.version ? ` · ${ref.version}` : ''}` }

function prepareInputValues(rows: ActionItem[]) {
  for (const item of rows) {
    if (item.kind !== 'input') continue
    inputValues[item.item_id] ||= {}
    booleanValues[item.item_id] ||= {}
    for (const field of item.input.fields) {
      if (inputValues[item.item_id][field.name] !== undefined) continue
      if (field.value_type === 'boolean') booleanValues[item.item_id][field.name] = false
      else inputValues[item.item_id][field.name] = ''
    }
  }
}

async function load() {
  loading.value = true; error.value = ''
  try { const next = await listActionItems(); items.value = next; prepareInputValues(next) }
  catch (cause) { error.value = cause instanceof Error ? cause.message : '读取待处理事项失败' }
  finally { loading.value = false }
}

function normalizedAnswers(item: InputActionItem) {
  const values = inputValues[item.item_id] || {}
  const answers: Record<string, unknown> = {}
  for (const field of item.input.fields) {
    const value = values[field.name]
    if (field.value_type === 'boolean') { answers[field.name] = Boolean(booleanValues[item.item_id]?.[field.name]); continue }
    if (value === '' || value === undefined || value === null) continue
    if (field.value_type === 'number' || field.value_type === 'integer') { answers[field.name] = Number(value); continue }
    if (field.value_type === 'array' || field.value_type === 'object') {
      try { answers[field.name] = JSON.parse(String(value)) }
      catch { throw new Error(`${field.label || field.name} 需要填写合法 JSON`) }
      continue
    }
    answers[field.name] = value
  }
  return answers
}

function setBoolean(itemId: string, fieldName: string, event: Event) {
  booleanValues[itemId] ||= {}
  booleanValues[itemId][fieldName] = (event.target as HTMLInputElement).checked
}

async function submitInput(item: InputActionItem) {
  busyItem.value = item.item_id
  try { await resolveActionInput(item, normalizedAnswers(item)); message.success('信息已提交，Run 将继续执行'); await load() }
  catch (cause) { message.error(cause instanceof Error ? cause.message : '提交失败') }
  finally { busyItem.value = '' }
}

async function resolveApproval(item: ApprovalActionItem, resolution: 'approve' | 'reject') {
  busyItem.value = item.item_id
  try { await resolveActionApproval(item, resolution); message.success(resolution === 'approve' ? '已批准，Run 将继续执行' : '已拒绝该操作'); await load() }
  catch (cause) { message.error(cause instanceof Error ? cause.message : '处理审批失败') }
  finally { busyItem.value = '' }
}

onMounted(load)
</script>

<style>
.action-summary { display:grid; grid-template-columns:repeat(3,minmax(0,1fr)); gap:12px; padding:14px; margin-bottom:18px; }
.action-summary article { padding:16px; border:1px solid var(--border); border-radius:12px; background:var(--surface-raised); }.action-summary span,.action-summary small { display:block; color:var(--text-muted); font-size:11px; }.action-summary strong { display:block; margin:5px 0; font-size:26px; }
.action-list { display:grid; gap:14px; }.action-card { padding:20px; }.action-card > header { display:flex; justify-content:space-between; gap:14px; border-bottom:1px solid var(--border); padding-bottom:14px; }.action-card h2 { margin:5px 0; font-size:18px; }.action-card header p,.action-question { margin:0; color:var(--text-muted); font-size:12px; }.action-kind { color:var(--accent); font-size:10px; font-weight:800; letter-spacing:.12em; }.action-card.input { border-left:3px solid var(--accent); }.action-card.approval { border-left:3px solid #dd9b35; }
.input-action,.approval-action { padding-top:15px; }.action-question { margin-bottom:14px; color:var(--text); font-size:14px; }.action-fields { display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:12px; }.action-fields > label { display:grid; gap:5px; }.action-fields span { font-size:12px; font-weight:700; }.action-fields b { color:var(--danger); margin-left:3px; }.action-fields small { color:var(--text-muted); font-size:11px; }.action-fields input,.action-fields select,.action-fields textarea { width:100%; box-sizing:border-box; }.boolean-field { display:flex !important; align-items:center; gap:8px; min-height:40px; }.boolean-field input { width:auto; }
.input-action footer,.approval-action footer { display:flex; justify-content:space-between; align-items:center; gap:12px; margin-top:15px; }.input-action footer small,.approval-action footer small { color:var(--text-muted); font-size:11px; }.approval-facts { display:flex; flex-wrap:wrap; gap:8px; }.approval-facts span { padding:6px 9px; border-radius:999px; background:var(--surface-raised); color:var(--text-muted); font-size:11px; }.approval-facts strong { color:var(--text); }.approval-action pre { max-height:220px; overflow:auto; margin:14px 0 0; padding:12px; border-radius:10px; background:var(--surface-raised); font-size:11px; }.approval-action footer > div { display:flex; gap:8px; }
@media (max-width:700px) { .action-summary,.action-fields { grid-template-columns:1fr; }.action-card > header,.input-action footer,.approval-action footer { align-items:flex-start; flex-direction:column; }.approval-action footer > div { width:100%; }.approval-action footer button { flex:1; } }
</style>
