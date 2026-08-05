<template>
  <div class="page studio-page">
    <header class="page-heading">
      <div><span class="eyebrow">SCENARIO STUDIO</span><h1>场景与追问编排</h1><p>把可重复的业务请求配置为协调 Agent 可识别、可追问、可执行和可回放的场景版本。</p></div>
      <div class="heading-actions"><button class="secondary-button" @click="createDraft">新建场景</button><button class="secondary-button" @click="load">刷新</button></div>
    </header>
    <div v-if="error" class="notice error-notice">{{ error }}</div>

    <div class="studio-shell">
      <aside class="panel scenario-list">
        <div class="panel-heading"><div><span class="eyebrow">VERSIONS</span><h2>场景版本</h2></div></div>
        <button v-for="item in scenarios" :key="`${item.scenario_id}:${item.version}`" :class="{ active: selectedKey === `${item.scenario_id}:${item.version}` }" @click="select(item)">
          <div><strong>{{ item.name }}</strong><span class="status-badge" :class="item.status">{{ item.status }}</span></div>
          <small>{{ item.scenario_id }} · v{{ item.version }}</small>
        </button>
        <div v-if="!scenarios.length" class="empty-state compact">暂无场景版本</div>
      </aside>

      <main v-if="draft" class="studio-editor">
        <section class="panel flow-explainer"><span class="eyebrow">HOW COORDINATION WORKS</span><h2>协调与执行流程</h2><p>先用确定性路由规则判断请求是否属于场景；命中后收集字段、发起必要追问，再把任务限制在选定能力边界内执行。未命中时请求交给通用协调 Agent。</p><div class="flow-steps"><span>1 请求</span><i>→</i><span>2 路由规则</span><i>→</i><span>3 字段/追问</span><i>→</i><span>4 能力边界</span><i>→</i><span>5 规划与 Run</span></div><small>场景发布后不可原地修改：复制为新版本、模拟验证，再发布；历史 Run 始终保留当时使用的版本。</small></section>
        <section class="panel studio-section">
          <div class="section-title"><div><span class="eyebrow">IDENTITY</span><h2>基本信息</h2></div><span class="status-badge" :class="draft.status">{{ draft.status }}</span></div>
          <div class="form-grid">
            <label><span>场景 ID</span><input v-model="draft.scenario_id" :disabled="persisted" /></label>
            <label><span>版本</span><input v-model.number="draft.version" type="number" min="1" :disabled="persisted" /></label>
            <label class="wide"><span>名称</span><input v-model="draft.name" /></label>
            <label class="wide"><span>说明</span><textarea v-model="draft.description" rows="2" /></label>
            <label class="wide"><span>路由关键词（逗号分隔）</span><input v-model="routingKeywords" placeholder="语音, 朗读, TTS" /></label>
          </div>
        </section>

        <section class="panel studio-section">
          <div class="section-title"><div><span class="eyebrow">INTERACTIVE INPUT DAG</span><h2>字段、交互与追问</h2><p>字段定义提交值，交互方式决定用户看到的控件。回答会在同一个 Run 中持久化，随后按条件边进入下一题或开始执行。</p></div><button class="secondary-button" @click="addField">添加字段</button></div>
          <article v-for="(field, index) in editableFields" :key="index" class="field-card">
            <div class="field-row"><input v-model="field.name" placeholder="字段名" /><select v-model="field.input_mode" @change="syncInputMode(field)"><option v-for="type in inputModes" :key="type.value" :value="type.value">{{ type.label }}</option></select><label class="check"><input v-model="field.required" type="checkbox" />必填</label><label class="check"><input v-model="field.sensitive" type="checkbox" />敏感</label><button @click="removeField(index)">删除</button></div>
            <input v-model="field.description" placeholder="字段说明" />
            <input v-model="field.question" :disabled="!field.required" placeholder="缺失时向用户追问的问题" />
            <template v-if="['single_choice', 'multi_choice'].includes(field.input_mode)"><textarea v-model="field.optionsText" rows="3" placeholder="选项，每行：value | 展示名称 | 可选说明" /><div class="choice-settings"><label class="check"><input v-model="field.allow_other" type="checkbox" />允许 Other 自由填写</label><label v-if="field.input_mode === 'multi_choice'">最少选择<input v-model.number="field.min_selections" type="number" min="0" /></label><label v-if="field.input_mode === 'multi_choice'">最多选择<input v-model.number="field.max_selections" type="number" min="1" /></label></div></template>
          </article>
          <div v-if="!editableFields.length" class="empty-state compact">没有必填参数时，Run 会直接进入规划。</div>
          <details class="dag-editor"><summary>高级：条件分支（DAG 边）</summary><p>节点 ID 为 <code>ask_字段名</code>，终点为 <code>ready</code>。条件支持 <code>role == 'recruiter'</code>、<code>present(city)</code>、<code>city in ['beijing', 'shanghai']</code>；按 priority 从高到低选择首条命中边。</p><textarea v-model="edgesJson" rows="8" spellcheck="false" placeholder="输入 Edge JSON 数组，例如从 ask_goal 到 ask_city 的条件边" /></details>
        </section>

        <section class="panel studio-section">
          <div class="section-title"><div><span class="eyebrow">CAPABILITIES</span><h2>能力边界</h2><p>这里只选择该场景允许调用的 Tool、Skill、Workflow 或子 Agent。动态规划由协调 Agent 在边界内选用能力；固定 DAG 则按下方任务图严格执行。</p></div><select v-model="draft.planning_mode"><option value="dynamic">动态规划</option><option value="fixed">固定 DAG</option></select></div>
          <div class="capability-grid"><label v-for="item in capabilities" :key="item.ref.capability_id"><input v-model="draft.allowed_capabilities" type="checkbox" :value="item.ref.capability_id" /><span><strong>{{ item.name }}</strong><small>{{ item.ref.kind }} · {{ item.ref.capability_id }}</small></span></label></div>
          <label v-if="draft.planning_mode === 'fixed'" class="json-field"><span>固定 DAG tasks（JSON 数组）</span><textarea v-model="tasksJson" rows="9" spellcheck="false" /></label>
        </section>

        <section class="panel studio-section">
          <div class="section-title"><div><span class="eyebrow">RESULT AGGREGATION</span><h2>多 Agent 结果合并</h2><p>策略会随场景版本冻结并写入每个 Run。确定性策略无需模型，可回放；LLM 综合只读取带任务 ID 的证据，并保留输入清单。</p></div></div>
          <div class="form-grid aggregation-grid">
            <label class="wide"><span>合并策略</span><select v-model="aggregation.mode"><option value="llm_synthesis">LLM 综合回答（默认）</option><option value="structured_merge">结构化合并（JSON 去重与冲突审计）</option><option value="evidence_merge">证据汇编（保留每个 Task 来源）</option><option value="rank_and_select">评分排序并选择</option><option value="raw">原始结果（不做转换）</option></select></label>
            <label v-if="aggregation.mode === 'structured_merge'"><span>冲突处理</span><select v-model="aggregation.conflict_resolution"><option value="prefer_first">保留最先完成来源</option><option value="prefer_last">后来的来源覆盖</option></select></label>
            <label v-if="aggregation.mode === 'rank_and_select'"><span>评分字段路径</span><input v-model.trim="aggregation.score_path" placeholder="score 或 metrics.score" /></label>
            <label><span>最多纳入结果数</span><input v-model.number="aggregation.max_items" type="number" min="1" max="1000" /></label>
            <label v-if="aggregation.mode === 'llm_synthesis'" class="wide"><span>综合指令（可选）</span><textarea v-model="aggregation.instructions" rows="3" placeholder="例如：按候选人评分排序；每条结论注明 task_id；发现冲突时明确说明。" /></label>
          </div>
          <small class="aggregation-note">结构化合并仅合并 Tool 返回的 data 或 Agent 输出的 JSON；普通文本会被标记为未结构化输入，不会静默伪造字段。</small>
        </section>

        <section class="panel studio-section simulation">
          <div class="section-title"><div><span class="eyebrow">SIMULATOR</span><h2>路由与追问预演</h2><p>不会创建 Run、不会调用模型或 Tool。它用原始请求和模拟字段值验证路由命中、默认值、缺失字段及下一步。</p></div><button class="secondary-button" @click="simulate">运行模拟</button></div>
          <label class="simulation-field"><span>模拟用户请求</span><textarea v-model="simulationPrompt" rows="3" placeholder="例如：在 Dinq 中搜索机器学习研究人员…" /></label>
          <label class="simulation-field"><span>模拟已收集字段（JSON）</span><textarea v-model="simulationInputsText" rows="6" spellcheck="false" placeholder='例如：{ "query": "机器学习", "limit": 5 }' /></label>
          <button class="text-button" type="button" @click="fillExampleInputs">按当前字段填充示例数据</button>
          <div v-if="simulationResult" class="simulation-outcome"><strong>线上实际选择：{{ simulationResult.live_route?.name || '未命中场景，交由通用协调 Agent' }}</strong><p>原因：{{ simulationResult.live_route?.reason_code || 'OPEN_AGENT_DEFAULT' }}</p><p :class="simulationResult.target_scenario?.matched ? 'match' : 'no-match'">当前编辑场景：{{ simulationResult.target_scenario?.matched ? '规则命中' : '规则未命中' }}</p><details><summary>查看路由、追问与规则明细</summary><pre>{{ pretty(simulationResult) }}</pre></details></div>
        </section>

        <footer class="studio-actions"><button v-if="draft.status === 'published'" class="secondary-button" @click="cloneVersion">复制为新版本</button><button v-else class="secondary-button" :disabled="saving" @click="save">保存草稿</button><button v-if="draft.status === 'draft' && persisted" class="primary-button" :disabled="saving" @click="publish">发布版本</button></footer>
      </main>
      <div v-else class="panel empty-state"><span>◇</span><strong>选择或创建场景</strong><p>配置识别规则、追问字段和可调用能力。</p></div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRoute } from 'vue-router'
