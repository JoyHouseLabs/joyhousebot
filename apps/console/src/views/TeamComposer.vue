<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRoute } from 'vue-router'
import { useMessage } from 'naive-ui'
import { useTeamComposer } from '../composables/useTeamComposer'
import MemberCards from '../components/teamComposer/MemberCards.vue'
import PresetCards from '../components/teamComposer/PresetCards.vue'
import GuardrailsForm from '../components/teamComposer/GuardrailsForm.vue'
import PublishChecks from '../components/teamComposer/PublishChecks.vue'
import PlanPreview from '../components/teamComposer/PlanPreview.vue'
import StageGraph from '../components/teamComposer/StageGraph.vue'

const route = useRoute()
const message = useMessage()
const composer = useTeamComposer()
const trialPrompt = ref('')

const STEPS = [
  { title: '成员与职责', hint: '从已发布 Agent 中选择成员并写清职责' },
  { title: '协作模式', hint: '选择可解释的预设并绑定角色' },
  { title: '护栏', hint: '并行上限、复核与人工确认' },
  { title: '预览发布', hint: '检查、发布并观察 Worker 加载' },
]

const maxParallelBudget = computed(() => Number(composer.form.budget_policy.max_parallel_tasks) || 4)
const publishedTeamId = computed(() => composer.publishedRevision?.team_id || (composer.stored ? composer.form.team_id : ''))

const guardrails = computed({
  get: () => composer.form.guardrails,
  set: (value: typeof composer.form.guardrails) => { composer.form.guardrails = value },
})
const roleBindings = computed({
  get: () => composer.form.role_bindings,
  set: (value: Record<string, string[]>) => { composer.form.role_bindings = value },
})

function setStep(index: number) {
  composer.step = Math.max(0, Math.min(3, index)) as typeof composer.step
}

async function guarded(action: () => Promise<unknown>) {
  composer.busy = true
  composer.error = ''
  try {
    await action()
    if (composer.notice) message.success(composer.notice)
  } catch (cause) {
    const text = cause instanceof Error ? cause.message : '操作失败'
    composer.error = text
    message.error(text)
  } finally {
    composer.busy = false
  }
}

async function next() {
  if (composer.step === 0 && composer.form.members.length < 2) { message.error('至少选择两名成员'); return }
  if (composer.step === 1) {
    const ok = await composer.runValidation()
    if (!ok) { message.error('Blueprint 校验未通过，请调整角色绑定'); return }
  }
  if (composer.step === 2) await guarded(() => composer.save())
  setStep(composer.step + 1)
}

onMounted(async () => {
  await composer.loadCatalog()
  const teamId = route.query.team_id ? String(route.query.team_id) : ''
  if (teamId) {
    try { await composer.loadExistingTeam(teamId) } catch { message.error('加载 Team 失败') }
  }
})
</script>

