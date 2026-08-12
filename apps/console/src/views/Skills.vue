<template>
  <div class="page skills-page">
    <header class="page-heading">
      <div>
        <span class="eyebrow">SKILL CONTROL PLANE</span>
        <h1>Skill 管理</h1>
        <p>创建方法资产、冻结不可变版本、声明依赖、执行发布校验，并按精确摘要绑定到 Agent 与 Workflow。</p>
      </div>
      <div class="heading-actions">
        <button class="secondary-button" :disabled="loading" @click="load">{{ loading ? '刷新中…' : '刷新' }}</button>
        <button class="primary-button" @click="createSkill">新建 Skill</button>
      </div>
    </header>

    <div v-if="error" class="notice error-notice">{{ error }}</div>

    <section class="contract panel">
      <article><span>01</span><div><strong>Skill 是方法资产</strong><p>内容、Schema、示例、依赖与 Eval 一起形成版本；它不是可执行 Tool。</p></div></article>
      <article><span>02</span><div><strong>引用必须精确</strong><p>Agent 与 Workflow 冻结 skill_id、version 和 content_sha256，更新不会静默漂移。</p></div></article>
      <article><span>03</span><div><strong>动作仍受能力治理</strong><p>Skill 只指导如何做；联网、代码与业务写入必须经过 Capability 和 Integration。</p></div></article>
    </section>

    <div class="workspace">
      <aside class="panel directory">
        <div class="directory-head">
          <div><span class="eyebrow">SKILL ASSETS</span><strong>{{ skills.length }}</strong></div>
          <input v-model.trim="search" type="search" placeholder="搜索名称、ID 或标签" />
        </div>
        <button
          v-for="item in filteredSkills"
          :key="item.skill_id"
          :class="{ active: selectedId === item.skill_id }"
          @click="selectSkill(item.skill_id)"
        >
          <span class="skill-icon">S</span>
          <div><strong>{{ item.name }}</strong><small>{{ item.skill_id }} · {{ item.current_version ? `v${item.current_version}` : '未发布' }}</small></div>
          <i :class="item.status" :title="statusLabel(item.status)" />
        </button>
        <div v-if="!filteredSkills.length" class="empty-state compact"><span>◇</span><strong>暂无 Skill</strong><p>创建第一个可版本化的方法资产。</p></div>
      </aside>

      <main class="panel editor">
        <template v-if="editing">
          <header class="editor-head">
            <div>
              <span class="eyebrow">{{ isNewAsset ? 'NEW METHOD ASSET' : 'VERSIONED METHOD ASSET' }}</span>
              <h2>{{ form.name || '未命名 Skill' }}</h2>
              <p><code>{{ form.skill_id || 'skill.*' }}</code><span v-if="form.version"> · v{{ form.version }}</span></p>
            </div>
            <div class="editor-actions">
              <span class="state-chip" :class="selectedVersion?.status || 'draft'">{{ versionStatusLabel(selectedVersion?.status || 'draft') }}</span>
              <button v-if="detail && detail.status !== 'disabled'" class="secondary-button danger" @click="changeStatus('disabled')">停用</button>
              <button v-else-if="detail?.status === 'disabled'" class="secondary-button" @click="changeStatus('active')">重新启用</button>
              <button class="primary-button" :disabled="saving || readOnly" @click="save">{{ saving ? '保存中…' : '保存 Draft' }}</button>
            </div>
          </header>

          <div v-if="detail" class="version-bar">
            <label><span>查看版本</span><select :value="selectedVersion?.version" @change="chooseVersion(($event.target as HTMLSelectElement).value)"><option v-for="item in detail.versions" :key="item.version" :value="item.version">v{{ item.version }} · {{ versionStatusLabel(item.status) }}</option></select></label>
            <label><span>新版本号</span><input v-model.trim="nextVersion" placeholder="例如 1.1.0" /></label>
            <button class="secondary-button" :disabled="!nextVersion" @click="cloneVersion">基于当前版本创建 Draft</button>
            <code v-if="selectedVersion?.content_sha256">{{ shortDigest(selectedVersion.content_sha256) }}</code>
          </div>

          <div v-if="readOnly" class="notice immutable-notice"><strong>此版本不可变</strong><span>Published / Retired / Staged 版本不能直接编辑。输入新版本号并创建 Draft，或重新发布 Retired 版本完成回退。</span></div>

          <section class="form-section">
            <header><div><span>01</span><h3>身份与方法说明</h3></div><p>普通用户看到名称与说明；运行时加载完整 instruction。</p></header>
            <div class="form-grid">
              <label><span>Skill ID</span><input v-model.trim="form.skill_id" :disabled="!isNewAsset" placeholder="skill.market-research" /></label>
              <label><span>版本</span><input v-model.trim="form.version" :disabled="Boolean(selectedVersion)" placeholder="1.0.0" /></label>
              <label><span>名称</span><input v-model.trim="form.name" :disabled="readOnly" /></label>
              <label><span>标签（逗号分隔）</span><input v-model="form.tags" :disabled="readOnly" placeholder="research, opc" /></label>
              <label class="wide"><span>说明</span><textarea v-model="form.description" :disabled="readOnly" rows="2" /></label>
              <label class="wide"><span>Instruction Content</span><textarea v-model="form.instruction_content" :disabled="readOnly" rows="14" placeholder="写清目标、步骤、判断标准、边界和输出要求。" /></label>
              <label class="wide"><span>变更说明</span><input v-model.trim="form.change_note" :disabled="readOnly" placeholder="这个版本为什么变化" /></label>
            </div>
          </section>

          <section class="form-section">
            <header><div><span>02</span><h3>Capability 与 Integration 依赖</h3></div><p>这里只声明完成方法所需的动作；Agent 仍需单独授权。</p></header>
            <div class="dependency-grid">
              <div><h4>精确 Capability 版本</h4><label v-for="item in capabilities" :key="capabilityKey(item)" class="dependency-row"><input v-model="form.capability_keys" :disabled="readOnly" type="checkbox" :value="capabilityKey(item)" /><span><strong>{{ item.name }}</strong><small>{{ capabilityKey(item) }}</small></span></label><p v-if="!capabilities.length">当前没有已发布 Capability。</p></div>
              <div><h4>Integration</h4><label v-for="item in integrations" :key="item.connection_id" class="dependency-row"><input v-model="form.required_integrations" :disabled="readOnly" type="checkbox" :value="item.connection_id" /><span><strong>{{ item.name }}</strong><small>{{ item.connection_id }} · {{ item.current_revision_id || '未发布' }}</small></span></label><p v-if="!integrations.length">当前没有已发布远程 Integration。</p></div>
            </div>
          </section>

          <section class="form-section">
            <header><div><span>03</span><h3>结构、示例与 Eval</h3></div><p>JSON 会在保存时解析；发布前由服务端再次验证。</p></header>
            <div class="json-grid">
              <label><span>Input Schema</span><textarea v-model="form.input_schema" :disabled="readOnly" rows="10" /></label>
              <label><span>Output Schema</span><textarea v-model="form.output_schema" :disabled="readOnly" rows="10" /></label>
              <label><span>Examples</span><textarea v-model="form.examples" :disabled="readOnly" rows="9" /></label>
              <label><span>Templates</span><textarea v-model="form.templates" :disabled="readOnly" rows="9" /></label>
              <label class="wide"><span>Eval Cases <small>每项需要 name、input、expected_behavior</small></span><textarea v-model="form.eval_cases" :disabled="readOnly" rows="10" /></label>
              <label class="wide"><span>Source / Provenance</span><textarea v-model="form.source" :disabled="readOnly" rows="5" /></label>
            </div>
          </section>

          <section class="release-section form-section">
            <header><div><span>04</span><h3>校验与发布</h3></div><p>发布经过 Worker 预热；旧版本保留，可重新激活回退。</p></header>
            <div class="release-grid">
              <div class="release-policy">
                <label><span>生效方式</span><select v-model="rollout.activation_mode"><option value="automatic">Worker 确认后自动生效</option><option value="manual">确认后等待人工批准</option></select></label>
                <label><span>预热超时</span><input v-model.number="rollout.timeout_seconds" type="number" min="10" max="86400" /></label>
                <label class="check"><input v-model="rollout.require_healthy_workers" type="checkbox" />必须存在健康 Agent Worker</label>
                <label class="check"><input v-model="rollout.auto_rollback" type="checkbox" />预热失败保护当前版本</label>
                <div class="release-actions"><button class="secondary-button" :disabled="!persistedDraft || validating" @click="validate">{{ validating ? '校验中…' : '运行发布校验' }}</button><button class="primary-button" :disabled="!canPublish || publishing" @click="publish">{{ selectedVersion?.status === 'retired' ? '重新激活此版本' : publishing ? '发布中…' : '发布版本' }}</button></div>
              </div>
              <div class="validation-report" :class="validation?.valid ? 'valid' : validation ? 'invalid' : ''">
                <template v-if="validation"><strong>{{ validation.valid ? '校验通过' : '校验未通过' }}</strong><ul><li v-for="item in validation.checks" :key="item.check"><i :class="{ pass: item.passed }" />{{ checkLabel(item.check) }}<small v-if="item.count !== undefined">{{ item.count }}</small></li></ul><p v-for="item in validation.errors" :key="item">{{ item }}</p><small v-for="item in validation.warnings" :key="item">{{ item }}</small></template>
                <template v-else><strong>尚未校验</strong><p>Draft 保存后运行结构、Schema、依赖与 Eval 覆盖检查。</p></template>
              </div>
            </div>
          </section>

          <section v-if="detail?.versions?.length" class="version-history form-section">
            <header><div><span>05</span><h3>版本证据</h3></div><p>每个版本内容不可变，摘要可用于 Agent / Workflow 对账。</p></header>
            <article v-for="item in detail.versions" :key="item.version" :class="{ current: detail.current_version === item.version }" @click="chooseVersion(item.version)"><i /><div><strong>v{{ item.version }} · {{ versionStatusLabel(item.status) }}</strong><small>{{ shortDigest(item.content_sha256) }} · {{ formatDate(item.published_at || item.updated_at) }}</small></div><span v-if="detail.current_version === item.version">CURRENT</span><code>{{ item.eval_cases.length }} EVAL</code></article>
          </section>
        </template>
        <div v-else class="empty-state"><span>S</span><strong>选择或新建一个 Skill</strong><p>在这里完成 Draft、校验、发布、停用和版本回退闭环。</p></div>
      </main>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { useMessage } from 'naive-ui'
