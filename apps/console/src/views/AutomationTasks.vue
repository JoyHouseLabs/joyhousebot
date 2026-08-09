<template>
  <div class="page automation-page">
    <header class="page-heading">
      <div><span class="eyebrow">AUTOMATION TASKS</span><h1>自动化任务</h1><p>集中创建、暂停、恢复、立即补跑并查看每次触发如何进入统一 Run 链路。</p></div>
      <div class="heading-actions"><button class="secondary-button" type="button" :disabled="loading" @click="load">刷新</button><button class="primary-button" type="button" @click="startCreate">＋ 新建任务</button></div>
    </header>

    <div class="automation-contract"><span>触发器只决定何时执行</span><b>→</b><span>冻结 occurrence 与幂等键</span><b>→</b><span>提交 Runtime Run</span><b>→</b><span>Worker 执行、重试和回放</span></div>
    <div v-if="error" class="notice error-notice">{{ error }}</div>

    <section class="automation-layout">
      <aside class="panel automation-list-panel">
        <div class="panel-heading compact"><div><span class="eyebrow">TASKS</span><h2>{{ schedules.length }} 个任务</h2></div><div class="task-counts"><span>{{ enabledCount }} 运行</span><span>{{ schedules.length - enabledCount }} 暂停</span></div></div>
        <div v-if="schedules.length" class="automation-list">
          <button v-for="item in schedules" :key="item.id" type="button" :class="{ active: selectedId === item.id }" @click="select(item)">
            <span class="state-dot" :class="{ on: item.enabled }" />
            <span><strong>{{ item.name }}</strong><small>{{ scheduleLabel(item) }}</small><em>{{ item.agent_id }} · {{ item.payload.managed_by === 'agent_revision' ? 'Agent 托管' : '个人任务' }}</em></span>
            <time>{{ nextRunLabel(item) }}</time>
          </button>
        </div>
        <div v-else class="empty-state"><span>◷</span><strong>还没有自动化任务</strong><p>创建任务后，Scheduler 会按 PostgreSQL Lease 接管每次触发。</p></div>
      </aside>

      <main class="panel automation-detail">
        <template v-if="editing">
          <div class="panel-heading"><div><span class="eyebrow">{{ createMode ? 'NEW AUTOMATION' : 'EDIT AUTOMATION' }}</span><h2>{{ createMode ? '创建自动化任务' : '编辑任务' }}</h2></div><button class="text-button" type="button" @click="cancelEdit">取消</button></div>
          <form class="automation-form" @submit.prevent="save">
            <label><span>任务名称</span><input v-model.trim="form.name" required maxlength="128" placeholder="例如：每天整理项目进展" /></label>
            <label><span>执行 Agent</span><select v-model="form.agent_id"><option v-for="agent in agents" :key="agent.id" :value="agent.id">{{ agent.name }} · {{ agent.id }}</option><option v-if="!agents.length" value="default">default</option></select></label>
            <label><span>触发类型</span><select v-model="form.kind"><option value="every">固定间隔</option><option value="cron">Cron 表达式</option><option value="at">单次执行</option></select></label>
            <label v-if="form.kind === 'every'"><span>间隔（分钟，至少 1 分钟）</span><input v-model.number="form.every_minutes" type="number" min="1" max="525600" required /></label>
            <label v-if="form.kind === 'cron'"><span>Cron 表达式</span><input v-model.trim="form.cron_expr" required placeholder="0 9 * * *" /></label>
            <label v-if="form.kind === 'cron'"><span>时区</span><input v-model.trim="form.timezone" required placeholder="Asia/Shanghai" /></label>
            <label v-if="form.kind === 'at'"><span>执行时间</span><input v-model="form.at_local" type="datetime-local" required /></label>
            <label class="wide"><span>执行目标</span><textarea v-model.trim="form.message" rows="7" required maxlength="10000" placeholder="清楚描述需要 Agent 可靠完成的工作，以及期望交付的成果。" /></label>
            <label class="toggle-row wide"><input v-model="form.enabled" type="checkbox" /><span><strong>保存后启用</strong><small>暂停状态仍可手动补跑，不会自动触发。</small></span></label>
            <div class="form-actions wide"><button class="primary-button" type="submit" :disabled="saving">{{ saving ? '保存中…' : (createMode ? '创建任务' : '保存修改') }}</button></div>
          </form>
        </template>

        <template v-else-if="selected">
          <div class="panel-heading detail-heading"><div><span class="eyebrow">SCHEDULE DETAIL</span><h2>{{ selected.name }}</h2><p>{{ selected.payload.message }}</p></div><span class="status-badge" :class="selected.enabled ? 'completed' : 'cancelled'">{{ selected.enabled ? '运行中' : '已暂停' }}</span></div>
          <div class="schedule-facts"><div><span>Agent</span><strong>{{ selected.agent_id }}</strong></div><div><span>触发规则</span><strong>{{ scheduleLabel(selected) }}</strong></div><div><span>下次执行</span><strong>{{ nextRunLabel(selected) }}</strong></div><div><span>最近状态</span><strong>{{ selected.state?.last_status || '尚未触发' }}</strong></div></div>
          <div class="detail-actions-row">
            <button class="primary-button" type="button" :disabled="acting" @click="runNow">立即补跑</button>
            <button class="secondary-button" type="button" :disabled="acting || managed" @click="toggleEnabled">{{ selected.enabled ? '暂停' : '恢复' }}</button>
            <button class="secondary-button" type="button" :disabled="managed" @click="startEdit">编辑</button>
            <button class="danger-button" type="button" :disabled="acting || managed" @click="remove">删除</button>
            <router-link v-if="managed" class="secondary-button" to="/agents">前往 Agent 配置</router-link>
          </div>
          <p v-if="managed" class="managed-note">该任务由 Agent 发布版本托管。触发策略请在 Agent 的 Monitor 配置中修改，避免控制面与运行时产生两个事实源。</p>

          <section class="history-section">
            <div class="section-heading"><div><span class="eyebrow">TRIGGER HISTORY</span><h3>触发历史</h3></div><span>{{ runs.length }} 条</span></div>
            <div v-if="runs.length" class="history-list">
              <article v-for="run in runs" :key="run.id"><span class="status-badge" :class="run.status">{{ occurrenceStatus(run.status) }}</span><div><strong>{{ formatDate(run.startedAtMs) }}</strong><small>Occurrence {{ run.id }} · 尝试 {{ run.attempt }} / 提交 {{ run.submitAttempt }}</small><p v-if="run.error">{{ run.error }}</p></div><router-link v-if="run.runId" :to="`/runs/${run.runId}`">查看 Run →</router-link><span v-else>未创建 Run</span></article>
            </div>
            <div v-else class="empty-state compact"><strong>尚无触发记录</strong><p>点击“立即补跑”可验证整条调度与执行链路。</p></div>
          </section>
        </template>

        <div v-else class="empty-state"><span>A</span><strong>选择一个自动化任务</strong><p>右侧将显示策略、状态、补跑操作和完整触发历史。</p></div>
      </main>
    </section>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { getAgents, type AgentListItem } from '../api/agent'