<template>
  <div class="page composer-page">
    <header class="page-heading">
      <div>
        <span class="eyebrow">TEAM COMPOSER</span>
        <h1>协作编排器</h1>
        <p>用角色、协作模式和护栏创建可审计的 AgentTeam；无需理解 revision ID 或编辑 JSON。高级配置请使用 <RouterLink to="/teams">AgentTeam 高级配置</RouterLink>。</p>
      </div>
      <div class="heading-actions">
        <button type="button" class="secondary-button" :disabled="composer.busy" @click="guarded(() => composer.save())">保存草稿</button>
        <button type="button" class="primary-button" :disabled="composer.busy || !composer.canSave" @click="guarded(() => composer.publish())">发布版本</button>
      </div>
    </header>

    <div v-if="composer.error" class="notice error-notice">{{ composer.error }}</div>

    <nav class="composer-steps">
      <button
        v-for="(item, index) in STEPS"
        :key="item.title"
        type="button"
        class="composer-step"
        :class="{ active: composer.step === index, done: composer.step > index }"
        @click="setStep(index)"
      >
        <span class="step-index">{{ composer.step > index ? '✓' : index + 1 }}</span>
        <span class="step-copy"><strong>{{ item.title }}</strong><small>{{ item.hint }}</small></span>
      </button>
    </nav>

    <section v-if="composer.step === 0" class="panel">
      <div class="panel-heading">
        <div><span class="eyebrow">IDENTITY</span><h2>Team 基本信息</h2><p>名称与描述会展示给使用者；ID 保存时自动生成。</p></div>
      </div>
      <div class="identity-form">
        <label><span>名称</span><input v-model="composer.form.name" placeholder="如：教学方案专家组" /></label>
        <label><span>描述</span><input v-model="composer.form.description" placeholder="这个 Team 解决什么问题" /></label>
        <label v-if="composer.form.team_id"><span>Team ID</span><input :value="composer.form.team_id" readonly /></label>
      </div>
    </section>

    <MemberCards
      v-if="composer.step === 0"
      :members="composer.form.members"
      :agents="composer.agents"
      :coordinator-member-id="composer.form.coordinator_member_id"
      @add="composer.addMember"
      @remove="composer.removeMember"
      @focus="(id) => { composer.form.coordinator_member_id = id; composer.rebuildBindings() }"
    />

    <template v-if="composer.step === 1">
      <PresetCards
        v-model="composer.form.preset"
        :presets="composer.presets"
        :bindings="roleBindings"
        :members="composer.memberIds"
        :coordinator-member-id="composer.form.coordinator_member_id"
        @update:bindings="roleBindings = $event"
      />
      <section class="panel">
        <div class="panel-heading"><div><span class="eyebrow">STAGE PREVIEW</span><h2>阶段图预览</h2><p>按当前绑定生成的协作阶段；发布后 Coordinator 的计划必须覆盖这些阶段。</p></div></div>
        <StageGraph compact :phases="(composer.validation?.normalized?.phases || []).map((phase) => ({ ...phase, step_ids: [] }))" />
      </section>
    </template>

    <GuardrailsForm v-if="composer.step === 2" v-model:guardrails="guardrails" :max-parallel-budget="maxParallelBudget" />

    <template v-if="composer.step === 3">
      <PublishChecks
        :members="composer.form.members"
        :coordinator-member-id="composer.form.coordinator_member_id"
        :validation="composer.validation"
        :rollout="composer.rollout"
        :saved="composer.stored"
      />
      <section class="panel">
        <div class="panel-heading">
          <div><span class="eyebrow">TRIAL SETUP</span><h2>试运行目标</h2><p>用一个真实目标验证协作流程；确认前不会有外部副作用。</p></div>
          <button type="button" class="secondary-button" @click="composer.refreshRollout(composer.form.team_id)">刷新 rollout</button>
        </div>
        <label class="trial-prompt"><span>目标</span>
          <textarea v-model="trialPrompt" rows="2" placeholder="例如：为 7 岁儿童设计一个两周的科学启蒙方案" />
        </label>
      </section>
      <PlanPreview :team-id="publishedTeamId" :prompt="trialPrompt" @error="(text) => message.error(text)" />
    </template>

    <footer class="composer-footer">
      <button type="button" class="secondary-button" :disabled="composer.step === 0" @click="setStep(composer.step - 1)">上一步</button>
      <button type="button" class="primary-button" :disabled="composer.step === 3 || composer.busy" @click="next">下一步</button>
    </footer>
  </div>
</template>

<style scoped>
.composer-page { display: grid; gap: 14px; max-width: 1500px; }
.composer-steps { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 8px; }
.composer-step { display: flex; gap: 10px; align-items: center; padding: 12px 13px; background: var(--surface); border: 1px solid var(--border); border-radius: 11px; text-align: left; cursor: pointer; }
.composer-step.active { border-color: var(--accent); box-shadow: 0 0 0 2px var(--accent-subtle); }
.composer-step.done { border-color: var(--success); }
.step-index { display: grid; width: 26px; height: 26px; flex: none; place-items: center; color: var(--accent); background: var(--accent-subtle); border: 1px solid var(--accent-border); border-radius: 8px; font: 600 11px var(--font-mono); }
.composer-step.done .step-index { color: var(--success); background: rgba(50, 182, 122, 0.12); border-color: rgba(50, 182, 122, 0.3); }
.step-copy { display: grid; gap: 2px; }
.step-copy strong { color: var(--text-strong); font-size: 11px; }
.step-copy small { color: var(--text-muted); font-size: 9px; }
.identity-form { display: grid; grid-template-columns: minmax(0, 1fr) minmax(0, 1.4fr) minmax(180px, 0.7fr); gap: 10px; padding: 0 14px 14px; }
.identity-form label { display: grid; gap: 5px; }
.identity-form span { color: var(--text-muted); font-size: 9px; }
.identity-form input { width: 100%; padding: 9px 11px; color: var(--text); background: var(--input); border: 1px solid var(--border-strong); border-radius: 8px; font-size: 10px; }
.identity-form input[readonly] { color: var(--text-muted); font-family: var(--font-mono); }
.trial-prompt { display: grid; gap: 6px; padding: 0 14px 14px; }
.trial-prompt span { color: var(--text-muted); font-size: 9px; }
.trial-prompt textarea { width: 100%; padding: 10px 12px; color: var(--text); background: var(--input); border: 1px solid var(--border-strong); border-radius: 9px; resize: vertical; font-size: 11px; }
.composer-footer { display: flex; justify-content: flex-end; gap: 8px; }
@media (max-width: 900px) { .composer-steps { grid-template-columns: repeat(2, minmax(0, 1fr)); } .identity-form { grid-template-columns: 1fr; } }
</style>
