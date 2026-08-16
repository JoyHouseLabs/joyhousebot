<template>
  <div class="app-shell" :class="{ 'chat-active': route.path === '/chat', 'sidebar-collapsed': sidebarCollapsed }">
    <aside class="app-sidebar">
      <router-link class="brand" to="/work">
        <img :src="logoSrc" alt="Porthousebot" />
        <div><strong>Porthousebot</strong><span>AI Work Center</span></div>
      </router-link>

      <div class="sidebar-scroll">
        <nav class="main-nav" aria-label="主导航">
          <div v-for="center in consoleCenters" :key="center.id" class="nav-center-group" :class="{ expanded: activeCenter.id === center.id }">
            <router-link class="nav-center-root" :class="{ 'center-active': activeCenter.id === center.id }" :to="center.to" :title="sidebarCollapsed ? center.label : undefined">
              <span class="nav-icon">{{ center.icon }}</span><span><strong>{{ center.label }}</strong><small>{{ center.caption }}</small></span>
            </router-link>
            <div v-if="center.navItems.length" v-show="activeCenter.id === center.id" class="nav-subnav">
              <router-link v-for="item in center.navItems" :key="item.to" :to="item.to"><span>{{ item.icon }}</span><strong>{{ item.label }}</strong></router-link>
            </div>
          </div>
        </nav>

      </div>

      <details class="sidebar-account">
        <summary :title="`当前操作 user_id：${userId}`">
          <span class="account-avatar">{{ userInitial }}</span>
          <span class="account-copy"><strong>{{ userId }}</strong><small>{{ identityLabel }}</small></span>
          <span class="account-chevron">⌃</span>
        </summary>
        <div class="sidebar-account-menu">
          <div><small>当前操作 user_id</small><strong>{{ userId }}</strong><span>{{ identityLabel }}</span></div>
          <div v-if="isImpersonating"><small>管理员账号</small><strong>{{ actorUserId }}</strong><span>{{ identityRole }}</span></div>
          <router-link class="account-menu-link" to="/security">账户安全</router-link>
          <button type="button" @click="logout">退出系统</button>
        </div>
      </details>
    </aside>

    <section class="app-stage" :class="{ 'chat-active': route.path === '/chat' }">
      <header class="app-topbar">
        <div class="mobile-brand"><img :src="logoSrc" alt="" /><strong>Porthousebot</strong></div>
        <button class="sidebar-toggle" type="button" :aria-label="sidebarCollapsed ? '展开侧栏' : '收起侧栏'" :title="sidebarCollapsed ? '展开侧栏' : '收起侧栏'" @click="toggleSidebar">
          {{ sidebarCollapsed ? '›' : '‹' }}
        </button>
        <div class="service-state" :class="healthClass">
          <span class="state-dot" />
          <span>{{ healthText }}</span>
        </div>
        <div class="topbar-spacer" />
        <div class="topbar-account">
          <div class="identity-chip" :title="`当前操作 user_id: ${userId}`">
            <span>{{ isImpersonating ? 'WORKING AS' : identityRole }}</span><code>{{ userId }}</code>
          </div>
          <button class="logout-button" type="button" @click="logout">退出</button>
        </div>
        <button class="icon-button" type="button" @click="toggleTheme" :title="theme === 'dark' ? '浅色模式' : '深色模式'">
          {{ theme === 'dark' ? '☀' : '☾' }}
        </button>
      </header>
      <main class="app-content" :class="{ 'chat-active': route.path === '/chat' }"><router-view /></main>
      <nav class="mobile-nav" aria-label="移动导航">
        <router-link v-for="center in consoleCenters" :key="center.id" :to="center.to" :class="{ active: activeCenter.id === center.id }">
          <span>{{ center.icon }}</span><small>{{ center.mobileLabel }}</small>
        </router-link>
      </nav>
    </section>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { logoutAdmin } from '../api/auth'
import { clearControlToken } from '../api/http'
import { getServiceHealth } from '../api/monitoring'
import { getIdentity } from '../api/monitoring'
import { clearImpersonationTarget, getImpersonationTarget, getRuntimeUserId } from '../api/identity'
import { centerForPath, consoleCenters } from '../navigation/centers'

const logoSrc = `${import.meta.env.BASE_URL}porthouse.png`
const operatorUserId = getRuntimeUserId()
const resolvedUserId = ref(getImpersonationTarget() || operatorUserId)
const actorUserId = ref(operatorUserId)
const route = useRoute()
const router = useRouter()

type Theme = 'dark' | 'light'
const theme = ref<Theme>((localStorage.getItem('porthouse-ui-theme') as Theme) || 'dark')
const apiHealthy = ref(false)
const dbHealthy = ref(false)
const identityRole = ref('USER')
const sidebarCollapsed = ref(localStorage.getItem('porthouse-sidebar-collapsed') === '1')
let healthTimer: number | null = null

const activeCenter = computed(() => centerForPath(route.path))
const userId = computed(() => resolvedUserId.value)
const userInitial = computed(() => userId.value.slice(0, 1).toUpperCase() || 'U')
const isImpersonating = computed(() => actorUserId.value !== resolvedUserId.value)
const identityLabel = computed(() => isImpersonating.value ? `${identityRole.value} · 代操作` : identityRole.value)
const healthClass = computed(() => ({ healthy: apiHealthy.value && dbHealthy.value, partial: apiHealthy.value && !dbHealthy.value }))
const healthText = computed(() => {
  if (apiHealthy.value && dbHealthy.value) return 'API / PostgreSQL 正常'
  if (apiHealthy.value) return 'API 正常 · 数据库未就绪'
  return '服务未连接'
})
function applyTheme() {
  document.documentElement.setAttribute('data-theme', theme.value)
  localStorage.setItem('porthouse-ui-theme', theme.value)
}

function toggleTheme() {
  theme.value = theme.value === 'dark' ? 'light' : 'dark'
  applyTheme()
}

function toggleSidebar() {
  sidebarCollapsed.value = !sidebarCollapsed.value
  localStorage.setItem('porthouse-sidebar-collapsed', sidebarCollapsed.value ? '1' : '0')
}

async function logout() {
  await logoutAdmin().catch(() => undefined)
  clearControlToken()
  clearImpersonationTarget()
  localStorage.removeItem('porthouse_auth_session')
  void router.replace('/login')
}

async function refreshHealth() {
  const [result, identity] = await Promise.all([getServiceHealth(), getIdentity().catch(() => null)])
  apiHealthy.value = result.api
  dbHealthy.value = result.database
  identityRole.value = identity?.is_admin ? 'ADMIN' : (identity?.role || 'USER').toUpperCase()
  if (identity?.user_id) resolvedUserId.value = identity.user_id
  if (identity?.actor_user_id) actorUserId.value = identity.actor_user_id
}

onMounted(() => {
  applyTheme()
  void refreshHealth()
  healthTimer = window.setInterval(refreshHealth, 15_000)
})

onUnmounted(() => { if (healthTimer) window.clearInterval(healthTimer) })
</script>
