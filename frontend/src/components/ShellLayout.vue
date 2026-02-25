<template>
  <div class="shell" :class="{ 'nav-collapsed': collapsed }">
    <header class="topbar">
      <div class="topbar-left">
        <div class="brand">
          <div class="brand-logo">
            <img :src="logoSrc" alt="Joyhousebot" />
          </div>
          <div class="brand-text">
            <span class="brand-title">Joyhousebot</span>
            <span class="brand-sub">配置</span>
          </div>
        </div>
      </div>
      <div class="topbar-right">
        <n-tag :type="gateway.connected ? 'success' : 'error'" size="small" round class="topbar-ws-status">
          {{ gateway.connected ? 'WS 已连接' : 'WS 未连接' }}
        </n-tag>
        <n-button quaternary circle size="small" @click="toggleTheme" :title="theme === 'dark' ? '切换到浅色' : '切换到深色'">
          <template #icon>
            <span class="theme-icon" v-html="theme === 'dark' ? sunSvg : moonSvg" />
          </template>
        </n-button>
      </div>
    </header>
    <div class="shell-nav-wrap">
      <aside class="shell-nav">
        <div class="nav-group">
          <div class="nav-label">对话</div>
          <nav class="nav-group__items">
            <router-link to="/chat" class="nav-item" :class="{ active: activeKey === 'chat' }" title="对话">
              <span class="nav-item-icon" v-html="iconChat" />
              <span class="nav-item-text">对话</span>
            </router-link>
          </nav>
        </div>
        <div class="nav-group nav-group--collapsible">
          <button
            type="button"
            class="nav-group-head"
            :class="{ expanded: controlExpanded, active: isControlSectionActive }"
            :title="controlExpanded ? '收起控制菜单' : '展开控制菜单'"
            @click="toggleControlExpanded"
          >
            <span class="nav-group-head-icon" v-html="iconControl" />
            <span class="nav-group-head-label">控制</span>
            <span class="nav-group-head-chevron" v-html="controlExpanded ? iconChevronDown : iconChevronRight" />
          </button>
          <nav v-show="controlExpanded" class="nav-group__items">
            <router-link to="/control/overview" class="nav-item" :class="{ active: controlSection === 'overview' }" title="概览">
              <span class="nav-item-icon" v-html="iconControl" />
              <span class="nav-item-text">概览</span>
            </router-link>
            <router-link to="/control/channels" class="nav-item" :class="{ active: controlSection === 'channels' }" title="通道状态">
              <span class="nav-item-icon" v-html="iconChannels" />
              <span class="nav-item-text">通道状态</span>
            </router-link>
            <router-link to="/control/queue" class="nav-item" :class="{ active: controlSection === 'queue' }" title="队列">
              <span class="nav-item-icon" v-html="iconQueue" />
              <span class="nav-item-text">队列</span>
            </router-link>
            <router-link to="/control/traces" class="nav-item" :class="{ active: controlSection === 'traces' }" title="轨迹">
              <span class="nav-item-icon" v-html="iconTraces" />
              <span class="nav-item-text">轨迹</span>
            </router-link>
            <router-link to="/control/instances" class="nav-item" :class="{ active: controlSection === 'instances' }" title="实例">
              <span class="nav-item-icon" v-html="iconInstances" />
              <span class="nav-item-text">实例</span>
            </router-link>
            <router-link to="/control/cron" class="nav-item" :class="{ active: controlSection === 'cron' }" title="定时任务">
              <span class="nav-item-icon" v-html="iconCron" />
              <span class="nav-item-text">定时任务</span>
            </router-link>
            <router-link to="/control/usage" class="nav-item" :class="{ active: controlSection === 'usage' }" title="用量统计">
              <span class="nav-item-icon" v-html="iconTraces" />
              <span class="nav-item-text">用量</span>
            </router-link>
            <router-link to="/control/devices" class="nav-item" :class="{ active: controlSection === 'devices' }" title="设备配对">
              <span class="nav-item-icon" v-html="iconControl" />
              <span class="nav-item-text">设备配对</span>
            </router-link>
            <router-link to="/control/approvals" class="nav-item" :class="{ active: controlSection === 'approvals' }" title="执行审批">
              <span class="nav-item-icon" v-html="iconQueue" />
              <span class="nav-item-text">执行审批</span>
            </router-link>
            <router-link to="/control/sandbox" class="nav-item" :class="{ active: controlSection === 'sandbox' }" title="沙箱">
              <span class="nav-item-icon" v-html="iconSandbox" />
              <span class="nav-item-text">沙箱</span>
            </router-link>
            <router-link to="/agent" class="nav-item" :class="{ active: activeKey === 'agent' }" title="Agents">
              <span class="nav-item-icon" v-html="iconAgent" />
              <span class="nav-item-text">Agents</span>
            </router-link>
            <router-link to="/skills" class="nav-item" :class="{ active: activeKey === 'skills' }" title="技能">
              <span class="nav-item-icon" v-html="iconSkills" />
              <span class="nav-item-text">技能</span>
            </router-link>
            <router-link to="/app" class="nav-item" :class="{ active: activeKey === 'app' }" title="应用">
              <span class="nav-item-icon" v-html="iconApp" />
              <span class="nav-item-text">应用</span>
            </router-link>
            <router-link to="/config" class="nav-item" :class="{ active: activeKey === 'config' }" title="配置">
              <span class="nav-item-icon" v-html="iconConfig" />
              <span class="nav-item-text">配置</span>
            </router-link>
          </nav>
        </div>
      </aside>
      <div class="nav-trigger" :title="collapsed ? '展开菜单' : '收起菜单'" @click="toggleCollapsed">
        <n-button quaternary circle size="small" class="nav-trigger-btn">
          <template #icon>
            <span class="collapse-icon" v-html="collapsed ? iconChevronRight : iconChevronLeft" />
          </template>
        </n-button>
      </div>
    </div>
    <main class="shell-content">
      <router-view />
    </main>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, watch, onMounted, provide } from 'vue'