import { useMessage } from 'naive-ui'
import { listScenarioCapabilities, listScenarios, publishScenario, saveScenario, simulateScenario, type CapabilityDefinition, type ScenarioNode, type ScenarioVersion } from '../api/scenarios'

type EditableField = ScenarioVersion['fields'][number] & { question: string; optionsText: string }
const toast = useMessage(); const route = useRoute(); const scenarios = ref<ScenarioVersion[]>([]); const capabilities = ref<CapabilityDefinition[]>([]); const draft = ref<ScenarioVersion | null>(null); const editableFields = ref<EditableField[]>([])
const selectedKey = ref(''); const routingKeywords = ref(''); const tasksJson = ref('[]'); const edgesJson = ref('[]'); const simulationPrompt = ref(''); const simulationInputsText = ref('{}'); const simulationResult = ref<Record<string, any> | null>(null); const error = ref(''); const saving = ref(false)
const inputModes = [
  { value: 'text', label: '单行文本' }, { value: 'textarea', label: '多行文本' },
  { value: 'single_choice', label: '单选卡片' }, { value: 'multi_choice', label: '多选卡片' },
  { value: 'boolean', label: '确认开关' }, { value: 'number', label: '数字' },
] as const
const aggregation = ref({ mode: 'llm_synthesis', version: 'v1', conflict_resolution: 'prefer_first', score_path: 'score', max_items: 20, instructions: '' })
const persisted = computed(() => scenarios.value.some((item) => item.scenario_id === draft.value?.scenario_id && item.version === draft.value?.version))

