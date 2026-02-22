<template>
  <div class="config-panel" v-if="config !== undefined">
    <div class="config-panel-header">
      <h2 class="config-panel-title">浏览器</h2>
      <p class="config-panel-desc">本地浏览器控制服务</p>
    </div>
    <n-form label-placement="left" label-width="140" class="config-form">
      <n-form-item label="启用">
        <n-switch v-model:value="browser.enabled" />
      </n-form-item>
      <n-form-item label="默认 Profile">
        <n-input v-model:value="browser.default_profile" placeholder="default" />
      </n-form-item>
      <n-form-item label="可执行路径">
        <n-input v-model:value="browser.executable_path" placeholder="留空自动检测" />
      </n-form-item>
      <n-form-item label="无头模式">
        <n-switch v-model:value="browser.headless" />
      </n-form-item>
    </n-form>
    <div v-if="Object.keys(browser.profiles || {}).length" class="profiles-section">
      <h3 class="subsection-title">Profiles</h3>
      <n-collapse>
        <n-collapse-item
          v-for="(prof, name) in (browser.profiles || {})"
          :key="String(name)"
          :title="String(name)"
        >
          <n-form label-placement="left" label-width="100" size="small">
            <n-form-item label="cdp_port">
              <n-input-number v-model:value="(prof as Record<string, unknown>).cdp_port" :min="0" style="width: 100%" />
            </n-form-item>
            <n-form-item label="cdp_url">
              <n-input v-model:value="(prof as Record<string, unknown>).cdp_url" />
            </n-form-item>
            <n-form-item label="color">
              <n-input v-model:value="(prof as Record<string, unknown>).color" />
            </n-form-item>
          </n-form>
        </n-collapse-item>
      </n-collapse>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import type { ConfigData } from '../../api/config'

const props = defineProps<{ config: ConfigData | null }>()

function ensureBrowser() {
  const c = props.config as Record<string, unknown>
  if (!c) return
  if (!c.browser || typeof c.browser !== 'object') {
    c.browser = {
      enabled: true,
      default_profile: 'default',
      profiles: {},
      executable_path: '',
      headless: false,
    }
  }
}

const browser = computed(() => {
  ensureBrowser()
  return ((props.config as Record<string, unknown>)?.browser ?? {}) as Record<string, unknown>
})
</script>

<style scoped>
.config-panel-header {
  margin-bottom: 16px;
}
.config-panel-title {
  font-size: 16px;
  font-weight: 600;
  margin: 0 0 4px 0;
}
.config-panel-desc {
  font-size: 12px;
  color: var(--n-text-color-3);
  margin: 0;
}
.config-form {
  max-width: 480px;
}
.profiles-section {
  margin-top: 20px;
}
.subsection-title {
  font-size: 14px;
  font-weight: 600;
  margin: 0 0 8px 0;
}
</style>
