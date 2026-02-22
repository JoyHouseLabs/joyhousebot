<template>
  <div class="config-panel" v-if="config?.channels">
    <div class="config-panel-header">
      <h2 class="config-panel-title">通道</h2>
      <p class="config-panel-desc">各通道的启用状态与 Token 等配置</p>
    </div>
    <n-collapse>
      <n-collapse-item
        v-for="(c, name) in config.channels"
        :key="String(name)"
        :title="String(name)"
      >
        <n-form label-placement="left" label-width="120" size="small">
          <n-form-item v-for="(v, k) in c" :key="String(k)" :label="String(k)">
            <template v-if="k === 'enabled'">
              <n-switch v-model:value="(c as Record<string, unknown>)[k]" />
            </template>
            <template v-else>
              <n-input
                v-model:value="(c as Record<string, unknown>)[k]"
                :type="isSecret(String(k)) ? 'password' : 'text'"
                show-password-on="click"
              />
            </template>
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
  return lower.includes('token') || lower.includes('secret')
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
