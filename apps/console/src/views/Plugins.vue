<template>
  <div class="page plugin-catalog-page">
    <header class="page-heading">
      <div><span class="eyebrow">EXTENSION CONTROL PLANE</span><h1>扩展中心</h1><p>目录负责发现，部署 allowlist 决定可加载边界，PostgreSQL 保存期望启停；只有 Worker 加载确认后才真正生效。</p></div>
      <div class="heading-actions"><button class="secondary-button" :disabled="loading" @click="scan">{{ loading ? '扫描中…' : '扫描扩展目录' }}</button></div>
    </header>

    <div v-if="error" class="notice error-notice">{{ error }}</div>
    <div v-if="!consoleActivationAllowed" class="notice policy-notice">当前部署禁止控制台启停。设置 <code>extensions.allowConsoleActivation=true</code> 后才可操作；目录仍可安全浏览。</div>

    <section v-if="extensions.length" class="catalog-summary panel">
      <div><strong>{{ extensions.length }}</strong><span>已发现</span></div><div><strong>{{ installedCount }}</strong><span>已安装</span></div><div><strong>{{ desiredCount }}</strong><span>期望启用</span></div><div><strong>{{ activeCount }}</strong><span>实际生效</span></div>
    </section>

    <section v-if="extensions.length" class="plugin-catalog">
      <article v-for="item in extensions" :key="item.extension_id" class="panel plugin-catalog-card">
        <div class="plugin-card-heading"><span class="plugin-mark">{{ initial(item.name) }}</span><span class="status-badge" :class="item.state">{{ stateLabel(item.state) }}</span></div>
        <span class="eyebrow">{{ item.extension_id }}</span>
        <h2>{{ item.name }}</h2>
        <p>{{ item.description || '这个扩展未提供描述。' }}</p>
        <div class="state-track">
          <span :class="{ done: item.source_available }"><i/>可用</span><span :class="{ done: item.installed }"><i/>已安装</span><span :class="{ done: item.desired_active }"><i/>期望启用</span><span :class="{ done: item.effective_active }"><i/>实际生效</span>
        </div>
        <div class="plugin-card-metrics"><span><strong>{{ item.extension_types.join(' · ') || 'unknown' }}</strong>类型</span><span><strong>{{ item.worker_summary.loaded }}/{{ item.worker_summary.total }}</strong>Worker</span><span><strong>{{ item.release ? `v${item.release.version}` : '—' }}</strong>发布版本</span></div>
        <ul v-if="visibleBlockers(item).length" class="blockers"><li v-for="blocker in visibleBlockers(item)" :key="blocker">{{ blocker }}</li></ul>
        <footer>
          <router-link v-if="item.release" class="detail-link" :to="`/extensions/${encodeURIComponent(item.extension_id)}`">查看详情</router-link><code v-else>{{ item.installed ? '等待发现发布单元' : '等待安装' }}</code>
          <button v-if="item.desired_active" class="secondary-button danger-text" :disabled="!consoleActivationAllowed || busyId === item.extension_id" @click="deactivate(item)">{{ busyId === item.extension_id ? '处理中…' : item.effective_active ? '停用' : '取消期望' }}</button>
          <button v-if="!item.effective_active" class="primary-button" :disabled="!canActivate(item) || busyId === item.extension_id" @click="activate(item)">{{ busyId === item.extension_id ? '处理中…' : item.desired_active ? '继续激活' : '激活' }}</button>
        </footer>
      </article>
    </section>
    <section v-else-if="!loading" class="panel empty-state"><span>◇</span><strong>扩展目录为空</strong><p>在 extensions.catalogDirectories 中配置目录，或安装带 Porthouse entry point 的 wheel。</p></section>
    <section v-else class="panel empty-state"><span>…</span><strong>正在读取扩展目录</strong></section>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { activateExtension, deactivateExtension, listExtensionInventory, scanExtensionInventory, type ExtensionInventoryItem } from '../api/plugins'

const extensions = ref<ExtensionInventoryItem[]>([])
const consoleActivationAllowed = ref(false)
const loading = ref(false)
const busyId = ref('')
const error = ref('')
const installedCount = computed(() => extensions.value.filter((item) => item.installed).length)
const desiredCount = computed(() => extensions.value.filter((item) => item.desired_active).length)
const activeCount = computed(() => extensions.value.filter((item) => item.effective_active).length)