import { useRoute } from 'vue-router'
import { NButton, NTag } from 'naive-ui'
import { useGateway, GatewayKey } from '../composables/useGateway'

const gateway = useGateway()
provide(GatewayKey, gateway)

const sunSvg = '<svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M4.93 4.93l1.41 1.41M17.66 17.66l1.41 1.41M2 12h2M20 12h2M6.34 17.66l-1.41 1.41M19.07 4.93l-1.41 1.41"/></svg>'
const moonSvg = '<svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><path d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z"/></svg>'

const iconChat = '<svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/></svg>'
const iconWorkspace = '<svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><polyline points="16 18 22 12 16 6"/><polyline points="8 6 2 12 8 18"/></svg>'
const iconControl = '<svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="3" width="7" height="7"/><rect x="14" y="3" width="7" height="7"/><rect x="14" y="14" width="7" height="7"/><rect x="3" y="14" width="7" height="7"/></svg>'
const iconConfig = '<svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1 0 2.83 2 2 0 0 1-2.83 0l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-2 2 2 2 0 0 1-2-2v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 0 1-2.83 0 2 2 0 0 1 0-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1-2-2 2 2 0 0 1 2-2h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 0 1 0-2.83 2 2 0 0 1 2.83 0l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 2-2 2 2 0 0 1 2 2v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 0 1 2.83 0 2 2 0 0 1 0 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 2 2 2 2 0 0 1-2 2h-.09a1.65 1.65 0 0 0-1.51 1z"/></svg>'
const iconInstances = '<svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><rect x="2" y="2" width="8" height="8" rx="1"/><rect x="14" y="2" width="8" height="8" rx="1"/><rect x="2" y="14" width="8" height="8" rx="1"/><rect x="14" y="14" width="8" height="8" rx="1"/></svg>'
const iconChannels = '<svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/></svg>'
const iconQueue = '<svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><line x1="8" y1="6" x2="21" y2="6"/><line x1="8" y1="12" x2="21" y2="12"/><line x1="8" y1="18" x2="21" y2="18"/><line x1="3" y1="6" x2="3.01" y2="6"/><line x1="3" y1="12" x2="3.01" y2="12"/><line x1="3" y1="18" x2="3.01" y2="18"/></svg>'
const iconTraces = '<svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/><line x1="16" y1="13" x2="8" y2="13"/><line x1="16" y1="17" x2="8" y2="17"/><polyline points="10 9 9 9 8 9"/></svg>'
const iconCron = '<svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><polyline points="12 6 12 12 16 14"/></svg>'
const iconSandbox = '<svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M21 16V8a2 2 0 0 0-1-1.73l-7-4a2 2 0 0 0-2 0l-7 4A2 2 0 0 0 3 8v8a2 2 0 0 0 1 1.73l7 4a2 2 0 0 0 2 0l7-4A2 2 0 0 0 21 16z"/><polyline points="3.27 6.96 12 12.01 20.73 6.96"/><line x1="12" y1="22.08" x2="12" y2="12"/></svg>'
const iconAgent = '<svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M12 8V4H8"/><rect x="2" y="14" width="8" height="8" rx="1"/><path d="M12 8h4a2 2 0 0 1 2 2v10"/><path d="M12 8v12"/></svg>'
const iconSkills = '<svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M14.7 6.3a1 1 0 0 0 0 1.4l1.6 1.6a1 1 0 0 0 1.4 0l3.77-3.77a6 6 0 0 1-7.94 7.94l-6.91 6.91a2.12 2.12 0 0 1-3-3l6.91-6.91a6 6 0 0 1 7.94-7.94l-3.76 3.76z"/></svg>'
const iconApp = '<svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="3" width="7" height="7"/><rect x="14" y="3" width="7" height="7"/><rect x="14" y="14" width="7" height="7"/><rect x="3" y="14" width="7" height="7"/></svg>'
const iconChevronLeft = '<svg xmlns="http://www.w3.org/2000/svg" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="15 18 9 12 15 6"/></svg>'
const iconChevronRight = '<svg xmlns="http://www.w3.org/2000/svg" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="9 18 15 12 9 6"/></svg>'
const iconChevronDown = '<svg xmlns="http://www.w3.org/2000/svg" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="6 9 12 15 18 9"/></svg>'

