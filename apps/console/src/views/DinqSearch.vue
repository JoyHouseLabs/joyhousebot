<template>
  <div class="page dinq-search-page">
    <header class="search-hero">
      <div>
        <span class="eyebrow">DINQ TALENT DISCOVERY</span>
        <h1>搜索并核验你需要的人才</h1>
        <p>从一句自然语言开始。Dinq 会先形成可确认的搜索简报，再以可审计证据搜索、排序和富化候选人。</p>
      </div>
      <div class="hero-actions">
        <router-link class="secondary-button" to="/plugins/dinq">查看能力与数据源</router-link>
        <router-link class="secondary-button" to="/runs">运行中心</router-link>
      </div>
    </header>

    <div v-if="error" class="notice error-notice">{{ error }}</div>
    <section class="search-layout">
      <article class="panel search-composer">
        <div class="composer-heading">
          <div><span class="eyebrow">NEW SEARCH</span><h2>描述你要找的人</h2></div>
          <span class="draft-state">{{ briefCount }}/4 项已预填</span>
        </div>

        <label class="prompt-field">
          <span>搜索目标</span>
          <textarea v-model="prompt" rows="4" placeholder="例如：寻找在中国从事强化学习或 RLHF 的工业界算法工程师，优先有大模型项目经验。" @keydown.meta.enter.prevent="startSearch" @keydown.ctrl.enter.prevent="startSearch" />
          <small>可先写一个目标；Dinq 会继续引导你确认候选人类型、地区和不可放宽条件。</small>
        </label>

        <section class="template-section" aria-label="常用搜索示例">
          <span class="section-label">从一个示例开始</span>
          <div class="template-list">
            <button v-for="item in templates" :key="item.title" type="button" :class="['template-chip', { active: chosenTemplate === item.title }]" @click="applyTemplate(item)">
              <strong>{{ item.title }}</strong><small>{{ item.caption }}</small>
            </button>
          </div>
        </section>

        <details class="brief-builder" :open="showBrief">
          <summary @click.prevent="showBrief = !showBrief">
            <span><b>可选：预填搜索简报</b><small>前端只收集已明确的条件，真正的校验与追问仍由场景执行。</small></span>
            <i>{{ showBrief ? '收起' : '展开' }}</i>
          </summary>
          <div v-show="showBrief" class="brief-fields">
            <label><span>搜索方向</span><select v-model="researchTopic"><option value="">交给 Dinq 追问</option><option value="reinforcement_learning">强化学习 / Deep RL</option><option value="large_language_models">大语言模型 / LLM</option><option value="multimodal_ai">多模态与视觉语言模型</option><option value="agent_systems">Agent 与多智能体系统</option></select></label>
            <label><span>候选人类型</span><select v-model="candidateType"><option value="">交给 Dinq 追问</option><option value="academic">学术界研究员 / 教授 / 博士生</option><option value="industry">工业界工程师 / 科学家</option><option value="both">不限，两者都要</option></select></label>
            <label><span>地区偏好</span><select v-model="region"><option value="">交给 Dinq 追问</option><option value="china">中国（含港澳）</option><option value="global">全球范围</option><option value="north_america">北美</option><option value="europe">欧洲</option></select></label>
            <label><span>候选人数</span><select v-model.number="limit"><option :value="0">由场景确认</option><option :value="10">10 人</option><option :value="20">20 人</option><option :value="50">50 人</option></select></label>
          </div>
        </details>

        <div class="composer-footer">
          <span>Enter 开始 · Ctrl / ⌘ + Enter 直接提交</span>
          <button class="primary-button" type="button" :disabled="submitting || !prompt.trim()" @click="startSearch">{{ submitting ? '正在创建搜索…' : '开始搜索 →' }}</button>
        </div>
      </article>

      <aside class="panel search-guide">
        <span class="eyebrow">HOW IT WORKS</span>
        <h2>把“找人”变成可回放的决策</h2>
        <ol>
          <li><i>1</i><div><strong>形成简报</strong><p>收集方向、候选人范围、地区和硬约束；未确认的内容不会被擅自假设。</p></div></li>
          <li><i>2</i><div><strong>证据化搜索</strong><p>协调多个 Dinq 数据能力；所有候选人、来源和调用都会写入 Run。</p></div></li>
          <li><i>3</i><div><strong>点击富化</strong><p>选择候选人后创建独立富化 Run，保留字段证据、冲突与人工反馈。</p></div></li>
        </ol>
        <div class="guide-note"><b>硬约束不会被静默放宽</b><span>没有可审计证据的条件会被标记或排除，而不是按姓名、国籍等信息猜测。</span></div>
      </aside>
    </section>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { submitRuntimeRun } from '../api/runtime'