function initial(value: string) { return value.trim().replace(/^porthouse-/i, '').slice(0, 1).toUpperCase() || 'X' }
function stateLabel(value: ExtensionInventoryItem['state']) { return ({ active: '已生效', activating: '生效中', installed: '已安装', available: '可安装', unavailable: '不可用' })[value] }
function visibleBlockers(item: ExtensionInventoryItem) { return item.activation_blockers.slice(0, 2) }
function canActivate(item: ExtensionInventoryItem) { return consoleActivationAllowed.value && item.installed && item.deployment_allowed && !!item.release && !item.metadata.source_conflict }
function replace(value: ExtensionInventoryItem) { const index = extensions.value.findIndex((item) => item.extension_id === value.extension_id); if (index >= 0) extensions.value[index] = value }
async function load() { loading.value = true; error.value = ''; try { const value = await listExtensionInventory(); extensions.value = value.items; consoleActivationAllowed.value = value.console_activation_allowed } catch (cause) { error.value = cause instanceof Error ? cause.message : '读取扩展目录失败' } finally { loading.value = false } }
async function scan() { loading.value = true; error.value = ''; try { const value = await scanExtensionInventory(); extensions.value = value.items } catch (cause) { error.value = cause instanceof Error ? cause.message : '扫描扩展目录失败' } finally { loading.value = false } }
async function activate(item: ExtensionInventoryItem) { busyId.value = item.extension_id; error.value = ''; try { replace(await activateExtension(item.extension_id)) } catch (cause) { error.value = cause instanceof Error ? cause.message : '激活扩展失败' } finally { busyId.value = '' } }
async function deactivate(item: ExtensionInventoryItem) { busyId.value = item.extension_id; error.value = ''; try { replace(await deactivateExtension(item.extension_id)) } catch (cause) { error.value = cause instanceof Error ? cause.message : '停用扩展失败' } finally { busyId.value = '' } }
onMounted(load)
</script>

<style scoped>
.plugin-catalog-page{display:flex;flex-direction:column}.policy-notice{color:var(--warning);border-color:color-mix(in srgb,var(--warning) 35%,var(--border));background:color-mix(in srgb,var(--warning) 7%,var(--surface))}.policy-notice code{font-size:10px}.catalog-summary{display:grid;grid-template-columns:repeat(4,1fr);margin-bottom:16px;padding:16px 20px}.catalog-summary div{display:grid;gap:3px;border-right:1px solid var(--border)}.catalog-summary div:last-child{border:0}.catalog-summary strong{color:var(--text-strong);font:600 22px var(--font-mono)}.catalog-summary span{color:var(--text-muted);font-size:10px}.plugin-catalog{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:16px}.plugin-catalog-card{display:flex;min-height:370px;flex-direction:column;padding:22px}.plugin-card-heading{display:flex;align-items:center;justify-content:space-between;margin-bottom:20px}.plugin-mark{display:grid;width:42px;height:42px;place-items:center;color:#fff;background:var(--accent-strong);border-radius:12px;font:600 16px var(--font-mono)}.status-badge.active{color:var(--success)}.status-badge.activating{color:var(--warning)}.status-badge.available{color:var(--text-muted)}.plugin-catalog-card h2{margin:8px 0;color:var(--text-strong);font-size:19px}.plugin-catalog-card>p{min-height:40px;margin:0;color:var(--text-muted);font-size:12px;line-height:1.6}.state-track{display:grid;grid-template-columns:repeat(4,1fr);gap:3px;margin-top:17px}.state-track span{display:grid;justify-items:center;gap:5px;color:var(--text-muted);font-size:8px;text-align:center}.state-track i{width:100%;height:3px;background:var(--border-strong);border-radius:4px}.state-track span.done{color:var(--text)}.state-track span.done i{background:var(--success)}.plugin-card-metrics{display:grid;grid-template-columns:repeat(3,1fr);gap:8px;margin:16px 0 10px;padding:12px 0;border-block:1px solid var(--border)}.plugin-card-metrics span{display:flex;min-width:0;flex-direction:column;color:var(--text-muted);font-size:9px}.plugin-card-metrics strong{overflow:hidden;color:var(--text-strong);font:600 11px var(--font-mono);text-overflow:ellipsis;white-space:nowrap}.blockers{display:grid;gap:3px;margin:0 0 12px;padding-left:16px;color:var(--warning);font-size:9px}.plugin-catalog-card footer{display:flex;align-items:center;gap:8px;margin-top:auto}.plugin-catalog-card footer .primary-button,.plugin-catalog-card footer .secondary-button{margin-left:auto}.detail-link{color:var(--accent);font-size:11px;text-decoration:none}.plugin-catalog-card footer code{color:var(--text-muted);font-size:9px}@media(max-width:1100px){.plugin-catalog{grid-template-columns:repeat(2,minmax(0,1fr))}}@media(max-width:680px){.plugin-catalog,.catalog-summary{grid-template-columns:1fr}.catalog-summary div{padding:8px 0;border-right:0;border-bottom:1px solid var(--border)}}
</style>
