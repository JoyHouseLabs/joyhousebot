<template>
  <div class="config-panel" v-if="config !== undefined">
    <div class="config-panel-header">
      <h2 class="config-panel-title">认证与用量</h2>
      <p class="config-panel-desc">API 认证配置与用量退避</p>
    </div>
    <n-form label-placement="left" label-width="200" class="config-form">
      <n-form-item label="计费退避 (小时)">
        <n-input-number v-model:value="cooldowns.billing_backoff_hours" :min="0" :step="0.5" style="width: 120px" />
      </n-form-item>
      <n-form-item label="计费最大窗口 (小时)">
        <n-input-number v-model:value="cooldowns.billing_max_hours" :min="0" style="width: 120px" />
      </n-form-item>
      <n-form-item label="失败窗口 (小时)">
        <n-input-number v-model:value="cooldowns.failure_window_hours" :min="0" style="width: 120px" />
      </n-form-item>
    </n-form>
    <h3 class="subsection-title">Profiles</h3>
    <n-collapse>
      <n-collapse-item
        v-for="(p, name) in (auth.profiles || {})"
        :key="String(name)"
        :title="String(name)"
      >
        <n-form label-placement="left" label-width="100" size="small">
          <n-form-item v-for="(v, k) in p" :key="String(k)" :label="String(k)">
            <n-input
              v-model:value="(p as Record<string, unknown>)[k]"
              :type="isSecret(String(k)) ? 'password' : 'text'"
              show-password-on="click"
            />
          </n-form-item>
        </n-form>
      </n-collapse-item>
    </n-collapse>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import type { ConfigData } from '../../api/config'

function isSecret(k: string) {
  const lower = k.toLowerCase()
  return lower.includes('key') || lower.includes('secret') || lower === 'token'
}

const props = defineProps<{ config: ConfigData | null }>()

function ensureAuth() {
  const c = props.config as Record<string, unknown>
  if (!c) return
  if (!c.auth || typeof c.auth !== 'object') c.auth = { profiles: {}, order: {}, cooldowns: {} }
  const a = c.auth as Record<string, unknown>
  if (!a.cooldowns || typeof a.cooldowns !== 'object') {
    a.cooldowns = {
      billing_backoff_hours: 5,
      billing_backoff_hours_by_provider: {},
      billing_max_hours: 24,
      failure_window_hours: 24,
    }
  }
}

const auth = computed(() => {
  ensureAuth()
  return ((props.config as Record<string, unknown>)?.auth ?? {}) as Record<string, unknown>
})

const cooldowns = computed(() => {
  ensureAuth()
  const a = (props.config as Record<string, unknown>)?.auth as Record<string, unknown>
  return (a?.cooldowns ?? {}) as Record<string, unknown>
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
  max-width: 520px;
  margin-bottom: 16px;
}
.subsection-title {
  font-size: 14px;
  font-weight: 600;
  margin: 0 0 8px 0;
}
</style>