function blank(): ScenarioVersion { return { scenario_id: '', version: 1, name: '', description: '', fields: [], nodes: [], edges: [], allowed_capabilities: [], planning_mode: 'dynamic', execution_policy: { aggregation_policy: { ...aggregation.value } }, routing_rules: [], status: 'draft' } }
// Scenario API values are JSON documents. JSON cloning intentionally strips
// Vue proxies before the draft becomes reactive again; structuredClone rejects
// proxies in some browser implementations.
function cloneScenario(value: ScenarioVersion): ScenarioVersion { return JSON.parse(JSON.stringify(value)) as ScenarioVersion }
function createDraft() { aggregation.value = { mode: 'llm_synthesis', version: 'v1', conflict_resolution: 'prefer_first', score_path: 'score', max_items: 20, instructions: '' }; draft.value = blank(); editableFields.value = []; selectedKey.value = ''; routingKeywords.value = ''; tasksJson.value = '[]'; edgesJson.value = '[]'; simulationInputsText.value = '{}'; simulationResult.value = null }
function optionText(field: ScenarioVersion['fields'][number]) { const options: Array<{ value: string; label: string; description?: string }> = field.options?.length ? field.options : field.enum.map((value) => ({ value: String(value), label: String(value) })); return options.map((item) => [item.value, item.label, item.description || ''].filter((value, index) => value || index < 2).join(' | ')).join('\n') }
function select(item: ScenarioVersion) { draft.value = cloneScenario(item); const savedPolicy = item.execution_policy.aggregation_policy as Record<string, unknown> | undefined; aggregation.value = { mode: String(savedPolicy?.mode || (item.execution_policy.aggregate === false ? 'raw' : 'llm_synthesis')), version: String(savedPolicy?.version || 'v1'), conflict_resolution: String(savedPolicy?.conflict_resolution || 'prefer_first'), score_path: String(savedPolicy?.score_path || 'score'), max_items: Number(savedPolicy?.max_items || 20), instructions: String(savedPolicy?.instructions || '') }; selectedKey.value = `${item.scenario_id}:${item.version}`; routingKeywords.value = String((item.routing_rules[0]?.contains_any as string[] | undefined)?.join(', ') || ''); tasksJson.value = JSON.stringify(item.execution_policy.tasks ?? [], null, 2); edgesJson.value = JSON.stringify(item.edges ?? [], null, 2); simulationInputsText.value = '{}'; editableFields.value = item.fields.map((field) => ({ ...field, input_mode: field.input_mode || (field.value_type === 'array' ? 'multi_choice' : field.enum.length ? 'single_choice' : 'text'), optionsText: optionText(field), question: item.nodes.find((node) => node.field_names.includes(field.name))?.question || '' })); simulationResult.value = null }
function addField() { editableFields.value.push({ name: '', value_type: 'string', required: true, description: '', enum: [], input_mode: 'text', options: [], allow_other: false, min_selections: null, max_selections: null, validation: {}, sensitive: false, question: '', optionsText: '' }) }
function removeField(index: number) { editableFields.value.splice(index, 1) }
function syncInputMode(field: EditableField) { if (field.input_mode === 'multi_choice') field.value_type = 'array'; else if (field.input_mode === 'single_choice') field.value_type = 'string'; else if (field.input_mode === 'boolean') field.value_type = 'boolean'; else if (field.input_mode === 'number') field.value_type = 'number'; else field.value_type = 'string' }
function parseOptions(text: string) { const values = new Set<string>(); return text.split('\n').map((line) => line.trim()).filter(Boolean).map((line) => { const [value = '', label = '', description = ''] = line.split('|').map((part) => part.trim()); if (!value || !label || values.has(value)) throw new Error('每个选项必须有唯一 value 和展示名称'); values.add(value); return { value, label, ...(description ? { description } : {}) } }) }
function materialize(): ScenarioVersion { if (!draft.value) throw new Error('没有场景'); const tasks = JSON.parse(tasksJson.value || '[]'); const configuredEdges = JSON.parse(edgesJson.value || '[]'); if (!Array.isArray(tasks)) throw new Error('固定 DAG tasks 必须是 JSON 数组'); if (!Array.isArray(configuredEdges)) throw new Error('条件边必须是 JSON 数组'); const fields = editableFields.value.map(({ question: _q, optionsText, ...field }) => { const options = ['single_choice', 'multi_choice'].includes(field.input_mode) ? parseOptions(optionsText) : []; return { ...field, options, enum: options.map((item) => item.value), min_selections: field.input_mode === 'multi_choice' ? Number(field.min_selections || 0) : null, max_selections: field.input_mode === 'multi_choice' && field.max_selections ? Number(field.max_selections) : null } }); const required = editableFields.value.filter((field) => field.required); const nodes: ScenarioNode[] = required.map((field, index) => ({ node_id: `ask_${field.name || index + 1}`, kind: 'question', question: field.question, field_names: [field.name], configuration: {} })); if (nodes.length) nodes.push({ node_id: 'ready', kind: 'terminal', question: '', field_names: [], configuration: {} }); const edges = configuredEdges.length ? configuredEdges : nodes.slice(0, -1).map((node, index) => ({ source_node_id: node.node_id, target_node_id: nodes[index + 1].node_id, condition: 'true', priority: 100 })); const keywords = routingKeywords.value.split(',').map((item) => item.trim()).filter(Boolean); return { ...draft.value, fields, nodes, edges, routing_rules: keywords.length ? [{ contains_any: keywords }] : [], execution_policy: { ...draft.value.execution_policy, tasks, aggregate: aggregation.value.mode !== 'raw', aggregation_policy: { ...aggregation.value, max_items: Math.max(1, Math.min(1000, Number(aggregation.value.max_items) || 20)) } } } }
async function load() { error.value = ''; try { [scenarios.value, capabilities.value] = await Promise.all([listScenarios(), listScenarioCapabilities()]); if (selectedKey.value) { const current = scenarios.value.find((item) => `${item.scenario_id}:${item.version}` === selectedKey.value); if (current) select(current) } } catch (cause) { error.value = cause instanceof Error ? cause.message : '读取失败' } }
async function save() { saving.value = true; try { const saved = await saveScenario(materialize()); toast.success('草稿已保存'); await load(); select(saved) } catch (cause) { toast.error(cause instanceof Error ? cause.message : '保存失败') } finally { saving.value = false } }
async function publish() { if (!draft.value) return; saving.value = true; try { const item = await publishScenario(draft.value.scenario_id, draft.value.version); toast.success('版本已发布'); await load(); select(item) } catch (cause) { toast.error(cause instanceof Error ? cause.message : '发布失败') } finally { saving.value = false } }
function cloneVersion() { if (!draft.value) return; const copy = cloneScenario(draft.value); copy.version = Math.max(...scenarios.value.filter((item) => item.scenario_id === copy.scenario_id).map((item) => item.version), copy.version) + 1; copy.status = 'draft'; copy.published_at = null; draft.value = copy; selectedKey.value = '' }
function pretty(value: unknown) { return JSON.stringify(value, null, 2) }
function fillExampleInputs() { const value: Record<string, unknown> = {}; for (const field of editableFields.value) { if (field.default !== undefined && field.default !== null) value[field.name] = field.default; else if (field.enum.length) value[field.name] = field.enum[0]; else if (field.value_type === 'integer' || field.value_type === 'number') value[field.name] = 1; else if (field.value_type === 'boolean') value[field.name] = true; else if (field.value_type === 'array') value[field.name] = []; else if (field.value_type === 'object') value[field.name] = {}; else value[field.name] = `示例${field.name}` } simulationInputsText.value = pretty(value) }
async function simulate() { if (!draft.value || !simulationPrompt.value.trim()) return; let inputs: Record<string, unknown>; try { const parsed = JSON.parse(simulationInputsText.value || '{}'); if (!parsed || Array.isArray(parsed) || typeof parsed !== 'object') throw new Error('模拟数据必须是 JSON 对象'); inputs = parsed as Record<string, unknown> } catch (cause) { toast.error(cause instanceof Error ? cause.message : '模拟数据 JSON 无效'); return } try { if (!persisted.value) await save(); simulationResult.value = await simulateScenario(draft.value.scenario_id, draft.value.version, simulationPrompt.value, inputs) } catch (cause) { toast.error(cause instanceof Error ? cause.message : '模拟失败') } }
onMounted(async () => { await load(); const scenarioId = String(route.query.scenario || ''); const version = Number(route.query.version || 0); const target = scenarios.value.find((item) => item.scenario_id === scenarioId && (!version || item.version === version)); if (target) select(target) })
</script>

