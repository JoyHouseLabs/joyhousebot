<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { consoleUrl, listWorks, type WorkSummary } from '../api'
import { dateLabel } from '../presentation'

const works = ref<WorkSummary[]>([])
const loading = ref(true)
const error = ref('')
async function load() { loading.value = true; try { works.value = await listWorks(); error.value = '' } catch (cause) { error.value = cause instanceof Error ? cause.message : '读取成果失败' } finally { loading.value = false } }
onMounted(load)
</script>

<template>
  <section class="page-header"><div><span class="eyebrow">YOUR WORKS</span><h1>真正属于你的成果</h1><p>执行结果经过筛选、验证和版本化后，成为可以持续改进与主动分享的作品。</p></div><a :href="consoleUrl('/works')" class="secondary-button">管理与分享</a></section>
  <p v-if="error" class="error-message">{{ error }}</p>
  <div v-if="loading" class="empty-state">正在读取成果…</div>
  <div v-else-if="!works.length" class="empty-state"><span>◆</span><strong>成果库还是空的</strong><p>先完成一件事，再把有价值的结果沉淀为 Work。</p><RouterLink to="/" class="primary-button">开始一件事</RouterLink></div>
  <div v-else class="work-grid">
    <article v-for="work in works" :key="work.work_id" class="work-card">
      <div class="work-cover"><span>◆</span><small>VERSION {{ work.current_version }}</small></div>
      <div class="work-content"><div class="card-topline"><span class="status-pill">{{ work.status === 'published' ? '已发布' : work.status === 'archived' ? '已归档' : '草稿' }}</span><time>{{ dateLabel(work.updated_at) }}</time></div><h2>{{ work.title }}</h2><p>{{ work.description || '持续积累和打磨这项成果。' }}</p><a :href="consoleUrl(`/works`)">查看与分享 →</a></div>
    </article>
  </div>
</template>
