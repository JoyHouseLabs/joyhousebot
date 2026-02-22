<template>
  <div class="config-panel" v-if="config?.providers">
    <div class="config-panel-header">
      <h2 class="config-panel-title">API 提供商</h2>
      <p class="config-panel-desc">各 API 提供商的密钥与端点配置</p>
    </div>
    <n-collapse>
      <n-collapse-item
        v-for="(p, name) in config.providers"
        :key="String(name)"
        :title="String(name)"
      >
        <n-form label-placement="left" label-width="100" size="small">
          <n-form-item v-for="(v, k) in p" :key="String(k)" :label="String(k)">
            <n-input
              v-model:value="(p as Record<string, unknown>)[k]"
              :type="isSecret(String(k)) ? 'password' : 'text'"
              :placeholder="String(k)"
              show-password-on="click"
            />
          </n-form-item>
        </n-form>
      </n-collapse-item>
    </n-collapse>
  </div>
</template>

<script setup lang="ts">
import type { ConfigData } from '../../api/config'

function isSecret(k: string) {
  const lower = k.toLowerCase()
  return lower.includes('key') || lower.includes('secret')
}

defineProps<{
  config: ConfigData | null
}>()
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
</style>
