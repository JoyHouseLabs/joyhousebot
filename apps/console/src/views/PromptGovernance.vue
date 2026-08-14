<template>
  <div class="page prompt-governance-page">
    <header class="page-heading">
      <div><span class="eyebrow">PROMPT & EXPERIMENT GOVERNANCE</span><h1>Prompt 与灰度实验</h1><p>Prompt 是可评审、可发布、可冻结的指令资产；Experiment 用稳定分桶验证已发布 Agent Revision，不把业务场景写进 Runtime。</p></div>
      <div class="heading-actions"><button class="secondary-button" :disabled="busy" @click="load">刷新</button></div>
    </header>
    <div v-if="error" class="notice error-notice">{{ error }}</div>

    <div class="prompt-grid">
      <aside class="panel asset-list">
        <div class="panel-heading"><div><span class="eyebrow">PROMPT ASSETS</span><h2>Prompt</h2></div><strong>{{ prompts.length }}</strong></div>
        <button v-for="item in prompts" :key="item.prompt_id" @click="selectPrompt(item.prompt_id)"><strong>{{ item.name }}</strong><small>{{ item.prompt_id }} · {{ item.current ? `v${item.current.version}` : 'draft' }}</small></button>
        <button class="new-asset" @click="newPrompt">＋ 新建 Prompt</button>
      </aside>

      <main class="governance-main">
        <section class="panel editor-panel">
          <div class="section-title"><div><span class="eyebrow">IMMUTABLE REVISION</span><h2>指令版本</h2></div><div><button class="secondary-button" :disabled="busy" @click="validate">校验</button><button class="primary-button" :disabled="busy" @click="save">保存草稿</button><button class="primary-button" :disabled="busy || !form.prompt_id" @click="publish">发布</button></div></div>
          <p class="section-note">自动绑定只接受无 <code v-pre>{{variable}}</code> 的静态 Prompt；动态变量必须由 Skill、App 或 Eval 显式提供。发布后不可修改，Run 会冻结精确内容摘要。</p>
          <div class="form-grid two"><label><span>Prompt ID</span><input v-model.trim="form.prompt_id" placeholder="prompt.evidence-policy" /></label><label><span>版本</span><input v-model.number="form.version" type="number" min="1" /></label><label><span>名称</span><input v-model.trim="form.name" /></label><label><span>标签（逗号分隔）</span><input v-model.trim="form.tags" /></label><label class="wide"><span>说明</span><input v-model.trim="form.description" /></label><label class="wide"><span>System Instruction</span><textarea v-model="form.content" rows="10" /></label><label><span>输入 Schema</span><textarea v-model="form.input_schema" rows="6" spellcheck="false" /></label><label><span>输出契约</span><textarea v-model="form.output_contract" rows="6" spellcheck="false" /></label><label class="wide"><span>变更说明</span><input v-model.trim="form.change_note" /></label></div>
          <pre v-if="validation" class="validation-report">{{ pretty(validation) }}</pre>
        </section>

        <section class="panel editor-panel">
          <div class="section-title"><div><span class="eyebrow">AGENT REVISION BINDING</span><h2>绑定并随 Run 冻结</h2></div><button class="primary-button" :disabled="busy || !publishedRevisionId" @click="bind">绑定已发布 Prompt</button></div>
          <div class="form-grid three"><label><span>Prompt Revision</span><input v-model="publishedRevisionId" placeholder="prompt.evidence-policy:v1" /></label><label><span>Agent ID</span><input v-model="binding.target_id" placeholder="default" /></label><label><span>Agent Revision</span><input v-model="binding.target_revision_id" placeholder="default:v1" /></label></div>
          <p class="section-note">绑定目标必须是已发布 Agent Revision。以后新 Run 才会采用新绑定；历史 Run 不会被改变。</p>
        </section>

        <section class="panel editor-panel">
          <div class="section-title"><div><span class="eyebrow">ONLINE EXPERIMENT</span><h2>稳定分桶与自动护栏</h2></div><div><button class="secondary-button" :disabled="busy || !experiment.experiment_id" @click="saveExperimentDraft">保存实验草稿</button><button class="primary-button" :disabled="busy || !experiment.experiment_id" @click="start">启动灰度</button></div></div>
          <p class="section-note">相同用户稳定进入同一实验臂；只有已发布 Agent Revision 可以启动。护栏按终态 Run 的失败率、平均延迟和成本自动暂停实验。</p>
          <textarea v-model="experimentJson" rows="17" spellcheck="false" />
          <div class="experiment-list"><article v-for="item in experiments" :key="item.experiment_id"><div><strong>{{ item.name }}</strong><small>{{ item.experiment_id }} · {{ item.traffic_basis_points / 100 }}% traffic</small></div><span class="status-badge" :class="item.status">{{ item.status }}</span><nav><button @click="selectExperiment(item)">编辑</button><button v-if="item.status === 'running'" @click="pause(item.experiment_id)">暂停</button><button @click="summary(item.experiment_id)">结果</button></nav></article></div>
          <pre v-if="experimentResult" class="validation-report">{{ pretty(experimentResult) }}</pre>
        </section>
      </main>
    </div>
  </div>
