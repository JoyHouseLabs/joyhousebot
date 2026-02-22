<template>
  <div class="config-tools-block" v-if="allowlist">
    <n-form label-placement="left" label-width="140" class="config-form">
      <n-form-item label="可选工具白名单">
        <n-dynamic-tags v-model:value="allowlist" />
        <p class="form-desc">留空表示不限制；填写后仅列出的工具对 Agent 可见。</p>
      </n-form-item>
    </n-form>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import type { ConfigData } from '../../api/config'

const props = defineProps<{ config: ConfigData | null }>()

const allowlist = computed({
  get() {
    const tools = props.config?.tools as Record<string, unknown> | undefined
    const list = tools?.optional_allowlist as string[] | undefined
    return Array.isArray(list) ? [...list] : []
  },
  set(val: string[]) {
    const tools = props.config?.tools as Record<string, unknown> | undefined
    if (tools) tools.optional_allowlist = val
  },
})
</script>

<style scoped>
.config-form {
  max-width: 520px;
}
.form-desc {
  font-size: 12px;
  color: var(--n-text-color-3);
  margin: 8px 0 0 0;
}
</style>
