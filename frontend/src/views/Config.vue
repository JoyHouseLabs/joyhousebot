<template>
  <div class="config-page">
    <div class="content-header">
      <h1 class="page-title">配置</h1>
      <p class="page-sub">Agent 默认、API 提供商、通道与 Gateway</p>
    </div>
    <n-spin :show="loading">
      <div v-if="config" class="config-layout">
        <!-- 左侧：配置分类 -->
        <aside class="config-sidebar">
          <div class="config-sidebar-header">
            <span class="config-sidebar-title">Settings</span>
            <n-tag type="success" size="small" round>valid</n-tag>
          </div>
          <n-input
            v-model:value="searchKeyword"
            placeholder="搜索配置..."
            clearable
            class="config-search"
          />
          <nav class="config-nav">
            <button
              v-for="cat in filteredCategories"
              :key="cat.key"
              type="button"
              class="config-nav-item"
              :class="{ active: activeKey === cat.key }"
              @click="activeKey = cat.key"
            >
              {{ cat.label }}
            </button>
          </nav>
        </aside>
        <!-- 右侧：当前分类的配置内容 -->
        <main class="config-main">
          <div class="config-main-header">
            <span class="config-main-status">{{ dirtyStatus }}</span>
            <n-space>
              <n-button quaternary @click="load">重新加载</n-button>
              <n-button type="primary" :loading="saving" @click="save">保存</n-button>
            </n-space>
          </div>
          <div class="config-main-body">
            <ConfigAgents v-if="activeKey === 'agents'" :config="local" />
            <ConfigProviders v-else-if="activeKey === 'providers'" :config="local" />
            <ConfigChannels v-else-if="activeKey === 'channels'" :config="local" />
            <ConfigTools v-else-if="activeKey === 'tools'" :config="local" />
            <ConfigGateway v-else-if="activeKey === 'gateway'" :config="local" />
            <ConfigMemory v-else-if="activeKey === 'memory'" :config="local" />
            <ConfigWallet v-else-if="activeKey === 'wallet'" ref="walletRef" :config="local" />
            <ConfigAuth v-else-if="activeKey === 'auth'" :config="local" />
            <ConfigSkills v-else-if="activeKey === 'skills'" :config="local" />
            <ConfigPlugins v-else-if="activeKey === 'plugins'" :config="local" />
            <ConfigApprovals v-else-if="activeKey === 'approvals'" :config="local" />
            <ConfigBrowser v-else-if="activeKey === 'browser'" :config="local" />
            <ConfigMessages v-else-if="activeKey === 'messages'" :config="local" />
            <ConfigCommands v-else-if="activeKey === 'commands'" :config="local" />
            <ConfigEnv v-else-if="activeKey === 'env'" :config="local" />
          </div>
        </main>
      </div>
      <n-empty v-else-if="!loading" description="无法加载配置，请确认 joyhousebot gateway 已启动" />
    </n-spin>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted } from 'vue'
import { useRoute } from 'vue-router'
import { useMessage } from 'naive-ui'
import type { ConfigData } from '../api/config'
import { loadConfig, buildUpdateBody, saveConfig, isDirty as checkDirty } from '../services/configService'
import {
  ConfigAgents,
  ConfigProviders,
  ConfigChannels,
  ConfigTools,
  ConfigGateway,
  ConfigMemory,
  ConfigWallet,
  ConfigAuth,
  ConfigSkills,
  ConfigPlugins,
  ConfigApprovals,
  ConfigBrowser,
  ConfigMessages,
  ConfigCommands,
  ConfigEnv,
  CONFIG_CATEGORIES,
} from './config'
import type { ConfigCategoryKey } from './config/types'

const route = useRoute()
const message = useMessage()
const loading = ref(true)
const saving = ref(false)
const config = ref<ConfigData | null>(null)
const local = ref<ConfigData | null>(null)
const walletRef = ref<{ getWalletUpdatePayload: () => { enabled: boolean; password?: string } } | null>(null)
const activeKey = ref<ConfigCategoryKey>('agents')
const searchKeyword = ref('')

