<template>
  <div class="config-panel" v-if="config?.agents?.defaults">
    <div class="config-panel-header">
      <h2 class="config-panel-title">Agent 默认</h2>
      <p class="config-panel-desc">模型、温度、最大 token 等默认参数；多 Agent 时可选默认 Agent</p>
    </div>
    <n-form label-placement="left" label-width="140" class="config-form">
      <n-form-item label="默认 Agent (default_id)">
        <n-select
          :value="defaultIdValue"
          :options="defaultIdOptions"
          placeholder="joy"
          clearable
          style="width: 100%"
          @update:value="onDefaultIdChange"
        />
      </n-form-item>
      <n-form-item label="模型">
        <n-input v-model:value="config.agents.defaults.model" placeholder="model" />
      </n-form-item>
      <n-form-item label="Provider（留空自动）">
        <n-input v-model:value="config.agents.defaults.provider" placeholder="如 zhipu, openrouter" />
      </n-form-item>
      <n-form-item label="Temperature">
        <n-input-number v-model:value="config.agents.defaults.temperature" :min="0" :max="2" :step="0.1" style="width: 100%" />
      </n-form-item>
      <n-form-item label="Max tokens">
        <n-input-number v-model:value="config.agents.defaults.max_tokens" :min="1" style="width: 100%" />
      </n-form-item>
      <n-form-item label="最大上下文 token（可选）">
        <n-input-number
          :value="maxContextTokensValue"
          placeholder="不限制"
          :min="1"
          clearable
          style="width: 100%"
          @update:value="onMaxContextTokensChange"
        />
      </n-form-item>
      <n-form-item label="工具迭代次数">
        <n-input-number v-model:value="config.agents.defaults.max_tool_iterations" :min="1" style="width: 100%" />
      </n-form-item>
      <n-form-item label="记忆窗口">
        <n-input-number v-model:value="config.agents.defaults.memory_window" :min="1" style="width: 100%" />
      </n-form-item>
    </n-form>
    <div v-if="agentList.length" class="agent-list-hint">
      当前共 {{ agentList.length }} 个 Agent：{{ agentListNames }}
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import type { ConfigData, AgentEntryData } from '../../api/config'

const props = defineProps<{
  config: ConfigData | null
}>()

const agentList = computed(() => {
  const list = props.config?.agents?.list
  return Array.isArray(list) ? list : []
})

const defaultIdOptions = computed(() =>
  agentList.value.map((e: AgentEntryData) => ({ label: e.name || e.id || '—', value: e.id }))
)

const defaultIdValue = computed(() => {
  const v = props.config?.agents?.default_id
  return v === undefined || v === null ? '' : String(v)
})

function onDefaultIdChange(val: string | null) {
  if (!props.config?.agents) return
  ;(props.config.agents as Record<string, unknown>).default_id = val || null
}

const maxContextTokensValue = computed(() => {
  const v = props.config?.agents?.defaults?.max_context_tokens
  return v === undefined || v === null ? null : Number(v)
})

function onMaxContextTokensChange(val: number | null) {
  if (!props.config?.agents?.defaults) return
  ;(props.config.agents.defaults as Record<string, unknown>).max_context_tokens = val ?? null
}

const agentListNames = computed(() => agentList.value.map((e: AgentEntryData) => e.name || e.id).join('、'))
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
.agent-list-hint {
  margin-top: 12px;
  font-size: 12px;
  color: var(--n-text-color-3);
}
</style>