import { getAdminCapabilities, type AdminCapability } from '../api/admin'
import { listRemoteConnections, type RemoteConnection } from '../api/remoteConnections'
import {
  getSkill,
  listSkills,
  publishSkillVersion,
  saveSkillDraft,
  setSkillStatus,
  validateSkillVersion,
  type SaveSkillDraft,
  type SkillRolloutPolicy,
  type SkillSummary,
  type SkillValidationReport,
  type SkillVersion,
} from '../api/skills'

interface SkillForm {
  skill_id: string; version: string; name: string; description: string; instruction_content: string
  tags: string; capability_keys: string[]; required_integrations: string[]; change_note: string
  input_schema: string; output_schema: string; examples: string; eval_cases: string; templates: string; source: string
}

const message = useMessage()
const skills = ref<SkillSummary[]>([])
const detail = ref<SkillSummary | null>(null)
const selectedVersion = ref<SkillVersion | null>(null)
const capabilities = ref<AdminCapability[]>([])
const integrations = ref<RemoteConnection[]>([])
const selectedId = ref('')
const search = ref('')
const nextVersion = ref('')
const loading = ref(false)
const saving = ref(false)
const validating = ref(false)
const publishing = ref(false)
const error = ref('')
const validation = ref<SkillValidationReport | null>(null)
const isNewAsset = ref(false)
const editing = ref(false)
const form = reactive<SkillForm>(blankForm())
const rollout = reactive<SkillRolloutPolicy>({ activation_mode: 'automatic', timeout_seconds: 300, auto_rollback: true, require_healthy_workers: true })

