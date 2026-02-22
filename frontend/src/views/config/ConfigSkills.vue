<template>
  <div class="config-panel" v-if="entries">
    <div class="config-panel-header">
      <h2 class="config-panel-title">技能</h2>
      <p class="config-panel-desc">按技能 ID 启用/禁用</p>
    </div>
    <n-form label-placement="left" label-width="160" class="config-form">
      <n-form-item
        v-for="(entry, name) in entries"
        :key="String(name)"
        :label="String(name)"
      >
        <n-switch v-model:value="(entry as Record<string, unknown>).enabled" />
      </n-form-item>
    </n-form>
    <p v-if="!Object.keys(entries).length" class="empty-hint">暂无技能配置，可从 Agent 页或配置文件维护。</p>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import type { ConfigData } from '../../api/config'

const props = defineProps<{ config: ConfigData | null }>()

const entries = computed(() => {
  const s = (props.config as Record<string, unknown>)?.skills as Record<string, unknown> | undefined
  const e = s?.entries as Record<string, { enabled: boolean }> | undefined
  return e ?? {}
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
.empty-hint {
  font-size: 12px;
  color: var(--n-text-color-3);
  margin: 0;
}
</style>
