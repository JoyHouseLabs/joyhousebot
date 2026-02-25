<template>
  <div class="app-host">
    <div class="app-host-header">
      <h1 class="app-host-title">应用</h1>
      <p class="app-host-desc">点击「打开」在新标签页中使用应用，不嵌入当前页面。</p>
    </div>
    <div v-if="!enabledApps.length" class="app-empty">
      <p>暂无已启用的插件应用，请在插件配置中启用带 webapp 的插件。</p>
    </div>
    <div v-else class="app-card-grid">
      <div
        v-for="app in enabledApps"
        :key="app.app_id"
        class="app-card"
      >
        <div class="app-card-icon-wrap">
          <img
            v-if="app.icon_url"
            :src="app.icon_url"
            :alt="app.name"
            class="app-card-icon"
          />
          <span v-else class="app-card-icon-placeholder" aria-hidden="true">应用</span>
        </div>
        <div class="app-card-body">
          <h2 class="app-card-name">{{ app.name }}</h2>
          <p v-if="app.description" class="app-card-desc">{{ app.description }}</p>
          <p v-if="app.activation_command" class="app-card-activation">
            可说：{{ app.activation_command }}
          </p>
          <a
            :href="appLink(app)"
            target="_blank"
            rel="noopener noreferrer"
            class="app-card-open"
          >
            打开
          </a>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, computed } from 'vue'
import type { PluginApp } from '../api/pluginsApps'
import { listPluginsApps } from '../api/pluginsApps'

const apps = ref<PluginApp[]>([])

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

function appLink(app: PluginApp): string {
  if (app.app_link) return app.app_link
  if (app.base_url) return `${app.base_url}/index.html`
  return '#'
}
</script>

<style scoped>
.app-host {
  padding: 1.5rem;
  min-height: 400px;
}
.app-host-header {
  margin-bottom: 1.5rem;
}
.app-host-title {
  font-size: 1.25rem;
  font-weight: 600;
  margin: 0 0 0.25rem 0;
  color: var(--n-text-color-1);
}
.app-host-desc {
  font-size: 0.875rem;
  color: var(--n-text-color-3);
  margin: 0;
}
.app-empty {
  padding: 2rem;
  color: var(--n-text-color-3);
}
.app-card-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(280px, 1fr));
  gap: 1rem;
}
.app-card {
  display: flex;
  gap: 1rem;
  padding: 1rem;
  border: 1px solid var(--n-border-color);
  border-radius: 8px;
  background: var(--n-color-modal);
}
.app-card-icon-wrap {
  flex-shrink: 0;
  width: 48px;
  height: 48px;
  border-radius: 8px;
  overflow: hidden;
  background: var(--n-color-hover);
  display: flex;
  align-items: center;
  justify-content: center;
}
.app-card-icon {
  width: 100%;
  height: 100%;
  object-fit: contain;
}
.app-card-icon-placeholder {
  font-size: 0.75rem;
  color: var(--n-text-color-3);
}
.app-card-body {
  flex: 1;
  min-width: 0;
}
.app-card-name {
  font-size: 1rem;
  font-weight: 600;
  margin: 0 0 0.25rem 0;
  color: var(--n-text-color-1);
}
.app-card-desc {
  font-size: 0.8125rem;
  color: var(--n-text-color-2);
  margin: 0 0 0.25rem 0;
  line-height: 1.4;
}
.app-card-activation {
  font-size: 0.75rem;
  color: var(--n-text-color-3);
  margin: 0 0 0.5rem 0;
}
.app-card-open {
  display: inline-block;
  font-size: 0.875rem;
  padding: 0.25rem 0.5rem;
  border-radius: 4px;
  background: var(--n-color-primary);
  color: var(--n-color-primary-foreground);
  text-decoration: none;
  margin-top: 0.25rem;
}
.app-card-open:hover {
  opacity: 0.9;
}
</style>