const route = useRoute()
const collapsed = ref(false)
const STORAGE_KEY = 'joyhousebot-ui-nav-collapsed'
const CONTROL_EXPANDED_KEY = 'joyhousebot-ui-control-expanded'
const controlExpanded = ref(true)

function loadCollapsed() {
  try {
    const v = localStorage.getItem(STORAGE_KEY)
    collapsed.value = v === '1'
  } catch (_) {}
}
function saveCollapsed() {
  try {
    localStorage.setItem(STORAGE_KEY, collapsed.value ? '1' : '0')
  } catch (_) {}
}
function toggleCollapsed() {
  collapsed.value = !collapsed.value
  saveCollapsed()
}
function loadControlExpanded() {
  try {
    const v = localStorage.getItem(CONTROL_EXPANDED_KEY)
    if (v !== null) controlExpanded.value = v === '1'
  } catch (_) {}
}
function saveControlExpanded() {
  try {
    localStorage.setItem(CONTROL_EXPANDED_KEY, controlExpanded.value ? '1' : '0')
  } catch (_) {}
}
function toggleControlExpanded() {
  controlExpanded.value = !controlExpanded.value
  saveControlExpanded()
}
onMounted(() => {
  loadCollapsed()
  loadControlExpanded()
})
const activeKey = computed(() => {
  const p = route.path.replace(/\/$/, '') || ''
  if (p === '/chat') return 'chat'
  if (p === '/workspace') return 'workspace'
  if (p.startsWith('/control')) return 'control'
  if (p === '/agent') return 'agent'
  if (p === '/skills') return 'skills'
  if (p.startsWith('/app')) return 'app'
  if (p === '/config') return 'config'
  return 'chat'
})
const isControlSectionActive = computed(() => {
  const k = activeKey.value
  return k === 'control' || k === 'agent' || k === 'skills' || k === 'app' || k === 'config'
})
const controlSection = computed(() => {
  const p = route.path.replace(/\/$/, '') || ''
  if (p === '/control/overview') return 'overview'
  if (p === '/control/channels') return 'channels'
  if (p === '/control/queue') return 'queue'
  if (p === '/control/traces') return 'traces'
  if (p === '/control/instances') return 'instances'
  if (p.startsWith('/control/cron')) return 'cron'
  if (p === '/control/usage') return 'usage'
  if (p === '/control/devices') return 'devices'
  if (p === '/control/approvals') return 'approvals'
  if (p === '/control/sandbox') return 'sandbox'
  return ''
})

