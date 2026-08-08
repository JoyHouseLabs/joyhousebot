<template>
  <div class="page governance-page">
    <header class="page-heading">
      <div><span class="eyebrow">QUALITY GOVERNANCE</span><h1>评测与发布门禁</h1><p>用版本化数据集验证 Agent、Scenario 和 Capability；只有精确 Revision 的有效证据可以通过发布门禁。</p></div>
      <div class="heading-actions"><button class="secondary-button" @click="resetSuite">新建评测集</button><button class="secondary-button" @click="load">刷新</button></div>
    </header>
    <div v-if="error" class="notice error-notice">{{ error }}</div>

    <div class="governance-grid">
      <aside class="panel governance-list">
        <div class="panel-heading"><div><span class="eyebrow">DATASETS</span><h2>评测集</h2></div><strong>{{ suites.length }}</strong></div>
        <button v-for="suite in suites" :key="`${suite.suite_id}:${suite.version}`" @click="selectSuite(suite)">
          <div><strong>{{ suite.name }}</strong><span class="status-badge" :class="suite.status">{{ suite.status }}</span></div>
          <small>{{ suite.suite_id }} · v{{ suite.version }} · {{ suite.case_count }} cases</small>
        </button>
        <div v-if="!suites.length" class="empty-state compact">暂无版本化评测集</div>
      </aside>

      <main class="governance-main">
        <section class="panel governance-section">
          <div class="section-title"><div><span class="eyebrow">SUITE DEFINITION</span><h2>确定性评分数据集</h2></div><button class="primary-button" :disabled="saving" @click="saveSuite">保存不可变版本</button></div>
          <p class="section-note">Scorer 在服务端受限执行，不运行任意代码。支持状态、精确值、包含、JSON Schema/Path、时延与成本。</p>
          <textarea v-model="suiteJson" rows="16" spellcheck="false" />
        </section>

        <section class="panel governance-section">
          <div class="section-title"><div><span class="eyebrow">EVIDENCE RUN</span><h2>生成 Revision 证据</h2></div><span class="status-badge" :class="activeRun?.status || 'draft'">{{ activeRun?.status || 'new' }}</span></div>
          <div class="form-grid three">
            <label><span>目标类型</span><select v-model="runForm.target_type"><option value="agent">Agent</option><option value="scenario">Scenario</option><option value="capability">Capability</option></select></label>
            <label><span>目标 ID</span><input v-model="runForm.target_id" placeholder="main-coordinator" /></label>
            <label><span>Revision ID</span><input v-model="runForm.target_revision_id" placeholder="main-coordinator:v10" /></label>
            <label><span>评测集</span><input v-model="runForm.suite_id" placeholder="quality.basic" /></label>
            <label><span>版本</span><input v-model.number="runForm.suite_version" type="number" min="1" /></label>
            <button class="secondary-button align-end" :disabled="saving" @click="startRun">创建 Eval Run</button>
          </div>
          <div v-if="activeRun" class="observation-box">
            <div><strong>{{ activeRun.eval_run_id }}</strong><button class="primary-button" :disabled="saving" @click="finalizeRun">完成评分</button></div>
            <textarea v-model="observationJson" rows="8" spellcheck="false" />
            <button class="secondary-button" :disabled="saving" @click="recordObservation">记录 Case 观测</button>
          </div>
        </section>

        <section class="panel governance-section">
          <div class="section-title"><div><span class="eyebrow">RELEASE GATE</span><h2>绑定发布门禁</h2></div><button class="primary-button" :disabled="saving" @click="saveGate">保存门禁</button></div>
          <div class="form-grid three"><label><span>目标类型</span><select v-model="gateForm.target_type"><option value="agent">Agent</option><option value="scenario">Scenario</option><option value="capability">Capability</option></select></label><label><span>目标 ID</span><input v-model="gateForm.target_id" /></label><label><span>Revision ID</span><input v-model="gateForm.target_revision_id" /></label></div>
          <textarea v-model="gateJson" rows="7" spellcheck="false" />
        </section>
      </main>

      <aside class="panel evidence-list">
        <div class="panel-heading"><div><span class="eyebrow">EVIDENCE</span><h2>最近运行</h2></div><strong>{{ runs.length }}</strong></div>
        <button v-for="run in runs" :key="run.eval_run_id" @click="useRun(run)">
          <div><strong>{{ run.target_id }}</strong><span class="status-badge" :class="run.status">{{ run.status }}</span></div>
          <small>{{ run.target_type }} · {{ run.target_revision_id }}</small><p>{{ run.suite_id }}@{{ run.suite_version }} · pass {{ percent(run.metrics?.pass_rate) }}</p>
        </button>
        <div v-if="!runs.length" class="empty-state compact">尚无评测证据</div>
      </aside>
    </div>
  </div>
</template>

<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue'
import { useMessage } from 'naive-ui'
import { createEvalRun, finalizeEvalRun, listEvalRuns, listEvalSuites, recordEvalObservation, saveEvalSuite, saveReleaseGate, type EvalRun, type EvalSuite } from '../api/evals'