const filteredSkills = computed(() => { const value = search.value.toLowerCase(); return skills.value.filter((item) => !value || `${item.name} ${item.skill_id} ${item.tags.join(' ')}`.toLowerCase().includes(value)) })
const readOnly = computed(() => Boolean(selectedVersion.value && selectedVersion.value.status !== 'draft'))
const persistedDraft = computed(() => Boolean(selectedVersion.value?.status === 'draft' && selectedVersion.value.skill_id === form.skill_id && selectedVersion.value.version === form.version))
const canPublish = computed(() => Boolean(selectedVersion.value && ['draft', 'retired'].includes(selectedVersion.value.status) && (selectedVersion.value.status === 'retired' || validation.value?.valid)))

function blankForm(): SkillForm { return { skill_id: '', version: '1.0.0', name: '', description: '', instruction_content: '', tags: '', capability_keys: [], required_integrations: [], change_note: '', input_schema: '{\n  "type": "object"\n}', output_schema: '{\n  "type": "object"\n}', examples: '[]', eval_cases: '[\n  {\n    "name": "basic",\n    "input": "",\n    "expected_behavior": ""\n  }\n]', templates: '[]', source: '{\n  "kind": "managed"\n}' } }
function pretty(value: unknown) { return JSON.stringify(value ?? {}, null, 2) }
function fill(value: SkillVersion) { Object.assign(form, { skill_id: value.skill_id, version: value.version, name: value.name, description: value.description, instruction_content: value.instruction_content, tags: value.tags.join(', '), capability_keys: value.required_capabilities.map((item) => `${item.capability_id}@${item.version}`), required_integrations: [...value.required_integrations], change_note: value.change_note, input_schema: pretty(value.input_schema), output_schema: pretty(value.output_schema), examples: pretty(value.examples), eval_cases: pretty(value.eval_cases), templates: pretty(value.templates), source: pretty(value.source) }); validation.value = Object.keys(value.validation_report || {}).length ? value.validation_report as SkillValidationReport : null }
function parseJson<T>(value: string, field: string): T { try { return JSON.parse(value) as T } catch { throw new Error(`${field} 不是有效 JSON`) } }
function payload(): SaveSkillDraft { return { skill_id: form.skill_id, version: form.version, name: form.name, description: form.description, instruction_content: form.instruction_content, tags: form.tags.split(',').map((item) => item.trim()).filter(Boolean), required_capabilities: form.capability_keys.map((item) => { const offset = item.lastIndexOf('@'); return { capability_id: item.slice(0, offset), version: item.slice(offset + 1) } }), required_integrations: [...form.required_integrations], change_note: form.change_note, input_schema: parseJson(form.input_schema, 'Input Schema'), output_schema: parseJson(form.output_schema, 'Output Schema'), examples: parseJson(form.examples, 'Examples'), eval_cases: parseJson(form.eval_cases, 'Eval Cases'), templates: parseJson(form.templates, 'Templates'), source: parseJson(form.source, 'Source') } }