import {
  createSchedule, deleteSchedule, listScheduleRuns, listSchedules, runScheduleNow,
  updateSchedule, type ScheduleOccurrence, type ScheduleWrite,
} from '../api/automation'
import type { ScheduleItem } from '../api/monitoring'

const schedules = ref<ScheduleItem[]>([])
const agents = ref<AgentListItem[]>([])
const runs = ref<ScheduleOccurrence[]>([])
const selectedId = ref('')
const loading = ref(false)
const saving = ref(false)
const acting = ref(false)
const editing = ref(false)
const createMode = ref(false)
const error = ref('')
const localTimezone = Intl.DateTimeFormat().resolvedOptions().timeZone || 'Asia/Shanghai'
const form = reactive({ name: '', agent_id: 'default', kind: 'every' as 'at' | 'every' | 'cron', every_minutes: 60, cron_expr: '0 9 * * *', timezone: localTimezone, at_local: '', message: '', enabled: true })

const selected = computed(() => schedules.value.find((item) => item.id === selectedId.value) || null)
const enabledCount = computed(() => schedules.value.filter((item) => item.enabled).length)
const managed = computed(() => selected.value?.payload.managed_by === 'agent_revision')

function resetForm() { Object.assign(form, { name: '', agent_id: agents.value[0]?.id || 'default', kind: 'every', every_minutes: 60, cron_expr: '0 9 * * *', timezone: localTimezone, at_local: '', message: '', enabled: true }) }
function startCreate() { resetForm(); createMode.value = true; editing.value = true; selectedId.value = ''; runs.value = [] }
function startEdit() { if (!selected.value) return; const item = selected.value; form.name = item.name; form.agent_id = item.agent_id; form.kind = item.schedule.kind as typeof form.kind; form.every_minutes = Math.max(1, Math.round(Number(item.schedule.every_ms || 60_000) / 60_000)); form.cron_expr = item.schedule.expr || '0 9 * * *'; form.timezone = item.schedule.tz || localTimezone; form.at_local = item.schedule.at_ms ? toLocalInput(item.schedule.at_ms) : ''; form.message = item.payload.message; form.enabled = item.enabled; createMode.value = false; editing.value = true }
function cancelEdit() { editing.value = false; if (createMode.value && schedules.value.length) void select(schedules.value[0]); createMode.value = false }
function toLocalInput(value: number) { const date = new Date(value - new Date(value).getTimezoneOffset() * 60_000); return date.toISOString().slice(0, 16) }
function schedulePayload(): ScheduleWrite { const schedule: ScheduleWrite['schedule'] = { kind: form.kind }; if (form.kind === 'every') schedule.every_ms = Math.round(form.every_minutes * 60_000); if (form.kind === 'cron') { schedule.cron_expr = form.cron_expr; schedule.timezone = form.timezone } if (form.kind === 'at') schedule.at_ms = new Date(form.at_local).getTime(); return { name: form.name, agent_id: form.agent_id, schedule, payload: { kind: 'agent_turn', message: form.message }, enabled: form.enabled } }