type Theme = 'dark' | 'light'
const theme = ref<Theme>((() => {
  if (typeof document === 'undefined') return 'dark'
  const t = document.documentElement.getAttribute('data-theme') as Theme | null
  if (t === 'light' || t === 'dark') return t
  try {
    const stored = localStorage.getItem('joyhousebot-ui-theme') as Theme | null
    if (stored === 'light' || stored === 'dark') return stored
  } catch (_) {}
  return window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light'
})())

function applyTheme(t: Theme) {
  document.documentElement.setAttribute('data-theme', t)
  try { localStorage.setItem('joyhousebot-ui-theme', t) } catch (_) {}
}

function toggleTheme() {
  theme.value = theme.value === 'dark' ? 'light' : 'dark'
  applyTheme(theme.value)
}

watch(theme, applyTheme, { immediate: true })

const logoSrc = import.meta.env.BASE_URL + 'joyhouse.png'
</script>

<style scoped>
.topbar-right {
  display: flex;
  align-items: center;
  gap: 10px;
}
.topbar-ws-status {
  flex-shrink: 0;
}
.theme-icon {
  display: inline-flex;
  align-items: center;
  justify-content: center;
}
.theme-icon :deep(svg) {
  width: 16px;
  height: 16px;
}
.nav-item-icon {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
  color: inherit;
}
.nav-item-icon :deep(svg) {
  width: 20px;
  height: 20px;
}
.nav-item.router-link-active,
.nav-item.active {
  color: var(--text-strong);
  background: var(--accent-subtle);
}
.nav-trigger {
  position: absolute;
  right: 0;
  top: 50%;
  transform: translate(50%, -50%);
  z-index: 10;
}
.nav-trigger-btn {
  background: var(--card) !important;
  border: 1px solid var(--border);
  color: var(--text);
  box-shadow: 0 1px 3px rgba(0, 0, 0, 0.12);
}
.nav-trigger-btn:hover {
  background: var(--bg-hover) !important;
  color: var(--text-strong);
}
.collapse-icon {
  display: inline-flex;
  align-items: center;
  justify-content: center;
}
.collapse-icon :deep(svg) {
  width: 18px;
  height: 18px;
}
.nav-group--collapsible .nav-group-head {
  display: flex;
  align-items: center;
  gap: 8px;
  width: 100%;
  padding: 0.4rem 0.75rem;
  border: none;
  border-radius: 6px;
  background: transparent;
  color: var(--text-color);
  font-size: inherit;
  cursor: pointer;
  text-align: left;
}
.nav-group--collapsible .nav-group-head:hover {
  background: var(--bg-hover);
}
.nav-group--collapsible .nav-group-head.active {
  color: var(--text-strong);
  background: var(--accent-subtle);
}
.nav-group-head-icon {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
}
.nav-group-head-icon :deep(svg) {
  width: 20px;
  height: 20px;
}
.nav-group-head-label {
  flex: 1;
}
.nav-group-head-chevron {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
  opacity: 0.7;
}
.nav-group-head-chevron :deep(svg) {
  width: 16px;
  height: 16px;
}
.nav-group--collapsible .nav-group__items {
  padding-left: 0.25rem;
}
</style>