type SearchTemplate = {
  title: string
  caption: string
  prompt: string
  researchTopic?: string
  candidateType?: string
  region?: string
}

const route = useRoute()
const router = useRouter()
const prompt = ref('')
const researchTopic = ref('')
const candidateType = ref('')
const region = ref('')
const limit = ref(0)
const showBrief = ref(false)
const submitting = ref(false)
const error = ref('')
const chosenTemplate = ref('')

const templates: SearchTemplate[] = [
  { title: '强化学习人才', caption: '研究员、算法工程师与专家', prompt: '寻找强化学习或 RLHF 方向的人才。', researchTopic: 'reinforcement_learning' },
  { title: 'LLM 工程师', caption: '训练、推理、对齐与应用', prompt: '寻找有大语言模型训练、推理或对齐经验的工程师。', researchTopic: 'large_language_models', candidateType: 'industry' },
  { title: '学术研究者', caption: '论文、实验室与研究方向', prompt: '寻找在智能体与多智能体系统方向有代表性研究的学术研究者。', researchTopic: 'agent_systems', candidateType: 'academic' },
]

const briefCount = computed(() => [researchTopic.value, candidateType.value, region.value, limit.value].filter(Boolean).length)

function applyTemplate(item: SearchTemplate) {
  chosenTemplate.value = item.title
  prompt.value = item.prompt
  researchTopic.value = item.researchTopic || ''
  candidateType.value = item.candidateType || ''
  region.value = item.region || ''
  showBrief.value = true
}

async function startSearch() {
  const content = prompt.value.trim()
  if (!content || submitting.value) return
  submitting.value = true
  error.value = ''
  try {
    const scenarioInputs: Record<string, unknown> = {}
    if (researchTopic.value) scenarioInputs.research_topic = researchTopic.value
    if (candidateType.value) scenarioInputs.candidate_type = candidateType.value
    if (region.value) scenarioInputs.region = region.value
    if (limit.value) scenarioInputs.limit = limit.value
    const sessionId = `ui:dinq-search-${Date.now()}`
    const run = await submitRuntimeRun({
      prompt: content,
      sessionId,
      agentId: 'main-coordinator',
      scenarioId: 'dinq.discover.search',
      scenarioInputs,
      channel: 'dinq-search',
      chatId: sessionId,
    })
    await router.push(`/dinq/runs/${encodeURIComponent(run.run_id)}`)
  } catch (cause) {
    error.value = cause instanceof Error ? cause.message : '创建搜索任务失败'
  } finally {
    submitting.value = false
  }
}

onMounted(() => {
  const initialPrompt = route.query.prompt
  if (typeof initialPrompt === 'string') prompt.value = initialPrompt
})
</script>

