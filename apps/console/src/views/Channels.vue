<template>
  <div class="page channels-page">
    <header class="page-heading">
      <div><span class="eyebrow">CHANNEL EXTENSIONS</span><h1>Channel 扩展</h1><p>Core 只负责 Run、Outbox、租约、重试与审计；外部消息协议由可独立安装的扩展提供。</p></div>
      <button class="secondary-button" type="button" :disabled="loading" @click="load">{{ loading ? '刷新中…' : '刷新状态' }}</button>
    </header>
    <div v-if="error" class="notice error-notice">{{ error }}</div>
    <div v-if="!loading && !channels.length" class="empty-state"><strong>没有安装 Channel 扩展</strong><span>安装独立扩展 wheel，将 ID 加入 extensions.allowedIds，再到扩展中心激活。</span></div>
    <section class="channel-grid">
      <article v-for="channel in channels" :key="channel.id" class="panel channel-card" :class="{ enabled: channel.enabled }">
        <div class="channel-card-heading"><div><span class="channel-icon">{{ channel.icon }}</span><div><h2>{{ channel.name }}</h2><small>{{ channel.extensionId }}</small></div></div><span class="status-badge" :class="channel.enabled ? 'completed' : channel.desired ? 'running' : 'cancelled'">{{ channel.enabled ? '已生效' : channel.desired ? '生效中' : '未启用' }}</span></div>
        <p>{{ channel.distribution || '独立 Channel distribution' }} · {{ channel.version || '版本未知' }}</p>
        <div class="channel-meta"><span><b>发现</b>仅读取 package metadata</span><span><b>部署准入</b>{{ channel.allowed ? '允许 Channel Worker 加载' : '不在 allowlist' }}</span><span><b>期望状态</b>{{ channel.desired ? 'active' : 'inactive' }}</span></div>
        <div class="channel-footer"><span>{{ channel.enabled ? '由 Channel Worker 通过 PG Lease 接管' : '到扩展中心完成部署准入与激活' }}</span><code>{{ channel.extensionId }}</code></div>
      </article>
    </section>
    <section class="panel channel-boundary"><div class="panel-heading"><div><span class="eyebrow">RUNTIME BOUNDARY</span><h2>扩展不是第二套 Runtime</h2></div></div><div class="boundary-flow"><span>Channel Extension</span><b>→</b><span>RunAdapter</span><b>→</b><span>PostgreSQL Run / Task</span><b>→</b><span>Coordinator / Worker</span></div><p>扩展只负责外部协议转换，不直接调用模型或维护执行队列。入站生成用户 Run，出站进入 PG Outbox，沿用统一重试、审计和多进程接管。</p></section>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { listExtensionInventory, type ExtensionInventoryItem } from '../api/extensions'

const loading = ref(false)
const error = ref('')
const inventory = ref<ExtensionInventoryItem[]>([])
const channels = computed(() => {
  return inventory.value.filter((item) => item.extension_types.includes('channel')).map((item) => {
    const extensionId = item.extension_id
    const transport = extensionId.replace(/^channel-/, '')
    return {
      id: transport,
      extensionId,
      name: transport.split('-').map((part) => part ? `${part[0].toUpperCase()}${part.slice(1)}` : '').join(' '),
      icon: transport.slice(0, 1).toUpperCase() || 'C',
      distribution: item.distribution_name,
      version: item.distribution_version || item.source_version,
      allowed: item.deployment_allowed,
      desired: item.desired_active,
      enabled: item.effective_active,
    }
  })
})

async function load() {
  loading.value = true; error.value = ''
  try { inventory.value = (await listExtensionInventory()).items }
  catch (cause) { error.value = cause instanceof Error ? cause.message : '读取 Channel 配置失败' }
  finally { loading.value = false }
}
onMounted(load)
</script>

<style scoped>
.channel-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 14px; }.channel-card { padding: 18px; }.channel-card.enabled { border-color: var(--accent-border); }.channel-card-heading { display: flex; align-items: flex-start; justify-content: space-between; gap: 12px; }.channel-card-heading>div { display: flex; gap: 10px; align-items: center; }.channel-card-heading h2 { margin: 0 0 3px; font-size: 16px; color: var(--text-strong); }.channel-card-heading small { color: var(--text-muted); font-family: var(--font-mono); font-size: 10px; }.channel-icon { display: grid; width: 34px; height: 34px; place-items: center; color: var(--accent); background: var(--accent-subtle); border-radius: 10px; font-weight: 700; }.channel-card p { min-height: 34px; margin: 15px 0; color: var(--text-muted); font-size: 12px; }.channel-meta { display: grid; gap: 7px; padding: 11px 0; border-top: 1px solid var(--border); border-bottom: 1px solid var(--border); color: var(--text-muted); font-size: 11px; }.channel-meta span { display: flex; justify-content: space-between; gap: 10px; }.channel-meta b { color: var(--text-strong); }.channel-footer { display: flex; justify-content: space-between; gap: 10px; margin-top: 12px; color: var(--text-muted); font-size: 10px; }.channel-footer code { color: var(--accent); white-space: nowrap; }.info-notice { display: flex; gap: 12px; color: var(--text-muted); background: var(--surface-raised); border: 1px solid var(--border); }.info-notice strong { color: var(--text-strong); white-space: nowrap; }.channel-boundary { margin-top: 16px; padding: 18px; }.boundary-flow { display: flex; flex-wrap: wrap; align-items: center; gap: 10px; color: var(--accent); font-size: 12px; }.boundary-flow span { padding: 8px 11px; background: var(--accent-subtle); border-radius: 8px; }.boundary-flow b { color: var(--text-muted); }.channel-boundary p { margin: 14px 0 0; color: var(--text-muted); font-size: 12px; line-height: 1.7; }
@media (max-width: 760px) { .channel-grid { grid-template-columns: 1fr; }.channel-footer { flex-direction: column; }.info-notice { flex-direction: column; gap: 5px; } }
</style>
