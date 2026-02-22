<template>
  <div class="config-tools-block" v-if="search">
    <n-form label-placement="left" label-width="120" class="config-form">
      <n-form-item label="API Key">
        <n-input
          v-model:value="search.api_key"
          type="password"
          show-password-on="click"
          placeholder="Brave Search API key"
        />
      </n-form-item>
      <n-form-item label="Max results">
        <n-input-number v-model:value="search.max_results" :min="1" :max="20" style="width: 100%" />
      </n-form-item>
    </n-form>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import type { ConfigData } from '../../api/config'

const props = defineProps<{ config: ConfigData | null }>()

const search = computed(() => {
  const tools = props.config?.tools as Record<string, unknown> | undefined
  const web = tools?.web as Record<string, unknown> | undefined
  let s = web?.search as Record<string, unknown> | undefined
  if (!s && tools && typeof tools.web === 'object') {
    s = (tools.web as Record<string, unknown>).search as Record<string, unknown>
  }
  if (!s) return null
  if (typeof s.api_key !== 'string') s.api_key = ''
  if (typeof s.max_results !== 'number') s.max_results = 5
  return s as { api_key: string; max_results: number }
})
</script>

<style scoped>
.config-form {
  max-width: 480px;
}
</style>