<style scoped>
.dinq-search-page{max-width:1220px;padding-top:46px}.search-hero{display:flex;align-items:flex-end;justify-content:space-between;gap:28px;margin:8px 0 32px}.search-hero h1{max-width:700px;margin:8px 0;color:var(--text-strong);font-size:clamp(32px,4vw,52px);line-height:1.04;letter-spacing:-.055em}.search-hero p{max-width:660px;margin:0;color:var(--text-muted);font-size:14px;line-height:1.7}.hero-actions{display:flex;gap:8px;flex-wrap:wrap;justify-content:flex-end}.search-layout{display:grid;grid-template-columns:minmax(0,1.35fr) minmax(280px,.65fr);gap:18px;align-items:stretch}.search-composer,.search-guide{padding:26px}.composer-heading{display:flex;align-items:flex-start;justify-content:space-between;gap:16px;padding-bottom:20px;border-bottom:1px solid var(--border)}.composer-heading h2,.search-guide h2{margin:7px 0 0;color:var(--text-strong);font-size:23px;line-height:1.15;letter-spacing:-.03em}.draft-state{padding:6px 9px;border-radius:99px;color:var(--text-muted);background:var(--surface-raised);font:10px var(--font-mono);white-space:nowrap}.prompt-field{display:grid;gap:8px;margin:22px 0}.prompt-field>span,.brief-fields span{color:var(--text-strong);font-size:13px;font-weight:650}.prompt-field textarea{box-sizing:border-box;width:100%;min-height:118px;padding:15px;color:var(--text-strong);background:var(--input);border:1px solid var(--border-strong);border-radius:13px;font:15px/1.6 var(--font-sans);resize:vertical;transition:border-color .15s,box-shadow .15s}.prompt-field textarea:focus{outline:0;border-color:var(--accent);box-shadow:0 0 0 3px var(--accent-subtle)}.prompt-field small{color:var(--text-muted);font-size:11px;line-height:1.5}.template-section{margin:22px 0}.section-label{display:block;margin-bottom:9px;color:var(--text-muted);font:10px var(--font-mono);letter-spacing:.09em;text-transform:uppercase}.template-list{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:8px}.template-chip{display:grid;gap:5px;min-height:76px;padding:11px;border:1px solid var(--border);border-radius:11px;color:var(--text);background:var(--surface-raised);text-align:left;cursor:pointer;transition:.15s ease}.template-chip:hover,.template-chip.active{border-color:var(--accent-border);background:var(--accent-subtle)}.template-chip strong{font-size:12px}.template-chip small{color:var(--text-muted);font-size:10px;line-height:1.35}.brief-builder{border-top:1px solid var(--border);border-bottom:1px solid var(--border)}.brief-builder summary{display:flex;align-items:center;justify-content:space-between;gap:12px;padding:15px 0;cursor:pointer;list-style:none}.brief-builder summary::-webkit-details-marker{display:none}.brief-builder summary span{display:grid;gap:3px}.brief-builder summary b{color:var(--text-strong);font-size:13px}.brief-builder summary small{color:var(--text-muted);font-size:10px;line-height:1.35}.brief-builder summary i{color:var(--accent);font-size:11px;font-style:normal;white-space:nowrap}.brief-fields{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:12px;padding:0 0 18px}.brief-fields label{display:grid;gap:6px}.brief-fields select{width:100%;padding:10px;color:var(--text);background:var(--input);border:1px solid var(--border);border-radius:9px;font:11px var(--font-sans)}.composer-footer{display:flex;align-items:center;justify-content:space-between;gap:16px;padding-top:20px}.composer-footer span{color:var(--text-muted);font-size:10px}.composer-footer .primary-button{min-width:136px}.search-guide{display:flex;flex-direction:column}.search-guide ol{display:grid;gap:20px;margin:24px 0;padding:0;list-style:none}.search-guide li{display:flex;gap:11px}.search-guide li>i{display:grid;place-items:center;flex:0 0 24px;width:24px;height:24px;border-radius:50%;color:var(--accent);background:var(--accent-subtle);font:700 10px var(--font-mono);font-style:normal}.search-guide li div{display:grid;gap:4px}.search-guide li strong{color:var(--text-strong);font-size:13px}.search-guide li p{margin:0;color:var(--text-muted);font-size:11px;line-height:1.55}.guide-note{display:grid;gap:6px;margin-top:auto;padding:14px;border:1px solid var(--accent-border);border-radius:11px;background:var(--accent-subtle)}.guide-note b{color:var(--text-strong);font-size:12px}.guide-note span{color:var(--text-muted);font-size:10px;line-height:1.5}@media(max-width:900px){.search-layout{grid-template-columns:1fr}.search-guide{min-height:auto}.guide-note{margin-top:10px}}@media(max-width:680px){.dinq-search-page{padding-top:26px}.search-hero{align-items:flex-start;flex-direction:column;margin-bottom:22px}.search-hero h1{font-size:34px}.hero-actions{justify-content:flex-start}.search-composer,.search-guide{padding:18px}.template-list,.brief-fields{grid-template-columns:1fr}.composer-footer{align-items:flex-start;flex-direction:column}.composer-footer .primary-button{width:100%}}
</style>
