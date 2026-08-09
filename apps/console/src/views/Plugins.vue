<template>
  <div class="page plugin-catalog-page">
    <header class="page-heading">
      <div><span class="eyebrow">EXTENSIBLE INTELLIGENCE</span><h1>插件中心</h1><p>安装和查看扩展能力；业务场景由插件拥有，核心 Runtime 保持通用。</p></div>
      <div class="heading-actions"><button class="secondary-button" :disabled="loading" @click="load">{{ loading ? '刷新中…' : '刷新目录' }}</button></div>
    </header>
    <div v-if="error" class="notice error-notice">{{ error }}</div>
    <section v-if="plugins.length" class="plugin-catalog">
      <router-link v-for="item in plugins" :key="item.plugin_id" class="panel plugin-catalog-card" :to="`/plugins/${encodeURIComponent(item.plugin_id)}`">
        <div class="plugin-card-heading"><span class="plugin-mark">{{ initial(item.name) }}</span><span class="status-badge" :class="item.status">{{ item.status }}</span></div>
        <span class="eyebrow">{{ item.plugin_id }}</span>
        <h2>{{ item.name }}</h2>
        <p>{{ item.description || '这个插件未提供描述。' }}</p>
        <div class="plugin-card-metrics"><span><strong>{{ item.component_count }}</strong>组件</span><span><strong>{{ item.manifest.quickstarts?.length || 0 }}</strong>Quickstarts</span><span><strong>{{ percent(item.metrics.success_rate) }}</strong>成功率</span></div>
        <footer><code>v{{ item.version }}</code><span>查看插件 →</span></footer>
      </router-link>
    </section>
    <section v-else-if="!loading" class="panel empty-state"><span>◇</span><strong>尚未安装插件</strong><p>安装插件包并启动 Worker 后，发布单元会显示在这里。</p></section>
    <section v-else class="panel empty-state"><span>…</span><strong>正在读取插件目录</strong></section>
  </div>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { listPlugins, type PluginListItem } from '../api/plugins'

const plugins = ref<PluginListItem[]>([])
const loading = ref(false)
const error = ref('')

function initial(value: string) { return value.trim().slice(0, 1).toUpperCase() || 'P' }
function percent(value: number) { return `${Math.round(Number(value || 0) * 100)}%` }
async function load() {
  loading.value = true
  error.value = ''
  try { plugins.value = (await listPlugins()).items }
  catch (cause) { error.value = cause instanceof Error ? cause.message : '读取插件目录失败' }
  finally { loading.value = false }
}

onMounted(load)
</script>

<style scoped>
.plugin-catalog-page{display:flex;flex-direction:column}.plugin-catalog{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:16px}.plugin-catalog-card{display:flex;min-height:280px;flex-direction:column;padding:22px;transition:.18s ease}.plugin-catalog-card:hover{transform:translateY(-2px);border-color:var(--accent-border);box-shadow:var(--shadow)}.plugin-card-heading{display:flex;align-items:center;justify-content:space-between;margin-bottom:20px}.plugin-mark{display:grid;width:42px;height:42px;place-items:center;color:#fff;background:var(--accent-strong);border-radius:12px;font:600 16px var(--font-mono)}.plugin-catalog-card h2{margin:8px 0;color:var(--text-strong);font-size:19px}.plugin-catalog-card>p{min-height:54px;margin:0;color:var(--text-muted);font-size:12px;line-height:1.6}.plugin-card-metrics{display:grid;grid-template-columns:repeat(3,1fr);gap:8px;margin:20px 0;padding:12px 0;border-block:1px solid var(--border)}.plugin-card-metrics span{display:flex;flex-direction:column;color:var(--text-muted);font-size:9px}.plugin-card-metrics strong{color:var(--text-strong);font:600 15px var(--font-mono)}.plugin-catalog-card footer{display:flex;align-items:center;justify-content:space-between;margin-top:auto}.plugin-catalog-card footer code{color:var(--text-muted);font-size:10px}.plugin-catalog-card footer span{color:var(--accent);font-size:11px}@media(max-width:1100px){.plugin-catalog{grid-template-columns:repeat(2,minmax(0,1fr))}}@media(max-width:680px){.plugin-catalog{grid-template-columns:1fr}}
</style>