const toast = useMessage(); const suites = ref<EvalSuite[]>([]); const runs = ref<EvalRun[]>([]); const activeRun = ref<EvalRun | null>(null); const saving = ref(false); const error = ref('')
const runForm = reactive({ suite_id: 'quality.basic', suite_version: 1, target_type: 'agent', target_id: '', target_revision_id: '' })
const gateForm = reactive({ target_type: 'agent', target_id: '', target_revision_id: '' })
const suiteTemplate = { suite_id: 'quality.basic', version: 1, name: '核心质量与成本', description: '', status: 'active', target_types: ['agent', 'scenario', 'capability'], thresholds: { min_pass_rate: 1, min_average_score: 1 }, cases: [{ case_id: 'answer', name: '输出符合契约', input: { prompt: '示例目标' }, expected: null, min_score: 1, tags: ['regression'], scorers: [{ type: 'status', required: true, weight: 1, value: 'completed' }, { type: 'json_schema', required: true, weight: 1, schema: { type: 'object' } }] }] }
const suiteJson = ref(JSON.stringify(suiteTemplate, null, 2)); const observationJson = ref(JSON.stringify({ case_id: 'answer', output: {}, status: 'completed', latency_ms: 0, cost_usd: 0, metadata: {} }, null, 2)); const gateJson = ref(JSON.stringify({ required: true, requirements: [{ suite_id: 'quality.basic', suite_version: 1, min_pass_rate: 1, max_age_hours: 168 }] }, null, 2))
function parse(text: string) { const value = JSON.parse(text); if (!value || Array.isArray(value) || typeof value !== 'object') throw new Error('内容必须是 JSON 对象'); return value as Record<string, unknown> }
function percent(value?: number) { return value == null ? '—' : `${Math.round(Number(value) * 100)}%` }
function resetSuite() { suiteJson.value = JSON.stringify(suiteTemplate, null, 2) }
function selectSuite(value: EvalSuite) { suiteJson.value = JSON.stringify(value, null, 2); runForm.suite_id = value.suite_id; runForm.suite_version = value.version }
function useRun(value: EvalRun) { activeRun.value = value; Object.assign(runForm, { suite_id: value.suite_id, suite_version: value.suite_version, target_type: value.target_type, target_id: value.target_id, target_revision_id: value.target_revision_id }); Object.assign(gateForm, { target_type: value.target_type, target_id: value.target_id, target_revision_id: value.target_revision_id }); gateJson.value = JSON.stringify({ required: true, requirements: [{ suite_id: value.suite_id, suite_version: value.suite_version, min_pass_rate: 1, max_age_hours: 168 }] }, null, 2) }
async function load() { error.value = ''; try { [suites.value, runs.value] = await Promise.all([listEvalSuites(), listEvalRuns()]) } catch (cause) { error.value = cause instanceof Error ? cause.message : '读取评测治理数据失败' } }
async function act(task: () => Promise<void>) { saving.value = true; try { await task() } catch (cause) { toast.error(cause instanceof Error ? cause.message : '操作失败') } finally { saving.value = false } }
function saveSuite() { void act(async () => { await saveEvalSuite(parse(suiteJson.value)); toast.success('评测集版本已保存'); await load() }) }
function startRun() { void act(async () => { activeRun.value = await createEvalRun({ ...runForm, idempotency_key: crypto.randomUUID() }); toast.success('Eval Run 已创建'); await load() }) }
function recordObservation() { if (!activeRun.value) return; void act(async () => { await recordEvalObservation(activeRun.value!.eval_run_id, parse(observationJson.value)); toast.success('Case 观测已评分') }) }
function finalizeRun() { if (!activeRun.value) return; void act(async () => { activeRun.value = await finalizeEvalRun(activeRun.value!.eval_run_id); toast.success('评测证据已冻结'); await load() }) }
function saveGate() { void act(async () => { await saveReleaseGate(gateForm.target_type, gateForm.target_id, gateForm.target_revision_id, parse(gateJson.value)); toast.success('发布门禁已绑定') }) }
onMounted(load)
</script>

<style scoped>
.governance-grid{display:grid;grid-template-columns:240px minmax(520px,1fr) 260px;gap:14px;align-items:start}.governance-main{display:grid;gap:14px}.governance-list,.evidence-list{overflow:hidden}.governance-list>button,.evidence-list>button{display:grid;gap:5px;width:100%;padding:13px 15px;border:0;border-top:1px solid var(--border);color:var(--text);background:transparent;text-align:left;cursor:pointer}.governance-list>button:hover,.evidence-list>button:hover{background:var(--surface-hover)}.governance-list button div,.evidence-list button div,.section-title,.observation-box>div{display:flex;align-items:center;justify-content:space-between;gap:8px}.governance-list small,.evidence-list small,.evidence-list p{margin:0;color:var(--text-muted);font:9px/1.45 var(--font-mono)}.governance-section{padding:20px}.section-title h2{margin:5px 0 0;color:var(--text-strong);font-size:17px}.section-note{margin:0 0 12px;color:var(--text-muted);font-size:11px}.governance-section textarea,.governance-section input,.governance-section select{box-sizing:border-box;width:100%;padding:9px;color:var(--text);background:var(--input);border:1px solid var(--border);border-radius:8px}.governance-section textarea{margin-top:12px;font:10px/1.55 var(--font-mono);resize:vertical}.form-grid{display:grid;gap:10px}.form-grid.three{grid-template-columns:repeat(3,minmax(0,1fr))}.form-grid label{display:grid;gap:5px;color:var(--text-muted);font-size:10px}.align-end{align-self:end}.observation-box{display:grid;gap:8px;margin-top:16px;padding:13px;border:1px solid var(--border);border-radius:10px;background:var(--surface-raised)}.observation-box strong{overflow:hidden;color:var(--text-strong);font:10px var(--font-mono);text-overflow:ellipsis}@media(max-width:1180px){.governance-grid{grid-template-columns:220px 1fr}.evidence-list{grid-column:1/-1}}@media(max-width:760px){.governance-grid,.form-grid.three{grid-template-columns:1fr}}
</style>
