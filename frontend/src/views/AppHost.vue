<template>
  <div class="app-host">
    <div class="app-host-sidebar" v-if="enabledApps.length">
      <div class="nav-label">应用</div>
      <nav class="app-nav">
        <router-link
          v-for="app in enabledApps"
          :key="app.app_id"
          :to="app.route"
          class="app-nav-item"
          :class="{ active: currentAppId === app.app_id }"
        >
          {{ app.name }}
        </router-link>
      </nav>
    </div>
    <div class="app-host-main">
      <template v-if="currentApp">
        <iframe
          v-if="currentApp.base_url"
          :src="iframeSrc"
          class="app-iframe"
          title="currentApp.name"
        />
        <div v-else class="app-placeholder">
          <p>应用「{{ currentApp.name }}」未找到可用的 dist 资源。</p>
        </div>
      </template>
      <div v-else class="app-empty">
        <p v-if="enabledApps.length">从左侧选择一个应用。</p>
        <p v-else>暂无已启用的插件应用，请在插件配置中启用带 webapp 的插件。</p>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, watch } from 'vue'
import { useRoute } from 'vue-router'
import type { PluginApp } from '../api/pluginsApps'
import { listPluginsApps } from '../api/pluginsApps'

const apps = ref<PluginApp[]>([])
const route = useRoute()

async function load() {
  try {
    const res = await listPluginsApps()
    if (res.ok && Array.isArray(res.apps)) apps.value = res.apps
  } catch {
    apps.value = []
  }
}

load()

const enabledApps = computed(() => apps.value.filter(a => a.enabled))

const currentAppId = computed(() => {
  const p = route.path
  if (p.startsWith('/app/') && p.length > 5) return p.slice(5).split('/')[0]
  return null
})

const currentApp = computed(() => {
  const id = currentAppId.value
  if (!id) return null
  return apps.value.find(a => a.app_id === id) ?? null
})

const iframeSrc = computed(() => {
  const app = currentApp.value
  if (!app?.base_url) return ''
  let src = `${app.base_url}/index.html`
  const routeQuery = route.query?.route
  if (typeof routeQuery === 'string' && routeQuery) {
    const hash = routeQuery.startsWith('/') ? routeQuery : `/${routeQuery}`
    src += `#${hash}`
  }
  return src
})

watch(() => route.path, () => {}, { immediate: true })
</script>

<style scoped>
.app-host {
  display: flex;
  height: 100%;
  min-height: 400px;
}
.app-host-sidebar {
  width: 160px;
  flex-shrink: 0;
  padding: 0.5rem 0;
  border-right: 1px solid var(--n-border-color);
}
.app-nav { display: flex; flex-direction: column; gap: 2px; }
.app-nav-item {
  padding: 0.4rem 0.75rem;
  text-decoration: none;
  color: var(--n-text-color);
  border-radius: 6px;
}
.app-nav-item:hover { background: var(--n-color-hover); }
.app-nav-item.active { background: var(--n-color-primary); color: var(--n-color-primary-foreground); }
.app-host-main {
  flex: 1;
  min-width: 0;
  position: relative;
}
.app-iframe {
  width: 100%;
  height: 100%;
  min-height: 400px;
  border: none;
}
.app-placeholder, .app-empty {
  padding: 2rem;
  color: var(--n-text-color-3);
}
.nav-label {
  font-size: 0.75rem;
  text-transform: uppercase;
  letter-spacing: 0.05em;
  color: var(--n-text-color-3);
  padding: 0.25rem 0.75rem;
  margin-bottom: 0.25rem;
}
</style>
