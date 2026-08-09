<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { listRuns, submitGoal, type RuntimeRun } from '../api'
import { dateLabel, isActiveRun, statusLabel } from '../presentation'

const router = useRouter()
const route = useRoute()
const goal = ref('')
const submitting = ref(false)
const error = ref('')
const recent = ref<RuntimeRun[]>([])

const examples = [
  '整理今天收集的资料，形成一份可以分享的简报',
  '调研这个想法的市场、竞品和下一步行动',
  '把我手上的事项梳理成今天能完成的计划',
  '检查最近失败的任务，给出修复建议',
]

async function loadRecent() {
  try { recent.value = (await listRuns(6)).slice(0, 3) } catch { /* 首屏仍可提交 */ }
}

async function start() {
  const value = goal.value.trim()
  if (!value || submitting.value) return
  submitting.value = true
  error.value = ''
  try {
    const run = await submitGoal(value)
    goal.value = ''
    await router.push(`/runs/${run.run_id}`)
  } catch (cause) {
    error.value = cause instanceof Error ? cause.message : '暂时无法开始，请检查执行体连接。'
  } finally {
    submitting.value = false
  }
}

onMounted(() => {
  if (route.query.intent === 'automation') {
    goal.value = '帮我创建一个自动化：每天晚上九点总结今天的工作，并形成明日计划。'
  }
  void loadRecent()
})
</script>

<template>
  <section class="home-hero">
    <div class="hero-copy">
      <span class="eyebrow">YOUR DATA + YOUR INTELLIGENCE</span>
      <h1>今天想完成什么？</h1>
      <p>交代目标，JoyClaw 会持续推进、主动追问并交付结果。过程可追踪，成果会沉淀为真正属于你的长期资产。</p>
    </div>

    <form class="goal-box" @submit.prevent="start">
      <textarea
        v-model="goal"
        rows="4"
        autofocus
        placeholder="例如：分析这些资料，找出三个最值得行动的机会，并形成一份可以分享的报告。"
        aria-label="告诉 JoyClaw 你想完成什么"
        @keydown.meta.enter.prevent="start"
        @keydown.ctrl.enter.prevent="start"
      />
      <div class="goal-actions">
        <div class="input-hints"><span>私人执行</span><span>⌘ Enter 提交</span></div>
        <button class="primary-button" type="submit" :disabled="!goal.trim() || submitting">
          {{ submitting ? '正在创建…' : '开始执行' }} <span>→</span>
        </button>
      </div>
      <p v-if="error" class="error-message">{{ error }}</p>
    </form>

    <div class="example-grid" aria-label="常用目标">
      <button v-for="item in examples" :key="item" type="button" @click="goal = item">
        <span>✦</span>{{ item }}
      </button>
    </div>
  </section>

  <section v-if="recent.length" class="section-block">
    <div class="section-heading">
      <div><span class="eyebrow">RECENT</span><h2>继续最近的事情</h2></div>
      <RouterLink to="/activity">查看全部 →</RouterLink>
    </div>
    <div class="run-grid">
      <RouterLink v-for="run in recent" :key="run.run_id" :to="`/runs/${run.run_id}`" class="run-card">
        <div class="card-topline">
          <span class="status-pill" :class="{ active: isActiveRun(run), failed: run.status === 'failed' }">{{ statusLabel(run.status) }}</span>
          <time>{{ dateLabel(run.updated_at || run.created_at) }}</time>
        </div>
        <strong>{{ run.prompt || run.status_summary || '未命名任务' }}</strong>
        <p>{{ run.status_summary || run.result?.content || '正在准备执行…' }}</p>
      </RouterLink>
    </div>
  </section>
</template>