async function load() { loading.value = true; error.value = ''; try { const [skillItems, capabilityItems, connectionItems] = await Promise.all([listSkills(), getAdminCapabilities(), listRemoteConnections().catch(() => [])]); skills.value = skillItems; capabilities.value = capabilityItems; integrations.value = connectionItems.filter((item) => Boolean(item.current_revision_id)); if (selectedId.value) await selectSkill(selectedId.value); else if (skillItems.length) await selectSkill(skillItems[0].skill_id) } catch (cause) { error.value = errorText(cause) } finally { loading.value = false } }
async function selectSkill(skillId: string) { selectedId.value = skillId; isNewAsset.value = false; editing.value = true; detail.value = await getSkill(skillId); const versions = detail.value.versions || []; const target = versions.find((item) => item.status === 'draft') || versions.find((item) => item.version === detail.value?.current_version) || versions[0]; selectedVersion.value = target || null; nextVersion.value = ''; if (target) fill(target) }
function chooseVersion(version: string) { const target = detail.value?.versions?.find((item) => item.version === version); if (target) { selectedVersion.value = target; isNewAsset.value = false; nextVersion.value = ''; fill(target) } }
function createSkill() { selectedId.value = ''; detail.value = null; selectedVersion.value = null; isNewAsset.value = true; editing.value = true; validation.value = null; nextVersion.value = ''; Object.assign(form, blankForm()) }
function cloneVersion() { if (!selectedVersion.value || !nextVersion.value) return; const base = { ...form }; selectedVersion.value = null; validation.value = null; Object.assign(form, base, { version: nextVersion.value, change_note: '' }); nextVersion.value = ''; isNewAsset.value = false }
async function save() { saving.value = true; try { const saved = await saveSkillDraft(payload()); selectedId.value = saved.skill_id; message.success('Skill Draft 已保存'); await selectSkill(saved.skill_id); chooseVersion(saved.version); await refreshList() } catch (cause) { message.error(errorText(cause)) } finally { saving.value = false } }
async function validate() { const version = selectedVersion.value; if (!version) return; validating.value = true; try { validation.value = await validateSkillVersion(version.skill_id, version.version); if (validation.value.valid) message.success('发布校验通过'); else message.warning('发布校验未通过') } catch (cause) { message.error(errorText(cause)) } finally { validating.value = false } }
async function publish() { const version = selectedVersion.value; if (!version) return; publishing.value = true; try { const result = await publishSkillVersion(version.skill_id, version.version, rollout); message.success(result.status === 'published' ? 'Skill 已发布' : 'Skill rollout 已启动'); await refreshList(); await selectSkill(version.skill_id) } catch (cause) { message.error(errorText(cause)) } finally { publishing.value = false } }
async function changeStatus(status: SkillSummary['status']) { const skill = detail.value; if (!skill) return; try { await setSkillStatus(skill.skill_id, status); message.success(status === 'active' ? 'Skill 已启用' : 'Skill 已停用'); await refreshList(); await selectSkill(skill.skill_id) } catch (cause) { message.error(errorText(cause)) } }
async function refreshList() { skills.value = await listSkills() }

