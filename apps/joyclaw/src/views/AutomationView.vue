<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { consoleUrl, listSchedules, type ScheduleSummary } from '../api'
import { scheduleLabel } from '../presentation'

const schedules = ref<ScheduleSummary[]>([])
const loading = ref(true)
const error = ref('')
async function load() { loading.value = true; try { schedules.value = await listSchedules(); error.value = '' } catch (cause) { error.value = cause instanceof Error ? cause.message : '读取自动化失败' } finally { loading.value = false } }
onMounted(load)
</script>

<template>
  <section class="page-header"><div><span class="eyebrow">AUTOMATION</span><h1>让重要的事情持续发生</h1><p>定时整理、持续关注和外部事件都进入同一条可靠执行链路。</p></div><a :href="consoleUrl('/automation/tasks')" class="secondary-button">管理自动化</a></section>
  <div class="automation-prompt"><span>↻</span><div><strong>用一句话创建自动化</strong><p>例如：“每天晚上九点总结今天的工作，并形成明日计划。”</p></div><RouterLink to="/?intent=automation" class="primary-button">告诉 JoyClaw →</RouterLink></div>
  <p v-if="error" class="error-message">{{ error }}</p>
  <div v-if="loading" class="empty-state">正在读取自动化任务…</div>
  <div v-else-if="!schedules.length" class="empty-state"><span>↻</span><strong>还没有自动运行的事情</strong><p>你可以通过自然语言提出需求，或进入高级控制台精确配置。</p><a :href="consoleUrl('/automation/tasks')" class="secondary-button">创建第一条自动化</a></div>
  <div v-else class="schedule-list">
    <article v-for="item in schedules" :key="item.id" class="schedule-row"><span class="schedule-icon">↻</span><div><strong>{{ item.name }}</strong><p>{{ item.payload.message }}</p></div><span class="schedule-rule">{{ scheduleLabel(item) }}</span><span class="status-pill" :class="{ active: item.enabled }">{{ item.enabled ? '运行中' : '已暂停' }}</span></article>
  </div>
</template>