const filteredCategories = computed(() => {
  const kw = searchKeyword.value.trim().toLowerCase()
  if (!kw) return CONFIG_CATEGORIES
  return CONFIG_CATEGORIES.filter(
    (c) => c.label.toLowerCase().includes(kw) || c.key.toLowerCase().includes(kw)
  )
})

const dirtyStatus = computed(() =>
  checkDirty(config.value, local.value) ? '有未保存更改' : 'No changes'
)

const hashKeys: string[] = CONFIG_CATEGORIES.map((c) => '#' + c.key)
if (route.hash && hashKeys.includes(route.hash)) {
  activeKey.value = route.hash.slice(1) as ConfigCategoryKey
}

async function load() {
  loading.value = true
  try {
    const data = await loadConfig()
    if (data) {
      config.value = data
      local.value = JSON.parse(JSON.stringify(data))
    } else {
      config.value = null
      local.value = null
    }
  } catch (e) {
    console.error(e)
    config.value = null
    local.value = null
  } finally {
    loading.value = false
  }
}

async function save() {
  if (!local.value) return
  saving.value = true
  try {
    const walletPayload = walletRef.value?.getWalletUpdatePayload?.()
    const body = buildUpdateBody(local.value, walletPayload)
    const res = await saveConfig(body)
    message.success('配置已保存')
    if (res?.wallet && local.value) {
      if (!local.value.wallet) local.value.wallet = { enabled: false, address: '' }
      local.value.wallet.enabled = res.wallet.enabled
      local.value.wallet.address = res.wallet.address ?? ''
    } else {
      await load()
    }
  } catch (e) {
    message.error(String(e))
  } finally {
    saving.value = false
  }
}

onMounted(load)
</script>

<style scoped>
.config-page {
  max-width: none;
}

.config-layout {
  display: grid;
  grid-template-columns: 240px 1fr;
  min-height: 420px;
  border: 1px solid var(--border);
  border-radius: var(--radius-md);
  overflow: hidden;
  background: var(--bg);
}

.config-sidebar {
  display: flex;
  flex-direction: column;
  border-right: 1px solid var(--border);
  background: var(--bg);
}

.config-sidebar-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 12px 14px;
  border-bottom: 1px solid var(--border);
}

.config-sidebar-title {
  font-size: 14px;
  font-weight: 600;
  color: var(--text-strong);
}

.config-search {
  margin: 10px;
}

.config-nav {
  flex: 1;
  overflow-y: auto;
  padding: 4px 8px 12px;
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.config-nav-item {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px 12px;
  border: none;
  border-radius: var(--radius-md);
  background: transparent;
  color: var(--muted);
  font-size: 13px;
  font-weight: 500;
  text-align: left;
  cursor: pointer;
  transition: background 0.15s ease, color 0.15s ease;
}

.config-nav-item:hover {
  color: var(--text);
  background: var(--bg-hover);
}

.config-nav-item.active {
  color: var(--primary);
  background: var(--accent-subtle);
}

.config-main {
  display: flex;
  flex-direction: column;
  min-width: 0;
  background: var(--bg-content);
}

.config-main-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 12px 16px;
  border-bottom: 1px solid var(--border);
  flex-shrink: 0;
}

.config-main-status {
  font-size: 13px;
  color: var(--muted);
}

.config-main-body {
  flex: 1;
  overflow-y: auto;
  padding: 20px 24px;
}

@media (max-width: 768px) {
  .config-layout {
    grid-template-columns: 1fr;
  }

  .config-sidebar {
    border-right: none;
    border-bottom: 1px solid var(--border);
  }

  .config-nav {
    flex-direction: row;
    flex-wrap: wrap;
    gap: 6px;
  }
}
</style>