</template>

<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue'
import { useMessage } from 'naive-ui'
import { bindPrompt, getPrompt, listPrompts, publishPrompt, savePrompt, validatePrompt, type PromptSummary } from '../api/prompts'
import { experimentSummary, listExperiments, saveExperiment, setExperimentStatus, startExperiment, type Experiment } from '../api/experiments'

const toast = useMessage(); const prompts = ref<PromptSummary[]>([]); const experiments = ref<Experiment[]>([]); const error = ref(''); const busy = ref(false); const validation = ref<Record<string, unknown> | null>(null); const experimentResult = ref<Record<string, unknown> | null>(null); const publishedRevisionId = ref('')
const form = reactive({ prompt_id: '', version: 1, name: '', description: '', content: '', tags: '', input_schema: '{\n  "type": "object",\n  "properties": {}\n}', output_contract: '{\n  "type": "object"\n}', change_note: '' })
const binding = reactive({ target_id: '', target_revision_id: '' })
const template = { experiment_id: 'experiment.agent-policy-v2', name: 'Agent policy V2', description: '稳定比较两个已发布 Agent Revision。', target_type: 'agent', traffic_basis_points: 1000, variants: [{ variant_id: 'control', target_id: 'default', target_revision_id: 'default:v1', weight_basis_points: 5000 }, { variant_id: 'candidate', target_id: 'default', target_revision_id: 'default:v2', weight_basis_points: 5000 }], guardrails: { min_assigned: 20, max_failure_rate: 0.1, max_avg_latency_ms: 30000, max_avg_cost_usd: 0.1 } }
const experimentJson = ref(JSON.stringify(template, null, 2))
const experiment = ref<Record<string, unknown>>(template)
function pretty(value: unknown) { return JSON.stringify(value, null, 2) }
function parse(value: string) { const result = JSON.parse(value); if (!result || Array.isArray(result) || typeof result !== 'object') throw new Error('内容必须是 JSON 对象'); return result as Record<string, unknown> }
async function act(task: () => Promise<void>) { busy.value = true; error.value = ''; try { await task() } catch (cause) { const message = cause instanceof Error ? cause.message : '操作失败'; error.value = message; toast.error(message) } finally { busy.value = false } }
async function load() { await act(async () => { [prompts.value, experiments.value] = await Promise.all([listPrompts(), listExperiments()]) }) }
function newPrompt() { Object.assign(form, { prompt_id: '', version: 1, name: '', description: '', content: '', tags: '', input_schema: '{\n  "type": "object",\n  "properties": {}\n}', output_contract: '{\n  "type": "object"\n}', change_note: '' }); validation.value = null; publishedRevisionId.value = '' }
async function selectPrompt(promptId: string) { await act(async () => { const item = await getPrompt(promptId); const revision = item.revisions[0]; if (!revision) return; Object.assign(form, { prompt_id: revision.prompt_id, version: revision.version, name: revision.name, description: revision.description, content: revision.content, tags: revision.tags.join(', '), input_schema: pretty(revision.input_schema), output_contract: pretty(revision.output_contract), change_note: revision.change_note }); validation.value = revision.validation_report; publishedRevisionId.value = revision.status === 'published' ? revision.revision_id : item.current_revision_id || '' }) }
function payload() { return { ...form, tags: form.tags.split(',').map((item) => item.trim()).filter(Boolean), input_schema: parse(form.input_schema), output_contract: parse(form.output_contract) } }
function save() { void act(async () => { const saved = await savePrompt(payload()); publishedRevisionId.value = saved.status === 'published' ? saved.revision_id : publishedRevisionId.value; toast.success('Prompt 草稿已保存'); await load() }) }
function validate() { void act(async () => { validation.value = await validatePrompt(form.prompt_id, form.version); toast.success('结构校验已记录') }) }
function publish() { void act(async () => { const released = await publishPrompt(form.prompt_id, form.version); publishedRevisionId.value = released.revision_id; toast.success('Prompt 已发布；后续 Run 才会采用该版本'); await load() }) }
function bind() { void act(async () => { await bindPrompt({ ...binding, prompt_revision_id: publishedRevisionId.value }); toast.success('已绑定到 Agent Revision') }) }
function saveExperimentDraft() { void act(async () => { experiment.value = parse(experimentJson.value); await saveExperiment(experiment.value); toast.success('实验草稿已保存'); await load() }) }
function start() { void act(async () => { const value = parse(experimentJson.value); await saveExperiment(value); await startExperiment(String(value.experiment_id)); toast.success('灰度已启动'); await load() }) }
function selectExperiment(value: Experiment) { experiment.value = value as unknown as Record<string, unknown>; experimentJson.value = pretty(value); experimentResult.value = null }
function pause(experimentId: string) { void act(async () => { await setExperimentStatus(experimentId, 'paused'); toast.success('实验已暂停'); await load() }) }
function summary(experimentId: string) { void act(async () => { experimentResult.value = await experimentSummary(experimentId) }) }
onMounted(load)
</script>

