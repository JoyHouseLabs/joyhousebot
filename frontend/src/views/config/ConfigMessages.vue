<template>
  <div class="config-panel" v-if="messages">
    <div class="config-panel-header">
      <h2 class="config-panel-title">消息行为</h2>
      <p class="config-panel-desc">确认反应、回复前缀、工具错误等</p>
    </div>
    <n-form label-placement="left" label-width="160" class="config-form">
      <n-form-item label="确认反应范围">
        <n-select
          v-model:value="messages.ack_reaction_scope"
          :options="[
            { label: '（不设置）', value: null },
            { label: 'group-mentions', value: 'group-mentions' },
            { label: 'group-all', value: 'group-all' },
            { label: 'direct', value: 'direct' },
            { label: 'all', value: 'all' },
          ]"
          clearable
          style="width: 200px"
        />
      </n-form-item>
      <n-form-item label="确认反应符号">
        <n-input v-model:value="messages.ack_reaction" placeholder="如 👍" clearable />
      </n-form-item>
      <n-form-item label="回复后移除确认">
        <n-switch v-model:value="messages.remove_ack_after_reply" />
      </n-form-item>
      <n-form-item label="回复前缀模板">
        <n-input
          v-model:value="messages.response_prefix"
          type="textarea"
          placeholder="{model}, {provider} 等"
          :autosize="{ minRows: 2 }"
          clearable
        />
      </n-form-item>
      <n-form-item label="隐藏工具错误">
        <n-switch v-model:value="messages.suppress_tool_errors" />
      </n-form-item>
      <n-form-item label="工具结果后提示">
        <n-input
          v-model:value="messages.after_tool_results_prompt"
          type="textarea"
          placeholder="可选，留空使用内置"
          :autosize="{ minRows: 2 }"
          clearable
        />
      </n-form-item>
    </n-form>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import type { ConfigData } from '../../api/config'

const props = defineProps<{ config: ConfigData | null }>()

function ensureMessages() {
  const c = props.config as Record<string, unknown>
  if (!c) return
  if (!c.messages || typeof c.messages !== 'object') {
    c.messages = {}
  }
}

const messages = computed(() => {
  ensureMessages()
  const m = (props.config as Record<string, unknown>)?.messages
  return (m && typeof m === 'object' ? m : undefined) as Record<string, unknown> | undefined
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
}
</style>