function capabilityKey(item: AdminCapability) { return `${item.ref.capability_id}@${item.ref.version}` }
function errorText(value: unknown) { return value instanceof Error ? value.message : '操作失败' }
function shortDigest(value: string) { return value.length > 24 ? `${value.slice(0, 18)}…${value.slice(-6)}` : value }
function statusLabel(value: SkillSummary['status']) { return ({ active: '启用', disabled: '停用', archived: '归档' } as const)[value] }
function versionStatusLabel(value: SkillVersion['status']) { return ({ draft: 'Draft', staged: '预热中', published: '已发布', retired: '历史版本' } as const)[value] }
function checkLabel(value: string) { return ({ instruction_content: '方法内容', eval_cases: 'Eval 覆盖', required_capabilities: 'Capability 依赖', required_integrations: 'Integration 依赖', document_schema: '文档结构' } as Record<string, string>)[value] || value }
function formatDate(value?: string | null) { return value ? new Date(value).toLocaleString('zh-CN') : '—' }

onMounted(load)
</script>

<style scoped>
.skills-page{display:grid;gap:16px}.heading-actions,.editor-actions,.release-actions{display:flex;align-items:center;gap:8px}.contract{display:grid;grid-template-columns:repeat(3,1fr);gap:1px;overflow:hidden}.contract article{display:grid;grid-template-columns:30px 1fr;gap:10px;padding:16px 18px;background:var(--surface)}.contract article>span{display:grid;width:28px;height:28px;place-items:center;color:var(--accent);background:var(--accent-subtle);border-radius:8px;font:9px var(--font-mono)}.contract strong{color:var(--text-strong);font-size:10px}.contract p{margin:4px 0 0;color:var(--text-muted);font-size:9px;line-height:1.55}.workspace{display:grid;grid-template-columns:300px minmax(0,1fr);gap:16px;align-items:start}.directory{position:sticky;top:calc(var(--topbar-height) + 18px);max-height:calc(100vh - var(--topbar-height) - 36px);overflow:auto}.directory-head{display:grid;gap:12px;padding:18px 15px}.directory-head>div{display:flex;justify-content:space-between}.directory-head strong{font:16px var(--font-mono)}.directory-head input,.version-bar input,.version-bar select,.form-grid input,.form-grid textarea,.release-policy input,.release-policy select,.json-grid textarea{width:100%;padding:9px 10px;color:var(--text);background:var(--input);border:1px solid var(--border-strong);border-radius:9px;outline:none}.directory>button{display:grid;width:100%;grid-template-columns:32px minmax(0,1fr) 7px;gap:10px;align-items:center;padding:13px 15px;color:var(--text);background:transparent;border:0;border-top:1px solid var(--border);text-align:left;cursor:pointer}.directory>button:hover,.directory>button.active{background:var(--accent-subtle)}.directory button div{display:grid;min-width:0;gap:3px}.directory button strong,.directory button small{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.directory button strong{color:var(--text-strong);font-size:11px}.directory button small{color:var(--text-muted);font:8px var(--font-mono)}.skill-icon{display:grid;width:31px;height:31px;place-items:center;color:var(--accent);background:var(--surface-raised);border-radius:8px;font-weight:700}.directory i{width:7px;height:7px;background:var(--success);border-radius:50%}.directory i.disabled{background:var(--warning)}.directory i.archived{background:var(--text-muted)}.editor{min-width:0;overflow:hidden}.editor-head{display:flex;align-items:center;justify-content:space-between;gap:20px;padding:20px 22px}.editor-head h2{margin:5px 0 2px;color:var(--text-strong);font-size:20px}.editor-head p{margin:0;color:var(--text-muted);font-size:9px}.state-chip{padding:5px 8px;color:var(--text-muted);background:var(--surface-raised);border-radius:7px;font:9px var(--font-mono)}.state-chip.published{color:var(--success);background:color-mix(in srgb,var(--success) 10%,transparent)}.state-chip.staged{color:var(--warning);background:var(--warning-subtle)}.secondary-button.danger{color:var(--danger)}.version-bar{display:grid;grid-template-columns:minmax(170px,.8fr) minmax(150px,.6fr) auto 1fr;gap:10px;align-items:end;padding:13px 22px;border-block:1px solid var(--border);background:var(--surface-raised)}.version-bar label,.form-grid label,.json-grid label,.release-policy label{display:grid;gap:5px;color:var(--text-muted);font-size:9px}.version-bar>code{align-self:center;justify-self:end;color:var(--text-muted);font-size:8px}.immutable-notice{display:flex;gap:9px;margin:16px 22px 0;color:var(--warning);background:var(--warning-subtle)}.form-section{margin:18px 22px 0;overflow:hidden;border:1px solid var(--border);border-radius:12px}.form-section>header{display:flex;align-items:flex-start;justify-content:space-between;gap:16px;padding:14px 16px;background:var(--surface-raised);border-bottom:1px solid var(--border)}.form-section>header>div{display:flex;align-items:center;gap:8px}.form-section>header span{color:var(--accent);font:9px var(--font-mono)}.form-section h3{margin:0;color:var(--text-strong);font-size:13px}.form-section>header p{margin:0;color:var(--text-muted);font-size:9px}.form-grid,.json-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:14px;padding:16px}.form-grid .wide,.json-grid .wide{grid-column:1/-1}.form-grid textarea,.json-grid textarea{resize:vertical;font:10px/1.6 var(--font-mono)}.dependency-grid{display:grid;grid-template-columns:1fr 1fr;gap:0}.dependency-grid>div{max-height:320px;overflow:auto;padding:15px 16px}.dependency-grid>div+div{border-left:1px solid var(--border)}.dependency-grid h4{margin:0 0 10px;color:var(--text-strong);font-size:10px}.dependency-grid>div>p{color:var(--text-muted);font-size:9px}.dependency-row{display:flex;gap:9px;align-items:flex-start;padding:8px 0;border-bottom:1px solid var(--border)}.dependency-row span{display:grid;gap:2px}.dependency-row strong{color:var(--text-strong);font-size:9px}.dependency-row small{color:var(--text-muted);font:8px var(--font-mono)}.release-section{margin-bottom:18px}.release-grid{display:grid;grid-template-columns:1fr 1fr;gap:16px;padding:16px}.release-policy{display:grid;grid-template-columns:1fr 130px;gap:12px}.release-policy .check{display:flex;align-items:center;gap:7px}.release-policy .check input{width:auto}.release-actions{grid-column:1/-1}.validation-report{display:grid;align-content:start;gap:8px;padding:14px;background:var(--surface-raised);border:1px solid var(--border);border-radius:10px}.validation-report.valid{border-color:color-mix(in srgb,var(--success) 35%,var(--border))}.validation-report.invalid{border-color:color-mix(in srgb,var(--danger) 35%,var(--border))}.validation-report strong{color:var(--text-strong);font-size:11px}.validation-report p,.validation-report>small{margin:0;color:var(--text-muted);font-size:9px;line-height:1.5}.validation-report ul{display:grid;gap:6px;margin:0;padding:0;list-style:none}.validation-report li{display:flex;align-items:center;gap:7px;color:var(--text);font-size:9px}.validation-report li i{width:7px;height:7px;background:var(--danger);border-radius:50%}.validation-report li i.pass{background:var(--success)}.validation-report li small{margin-left:auto}.version-history{margin-bottom:22px}.version-history article{display:grid;grid-template-columns:10px minmax(0,1fr) auto auto;gap:10px;align-items:center;padding:12px 16px;border-bottom:1px solid var(--border);cursor:pointer}.version-history article:hover,.version-history article.current{background:var(--accent-subtle)}.version-history article>i{width:8px;height:8px;border:2px solid var(--accent);border-radius:50%}.version-history article>div{display:grid;gap:3px}.version-history strong{color:var(--text-strong);font-size:10px}.version-history small,.version-history code{color:var(--text-muted);font:8px var(--font-mono)}.version-history article>span{color:var(--accent);font:8px var(--font-mono)}
@media(max-width:1050px){.workspace{grid-template-columns:1fr}.directory{position:static;max-height:280px}.contract{grid-template-columns:1fr}.release-grid{grid-template-columns:1fr}}@media(max-width:700px){.editor-head,.form-section>header{align-items:flex-start;flex-direction:column}.version-bar,.form-grid,.json-grid,.dependency-grid,.release-policy{grid-template-columns:1fr}.version-bar>code{justify-self:start}.dependency-grid>div+div{border-left:0;border-top:1px solid var(--border)}.heading-actions,.editor-actions{flex-wrap:wrap}}
</style>