<style scoped>
.studio-shell{display:grid;grid-template-columns:260px minmax(0,1fr);gap:16px}.scenario-list{align-self:start;overflow:hidden}.scenario-list>button{width:100%;padding:13px 16px;border:0;border-top:1px solid var(--border);color:var(--text);background:transparent;text-align:left;cursor:pointer}.scenario-list>button.active,.scenario-list>button:hover{background:var(--surface-hover)}.scenario-list>button div{display:flex;justify-content:space-between;gap:8px}.scenario-list strong{color:var(--text-strong)}.scenario-list small{color:var(--text-muted);font:10px var(--font-mono)}.studio-editor{display:flex;min-width:0;flex-direction:column;gap:14px}.studio-section{padding:20px}.section-title{display:flex;align-items:center;justify-content:space-between;margin-bottom:16px}.section-title h2{margin:5px 0 0;color:var(--text-strong);font-size:17px}.form-grid{display:grid;grid-template-columns:1fr 140px;gap:12px}.form-grid .wide{grid-column:1/-1}.form-grid label,.json-field{display:flex;flex-direction:column;gap:5px;color:var(--text-muted);font-size:10px}.studio-section input,.studio-section textarea,.studio-section select{width:100%;padding:9px 10px;outline:none;color:var(--text);background:var(--input);border:1px solid var(--border);border-radius:8px}.field-card{display:grid;gap:8px;margin-top:10px;padding:12px;background:var(--surface-raised);border:1px solid var(--border);border-radius:10px}.field-row{display:grid;grid-template-columns:1fr 160px auto auto auto;align-items:center;gap:8px}.field-row .check,.choice-settings .check{display:flex;align-items:center;gap:5px;white-space:nowrap}.field-row .check input,.choice-settings .check input{width:auto}.field-row button{border:0;color:var(--danger);background:transparent;cursor:pointer}.choice-settings{display:flex;align-items:center;gap:12px;flex-wrap:wrap;color:var(--text-muted);font-size:10px}.choice-settings label:not(.check){display:flex;align-items:center;gap:5px}.choice-settings input[type=number]{width:72px;padding:6px}.dag-editor{display:grid;gap:8px;margin-top:16px;padding:12px;border:1px dashed var(--border);border-radius:9px;color:var(--text-muted);font-size:11px}.dag-editor summary{color:var(--text-strong);cursor:pointer}.dag-editor p{margin:0;line-height:1.65}.dag-editor code{color:var(--accent)}.capability-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:8px}.capability-grid label{display:flex;gap:9px;padding:10px;background:var(--surface-raised);border:1px solid var(--border);border-radius:9px}.capability-grid input{width:auto}.capability-grid span{display:flex;min-width:0;flex-direction:column}.capability-grid strong{color:var(--text-strong);font-size:11px}.capability-grid small{overflow:hidden;color:var(--text-muted);font:9px var(--font-mono);text-overflow:ellipsis}.json-field{margin-top:16px}.aggregation-note{display:block;margin-top:12px;color:var(--text-muted);font-size:10px;line-height:1.6}.simulation pre{max-height:280px;overflow:auto;padding:12px;background:var(--input);border-radius:9px;font-size:10px}.simulation-outcome{display:grid;gap:6px;margin-top:12px;padding:12px;background:var(--surface-raised);border:1px solid var(--border);border-radius:9px;font-size:12px}.simulation-outcome strong{color:var(--text-strong)}.simulation-outcome p{margin:0;color:var(--text-muted)}.simulation-outcome .match{color:var(--success)}.simulation-outcome .no-match{color:var(--warning)}.simulation-outcome details{color:var(--text-muted);font-size:11px}.studio-actions{position:sticky;bottom:0;display:flex;justify-content:flex-end;gap:9px;padding:12px;background:color-mix(in srgb,var(--bg) 90%,transparent);backdrop-filter:blur(12px)}@media(max-width:900px){.studio-shell{grid-template-columns:1fr}.scenario-list{max-height:260px;overflow:auto}.capability-grid{grid-template-columns:1fr}}@media(max-width:600px){.form-grid,.field-row{grid-template-columns:1fr}.form-grid .wide{grid-column:auto}}
</style>

<style scoped>
.flow-explainer{padding:20px}.flow-explainer h2{margin:6px 0 8px;color:var(--text-strong);font-size:17px}.flow-explainer p,.flow-explainer small,.section-title p{margin:0;color:var(--text-muted);font-size:11px;line-height:1.7}.flow-steps{display:flex;align-items:center;gap:9px;flex-wrap:wrap;margin:16px 0 10px}.flow-steps span{padding:7px 9px;color:var(--text-strong);background:var(--surface-raised);border:1px solid var(--border);border-radius:7px;font-size:10px}.flow-steps i{color:var(--accent);font-style:normal}.section-title>div{display:grid;gap:4px}.simulation-field{display:grid;gap:6px;margin-top:12px;color:var(--text-muted);font-size:10px}.simulation-field textarea{font-family:var(--font-mono);line-height:1.55}.text-button{margin-top:8px;padding:0;color:var(--accent);border:0;background:transparent;font-size:11px;text-align:left;cursor:pointer}.text-button:hover{text-decoration:underline}@media(max-width:600px){.flow-steps{gap:5px}.flow-steps i{display:none}}
</style>
