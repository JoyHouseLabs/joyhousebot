<template>
  <div class="app-shell" :class="{ 'chat-active': route.path === '/chat', 'sidebar-collapsed': sidebarCollapsed }">
    <aside class="app-sidebar">
      <router-link class="brand" to="/overview">
        <img :src="logoSrc" alt="Joyhousebot" />
        <div><strong>Joyhousebot</strong><span>Agent Cloud</span></div>
      </router-link>

      <nav class="main-nav" aria-label="主导航">
        <router-link v-for="item in navItems" :key="item.to" :to="item.to">
          <span class="nav-icon">{{ item.icon }}</span>
          <span><strong>{{ item.label }}</strong><small>{{ item.caption }}</small></span>
        </router-link>
        <div class="nav-config-group" :class="{ expanded: isConfigRoute }">
          <router-link class="nav-config-root" to="/agents">
            <span class="nav-icon">⚙</span><span><strong>配置</strong><small>平台、Agent、Skills 与 Tools</small></span>
          </router-link>
          <div v-show="isConfigRoute" class="nav-subnav">
            <router-link v-for="item in configItems" :key="item.to" :to="item.to"><span>{{ item.icon }}</span><strong>{{ item.label }}</strong></router-link>
          </div>
        </div>
      </nav>

      <div class="architecture-note">
        <span class="eyebrow">RUNTIME</span>
        <strong>PG-first distributed</strong>
        <p>API 提交 · Worker 执行<br />SSE 回放 · Lease 接管</p>
      </div>
    </aside>

    <section class="app-stage" :class="{ 'chat-active': route.path === '/chat' }">
      <header class="app-topbar">
        <div class="mobile-brand"><img :src="logoSrc" alt="" /><strong>Joyhousebot</strong></div>
        <button class="sidebar-toggle" type="button" :aria-label="sidebarCollapsed ? '展开侧栏' : '收起侧栏'" :title="sidebarCollapsed ? '展开侧栏' : '收起侧栏'" @click="toggleSidebar">
          {{ sidebarCollapsed ? '›' : '‹' }}
        </button>
        <div class="service-state" :class="healthClass">
          <span class="state-dot" />
          <span>{{ healthText }}</span>
        </div>
        <div class="topbar-spacer" />
        <div class="identity-chip" :title="`当前 user_id: ${userId}`">
          <span>{{ identityRole }}</span><code>{{ userId }}</code>
        </div>
        <button class="logout-button" type="button" @click="logout">退出</button>
        <button class="icon-button" type="button" @click="toggleTheme" :title="theme === 'dark' ? '浅色模式' : '深色模式'">
          {{ theme === 'dark' ? '☀' : '☾' }}
        </button>
      </header>
      <main class="app-content" :class="{ 'chat-active': route.path === '/chat' }"><router-view /></main>
      <nav class="mobile-nav" aria-label="移动导航">
        <router-link v-for="item in [...navItems, { to: '/agents', label: '配置', icon: '⚙' }]" :key="item.to" :to="item.to">
          <span>{{ item.icon }}</span><small>{{ item.label }}</small>
        </router-link>
      </nav>
    </section>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { clearControlToken } from '../api/http'
import { getServiceHealth } from '../api/monitoring'
import { getIdentity } from '../api/monitoring'
import { getRuntimeUserId } from '../api/identity'

const logoSrc = `${import.meta.env.BASE_URL}joyhouse.png`
const userId = getRuntimeUserId()
const route = useRoute()
const router = useRouter()
const navItems = [
  { to: '/overview', label: '监控概览', caption: '健康、用量与最近运行', icon: '◫' },
  { to: '/dinq/search', label: '人才搜索', caption: '条件引导、候选人与富化', icon: '⌕' },
  { to: '/runs', label: '运行中心', caption: 'Run、Task、日志与产物', icon: '◎' },
  { to: '/chat', label: 'Agent 试用', caption: '真实会话与执行时间线', icon: '✦' },
  { to: '/scenarios', label: '场景工作台', caption: '路由、追问 DAG 与能力', icon: '◇' },
  { to: '/plugins/dinq', label: 'Dinq 运维', caption: '插件、工具与执行链路', icon: '◈' },
]
const configItems = [
  { to: '/platform', label: '平台', icon: 'P' },
  { to: '/agents', label: 'Agent', icon: 'A' },
  { to: '/skills', label: 'Skills', icon: 'S' },
  { to: '/tools', label: 'Tools', icon: 'T' },
  { to: '/mcp', label: 'MCP Server', icon: 'M' },
  { to: '/channels', label: 'Channels', icon: 'C' },
]

type Theme = 'dark' | 'light'
const theme = ref<Theme>((localStorage.getItem('joyhousebot-ui-theme') as Theme) || 'dark')
const apiHealthy = ref(false)
const dbHealthy = ref(false)
const identityRole = ref('USER')
const sidebarCollapsed = ref(localStorage.getItem('joyhousebot-sidebar-collapsed') === '1')
let healthTimer: number | null = null

const healthClass = computed(() => ({ healthy: apiHealthy.value && dbHealthy.value, partial: apiHealthy.value && !dbHealthy.value }))
const healthText = computed(() => {
  if (apiHealthy.value && dbHealthy.value) return 'API / PostgreSQL 正常'
  if (apiHealthy.value) return 'API 正常 · 数据库未就绪'
  return '服务未连接'
})
const isConfigRoute = computed(() => ['/platform', '/agents', '/skills', '/tools', '/mcp', '/channels'].some((path) => route.path === path))

function applyTheme() {
  document.documentElement.setAttribute('data-theme', theme.value)
  localStorage.setItem('joyhousebot-ui-theme', theme.value)
}

function toggleTheme() {
  theme.value = theme.value === 'dark' ? 'light' : 'dark'
  applyTheme()
}

function toggleSidebar() {
  sidebarCollapsed.value = !sidebarCollapsed.value
  localStorage.setItem('joyhousebot-sidebar-collapsed', sidebarCollapsed.value ? '1' : '0')
}

function logout() {
  clearControlToken()
  localStorage.removeItem('joyhousebot_auth_session')
  void router.replace('/login')
}

async function refreshHealth() {
  const [result, identity] = await Promise.all([getServiceHealth(), getIdentity().catch(() => null)])
  apiHealthy.value = result.api
  dbHealthy.value = result.database
  identityRole.value = identity?.is_admin ? 'ADMIN' : (identity?.role || 'USER').toUpperCase()
}

onMounted(() => {
  applyTheme()
  void refreshHealth()
  healthTimer = window.setInterval(refreshHealth, 15_000)
})

onUnmounted(() => { if (healthTimer) window.clearInterval(healthTimer) })
</script>