<style scoped>
.prompt-grid{display:grid;grid-template-columns:260px minmax(0,1fr);gap:14px;align-items:start}.asset-list{overflow:hidden}.asset-list>button{display:grid;gap:5px;width:100%;padding:13px 15px;border:0;border-top:1px solid var(--border);color:var(--text);background:transparent;text-align:left;cursor:pointer}.asset-list>button:hover{background:var(--surface-hover)}.asset-list small,.experiment-list small{color:var(--text-muted);font:9px var(--font-mono)}.asset-list .new-asset{color:var(--accent);font-weight:700}.governance-main{display:grid;gap:14px}.editor-panel{padding:20px}.section-title{display:flex;align-items:flex-start;justify-content:space-between;gap:12px}.section-title h2{margin:5px 0 0;color:var(--text-strong);font-size:17px}.section-title>div:last-child{display:flex;gap:7px;flex-wrap:wrap}.section-note{margin:10px 0 14px;color:var(--text-muted);font-size:11px}.form-grid{display:grid;gap:10px}.form-grid.two{grid-template-columns:repeat(2,minmax(0,1fr))}.form-grid.three{grid-template-columns:repeat(3,minmax(0,1fr))}.form-grid label{display:grid;gap:5px;color:var(--text-muted);font-size:10px}.form-grid .wide{grid-column:1/-1}.form-grid input,.form-grid textarea,.editor-panel>textarea{box-sizing:border-box;width:100%;padding:9px;color:var(--text);background:var(--input);border:1px solid var(--border);border-radius:8px}.form-grid textarea,.editor-panel>textarea{font:10px/1.55 var(--font-mono);resize:vertical}.validation-report{overflow:auto;max-height:280px;margin:14px 0 0;padding:12px;border:1px solid var(--border);border-radius:9px;background:var(--surface-raised);font:10px/1.5 var(--font-mono)}.experiment-list{display:grid;margin-top:14px}.experiment-list article{display:grid;grid-template-columns:1fr auto auto;align-items:center;gap:12px;padding:11px 0;border-top:1px solid var(--border)}.experiment-list article>div{display:grid;gap:3px}.experiment-list nav{display:flex;gap:6px}.experiment-list button{padding:5px 8px;border:1px solid var(--border);border-radius:6px;color:var(--text);background:var(--surface-raised);cursor:pointer}@media(max-width:760px){.prompt-grid,.form-grid.two,.form-grid.three{grid-template-columns:1fr}.experiment-list article{grid-template-columns:1fr auto}.experiment-list nav{grid-column:1/-1}}
</style>