async function load() { loading.value = true; error.value = ''; try { const [taskItems, agentItems] = await Promise.all([listSchedules(), getAgents().catch(() => ({ ok: false, agents: [] }))]); schedules.value = taskItems; agents.value = agentItems.agents; if (selectedId.value) { const fresh = schedules.value.find((item) => item.id === selectedId.value); if (fresh) await select(fresh); else selectedId.value = '' } } catch (cause) { error.value = cause instanceof Error ? cause.message : '读取自动化任务失败' } finally { loading.value = false } }
async function select(item: ScheduleItem) { selectedId.value = item.id; editing.value = false; createMode.value = false; error.value = ''; try { runs.value = await listScheduleRuns(item.id) } catch (cause) { error.value = cause instanceof Error ? cause.message : '读取触发历史失败' } }
async function save() { saving.value = true; error.value = ''; try { const payload = schedulePayload(); const saved = createMode.value ? await createSchedule(payload) : await updateSchedule(selectedId.value, payload); await load(); const current = schedules.value.find((item) => item.id === saved.id); if (current) await select(current) } catch (cause) { error.value = cause instanceof Error ? cause.message : '保存自动化任务失败' } finally { saving.value = false } }
async function toggleEnabled() { if (!selected.value) return; acting.value = true; error.value = ''; try { await updateSchedule(selected.value.id, { enabled: !selected.value.enabled }); await load() } catch (cause) { error.value = cause instanceof Error ? cause.message : '更新状态失败' } finally { acting.value = false } }
async function runNow() { if (!selected.value) return; acting.value = true; error.value = ''; try { await runScheduleNow(selected.value.id); await load(); if (selected.value) runs.value = await listScheduleRuns(selected.value.id) } catch (cause) { error.value = cause instanceof Error ? cause.message : '补跑失败' } finally { acting.value = false } }
async function remove() { if (!selected.value || !window.confirm(`删除自动化任务“${selected.value.name}”？历史 Run 不会删除。`)) return; acting.value = true; error.value = ''; try { await deleteSchedule(selected.value.id); selectedId.value = ''; runs.value = []; await load() } catch (cause) { error.value = cause instanceof Error ? cause.message : '删除失败' } finally { acting.value = false } }
function scheduleLabel(item: ScheduleItem) { if (item.schedule.kind === 'cron') return `${item.schedule.expr || 'cron'} · ${item.schedule.tz || 'local'}`; if (item.schedule.kind === 'every') return `每 ${Math.round(Number(item.schedule.every_ms || 0) / 60_000)} 分钟`; return item.schedule.at_ms ? `单次 · ${formatDate(item.schedule.at_ms)}` : '单次执行' }
function nextRunLabel(item: ScheduleItem) { return item.enabled && item.state?.next_run_at_ms ? formatDate(item.state.next_run_at_ms) : '未计划' }
function formatDate(value: number) { return new Date(value).toLocaleString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' }) }
function occurrenceStatus(value: string) { return ({ submitted: '已提交', completed: '完成', failed: '失败', error: '提交失败', retry_wait: '等待重试', skipped_misfire: '错过已跳过', skipped_overlap: '重叠已跳过', skipped_busy: '繁忙已跳过', skipped_unchanged: '无变化' } as Record<string, string>)[value] || value }

onMounted(async () => { await load(); if (schedules.value[0]) await select(schedules.value[0]) })
</script>

<style scoped>
.automation-page { display: grid; gap: 16px; }.automation-contract { display: flex; flex-wrap: wrap; align-items: center; gap: 10px; padding: 12px 16px; color: var(--text-muted); background: var(--surface-raised); border: 1px solid var(--border); border-radius: var(--radius-md); font-size: 11px; }.automation-contract span { padding: 6px 9px; color: var(--text); background: var(--surface); border-radius: 7px; }.automation-contract b { color: var(--accent); }.automation-layout { display: grid; grid-template-columns: minmax(290px, .72fr) minmax(0, 1.55fr); gap: 16px; min-height: 620px; }.automation-list-panel,.automation-detail { overflow: hidden; }.panel-heading.compact { padding-bottom: 12px; }.task-counts { display: flex; gap: 6px; }.task-counts span { padding: 5px 7px; color: var(--text-muted); background: var(--surface-raised); border-radius: 6px; font: 9px var(--font-mono); }.automation-list { display: grid; padding: 0 10px 12px; }.automation-list button { display: grid; grid-template-columns: 12px minmax(0, 1fr) auto; gap: 10px; align-items: center; min-height: 82px; padding: 12px; color: var(--text); background: transparent; border: 0; border-top: 1px solid var(--border); text-align: left; cursor: pointer; }.automation-list button:hover,.automation-list button.active { background: var(--surface-hover); border-radius: 10px; }.automation-list button>span:nth-child(2) { display: grid; min-width: 0; gap: 3px; }.automation-list strong,.automation-list small,.automation-list em { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }.automation-list strong { color: var(--text-strong); font-size: 12px; }.automation-list small,.automation-list em,.automation-list time { color: var(--text-muted); font-size: 9px; font-style: normal; }.automation-list time { align-self: start; white-space: nowrap; }.automation-detail>.empty-state { min-height: 500px; }.automation-form { display: grid; grid-template-columns: 1fr 1fr; gap: 15px; padding: 0 22px 24px; }.automation-form label { display: grid; gap: 6px; }.automation-form label>span { color: var(--text-muted); font-size: 10px; }.automation-form input,.automation-form select,.automation-form textarea { width: 100%; padding: 10px 11px; color: var(--text); background: var(--input); border: 1px solid var(--border); border-radius: 9px; outline: none; }.automation-form input:focus,.automation-form select:focus,.automation-form textarea:focus { border-color: var(--accent-border); }.automation-form .wide { grid-column: 1 / -1; }.toggle-row { grid-template-columns: auto 1fr; align-items: center; padding: 12px; background: var(--surface-raised); border-radius: 10px; }.toggle-row input { width: auto; }.toggle-row span { display: grid; gap: 2px; }.toggle-row strong { color: var(--text-strong); font-size: 11px; }.toggle-row small { font-size: 9px; }.form-actions { display: flex; justify-content: flex-end; }.detail-heading p { max-width: 680px; margin: 7px 0 0; color: var(--text-muted); font-size: 11px; }.schedule-facts { display: grid; grid-template-columns: repeat(4, 1fr); border-block: 1px solid var(--border); }.schedule-facts div { min-width: 0; padding: 13px 17px; border-right: 1px solid var(--border); }.schedule-facts div:last-child { border-right: 0; }.schedule-facts span,.schedule-facts strong { display: block; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }.schedule-facts span { color: var(--text-muted); font: 9px var(--font-mono); }.schedule-facts strong { margin-top: 5px; color: var(--text-strong); font-size: 11px; }.detail-actions-row { display: flex; flex-wrap: wrap; gap: 8px; padding: 17px 22px; }.danger-button { padding: 9px 14px; color: var(--danger); background: rgba(227,93,106,.07); border: 1px solid rgba(227,93,106,.2); border-radius: 9px; cursor: pointer; }.danger-button:disabled { opacity: .45; cursor: not-allowed; }.managed-note { margin: 0 22px 15px; padding: 10px 12px; color: var(--text-muted); background: var(--accent-subtle); border-left: 2px solid var(--accent); font-size: 10px; }.history-section { border-top: 1px solid var(--border); }.section-heading { display: flex; justify-content: space-between; align-items: center; padding: 18px 22px 11px; }.section-heading h3 { margin: 4px 0 0; color: var(--text-strong); font-size: 15px; }.section-heading>span { color: var(--text-muted); font: 10px var(--font-mono); }.history-list { display: grid; padding: 0 12px 14px; }.history-list article { display: grid; grid-template-columns: 76px minmax(0,1fr) auto; gap: 11px; align-items: center; min-height: 64px; padding: 9px 10px; border-top: 1px solid var(--border); }.history-list article div { min-width: 0; }.history-list strong,.history-list small { display: block; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }.history-list strong { color: var(--text-strong); font-size: 11px; }.history-list small,.history-list p,.history-list article>span:last-child { color: var(--text-muted); font-size: 9px; }.history-list p { margin: 4px 0 0; color: var(--danger); }.history-list a { color: var(--accent); font-size: 10px; white-space: nowrap; }
@media (max-width: 1050px) { .automation-layout { grid-template-columns: 1fr; }.automation-list-panel { max-height: 430px; overflow-y: auto; } }
@media (max-width: 700px) { .automation-form,.schedule-facts { grid-template-columns: 1fr; }.schedule-facts div { border-right: 0; border-bottom: 1px solid var(--border); }.history-list article { grid-template-columns: 70px minmax(0,1fr); }.history-list article a,.history-list article>span:last-child { grid-column: 2; } }
</style>
